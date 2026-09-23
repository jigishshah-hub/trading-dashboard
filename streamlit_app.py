"""
India Master Portfolio — V2 Analytics & Decision Dashboard
Merged dashboard: V2 decision architecture + interactive analytics.
Live from Supabase Postgres.
"""

import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
import math
import yfinance as yf

# ── Config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="India Master Portfolio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SB_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# ── Theme colors ────────────────────────────────────────────
SLEEVE_COLORS = {
    "Anchor": "#355ec9",
    "Power Sleeve": "#eb6834",
    "Tracker": "#1baf7a",
    "Multibagger List": "#eda100",
}
SECTOR_COLORS = ["#355ec9", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6d4bc3", "#d95926"]
GREEN = "#237a35"
RED = "#c83b3b"
AMBER = "#a76a00"
MUTED = "#667085"


# ── Data layer ──────────────────────────────────────────────
@st.cache_resource
def _sb():
    return create_client(SB_URL, st.secrets["SUPABASE_SERVICE_ROLE_KEY"])


@st.cache_data(ttl=60)
def load():
    sb = _sb()
    return {
        "pos": sb.table("position_monitoring").select("*").order("trade_id").execute().data or [],
        "conds": sb.table("position_monitoring_conditions").select("*").execute().data or [],
        "lots": sb.table("lots").select("*").order("lot_id").execute().data or [],
        "stocks": sb.table("stocks").select("ticker, sector, current_price, price_updated_at").execute().data or [],
        "closed": sb.table("trade_history").select("*").order("exit_date", desc=True).execute().data or [],
        "news": sb.table("news_raw").select("*").order("published_at", desc=True).limit(200).execute().data or [],
        "research": sb.table("research_log").select("*").order("created_at", desc=True).execute().data or [],
        "fundsnap": sb.table("fundamentals_snapshots").select("*").order("pulled_at", desc=True).execute().data or [],
        "breadth_1pct": sb.table("breadth_readings").select("*").eq("source", "rzone_pnf_1pct").order("reading_date", desc=True).limit(1).execute().data or [],
        "breadth_025pct": sb.table("breadth_readings").select("*").eq("source", "rzone_pnf_025pct").order("reading_date", desc=True).limit(1).execute().data or [],
        "snapshots": sb.table("daily_snapshots").select("*").order("snapshot_date").execute().data or [],
    }


D = load()


# ── Live price fetch (yfinance) during market hours ────────
def _is_nse_market_hours():
    """Check if NSE is likely open (Mon-Fri, 9:15 AM - 3:30 PM IST)."""
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=35, second=0, microsecond=0)  # 5-min buffer
    return market_open <= now <= market_close


@st.cache_data(ttl=120)  # 2-min cache
def fetch_live_prices(tickers_tuple):
    """Fetch live prices from yfinance for active tickers.
    Returns dict of ticker -> price. Only called during market hours."""
    if not tickers_tuple:
        return {}
    symbols = " ".join(f"{t}.NS" for t in tickers_tuple)
    try:
        data = yf.download(symbols, period="1d", interval="1m", progress=False)
        if data.empty:
            return {}
        # For multi-ticker downloads, columns are MultiIndex (Price, Ticker)
        prices = {}
        if isinstance(data.columns, pd.MultiIndex):
            for t in tickers_tuple:
                sym = f"{t}.NS"
                try:
                    col = data[("Close", sym)].dropna()
                    if not col.empty:
                        prices[t] = round(float(col.iloc[-1]), 2)
                except (KeyError, IndexError):
                    pass
        else:
            # Single ticker case
            col = data["Close"].dropna()
            if not col.empty and len(tickers_tuple) == 1:
                prices[tickers_tuple[0]] = round(float(col.iloc[-1]), 2)
        return prices
    except Exception:
        return {}


# ── Nifty 50 Market Regime ──────────────────────────────────
@st.cache_data(ttl=120)  # 2-min cache during market hours
def fetch_nifty_regime():
    """Fetch Nifty 50 price + 200 EMA for Tactical Ladder framework."""
    try:
        nifty = yf.Ticker("^NSEI")
        hist = nifty.history(period="2y")
        if hist.empty:
            return None
        hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()
        ema200 = float(hist["EMA200"].iloc[-1])

        # 200 EMA history for chart (always from daily bars)
        chart_df = hist[["Close", "EMA200"]].tail(120).reset_index()
        chart_df.columns = ["Date", "Close", "EMA200"]
        chart_df["Date"] = chart_df["Date"].dt.strftime("%Y-%m-%d")

        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        is_trading = (now_ist.weekday() < 5
                      and now_ist.replace(hour=9, minute=15, second=0) <= now_ist
                      <= now_ist.replace(hour=15, minute=35, second=0))

        if is_trading:
            # During market hours, yfinance daily bars can LAG by a day
            # (yesterday's bar may not appear yet). So use multi-day intraday
            # data to reliably get yesterday's close + today's live price.
            nifty_close = None
            prev_close = None
            try:
                intra = nifty.history(period="5d", interval="5m")
                if not intra.empty:
                    today_str = now_ist.strftime("%Y-%m-%d")
                    bar_dates = [d.strftime("%Y-%m-%d") for d in intra.index]
                    # Today's latest bar = live price
                    today_closes = [float(intra.iloc[i]["Close"])
                                    for i, d in enumerate(bar_dates) if d == today_str]
                    if today_closes:
                        nifty_close = today_closes[-1]
                    # Previous trading day's last bar = prev close
                    prev_day_dates = sorted(set(d for d in bar_dates if d < today_str))
                    if prev_day_dates:
                        prev_date = prev_day_dates[-1]
                        prev_closes = [float(intra.iloc[i]["Close"])
                                       for i, d in enumerate(bar_dates) if d == prev_date]
                        if prev_closes:
                            prev_close = prev_closes[-1]
            except Exception:
                pass

            # Fallback: daily bars (less reliable during market hours)
            if nifty_close is None:
                nifty_close = float(hist.iloc[-1]["Close"])
            if prev_close is None:
                prev_close = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else nifty_close

            daily_chg = ((nifty_close - prev_close) / prev_close) * 100
        else:
            # Outside market hours: daily bars are complete and reliable
            latest = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else latest
            nifty_close = float(latest["Close"])
            daily_chg = ((nifty_close - float(prev["Close"])) / float(prev["Close"])) * 100

        pct_from_ema = ((nifty_close - ema200) / ema200) * 100

        return {
            "price": nifty_close,
            "ema200": ema200,
            "pct_from_ema": pct_from_ema,
            "daily_chg": daily_chg,
            "date": now_ist.strftime("%d %b %Y"),
            "chart": chart_df,
        }
    except Exception:
        return None


nifty = fetch_nifty_regime()


# ── Per-Stock Analysis Engine ────────────────────────────────
import numpy as np

@st.cache_data(ttl=1800)  # 30-min cache
def fetch_stock_history(ticker, period="2y"):
    """Fetch daily OHLCV from yfinance for an Indian stock."""
    try:
        t = yf.Ticker(f"{ticker}.NS")
        hist = t.history(period=period)
        if hist.empty:
            return None
        df = hist[["Open", "High", "Low", "Close", "Volume"]].reset_index()
        df.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
        df["Date"] = df["Date"].dt.tz_localize(None)
        # Drop rows with NaN in OHLC — yfinance returns NaN for holidays/splits
        df = df.dropna(subset=["Open", "High", "Low", "Close"]).reset_index(drop=True)
        return df if len(df) > 0 else None
    except Exception:
        return None


_RS_BENCHMARKS = {
    "Nifty 50": "^NSEI",
    "Nifty Bank": "^NSEBANK",
    "Nifty IT": "^CNXIT",
    "Nifty Midcap 50": "^NSEMDCP50",
}


@st.cache_data(ttl=1800)
def fetch_benchmark_history(symbol, period="2y"):
    """Fetch benchmark index close prices."""
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=period)
        if hist.empty:
            return None
        df = hist[["Close"]].reset_index()
        df.columns = ["Date", "Close"]
        df["Date"] = df["Date"].dt.tz_localize(None)
        return df
    except Exception:
        return None


def compute_relative_strength(stock_df, bench_df):
    """Compute RS ratio = stock / benchmark, normalised to start at 100."""
    merged = pd.merge(stock_df[["Date", "Close"]], bench_df, on="Date",
                       suffixes=("_stock", "_bench"), how="inner")
    if merged.empty:
        return None
    merged["RS_Raw"] = merged["Close_stock"] / merged["Close_bench"]
    merged["RS"] = merged["RS_Raw"] / merged["RS_Raw"].iloc[0] * 100
    merged["RS_MA"] = merged["RS"].rolling(20).mean()
    return merged[["Date", "RS", "RS_MA"]]


