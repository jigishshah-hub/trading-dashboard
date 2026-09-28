# BACKTEST_UNITS.md — Session 2 baseline
*Generated from frozen series/ snapshot. No network or credentials needed.*

> **Status:** Ledger reconciles (all tests pass in test_unit_ledger.py).
> Parameter sweeps not yet run (cost-control stop after baseline).

---

## 2008 stress window

*Feed 2007-09-17 → 2010-12-31. Gates: True. VIX gates on from 3 Mar 2008 (T1 VIX≥20, T2 VIX≥25). Breadth off (series starts 2015-06-01). EMA warms mid-July 2008; first decision bar already at ~−22.7%.*

### Cycles detected

| cycle_id | peak_date | peak_px | trough_date | trough_px | DD% | recovery_date | status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cy2008 | 2008-08-11 | 4620 | 2008-10-27 | 2524 | -45.4% | 2009-06-10 | complete |

### Per-cycle unit ledger (ladder, normal deposit yield)

Only completed cycles are shown for the unit-gain comparison.


#### cy2008  [complete]  peak 2008-08-11 @ 4620  →  trough 2008-10-27 @ 2524 (-45.4%)  →  recovery 2009-06-10

Units at peak:      NIFTY 86.03   MIDCAP 31.26
Units at recovery:  NIFTY 78.95   MIDCAP 33.22

Net unit change:    NIFTY -7.08   MIDCAP +1.95

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.40 | 7048.12 | 0.028 |
| +8..+20% | NIFTY | 0.78 | 4116.70 | 0.032 |
| Frothy >+20% | GOLD | 0.42 | 45648.21 | 0.191 |
| Frothy >+20% | MIDCAP | 10.21 | 6662.79 | 0.680 |
| Frothy >+20% | NIFTY | 1.46 | 4314.33 | 0.063 |

