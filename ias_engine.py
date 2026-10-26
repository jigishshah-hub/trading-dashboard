"""
ias_engine.py — Institutional Accumulation Score (IAS) engine.

Three components, all computable from OHLCV:

  IAS = 0.40 × AccumulationPattern + 0.35 × BaseQuality + 0.25 × RelativeMomentum_3m

AccumulationPattern (APS):
    Average volume on up-days vs down-days over 20 sessions.
    High ratio → institutions buying quietly without pushing price.
    AR = avg_vol_up / avg_vol_down. Mapped to [0,100].

BaseQuality (BQS):
    How tight and how long the consolidation base.
    Tight 20-day range = coiled. ATH proximity = no overhead resistance.

RelativeMomentum_3m (RMS):
    3-month RS vs Nifty (recency-weighted, captures current leadership).

Entry trigger (separate from IAS score):
    price closes above 20-day high AND volume > 1.5× 20-day avg volume.
    This is the breakout confirmation — institutions are visibly stepping in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class IASResult:
    ias: float
    aps: float           # Accumulation Pattern Score [0,100]
    bqs: float           # Base Quality Score [0,100]
    rms: float           # 3m Relative Momentum Score [0,100]
    ar: float            # raw up/down volume ratio
    base_range_pct: float  # 20-day price range as % of price
    breakout: bool       # volume-confirmed 20d high breakout today
    close: float
    atr20: float
    ema20: float
    varb_spread: float
    vol_ratio: float     # today vol / 20-day avg vol


# ── Constants ────────────────────────────────────────────────────────────────

_AR_LOW  = 0.7   # below this → distribution, score → 0
_AR_HIGH = 2.5   # above this → strong accumulation, score → 100
_RANGE_MAX_PCT = 15.0  # 15% range → BQS → 0 (wide, extended)
_BREAKOUT_VOL_MULT = 1.5  # volume must be 1.5× avg to confirm breakout


def compute_ias(
    ohlcv: pd.DataFrame,
    india_vix: float,
    nifty_return_63d: float = 0.0,
    w_aps: float = 0.40,
    w_bqs: float = 0.35,
    w_rms: float = 0.25,
) -> IASResult | None:
    """
    Compute IAS for the most recent bar in ohlcv.

    ohlcv must have lowercase columns: open, high, low, close, volume.
    Minimum 130 rows required (63 for 3m RS + 20 for indicators + buffer).
    """
    if len(ohlcv) < 130:
        return None

    close  = ohlcv["close"]
    high   = ohlcv["high"]
    low    = ohlcv["low"]
    volume = ohlcv["volume"]

    # ── ATR20 ────────────────────────────────────────────────────────────────
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr20 = tr.rolling(20).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()

    # ── VARB ─────────────────────────────────────────────────────────────────
    log_ret = np.log(close / close.shift(1))
    realvol20 = log_ret.rolling(20).std() * np.sqrt(252) * 100
    varb = realvol20.iloc[-1] - india_vix

    # ── AccumulationPattern Score (APS) ─────────────────────────────────────
    daily_ret = close - close.shift(1)
    up_mask   = (daily_ret > 0).astype(float)
    dn_mask   = (daily_ret <= 0).astype(float)

    vol_sum_up = (volume * up_mask).rolling(20).sum()
    vol_sum_dn = (volume * dn_mask).rolling(20).sum()
    days_up    = up_mask.rolling(20).sum().clip(lower=1)
    days_dn    = dn_mask.rolling(20).sum().clip(lower=1)

    avg_vol_up = vol_sum_up / days_up
    avg_vol_dn = vol_sum_dn / days_dn
    ar = (avg_vol_up / avg_vol_dn.replace(0, np.nan)).fillna(1.0)

    aps = ((ar - _AR_LOW) / (_AR_HIGH - _AR_LOW) * 100).clip(0, 100)

    # ── BaseQuality Score (BQS) ──────────────────────────────────────────────
    high_20d = close.rolling(20).max()
    low_20d  = close.rolling(20).min()
    base_range_pct = (high_20d - low_20d) / low_20d.replace(0, np.nan) * 100

    # Tight range → high score; extended range → low score
    tightness = (1.0 - base_range_pct / _RANGE_MAX_PCT).clip(0, 1) * 100

    # ATH proximity bonus: stock within 5% of 252-day high adds up to 20pts
    high_252d = close.rolling(min(252, len(close))).max()
    ath_proximity = (close / high_252d.replace(0, np.nan)).clip(0, 1)
    ath_bonus = ((ath_proximity - 0.90) / 0.10).clip(0, 1) * 20  # 0 if <90% of ATH, 20 if at ATH

    bqs = (tightness * 0.80 + ath_bonus).clip(0, 100)

    # ── Relative Momentum Score (RMS) ────────────────────────────────────────
    if len(close) >= 64:
        stock_ret_63 = close / close.shift(63) - 1.0
        # Normalise: outperforming Nifty by >30% → 100, underperforming by >30% → 0
        rms = ((stock_ret_63 - nifty_return_63d + 0.30) / 0.60 * 100).clip(0, 100)
    else:
        rms = pd.Series(50.0, index=close.index)

    # ── Composite IAS ────────────────────────────────────────────────────────
    ias = (w_aps * aps + w_bqs * bqs + w_rms * rms).clip(0, 100)

    # ── Breakout trigger ────────────────────────────────────────────────────
    # Close breaks above the highest close of the PREVIOUS 20 days,
    # confirmed by volume > 1.5× 20-day average.
    high_20d_lag = close.shift(1).rolling(20).max()
    vol_avg_20   = volume.rolling(20).mean()
    vol_ratio    = volume / vol_avg_20.replace(0, np.nan)
    breakout_series = (
        (close > high_20d_lag) & (vol_ratio >= _BREAKOUT_VOL_MULT)
    )

    last = -1
    return IASResult(
        ias=float(ias.iloc[last]),
        aps=float(aps.iloc[last]),
        bqs=float(bqs.iloc[last]),
        rms=float(rms.iloc[last]),
        ar=float(ar.iloc[last]),
        base_range_pct=float(base_range_pct.iloc[last]),
        breakout=bool(breakout_series.iloc[last]),
        close=float(close.iloc[last]),
        atr20=float(atr20.iloc[last]),
        ema20=float(ema20.iloc[last]),
        varb_spread=float(varb),
        vol_ratio=float(vol_ratio.iloc[last]),
    )


def classify_ias_signal(
    ias: float,
    breakout: bool,
    varb_spread: float,
    regime: str,
    ias_min: float = 65.0,
    ias_watch_min: float = 50.0,
    varb_max: float = 15.0,
) -> str:
    """
    BREAKOUT  — confirmed volume breakout with quality score
    BUILDING  — accumulation pattern forming, no breakout yet (monitor)
    AVOID     — poor quality or risk_off
    """
    if regime == "risk_off" or ias < ias_watch_min:
        return "AVOID"
    if ias >= ias_min and breakout and varb_spread <= varb_max:
        return "BREAKOUT"
    if ias >= ias_watch_min:
        return "BUILDING"
    return "AVOID"


def compute_exits(close: float, atr20: float, stop_atr: float = 1.0, target_atr: float = 2.0) -> dict:
    return {
        "entry":    round(close, 2),
        "stop":     round(close - stop_atr * atr20, 2),
        "target_1": round(close + target_atr * atr20, 2),
    }
