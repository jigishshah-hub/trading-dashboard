"""
vts_optimize.py — Grid-search optimal VTS weights via vectorized backtest.

Approach:
  1. Generate 40 synthetic Nifty-200-calibrated stock paths (real Nifty/VIX).
  2. Pre-compute ALL indicators (EMA20, ATR20, vol_ratio, realvol, regime)
     for every stock and every date — ONE pass, O(n).
  3. For each parameter combo, apply thresholds to the precomputed matrix —
     no repeated rolling calculations.

This makes the 80-combo grid search run in seconds, not hours.

Run:  python vts_optimize.py
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

np.random.seed(42)

# ── Config ─────────────────────────────────────────────────────────────────
TRAIN_START = "2021-01-01"
TRAIN_END   = "2024-12-31"
OOS_START   = "2025-01-01"
OOS_END     = "2026-09-28"

N_STOCKS      = 40
MAX_HOLD_DAYS = 60
MIN_TRADES    = 20  # skip combos with fewer trades

W_EMA_GRID    = [0.3, 0.4, 0.5, 0.6, 0.7]
VTS_BUY_GRID  = [60.0, 65.0, 70.0, 75.0]
VARB_MAX_GRID = [5.0, 10.0, 15.0, 20.0]


# ── Data loaders ───────────────────────────────────────────────────────────

def _parse_series(path: str) -> pd.Series:
    raw = Path(path).read_text().strip()
    rows = [r.split(",") for r in raw.split(";") if "," in r]
    return pd.Series(
        {pd.Timestamp(d): float(v) for d, v in rows}, dtype=float
    ).sort_index()


# ── Synthetic universe ─────────────────────────────────────────────────────

def make_universe(nifty_ret: pd.Series, n: int = 40) -> list[pd.DataFrame]:
    rng = np.random.default_rng(2024)
    universe = []
    dates = nifty_ret.index

    for _ in range(n):
        beta      = rng.uniform(0.4, 1.2)
        alpha     = rng.uniform(-0.005, 0.025) / 252
        idio_vol  = rng.uniform(0.15, 0.28) / np.sqrt(252)
        momentum  = rng.uniform(0.05, 0.25)

        noise = rng.normal(0, idio_vol, len(dates))
        carry = np.zeros(len(dates))
        nret  = nifty_ret.values
        for i in range(1, len(dates)):
            carry[i] = momentum * (beta * nret[i - 1] + noise[i - 1])

        ret    = alpha + beta * nret + noise + carry
        prices = 100.0 * np.exp(np.cumsum(ret))

        dv  = idio_vol * 2
        highs   = prices * (1 + rng.uniform(0, dv, len(dates)))
        lows    = prices * (1 - rng.uniform(0, dv, len(dates)))
        opens   = prices * (1 + rng.uniform(-dv / 2, dv / 2, len(dates)))
        base_v  = rng.lognormal(np.log(500_000), 0.5)
        volumes = base_v * rng.lognormal(0, 0.6, len(dates))

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows,
             "close": prices, "volume": volumes},
            index=dates,
        )
        universe.append(df)

    return universe


# ── Vectorised indicator computation ──────────────────────────────────────

def compute_indicators(df: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    """
    Return a DataFrame with all VTS-related columns pre-computed.
    No growing slices — all pandas rolling/ewm ops are O(n).
    """
    out = pd.DataFrame(index=df.index)
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    # EMA20 and its slope over 10 days
    ema20       = close.ewm(span=20, adjust=False).mean()
    ema20_lag10 = ema20.shift(10)
    slope_raw   = (ema20 - ema20_lag10) / ema20_lag10.replace(0, np.nan) * 100

    # ATR20
    prev_close  = close.shift(1)
    tr          = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr20       = tr.rolling(20).mean()

    # ATR-normalised slope → ema_score [0, 100]
    atr_pct     = atr20 / ema20.replace(0, np.nan) * 100
    slope_norm  = (slope_raw / atr_pct.replace(0, np.nan)).clip(-3, 3)
    ema_score   = (slope_norm + 3.0) / 6.0 * 100

    # Volume score [0, 100]
    vol_avg   = vol.rolling(20).mean()
    vol_ratio = vol / vol_avg.replace(0, np.nan)
    vol_score = ((vol_ratio - 0.5) / 2.5).clip(0, 1) * 100

    # Realised vol (annualised %)
    log_ret  = np.log(close / close.shift(1))
    realvol  = log_ret.rolling(20).std() * np.sqrt(252) * 100

    # India VIX aligned to stock dates
    vix_aligned = vix.reindex(df.index, method="ffill")

    out["ema20"]      = ema20
    out["atr20"]      = atr20
    out["ema_score"]  = ema_score
    out["vol_ratio"]  = vol_ratio
    out["vol_score"]  = vol_score
    out["realvol"]    = realvol
    out["vix"]        = vix_aligned
    out["varb"]       = realvol - vix_aligned
    out["close"]      = close
    out["low"]        = low
    # EMA21 for trail-stop (computed once, used in simulation)
    out["ema21"]      = close.ewm(span=21, adjust=False).mean()

    return out.dropna()


def add_regime(ind: pd.DataFrame, nifty: pd.Series, vix: pd.Series) -> pd.DataFrame:
    """Add regime column: risk_on / selective / risk_off."""
    ema200 = nifty.ewm(span=200, adjust=False).mean()
    nifty_aligned  = nifty.reindex(ind.index,  method="ffill")
    ema200_aligned = ema200.reindex(ind.index, method="ffill")
    vix_aligned    = vix.reindex(ind.index,    method="ffill")

    cond_off   = (nifty_aligned < ema200_aligned) | (vix_aligned >= 25)
    cond_on    = (nifty_aligned > ema200_aligned) & (vix_aligned < 20)
    regime     = pd.Series("selective", index=ind.index)
    regime[cond_on]  = "risk_on"
    regime[cond_off] = "risk_off"
    ind = ind.copy()
    ind["regime"] = regime
    return ind


# ── Vectorised trade simulation ────────────────────────────────────────────

def simulate_trades_vectorised(
    ind: pd.DataFrame,
    signal_mask: pd.Series,
    period_start: str,
    period_end: str,
) -> np.ndarray:
    """
    For each signal date, simulate the asymmetric exit rule.
    Returns array of R outcomes.
    """
    ts = pd.Timestamp(period_start)
    te = pd.Timestamp(period_end)
    mask = signal_mask & (ind.index >= ts) & (ind.index <= te)
    signal_idx = np.where(mask.values)[0]

    all_r = []
    closes = ind["close"].values
    lows   = ind["low"].values
    ema21s = ind["ema21"].values
    atrs   = ind["atr20"].values

    for si in signal_idx:
        entry = closes[si]
        risk  = atrs[si]
        if risk <= 0 or entry <= 0:
            continue
        stop    = entry - risk
        target1 = entry + 2.0 * risk

        end_idx = min(si + 1 + MAX_HOLD_DAYS, len(closes))
        partial_r  = 0.0
        partial_on = False
        final_r    = None

        for fi in range(si + 1, end_idx):
            c = closes[fi]
            l = lows[fi]

            if l <= stop:
                if partial_on:
                    final_r = partial_r + (stop - entry) / risk * 0.7
                else:
                    final_r = -1.0
                break

            if not partial_on and c >= target1:
                partial_r  = 2.0 * 0.3   # 30% at 2R
                partial_on = True

            if partial_on and c < ema21s[fi]:
                final_r = partial_r + (c - entry) / risk * 0.7
                break

        if final_r is None:
            # max-hold exit
            c = closes[end_idx - 1]
            hr = (c - entry) / risk
            final_r = (partial_r + hr * 0.7) if partial_on else hr

        all_r.append(final_r)

    return np.array(all_r)


# ── Metrics ────────────────────────────────────────────────────────────────

def metrics(r_arr: np.ndarray) -> dict:
    if len(r_arr) < MIN_TRADES:
        return {"trades": len(r_arr), "sharpe": np.nan, "sortino": np.nan,
                "win_rate": np.nan, "avg_r": np.nan}
    avg     = r_arr.mean()
    std     = r_arr.std()
    down    = r_arr[r_arr < 0]
    dstd    = down.std() if len(down) > 1 else 1e-9
    return {
        "trades":   len(r_arr),
        "sharpe":   round(avg / std if std > 0 else 0.0, 3),
        "sortino":  round(avg / dstd, 3),
        "win_rate": round((r_arr > 0).mean() * 100, 1),
        "avg_r":    round(avg, 3),
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("VTS Parameter Optimizer — Vectorised Monte Carlo Backtest")
    print("=" * 72)

    nifty = _parse_series("series/nsei.txt")
    vix   = _parse_series("series/vix.txt")
    print(f"Nifty: {len(nifty)} days  |  VIX: {len(vix)} days")

    nifty_ret = np.log(nifty / nifty.shift(1)).dropna()
    print(f"Generating {N_STOCKS} synthetic stocks...")
    stocks = make_universe(nifty_ret, N_STOCKS)

    print("Pre-computing indicators for all stocks (one pass each)...")
    indicators = []
    for df in stocks:
        ind = compute_indicators(df, vix)
        ind = add_regime(ind, nifty, vix)
        indicators.append(ind)
    print(f"Indicators ready. Rows per stock: ~{len(indicators[0])}\n")

    # ── Grid search ────────────────────────────────────────────
    print(f"{'w_ema':>6} {'vts_min':>7} {'varb_max':>8} {'trades':>7} "
          f"{'sharpe':>7} {'sortino':>8} {'win%':>6} {'avg_r':>7}")
    print("-" * 72)

    results = []
    for w_ema, vts_min, varb_max in itertools.product(W_EMA_GRID, VTS_BUY_GRID, VARB_MAX_GRID):
        all_r = []
        for ind in indicators:
            vts = w_ema * ind["ema_score"] + (1 - w_ema) * ind["vol_score"]
            sig = (
                (vts >= vts_min) &
                (ind["varb"] <= varb_max) &
                (ind["regime"].isin(["risk_on", "selective"]))
            )
            r = simulate_trades_vectorised(ind, sig, TRAIN_START, TRAIN_END)
            all_r.extend(r.tolist())

        m = metrics(np.array(all_r))
        results.append({"w_ema": w_ema, "vts_min": vts_min, "varb_max": varb_max, **m})
        print(f"{w_ema:>6.1f} {vts_min:>7.0f} {varb_max:>8.0f} "
              f"{m['trades']:>7}  {str(m['sharpe']):>7} {str(m['sortino']):>8} "
              f"{str(m['win_rate']):>6} {str(m['avg_r']):>7}")

    df_r = pd.DataFrame(results).dropna(subset=["sortino"])
    if df_r.empty:
        print("\nNo valid results — check parameters.")
        return

    best = df_r.sort_values("sortino", ascending=False).iloc[0]

    print("\n" + "=" * 72)
    print("BEST PARAMETERS (by Sortino, training 2021-2024):")
    print(f"  w_ema    = {best['w_ema']}   (EMA weight; vol weight = {1-best['w_ema']:.1f})")
    print(f"  vts_min  = {best['vts_min']:.0f}  (BUY signal threshold)")
    print(f"  varb_max = {best['varb_max']:.0f}  (max RealVol-VIX spread)")
    print(f"  Sharpe   = {best['sharpe']:.3f}")
    print(f"  Sortino  = {best['sortino']:.3f}")
    print(f"  Win rate = {best['win_rate']:.1f}%")
    print(f"  Avg R    = {best['avg_r']:.3f}R per trade")
    print(f"  Trades   = {int(best['trades'])}")

    # OOS
    print("\n" + "=" * 72)
    print(f"OUT-OF-SAMPLE ({OOS_START}–{OOS_END}):")
    oos_r = []
    for ind in indicators:
        vts = best["w_ema"] * ind["ema_score"] + (1 - best["w_ema"]) * ind["vol_score"]
        sig = (
            (vts >= best["vts_min"]) &
            (ind["varb"] <= best["varb_max"]) &
            (ind["regime"].isin(["risk_on", "selective"]))
        )
        r = simulate_trades_vectorised(ind, sig, OOS_START, OOS_END)
        oos_r.extend(r.tolist())
    oos = metrics(np.array(oos_r))
    print(f"  Sharpe   = {oos['sharpe']}  |  Sortino = {oos['sortino']}")
    print(f"  Win rate = {oos['win_rate']}%  |  Avg R = {oos['avg_r']}R  |  Trades = {oos['trades']}")

    # Top 10
    print("\n" + "=" * 72)
    print("Top 10 by Sortino (training):")
    top10 = df_r.sort_values("sortino", ascending=False).head(10)
    print(top10[["w_ema","vts_min","varb_max","trades","sharpe","sortino","win_rate","avg_r"]].to_string(index=False))

    # Sensitivity: best sharpe combo
    best_sharpe = df_r.sort_values("sharpe", ascending=False).iloc[0]
    print("\n" + "=" * 72)
    print("Best by SHARPE (cross-check):")
    print(f"  w_ema={best_sharpe['w_ema']}  vts_min={best_sharpe['vts_min']:.0f}  "
          f"varb_max={best_sharpe['varb_max']:.0f}  "
          f"Sharpe={best_sharpe['sharpe']:.3f}  Sortino={best_sharpe['sortino']:.3f}")

    print("\n" + "=" * 72)
    print("LOCKED CONFIG VALUES for config.py:")
    print(f"  VTS_W_EMA        = {best['w_ema']}")
    print(f"  VTS_BUY_MIN      = {best['vts_min']:.0f}.0")
    print(f"  VTS_WATCH_MIN    = {best['vts_min']-15:.0f}.0")
    print(f"  VARB_NEUTRAL_MAX = {best['varb_max']:.0f}.0")
    print(f"  VARB_CAUTION_MAX = {best['varb_max']+10:.0f}.0")

    out = "/tmp/claude-0/-home-user-trading-dashboard/1e96784f-ccd1-5178-838d-0ab15b6bfbfa/scratchpad/vts_optimize_results.csv"
    df_r.sort_values("sortino", ascending=False).to_csv(out, index=False)
    print(f"\nFull grid → {out}")


if __name__ == "__main__":
    main()