Avg NIFTY buy price 4246 vs NIFTY at peak 4620 → **8.1% cheaper** (8.8% more units per rupee)
Avg MIDCAP buy price 6677 vs MIDCAP at peak 7655 → **12.8% cheaper** (14.6% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.02 | 45600.52 | 0.007 |
| Frothy >+20% | GOLD | 0.54 | 46391.66 | 0.252 |
| Frothy >+20% | MIDCAP | 8.66 | 7356.40 | 0.637 |
| Frothy >+20% | NIFTY | 9.32 | 4427.49 | 0.412 |

### Unit gain per cycle — ladder vs comparators

> Unit gain = (capital_at_recovery / nifty_at_recovery) ÷ (capital_at_peak / nifty_at_peak) − 1

> 'with yield' runs include 6.5% deposit interest on reserve; 'zero yield' isolates the ladder's rules.

**With deposit yield (6.5%)**

| cycle | DD% | ladder N | ladder M | fixed 75/15/10 N | fixed M | passive 58/42 N | passive M | ladder beats fixed? | ladder beats passive? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cy2008 | -45.4% | +4.00% | +0.11% | +4.89% | +0.98% | +1.46% | -2.33% | ✗ | ✓ |

**Zero deposit yield (isolates ladder rules)**

| cycle | DD% | ladder N | ladder M | fixed 75/15/10 N | fixed M | passive 58/42 N | passive M | ladder beats fixed? | ladder beats passive? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cy2008 | -45.4% | +2.26% | -1.56% | +4.11% | +0.22% | +1.46% | -2.33% | ✗ | ✓ |

### Plain answer for this window

Completed cycles: 1.  Ladder beats fixed 75/15/10 on Nifty-equivalent units in **0/1** cycles (minority).  Ladder beats passive 58/42 in **1/1** cycles.

*(Zero-yield run separates deposit interest from ladder skill.)*


---

## Modern window (2015-06 → 2026-09)

*Feed 2014-06-01 → 2026-12-31. Gates: True. Breadth and VIX gates on.*

### Cycles detected

| cycle_id | peak_date | peak_px | trough_date | trough_px | DD% | recovery_date | status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cy2015 | 2015-07-22 | 8634 | 2016-02-25 | 6971 | -19.3% | 2016-07-25 | complete |
| cy2016 | 2016-09-08 | 8952 | 2016-12-26 | 7908 | -11.7% | 2017-03-06 | complete |
| cy2018 | 2018-01-29 | 11130 | 2018-03-23 | 9998 | -10.2% | 2018-07-24 | complete |
| cy2018b | 2018-08-28 | 11738 | 2018-10-26 | 10030 | -14.6% | 2019-04-16 | complete |
| cy2019 | 2019-06-03 | 12089 | 2019-09-19 | 10705 | -11.4% | 2019-11-27 | complete |
| cy2020 | 2020-01-14 | 12362 | 2020-03-23 | 7610 | -38.4% | 2020-11-09 | complete |
| cy2021 | 2021-10-18 | 18477 | 2022-06-17 | 15294 | -17.2% | 2022-11-24 | complete |
| cy2022 | 2022-12-01 | 18812 | 2023-03-24 | 16945 | -9.9% | 2023-06-16 | complete |
| cy2024 | 2024-09-26 | 26216 | 2025-03-04 | 22083 | -15.8% | 2026-01-02 | complete |
| cy2026 | 2026-01-02 | 26329 | 2026-03-30 | 22331 | -15.2% | open | open |

### Per-cycle unit ledger (ladder, normal deposit yield)

Only completed cycles are shown for the unit-gain comparison.


#### cy2015  [complete]  peak 2015-07-22 @ 8634  →  trough 2016-02-25 @ 6971 (-19.3%)  →  recovery 2016-07-25

Units at peak:      NIFTY 52.52   MIDCAP 17.75
Units at recovery:  NIFTY 54.74   MIDCAP 15.41

Net unit change:    NIFTY +2.22   MIDCAP -2.35

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.04 | 88436.44 | 0.031 |
| Baseline | GOLD | 0.36 | 79484.34 | 0.290 |
| Baseline | MIDCAP | 2.13 | 19204.22 | 0.409 |
| Baseline | NIFTY | 8.60 | 7797.48 | 0.671 |
| T1 -5..-10% | GOLD | 0.10 | 78385.04 | 0.079 |
| T1 -5..-10% | MIDCAP | 3.04 | 18129.87 | 0.551 |
| T1 -5..-10% | NIFTY | 10.10 | 7504.44 | 0.758 |
| T2 -10..-15% | MIDCAP | 0.37 | 17028.13 | 0.063 |
| T2 -10..-15% | NIFTY | 1.33 | 6976.35 | 0.093 |

Avg NIFTY buy price 7595 vs NIFTY at peak 8634 → **12.0% cheaper** (13.7% more units per rupee)
Avg MIDCAP buy price 18470 vs MIDCAP at peak 20774 → **11.1% cheaper** (12.5% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.83 | 21627.71 | 0.180 |
| +8..+20% | NIFTY | 0.88 | 8602.36 | 0.075 |
| Baseline | GOLD | 0.26 | 80508.90 | 0.210 |
| Baseline | MIDCAP | 5.30 | 19130.75 | 1.013 |
| Baseline | NIFTY | 16.33 | 7741.09 | 1.264 |
| T1 -5..-10% | GOLD | 0.15 | 78139.37 | 0.114 |
| T1 -5..-10% | MIDCAP | 1.76 | 19310.95 | 0.339 |
| T1 -5..-10% | NIFTY | 0.61 | 7597.26 | 0.046 |
| T2 -10..-15% | GOLD | 0.02 | 82304.51 | 0.019 |

#### cy2016  [complete]  peak 2016-09-08 @ 8952  →  trough 2016-12-26 @ 7908 (-11.7%)  →  recovery 2017-03-06

Units at peak:      NIFTY 53.48   MIDCAP 14.72
Units at recovery:  NIFTY 56.75   MIDCAP 15.73

Net unit change:    NIFTY +3.27   MIDCAP +1.01

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.03 | 89760.67 | 0.025 |
| +8..+20% | NIFTY | 0.03 | 8866.70 | 0.003 |
| Baseline | GOLD | 0.09 | 85630.78 | 0.081 |
| Baseline | MIDCAP | 2.79 | 22238.31 | 0.620 |
| Baseline | NIFTY | 5.75 | 8511.14 | 0.490 |

Avg NIFTY buy price 8513 vs NIFTY at peak 8952 → **4.9% cheaper** (5.2% more units per rupee)
Avg MIDCAP buy price 22238 vs MIDCAP at peak 23551 → **5.6% cheaper** (5.9% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.20 | 23326.05 | 0.046 |
| +8..+20% | NIFTY | 0.57 | 8901.96 | 0.051 |
| Baseline | GOLD | 0.15 | 88560.70 | 0.136 |
| Baseline | MIDCAP | 1.65 | 22689.31 | 0.374 |
| Baseline | NIFTY | 2.18 | 8418.01 | 0.183 |

#### cy2018  [complete]  peak 2018-01-29 @ 11130  →  trough 2018-03-23 @ 9998 (-10.2%)  →  recovery 2018-07-24

Units at peak:      NIFTY 51.34   MIDCAP 13.39
Units at recovery:  NIFTY 54.59   MIDCAP 14.59

Net unit change:    NIFTY +3.25   MIDCAP +1.20

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.02 | 86042.52 | 0.019 |
| +8..+20% | MIDCAP | 0.07 | 30697.23 | 0.021 |
| +8..+20% | NIFTY | 0.06 | 11049.65 | 0.007 |
| Baseline | GOLD | 0.08 | 86765.11 | 0.066 |
| Baseline | MIDCAP | 2.37 | 29173.98 | 0.692 |
| Baseline | NIFTY | 6.51 | 10403.58 | 0.677 |

Avg NIFTY buy price 10410 vs NIFTY at peak 11130 → **6.5% cheaper** (6.9% more units per rupee)
Avg MIDCAP buy price 29217 vs MIDCAP at peak 30891 → **5.4% cheaper** (5.7% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.04 | 30543.92 | 0.011 |
| +8..+20% | NIFTY | 0.51 | 11098.29 | 0.056 |
| Baseline | GOLD | 0.25 | 87049.34 | 0.215 |
| Baseline | MIDCAP | 1.17 | 29380.94 | 0.345 |
| Baseline | NIFTY | 3.16 | 10476.23 | 0.332 |

#### cy2018b  [complete]  peak 2018-08-28 @ 11738  →  trough 2018-10-26 @ 10030 (-14.6%)  →  recovery 2019-04-16

Units at peak:      NIFTY 50.09   MIDCAP 13.84
Units at recovery:  NIFTY 54.23   MIDCAP 15.20

Net unit change:    NIFTY +4.14   MIDCAP +1.36

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.03 | 85652.94 | 0.027 |
| +8..+20% | MIDCAP | 0.03 | 30868.96 | 0.008 |
| +8..+20% | NIFTY | 0.26 | 11650.67 | 0.030 |
| Baseline | GOLD | 0.12 | 88349.63 | 0.105 |
| Baseline | MIDCAP | 3.32 | 27769.39 | 0.921 |
| Baseline | NIFTY | 7.54 | 10687.99 | 0.806 |

Avg NIFTY buy price 10720 vs NIFTY at peak 11738 → **8.7% cheaper** (9.5% more units per rupee)
Avg MIDCAP buy price 27795 vs MIDCAP at peak 30768 → **9.7% cheaper** (10.7% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.00 | 85431.22 | 0.002 |
| +8..+20% | MIDCAP | 0.36 | 30867.17 | 0.111 |
| +8..+20% | NIFTY | 0.31 | 11688.41 | 0.036 |
| Baseline | GOLD | 0.35 | 87676.82 | 0.310 |
| Baseline | MIDCAP | 1.60 | 27008.02 | 0.433 |
| Baseline | NIFTY | 3.56 | 10592.95 | 0.377 |

#### cy2019  [complete]  peak 2019-06-03 @ 12089  →  trough 2019-09-19 @ 10705 (-11.4%)  →  recovery 2019-11-27

Units at peak:      NIFTY 50.92   MIDCAP 15.84
Units at recovery:  NIFTY 53.46   MIDCAP 16.44

Net unit change:    NIFTY +2.53   MIDCAP +0.60

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.06 | 90533.66 | 0.050 |
| +8..+20% | MIDCAP | 0.64 | 28142.52 | 0.179 |
| Baseline | GOLD | 0.05 | 94157.09 | 0.046 |
| Baseline | MIDCAP | 1.16 | 26897.96 | 0.313 |
| Baseline | NIFTY | 3.51 | 11524.99 | 0.405 |

Avg NIFTY buy price 11525 vs NIFTY at peak 12089 → **4.7% cheaper** (4.9% more units per rupee)
Avg MIDCAP buy price 27338 vs MIDCAP at peak 28143 → **2.9% cheaper** (2.9% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.03 | 27969.57 | 0.009 |
| +8..+20% | NIFTY | 3.44 | 12085.99 | 0.415 |
| Baseline | GOLD | 0.21 | 97667.08 | 0.208 |
| Baseline | MIDCAP | 0.53 | 27291.03 | 0.146 |
| Baseline | NIFTY | 0.85 | 11810.29 | 0.100 |

#### cy2020  [complete]  peak 2020-01-14 @ 12362  →  trough 2020-03-23 @ 7610 (-38.4%)  →  recovery 2020-11-09

Units at peak:      NIFTY 53.46   MIDCAP 16.44
Units at recovery:  NIFTY 52.49   MIDCAP 16.48

Net unit change:    NIFTY -0.96   MIDCAP +0.04

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.12 | 141549.06 | 0.163 |
| +8..+20% | MIDCAP | 0.05 | 27455.69 | 0.014 |
| Baseline | GOLD | 0.38 | 132008.00 | 0.507 |
| Baseline | MIDCAP | 2.34 | 26472.46 | 0.620 |
| Baseline | NIFTY | 7.45 | 11087.52 | 0.826 |
| T1 -5..-10% | GOLD | 0.01 | 131327.68 | 0.011 |
| T1 -5..-10% | MIDCAP | 0.41 | 25584.47 | 0.105 |
| T1 -5..-10% | NIFTY | 4.46 | 9759.73 | 0.435 |
| T2 -10..-15% | GOLD | 0.01 | 127758.89 | 0.010 |
| T2 -10..-15% | MIDCAP | 1.18 | 24228.45 | 0.287 |
| T2 -10..-15% | NIFTY | 4.59 | 9797.19 | 0.450 |
| T3 -15..-20% | GOLD | 0.02 | 131385.23 | 0.029 |
| T3 -15..-20% | MIDCAP | 2.23 | 22727.82 | 0.507 |
| T3 -15..-20% | NIFTY | 7.91 | 9186.61 | 0.727 |
| T4 -20..-25% | GOLD | 0.01 | 110449.67 | 0.006 |
| T4 -20..-25% | MIDCAP | 1.20 | 21729.63 | 0.260 |
| T4 -20..-25% | NIFTY | 7.87 | 8848.02 | 0.697 |
| T5 -25..-30% | MIDCAP | 2.06 | 20539.51 | 0.424 |
| T5 -25..-30% | NIFTY | 7.46 | 8292.71 | 0.618 |
| T6 -30..-40% | MIDCAP | 0.99 | 18524.30 | 0.183 |
| T6 -30..-40% | NIFTY | 4.35 | 7610.25 | 0.331 |

Avg NIFTY buy price 9262 vs NIFTY at peak 12362 → **25.1% cheaper** (33.5% more units per rupee)
Avg MIDCAP buy price 22928 vs MIDCAP at peak 28845 → **20.5% cheaper** (25.8% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.78 | 27815.98 | 0.217 |
| +8..+20% | NIFTY | 5.63 | 11963.41 | 0.673 |
| Baseline | GOLD | 0.28 | 138634.74 | 0.385 |
| Baseline | MIDCAP | 9.00 | 23856.28 | 2.147 |
| Baseline | NIFTY | 38.91 | 9567.19 | 3.722 |
| T1 -5..-10% | GOLD | 0.11 | 125537.39 | 0.143 |
| T1 -5..-10% | MIDCAP | 0.64 | 21860.56 | 0.140 |
| T1 -5..-10% | NIFTY | 0.21 | 9820.53 | 0.021 |
| T2 -10..-15% | GOLD | 0.03 | 131238.63 | 0.044 |
| T2 -10..-15% | NIFTY | 0.31 | 9187.30 | 0.029 |
| T3 -15..-20% | GOLD | 0.06 | 127747.27 | 0.074 |
| T4 -20..-25% | GOLD | 0.02 | 117177.98 | 0.026 |
| T5 -25..-30% | GOLD | 0.05 | 114955.38 | 0.054 |
| T6 -30..-40% | GOLD | 0.03 | 111052.18 | 0.036 |

#### cy2021  [complete]  peak 2021-10-18 @ 18477  →  trough 2022-06-17 @ 15294 (-17.2%)  →  recovery 2022-11-24

Units at peak:      NIFTY 47.79   MIDCAP 14.01
Units at recovery:  NIFTY 51.46   MIDCAP 14.94

Net unit change:    NIFTY +3.67   MIDCAP +0.93

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.11 | 131057.62 | 0.144 |
| +8..+20% | MIDCAP | 0.10 | 42957.74 | 0.042 |
| +8..+20% | NIFTY | 0.28 | 17891.47 | 0.049 |
| Baseline | GOLD | 0.37 | 138156.99 | 0.518 |
| Baseline | MIDCAP | 5.28 | 40335.99 | 2.128 |
| Baseline | NIFTY | 14.00 | 16939.55 | 2.372 |
| T1 -5..-10% | GOLD | 0.03 | 137733.09 | 0.036 |
| T1 -5..-10% | MIDCAP | 2.17 | 37147.58 | 0.805 |
| T1 -5..-10% | NIFTY | 4.43 | 15730.72 | 0.697 |

Avg NIFTY buy price 16667 vs NIFTY at peak 18477 → **9.8% cheaper** (10.9% more units per rupee)
Avg MIDCAP buy price 39453 vs MIDCAP at peak 44707 → **11.8% cheaper** (13.3% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.00 | 131664.38 | 0.002 |
| +8..+20% | MIDCAP | 0.59 | 43075.79 | 0.253 |
| +8..+20% | NIFTY | 2.32 | 17999.26 | 0.417 |
| Baseline | GOLD | 0.53 | 135715.42 | 0.715 |
| Baseline | MIDCAP | 5.94 | 40573.89 | 2.409 |
| Baseline | NIFTY | 12.65 | 16780.23 | 2.122 |
| T1 -5..-10% | GOLD | 0.11 | 141636.16 | 0.159 |
| T1 -5..-10% | MIDCAP | 0.08 | 38073.33 | 0.032 |
| T1 -5..-10% | NIFTY | 0.08 | 15350.15 | 0.012 |

#### cy2022  [complete]  peak 2022-12-01 @ 18812  →  trough 2023-03-24 @ 16945 (-9.9%)  →  recovery 2023-06-16

Units at peak:      NIFTY 48.28   MIDCAP 15.00
Units at recovery:  NIFTY 49.73   MIDCAP 16.46

Net unit change:    NIFTY +1.45   MIDCAP +1.47

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.02 | 144859.39 | 0.024 |
| +8..+20% | NIFTY | 0.05 | 18696.10 | 0.008 |
| Baseline | GOLD | 0.04 | 151571.92 | 0.057 |
| Baseline | MIDCAP | 2.44 | 40499.81 | 0.990 |
| Baseline | NIFTY | 3.96 | 18031.01 | 0.714 |

Avg NIFTY buy price 18038 vs NIFTY at peak 18812 → **4.1% cheaper** (4.3% more units per rupee)
Avg MIDCAP buy price 40500 vs MIDCAP at peak 43857 → **7.7% cheaper** (8.3% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | MIDCAP | 0.21 | 43884.63 | 0.093 |
| +8..+20% | NIFTY | 0.41 | 18760.89 | 0.077 |
| Baseline | GOLD | 0.15 | 150384.45 | 0.228 |
| Baseline | MIDCAP | 0.85 | 40858.21 | 0.346 |
| Baseline | NIFTY | 2.36 | 18039.67 | 0.427 |

#### cy2024  [complete]  peak 2024-09-26 @ 26216  →  trough 2025-03-04 @ 22083 (-15.8%)  →  recovery 2026-01-02

Units at peak:      NIFTY 46.05   MIDCAP 11.34
Units at recovery:  NIFTY 50.09   MIDCAP 13.66

Net unit change:    NIFTY +4.04   MIDCAP +2.32

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| Baseline | GOLD | 0.19 | 225514.88 | 0.419 |
| Baseline | MIDCAP | 4.22 | 68211.49 | 2.880 |
| Baseline | NIFTY | 7.63 | 24457.41 | 1.866 |
| T1 -5..-10% | MIDCAP | 0.36 | 59546.20 | 0.213 |
| T1 -5..-10% | NIFTY | 1.41 | 22161.60 | 0.312 |

Avg NIFTY buy price 24100 vs NIFTY at peak 26216 → **8.1% cheaper** (8.8% more units per rupee)
Avg MIDCAP buy price 67534 vs MIDCAP at peak 77087 → **12.4% cheaper** (14.1% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| +8..+20% | GOLD | 0.01 | 202221.52 | 0.014 |
| +8..+20% | MIDCAP | 0.09 | 77086.95 | 0.073 |
| +8..+20% | NIFTY | 0.16 | 26216.05 | 0.043 |
| Baseline | GOLD | 0.47 | 216689.84 | 1.026 |
| Baseline | MIDCAP | 2.26 | 67778.01 | 1.534 |
| Baseline | NIFTY | 5.00 | 23981.13 | 1.198 |
| T1 -5..-10% | GOLD | 0.06 | 234122.86 | 0.138 |

#### cy2026  [open]  peak 2026-01-02 @ 26329  →  trough 2026-03-30 @ 22331 (-15.2%)  →  recovery (open)

Units at peak:      NIFTY 50.09   MIDCAP 13.66
Units at recovery:  NIFTY 54.99   MIDCAP 13.19

Net unit change:    NIFTY +4.90   MIDCAP -0.47

**Buys during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| Baseline | GOLD | 0.10 | 390514.83 | 0.406 |
| Baseline | MIDCAP | 1.00 | 69073.73 | 0.694 |
| Baseline | NIFTY | 3.42 | 23757.29 | 0.813 |
| T1 -5..-10% | GOLD | 0.03 | 389829.73 | 0.110 |
| T1 -5..-10% | MIDCAP | 0.87 | 65011.97 | 0.568 |
| T1 -5..-10% | NIFTY | 5.45 | 23648.30 | 1.288 |
| T2 -10..-15% | GOLD | 0.02 | 351105.01 | 0.081 |
| T2 -10..-15% | MIDCAP | 0.67 | 60901.25 | 0.406 |
| T2 -10..-15% | NIFTY | 1.48 | 22406.15 | 0.331 |

Avg NIFTY buy price 23507 vs NIFTY at peak 26329 → **10.7% cheaper** (12.0% more units per rupee)
Avg MIDCAP buy price 65537 vs MIDCAP at peak 70417 → **6.9% cheaper** (7.4% more units per rupee)

**Sells (harvest) during cycle:**

| tier | fund | units | avg_px | rupees(L) |
| --- | --- | --- | --- | --- |
| Baseline | GOLD | 0.08 | 397395.39 | 0.306 |
| Baseline | MIDCAP | 2.88 | 68809.33 | 1.980 |
| Baseline | NIFTY | 5.07 | 23805.37 | 1.207 |
| T1 -5..-10% | GOLD | 0.20 | 417710.95 | 0.817 |
| T1 -5..-10% | MIDCAP | 0.14 | 61912.75 | 0.086 |
| T1 -5..-10% | NIFTY | 0.38 | 23263.75 | 0.087 |
| T2 -10..-15% | GOLD | 0.02 | 384306.50 | 0.061 |

### Unit gain per cycle — ladder vs comparators

> Unit gain = (capital_at_recovery / nifty_at_recovery) ÷ (capital_at_peak / nifty_at_peak) − 1

> 'with yield' runs include 6.5% deposit interest on reserve; 'zero yield' isolates the ladder's rules.

**With deposit yield (6.5%)**

| cycle | DD% | ladder N | ladder M | fixed 75/15/10 N | fixed M | passive 58/42 N | passive M | ladder beats fixed? | ladder beats passive? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cy2015 | -19.3% | +4.98% | -1.82% | +5.19% | -1.63% | +3.11% | -3.57% | ✗ | ✓ |
| cy2016 | -11.7% | +1.66% | -1.06% | +0.80% | -1.89% | +1.29% | -1.41% | ✓ | ✓ |
| cy2018 | -10.2% | -0.88% | +6.41% | -1.94% | +5.27% | -3.31% | +3.79% | ✓ | ✓ |
| cy2018b | -14.6% | -0.63% | +7.56% | -1.62% | +6.49% | -3.58% | +4.37% | ✓ | ✓ |
| cy2019 | -11.4% | +1.92% | +0.85% | +2.43% | +1.36% | +0.46% | -0.58% | ✗ | ✓ |
| cy2020 | -38.4% | +3.98% | +5.17% | +4.26% | +5.46% | -0.50% | +0.64% | ✗ | ✓ |
| cy2021 | -17.2% | +2.13% | +6.93% | +0.43% | +5.15% | -2.02% | +2.59% | ✓ | ✓ |
| cy2022 | -9.9% | +1.64% | +1.76% | +1.61% | +1.73% | -0.05% | +0.07% | ✓ | ✓ |
| cy2024 | -15.8% | +2.58% | +12.78% | +5.05% | +15.50% | -4.51% | +4.99% | ✗ | ✓ |

**Zero deposit yield (isolates ladder rules)**

| cycle | DD% | ladder N | ladder M | fixed 75/15/10 N | fixed M | passive 58/42 N | passive M | ladder beats fixed? | ladder beats passive? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cy2015 | -19.3% | +3.04% | -3.64% | +4.22% | -2.54% | +3.11% | -3.57% | ✗ | ✗ |
| cy2016 | -11.7% | +0.79% | -1.91% | +0.38% | -2.30% | +1.29% | -1.41% | ✓ | ✗ |
| cy2018 | -10.2% | -1.75% | +5.47% | -2.38% | +4.80% | -3.31% | +3.79% | ✓ | ✓ |
| cy2018b | -14.6% | -1.73% | +6.38% | -2.18% | +5.88% | -3.58% | +4.37% | ✓ | ✓ |
| cy2019 | -11.4% | +1.07% | +0.01% | +1.96% | +0.89% | +0.46% | -0.58% | ✗ | ✓ |
| cy2020 | -38.4% | +2.49% | +3.66% | +3.48% | +4.67% | -0.50% | +0.64% | ✗ | ✓ |
| cy2021 | -17.2% | +0.07% | +4.77% | -0.56% | +4.11% | -2.02% | +2.59% | ✓ | ✓ |
| cy2022 | -9.9% | +0.68% | +0.79% | +1.10% | +1.22% | -0.05% | +0.07% | ✗ | ✓ |
| cy2024 | -15.8% | +0.28% | +10.25% | +3.83% | +14.15% | -4.51% | +4.99% | ✗ | ✓ |

### Plain answer for this window

Completed cycles: 9.  Ladder beats fixed 75/15/10 on Nifty-equivalent units in **5/9** cycles (most).  Ladder beats passive 58/42 in **9/9** cycles.

*(Zero-yield run separates deposit interest from ladder skill.)*


---

## Data and methodology notes

- **Ladder configuration:** `reserve_over_base=0.20`, `deploy_cap=1.20`,
  `whipsaw=glide(1/3 wk)`, `spend_order=debt_first`. Default matrix bands.
- **Fixed comparator:** 75% equity / 15% debt / 10% gold, annual rebalance,
  same 6.5% deposit yield on debt sleeve.
- **Passive comparator:** 58/42 Nifty/Midcap buy-and-hold, no cash reserve,
  no rebalancing.
- **Unit gain formula:** (capital÷price at recovery) ÷ (capital÷price at peak) − 1.
  Price is the Nifty close on each date. Since recovery price ≥ peak price, the
  denominator uses the peak-date price and numerator uses the recovery-date price,
  so any difference in absolute Nifty level at recovery mildly affects the result;
  the zero-yield run removes the deposit-interest distortion.
- **Breadth data:** available from 2015-06-01 only; breadth gate cannot confirm
  tiers in the 2008 window.
- **Open cycles:** reported for information but excluded from the beats/fails
  count, per the spec ("A cycle that has not recovered by end of window is
  reported as open, never as a result.").
- **Reconciliation:** test_unit_ledger.py verifies that cumulative ledger units
  match engine sleeve units at every bar, and that units × price == rupees on
  every row.

---

## Next steps (parameter sweeps — not yet run)

Per BACKTEST_SPEC_SESSION2.md §5, the three families to vary are:
1. Harvest sizing (fixed fraction of units vs fixed rupee vs shrinking fraction)
2. Deploy pacing (glide rate and tranche sizing on shallow tiers)
3. Rebuy rule after harvest

Grid ≤ 20 combinations total before any evaluation.
