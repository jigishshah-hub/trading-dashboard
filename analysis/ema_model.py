"""
EMA-Distance Allocation Model — Statistical Analysis
=====================================================
Uses daily Nifty 50 and Nifty Midcap 150 closes to answer:

  1. Which EMA-distance zones give statistically significant forward returns?
  2. What is the optimal deploy threshold (sweep -1% → -30% below 200-EMA)?
  3. What is the optimal Nifty/Midcap split at each EMA-distance zone?
  4. How do forward returns scale with distance? (linear or convex?)

The analysis mirrors vix_model.py: same series file format, same JSON output,
same threshold-sweep design so results are directly comparable.

Usage:
    python ema_model.py               # 20-day forward return, 200-EMA
    python ema_model.py --lookback 60 --ema-span 200
    python ema_model.py --with-vix    # add VIX regime breakdown (needs vix.txt)

Series files (in analysis/series/) — comma-separated date,value pairs, one per line.
Export from Supabase with:
    SELECT bar_date || ',' || close FROM index_history
    WHERE symbol = 'NIFTY50'   ORDER BY bar_date;
    -- save as series/nifty.txt

    SELECT bar_date || ',' || close FROM index_history
    WHERE symbol = 'MIDCAP150' ORDER BY bar_date;
    -- save as series/midcap.txt

    SELECT bar_date || ',' || close FROM index_history
    WHERE symbol = '^INDIAVIX'  ORDER BY bar_date;
    -- save as series/vix.txt  (optional, for --with-vix)
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ── Series loader ────────────────────────────────────────────────────────────
SERIES_DIR = Path(__file__).parent / "series"


def _load(fname: str) -> dict[date, float]:
    """Read comma-separated date,value text file → {date: float}."""
    path = SERIES_DIR / fname
    if not path.exists():
        raise FileNotFoundError(
            f"Series file not found: {path}\n"
            "Export from Supabase (see docstring at top of this file)."
        )
    out: dict[date, float] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d_str, v_str = line.split(",", 1)
            out[date.fromisoformat(d_str.strip())] = float(v_str.strip())
        except ValueError:
            continue
    return out


# ── EMA ─────────────────────────────────────────────────────────────────────
def _ema(values: pd.Series, span: int) -> pd.Series:
    """Exponential moving average with min_periods=span (first span bars NaN)."""
    return values.ewm(span=span, min_periods=span, adjust=False).mean()


# ── EMA-distance buckets ─────────────────────────────────────────────────────
# Each tuple: (lo_pct, hi_pct, label)
# lo_pct ≤ ema_dist < hi_pct  (all values are %)
EMA_ZONES = [
    (-100.0, -30.0, "Crash  (< -30%)"),
    (-30.0,  -20.0, "Deep   (-30 to -20%)"),
    (-20.0,  -15.0, "Zone-5 (-20 to -15%)"),
    (-15.0,  -10.0, "Zone-4 (-15 to -10%)"),
    (-10.0,   -5.0, "Zone-3 (-10 to  -5%)"),
    ( -5.0,    0.0, "Zone-2 ( -5 to   0%)"),
    (  0.0,    5.0, "Mild+  (  0 to  +5%)"),
    (  5.0,   15.0, "Above  ( +5 to +15%)"),
    ( 15.0,  100.0, "Bull   (> +15%)"),
]


def zone_label(ema_dist: float) -> str:
    for lo, hi, lbl in EMA_ZONES:
        if lo <= ema_dist < hi:
            return lbl
    return "Bull   (> +15%)"


# ── Build DataFrame ───────────────────────────────────────────────────────────
def build_df(nifty: dict[date, float],
             midcap: dict[date, float],
             vix: dict[date, float] | None = None,
             ema_span: int = 200) -> pd.DataFrame:
    """Align series, compute EMA, EMA-distance, and optional VIX regime."""
    if vix:
        common = sorted(set(nifty) & set(midcap) & set(vix))
        df = pd.DataFrame({
            "nifty":  [nifty[d]  for d in common],
            "midcap": [midcap[d] for d in common],
            "vix":    [vix[d]    for d in common],
        }, index=pd.DatetimeIndex([pd.Timestamp(d) for d in common]))
    else:
        common = sorted(set(nifty) & set(midcap))
        df = pd.DataFrame({
            "nifty":  [nifty[d]  for d in common],
            "midcap": [midcap[d] for d in common],
        }, index=pd.DatetimeIndex([pd.Timestamp(d) for d in common]))
    df.index.name = "date"

    df["ema200"] = _ema(df["nifty"], ema_span)
    df["ema_dist"] = (df["nifty"] - df["ema200"]) / df["ema200"] * 100
    df["zone"] = df["ema_dist"].apply(zone_label)
    return df.dropna(subset=["ema200"])


# ── Forward returns ───────────────────────────────────────────────────────────
def add_forward_returns(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Add n-day annualised log forward returns for Nifty and Midcap."""
    td = 252 / n
    out = df.copy()
    out["fwd_nifty"]  = np.log(df["nifty"].shift(-n)  / df["nifty"])  * td
    out["fwd_midcap"] = np.log(df["midcap"].shift(-n) / df["midcap"]) * td
    return out.dropna(subset=["fwd_nifty", "fwd_midcap"])


