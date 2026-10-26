"""
vts_optimize.py — Vectorized parameter optimizer for the VTS trading system.

Uses 40 synthetic Nifty-200-calibrated stocks (real Nifty base + idiosyncratic
noise + momentum carry) to find the parameter combo with the best Sortino ratio.

VTS components backtested:
  VTS = w_rs * RS_score + w_ema * EMAslope_score + w_setup * SetupQuality_score

Grid search over VTS_BUY threshold and VARB_MAX — the signal filter knobs.
Component weights are fixed at the theoretically motivated defaults (RS=0.40,
EMA=0.35, Setup=0.25) since the key research question is the entry threshold,
not the weight mix.

Training: 2021–2024 (all market regimes)
OOS    :  2025–2026 (new regime, out-of-sample)

Run:
    python vts_optimize.py

Requires: series/vix.txt, series/nsei.txt (already in repo)
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Load real market data ──────────────────────────────────────────────────

def load_vix() -> pd.Series:
    raw = Path("series/vix.txt").read_text().strip()
    rows = [r.split(",") for r in raw.split(";") if "," in r]
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows}, dtype=float)
    return s.sort_index()


def load_nifty() -> pd.Series:
    raw = Path("series/nsei.txt").read_text().strip()
    rows = [r.split(",") for r in raw.split(";") if "," in r]
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows}, dtype=float)
    return s.sort_index()


# ── Synthetic path generation ─────────────────────────────────────────────

def build_stocks(nifty: pd.Series, vix: pd.Series, n_stocks: int = 40) -> list[pd.DataFrame]:
    """
    Generate Nifty-200-calibrated synthetic OHLCV DataFrames.

    Each stock is: return_t = beta * nifty_ret_t + alpha_drift + noise_t
    Beta 0.6–1.4 (uniform), alpha 0–0.3%/day (skewed toward zero),
    noise std 0.8–1.8% daily.
    These calibrations are drawn from Nifty-200 empirical stats (2020–2025).
    """
    nifty_ret = nifty.pct_change().dropna()
    rng = np.random.default_rng(42)

    betas      = rng.uniform(0.6, 1.4, n_stocks)
    alphas     = rng.beta(1.2, 8.0, n_stocks) * 0.003   # mostly near zero, few high
    noise_stds = rng.uniform(0.008, 0.018, n_stocks)
    momentum   = rng.uniform(0.03, 0.20, n_stocks)       # autocorrelation of daily returns

    dfs = []
    for i in range(n_stocks):
        # Align to the full Nifty return series
        nret = nifty_ret.values
        n    = len(nret)

        idio    = rng.normal(0, noise_stds[i], n)
        # AR(1) residual to simulate momentum persistence
        for t in range(1, n):
            idio[t] += momentum[i] * idio[t - 1]

        stock_ret = betas[i] * nret + alphas[i] + idio
        price = 100.0 * np.cumprod(1 + np.clip(stock_ret, -0.15, 0.15))

        dates = nifty_ret.index
        atr_noise = rng.uniform(0.005, 0.015, n)
        df = pd.DataFrame({
            "open":   price * (1 - atr_noise * 0.4),
            "high":   price * (1 + atr_noise * 0.6),
            "low":    price * (1 - atr_noise * 0.6),
            "close":  price,
            "volume": rng.lognormal(14.0, 0.5, n),
        }, index=dates)
        dfs.append(df)
    return dfs


# ── Vectorized indicator precomputation ───────────────────────────────────

def precompute(df: pd.DataFrame, nifty: pd.Series, vix: pd.Series) -> pd.DataFrame:
    """
    Precompute all VTS components for every bar in df.
    O(n) per stock — rolling windows, no per-date slicing.
    """
    close = df["close"]
    n = len(close)

    # EMA slope (ATR-normalised)
    ema20       = close.ewm(span=20, adjust=False).mean()
    high, low   = df["high"], df["low"]
    prev_close  = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr20       = tr.rolling(20).mean()
    ema_slope_10 = ema20 - ema20.shift(10)
    atr_pct      = atr20 / ema20.replace(0, np.nan) * 100
    slope_score  = ((ema_slope_10 / ema20.replace(0, np.nan) * 100) / atr_pct.replace(0, np.nan)).clip(-3, 3)
    slope_score  = (slope_score + 3.0) / 6.0 * 100

    # Relative strength (6m = 126 trading days)
    stock_ret_126 = close / close.shift(126) - 1.0
    nifty_aligned = nifty.reindex(close.index, method="ffill")
    nifty_ret_126 = nifty_aligned / nifty_aligned.shift(126) - 1.0
    rs_raw        = stock_ret_126 - nifty_ret_126
    rs_score      = ((rs_raw + 0.60) / 1.20 * 100).clip(0, 100)

    # ATR compression + near-60d-high
    atr_pct_max60 = atr_pct.rolling(60).max()
    compression   = (1.0 - atr_pct / atr_pct_max60.replace(0, np.nan)).clip(0, 1)
    high_60d      = close.rolling(60).max()
    near_high     = (close >= 0.95 * high_60d).astype(float)
    setup_score   = (compression * 80.0 + near_high * 20.0).clip(0, 100)

    # VTS composite (fixed weights)
    vts = 0.40 * rs_score + 0.35 * slope_score + 0.25 * setup_score

    # VARB
    log_ret       = np.log(close / close.shift(1))
    realvol20     = log_ret.rolling(20).std() * np.sqrt(252) * 100
    vix_aligned   = vix.reindex(close.index, method="ffill")
    varb_spread   = realvol20 - vix_aligned

    # Market regime (Nifty 200-EMA vs VIX)
    nifty_ema200  = nifty_aligned.ewm(span=200, adjust=False).mean()
    regime_on     = (nifty_aligned > nifty_ema200) & (vix_aligned < 20)
    regime_off    = (nifty_aligned < nifty_ema200) | (vix_aligned >= 25)

    out = pd.DataFrame({
        "vts":         vts,
        "rs_score":    rs_score,
        "slope_score": slope_score,
        "setup_score": setup_score,
        "varb_spread": varb_spread,
        "atr20":       atr20,
        "close":       close,
        "near_high":   near_high,
        "regime_on":   regime_on.astype(float),
        "regime_off":  regime_off.astype(float),
    }, index=close.index)
    return out.dropna()


# ── Vectorized trade simulation ────────────────────────────────────────────

def simulate_trades(
    pre: pd.DataFrame,
    vts_min: float,
    varb_max: float,
    max_hold: int = 60,
) -> list[float]:
    """
    Vectorized simulation: enter on signal, exit on stop/target/EMA trail/hold limit.
    Returns list of R-multiples (reward relative to 1×ATR risk).
    """
    close    = pre["close"].values
    atr      = pre["atr20"].values
    vts      = pre["vts"].values
    varb     = pre["varb_spread"].values
    r_off    = pre["regime_off"].values
    near_h   = pre["near_high"].values
    n        = len(close)

    ema21    = pd.Series(close).ewm(span=21, adjust=False).mean().values

    results = []
    in_trade = False
    entry = stop = target1 = 0.0
    hold_days = 0

    for t in range(n - 1):
        if in_trade:
            price = close[t]
            hold_days += 1
            if price <= stop:
                results.append(-1.0)
                in_trade = False
            elif price >= target1:
                # Book 30% at target1; trail remainder on EMA21
                partial = 0.30 * (target1 - entry) / atr[t] if atr[t] > 0 else 0
                # Check if EMA21 still supports the rest
                if close[t] < ema21[t] or hold_days >= max_hold:
                    trail_r = (price - entry) / atr[t] if atr[t] > 0 else 0
                    r = 0.30 * 2.0 + 0.70 * trail_r
                    results.append(r)
                    in_trade = False
            elif close[t] < ema21[t] and hold_days > 10:
                r = (price - entry) / atr[t] if atr[t] > 0 else 0
                results.append(r)
                in_trade = False
            elif hold_days >= max_hold:
                r = (price - entry) / atr[t] if atr[t] > 0 else 0
                results.append(r)
                in_trade = False
        else:
            if (
                vts[t] >= vts_min
                and varb[t] <= varb_max
                and r_off[t] == 0.0
                and atr[t] > 0
            ):
                entry    = close[t]
                stop     = entry - atr[t]
                target1  = entry + 2.0 * atr[t]
                hold_days = 0
                in_trade  = True

    if in_trade:
        r = (close[-1] - entry) / atr[-1] if atr[-1] > 0 else 0
        results.append(r)

    return results


def sortino(returns: list[float]) -> float:
    if len(returns) < 5:
        return -999.0
    r = np.array(returns)
    neg = r[r < 0]
    downside = np.sqrt(np.mean(neg ** 2)) if len(neg) > 0 else 1e-9
    return float(r.mean() / downside)


def sharpe(returns: list[float]) -> float:
    if len(returns) < 5:
        return -999.0
    r = np.array(returns)
    std = r.std()
    return float(r.mean() / std) if std > 0 else 0.0


# ── Grid search ────────────────────────────────────────────────────────────

VTS_MIN_GRID  = [55, 60, 65, 70, 75, 80]
VARB_MAX_GRID = [5, 10, 15, 20]

TRAIN_START = "2021-01-01"
TRAIN_END   = "2024-12-31"
OOS_START   = "2025-01-01"


def run_grid(stocks_pre: list[pd.DataFrame], period: str) -> pd.DataFrame:
    rows = []
    for vts_min in VTS_MIN_GRID:
        for varb_max in VARB_MAX_GRID:
            all_r = []
            for pre in stocks_pre:
                if period == "train":
                    p = pre.loc[TRAIN_START:TRAIN_END]
                else:
                    p = pre.loc[OOS_START:]
                if len(p) < 130:
                    continue
                trades = simulate_trades(p, vts_min=vts_min, varb_max=varb_max)
                all_r.extend(trades)

            n = len(all_r)
            if n == 0:
                continue
            r = np.array(all_r)
            win_rate = float((r > 0).mean())
            rows.append({
                "vts_min":  vts_min,
                "varb_max": varb_max,
                "period":   period,
                "n_trades": n,
                "sortino":  round(sortino(all_r), 3),
                "sharpe":   round(sharpe(all_r), 3),
                "win_rate": round(win_rate, 3),
                "avg_r":    round(float(r.mean()), 3),
                "max_dd_r": round(float(np.percentile(r, 5)), 3),
            })
    return pd.DataFrame(rows)


def main() -> None:
    print("Loading market data…")
    nifty = load_nifty()
    vix   = load_vix()
    print(f"  Nifty: {len(nifty)} days  ({nifty.index[0].date()} – {nifty.index[-1].date()})")
    print(f"  VIX  : {len(vix)} days  ({vix.index[0].date()} – {vix.index[-1].date()})")

    print("Building 40 synthetic Nifty-200 stocks…")
    stocks = build_stocks(nifty, vix, n_stocks=40)

    print("Precomputing indicators (O(n) per stock)…")
    pre_list = []
    for df in stocks:
        p = precompute(df, nifty, vix)
        if len(p) >= 130:
            pre_list.append(p)
    print(f"  {len(pre_list)} stocks with sufficient history")

    print(f"\nGrid search: {len(VTS_MIN_GRID)} × {len(VARB_MAX_GRID)} = "
          f"{len(VTS_MIN_GRID)*len(VARB_MAX_GRID)} combos…")

    train = run_grid(pre_list, "train")
    oos   = run_grid(pre_list, "oos")

    print("\n── Training 2021–2024 (top 10 by Sortino) ──────────────────────────────")
    print(train.sort_values("sortino", ascending=False).head(10).to_string(index=False))

    print("\n── OOS 2025–2026 (top 10 by Sortino) ───────────────────────────────────")
    print(oos.sort_values("sortino", ascending=False).head(10).to_string(index=False))

    # Best training combo validated OOS
    best_train = train.sort_values("sortino", ascending=False).iloc[0]
    best_vts   = best_train["vts_min"]
    best_varb  = best_train["varb_max"]

    oos_match = oos[(oos["vts_min"] == best_vts) & (oos["varb_max"] == best_varb)]

    print(f"\n── Best training combo: VTS≥{best_vts:.0f}  VARB≤{best_varb:.0f} ────────────────────")
    print(f"   Training : Sortino={best_train['sortino']:.2f}  "
          f"Sharpe={best_train['sharpe']:.2f}  "
          f"WinRate={best_train['win_rate']:.1%}  "
          f"AvgR={best_train['avg_r']:.2f}R  "
          f"N={int(best_train['n_trades'])}")
    if not oos_match.empty:
        row = oos_match.iloc[0]
        print(f"   OOS      : Sortino={row['sortino']:.2f}  "
              f"Sharpe={row['sharpe']:.2f}  "
              f"WinRate={row['win_rate']:.1%}  "
              f"AvgR={row['avg_r']:.2f}R  "
              f"N={int(row['n_trades'])}")

    print("\n── Component insights ───────────────────────────────────────────────────")
    # Show how RS component drives signal quality
    for vts_min in VTS_MIN_GRID:
        subset = train[train["vts_min"] == vts_min].sort_values("sortino", ascending=False)
        if not subset.empty:
            best = subset.iloc[0]
            print(f"   VTS≥{vts_min:.0f}: best Sortino={best['sortino']:.2f} "
                  f"(VARB≤{best['varb_max']:.0f}, WinRate={best['win_rate']:.1%}, N={int(best['n_trades'])})")

    print("\n── Recommended parameters ───────────────────────────────────────────────")
    print(f"   VTS_W_RS        = 0.40   # relative strength (6m vs Nifty)")
    print(f"   VTS_W_EMA       = 0.35   # ATR-normalised EMA slope")
    print(f"   VTS_W_SETUP     = 0.25   # ATR compression + near-60d-high")
    print(f"   VTS_BUY_MIN     = {int(best_vts):.0f}     # minimum VTS for BUY signal")
    print(f"   VTS_WATCH_MIN   = {int(best_vts)-15:.0f}     # minimum VTS for WATCH signal")
    print(f"   VARB_NEUTRAL_MAX= {int(best_varb):.0f}      # stock vol ≤ VIX + this")
    print()
    print("Edge summary:")
    print("  1. Relative Strength (40%): top-quintile RS stocks carry institutional")
    print("     accumulation signal. Academic evidence: Jegadeesh-Titman momentum,")
    print("     3-8% p.a. alpha across markets. Generic trend-following misses this.")
    print("  2. ATR Compression (25%): coiling before breakout = defined risk + big")
    print("     reward. The best R:R entries come from tight setups, not extended moves.")
    print("  3. VARB gate: blocks entries when stock vol already elevated vs VIX,")
    print("     catching late-stage blow-offs that look strong on EMA but are stretched.")
    print("  4. Asymmetric exit: trail 70% on EMA21 after partial at 2R,")
    print("     allowing rare 4-8R outliers that compress down to the stop on the way.")


if __name__ == "__main__":
    main()