def compute_technicals(df):
    """Compute technical indicators on a price DataFrame."""
    d = df.copy()
    # EMAs
    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()
    # Bollinger Bands (20, 2)
    d["BB_Mid"] = d["Close"].rolling(20).mean()
    bb_std = d["Close"].rolling(20).std()
    d["BB_Upper"] = d["BB_Mid"] + 2 * bb_std
    d["BB_Lower"] = d["BB_Mid"] - 2 * bb_std
    d["BB_Width"] = ((d["BB_Upper"] - d["BB_Lower"]) / d["BB_Mid"] * 100)
    d["BB_Width_Pctile"] = d["BB_Width"].rolling(100, min_periods=20).apply(
        lambda x: (x.values[-1] > x.values[:-1]).sum() / len(x.values[:-1]) * 100 if len(x) > 1 else 50,
        raw=False
    )
    # RSI(14)
    delta = d["Close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    d["RSI"] = 100 - (100 / (1 + rs))
    d["RSI_MA"] = d["RSI"].rolling(14).mean()
    # MACD (12, 26, 9)
    ema12 = d["Close"].ewm(span=12, adjust=False).mean()
    ema26 = d["Close"].ewm(span=26, adjust=False).mean()
    d["MACD"] = ema12 - ema26
    d["MACD_Signal"] = d["MACD"].ewm(span=9, adjust=False).mean()
    d["MACD_Hist"] = d["MACD"] - d["MACD_Signal"]
    # ATR(14)
    tr = pd.concat([
        d["High"] - d["Low"],
        (d["High"] - d["Close"].shift()).abs(),
        (d["Low"] - d["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    d["ATR"] = tr.rolling(14).mean()
    return d


def compute_stats_model(df, entry, stop, target):
    """Statistical edge model: Monte Carlo, MFE/MAE, momentum, volatility.

    All positions are assumed LONG (Indian equity — no retail short selling).
    Stop can be above entry when it has been trailed up.
    """
    _none_result = {k: None for k in [
        "momentum_char", "momentum_advice", "autocorr", "atr", "atr_pct",
        "stop_atr", "target_atr", "bb_pctile", "target_prob", "stop_prob",
        "chop_prob", "mfe_further", "mfe_pullback", "std_daily",
        "big_move_pct", "current_streak", "streak_dir", "pct_from_entry",
        "target_already_hit", "stop_already_hit",
        "exp_days", "med_terminal", "expected_value", "edge_ratio",
        "conviction", "vol_regime", "ewma_sigma", "sigma_14",
    ]}
    closes = df["Close"].dropna().values
    if len(closes) < 20:
        return _none_result
    cmp = closes[-1]
    daily_returns = np.diff(closes) / closes[:-1]
    daily_returns = daily_returns[np.isfinite(daily_returns)]
    if len(daily_returns) < 20:
        return _none_result

    # ── 1. Momentum character ─────────────────────────────────
    # Lag-1 autocorrelation of daily returns
    # Also compute lag-5 (weekly) for a richer picture
    try:
        autocorr_1 = np.corrcoef(daily_returns[:-1], daily_returns[1:])[0, 1]
        if np.isnan(autocorr_1):
            autocorr_1 = 0.0
    except Exception:
        autocorr_1 = 0.0

    autocorr_5 = 0.0
    if len(daily_returns) > 25:
        try:
            autocorr_5 = np.corrcoef(daily_returns[:-5], daily_returns[5:])[0, 1]
            if np.isnan(autocorr_5):
                autocorr_5 = 0.0
        except Exception:
            autocorr_5 = 0.0

    # Use combined signal: if either lag shows persistence, flag it
    # Thresholds: ρ₁ > 0.03 or ρ₅ > 0.05 → trending
    if autocorr_1 > 0.03 or autocorr_5 > 0.05:
        momentum_char = "Trending"
        momentum_advice = "Let winners run; trail don't cut"
    elif autocorr_1 < -0.03 or autocorr_5 < -0.05:
        momentum_char = "Mean-reverting"
        momentum_advice = "Take profits faster; moves tend to reverse"
    else:
        momentum_char = "Neutral"
        momentum_advice = "No strong persistence pattern"

    # ── 2. ATR context ────────────────────────────────────────
    atr = df["ATR"].iloc[-1] if "ATR" in df.columns and pd.notna(df["ATR"].iloc[-1]) else None
    stop_atr = abs(cmp - stop) / atr if atr and atr > 0 and stop > 0 else None
    target_atr = abs(target - cmp) / atr if atr and atr > 0 and target else None

    # ── 3. BB Width percentile ────────────────────────────────
    bb_pctile = df["BB_Width_Pctile"].iloc[-1] if "BB_Width_Pctile" in df.columns else None

    # ── 4. Monte Carlo — EWMA vol + momentum-adjusted drift ───
    # ALL positions are LONG (Indian equity, no short selling).
    # Stop can be above entry (trailed stop) — doesn't change direction.
    #
    # Enhancement over flat historical sampling:
    # - σ from EWMA (span=30) — recent vol weighs more than old vol
    # - μ adjusted by lag-1 autocorrelation — trending stocks get drift tilt
    # - Tracks: days to resolution, median terminal price, edge ratio
    n_sims = 10000
    n_days = 40  # ~2 months of trading

    # Check if target/stop already reached from CMP
    target_already_hit = (target > 0 and cmp >= target)
    stop_already_hit = (stop > 0 and cmp <= stop)

    # EWMA volatility (span=30, giving λ≈0.935 — recent days weigh ~3× more)
    ewma_span = 30
    ewma_alpha = 2.0 / (ewma_span + 1)
    weights = np.array([(1 - ewma_alpha) ** i for i in range(len(daily_returns) - 1, -1, -1)])
    weights /= weights.sum()
    ewma_mu = np.dot(weights, daily_returns)
    ewma_var = np.dot(weights, (daily_returns - ewma_mu) ** 2)
    ewma_sigma = np.sqrt(ewma_var)

    # Also compute short-term σ (14-day) and long-term σ (full) for vol regime
    sigma_14 = np.std(daily_returns[-14:]) if len(daily_returns) >= 14 else ewma_sigma
    sigma_full = np.std(daily_returns)
    vol_ratio = sigma_14 / sigma_full if sigma_full > 0 else 1.0
    if vol_ratio > 1.3:
        vol_regime = "Expanding"
    elif vol_ratio < 0.7:
        vol_regime = "Contracting"
    else:
        vol_regime = "Normal"

    # Momentum-adjusted drift: tilt μ by autocorrelation signal
    # If trending (ρ₁ > 0), recent direction has persistence → use recent 14-day μ
    # If mean-reverting (ρ₁ < 0), recent moves tend to reverse → dampen recent μ
    mu_14 = np.mean(daily_returns[-14:]) if len(daily_returns) >= 14 else ewma_mu
    momentum_weight = min(max(autocorr_1, -0.3), 0.3)  # cap at ±0.3
    # Blend: more autocorrelation → lean more on recent drift direction
    sim_mu = ewma_mu + momentum_weight * (mu_14 - ewma_mu)
    sim_sigma = ewma_sigma

    target_hits = 0
    stop_hits = 0
    neither = 0
    days_to_target = []
    days_to_stop = []
    terminal_prices = []

    if len(daily_returns) > 20 and target and target > 0 and stop > 0 and not target_already_hit and not stop_already_hit:
        np.random.seed(42)  # reproducible across refreshes
        for _ in range(n_sims):
            path = cmp
            hit_target = False
            hit_stop = False
            for d_idx in range(n_days):
                ret = np.random.normal(sim_mu, sim_sigma)
                path *= (1 + ret)
                if path <= stop:
                    hit_stop = True
                    days_to_stop.append(d_idx + 1)
                    break
                if path >= target:
                    hit_target = True
                    days_to_target.append(d_idx + 1)
                    break
            if hit_target:
                target_hits += 1
            elif hit_stop:
                stop_hits += 1
            else:
                neither += 1
                terminal_prices.append(path)
        target_prob = target_hits / n_sims * 100
        stop_prob = stop_hits / n_sims * 100
        chop_prob = neither / n_sims * 100
        # Expected days to resolution (median of resolved paths)
        all_days = days_to_target + days_to_stop
        exp_days = int(np.median(all_days)) if all_days else n_days
        # Median terminal price for chop paths
        med_terminal = float(np.median(terminal_prices)) if terminal_prices else cmp
        # Edge ratio: target_prob weighted by reward vs stop_prob weighted by risk
        reward_pct = (target - cmp) / cmp * 100 if cmp > 0 else 0
        risk_pct = (cmp - stop) / cmp * 100 if cmp > 0 else 0
        expected_value = (target_prob / 100 * reward_pct) - (stop_prob / 100 * risk_pct)
        edge_ratio = (target_prob * reward_pct) / (stop_prob * risk_pct) if (stop_prob * risk_pct) > 0 else 99.0
    elif target_already_hit:
        target_prob = 100.0
        stop_prob = 0.0
        chop_prob = 0.0
        exp_days = 0
        med_terminal = cmp
        expected_value = 0
        edge_ratio = 99.0
    elif stop_already_hit:
        target_prob = 0.0
        stop_prob = 100.0
        chop_prob = 0.0
        exp_days = 0
        med_terminal = cmp
        expected_value = 0
        edge_ratio = 0.0
    else:
        target_prob = stop_prob = chop_prob = None
        exp_days = None
        med_terminal = None
        expected_value = None
        edge_ratio = None

    # Conviction score (0-100): synthesizes edge ratio, momentum, vol regime
    conviction = None
    if target_prob is not None:
        # Base: edge ratio contribution (capped at 50 pts)
        er_score = min(max((edge_ratio - 0.5) * 30, 0), 50) if edge_ratio < 99 else 50
        # Momentum boost: trending +15, neutral 0, mean-reverting -10
        mom_score = 15 if momentum_char == "Trending" else (-10 if momentum_char == "Mean-reverting" else 0)
        # Vol regime: contracting +10 (coiling), normal 0, expanding -5 (whipsaw risk)
        vol_score = 10 if vol_regime == "Contracting" else (-5 if vol_regime == "Expanding" else 0)
        # Chop penalty: high chop = uncertain
        chop_penalty = -min(chop_prob * 0.3, 15) if chop_prob else 0
        conviction = int(min(max(er_score + mom_score + vol_score + chop_penalty, 0), 100))

    # ── 5. MFE — after moves of similar magnitude, how much further?
    pct_from_entry = ((cmp - entry) / entry * 100) if entry else 0
    mfe_further = None
    mfe_pullback = None
    if len(closes) > 50 and abs(pct_from_entry) > 1:
        move_pct = abs(pct_from_entry)
        further_moves = []
        pullbacks = []
        for i in range(20, len(closes) - 20):
            for window in [5, 10, 15, 20]:
                if i - window < 0:
                    continue
                hist_move = (closes[i] - closes[i - window]) / closes[i - window] * 100
                if abs(hist_move - pct_from_entry) < move_pct * 0.3:
                    future = closes[i:i+10]
                    if len(future) > 1:
                        # Always long — further = up, pullback = down
                        max_fwd = (max(future) - closes[i]) / closes[i] * 100
                        max_pull = (closes[i] - min(future)) / closes[i] * 100
                        further_moves.append(max_fwd)
                        pullbacks.append(max_pull)
        if further_moves:
            mfe_further = np.median(further_moves)
            mfe_pullback = np.median(pullbacks)

    # ── 6. Return distribution stats ──────────────────────────
    std_daily = np.std(daily_returns) * 100 if len(daily_returns) > 10 else None
    big_move_pct = (np.sum(np.abs(daily_returns) > 0.03) / len(daily_returns) * 100
                    if len(daily_returns) > 10 else None)

    # ── 7. Streak analysis ────────────────────────────────────
    recent = daily_returns[-10:] if len(daily_returns) >= 10 else daily_returns
    current_streak = 0
    streak_dir = "green" if recent[-1] > 0 else "red"
    for r in reversed(recent):
        if (r > 0 and streak_dir == "green") or (r < 0 and streak_dir == "red"):
            current_streak += 1
        else:
            break

    return {
        "momentum_char": momentum_char,
        "momentum_advice": momentum_advice,
        "autocorr": autocorr_1,
        "autocorr_5": autocorr_5,
        "atr": atr,
        "atr_pct": (atr / cmp * 100) if atr else None,
        "stop_atr": stop_atr,
        "target_atr": target_atr,
        "bb_pctile": bb_pctile,
        "target_prob": target_prob,
        "stop_prob": stop_prob,
        "chop_prob": chop_prob,
        "target_already_hit": target_already_hit,
        "stop_already_hit": stop_already_hit,
        "exp_days": exp_days,
        "med_terminal": med_terminal,
        "expected_value": expected_value,
        "edge_ratio": edge_ratio,
        "conviction": conviction,
        "vol_regime": vol_regime,
        "ewma_sigma": ewma_sigma * 100 if ewma_sigma else None,  # as %
        "sigma_14": sigma_14 * 100 if sigma_14 else None,  # as %
        "mfe_further": mfe_further,
        "mfe_pullback": mfe_pullback,
        "std_daily": std_daily,
        "big_move_pct": big_move_pct,
        "current_streak": current_streak,
        "streak_dir": streak_dir,
        "pct_from_entry": pct_from_entry,
    }


# ── Nifty 50 Breadth — % stocks above 200 DMA ─────────────
NIFTY50_TICKERS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "LT.NS", "AXISBANK.NS", "BAJFINANCE.NS", "ASIANPAINT.NS", "MARUTI.NS",
    "HCLTECH.NS", "SUNPHARMA.NS", "TATAMOTORS.NS", "NTPC.NS", "TITAN.NS",
    "WIPRO.NS", "ULTRACEMCO.NS", "ONGC.NS", "NESTLEIND.NS", "POWERGRID.NS",
    "JSWSTEEL.NS", "M&M.NS", "TATASTEEL.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "BAJAJFINSV.NS", "TECHM.NS", "HDFCLIFE.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "SBILIFE.NS", "BRITANNIA.NS", "CIPLA.NS", "APOLLOHOSP.NS", "GRASIM.NS",
    "INDUSINDBK.NS", "EICHERMOT.NS", "TATACONSUM.NS", "COALINDIA.NS",
    "BAJAJ-AUTO.NS", "BPCL.NS", "BEL.NS", "TRENT.NS", "SHRIRAMFIN.NS",
    "HEROMOTOCO.NS",
]


@st.cache_data(ttl=120)  # 2-min cache during market hours
def fetch_nifty50_breadth():
    """Compute % of Nifty 50 stocks trading above their 200 DMA."""
    try:
        data = yf.download(NIFTY50_TICKERS, period="1y", group_by="ticker",
                           progress=False, threads=True)
        above = 0
        total = 0
        details = []  # (ticker, above_200dma: bool)
        for tkr in NIFTY50_TICKERS:
            try:
                closes = data[tkr]["Close"].dropna()
                if len(closes) < 200:
                    continue
                sma200 = closes.rolling(200).mean().iloc[-1]
                last = closes.iloc[-1]
                is_above = float(last) > float(sma200)
                above += int(is_above)
                total += 1
                details.append({"ticker": tkr.replace(".NS", ""), "above": is_above,
                                "price": float(last), "sma200": float(sma200)})
            except Exception:
                continue
        if total == 0:
            return None
        return {
            "above_count": above, "total": total,
            "pct_above": round((above / total) * 100, 1),
            "details": sorted(details, key=lambda d: d["ticker"]),
        }
    except Exception:
        return None


breadth_yf = fetch_nifty50_breadth()

# ── P&F Breadth from Supabase (authoritative) ─────────────
# Two P&F breadth sources: 1% box (structural trend) and 0.25% box (short-term shifts).
# Falls back to yfinance-computed % above 200 DMA if no P&F reading exists.
def _parse_pnf_row(row):
    """Parse a breadth_readings row into a breadth dict."""
    if not row:
        return None
    return {
        "pct_above": float(row["breadth_pct"]),
        "avg": float(row["avg_breadth_pct"]) if row.get("avg_breadth_pct") else None,
        "source": row.get("source", "rzone_pnf"),
        "date": row["reading_date"],
        "above_count": None, "total": None,
    }

pnf_1pct_row = D["breadth_1pct"][0] if D["breadth_1pct"] else None
pnf_025pct_row = D["breadth_025pct"][0] if D["breadth_025pct"] else None

breadth_1pct = _parse_pnf_row(pnf_1pct_row)
breadth_025pct = _parse_pnf_row(pnf_025pct_row)

# Primary breadth for ladder logic: 1% P&F (structural), fallback to yfinance
if breadth_1pct:
    breadth = breadth_1pct
elif breadth_yf:
    breadth = {**breadth_yf, "source": "yfinance", "avg": None, "date": None}
else:
    breadth = None

# Tactical Ladder tiers (dual-condition: EMA distance + breadth)
LADDER_TIERS = [
    {"tier": 1, "threshold": -5.0,  "breadth_max": 40, "deploy_pct": 8, "label": "Tier 1 — Light correction"},
    {"tier": 2, "threshold": -10.0, "breadth_max": 35, "deploy_pct": 8, "label": "Tier 2 — Moderate correction"},
    {"tier": 3, "threshold": -15.0, "breadth_max": 30, "deploy_pct": 8, "label": "Tier 3 — Deep correction"},
    {"tier": 4, "threshold": -20.0, "breadth_max": 25, "deploy_pct": 8, "label": "Tier 4 — Severe correction"},
    {"tier": 5, "threshold": -25.0, "breadth_max": 20, "deploy_pct": 8, "label": "Tier 5 — Panic lows"},
]

CAPITAL_ARCH = [
    {"name": "Core NiftyBees", "pct": 35, "desc": "Large-cap anchor"},
    {"name": "Core Nifty Midcap 150", "pct": 25, "desc": "Growth anchor"},
    {"name": "Tactical Cash Reserve", "pct": 40, "desc": "Liquid/arb funds ~6.5% p.a."},
]

active = [p for p in D["pos"] if p.get("status") == "active"]
exited = [p for p in D["pos"] if p.get("status") == "exited"]

# Build price + sector lookups
price_map = {}
sector_map = {}
for s in D["stocks"]:
    sector_map[s["ticker"]] = s.get("sector") or "Unknown"
    cp = s.get("current_price")
    pu = s.get("price_updated_at")
    if cp is not None:
        is_fresh = True
        if pu:
            try:
                updated = datetime.fromisoformat(str(pu).replace("Z", "+00:00"))
                is_fresh = (datetime.now(timezone.utc) - updated) < timedelta(hours=24)
            except Exception:
                is_fresh = False
        if is_fresh:
            price_map[s["ticker"]] = float(cp)

# Override with live yfinance prices during market hours
if _is_nse_market_hours():
    active_tickers = tuple(sorted(set(p.get("ticker") for p in active if p.get("ticker"))))
    live_prices = fetch_live_prices(active_tickers)
    price_map.update(live_prices)


# ── Helpers ─────────────────────────────────────────────────
def f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def get_cmp(ticker, entry_price=None):
    if ticker in price_map:
        return price_map[ticker], True
    if entry_price is not None:
        return entry_price, False
    return None, False


def fmt(n):
    if n is None:
        return "—"
    if abs(n) >= 100000:
        return f"₹{n / 100000:.2f}L"
    return f"₹{n:,.0f}"


def fmt_pct(n):
    if n is None:
        return "—"
    return f"{'+' if n >= 0 else ''}{n:.2f}%"


def fmt_date(d):
    if not d:
        return ""
    try:
        return datetime.fromisoformat(str(d).replace("Z", "+00:00")).strftime("%d %b %Y")
    except Exception:
        return str(d)[:10]


def short_date(d):
    if not d:
        return ""
    try:
        return datetime.fromisoformat(str(d).replace("Z", "+00:00")).strftime("%d %b")
    except Exception:
        return str(d)[:10]


def conds_for(trade_id, ctype=None):
    return [c for c in D["conds"]
            if c.get("trade_id") == trade_id
            and (ctype is None or c.get("condition_type") == ctype)]


def days_since(date_str):
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(str(date_str)).date()
        return (datetime.now().date() - d).days
    except Exception:
        return None


def sev_icon(tag):
    if tag == "thesis-threatening":
        return "🔴"
    if tag == "material change":
        return "🟡"
    return "⚪"


def review_urgency(review_date):
    if not review_date:
        return ""
    try:
        rd = datetime.fromisoformat(str(review_date)).date()
        days_until = (rd - datetime.now().date()).days
        if days_until < 0:
            return "🔴 overdue"
        if days_until <= 3:
            return "⏰ soon"
        return ""
    except Exception:
        return ""


# ── Compute enriched positions ──────────────────────────────
positions = []
for p in active:
    ticker = p.get("ticker")
    entry = f(p.get("entry_price")) or 0
    stop = f(p.get("stop_loss")) or 0
    initial_stop = f(p.get("initial_stop")) or stop
    qty = f(p.get("qty_open")) or f(p.get("quantity")) or 0
    cmp, is_live = get_cmp(ticker, entry)
    price = cmp or entry

    cost_basis = entry * qty
    current_value = price * qty
    pnl = current_value - cost_basis
    pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0
    # R-multiple uses initial risk (original stop), stop distance uses current stop
    initial_risk = entry - initial_stop if initial_stop else 0
    risk_per_share = entry - stop if stop else 0
    capital_at_risk = risk_per_share * qty
    stop_dist_pct = ((price - stop) / price * 100) if price else 0
    r_multiple = ((price - entry) / initial_risk) if initial_risk > 0 else 0
    days_in = days_since(p.get("entry_date"))
    sector = sector_map.get(ticker, p.get("sector") or "Unknown")
    sleeve = p.get("sleeve") or "Unassigned"

    targets = conds_for(p["trade_id"], "target")
    t1 = None
    if targets:
        # Try numeric fields first, then extract from description
        tv = f(targets[0].get("trigger_value"))
        pl = f(targets[0].get("price_level"))
        t1 = tv if (tv and tv > 0) else (pl if (pl and pl > 0) else None)
        if t1 is None:
            import re as _re
            desc = str(targets[0].get("description") or "")
            # Extract meaningful price number from description (skip small numbers like "Target 1")
            nums = _re.findall(r'[\d,]+\.?\d*', desc.replace(",", ""))
            for n in nums:
                val = f(n)
                if val and val > 10:  # skip ordinals like "Target 1"
                    t1 = val
                    break

    # Thesis status from news
    ticker_news = [n for n in D["news"] if n.get("ticker") == ticker]
    has_threat = any(n.get("severity_tag") == "thesis-threatening" for n in ticker_news)
    has_material = any(n.get("severity_tag") == "material change" for n in ticker_news)
    thesis = "Threatened" if has_threat else "Under review" if has_material else "Intact"

    positions.append({
        "trade_id": p["trade_id"],
        "ticker": ticker,
        "sleeve": sleeve,
        "sector": sector,
        "entry": entry,
        "stop": stop,
        "qty": qty,
        "cmp": price,
        "is_live": is_live,
        "cost_basis": cost_basis,
        "current_value": current_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "risk_per_share": risk_per_share,
        "capital_at_risk": capital_at_risk,
        "stop_dist_pct": stop_dist_pct,
        "stop_dist_abs": price - stop,
        "r_multiple": r_multiple,
        "days_in": days_in,
        "target": t1,
        "entry_date": p.get("entry_date"),
        "thesis": thesis,
        "thesis_note": p.get("thesis_note") or "",
        "review_date": p.get("review_date"),
        "setup": p.get("setup") or "",
        "position_size_pct": f(p.get("position_size_pct")),
    })

# Portfolio totals
total_invested = sum(p["cost_basis"] for p in positions)
total_value = sum(p["current_value"] for p in positions)
unrealized_pnl = total_value - total_invested
realized_pnl = sum(f(t.get("realized_return_pct") or 0) / 100 * (f(t.get("entry_price")) or 0) * (f(t.get("exit_price")) or 1)
                   for t in D["closed"])
# More accurate realized P&L
realized_pnl = 0
for t in D["closed"]:
    ep = f(t.get("entry_price")) or 0
    xp = f(t.get("exit_price")) or 0
    # Use trade_history qty - we need to infer from cost if not stored
    # Best estimate from entry/exit and return %
    ret_pct = f(t.get("realized_return_pct"))
    if ret_pct is not None:
        # P&L = entry_cost * return_pct / 100
        # But we don't have qty in trade_history directly
        # Use lots to find qty for this trade
        trade_lots = [l for l in D["lots"] if l.get("trade_id") == t.get("trade_id")]
        trade_qty = sum(f(l.get("qty")) or 0 for l in trade_lots)
        if trade_qty > 0:
            realized_pnl += (xp - ep) * trade_qty
        else:
            realized_pnl += ep * (ret_pct / 100)  # fallback

net_pnl = unrealized_pnl + realized_pnl

# Weight per position
for p in positions:
    p["weight"] = (p["current_value"] / total_value * 100) if total_value else 0

has_live = len(price_map) > 0
wins = sum(1 for t in D["closed"] if (f(t.get("exit_price")) or 0) > (f(t.get("entry_price")) or 0))

# ── Build alerts ────────────────────────────────────────────
alerts = []
for p in positions:
    # Stop breach
    if p["cmp"] <= p["stop"] and p["stop"] > 0:
        alerts.append({"ticker": p["ticker"], "level": "danger", "type": "STOP BREACH",
                        "msg": f"Price ₹{p['cmp']:,.0f} vs stop ₹{p['stop']:,.0f}"})
    elif p["stop_dist_pct"] < 80 and p["cmp"] < p["entry"]:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "NEAR STOP",
                        "msg": f"₹{p['stop_dist_abs']:.0f} from stop ({p['stop_dist_pct']:.0f}% buffer)"})

    # Concentration
    if p["weight"] > 40:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "CONCENTRATION",
                        "msg": f"{p['weight']:.1f}% portfolio weight"})

    # R = 0 (stop = entry)
    if p["risk_per_share"] == 0 and p["stop"] > 0:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "DATA REVIEW",
                        "msg": "Stop equals entry — R basis needs review"})

    # Thesis threat
    if p["thesis"] == "Threatened":
        alerts.append({"ticker": p["ticker"], "level": "danger", "type": "THESIS THREAT",
                        "msg": "Thesis-threatening news detected"})

    # Review overdue
    urg = review_urgency(p["review_date"])
    if "overdue" in urg:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "REVIEW",
                        "msg": f"Review overdue since {fmt_date(p['review_date'])}"})

