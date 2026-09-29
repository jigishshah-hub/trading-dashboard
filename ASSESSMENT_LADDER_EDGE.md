# Does the tactical ladder have edge? — measured assessment

All numbers below charge **tax and 10bps costs to every strategy**, after fixing
a defect in the benchmark (see §3). Initial capital ₹10,00,000.

## 1. Modern window, 2015-06 → 2026-09

| | final | CAGR | max DD | tax paid |
|---|---|---|---|---|
| ladder, current settings | 30,24,384 | 9.40% | −27.9% | 2,04,221 |
| ladder, best case (no reserve) | 32,00,562 | 9.90% | −32.3% | 2,37,515 |
| **fixed 75/15/10, rebalanced yearly** | **35,86,360** | **10.92%** | **−28.1%** | 81,110 |
| passive 58/42 buy & hold | 36,66,872 | 11.12% | −37.6% | 0 |

## 2. 2008 window, 2007-09 → 2014-12 (EMA pre-warmed, gates off)

| | final | CAGR | max DD | tax paid |
|---|---|---|---|---|
| ladder | 18,89,256 | 8.18% | −51.4% | 1,23,447 |
| **fixed 75/15/10, rebalanced yearly** | **24,56,636** | **11.75%** | **−47.5%** | 18,108 |
| passive 58/42 buy & hold | 20,94,201 | 9.57% | −64.4% | 0 |

In the crash the ladder was built for, it finished behind **passive buy & hold**
and far behind yearly rebalancing, on both return and drawdown. On the unit
measure for cy2008 (untaxed): ladder +14.1% vs fixed **+27.5%** — yearly
rebalancing accumulated nearly twice the units.

## 3. Benchmark defect found and fixed

`fixed_rebalance()` claimed a "like-for-like compare" but hardcoded
`sell_to_value(..., False)`, and after a `tax` parameter was added it still
discarded the returned tax with `p, _ = ...`. The benchmark never paid CGT while
the ladder did. Now deducted from proceeds and recorded in `res.tax_paid`.

This made the benchmark **harder** to beat, not easier — correcting it moved
fixed from 36,91,661 to 35,86,360. The ladder still loses by 5,61,976 (1.52pp
CAGR).

## 4. Decomposition — where the ladder loses

| variant | final | CAGR | max DD | trades |
|---|---|---|---|---|
| base (current) | 30,24,384 | 9.40% | −27.9% | 4,377 |
| reserve 0% | 32,00,562 | 9.90% | −32.3% | 4,380 |
| reserve 10% | 31,07,391 | 9.64% | −30.0% | 4,377 |
| reserve 30% | 29,50,496 | 9.18% | −25.9% | 4,377 |
| event-driven (no glide) | 29,06,377 | 9.04% | −31.5% | 435 |
| base, no tax/costs | 33,56,526 | 10.33% | −27.8% | 4,377 |

- **Churn is not the leak.** Cutting trades 10× (4,377 → 435) made returns
  *worse* and tax *higher* — fewer, larger sales realise bigger gains at once.
- **The reserve is not a leak either.** It is a straight trade: reserve 0% earns
  +0.50pp CAGR and gives up 4.4pp of drawdown protection. Priced fairly.
- **Tax is ~22% of the gap** (ladder 2,04,221 vs fixed 81,110). The remaining
  ~78% is the strategy: less equity exposure during the rises, which is most of
  the time.

### The decisive number
Ladder with **every** advantage — no reserve, no tax, no costs — reaches
35,88,852 (10.93%, −32.2%). That merely *draws level* with a **taxed** yearly
rebalance (35,86,360, 10.92%, −28.1%), and with a worse drawdown.

A strategy that needs tax exemption and zero costs to tie a benchmark that pays
both is not carrying an edge.

## 5. Why the unit metric hid this

`unit_gain_per_cycle` measures peak → recovery only. It excludes the long rises
between cycles, which is exactly where the reserve drag and tax accrue. The
Session 2 spec's own calibration note concedes that at the same date and price,
capital ÷ price ranks strategies identically to rupee value — so the unit lens
did not uncover a hidden edge, it removed the periods where the cost appears.

## 6. Recommendation

Do not tune the tier boundaries or levels yet. Tuning 8 boundaries and 11 levels
against 11 cycles, for a strategy that currently trails a three-line benchmark,
would fit noise — the exact trap the Session 2 guardrails were written to avoid.

The open question is not "which thresholds are optimal" but "can this shape of
rule beat yearly rebalancing at all". Suggested next test: whether deploying the
reserve *faster and deeper* (the ladder's only structural advantage over
rebalancing) can close a 1.5pp CAGR gap. If it cannot, the ladder's tier
structure is not worth optimising and yearly rebalancing plus a drawdown overlay
is the stronger base to build on.
