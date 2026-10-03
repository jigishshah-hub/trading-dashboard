"""
India Master Portfolio — V2 Analytics & Decision Dashboard
Merged dashboard: V2 decision architecture + interactive analytics.
Live from Supabase Postgres.
"""

import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta, date
import math
import yfinance as yf

import backtest_engine as bte
import config as cfg

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
        "research_notes": sb.table("research_notes").select("*").execute().data or [],
        "thesis_killers": sb.table("thesis_killers").select("*").eq("is_active", True).execute().data or [],
        "monitoring_checklist": sb.table("monitoring_checklist").select("*").eq("is_active", True).execute().data or [],
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

# ── Fear Gauges: MOVE Index + India VIX + Junk Bond Spread ──
@st.cache_data(ttl=900)  # 15-min cache
def fetch_fear_gauges():
    """Fetch MOVE Index, India VIX, and compute confirmation signals."""
    gauges = {}

    # 1. MOVE Index (^MOVE) — US Treasury implied volatility
    try:
        move_tk = yf.Ticker("^MOVE")
        move_hist = move_tk.history(period="6mo")
        if not move_hist.empty:
            move_close = float(move_hist["Close"].iloc[-1])
            move_prev = float(move_hist["Close"].iloc[-2]) if len(move_hist) > 1 else move_close
            move_chg = ((move_close - move_prev) / move_prev) * 100
            # 30-day rolling average
            move_30d = float(move_hist["Close"].tail(22).mean())
            # Sparkline data (last 60 trading days)
            move_spark = move_hist["Close"].tail(60).tolist()
            gauges["move"] = {
                "value": round(move_close, 1),
                "prev": round(move_prev, 1),
                "chg": round(move_chg, 2),
                "avg_30d": round(move_30d, 1),
                "spark": [round(v, 1) for v in move_spark],
                "status": cfg.classify(move_close, cfg.MOVE_BANDS),
            }
    except Exception:
        pass

    # 2. India VIX (^INDIAVIX) — local fear gauge
    try:
        vix_tk = yf.Ticker("^INDIAVIX")
        vix_hist = vix_tk.history(period="6mo")
        if not vix_hist.empty:
            vix_close = float(vix_hist["Close"].iloc[-1])
            vix_prev = float(vix_hist["Close"].iloc[-2]) if len(vix_hist) > 1 else vix_close
            vix_chg = ((vix_close - vix_prev) / vix_prev) * 100
            vix_30d = float(vix_hist["Close"].tail(22).mean())
            vix_spark = vix_hist["Close"].tail(60).tolist()
            gauges["india_vix"] = {
                "value": round(vix_close, 2),
                "prev": round(vix_prev, 2),
                "chg": round(vix_chg, 2),
                "avg_30d": round(vix_30d, 2),
                "spark": [round(v, 2) for v in vix_spark],
                "status": cfg.classify(vix_close, cfg.VIX_BANDS),
            }
    except Exception:
        pass

    # 3. Junk Bond Spread proxy: HYG/LQD ratio as credit stress indicator
    # (FRED BAMLH0A0HYM2 not available via yfinance — use ETF ratio as proxy)
    try:
        hyg = yf.Ticker("HYG")
        lqd = yf.Ticker("LQD")
        hyg_h = hyg.history(period="6mo")
        lqd_h = lqd.history(period="6mo")
        if not hyg_h.empty and not lqd_h.empty:
            # HYG/LQD ratio: falling = credit stress (junk underperforming IG)
            # We invert to make higher = more stress (like spread)
            ratio = lqd_h["Close"] / hyg_h["Close"]
            ratio = ratio.dropna()
            if len(ratio) > 1:
                ratio_val = float(ratio.iloc[-1])
                ratio_prev = float(ratio.iloc[-2])
                ratio_chg = ((ratio_val - ratio_prev) / ratio_prev) * 100
                ratio_30d = float(ratio.tail(22).mean())
                # Percentile rank within 6mo for relative stress reading
                ratio_pctile = float((ratio <= ratio_val).sum() / len(ratio) * 100)
                ratio_spark = ratio.tail(60).tolist()
                gauges["credit_stress"] = {
                    "value": round(ratio_val, 3),
                    "prev": round(ratio_prev, 3),
                    "chg": round(ratio_chg, 2),
                    "avg_30d": round(ratio_30d, 3),
                    "pctile": round(ratio_pctile, 0),
                    "spark": [round(v, 3) for v in ratio_spark],
                    "status": cfg.classify(ratio_pctile, cfg.CREDIT_STRESS_BANDS),
                }
    except Exception:
        pass

    # Compute confirmation count
    signals_on = 0
    if gauges.get("move", {}).get("status") in cfg.MOVE_CONFIRMING_STATUSES:
        signals_on += 1
    if gauges.get("india_vix", {}).get("status") in cfg.VIX_CONFIRMING_STATUSES:
        signals_on += 1
    if gauges.get("credit_stress", {}).get("status") == "elevated":
        signals_on += 1

    gauges["signals_on"] = signals_on
    gauges["confirmation"] = (
        "TRIPLE" if signals_on == 3 else
        "DOUBLE" if signals_on == 2 else
        "SINGLE" if signals_on == 1 else
        "NONE"
    )

    return gauges

fear_gauges = fetch_fear_gauges()

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

def compute_relative_strength(stock_df, bench_df, ma_period=20):
    """Compute RS ratio = stock / benchmark, normalised to start at 100."""
    merged = pd.merge(stock_df[["Date", "Close"]], bench_df, on="Date",
                       suffixes=("_stock", "_bench"), how="inner")
    if merged.empty:
        return None
    merged["RS_Raw"] = merged["Close_stock"] / merged["Close_bench"]
    merged["RS"] = merged["RS_Raw"] / merged["RS_Raw"].iloc[0] * 100
    merged["RS_MA"] = merged["RS"].rolling(ma_period).mean()
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
LADDER_TIERS = cfg.LADDER_TIERS

CAPITAL_ARCH = cfg.CAPITAL_ARCH

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
        # Open positions are marked to the last bhavcopy (EOD) close, not an
        # intraday feed. A 24h wall-clock window wrongly drops that close every
        # weekend and market holiday (e.g. Gandhi Jayanti), silently zeroing P&L.
        # Accept the stored close whenever it is recent enough to be the latest
        # trading session's — PRICE_EOD_STALE_DAYS covers weekend + holiday
        # clusters; beyond it the feed is treated as stale (dead sync).
        PRICE_EOD_STALE_DAYS = cfg.PRICE_EOD_STALE_DAYS
        is_fresh = True
        if pu:
            try:
                updated = datetime.fromisoformat(str(pu).replace("Z", "+00:00"))
                is_fresh = (datetime.now(timezone.utc) - updated) <= timedelta(days=PRICE_EOD_STALE_DAYS)
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

def _breadth_age(date_str):
    """Label a manually-entered breadth reading with its date and how stale it is.

    Breadth is entered by hand, so a date alone can't be told apart from a
    broken feed. Count completed NSE sessions (Mon-Fri, holidays not modelled)
    since the reading and say so explicitly.

    Always evaluated in IST: the host clock is UTC, so a naive datetime.now()
    would still read "before the close" for hours after the 15:30 IST close.
    """
    if not date_str:
        return "date unknown"
    try:
        d = datetime.fromisoformat(str(date_str)).date()
    except Exception:
        return str(date_str)

    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    today = now_ist.date()
    # Today only counts as a completed session after the 15:30 IST close.
    today_done = _is_weekday(today) and now_ist.hour >= 16
    probe, sessions = today, 0
    while probe > d:
        if _is_weekday(probe) and (probe != today or today_done):
            sessions += 1
        probe -= timedelta(days=1)

    if sessions <= 0:
        return f"{d:%d %b} · current"
    return f"{d:%d %b} · {sessions} session{'s' if sessions > 1 else ''} old"

