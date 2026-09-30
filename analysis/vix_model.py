"""
VIX-Gated Allocation Model — Statistical Analysis
==================================================
Uses 19 years of daily India VIX, Nifty 50, and Nifty Midcap 150 closes
to answer three questions:

  1. What are the optimal VIX entry thresholds for deploying into equities?
  2. When does Midcap outperform Nifty, and is VIX a reliable predictor?
  3. What is the statistically-derived optimal Nifty/Midcap split by VIX regime?

Output: console report + vix_model_results.json for the dashboard to read.

Usage:
      python vix_model.py              # uses series/ text files
      python vix_model.py --lookback 20  # forward-return horizon (trading days)

Series files (in analysis/series/) — semicolon-separated date;value pairs,
one per line.  Export from Supabase index_history with:
    SELECT bar_date, close FROM index_history
    WHERE symbol = '^INDIAVIX' ORDER BY bar_date;
and save as:
    series/vix.txt    — India VIX daily closes
    series/nifty.txt  — Nifty 50 daily closes
    series/midcap.txt — Nifty Midcap 150 daily closes
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.regression.rolling import RollingOLS
from scipy import stats

# ── Load series ──────────────────────────────────────────────────────────────
SERIES_DIR = Path(__file__).parent / "series"


def _load(fname: str) -> dict[date, float]:
    """Read a semicolon-separated date;value text file → {date: float}."""
    raw = (SERIES_DIR / fname).read_text()
    out: dict[date, float] = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        try:
            d_str, v_str = pair.split(",", 1)
            out[date.fromisoformat(d_str.strip())] = float(v_str.strip())
        except ValueError:
            continue
    return out


def build_df(vix: dict[date, float],
             nifty: dict[date, float],
             midcap: dict[date, float]) -> pd.DataFrame:
    """Align the three series on common dates, return a clean DataFrame."""
    common = sorted(set(vix) & set(nifty) & set(midcap))
    df = pd.DataFrame({
        "vix":    [vix[d]    for d in common],
        "nifty":  [nifty[d]  for d in common],
        "midcap": [midcap[d] for d in common],
    }, index=pd.DatetimeIndex([pd.Timestamp(d) for d in common]))
    df.index.name = "date"
    return df


# ── VIX regime labels ────────────────────────────────────────────────────────
REGIMES = [
    (0.0,  13.0, "Calm"),
    (13.0, 16.0, "Normal"),
    (16.0, 20.0, "Elevated"),
    (20.0, 25.0, "Fear"),
    (25.0, 30.0, "Stress"),
    (30.0, 1e9,  "Panic"),
]


def regime_label(v: float) -> str:
    for lo, hi, lbl in REGIMES:
        if lo <= v < hi:
            return lbl
    return "Panic"


# ── Forward returns ──────────────────────────────────────────────────────────
def compute_forward_returns(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Add n-day forward annualised log-returns for Nifty and Midcap."""
    fwd = df.copy()
    td = 252 / n
    fwd["fwd_nifty"]  = np.log(df["nifty"].shift(-n)  / df["nifty"])  * td
    fwd["fwd_midcap"] = np.log(df["midcap"].shift(-n) / df["midcap"]) * td
    fwd["fwd_ret"]    = fwd[["fwd_nifty", "fwd_midcap"]].mean(axis=1)  # blended
    fwd["regime"]     = fwd["vix"].apply(regime_label)
    fwd["log_vix"]    = np.log(fwd["vix"])
    fwd["log_vix2"]   = fwd["log_vix"] ** 2
    return fwd.dropna(subset=["fwd_nifty", "fwd_midcap"])


