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
    python vix_model.py            # uses series/ text files
    python vix_model.py --lookback 20  # forward-return horizon (trading days)
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

# ── Load series ──────────────────────────────────────────────
SERIES_DIR = Path(__file__).parent / "series"


def _load(fname: str) -> dict[date, float]:
    raw = (SERIES_DIR / fname).read_text()
    out: dict[date, float] = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if "," not in pair:
            continue
        d_str, v_str = pair.split(",", 1)
        try:
            out[date.fromisoformat(d_str.strip())] = float(v_str.strip())
        except ValueError:
            pass
    return out


def build_frame(lookback_days: int = 20) -> pd.DataFrame:
    """Aligned daily frame with forward returns baked in."""
    nsei = _load("nsei.txt")
    mid = _load("midcap.txt")
    vix = _load("vix.txt")

    common = sorted(nsei.keys() & mid.keys() & vix.keys())
    df = pd.DataFrame({
        "nsei": [nsei[d] for d in common],
        "mid":  [mid[d]  for d in common],
        "vix":  [vix[d]  for d in common],
    }, index=common)

    # Daily log returns
    df["r_nsei"] = np.log(df["nsei"]).diff()
    df["r_mid"]  = np.log(df["mid"]).diff()

    # Forward returns over `lookback_days` bars (tomorrow open → N days later)
    n = lookback_days
    df[f"fwd{n}_nsei"] = np.log(df["nsei"].shift(-n) / df["nsei"].shift(-1))
    df[f"fwd{n}_mid"]  = np.log(df["mid"].shift(-n)  / df["mid"].shift(-1))
    df[f"fwd{n}_rel"]  = df[f"fwd{n}_mid"] - df[f"fwd{n}_nsei"]

    # 20-day realised vol of Nifty (annualised)
    df["rvol20"] = df["r_nsei"].rolling(20).std() * math.sqrt(252)

    # VIX lag-1 (today's VIX → tomorrow's forward return window)
    df["vix_lag1"] = df["vix"].shift(1)

    # VIX percentile rank over trailing 252-day window
    df["vix_pct"] = df["vix"].rolling(252, min_periods=63).rank(pct=True)

    df.dropna(inplace=True)
    return df


# ── VIX regime bucketing ─────────────────────────────────────
VIX_CUTS = [0, 13, 16, 20, 25, 30, 100]
VIX_LABELS = ["<13 Calm", "13–16 Normal", "16–20 Elevated",
               "20–25 Fear", "25–30 Stress", ">30 Panic"]


def regime_stats(df: pd.DataFrame, fwd_col: str) -> pd.DataFrame:
    """Mean, median, std, Sharpe, and hit-rate by VIX regime."""
    df = df.copy()
    df["regime"] = pd.cut(df["vix_lag1"], bins=VIX_CUTS, labels=VIX_LABELS, right=True)
    g = df.groupby("regime", observed=True)[fwd_col]
    try:
        ann_f = 252 / int(fwd_col.split("fwd")[1].split("_")[0])
    except Exception:
        ann_f = 1.0

    result = pd.DataFrame({
        "n":       g.count(),
        "mean_ann": g.mean() * ann_f * 100,
        "median_ann": g.median() * ann_f * 100,
        "std_ann":  g.std() * math.sqrt(ann_f) * 100,
        "hit_rate": g.apply(lambda x: (x > 0).mean() * 100),
    })
    result["sharpe"] = result["mean_ann"] / result["std_ann"]
    return result.round(2)


# ── OLS: forward return ~ VIX ────────────────────────────────
def ols_vix_return(df: pd.DataFrame, fwd_col: str) -> sm.regression.linear_model.RegressionResults:
    """OLS of forward return on log-VIX (more linear relationship than raw VIX)."""
    d2 = df.copy()
    d2["log_vix"] = np.log(d2["vix_lag1"])
    d2["log_vix2"] = d2["log_vix"] ** 2
    formula = f"{fwd_col} ~ log_vix + log_vix2"
    return smf.ols(formula, data=d2).fit(cov_type="HC3")


