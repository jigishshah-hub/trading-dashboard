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


# ── Precompute VTS components ─────────────────────────────────────────────

def precompute(df: pd.DataFrame, nifty: pd.Series, vix_series: pd.Series) -> pd.DataFrame:
    close = df["close"]
    high, low = df["high"], df["low"]

    # EMA slope
    ema20 = close.ewm(span=20, adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    atr_pct = atr20 / ema20.replace(0, np.nan) * 100
    slope_raw = (ema20 - ema20.shift(10)) / ema20.replace(0, np.nan) * 100
    slope_score = ((slope_raw / atr_pct.replace(0, np.nan)).clip(-3, 3) + 3.0) / 6.0 * 100

    # Relative strength (6m)
    stock_ret_126 = close / close.shift(126) - 1.0
    nifty_al = nifty.reindex(close.index, method="ffill")
    nifty_ret_126 = nifty_al / nifty_al.shift(126) - 1.0
    rs_score = ((stock_ret_126 - nifty_ret_126 + 0.60) / 1.20 * 100).clip(0, 100)

    # Setup quality
    atr_pct_max60 = atr_pct.rolling(60).max()
    compression = (1.0 - atr_pct / atr_pct_max60.replace(0, np.nan)).clip(0, 1)
    high_60d = close.rolling(60).max()
    near_high = (close >= 0.95 * high_60d).astype(float)
    setup_score = (compression * 80.0 + near_high * 20.0).clip(0, 100)

    vts = 0.40 * rs_score + 0.35 * slope_score + 0.25 * setup_score

    # VARB
    log_ret = np.log(close / close.shift(1))
    realvol20 = log_ret.rolling(20).std() * np.sqrt(252) * 100
    vix_al = vix_series.reindex(close.index, method="ffill")
    varb = realvol20 - vix_al

    # Regime
    nifty_ema200 = nifty_al.ewm(span=200, adjust=False).mean()
    regime_off = ((nifty_al < nifty_ema200) | (vix_al >= 25)).astype(float)

    out = pd.DataFrame({
        "vts": vts, "rs_score": rs_score, "slope_score": slope_score,
        "setup_score": setup_score, "varb_spread": varb,
        "atr20": atr20, "close": close,
        "near_high": near_high, "regime_off": regime_off,
    }, index=close.index)
    return out.dropna()


# ── Trade simulation ──────────────────────────────────────────────────────

def simulate(pre: pd.DataFrame, vts_min: float, varb_max: float) -> list[float]:
    close = pre["close"].values
    atr   = pre["atr20"].values
    vts   = pre["vts"].values
    varb  = pre["varb_spread"].values
    r_off = pre["regime_off"].values
    ema21 = pd.Series(close).ewm(span=21, adjust=False).mean().values
    n = len(close)

    results, in_trade = [], False
    entry = stop = target1 = 0.0
    hold = 0

    for t in range(n - 1):
        if in_trade:
            hold += 1
            p = close[t]
            if p <= stop:
                results.append(-1.0); in_trade = False
            elif p >= target1:
                if close[t] < ema21[t] or hold >= 60:
                    trail_r = (p - entry) / atr[t] if atr[t] > 0 else 0
                    results.append(0.30 * 2.0 + 0.70 * trail_r); in_trade = False
            elif close[t] < ema21[t] and hold > 10:
                results.append((p - entry) / atr[t] if atr[t] > 0 else 0); in_trade = False
            elif hold >= 60:
                results.append((p - entry) / atr[t] if atr[t] > 0 else 0); in_trade = False
        else:
            if vts[t] >= vts_min and varb[t] <= varb_max and r_off[t] == 0 and atr[t] > 0:
                entry = close[t]; stop = entry - atr[t]
                target1 = entry + 2 * atr[t]; hold = 0; in_trade = True
    if in_trade and atr[-1] > 0:
        results.append((close[-1] - entry) / atr[-1])
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

    print(f"Grid search: {len(VTS_GRID)}×{len(VARB_GRID)} = {len(VTS_GRID)*len(VARB_GRID)} combos × 2 periods…")
    train_rows = grid_search(pre_only, "train")
    oos_rows   = grid_search(pre_only, "oos")

    # Save results
    results = {"train": train_rows, "oos": oos_rows, "n_stocks": len(pre_list)}
    Path("backtest_results.json").write_text(json.dumps(results, indent=2))
    print("Saved backtest_results.json")

    # Print top results
    train_df = pd.DataFrame(train_rows).sort_values("sortino", ascending=False)
    oos_df   = pd.DataFrame(oos_rows).sort_values("sortino", ascending=False)

    print("\n── Training 2021–2024 (top 10 by Sortino) ─────────────────────────")
    print(train_df.head(10).to_string(index=False))

    print("\n── OOS 2025–2026 (top 10 by Sortino) ──────────────────────────────")
    print(oos_df.head(10).to_string(index=False))

    best = train_df.iloc[0]
    oos_match = oos_df[(oos_df["vts_min"]==best["vts_min"]) & (oos_df["varb_max"]==best["varb_max"])]

    print(f"\n── Best (VTS≥{best['vts_min']:.0f} VARB≤{best['varb_max']:.0f}) ─────────────────────────────────────────")
    print(f"   Training : Sortino={best['sortino']:.2f}  Sharpe={best['sharpe']:.2f}  "
          f"WinRate={best['win_rate']:.1%}  AvgR={best['avg_r']:.2f}R  N={int(best['n_trades'])}")
    if not oos_match.empty:
        row = oos_match.iloc[0]
        print(f"   OOS      : Sortino={row['sortino']:.2f}  Sharpe={row['sharpe']:.2f}  "
              f"WinRate={row['win_rate']:.1%}  AvgR={row['avg_r']:.2f}R  N={int(row['n_trades'])}")

    # Per-threshold summary
    print("\n── VTS threshold effect (best VARB for each threshold) ─────────────")
    for vts_min in VTS_GRID:
        sub = train_df[train_df["vts_min"]==vts_min]
        if not sub.empty:
            b = sub.iloc[0]
            print(f"  VTS≥{vts_min:.0f}: Sortino={b['sortino']:.2f}  WinRate={b['win_rate']:.1%}  "
                  f"N={int(b['n_trades'])}  VARB≤{b['varb_max']:.0f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
