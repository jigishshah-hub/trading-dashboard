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