# ── Threshold sweep: Sharpe-maximising VIX entry level ───────
def threshold_sweep(df: pd.DataFrame, fwd_col: str,
                    vix_lo: float = 13.0, vix_hi: float = 45.0,
                    step: float = 0.5) -> pd.DataFrame:
    """
    For each VIX threshold T:
      - signal = 1 when vix_lag1 >= T  (deploy / stay invested)
      - signal = 0 otherwise (hold cash at 6.5% p.a.)

    Measures Sharpe of the resulting strategy vs always-invested baseline.
    """
    CASH_DAILY = (1.065 ** (1 / 252)) - 1
    thresholds = np.arange(vix_lo, vix_hi + step, step)
    rows = []
    base_sharpe = _sharpe(df[fwd_col])
    for t in thresholds:
        mask = df["vix_lag1"] >= t
        n_deploy = mask.sum()
        if n_deploy < 30:
            continue
        deployed_ret = df.loc[mask, fwd_col]
        # days NOT deployed earn cash (convert daily yield to fwd_col horizon)
        horizon = int(fwd_col.split("fwd")[1].split("_")[0])
        cash_fwd = (1 + CASH_DAILY) ** horizon - 1
        combined = pd.concat([deployed_ret, pd.Series([cash_fwd] * (len(df) - n_deploy))])
        rows.append({
            "vix_threshold": round(t, 1),
            "n_signals":     int(n_deploy),
            "pct_deployed":  round(n_deploy / len(df) * 100, 1),
            "mean_fwd":      round(deployed_ret.mean() * 100, 3),
            "sharpe_strat":  round(_sharpe(deployed_ret), 3),
            "sharpe_base":   round(base_sharpe, 3),
            "sharpe_lift":   round(_sharpe(deployed_ret) - base_sharpe, 3),
        })
    return pd.DataFrame(rows)


def _sharpe(s: pd.Series) -> float:
    if s.std() < 1e-10:
        return 0.0
    return s.mean() / s.std()


# ── VIX regime: Midcap vs Nifty relative performance ─────────
def midcap_vs_nifty_by_regime(df: pd.DataFrame, fwd_col: str) -> pd.DataFrame:
    """
    Tests: does Midcap statistically significantly outperform Nifty in each regime?
    Uses paired t-test on relative returns (mid - nifty).
    """
    rel_col = fwd_col.replace("_nsei", "_rel").replace("_mid", "_rel")
    if rel_col not in df.columns:
        # fallback: build it
        n_col = fwd_col.replace("_mid", "_nsei")
        if n_col in df.columns:
            df = df.copy()
            df[rel_col] = df[fwd_col] - df[n_col]
        else:
            return pd.DataFrame()

    df2 = df.copy()
    df2["regime"] = pd.cut(df2["vix_lag1"], bins=VIX_CUTS, labels=VIX_LABELS, right=True)
    rows = []
    for regime, grp in df2.groupby("regime", observed=True):
        rel = grp[rel_col].dropna()
        if len(rel) < 10:
            continue
        t_stat, p_val = stats.ttest_1samp(rel, 0)
        rows.append({
            "regime":         str(regime),
            "n":              len(rel),
            "mid_beats_nifty_pct": round((rel > 0).mean() * 100, 1),
            "mean_outperform_ann": round(rel.mean() * 252 / 20 * 100, 2),
            "t_stat":         round(t_stat, 3),
            "p_value":        round(p_val, 4),
            "significant":    p_val < 0.05,
        })
    return pd.DataFrame(rows)


# ── Optimal Nifty/Midcap mix by VIX regime ───────────────────
def optimal_mix_by_regime(df: pd.DataFrame, fwd_col: str,
                           n_col: str, m_col: str) -> pd.DataFrame:
    """
    For each VIX regime, sweep Midcap weight from 0% to 100% and find
    the weight that maximises the Sharpe ratio of the blended return.
    """
    df2 = df.copy()
    df2["regime"] = pd.cut(df2["vix_lag1"], bins=VIX_CUTS, labels=VIX_LABELS, right=True)
    weights = np.arange(0.0, 1.01, 0.05)
    rows = []
    for regime, grp in df2.groupby("regime", observed=True):
        grp = grp[[n_col, m_col]].dropna()
        if len(grp) < 20:
            continue
        best_w, best_sh = 0.0, -np.inf
        for w in weights:
            blend = (1 - w) * grp[n_col] + w * grp[m_col]
            sh = _sharpe(blend)
            if sh > best_sh:
                best_sh, best_w = sh, w
        rows.append({
            "regime":        str(regime),
            "n":             len(grp),
            "opt_midcap_wt": round(best_w * 100, 0),
            "opt_sharpe":    round(best_sh, 3),
        })
    return pd.DataFrame(rows)


