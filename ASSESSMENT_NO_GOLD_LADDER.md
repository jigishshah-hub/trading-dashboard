# Best way to run the ladder — no gold, fully invested

Follow-up to `ASSESSMENT_LADDER_EDGE.md`. Gold removed (`_BASE_GOLD_CAP = 0`),
standby reserve removed (`reserve_over_base = 0`, `deploy_cap = 1.0`), so the
book is equity + debt, fully invested. Tax and 10bps costs on everything.

## 1. Ladder shapes, no gold, fully invested

| MODERN 2015-06→2026-09 | final | CAGR | max DD |
|---|---|---|---|
| current shape | 30,83,892 | 9.57% | −32.3% |
| compressed (−5..−20) | 31,10,009 | 9.64% | −33.7% |
| high baseline (.90) | 31,74,333 | 9.83% | −35.1% |
| no froth-timing (.85 floor) | 32,70,086 | 10.09% | −33.9% |
| **fixed 85/15 yearly** | **33,71,376** | **10.36%** | **−32.0%** |

| 2008 2007-09→2014-12 | final | CAGR | max DD |
|---|---|---|---|
| current shape | 19,23,571 | 8.42% | −58.2% |
| no froth-timing (.85 floor) | 20,04,614 | 8.98% | −61.0% |
| **fixed 75/25 yearly** | **22,96,406** | **10.82%** | **−49.8%** |

**The ordering is monotone: the less timing the ladder does, the better it
performs.** The limit of "do less" is a fixed weight, and that wins.

## 2. One-sided ladder (add only, never de-risk)

Holding a fixed base and adding to full equity only below a trigger:

| | MODERN | 2008 |
|---|---|---|
| fixed 85/15, no ladder | 33,71,376 / −32.0% | **22,64,503 / −56.1%** |
| 85/15 + full eq below −15% | 34,16,694 / −33.3% | 20,99,293 / −59.2% |
| fixed 75/25, no ladder | 32,18,251 / −28.3% | **22,96,406 / −49.8%** |
| 75/25 + full eq below −15% | 33,16,570 / −30.3% | 21,03,862 / −55.0% |

Marginally positive in the modern window (+45k, worse drawdown), clearly
negative in 2008 (−1.65 lakh AND worse drawdown). It does not survive.

### Why — the buffer-exhaustion mechanism
A threshold ladder spends its **whole** buffer at one price level. In 2008 it
reached 100% equity at −15/−20% below the EMA and then rode the remaining fall
fully exposed with nothing left to deploy. Yearly rebalancing spends the buffer
continuously and never exhausts it — at −60% it still holds debt to sell.

Rebalancing is a ladder with infinitely many infinitely small rungs that never
runs out of ammunition. That is why it beats every discrete ladder tested.

## 3. The two parameters that actually matter

Rebalance frequency (no gold, taxed):

| | MODERN CAGR | 2008 CAGR |
|---|---|---|
| monthly | 9.72% | 9.41% |
| quarterly | 9.87% | 9.76% |
| half-yearly | 9.90% | 9.89% |
| **yearly** | **9.95%** | **10.82%** |

(75/25 shown; same monotone ordering at 85/15 and 65/35.) Less frequent is
better in both windows — tax and costs dominate.

Equity weight is a clean risk dial, yearly rebalanced:

| mix | MODERN CAGR / DD | 2008 CAGR / DD |
|---|---|---|
| 85/15 | 10.36% / −32.0% | 10.63% / −56.1% |
| 75/25 | 9.95% / −28.3% | 10.82% / −49.8% |
| 65/35 | 9.52% / −24.5% | 10.79% / −43.2% |

**65/35 yearly is the standout.** Versus 75/25 it gives up 0.43pp of CAGR in the
modern window and 0.03pp in 2008, and buys 3.8pp and **6.6pp** less drawdown.

## 4. Recommendation

Run a fixed equity/debt mix, rebalanced yearly, no gold. 65/35 or 75/25
depending on drawdown tolerance. Do not run a tiered ladder.

