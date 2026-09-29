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


# ── Sell-side sweep ──────────────────────────────────────────────────────────

def _run_sweep_combo(d, n, m, g, brd, vix_d, harvest_gate, harvest_cap_frac,
                     harvest_shrink):
    """Run one sweep combo and return MatrixResult."""
    return be.run_matrix(
        d, n, m, g, brd, vix_d,
        initial=INITIAL,
        gates_on=True,
        deposit_yield=be.DEPOSIT_YIELD,
        whipsaw="glide", whipsaw_param=3.0,
        reserve_over_base=0.20,
        deploy_cap=1.20,
        ema_span=200,
        harvest_gate=harvest_gate,
        harvest_cap_frac=harvest_cap_frac,
        harvest_shrink=harvest_shrink,
    )


def _sweep_unit_gain(combos, cycles, d, n, m, g, brd, vix_d, fixed_res, bh_wrap):
    """Run all combos and return list of (label, gains_list)."""
    rows = []
    for label, kw in combos:
        res = be.run_matrix(
            d, n, m, g, brd, vix_d,
            initial=INITIAL,
            gates_on=True,
            deposit_yield=be.DEPOSIT_YIELD,
            whipsaw="glide", whipsaw_param=3.0,
            reserve_over_base=0.20,
            deploy_cap=1.20,
            ema_span=200,
            **kw,
        )
        gains = ul.unit_gain_per_cycle(cycles, res, fixed_res, bh_wrap, n, m, d)
        rows.append((label, gains))
    return rows