# ── Regime stats ─────────────────────────────────────────────────────────────
def regime_stats(fwd: pd.DataFrame, col: str) -> pd.DataFrame:
    """Mean annualised return and Sharpe by VIX regime for a given column."""
    rows = []
    for lo, hi, lbl in REGIMES:
        mask = (fwd["vix"] >= lo) & (fwd["vix"] < hi)
        sub = fwd.loc[mask, col].dropna()
        if len(sub) < 5:
            continue
        mean  = sub.mean()
        std   = sub.std()
        sharpe = mean / std if std > 1e-9 else 0.0
        rows.append({
            "regime": lbl,
            "vix_lo": lo,
            "vix_hi": hi if hi < 1e8 else float("inf"),
            "n_bars": len(sub),
            "mean_ann": round(mean, 4),
            "std_ann":  round(std,  4),
            "sharpe":   round(sharpe, 4),
        })
    return pd.DataFrame(rows)


# ── Relative performance (Midcap vs Nifty) ──────────────────────────────────
def relative_perf_by_regime(fwd: pd.DataFrame) -> pd.DataFrame:
    """
    For each VIX regime, test whether Midcap fwd-return > Nifty fwd-return.
    Reports mean excess return and a paired t-test p-value.
    """
    rows = []
    for lo, hi, lbl in REGIMES:
        mask = (fwd["vix"] >= lo) & (fwd["vix"] < hi)
        sub = fwd[mask].dropna(subset=["fwd_nifty", "fwd_midcap"])
        if len(sub) < 5:
            continue
        diff = sub["fwd_midcap"] - sub["fwd_nifty"]
        t_stat, p_val = stats.ttest_1samp(diff, 0.0)
        rows.append({
            "regime":           lbl,
            "n_bars":           len(sub),
            "mean_midcap_ann":  round(sub["fwd_midcap"].mean(), 4),
            "mean_nifty_ann":   round(sub["fwd_nifty"].mean(),  4),
            "mean_excess":      round(diff.mean(), 4),
            "t_stat":           round(t_stat, 4),
            "p_value":          round(p_val,  4),
            "midcap_wins":      bool(p_val < 0.05 and diff.mean() > 0),
        })
    return pd.DataFrame(rows)


# ── Threshold sweep ──────────────────────────────────────────────────────────
def threshold_sweep(fwd: pd.DataFrame,
                    col: str,
                    thresholds: list[float] | None = None) -> pd.DataFrame:
    """
    Sweep VIX entry thresholds.  For each threshold T, compute the Sharpe of
    a strategy that invests only on days where VIX ≥ T, vs the baseline of
    always being invested.  Returns a DataFrame sorted by threshold.
    """
    if thresholds is None:
        thresholds = [t / 2 for t in range(20, 100)]  # 10 to 49.5

    baseline_mean = fwd[col].mean()
    baseline_std  = fwd[col].std()
    baseline_sharpe = baseline_mean / baseline_std if baseline_std > 1e-9 else 0.0

    rows = []
    for t in thresholds:
        sub = fwd.loc[fwd["vix"] >= t, col].dropna()
        if len(sub) < 20:
            continue
        mean  = sub.mean()
        std   = sub.std()
        sharpe = mean / std if std > 1e-9 else 0.0
        rows.append({
            "vix_threshold":   t,
            "n_bars":          len(sub),
            "coverage_pct":    round(len(sub) / len(fwd) * 100, 1),
            "mean_ann":        round(mean, 4),
            "sharpe_strat":    round(sharpe, 4),
            "sharpe_baseline": round(baseline_sharpe, 4),
            "sharpe_lift":     round(sharpe - baseline_sharpe, 4),
        })
    return pd.DataFrame(rows)


# ── Optimal Nifty/Midcap mix ─────────────────────────────────────────────────
def optimal_mix_by_regime(fwd: pd.DataFrame,
                          fwd_r: str,
                          fwd_n: str,
                          fwd_m: str,
                          steps: int = 21) -> pd.DataFrame:
    """
    For each VIX regime, find the Midcap fraction (0..1 in `steps` steps) that
    maximises the Sharpe of a blended Nifty/Midcap forward return.
    """
    rows = []
    fracs = np.linspace(0, 1, steps)
    for lo, hi, lbl in REGIMES:
        mask = (fwd["vix"] >= lo) & (fwd["vix"] < hi)
        sub = fwd[mask].dropna(subset=[fwd_n, fwd_m])
        if len(sub) < 10:
            continue
        best_frac, best_sharpe = 0.0, -1e9
        for f in fracs:
            blended = (1 - f) * sub[fwd_n] + f * sub[fwd_m]
            std = blended.std()
            s = blended.mean() / std if std > 1e-9 else 0.0
            if s > best_sharpe:
                best_sharpe, best_frac = s, f
        rows.append({
            "regime":        lbl,
            "n_bars":        len(sub),
            "opt_mid_frac":  round(best_frac, 2),
            "opt_nifty_frac": round(1 - best_frac, 2),
            "opt_sharpe":    round(best_sharpe, 4),
        })
    return pd.DataFrame(rows)