# ── Rolling correlation: Nifty-Midcap by VIX level ───────────
def rolling_corr_by_vix(df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """
    Shows how Nifty-Midcap correlation evolves with VIX.
    High correlation = Midcap provides no diversification.
    """
    df2 = df.copy()
    df2["corr"] = df2["r_nsei"].rolling(window).corr(df2["r_mid"])
    df2["vix_regime"] = pd.cut(df2["vix_lag1"], bins=VIX_CUTS,
                                labels=VIX_LABELS, right=True)
    return df2.groupby("vix_regime", observed=True)["corr"].agg(
        ["mean", "median", "std"]
    ).round(3)


# ── Main ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=20,
                    help="Forward-return horizon in trading days (default 20)")
    ap.add_argument("--json", dest="out_json", default="vix_model_results.json",
                    help="Output JSON path")
    args = ap.parse_args()

    n = args.lookback
    df = build_frame(lookback_days=n)
    fwd_n  = f"fwd{n}_nsei"
    fwd_m  = f"fwd{n}_mid"
    fwd_r  = f"fwd{n}_rel"

    print(f"\n{'='*60}")
    print(f"VIX-Gated Allocation Model  |  {n}-day forward returns")
    print(f"Data: {df.index[0]} → {df.index[-1]}  ({len(df)} aligned bars)")
    print(f"{'='*60}\n")

    # ── 1. Regime stats ──────────────────────────────────────
    print("── 1. NIFTY FORWARD RETURNS BY VIX REGIME ──")
    rs_n = regime_stats(df, fwd_n)
    print(rs_n.to_string())

    print("\n── 2. MIDCAP FORWARD RETURNS BY VIX REGIME ──")
    rs_m = regime_stats(df, fwd_m)
    print(rs_m.to_string())

    # ── 2. OLS ──────────────────────────────────────────────
    print("\n── 3. OLS: NIFTY FWD RETURN ~ LOG(VIX) ──")
    ols_n = ols_vix_return(df, fwd_n)
    print(ols_n.summary2().tables[1].to_string())

    print("\n── 4. OLS: MIDCAP FWD RETURN ~ LOG(VIX) ──")
    ols_m = ols_vix_return(df, fwd_m)
    print(ols_m.summary2().tables[1].to_string())

    # ── 3. Midcap vs Nifty by regime ────────────────────────
    print("\n── 5. MIDCAP vs NIFTY RELATIVE PERFORMANCE BY REGIME ──")
    rel_df = midcap_vs_nifty_by_regime(df, fwd_m)
    print(rel_df.to_string(index=False))

    # ── 4. Threshold sweep ───────────────────────────────────
    print(f"\n── 6. VIX ENTRY THRESHOLD SWEEP (deploy when VIX ≥ T) ──")
    sweep_n = threshold_sweep(df, fwd_n)
    top_n = sweep_n.nlargest(10, "sharpe_strat")
    print("  Top Nifty entries by Sharpe:")
    print(top_n.to_string(index=False))

    sweep_m = threshold_sweep(df, fwd_m)
    top_m = sweep_m.nlargest(10, "sharpe_strat")
    print("\n  Top Midcap entries by Sharpe:")
    print(top_m.to_string(index=False))

    # ── 5. Optimal Nifty/Midcap mix ─────────────────────────
    print("\n── 7. OPTIMAL NIFTY/MIDCAP MIX BY REGIME ──")
    mix_df = optimal_mix_by_regime(df, fwd_r, fwd_n, fwd_m)
    print(mix_df.to_string(index=False))

    # ── 6. Correlation by regime ─────────────────────────────
    print("\n── 8. NIFTY-MIDCAP DAILY RETURN CORRELATION BY VIX REGIME ──")
    corr_df = rolling_corr_by_vix(df)
    print(corr_df.to_string())

    # ── Summary: recommended thresholds ─────────────────────
    best_n_thresh = sweep_n.loc[sweep_n["sharpe_strat"].idxmax(), "vix_threshold"]
    best_m_thresh = sweep_m.loc[sweep_m["sharpe_strat"].idxmax(), "vix_threshold"]

    print(f"\n{'='*60}")
    print(f"RECOMMENDED VIX THRESHOLDS (maximise Sharpe, {n}-day fwd):")
    print(f"  Nifty deploy when VIX ≥   {best_n_thresh:.1f}  "
          f"(current engine: T1=20, T2=25)")
    print(f"  Midcap deploy when VIX ≥  {best_m_thresh:.1f}  "
          f"(current engine: T1=20, T2=25)")
    print(f"{'='*60}\n")

    # ── JSON output ──────────────────────────────────────────
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
