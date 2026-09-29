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


# ══════════════════════════════════════════════════════════════
#  Allocation-matrix engine (BACKTEST_SPEC_2008.md §2, §5)
# ══════════════════════════════════════════════════════════════
#
# The tranche ladder above deploys fixed 8% slices of a 40% reserve, which caps
# total deployment at 16% of the portfolio and never reaches MIN_CASH_FRAC. The
# matrix engine replaces that with a *target allocation per cycle state* and
# rebalances toward it, so a late tier still lands on the right weight and
# harvest is the same mechanism in reverse.
#
# CAPITAL MODEL — read this, it is the accounting trap §3 warns about.
#
#   Total capital C(t) = equity + debt + gold + standby reserve. EVERYTHING
#   (CAGR, drawdown, XIRR) is measured on C(t). The standby reserve is part of
#   C from day one, sitting in deposits earning DEPOSIT_YIELD until deployed.
#   Going "above 100% gross" is NOT leverage: it is spending the investor's own
#   deposit money, which was always inside C. There is no external infusion to
#   mistake for performance.
#
#   Weights are expressed against a base reference  base_ref = C(t) / (1 + rob),
#   where rob = reserve-over-base (the standby set aside, e.g. 0.20 for a family
#   whose deepest tier reaches 120% gross). Then:
#       equity target  = equity_frac * base_ref        (equity_frac may exceed 1)
#       reserve set-aside (capacity) = rob * base_ref
#       reserve DEPLOYED into equity = max(0, equity_frac - 1) * base_ref
#       reserve left idle in deposits = capacity - deployed
#   The four sleeve targets always sum to C(t) exactly, so a rebalance moves
#   money between sleeves without inventing or destroying any. Depletion is when
#   idle reserve hits zero — which happens exactly when equity_frac reaches
#   1 + rob. That is the "reserve exhausted before the bottom" event.
#
#   The 100%-capped CONTROL uses the SAME rob (same money set aside) but caps
#   equity_frac at 1.0, so its reserve never deploys and just earns yield. That
#   is what isolates the contribution of the above-100% deployment.

DEPOSIT_YIELD = 0.065        # p.a. on idle standby deposits AND the debt sleeve
STCG_RATE = 0.20            # < 1 year, Indian equity/gold short-term capital gains
LTCG_RATE = 0.125           # >= 1 year, long-term capital gains
LTCG_DAYS = 365


@dataclass
class LedgerRow:
    """One trade row in the unit ledger (Session 2 spec §2).

    cycle_id is left empty by run_matrix; call annotate_cycles() afterwards
    to fill it in from the detected cycle list.
    """
    date: date
    cycle_id: str            # filled in by annotate_cycles()
    tier: str                # allocation-matrix band name
    fund: str                # "NIFTY" | "MIDCAP" | "GOLD"
    side: str                # "BUY" | "SELL"
    units: float
    price: float
    rupees: float
    cum_units_fund: float    # sleeve.units immediately after this trade
    avg_cost_per_unit_fund: float
EQUITY_NIFTY_FRAC = 0.58    # equity is 58/42 Nifty/Midcap (the 35/25 core proportion)

# Non-equity sleeve caps (fractions of base_ref) used to split the debt/gold
# pool by spend order in the deploy region. Match the matrix's baseline row
# (debt 15 / gold 5); debt_first then reproduces the matrix's gold-preserving
# glide (T1 10/5, ...), gold_first is the alternative that dumps gold early.
_BASE_DEBT_CAP = 0.15
_BASE_GOLD_CAP = 0.05


@dataclass
class Band:
    """One cycle-state row of the allocation matrix.

    `lo`/`hi` are the EMA-distance bounds in percent: the band applies when
    lo < dist <= hi. `equity` is the target equity weight as a fraction of
    base_ref (may exceed 1.0). `gross` is equity+debt+gold as a fraction of
    base_ref. `breadth_max`/`vix_min` are the deploy gate (None = no gate).
    """
    name: str
    lo: float
    hi: float
    equity: float
    gross: float
    breadth_max: float | None = None
    vix_min: float | None = None