# ── Correlation by VIX regime ────────────────────────────────────────────────
def rolling_corr_by_vix(df: pd.DataFrame) -> pd.DataFrame:
    """Daily return correlation between Nifty and Midcap, split by VIX regime."""
    ret_n = df["nifty"].pct_change()
    ret_m = df["midcap"].pct_change()
    rows = []
    for lo, hi, lbl in REGIMES:
        mask = (df["vix"] >= lo) & (df["vix"] < hi)
        rn = ret_n[mask].dropna()
        rm = ret_m[mask].dropna()
        common_idx = rn.index.intersection(rm.index)
        rn, rm = rn[common_idx], rm[common_idx]
        if len(rn) < 10:
            continue
        corr, p_val = stats.pearsonr(rn, rm)
        rows.append({
            "regime":  lbl,
            "n_bars":  len(rn),
            "corr":    round(corr, 4),
            "p_value": round(p_val, 6),
        })
    return pd.DataFrame(rows).set_index("regime")


# ── OLS: return ~ log(VIX) + log(VIX)² ─────────────────────────────────────
def fit_ols(fwd: pd.DataFrame, col: str):
    """Fit return ~ log_vix + log_vix² (U-shaped curve).  Returns OLS result."""
    sub = fwd[["log_vix", "log_vix2", col]].dropna()
    X = sm.add_constant(sub[["log_vix", "log_vix2"]])
    return sm.OLS(sub[col], X).fit()


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="VIX-gated allocation model")
    ap.add_argument("--lookback", type=int, default=20,
                    help="Forward-return horizon in trading days (default 20)")
    ap.add_argument("--out-json", default="vix_model_results.json",
                    help="Path to write JSON results")
    args = ap.parse_args()
    n = args.lookback

    # Load series
    vix    = _load("vix.txt")
    nifty  = _load("nifty.txt")
    midcap = _load("midcap.txt")

    df  = build_df(vix, nifty, midcap)
    fwd = compute_forward_returns(df, n)

    print(f"Loaded {len(df):,} aligned bars  "
          f"({df.index[0].date()} → {df.index[-1].date()})")
    print(f"Forward-return horizon: {n} trading days\n")

    fwd_r = "fwd_ret"
    fwd_n = "fwd_nifty"
    fwd_m = "fwd_midcap"

    # ── 1. Regime stats — Nifty ──────────────────────────────────────────────
    print("── 1. VIX REGIMES — NIFTY 50 FORWARD RETURNS ──")
    rs_n = regime_stats(fwd, fwd_n)
    print(rs_n.to_string(index=False))

    # ── 2. Regime stats — Midcap ─────────────────────────────────────────────
    print("\n── 2. VIX REGIMES — MIDCAP 150 FORWARD RETURNS ──")
    rs_m = regime_stats(fwd, fwd_m)
    print(rs_m.to_string(index=False))

    # ── 3. Relative performance (Midcap vs Nifty) ────────────────────────────
    print("\n── 3. MIDCAP vs NIFTY — STATISTICAL SIGNIFICANCE BY REGIME ──")
    rel_df = relative_perf_by_regime(fwd)
    print(rel_df.to_string(index=False))

    # ── 4. Threshold sweep ───────────────────────────────────────────────────
    print("\n── 4. VIX THRESHOLD SWEEP — NIFTY (top 10 by Sharpe) ──")
    sweep_n = threshold_sweep(fwd, fwd_n)
    top_n = sweep_n.nlargest(10, "sharpe_strat")
    print(top_n.to_string(index=False))

    print("\n── 5. VIX THRESHOLD SWEEP — MIDCAP (top 10 by Sharpe) ──")
    sweep_m = threshold_sweep(fwd, fwd_m)
    top_m = sweep_m.nlargest(10, "sharpe_strat")

    # ── OLS regressions ──────────────────────────────────────────────────────
    print("\n── 6. OLS: return ~ log(VIX) + log(VIX)² ──")
    ols_n = fit_ols(fwd, fwd_n)
    ols_m = fit_ols(fwd, fwd_m)
    print(f"  Nifty  R²={ols_n.rsquared:.4f}  "
          f"log_vix p={ols_n.pvalues['log_vix']:.5f}  "
          f"log_vix² p={ols_n.pvalues['log_vix2']:.5f}")
    print(f"  Midcap R²={ols_m.rsquared:.4f}  "
          f"log_vix p={ols_m.pvalues['log_vix']:.5f}  "
          f"log_vix² p={ols_m.pvalues['log_vix2']:.5f}")

    print("\n  Top Midcap entries by Sharpe:")
    print(top_m.to_string(index=False))

    # ── 5. Optimal Nifty/Midcap mix ─────────────────────────────────────────
    print("\n── 7. OPTIMAL NIFTY/MIDCAP MIX BY REGIME ──")
    mix_df = optimal_mix_by_regime(df, fwd_r, fwd_n, fwd_m)
    print(mix_df.to_string(index=False))

    # ── 6. Correlation by regime ─────────────────────────────────────────────
    print("\n── 8. NIFTY-MIDCAP DAILY RETURN CORRELATION BY VIX REGIME ──")
    corr_df = rolling_corr_by_vix(df)
    print(corr_df.to_string())

    # ── Summary: recommended thresholds ─────────────────────────────────────
    best_n_thresh = sweep_n.loc[sweep_n["sharpe_strat"].idxmax(), "vix_threshold"]
    best_m_thresh = sweep_m.loc[sweep_m["sharpe_strat"].idxmax(), "vix_threshold"]

    print(f"\n{'='*60}")
    print(f"RECOMMENDED VIX THRESHOLDS (maximise Sharpe, {n}-day fwd):")
    print(f"  Nifty deploy when VIX ≥   {best_n_thresh:.1f}  "
          f"(current engine: T1=20, T2=25)")
    print(f"  Midcap deploy when VIX ≥  {best_m_thresh:.1f}  "
          f"(current engine: T1=20, T2=25)")
    print(f"{'='*60}\n")

    # ── JSON output ──────────────────────────────────────────────────────────
    out = {
        "generated": str(df.index[-1]),
        "horizon_days": n,
        "vix_regimes": rs_n.reset_index().to_dict("records"),
        "midcap_regimes": rs_m.reset_index().to_dict("records"),
        "relative_perf": rel_df.to_dict("records"),
        "optimal_mix": mix_df.to_dict("records"),
        "nifty_threshold_sweep": sweep_n.to_dict("records"),
        "midcap_threshold_sweep": sweep_m.to_dict("records"),
        "recommended_thresholds": {
            "nifty_vix_min": float(best_n_thresh),
            "midcap_vix_min": float(best_m_thresh),
        },
        "ols_nifty": {
            "r2": round(ols_n.rsquared, 4),
            "params": {k: round(float(v), 6) for k, v in ols_n.params.items()},
            "pvalues": {k: round(float(v), 6) for k, v in ols_n.pvalues.items()},
        },
        "ols_midcap": {
            "r2": round(ols_m.rsquared, 4),
            "params": {k: round(float(v), 6) for k, v in ols_m.params.items()},
            "pvalues": {k: round(float(v), 6) for k, v in ols_m.pvalues.items()},
        },
        "correlation_by_regime": corr_df.reset_index().to_dict("records"),
    }
    Path(args.out_json).write_text(json.dumps(out, indent=2, default=str))
    print(f"Results written to {args.out_json}")


if __name__ == "__main__":
    main()
