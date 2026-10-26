# Spec: VTS Scanner System (Momentum Alpha with Asymmetric Exits)

## Capability Map

| Module ID    | Responsibility                                                     | Depends On              |
|--------------|--------------------------------------------------------------------|-------------------------|
| `vts-engine` | Compute VTS + VARB scores for a single stock from raw OHLCV + VIX | —                       |
| `scanner`    | Batch-run vts-engine across Nifty 200; persist results to Supabase | `vts-engine`            |
| `signals`    | Generate BUY/WATCH/AVOID signals with entry, stop, targets         | `vts-engine`, `config`  |
| `dashboard`  | New Streamlit tab showing ranked scanner output + trade cards      | `scanner`, `signals`    |

**Build order:** `vts-engine` → `scanner` → `signals` → `dashboard`

---

## Objective

Build a daily EOD stock scanner that scores the Nifty 200 universe using two
proprietary indicators — VelociTrend Score (VTS) and Volatility-Adjusted
Reversion Band (VARB) — and surfaces actionable trade signals with asymmetric
entry/stop/target levels.

**Target user:** Portfolio owner (jigish.shah@gmail.com). Replaces manual
scanning. Runs automatically every evening; results visible next morning in the
existing dashboard.

**Success looks like:** Each morning, opening the Streamlit dashboard shows a
ranked list of Nifty 200 stocks by VTS with signal classifications, entry
prices, stops, and position sizes — all computed without manual input.

---

## Indicator Definitions

### VelociTrend Score (VTS) — 0 to 100

Composite momentum score. Higher = stronger trending momentum.

```
Component 1 — EMA Slope (weight 50%)
  slope_raw  = (EMA20_today - EMA20_10days_ago) / EMA20_10days_ago * 100
  slope_norm = clip(slope_raw / ATR20_pct, -3, 3)  # ATR-normalised, [-3,3]
  ema_score  = (slope_norm + 3) / 6 * 100           # rescale to [0, 100]

Component 2 — Volume Momentum (weight 50%)
  vol_ratio  = volume_today / rolling_avg_volume_20d
  vol_score  = clip((vol_ratio - 0.5) / 2.5, 0, 1) * 100  # 0.5x→0, 3x→100

VTS = 0.5 * ema_score + 0.5 * vol_score
```

Interpretation:
- VTS >= 70  → Strong trend (BUY zone if VARB confirms)
- VTS 50–69  → Moderate momentum (WATCH)
- VTS < 50   → Weak / no trend (AVOID or short candidates)

### VARB Spread (Volatility-Adjusted Reversion Band)

```
stock_realvol = annualised 20-day rolling σ of log returns (%)
india_vix     = India VIX on same date (already annualised %)

varb_spread = stock_realvol - india_vix

Interpretation:
  spread > +15   → stock vol ripping above market fear → EXTREME (don't chase)
  spread +5..+15 → elevated, caution on entry
  spread -5..+5  → NEUTRAL → safe entry zone
  spread < -5    → stock quieter than market → smooth trend, ideal entry
```

### Entry Signal Logic

```
STRONG_BUY : VTS >= 70 AND varb_spread <= +5   AND regime == "risk_on"
BUY        : VTS >= 70 AND varb_spread <= +15  AND regime in ("risk_on", "selective")
WATCH      : VTS 55–69 OR (VTS >= 70 AND varb_spread > +15)
AVOID      : VTS < 55  OR regime == "risk_off"
```

### Market Regime (from existing config.py logic)

```
risk_on    : Nifty > 200 EMA AND VIX < 20
selective  : Nifty between 50 EMA and 200 EMA
risk_off   : Nifty < 200 EMA OR VIX >= 25
```

### Asymmetric Exit Levels (per signal)

```
entry_price = latest close (signal fires next open — use close as proxy)
atr20       = 20-day Average True Range
stop        = entry - 1.0 * atr20
target_1    = entry + 2.0 * atr20   (book 30% here)
target_2    = trail via 21-EMA (hold remainder until VTS < 40)
risk_pct    = 1.5% of capital per trade (fixed fractional)
```

---

## Tech Stack

- **Language:** Python 3.11 (consistent with existing codebase)
- **Data — stocks:** NSE Bhavcopy MCP (`get_stock_history`, `moving_average`)
- **Data — VIX:** `series/vix.txt` (local file, updated by existing sync jobs)
- **Data — Nifty 200 universe:** hardcoded list in `vts_universe.py` (symbols only)
- **Storage:** Supabase Postgres (existing project `egfsjboyzajyemjqazot`)
- **Scheduler:** GitHub Actions (daily cron, after market close ~4pm IST)
- **Dashboard:** Streamlit (new tab in existing `streamlit_app.py`)
- **Dependencies:** `pandas`, `numpy` (already available), `supabase` (existing)

---

## Commands

```
# Run scanner manually (requires SUPABASE_SERVICE_ROLE_KEY env var)
python vts_scanner.py

# Run engine tests
pytest tests/test_vts_engine.py -v

# Run all tests
pytest tests/ -v

# Lint
ruff check . --fix

# Dashboard (local)
streamlit run streamlit_app.py
```

---

## Project Structure

