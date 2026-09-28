"""Unit-ledger helpers for the Session 2 spec (BACKTEST_SPEC_SESSION2.md).

Provides:
  detect_cycles()       — find peak→trough→recovery cycles in a price series
  annotate_cycles()     — stamp cycle_id on LedgerRow objects
  per_cycle_summary()   — per-cycle unit buy/sell/net table
  unit_gain_per_cycle() — ladder vs comparators at equal-price checkpoints
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import backtest_engine as be


# ── Cycle detection ──────────────────────────────────────────────────────────

def detect_cycles(
    dates: list[date],
    prices: list[float],
    min_drawdown_pct: float = 0.08,
) -> list[dict]:
    """Find market cycles: all-time-high peak → trough → recovery.

    A cycle begins at an all-time high and ends when the price first returns
    to (or above) that peak level.  Only cycles whose drawdown >=
    min_drawdown_pct are returned.  A cycle that has not yet recovered is
    included with status='open'.

    Returns a list of dicts with keys:
      cycle_id, peak_date, peak_price, peak_idx,
      trough_date, trough_price, trough_idx,
      recovery_date, recovery_idx (None if open),
      drawdown_pct, status ('complete' | 'open')
    """
    n = len(prices)
    if n == 0:
        return []

    # Find every index that sets a new all-time high.
    ath_indices: list[int] = []
    running_max = float("-inf")
    for i, p in enumerate(prices):
        if p > running_max:
            running_max = p
            ath_indices.append(i)

    cycles: list[dict] = []
    year_seen: dict[int, int] = {}

    for k, ath_i in enumerate(ath_indices):
        peak_price = prices[ath_i]
        peak_date = dates[ath_i]

        # Segment: from this ATH to just before the next ATH (or end of series)
        end_i = ath_indices[k + 1] if k + 1 < len(ath_indices) else n
        seg = prices[ath_i:end_i]
        if len(seg) <= 1:
            continue

        # Trough is the minimum in this segment
        trough_rel = min(range(len(seg)), key=lambda x: seg[x])
        trough_abs = ath_i + trough_rel
        trough_price = prices[trough_abs]
        trough_date = dates[trough_abs]

        drawdown = (trough_price - peak_price) / peak_price  # negative
        if abs(drawdown) < min_drawdown_pct:
            continue

        # Recovery: first close >= peak_price AFTER the trough
        recovery_date: date | None = None
        recovery_idx: int | None = None
        for j in range(trough_abs, n):
            if prices[j] >= peak_price:
                recovery_date = dates[j]
                recovery_idx = j
                break

        yr = peak_date.year
        year_seen[yr] = year_seen.get(yr, 0) + 1
        sfx = chr(ord("a") + year_seen[yr] - 1) if year_seen[yr] > 1 else ""
        cycle_id = f"cy{yr}{sfx}"

        cycles.append({
            "cycle_id": cycle_id,
            "peak_date": peak_date,
            "peak_price": peak_price,
            "peak_idx": ath_i,
            "trough_date": trough_date,
            "trough_price": trough_price,
            "trough_idx": trough_abs,
            "recovery_date": recovery_date,
            "recovery_idx": recovery_idx,
            "drawdown_pct": drawdown,
            "status": "complete" if recovery_date is not None else "open",
        })

    return cycles


# ── Cycle annotation ─────────────────────────────────────────────────────────

def annotate_cycles(
    rows: list[be.LedgerRow],
    cycles: list[dict],
) -> None:
    """Stamp cycle_id on each LedgerRow in-place.

    A row is assigned to the cycle whose [peak_date, recovery_date) interval
    contains the row's date.  Rows outside all cycles keep cycle_id="".
    For open cycles the interval is [peak_date, ∞).
    """
    for row in rows:
        for cy in cycles:
            start = cy["peak_date"]
            end = cy["recovery_date"]  # None for open
            if row.date >= start and (end is None or row.date <= end):
                row.cycle_id = cy["cycle_id"]
                break


# ── Per-cycle unit summary ───────────────────────────────────────────────────

@dataclass
class CycleSummary:
    cycle_id: str
    status: str                 # 'complete' | 'open'
    peak_date: date
    peak_price: float           # Nifty close at peak (cycle is Nifty-defined)
    trough_date: date
    trough_price: float         # Nifty close at trough
    drawdown_pct: float
    recovery_date: date | None

    # Same-date prices for the equity funds (to compare buy prices correctly)
    midcap_price_at_peak: float = 0.0
    midcap_price_at_trough: float = 0.0

    # Units held at the peak date (before drawdown)
    nifty_units_at_peak: float = 0.0
    mid_units_at_peak: float = 0.0

    # Units held at recovery (or end if open)
    nifty_units_at_recovery: float = 0.0
    mid_units_at_recovery: float = 0.0

    # Buy activity during the cycle
    buys: list[dict] = field(default_factory=list)    # {tier, fund, units, avg_px, rupees}
    sells: list[dict] = field(default_factory=list)   # {tier, fund, units, avg_px, rupees}

    # Aggregate averages
    avg_buy_price_nifty: float = 0.0
    avg_buy_price_mid: float = 0.0
    avg_sell_price_nifty: float = 0.0
    avg_sell_price_mid: float = 0.0

    total_buy_rupees_nifty: float = 0.0
    total_buy_rupees_mid: float = 0.0
    total_sell_rupees_nifty: float = 0.0
    total_sell_rupees_mid: float = 0.0

    total_buy_units_nifty: float = 0.0
    total_buy_units_mid: float = 0.0
    total_sell_units_nifty: float = 0.0
    total_sell_units_mid: float = 0.0


def _idx_for_date(dates: list[date], d: date) -> int | None:
    """Binary-search for the last index whose date <= d."""
    lo, hi = 0, len(dates) - 1
    result = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= d:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def per_cycle_summary(
    rows: list[be.LedgerRow],
    cycles: list[dict],
    result: be.MatrixResult,
    midcap_prices: list[float] | None = None,
) -> list[CycleSummary]:
    """Build a CycleSummary for each cycle.

    `result` must have been produced by run_matrix(..., ledger=rows) so that
    result.nifty_units and result.mid_units are populated.
    `midcap_prices` (aligned to result.dates) is used to store the fund-level
    price at the peak/trough dates for correct buy-price comparison.
    """
    summaries = []
    dates = result.dates

    for cy in cycles:
        cs = CycleSummary(
            cycle_id=cy["cycle_id"],
            status=cy["status"],
            peak_date=cy["peak_date"],
            peak_price=cy["peak_price"],
            trough_date=cy["trough_date"],
            trough_price=cy["trough_price"],
            drawdown_pct=cy["drawdown_pct"],
            recovery_date=cy["recovery_date"],
        )

        # Units and fund prices at peak
        peak_i = _idx_for_date(dates, cy["peak_date"])
        if peak_i is not None:
            if result.nifty_units:
                cs.nifty_units_at_peak = result.nifty_units[peak_i]
                cs.mid_units_at_peak   = result.mid_units[peak_i]
            if midcap_prices is not None:
                cs.midcap_price_at_peak = midcap_prices[peak_i]
        trough_i = _idx_for_date(dates, cy["trough_date"])
        if trough_i is not None and midcap_prices is not None:
            cs.midcap_price_at_trough = midcap_prices[trough_i]

        # Units at recovery (or end of series for open cycles)
        if cy["recovery_date"] is not None:
            rec_i = _idx_for_date(dates, cy["recovery_date"])
        else:
            rec_i = len(dates) - 1
        if rec_i is not None and result.nifty_units:
            cs.nifty_units_at_recovery = result.nifty_units[rec_i]
            cs.mid_units_at_recovery = result.mid_units[rec_i]

        # Aggregate ledger rows for this cycle
        cycle_rows = [r for r in rows if r.cycle_id == cy["cycle_id"]]

        tier_buy: dict[tuple, list] = {}   # (tier, fund) -> list of rows
        tier_sell: dict[tuple, list] = {}

        for r in cycle_rows:
            key = (r.tier, r.fund)
            if r.side == "BUY":
                tier_buy.setdefault(key, []).append(r)
                if r.fund == "NIFTY":
                    cs.total_buy_units_nifty += r.units
                    cs.total_buy_rupees_nifty += r.rupees
                elif r.fund == "MIDCAP":
                    cs.total_buy_units_mid += r.units
                    cs.total_buy_rupees_mid += r.rupees
            else:
                tier_sell.setdefault(key, []).append(r)
                if r.fund == "NIFTY":
                    cs.total_sell_units_nifty += r.units
                    cs.total_sell_rupees_nifty += r.rupees
                elif r.fund == "MIDCAP":
                    cs.total_sell_units_mid += r.units
                    cs.total_sell_rupees_mid += r.rupees

        for (tier, fund), rlist in sorted(tier_buy.items()):
            tot_u = sum(r.units for r in rlist)
            tot_r = sum(r.rupees for r in rlist)
            cs.buys.append({"tier": tier, "fund": fund, "units": tot_u,
                            "avg_px": tot_r / tot_u if tot_u > 0 else 0,
                            "rupees": tot_r})

        for (tier, fund), rlist in sorted(tier_sell.items()):
            tot_u = sum(r.units for r in rlist)
            tot_r = sum(r.rupees for r in rlist)
            cs.sells.append({"tier": tier, "fund": fund, "units": tot_u,
                             "avg_px": tot_r / tot_u if tot_u > 0 else 0,
                             "rupees": tot_r})

        if cs.total_buy_units_nifty > 0:
            cs.avg_buy_price_nifty = cs.total_buy_rupees_nifty / cs.total_buy_units_nifty
        if cs.total_buy_units_mid > 0:
            cs.avg_buy_price_mid = cs.total_buy_rupees_mid / cs.total_buy_units_mid
        if cs.total_sell_units_nifty > 0:
            cs.avg_sell_price_nifty = cs.total_sell_rupees_nifty / cs.total_sell_units_nifty
        if cs.total_sell_units_mid > 0:
            cs.avg_sell_price_mid = cs.total_sell_rupees_mid / cs.total_sell_units_mid

        summaries.append(cs)

    return summaries


# ── Unit gain per cycle ──────────────────────────────────────────────────────

@dataclass
class CycleGain:
    cycle_id: str
    status: str
    peak_date: date
    recovery_date: date | None
    drawdown_pct: float

    # Ladder
    ladder_unit_gain_nifty: float = 0.0
    ladder_unit_gain_mid: float = 0.0
    ladder_nifty_at_peak: float = 0.0
    ladder_nifty_at_recovery: float = 0.0
    ladder_mid_at_peak: float = 0.0
    ladder_mid_at_recovery: float = 0.0

    # Fixed 75/15/10 comparator (no-deposit variant available separately)
    fixed_unit_gain_nifty: float = 0.0
    fixed_unit_gain_mid: float = 0.0

    # Passive 58/42 buy-and-hold
    passive_unit_gain_nifty: float = 0.0
    passive_unit_gain_mid: float = 0.0


def unit_gain_per_cycle(
    cycles: list[dict],
    ladder_result: be.MatrixResult,
    fixed_result: be.MatrixResult,
    passive_result: be.MatrixResult,
    nifty_prices: list[float],
    midcap_prices: list[float],
    dates: list[date],
) -> list[CycleGain]:
    """Compute unit gain per cycle for ladder vs comparators.

    Unit gain = (capital_at_recovery / price_at_recovery) /
                (capital_at_peak    / price_at_peak)    - 1

    Since recovery price >= peak price, this is an approximation; we use the
    recovery-date price for both ends (a small upward bias in the denominator
    would slightly understate the gain, so we use each date's own price).

    For the ladder we can also look at actual equity units held (nifty_units,
    mid_units) from the run.  For fixed/passive, capital/price is the only lens
    (they don't have per-sleeve unit tracking in MatrixResult by default).
    """
    gains = []

    for cy in cycles:
        if cy["status"] == "open":
            continue

        peak_i = _idx_for_date(dates, cy["peak_date"])
        rec_i = _idx_for_date(dates, cy["recovery_date"])
        if peak_i is None or rec_i is None:
            continue

        px_peak_n = nifty_prices[peak_i]
        px_rec_n = nifty_prices[rec_i]
        px_peak_m = midcap_prices[peak_i]
        px_rec_m = midcap_prices[rec_i]

        def _gain(res, px_pk, px_rc, peak_idx, rec_idx):
            cap_pk = res.total[peak_idx] if hasattr(res, "total") else res.equity[peak_idx]
            cap_rc = res.total[rec_idx] if hasattr(res, "total") else res.equity[rec_idx]
            if cap_pk <= 0 or px_pk <= 0 or px_rc <= 0:
                return 0.0
            return (cap_rc / px_rc) / (cap_pk / px_pk) - 1.0

        cg = CycleGain(
            cycle_id=cy["cycle_id"],
            status=cy["status"],
            peak_date=cy["peak_date"],
            recovery_date=cy["recovery_date"],
            drawdown_pct=cy["drawdown_pct"],
        )

        cg.ladder_unit_gain_nifty = _gain(ladder_result, px_peak_n, px_rec_n, peak_i, rec_i)
        cg.ladder_unit_gain_mid   = _gain(ladder_result, px_peak_m, px_rec_m, peak_i, rec_i)
        cg.fixed_unit_gain_nifty  = _gain(fixed_result,  px_peak_n, px_rec_n, peak_i, rec_i)
        cg.fixed_unit_gain_mid    = _gain(fixed_result,  px_peak_m, px_rec_m, peak_i, rec_i)
        cg.passive_unit_gain_nifty = _gain(passive_result, px_peak_n, px_rec_n, peak_i, rec_i)
        cg.passive_unit_gain_mid   = _gain(passive_result, px_peak_m, px_rec_m, peak_i, rec_i)

        # Ladder actual equity units (from sleeve tracking)
        if ladder_result.nifty_units:
            cg.ladder_nifty_at_peak     = ladder_result.nifty_units[peak_i]
            cg.ladder_nifty_at_recovery = ladder_result.nifty_units[rec_i]
            cg.ladder_mid_at_peak       = ladder_result.mid_units[peak_i]
            cg.ladder_mid_at_recovery   = ladder_result.mid_units[rec_i]

        gains.append(cg)

    return gains
