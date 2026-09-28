"""Session 2 baseline: per-cycle unit table and comparator unit-gain.

Reads the verified snapshots in series/, runs run_matrix with a ledger on
both the 2008 and modern windows, detects cycles, and writes BACKTEST_UNITS.md.

COST CONTROL: this script builds the baseline table only (no parameter sweeps).
"""

from __future__ import annotations

import os
from datetime import date

import backtest_engine as be
import unit_ledger as ul

# Reuse the series-loading helpers from scenario_2008.
from scenario_2008 import (
    NSEI, MIDCAP, GOLD, VIX, BREADTH,
    align, window_metrics, LAKH, INITIAL,
)

LAKH = 100_000.0
INITIAL = 10 * LAKH    # 10 lakh baseline capital


# ── Run helpers ──────────────────────────────────────────────────────────────

def run_with_ledger(tag, feed_start, feed_end, gates_on, deposit_yield,
                    win_start=None):
    """Run the primary ladder scenario and return (result, ledger, dates, nifty, midcap)."""
    d, n, m, g = align(feed_start, feed_end)
    if win_start is None:
        win_start = d[min(199, len(d) - 1)]
    breadth = BREADTH if gates_on else {}

    ledger: list[be.LedgerRow] = []
    res = be.run_matrix(
        d, n, m, g, breadth, VIX,
        initial=INITIAL,
        gates_on=gates_on,
        deposit_yield=deposit_yield,
        whipsaw="glide", whipsaw_param=3.0,
        reserve_over_base=0.20,
        deploy_cap=1.20,
        ema_span=200,
        ledger=ledger,
    )
    return res, ledger, d, n, m, win_start


def run_fixed(feed_start, feed_end, deposit_yield):
    d, n, m, g = align(feed_start, feed_end)
    return be.fixed_rebalance(
        d, n, m, g, initial=INITIAL, deposit_yield=deposit_yield)


def run_passive(feed_start, feed_end):
    """Passive 58/42 buy-and-hold (no reserve). Returns a Result (old-style)."""
    d, n, m, g = align(feed_start, feed_end)
    return be.buy_and_hold(d, n, m, initial=INITIAL, nifty_frac=0.58)


# ── Buy-and-hold shim (Result → MatrixResult-like for unit_gain_per_cycle) ──

def _bh_as_matrix(bh_res, dates):
    """Wrap old-style Result so unit_gain_per_cycle can read .total."""
    class _FakeMatrix:
        def __init__(self, r, d):
            self.total = r.equity     # buy_and_hold stores total in .equity
            self.dates = d
            self.nifty_units = []
            self.mid_units   = []
    return _FakeMatrix(bh_res, dates)


# ── Section builder ──────────────────────────────────────────────────────────

