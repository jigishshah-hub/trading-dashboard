# Gate ratchet + 2008 re-test of the sell-side sweeps

Follow-up to `54087ca` (sell-side harvest sweeps). Two questions: is the cy2020
diagnosis real, and does harvest_gate survive a cycle the modern window cannot show?

## 1. The cy2020 diagnosis is real, and the defect is upstream of harvest_gate

`resolve_band()` is documented as a **deploy** gate — "never deploying past what
the gates allow". But the resolved band's `equity` is applied as an unconditional
target, and the sell path (`if eff_eq_frac < held_eq_frac`) therefore reads a
gate failure as a **sell**.

During Mar–Sep 2020 India breadth ran 46–90. Every gated tier (T1..T8,
`breadth_max` 20–40) fails, the walk lands on ungated Baseline (equity 0.80), and
whatever was accumulated at deeper tiers gets liquidated — at the bottom. A gate
built to stop over-buying was wired to force selling.

## 2. Fix: `gate_ratchet` (no tunable parameter)

`resolve_band_ex()` now also reports whether the returned band is shallower than
price alone would select, i.e. whether a **gate** caused the fallback. With
`gate_ratchet=True`, a gate-induced fallback floors the target at the current
holding: it may block an increase, never mandate a decrease. Genuine price-driven
harvests are untouched.

Verified inert when off: the modern window produces a byte-identical
`MatrixResult` (sha256 `9b3f2375…`) before and after the change.

### Modern window (9 complete cycles, gates ON)

| config | cy2020 Δ | units sold | avg px | % ATH | net unit Δ | wins | final |
|---|---|---|---|---|---|---|---|
| baseline | — | 45.1 | 9,865 | 79.8% | −0.96 | 5/9 | 3,321,553 |
| **gate_ratchet** | +1.1 | 30.2 | 10,697 | 86.5% | −0.43 | 6/9 | 3,356,526 |
| harvest_gate=0.85 | +1.8 | 27.6 | 11,028 | 89.2% | −0.05 | 6/9 | 3,378,815 |
| harvest_gate=0.90 | +2.2 | 25.7 | 11,280 | 91.2% | +0.19 | 7/9 | 3,414,690 |
| ratchet + gate=0.85 | +1.8 | 27.6 | 11,028 | 89.2% | −0.05 | 6/9 | 3,383,222 |

Decomposition: of the 45.1 units baseline sells in cy2020, **15 are gate
artefacts** (the ratchet removes exactly those); the remaining 30.2 are
legitimate glide harvests. `gate=0.85` suppresses 17.5 — it fixes the bug *and*
blocks ~2.5 units of real harvesting; `gate=0.90` blocks ~4.

## 3. 2008, with the EMA pre-warmed

Blocker was never index history: `NSEI`/`MIDCAP` start 2007-09-17, ~4 months
before the peak, so the 200-EMA warmed 2008-07-07 with price already −35.9% off
the peak and the first decision bar at −18.1%.

Fix: prepend **199** synthetic bars from BSE Sensex 1979–2026 (scaled at the
2007-09-17 join, ×0.28989). `ema()` returns None until the seed window is full,
so the first decision bar is the first *real* bar — no synthetic bar is ever
traded. Midcap/gold over the prepend are held flat and never read.

Result: first decision bar moves from −18.1% to **+9.5%**, the Jan-2008 peak is
inside the window, and `cy2008` becomes a complete cycle (−59.9%, peak
2008-01-08 → recovery 2010-11-09).

**Gates must be OFF for 2008** — `series/breadth.txt` starts 2015-06-01 and
`_gate_ok` fails closed on missing readings, so gates-on pins every 2008 bar to
Baseline. This is a different configuration from the modern 9 cycles.

| config | final | units sold | avg px | % ATH | net unit Δ |
|---|---|---|---|---|---|
| baseline | 2,093,368 | 211.6 | 4,259 | 67.7% | +15.77 |
| gate=0.85 | 2,447,222 | 100.6 | 4,919 | 78.2% | +30.37 |
| gate=0.90 | 2,521,161 | 93.8 | 5,168 | 82.2% | +36.16 |
| gate_ratchet | 2,093,368 | 211.6 | 4,259 | 67.7% | +15.77 |

### The ratchet is untestable in 2008
Identical to baseline to the rupee. No breadth → gates off → no gate-induced
sells to block. Inert, not neutral-good.

### harvest_gate survives 2008
The prior session's hypothesis — that a gate would freeze through a long
drawdown and cost dearly — is **not supported**. Truncation test, marking the
book *inside* the drawdown rather than at recovery:

| mark | gate=0.85 vs baseline |
|---|---|
| trough (2008-10-27, −59.9%) | −27,547 (−4.1%) |
| +6m post-trough | −52 (flat) |
| +1y post-trough | +177,644 |
| at recovery | +252,846 |

Worst case is ~4% at the exact bottom, gone within six months.

## 4. Standing caveat

`detect_cycles` marks a cycle complete only on recovery, and
`unit_gain_per_cycle` scores only complete cycles — so the per-cycle metric
conditions on recovery, and a rule meaning "don't sell until price recovers"
is graded only on paths where it did. The truncation test above bypasses this
and the gate still wins, so the concern is not decisive here. But Indian equity
1979–2026 contains no permanently-impaired cycle, so a Japan-1989 path remains
untested and untestable with this data.

## 5. Recommendation

- **Ship `gate_ratchet`.** It is a correctness fix, not a fitted parameter: it
  makes the gate behave as its own docstring describes. Marginally positive on
  top of gate=0.85 (3,383,222 vs 3,378,815).
- **`harvest_gate` is better supported than the last session's caveats implied** —
  monotone across 0.70–0.90 in two independent windows and robust to
  mid-drawdown marking. It remains a preference knob, not a bug fix. 0.85 vs
  0.90 is a risk-appetite choice, not something this data resolves.

## Correction to `BACKTEST_UNITS.md`
§1 prose states cy2020 sells averaged ~9,567. The family (a) table says 9,865,
and 9,865 / 12,362 = 79.8% matches the stated % of ATH. **9,567 is wrong.**
