"""
Tactical Ladder backtest engine.

Pure functions over plain data structures — no Streamlit, no network, no
Supabase. The caller supplies price and breadth series; this module only
decides and accounts. That keeps it unit-testable against synthetic paths
where the correct answer is known by hand.

Model, in the framework's own terms:

  Capital starts as 35% NiftyBees / 25% Midcap 150 / 40% tactical cash.
  Cash earns CASH_YIELD while idle.

  DEPLOY (below the 200 EMA): a tier arms when BOTH the Nifty's distance
  below its 200 EMA is at or beyond the tier threshold AND breadth is at or
  below the tier's ceiling. An armed tier deploys its tranche into Midcap 150
  once, then stays spent until re-armed (see re-arm below).

  HARVEST (above the 200 EMA): H1 activates trailing stops; H2/H3/H4 book
  25/50/75% of the *tactical* holding back to cash. Each harvest tier also
  fires once per cycle.

  RE-ARM: tier state resets when the Nifty closes back above its 200 EMA
  (deploy tiers) or back below it (harvest tiers). One correction, one pass
  down the ladder — otherwise a long grind below the EMA would re-deploy on
  every bar.

  CASH FLOOR: never deploy below MIN_CASH_FRAC of the original tactical
  reserve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ── Framework constants ──────────────────────────────────────
CASH_YIELD = 0.065          # p.a. on idle tactical cash (liquid/arb funds)
MIN_CASH_FRAC = 0.25        # never deploy more than 75% of tactical reserve
TRADING_DAYS = 252

DEPLOY_TIERS = [
    {"tier": 1, "threshold": -5.0,  "breadth_max": 40, "deploy_pct": 8},
    {"tier": 2, "threshold": -10.0, "breadth_max": 35, "deploy_pct": 8},
    {"tier": 3, "threshold": -15.0, "breadth_max": 30, "deploy_pct": 8},
    {"tier": 4, "threshold": -20.0, "breadth_max": 25, "deploy_pct": 8},
    {"tier": 5, "threshold": -25.0, "breadth_max": 20, "deploy_pct": 8},
]

# (tier, EMA distance trigger, fraction of tactical holding to book)
HARVEST_TIERS = [
    {"tier": "H1", "threshold": 5.0,  "book_frac": 0.00},   # trail only
    {"tier": "H2", "threshold": 10.0, "book_frac": 0.25},
    {"tier": "H3", "threshold": 15.0, "book_frac": 0.50},
    {"tier": "H4", "threshold": 20.0, "book_frac": 0.75},
]


@dataclass
class Event:
    d: date
    kind: str            # "deploy" | "harvest"
    tier: str
    ema_dist: float
    breadth: float | None
    amount: float        # rupees moved
    multiplier: float = 1.0
    note: str = ""


@dataclass
class Result:
    dates: list[date] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    cash: list[float] = field(default_factory=list)
    deployed: list[float] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    @property
    def final(self) -> float:
        return self.equity[-1] if self.equity else 0.0

    def cagr(self) -> float:
        if len(self.equity) < 2 or self.equity[0] <= 0:
            return 0.0
        years = (self.dates[-1] - self.dates[0]).days / 365.25
        if years <= 0:
            return 0.0
        return (self.equity[-1] / self.equity[0]) ** (1 / years) - 1

    def max_drawdown(self) -> float:
        peak, mdd = float("-inf"), 0.0
        for v in self.equity:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, v / peak - 1)
        return mdd


def ema(values: list[float], span: int) -> list[float | None]:
    """Standard EMA with an SMA seed, matching the dashboard's 200 EMA.

    Returns None for bars before the seed window is full, so callers cannot
    silently backtest against a half-warmed average.
    """
    out: list[float | None] = [None] * len(values)
    if len(values) < span:
        return out
    seed = sum(values[:span]) / span
    out[span - 1] = seed
    k = 2 / (span + 1)
    prev = seed
    for i in range(span, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def fear_multiplier(signals_on: int) -> tuple[float, float]:
    """(size multiplier, early-fire allowance in EMA-distance points).

    0 or 1 of 3 -> 1x; 2 of 3 -> 1.5x; 3 of 3 -> 2x and tiers fire 1% early.
    """
    if signals_on >= 3:
        return 2.0, 1.0
    if signals_on == 2:
        return 1.5, 0.0
    return 1.0, 0.0


def run(
    dates: list[date],
    nifty: list[float],
    midcap: list[float],
    breadth: dict[date, float],
    *,
    initial: float = 1_000_000.0,
    core_nifty_frac: float = 0.35,
    core_mid_frac: float = 0.25,
    tactical_frac: float = 0.40,
    use_breadth: bool = True,
    fear_signals: dict[date, int] | None = None,
    enable_harvest: bool = True,
    ema_span: int = 200,
) -> Result:
    """Run the ladder over aligned daily series.

    `dates`, `nifty` and `midcap` must be the same length and aligned.
    `breadth` and `fear_signals` are looked up per date; a missing breadth
    reading means the breadth gate cannot confirm that day (fail-closed).
    """
    if not (len(dates) == len(nifty) == len(midcap)):
        raise ValueError("dates, nifty and midcap must be the same length")
    if abs(core_nifty_frac + core_mid_frac + tactical_frac - 1.0) > 1e-9:
        raise ValueError("allocation fractions must sum to 1.0")

    ema200 = ema(nifty, ema_span)
    fear_signals = fear_signals or {}

    res = Result()

    # Core sleeves are buy-and-hold; tactical is what the ladder moves.
    core_n_units = initial * core_nifty_frac / nifty[0]
    core_m_units = initial * core_mid_frac / midcap[0]

    tactical_cash = initial * tactical_frac
    tactical_reserve0 = tactical_cash
    tactical_units = 0.0                      # Midcap 150 units held by the ladder
    cash_floor = tactical_reserve0 * MIN_CASH_FRAC

    deploy_fired: set[int] = set()
    harvest_fired: set[str] = set()
    daily_yield = (1 + CASH_YIELD) ** (1 / TRADING_DAYS) - 1

    for i, d in enumerate(dates):
        e = ema200[i]
        px_n, px_m = nifty[i], midcap[i]

        # Idle cash accrues before any decision on this bar — but not on bar 0,
        # where no time has yet passed since the initial allocation.
        if i > 0:
            tactical_cash *= 1 + daily_yield

        if e is not None and e > 0:
            dist = (px_n - e) / e * 100.0
            b = breadth.get(d)
            mult, early = fear_multiplier(fear_signals.get(d, 0))

            if dist < 0:
                harvest_fired.clear()          # re-arm harvest once back below
                for t in DEPLOY_TIERS:
                    if t["tier"] in deploy_fired:
                        continue
                    ema_ok = dist <= t["threshold"] + early
                    breadth_ok = (not use_breadth) or (b is not None and b <= t["breadth_max"])
                    if not (ema_ok and breadth_ok):
                        continue

                    want = tactical_reserve0 * (t["deploy_pct"] / 100.0) * mult
                    room = max(0.0, tactical_cash - cash_floor)
                    amt = min(want, room)
                    if amt <= 0:
                        continue

                    tactical_cash -= amt
                    tactical_units += amt / px_m
                    deploy_fired.add(t["tier"])
                    res.events.append(Event(
                        d, "deploy", f"T{t['tier']}", dist, b, amt, mult,
                        note="capped by cash floor" if amt < want - 1e-6 else ""))
            else:
                deploy_fired.clear()           # re-arm deploy once back above
                if enable_harvest and tactical_units > 0:
                    for h in HARVEST_TIERS:
                        if h["tier"] in harvest_fired or dist < h["threshold"]:
                            continue
                        harvest_fired.add(h["tier"])
                        if h["book_frac"] <= 0:
                            res.events.append(Event(
                                d, "harvest", h["tier"], dist, b, 0.0,
                                note="trailing stops activated"))
                            continue
                        units = tactical_units * h["book_frac"]
                        proceeds = units * px_m
                        tactical_units -= units
                        tactical_cash += proceeds
                        res.events.append(Event(
                            d, "harvest", h["tier"], dist, b, proceeds))

        equity = (core_n_units * px_n + core_m_units * px_m
                  + tactical_units * px_m + tactical_cash)
        res.dates.append(d)
        res.equity.append(equity)
        res.cash.append(tactical_cash)
        res.deployed.append(tactical_units * px_m)

    return res


def static_allocation(dates: list[date], nifty: list[float], midcap: list[float],
                      *, initial: float = 1_000_000.0,
                      core_nifty_frac: float = 0.35,
                      core_mid_frac: float = 0.25,
                      tactical_frac: float = 0.40) -> Result:
    """Same capital structure as the ladder, but the tactical reserve is never
    deployed — it just earns CASH_YIELD.

    This is the benchmark that isolates the ladder's RULES. Comparing the
    ladder against a fully-invested portfolio instead measures the cash
    allocation, which is a separate decision: the Nifty sits above its 200 EMA
    roughly 90% of the time, so a reserve waiting for dips is idle most of the
    time whatever the entry rules are.
    """
    res = Result()
    n_units = initial * core_nifty_frac / nifty[0]
    m_units = initial * core_mid_frac / midcap[0]
    cash = initial * tactical_frac
    daily_yield = (1 + CASH_YIELD) ** (1 / TRADING_DAYS) - 1
    for i, d in enumerate(dates):
        if i > 0:
            cash *= 1 + daily_yield
        res.dates.append(d)
        res.equity.append(n_units * nifty[i] + m_units * midcap[i] + cash)
        res.cash.append(cash)
        res.deployed.append(0.0)
    return res


def lump_sum(dates: list[date], nifty: list[float], midcap: list[float],
             *, initial: float = 1_000_000.0,
             core_nifty_frac: float = 0.35,
             core_mid_frac: float = 0.25,
             tactical_frac: float = 0.40) -> Result:
    """Tactical reserve deployed into midcap on day one and held.

    Answers the other half of the question: does waiting for dips beat simply
    being invested? The ladder only earns its keep if it beats this too.
    """
    res = Result()
    n_units = initial * core_nifty_frac / nifty[0]
    m_units = initial * (core_mid_frac + tactical_frac) / midcap[0]
    for i, d in enumerate(dates):
        res.dates.append(d)
        res.equity.append(n_units * nifty[i] + m_units * midcap[i])
        res.cash.append(0.0)
        res.deployed.append(0.0)
    return res


def buy_and_hold(dates: list[date], nifty: list[float], midcap: list[float],
                 *, initial: float = 1_000_000.0,
                 nifty_frac: float = 0.60) -> Result:
    """Fully invested static split — an allocation ceiling, NOT a test of the
    ladder's rules. It holds no cash, so it will normally beat any strategy
    that keeps a reserve during a rising market. Read it as context, not as
    the benchmark the ladder has to clear.
    """
    res = Result()
    n_units = initial * nifty_frac / nifty[0]
    m_units = initial * (1 - nifty_frac) / midcap[0]
    for i, d in enumerate(dates):
        res.dates.append(d)
        res.equity.append(n_units * nifty[i] + m_units * midcap[i])
        res.cash.append(0.0)
        res.deployed.append(0.0)
    return res