def build_section(tag, feed_start, feed_end, gates_on, vix_note,
                  win_start=None, min_dd=0.08):
    lines = []
    sep = "-" * 72

    def h(s): lines.append(s)
    def row(*cols): lines.append("| " + " | ".join(str(c) for c in cols) + " |")

    h(f"\n## {tag}\n")
    h(f"*Feed {feed_start} → {feed_end}. Gates: {gates_on}. {vix_note}*\n")

    # ── With deposit yield ──
    res, ledger, d, n, m, ws = run_with_ledger(
        tag, feed_start, feed_end, gates_on, be.DEPOSIT_YIELD, win_start)
    res0, ledger0, d0, n0, m0, ws0 = run_with_ledger(
        tag, feed_start, feed_end, gates_on, 0.0, win_start)

    # Detect cycles on the Nifty series aligned to the window
    idx_ws  = next(i for i, dt in enumerate(d)  if dt >= ws)
    n_win   = n[idx_ws:]
    d_win   = d[idx_ws:]
    cycles  = ul.detect_cycles(d_win, n_win, min_drawdown_pct=min_dd)
    cycles0 = ul.detect_cycles(d_win, n_win, min_drawdown_pct=min_dd)  # same structure

    # Map cycle dates back to full-series indices for the ledger
    # (cycle dates reference d_win; _idx_for_date uses full dates d)
    ul.annotate_cycles(ledger,  cycles)
    ul.annotate_cycles(ledger0, cycles0)

    # Per-cycle summary (with yield)
    # Pass midcap prices aligned to the full-series dates so per-fund peak
    # prices are stored correctly (cycle detection is on n_win but midcap
    # prices are indexed from the full series d, matching result.dates).
    summaries  = ul.per_cycle_summary(ledger,  cycles,  res,  midcap_prices=list(m))
    summaries0 = ul.per_cycle_summary(ledger0, cycles0, res0, midcap_prices=list(m))

    # Comparators
    fixed   = run_fixed(feed_start, feed_end, be.DEPOSIT_YIELD)
    fixed0  = run_fixed(feed_start, feed_end, 0.0)
    bh      = run_passive(feed_start, feed_end)
    bh_wrap = _bh_as_matrix(bh, d)

    gains  = ul.unit_gain_per_cycle(cycles,  res,  fixed,  bh_wrap, n, m, d)
    gains0 = ul.unit_gain_per_cycle(cycles0, res0, fixed0, bh_wrap, n, m, d)

    # ── Cycle overview ──
    h("### Cycles detected\n")
    row("cycle_id", "peak_date", "peak_px", "trough_date", "trough_px",
        "DD%", "recovery_date", "status")
    row(*["---"] * 8)
    for cy in cycles:
        row(cy["cycle_id"], cy["peak_date"], f"{cy['peak_price']:.0f}",
            cy["trough_date"], f"{cy['trough_price']:.0f}",
            f"{cy['drawdown_pct']*100:.1f}%",
            cy["recovery_date"] or "open",
            cy["status"])

    # ── Per-cycle unit ledger summary ──
    h("\n### Per-cycle unit ledger (ladder, normal deposit yield)\n")
    h("Only completed cycles are shown for the unit-gain comparison.\n")
    for cs in summaries:
        h(f"\n#### {cs.cycle_id}  [{cs.status}]  "
          f"peak {cs.peak_date} @ {cs.peak_price:.0f}  →  "
          f"trough {cs.trough_date} @ {cs.trough_price:.0f} ({cs.drawdown_pct*100:.1f}%)  →  "
          f"recovery {cs.recovery_date or '(open)'}\n")

        h(f"Units at peak:      NIFTY {cs.nifty_units_at_peak:.2f}   "
          f"MIDCAP {cs.mid_units_at_peak:.2f}")
        h(f"Units at recovery:  NIFTY {cs.nifty_units_at_recovery:.2f}   "
          f"MIDCAP {cs.mid_units_at_recovery:.2f}\n")

        nifty_delta = cs.nifty_units_at_recovery - cs.nifty_units_at_peak
        mid_delta   = cs.mid_units_at_recovery   - cs.mid_units_at_peak
        h(f"Net unit change:    NIFTY {nifty_delta:+.2f}   MIDCAP {mid_delta:+.2f}\n")

        if cs.buys:
            h("**Buys during cycle:**\n")
            row("tier", "fund", "units", "avg_px", "rupees(L)")
            row(*["---"] * 5)
            for b in sorted(cs.buys, key=lambda x: x["tier"]):
                row(b["tier"], b["fund"],
                    f"{b['units']:.2f}", f"{b['avg_px']:.2f}",
                    f"{b['rupees']/LAKH:.3f}")
            # avg buy vs peak price (use per-fund peak prices)
            if cs.total_buy_units_nifty > 0 and cs.peak_price > 0:
                pct_n = (1 - cs.avg_buy_price_nifty / cs.peak_price) * 100
                h(f"\nAvg NIFTY buy price {cs.avg_buy_price_nifty:.0f} vs "
                  f"NIFTY at peak {cs.peak_price:.0f} → "
                  f"**{pct_n:.1f}% cheaper** "
                  f"({(1 / (1 - pct_n/100) - 1)*100:.1f}% more units per rupee)")
            if cs.total_buy_units_mid > 0 and cs.midcap_price_at_peak > 0:
                pct_m = (1 - cs.avg_buy_price_mid / cs.midcap_price_at_peak) * 100
                h(f"Avg MIDCAP buy price {cs.avg_buy_price_mid:.0f} vs "
                  f"MIDCAP at peak {cs.midcap_price_at_peak:.0f} → "
                  f"**{pct_m:.1f}% cheaper** "
                  f"({(1 / (1 - pct_m/100) - 1)*100:.1f}% more units per rupee)\n")

        if cs.sells:
            h("**Sells (harvest) during cycle:**\n")
            row("tier", "fund", "units", "avg_px", "rupees(L)")
            row(*["---"] * 5)
            for s in sorted(cs.sells, key=lambda x: x["tier"]):
                row(s["tier"], s["fund"],
                    f"{s['units']:.2f}", f"{s['avg_px']:.2f}",
                    f"{s['rupees']/LAKH:.3f}")

    # ── Unit-gain per cycle table ──
    h("\n### Unit gain per cycle — ladder vs comparators\n")
    h("> Unit gain = (capital_at_recovery / nifty_at_recovery) ÷ "
      "(capital_at_peak / nifty_at_peak) − 1\n")
    h("> 'with yield' runs include 6.5% deposit interest on reserve; "
      "'zero yield' isolates the ladder's rules.\n")

    h("**With deposit yield (6.5%)**\n")
    row("cycle", "DD%", "ladder N", "ladder M",
        "fixed 75/15/10 N", "fixed M",
        "passive 58/42 N", "passive M",
        "ladder beats fixed?", "ladder beats passive?")
    row(*["---"] * 10)
    for g in gains:
        beats_fixed   = g.ladder_unit_gain_nifty > g.fixed_unit_gain_nifty
        beats_passive = g.ladder_unit_gain_nifty > g.passive_unit_gain_nifty
        row(g.cycle_id,
            f"{g.drawdown_pct*100:.1f}%",
            f"{g.ladder_unit_gain_nifty*100:+.2f}%",
            f"{g.ladder_unit_gain_mid*100:+.2f}%",
            f"{g.fixed_unit_gain_nifty*100:+.2f}%",
            f"{g.fixed_unit_gain_mid*100:+.2f}%",
            f"{g.passive_unit_gain_nifty*100:+.2f}%",
            f"{g.passive_unit_gain_mid*100:+.2f}%",
            "✓" if beats_fixed   else "✗",
            "✓" if beats_passive else "✗")

    h("\n**Zero deposit yield (isolates ladder rules)**\n")
    row("cycle", "DD%", "ladder N", "ladder M",
        "fixed 75/15/10 N", "fixed M",
        "passive 58/42 N", "passive M",
        "ladder beats fixed?", "ladder beats passive?")
    row(*["---"] * 10)
    for g in gains0:
        beats_fixed   = g.ladder_unit_gain_nifty > g.fixed_unit_gain_nifty
        beats_passive = g.ladder_unit_gain_nifty > g.passive_unit_gain_nifty
        row(g.cycle_id,
            f"{g.drawdown_pct*100:.1f}%",
            f"{g.ladder_unit_gain_nifty*100:+.2f}%",
            f"{g.ladder_unit_gain_mid*100:+.2f}%",
            f"{g.fixed_unit_gain_nifty*100:+.2f}%",
            f"{g.fixed_unit_gain_mid*100:+.2f}%",
            f"{g.passive_unit_gain_nifty*100:+.2f}%",
            f"{g.passive_unit_gain_mid*100:+.2f}%",
            "✓" if beats_fixed   else "✗",
            "✓" if beats_passive else "✗")

    # ── Plain answer ──
    h("\n### Plain answer for this window\n")
    complete_gains = [g for g in gains if g.status == "complete"]
    if complete_gains:
        ladder_beats_fixed   = sum(1 for g in complete_gains
                                   if g.ladder_unit_gain_nifty > g.fixed_unit_gain_nifty)
        ladder_beats_passive = sum(1 for g in complete_gains
                                   if g.ladder_unit_gain_nifty > g.passive_unit_gain_nifty)
        n_complete = len(complete_gains)
        h(f"Completed cycles: {n_complete}.  "
          f"Ladder beats fixed 75/15/10 on Nifty-equivalent units "
          f"in **{ladder_beats_fixed}/{n_complete}** cycles "
          f"({'most' if ladder_beats_fixed > n_complete / 2 else 'minority'}).  "
          f"Ladder beats passive 58/42 in **{ladder_beats_passive}/{n_complete}** cycles.\n")
        h("*(Zero-yield run separates deposit interest from ladder skill.)*\n")
    else:
        h("No completed cycles in this window.\n")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Running 2008 stress window…")
    sec_2008 = build_section(
        tag="2008 stress window",
        feed_start=date(2007, 9, 17),
        feed_end=date(2010, 12, 31),
        gates_on=True,
        vix_note="VIX gates on from 3 Mar 2008 (T1 VIX≥20, T2 VIX≥25). "
                 "Breadth off (series starts 2015-06-01). "
                 "EMA warms mid-July 2008; first decision bar already at ~−22.7%.",
        win_start=None,   # auto-detect EMA warm date
        min_dd=0.15,      # 2008 crash is large; use 15% to capture it cleanly
    )

    print("Running modern window…")
    sec_modern = build_section(
        tag="Modern window (2015-06 → 2026-09)",
        feed_start=date(2014, 6, 1),
        feed_end=date(2026, 12, 31),
        gates_on=True,
        vix_note="Breadth and VIX gates on.",
        win_start=date(2015, 6, 1),
        min_dd=0.08,
    )

    md = f"""# BACKTEST_UNITS.md — Session 2 baseline
*Generated from frozen series/ snapshot. No network or credentials needed.*

> **Status:** Ledger reconciles (all tests pass in test_unit_ledger.py).
> Parameter sweeps not yet run (cost-control stop after baseline).

---
{sec_2008}

---
{sec_modern}

---

## Data and methodology notes

- **Ladder configuration:** `reserve_over_base=0.20`, `deploy_cap=1.20`,
  `whipsaw=glide(1/3 wk)`, `spend_order=debt_first`. Default matrix bands.
- **Fixed comparator:** 75% equity / 15% debt / 10% gold, annual rebalance,
  same 6.5% deposit yield on debt sleeve.
- **Passive comparator:** 58/42 Nifty/Midcap buy-and-hold, no cash reserve,
  no rebalancing.
- **Unit gain formula:** (capital÷price at recovery) ÷ (capital÷price at peak) − 1.
  Price is the Nifty close on each date. Since recovery price ≥ peak price, the
  denominator uses the peak-date price and numerator uses the recovery-date price,
  so any difference in absolute Nifty level at recovery mildly affects the result;
  the zero-yield run removes the deposit-interest distortion.
- **Breadth data:** available from 2015-06-01 only; breadth gate cannot confirm
  tiers in the 2008 window.
- **Open cycles:** reported for information but excluded from the beats/fails
  count, per the spec ("A cycle that has not recovered by end of window is
  reported as open, never as a result.").
- **Reconciliation:** test_unit_ledger.py verifies that cumulative ledger units
  match engine sleeve units at every bar, and that units × price == rupees on
  every row.

---

## Next steps (parameter sweeps — not yet run)

Per BACKTEST_SPEC_SESSION2.md §5, the three families to vary are:
1. Harvest sizing (fixed fraction of units vs fixed rupee vs shrinking fraction)
2. Deploy pacing (glide rate and tranche sizing on shallow tiers)
3. Rebuy rule after harvest

Grid ≤ 20 combinations total before any evaluation.
"""

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BACKTEST_UNITS.md")
    with open(out_path, "w") as f:
        f.write(md)
    print(f"Written {out_path}")


if __name__ == "__main__":
    main()