# ── Zone stats ────────────────────────────────────────────────────────────────
def zone_stats(fwd: pd.DataFrame, col: str) -> pd.DataFrame:
    """Forward return mean, std, Sharpe, win-rate by EMA-distance zone."""
    rows = []
    for lo, hi, lbl in EMA_ZONES:
        mask = (fwd["ema_dist"] >= lo) & (fwd["ema_dist"] < hi)
        sub = fwd.loc[mask, col].dropna()
        if len(sub) < 5:
            continue
        mean  = sub.mean()
        std   = sub.std()
        sharpe = mean / std if std > 1e-9 else 0.0
        win    = (sub > 0).mean()
        rows.append({
            "zone":       lbl,
            "ema_lo":     lo,
            "ema_hi":     hi if hi < 99 else float("inf"),
            "n_bars":     len(sub),
            "mean_ann":   round(mean,   4),
            "std_ann":    round(std,    4),
            "sharpe":     round(sharpe, 4),
            "win_rate":   round(win,    4),
        })
    return pd.DataFrame(rows)


# ── Threshold sweep ───────────────────────────────────────────────────────────
def threshold_sweep(fwd: pd.DataFrame,
                    col: str,
                    thresholds: list[float] | None = None) -> pd.DataFrame:
    """
    Sweep deploy thresholds (below-EMA %).
    For each threshold T, simulate: invest only when ema_dist ≤ -T%.
    Reports Sharpe, win-rate, and coverage vs always-invested baseline.
    """
    if thresholds is None:
        thresholds = [t / 2 for t in range(0, 62)]   # 0 to 30.5 in 0.5 steps

    baseline_mean   = fwd[col].mean()
    baseline_std    = fwd[col].std()
    baseline_sharpe = baseline_mean / baseline_std if baseline_std > 1e-9 else 0.0

    rows = []
    for t in thresholds:
        sub = fwd.loc[fwd["ema_dist"] <= -t, col].dropna()
        if len(sub) < 20:
            continue
        mean   = sub.mean()
        std    = sub.std()
        sharpe = mean / std if std > 1e-9 else 0.0
        win    = (sub > 0).mean()
        rows.append({
            "ema_threshold_pct": -t,            # negative = below EMA
            "n_bars":            len(sub),
            "coverage_pct":      round(len(sub) / len(fwd) * 100, 1),
            "mean_ann":          round(mean,    4),
            "std_ann":           round(std,     4),
            "win_rate":          round(win,     4),
            "sharpe_strat":      round(sharpe,  4),
            "sharpe_baseline":   round(baseline_sharpe, 4),
            "sharpe_lift":       round(sharpe - baseline_sharpe, 4),
        })
    return pd.DataFrame(rows)


# ── Optimal Nifty/Midcap mix by EMA zone ─────────────────────────────────────
def optimal_mix_by_zone(fwd: pd.DataFrame,
                        fwd_n: str = "fwd_nifty",
                        fwd_m: str = "fwd_midcap",
                        steps: int = 21) -> pd.DataFrame:
    """
    For each EMA-distance zone, find the Midcap fraction (0..1) that
    maximises the Sharpe of the blended Nifty/Midcap forward return.
    """
    rows = []
    fracs = np.linspace(0, 1, steps)
    for lo, hi, lbl in EMA_ZONES:
        mask = (fwd["ema_dist"] >= lo) & (fwd["ema_dist"] < hi)
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
            "zone":          lbl,
            "ema_lo":        lo,
            "n_bars":        len(sub),
            "opt_mid_frac":  round(best_frac,  2),
            "opt_nifty_frac": round(1 - best_frac, 2),
            "opt_sharpe":    round(best_sharpe, 4),
        })
    return pd.DataFrame(rows)


# ── EMA × VIX cross-tab (if VIX loaded) ──────────────────────────────────────
VIX_REGIMES = [
    (0.0,  16.0, "Low VIX (<16)"),
    (16.0, 20.0, "Elevated (16-20)"),
    (20.0, 25.0, "Fear (20-25)"),
    (25.0, 30.0, "Stress (25-30)"),
    (30.0, 1e9,  "Panic (>30)"),
]

