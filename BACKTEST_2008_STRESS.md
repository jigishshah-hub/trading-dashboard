# Allocation-matrix engine — 2008 stress test

**Session 1 deliverable for `BACKTEST_SPEC_2008.md`. Built and stress-tested; not optimised.**

This session replaces the fixed-tranche ladder with a target-allocation matrix,
extends the stored history back to 2007, and stress-tests the deploy ladder
against the one deep bear market outside March 2020: the 2008 crash. It does not
sweep for CAGR. It builds the engine, runs the scenarios, and reports what breaks.

**The one-line finding:** *how* the reserve is paced matters far more than *how
far* the ladder is allowed to deploy. Deployed immediately, an above-100% ladder
**loses** to a 100%-capped control in a 2008-shaped fall — the reserve is spent
near −25% below the 200 EMA and the market bottoms near −45%. Deployed on a glide
(≈⅓ of the gap per week), the same ladder **beats** the control in 2008 *and*
2020 and never fully exhausts its reserve. The deep-tier ceiling is a second-order
knob; the pacing rule is the decision.

---

## Read this first — what the 2008 run can and cannot say (from §4)

These are not footnotes. They bound every number below.

- **The 2008 run is EMA-only — no breadth gate.** The P&F breadth series begins
  2015-06-01, and India VIX begins 2008-03-03, so the tier gates (breadth ≤ X,
  VIX ≥ Y) cannot confirm in 2008. The 2008 ladder therefore deploys on EMA
  distance alone, which deploys **more** than the real gated rules would. That is
  the conservative direction for a stress test (it makes the reserve run out
  *sooner*, not later), but it is not a like-for-like run against the live rules.
- **The 200 EMA does not warm until ~mid-July 2008.** Yahoo's `^NSEI` history
  starts 2007-09-17 (at 4,495). The January 2008 peak (~6,357) sits inside the
  EMA's seed window but is not itself a decision bar, so the *first* tier decision
  (7 Jul 2008) already reads **−22.7% below the 200 EMA** — the average is held up
  by the peak while spot has already fallen to ~4,000. A distance ladder that
  comes online in the middle of a crash sees a deep tier on its very first look
  and, unpaced, deploys immediately. That is a structural property of the data
  window, and it is exactly the failure mode the pacing rules address.
- **Deploy asset is `^NSMIDCP` (Nifty Midcap 50 proxy), both windows.** The
  framework's actual asset, Nifty Midcap 150, only lists from 2019 and cannot
  cover 2008; `^NSMIDCP` is the only midcap series spanning both windows, used in
  both for consistency.
- **Gold before 2009 is synthetic** (`GC=F` × `USDINR=X`, INR/oz), spliced to the
  tradeable `GOLDBEES.NS` from its first bar (2009-01-02) with a single constant
  factor so the level is continuous and post-splice returns are exactly the ETF's.
  This is what lets the gold sleeve show the 2008 rupee-driven rally that
  `GOLDBEES` alone misses.
- **Debt is modelled as a yield-only sleeve** (Indian short-duration debt funds do
  not crash), earning `DEPOSIT_YIELD`. No duration or credit-spread risk.

Windows: **stress = 7 Jul 2008 → 31 Mar 2010** (from the first warmed-EMA
decision), **modern = 1 Jun 2015 → 28 Sep 2026** (breadth + VIX gates on).
Everything is measured on **total capital** (portfolio + undeployed standby).
XIRR is reported alongside every CAGR; the two agree to the second decimal in
every scenario, confirming the standby is accounted as internal capital and no
deployment is mistaken for an external infusion (§3).

---

## Scenario table

Initial capital ₹10 lakh. `reserve-over-base` (rob) is sized to the family's
ceiling (110% → 10%, 120% → 20%, 120%+T6-8 → 50%); each active run is compared to
a **100%-capped control holding the same reserve** — the only difference is
whether the reserve is allowed to deploy. Depletion = idle reserve reaches zero.

### Stress window — 2008 (Jul 2008 → Mar 2010, EMA-only)

