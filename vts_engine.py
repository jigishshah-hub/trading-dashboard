"""
vts_engine.py — VelociTrend Score (VTS) + VARB computation engine.

VTS is a three-component composite designed to identify stocks in
institutional accumulation + coiling-spring setup:

  VTS = 0.40 × RelativeStrength  +  0.35 × EMASlope  +  0.25 × SetupQuality

  RelativeStrength (40%):
    6-month return of stock vs Nifty, normalised. Top-quintile RS stocks
    (academic: Jegadeesh-Titman) persistently outperform by 3-8% p.a.
    Ensures we trade what institutions are already buying, not random drifters.

  EMASlope (35%):
    ATR-normalised slope of EMA20 over 10 days. Confirms the trend is
    real and not just RS coasting. Prevents buying extended moves.

  SetupQuality (25%):
    ATR compression ratio: current ATR20% vs its 60-day max.
    A stock coiling (ATR contracting) near its recent high = coiled spring.
    Best R:R entries come from tight setups — risk is small and defined.

VARB:
    Stock 20-day realised vol minus India VIX. Guards against chasing
    blow-off tops — even a high-RS, coiling stock should NOT be entered
    when its own vol is ripping well above market fear (VIX).

Pure functions. Zero network calls, zero Supabase, zero Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VTSResult:
    symbol: str
    date: str
    vts: float            # 0–100 composite score
    rs_score: float       # relative strength component  [0,100]
    slope_score: float    # EMA slope component          [0,100]
    setup_score: float    # ATR compression component    [0,100]
    varb_spread: float    # stock_realvol - india_vix
    ema20: float
    atr20: float
    atr_compression: float  # 0=at max vol, 1=fully compressed
    near_high: bool         # close within 5% of 60-day high
    vol_ratio: float
    close: float


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_vts(
    ohlcv: pd.DataFrame,
    india_vix: float,
    nifty_return_126d: float = 0.0,   # Nifty 6-month return (fraction, e.g. 0.12)
    w_rs: float   = 0.40,
    w_ema: float  = 0.35,
    w_setup: float = 0.25,
    ema_slope_days: int = 10,
    realvol_window: int = 20,
) -> VTSResult | None:
    """
    Compute VTS + VARB for the latest bar.

    ohlcv: DataFrame with columns open/high/low/close/volume (date as index).
    nifty_return_126d: Nifty 50 total return over same trailing 126 trading
      days as the stock. Used to compute relative strength. Pass 0.0 if Nifty
      history is unavailable (RS score will then just be absolute momentum).
    Requires at least 130 rows.
    """
    df = ohlcv.copy()
    if "date" in df.columns:
        df = df.set_index("date")
    df = df.sort_index()
    df.columns = [c.lower() for c in df.columns]

    if len(df) < 130:
        return None

    close = df["close"]

    # ── Component 1: Relative Strength (6-month, 126 trading days) ────────
    # RS = stock 6m return / (1 + nifty 6m return)
    # Normalise with a sigmoid-like clip so an extreme RS outlier
    # doesn't dominate. Cap at ±60% relative return → [0,100].
    stock_return_126d = (close.iloc[-1] / close.iloc[-127] - 1.0)
    rs_raw = stock_return_126d - nifty_return_126d
    rs_score = float(np.clip((rs_raw + 0.60) / 1.20 * 100, 0.0, 100.0))

    # ── Component 2: EMA Slope (ATR-normalised) ───────────────────────────
    ema20   = _ema(close, 20)
    atr20   = _atr(df, 20)

    today_ema = float(ema20.iloc[-1])
    past_ema  = float(ema20.iloc[-(ema_slope_days + 1)])
    atr_today = float(atr20.iloc[-1])

    if past_ema == 0 or atr_today == 0 or today_ema == 0:
        return None

    slope_raw_pct = (today_ema - past_ema) / past_ema * 100
    atr_pct       = atr_today / today_ema * 100
    slope_norm    = float(np.clip(slope_raw_pct / atr_pct, -3.0, 3.0))
    slope_score   = (slope_norm + 3.0) / 6.0 * 100

    # ── Component 3: Setup Quality (ATR compression + near high) ─────────
    # ATR compression: current ATR% vs its rolling 60-day maximum.
    # 1.0 = fully compressed (coiling), 0.0 = at max expansion.
    atr_pct_series  = atr20 / ema20.replace(0, np.nan) * 100
    atr_pct_max_60d = atr_pct_series.rolling(60).max().iloc[-1]
    atr_compression = float(
        np.clip(1.0 - (atr_pct / atr_pct_max_60d), 0.0, 1.0)
        if atr_pct_max_60d > 0 else 0.0
    )

    # Near-high bonus: price within 5% of 60-day high → Stage 2 territory.
    high_60d  = float(close.rolling(60).max().iloc[-1])
    near_high = close.iloc[-1] >= 0.95 * high_60d
    near_high_bonus = 20.0 if near_high else 0.0

    setup_score = float(np.clip(atr_compression * 80.0 + near_high_bonus, 0.0, 100.0))

    # ── VTS Composite ─────────────────────────────────────────────────────
    vts = w_rs * rs_score + w_ema * slope_score + w_setup * setup_score

    # ── Volume ratio (informational) ──────────────────────────────────────
    vol_avg   = df["volume"].rolling(20).mean().iloc[-1]
    vol_ratio = float(df["volume"].iloc[-1] / vol_avg) if vol_avg > 0 else 1.0

    # ── VARB spread ───────────────────────────────────────────────────────
    log_returns = np.log(close / close.shift(1)).dropna()
    if len(log_returns) < realvol_window:
        return None
    realvol_ann = float(log_returns.iloc[-realvol_window:].std() * np.sqrt(252) * 100)
    varb_spread = realvol_ann - india_vix

    return VTSResult(
        symbol="",
        date=str(df.index[-1]),
        vts=round(float(vts), 2),
        rs_score=round(rs_score, 2),
        slope_score=round(slope_score, 2),
        setup_score=round(setup_score, 2),
        varb_spread=round(varb_spread, 2),
        ema20=round(today_ema, 4),
        atr20=round(atr_today, 4),
        atr_compression=round(atr_compression, 3),
        near_high=bool(near_high),
        vol_ratio=round(vol_ratio, 3),
        close=round(float(close.iloc[-1]), 4),
    )


def classify_signal(
    vts: float,
    varb_spread: float,
    regime: str,
    near_high: bool = False,
    vts_buy_min: float = 70.0,
    vts_watch_min: float = 55.0,
    varb_neutral_max: float = 5.0,
    varb_caution_max: float = 15.0,
) -> str:
    """Return STRONG_BUY | BUY | WATCH | AVOID."""
    if regime == "risk_off" or vts < vts_watch_min:
        return "AVOID"
    if vts >= vts_buy_min:
        if varb_spread <= varb_neutral_max and regime == "risk_on" and near_high:
            return "STRONG_BUY"
        if varb_spread <= varb_caution_max:
            return "BUY"
        return "WATCH"
    return "WATCH"


def compute_exits(close: float, atr20: float) -> dict:
    """Stop, target_1 and risk R for a trade entry at close."""
    return {
        "entry":    close,
        "stop":     round(close - atr20, 4),
        "target_1": round(close + 2.0 * atr20, 4),
        "risk_r":   round(atr20, 4),
    }