# Frothy → deepest. T6–T8 are the free parameters this session explores; the
# defaults below are a documented starting schedule, overridable per scenario.
def default_matrix(t6: float = 1.30, t7: float = 1.40, t8: float = 1.50) -> list[Band]:
    return [
        Band("Frothy >+20%",  20.0,  1e9,  0.70, 1.00),
        Band("+8..+20%",       8.0,  20.0, 0.75, 1.00),
        Band("Baseline",      -5.0,   8.0, 0.80, 1.00),
        Band("T1 -5..-10%",  -10.0,  -5.0, 0.85, 1.00, breadth_max=40, vix_min=20),
        Band("T2 -10..-15%", -15.0, -10.0, 0.92, 1.00, breadth_max=35, vix_min=25),
        Band("T3 -15..-20%", -20.0, -15.0, 1.00, 1.00, breadth_max=30),
        Band("T4 -20..-25%", -25.0, -20.0, 1.10, 1.10, breadth_max=25),
        Band("T5 -25..-30%", -30.0, -25.0, 1.20, 1.20, breadth_max=20),
        Band("T6 -30..-40%", -40.0, -30.0, t6,   t6,   breadth_max=20),
        Band("T7 -40..-50%", -50.0, -40.0, t7,   t7,   breadth_max=20),
        Band("T8 <=-50%",    -1e9, -50.0,  t8,   t8,   breadth_max=20),
    ]


ALLOC_MATRIX = default_matrix()


def _band_for_dist(dist: float, matrix: list[Band]) -> int:
    for i, b in enumerate(matrix):
        if b.lo < dist <= b.hi:
            return i
    return len(matrix) - 1          # dist below the deepest band's lo


def _gate_ok(b: Band, breadth: float | None, vix: float | None, gates_on: bool) -> bool:
    """Deploy gate. Fails closed when a required reading is missing — the same
    discipline as the tranche engine, so a data gap cannot deploy by default."""
    if not gates_on:
        return True
    if b.breadth_max is not None:
        if breadth is None or breadth > b.breadth_max:
            return False
    if b.vix_min is not None:
        if vix is None or vix < b.vix_min:
            return False
    return True


def resolve_band_ex(dist: float, breadth: float | None, vix: float | None,
                    gates_on: bool, matrix: list[Band]) -> tuple[int, bool]:
    """Deepest reachable band whose gate passes, plus whether a gate forced it.

    Above baseline (dist > -5) there are no gates. In the deploy region the
    market has, by definition, also reached every shallower deploy band, so if
    the current band's gate fails we fall back to the deepest shallower deploy
    band that confirms — never deploying past what the gates allow.

    The second element is True when the returned band is SHALLOWER than the one
    price alone would select, i.e. the fallback was caused by a gate failure and
    not by price recovering. Callers use it to keep the gate a deploy gate: it
    may block an increase, but it must never mandate a decrease of an existing
    holding (see `gate_ratchet` in run_matrix).
    """
    raw = _band_for_dist(dist, matrix)
    if matrix[raw].breadth_max is None and matrix[raw].vix_min is None:
        return raw, False                           # baseline / frothy, no gate
    for i in range(raw, -1, -1):                    # walk shallower until one confirms
        b = matrix[i]
        if b.breadth_max is None and b.vix_min is None:
            return i, i != raw                      # reached baseline
        if _gate_ok(b, breadth, vix, gates_on):
            return i, i != raw
    return raw, False


def resolve_band(dist: float, breadth: float | None, vix: float | None,
                 gates_on: bool, matrix: list[Band]) -> int:
    """Deepest reachable band whose gate passes. See resolve_band_ex."""
    return resolve_band_ex(dist, breadth, vix, gates_on, matrix)[0]


# ── XIRR (cash-flow IRR), the §3 cross-check ─────────────────
def xirr(flows: list[tuple[date, float]], guess: float = 0.1) -> float:
    """Annualised money-weighted return of dated cash flows (Actual/365).

    For these strategies the only flows are -C0 at the start and +C_final at
    the end (the standby is internal, never an external infusion), so XIRR must
    equal the total-capital CAGR. A divergence means deployment was wrongly
    booked as an infusion — exactly the trap §3 says to catch.
    """
    if len(flows) < 2:
        return 0.0
    t0 = flows[0][0]
    yrs = [(d - t0).days / 365.0 for d, _ in flows]
    amts = [a for _, a in flows]

    def npv(r):
        return sum(a / (1 + r) ** y for a, y in zip(amts, yrs))

    def dnpv(r):
        return sum(-y * a / (1 + r) ** (y + 1) for a, y in zip(amts, yrs))

    r = guess
    for _ in range(100):
        f = npv(r)
        df = dnpv(r)
        if abs(df) < 1e-12:
            break
        step = f / df
        r -= step
        if r <= -0.999999:
            r = -0.999 + 1e-6
        if abs(step) < 1e-10:
            break
    return r