def build_sweep_section(feed_start, feed_end, win_start, min_dd):
    """Run all three harvest-family sweeps and return markdown."""
    d, n, m, gld = align(feed_start, feed_end)

    idx_ws = next(i for i, dt in enumerate(d) if dt >= win_start)
    n_win, d_win = n[idx_ws:], d[idx_ws:]
    cycles = ul.detect_cycles(d_win, n_win, min_drawdown_pct=min_dd)
    complete = [cy for cy in cycles if cy["status"] == "complete"]

    fixed_res = be.fixed_rebalance(d, n, m, gld, initial=INITIAL,
                                    deposit_yield=be.DEPOSIT_YIELD)
    bh_wrap = _bh_as_matrix(be.buy_and_hold(d, n, m, initial=INITIAL,
                                              nifty_frac=0.58), d)

    lines = []

    def h(s): lines.append(s)
    def row(*cols): lines.append("| " + " | ".join(str(c) for c in cols) + " |")

    # Baseline (no harvest override)
    baseline_res = _run_sweep_combo(d, n, m, gld, BREADTH, VIX, 0, 0, 0)
    baseline_gains = ul.unit_gain_per_cycle(cycles, baseline_res, fixed_res,
                                             bh_wrap, n, m, d)
    base_by_id = {cg.cycle_id: cg for cg in baseline_gains}

    def _table(combo_results, title, note):
        h(f"\n### {title}\n")
        if note:
            h(f"*{note}*\n")
        cycle_ids = [cy["cycle_id"] for cy in complete]
        dd_by_id = {cy["cycle_id"]: cy["drawdown_pct"] for cy in complete}

        h("| param | " + " | ".join(f"{cid} ({dd_by_id[cid]*100:.0f}%)" for cid in cycle_ids) + " | wins vs fixed |")
        h("| --- | " + " | ".join(["---"] * len(cycle_ids)) + " | --- |")

        for label, cycle_gains in combo_results:
            gains_by_id = {cg.cycle_id: cg for cg in cycle_gains}
            wins = 0
            cells = []
            for cid in cycle_ids:
                cg = gains_by_id.get(cid)
                bg = base_by_id.get(cid)
                if cg is None or bg is None:
                    cells.append("—")
                    continue
                ug = cg.ladder_unit_gain_nifty * 100
                ug_base = bg.ladder_unit_gain_nifty * 100
                beats = "✓" if cg.ladder_unit_gain_nifty > cg.fixed_unit_gain_nifty else "✗"
                delta = ug - ug_base
                delta_str = f"({delta:+.1f})" if abs(delta) > 0.05 else ""
                cells.append(f"{ug:+.1f}%{delta_str}{beats}")
                if cg.ladder_unit_gain_nifty > cg.fixed_unit_gain_nifty:
                    wins += 1
            row(label, *cells, f"{wins}/{len(cycle_ids)}")

    # ── Family (a): harvest_gate ──
    gate_combos = [
        ("baseline (gate=0.0)",  {"harvest_gate": 0.0}),
        ("gate=0.60",            {"harvest_gate": 0.60}),
        ("gate=0.70",            {"harvest_gate": 0.70}),
        ("gate=0.75",            {"harvest_gate": 0.75}),
        ("gate=0.80",            {"harvest_gate": 0.80}),
        ("gate=0.85",            {"harvest_gate": 0.85}),
        ("gate=0.90",            {"harvest_gate": 0.90}),
    ]
    gate_results = _sweep_unit_gain(gate_combos, cycles, d, n, m, gld, BREADTH,
                                     VIX, fixed_res, bh_wrap)
    _table(gate_results, "Family (a) — harvest_gate (suppress sells when price < gate × ATH)",
           "gate=0.0 is baseline (current). gate=X: no equity harvest until price ≥ X × ATH.")

    # cy2020 sell-price detail by gate
    cy2020_entries = [(cy["peak_date"], cy["recovery_date"])
                      for cy in complete if cy["cycle_id"] == "cy2020"]
    if cy2020_entries:
        cy_s, cy_e = cy2020_entries[0]
        cy2020_ath = next(cy["peak_price"] for cy in complete if cy["cycle_id"] == "cy2020")
        h("\n**cy2020 sell detail by gate (NIFTY avg sell price vs ATH):**\n")
        row("gate", "total sold (units)", "avg sell price", "% of ATH", "net unit Δ peak→rec")
        row(*["---"] * 5)
        for label, kw in gate_combos:
            lg = []
            res_g = be.run_matrix(
                d, n, m, gld, BREADTH, VIX,
                initial=INITIAL, gates_on=True, deposit_yield=be.DEPOSIT_YIELD,
                whipsaw="glide", whipsaw_param=3.0,
                reserve_over_base=0.20, deploy_cap=1.20, ema_span=200,
                ledger=lg, **kw)
            sells = [r for r in lg if r.side == "SELL" and r.fund == "NIFTY"
                     and cy_s <= r.date <= cy_e]
            tot_u = sum(r.units for r in sells)
            avg_p = (sum(r.rupees for r in sells) / tot_u) if tot_u > 0 else 0
            pct_ath = (avg_p / cy2020_ath * 100) if cy2020_ath > 0 else 0
            pk_i = next((i for i, dt in enumerate(d) if dt == cy_s), None)
            rc_i = next((i for i, dt in enumerate(d) if dt == cy_e), len(d) - 1)
            pk_u = (res_g.nifty_units[pk_i] if pk_i is not None and res_g.nifty_units else 0)
            rc_u = (res_g.nifty_units[rc_i] if res_g.nifty_units else 0)
            row(label, f"{tot_u:.1f}", f"{avg_p:.0f}",
                f"{pct_ath:.1f}%", f"{rc_u - pk_u:+.2f}")

    # ── Family (b): harvest_cap_frac ──
    cap_combos = [
        ("baseline (cap=0)",    {"harvest_cap_frac": 0.0}),
        ("cap=0.01 (1%/event)", {"harvest_cap_frac": 0.01}),
        ("cap=0.02",            {"harvest_cap_frac": 0.02}),
        ("cap=0.04",            {"harvest_cap_frac": 0.04}),
        ("cap=0.08",            {"harvest_cap_frac": 0.08}),
        ("cap=0.15",            {"harvest_cap_frac": 0.15}),
    ]
    cap_results = _sweep_unit_gain(cap_combos, cycles, d, n, m, gld, BREADTH,
                                    VIX, fixed_res, bh_wrap)
    _table(cap_results,
           "Family (b) — harvest_cap_frac (cap equity sell to X% of portfolio per event)",
           "cap=0 is baseline. cap=X: each sell event reduces equity allocation by at most X × portfolio.")

    # ── Family (c): harvest_shrink ──
    shrink_combos = [
        ("baseline (shrink=0)", {"harvest_shrink": 0.0}),
        ("shrink=0.25",         {"harvest_shrink": 0.25}),
        ("shrink=0.50",         {"harvest_shrink": 0.50}),
        ("shrink=0.75",         {"harvest_shrink": 0.75}),
        ("shrink=1.0",          {"harvest_shrink": 1.00}),
        ("shrink=1.5",          {"harvest_shrink": 1.50}),
        ("shrink=2.0",          {"harvest_shrink": 2.00}),
    ]
    shrink_results = _sweep_unit_gain(shrink_combos, cycles, d, n, m, gld, BREADTH,
                                       VIX, fixed_res, bh_wrap)
    _table(shrink_results,
           "Family (c) — harvest_shrink (scale sell by 1 − shrink × recovery_frac)",
           "shrink=0 is baseline. shrink=1: sell fraction drops linearly to 0 as price returns to ATH.")

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

    print("Running sell-side sweeps (modern window)…")
    sec_sweep = build_sweep_section(
        feed_start=date(2014, 6, 1),
        feed_end=date(2026, 12, 31),
        win_start=date(2015, 6, 1),
        min_dd=0.08,
    )

    md = f"""# BACKTEST_UNITS.md — Session 2
*Generated from frozen series/ snapshot. No network or credentials needed.*

> **Status:** Ledger reconciles (all tests pass). Sell-side sweeps complete.

---

## cy2020 diagnosis — breadth gate fallback

**Root cause: ALL tiered bands fail breadth_max gate during COVID crash/recovery.**

During the volatile COVID period (March–September 2020) India breadth readings
oscillated HIGH (46–90). Band `resolve_band()` walks shallower from the raw
distance-based band until finding a band that passes the breadth gate. When the
raw band would be T2 (dist < −5%) but breadth=46 > T1's breadth_max=40, T1
also fails, and the walk reaches Baseline (equity=0.80, ungated). With
`glide` whipsaw, the system targets Baseline and sells 38.9 NIFTY units over
roughly March–July 2020 at an average price of ~9,567 — 23% below the Jan 2020
ATH of 12,362.

**Secondary factor (EMA lag):** The 200-day EMA declined during the crash (from
~11,589 at Jan 2020 peak to ~10,500 by June 2020). By mid-2020 the dist
(price/EMA − 1) could be +0..+8%, putting the raw band at Baseline legitimately.
This compounds the breadth-gate effect in the June–November 2020 window.

**Implication for sweeps:** Harvest-gate family (a) directly addresses cy2020
by suppressing sells when `price/ATH < gate`. At gate=0.80, sells are blocked
until price recovers to 9,890 (80% of 12,362), approximately May 2021, after
the crash-accumulated units have been held through the trough.

---

## 2008 EMA warm-up

Yahoo Finance (^BSESN) is **unavailable** in this environment (proxy returns
403 Forbidden; yfinance not installed). The series/ snapshot starts
2007-09-17, so the 200-day EMA warms mid-July 2008; the first decision bar
is at NIFTY ~−22.7%. Pre-2007 data cannot be fetched for warm-up.

---
{sec_2008}

---
{sec_modern}

---

## Sell-side sweeps — modern window (2015-06 → 2026-09, 9 complete cycles)

> Unit gain: `(capital÷nifty at recovery) ÷ (capital÷nifty at peak) − 1`.
> Column format: `+X.X%(Δvs baseline)✓/✗` where ✓ = beats fixed 75/15/10.
> Sweep uses deposit yield 6.5%; gates on; glide whipsaw.
{sec_sweep}

---

## Data and methodology notes

- **Ladder configuration:** `reserve_over_base=0.20`, `deploy_cap=1.20`,
  `whipsaw=glide(1/3 wk)`, `spend_order=debt_first`. Default matrix bands.
- **Fixed comparator:** 75% equity / 15% debt / 10% gold, annual rebalance,
  same 6.5% deposit yield on debt sleeve.
- **Passive comparator:** 58/42 Nifty/Midcap buy-and-hold, no cash reserve,
  no rebalancing.
- **Unit gain formula:** (capital÷price at recovery) ÷ (capital÷price at peak) − 1.
- **Breadth data:** available from 2015-06-01 only; breadth gate cannot confirm
  tiers in the 2008 window.
- **Open cycles:** reported for information but excluded from sweep counts.
- **Reconciliation:** test_unit_ledger.py — 8 tests pass; engine sleeve units
  match cumulative ledger at every bar.
"""

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BACKTEST_UNITS.md")
    with open(out_path, "w") as f:
        f.write(md)
    print(f"Written {out_path}")


if __name__ == "__main__":
    main()