def _is_weekday(d):
    return d.weekday() < 5

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

    /* ── Framework tab ─────────────────────────────────── */
    .fw-h {font-size: 15px; font-weight: 700; letter-spacing: .01em; color: #111827;
           margin: 26px 0 2px; display: flex; align-items: center; gap: 8px;}
    .fw-h:first-child {margin-top: 4px;}
    .fw-h .rule {flex: 1; height: 1px; background: #e5e7eb;}
    .fw-sub {font-size: 12.5px; color: #6b7280; margin: 0 0 10px;}

    .fw-card {background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px;}
    .fw-card .k {font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #667085;}
    .fw-card .v {font-size: 22px; font-weight: 800; line-height: 1.25; margin-top: 2px;}
    .fw-card .d {font-size: 11.5px; color: #6b7280; margin-top: 3px;}

    /* Cycle diagram */
    .cyc {border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;}
    .cyc-band {padding: 12px 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;}
    .cyc-harvest {background: linear-gradient(90deg,#f0fdf4,#ffffff);}
    .cyc-deploy  {background: linear-gradient(90deg,#fef2f2,#ffffff);}
    .cyc-zone {font-size: 12px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase;}
    .cyc-note {font-size: 12px; color: #6b7280;}
    .cyc-tiers {display: flex; gap: 5px; margin-left: auto; flex-wrap: wrap;}
    .cyc-t {font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 5px;
            background: #fff; border: 1px solid #e5e7eb; color: #6b7280;}
    .cyc-t.on {border-color: transparent; color: #fff;}
    .cyc-ema {display: flex; align-items: center; gap: 10px; padding: 7px 16px;
              background: #f8f9fb; border-top: 1px solid #e5e7eb; border-bottom: 1px solid #e5e7eb;}
    .cyc-ema .lbl {font-size: 11px; font-weight: 800; letter-spacing: .08em; color: #374151;
                   white-space: nowrap;}
    .cyc-ema .line {flex: 1; height: 0; border-top: 2px dashed #9ca3af;}
    .cyc-ema .now {font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
                   white-space: nowrap;}

    /* Framework tables */
    .fw-scroll {overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 2px;}
    .fw-tbl {width: 100%; min-width: 520px; border-collapse: separate; border-spacing: 0; font-size: 12.5px;
             border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;}
    .fw-tbl th {background: #f8f9fb; color: #667085; font-size: 10.5px; font-weight: 700;
                text-transform: uppercase; letter-spacing: .04em; text-align: left;
                padding: 8px 12px; border-bottom: 1px solid #e5e7eb; white-space: nowrap;}
    .fw-tbl td {padding: 9px 12px; border-bottom: 1px solid #f1f3f7; color: #1f2937;
                vertical-align: middle;}
    .fw-tbl tr:last-child td {border-bottom: none;}
    .fw-tbl td.num, .fw-tbl th.num {text-align: right; font-variant-numeric: tabular-nums;}
    .fw-tbl td.c, .fw-tbl th.c {text-align: center;}
    .fw-tbl tr.on td {background: #fffbeb; font-weight: 600;}
    .fw-tbl tr.done td {background: #fafafa; color: #9ca3af;}
    .fw-tbl .tier {font-weight: 800; letter-spacing: .02em;}
    .fw-tbl .why {font-size: 11.5px; color: #6b7280;}
    .fw-here {display: inline-block; font-size: 9.5px; font-weight: 800; letter-spacing: .05em;
              background: #f59e0b; color: #fff; padding: 1px 6px; border-radius: 4px;
              margin-left: 6px; vertical-align: 1px;}
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
# ── Thesis-killer assessment engine (shared by Risk + Thesis Monitor) ──
# Indexes research tables and classifies each killer against material news.
# Index the research repository
notes_by_ticker = {r["ticker"]: r for r in D["research_notes"]}
killers_by_ticker = {}
for k in D["thesis_killers"]:
    killers_by_ticker.setdefault(k["ticker"], []).append(k)
monitors_by_ticker = {}
for m in D["monitoring_checklist"]:
    monitors_by_ticker.setdefault(m["ticker"], []).append(m)

# Generic adverse-signal lexicon: words that indicate a thesis-negative event.
# A killer flags only when its subject AND an adverse signal both appear (or the
# news is already classified thesis-threatening). This stops positive news that
# merely shares a subject word (e.g. "RERA registration", "demerger approved")
# from firing a killer whose actual condition is the opposite.
# Tiered adverse lexicons. STRONG terms are unambiguous thesis negatives -> red
# trigger. SOFT terms are context-dependent (below/miss/decline) -> amber review,
# never a confident red on their own.
STRONG_ADVERSE = frozenset({
    "withdrawn","withdraw","withdrawal","suspend","suspended","suspension",
    "abandon","abandoned","cancel","cancelled","cancellation","terminate",
    "terminated","default","defaulted","fraud","fraudulent","probe","raid",
    "raids","chargesheet","fir","arrest","arrested","freeze","frozen","freezing",
    "resign","resigned","resignation","qualification","disqualified","downgrade",
    "downgraded","insolvency","winding","litigation","lawsuit","delisting",
    "impairment","scam","penalty","penalised","penalized","investigation","npa",
})
SOFT_ADVERSE = frozenset({
    "delay","delayed","below","miss","missed","decline","declined","fell","fall",
    "drop","dropped","weaker","negative","loss","losses","stress","dispute",
    "dilution","shortfall","breach","violation","lapse","recall","halt","halted",
    "slump","slowdown","warning","fine","cut","pressure","concern","provision",
    "pledge","pledged","pledging",
})
# Generic label tokens that don't reliably identify a subject in news text.
# Killers built only from these (revenue/margin/etc.) simply won't news-match,
# which is honest: those are fundamentals, confirmed from filings not headlines.
STOPTOKENS = frozenset({
    "revenue","margin","ebitda","roce","capex","debt","order","guidance",
    "status","report","days","concentration","result","results","update",
    "growth","target","ratio","level","plan","plans","weak",
})

def _material_news(ticker):
    return [n for n in D["news"]
            if n.get("ticker") == ticker and n.get("severity_tag") != "routine update"]

# Favorable-signal lexicon: subject-matched news carrying one of these (and no
# adverse word) is thesis-SUPPORTIVE, not a caution. Stops a demerger-APPROVAL
# from showing amber under a demerger-ABANDON killer.
POSITIVE = frozenset({
    "approve", "approved", "approves", "approval", "complete", "completed",
    "completion", "grant", "granted", "receipt", "received", "registration",
    "registered", "secured", "secures", "award", "awarded", "wins", "won",
    "commission", "commissioned", "launch", "launched", "upgrade", "upgraded",
    "sanction", "sanctioned", "allotted", "bagged", "cleared", "clearance",
    "resolved", "progress", "progresses", "on-track", "ontrack", "record",
    "strong", "beats", "robust", "surge", "surges", "jump", "jumps", "rise",
})

def _tokens(text):
    """Word-level tokens (lowercased). Whole-word matching downstream prevents
    substring bleed like 'ed' inside 'reduced' or 'rera' inside 'overall'."""
    out, cur = set(), []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.add("".join(cur)); cur = []
    if cur:
        out.add("".join(cur))
    return out

def _subject_present(subj, toks):
    """Match a subject token as a whole word, allowing light inflection
    (pledge -> pledged/pledging: term + up to 3 trailing chars). No substring bleed."""
    for s in subj:
        if s in toks:
            return True
        for t in toks:
            if t.startswith(s) and 0 < len(t) - len(s) <= 3:
                return True
    return False

def _lex_hit(lexicon, toks):
    """Whole-word lexicon match with de-stemming, so verb inflections
    (resigns -> resign, approves -> approve, concerns -> concern) are caught."""
    for t in toks:
        if t in lexicon:
            return True
        for suf in ("s", "es", "ed", "ing", "d"):
            if t.endswith(suf) and len(t) - len(suf) >= 3 and t[:-len(suf)] in lexicon:
                return True
    return False

def _classify(killer, mnews):
    """Precision-first / never-assert. A thesis killer is a DOWNSIDE watch, so it
    only ever attaches a news card when the news is genuinely adverse. Positive or
    neutral mentions of the same subject do NOT attach to the killer (they stay in
    the Material News Feed) -- this stops a demerger APPROVAL from showing under a
    demerger-ABANDON killer.
    trigger = subject + a STRONG adverse term, or thesis-threatening severity.
    related = subject + a SOFT adverse term (ambiguous downside -> review).
    clear   = no adverse news on the subject (incl. positive / neutral mentions).
    Subject tokens are >=4 chars and exclude generic metric words (STOPTOKENS)."""
    subj = [t for t in (killer.get("label") or "").lower().split("_")
            if len(t) >= 4 and t not in STOPTOKENS]
    if not subj:
        return "clear", None
    review_hit = None
    for n in mnews:
        toks = _tokens(f"{n.get('headline','')} {n.get('body_snippet','')} {n.get('severity_reasoning','')}")
        if not _subject_present(subj, toks):
            continue
        if n.get("severity_tag") == "thesis-threatening" or _lex_hit(STRONG_ADVERSE, toks):
            return "trigger", n
        if _lex_hit(SOFT_ADVERSE, toks) and review_hit is None:
            review_hit = n
        # positive or neutral subject overlap -> not attached to the killer (clear)
    if review_hit:
        return "related", review_hit
    return "clear", None

def _assess(ticker):
    kl = killers_by_ticker.get(ticker, [])
    mnews = _material_news(ticker)
    trig, rel, sup, clr = [], [], [], []
    for k in kl:
        state, matched = _classify(k, mnews)
        if state == "trigger":
            trig.append((k, matched))
        elif state == "related":
            rel.append((k, matched))
        elif state == "supportive":
            sup.append((k, matched))
        else:
            clr.append(k)
    return kl, trig, rel, sup, clr

def _news_line(n, color="#888"):
    d = short_date(n.get("published_at")) or ""
    sev = n.get("severity_tag", "")
    hl = n.get("headline", "")
    snip = (n.get("body_snippet") or n.get("severity_reasoning") or "")[:150]
    url = n.get("url") or ""
    link = (f' <a href="{url}" target="_blank" style="color:#2563eb;text-decoration:none">open \u2197</a>'
            if url.startswith("http") else "")
    meta = " \u00b7 ".join(x for x in [d, sev] if x)
    snip_html = f'<br><span style="color:#999">{snip}</span>' if snip else ""
    return (f'<div style="font-size:11.5px;color:{color};margin-top:5px;line-height:1.45">'
            f'\u21B3 <b>{hl}</b>{link}'
            f'<br><span style="color:#999">{meta}</span>{snip_html}</div>')


tab_cockpit, tab_positions, tab_risk, tab_perf, tab_thesis, tab_system, tab_framework, tab_backtest = st.tabs([
    "🎯 Cockpit", "📊 Positions", "⚡ Risk", "📈 Performance", "🔬 Thesis Monitor", "⚙️ System & Data", "🔄 Full Cycle Framework", "🧪 Backtest"
])

# ═══════════════════════════════════════════════════════════
# TAB 1 — COCKPIT
# ═══════════════════════════════════════════════════════════
with tab_cockpit:

    # ── DECISION BOARD — alert banners ──
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

    # ════════════════════════════════════════════════════════
    # ① MARKET REGIME — "What's the environment?"
    # ════════════════════════════════════════════════════════
    st.markdown("### ① Market Regime")
    st.caption("What's the environment?")

    if nifty:
        pct = nifty["pct_from_ema"]
        bpct = breadth["pct_above"] if breadth else None
        bpct_025 = breadth_025pct["pct_above"] if breadth_025pct else None

        # Determine system state using DUAL-CONDITION logic
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
        elif tier_met(-8, 40):
            sys_state, sys_icon, sys_color, sys_bg = "DEPLOY T1", "🟡", "#a76a00", "#fff6dd"
            deploy_perm = "DEPLOY TIER 1 — 8% tactical deployed"
        elif pct >= 20 and bpct is not None and bpct >= 80:
            sys_state, sys_icon, sys_color, sys_bg = "OVEREXTENDED", "⚡", "#7c3aed", "#f3f0ff"
            deploy_perm = "PROFIT HARVEST → peel tactical back to cash"
        elif pct >= 15:
            sys_state, sys_icon, sys_color, sys_bg = "EXTENDED", "📈", "#2563eb", "#eff6ff"
            deploy_perm = "MONITOR — approaching harvest zone"
        elif pct <= -5 and (bpct is None or bpct > 40):
            sys_state, sys_icon, sys_color, sys_bg = "CAUTION", "⚠️", "#a76a00", "#fff6dd"
            deploy_perm = "EMA DIPPED — BREADTH NOT CONFIRMED, HOLD"
        else:
            sys_state, sys_icon, sys_color, sys_bg = "NORMAL", "🟢", "#216c30", "#eaf7ed"
            deploy_perm = "HOLD / WAIT FOR RULE TRIGGER"

        # Fear gauge confirmation overlay
        fg_signals = fear_gauges.get("signals_on", 0) if fear_gauges else 0
        fg_conf_label = ""
        if sys_state.startswith("DEPLOY") and fg_signals > 0:
            if fg_signals >= 2:
                fg_conf_label = " · 🔴 2+ fear gauges — 2× sizing + early fire"
            elif fg_signals == 1:
                fg_conf_label = " · 🟡 1 fear gauge elevated — 1.5× sizing"

        # ── Market Regime: Left (Nifty strip + Fear gauges) | Right (Nifty chart) ──
        regime_left, regime_right = st.columns(2)

        with regime_left:
            # Nifty price strip
            t1_trigger = nifty["ema200"] * 0.92
            dist_to_t1 = abs(pct - (-8.0))
            ema_dist_bg = "#feecec" if pct < -10 else "#fff6dd" if pct < 0 else "#dcfce7" if pct < 10 else "#ccfbf1"
            ema_dist_color = "#991b1b" if pct < -10 else "#92400e" if pct < 0 else "#166534" if pct < 10 else "#115e59"
            st.markdown(
                f'<div style="background:var(--surface, #fff);border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px">'
                f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
                f'<span style="font-size:26px;font-weight:800">{nifty["price"]:,.0f}</span>'
                f'<span style="font-size:14px;font-weight:600;color:{"#16a34a" if nifty["daily_chg"] >= 0 else "#dc2626"}">'
                f'{"↑" if nifty["daily_chg"] >= 0 else "↓"} {nifty["daily_chg"]:+.2f}%</span>'
                f'<span style="font-size:12px;padding:3px 8px;border-radius:6px;background:{ema_dist_bg};color:{ema_dist_color};font-weight:600">'
                f'{pct:+.2f}% vs 200 EMA</span>'
                f'</div>'
                f'<div style="display:flex;gap:16px;font-size:11px;color:#6b7280;margin-top:6px;flex-wrap:wrap">'
                f'<span>200 EMA: {nifty["ema200"]:,.0f}</span>'
                f'<span>T1 trigger: {t1_trigger:,.0f} (−8.0%)</span>'
                f'<span style="color:#d97706">{dist_to_t1:.2f}% from T1</span>'
                f'</div>'
                f'</div>', unsafe_allow_html=True)

            # Breadth metrics row
            def _breadth_stamp(col, date_str):
                """Age line under a breadth tile. Rendered as its own caption
                rather than st.metric(delta=...), which always draws an arrow."""
                txt = _breadth_age(date_str)
                stale = "session" in txt
                col.markdown(
                    f'<div style="font-size:11px;margin-top:-8px;'
                    f'color:{"#b45309" if stale else "#6b7280"};'
                    f'font-weight:{"600" if stale else "400"}">{txt}</div>',
                    unsafe_allow_html=True)

            bm1, bm2 = st.columns(2)
            if breadth_1pct:
                bm1.metric("P&F 1% Breadth", f'{breadth_1pct["pct_above"]}%')
                _breadth_stamp(bm1, breadth_1pct["date"])
            elif breadth_yf:
                b1_color = "inverse" if breadth_yf["pct_above"] < 50 else "normal"
                bm1.metric("Breadth (yf)", f'{breadth_yf["pct_above"]}%',
                           delta=f'{breadth_yf["above_count"]}/{breadth_yf["total"]} >200 DMA',
                           delta_color=b1_color)
            else:
                bm1.metric("P&F 1%", "—", delta="unavailable")
            if breadth_025pct:
                bm2.metric("P&F 0.25%", f'{breadth_025pct["pct_above"]}%')
                _breadth_stamp(bm2, breadth_025pct["date"])
            else:
                bm2.metric("P&F 0.25%", "—", delta="unavailable")

            # Fear Gauges inline
            st.markdown("")
            st.markdown("**Fear Gauges** <span style='font-size:11px;color:#888'>(triple confirmation for contrarian deployment)</span>",
                        unsafe_allow_html=True)

            if fear_gauges:
                fg_signals = fear_gauges.get("signals_on", 0)
                fg_conf = fear_gauges.get("confirmation", "NONE")
                conf_colors = {
                    "TRIPLE": ("#a92e2e", "#feecec", "🔴🔴🔴 Maximum conviction — deploy aggressively"),
                    "DOUBLE": ("#c05621", "#fff0e6", "🟠🟠 Elevated — enhanced tactical sizing"),
                    "SINGLE": ("#a76a00", "#fff6dd", "🟡 One gauge elevated — standard ladder"),
                    "NONE":   ("#216c30", "#eaf7ed", "🟢 All calm — standard ladder rules"),
                }
                cc, cbg, clabel = conf_colors.get(fg_conf, conf_colors["NONE"])
                st.markdown(
                    f'<div style="background:{cbg};border:1px solid {cc}30;border-radius:8px;padding:8px 12px;margin:6px 0">'
                    f'<div style="font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:0.04em">Confirmation signals: {fg_signals}/3</div>'
                    f'<div style="font-size:12px;color:{cc};font-weight:600;margin-top:2px">{clabel}</div>'
                    f'</div>', unsafe_allow_html=True)

                gauge_defs = [
                    ("MOVE Index", "move", "US Treasury vol", {
                        "extreme": ("🔴", "#a92e2e", "≥100"), "elevated": ("🟠", "#c05621", "≥80"),
                        "moderate": ("🟡", "#a76a00", "70-80"), "calm": ("🟢", "#216c30", "<70")
                    }),
                    ("India VIX", "india_vix", "Nifty options vol", {
                        "panic": ("🔴", "#a92e2e", "≥30"), "stress": ("🔴", "#c0341d", "25-30"),
                        "fear": ("🟠", "#c05621", "20-25"), "elevated": ("🟡", "#a76a00", "16-20"),
                        "normal": ("🟢", "#2f7d4f", "13-16"), "calm": ("🟢", "#216c30", "<13")
                    }),
                    ("Credit Stress", "credit_stress", "LQD/HYG ratio", {
                        "elevated": ("🟠", "#c05621", "≥80th pctile"),
                        "watch": ("🟡", "#a76a00", "60-80th"), "calm": ("🟢", "#216c30", "<60th")
                    }),
                ]
                for g_name, g_key, g_desc, g_levels in gauge_defs:
                    g = fear_gauges.get(g_key)
                    if g:
                        g_status = g.get("status", "calm")
                        g_icon, g_color, g_thr = g_levels.get(g_status, ("⚪", "#888", "—"))
                        g_val = g["value"]
                        g_chg = g.get("chg", 0)
                        chg_arrow = "▲" if g_chg > 0 else "▼" if g_chg < 0 else "–"
                        chg_color = "#a92e2e" if g_chg > 2 else "#c05621" if g_chg > 0 else "#216c30" if g_chg < 0 else "#888"
                        spark = g.get("spark", [])
                        spark_str = ""
                        if spark and len(spark) > 4:
                            mn, mx = min(spark), max(spark)
                            rng = mx - mn if mx > mn else 1
                            blocks = "▁▂▃▄▅▆▇█"
                            spark_str = "".join(blocks[min(7, int((v - mn) / rng * 7.99))] for v in spark[-20:])
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:6px;padding:5px 10px;margin:2px 0;'
                            f'border-radius:6px;background:#f8f9fb;border-left:3px solid {g_color}">'
                            f'<span style="font-size:12px">{g_icon}</span>'
                            f'<span style="font-size:12px;font-weight:700;min-width:85px">{g_name}</span>'
                            f'<span style="font-size:14px;font-weight:700;min-width:50px">{g_val}</span>'
                            f'<span style="font-size:10px;color:{chg_color};min-width:45px">{chg_arrow} {abs(g_chg):.1f}%</span>'
                            f'<span style="font-size:10px;color:#aaa;letter-spacing:-0.5px">{spark_str}</span>'
                            f'<span style="flex:1;font-size:10px;color:{MUTED};text-align:right">{g_thr}</span>'
                            f'</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:6px;padding:5px 10px;margin:2px 0;'
                            f'border-radius:6px;background:#f8f9fb;border-left:3px solid #ccc">'
                            f'<span style="font-size:12px">⚪</span>'
                            f'<span style="font-size:12px;font-weight:700;min-width:85px">{g_name}</span>'
                            f'<span style="font-size:12px;color:{MUTED}">unavailable</span>'
                            f'<span style="flex:1;font-size:10px;color:{MUTED};text-align:right">{g_desc}</span>'
                            f'</div>', unsafe_allow_html=True)

        with regime_right:
            # Nifty vs 200 EMA chart — Full Cycle View with harvest + deploy zones
            if nifty.get("chart") is not None:
                st.markdown("**Nifty 50 vs 200 EMA** <span style='font-size:11px;color:#888'>Full Cycle View — 120 trading days</span>",
                            unsafe_allow_html=True)
                cdf = nifty["chart"]
                fig_nifty = go.Figure()

                # Harvest zone shading (above EMA)
                ema_val = nifty["ema200"]
                h1_price = ema_val * 1.05
                h4_price = ema_val * 1.20

                # Add harvest zone rectangle (H1 to H4)
                fig_nifty.add_hrect(
                    y0=h1_price, y1=h4_price,
                    fillcolor="rgba(13,148,136,0.06)", line_width=0,
                    annotation_text="HARVEST ZONE", annotation_position="top left",
                    annotation_font_size=9, annotation_font_color="#0d9488",
                )

                # Add deploy zone rectangle (T1 to T3)
                t1_price = ema_val * 0.95
                t3_price = ema_val * 0.85
                fig_nifty.add_hrect(
                    y0=t3_price, y1=t1_price,
                    fillcolor="rgba(220,38,38,0.04)", line_width=0,
                    annotation_text="DEPLOY ZONE", annotation_position="bottom left",
                    annotation_font_size=9, annotation_font_color="#dc2626",
                )

                # Nifty price line
                fig_nifty.add_trace(go.Scatter(
                    x=cdf["Date"], y=cdf["Close"], mode='lines',
                    name='Nifty 50', line=dict(color="#355ec9", width=2),
                    hovertemplate='%{x}<br>Nifty: %{y:,.0f}<extra></extra>',
                ))
                # 200 EMA line
                fig_nifty.add_trace(go.Scatter(
                    x=cdf["Date"], y=cdf["EMA200"], mode='lines',
                    name='200 EMA', line=dict(color="#eb6834", width=2, dash='dash'),
                    hovertemplate='%{x}<br>200 EMA: %{y:,.0f}<extra></extra>',
                ))

                # Harvest tier lines (H1-H4)
                HARVEST_LINES = [(p, l, "#0d9488") for p, l in cfg.HARVEST_LINES]
                for h_pct, h_label, h_color in HARVEST_LINES:
                    h_price = ema_val * (1 + h_pct / 100)
                    fig_nifty.add_hline(y=h_price, line_dash="dot", line_color=h_color,
                                        line_width=1, opacity=0.35,
                                        annotation_text=f"{h_label} (+{h_pct}%)",
                                        annotation_position="right",
                                        annotation_font_size=8, annotation_font_color="#0d9488")

                # Deploy tier lines (T1-T3)
                for tier in LADDER_TIERS[:3]:
                    trigger = ema_val * (1 + tier["threshold"] / 100)
                    fig_nifty.add_hline(y=trigger, line_dash="dot", line_color="#c83b3b",
                                        line_width=1, opacity=0.35,
                                        annotation_text=f"T{tier['tier']} ({tier['threshold']}%)",
                                        annotation_position="left",
                                        annotation_font_size=8, annotation_font_color="#999")

                fig_nifty.update_layout(
                    height=360, margin=dict(l=0, r=60, t=10, b=10),
                    xaxis=dict(showgrid=False, title=None),
                    yaxis=dict(showgrid=True, gridcolor="#eef0f3", title=None),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    showlegend=True,
                )
                st.plotly_chart(fig_nifty, use_container_width=True)
            else:
                st.info("Nifty data unavailable — install yfinance for live chart.")
    else:
        st.warning("⚠️ Could not fetch Nifty 50 data — check yfinance connection.")
        st.caption("The Tactical Ladder requires live Nifty 50 price and 200 EMA.")

    st.divider()

    # ════════════════════════════════════════════════════════
    # ② TACTICAL CYCLE — "Should I deploy or harvest?"
    # ════════════════════════════════════════════════════════
    st.markdown("### ② Tactical Cycle")
    st.caption("Should I deploy or harvest?")

    if nifty:
        tc_left, tc_right = st.columns([1, 2])

        with tc_left:
            # Cycle position badge
            st.markdown(
                f'<div style="background:{sys_bg};border:1px solid {sys_color}30;border-left:4px solid {sys_color};'
                f'border-radius:10px;padding:14px 16px;margin-bottom:10px">'
                f'<div style="font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:0.04em">Cycle Position</div>'
                f'<div style="font-size:22px;font-weight:800;color:{sys_color};margin:4px 0">{sys_icon} {sys_state}</div>'
                f'<div style="font-size:12px;color:#444">Deployment permission: <strong>{deploy_perm}</strong>{fg_conf_label}</div>'
                f'</div>', unsafe_allow_html=True)

            # Fear modifier summary
            fg_s = fear_gauges.get("signals_on", 0) if fear_gauges else 0
            fg_mult = "2×" if fg_s >= 2 else "1.5×" if fg_s == 1 else "1×"
            st.markdown(
                f'<div style="font-size:12px;color:#444;line-height:1.7;margin-top:8px">'
                f'<strong>Fear modifier:</strong> {fg_s}/3 → {fg_mult} allocation<br>'
                f'<strong>If 1/3:</strong> 1.5× (T1 = 12% not 8%)<br>'
                f'<strong>If 2+/3:</strong> 2× + fires 1% early'
                f'</div>', unsafe_allow_html=True)

            # Capital architecture
            st.markdown("")
            st.markdown("**Capital Architecture**")
            arch_colors = ["#355ec9", "#1baf7a", "#eda100"]
            for i, ca in enumerate(CAPITAL_ARCH):
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                    f'<div style="width:10px;height:10px;border-radius:3px;background:{arch_colors[i]}"></div>'
                    f'<span style="font-size:13px;min-width:140px"><strong>{ca["name"]}</strong></span>'
                    f'<span style="font-size:13px;font-weight:700;min-width:35px">{ca["pct"]}%</span>'
                    f'<span style="font-size:11px;color:{MUTED}">{ca["desc"]}</span>'
                    f'</div>', unsafe_allow_html=True)

        with tc_right:
            # Full Cycle Ladder — Harvest above EMA + Deploy below EMA
            st.markdown("**Full Cycle Ladder** <span style='font-size:11px;color:#888'>Deploy below EMA · Harvest above EMA</span>",
                        unsafe_allow_html=True)

            # ── Harvest tiers (H4 → H1, above EMA) ──
            st.markdown(
                '<div style="background:#ccfbf120;border:1px solid #0d948830;border-radius:6px;padding:4px 10px;margin:6px 0;'
                'font-size:11px;font-weight:700;color:#115e59">▲ HARVEST — Profit Booking (above 200 EMA)</div>',
                unsafe_allow_html=True)

            HARVEST_TIERS = cfg.HARVEST_TIERS

            # Determine active harvest tier
            active_harvest = None
            if pct > 0:
                for ht in HARVEST_TIERS:
                    if pct >= ht["pct"]:
                        active_harvest = ht["id"]
                        break
                if active_harvest is None and pct >= 5:
                    active_harvest = "H1"

            for ht in HARVEST_TIERS:
                h_price = nifty["ema200"] * (1 + ht["pct"] / 100)
                is_active_h = (active_harvest == ht["id"])
                if is_active_h:
                    h_bg = "#ccfbf140"
                    h_border = "#0d9488"
                    h_opacity = "1"
                    h_weight = "700"
                else:
                    h_bg = "#f8f9fb"
                    h_border = "#e5e7eb"
                    h_opacity = "0.45"
                    h_weight = "400"

                h_action_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                                 f'background:#ccfbf1;color:#115e59">{ht["action"]}</span>')
                h_note_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                               f'background:#f3f4f6;color:#6b7280">{ht["note"]}</span>')

                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:6px;padding:5px 10px;margin:2px 0;'
                    f'border-radius:6px;background:{h_bg};border-left:3px solid {h_border};opacity:{h_opacity};font-weight:{h_weight}">'
                    f'<span style="min-width:26px;font-weight:700;font-size:13px;color:#0d9488">{ht["id"]}</span>'
                    f'<span style="font-size:12px;color:#444;min-width:50px">+{ht["pct"]}.0%</span>'
                    f'{h_action_pill} {h_note_pill}'
                    f'<span style="flex:1;font-size:11px;color:#0d9488;text-align:right">→ Tactical → Cash</span>'
                    f'<span style="font-size:11px;color:{MUTED};min-width:70px;text-align:right">₹{h_price:,.0f}</span>'
                    f'</div>', unsafe_allow_html=True)

            # ── EMA divider ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;margin:10px 0;padding:0 8px">'
                f'<div style="flex:1;height:2px;background:#ea580c"></div>'
                f'<span style="font-size:11px;font-weight:700;color:#ea580c">200 EMA = {nifty["ema200"]:,.0f}</span>'
                f'<div style="flex:1;height:2px;background:#ea580c"></div>'
                f'</div>', unsafe_allow_html=True)

            # ── Deploy tiers (T1 → T5, below EMA) ──
            st.markdown(
                '<div style="background:#fef2f220;border:1px solid #dc262630;border-radius:6px;padding:4px 10px;margin:6px 0;'
                'font-size:11px;font-weight:700;color:#991b1b">▼ DEPLOY — Capital Deployment (below 200 EMA)</div>',
                unsafe_allow_html=True)

            # Find the approaching/active deploy tier
            active_deploy_tier = None
            if pct < 0:
                for tier in LADDER_TIERS:
                    thr = tier["threshold"]
                    bmax = tier["breadth_max"]
                    ema_hit = pct <= thr
                    breadth_hit = bpct is not None and bpct <= bmax
                    both_met = ema_hit and breadth_hit
                    is_current = both_met and (tier["tier"] == 1 or not (
                        pct <= LADDER_TIERS[tier["tier"] - 2]["threshold"] and
                        (bpct is not None and bpct <= LADDER_TIERS[tier["tier"] - 2]["breadth_max"])
                    ))
                    if is_current:
                        active_deploy_tier = tier["tier"]
                        break

                # If no tier is fully met, find the approaching tier
                if active_deploy_tier is None:
                    for tier in LADDER_TIERS:
                        if pct > tier["threshold"]:
                            active_deploy_tier = tier["tier"]
                            break

            for tier in LADDER_TIERS:
                thr = tier["threshold"]
                bmax = tier["breadth_max"]
                ema_hit = pct <= thr
                breadth_hit = bpct is not None and bpct <= bmax
                trigger_price = nifty["ema200"] * (1 + thr / 100)

                # Active tier highlighting
                is_active_t = (active_deploy_tier == tier["tier"])
                if is_active_t:
                    t_bg = "#fff6dd"
                    t_border = "#d97706"
                    t_opacity = "1"
                    t_weight = "700"
                else:
                    t_bg = "#f8f9fb"
                    t_border = "#e5e7eb"
                    t_opacity = "0.45"
                    t_weight = "400"

                # Condition pills
                ema_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                            f'background:{"#dcfce7" if ema_hit else "#fee2e2"};'
                            f'color:{"#166534" if ema_hit else "#991b1b"}">EMA {"✓" if ema_hit else "✗"}</span>')
                breadth_pill_color = "#dcfce7" if breadth_hit else "#fee2e2" if bpct is not None else "#f3f4f6"
                breadth_pill_text = "#166534" if breadth_hit else "#991b1b" if bpct is not None else "#888"
                breadth_status = "✓" if breadth_hit else "✗" if bpct is not None else "?"
                breadth_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                                f'background:{breadth_pill_color};color:{breadth_pill_text}">'
                                f'1%≤{bmax} {breadth_status}</span>')

                # Fear modifier pill
                fg_s_val = fear_gauges.get("signals_on", 0) if fear_gauges else 0
                fear_label = f"Fear {fg_s_val}/3 → {'2×' if fg_s_val >= 2 else '1.5×' if fg_s_val == 1 else '1×'}"
                fear_pill = (f'<span style="font-size:10px;padding:1px 5px;border-radius:3px;'
                             f'background:#f3f4f6;color:#6b7280">{fear_label}</span>')

                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:6px;padding:5px 10px;margin:2px 0;'
                    f'border-radius:6px;background:{t_bg};border-left:3px solid {t_border};opacity:{t_opacity};font-weight:{t_weight}">'
                    f'<span style="min-width:26px;font-weight:700;font-size:13px;color:{"#d97706" if is_active_t else "#dc2626"}">T{tier["tier"]}</span>'
                    f'<span style="font-size:12px;color:#444;min-width:50px">{thr}%</span>'
                    f'{ema_pill} {breadth_pill} {fear_pill}'
                    f'<span style="flex:1;font-size:11px;color:#666;text-align:right">→ {tier["deploy_pct"]}% Midcap 150</span>'
                    f'<span style="font-size:11px;color:{MUTED};min-width:70px;text-align:right">₹{trigger_price:,.0f}</span>'
                    f'</div>', unsafe_allow_html=True)

    st.divider()

    # ════════════════════════════════════════════════════════
    # ③ PORTFOLIO — "How am I positioned?"
    # ════════════════════════════════════════════════════════
    st.markdown("### ③ Portfolio")
    st.caption("How am I positioned?")

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

    # Portfolio Equity Curve
    st.markdown("#### 📈 Portfolio Equity Curve")

    snapshots = D.get("snapshots", [])
    all_lots = sorted(D["lots"], key=lambda l: l.get("lot_date") or "9999")

    if snapshots:
        from collections import defaultdict
        _exit_map = {}
        for cl in D.get("closed", []):
            tid = cl.get("trade_id")
            exd = cl.get("exit_date")
            if tid and exd:
                _exit_map[tid] = exd

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

        cum = 0
        min_cum = 0
        for _, amt in _cash_ev:
            cum += amt
            min_cum = min(min_cum, cum)
        _init_cap = -min_cum if min_cum < 0 else 0

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

        curve_dates = list(all_dates)
        curve_values = []
        for d in curve_dates:
            stock = _daily_stock[d]
            cash = _init_cap + sum(amt for ed, amt in _cash_ev if ed <= d)
            curve_values.append(stock + cash)

        curve_capital = [_init_cap] * len(curve_dates)

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

    # Sector Allocation + Action Queue side by side
    port_left, port_right = st.columns([1, 1.3])

    with port_left:
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
            height=250, margin=dict(l=0, r=40, t=10, b=10),
            xaxis=dict(showgrid=True, gridcolor="#eef0f3", title=None, showticklabels=False),
            yaxis=dict(showgrid=False, title=None),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_sector, use_container_width=True)

    with port_right:
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

    # ── Position Sizing Calculator v2 ───────────────────────────────────────
    with st.expander("🧮 Position Sizing Calculator", expanded=False):
        st.caption(
            "Pick a tracked stock or type any ticker. Exchange toggle supports NSE & BSE. "
            "Portfolio size pulled from live data."
        )

        # ── Row 1: quick-pick + free text + exchange + stop mode ─────────
        _ps_r1a, _ps_r1b, _ps_r1c, _ps_r1d = st.columns([2, 2, 1, 1])

        # Quick-pick from tracked stocks (searchable selectbox)
        _ps_tracked = sorted(set(s["ticker"] for s in D["stocks"] if s.get("ticker")))
        _ps_quickpick = _ps_r1a.selectbox(
            "Quick-pick (tracked stocks)", ["— type below —"] + _ps_tracked,
            key="ps_quickpick"
        )
        # Free-text overrides quick-pick when filled
        _ps_freetext = _ps_r1b.text_input(
            "Or type any ticker", value="", key="ps_freetext",
            placeholder="e.g. DIXON, TITAN"
        ).strip().upper()
        _ps_exchange = _ps_r1c.radio(
            "Exchange", ["NSE", "BSE"], horizontal=True, key="ps_exchange"
        )
        _ps_stop_mode = _ps_r1d.radio(
            "Stop method", ["ATR-based", "Manual"], horizontal=True, key="ps_stop_mode"
        )

        # Resolve ticker: free-text wins over quick-pick
        if _ps_freetext:
            _ps_ticker = _ps_freetext
        elif _ps_quickpick != "— type below —":
            _ps_ticker = _ps_quickpick
        else:
            _ps_ticker = ""

        _ps_yf_suffix = ".NS" if _ps_exchange == "NSE" else ".BO"

        # ── Fetch data when ticker resolved ──────────────────────────────
        # fetch_stock_history always appends .NS internally — pass raw ticker for NSE.
        # For BSE, bypass it entirely and use yf.download with .BO suffix.
        _ps_atr = _ps_cmp_live = _ps_52w_high = _ps_52w_low = None
        if _ps_ticker:
            # Strip any exchange suffix the user may have typed
            _ps_base = (_ps_ticker.removesuffix(".NS").removesuffix(".BO")
                        if hasattr(str, "removesuffix")
                        else _ps_ticker.replace(".NS", "").replace(".BO", ""))
            try:
                if _ps_exchange == "NSE":
                    # fetch_stock_history appends .NS itself — pass base ticker
                    _ps_hist = fetch_stock_history(_ps_base, period="1y")
                else:
                    import yfinance as _yf
                    _ps_raw = _yf.download(
                        f"{_ps_base}.BO", period="1y", progress=False, auto_adjust=True
                    )
                    if not _ps_raw.empty:
                        _ps_raw = _ps_raw.reset_index()
                        _ps_raw.columns = [c[0] if isinstance(c, tuple) else c
                                           for c in _ps_raw.columns]
                        _ps_hist = _ps_raw
                    else:
                        _ps_hist = None

                if _ps_hist is not None and not _ps_hist.empty:
                    _ps_tech    = compute_technicals(_ps_hist)
                    _ps_cmp_live = float(_ps_tech["Close"].iloc[-1])
                    _ps_atr      = float(_ps_tech["ATR"].iloc[-1])
                    _ps_52w_high = float(_ps_tech["Close"].max())
                    _ps_52w_low  = float(_ps_tech["Close"].min())
                    # Force-update entry/target in session state when ticker changes
                    if st.session_state.get("_ps_last_ticker") != _ps_ticker:
                        st.session_state["ps_entry"]  = round(_ps_cmp_live, 2)
                        st.session_state["ps_target"] = round(_ps_cmp_live * 1.15, 2)
                        st.session_state["ps_stop_manual"] = round(_ps_cmp_live * 0.95, 2)
                        st.session_state["_ps_last_ticker"] = _ps_ticker
                    st.success(
                        f"**{_ps_base}** ({_ps_exchange}) — "
                        f"CMP ₹{_ps_cmp_live:,.2f} · ATR(14) ₹{_ps_atr:,.2f} · "
                        f"52w H ₹{_ps_52w_high:,.0f} / L ₹{_ps_52w_low:,.0f}"
                    )
                else:
                    st.warning(
                        f"No data for **{_ps_base}** on {_ps_exchange} — "
                        "check the ticker or try the other exchange."
                    )
            except Exception as _ps_err:
                st.warning(f"Could not fetch **{_ps_base}**: {_ps_err}")

        # ── Inputs (session state drives values after ticker fetch) ──────
        _ps_i1, _ps_i2, _ps_i3 = st.columns(3)
        _ps_entry = _ps_i1.number_input(
            "Entry price (₹)", min_value=0.1, step=0.5, key="ps_entry",
        )
        _ps_target = _ps_i2.number_input(
            "Target (₹)", min_value=0.1, step=0.5, key="ps_target",
        )

        if _ps_stop_mode == "ATR-based" and _ps_atr:
            _ps_atr_mult = _ps_i3.slider(
                "ATR multiple for stop", 1.0, 3.0, 2.0, 0.25, key="ps_atr_mult"
            )
            _ps_stop = _ps_entry - _ps_atr_mult * _ps_atr
            st.caption(
                f"Stop = ₹{_ps_entry:,.2f} − {_ps_atr_mult}× ATR(₹{_ps_atr:,.2f}) "
                f"= **₹{_ps_stop:,.2f}**"
            )
        else:
            _ps_stop = _ps_i3.number_input(
                "Stop-loss (₹)", min_value=0.1, step=0.5, key="ps_stop_manual",
            )

        # Portfolio size pre-filled from live portfolio value
        _ps_port_default = int(total_value) if total_value and total_value > 0 else 1_000_000
        if "_ps_port_init" not in st.session_state:
            st.session_state["ps_port"] = _ps_port_default
            st.session_state["_ps_port_init"] = True
        _ps_risk_pct = st.slider(
            "Max risk per trade (% of portfolio)", 0.5, 5.0, 1.0, 0.25, key="ps_risk_pct"
        )
        _ps_port = st.number_input(
            "Portfolio size (₹) — pre-filled from live data",
            min_value=10_000, step=10_000, key="ps_port",
        )

        # ── Output ───────────────────────────────────────────────────────
        _ps_risk_amt = _ps_port * _ps_risk_pct / 100
        _ps_risk_per = _ps_entry - _ps_stop
        _ps_reward   = _ps_target - _ps_entry

        st.divider()
        if _ps_risk_per > 0 and _ps_entry > 0 and _ps_target > _ps_entry:
            _ps_shares   = int(_ps_risk_amt / _ps_risk_per)
            _ps_r        = _ps_reward / _ps_risk_per
            _ps_stop_pct = _ps_risk_per / _ps_entry * 100
            _ps_tgt_pct  = _ps_reward / _ps_entry * 100

            # Row 1 — Trade setup
            st.markdown("**Trade Setup**")
            _po1, _po2, _po3, _po4 = st.columns(4)
            _po1.metric("Entry",  f"₹{_ps_entry:,.2f}")
            _po2.metric("Stop",   f"₹{_ps_stop:,.2f}",
                        delta=f"−{_ps_stop_pct:.1f}%", delta_color="inverse")
            _po3.metric("Target", f"₹{_ps_target:,.2f}",
                        delta=f"+{_ps_tgt_pct:.1f}%")
            _po4.metric("R:R",    f"{_ps_r:.2f}R",
                        delta="Good" if _ps_r >= 2 else "Below 2R min",
                        delta_color="normal" if _ps_r >= 2 else "inverse")

            # Row 2 — Qty input + computed sizing
            st.markdown("**Position Sizing**")
            _pq_col, _pm_col = st.columns([1, 3])
            _ps_qty = _pq_col.number_input(
                "Qty (shares)",
                min_value=1, value=_ps_shares, step=1, key="ps_qty",
                help="Pre-filled to max risk-based shares — adjust to your actual lot size"
            )
            # Recompute all outputs from actual qty
            _ps_capital = _ps_qty * _ps_entry
            _ps_actual_risk = _ps_qty * _ps_risk_per
            _ps_actual_reward = _ps_qty * _ps_reward
            _ps_weight  = _ps_capital / _ps_port * 100 if _ps_port else 0

            with _pm_col:
                _ps1, _ps2, _ps3, _ps4 = st.columns(4)
                _ps1.metric("Capital required", fmt(_ps_capital))
                _ps2.metric("Portfolio weight", f"{_ps_weight:.1f}%")
                _ps3.metric("Risk ₹",           fmt(_ps_actual_risk))
                _ps4.metric("Profit potential", fmt(_ps_actual_reward))

            if _ps_atr:
                st.caption(
                    f"Max risk-based qty: {_ps_shares:,} shares · "
                    f"Stop = {_ps_risk_per / _ps_atr:.2f}× ATR(₹{_ps_atr:,.2f})"
                )
            if _ps_weight > 25:
                st.warning(f"Position weight {_ps_weight:.1f}% exceeds 25% concentration limit.")
            if _ps_actual_risk > _ps_risk_amt * 1.1:
                st.warning(
                    f"Qty {_ps_qty:,} risks {fmt(_ps_actual_risk)} — "
                    f"above your {_ps_risk_pct}% limit of {fmt(_ps_risk_amt)}."
                )

        elif _ps_target <= _ps_entry:
            st.error("Target must be above entry price.")
        elif _ps_risk_per <= 0:
            st.error("Stop-loss must be below entry price.")

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

                # ── Chart period selector ──
                _period_options = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "All": 0}
                _period_sel = st.radio(
                    "Chart period", list(_period_options.keys()),
                    index=2, horizontal=True, key=f"period_{sel_ticker}",
                )
                _period_days = _period_options[_period_sel]
                if _period_days > 0 and len(tech_df) > _period_days:
                    chart_df = tech_df.iloc[-_period_days:].reset_index(drop=True)
                else:
                    chart_df = tech_df

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

                # Benchmark selector + RS MA period (visible when RS toggled on)
                rs_df = None
                _rs_ma_period = 20
                if show_rs:
                    _rs_col1, _rs_col2 = st.columns(2)
                    rs_bench = _rs_col1.selectbox(
                        "RS benchmark", list(_RS_BENCHMARKS.keys()),
                        index=0, key=f"rs_bench_{sel_ticker}",
                    )
                    _rs_ma_period = _rs_col2.selectbox(
                        "RS MA period", [10, 20, 50],
                        index=1, key=f"rs_ma_{sel_ticker}",
                    )
                    bench_sym = _RS_BENCHMARKS[rs_bench]
                    bench_data = fetch_benchmark_history(bench_sym)
                    if bench_data is not None:
                        rs_df = compute_relative_strength(tech_df, bench_data, ma_period=_rs_ma_period)
                        # Filter RS to chart period too
                        if _period_days > 0 and len(rs_df) > _period_days:
                            rs_df = rs_df.iloc[-_period_days:].reset_index(drop=True)
                            # Re-normalize RS to start of visible period
                            if len(rs_df) > 0:
                                rs_df["RS"] = rs_df["RS"] / rs_df["RS"].iloc[0] * 100

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
                    x=chart_df["Date"], open=chart_df["Open"],
                    high=chart_df["High"], low=chart_df["Low"],
                    close=chart_df["Close"], name="Price",
                    increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
                    text=[f"O: ₹{o:,.1f}<br>H: ₹{h:,.1f}<br>L: ₹{l:,.1f}<br>C: ₹{c:,.1f}<br>Vol: {v:,.0f}"
                          for o, h, l, c, v in zip(chart_df["Open"], chart_df["High"],
                                                    chart_df["Low"], chart_df["Close"],
                                                    chart_df["Volume"])],
                    hoverinfo="text+x",
                ), row=1, col=1)

                # EMAs
                if show_ema:
                    for span, clr in [(20, "#f59e0b"), (50, "#3b82f6"), (200, "#a855f7")]:
                        col_name = f"EMA{span}"
                        if col_name in chart_df.columns:
                            valid = chart_df.dropna(subset=[col_name])
                            fig_tech.add_trace(go.Scatter(
                                x=valid["Date"], y=valid[col_name],
                                mode="lines", name=col_name,
                                line=dict(color=clr, width=1),
                                hovertemplate=f"{col_name}: ₹%{{y:,.1f}}<extra></extra>",
                            ), row=1, col=1)

                # Bollinger Bands
                if show_bb:
                    valid_bb = chart_df.dropna(subset=["BB_Upper"])
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
                                mode="lines", name=f"RS MA({_rs_ma_period})",
                                line=dict(color="#0ea5e9", width=0.8, dash="dash"),
                                hovertemplate=f"RS MA({_rs_ma_period}): %{{y:.1f}}<extra></extra>",
                                showlegend=False,
                            ), row=cur_row, col=1)
                            fig_tech.add_hline(y=100, line_dash="dot", line_color="#94a3b8",
                                               line_width=0.5, row=cur_row, col=1)
                        fig_tech.update_yaxes(title_text="RS", row=cur_row, col=1)

                    elif sub_name == "RSI":
                        valid_rsi = chart_df.dropna(subset=["RSI"])
                        fig_tech.add_trace(go.Scatter(
                            x=valid_rsi["Date"], y=valid_rsi["RSI"],
                            mode="lines", name="RSI(14)",
                            line=dict(color="#8b5cf6", width=1.2),
                            hovertemplate="RSI: %{y:.1f}<extra></extra>",
                        ), row=cur_row, col=1)
                        valid_rsi_ma = chart_df.dropna(subset=["RSI_MA"])
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
                        valid_macd = chart_df.dropna(subset=["MACD"])
                        fig_tech.add_trace(go.Scatter(
                            x=valid_macd["Date"], y=valid_macd["MACD"],
                            mode="lines", name="MACD",
                            line=dict(color="#3b82f6", width=1),
                            hovertemplate="MACD: %{y:.2f}<extra></extra>",
                        ), row=cur_row, col=1)
                        fig_tech.add_trace(go.Scatter(
                            x=valid_macd["Date"], y=valid_macd["MACD_Signal"],
                            mode="lines", name="Signal",
                            line=dict(color="#f59e0b", width=1, dash="dash"),
                            hovertemplate="Signal: %{y:.2f}<extra></extra>",
                        ), row=cur_row, col=1)
                        colors_macd = ["#22c55e" if v >= 0 else "#ef4444"
                                       for v in valid_macd["MACD_Hist"]]
                        fig_tech.add_trace(go.Bar(
                            x=valid_macd["Date"], y=valid_macd["MACD_Hist"],
                            name="Histogram", marker_color=colors_macd,
                            hovertemplate="Hist: %{y:.2f}<extra></extra>",
                            showlegend=False,
                        ), row=cur_row, col=1)
                        fig_tech.update_yaxes(title_text="MACD", row=cur_row, col=1)

                    elif sub_name == "Volume":
                        vol_colors = ["#22c55e" if chart_df["Close"].iloc[i] >= chart_df["Open"].iloc[i]
                                      else "#ef4444" for i in range(len(chart_df))]
                        fig_tech.add_trace(go.Bar(
                            x=chart_df["Date"], y=chart_df["Volume"],
                            name="Volume", marker_color=vol_colors,
                            opacity=0.5, showlegend=False,
                            hovertemplate="Vol: %{y:,.0f}<extra></extra>",
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
                    "hovermode": "x unified",
                    "hoverlabel": dict(bgcolor="white", font_size=11, font_family="sans-serif"),
                    "dragmode": "zoom",
                    "xaxis": dict(
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
                fig_tech.update_xaxes(showgrid=False,
                    showspikes=True, spikemode="across", spikesnap="cursor",
                    spikethickness=0.5, spikecolor="#94a3b8", spikedash="dot")
                fig_tech.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.05)",
                    showspikes=True, spikethickness=0.5, spikecolor="#94a3b8",
                    spikedash="dot", spikemode="across")
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
    thesis_break_pos = [p for p in positions if _assess(p["ticker"])[1]]
    r3.metric("Thesis Breaks", len(thesis_break_pos),
              delta="positions with a triggered thesis killer",
              delta_color="inverse" if thesis_break_pos else "normal")

    st.divider()

    # Stop proximity visualization
    st.markdown("#### Stop Proximity")
    st.caption("How much buffer each position has before stop-loss")

    st.caption("Red ≤3% buffer · amber ≤7% · green above — matches the watchlist thresholds.")
    sorted_by_stop = sorted(positions, key=lambda p: p["stop_dist_pct"])
    for p in sorted_by_stop:
        dist_pct = p["stop_dist_pct"]  # (cmp - stop) / cmp * 100
        buf_pct = max(0, min(100, dist_pct))
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            bar_color = RED
            label = "BREACHED"
        elif dist_pct <= 3:
            bar_color = RED
            label = f"₹{p['stop_dist_abs']:.0f} ({dist_pct:.1f}%)"
        elif dist_pct <= 7:
            bar_color = AMBER
            label = f"₹{p['stop_dist_abs']:.0f} ({dist_pct:.1f}%)"
        else:
            bar_color = GREEN
            label = f"₹{p['stop_dist_abs']:.0f} ({dist_pct:.1f}%)"

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

    # ── Sector Allocation (weight, not risk) ───────────────────────────────
    st.markdown("#### Sector Allocation")
    st.caption("Portfolio weight % by sector — current open positions only")
    if positions:
        _sec_wt = {}
        for _p in positions:
            _sec_wt[_p["sector"]] = _sec_wt.get(_p["sector"], 0) + _p["weight"]
        _sec_sorted = sorted(_sec_wt.items(), key=lambda kv: kv[1], reverse=True)
        _sec_names = [s for s, _ in _sec_sorted]
        _sec_vals  = [round(w, 1) for _, w in _sec_sorted]
        fig_sec = go.Figure(go.Bar(
            x=_sec_vals, y=_sec_names, orientation="h",
            marker_color="#5d7bd0",
            text=[f"{v:.1f}%" for v in _sec_vals],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
        ))
        fig_sec.update_layout(
            height=max(180, 38 * len(_sec_names)),
            margin=dict(l=0, r=40, t=10, b=10),
            xaxis=dict(title="Weight %", showgrid=True, gridcolor="#eef0f3"),
            yaxis=dict(showgrid=False, autorange="reversed"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_sec, use_container_width=True)

    st.divider()
    st.markdown("#### Live Risk Checks")
    st.caption("Evaluated against current positions. Adjust the limits to match your risk policy.")

    lc1, lc2, lc3 = st.columns(3)
    pos_limit = lc1.number_input("Max position weight %", min_value=5, max_value=100, value=25, step=5, key="risk_pos_lim")
    sec_limit = lc2.number_input("Max sector weight %", min_value=10, max_value=100, value=40, step=5, key="risk_sec_lim")
    risk_budget = lc3.number_input("Portfolio risk budget %", min_value=1, max_value=25, value=5, step=1, key="risk_budget")

    # Sector weights
    sec_weight = {}
    for p in positions:
        sec_weight[p["sector"]] = sec_weight.get(p["sector"], 0) + p["weight"]

    pos_hot = sorted([p for p in positions if p["weight"] > pos_limit], key=lambda x: -x["weight"])
    sec_hot = sorted([(s, w) for s, w in sec_weight.items() if w > sec_limit], key=lambda x: -x[1])
    breached = [p for p in positions if p["cmp"] <= p["stop"] and p["stop"] > 0]
    risk_pct = (total_car / total_invested * 100) if total_invested else 0

    checks = [
        ("Position concentration", not pos_hot, f"any name > {pos_limit}%",
         ", ".join(f"{p['ticker']} {p['weight']:.0f}%" for p in pos_hot) if pos_hot else "all names within limit"),
        ("Sector concentration", not sec_hot, f"any sector > {sec_limit}%",
         ", ".join(f"{s} {w:.0f}%" for s, w in sec_hot) if sec_hot else "all sectors within limit"),
        ("Stop breach", not breached, "hard alert on any breach",
         ", ".join(p["ticker"] for p in breached) if breached else "no positions breached"),
        ("Portfolio risk budget", risk_pct <= risk_budget, f"≤ {risk_budget}% of invested at risk",
         f"{risk_pct:.1f}% of invested at risk to stops"),
        ("Thesis killers", not thesis_break_pos, "no triggered killers on holdings",
         ", ".join(p["ticker"] for p in thesis_break_pos) if thesis_break_pos else "no triggered killers"),
    ]

    for name, ok_flag, threshold, detail in checks:
        if ok_flag:
            border, bg, tag, tagcol = GREEN, "rgba(35,122,53,.05)", "✓ OK", GREEN
        else:
            border, bg, tag, tagcol = RED, "rgba(200,59,59,.06)", "⚠ ALERT", RED
        st.markdown(
            f'<div style="padding:9px 13px;margin:5px 0;background:{bg};border-left:3px solid {border};border-radius:5px;'
            f'display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
            f'<span style="font-weight:700;font-size:13px;min-width:170px">{name}</span>'
            f'<span style="font-size:10px;font-weight:700;color:{tagcol}">{tag}</span>'
            f'<span style="font-size:12px;color:#666">{detail}</span>'
            f'<span style="margin-left:auto;font-size:11px;color:#999">{threshold}</span>'
            f'</div>', unsafe_allow_html=True)

    st.caption("Planned (need data not yet wired): liquidity (ADV / position size) · correlation (30/60/120d) · portfolio & sleeve drawdown · stress test (Nifty / sector shock).")

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
    setup_by_tid = {p.get("trade_id"): (p.get("setup") or "") for p in D["pos"]}

    with pc1:
        st.markdown("#### P&L Attribution")
        _attr_view = st.radio(
            "P&L attribution view", ["Open (unrealized)", "Realized (closed)"],
            horizontal=True, label_visibility="collapsed", key="attr_view")

        # Open view: unrealized P&L per open position — naturally bounded by the
        # number of holdings, so it never skews as trade history grows.
        # Realized view: booked P&L per closed-trade ticker, capped to the top
        # winners + top losers with the rest aggregated into "Others", so a long
        # history stays readable.
        _TOPN = 7
        if _attr_view.startswith("Open"):
            st.caption("Unrealized P&L by open position — current book")
            attr_items = sorted(((p["ticker"], p["pnl"]) for p in positions),
                                key=lambda kv: kv[1])
            _empty_msg = "No open positions to attribute."
        else:
            st.caption(f"Realized P&L by closed trade — top {_TOPN} winners/losers; rest as “Others”")
            realized = {}
            for t in D["closed"]:
                tk = t.get("ticker", "")
                ep = f(t.get("entry_price")) or 0
                xp = f(t.get("exit_price")) or 0
                tlots = [l for l in D["lots"] if l.get("trade_id") == t.get("trade_id")]
                tqty = sum(f(l.get("qty")) or 0 for l in tlots)
                if tqty > 0:
                    pnl_v = (xp - ep) * tqty
                else:
                    rp = f(t.get("realized_return_pct"))
                    pnl_v = ep * (rp / 100) if rp is not None else 0.0
                realized[tk] = realized.get(tk, 0) + pnl_v
            ranked = sorted(realized.items(), key=lambda kv: kv[1])
            if len(ranked) > 2 * _TOPN:
                mid = ranked[_TOPN:-_TOPN]
                attr_items = ranked[:_TOPN] + [("Others", sum(v for _, v in mid))] + ranked[-_TOPN:]
                attr_items = sorted(attr_items, key=lambda kv: kv[1])
            else:
                attr_items = ranked
            _empty_msg = "No closed trades to attribute yet."

        if not attr_items or not any(v for _, v in attr_items):
            st.info(_empty_msg)
        else:
            fig_attr = go.Figure()
            fig_attr.add_trace(go.Bar(
                y=[k for k, _ in attr_items],
                x=[v for _, v in attr_items],
                orientation='h',
                marker_color=[(MUTED if k == "Others" else (GREEN if v >= 0 else RED))
                              for k, v in attr_items],
                text=[fmt(v) for _, v in attr_items],
                textposition='outside',
                cliponaxis=False,
                hovertemplate='%{y}: %{x:,.0f}<extra></extra>',
            ))
            _pv = [v for _, v in attr_items] or [0]
            _pad = max((abs(v) for v in _pv), default=0) * 0.30 or 1
            fig_attr.update_layout(
                height=max(200, 45 * len(attr_items)),
                margin=dict(l=10, r=20, t=10, b=10),
                xaxis=dict(showgrid=True, gridcolor="#eef0f3", title="P&L (₹)", zeroline=True,
                           zerolinecolor="#999", range=[min(_pv + [0]) - _pad, max(_pv + [0]) + _pad]),
                yaxis=dict(showgrid=False),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_attr, use_container_width=True)

    with pc2:
        st.markdown("#### Closed Trades")
        st.caption(f"{len(D['closed'])} exited trades — sortable; click a column header to rank")
        if D["closed"]:
            closed_rows = []
            for t in D["closed"]:
                ret = f(t.get("realized_return_pct"))
                hd = f(t.get("holding_period_days"))
                closed_rows.append({
                    "Result": "Win" if (ret or 0) >= 0 else "Loss",
                    "Ticker": t.get("ticker", ""), "Sleeve": t.get("sleeve", ""),
                    "Setup": setup_by_tid.get(t.get("trade_id"), ""),
                    "Entry": f(t.get("entry_price")), "Exit": f(t.get("exit_price")),
                    "Return %": round(ret, 1) if ret is not None else None,
                    "Exit Reason": t.get("exit_reason", ""),
                    "Hold (d)": int(hd) if hd is not None else None,
                    "Exit Date": t.get("exit_date"),
                })
            df_closed = pd.DataFrame(closed_rows).sort_values("Exit Date", ascending=False, na_position="last")
            st.dataframe(
                df_closed, use_container_width=True, hide_index=True, height=360,
                column_config={
                    "Return %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Entry": st.column_config.NumberColumn(format="₹%.0f"),
                    "Exit": st.column_config.NumberColumn(format="₹%.0f"),
                })
            st.download_button(
                "⬇ Export trade log CSV",
                data=df_closed.to_csv(index=False),
                file_name=f"trade_log_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key="dl_trade_log",
            )
        else:
            st.caption("No closed trades yet.")

    st.divider()

    # ── Closed-trade analysis (strategy edge) ──
    if D["closed"]:
        st.markdown("#### Closed-Trade Analysis")
        st.caption("Win rate and average return grouped by exit reason and by setup — where the edge actually is.")

        def _grp_stats(key_fn):
            g = {}
            for t in D["closed"]:
                k = key_fn(t) or "—"
                ret = f(t.get("realized_return_pct"))
                g.setdefault(k, []).append(ret if ret is not None else 0.0)
            out = []
            for k, rs in g.items():
                n = len(rs)
                wins_k = sum(1 for r in rs if r >= 0)
                out.append({
                    "Group": k, "Trades": n,
                    "Win %": round(wins_k / n * 100) if n else 0,
                    "Avg Return %": round(sum(rs) / n, 1) if n else 0.0,
                    "Best %": round(max(rs), 1) if rs else 0.0,
                    "Worst %": round(min(rs), 1) if rs else 0.0,
                })
            return pd.DataFrame(sorted(out, key=lambda x: -x["Avg Return %"]))

        _cfg = {
            "Win %": st.column_config.NumberColumn(format="%d%%"),
            "Avg Return %": st.column_config.NumberColumn(format="%.1f%%"),
            "Best %": st.column_config.NumberColumn(format="%.1f%%"),
            "Worst %": st.column_config.NumberColumn(format="%.1f%%"),
        }
        ca1, ca2 = st.columns(2)
        with ca1:
            st.markdown("**By Exit Reason**")
            st.dataframe(_grp_stats(lambda t: t.get("exit_reason")),
                         hide_index=True, use_container_width=True, column_config=_cfg)
        with ca2:
            st.markdown("**By Setup**")
            st.dataframe(_grp_stats(lambda t: setup_by_tid.get(t.get("trade_id"))),
                         hide_index=True, use_container_width=True, column_config=_cfg)
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
        cliponaxis=False,
        hovertemplate='%{x}: %{y:,.0f}<extra></extra>',
    ))
    fig_sleeve.update_layout(
        height=280, margin=dict(l=0, r=0, t=28, b=10),
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

            st.markdown("##### Equity Curve")
            _run, _rp = [], 0
            for _v in daily_portfolio:
                _rp = max(_rp, _v)
                _run.append(_rp)
            _line_col = GREEN if current_val >= peak_val else RED
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=sorted_dates, y=_run, mode="lines", name="Peak",
                line=dict(color="rgba(167,106,0,0.55)", width=1, dash="dot"),
                hovertemplate="peak ₹%{y:,.0f}<extra></extra>"))
            fig_eq.add_trace(go.Scatter(
                x=sorted_dates, y=daily_portfolio, mode="lines", name="Portfolio",
                line=dict(color=_line_col, width=2),
                hovertemplate="%{x}: ₹%{y:,.0f}<extra></extra>"))
            fig_eq.update_layout(
                height=240, margin=dict(l=0, r=0, t=6, b=0),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(tickprefix="₹", gridcolor="rgba(0,0,0,.06)"),
                xaxis=dict(showgrid=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                showlegend=True)
            st.plotly_chart(fig_eq, use_container_width=True, key="perf_equity_curve")
            st.caption(f"Stock + Cash · {len(sorted_dates)} trading days · Capital: {fmt(initial_capital)} · gap to the dotted peak line = drawdown")
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
    st.caption("Business thesis vs price. Research-backed thesis killers are cross-checked against material news \u2014 a killer only flags when its subject appears alongside an adverse signal.")


    # -- Thesis health + attention (holdings-focused; full catalog in Research Library) --
    all_researched = list(notes_by_ticker.keys())
    for t in killers_by_ticker:
        if t not in all_researched:
            all_researched.append(t)
    assess_cache = {t: _assess(t) for t in all_researched}
    tot_trig = sum(len(v[1]) for v in assess_cache.values())
    tot_rel = sum(len(v[2]) for v in assess_cache.values())

    hs1, hs2, hs3 = st.columns(3)
    hs1.metric("Research notes", len(notes_by_ticker))
    hs2.metric("Killers triggered", tot_trig, delta="review" if tot_trig else None, delta_color="inverse")
    hs3.metric("Related news to check", tot_rel)

    if tot_trig:
        alerts = []
        for t in all_researched:
            for k, matched in assess_cache[t][1]:
                alerts.append(f'<b>{t}</b> <span style="font-family:monospace;font-size:11px">[{k.get("label","")}]</span>')
        st.markdown(
            '<div style="padding:10px 14px;margin:6px 0;background:rgba(239,68,68,.07);'
            'border-left:3px solid #ef4444;border-radius:5px;font-size:13px">'
            '\u26A0\uFE0F <b>Possible thesis-killer trigger \u2014 confirm</b> \u00b7 ' + " \u00b7 ".join(alerts) +
            '<div style="font-size:11px;color:#888;margin-top:3px">Open the Research Library below for detail.</div></div>',
            unsafe_allow_html=True)

    # -- Holdings summary (open positions only) --
    st.markdown("##### Holdings")
    hold_rows = []
    for p in sorted(positions, key=lambda x: x["weight"], reverse=True):
        ticker = p["ticker"]
        ticker_news = [n for n in D["news"] if n.get("ticker") == ticker]
        has_threat = any(n.get("severity_tag") == "thesis-threatening" for n in ticker_news)
        has_material = any(n.get("severity_tag") == "material change" for n in ticker_news)
        snap = next((s for s in D["fundsnap"] if s.get("ticker") == ticker), None)
        note = notes_by_ticker.get(ticker)
        kl, trig, rel, sup, clr = assess_cache.get(ticker) or _assess(ticker)
        below_stop = p["cmp"] <= p["stop"] and p["stop"] > 0
        if below_stop:
            tech = "\U0001F534 STOP"
        elif p["cmp"] < p["entry"]:
            tech = "\U0001F7E1 Below entry"
        else:
            tech = "\U0001F7E2 Positive"
        if has_threat or trig:
            overall = "\U0001F534 Review"
        elif has_material or rel or below_stop:
            overall = "\U0001F7E1 Review"
        else:
            overall = "\U0001F7E2 Intact"
        if trig:
            kc = f"\U0001F534 {len(trig)}/{len(kl)}"
        elif rel:
            kc = f"\U0001F7E1 {len(rel)}/{len(kl)}"
        elif kl:
            kc = f"\U0001F7E2 {len(kl)}"
        else:
            kc = "\u2014"
        hold_rows.append({
            "Ticker": ticker, "Sleeve": p["sleeve"],
            "Verdict": (note.get("analyst_verdict") or "\u2014") if note else "No note",
            "Killers": kc,
            "Fundamentals": "\U0001F4CA" if snap else "\u23F3",
            "News": "\U0001F534 Threat" if has_threat else ("\U0001F7E1 Material" if has_material else "\U0001F7E2 Clear"),
            "Technical": tech, "Overall": overall,
        })
    if hold_rows:
        st.dataframe(pd.DataFrame(hold_rows), use_container_width=True, hide_index=True)
        st.caption("Killers = \U0001F534 possible trigger (confirm) \u00b7 \U0001F7E1 review \u00b7 \U0001F7E2 clear, over total active. A killer only attaches a news card when the news is adverse; positive/neutral news stays in the Material News Feed. Matches are keyword candidates \u2014 confirm against the source.")
    else:
        st.caption("No open positions.")

    st.divider()

    # -- Research Library (all notes, collapsed) --
    pos_tickers = {p["ticker"] for p in positions}
    lib_tickers = sorted(all_researched)

    def _card(label, desc, border, bg, tag, news_html=""):
        st.markdown(
            f'<div style="padding:9px 12px;margin:5px 0;background:{bg};'
            f'border-left:3px solid {border};border-radius:5px">'
            f'<span style="font-family:monospace;font-size:11px;color:#888">[{label}]</span> '
            f'<span style="font-size:13px">{desc}</span> '
            f'<span style="font-size:10px;color:{border};font-weight:700;white-space:nowrap">{tag}</span>'
            f'{news_html}</div>', unsafe_allow_html=True)

    with st.expander(f"\U0001F4DA Research Library \u2014 {len(notes_by_ticker)} note(s)", expanded=False):
        if not lib_tickers:
            st.info("No research notes synced yet. Add `## Thesis Killers` ([TK]) and `## Monitoring Checklist` ([MC]) sections to a note in `12 Research`; the watcher syncs it here.")
        else:
            fcol1, fcol2 = st.columns([2, 1])
            q = fcol1.text_input("Search ticker / company", key="lib_q").strip().lower()
            only_flag = fcol2.checkbox("Only flagged", key="lib_flag")
            lib_rows = []
            for t in lib_tickers:
                note = notes_by_ticker.get(t, {})
                kl, trig, rel, sup, clr = assess_cache.get(t) or _assess(t)
                if only_flag and not (trig or rel):
                    continue
                if q and q not in t.lower() and q not in (note.get("company", "") or "").lower():
                    continue
                if trig:
                    kc = f"\U0001F534 {len(trig)}/{len(kl)}"
                elif rel:
                    kc = f"\U0001F7E1 {len(rel)}/{len(kl)}"
                elif kl:
                    kc = f"\U0001F7E2 {len(kl)}"
                else:
                    kc = "\u2014"
                lib_rows.append({
                    "Ticker": t, "Company": note.get("company", "") or "",
                    "Verdict": note.get("analyst_verdict", "") or "\u2014",
                    "Status": note.get("status", "") or "\u2014",
                    "Held": "\u2713" if t in pos_tickers else "\u2014",
                    "Killers": kc, "Monitors": len(monitors_by_ticker.get(t, [])),
                })
            if lib_rows:
                st.dataframe(pd.DataFrame(lib_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No notes match the filter.")

            open_choices = [r["Ticker"] for r in lib_rows]
            if not open_choices:
                st.caption("No notes to open under the current filter \u2014 clear the search or untick \u201cOnly flagged\u201d.")
            else:
                st.markdown("**Open a note**")
                sel = st.selectbox("Stock", open_choices, key="lib_stock", label_visibility="collapsed")
                note = notes_by_ticker.get(sel)
                kl, trig, rel, sup, clr = assess_cache.get(sel) or _assess(sel)
                if note:
                    vc1, vc2, vc3, vc4 = st.columns(4)
                    vc1.metric("Verdict", note.get("analyst_verdict") or "\u2014")
                    vc2.metric("Status", note.get("status") or "\u2014")
                    iv_lo, iv_hi = f(note.get("intrinsic_value_low")), f(note.get("intrinsic_value_high"))
                    vc3.metric("Intrinsic Value", f"{fmt(iv_lo)}\u2013{fmt(iv_hi)}" if iv_lo and iv_hi else "\u2014")
                    vc4.metric("CMP at analysis", fmt(f(note.get("cmp_at_analysis"))))
                    if note.get("primary_risk"):
                        st.caption(f"**Primary risk:** {note['primary_risk']}")
                if not kl:
                    st.caption("No active thesis killers for this stock.")
                else:
                    st.markdown("##### Thesis Killers")
                    for k, matched in trig:
                        _card(k.get("label", ""), k.get("description", ""), "#ef4444",
                              "rgba(239,68,68,.06)", "\u26A0\uFE0F POSSIBLE TRIGGER \u2014 CONFIRM",
                              _news_line(matched, "#b23b3b") if matched else "")
                    for k, matched in rel:
                        _card(k.get("label", ""), k.get("description", ""), "#f59e0b",
                              "rgba(245,158,11,.05)", "\U0001F50E review \u2014 unconfirmed",
                              _news_line(matched, "#8a6d1a") if matched else "")
                    for k, matched in sup:
                        _card(k.get("label", ""), k.get("description", ""), "#3f9142",
                              "rgba(63,145,66,.05)", "\U0001F7E2 supportive news",
                              _news_line(matched, "#2f6f4f") if matched else "")
                    if clr:
                        chips = " \u00b7 ".join(
                            f'<span style="font-family:monospace;font-size:11px;color:#3f9142">{k.get("label","")}</span>'
                            for k in clr)
                        st.markdown(
                            f'<div style="padding:8px 12px;margin:5px 0;background:rgba(63,145,66,.04);'
                            f'border-left:3px solid #3f9142;border-radius:5px;font-size:12px">'
                            f'<span style="color:#3f9142;font-weight:700">\U0001F7E2 Clear</span> \u00b7 {chips}</div>',
                            unsafe_allow_html=True)
                monitors = monitors_by_ticker.get(sel, [])
                if monitors:
                    st.markdown("##### Monitoring Checklist")
                    freq_col = {"QUARTERLY": "#2f6f4f", "EVENT": "#5b4bb0", "MONTHLY": "#b4690e", "ANNUAL": "#2563eb"}
                    order = {"QUARTERLY": 0, "EVENT": 1, "MONTHLY": 2, "ANNUAL": 3}
                    for m in sorted(monitors, key=lambda x: order.get(x.get("frequency"), 9)):
                        fq = m.get("frequency", "")
                        col = freq_col.get(fq, "#666")
                        st.markdown(
                            f'<div style="padding:5px 0;font-size:13px;border-bottom:1px solid rgba(0,0,0,.05)">'
                            f'<span style="display:inline-block;min-width:88px;font-size:9.5px;font-weight:700;'
                            f'letter-spacing:.04em;color:#fff;background:{col};padding:2px 7px;border-radius:10px;'
                            f'text-align:center">{fq}</span> '
                            f'<span style="font-family:monospace;font-size:11px;color:#888">{m.get("label","")}</span> '
                            f'\u2014 {m.get("description","")}</div>',
                            unsafe_allow_html=True)
    st.divider()

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

# ═══════════════════════════════════════════════════════════
# TAB 7 — FULL CYCLE FRAMEWORK
# ═══════════════════════════════════════════════════════════
with tab_framework:
    st.markdown("## Full Cycle Tactical Framework")
    st.caption("Complete deploy-and-harvest system for the tactical cash reserve")

    # Live context — drives the "you are here" markers below.
    fw_pct = nifty["pct_from_ema"] if nifty else None
    fw_bpct = breadth["pct_above"] if breadth else None

    def _fw_head(title, sub=None):
        st.markdown(f'<div class="fw-h">{title}<span class="rule"></span></div>', unsafe_allow_html=True)
        if sub:
            st.markdown(f'<div class="fw-sub">{sub}</div>', unsafe_allow_html=True)

    # ── Capital Architecture ──
    _fw_head("Capital Architecture")
    arch_cols = st.columns(3)
    for i, a in enumerate(CAPITAL_ARCH):
        with arch_cols[i]:
            st.markdown(
                f'<div class="fw-card"><div class="k">{a["name"]}</div>'
                f'<div class="v">{a["pct"]}%</div>'
                f'<div class="d">{a["desc"]}</div></div>', unsafe_allow_html=True)

    # ── The Cycle ──
    _fw_head("The Cycle", "Which side of the 200 EMA you are on decides whether you deploy or harvest.")

    if fw_pct is None:
        now_html = '<span class="now" style="background:#eef1f5;color:#6b7280">Nifty unavailable</span>'
    else:
        _nb, _nc = ("#feecec", "#991b1b") if fw_pct < 0 else ("#eaf7ed", "#166534")
        _nv = ("%+.2f" % fw_pct).replace("-", "−")
        now_html = (f'<span class="now" style="background:{_nb};color:{_nc}">'
                    f'Now {_nv}%</span>')

    # Active tier markers
    def _chip(name, active, color):
        if active:
            return f'<span class="cyc-t on" style="background:{color}">{name}</span>'
        return f'<span class="cyc-t">{name}</span>'

    h_levels = [("H1", 5), ("H2", 12), ("H3", 17), ("H4", 20)]
    h_chips = "".join(
        _chip(nm, fw_pct is not None and fw_pct >= lv, "#16a34a") for nm, lv in h_levels)
    t_chips = "".join(
        _chip(f'T{t["tier"]}', fw_pct is not None and fw_pct <= t["threshold"], "#dc2626")
        for t in LADDER_TIERS)

    st.markdown(
        f'<div class="cyc">'
        f'<div class="cyc-band cyc-harvest">'
        f'<span class="cyc-zone" style="color:#166534">Harvest zone</span>'
        f'<span class="cyc-note">Above EMA — book profits, trail stops</span>'
        f'<span class="cyc-tiers">{h_chips}</span></div>'
        f'<div class="cyc-ema"><span class="lbl">NIFTY 200 EMA</span>'
        f'<span class="line"></span>{now_html}</div>'
        f'<div class="cyc-band cyc-deploy">'
        f'<span class="cyc-zone" style="color:#991b1b">Deploy zone</span>'
        f'<span class="cyc-note">Below EMA — buy the dip, fear = opportunity</span>'
        f'<span class="cyc-tiers">{t_chips}</span></div>'
        f'</div>', unsafe_allow_html=True)

    # ── Deployment Ladder ──
    _fw_head("Deployment Ladder",
             "Deploy tactical cash in 5 tranches as Nifty falls further below its 200 EMA. "
             "Both the EMA distance <em>and</em> breadth must confirm.")

    rows = ""
    for t in LADDER_TIERS:
        base = t["deploy_pct"]
        ema_ok = fw_pct is not None and fw_pct <= t["threshold"]
        br_ok = fw_bpct is not None and fw_bpct <= t["breadth_max"]
        if ema_ok and br_ok:
            cls, gate = " class=\"on\"", '<span style="color:#b45309;font-weight:700">Armed</span>'
        elif ema_ok:
            cls, gate = "", '<span style="color:#9ca3af">Breadth not confirmed</span>'
        else:
            cls, gate = "", '<span style="color:#c7cbd3">—</span>'
        here = '<span class="fw-here">HERE</span>' if (ema_ok and br_ok) else ""
        rows += (
            f'<tr{cls}><td class="tier">T{t["tier"]}{here}</td>'
            f'<td class="why">{t["label"].split("— ")[-1]}</td>'
            f'<td class="num">{("%+.0f" % t["threshold"]).replace("-", chr(0x2212))}%</td>'
            f'<td class="num">&le; {t["breadth_max"]}%</td>'
            f'<td class="num">{base}%</td>'
            f'<td class="num">{base * 1.5:.0f}%</td>'
            f'<td class="num">{base * 2:.0f}%</td>'
            f'<td class="c">{gate}</td></tr>')

    st.markdown(
        f'<div class="fw-scroll"><table class="fw-tbl"><thead><tr>'
        f'<th>Tier</th><th>Condition</th><th class="num">EMA</th><th class="num">Breadth</th>'
        f'<th class="num">Base</th><th class="num">Fear 1+</th><th class="num">Fear 2+</th>'
        f'<th class="c">Status</th></tr></thead><tbody>{rows}</tbody></table></div>',
        unsafe_allow_html=True)
    st.caption("Breadth = % of Nifty 200 stocks above their own 200 DMA. "
               "Fear multipliers: 1 gauge elevated → 1.5×; 2+ gauges elevated → 2× + fires 1% early.")

    # ── Fear Gauge Modifier ──
    _fw_head("Fear Gauge Modifier", "Three independent fear signals — each is binary ON/OFF.")

    fear_cols = st.columns(3)
    fear_gauges_def = [
        ("MOVE Index", "≥ 80 (extreme ≥ 100)", "US rates volatility → global risk-off"),
        ("India VIX", "≥ 20 (Fear/Stress/Panic)", "Domestic implied volatility spike"),
        ("Credit Stress", "LQD/HYG ratio ≥ 80th pctile", "IG–Govt spread widens — credit fear"),
    ]
    for i, (name, trigger, desc) in enumerate(fear_gauges_def):
        with fear_cols[i]:
            st.markdown(
                f'<div class="fw-card"><div class="k">{name}</div>'
                f'<div class="v" style="font-size:16px">{trigger}</div>'
                f'<div class="d">{desc}</div></div>', unsafe_allow_html=True)

    fg_now = fear_gauges.get("signals_on", 0) if fear_gauges else None
    fear_rows = ""
    for label, n, mult, eff, early in [
        ("0 of 3 (none)", (0,), "1×", "8% per tier", "—"),
        ("1 of 3", (1,), "1.5×", "12% per tier", "—"),
        ("2+ of 3", (2, 3), "2×", "16% per tier", "+1% early"),
    ]:
        on = fg_now is not None and fg_now in n
        here = '<span class="fw-here">NOW</span>' if on else ""
        rcls = ' class="on"' if on else ''
        fear_rows += (f'<tr{rcls}><td>{label}{here}</td>'
                      f'<td class="c">{mult}</td><td class="c">{eff}</td>'
                      f'<td class="c">{early}</td></tr>')
    st.markdown(
        f'<div class="fw-scroll"><table class="fw-tbl"><thead><tr><th>Confirmations</th><th class="c">Multiplier</th>'
        f'<th class="c">Effective deploy</th><th class="c">Early-fire</th></tr></thead>'
        f'<tbody>{fear_rows}</tbody></table></div>', unsafe_allow_html=True)
    st.caption("2+ confirmations also fires the next tier 1% early — e.g. T2 at −9% instead of −10%.")

    # ── Harvest Ladder ──
    _fw_head("Harvest Ladder", "Book profits systematically as Nifty rises above its 200 EMA.")

    harvest_rows = ""
    for tier, lvl, action, book, basis in [
        ("H1", 5, "Activate trailing stops", "—",
         "Median rally off 200 EMA ≈ 8–12%. Trail protects gains if momentum fades."),
        ("H2", 12, "Book 25% of tactical", "25%",
         "75th-pctl rally from EMA. Historically only ~40% of rallies sustain past +12%."),
        ("H3", 17, "Book 50% of tactical", "50%",
         "90th-pctl extension. Mean-reversion risk rises sharply."),
        ("H4", 20, "Book 75% of tactical", "75%",
         "97th-pctl — extreme overextension. Rare; preserve capital."),
    ]:
        on = fw_pct is not None and fw_pct >= lvl
        here = '<span class="fw-here">HERE</span>' if on else ""
        rcls = ' class="on"' if on else ''
        harvest_rows += (
            f'<tr{rcls}><td class="tier">{tier}{here}</td>'
            f'<td class="num">+{lvl}%</td><td>{action}</td>'
            f'<td class="num">{book}</td><td class="why">{basis}</td></tr>')
    st.markdown(
        f'<div class="fw-scroll"><table class="fw-tbl"><thead><tr><th>Tier</th><th class="num">Above EMA</th>'
        f'<th>Action</th><th class="num">Book</th><th>Basis</th></tr></thead>'
        f'<tbody>{harvest_rows}</tbody></table></div>', unsafe_allow_html=True)

    # ── Complacency Modifier ──
    _fw_head("Complacency Modifier",
             "When fear gauges show extreme calm during extended rallies, accelerate harvesting.")
    st.markdown(
        '<div class="fw-scroll"><table class="fw-tbl"><thead><tr><th>Condition</th><th>Rule</th></tr></thead><tbody>'
        '<tr><td>VIX &lt; 12 <strong>and</strong> Nifty +10%+ above EMA</td>'
        '<td>Accelerate harvest by 1 tier (at H2, act as H3)</td></tr>'
        '<tr><td>All 3 gauges calm <strong>and</strong> +15%+ above EMA</td>'
        '<td>Book 75% immediately (jump to H4 action)</td></tr>'
        '</tbody></table></div>', unsafe_allow_html=True)
    st.caption("Complacency = low VIX + extended rally. This is when markets are most "
               "vulnerable to sharp reversals.")

    # ── Framework Rules Summary ──
    _fw_head("Framework Rules")

    with st.expander("Core rules", expanded=True):
        st.markdown("""
        1. **Never deploy above the 200 EMA** — above EMA is harvest territory
        2. **Never harvest below the 200 EMA** — below EMA is deployment territory
        3. **Dual-condition gating** — both EMA distance AND breadth must confirm before deploying
        4. **Fear amplifies deployment** — more fear signals → bigger tranches + earlier triggers
        5. **Complacency accelerates harvest** — calm gauges during rallies → take profits faster
        6. **25% minimum cash floor** — never deploy more than 75% of tactical reserve
        7. **Trail stops on all tactical positions** once H1 is reached
        """)

    with st.expander("Override conditions"):
        st.markdown("""
        - **Thesis breach** on any position → exit regardless of cycle position
        - **Concentration > 15%** in any single name → trim to limit
        - **Sector concentration > 30%** → rebalance across sectors
        - **Stop-loss hit** → exit at stop, no averaging down
        """)

    with st.expander("Historical basis"):
        st.markdown("""
        Based on Nifty 50 data (2005–2024):

        - **Median drawdown from 200 EMA**: −12% (supports T1–T3 as primary deploy zone)
        - **75th percentile drawdown**: −18% (T4 territory — happens ~25% of corrections)
        - **95th percentile drawdown**: −30%+ (beyond T5 — GFC/COVID-level, ~5% of events)
        - **Median rally above 200 EMA**: +8–12% (H1 activation zone)
        - **Time below EMA per episode**: median 45 trading days, mean 65 days
        - **Recovery to EMA**: median 30 trading days from bottom
        - **VIX correlation**: corrections with VIX > 25 tend to be 1.5× deeper but recover faster
        """)

# ═══════════════════════════════════════════════════════════
# TAB 8 — BACKTEST
# ═══════════════════════════════════════════════════════════
with tab_backtest:
    st.markdown("## Tactical Ladder Backtest")
    st.caption("Replays the deploy-and-harvest rules over stored history. "
               "Rules live in backtest_engine.py and are unit-tested separately.")

    @st.cache_data(ttl=900)
    def _load_series(symbol):
        """Fetch one symbol's full history.

        PostgREST caps a select at 1000 rows, so page explicitly — otherwise
        an 11-year series silently becomes its first four years.
        """
        sb = _sb()
        rows, step, start = [], 1000, 0
        while True:
            page = (sb.table("index_history")
                      .select("bar_date, close")
                      .eq("symbol", symbol)
                      .order("bar_date")
                      .range(start, start + step - 1)
                      .execute().data or [])
            rows.extend(page)
            if len(page) < step:
                break
            start += step
        return rows

    @st.cache_data(ttl=900)
    def _load_breadth(source="rzone_pnf_1pct"):
        sb = _sb()
        rows, step, start = [], 1000, 0
        while True:
            page = (sb.table("breadth_readings")
                      .select("reading_date, breadth_pct")
                      .eq("source", source)
                      .order("reading_date")
                      .range(start, start + step - 1)
                      .execute().data or [])
            rows.extend(page)
            if len(page) < step:
                break
            start += step
        return rows

    @st.cache_data(ttl=900)
    def _symbol_coverage(_bust=0):
        """Symbol inventory from a view.

        A plain select on index_history is capped at 1000 rows by PostgREST,
        so counting symbols from raw rows can miss whole series. The view
        aggregates server-side and returns one row per symbol.
        """
        sb = _sb()
        rows = sb.table("index_history_symbols").select("*").execute().data or []
        return {r["symbol"]: {"bars": int(r["bars"]),
                              "first": datetime.fromisoformat(r["first_bar"]).date(),
                              "last": datetime.fromisoformat(r["last_bar"]).date()}
                for r in rows}

    _bust = st.session_state.get("bt_bust", 0)
    if st.button("↻ Refresh data", key="bt_refresh",
                 help="Clear cached series after a fresh Index History Sync run"):
        st.cache_data.clear()
        st.session_state["bt_bust"] = _bust + 1
        st.rerun()

    try:
        cover = _symbol_coverage(_bust)
    except Exception as e:
        cover = {}
        st.error(f"Could not reach index_history: {e}")
    have = sorted(cover)

    MIDCAP_LABEL = {
        "NIFTYMIDCAP150.NS": "Nifty Midcap 150 — the framework's actual asset",
        "^NSEMDCP50": "Nifty Midcap 50 — proxy",
        "^NSMIDCP": "Nifty Midcap 50 — proxy",
        "^CNXMID": "Nifty Midcap 100 — proxy",
        "MID150BEES.NS": "Midcap 150 ETF — proxy",
        "MIDCAPETF.NS": "Midcap ETF — proxy",
        "^NSEI": "Nifty 50 — fallback, not a midcap series",
    }

    # A guard flag, not st.stop(): st.stop() would abort the whole script run,
    # taking the footer and every later element with it, just because this one
    # tab lacked data.
    _bt_ok = "^NSEI" in have
    if not _bt_ok:
        st.warning(
            "No Nifty history in `index_history` yet. Run the **Index History Sync** "
            "workflow with `backfill = true` to seed it, then hit Refresh data."
        )

    if _bt_ok:
        # The deploy asset is an explicit choice, not a silent default: the
        # framework's own asset (Midcap 150) only lists from 2019, while the
        # Midcap 50 proxies reach back to 2014. Picking the "correct" symbol
        # automatically would quietly cut four years — including the 2018
        # correction — off the backtest without saying so.
        mid_opts = [s for s in MIDCAP_LABEL if s in cover and s != "^NSEI"]
        mid_opts.sort(key=lambda s: cover[s]["first"])          # longest history first
        mid_opts = [s for s in mid_opts if cover[s]["bars"] >= 250]
        if not mid_opts:
            mid_opts = ["^NSEI"]

        def _mid_fmt(s):
            c = cover.get(s, {})
            return (f"{MIDCAP_LABEL.get(s, s)} · from {c.get('first')} "
                    f"({c.get('bars', 0):,} bars)")

        mid_sym = st.selectbox(
            "Deploy asset — what tactical cash buys",
            mid_opts, index=0, format_func=_mid_fmt, key="bt_midcap",
            help="Longest history first. Midcap 150 is the framework's asset but "
                 "only lists from 2019; the Midcap 50 proxies reach back to 2014.")

        # ── Controls ──
        c1, c2 = st.columns([1.2, 1])
        with c1:
            bt_start = st.date_input("Start", value=date(2015, 6, 1), key="bt_start")
        with c2:
            bt_initial = st.number_input("Initial (₹ lakh)", 1.0, 1000.0, 10.0, 1.0,
                                         key="bt_initial")

        chk1, chk2, chk3 = st.columns(3)
        with chk1:
            bt_breadth = st.checkbox("Breadth gate", value=True, key="bt_breadth",
                                     help="Require low breadth to confirm deploy "
                                          "(statistically validated — T1/T2 Sharpe 0.54 vs 0.21 baseline)")
        with chk2:
            bt_harvest = st.checkbox("Harvest ladder", value=True, key="bt_harvest",
                                     help="Book deployed positions back to liquid as EMA recovers "
                                          "(H2 +12%, H3 +17%, H4 +20% — stats-calibrated thresholds)")
        with chk3:
            bt_v2 = st.checkbox("V2 model", value=False, key="bt_v2",
                                 help="₹10L always-on equity core + ₹4L revolving tactical buffer. "
                                      "Annual Dec rebalance. Results shown normalised to ₹10L.")

        if bt_v2:
            _cv1, _cv2, _cv3 = st.columns([1, 1, 2])
            with _cv1:
                bt_v2_core = st.number_input(
                    "Core (₹ lakh)", 1.0, 1000.0, 10.0, 1.0, key="bt_v2_core",
                    help="Always-on equity sleeve (60% Midcap / 40% Nifty)"
                )
            with _cv2:
                bt_v2_tactical = st.number_input(
                    "Tactical (₹ lakh)", 1.0, 500.0, 4.0, 1.0, key="bt_v2_tactical",
                    help="Revolving tactical buffer — liquid at 6.5% when idle"
                )
            with _cv3:
                st.write("")
                bt_v2_vix_gate = st.checkbox(
                    "VIX gate — require min VIX before deploy "
                    "*(T1/T2 need VIX≥20, T3+ need VIX≥25)*",
                    value=True, key="bt_v2_vix_gate",
                    help="Filters low-fear deploys (VIX<20 Calm/Elevated had Sharpe≈0.21 — barely above baseline)"
                )
        else:
            bt_v2_core, bt_v2_tactical, bt_v2_vix_gate = 10.0, 4.0, True

        with st.expander("Advanced options"):
            bt_fear = st.checkbox("Fear multipliers", value=False, key="bt_fear",
                                  help="VIX≥20 → 1.5x deploy size; VIX≥20 AND MOVE≥80 → 2x + tiers fire 1% early. "
                                       "Credit-stress gauge is live-only; backtest uses 2 signals.")
            bt_vix_split = st.checkbox(
                "VIX-regime Nifty/Midcap split",
                value=False, key="bt_vix_split",
                help=(
                    "Adapts Nifty/Midcap split to VIX regime (19-yr analysis, 4,550+ bars). "
                    "Calm/Normal/Fear/Panic → 100% Midcap; Elevated 16-20 → 0% Midcap; "
                    "Stress 25-30 → 55% Midcap / 45% Nifty."
                )
            )

        nifty_rows = _load_series("^NSEI")
        mid_rows = _load_series(mid_sym) if mid_sym != "^NSEI" else nifty_rows
        breadth_rows = _load_breadth()

        n_by_date = {datetime.fromisoformat(r["bar_date"]).date(): float(r["close"])
                     for r in nifty_rows}
        m_by_date = {datetime.fromisoformat(r["bar_date"]).date(): float(r["close"])
                     for r in mid_rows}
        b_by_date = {datetime.fromisoformat(r["reading_date"]).date(): float(r["breadth_pct"])
                     for r in breadth_rows}

        # The ladder needs 200 bars of Nifty before the first decision, so start the
        # series early and only *evaluate* from bt_start.
        warm = 260
        all_dates = sorted(d for d in n_by_date if d in m_by_date)
        eval_from = bt_start
        idx0 = next((i for i, d in enumerate(all_dates) if d >= eval_from), 0)
        series_dates = all_dates[max(0, idx0 - warm):]

        if len(series_dates) < 250:
            st.warning(f"Only {len(series_dates)} aligned bars — not enough to warm a 200 EMA.")
            st.stop()

        # VIX series — loaded whenever fear multipliers, VIX split or V2 are enabled
        vix_by_date: dict = {}
        if bt_fear or bt_vix_split or bt_v2:
            vix_by_date = {datetime.fromisoformat(r["bar_date"]).date(): float(r["close"])
                           for r in _load_series("^INDIAVIX")}

        fear_map = {}
        if bt_fear:
            move = {datetime.fromisoformat(r["bar_date"]).date(): float(r["close"])
                    for r in _load_series("^MOVE")}
            for d in series_dates:
                on = 0
                if vix_by_date.get(d) is not None and vix_by_date.get(d, 0) >= cfg.FEAR_SIGNAL_VIX_MIN:
                    on += 1
                if move.get(d) is not None and move.get(d, 0) >= cfg.FEAR_SIGNAL_MOVE_MIN:
                    on += 1
                fear_map[d] = on   # credit-stress gauge not stored; max 2 of 3 here

        res = bte.run(
            series_dates,
            [n_by_date[d] for d in series_dates],
            [m_by_date[d] for d in series_dates],
            b_by_date,
            initial=bt_initial * 100_000,
            use_breadth=bt_breadth,
            fear_signals=fear_map or None,
            enable_harvest=bt_harvest,
        )
        bh = bte.buy_and_hold(series_dates,
                              [n_by_date[d] for d in series_dates],
                              [m_by_date[d] for d in series_dates],
                              initial=bt_initial * 100_000)
        static = bte.static_allocation(series_dates,
                                       [n_by_date[d] for d in series_dates],
                                       [m_by_date[d] for d in series_dates],
                                       initial=bt_initial * 100_000)
        lump = bte.lump_sum(series_dates,
                            [n_by_date[d] for d in series_dates],
                            [m_by_date[d] for d in series_dates],
                            initial=bt_initial * 100_000)

        # ── VIX-split comparison (run_matrix engine) ──
        gold_rows = _load_series("GOLD_INR_SYNTH")
        g_by_date = {datetime.fromisoformat(r["bar_date"]).date(): float(r["close"])
                     for r in gold_rows}
        # Align gold: fill missing bars with nearest prior close so run_matrix
        # always gets a valid series aligned to series_dates.
        g_series: list[float] = []
        g_last = None
        for d in series_dates:
            v = g_by_date.get(d, g_last)
            if v is None:
                v = next((g_by_date[x] for x in sorted(g_by_date) if x <= d), None)
            g_last = v
            g_series.append(v or 0.0)

        res_vix = None
        if bt_vix_split and any(v > 0 for v in g_series):
            try:
                res_vix = bte.run_matrix(
                    series_dates,
                    [n_by_date[d] for d in series_dates],
                    [m_by_date[d] for d in series_dates],
                    g_series,
                    b_by_date,
                    vix_by_date or None,
                    initial=bt_initial * 100_000,
                    gates_on=bt_breadth,
                    use_vix_split=True,
                )
            except Exception as _e:
                st.warning(f"VIX-split backtest failed: {_e}")

        # ── V2 model run ──────────────────────────────────────────────────
        res_v2 = None
        if bt_v2:
            try:
                res_v2 = bte.run_v2(
                    series_dates,
                    [n_by_date[d] for d in series_dates],
                    [m_by_date[d] for d in series_dates],
                    b_by_date,
                    vix_by_date or None,
                    core_initial=bt_v2_core * 100_000,
                    tactical_initial=bt_v2_tactical * 100_000,
                    use_breadth=bt_breadth,
                    use_vix_gate=bt_v2_vix_gate,
                )
            except Exception as _e:
                st.warning(f"V2 backtest failed: {_e}")

        # ── Provenance ──
        first_decision = series_dates[min(199, len(series_dates) - 1)]
        st.markdown(
            f'<div class="fw-sub">Deploy asset: <strong>{MIDCAP_LABEL.get(mid_sym, mid_sym)}</strong> '
            f'(<code>{mid_sym}</code>) · {len(series_dates):,} aligned bars · '
            f'series {series_dates[0]:%d %b %Y} → {series_dates[-1]:%d %b %Y} · '
            f'first possible tier decision {first_decision:%d %b %Y} '
            f'(200 bars warm the EMA) · '
            f'breadth matched on {sum(1 for d in series_dates if d in b_by_date):,} bars'
            f'</div>', unsafe_allow_html=True)
        if mid_sym == "^NSEI":
            st.warning("No midcap series loaded — deploying into Nifty 50 instead. "
                       "Re-run the Index History Sync backfill, then hit Refresh data.")
        if series_dates[0] > date(2016, 1, 1):
            st.info(
                f"This series only starts {series_dates[0]:%b %Y}, so the backtest "
                f"misses 2015–2018. Pick a Midcap 50 proxy above for the full window, "
                f"at the cost of using a different index than the framework specifies."
            )

        # ── Equity curve (always shown, overlays all active strategies) ──
        eq_dict: dict = {
            "Date": res.dates,
            "Tactical Ladder": res.equity,
            "Same allocation, no ladder": static.equity,
            "Lump sum": lump.equity,
            "Fully invested 60/40": bh.equity,
        }
        if res_vix is not None:
            vix_eq_map = dict(zip(res_vix.dates, res_vix.total))
            eq_dict["VIX-regime split"] = [vix_eq_map.get(d) for d in res.dates]
        if res_v2 is not None:
            # Normalise V2 to same starting capital as ladder for visual comparison
            v2_start = res_v2.total[0] if res_v2.total else 1
            ladder_start = res.equity[0] if res.equity else 1
            v2_map = dict(zip(res_v2.dates, res_v2.total))
            eq_dict["V2 (normalised)"] = [
                (v2_map.get(d, None) / v2_start * ladder_start) if v2_map.get(d) else None
                for d in res.dates
            ]
        eq = pd.DataFrame(eq_dict).set_index("Date")
        st.line_chart(eq, height=300)

        # ── Results detail ──
        def _render_ladder_detail(container=st):
            """Headline metrics for all active strategies, comparison table, deployment chart, events."""
            edge = res.cagr() - static.cagr()

            # ── Ladder headline ──
            container.markdown("**Tactical Ladder**")
            h1, h2, h3 = container.columns(3)
            h1.metric("Ladder CAGR", f"{res.cagr()*100:.2f}%")
            h2.metric("Same allocation, no ladder", f"{static.cagr()*100:.2f}%",
                      delta=f"{edge*100:+.2f} pp from the rules", delta_color="normal")
            h3.metric("Max drawdown", f"{res.max_drawdown()*100:.1f}%",
                      delta=f"vs {static.max_drawdown()*100:.1f}% static", delta_color="off")

            # ── V2 headline — shown immediately below ──
            if res_v2 is not None:
                container.markdown("**V2 model** *(always-on equity core + revolving tactical buffer)*")
                w1, w2, w3 = container.columns(3)
                w1.metric("V2 CAGR", f"{res_v2.cagr()*100:.2f}%",
                          delta=f"{(res_v2.cagr()-res.cagr())*100:+.2f} pp vs Ladder",
                          delta_color="normal")
                w2.metric("V2 final", f"₹{res_v2.final/100000:.2f}L",
                          help=f"Starting ₹{(bt_v2_core+bt_v2_tactical):.0f}L "
                               f"(₹{bt_v2_core:.0f}L core + ₹{bt_v2_tactical:.0f}L tactical)")
                w3.metric("V2 max DD", f"{res_v2.max_drawdown()*100:.1f}%",
                          delta=f"{(res_v2.max_drawdown()-res.max_drawdown())*100:+.1f} pp vs Ladder",
                          delta_color="off")

            # ── VIX-split headline — shown together with other strategy headlines ──
            if res_vix is not None:
                container.markdown("**VIX-regime Nifty/Midcap split**")
                v1, v2c, v3 = container.columns(3)
                v1.metric("VIX-split CAGR", f"{res_vix.cagr()*100:.2f}%",
                          delta=f"{(res_vix.cagr()-res.cagr())*100:+.2f} pp vs Ladder",
                          delta_color="normal")
                v2c.metric("VIX-split final", f"₹{res_vix.final/100000:.2f}L")
                v3.metric("VIX-split max DD", f"{res_vix.max_drawdown()*100:.1f}%",
                          delta=f"{(res_vix.max_drawdown()-res.max_drawdown())*100:+.1f} pp",
                          delta_color="off")
                container.caption(
                    "VIX regime steers Nifty/Midcap allocation at each rebalance. "
                    "Calm/Normal → 100% Midcap; Elevated (16–20) → 0% Midcap; "
                    "Stress (25–30) → 55% Midcap / 45% Nifty."
                )

            # ── Comparison table (all active strategies) ──
            n_strats = 4 + (1 if res_v2 is not None else 0) + (1 if res_vix is not None else 0)
            container.markdown(
                f'<div class="fw-h">All {n_strats} strategies<span class="rule"></span></div>',
                unsafe_allow_html=True)
            comp_rows = [
                {"Strategy": "Tactical Ladder",
                 "What it is": "35/25 core + 40% tactical deployed by the rules",
                 "CAGR": f"{res.cagr()*100:.2f}%",
                 "Final": f"₹{res.final/100000:.2f}L",
                 "Max DD": f"{res.max_drawdown()*100:.1f}%"},
                {"Strategy": "Same allocation, no ladder",
                 "What it is": "35/25 core + 40% left in cash at 6.5%",
                 "CAGR": f"{static.cagr()*100:.2f}%",
                 "Final": f"₹{static.final/100000:.2f}L",
                 "Max DD": f"{static.max_drawdown()*100:.1f}%"},
                {"Strategy": "Lump sum",
                 "What it is": "Tactical deployed into midcap on day one, held",
                 "CAGR": f"{lump.cagr()*100:.2f}%",
                 "Final": f"₹{lump.final/100000:.2f}L",
                 "Max DD": f"{lump.max_drawdown()*100:.1f}%"},
                {"Strategy": "Fully invested 60/40",
                 "What it is": "No cash at all — allocation ceiling, not a rules test",
                 "CAGR": f"{bh.cagr()*100:.2f}%",
                 "Final": f"₹{bh.final/100000:.2f}L",
                 "Max DD": f"{bh.max_drawdown()*100:.1f}%"},
            ]
            if res_v2 is not None:
                comp_rows.append({
                    "Strategy": "V2 model",
                    "What it is": f"₹{bt_v2_core:.0f}L equity core + ₹{bt_v2_tactical:.0f}L revolving tactical",
                    "CAGR": f"{res_v2.cagr()*100:.2f}%",
                    "Final": f"₹{res_v2.final/100000:.2f}L",
                    "Max DD": f"{res_v2.max_drawdown()*100:.1f}%",
                })
            if res_vix is not None:
                comp_rows.append({
                    "Strategy": "VIX-regime split",
                    "What it is": "VIX-driven Nifty/Midcap allocation at each rebalance",
                    "CAGR": f"{res_vix.cagr()*100:.2f}%",
                    "Final": f"₹{res_vix.final/100000:.2f}L",
                    "Max DD": f"{res_vix.max_drawdown()*100:.1f}%",
                })
            container.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)
            pct_below = sum(
                1 for i, d in enumerate(res.dates)
                if res.deployed[i] > 0) / max(1, len(res.dates)) * 100
            ladder_note = (
                f"Ladder held tactical exposure on {pct_below:.0f}% of bars. "
                "Compare **Ladder vs Same allocation, no ladder** to judge the entry rules: "
                "both hold the same 40% reserve — the only difference is whether it gets deployed on dips."
            )
            if bt_fear:
                ladder_note += " ⚡ Fear multipliers apply to Ladder only (not V2 or VIX-split)."
            container.caption(ladder_note)

            # ── Tactical cash chart ──
            container.markdown(
                '<div class="fw-h">Tactical cash vs deployed<span class="rule"></span></div>',
                unsafe_allow_html=True)
            dep = pd.DataFrame({
                "Date": res.dates, "Cash": res.cash, "Deployed": res.deployed,
            }).set_index("Date")
            container.area_chart(dep, height=200)

            # ── Ladder events ──
            container.markdown(
                '<div class="fw-h">Ladder events<span class="rule"></span></div>',
                unsafe_allow_html=True)
            if not res.events:
                container.info("No tier fired over this window with these settings.")
            else:
                ev = pd.DataFrame([{
                    "Date": e.d,
                    "Action": e.kind.title(),
                    "Tier": e.tier,
                    "EMA dist": f"{e.ema_dist:+.1f}%",
                    "Breadth": "—" if e.breadth is None else f"{e.breadth:.0f}%",
                    "Amount": "—" if e.amount == 0 else f"₹{e.amount:,.0f}",
                    "Note": e.note,
                } for e in res.events])
                n_dep = sum(1 for e in res.events if e.kind == "deploy")
                n_har = sum(1 for e in res.events if e.kind == "harvest")
                container.caption(f"{len(res.events)} events — {n_dep} deploys, {n_har} harvests")
                container.dataframe(ev, use_container_width=True, hide_index=True, height=320)

        _render_ladder_detail(st)

        # V2 events section (metrics already shown inline above)
        if res_v2 is not None:
            st.caption(
                f"V2: ₹{bt_v2_core:.0f}L equity core (60% Midcap / 40% Nifty, annual Dec rebalance) "
                f"+ ₹{bt_v2_tactical:.0f}L revolving tactical (liquid at 6.5% when idle). "
                "December harvest if Nifty YTD ≥ mean+2σ OR Nifty ≥ 10% above 200-EMA."
            )
            st.markdown('<div class="fw-h">V2 events<span class="rule"></span></div>',
                        unsafe_allow_html=True)
            if res_v2.events:
                v2_deploys = [e for e in res_v2.events if e.kind == "deploy"]
                v2_harvests = [e for e in res_v2.events if e.kind == "harvest"]
                st.caption(f"{len(res_v2.events)} events — "
                           f"{len(v2_deploys)} deploys · {len(v2_harvests)} December harvests")
                ev2 = pd.DataFrame([{
                    "Date": e.d,
                    "Action": "Harvest" if e.kind == "harvest" else "Deploy",
                    "Tier": e.tier,
                    "EMA dist": f"{e.ema_dist:+.1f}%",
                    "Amount": f"₹{e.amount:,.0f}",
                    "Note": e.note,
                } for e in res_v2.events])
                st.dataframe(ev2, use_container_width=True, hide_index=True)
            else:
                st.info("No V2 events in this window.")

        # ── Rolling horizon analysis ──────────────────────────────────────
        st.markdown('<div class="fw-h">Investment horizon analysis<span class="rule"></span></div>',
                    unsafe_allow_html=True)
        _hz_opts = st.multiselect(
            "Horizons (years)", [3, 5, 6, 8, 10, 15], default=[5, 6, 10],
            key="bt_horizons",
            help="For each horizon, shows rolling window stats across all start dates in the series."
        )
        if _hz_opts:
            _strats: dict = {
                "Tactical Ladder": (res.dates, res.equity),
                "Same alloc, no ladder": (static.dates, static.equity),
                "Lump sum": (lump.dates, lump.equity),
                "Fully invested 60/40": (bh.dates, bh.equity),
            }
            if res_vix is not None:
                _strats["VIX-regime split"] = (res_vix.dates, res_vix.total)
            if res_v2 is not None:
                _strats["V2 model"] = (res_v2.dates, res_v2.total)

            _rows = bte.horizon_table(_strats, horizons=[float(h) for h in _hz_opts])
            if _rows:
                _hdf = pd.DataFrame(_rows)
                # Format as percentages / readable numbers
                _pct_cols = ["Worst CAGR", "P25 CAGR", "Median CAGR", "P75 CAGR",
                             "Best CAGR", "Avg Max DD", "Worst Max DD"]
                _ratio_cols = ["% Positive"]
                _float2_cols = ["Avg Sharpe", "Avg Calmar"]
                _fmt: dict = {}
                for c in _pct_cols:
                    _fmt[c] = "{:.1f}%"
                for c in _ratio_cols:
                    _fmt[c] = "{:.0f}%"
                for c in _float2_cols:
                    _fmt[c] = "{:.2f}"
                _hdf_disp = _hdf.copy()
                for c in _pct_cols:
                    _hdf_disp[c] = _hdf[c].map(lambda x: f"{x*100:.1f}%")
                for c in _ratio_cols:
                    _hdf_disp[c] = _hdf[c].map(lambda x: f"{x*100:.0f}%")
                for c in _float2_cols:
                    _hdf_disp[c] = _hdf[c].map(lambda x: f"{x:.2f}")
                st.dataframe(_hdf_disp, use_container_width=True, hide_index=True)
                st.caption(
                    "Each row = rolling windows of that length slid 1 bar at a time. "
                    "Worst/Median/Best CAGR show the distribution of outcomes across all entry dates. "
                    "Avg Sharpe = excess over 6.5% risk-free, annualised. "
                    "Avg Calmar = median CAGR / avg max DD within window."
                )
            else:
                st.info("Not enough data for selected horizons — shorten the horizon or extend the start date.")

        with st.expander("What this does and does not model"):
            st.markdown(f"""
            **Models:** dual-condition tier arming (EMA distance *and* breadth),
            one fire per tier per cycle with re-arm on an EMA crossing, the 25%
            cash floor against the original reserve, {bte.CASH_YIELD*100:.1f}% p.a.
            on idle tactical cash, and the harvest ladder booking 25/50/75%.

            **Does not model:** brokerage, STT, slippage, tracking error between
            the index and a real ETF, dividends on the core sleeves, or taxes.
            Returns are index price returns, so they understate a total-return
            view. Fear gauges here use India VIX and MOVE only — the credit-stress
            gauge is derived live in the Cockpit and is not stored historically,
            so triple confirmation cannot fire in this backtest (max 2 of 3).

            **Survivorship:** breadth is the stored P&F X-Percent series, so it
            carries whatever construction Definedge used; it is not recomputed here.
            """)

# ── Footer ──────────────────────────────────────────────────
st.divider()
st.caption("India Master Portfolio V2 · Live from Supabase · Dashboard auto-refreshes every 60s")
