# Where should a depth trigger sit? — event study with a baseline

## Correction to earlier assessments

`ASSESSMENT_LADDER_EDGE.md` §5 and `ASSESSMENT_NO_GOLD_LADDER.md` report forward
returns by depth bucket (+33.2% at −15..−20%, +55.4% at −20..−25%, +82.9% below
−30%). **Those figures are not a valid basis for a trigger rule and overstate the
case**, for two reasons:

1. They average every **bar-day** in each band. Deep bands are hundreds of
   clustered days from three crashes, and the single rebound that follows each is
   counted once per day. Observations are near-totally overlapping.
2. They were never compared against an unconditional baseline, so the market's
   own drift was being read as signal.

The event study below supersedes them: one observation per episode, taken at the
crossing (when a decision would actually be made), re-armed only after price
recovers above the 200 EMA, and measured as *edge over a random day*.

## Baseline — median forward return from a random day, Sensex 1996–2026

| 1 year | 2 years | 3 years |
|---|---|---|
| +11.3% | +25.3% | +36.9% |

## Edge over baseline, by trigger level

| trigger | events / 30 yrs | 1y edge | 2y edge | 3y edge | typical further fall |
|---|---|---|---|---|---|
| −5% | 32 | −2.7% | −0.1% | +8.8% | −10.9% |
| −8.5% | 19 | −2.6% | +0.1% | +5.6% | −11.5% |
| −10% | 16 | −2.9% | −7.8% | +7.6% | −18.3% |
| −12.5% | 12 | −4.4% | −4.0% | +8.0% | −17.3% |
| −15% | 7 | −10.7% | −16.4% | −4.9% | −26.6% |
| −17.5% | 7 | −5.4% | −12.0% | +0.6% | −21.8% |
| −20% | 4 | −3.5% | −9.3% | +26.5% | −23.0% |
| −25% | 3 | +36.5% | +51.3% | +67.9% | −10.0% |
| −30% | 2 | +59.6% | +83.5% | +61.2% | −11.2% |

Crossing dates, so the independence is visible:

- −15%: 1996-12, 1998-06, 2000-05, 2000-10, 2001-03, 2008-06, 2020-03
- −20%: 2000-10, 2001-04, 2008-07, 2020-03
- −25%: 2001-09, 2008-10, 2020-03

## Findings

- **No trigger from −5% to −17.5% beats simply buying on a random day** at a 1- or
  2-year horizon; most are negative. The 3-year edges (+5.6 to +8.8pp) are small
  and not monotone in depth.
- **−15% is the worst level tested** — negative at every horizon, and price
  typically falls a further 26.6% after the crossing.
- **−20% and deeper show real edge but rest on 2–4 events**, all from the same
  three dislocations (2001, 2008, 2020). Directionally credible, statistically
  unusable.

## Recommendation

Do not build a multi-level trigger ladder at −8.5 / −15 / −20%. The levels are
not supported, and −15% is the single worst place to put one.

For surplus cash the supported policy is: **invest it when you have it.** A
random day beats −8.5% and −15% triggers outright.

A standing intention at −25% is reasonable *provided it never causes cash to be
held back* — it costs nothing because it only spends money already spare, and it
should be expected to fire about once a decade. It cannot be validated on three
observations and should not be relied on in planning.