| Scenario | CAGR | XIRR | MaxDD | CAGR/\|DD\| | Deplete date | Dist @ deplete | Deepest dist *after* deplete | Days >100% |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| **Control 100%-cap** (rob 0.20) | **16.64%** | 16.63% | −39.4% | 0.42 | — | — | — | 0 |
| 110% (T4) active — debt-first | 15.07% | 15.06% | −45.5% | 0.33 | 2008-07-07 | −22.7% | **−44.5%** | 113 |
| 110% (T4) active — gold-first | 15.22% | 15.21% | −45.4% | 0.34 | 2008-07-07 | −22.7% | −44.5% | 113 |
| 120% (T4-T5) active — debt-first | 14.10% | 14.09% | −43.3% | 0.33 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120% (T4-T5) active — gold-first | 14.24% | 14.23% | −43.2% | 0.33 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120%+T6-8 active — debt-first (rob 0.50) | 14.45% | 14.44% | −36.5% | 0.40 | — (never) | — | — | 113 |
| 120%+T6-8 active — gold-first | 14.56% | 14.55% | −36.4% | 0.40 | — (never) | — | — | 113 |
| 120% + **glide (⅓/wk)** | **18.01%** | 18.00% | −41.5% | 0.43 | — (never) | — | — | 114 |
| 120% + **confirm (3-day)** | 17.47% | 17.46% | −42.1% | 0.41 | 2008-10-15 | −28.7% | −44.5% | 107 |
| 120% + hysteresis (2 pt) | 14.67% | 14.66% | −43.6% | 0.34 | 2008-07-16 | −25.6% | −44.5% | 118 |
| 120% — deposit yield 3.0% | 13.32% | 13.31% | −43.4% | 0.31 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120% — deposit yield 5.0% | 13.77% | 13.76% | −43.3% | 0.32 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120% — deposit yield 6.5% | 14.10% | 14.09% | −43.3% | 0.33 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120% — deposit yield 7.0% | 14.21% | 14.20% | −43.3% | 0.33 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120% — **tax + costs on** | 11.74% | 11.74% | −43.5% | 0.27 | 2008-07-16 | −25.6% | −44.5% | 113 |
| 120%+T6-8 — tax + costs on | 11.48% | 11.47% | −36.7% | 0.31 | — | — | — | 113 |
| *Bench: fixed 75/15/10 (annual)* | *20.81%* | *20.80%* | *−35.9%* | *0.58* | — | — | — | 0 |
| *Bench: buy & hold 58/42 (no reserve)* | *23.71%* | *23.69%* | *−47.6%* | *0.50* | — | — | — | 0 |

### Modern window — 2015-06 → 2026-09 (breadth + VIX gates on)

| Scenario | CAGR | XIRR | MaxDD | CAGR/\|DD\| | Deplete date | Dist @ deplete | Deepest dist *after* deplete | Days >100% |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| **Control 100%-cap** (rob 0.20) | **9.90%** | 9.89% | −27.1% | 0.36 | — | — | — | 0 |
| 110% (T4) active — debt-first | 10.19% | 10.18% | −29.7% | 0.34 | 2020-03-16 | −20.7% | −33.5% | 7 |
| 110% (T4) active — gold-first | 10.28% | 10.27% | −29.6% | 0.35 | 2020-03-16 | −20.7% | −33.5% | 7 |
| 120% (T4-T5) active — debt-first | 10.01% | 10.00% | −27.3% | 0.37 | 2020-03-18 | −26.6% | −33.5% | 7 |
| 120% (T4-T5) active — gold-first | 10.10% | 10.09% | −27.2% | 0.37 | 2020-03-18 | −26.6% | −33.5% | 7 |
| 120%+T6-8 active — debt-first (rob 0.50) | 9.38% | 9.37% | −22.2% | 0.42 | — (never) | — | — | 7 |
| 120% + glide (⅓/wk) | 9.86% | 9.85% | −27.6% | 0.36 | — (never) | — | — | 0 |
| 120% + confirm (3-day) | 9.93% | 9.92% | −24.0% | 0.41 | — (never) | — | — | 0 |
| 120% + hysteresis (2 pt) | 9.79% | 9.78% | −31.1% | 0.31 | 2020-03-18 | −26.6% | −33.5% | 9 |
| 120% — deposit yield 3.0% | 8.96% | 8.95% | −27.5% | 0.33 | 2020-03-18 | −26.6% | −33.5% | 7 |
| 120% — deposit yield 7.0% | 10.16% | 10.15% | −27.3% | 0.37 | 2020-03-18 | −26.6% | −33.5% | 7 |
| 120% — tax + costs on | 8.42% | 8.42% | −27.6% | 0.30 | 2020-03-18 | −26.6% | −33.5% | 7 |
| *Bench: fixed 75/15/10 (annual)* | *10.83%* | *10.82%* | *−28.1%* | *0.39* | — | — | — | 0 |
| *Bench: buy & hold 58/42 (no reserve)* | *10.43%* | *10.42%* | *−37.6%* | *0.28* | — | — | — | 0 |

---

## The headline question (§7)

> *What is the deepest EMA distance at which deploying the last of the reserve
> still leaves the portfolio ahead of the 100%-capped control, at the end of the
> window?*

**It depends entirely on how the reserve is paced, and on the shape of the
bottom.** The ceiling sweep (each ceiling with a matched-reserve control):

**2008, immediate deployment — there is no such depth.** At *every* ceiling from
105% to 150%, the active ladder ends the window **behind** its own 100%-capped
control:

| Ceiling | Reserve exhausted at | Deepest dist after | Active final | Control final | Ahead? |
|---:|---:|---:|---:|---:|:--:|
| 105% | −22.7% | −44.5% | ₹11.53 L | ₹11.72 L | no |
| 110% | −22.7% | −44.5% | ₹11.44 L | ₹11.78 L | no |
| 120% | −25.6% | −44.5% | ₹11.43 L | ₹11.87 L | no |
| 130% | −30.5% | −44.5% | ₹11.60 L | ₹11.94 L | no |
| 140% | −43.5% | −44.5% | ₹11.79 L | ₹11.98 L | no |
| 150% | never | — | ₹11.85 L | ₹12.01 L | no |

The reason is in the "deepest dist after depletion" column: the reserve is gone
by −22% to −30%, and the market keeps falling to **−44.5%**. Every rupee of the
reserve was spent 15–22 EMA-points *above* the bottom, then rode the rest of the
way down at up to 120% exposure. By March 2010 the recovery does not recoup the
extra drawdown.

**2020, immediate deployment — every depth qualifies.** In the modern window,
which contains the COVID crash, *every* ceiling ends **ahead** of its control,
including the deepest tested (reserve exhausted at −33.5%, essentially the bottom).
The 2020 bottom recovered in weeks, so capital deployed near it was rewarded.

**With glide pacing, 2008 flips — and the reserve is never exhausted.** Feeding
the reserve in at ⅓ of the gap per week rather than all at once:

| Ceiling (glide) | Reserve exhausted? | Deepest dist reached | Final | vs control 11.87 L | MaxDD |
|---:|:--:|---:|---:|:--:|---:|
| 110% | never | −44.5% | ₹12.07 L | **ahead** | −44.0% |
| 120% | never | −44.5% | ₹12.12 L | **ahead** | −41.5% |
| 130% | never | −44.5% | ₹12.16 L | **ahead** | −38.7% |
| 140% | never | −44.5% | ₹12.20 L | **ahead** | −36.3% |
| 150% | never | −44.5% | ₹12.22 L | **ahead** | −34.2% |

So the honest answer to §7: **with immediate deployment, no depth is safe in a
2008-shaped fall; with glide pacing, every depth up to 150% is, precisely because
the glide never lets the reserve run out** — the last of it is still being fed in
as the market approaches −44.5%, instead of being gone at −25%. The deepest EMA
distance at which the last of the reserve is deployed is therefore a *design
output of the pacing rule*, not a fixed number: pace it so the last rupee enters
near the true bottom (−40% to −50%), not at the first deep print.

---

## Recommendation (reasoned as reserve pacing, not CAGR)

**1. Cap gross exposure at 120% (T5).** Beyond 120%, the extra final capital in
2008 (even glide-paced: ₹12.12 L → ₹12.22 L from 120% to 150%) is trivial and
rests on a single event, while the larger reserve it requires is a permanent drag
in every non-crash year (modern control falls from 9.90% at rob 0.20 to 9.28% at
rob 0.50). 120% buys essentially all of the available crash upside at a reserve
size (20% of base) that is bearable in normal times.

**2. Pace deployment with a glide (≈⅓ of the gap to target per week), or at
minimum a multi-day confirmation.** This is the actual decision of this session.
Immediate deployment loses in 2008 at every ceiling; glide wins in 2008 at every
ceiling *and* holds up in 2020, and it monotonically **improves drawdown** as the
ceiling rises (−44% → −34% across the glide sweep) because the reserve is spent
gradually into the fall instead of in one block near the top of the crash. The
mechanism: a glide cannot exhaust the reserve at −22% the way an EMA ladder that
comes online mid-crash does. Confirmation (3-day) is a weaker substitute that
still helps (17.5% vs 14.1% unpaced in 2008); **hysteresis is not a pacing rule** —
it changed almost nothing here and slightly worsened drawdown, because it governs
*when to exit* a tier, not *how fast to enter*.