danger_alerts = [a for a in alerts if a["level"] == "danger"]
warning_alerts = [a for a in alerts if a["level"] == "warning"]


# ── Header ──────────────────────────────────────────────────
st.markdown("""
<style>
    .stApp header {visibility: hidden;}
    div[data-testid="stMetric"] {background: #f8f9fb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 16px;}
    div[data-testid="stMetric"] label {font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: #667085;}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {font-size: 24px; font-weight: 700;}
    .action-card {padding: 10px 14px; margin: 4px 0; border-radius: 8px; border: 1px solid #e5e7eb;
                  display: flex; align-items: center; justify-content: space-between; gap: 12px;}
    .action-card .dot {width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 8px;}
    .danger-dot {background: #c83b3b;} .warning-dot {background: #c58b19;}
    .badge {display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 700;}
    .badge-red {background: #feecec; color: #a92e2e;}
    .badge-amber {background: #fff6dd; color: #8a5b00;}
    .badge-green {background: #eaf7ed; color: #216c30;}
    .r-badge {display: inline-block; padding: 2px 8px; border-radius: 5px; font-weight: 700; font-size: 13px;}
    .r-pos {background: #eaf7ed; color: #216c30;} .r-neg {background: #feecec; color: #a92e2e;}
    .stop-bar {height: 8px; border-radius: 4px; background: #eef1f5; overflow: hidden; margin: 4px 0;}
    .stop-fill {height: 100%; border-radius: 4px;}
</style>
""", unsafe_allow_html=True)

# Title row
c1, c2 = st.columns([4, 1])
with c1:
    st.markdown("# India Master Portfolio")
    st.caption(f"V2 Analytics & Decision Dashboard · {datetime.now().strftime('%d %b %Y')}")
with c2:
    state_label = "CAUTION" if danger_alerts else "NORMAL" if not warning_alerts else "WATCH"
    state_color = "#a92e2e" if danger_alerts else "#216c30" if not warning_alerts else "#8a5b00"
    state_bg = "#feecec" if danger_alerts else "#eaf7ed" if not warning_alerts else "#fff6dd"
    st.markdown(f'<div style="text-align:right;margin-top:20px"><span style="background:{state_bg};color:{state_color};'
                f'padding:8px 14px;border-radius:999px;font-weight:700;font-size:13px">● {state_label}</span></div>',
                unsafe_allow_html=True)

# ── Tabs ────────────────────────────────────────────────────
tab_cockpit, tab_positions, tab_risk, tab_perf, tab_thesis, tab_system = st.tabs([
    "🎯 Cockpit", "📊 Positions", "⚡ Risk", "📈 Performance", "🔬 Thesis Monitor", "⚙️ System & Data"
])