EMA_CROSS_ZONES = [
    (-100, -10, "Deep dip (< -10%)"),
    ( -10,   0, "Shallow dip (-10 to 0%)"),
    (   0, 100, "Above EMA (> 0%)"),
]


def ema_vix_crosstab(fwd: pd.DataFrame, col: str) -> pd.DataFrame:
    """Sharpe heatmap: EMA-zone rows × VIX-regime cols."""
    rows = []
    for elo, ehi, elbl in EMA_CROSS_ZONES:
        row = {"ema_zone": elbl}
        for vlo, vhi, vlbl in VIX_REGIMES:
            mask = (
                (fwd["ema_dist"] >= elo) & (fwd["ema_dist"] < ehi) &
                (fwd["vix"] >= vlo) & (fwd["vix"] < vhi)
            )
            sub = fwd.loc[mask, col].dropna()
            if len(sub) >= 10:
                std = sub.std()
                sharpe = sub.mean() / std if std > 1e-9 else 0.0
                row[vlbl] = f"{round(sharpe, 2):+.2f}  (n={len(sub)})"
            else:
                row[vlbl] = f"—  (n={len(sub)})"
        rows.append(row)
    return pd.DataFrame(rows).set_index("ema_zone")


# ── Monotonicity test ─────────────────────────────────────────────────────────
def monotonicity_test(fwd: pd.DataFrame, col: str) -> dict:
    """
    Spearman rank correlation between ema_dist and forward return.
    A strongly negative correlation means deeper dips → higher fwd returns.
    """
    sub = fwd[["ema_dist", col]].dropna()
    sub = sub[sub["ema_dist"] < 0]   # below-EMA bars only
    if len(sub) < 30:
        return {"n": len(sub), "spearman_r": None, "p_value": None}
    r, p = stats.spearmanr(sub["ema_dist"], sub[col])
    return {
        "n": len(sub),
        "note": "below-EMA bars only; negative r means deeper dip → better return",
        "spearman_r": round(r, 4),
        "p_value":    round(p, 6),
        "significant": bool(p < 0.05),
    }


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="EMA-distance allocation model")
    ap.add_argument("--lookback",  type=int,   default=20,
                    help="Forward-return horizon in trading days (default 20)")
    ap.add_argument("--ema-span",  type=int,   default=200,
                    help="EMA span in trading days (default 200)")
    ap.add_argument("--with-vix",  action="store_true",
                    help="Also load vix.txt and show EMA×VIX crosstab")
    ap.add_argument("--out-json",  default="ema_model_results.json",
                    help="Path to write JSON results")
    args = ap.parse_args()
    n = args.lookback

    nifty  = _load("nifty.txt")
    midcap = _load("midcap.txt")
    vix    = _load("vix.txt") if args.with_vix else None

    df  = build_df(nifty, midcap, vix, ema_span=args.ema_span)
    fwd = add_forward_returns(df, n)

    print(f"Loaded {len(df):,} bars  ({df.index[0].date()} → {df.index[-1].date()})")
    print(f"EMA span: {args.ema_span}  |  Forward horizon: {n} trading days\n")

    below = fwd[fwd["ema_dist"] < 0]
    print(f"Below-EMA bars: {len(below):,} of {len(fwd):,} "
          f"({100*len(below)/len(fwd):.1f}%)")

    # ── 1. Zone stats — Nifty ────────────────────────────────────────────────
    print("\n── 1. EMA-DISTANCE ZONES — NIFTY 50 FORWARD RETURNS ──")
    zs_n = zone_stats(fwd, "fwd_nifty")
    print(zs_n.to_string(index=False))

    # ── 2. Zone stats — Midcap ───────────────────────────────────────────────
    print("\n── 2. EMA-DISTANCE ZONES — MIDCAP 150 FORWARD RETURNS ──")
    zs_m = zone_stats(fwd, "fwd_midcap")
    print(zs_m.to_string(index=False))

    # ── 3. Threshold sweep — Nifty ───────────────────────────────────────────
    print("\n── 3. THRESHOLD SWEEP — NIFTY (top 10 by Sharpe) ──")
    sweep_n = threshold_sweep(fwd, "fwd_nifty")
    top_n = sweep_n.nlargest(10, "sharpe_strat")
    print(top_n.to_string(index=False))

    # ── 4. Threshold sweep — Midcap ──────────────────────────────────────────
    print("\n── 4. THRESHOLD SWEEP — MIDCAP (top 10 by Sharpe) ──")
    sweep_m = threshold_sweep(fwd, "fwd_midcap")
    top_m = sweep_m.nlargest(10, "sharpe_strat")
    print(top_m.to_string(index=False))

    # ── 5. Optimal Nifty/Midcap mix by zone ─────────────────────────────────
    print("\n── 5. OPTIMAL NIFTY/MIDCAP MIX BY EMA-DISTANCE ZONE ──")
    mix_df = optimal_mix_by_zone(fwd)
    print(mix_df.to_string(index=False))

    # ── 6. Monotonicity test ─────────────────────────────────────────────────
    print("\n── 6. MONOTONICITY: deeper dip → better forward return? ──")
    mono_n = monotonicity_test(fwd, "fwd_nifty")
    mono_m = monotonicity_test(fwd, "fwd_midcap")
    print(f"  Nifty  Spearman r={mono_n['spearman_r']}  p={mono_n['p_value']}"
          f"  sig={mono_n['significant']}")
    print(f"  Midcap Spearman r={mono_m['spearman_r']}  p={mono_m['p_value']}"
          f"  sig={mono_m['significant']}")
    print(f"  ({mono_n['note']})")

    # ── 7. EMA × VIX crosstab ────────────────────────────────────────────────
    if vix:
        print("\n── 7. SHARPE HEATMAP — EMA ZONE × VIX REGIME (Nifty) ──")
        cross_n = ema_vix_crosstab(fwd, "fwd_nifty")
        print(cross_n.to_string())
        print("\n── 8. SHARPE HEATMAP — EMA ZONE × VIX REGIME (Midcap) ──")
        cross_m = ema_vix_crosstab(fwd, "fwd_midcap")
        print(cross_m.to_string())

    # ── Summary: recommended thresholds ─────────────────────────────────────
    best_n_thresh = sweep_n.loc[sweep_n["sharpe_strat"].idxmax(), "ema_threshold_pct"]
    best_m_thresh = sweep_m.loc[sweep_m["sharpe_strat"].idxmax(), "ema_threshold_pct"]

    # Practical tiers: top-5 thresholds spaced > 3% apart (to avoid cluster)
    def pick_tiers(sweep: pd.DataFrame, n_tiers: int = 5) -> list[float]:
        ranked = sweep.sort_values("sharpe_strat", ascending=False)
        chosen: list[float] = []
        for _, row in ranked.iterrows():
            t = row["ema_threshold_pct"]
            if all(abs(t - c) >= 3.0 for c in chosen):
                chosen.append(t)
            if len(chosen) == n_tiers:
                break
        return sorted(chosen)

    tiers_n = pick_tiers(sweep_n)
    tiers_m = pick_tiers(sweep_m)

    print(f"\n{'='*65}")
    print(f"RECOMMENDED EMA DEPLOY THRESHOLDS ({n}-day fwd return, n≥20 bars):")
    print(f"  Best single threshold (Nifty) :  ema_dist ≤ {best_n_thresh:.1f}%")
    print(f"  Best single threshold (Midcap):  ema_dist ≤ {best_m_thresh:.1f}%")
    print(f"\n  Practical 5-tier ladder (Nifty) :  {[f'{t:.1f}%' for t in tiers_n]}")
    print(f"  Practical 5-tier ladder (Midcap):  {[f'{t:.1f}%' for t in tiers_m]}")
    print(f"\n  Current DEPLOY_TIERS: [-5%, -10%, -15%, -20%, -25%]")
    print(f"{'='*65}\n")

    # ── JSON output ──────────────────────────────────────────────────────────
    out: dict = {
        "generated":       str(df.index[-1].date()),
        "ema_span":        args.ema_span,
        "horizon_days":    n,
        "bars_total":      len(fwd),
        "bars_below_ema":  int((fwd["ema_dist"] < 0).sum()),
        "ema_zones_nifty":  zs_n.to_dict("records"),
        "ema_zones_midcap": zs_m.to_dict("records"),
        "nifty_threshold_sweep":  sweep_n.to_dict("records"),
        "midcap_threshold_sweep": sweep_m.to_dict("records"),
        "optimal_mix":           mix_df.to_dict("records"),
        "monotonicity": {
            "nifty":  mono_n,
            "midcap": mono_m,
        },
        "recommended": {
            "best_single_nifty_pct":  float(best_n_thresh),
            "best_single_midcap_pct": float(best_m_thresh),
            "five_tier_nifty":        tiers_n,
            "five_tier_midcap":       tiers_m,
        },
    }
    if vix:
        out["ema_vix_crosstab_nifty"]  = ema_vix_crosstab(fwd, "fwd_nifty").reset_index().to_dict("records")
        out["ema_vix_crosstab_midcap"] = ema_vix_crosstab(fwd, "fwd_midcap").reset_index().to_dict("records")

    Path(args.out_json).write_text(json.dumps(out, indent=2, default=str))
    print(f"Results written to {args.out_json}")


if __name__ == "__main__":
    main()
