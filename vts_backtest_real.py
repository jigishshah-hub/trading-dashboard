"""
vts_backtest_real.py — VTS backtest on real NSE data.

Downloads 5 years of OHLCV for all Nifty 200 stocks via yfinance,
runs the same grid search as vts_optimize.py, and writes results to
backtest_results.json (picked up by the GitHub Actions artifact).

Run in GitHub Actions only (yfinance works there).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Nifty 200 symbols ─────────────────────────────────────────────────────

NIFTY_200 = [
    "RELIANCE","TCS","HDFCBANK","BHARTIARTL","ICICIBANK","INFOSYS","SBIN","HINDUNILVR",
    "ITC","LT","KOTAKBANK","HCLTECH","AXISBANK","BAJFINANCE","MARUTI","ONGC","NTPC",
    "TITAN","SUNPHARMA","BAJAJFINSV","ADANIPORTS","ADANIENT","WIPRO","ULTRACEMCO",
    "TECHM","TATAMOTORS","HINDALCO","COALINDIA","GRASIM","TATASTEEL","INDUSINDBK",
    "JSWSTEEL","POWERGRID","TRENT","DABUR","HAVELLS","COLPAL","NESTLEIND","PIDILITIND",
    "BERGEPAINT","ASIANPAINT","GODREJCP","MARICO","BRITANNIA","DMART","ZOMATO","NYKAA",
    "PAYTM","POLICYBZR","IRFC","RECLTD","PFC","HAL","BEL","BHEL","CONCOR","SIEMENS",
    "ABB","CUMMINSIND","THERMAX","TIMKEN","SKFINDIA","BPCL","IOC","GAIL","PETRONET",
    "MGL","IGL","ATGL","TATAPOWER","ADANIGREEN","TORNTPOWER","NHPC","SJVN","CESC",
    "KPRMILL","TRIDENT","WELSPUNLIV","PAGEIND","MUTHOOTFIN","BAJAJHLDNG","CHOLAFIN",
    "LICHSGFIN","HDFCLIFE","SBILIFE","ICICIGI","STARHEALTH","MAX","PIIND","AARTIIND",
    "ALKEM","AUROPHARMA","BIOCON","CIPLA","DIVISLAB","DRREDDY","GLENMARK","IPCA",
    "LUPIN","NATCOPHARMA","PFIZER","SANOFI","TORNTPHARM","ZYDUSLIFE","APOLLOHOSP",
    "FORTIS","LALPATHLAB","METROPOLIS","THYROCARE","IPCALAB","NAUKRI","JUSTDIAL",
    "INDIAMART","MAPMYINDIA","IRCTC","RVNL","RAILVIKAS","NBCC","HUDCO","NCC",
    "KPITTECH","LTTS","MASTEK","MPHASIS","PERSISTENT","COFORGE","BIRLASOFT",
    "HEXAWARE","ZENSAR","RCOMPARE","TATACOMM","TEJASNET","ROUTE","INTELLECT",
    "CYIENT","CAMS","CDSL","BSE","MCX","MCXINDIA","ANGELONE","MOTILALOFS","IIFLWAM",
    "360ONE","AFFLE","INFOEDGE","POLICYBZR","EASEMYTRIP","CARTRADE","DELHIVERY",
    "NAZARA","HFCL","STLTECH","TATAELXSI","LTIM","INFY","HCLTECH","WIPRO","TCS",
    "OFSS","MFSL","SHRIRAMFIN","SUNDARMFIN","UGROCAP","APTUS","HOMEFIRST",
    "AAVAS","CREDACC","FIVESTAR","SBFC","JMFINANCL","EDELWEISS","GEOJITFSL",
    "MANAPPURAM","ARMANFIN","REPCO","EQUITASBNK","SURYODAY","UJJIVAN","ESAFSFB",
    "IDFCFIRSTB","BANDHANBNK","DCBBANK","SOUTHBANK","KARURVSYA","LAKSHVILAS",
    "CSBBANK","TMB","RBLBANK","FEDERALBNK","JKBANK","CITYUNIONBK","TAMILNADBNK",
    "KARURVYSYA","UJJIVANSFB","AUBANK","FINOPAYTECH",
]
# Deduplicate and limit to 200
NIFTY_200 = list(dict.fromkeys(NIFTY_200))[:200]


def fetch_all(symbols: list[str], period: str = "5y") -> dict[str, pd.DataFrame]:
    """Bulk yfinance download, returns {symbol: ohlcv_df}."""
    yf_syms = [s + ".NS" for s in symbols]
    print(f"Downloading {len(yf_syms)} symbols (period={period})…")
    raw = yf.download(
        yf_syms, period=period, group_by="ticker",
        auto_adjust=True, progress=True, threads=True,
    )
    result = {}
    for sym, yf_sym in zip(symbols, yf_syms):
        try:
            df = raw[yf_sym].copy() if len(yf_syms) > 1 else raw.copy()
            df = df.dropna(subset=["Close"])
            if len(df) < 200:
                continue
            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index)
            result[sym] = df
        except (KeyError, TypeError):
            pass
    print(f"  Got {len(result)}/{len(symbols)} symbols")
    return result


def fetch_index(ticker: str, period: str = "5y") -> pd.Series:
    df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
    s = df["Close"].squeeze()
    s.index = pd.to_datetime(s.index)
    return s.dropna()


# ── Precompute VTS + IAS components ──────────────────────────────────────

def precompute(df: pd.DataFrame, nifty: pd.Series, vix_series: pd.Series) -> pd.DataFrame:
    close  = df["close"]
    high, low = df["high"], df["low"]
    volume = df["volume"]

    nifty_al    = nifty.reindex(close.index, method="ffill")
    vix_al      = vix_series.reindex(close.index, method="ffill")

    # Shared: ATR20, EMA20
    prev_close = close.shift(1)
    tr    = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    atr_pct = atr20 / ema20.replace(0, np.nan) * 100

    # Shared: VARB
    log_ret   = np.log(close / close.shift(1))
    realvol20 = log_ret.rolling(20).std() * np.sqrt(252) * 100
    varb      = realvol20 - vix_al

    # Shared: Regime
    nifty_ema200 = nifty_al.ewm(span=200, adjust=False).mean()
    regime_off   = ((nifty_al < nifty_ema200) | (vix_al >= 25)).astype(float)

    # ── VTS components ───────────────────────────────────────────────────────
    slope_raw   = (ema20 - ema20.shift(10)) / ema20.replace(0, np.nan) * 100
    slope_score = ((slope_raw / atr_pct.replace(0, np.nan)).clip(-3, 3) + 3.0) / 6.0 * 100

    stock_ret_126 = close / close.shift(126) - 1.0
    nifty_ret_126 = nifty_al / nifty_al.shift(126) - 1.0
    rs_score_6m   = ((stock_ret_126 - nifty_ret_126 + 0.60) / 1.20 * 100).clip(0, 100)

    atr_pct_max60 = atr_pct.rolling(60).max()
    compression   = (1.0 - atr_pct / atr_pct_max60.replace(0, np.nan)).clip(0, 1)
    high_60d      = close.rolling(60).max()
    near_high     = (close >= 0.95 * high_60d).astype(float)
    setup_score   = (compression * 80.0 + near_high * 20.0).clip(0, 100)

    vts = 0.40 * rs_score_6m + 0.35 * slope_score + 0.25 * setup_score

    # ── IAS components ───────────────────────────────────────────────────────
    # 1. Accumulation Pattern Score: up-day volume vs down-day volume
    daily_ret  = close - close.shift(1)
    up_mask    = (daily_ret > 0).astype(float)
    dn_mask    = (daily_ret <= 0).astype(float)
    avg_vol_up = (volume * up_mask).rolling(20).sum() / up_mask.rolling(20).sum().clip(lower=1)
    avg_vol_dn = (volume * dn_mask).rolling(20).sum() / dn_mask.rolling(20).sum().clip(lower=1)
    ar         = (avg_vol_up / avg_vol_dn.replace(0, np.nan)).fillna(1.0)
    aps        = ((ar - 0.7) / (2.5 - 0.7) * 100).clip(0, 100)

    # 2. Base Quality Score: tightness of 20-day range + ATH proximity
    high_20d       = close.rolling(20).max()
    low_20d        = close.rolling(20).min()
    base_range_pct = (high_20d - low_20d) / low_20d.replace(0, np.nan) * 100
    tightness      = (1.0 - base_range_pct / 15.0).clip(0, 1) * 100
    high_252d      = close.rolling(min(252, len(close))).max()
    ath_prox       = (close / high_252d.replace(0, np.nan)).clip(0, 1)
    ath_bonus      = ((ath_prox - 0.90) / 0.10).clip(0, 1) * 20
    bqs            = (tightness * 0.80 + ath_bonus).clip(0, 100)

    # 3. Relative Momentum 3m
    stock_ret_63 = close / close.shift(63) - 1.0
    nifty_ret_63 = nifty_al / nifty_al.shift(63) - 1.0
    rms          = ((stock_ret_63 - nifty_ret_63 + 0.30) / 0.60 * 100).clip(0, 100)

    ias = (0.40 * aps + 0.35 * bqs + 0.25 * rms).clip(0, 100)

    # Breakout trigger: close > prev-20d-high AND volume > 1.5× avg
    high_20d_lag = close.shift(1).rolling(20).max()
    vol_avg_20   = volume.rolling(20).mean()
    breakout     = ((close > high_20d_lag) & (volume > 1.5 * vol_avg_20)).astype(float)

    out = pd.DataFrame({
        # VTS
        "vts": vts, "rs_score": rs_score_6m, "slope_score": slope_score,
        "setup_score": setup_score,
        # IAS
        "ias": ias, "aps": aps, "bqs": bqs, "rms": rms, "breakout": breakout,
        # Shared
        "varb_spread": varb, "atr20": atr20, "close": close,
        "near_high": near_high, "regime_off": regime_off,
    }, index=close.index)
    return out.dropna()


# ── Trade simulation ──────────────────────────────────────────────────────

def simulate(pre: pd.DataFrame, vts_min: float, varb_max: float, return_dates: bool = False):
    dates = pre.index
    close = pre["close"].values
    atr   = pre["atr20"].values
    vts   = pre["vts"].values
    varb  = pre["varb_spread"].values
    r_off = pre["regime_off"].values
    ema21 = pd.Series(close).ewm(span=21, adjust=False).mean().values
    n = len(close)

    results, in_trade = [], False
    entry_dates = [] if return_dates else None
    entry = stop = target1 = 0.0
    hold = 0
    entry_date = None

    for t in range(n - 1):
        if in_trade:
            hold += 1
            p = close[t]
            if p <= stop:
                results.append(-1.0)
                if return_dates: entry_dates.append(entry_date)
                in_trade = False
            elif p >= target1:
                if close[t] < ema21[t] or hold >= 60:
                    trail_r = (p - entry) / atr[t] if atr[t] > 0 else 0
                    results.append(0.30 * 2.0 + 0.70 * trail_r)
                    if return_dates: entry_dates.append(entry_date)
                    in_trade = False
            elif close[t] < ema21[t] and hold > 10:
                results.append((p - entry) / atr[t] if atr[t] > 0 else 0)
                if return_dates: entry_dates.append(entry_date)
                in_trade = False
            elif hold >= 60:
                results.append((p - entry) / atr[t] if atr[t] > 0 else 0)
                if return_dates: entry_dates.append(entry_date)
                in_trade = False
        else:
            if vts[t] >= vts_min and varb[t] <= varb_max and r_off[t] == 0 and atr[t] > 0:
                entry = close[t]; stop = entry - atr[t]
                target1 = entry + 2 * atr[t]; hold = 0; in_trade = True
                entry_date = dates[t]
    if in_trade and atr[-1] > 0:
        results.append((close[-1] - entry) / atr[-1])
        if return_dates: entry_dates.append(entry_date)
    if return_dates:
        return results, entry_dates
    return results


def simulate_ias(pre: pd.DataFrame, ias_min: float, varb_max: float, return_dates: bool = False):
    """IAS backtest: enter ONLY on volume-confirmed breakout days with IAS >= ias_min."""
    dates    = pre.index
    close    = pre["close"].values
    atr      = pre["atr20"].values
    ias      = pre["ias"].values
    varb     = pre["varb_spread"].values
    r_off    = pre["regime_off"].values
    breakout = pre["breakout"].values
    ema21    = pd.Series(close).ewm(span=21, adjust=False).mean().values
    n = len(close)

    results, in_trade = [], False
    entry_dates = [] if return_dates else None
    entry = stop = target1 = 0.0
    hold = 0
    entry_date = None

    for t in range(n - 1):
        if in_trade:
            hold += 1
            p = close[t]
            if p <= stop:
                results.append(-1.0)
                if return_dates: entry_dates.append(entry_date)
                in_trade = False
            elif p >= target1:
                if close[t] < ema21[t] or hold >= 60:
                    trail_r = (p - entry) / atr[t] if atr[t] > 0 else 0
                    results.append(0.30 * 2.0 + 0.70 * trail_r)
                    if return_dates: entry_dates.append(entry_date)
                    in_trade = False
            elif close[t] < ema21[t] and hold > 10:
                results.append((p - entry) / atr[t] if atr[t] > 0 else 0)
                if return_dates: entry_dates.append(entry_date)
                in_trade = False
            elif hold >= 60:
                results.append((p - entry) / atr[t] if atr[t] > 0 else 0)
                if return_dates: entry_dates.append(entry_date)
                in_trade = False
        else:
            # IAS entry: quality score + CONFIRMED BREAKOUT (volume + price level)
            if (ias[t] >= ias_min and breakout[t] == 1 and
                    varb[t] <= varb_max and r_off[t] == 0 and atr[t] > 0):
                entry = close[t]; stop = entry - atr[t]
                target1 = entry + 2 * atr[t]; hold = 0; in_trade = True
                entry_date = dates[t]
    if in_trade and atr[-1] > 0:
        results.append((close[-1] - entry) / atr[-1])
        if return_dates: entry_dates.append(entry_date)
    if return_dates:
        return results, entry_dates
    return results


def sortino(r: list[float]) -> float:
    if len(r) < 5: return -999.0
    a = np.array(r); neg = a[a < 0]
    dd = np.sqrt(np.mean(neg**2)) if len(neg) > 0 else 1e-9
    return float(a.mean() / dd)

def sharpe(r: list[float]) -> float:
    if len(r) < 5: return -999.0
    a = np.array(r); s = a.std()
    return float(a.mean() / s) if s > 0 else 0.0


# ── Grid search ────────────────────────────────────────────────────────────

VTS_GRID  = [55, 60, 65, 70, 75, 80]
VARB_GRID = [5, 10, 15, 20]

TRAIN_START = "2021-01-01"; TRAIN_END = "2024-12-31"
OOS_START   = "2025-01-01"


def grid_search(pre_list: list[pd.DataFrame], period: str) -> list[dict]:
    rows = []
    for vts_min in VTS_GRID:
        for varb_max in VARB_GRID:
            all_r = []
            for pre in pre_list:
                p = pre.loc[TRAIN_START:TRAIN_END] if period == "train" else pre.loc[OOS_START:]
                if len(p) >= 130:
                    all_r.extend(simulate(p, vts_min, varb_max))
            if not all_r: continue
            a = np.array(all_r)
            rows.append({
                "vts_min": vts_min, "varb_max": varb_max, "period": period,
                "n_trades": len(all_r), "sortino": round(sortino(all_r), 3),
                "sharpe": round(sharpe(all_r), 3),
                "win_rate": round(float((a > 0).mean()), 3),
                "avg_r": round(float(a.mean()), 3),
                "p5_r": round(float(np.percentile(a, 5)), 3),
                "p95_r": round(float(np.percentile(a, 95)), 3),
            })
    return rows


def main():
    print("=== VTS Backtest on Real NSE Data ===\n")

    # VIX from local file (already in repo)
    vix_raw = Path("series/vix.txt").read_text().strip()
    vix_rows = [r.split(",") for r in vix_raw.split(";") if "," in r]
    vix = pd.Series({pd.Timestamp(d): float(v) for d, v in vix_rows}, dtype=float).sort_index()
    print(f"VIX loaded: {len(vix)} days ({vix.index[0].date()} – {vix.index[-1].date()})")

    # Nifty from yfinance (covers 2020–2026)
    print("Fetching Nifty 50…")
    nifty = fetch_index("^NSEI", period="6y")
    print(f"Nifty loaded: {len(nifty)} days ({nifty.index[0].date()} – {nifty.index[-1].date()})")

    # Stocks
    ohlcv = fetch_all(NIFTY_200, period="6y")
    print(f"\nStocks with data: {len(ohlcv)}")

    # Precompute
    print("Precomputing indicators…")
    pre_list = []
    for sym, df in ohlcv.items():
        try:
            p = precompute(df, nifty, vix)
            if len(p) >= 200:
                pre_list.append((sym, p))
        except Exception as e:
            pass
    print(f"  {len(pre_list)} stocks with sufficient history for backtest\n")

    pre_only = [p for _, p in pre_list]

    print(f"VTS grid search: {len(VTS_GRID)}×{len(VARB_GRID)} combos × 2 periods…")
    train_rows = grid_search(pre_only, "train")
    oos_rows   = grid_search(pre_only, "oos")

    # IAS grid search (breakout-confirmed entries)
    IAS_GRID = [55, 60, 65, 70, 75]
    print(f"IAS grid search: {len(IAS_GRID)}×{len(VARB_GRID)} combos × 2 periods…")
    ias_train_rows, ias_oos_rows = [], []
    for ias_min in IAS_GRID:
        for varb_max in VARB_GRID:
            for period, start, end in [("train", TRAIN_START, TRAIN_END), ("oos", OOS_START, None)]:
                all_r = []
                for pre in pre_only:
                    p = pre.loc[start:end] if end else pre.loc[start:]
                    if len(p) >= 130:
                        all_r.extend(simulate_ias(p, ias_min, varb_max))
                if not all_r:
                    continue
                a = np.array(all_r)
                row = {
                    "ias_min": ias_min, "varb_max": varb_max, "period": period,
                    "n_trades": len(all_r), "sortino": round(sortino(all_r), 3),
                    "sharpe": round(sharpe(all_r), 3),
                    "win_rate": round(float((a > 0).mean()), 3),
                    "avg_r": round(float(a.mean()), 3),
                    "p5_r": round(float(np.percentile(a, 5)), 3),
                    "p95_r": round(float(np.percentile(a, 95)), 3),
                }
                (ias_train_rows if period == "train" else ias_oos_rows).append(row)

    # Save results
    results = {
        "vts_train": train_rows, "vts_oos": oos_rows,
        "ias_train": ias_train_rows, "ias_oos": ias_oos_rows,
        "n_stocks": len(pre_list),
    }
    Path("backtest_results.json").write_text(json.dumps(results, indent=2))
    print("Saved backtest_results.json")

    from collections import Counter

    # ── VTS results ─────────────────────────────────────────────────────────
    train_df = pd.DataFrame(train_rows).sort_values("sortino", ascending=False)
    oos_df   = pd.DataFrame(oos_rows).sort_values("sortino", ascending=False)

    print("\n════ VTS (momentum-quality score, enter any qualifying day) ════════")
    print("\n-- Training 2021-2024 (top 8 by Sortino) --")
    print(train_df.head(8).to_string(index=False))

    best_vts = train_df.iloc[0]
    print(f"\nBest VTS: VTS>={best_vts['vts_min']:.0f} VARB<={best_vts['varb_max']:.0f}")
    print(f"  Train: Sortino={best_vts['sortino']:.2f}  WinRate={best_vts['win_rate']:.1%}  "
          f"AvgR={best_vts['avg_r']:.2f}R  N={int(best_vts['n_trades'])}")
    oos_vts = oos_df[(oos_df["vts_min"]==best_vts["vts_min"]) & (oos_df["varb_max"]==best_vts["varb_max"])]
    if not oos_vts.empty:
        r = oos_vts.iloc[0]
        print(f"  OOS:   Sortino={r['sortino']:.2f}  WinRate={r['win_rate']:.1%}  "
              f"AvgR={r['avg_r']:.2f}R  N={int(r['n_trades'])}")

    # ── IAS results ─────────────────────────────────────────────────────────
    ias_train_df = pd.DataFrame(ias_train_rows).sort_values("sortino", ascending=False)
    ias_oos_df   = pd.DataFrame(ias_oos_rows).sort_values("sortino", ascending=False)

    print("\n════ IAS (accumulation pattern + breakout confirmation) ════════════")
    print("\n-- Training 2021-2024 (top 8 by Sortino) --")
    print(ias_train_df.head(8).to_string(index=False))

    best_ias = ias_train_df.iloc[0]
    print(f"\nBest IAS: IAS>={best_ias['ias_min']:.0f} VARB<={best_ias['varb_max']:.0f}")
    print(f"  Train: Sortino={best_ias['sortino']:.2f}  WinRate={best_ias['win_rate']:.1%}  "
          f"AvgR={best_ias['avg_r']:.2f}R  N={int(best_ias['n_trades'])}")
    oos_ias = ias_oos_df[(ias_oos_df["ias_min"]==best_ias["ias_min"]) & (ias_oos_df["varb_max"]==best_ias["varb_max"])]
    if not oos_ias.empty:
        r = oos_ias.iloc[0]
        print(f"  OOS:   Sortino={r['sortino']:.2f}  WinRate={r['win_rate']:.1%}  "
              f"AvgR={r['avg_r']:.2f}R  N={int(r['n_trades'])}")

    # ── Head-to-head year-by-year ────────────────────────────────────────────
    print("\n════ Year-by-year: VTS≥75/VARB≤10 vs IAS≥65/VARB≤15 ══════════════")
    print(f"  {'Year':<6} {'VTS trades':>10} {'VTS WR':>8} {'VTS AvgR':>9} │ "
          f"{'IAS trades':>10} {'IAS WR':>8} {'IAS AvgR':>9}")
    print("  " + "-"*70)

    def year_stats(sim_fn, pre_list, vts_min, varb_max, use_ias=False):
        yc: Counter = Counter(); yr_dict: dict = {}
        for sym, pre in pre_list:
            for period_start, period_end in [(TRAIN_START, TRAIN_END), (OOS_START, None)]:
                p = pre.loc[period_start:period_end] if period_end else pre.loc[period_start:]
                if len(p) >= 130:
                    rs, dates = sim_fn(p, vts_min, varb_max, return_dates=True)
                    for r, d in zip(rs, dates):
                        yr = d.year; yc[yr] += 1
                        yr_dict.setdefault(yr, []).append(r)
        return yc, yr_dict

    vts_yc, vts_yr = year_stats(simulate,     pre_list, 75, 10)
    ias_yc, ias_yr = year_stats(simulate_ias,  pre_list, 65, 15)

    for yr in sorted(set(list(vts_yc.keys()) + list(ias_yc.keys()))):
        def fmt(yc, yr_d, yr):
            if yr not in yc: return f"{'—':>10} {'—':>8} {'—':>9}"
            a = np.array(yr_d[yr])
            return f"{yc[yr]:>10d} {(a>0).mean():>8.0%} {a.mean():>+9.2f}R"
        print(f"  {yr:<6} {fmt(vts_yc, vts_yr, yr)} │ {fmt(ias_yc, ias_yr, yr)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
