# Operating rule — agreed 2026-09-29

Conclusion of five assessments in this branch. Read those for the evidence;
this file is what to actually do.

## Main strategy

**Fixed equity/debt allocation, rebalanced yearly. No gold.**

- Equity weight is the risk dial, not an optimisation target:

  | mix | modern window | 2008 window |
  |---|---|---|
  | 85/15 | 10.36% CAGR / −32.0% | 10.63% / −56.1% |
  | 75/25 | 9.95% / −28.3% | 10.82% / −49.8% |
  | 65/35 | 9.52% / −24.5% | 10.79% / −43.2% |

- Rebalance **yearly**, not monthly or quarterly — less frequent won in both
  windows, tax-driven.
- Each year's capital goes in as a lump when available. Spreading it monthly
  cost ~5% (time in market), though that is sample-dependent.

## Opportunistic overlay

Deploy **excess** funds when a dislocation happens. Otherwise maintain the
annual allocation and do nothing.

| condition | action |
|---|---|
| surplus cash arrives, market normal | invest at the normal weight, immediately |
| Nifty ~20% below its 200 EMA | deploy surplus; pull forward what you can |
| Nifty ~30% below its 200 EMA | deploy whatever is available |

Expect −20% about four times in 30 years and −30% twice (2001, 2008, 2020).

"About −20%" is the claim — the data cannot distinguish −18% from −22%, so do
not tune it further.

## Anti-rules — these were each tested and each lost

1. **Never hold cash back waiting for a trigger.** Park-until-dip lost 82% of
   the time versus investing immediately, with a worse worst case. The overlay
   only ever spends money that was already spare; the moment it causes cash to
   be withheld, it becomes the losing policy.
2. **Never sell into a recovery.** Trimming as price recovers through bands was
   the single largest destroyer of value across every ladder tested.
3. **No triggers at −8.5% or −15%.** −8.5% fires 19 times in 30 years with no
   edge over a random day. −15% was the worst level tested: negative at every
   horizon, and price typically falls a further 27% after crossing.
4. **No leverage at depth.** Worst variant tested: −3.01pp CAGR and 5.1pp more
   drawdown than a plain fixed 65/35 in 2008.
5. **Do not tune tier boundaries.** ~25 configurations were tested and all lost
   to a constant weight. Further search fits noise.

## Why staging is still reasonable

Splitting a deployment across levels is **insurance, not edge**. Across 18
episodes it won 4/4 that reached −20% (+17.7 pts average) and lost 11/14 that
stayed mild (−5.9 pts), netting to zero. It does not improve the worst case. Use
it because you cannot tell in advance how deep an episode will go, not because
it raises expected return.

## Standing caveats

- One market, 30 years, three genuine dislocations. Deep-level conclusions rest
  on 2–4 independent events.
- No permanently-impaired cycle exists in Indian data; a Japan-1989 path is
  untested.
## Midcap weight — resolved, keep 42%

Earlier note flagged the 42% midcap weight as a likely drag because midcap falls
harder in individual corrections. **That was wrong at the portfolio level.**

| midcap share | MODERN CAGR / DD | 2008 CAGR / DD |
|---|---|---|
| 0% | 8.85% / −29.0% | 10.07% / −44.8% |
| 30% | 9.65% / −28.5% | 10.64% / −48.4% |
| **42% (current)** | **9.95% / −28.3%** | **10.82% / −49.8%** |
| 60% | 10.41% / −28.0% | 11.05% / −51.9% |
| 100% | 11.42% / −30.2% | 11.38% / −56.4% |

Return rises with midcap weight in both windows (underlying indices: Nifty 9.60%
vs Midcap 12.90% modern; 8.75% vs 10.61% across 2008). Portfolio drawdown is
almost flat in the modern window — the two sleeves do not bottom together — and
the cost appears only in severe crashes, where it is steady and material.

42% sits near the point where the trade stops being favourable either way: going
to 60% buys +0.23pp of 2008 CAGR for +2.1pp of drawdown; going to 30% saves
1.4pp of 2008 drawdown for −0.30pp of modern CAGR. **Keep 42%.**

Caveats: the modern window was an exceptional stretch for Indian midcaps (a
3.3pp annual edge over Nifty) and is unlikely to repeat; if it narrows the
drawdown cost remains while the return advantage shrinks. Index midcap returns
also ignore liquidity and impact costs.
