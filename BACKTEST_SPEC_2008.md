# Session 1 spec — allocation matrix engine + 2008 stress test

Status: design agreed 28 Sep 2026. This session builds and stress-tests.
It does **not** optimise. Optimisation is Session 2, and it is gated on the
ceiling this session establishes.

---

## 1. What this session answers

One question:

> Given a finite standby reserve and a ladder that deploys it as the market
> falls, how should that reserve be paced so it is not exhausted before the
> bottom — when the bottom is 2008-shaped rather than 2020-shaped?

Every existing observation of the deep tiers comes from March 2020, which
recovered at record speed. T4 has fired on **3 days** in eleven years; T5 on
**4**. All of them are the same fortnight. Any deep-tier rule tuned on that
sample is fitted to one event. 2008 is the second observation.

**Do not optimise anything in this session.** Do not sweep parameters to
maximise CAGR. Build the engine, run the scenarios, report what breaks.

---

## 2. Model change: tranches → target allocation matrix

The current engine deploys fixed 8% tranches of the tactical reserve
(`backtest_engine.py`, `DEPLOY_TIERS`). Note that 5 tiers x 8% of a reserve
that is itself 40% of the portfolio caps total deployment at 16% of the
portfolio — `MIN_CASH_FRAC` is never reached and is effectively dead code.
That formulation is being replaced.

The new model names a **target allocation per cycle state** and rebalances
toward it. No tranche size to argue about; a late tier still lands on the
right weight; harvest becomes the same mechanism running in reverse.

| State (Nifty vs 200 EMA) | Equity | Debt | Gold | Gross | Gates |
|---|---|---|---|---|---|
| > +20% | 70% | 20% | 10% | 100% | — |
| +8% to +20% | 75% | 17% | 8% | 100% | — |
| −5% to +8% | 80% | 15% | 5% | 100% | — |
| T1 −5% to −10% | 85% | 10% | 5% | 100% | breadth ≤40 AND VIX ≥20 |
| T2 −10% to −15% | 92% | 5% | 3% | 100% | breadth ≤35 AND VIX ≥25 |
| T3 −15% to −20% | 100% | 0% | 0% | 100% | breadth ≤30 |
| T4 −20% to −25% | 110% | 0% | 0% | 110% | breadth ≤25 |
| T5 −25% to −30% | 120% | 0% | 0% | 120% | breadth ≤20 |
| T6 −30% to −40% | TBD | — | — | TBD | breadth ≤20 |
| T7 −40% to −50% | TBD | — | — | TBD | breadth ≤20 |
| T8 below −50% | TBD | — | — | TBD | breadth ≤20 |

T6–T8 are what this session exists to determine. Treat them as free
parameters to *explore*, not to optimise.

### Why the VIX gates sit where they do

Measured against the stored series (200 SMA used as an EMA proxy):

| Band vs 200-day | Days | VIX ≥20 | VIX ≥25 |
|---|---|---|---|
| ≤ −20% | 21 | 21 (100%) | 21 (100%) |
| −15 to −20% | 25 | 25 (100%) | 25 (100%) |
| −10 to −15% | 31 | 31 (100%) | 14 (45%) |
| −5 to −10% | 211 | 98 (46%) | 32 (15%) |
| 0 to −5% | 489 | 56 (11%) | 24 (5%) |

A VIX ≥20 gate is 100% non-binding at −10% and below — it has never blocked
anything. It only discriminates at T1 (46% of days). VIX ≥25 is the
threshold that bites at T2 (45%). Below T3, fear is universal and a further
gate only adds a way to miss the bottom.

**These thresholds are structural, not fitted. Do not tune them.** Test them
on/off and report the difference, nothing more.

---

## 3. Capital accounting — read this carefully

There is **no margin, no broker leverage, no MTF, and no interest cost.**
Gross above 100% is funded by the user's own money parked in savings and
deposits outside the portfolio.

This creates an accounting trap that must not be sprung:

- The standby reserve is **part of total capital from day one**, sitting in
  deposits and earning `DEPOSIT_YIELD` until deployed.
- CAGR, drawdown and every comparison are computed on **total capital**
  (portfolio + undeployed standby), never on the portfolio alone.
- Treating the injection as an external infusion and measuring return on the
  original portfolio would credit a cash transfer as investment
  performance. It would look excellent and mean nothing.
- Report **XIRR as a cross-check** on every headline CAGR. If they diverge
  materially, the accounting is wrong — stop and say so.

`DEPOSIT_YIELD`: default 6.5% p.a., swept at 3% / 5% / 6.5% / 7%.

Replace margin-call modelling with **depletion tracking**:
- Record the date and EMA-distance at which the standby reserve first hits
  zero, for every scenario.
- Record the maximum EMA distance reached *after* depletion. That gap is the
  headline risk number of this whole exercise.
- The reserve refills only through the harvest ladder. Model that explicitly.

---

## 4. Data tasks

All in `index_sync.py`.

1. `BACKFILL_START` → `"2007-01-01"`.
2. Remove `^CNXMID` from `MIDCAP_CANDIDATES` — Yahoo reports it delisted; it
   has been failing silently on every run.
3. Add credit-stress inputs: `HYG` (from Apr 2007) and `LQD` (from 2002).
   The dashboard currently derives LQD/HYG live and does not store it, so
   backtests silently run on 2-of-3 fear confirmation. Storing it fixes that.