@dataclass
class _Lot:
    units: float
    cost: float          # cost per unit
    d: date


class _PriceSleeve:
    """A price-driven sleeve (an equity fund or gold) with FIFO tax lots.

    Lots are tracked always; capital-gains tax is only *charged* when the run
    asks for it, so turning tax on cannot change the pre-tax unit arithmetic.
    """
    def __init__(self):
        self.lots: list[_Lot] = []

    @property
    def units(self) -> float:
        return sum(l.units for l in self.lots)

    def value(self, px: float) -> float:
        return self.units * px

    def buy(self, rupees: float, px: float, d: date) -> None:
        if rupees <= 0 or px <= 0:
            return
        self.lots.append(_Lot(rupees / px, px, d))

    def avg_cost(self) -> float:
        u = self.units
        if u <= 1e-12:
            return 0.0
        return sum(l.cost * l.units for l in self.lots) / u

    def sell_to_value(self, target_val: float, px: float, d: date,
                      taxable: bool) -> tuple[float, float]:
        """Sell FIFO down to `target_val`. Returns (gross_proceeds, tax)."""
        cur = self.value(px)
        if px <= 0 or target_val >= cur:
            return 0.0, 0.0
        sell_units = (cur - target_val) / px
        proceeds = sell_units * px
        tax = 0.0
        remaining = sell_units
        while remaining > 1e-12 and self.lots:
            lot = self.lots[0]
            take = min(lot.units, remaining)
            gain = (px - lot.cost) * take
            if taxable and gain > 0:
                rate = LTCG_RATE if (d - lot.d).days >= LTCG_DAYS else STCG_RATE
                tax += rate * gain
            lot.units -= take
            remaining -= take
            if lot.units <= 1e-12:
                self.lots.pop(0)
        return proceeds, tax


def _split_nonequity(ne_val: float, base_ref: float, spend_order: str) -> tuple[float, float]:
    """Split the non-equity pool into (debt, gold) targets by spend order.

    gold_first keeps debt (spends gold first); debt_first keeps gold. In the
    deploy region (pool <= 20% of base) this reproduces the matrix's own glide
    for debt_first and the gold-dumping alternative for gold_first. Above that
    (frothy states, above the EMA) both split 75/25 debt/gold — those states are
    not part of the crash stress and both orders coincide there.
    """
    if ne_val <= 0:
        return 0.0, 0.0
    cap_sum = (_BASE_DEBT_CAP + _BASE_GOLD_CAP) * base_ref
    if ne_val > cap_sum:
        return 0.75 * ne_val, 0.25 * ne_val
    if spend_order == "gold_first":
        debt = min(ne_val, _BASE_DEBT_CAP * base_ref)
        return debt, ne_val - debt
    # debt_first (default): preserve gold
    gold = min(ne_val, _BASE_GOLD_CAP * base_ref)
    return ne_val - gold, gold


@dataclass
class MatrixResult:
    dates: list[date] = field(default_factory=list)
    total: list[float] = field(default_factory=list)      # total capital C(t)
    equity: list[float] = field(default_factory=list)
    debt: list[float] = field(default_factory=list)
    gold: list[float] = field(default_factory=list)
    reserve: list[float] = field(default_factory=list)     # idle standby deposits
    band: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    tax_paid: float = 0.0
    cost_paid: float = 0.0
    # Per-bar sleeve unit counts (populated when run_matrix receives a ledger list)
    nifty_units: list[float] = field(default_factory=list)
    mid_units: list[float] = field(default_factory=list)
    # Depletion (the headline risk numbers of the whole exercise):
    depletion_date: date | None = None
    depletion_dist: float | None = None
    max_dist_after_depletion: float | None = None
    days_above_100: int = 0

    @property
    def final(self) -> float:
        return self.total[-1] if self.total else 0.0

    def cagr(self) -> float:
        if len(self.total) < 2 or self.total[0] <= 0:
            return 0.0
        years = (self.dates[-1] - self.dates[0]).days / 365.25
        if years <= 0:
            return 0.0
        return (self.total[-1] / self.total[0]) ** (1 / years) - 1

    def xirr(self) -> float:
        if len(self.total) < 2:
            return 0.0
        return xirr([(self.dates[0], -self.total[0]),
                     (self.dates[-1], self.total[-1])])

    def max_drawdown(self) -> float:
        peak, mdd = float("-inf"), 0.0
        for v in self.total:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, v / peak - 1)
        return mdd

    def calmar(self) -> float:
        mdd = abs(self.max_drawdown())
        return self.cagr() / mdd if mdd > 1e-9 else float("inf")


