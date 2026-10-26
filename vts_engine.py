"""
vts_engine.py — VelociTrend Score (VTS) + VARB computation engine.

Pure functions over pandas DataFrames. Zero network calls, zero Supabase,
zero Streamlit. Feed it OHLCV data and a VIX value; it returns a VTSResult.

All formula weights and thresholds are injected as parameters so the
optimizer can sweep them without touching this file.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VTSResult:
    symbol: str
    date: str
    vts: float          # 0–100
    varb_spread: float  # stock_realvol_annualised - india_vix
    ema20: float
    ema20_slope: float  # raw % change over 10 days
    atr20: float        # in price units
    vol_ratio: float    # today_vol / 20d_avg_vol
    close: float


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_vts(
    ohlcv: pd.DataFrame,
    india_vix: float,
    w_ema: float = 0.5,
    ema_slope_days: int = 10,
    vol_window: int = 20,
    realvol_window: int = 20,
) -> VTSResult | None:
    """
    Compute VTS and VARB for the latest row of an OHLCV DataFrame.

    ohlcv must have columns: date (index or column), open, high, low, close, volume.
    Requires at least max(ema_slope_days + 20, 40) rows.
    Returns None if data is insufficient.
    """
    df = ohlcv.copy()
    if "date" in df.columns:
        df = df.set_index("date")
    df = df.sort_index()
    df.columns = [c.lower() for c in df.columns]

    min_rows = max(ema_slope_days + 20, realvol_window + 5, 40)
    if len(df) < min_rows:
        return None

    # ── EMA slope component ────────────────────────────────────
    ema20 = _ema(df["close"], 20)
    atr20 = _atr(df, 20)

    today_ema = ema20.iloc[-1]
    past_ema = ema20.iloc[-(ema_slope_days + 1)]
    atr_today = atr20.iloc[-1]

    if past_ema == 0 or atr_today == 0 or today_ema == 0:
        return None

    slope_raw_pct = (today_ema - past_ema) / past_ema * 100
    atr_pct = atr_today / today_ema * 100  # ATR as % of price
    if atr_pct == 0:
        return None

    slope_norm = np.clip(slope_raw_pct / atr_pct, -3.0, 3.0)
    ema_score = (slope_norm + 3.0) / 6.0 * 100  # rescale [-3,3] → [0,100]

    # ── Volume momentum component ──────────────────────────────
    vol_avg = df["volume"].rolling(vol_window).mean().iloc[-1]
    vol_today = df["volume"].iloc[-1]
    if vol_avg == 0:
        return None

    vol_ratio = vol_today / vol_avg
    # 0.5x → 0 score, 3.0x → 100 score, linear clip
    vol_score = float(np.clip((vol_ratio - 0.5) / 2.5, 0.0, 1.0) * 100)

    # ── VTS composite ──────────────────────────────────────────
    w_vol = 1.0 - w_ema
    vts = w_ema * ema_score + w_vol * vol_score

    # ── VARB spread ────────────────────────────────────────────
    log_returns = np.log(df["close"] / df["close"].shift(1)).dropna()
    if len(log_returns) < realvol_window:
        return None
    realvol_daily = log_returns.iloc[-realvol_window:].std()
    realvol_ann = realvol_daily * np.sqrt(252) * 100  # annualised %
    varb_spread = realvol_ann - india_vix

    return VTSResult(
        symbol="",
        date=str(df.index[-1]),
        vts=round(float(vts), 2),
        varb_spread=round(float(varb_spread), 2),
        ema20=round(float(today_ema), 4),
        ema20_slope=round(float(slope_raw_pct), 4),
        atr20=round(float(atr_today), 4),
        vol_ratio=round(float(vol_ratio), 3),
        close=round(float(df["close"].iloc[-1]), 4),
    )


def classify_signal(
    vts: float,
    varb_spread: float,
    regime: str,
    vts_buy_min: float = 70.0,
    vts_watch_min: float = 55.0,
    varb_neutral_max: float = 5.0,
    varb_caution_max: float = 15.0,
) -> str:
    """Return STRONG_BUY | BUY | WATCH | AVOID."""
    if regime == "risk_off" or vts < vts_watch_min:
        return "AVOID"
    if vts >= vts_buy_min:
        if varb_spread <= varb_neutral_max and regime == "risk_on":
            return "STRONG_BUY"
        if varb_spread <= varb_caution_max:
            return "BUY"
        return "WATCH"
    return "WATCH"


def compute_exits(close: float, atr20: float) -> dict:
    """Compute stop, target_1 and R for a trade entry."""
    risk = atr20
    return {
        "entry": close,
        "stop": round(close - risk, 4),
        "target_1": round(close + 2.0 * risk, 4),
        "risk_r": round(risk, 4),
    }
