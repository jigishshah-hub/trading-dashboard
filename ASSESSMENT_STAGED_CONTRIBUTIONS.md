# The ladder under staged contributions (₹10L/yr), not a lump sum

Every earlier assessment — and both prior sessions — modelled a **lump sum**.
`run_matrix` had no contribution mechanism and its own comments state "there is
no external infusion". Jigs's actual plan is ₹10 lakh per year for 5–6 years, so
none of the previous work tested his situation.

This adds `contributions: dict[date, float]` to `run_matrix` and
`fixed_rebalance` (default None; both verified inert without it — the modern
window returns identical totals). New money lands in debt and the band target
pulls it to the intended weight on a forced rebalance.

## Method

₹10 lakh at the start of each of 6 years (₹60 lakh total), measured at year 10.
Run from **103 rolling monthly start dates**, 2007-09 → 2016-06, so the result
does not depend on one lucky path. Gold removed, taxed + 10bps, gates off.

## Result

| strategy | median | mean | worst | best |
|---|---|---|---|---|
| ladder (aggressive) | 1,21,21,497 | 1,20,71,906 | 87,14,240 | 1,37,77,954 |
| ladder (graduated) | 1,25,56,554 | 1,24,94,366 | 90,73,502 | 1,44,05,523 |
| fixed 65/35 yearly | 1,32,52,988 | 1,31,65,282 | 1,00,99,375 | 1,51,10,793 |
| fixed 75/25 yearly | 1,37,77,788 | 1,37,33,429 | 1,00,58,363 | 1,60,38,967 |
| **fixed 100/0 yearly** | **1,54,02,635** | 1,51,49,727 | 97,81,875 | 1,84,17,424 |

Head to head against fixed 65/35, across all 103 start dates:

- ladder (graduated): **0 wins / 103**
- ladder (aggressive): **0 wins / 103**
- fixed 75/25: 102/103
- fixed 100/0: 101/103

The ladder also has the **worst** worst-case (₹87–91 lakh vs ₹98–101 lakh), so it
is not buying downside protection either.

## It is not an exposure artifact

| | avg equity exposure | final | tax paid |
|---|---|---|---|
| fixed 65/35 | 65.0% | 1,34,12,803 | — |
| fixed 75/25 | 75.0% | 1,40,14,068 | — |
| ladder (graduated) | **71.3%** | 1,25,39,941 | 6,62,225 |
| ladder (aggressive) | 68.8% | 1,20,68,404 | 8,76,753 |

The ladder holds **more** equity on average than fixed 65/35 and still finishes
₹8.7 lakh below it. Interpolating the fixed line to 71.3% exposure gives roughly
₹1.38 crore, so the timing itself costs about **₹1.25 lakh per ₹60 lakh
contributed**, on top of the exposure it does hold.

Tax is the visible leak: ₹6.6–8.8 lakh, over 5% of the final value, from
reallocating the book every time the band moves.

## Why the mechanism cannot fire

Price sits 15% or more below the 200 EMA on **3.4% of days** (159 of 4,668).
With six annual contributions, the expected number landing inside such a window
is **0.20** — four plans in five never get a single contribution into a deep
drawdown.

The depth signal is real, but staged contributions arrive on a calendar, not on
a signal. Six discrete dates cannot reliably intersect a state that exists 3% of
the time, so the ladder ends up reallocating the *existing* book instead — which
is the activity already shown to lose.

## Recommendation for the staged plan

Invest each ₹10 lakh at a fixed weight when it arrives, rebalance yearly, no
gold. Equity weight is a straight risk dial over a 10-year horizon:

- 100/0 — highest median (₹1.54 cr) and highest mean
- 75/25 — ₹1.38 cr median, tighter worst case
- 65/35 — ₹1.33 cr median, best worst case (₹1.01 cr)

Spread between the best and worst *worst cases* is only ~3%, which argues for the
higher equity weight over a horizon this long — but that rests on 103
**overlapping** windows from a 19-year sample containing one severe crash, so
treat the worst-case column as weak evidence.

## Caveats
- One market, 19 years, one severe crash. Overlapping windows are not
  independent observations.
- Measured at exactly year 10; a different end date changes the ranking.
- Contribution dates are anniversaries of the start, not chosen.