```
trading-dashboard/
├── vts_engine.py           # Module: vts-engine — pure functions, no network
├── vts_universe.py         # Nifty 200 symbol list (data only)
├── vts_scanner.py          # Module: scanner — fetches data, runs engine, writes Supabase
├── vts_signals.py          # Module: signals — signal + sizing logic
├── config.py               # EXTENDED: add VTS thresholds here (VTS_BUY_MIN etc.)
├── streamlit_app.py        # EXTENDED: new "VTS Scanner" tab
├── series/
│   └── vix.txt             # India VIX history (existing, used by vts_engine)
├── .github/workflows/
│   └── vts_scan.yml        # NEW: daily cron job
├── tests/
│   ├── test_vts_engine.py  # Unit tests for VTS + VARB math
│   ├── test_vts_signals.py # Unit tests for signal logic
│   └── test_vts_scanner.py # Integration test (mock Supabase)
└── SPEC-vts-system.md      # This file
```

---

## Code Style

Consistent with `backtest_engine.py` — pure functions, plain data structures,
no network calls inside engine modules.

```python
# vts_engine.py — representative style
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class VTSResult:
    symbol: str
    date: str
    vts: float          # 0–100
    varb_spread: float  # stock_realvol - india_vix
    ema20_slope: float  # raw slope %
    vol_ratio: float    # today vol / 20d avg
    atr20: float        # rupees

def compute_vts(ohlcv: pd.DataFrame, india_vix: float) -> VTSResult:
    """Pure function. ohlcv has columns: date, open, high, low, close, volume."""
    ...
```

- Module docstring at top of every file (one paragraph, explains what the module does and does NOT do)
- `config.py` is the only place for magic numbers — all VTS thresholds go there
- No `print()` in library modules — use `logging`
- Scanner script logs to stdout; GitHub Actions captures it

---

## Supabase Schema

### New table: `vts_scores`

```sql
CREATE TABLE vts_scores (
    id            bigserial PRIMARY KEY,
    scan_date     date        NOT NULL,
    symbol        text        NOT NULL,
    vts           numeric(6,2) NOT NULL,
    varb_spread   numeric(6,2) NOT NULL,
    signal        text        NOT NULL,  -- STRONG_BUY | BUY | WATCH | AVOID
    entry_price   numeric(12,4),
    stop_price    numeric(12,4),
    target_1      numeric(12,4),
    atr20         numeric(12,4),
    ema20         numeric(12,4),
    vol_ratio     numeric(6,2),
    regime        text        NOT NULL,  -- risk_on | selective | risk_off
    created_at    timestamptz DEFAULT now(),
    UNIQUE(scan_date, symbol)
);
CREATE INDEX ON vts_scores (scan_date DESC, vts DESC);
```

---

## Testing Strategy

- **Framework:** `pytest`
- **Test locations:** `tests/test_vts_engine.py`, `tests/test_vts_signals.py`, `tests/test_vts_scanner.py`
- **Coverage target:** 90%+ on `vts_engine.py` and `vts_signals.py` (pure functions — easy to test)
- **Test levels:**
  - **Unit (engine):** synthetic 30-day OHLCV series with known VTS answers computed by hand; assert within 0.1
  - **Unit (signals):** parametric table of (vts, varb_spread, regime) → expected signal
  - **Integration (scanner):** mock NSE API and Supabase; verify it writes the right number of rows and handles missing data gracefully
- **CI:** existing `ci.yml` workflow — add `pytest tests/test_vts_*.py` step

---

## Boundaries

**Always do:**
- All thresholds (VTS_BUY_MIN, VARB_NEUTRAL_MAX etc.) live in `config.py`
- `vts_engine.py` is pure — zero network, zero Supabase, zero Streamlit imports
- Run `pytest tests/test_vts_engine.py` before any commit touching the engine
- Handle missing/stale data gracefully — skip a symbol rather than crash the whole scan
- Respect `SUPABASE_SERVICE_ROLE_KEY` from env, never hardcode

**Ask first:**
- Changing the VTS formula weights (50/50 split)
- Expanding universe beyond Nifty 200
- Adding a new data source (e.g. FII/DII flow)
- Schema changes to existing Supabase tables

**Never do:**
- Call network APIs from `vts_engine.py` or `vts_signals.py`
- Hardcode Supabase URL or keys
- Push to `main` without tests passing
- Remove or skip failing tests — fix the root cause

---

## Success Criteria

1. `pytest tests/test_vts_engine.py` passes — VTS and VARB values match hand-computed reference cases within ±0.1
2. `python vts_scanner.py` completes for full Nifty 200 in under 10 minutes
3. Supabase `vts_scores` table is populated with today's scan date, 200 rows, no nulls in signal column
4. Streamlit dashboard shows a new "VTS Scanner" tab with stocks ranked by VTS, filterable by signal type
5. GitHub Actions `vts_scan.yml` runs successfully on schedule (4:30 PM IST weekdays)
6. At least one STRONG_BUY signal appears when Nifty is in `risk_on` regime (smoke test against live data)

---

## Open Questions

None — all design decisions resolved:
- Universe: **Nifty 200** ✅
- Trigger: **Daily GitHub Actions** (4:30 PM IST, after market close) ✅
- IV proxy: **India VIX from `series/vix.txt`** ✅
- Vol proxy for VTS: **20-day rolling volume ratio** (delivery data unavailable via bhavcopy) ✅