This answers the original four design questions differently than they were
asked. "How far below the EMA to keep buying" and "what range for each step" have
no optimum, because stepping is itself the problem: buy continuously all the way
down by rebalancing, and never exhaust the buffer at any single level.

The depth signal is real — Sensex 1996–2026 forward 1y returns rise from +12.6%
above the EMA to +82.9% below −30%. Rebalancing harvests it automatically and
without timing risk. Discrete tiers harvest it worse.

## 5. Caveats

- Yearly rebalancing winning is partly a tax artifact; in a tax-free wrapper the
  frequency ranking would narrow.
- Which month the yearly rebalance falls on is unmodelled luck and untested.
- Deep-bucket forward returns rest on few independent episodes (32 overlapping
  bars below −30%, perhaps three distinct events).
- Two windows, one market. No permanently-impaired cycle exists in Indian data.

---

# Addendum — yearly rebalance combined with a depth-triggered lever

Adds `periodic_rebalance_days` to `run_matrix` (default 0 = off, verified inert:
the modern window returns an identical total) so the ladder can rebalance on a
calendar as well as on band changes. This makes "yearly rebalance PLUS add when
Nifty falls below the 200 EMA" expressible, which it previously was not.

Designs tested, all no gold, yearly periodic rebalance, taxed + 10bps:

- **graduated add** — target equity rises 0.65 → 0.75 → 0.85 → 0.95 → 1.00 at
  0 / −10 / −20 / −30%, so buffer is never fully spent until extreme depth
- **jump to full** — 0.65 base, straight to 1.00 below −15%
- **lever to 120%** — 0.65 base rising to 1.20 below −30%, funded by a 20% reserve

| MODERN | final | CAGR | max DD |
|---|---|---|---|
| **fixed 65/35 yearly** | **30,66,154** | **9.52%** | **−24.5%** |
| fixed 75/25 yearly | 32,18,251 | 9.95% | −28.3% |
| 65 + graduated add | 28,54,678 | 8.88% | −32.4% |
| 65 + jump to full | 31,84,453 | 9.86% | −30.5% |
| 65 + lever to 120% | 27,62,232 | 8.59% | −28.1% |

| 2008 | final | CAGR | max DD |
|---|---|---|---|
| **fixed 65/35 yearly** | **22,90,346** | **10.79%** | **−43.2%** |
| fixed 75/25 yearly | 22,96,406 | 10.82% | −49.8% |
| 65 + graduated add | 18,37,403 | 7.81% | −54.0% |
| 65 + jump to full | 19,49,318 | 8.60% | −54.3% |
| 65 + lever to 120% | 18,32,963 | 7.78% | −48.3% |

Every lever variant is worse than the constant weight on **both** return and
drawdown, in **both** windows. Leverage is the worst of all: −3.01pp CAGR and
5.1pp more drawdown than fixed 65/35 in 2008.

### Likely mechanism (inference, not isolated)
Any depth-varying target forces selling on the way back **up**: as price recovers
through each band the target steps down and the book trims, capping
participation in the rebound that the deep buy was meant to capture. A constant
weight trims once a year, to the same number. This is consistent with the
earlier finding that suppressing recovery sells (`harvest_gate`) was the single
biggest improvement available to the ladder — it was patching this.

## Stopping rule
About 25 configurations of one idea have now been tested: the original ladder,
four no-gold shapes, six add-only variants, graduated tilts, jump-to-full and
leverage. All lose to a constant weight rebalanced yearly.

Further variant search is not advisable. With ~10 complete cycles and this many
attempts, something will eventually clear the benchmark by chance and it will
not survive contact with real money. The consistency of the result across
unrelated parameterisations is itself the evidence: this is the shape of the
idea, not a bad choice of numbers.

## Standing recommendation
Fixed 65/35 equity/debt, rebalanced yearly, no gold.

Better next steps than more ladder variants:
1. **Validate** 65/35 yearly — vary the rebalance month, and test on 1996–2007,
   which the Sensex file covers and nothing here has used.
2. **Revisit the 42% midcap weight.** Midcap fell −69.6% vs Nifty −59.9% in 2008
   and −24.8% vs −15.8% in cy2024. That weight may cost more drawdown than the
   ladder was ever going to save.