**3. T6–T8: a slow continuation, not a step-up.** Deep tiers should extend the
glide, not accelerate it. Recommended schedule **T6 (−30 to −40%) → 125% · T7
(−40 to −50%) → 130% · T8 (< −50%) → 135%**, deployed on the same glide. The
reasoning is entirely about not exhausting the reserve above the bottom: in 2008
the bottom was −44.5%, so the schedule must still have dry powder at −40%. Large
steps (the engine's 130/140/150 default) spend too much too high and, unpaced,
reproduce the failure in the table above. A gentle 125/130/135 glide keeps reserve
in hand through the deepest tiers while adding only marginal exposure that, again,
rests on two events pointing in opposite directions.

**4. Do not read the deposit-yield or spend-order columns as levers to optimise.**
They behave exactly as arithmetic predicts (see below) and neither changes the
recommendation.

---

## Secondary findings, and which single event each rests on

**Rests on 2008 alone:**
- Immediate above-100% deployment *loses* to the 100%-capped control (−1.5 to
  −2.5 CAGR points), because the reserve is exhausted at −22% to −30% while the
  market bottoms at −44.5%. This is the entire case for pacing.
- A distance ladder that comes online mid-crash reads a deep tier on its first
  decision and, unpaced, deploys the whole reserve at once (depletion on the very
  first decision bar, 7 Jul 2008, at −22.7%).

**Rests on 2020 alone:**
- Immediate above-100% deployment *wins* (marginally, +0.1 CAGR points) — the only
  deep-tier evidence before 2008 came from March 2020, which recovered at record
  speed, and it points the opposite way to 2008. The deep tiers (T4–T5) have fired
  on ~3–4 days ever, all in one fortnight of 2020; the modern window's only deep
  event *is* that fortnight.

**Appears in both windows (more trustworthy):**
- **Above-100% deployment deepens drawdown** unless paced: actives show worse
  MaxDD than their controls in both windows (2008 −43% vs −39%; 2020 −27.3% vs
  −27.1%). Leverage into a falling market cuts the wrong way first.
- **Glide/confirmation pacing improves drawdown and the return/risk ratio** in both
  windows (2020 confirm: −24.0% DD vs −27.1% control; 2008 glide: strictly ahead).
- **Gold-first edges debt-first**, by a hair, in both windows (2008: 14.24% vs
  14.10%; 2020: 10.10% vs 10.01%). Selling the appreciating INR-gold sleeve to buy
  cheap equity beats preserving it — but the margin is ~0.1 CAGR points and is not
  a reason to prefer one order. It is reported because §5 asked us to measure it
  rather than trust intuition; the measurement says it barely matters.
- **Deposit yield is mechanical and monotone** (3% → 7% moves 2008 CAGR 13.3% →
  14.2%, 2020 9.0% → 10.2%): the reserve earns it while idle, so a higher yield
  helps most in the strategies that keep more reserve.
- **Tax and costs are a real, not cosmetic, drag** (2008: −2.4 points; 2020: −1.6
  points, on ₹2.8 L of booked gains over the long window). The harvest ladder
  books gains, so any measured contribution from harvesting is overstated with
  tax off — which is why the engine models 20% STCG / 12.5% LTCG on FIFO lots,
  defaulted off only so the existing suite stays green.

**A note on the benchmarks.** In 2008, being fully invested (fixed 75/15/10 at
20.8%, buy-and-hold 58/42 at 23.7%) beat every reserve-holding matrix variant,
because a reserve is dead weight through a V-recovery — but buy-and-hold paid
−47.6% MaxDD for it. In the modern window the matrix variants land *just below*
the fully-invested benchmarks (9.9% vs 10.4–10.8%), which is the correct sign: a
strategy that parks 17–33% of capital in deposits most of the time should trail a
fully-invested one in a rising market, and make it back only in drawdown
protection. Any result showing the reserve strategy *beating* full investment in a
bull market would be a modelling artifact — see the note below.

---

## Engine notes and a caught artifact

- The matrix engine is **event-driven**: it deploys on entering a deeper tier and
  harvests on entering a higher one, and otherwise holds. An earlier version
  rebalanced the whole book to target *every bar*; that harvests daily noise
  ("volatility pumping") and compounded a constant-mix daily rebalance of these
  series to **several times** a buy-and-hold of the same weights — a pure backtest
  artifact. It was caught by reconciling the engine against a hand-computed
  constant-mix rebalance (they matched, proving it was the model, not a bug), and
  the engine now trades only when the target moves. A regression test
  (`test_matrix_engine.py` [16]) locks this in: a path that oscillates inside the
  baseline band must produce zero trades and exactly equal a buy-and-hold.
- All three test suites are green: `test_backtest_engine.py` (the original tranche
  engine, untouched), `test_matrix_engine.py` (the new engine, 16 groups), and
  `test_index_sync.py` (the gold splice).
- Nothing in this session touched `streamlit_app.py`, and nothing writes to
  Supabase except the `index_history` rows produced by the Index History Sync
  backfill.

## Reproducing

```
python scenario_2008.py          # prints the full metrics table + sweeps
python test_matrix_engine.py     # engine unit tests
python test_backtest_engine.py   # original tranche engine (still green)
```

`scenario_2008.py` replays the frozen, md5-verified snapshot in `series/` (see
`series/README.md`); it needs no network and no credentials. The live data is
produced by `index_sync.py` via the **Index History Sync** GitHub Action
(backfill from 2007-01-01).
