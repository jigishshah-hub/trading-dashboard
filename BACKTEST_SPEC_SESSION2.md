# Session 2 spec — unit-accumulation test of the Tactical Ladder

Status: design agreed 29 Sep 2026. Builds on Session 1 (PR #2 must be merged
into `main` first: it provides the matrix engine in `backtest_engine.py`,
`scenario_2008.py`, `fetch_series.py` and the frozen `series/` snapshot).

---

## 1. Objective

> Measure whether the ladder accumulates more **units** than a passive holding
> over each market cycle, using a unit ledger that records every buy and every
> sell. Then find the pacing and harvest rules that maximise net units per
> cycle without giving up the drawdown protection.

Why units, not rupee CAGR: the ladder exists to buy more units when they are
cheap and sell fewer when they are dear. Session 1 scored total rupee value
against fixed 75/15/10 and the ladder trailed it, but that comparison is
dominated by idle-cash drag in rising markets, which is not what the rules are
for. Session 1 never tracked units at all.

Calibration, so the unit lens is not oversold: at the same date and the same
price, "capital ÷ price" ranks strategies identically to rupee value. Units only
change the verdict when (a) compared at equal-price checkpoints, which removes
market drift, or (b) the actual unit path is examined. Both are required here.

---

## 2. The unit ledger (build this first, before any tuning)

Every trade, in every scenario, writes one row:

`date, cycle_id, tier, fund (NIFTY|MIDCAP), side (BUY|SELL), units, price,
rupees, cum_units_fund, avg_cost_per_unit_fund`

Per cycle, report:

- Units bought at each tier, with the average price paid.
- Units sold at each harvest tier, with the average price received.
- Net units per fund at the end of the cycle.
- Average buy price against the cycle-peak price (buying at -20% from the peak
  is 25% more units per rupee).

The ledger must reconcile: units held at any date equals the sum of the ledger
up to that date, and rupees in equals rupees out plus change in holdings. Add a
test that fails if it does not.

---

## 3. Primary metric: net units per cycle

- **Cycle:** peak = running maximum Nifty close; the cycle runs from that peak
  through the trough to the first close at or above the peak. A cycle that has
  not recovered by the end of the window is reported as open, never as a result.
- **Unit gain per cycle** = (total capital at recovery ÷ price) ÷ (total capital
  at peak ÷ price) − 1, computed separately in Nifty units and Midcap units.
  Since price is the same at both ends, this is the pure effect of the rules.
- Also report equity units held at recovery against equity units held at the
  peak, per fund.
- **Isolate the rules from deposit interest:** run every headline with
  `DEPOSIT_YIELD = 0` alongside the normal run. Cash interest earned while
  waiting must not be credited as ladder skill.
- **Comparators at the same dates:** fixed 75/15/10 rebalanced annually, and
  passive buy-and-hold 58/42 with no reserve.
- Return-to-drawdown stays as a secondary check, not the target.

Capital accounting is unchanged from Session 1: the standby reserve is part of
total capital from day one, no MTF, no interest cost, XIRR reported next to
CAGR.

---

## 4. Windows

- **2008:** extend to about 31 Dec 2010 so it includes recovery to the Jan 2008
  peak (Session 1 ended Mar 2010, before any cycle completed). Turn the **India
  VIX gates on** from 3 Mar 2008 (T1 VIX>=20, T2 VIX>=25). Only breadth stays
  off, because the series starts 2015-06-01. State this in the report.
- **Modern:** 2015-06 to 2026-09 with breadth and VIX gates on. Report cycles
  separately: 2016, 2018, 2020, 2022, 2026. No blended average across cycles.
- Data limits carry over: the 200 EMA only warms in mid-July 2008, so the first
  decision bar already reads about -22.7%. Do not start earlier than the EMA
  allows.

---

## 5. What to vary (small families only)

1. **Harvest sizing.** First read how the matrix engine currently sizes sells
   and record it. The old tranche engine sold a fixed fraction of units
   (25/50/75%). Compare:
   - fixed fraction of units (current);
   - fixed rupee amount per harvest tier, so fewer units are sold as price
     rises (the stated intent);
   - a fraction that shrinks as distance above the EMA grows.
2. **Deploy pacing.** Start from the Session 1 glide (about 1/3 of the gap per
   week). Vary the glide rate and test tranche sizing on the shallow tiers.
3. **Rebuy rule after harvest.** Whether harvested rupees wait in cash for the
   next dip or partly rotate; this is where units are won or lost.

Fixed, not varied: gross cap 120%, gold-first vs debt-first (Session 1 showed
it barely matters), VIX gate thresholds (structural, measured, not fitted).

---

## 6. Guardrails

- Walk-forward: tune on the earlier cycles, evaluate on the later ones with
  parameters frozen; report both side by side.
- At most 3 parameters varied jointly. For every result report how the metric
  moves when each parameter shifts one grid step; flag isolated peaks as fitted.
- Tax and costs (20% STCG under one year, 12.5% LTCG after) on for every
  headline number, off only for comparison.
- Label every finding by the cycles it rests on. A conclusion resting on one
  cycle is stated as such in the text, not a footnote.
- Do not change the tier structure or add signals. Do not modify
  `streamlit_app.py`. Keep all existing tests green. Commit to a branch, do not
  merge to main.

---

## 7. Deliverable

`BACKTEST_UNITS.md`, on a branch, containing:

- The per-cycle unit ledger summary (units bought and sold per tier, average
  prices, net units) for every cycle in both windows.
- Unit gain per cycle for the ladder against both comparators, with and without
  deposit interest.
- A plain answer to: **does the ladder end each cycle with more units than
  passive, and which harvest rule maximises that?**
- The neighbourhood analysis and an honest split of what is robust across
  cycles versus what rests on one.
- Tests for the ledger and reconciliation.

If the ladder does not beat passive on units in most cycles, say so plainly and
say what that implies for the framework. Do not tune until it does.