def run_matrix(
    dates: list[date],
    nifty: list[float],
    midcap: list[float],
    gold: list[float],
    breadth: dict[date, float],
    vix: dict[date, float] | None = None,
    *,
    initial: float = 1_000_000.0,
    matrix: list[Band] | None = None,
    reserve_over_base: float = 0.20,
    deploy_cap: float | None = None,
    spend_order: str = "debt_first",
    whipsaw: str = "none",
    whipsaw_param: float = 0.0,
    gates_on: bool = True,
    deposit_yield: float = DEPOSIT_YIELD,
    tx_cost_bps: float = 0.0,
    tax: bool = False,
    ema_span: int = 200,
    ledger: list | None = None,
    harvest_gate: float = 0.0,
    harvest_cap_frac: float = 0.0,
    harvest_shrink: float = 0.0,
    gate_ratchet: bool = False,
) -> MatrixResult:
    """Allocation-matrix backtest on aligned daily series.

    `nifty`, `midcap`, `gold` are aligned closes; `breadth`/`vix` are per-date
    lookups for the deploy gate. `reserve_over_base` (rob) is the standby set
    aside as a fraction of base; `deploy_cap` caps equity_frac (pass 1.0 for the
    100%-capped control; default = 1 + rob, i.e. willing to deploy all reserve).

    Anti-whipsaw modes (`whipsaw`): "none"; "hysteresis" (exit a tier only once
    dist rises whipsaw_param points above its entry); "confirm" (adopt a new
    band only after it holds whipsaw_param bars); "glide" (close 1/whipsaw_param
    of the gap to the target equity per week).
    """
    n = len(dates)
    if not (n == len(nifty) == len(midcap) == len(gold)):
        raise ValueError("dates, nifty, midcap and gold must be the same length")
    if spend_order not in ("debt_first", "gold_first"):
        raise ValueError("spend_order must be 'debt_first' or 'gold_first'")
    if whipsaw not in ("none", "hysteresis", "confirm", "glide"):
        raise ValueError("unknown whipsaw mode")

    matrix = matrix or ALLOC_MATRIX
    rob = reserve_over_base
    if deploy_cap is None:
        deploy_cap = 1.0 + rob
    vix = vix or {}
    ema200 = ema(nifty, ema_span)

    res = MatrixResult()
    daily_yield = (1 + deposit_yield) ** (1 / TRADING_DAYS) - 1
    cost_frac = tx_cost_bps / 10_000.0

    # ── Initial allocation (baseline state), before the EMA warms ──
    base_ref0 = initial / (1.0 + rob)
    eq0 = 0.80 * base_ref0
    ne0 = 0.20 * base_ref0
    d0_debt, d0_gold = _split_nonequity(ne0, base_ref0, spend_order)
    nifty_s, mid_s, gold_s = _PriceSleeve(), _PriceSleeve(), _PriceSleeve()
    nifty_s.buy(EQUITY_NIFTY_FRAC * eq0, nifty[0], dates[0])
    mid_s.buy((1 - EQUITY_NIFTY_FRAC) * eq0, midcap[0], dates[0])
    gold_s.buy(d0_gold, gold[0], dates[0])
    debt_val = d0_debt
    reserve = rob * base_ref0

    # Emit initial allocation rows so the ledger is complete from day 0.
    if ledger is not None:
        for _s, _px, _fn in ((nifty_s, nifty[0], "NIFTY"),
                              (mid_s, midcap[0], "MIDCAP"),
                              (gold_s, gold[0], "GOLD")):
            if _s.units > 1e-12:
                ledger.append(LedgerRow(
                    date=dates[0], cycle_id="", tier="initial",
                    fund=_fn, side="BUY",
                    units=_s.units, price=_px, rupees=_s.units * _px,
                    cum_units_fund=_s.units,
                    avg_cost_per_unit_fund=_s.avg_cost()))

    eff_idx = 2                       # baseline band index
    pending_idx, pending_cnt = eff_idx, 0
    eq_level = 0.80                   # current equity_frac (for glide)
    held_eq_frac = 0.80               # equity_frac we last rebalanced to

    # Harvest-mode state (used when harvest_gate / harvest_cap_frac / harvest_shrink > 0)
    _ath = nifty[0]        # running all-time-high
    _trough = nifty[0]     # min price since last ATH

    def _charge(amount_cost: float, amount_tax: float):
        """Deduct costs+tax from the reserve first, then debt if reserve empty."""
        nonlocal reserve, debt_val
        res.cost_paid += amount_cost
        res.tax_paid += amount_tax
        leak = amount_cost + amount_tax
        take = min(reserve, leak)
        reserve -= take
        leak -= take
        if leak > 0:                  # reserve empty (deep deployment) — take from debt
            take = min(debt_val, leak)
            debt_val -= take

    for i, d in enumerate(dates):
        e = ema200[i]
        px_n, px_m, px_g = nifty[i], midcap[i], gold[i]

        if i > 0:
            reserve *= 1 + daily_yield
            debt_val *= 1 + daily_yield

        # ── ATH / trough tracking (needed by all three harvest modes) ──
        if px_n > _ath:
            _ath = px_n
            _trough = px_n
        else:
            _trough = min(_trough, px_n)
        _rec_frac = ((px_n - _trough) / (_ath - _trough)
                     if _ath > _trough + 1e-6 else 1.0)

        if e is not None and e > 0:
            dist = (px_n - e) / e * 100.0
            b = breadth.get(d)
            vx = vix.get(d)
            raw_idx, gate_blocked = resolve_band_ex(dist, b, vx, gates_on, matrix)

            # ── Anti-whipsaw → effective band ──
            if whipsaw == "none" or whipsaw == "glide":
                eff_idx = raw_idx
            elif whipsaw == "confirm":
                need = max(1, int(whipsaw_param))
                if raw_idx == pending_idx:
                    pending_cnt += 1
                else:
                    pending_idx, pending_cnt = raw_idx, 1
                if pending_cnt >= need:
                    eff_idx = pending_idx
            elif whipsaw == "hysteresis":
                if raw_idx > eff_idx:
                    eff_idx = raw_idx                       # deepen immediately
                elif raw_idx < eff_idx:
                    # exit current tier only once dist rises whipsaw_param
                    # points above the current effective band's entry edge.
                    if dist > matrix[eff_idx].hi + whipsaw_param:
                        eff_idx = raw_idx

            target_band = matrix[eff_idx]
            target_eq_frac = min(target_band.equity, deploy_cap, 1.0 + rob)

            if whipsaw == "glide":
                span = max(1.0, whipsaw_param)              # gap fraction per WEEK
                per_bar = 1.0 - (1.0 - 1.0 / span) ** (1.0 / 5.0)
                eq_level += (target_eq_frac - eq_level) * per_bar
                eff_eq_frac = min(eq_level, deploy_cap, 1.0 + rob)
            else:
                eff_eq_frac = target_eq_frac
                eq_level = eff_eq_frac

            # ── Gate ratchet ──────────────────────────────────────────────
            # The breadth/VIX gate is a DEPLOY gate: it exists to stop us
            # buying past what confirmation allows. Without this, a gate
            # failure also drags the target down to the fallback band's
            # equity, and because the target is applied unconditionally below,
            # that reads as a SELL of whatever is already held above it — i.e.
            # the gate liquidates at the bottom, which is the opposite of its
            # purpose. With the ratchet on, a gate-induced fallback floors the
            # target at the current holding: it can block an increase, never
            # mandate a decrease. Genuine price-driven harvests (no gate
            # involved) are untouched.
            if gate_ratchet and gate_blocked and eff_eq_frac < held_eq_frac:
                eff_eq_frac = held_eq_frac
                if whipsaw == "glide":
                    eq_level = held_eq_frac

            # ── Harvest mode overrides (only affect sells, not buys) ──
            if eff_eq_frac < held_eq_frac:
                # (a) gate: suppress sell entirely until price recovers to gate×ATH
                if harvest_gate > 0 and px_n < _ath * harvest_gate:
                    eff_eq_frac = held_eq_frac
                    if whipsaw == "glide":
                        eq_level = held_eq_frac

                # (b) cap: limit sell to harvest_cap_frac × portfolio per event
                elif harvest_cap_frac > 0:
                    C_now = (nifty_s.value(px_n) + mid_s.value(px_m)
                             + gold_s.value(px_g) + debt_val + reserve)
                    max_reduce = harvest_cap_frac * C_now / (1.0 + rob)
                    capped_frac = max(eff_eq_frac, held_eq_frac - max_reduce)
                    eff_eq_frac = capped_frac
                    if whipsaw == "glide":
                        eq_level = max(eq_level, capped_frac)

                # (c) shrink: scale sell amount by (1 − shrink × recovery_frac)
                elif harvest_shrink > 0:
                    scale = max(0.0, 1.0 - harvest_shrink * _rec_frac)
                    shrunk_frac = held_eq_frac - (held_eq_frac - eff_eq_frac) * scale
                    eff_eq_frac = shrunk_frac
                    if whipsaw == "glide":
                        eq_level = max(eq_level, shrunk_frac)

            # ── Rebalance ONLY when the target actually moves ──
            # The tactical ladder is event-driven: it deploys on entering a
            # deeper tier and harvests on entering a higher one, and otherwise
            # HOLDS. Rebalancing the whole book every bar instead would harvest
            # daily noise ("volatility pumping") and massively overstate returns
            # — a constant-mix daily rebalance of these series compounds to
            # several times a buy-and-hold of the same weights, which is a
            # backtest artifact, not a tradeable return. So we trade only when
            # the effective equity target has moved materially since the last
            # rebalance; between moves, positions ride.
            if abs(eff_eq_frac - held_eq_frac) > 1e-4:
                C = (nifty_s.value(px_n) + mid_s.value(px_m)
                     + gold_s.value(px_g) + debt_val + reserve)
                base_ref = C / (1.0 + rob)
                tgt_eq = eff_eq_frac * base_ref
                if eff_eq_frac <= 1.0:
                    ne_val = (1.0 - eff_eq_frac) * base_ref
                else:
                    ne_val = 0.0
                tgt_debt, tgt_gold = _split_nonequity(ne_val, base_ref, spend_order)
                tgt_n = EQUITY_NIFTY_FRAC * tgt_eq
                tgt_m = (1 - EQUITY_NIFTY_FRAC) * tgt_eq
                cost = tax_amt = 0.0

                # sells first (raise cash into reserve), then buys draw it down
                _sell_iter = ((nifty_s, px_n, tgt_n, "NIFTY"),
                              (mid_s, px_m, tgt_m, "MIDCAP"),
                              (gold_s, px_g, tgt_gold, "GOLD"))
                for sleeve, px, tgt, fname in _sell_iter:
                    cur = sleeve.value(px)
                    if tgt < cur:
                        proceeds, t = sleeve.sell_to_value(tgt, px, d, tax)
                        cost += cost_frac * proceeds
                        tax_amt += t
                        reserve += proceeds
                        if ledger is not None and proceeds > 1e-9:
                            ledger.append(LedgerRow(
                                date=d, cycle_id="", tier=target_band.name,
                                fund=fname, side="SELL",
                                units=proceeds / px, price=px, rupees=proceeds,
                                cum_units_fund=sleeve.units,
                                avg_cost_per_unit_fund=sleeve.avg_cost()))
                if tgt_debt < debt_val:
                    reserve += debt_val - tgt_debt
                    debt_val = tgt_debt
                _buy_iter = ((nifty_s, px_n, tgt_n, "NIFTY"),
                             (mid_s, px_m, tgt_m, "MIDCAP"),
                             (gold_s, px_g, tgt_gold, "GOLD"))
                for sleeve, px, tgt, fname in _buy_iter:
                    cur = sleeve.value(px)
                    if tgt > cur:
                        spend = tgt - cur
                        sleeve.buy(spend, px, d)
                        reserve -= spend
                        cost += cost_frac * spend
                        if ledger is not None and spend > 1e-9:
                            ledger.append(LedgerRow(
                                date=d, cycle_id="", tier=target_band.name,
                                fund=fname, side="BUY",
                                units=spend / px, price=px, rupees=spend,
                                cum_units_fund=sleeve.units,
                                avg_cost_per_unit_fund=sleeve.avg_cost()))
                if tgt_debt > debt_val:
                    reserve -= tgt_debt - debt_val
                    debt_val = tgt_debt
                _charge(cost, tax_amt)
                held_eq_frac = eff_eq_frac

            # ── Depletion + gross tracking (on the held state) ──
            if held_eq_frac > 1.0 + 1e-9:
                res.days_above_100 += 1
            depleted_now = held_eq_frac >= 1.0 + rob - 1e-9
            if depleted_now and res.depletion_date is None:
                res.depletion_date = d
                res.depletion_dist = dist
                res.max_dist_after_depletion = dist
            if res.depletion_date is not None:
                res.max_dist_after_depletion = min(res.max_dist_after_depletion, dist)

            res.band.append(target_band.name)
        else:
            res.band.append("(warmup)")

        C = (nifty_s.value(px_n) + mid_s.value(px_m)
             + gold_s.value(px_g) + debt_val + reserve)
        res.dates.append(d)
        res.total.append(C)
        res.equity.append(nifty_s.value(px_n) + mid_s.value(px_m))
        res.gold.append(gold_s.value(px_g))
        res.debt.append(debt_val)
        res.reserve.append(reserve)
        if ledger is not None:
            res.nifty_units.append(nifty_s.units)
            res.mid_units.append(mid_s.units)

    return res


