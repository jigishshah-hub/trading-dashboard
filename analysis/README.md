# analysis/

Statistical analysis scripts that inform the backtest engine constants.

## vix_model.py

Produces the VIX-regime optimal Nifty/Midcap split used in
`backtest_engine.VIX_MID_FRAC_SCHEDULE`.

### What it answers

1. Optimal VIX entry thresholds for deploying into equities
2. When does Midcap statistically outperform Nifty (and by how much)
3. Optimal Nifty/Midcap split by VIX regime (Sharpe-maximised)

### Setup

Export the three series from Supabase and save under `analysis/series/`:

```sql
-- vix.txt
SELECT bar_date || ',' || close FROM index_history
WHERE symbol = '^INDIAVIX' ORDER BY bar_date;

-- nifty.txt
SELECT bar_date || ',' || close FROM index_history
WHERE symbol = '^NSEI' ORDER BY bar_date;

-- midcap.txt
SELECT bar_date || ',' || close FROM index_history
WHERE symbol IN ('^CNX150', 'NIFTY_MIDCAP_150') ORDER BY bar_date;
```

Save each result as a semicolon-free CSV (`date,value` per line) in:

```
analysis/series/vix.txt
analysis/series/nifty.txt
analysis/series/midcap.txt
```

### Run

```bash
cd analysis
pip install pandas numpy statsmodels scipy
python vix_model.py                    # 20-day forward horizon (default)
python vix_model.py --lookback 20      # explicit
python vix_model.py --out-json results.json
```

### Output

Console report with 8 sections + `vix_model_results.json`.

Key finding driving `VIX_MID_FRAC_SCHEDULE` in `backtest_engine.py`:

| VIX regime | Midcap % | Rationale |
|---|---|---|
| < 16 Calm/Normal | 100% | Significant outperformance in both sub-regimes |
| 16–20 Elevated | 0% | p=0.61 — not significant, hold Nifty only |
| 20–25 Fear | 100% | Significant outperformance restored |
| 25–30 Stress | 55% | Sharpe 1.57 vs 1.53 — diversify at peak stress |
| > 30 Panic | 100% | Fastest recovery (+42.5% vs +31%) |

T1=20, T2=25 engine thresholds are statistically confirmed by the
threshold sweep (max-Sharpe practical entry is VIX ≥ 25).