# ═══════════════════════════════════════════════════════════
# TAB 1 — COCKPIT
# ═══════════════════════════════════════════════════════════
with tab_cockpit:
    # Alert banner
    if danger_alerts:
        html = "".join(
            f'<div style="padding:5px 12px;font-size:13px"><span class="dot danger-dot"></span>'
            f'<strong>{a["ticker"]}</strong> — {a["msg"]}'
            f'<span class="badge badge-red" style="float:right">{a["type"]}</span></div>'
            for a in danger_alerts
        )
        st.markdown(
            f'<div style="background:rgba(239,68,68,.06);border:1px solid #ef4444;border-radius:10px;padding:8px 4px;margin-bottom:12px">'
            f'<div style="padding:4px 12px;font-weight:700;color:#dc2626;font-size:14px">🚨 {len(danger_alerts)} Hard Alert(s)</div>'
            f'{html}</div>', unsafe_allow_html=True)

    if warning_alerts:
        html = "".join(
            f'<div style="padding:4px 12px;font-size:12px"><span class="dot warning-dot"></span>'
            f'<strong>{a["ticker"]}</strong> — {a["msg"]}'
            f'<span class="badge badge-amber" style="float:right">{a["type"]}</span></div>'
            for a in warning_alerts
        )
        st.markdown(
            f'<div style="background:rgba(245,158,11,.05);border:1px solid #f59e0b;border-radius:10px;padding:6px 4px;margin-bottom:12px">'
            f'<div style="padding:4px 12px;font-weight:600;color:#d97706;font-size:13px">⚡ {len(warning_alerts)} Watch Item(s)</div>'
            f'{html}</div>', unsafe_allow_html=True)

    # Live price badge
    if has_live:
        if _is_nse_market_hours():
            st.markdown(
                '<div style="text-align:right;margin-bottom:-12px;font-size:11px;color:#667085">'
                '🟢 Live prices · yfinance · refreshes every 2 min</div>', unsafe_allow_html=True)
        else:
            freshest = max((s.get("price_updated_at") or "" for s in D["stocks"] if s.get("current_price")), default="")
            if freshest:
                try:
                    upd = datetime.fromisoformat(str(freshest).replace("Z", "+00:00"))
                    mins_ago = int((datetime.now(timezone.utc) - upd).total_seconds() / 60)
                    if mins_ago < 60:
                        age_txt = f"{mins_ago}m ago"
                    elif mins_ago < 1440:
                        age_txt = f"{mins_ago // 60}h ago"
                    else:
                        age_txt = f"{mins_ago // 1440}d ago"
                    st.markdown(
                        f'<div style="text-align:right;margin-bottom:-12px;font-size:11px;color:#667085">'
                        f'🔵 Screener.in prices · updated {age_txt}</div>', unsafe_allow_html=True)
                except Exception:
                    pass

    # KPIs
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Portfolio Value", fmt(total_value),
              delta=f"{unrealized_pnl / total_invested * 100:+.1f}% unrealized" if total_invested else None)
    k2.metric("Invested", fmt(total_invested))
    k3.metric("Net P&L", fmt(net_pnl),
              delta="realized + unrealized")
    k4.metric("Active Positions", len(positions), delta=f"{len(set(p['sector'] for p in positions))} sectors")
    largest = max(positions, key=lambda p: p["weight"]) if positions else None
    k5.metric("Largest Weight", f"{largest['weight']:.1f}%" if largest else "—",
              delta=largest["ticker"] if largest else None)
    k6.metric("Closed Trades", len(D["closed"]),
              delta=f"Win rate {wins}/{len(D['closed'])}" if D["closed"] else None)

    st.divider()

    # Two columns: Action Queue + Sector Allocation
    col_left, col_right = st.columns([1.3, 1])

    with col_left:
        st.markdown("#### 🚨 Action Queue")
        st.caption("Exception-first view — items that need your decision")
        all_alerts = danger_alerts + warning_alerts
        if all_alerts:
            for a in all_alerts:
                dot_cls = "danger-dot" if a["level"] == "danger" else "warning-dot"
                badge_cls = "badge-red" if a["level"] == "danger" else "badge-amber"
                st.markdown(
                    f'<div class="action-card">'
                    f'<div><span class="dot {dot_cls}"></span><strong>{a["ticker"]}</strong><br/>'
                    f'<small style="color:#667085">{a["msg"]}</small></div>'
                    f'<span class="badge {badge_cls}">{a["type"]}</span></div>',
                    unsafe_allow_html=True)
        else:
            st.success("No action items — all positions within parameters.")

    with col_right:
        st.markdown("#### Sector Allocation")
        st.caption("By current portfolio value")
        sector_vals = {}
        for p in positions:
            sector_vals[p["sector"]] = sector_vals.get(p["sector"], 0) + p["current_value"]
        sectors_sorted = sorted(sector_vals.items(), key=lambda x: x[1], reverse=True)

        fig_sector = go.Figure()
        names = [s[0] for s in sectors_sorted]
        vals = [s[1] for s in sectors_sorted]
        pcts = [v / total_value * 100 for v in vals]
        colors = SECTOR_COLORS[:len(names)]

        fig_sector.add_trace(go.Bar(
            y=names[::-1], x=pcts[::-1],
            orientation='h',
            marker_color=colors[:len(names)][::-1],
            text=[f"{p:.1f}%" for p in pcts[::-1]],
            textposition='outside',
            hovertemplate='%{y}: %{x:.1f}%<extra></extra>',
        ))
        fig_sector.update_layout(
            height=280, margin=dict(l=0, r=40, t=10, b=10),
            xaxis=dict(showgrid=True, gridcolor="#eef0f3", title=None, showticklabels=False),
            yaxis=dict(showgrid=False, title=None),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_sector, use_container_width=True)

    st.divider()

    # Market / Deployment State + Equity Curve
    md_left, md_right = st.columns(2)

    with md_left:
        st.markdown("#### 📡 Market / Deployment State")
        st.caption("Dual-Asset Tactical Ladder — rules-based market engine")

        if nifty:
            pct = nifty["pct_from_ema"]
            bpct = breadth["pct_above"] if breadth else None  # 1% P&F for ladder decisions
            bpct_025 = breadth_025pct["pct_above"] if breadth_025pct else None  # 0.25% for info

            # Determine system state using DUAL-CONDITION logic
            # Both EMA distance AND breadth filter must be met for deployment tiers
            def tier_met(ema_thr, breadth_max):
                """Check if both conditions are satisfied."""
                ema_ok = pct <= ema_thr
                breadth_ok = bpct is not None and bpct <= breadth_max
                return ema_ok and breadth_ok

            if tier_met(-25, 20):
                sys_state, sys_icon, sys_color, sys_bg = "DEPLOY ALL", "🔴", "#a92e2e", "#feecec"
                deploy_perm = "FULL DEPLOYMENT — ALL 5 TIERS"
            elif tier_met(-20, 25):
                sys_state, sys_icon, sys_color, sys_bg = "DEPLOY T4", "🟠", "#c05621", "#fff0e6"
                deploy_perm = "DEPLOY TIER 4 — 32% tactical deployed"
            elif tier_met(-15, 30):
                sys_state, sys_icon, sys_color, sys_bg = "DEPLOY T3", "🟡", "#a76a00", "#fff6dd"
                deploy_perm = "DEPLOY TIER 3 — 24% tactical deployed"
            elif tier_met(-10, 35):
                sys_state, sys_icon, sys_color, sys_bg = "DEPLOY T2", "🟡", "#a76a00", "#fff6dd"
                deploy_perm = "DEPLOY TIER 2 — 16% tactical deployed"
            elif tier_met(-5, 40):
                sys_state, sys_icon, sys_color, sys_bg = "DEPLOY T1", "🟡", "#a76a00", "#fff6dd"
                deploy_perm = "DEPLOY TIER 1 — 8% tactical deployed"
            elif pct >= 20 and bpct is not None and bpct >= 80:
                sys_state, sys_icon, sys_color, sys_bg = "OVEREXTENDED", "⚡", "#7c3aed", "#f3f0ff"
                deploy_perm = "PROFIT HARVEST → peel tactical back to cash"
            elif pct >= 15:
                sys_state, sys_icon, sys_color, sys_bg = "EXTENDED", "📈", "#2563eb", "#eff6ff"
                deploy_perm = "MONITOR — approaching harvest zone"
            elif pct <= -5 and (bpct is None or bpct > 40):
                # EMA dipped but breadth hasn't confirmed — false start filter
                sys_state, sys_icon, sys_color, sys_bg = "CAUTION", "⚠️", "#a76a00", "#fff6dd"
                deploy_perm = "EMA DIPPED — BREADTH NOT CONFIRMED, HOLD"
            else:
                sys_state, sys_icon, sys_color, sys_bg = "NORMAL", "🟢", "#216c30", "#eaf7ed"
                deploy_perm = "HOLD / WAIT FOR RULE TRIGGER"

            # State badge
            st.markdown(
                f'<div style="background:{sys_bg};border:1px solid {sys_color}30;border-radius:10px;padding:14px 16px;margin-bottom:10px">'
                f'<div style="font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:0.04em">Current portfolio state</div>'
                f'<div style="font-size:22px;font-weight:800;color:{sys_color};margin:4px 0">{sys_icon} {sys_state}</div>'
                f'<div style="font-size:12px;color:#444">Deployment permission: <strong>{deploy_perm}</strong></div>'
                f'</div>', unsafe_allow_html=True)

            # Nifty metrics row — 5 cols (EMA + both breadth sources)
            nm1, nm2, nm3, nm4, nm5 = st.columns(5)
            nm1.metric("Nifty 50", f"{nifty['price']:,.0f}", delta=f"{nifty['daily_chg']:+.2f}%")
            nm2.metric("200 EMA", f"{nifty['ema200']:,.0f}")
            ema_delta_color = "inverse" if pct < 0 else "normal"
            nm3.metric("vs 200 EMA", f"{pct:+.2f}%",
                       delta="below" if pct < 0 else "above", delta_color=ema_delta_color)
            # P&F 1% breadth (structural trend)
            if breadth_1pct:
                b1_color = "inverse" if breadth_1pct["pct_above"] < 50 else "normal"
                nm4.metric("P&F 1%", f'{breadth_1pct["pct_above"]}%',
                           delta=f'{breadth_1pct["date"]}', delta_color=b1_color)
            elif breadth_yf:
                b1_color = "inverse" if breadth_yf["pct_above"] < 50 else "normal"
                nm4.metric("Breadth (yf)", f'{breadth_yf["pct_above"]}%',
                           delta=f'{breadth_yf["above_count"]}/{breadth_yf["total"]} >200 DMA',
                           delta_color=b1_color)
            else:
                nm4.metric("P&F 1%", "—", delta="unavailable")
            # P&F 0.25% breadth (short-term shifts)
            if breadth_025pct:
                b025_color = "inverse" if breadth_025pct["pct_above"] < 50 else "normal"
                nm5.metric("P&F 0.25%", f'{breadth_025pct["pct_above"]}%',
                           delta=f'{breadth_025pct["date"]}', delta_color=b025_color)
            else:
                nm5.metric("P&F 0.25%", "—", delta="unavailable")

            # Tier ladder visualization — now shows BOTH conditions
            st.markdown("")
            st.markdown("**Deployment Ladder** <span style='font-size:11px;color:#888'>(dual-condition: EMA + 1% P&F breadth · 0.25% shown for context)</span>",
                        unsafe_allow_html=True)
            for tier in LADDER_TIERS:
                thr = tier["threshold"]
                bmax = tier["breadth_max"]
                ema_hit = pct <= thr
                breadth_hit = bpct is not None and bpct <= bmax
                both_met = ema_hit and breadth_hit
                trigger_price = nifty["ema200"] * (1 + thr / 100)

                # Check if this is the "current" active tier
                is_current = both_met and (tier["tier"] == 1 or not (
                    pct <= LADDER_TIERS[tier["tier"] - 2]["threshold"] and
                    (bpct is not None and bpct <= LADDER_TIERS[tier["tier"] - 2]["breadth_max"])
                ))

                if is_current:
                    row_bg = f"{sys_color}15"
                    row_border = sys_color
                    marker = "▶"
                elif both_met:
                    row_bg = "#eaf7ed"
                    row_border = GREEN
                    marker = "✅"
                else:
                    row_bg = "#f8f9fb"
                    row_border = "#e5e7eb"
                    marker = "⬜"

                # Status pills for each condition
                ema_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                            f'background:{"#dcfce7" if ema_hit else "#fee2e2"};'
                            f'color:{"#166534" if ema_hit else "#991b1b"}">EMA {"✓" if ema_hit else "✗"}</span>')
                # 1% breadth pill (used for deployment decisions)
                breadth_pill_color = "#dcfce7" if breadth_hit else "#fee2e2" if bpct is not None else "#f3f4f6"
                breadth_pill_text = "#166534" if breadth_hit else "#991b1b" if bpct is not None else "#888"
                breadth_status = "✓" if breadth_hit else "✗" if bpct is not None else "?"
                breadth_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                                f'background:{breadth_pill_color};color:{breadth_pill_text}">'
                                f'1%≤{bmax} {breadth_status}</span>')
                # 0.25% breadth pill (informational — short-term sensitivity)
                b025_hit = bpct_025 is not None and bpct_025 <= bmax
                b025_pill_color = "#dbeafe" if b025_hit else "#fef3c7" if bpct_025 is not None else "#f3f4f6"
                b025_pill_text = "#1e40af" if b025_hit else "#92400e" if bpct_025 is not None else "#888"
                b025_status = "✓" if b025_hit else "✗" if bpct_025 is not None else "?"
                breadth_025_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                                    f'background:{b025_pill_color};color:{b025_pill_text}">'
                                    f'.25%≤{bmax} {b025_status}</span>')

                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:6px;padding:6px 10px;margin:2px 0;'
                    f'border-radius:6px;background:{row_bg};border-left:3px solid {row_border}">'
                    f'<span style="font-size:14px">{marker}</span>'
                    f'<span style="min-width:26px;font-weight:700;font-size:13px">T{tier["tier"]}</span>'
                    f'<span style="font-size:12px;color:#444">{thr}%</span>'
                    f'{ema_pill}{breadth_pill}{breadth_025_pill}'
                    f'<span style="flex:1;font-size:11px;color:#666;text-align:right">→ {tier["deploy_pct"]}% Midcap 150</span>'
                    f'<span style="font-size:11px;color:{MUTED};min-width:70px;text-align:right">₹{trigger_price:,.0f}</span>'
                    f'</div>', unsafe_allow_html=True)

            # Capital architecture
            st.markdown("")
            st.markdown("**Capital Architecture**")
            arch_colors = ["#355ec9", "#1baf7a", "#eda100"]
            for i, ca in enumerate(CAPITAL_ARCH):
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                    f'<div style="width:10px;height:10px;border-radius:3px;background:{arch_colors[i]}"></div>'
                    f'<span style="font-size:13px;min-width:160px"><strong>{ca["name"]}</strong></span>'
                    f'<span style="font-size:13px;font-weight:700;min-width:35px">{ca["pct"]}%</span>'
                    f'<span style="font-size:11px;color:{MUTED}">{ca["desc"]}</span>'
                    f'</div>', unsafe_allow_html=True)

        else:
            st.warning("⚠️ Could not fetch Nifty 50 data — check yfinance connection.")
            st.caption("The Tactical Ladder requires live Nifty 50 price and 200 EMA.")

    with md_right:
        # Nifty vs 200 EMA chart
        if nifty and nifty.get("chart") is not None:
            st.markdown("#### 📈 Nifty 50 vs 200 EMA")
            st.caption("Tactical Ladder trigger zones — 120 trading days")
            cdf = nifty["chart"]
            fig_nifty = go.Figure()
            fig_nifty.add_trace(go.Scatter(
                x=cdf["Date"], y=cdf["Close"], mode='lines',
                name='Nifty 50', line=dict(color="#355ec9", width=2),
                hovertemplate='%{x}<br>Nifty: %{y:,.0f}<extra></extra>',
            ))
            fig_nifty.add_trace(go.Scatter(
                x=cdf["Date"], y=cdf["EMA200"], mode='lines',
                name='200 EMA', line=dict(color="#eb6834", width=2, dash='dash'),
                hovertemplate='%{x}<br>200 EMA: %{y:,.0f}<extra></extra>',
            ))
            # Add tier trigger lines
            for tier in LADDER_TIERS[:3]:  # Show T1-T3 lines
                trigger = nifty["ema200"] * (1 + tier["threshold"] / 100)
                fig_nifty.add_hline(y=trigger, line_dash="dot", line_color="#c83b3b",
                                    line_width=1, opacity=0.4,
                                    annotation_text=f"T{tier['tier']} ({tier['threshold']}%)",
                                    annotation_position="left",
                                    annotation_font_size=9, annotation_font_color="#999")
            fig_nifty.update_layout(
                height=300, margin=dict(l=0, r=0, t=10, b=10),
                xaxis=dict(showgrid=False, title=None),
                yaxis=dict(showgrid=True, gridcolor="#eef0f3", title=None),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                showlegend=True,
            )
            st.plotly_chart(fig_nifty, use_container_width=True)
        else:
            st.info("Nifty data unavailable — install yfinance for live chart.")

    # Portfolio Equity Curve — full width below
    st.markdown("#### 📈 Portfolio Equity Curve")

    snapshots = D.get("snapshots", [])
    all_lots = sorted(D["lots"], key=lambda l: l.get("lot_date") or "9999")

    if snapshots:
        # ── Portfolio equity curve: Stock + Cash with carry-forward ──
        # Stock  = sum of open positions' market value (from snapshots)
        # Cash   = initial capital + cumulative cash events to date
        # Carry-forward: if a position has no snapshot on a date (yfinance
        # gap, partial day), use its last known value instead of zero.
        from collections import defaultdict

        # Exit date lookup
        _exit_map = {}
        for cl in D.get("closed", []):
            tid = cl.get("trade_id")
            exd = cl.get("exit_date")
            if tid and exd:
                _exit_map[tid] = exd

        # Cash flow events: entries (negative) and exits (positive)
        _cash_ev = []
        _cb_map = {}
        for pm in D["pos"]:
            tid = pm.get("trade_id")
            ep = f(pm.get("entry_price"))
            qty = f(pm.get("quantity"))
            ed = pm.get("entry_date")
            if tid and ep and qty:
                cb = ep * qty
                _cb_map[tid] = cb
                if ed:
                    _cash_ev.append((ed, -cb))
        for cl in D.get("closed", []):
            tid = cl.get("trade_id")
            exit_date = cl.get("exit_date")
            exit_price = f(cl.get("exit_price"))
            if tid in _cb_map and exit_date and exit_price:
                qty_pm = next((p for p in D["pos"] if p["trade_id"] == tid), None)
                qty = f(qty_pm.get("quantity")) if qty_pm else None
                if qty:
                    _cash_ev.append((exit_date, exit_price * qty))
        _cash_ev.sort()

        # Initial capital = enough cash so balance never goes negative
        cum = 0
        min_cum = 0
        for _, amt in _cash_ev:
            cum += amt
            min_cum = min(min_cum, cum)
        _init_cap = -min_cum if min_cum < 0 else 0

        # Per-position snapshots (filtered: exclude post-exit)
        _pos_snap = defaultdict(dict)
        for snap in snapshots:
            tid = snap.get("trade_id")
            sd = snap.get("snapshot_date")
            pv = f(snap.get("position_value"))
            if tid and sd and pv is not None:
                if tid in _exit_map and sd >= _exit_map[tid]:
                    continue
                _pos_snap[tid][sd] = pv

        all_dates = sorted(
            set().union(*(d.keys() for d in _pos_snap.values()))
            if _pos_snap else []
        )

        # Sum stock with carry-forward for missing snapshots
        _last = {}
        _daily_stock = {}
        for d in all_dates:
            total = 0
            for tid, dv in _pos_snap.items():
                if d in dv:
                    _last[tid] = dv[d]
                    total += dv[d]
                elif tid in _last and (tid not in _exit_map or d < _exit_map[tid]):
                    total += _last[tid]
            _daily_stock[d] = total

        # Build portfolio value = stock + cash for each date
        curve_dates = list(all_dates)
        curve_values = []
        for d in curve_dates:
            stock = _daily_stock[d]
            cash = _init_cap + sum(amt for ed, amt in _cash_ev if ed <= d)
            curve_values.append(stock + cash)

        # Capital base line (constant)
        curve_capital = [_init_cap] * len(curve_dates)

        # Add today's live point
        today_str = datetime.now().strftime("%Y-%m-%d")
        active_stock_eq = sum(p["current_value"] for p in positions)
        today_cash_eq = _init_cap + sum(amt for _, amt in _cash_ev)
        today_portfolio_eq = active_stock_eq + today_cash_eq
        if today_str not in set(curve_dates):
            curve_dates.append(today_str)
            curve_values.append(today_portfolio_eq)
            curve_capital.append(_init_cap)
        else:
            idx = curve_dates.index(today_str)
            curve_values[idx] = today_portfolio_eq

        st.caption(f"Daily portfolio value vs capital deployed ({len(all_dates)} trading days)")

        fig_equity = go.Figure()
        fig_equity.add_trace(go.Scatter(
            x=curve_dates, y=curve_values,
            mode='lines', name='Portfolio Value',
            line=dict(color='#2563eb', width=2.5),
            fill='tozeroy', fillcolor='rgba(37,99,235,0.08)',
            hovertemplate='%{x}<br>Value: ₹%{y:,.0f}<extra></extra>',
        ))
        fig_equity.add_trace(go.Scatter(
            x=curve_dates, y=curve_capital,
            mode='lines', name='Capital',
            line=dict(color=MUTED, width=1.5, dash='dot'),
            hovertemplate='%{x}<br>Capital: ₹%{y:,.0f}<extra></extra>',
        ))
        # Today's marker
        fig_equity.add_trace(go.Scatter(
            x=[curve_dates[-1]], y=[curve_values[-1]],
            mode='markers', name=f'Today ({fmt(curve_values[-1])})',
            marker=dict(size=10, color=GREEN if curve_values[-1] >= _init_cap else RED, symbol='diamond'),
            hovertemplate='Today<br>Value: ₹%{y:,.0f}<extra></extra>',
        ))
        fig_equity.update_layout(
            height=260, margin=dict(l=0, r=0, t=10, b=10),
            xaxis=dict(showgrid=False, title=None),
            yaxis=dict(showgrid=True, gridcolor="#eef0f3", title="₹"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_equity, use_container_width=True)

    elif all_lots:
        # Fallback: lot-event based curve (no daily snapshots yet)
        st.caption("Cumulative capital deployed (daily snapshots pending — run backfill for full equity curve)")
        lot_events = {}
        for lot in all_lots:
            ld = lot.get("lot_date")
            if not ld:
                continue
            lqty = f(lot.get("qty")) or 0
            lprice = f(lot.get("price")) or 0
            lot_cost = lqty * lprice
            if (lot.get("lot_type") or "entry") == "exit":
                lot_cost = -lot_cost
            lot_events[ld] = lot_events.get(ld, 0) + lot_cost

        curve_dates = []
        curve_invested = []
        running = 0
        for date_str in sorted(lot_events.keys()):
            running += lot_events[date_str]
            curve_dates.append(date_str)
            curve_invested.append(running)

        today_str = datetime.now().strftime("%Y-%m-%d")
        curve_dates.append(today_str)
        curve_invested.append(total_invested)

        fig_equity = go.Figure()
        fig_equity.add_trace(go.Scatter(
            x=curve_dates, y=curve_invested,
            mode='lines+markers', name='Invested',
            line=dict(color=MUTED, width=2, dash='dot'), marker=dict(size=5),
            hovertemplate='%{x}<br>Invested: ₹%{y:,.0f}<extra></extra>',
        ))
        fig_equity.add_trace(go.Scatter(
            x=[curve_dates[-1]], y=[total_value],
            mode='markers', name=f'Current Value ({fmt(total_value)})',
            marker=dict(size=12, color=GREEN if total_value >= total_invested else RED, symbol='diamond'),
            hovertemplate='Today<br>Value: ₹%{y:,.0f}<extra></extra>',
        ))
        fig_equity.update_layout(
            height=220, margin=dict(l=0, r=0, t=10, b=10),
            xaxis=dict(showgrid=False, title=None),
            yaxis=dict(showgrid=True, gridcolor="#eef0f3", title="₹"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_equity, use_container_width=True)
    else:
        st.caption("No lot data yet — equity curve needs lot entries with dates.")

    st.divider()

    # Sleeve P&L — expandable
    st.markdown("#### Sleeve Performance")
    st.caption("Click a sleeve to expand individual positions")

    sleeve_groups = {}
    for p in positions:
        sl = p["sleeve"]
        if sl not in sleeve_groups:
            sleeve_groups[sl] = []
        sleeve_groups[sl].append(p)

    for sleeve, pos_list in sorted(sleeve_groups.items(), key=lambda x: sum(p["cost_basis"] for p in x[1]), reverse=True):
        sl_invested = sum(p["cost_basis"] for p in pos_list)
        sl_value = sum(p["current_value"] for p in pos_list)
        sl_pnl = sl_value - sl_invested
        sl_ret = (sl_pnl / sl_invested * 100) if sl_invested else 0
        sl_color = SLEEVE_COLORS.get(sleeve, "#666")

        with st.expander(f"🔵 **{sleeve}** ({len(pos_list)}) — Invested: {fmt(sl_invested)} · Value: {fmt(sl_value)} · P&L: {fmt_pct(sl_ret)}", expanded=False):
            rows = []
            for p in sorted(pos_list, key=lambda x: x["cost_basis"], reverse=True):
                r_text = f"{p['r_multiple']:+.1f}R"
                rows.append({
                    "Ticker": p["ticker"],
                    "Sector": p["sector"],
                    "Entry": f"₹{p['entry']:,.0f}",
                    "CMP": f"₹{p['cmp']:,.1f}",
                    "Stop": f"₹{p['stop']:,.0f}",
                    "Qty": int(p["qty"]),
                    "Invested": fmt(p["cost_basis"]),
                    "Value": fmt(p["current_value"]),
                    "P&L": fmt(p["pnl"]),
                    "Return": fmt_pct(p["pnl_pct"]),
                    "R": r_text,
                    "Days": p["days_in"] or "—",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Total row
    total_ret = (unrealized_pnl / total_invested * 100) if total_invested else 0
    st.markdown(f"**Total Active** — Invested: {fmt(total_invested)} · Value: {fmt(total_value)} · "
                f"P&L: **{fmt_pct(total_ret)}** ({fmt(unrealized_pnl)})")


# ═══════════════════════════════════════════════════════════
# TAB 2 — POSITIONS
# ═══════════════════════════════════════════════════════════
with tab_positions:
    if not has_live:
        st.info("💡 Live prices not yet connected — distances and P&L use entry price. "
                "Price sync runs every 30 min during market hours.")

    st.markdown("#### Position Detail")
    st.caption("Decision-oriented view: exposure + stop + R + thesis + action")

    # Build display dataframe
    pos_rows = []
    for p in sorted(positions, key=lambda x: x["weight"], reverse=True):
        # Action recommendation
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            action = "Review stop breach"
            risk_badge = "Critical"
        elif p["stop_dist_pct"] < 80 and p["cmp"] < p["entry"]:
            action = "Monitor — near stop"
            risk_badge = "Watch"
        elif p["risk_per_share"] == 0:
            action = "Review stop / R basis"
            risk_badge = "Review"
        else:
            action = "Hold / monitor"
            risk_badge = "Normal"

        pos_rows.append({
            "Ticker": p["ticker"],
            "Sleeve": p["sleeve"],
            "Sector": p["sector"],
            "Weight %": round(p["weight"], 1),
            "Entry": p["entry"],
            "CMP": p["cmp"],
            "Stop": p["stop"],
            "R": round(p["r_multiple"], 1),
            "Days": p["days_in"] or 0,
            "P&L %": round(p["pnl_pct"], 2),
            "P&L ₹": round(p["pnl"]),
            "Thesis": p["thesis"],
            "Risk": risk_badge,
            "Action": action,
        })

    df_pos = pd.DataFrame(pos_rows)
    if not df_pos.empty:
        def color_r(val):
            if val >= 0:
                return f"color: {GREEN}; font-weight: 700"
            return f"color: {RED}; font-weight: 700"

        def color_pnl(val):
            if val >= 0:
                return f"color: {GREEN}; font-weight: 600"
            return f"color: {RED}; font-weight: 600"

        def color_risk(val):
            if val == "Critical":
                return f"background-color: #feecec; color: #a92e2e; font-weight: 700"
            if val in ("Watch", "Review"):
                return f"background-color: #fff6dd; color: #8a5b00; font-weight: 600"
            return f"background-color: #eaf7ed; color: #216c30"

        styled = (
            df_pos.style
            .map(color_r, subset=["R"])
            .map(color_pnl, subset=["P&L %", "P&L ₹"])
            .map(color_risk, subset=["Risk"])
            .format({
                "Entry": "₹{:,.0f}",
                "CMP": "₹{:,.1f}",
                "Stop": "₹{:,.0f}",
                "R": "{:+.1f}R",
                "Weight %": "{:.1f}%",
                "P&L %": "{:+.2f}%",
                "P&L ₹": "₹{:,.0f}",
            })
        )
        event = st.dataframe(styled, use_container_width=True, hide_index=True,
                             selection_mode="single-row", on_select="rerun", key="pos_table")

    # Summary cards below table
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.markdown("##### Exposure")
        if largest:
            st.metric("Largest position", f"{largest['weight']:.1f}%", delta=largest["ticker"])
            st.progress(min(largest["weight"] / 100, 1.0))
    with sc2:
        st.markdown("##### Stop Status")
        breached = sum(1 for p in positions if p["cmp"] <= p["stop"] and p["stop"] > 0)
        sc2a, sc2b = st.columns(2)
        sc2a.metric("Breached", breached)
        sc2b.metric("Clear", len(positions) - breached)
    with sc3:
        st.markdown("##### Holding Period")
        days_list = [p["days_in"] for p in positions if p["days_in"] is not None]
        if days_list:
            sc3a, sc3b = st.columns(2)
            sc3a.metric("Max", f"{max(days_list)}d")
            sc3b.metric("Min", f"{min(days_list)}d")

    # Detail panel on row click
    st.divider()
    selected_rows = event.selection.rows if (not df_pos.empty and event.selection) else []
    if selected_rows:
        sel_ticker = df_pos.iloc[selected_rows[0]]["Ticker"]
    elif positions:
        sel_ticker = positions[0]["ticker"]
        st.caption("👆 Click any row above to view its details")
    else:
        sel_ticker = None

    if sel_ticker:
        pos = next((p for p in positions if p["ticker"] == sel_ticker), None)
        if pos:
            st.markdown(f"### {sel_ticker} — {pos['sleeve']} · {pos['sector']}")

            mc = st.columns(6)
            mc[0].metric("CMP" if pos["is_live"] else "Entry", f"₹{pos['cmp']:,.2f}")
            mc[1].metric("Stop-loss", f"₹{pos['stop']:,.0f}",
                         delta=f"₹{pos['stop_dist_abs']:.0f} away" if pos['stop'] else None,
                         delta_color="inverse")
            if pos["target"]:
                pct_to = ((pos["target"] - pos["cmp"]) / pos["cmp"] * 100) if pos["cmp"] else 0
                mc[2].metric("Target", f"₹{pos['target']:,.0f}", delta=f"{pct_to:.1f}% to go")
            else:
                mc[2].metric("Target", "—")
            mc[3].metric("R-Multiple", f"{pos['r_multiple']:+.1f}R")
            mc[4].metric("Qty", f"{int(pos['qty'])}")
            mc[5].metric("Unrealized P&L", fmt(pos["pnl"]),
                         delta=fmt_pct(pos["pnl_pct"]))

            # Lots + News
            dl, dr = st.columns(2)
            with dl:
                tlots = [l for l in D["lots"] if l.get("trade_id") == pos["trade_id"]]
                if tlots:
                    st.markdown("**📦 Lots**")
                    for lot in tlots:
                        lt = lot.get('lot_type') or 'entry'
                        prefix = "🔴 SOLD" if lt == 'exit' else ""
                        qty_label = lot.get('qty')
                        price_label = lot.get('price')
                        date_label = lot.get('lot_date') or 'date not set'
                        if lt == 'exit':
                            st.markdown(f"  🔴 **SOLD** {qty_label} shares @ ₹{price_label} — {date_label}")
                        else:
                            st.text(f"  {qty_label} shares @ ₹{price_label} — {date_label}")

            with dr:
                tnews = [n for n in D["news"] if n.get("ticker") == sel_ticker][:8]
                if tnews:
                    st.markdown("**📰 Recent announcements**")
                    for n in tnews:
                        sev = n.get("severity_tag", "routine update")
                        headline = n.get("headline") or ""
                        reasoning = n.get("severity_reasoning") or ""
                        snippet = n.get("body_snippet") or ""
                        url = n.get("url") or ""
                        source = n.get("source") or ""
                        date_str = short_date(n.get("published_at"))

                        # Context: prefer body_snippet, fall back to severity_reasoning
                        context = snippet or reasoning
                        context_html = ""
                        if context:
                            context_html = (
                                f'<div style="font-size:11px;color:#666;margin-top:2px;'
                                f'line-height:1.3;white-space:normal">{context}</div>'
                            )

                        # Source label + link
                        source_label = "NSE Filing" if source == "nse" else "Google News" if source == "google_news" else source
                        link_html = ""
                        if url and url.startswith("http"):
                            link_html = (
                                f' · <a href="{url}" target="_blank" '
                                f'style="color:#2563eb;text-decoration:none;font-size:11px">'
                                f'{source_label} ↗</a>'
                            )
                        else:
                            link_html = f' · {source_label}' if source_label else ''

                        # Border color by severity
                        if sev == "thesis-threatening":
                            border = "#ef4444"; bg = "rgba(239,68,68,.05)"
                        elif sev == "material change":
                            border = "#f59e0b"; bg = "rgba(245,158,11,.04)"
                        else:
                            border = "#d1d5db"; bg = "rgba(0,0,0,.02)"

                        st.markdown(
                            f'<div style="padding:8px 10px;margin:3px 0;background:{bg};'
                            f'border-left:3px solid {border};border-radius:3px">'
                            f'<div style="font-size:12px;font-weight:500;line-height:1.3">'
                            f'{sev_icon(sev)} {headline}</div>'
                            f'{context_html}'
                            f'<div style="font-size:10px;color:#999;margin-top:3px">'
                            f'{date_str}{link_html}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

            # Fundamentals
            snap = next((s for s in D["fundsnap"] if s.get("ticker") == sel_ticker), None)
            st.markdown("**📊 Fundamentals**")
            if snap:
                fc = st.columns(6)
                pe = f(snap.get("pe"))
                roe = f(snap.get("roe"))
                roce = f(snap.get("roce"))
                ebit = f(snap.get("ebit_margin"))
                rev = f(snap.get("revenue_growth_yoy"))
                de = f(snap.get("debt_to_equity"))
                fc[0].metric("PE", f"{pe:.1f}x" if pe else "—")
                fc[1].metric("ROE", f"{roe:.1f}%" if roe else "—")
                fc[2].metric("ROCE", f"{roce:.1f}%" if roce else "—")
                fc[3].metric("EBIT Margin", f"{ebit:.1f}%" if ebit else "—")
                fc[4].metric("Rev Growth", f"{rev:.1f}%" if rev else "—")
                fc[5].metric("D/E", f"{de:.2f}x" if de else "—")
            else:
                st.caption("No fundamentals snapshot yet.")

            # ── Technical Chart + Statistical Edge Model ──────────
            st.divider()
            st.markdown("**📈 Technical Analysis & Statistical Edge**")

            hist_df = fetch_stock_history(sel_ticker)
            if hist_df is not None and len(hist_df) > 30:
                tech_df = compute_technicals(hist_df)

                # Indicator toggles — row 1 (overlays on price chart)
                ovr1, ovr2 = st.columns(2)
                show_ema = ovr1.checkbox("EMAs (20/50/200)", value=True, key=f"ema_{sel_ticker}")
                show_bb = ovr2.checkbox("Bollinger Bands", value=True, key=f"bb_{sel_ticker}")

                # Sub-chart indicators — user picks order via multiselect
                available_subs = ["RSI", "Rel. Strength", "MACD", "Volume"]
                active_subs = st.multiselect(
                    "Sub-chart indicators (drag to reorder)",
                    available_subs, default=["RSI", "Rel. Strength", "Volume"],
                    key=f"subs_{sel_ticker}",
                )

                show_rsi = "RSI" in active_subs
                show_rs = "Rel. Strength" in active_subs
                show_macd = "MACD" in active_subs
                show_vol = "Volume" in active_subs

                # Benchmark selector (visible when RS toggled on)
                rs_df = None
                if show_rs:
                    rs_bench = st.selectbox(
                        "RS benchmark", list(_RS_BENCHMARKS.keys()),
                        index=0, key=f"rs_bench_{sel_ticker}",
                    )
                    bench_sym = _RS_BENCHMARKS[rs_bench]
                    bench_data = fetch_benchmark_history(bench_sym)
                    if bench_data is not None:
                        rs_df = compute_relative_strength(tech_df, bench_data)

                # Build subplot list in user-chosen order
                sub_configs = []  # list of (name, height)
                for sub_name in active_subs:
                    sub_configs.append((sub_name, 0.13))

                n_rows = 1 + len(sub_configs)
                row_specs = [{"secondary_y": False}] * n_rows
                main_h = max(0.35, 0.65 - len(sub_configs) * 0.08)
                row_heights = [main_h] + [s[1] for s in sub_configs]

                from plotly.subplots import make_subplots
                fig_tech = make_subplots(
                    rows=n_rows, cols=1, shared_xaxes=True,
                    vertical_spacing=0.03,
                    row_heights=row_heights,
                    specs=[[s] for s in row_specs],
                )

                # Candlestick — with OHLCV hover
                fig_tech.add_trace(go.Candlestick(
                    x=tech_df["Date"], open=tech_df["Open"],
                    high=tech_df["High"], low=tech_df["Low"],
                    close=tech_df["Close"], name="Price",
                    increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
                    text=[f"O: ₹{o:,.1f}<br>H: ₹{h:,.1f}<br>L: ₹{l:,.1f}<br>C: ₹{c:,.1f}<br>Vol: {v:,.0f}"
                          for o, h, l, c, v in zip(tech_df["Open"], tech_df["High"],
                                                    tech_df["Low"], tech_df["Close"],
                                                    tech_df["Volume"])],
                    hoverinfo="text+x",
                ), row=1, col=1)

                # EMAs
                if show_ema:
                    for span, clr in [(20, "#f59e0b"), (50, "#3b82f6"), (200, "#a855f7")]:
                        col_name = f"EMA{span}"
                        if col_name in tech_df.columns:
                            valid = tech_df.dropna(subset=[col_name])
                            fig_tech.add_trace(go.Scatter(
                                x=valid["Date"], y=valid[col_name],
                                mode="lines", name=col_name,
                                line=dict(color=clr, width=1),
                                hovertemplate=f"{col_name}: ₹%{{y:,.1f}}<extra></extra>",
                            ), row=1, col=1)

                # Bollinger Bands
                if show_bb:
                    valid_bb = tech_df.dropna(subset=["BB_Upper"])
                    fig_tech.add_trace(go.Scatter(
                        x=valid_bb["Date"], y=valid_bb["BB_Upper"],
                        mode="lines", name="BB Upper",
                        line=dict(color="#94a3b8", width=0.8, dash="dash"),
                        hovertemplate="BB Upper: ₹%{y:,.1f}<extra></extra>",
                        showlegend=False,
                    ), row=1, col=1)
                    fig_tech.add_trace(go.Scatter(
                        x=valid_bb["Date"], y=valid_bb["BB_Lower"],
                        mode="lines", name="BB Lower",
                        line=dict(color="#94a3b8", width=0.8, dash="dash"),
                        fill="tonexty", fillcolor="rgba(148,163,184,0.08)",
                        hovertemplate="BB Lower: ₹%{y:,.1f}<extra></extra>",
                        showlegend=False,
                    ), row=1, col=1)

                # Entry / Stop / Target horizontal lines
                if pos["entry"] and pos["entry"] > 0:
                    fig_tech.add_hline(y=pos["entry"], line_dash="dot", line_color="#3b82f6",
                                       line_width=1, annotation_text="Entry",
                                       annotation_position="right", row=1, col=1)
                if pos["stop"] and pos["stop"] > 0:
                    fig_tech.add_hline(y=pos["stop"], line_dash="dot", line_color="#ef4444",
                                       line_width=1, annotation_text="Stop",
                                       annotation_position="right", row=1, col=1)
                if pos.get("target") and pos["target"] > 0:
                    fig_tech.add_hline(y=pos["target"], line_dash="dot", line_color="#22c55e",
                                       line_width=1, annotation_text="Target",
                                       annotation_position="right", row=1, col=1)

                # Render sub-chart indicators in user-chosen order
                cur_row = 2
                for sub_name, _ in sub_configs:
                    if sub_name == "Rel. Strength":
                        if rs_df is not None and not rs_df.empty:
                            fig_tech.add_trace(go.Scatter(
                                x=rs_df["Date"], y=rs_df["RS"],
                                mode="lines", name=f"RS vs {rs_bench}",
                                line=dict(color="#0ea5e9", width=1.5),
                                hovertemplate=f"RS vs {rs_bench}: %{{y:.1f}}<extra></extra>",
                            ), row=cur_row, col=1)
                            fig_tech.add_trace(go.Scatter(
                                x=rs_df["Date"], y=rs_df["RS_MA"],
                                mode="lines", name="RS MA(20)",
                                line=dict(color="#0ea5e9", width=0.8, dash="dash"),
                                hovertemplate="RS MA(20): %{y:.1f}<extra></extra>",
                                showlegend=False,
                            ), row=cur_row, col=1)
                            fig_tech.add_hline(y=100, line_dash="dot", line_color="#94a3b8",
                                               line_width=0.5, row=cur_row, col=1)
                        fig_tech.update_yaxes(title_text="RS", row=cur_row, col=1)

                    elif sub_name == "RSI":
                        valid_rsi = tech_df.dropna(subset=["RSI"])
                        fig_tech.add_trace(go.Scatter(
                            x=valid_rsi["Date"], y=valid_rsi["RSI"],
                            mode="lines", name="RSI(14)",
                            line=dict(color="#8b5cf6", width=1.2),
                            hovertemplate="RSI: %{y:.1f}<extra></extra>",
                        ), row=cur_row, col=1)
                        valid_rsi_ma = tech_df.dropna(subset=["RSI_MA"])
                        fig_tech.add_trace(go.Scatter(
                            x=valid_rsi_ma["Date"], y=valid_rsi_ma["RSI_MA"],
                            mode="lines", name="RSI MA",
                            line=dict(color="#f59e0b", width=0.8, dash="dash"),
                            hovertemplate="RSI MA: %{y:.1f}<extra></extra>",
                        ), row=cur_row, col=1)
                        fig_tech.add_hline(y=70, line_dash="dash", line_color="#ef4444",
                                           line_width=0.5, row=cur_row, col=1)
                        fig_tech.add_hline(y=30, line_dash="dash", line_color="#22c55e",
                                           line_width=0.5, row=cur_row, col=1)
                        fig_tech.update_yaxes(title_text="RSI", range=[10, 90], row=cur_row, col=1)

                    elif sub_name == "MACD":
                        valid_macd = tech_df.dropna(subset=["MACD"])
                        fig_tech.add_trace(go.Scatter(
                            x=valid_macd["Date"], y=valid_macd["MACD"],
                            mode="lines", name="MACD",
                            line=dict(color="#3b82f6", width=1),
                        ), row=cur_row, col=1)
                        fig_tech.add_trace(go.Scatter(
                            x=valid_macd["Date"], y=valid_macd["MACD_Signal"],
                            mode="lines", name="Signal",
                            line=dict(color="#f59e0b", width=1, dash="dash"),
                        ), row=cur_row, col=1)
                        colors_macd = ["#22c55e" if v >= 0 else "#ef4444"
                                       for v in valid_macd["MACD_Hist"]]
                        fig_tech.add_trace(go.Bar(
                            x=valid_macd["Date"], y=valid_macd["MACD_Hist"],
                            name="Histogram", marker_color=colors_macd,
                            showlegend=False,
                        ), row=cur_row, col=1)
                        fig_tech.update_yaxes(title_text="MACD", row=cur_row, col=1)

                    elif sub_name == "Volume":
                        vol_colors = ["#22c55e" if tech_df["Close"].iloc[i] >= tech_df["Open"].iloc[i]
                                      else "#ef4444" for i in range(len(tech_df))]
                        fig_tech.add_trace(go.Bar(
                            x=tech_df["Date"], y=tech_df["Volume"],
                            name="Volume", marker_color=vol_colors,
                            opacity=0.5, showlegend=False,
                        ), row=cur_row, col=1)
                        fig_tech.update_yaxes(title_text="Vol", row=cur_row, col=1)

                    cur_row += 1

                chart_height = 400 + len(sub_configs) * 120
                # Rangeslider goes on the LAST x-axis (bottom subplot) to avoid
                # the Plotly candlestick rangeslider rendering bug
                last_xaxis = f"xaxis{n_rows}" if n_rows > 1 else "xaxis"
                layout_update = {
                    "height": chart_height,
                    "margin": dict(l=0, r=0, t=30, b=10),
                    "legend": dict(orientation="h", yanchor="top", y=1.04,
                                   xanchor="left", x=0, font=dict(size=10)),
                    "plot_bgcolor": "rgba(0,0,0,0)",
                    "paper_bgcolor": "rgba(0,0,0,0)",
                    "dragmode": "zoom",
                    "xaxis": dict(
                        rangeselector=dict(
                            buttons=[
                                dict(count=1, label="1M", step="month", stepmode="backward"),
                                dict(count=3, label="3M", step="month", stepmode="backward"),
                                dict(count=6, label="6M", step="month", stepmode="backward"),
                                dict(count=1, label="1Y", step="year", stepmode="backward"),
                                dict(step="all", label="All"),
                            ],
                            bgcolor="#f3f4f6", activecolor="#355ec9",
                        ),
                        rangeslider=dict(visible=False),
                    ),
                }
                # Put rangeslider on the bottom subplot axis
                if n_rows > 1:
                    layout_update[last_xaxis] = dict(
                        rangeslider=dict(visible=True, thickness=0.05),
                    )
                else:
                    layout_update["xaxis"]["rangeslider"] = dict(visible=True, thickness=0.05)

                fig_tech.update_layout(**layout_update)
                fig_tech.update_xaxes(showgrid=False)
                fig_tech.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.05)")
                st.plotly_chart(fig_tech, use_container_width=True, config={
                    "displayModeBar": True,
                    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
                    "displaylogo": False,
                })

                # ── Statistical Edge Scorecard ────────────────
                _entry = pos.get("entry") or 0
                _stop = pos.get("stop") or 0
                _target = pos.get("target") or 0
                # If target is 0 but we have entry+stop, estimate 2:1 R:R target
                # For trailed stops (stop > entry), use initial risk estimate
                if _target == 0 and _entry > 0 and _stop > 0:
                    _risk = abs(_entry - _stop) if _stop < _entry else _entry * 0.05  # 5% default risk if stop trailed above
                    _target = _entry + 2 * _risk
                stats = compute_stats_model(tech_df, entry=_entry, stop=_stop, target=_target)

                st.markdown("**🎯 Statistical Edge Scorecard**")

                # Debug: show actual values being used (can remove later)
                _cmp_val = tech_df['Close'].iloc[-1]
                _atr_display = f"₹{stats['atr']:.1f}" if stats.get('atr') else "—"
                st.caption(f"📊 Entry: ₹{_entry:,.0f} | Stop: ₹{_stop:,.0f} | Target: ₹{_target:,.0f} | CMP: ₹{_cmp_val:,.0f} | ATR: {_atr_display}")

                # ── Row 1: Conviction + Monte Carlo probabilities ────
                sc1, sc2, sc3, sc4 = st.columns(4)

                _mc_note = "EWMA vol · 10K sims" if (pos.get("target") and pos["target"] > 0) else "EWMA vol · est. 2:1 tgt"
                _tgt_hit = stats.get("target_already_hit", False)
                _stp_hit = stats.get("stop_already_hit", False)
                _conviction = stats.get("conviction")

                # Conviction score (col 1) — always show when available
                if _conviction is not None:
                    if _conviction >= 65:
                        conv_color = "#22c55e"
                        conv_label = "Strong"
                    elif _conviction >= 40:
                        conv_color = "#3b82f6"
                        conv_label = "Moderate"
                    elif _conviction >= 20:
                        conv_color = "#f59e0b"
                        conv_label = "Weak"
                    else:
                        conv_color = "#ef4444"
                        conv_label = "Poor"
                    _er = stats.get("edge_ratio") or 0
                    _er_txt = f'{_er:.1f}×' if _er < 99 else "∞"
                    sc1.markdown(
                        f'<div style="padding:8px;background:rgba(59,130,246,.06);'
                        f'border-radius:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Conviction</div>'
                        f'<div style="font-size:22px;font-weight:700;color:{conv_color}">'
                        f'{_conviction}</div>'
                        f'<div style="font-size:10px;font-weight:600;color:{conv_color}">{conv_label}</div>'
                        f'<div style="font-size:9px;color:#999">Edge ratio: {_er_txt}</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                else:
                    sc1.caption("Conviction: insufficient data")

                # Target / Stop / Chop (col 2) — combined probabilities bar
                if _tgt_hit:
                    sc2.markdown(
                        f'<div style="padding:8px;background:rgba(34,197,94,.12);'
                        f'border-radius:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Target Status</div>'
                        f'<div style="font-size:16px;font-weight:700;color:#22c55e">'
                        f'✅ Already above</div>'
                        f'<div style="font-size:9px;color:#999">Trail stop · buffer {(_cmp_val - _stop) / _cmp_val * 100:.1f}%</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                elif _stp_hit:
                    sc2.markdown(
                        f'<div style="padding:8px;background:rgba(239,68,68,.12);'
                        f'border-radius:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Stop Status</div>'
                        f'<div style="font-size:16px;font-weight:700;color:#ef4444">'
                        f'🔴 BREACHED</div>'
                        f'<div style="font-size:9px;color:#999">Exit per rules</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                elif stats["target_prob"] is not None:
                    _tp = stats["target_prob"]
                    _sp = stats["stop_prob"]
                    _cp = stats["chop_prob"] or 0
                    # Stacked probability bar
                    sc2.markdown(
                        f'<div style="padding:8px;background:rgba(0,0,0,.02);'
                        f'border-radius:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Monte Carlo Outcome</div>'
                        f'<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin:6px 0">'
                        f'<div style="width:{_tp}%;background:#22c55e"></div>'
                        f'<div style="width:{_cp}%;background:#94a3b8"></div>'
                        f'<div style="width:{_sp}%;background:#ef4444"></div>'
                        f'</div>'
                        f'<div style="display:flex;justify-content:space-between;font-size:11px">'
                        f'<span style="color:#22c55e;font-weight:600">🎯 {_tp:.0f}%</span>'
                        f'<span style="color:#94a3b8">{_cp:.0f}% chop</span>'
                        f'<span style="color:#ef4444;font-weight:600">🛑 {_sp:.0f}%</span>'
                        f'</div>'
                        f'<div style="font-size:9px;color:#999;margin-top:2px">{_mc_note}</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                else:
                    sc2.caption("Monte Carlo: insufficient data")

                # Momentum + Vol regime (col 3)
                ac = stats["autocorr"] or 0.0
                ac5 = stats.get("autocorr_5") or 0.0
                momentum_char = stats["momentum_char"] or "—"
                mc_color = "#3b82f6" if momentum_char == "Trending" else (
                    "#f59e0b" if momentum_char == "Mean-reverting" else "#94a3b8")
                _vr = stats.get("vol_regime") or "—"
                _vr_color = "#ef4444" if _vr == "Expanding" else ("#22c55e" if _vr == "Contracting" else "#94a3b8")
                sc3.markdown(
                    f'<div style="padding:8px;background:rgba(59,130,246,.06);'
                    f'border-radius:6px;text-align:center">'
                    f'<div style="font-size:10px;color:#666">Momentum · Vol</div>'
                    f'<div style="font-size:15px;font-weight:700;color:{mc_color}">'
                    f'{momentum_char}</div>'
                    f'<div style="font-size:10px;color:{_vr_color};font-weight:600">Vol {_vr}</div>'
                    f'<div style="font-size:9px;color:#999">ρ₁={ac:.3f} ρ₅={ac5:.3f}</div>'
                    f'</div>', unsafe_allow_html=True
                )

                # Resolution + Volatility (col 4)
                atr_txt = f'ATR ₹{stats["atr"]:.1f} ({stats["atr_pct"]:.1f}%)' if stats["atr"] else "—"
                stop_atr_txt = f'Stop: {stats["stop_atr"]:.1f}×ATR' if stats["stop_atr"] else ""
                _exp_days = stats.get("exp_days")
                _exp_days_txt = f'~{_exp_days}d to resolve' if _exp_days and _exp_days < 40 else "May not resolve in 40d"
                _ev = stats.get("expected_value")
                _ev_txt = f'EV: {"+" if _ev >= 0 else ""}{_ev:.1f}%' if _ev is not None else ""
                sc4.markdown(
                    f'<div style="padding:8px;background:rgba(168,85,247,.06);'
                    f'border-radius:6px;text-align:center">'
                    f'<div style="font-size:10px;color:#666">Resolution</div>'
                    f'<div style="font-size:14px;font-weight:600">{_exp_days_txt}</div>'
                    f'<div style="font-size:10px;font-weight:600;color:{"#22c55e" if (_ev or 0) > 0 else "#ef4444"}">{_ev_txt}</div>'
                    f'<div style="font-size:9px;color:#999">{atr_txt} · {stop_atr_txt}</div>'
                    f'</div>', unsafe_allow_html=True
                )

                # ── Row 2: MFE / MAE / Streak / Daily σ ────
                if stats["mfe_further"] is not None:
                    mf1, mf2, mf3, mf4 = st.columns(4)
                    mf1.markdown(
                        f'<div style="padding:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Median Further Upside (MFE)</div>'
                        f'<div style="font-size:16px;font-weight:600;color:#22c55e">'
                        f'+{stats["mfe_further"]:.1f}%</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                    mf2.markdown(
                        f'<div style="padding:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Median Pullback (MAE)</div>'
                        f'<div style="font-size:16px;font-weight:600;color:#ef4444">'
                        f'-{stats["mfe_pullback"]:.1f}%</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                    streak_icon = "🟢" if stats.get("streak_dir") == "green" else "🔴"
                    mf3.markdown(
                        f'<div style="padding:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">Current Streak</div>'
                        f'<div style="font-size:16px;font-weight:600">'
                        f'{streak_icon} {stats.get("current_streak", 0)} day(s)</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                    big_move = stats.get("big_move_pct") or 0
                    _ewma_s = stats.get("ewma_sigma") or 0
                    _s14 = stats.get("sigma_14") or 0
                    mf4.markdown(
                        f'<div style="padding:6px;text-align:center">'
                        f'<div style="font-size:10px;color:#666">σ EWMA / 14d</div>'
                        f'<div style="font-size:16px;font-weight:600">'
                        f'{_ewma_s:.2f}% / {_s14:.2f}%</div>'
                        f'<div style="font-size:9px;color:#999">{big_move:.0f}% days >3% move</div>'
                        f'</div>', unsafe_allow_html=True
                    )

                # ── Action signals ────
                signals = []
                if _tgt_hit:
                    signals.append("🟢 Target already reached — consider booking partial / trailing stop")
                elif _stp_hit:
                    signals.append("🔴 Stop breached — exit per position rules")
                elif stats["target_prob"] is not None:
                    if _conviction and _conviction >= 65:
                        signals.append("🟢 High conviction — hold / add on dips")
                    elif _conviction and _conviction >= 40:
                        signals.append("🔵 Moderate conviction — hold, no add")
                    elif stats["stop_prob"] > 55:
                        signals.append("🔴 Stop probability dominant — consider reducing or tightening")
                    elif stats["chop_prob"] and stats["chop_prob"] > 50:
                        signals.append("🟡 High chop — capital likely stuck; reduce size or wait for breakout")
                    if stats.get("expected_value") is not None and stats["expected_value"] < -1:
                        signals.append("⚠️ Negative expected value — risk/reward unfavorable at current levels")
                if momentum_char == "Trending" and (stats.get("pct_from_entry") or 0) > 5:
                    signals.append("📈 Trending with momentum — trail stop, don't exit early")
                if momentum_char == "Mean-reverting" and (stats.get("pct_from_entry") or 0) > 10:
                    signals.append("🔄 Mean-reverting stock up big — consider partial profit")
                if stats["stop_atr"] and stats["stop_atr"] < 1:
                    signals.append("⚠️ Stop < 1×ATR — very tight; one normal day can trigger it")
                _vr_val = stats.get("vol_regime")
                if _vr_val == "Expanding":
                    signals.append("💥 Volatility expanding — wider swings; be prepared for whipsaws")
                elif _vr_val == "Contracting":
                    signals.append("🔋 Volatility contracting — coiling; breakout move likely imminent")

                if signals:
                    signal_html = "".join(
                        f'<div style="padding:4px 8px;margin:2px 0;background:rgba(0,0,0,.02);'
                        f'border-radius:3px;font-size:12px">{s}</div>' for s in signals
                    )
                    st.markdown(
                        f'<div style="margin-top:6px;padding:8px;border:1px solid #e5e7eb;border-radius:6px">'
                        f'<div style="font-size:11px;font-weight:600;color:#666;margin-bottom:4px">'
                        f'ACTION SIGNALS</div>{signal_html}</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.caption("Could not load price history from Yahoo Finance.")


# ═══════════════════════════════════════════════════════════
# TAB 3 — RISK
# ═══════════════════════════════════════════════════════════
with tab_risk:
    # Top-level risk KPIs
    r1, r2, r3 = st.columns(3)
    total_car = sum(p["capital_at_risk"] for p in positions)
    r1.metric("Capital Deployed", fmt(total_invested), delta=f"{len(positions)} positions")
    r2.metric("Capital at Risk (to stop)", fmt(total_car),
              delta=f"{total_car / total_invested * 100:.1f}% of invested" if total_invested else None)
    r3.metric("Thesis Breaks", sum(1 for p in positions if p["thesis"] == "Threatened"),
              delta="positions with thesis-threatening news")

    st.divider()

    # Stop proximity visualization
    st.markdown("#### Stop Proximity")
    st.caption("How much buffer each position has before stop-loss")

    sorted_by_stop = sorted(positions, key=lambda p: p["stop_dist_pct"])
    for p in sorted_by_stop:
        buf_pct = max(0, min(100, p["stop_dist_pct"]))
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            bar_color = RED
            label = "BREACHED"
        elif buf_pct < 50:
            bar_color = AMBER
            label = f"₹{p['stop_dist_abs']:.0f} ({(p['cmp'] - p['stop']) / p['cmp'] * 100:.1f}%)"
        else:
            bar_color = GREEN
            label = f"₹{p['stop_dist_abs']:.0f} ({(p['cmp'] - p['stop']) / p['cmp'] * 100:.1f}%)"

        c1, c2, c3 = st.columns([1.5, 4, 1.5])
        c1.markdown(f"**{p['ticker']}**")
        c2.markdown(
            f'<div class="stop-bar"><div class="stop-fill" style="width:{buf_pct}%;background:{bar_color}"></div></div>',
            unsafe_allow_html=True)
        c3.markdown(f"<small>{label}</small>", unsafe_allow_html=True)

    st.divider()

    # Risk concentration heatmap
    st.markdown("#### Risk Concentration — Sector × Sleeve")
    st.caption("Capital at risk by sector and sleeve")

    sleeves = sorted(set(p["sleeve"] for p in positions))
    sectors = sorted(set(p["sector"] for p in positions))

    matrix = {}
    max_risk = 0
    for p in positions:
        key = (p["sector"], p["sleeve"])
        matrix[key] = matrix.get(key, 0) + p["capital_at_risk"]
        max_risk = max(max_risk, matrix[key])

    z_data = []
    text_data = []
    for sector in sectors:
        row = []
        text_row = []
        for sleeve in sleeves:
            val = matrix.get((sector, sleeve), 0)
            row.append(val)
            text_row.append(fmt(val) if val > 0 else "—")
        z_data.append(row)
        text_data.append(text_row)

    fig_heat = go.Figure(data=go.Heatmap(
        z=z_data, x=sleeves, y=sectors,
        text=text_data, texttemplate="%{text}",
        colorscale=[[0, "#eaf0fb"], [0.3, "#bfcff2"], [0.6, "#5d7bd0"], [1, "#2a4a8f"]],
        showscale=True, colorbar=dict(title="Risk ₹"),
        hovertemplate='%{y} × %{x}: %{text}<extra></extra>',
    ))
    fig_heat.update_layout(
        height=max(200, 50 * len(sectors)),
        margin=dict(l=0, r=0, t=10, b=10),
        xaxis=dict(side="top"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()
    st.markdown("#### Risk Rules — Future Implementation")
    rc1, rc2 = st.columns(2)
    with rc1:
        st.markdown("""
        | Rule | Threshold |
        |------|-----------|
        | Position concentration alert | > configurable limit |
        | Sector concentration alert | > configurable limit |
        | Portfolio risk budget | configurable % |
        | Stop breach | Hard alert |
        """)
    with rc2:
        st.markdown("""
        | Rule | Threshold |
        |------|-----------|
        | Liquidity check | ADV / position size |
        | Correlation | 30/60/120d |
        | Drawdown | Portfolio + sleeve |
        | Stress test | Nifty / sector shock |
        """)


# ═══════════════════════════════════════════════════════════
# TAB 4 — PERFORMANCE
# ═══════════════════════════════════════════════════════════
with tab_perf:
    # KPI row
    pk1, pk2, pk3, pk4, pk5, pk6 = st.columns(6)
    pk1.metric("Net P&L", fmt(net_pnl))
    pk2.metric("Realized P&L", fmt(realized_pnl))
    pk3.metric("Unrealized P&L", fmt(unrealized_pnl))
    pk4.metric("Closed Trades", len(D["closed"]))
    pk5.metric("Win Rate", f"{wins}/{len(D['closed'])}" if D["closed"] else "—")
    # Avg holding period of closed trades
    hold_days = [f(t.get("holding_period_days")) for t in D["closed"] if f(t.get("holding_period_days"))]
    pk6.metric("Avg Hold", f"{sum(hold_days) / len(hold_days):.0f}d" if hold_days else "—")

    st.divider()

    pc1, pc2 = st.columns(2)

    with pc1:
        st.markdown("#### P&L Attribution")
        st.caption("Current P&L contribution by position")

        sorted_pos = sorted(positions, key=lambda p: p["pnl"])
        fig_attr = go.Figure()
        fig_attr.add_trace(go.Bar(
            y=[p["ticker"] for p in sorted_pos],
            x=[p["pnl"] for p in sorted_pos],
            orientation='h',
            marker_color=[GREEN if p["pnl"] >= 0 else RED for p in sorted_pos],
            text=[fmt(p["pnl"]) for p in sorted_pos],
            textposition='outside',
            hovertemplate='%{y}: %{x:,.0f}<extra></extra>',
        ))
        fig_attr.update_layout(
            height=max(200, 45 * len(sorted_pos)),
            margin=dict(l=0, r=60, t=10, b=10),
            xaxis=dict(showgrid=True, gridcolor="#eef0f3", title="P&L (₹)", zeroline=True, zerolinecolor="#999"),
            yaxis=dict(showgrid=False),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_attr, use_container_width=True)

    with pc2:
        st.markdown("#### Closed Trades")
        if D["closed"]:
            for t in D["closed"]:
                ep = f(t.get("entry_price")) or 0
                xp = f(t.get("exit_price")) or 0
                ret = f(t.get("realized_return_pct"))
                win = (ret or 0) >= 0
                icon = "✅" if win else "❌"
                col = GREEN if win else RED
                ret_s = f"+{ret:.1f}%" if ret and ret > 0 else f"{ret:.1f}%" if ret else "—"
                reason = t.get("exit_reason", "")
                ticker = t.get("ticker", "")
                sleeve = t.get("sleeve", "")

                st.markdown(
                    f'<div style="padding:8px 12px;margin:3px 0;border-radius:6px;border:1px solid #e5e7eb;'
                    f'display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
                    f'<span style="font-weight:700;min-width:85px">{icon} {ticker}</span>'
                    f'<span class="badge" style="background:#818cf822;color:#818cf8">{sleeve}</span>'
                    f'<span style="color:#666;font-size:12px">₹{ep:,.0f} → ₹{xp:,.0f}</span>'
                    f'<span class="badge" style="background:{col}18;color:{col}">{reason}</span>'
                    f'<span style="color:{col};font-weight:700;font-size:16px;margin-left:auto">{ret_s}</span>'
                    f'</div>', unsafe_allow_html=True)
        else:
            st.caption("No closed trades yet.")

    st.divider()

    # Sleeve P&L breakdown chart
    st.markdown("#### Sleeve P&L Breakdown")
    sleeve_pnl = {}
    for p in positions:
        sl = p["sleeve"]
        sleeve_pnl[sl] = sleeve_pnl.get(sl, 0) + p["pnl"]
    # Add closed trades
    for t in D["closed"]:
        sl = t.get("sleeve", "Unknown")
        ep = f(t.get("entry_price")) or 0
        xp = f(t.get("exit_price")) or 0
        trade_lots = [l for l in D["lots"] if l.get("trade_id") == t.get("trade_id")]
        trade_qty = sum(f(l.get("qty")) or 0 for l in trade_lots)
        if trade_qty > 0:
            sleeve_pnl[sl] = sleeve_pnl.get(sl, 0) + (xp - ep) * trade_qty

    fig_sleeve = go.Figure()
    sl_names = sorted(sleeve_pnl.keys())
    sl_vals = [sleeve_pnl[s] for s in sl_names]
    fig_sleeve.add_trace(go.Bar(
        x=sl_names, y=sl_vals,
        marker_color=[SLEEVE_COLORS.get(s, "#666") for s in sl_names],
        text=[fmt(v) for v in sl_vals],
        textposition='outside',
        hovertemplate='%{x}: %{y:,.0f}<extra></extra>',
    ))
    fig_sleeve.update_layout(
        height=280, margin=dict(l=0, r=0, t=10, b=10),
        yaxis=dict(showgrid=True, gridcolor="#eef0f3", title="P&L (₹)", zeroline=True, zerolinecolor="#999"),
        xaxis=dict(showgrid=False),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_sleeve, use_container_width=True)

    st.divider()

    # Trading System Statistics
    ts_left, ts_right = st.columns(2)

    with ts_left:
        st.markdown("#### 🎯 Trading System Statistics")
        st.caption("Computed from closed trades — builds as your track record grows")

        closed = D["closed"]
        if closed:
            # Core stats
            total_trades = len(closed)
            win_trades = [t for t in closed if (f(t.get("realized_return_pct")) or 0) > 0]
            loss_trades = [t for t in closed if (f(t.get("realized_return_pct")) or 0) <= 0]
            win_count = len(win_trades)
            loss_count = len(loss_trades)
            win_rate = (win_count / total_trades * 100) if total_trades else 0

            win_returns = [f(t.get("realized_return_pct")) or 0 for t in win_trades]
            loss_returns = [f(t.get("realized_return_pct")) or 0 for t in loss_trades]

            avg_win = (sum(win_returns) / len(win_returns)) if win_returns else 0
            avg_loss = (sum(loss_returns) / len(loss_returns)) if loss_returns else 0
            # Payoff ratio (avg win / avg loss magnitude)
            payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else float('inf')
            # Expectancy = (win% × avg_win) - (loss% × avg_loss_magnitude)
            expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * abs(avg_loss))

            # Largest win / loss
            all_returns = [f(t.get("realized_return_pct")) or 0 for t in closed]
            max_win = max(all_returns) if all_returns else 0
            max_loss = min(all_returns) if all_returns else 0

            # Exit reason breakdown
            exit_reasons = {}
            for t in closed:
                reason = t.get("exit_reason") or "Unknown"
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

            # Display
            stat_rows = [
                {"Metric": "Total Trades", "Value": str(total_trades)},
                {"Metric": "Win Rate", "Value": f"{win_rate:.0f}% ({win_count}W / {loss_count}L)"},
                {"Metric": "Avg Win", "Value": f"+{avg_win:.1f}%"},
                {"Metric": "Avg Loss", "Value": f"{avg_loss:.1f}%"},
                {"Metric": "Payoff Ratio", "Value": f"{payoff:.2f}x" if payoff != float('inf') else "∞ (no losses)"},
                {"Metric": "Expectancy", "Value": f"{expectancy:+.2f}% per trade"},
                {"Metric": "Largest Win", "Value": f"+{max_win:.1f}%"},
                {"Metric": "Largest Loss", "Value": f"{max_loss:.1f}%"},
                {"Metric": "Avg Hold Period", "Value": f"{sum(hold_days)/len(hold_days):.0f} days" if hold_days else "—"},
            ]
            st.dataframe(pd.DataFrame(stat_rows), use_container_width=True, hide_index=True)

            # Exit reason pie
            if exit_reasons:
                st.markdown("**Exit Reasons**")
                fig_exit = go.Figure(data=go.Pie(
                    labels=list(exit_reasons.keys()),
                    values=list(exit_reasons.values()),
                    hole=0.4,
                    marker_colors=SECTOR_COLORS[:len(exit_reasons)],
                    textinfo='label+value',
                    hovertemplate='%{label}: %{value} trades<extra></extra>',
                ))
                fig_exit.update_layout(
                    height=200, margin=dict(l=0, r=0, t=10, b=10),
                    showlegend=False,
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_exit, use_container_width=True)
        else:
            st.info("No closed trades yet — statistics build as you close positions.")

    with ts_right:
        st.markdown("#### 📊 Benchmark & Drawdown")

        # Use daily_snapshots for real drawdown if available
        snapshots = D.get("snapshots", [])
        has_snapshots = len(snapshots) > 0

        if has_snapshots:
            # ── Portfolio Value = Stock (open positions) + Cash ──
            # Cash tracks capital not currently deployed in positions.
            # Entry → cash decreases by cost basis.
            # Exit  → cash increases by exit proceeds.
            # This keeps portfolio value continuous through capital rotation:
            # when a position exits, stock drops but cash rises by the same
            # proceeds, so portfolio stays flat (± P&L). When that cash funds
            # a new position, cash drops but stock rises by the new cost.
            from collections import defaultdict

            # Build cash flow events: (date, amount)
            # Negative = capital deployed (buy), Positive = capital returned (sell)
            cash_events = []
            cost_basis_map = {}  # trade_id -> cost_basis
            for pm in D["pos"]:
                tid = pm.get("trade_id")
                ep = f(pm.get("entry_price"))
                qty = f(pm.get("quantity"))
                ed = pm.get("entry_date")
                if tid and ep and qty:
                    cb = ep * qty
                    cost_basis_map[tid] = cb
                    if ed:
                        cash_events.append((ed, -cb))

            realized_pnl = 0
            for cl in D.get("closed", []):
                tid = cl.get("trade_id")
                exit_date = cl.get("exit_date")
                exit_price = f(cl.get("exit_price"))
                if tid in cost_basis_map and exit_date and exit_price:
                    qty_pm = next((p for p in D["pos"] if p["trade_id"] == tid), None)
                    qty = f(qty_pm.get("quantity")) if qty_pm else None
                    if qty:
                        proceeds = exit_price * qty
                        cash_events.append((exit_date, proceeds))
                        realized_pnl += proceeds - cost_basis_map[tid]

            cash_events.sort()

            # Initial capital = enough so cash never goes negative
            # (= maximum capital simultaneously deployed at any point)
            cum = 0
            min_cum = 0
            for _, amt in cash_events:
                cum += amt
                min_cum = min(min_cum, cum)
            initial_capital = -min_cum if min_cum < 0 else 0

            # Build exit date lookup to filter out post-exit snapshots.
            # The backfill script fetches data through today for ALL positions
            # (including exited), so snapshots exist after exit — exclude them.
            exit_date_map = {}
            for cl in D.get("closed", []):
                tid = cl.get("trade_id")
                exd = cl.get("exit_date")
                if tid and exd:
                    exit_date_map[tid] = exd

            # Per-position snapshots with carry-forward for missing dates.
            # yfinance may not return data for every position on every date
            # (partial trading days, data gaps). Carry forward last known
            # value so a missing snapshot doesn't zero out a position.
            pos_snap = defaultdict(dict)
            for snap in snapshots:
                tid = snap.get("trade_id")
                sd = snap.get("snapshot_date")
                pv = f(snap.get("position_value"))
                if tid and sd and pv is not None:
                    if tid in exit_date_map and sd >= exit_date_map[tid]:
                        continue
                    pos_snap[tid][sd] = pv

            all_snap_dates = sorted(
                set().union(*(d.keys() for d in pos_snap.values()))
                if pos_snap else []
            )

            daily_stock = {}
            last_val = {}
            for d in all_snap_dates:
                total = 0
                for tid, dv in pos_snap.items():
                    if d in dv:
                        last_val[tid] = dv[d]
                        total += dv[d]
                    elif tid in last_val and (tid not in exit_date_map or d < exit_date_map[tid]):
                        total += last_val[tid]
                daily_stock[d] = total

            # Portfolio(D) = stock(D) + cash(D)
            # cash(D) = initial_capital + Σ cash_events on or before D
            sorted_dates = all_snap_dates
            daily_portfolio = []
            for d in sorted_dates:
                stock = daily_stock[d]
                cash = initial_capital + sum(amt for ed, amt in cash_events if ed <= d)
                daily_portfolio.append(stock + cash)

            # Add today's live value
            today_str = datetime.now().strftime("%Y-%m-%d")
            active_stock = sum(p["current_value"] for p in positions)
            today_cash = initial_capital + sum(amt for _, amt in cash_events)
            today_portfolio = active_stock + today_cash
            if today_str not in set(sorted_dates):
                sorted_dates = list(sorted_dates) + [today_str]
                daily_portfolio.append(today_portfolio)
            else:
                # Replace snapshot-date value with live
                idx = sorted_dates.index(today_str)
                daily_portfolio[idx] = today_portfolio

            # Peak and drawdown
            peak_val = max(daily_portfolio) if daily_portfolio else today_portfolio
            current_val = daily_portfolio[-1] if daily_portfolio else today_portfolio
            dd_from_peak = ((current_val - peak_val) / peak_val * 100) if peak_val > 0 else 0

            # Max drawdown (worst peak-to-trough)
            max_dd = 0
            running_peak = 0
            for v in daily_portfolio:
                if v > running_peak:
                    running_peak = v
                dd = ((v - running_peak) / running_peak * 100) if running_peak > 0 else 0
                if dd < max_dd:
                    max_dd = dd

            st.caption(f"Stock + Cash · {len(sorted_dates)} trading days · Capital: {fmt(initial_capital)}")
        else:
            # Fallback: estimate from positions
            realized_pnl = 0
            for cl in D.get("closed", []):
                exit_price = f(cl.get("exit_price"))
                pm_c = next((p for p in D["pos"] if p["trade_id"] == cl["trade_id"]), None)
                if pm_c and exit_price:
                    ep_c = f(pm_c.get("entry_price"))
                    qty_c = f(pm_c.get("quantity"))
                    if ep_c and qty_c:
                        realized_pnl += (exit_price - ep_c) * qty_c
            current_val = total_value
            peak_val = sum(max(p["current_value"], p["cost_basis"]) for p in positions)
            dd_from_peak = ((current_val - peak_val) / peak_val * 100) if peak_val > 0 else 0
            max_dd = dd_from_peak
            initial_capital = sum(p["cost_basis"] for p in positions)
            today_cash = 0
            active_stock = current_val
            st.caption("Estimated from position cost basis vs current")

        dd_color = RED if dd_from_peak < -5 else AMBER if dd_from_peak < 0 else GREEN

        if peak_val > 0:
            m1, m2, m3 = st.columns(3)
            m1.metric("Portfolio Value", fmt(current_val))
            m2.metric("Stock", fmt(active_stock))
            m3.metric("Cash", fmt(today_cash))

            st.markdown(
                f'<div style="text-align:center;padding:16px;margin:8px 0;border-radius:10px;'
                f'background:{dd_color}10;border:1px solid {dd_color}30">'
                f'<div style="font-size:11px;text-transform:uppercase;color:{MUTED};letter-spacing:0.04em">Drawdown from Peak</div>'
                f'<div style="font-size:28px;font-weight:800;color:{dd_color}">{dd_from_peak:+.1f}%</div>'
                f'<div style="font-size:11px;color:{MUTED}">Max drawdown: {max_dd:+.1f}% · Peak: {fmt(peak_val)} · Realized P&L: {fmt(realized_pnl)}</div>'
                f'</div>', unsafe_allow_html=True)

            st.markdown("")

            # Sleeve-level drawdowns
            st.markdown("**Sleeve Drawdowns**")
            for sleeve_name in sorted(set(p["sleeve"] for p in positions)):
                sl_pos = [p for p in positions if p["sleeve"] == sleeve_name]
                sl_val = sum(p["current_value"] for p in sl_pos)
                sl_peak = sum(max(p["current_value"], p["cost_basis"]) for p in sl_pos)
                sl_dd = ((sl_val - sl_peak) / sl_peak * 100) if sl_peak else 0
                sl_dd_col = RED if sl_dd < -5 else AMBER if sl_dd < 0 else GREEN
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                    f'<span style="min-width:110px;font-size:13px">{sleeve_name}</span>'
                    f'<span style="color:{sl_dd_col};font-weight:700;font-size:14px">{sl_dd:+.1f}%</span>'
                    f'<span style="color:{MUTED};font-size:12px">({fmt(sl_val)} / {fmt(sl_peak)})</span>'
                    f'</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# TAB 5 — THESIS MONITOR
# ═══════════════════════════════════════════════════════════
with tab_thesis:
    st.markdown("#### Investment Thesis Monitor")
    st.caption("Separate business thesis from price movement. Anchor positions deserve deepest monitoring.")

    # Build thesis table
    thesis_rows = []
    for p in sorted(positions, key=lambda x: x["weight"], reverse=True):
        ticker = p["ticker"]

        # Check news categories
        ticker_news = [n for n in D["news"] if n.get("ticker") == ticker]
        has_threat = any(n.get("severity_tag") == "thesis-threatening" for n in ticker_news)
        has_material = any(n.get("severity_tag") == "material change" for n in ticker_news)

        # Fundamentals snapshot
        snap = next((s for s in D["fundsnap"] if s.get("ticker") == ticker), None)

        # Technical: based on price vs stop
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            tech = "🔴 STOP"
        elif p["cmp"] < p["entry"]:
            tech = "🟡 Below entry"
        else:
            tech = "🟢 Positive"

        # Overall
        if has_threat:
            overall = "🔴 Review"
        elif has_material or (p["cmp"] <= p["stop"] and p["stop"] > 0):
            overall = "🟡 Review"
        else:
            overall = "🟢 Intact"

        urg = review_urgency(p["review_date"])

        thesis_rows.append({
            "Ticker": ticker,
            "Sleeve": p["sleeve"],
            "Fundamentals": "📊 Available" if snap else "⏳ Pending",
            "News": f"🔴 Threat" if has_threat else (f"🟡 Material" if has_material else "🟢 Clear"),
            "Technical": tech,
            "Overall": overall,
            "Next Review": f"{fmt_date(p['review_date'])} {urg}" if p["review_date"] else "Set date",
            "Thesis Note": p["thesis_note"][:50] + "…" if len(p["thesis_note"]) > 50 else p["thesis_note"],
        })

    st.dataframe(pd.DataFrame(thesis_rows), use_container_width=True, hide_index=True)

    st.divider()

    # Per-ticker thesis detail
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        st.markdown("##### Fundamental Alerts")
        st.caption("Automate from filings/results")
        st.markdown("Revenue / EBITDA miss → **Monitor**")
        st.markdown("Cash-flow deterioration → **Monitor**")
        st.markdown("Debt / dilution → **Monitor**")
    with tc2:
        st.markdown("##### Governance Alerts")
        st.caption("High-impact exceptions")
        st.markdown("Promoter selling / pledge → **Monitor**")
        st.markdown("RPT / related-party changes → **Monitor**")
        st.markdown("Credit rating → **Monitor**")
    with tc3:
        st.markdown("##### Upcoming Events")
        st.caption("Next 30 days")
        st.markdown("Results → **Connect calendar**")
        st.markdown("Concall / presentation → **Connect filings**")
        st.markdown("Corporate actions → **Connect filings**")

    # News feed for thesis
    st.divider()
    st.markdown("#### Material News Feed")
    mat_news = [n for n in D["news"] if n.get("severity_tag") != "routine update"]
    if mat_news:
        for n in mat_news[:15]:
            sev = n.get("severity_tag", "routine update")
            ticker = n.get("ticker", "")
            headline = n.get("headline", "")
            reasoning = n.get("severity_reasoning") or ""
            snippet = n.get("body_snippet") or ""
            url = n.get("url") or ""
            source = n.get("source") or ""
            border = "#ef4444" if sev == "thesis-threatening" else "#f59e0b"
            bg = "rgba(239,68,68,.04)" if sev == "thesis-threatening" else "rgba(245,158,11,.03)"

            # Build context line: prefer body_snippet, fall back to severity_reasoning
            context = snippet or reasoning
            context_html = ""
            if context:
                context_html = (
                    f'<div style="font-size:12px;color:#555;margin-top:3px;line-height:1.4">'
                    f'{context}</div>'
                )

            # Source badge + link
            source_label = "NSE Filing" if source == "nse" else "Google News" if source == "google_news" else source
            date_str = short_date(n.get("published_at"))
            meta_parts = [f'{date_str}' if date_str else '', f'{sev}']
            if url and url.startswith("http"):
                meta_parts.append(f'<a href="{url}" target="_blank" style="color:#2563eb;text-decoration:none">{source_label} ↗</a>')
            else:
                meta_parts.append(source_label)
            meta_html = ' · '.join(p for p in meta_parts if p)

            st.markdown(
                f'<div style="padding:10px 14px;margin:4px 0;background:{bg};border-left:3px solid {border};border-radius:4px">'
                f'<div style="font-size:13px;font-weight:500">{sev_icon(sev)} <strong>{ticker}</strong> — {headline}</div>'
                f'{context_html}'
                f'<div style="font-size:11px;color:#888;margin-top:4px">{meta_html}</div>'
                f'</div>', unsafe_allow_html=True)
    else:
        st.success("No material news alerts.")


# ═══════════════════════════════════════════════════════════
# TAB 6 — SYSTEM & DATA
# ═══════════════════════════════════════════════════════════
with tab_system:
    st.markdown("#### Data Feed Status")

    ds1, ds2, ds3 = st.columns(3)
    with ds1:
        if has_live:
            st.success(f"**Prices** — ✅ Connected ({len(price_map)} tickers)")
        else:
            st.warning("**Prices** — ⚠️ No fresh prices")
    with ds2:
        fund_count = len(D["fundsnap"])
        if fund_count > 0:
            st.success(f"**Fundamentals** — ✅ {fund_count} snapshots")
        else:
            st.warning("**Fundamentals** — ⏳ Pending feed")
    with ds3:
        news_count = len(D["news"])
        if news_count > 0:
            latest_news = D["news"][0].get("published_at", "")
            st.success(f"**News** — ✅ {news_count} items (latest: {short_date(latest_news)})")
        else:
            st.warning("**News** — ⏳ Pending feed")

    st.divider()
    st.markdown("#### Automation Health")
    st.caption("The dashboard should never look healthy when the underlying data is stale.")

    auto_rows = [
        {"Feed / Job": "Market prices", "Status": "✅ Healthy" if has_live else "⚠️ Pending",
         "Last Update": short_date(D["stocks"][0].get("price_updated_at")) if D["stocks"] and D["stocks"][0].get("price_updated_at") else "—",
         "Cadence": "Every 30 min (market hours)", "Failure Action": "Alert"},
        {"Feed / Job": "Portfolio transactions", "Status": "✅ Healthy",
         "Last Update": "Live from Supabase", "Cadence": "On change", "Failure Action": "Alert"},
        {"Feed / Job": "Announcements scan", "Status": "✅ Active" if news_count > 0 else "⚠️ Pending",
         "Last Update": short_date(D["news"][0].get("fetched_at")) if D["news"] else "—",
         "Cadence": "Hourly", "Failure Action": "Alert + retry"},
        {"Feed / Job": "Fundamentals sync", "Status": "✅ Active" if fund_count > 0 else "⚠️ Pending",
         "Last Update": short_date(D["fundsnap"][0].get("pulled_at")) if D["fundsnap"] else "—",
         "Cadence": "Quarterly / event", "Failure Action": "Flag stale"},
    ]
    st.dataframe(pd.DataFrame(auto_rows), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Core Data Objects")
    do1, do2, do3 = st.columns(3)
    do1.markdown("**1. Security Master** — `stocks` table\n\nTicker, exchange, sector, identifiers")
    do1.markdown("**2. Position Ledger** — `position_monitoring` + `lots`\n\nTransactions, quantity, cost, stops")
    do2.markdown("**3. Thesis Record** — `research_log` + `news_raw`\n\nDrivers, risks, valuation, invalidation")
    do2.markdown("**4. Trade History** — `trade_history`\n\nClosed trades with full entry/exit details")
    do3.markdown("**5. Fundamentals** — `fundamentals_snapshots`\n\nPE, ROE, ROCE, margins, growth")
    do3.markdown("**6. Alert Log** — `alert_log`\n\nSent alerts for cooldown deduplication")

    st.divider()

    # ── P&F Breadth Management ─────────────────────────────
    st.markdown("#### 📊 P&F X-Percent Breadth")
    st.caption("Dual breadth signals: **1% box** (structural trend) + **0.25% box** (short-term shifts) from Rzone P&F charts")

    bread_tab1, bread_tab2, bread_tab3 = st.tabs(["📝 Add Reading", "📋 History", "📤 Import CSV"])

    with bread_tab1:
        with st.form("add_breadth"):
            bc0, bc1, bc2, bc3 = st.columns([1.2, 1, 1, 1])
            b_source = bc0.selectbox("Box Value", ["rzone_pnf_1pct", "rzone_pnf_025pct"],
                                      format_func=lambda x: "1% box" if "1pct" in x and "025" not in x else "0.25% box")
            b_date = bc1.date_input("Reading Date", value=datetime.now())
            b_val = bc2.number_input("Breadth %", min_value=0.0, max_value=100.0,
                                      value=25.0, step=0.01, format="%.2f")
            b_avg = bc3.number_input("Avg Breadth %", min_value=0.0, max_value=100.0,
                                      value=20.0, step=0.01, format="%.2f")
            b_notes = st.text_input("Notes (optional)", placeholder="e.g. Weekly evaluation")
            b_submit = st.form_submit_button("💾 Save Reading")

        if b_submit:
            try:
                _sb().table("breadth_readings").upsert({
                    "reading_date": str(b_date),
                    "source": b_source,
                    "breadth_pct": b_val,
                    "avg_breadth_pct": b_avg,
                    "notes": b_notes or None,
                }, on_conflict="reading_date,source").execute()
                src_label = "1%" if "1pct" in b_source and "025" not in b_source else "0.25%"
                st.success(f"✅ Saved {src_label} breadth {b_val}% for {b_date}")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Failed to save: {e}")

    with bread_tab2:
        try:
            hist_source = st.radio("Source filter", ["Both", "1% box", "0.25% box"],
                                    horizontal=True, key="breadth_hist_filter")
            query = _sb().table("breadth_readings").select("*")
            if hist_source == "1% box":
                query = query.eq("source", "rzone_pnf_1pct")
            elif hist_source == "0.25% box":
                query = query.eq("source", "rzone_pnf_025pct")
            hist_rows = query.order("reading_date", desc=True).limit(500).execute().data or []
            if hist_rows:
                bdf = pd.DataFrame(hist_rows)[["reading_date", "breadth_pct", "avg_breadth_pct", "source", "notes"]]
                bdf["source"] = bdf["source"].map({"rzone_pnf_1pct": "1%", "rzone_pnf_025pct": "0.25%",
                                                     "rzone_pnf": "legacy"}).fillna(bdf["source"])
                bdf.columns = ["Date", "Breadth %", "Avg %", "Box", "Notes"]
                st.dataframe(bdf, use_container_width=True, hide_index=True)

                # Breadth chart — overlay both sources when showing both
                bdf_all = pd.DataFrame(hist_rows).sort_values("reading_date")
                from plotly.subplots import make_subplots
                fig_b = make_subplots(specs=[[{"secondary_y": True}]])

                if hist_source == "Both":
                    for src, color, label in [("rzone_pnf_1pct", "#355ec9", "1% box"),
                                               ("rzone_pnf_025pct", "#10b981", "0.25% box")]:
                        src_df = bdf_all[bdf_all["source"] == src]
                        if not src_df.empty:
                            fig_b.add_trace(go.Scatter(
                                x=src_df["reading_date"],
                                y=src_df["breadth_pct"].astype(float),
                                mode="lines", name=label,
                                line=dict(color=color, width=2),
                            ), secondary_y=False)
                else:
                    fig_b.add_trace(go.Scatter(
                        x=bdf_all["reading_date"],
                        y=bdf_all["breadth_pct"].astype(float),
                        mode="lines+markers", name="P&F Breadth",
                        line=dict(color="#355ec9", width=2), marker=dict(size=4),
                    ), secondary_y=False)
                    if bdf_all["avg_breadth_pct"].notna().any():
                        fig_b.add_trace(go.Scatter(
                            x=bdf_all["reading_date"],
                            y=bdf_all["avg_breadth_pct"].astype(float),
                            mode="lines", name="Average",
                            line=dict(color="#eb6834", width=1, dash="dash"),
                        ), secondary_y=False)

                # Overlay Nifty 50 price on secondary y-axis
                if nifty and nifty.get("chart") is not None:
                    nifty_chart = nifty["chart"]
                    # Filter to match breadth date range
                    min_date = bdf_all["reading_date"].min()
                    nifty_in_range = nifty_chart[nifty_chart["Date"] >= min_date]
                    if not nifty_in_range.empty:
                        fig_b.add_trace(go.Scatter(
                            x=nifty_in_range["Date"],
                            y=nifty_in_range["Close"],
                            mode="lines", name="Nifty 50",
                            line=dict(color="#a855f7", width=1.5, dash="dot"),
                            opacity=0.6,
                        ), secondary_y=True)

                # Tier reference lines
                for thr, lbl in [(40, "T1"), (35, "T2"), (30, "T3"), (25, "T4"), (20, "T5")]:
                    fig_b.add_hline(y=thr, line_dash="dot", line_color="#ccc", line_width=1,
                                    annotation_text=lbl, annotation_position="right",
                                    annotation_font_size=9, annotation_font_color="#aaa")

                fig_b.update_layout(
                    height=380, margin=dict(l=0, r=0, t=10, b=10),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    # Range slider + zoom buttons
                    xaxis=dict(
                        showgrid=False,
                        rangeslider=dict(visible=True, thickness=0.06),
                        rangeselector=dict(
                            buttons=[
                                dict(count=7, label="1W", step="day", stepmode="backward"),
                                dict(count=1, label="1M", step="month", stepmode="backward"),
                                dict(count=3, label="3M", step="month", stepmode="backward"),
                                dict(step="all", label="All"),
                            ],
                            bgcolor="#f3f4f6", activecolor="#355ec9",
                            font=dict(size=11),
                        ),
                    ),
                )
                fig_b.update_yaxes(title_text="Breadth %", showgrid=True, gridcolor="#eef0f3",
                                    secondary_y=False)
                fig_b.update_yaxes(title_text="Nifty 50", showgrid=False,
                                    secondary_y=True)
                st.plotly_chart(fig_b, use_container_width=True)
            else:
                st.info("No breadth readings yet.")
        except Exception as e:
            st.warning(f"Could not load breadth history: {e}")

    with bread_tab3:
        st.markdown("Upload a CSV exported from **Rzone → Breadth → EXPORT**.")
        st.caption("Expected columns: `Date`, `Breadth Value` (or `X Percent Breadth`), and optionally `Average Value`.")
        csv_source = st.selectbox("Import as source", ["rzone_pnf_1pct", "rzone_pnf_025pct"],
                                   format_func=lambda x: "1% box" if "1pct" in x and "025" not in x else "0.25% box",
                                   key="csv_import_source")
        sma_period = st.number_input("SMA period for Avg %", min_value=2, max_value=50, value=5,
                                      help="Rolling average window (trading days). Applied when CSV has no Average column.")
        csv_file = st.file_uploader("Choose CSV file", type=["csv"], key="breadth_csv")
        if csv_file:
            try:
                raw = pd.read_csv(csv_file)
                st.dataframe(raw.head(), use_container_width=True)

                # Auto-detect columns
                date_col = next((c for c in raw.columns if "date" in c.lower()), None)
                val_col = next((c for c in raw.columns if "breadth" in c.lower() or "value" in c.lower() or "x percent" in c.lower()), None)
                avg_col = next((c for c in raw.columns if "average" in c.lower() or "avg" in c.lower()), None)

                if date_col and val_col:
                    src_label = "1%" if "1pct" in csv_source and "025" not in csv_source else "0.25%"

                    # Clean percentage strings: "62.00%" → 62.0
                    def _clean_pct(v):
                        if pd.isna(v):
                            return None
                        s = str(v).strip().rstrip("%").strip()
                        try:
                            return float(s)
                        except ValueError:
                            return None

                    raw["_clean_val"] = raw[val_col].apply(_clean_pct)
                    # Parse dates with dayfirst=True (DD/MM/YYYY from Rzone)
                    raw["_clean_date"] = pd.to_datetime(raw[date_col], dayfirst=True, errors="coerce")
                    raw = raw.dropna(subset=["_clean_date", "_clean_val"])
                    raw = raw.sort_values("_clean_date")

                    # Compute SMA if CSV has no avg column
                    if avg_col:
                        raw["_clean_avg"] = raw[avg_col].apply(_clean_pct)
                    else:
                        raw["_clean_avg"] = raw["_clean_val"].rolling(window=sma_period, min_periods=1).mean().round(2)

                    valid_count = len(raw)
                    st.success(f"Detected: Date=`{date_col}`, Breadth=`{val_col}`" +
                               (f", Avg=`{avg_col}`" if avg_col else f" (Avg = {sma_period}-day SMA, computed)") +
                               f" → importing **{valid_count} rows** as **{src_label} box**")

                    # Preview cleaned data
                    preview = raw[["_clean_date", "_clean_val", "_clean_avg"]].tail(10).copy()
                    preview.columns = ["Date", "Breadth %", f"Avg % ({sma_period}d SMA)"]
                    st.dataframe(preview, use_container_width=True, hide_index=True)

                    if st.button(f"📥 Import {valid_count} rows as {src_label}", type="primary"):
                        imported = 0
                        for _, row in raw.iterrows():
                            try:
                                rd = row["_clean_date"].strftime("%Y-%m-%d")
                                bv = row["_clean_val"]
                                av = row["_clean_avg"]
                                _sb().table("breadth_readings").upsert({
                                    "reading_date": rd, "source": csv_source,
                                    "breadth_pct": bv, "avg_breadth_pct": av,
                                    "notes": f"Rzone CSV import ({src_label})",
                                }, on_conflict="reading_date,source").execute()
                                imported += 1
                            except Exception:
                                continue
                        st.success(f"✅ Imported {imported}/{valid_count} {src_label} readings")
                        st.cache_data.clear()
                else:
                    st.warning("Could not auto-detect columns. Ensure CSV has a `Date` column and a breadth value column (containing 'breadth', 'value', or 'x percent').")
            except Exception as e:
                st.error(f"CSV parse error: {e}")

    st.divider()

    # Refresh + sidebar info
    col_r1, col_r2 = st.columns([1, 3])
    with col_r1:
        if st.button("🔄 Refresh all data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_r2:
        st.caption("Data refreshes automatically every 60 seconds. Prices sync every 30 min during market hours. "
                    "Announcements scan hourly.")


# ── Footer ──────────────────────────────────────────────────
st.divider()
st.caption("India Master Portfolio V2 · Live from Supabase · Dashboard auto-refreshes every 60s")