def fixed_rebalance(
    dates: list[date], nifty: list[float], midcap: list[float], gold: list[float],
    *, initial: float = 1_000_000.0,
    equity_frac: float = 0.75, debt_frac: float = 0.15, gold_frac: float = 0.10,
    deposit_yield: float = DEPOSIT_YIELD, rebalance_days: int = TRADING_DAYS,
) -> MatrixResult:
    """Fixed equity/debt/gold, rebalanced every `rebalance_days`. The honest
    passive baseline (§5). Fully invested — no standby reserve. Equity is split
    58/42 Nifty/Midcap like the matrix; debt earns deposit_yield; gold rides the
    synthetic INR series. Measured on total capital for a like-for-like compare.
    """
    res = MatrixResult()
    daily_yield = (1 + deposit_yield) ** (1 / TRADING_DAYS) - 1
    nifty_s, mid_s, gold_s = _PriceSleeve(), _PriceSleeve(), _PriceSleeve()
    nifty_s.buy(EQUITY_NIFTY_FRAC * equity_frac * initial, nifty[0], dates[0])
    mid_s.buy((1 - EQUITY_NIFTY_FRAC) * equity_frac * initial, midcap[0], dates[0])
    gold_s.buy(gold_frac * initial, gold[0], dates[0])
    debt_val = debt_frac * initial
    last_rebal = 0

    for i, d in enumerate(dates):
        px_n, px_m, px_g = nifty[i], midcap[i], gold[i]
        if i > 0:
            debt_val *= 1 + daily_yield
        if i > 0 and i - last_rebal >= rebalance_days:
            C = nifty_s.value(px_n) + mid_s.value(px_m) + gold_s.value(px_g) + debt_val
            tgt_n = EQUITY_NIFTY_FRAC * equity_frac * C
            tgt_m = (1 - EQUITY_NIFTY_FRAC) * equity_frac * C
            tgt_g = gold_frac * C
            cash = 0.0
            for sleeve, px, tgt in ((nifty_s, px_n, tgt_n), (mid_s, px_m, tgt_m),
                                    (gold_s, px_g, tgt_g)):
                cur = sleeve.value(px)
                if tgt < cur:
                    p, _ = sleeve.sell_to_value(tgt, px, d, False)
                    cash += p
            cash += max(0.0, debt_val - debt_frac * C)
            debt_val = min(debt_val, debt_frac * C)
            for sleeve, px, tgt in ((nifty_s, px_n, tgt_n), (mid_s, px_m, tgt_m),
                                    (gold_s, px_g, tgt_g)):
                cur = sleeve.value(px)
                if tgt > cur:
                    spend = tgt - cur
                    sleeve.buy(spend, px, d)
                    cash -= spend
            debt_val += cash             # residual back to debt
            last_rebal = i
        C = nifty_s.value(px_n) + mid_s.value(px_m) + gold_s.value(px_g) + debt_val
        res.dates.append(d)
        res.total.append(C)
        res.equity.append(nifty_s.value(px_n) + mid_s.value(px_m))
        res.gold.append(gold_s.value(px_g))
        res.debt.append(debt_val)
        res.reserve.append(0.0)
        res.band.append("fixed")
    return res