4. Add gold. `GOLDBEES.NS` only starts 31 Dec 2008 and misses the crash.
   Build **synthetic INR gold** from `GC=F` x `USDINR=X` (both cover 2008),
   and splice to `GOLDBEES.NS` from 2009 for the tradeable instrument.
   Store as symbol `GOLD_INR_SYNTH`. Document the splice point in the file.

Verified availability (checked 28 Sep 2026):

| Symbol | First bar |
|---|---|
| `^NSEI` | 2007-09-17 |
| `^NSMIDCP` | 2007-09-17 |
| `^NSEMDCP50` | 2007-09-24 |
| `GC=F` | 2000-09 |
| `USDINR=X` | 2003-12 |
| `HYG` | 2007-04-11 |
| `LQD` | 2002-07-30 |
| `GOLDBEES.NS` | 2008-12-31 |
| `^CNXMID` | delisted |

**Known limitation to state in the report:** the Nifty series begins
2007-09-17, so the 200 EMA does not warm until roughly mid-July 2008. The
January 2008 peak (~6,357) is outside the testable window. The first valid
decision lands with the Nifty near 4,000. This still captures the fall to
~2,250 in late October 2008 — a further ~44% — which is the part that
matters. Do not silently start the backtest earlier than the EMA warm-up
allows.

Breadth (`rzone_pnf_1pct`) exists only from 2015-06-01. **The 2008 run is
therefore EMA-only, with no breadth gate.** That deploys *more* than the real
rules would, which is the conservative direction for a stress test. Say so
explicitly in the report rather than presenting it as a like-for-like run.

---

## 5. Engine changes

Work in `backtest_engine.py` and keep it what it already is: pure functions,
no Streamlit, no network, no Supabase. Do not touch `streamlit_app.py` in
this session. Do not write to Supabase except the `index_history` rows the
sync produces.

- Add a matrix-driven mode alongside the existing tier logic. **Keep the
  existing `run()` working and keep `test_backtest_engine.py` green** — the
  dashboard's Backtest tab depends on it.
- Equity is two funds: `^NSEI` and the midcap series, split **58/42**,
  holding the existing 35/25 core proportion. Both move on deployment.
- Three sleeves now: equity, debt, gold. Deployment draws down debt and gold
  to reach the target equity weight.
- **Test spend order explicitly: gold-first vs debt-first.** Gold in INR rose
  through 2008 partly on rupee weakness, so the gold sleeve may be worth
  most exactly when the ladder wants to spend it. Nobody's intuition is
  reliable here; measure it.
- Rebalancing needs an anti-whipsaw rule, since a Nifty oscillating around a
  boundary would otherwise churn a 7% equity swing repeatedly. Implement all
  three and compare: hysteresis band (enter T2 at −10%, exit at −8%);
  glide (close 1/3 of the gap per week); N-day confirmation delay.
- Add transaction costs and Indian capital gains tax as optional parameters,
  **defaulting off** so existing tests pass unchanged: 20% STCG under one
  year, 12.5% LTCG after. The harvest ladder books gains, so its measured
  contribution is overstated without this.

### Benchmarks

`static_allocation()` no longer applies — it assumes a fixed reserve that
never moves. Build:

1. Fixed 75/15/10, rebalanced annually — the honest passive baseline.
2. **The matrix capped at 100% gross** — identical in every other respect.
   This is the most important number in the report: it isolates exactly what
   the above-100% deployment contributes, so the extra return can be priced
   against the extra concentration.
3. Buy-and-hold 58/42 equity, no reserve — context only, not a target.

---

## 6. Scenarios to run

Run each over **Jul 2008 → Mar 2010** (the stress window) and again over
**2015-06 → 2026-09** (the modern window, with breadth).

1. Matrix capped at 100% — the control.
2. Matrix to 110% (T4 only).
3. Matrix to 120% (T4–T5).
4. Matrix to 120% with T6–T8 extending deployment below −30%.
5. Each of 2–4 again with gold-first vs debt-first spend order.
6. Each of the three anti-whipsaw rules on the best one or two above.
7. `DEPOSIT_YIELD` sensitivity: 3% / 5% / 6.5% / 7%.
8. Tax and costs on vs off, on the best two configurations.

For every scenario report: CAGR on total capital, XIRR, max drawdown on
total capital, CAGR/|MDD|, **date and EMA distance at reserve depletion**,
**maximum EMA distance reached after depletion**, and the number of days
spent above 100% gross.

---

## 7. Deliverable

`BACKTEST_2008_STRESS.md`, committed to a branch. Do not merge to main.

It must contain:

- The scenario table described above.
- A direct answer to: **what is the deepest EMA distance at which deploying
  the last of the reserve still leaves the portfolio ahead of the
  100%-capped control, measured at the end of the window?**
- A recommended maximum gross exposure and a recommended T6–T8 schedule,
  with the reasoning stated in terms of reserve pacing, not CAGR.
- An explicit statement of which findings rest on 2008 alone, which on 2020
  alone, and which appear in both. Anything resting on a single event is
  labelled as such in the text, not only in a footnote.
- The known limitations from §4 restated in the reader's path, not hidden.

Add tests for everything new. Keep the existing suite green.

If the data work in §4 reveals that any of this is not buildable as
specified, stop and report that rather than substituting a proxy silently.
