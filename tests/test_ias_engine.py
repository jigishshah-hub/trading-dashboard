"""Tests for ias_engine.py"""

import numpy as np
import pandas as pd
import pytest

from ias_engine import compute_ias, classify_ias_signal


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_ohlcv(
    n: int = 200,
    trend: float = 0.0005,
    vol_on_up: float = 1.0,
    vol_on_dn: float = 1.0,
    tight_pct: float = 0.008,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV with controllable accumulation pattern."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    close = 1000.0
    closes, highs, lows, volumes = [], [], [], []
    for _ in range(n):
        ret = rng.normal(trend, tight_pct)
        close *= (1 + ret)
        highs.append(close * (1 + abs(rng.normal(0, 0.003))))
        lows.append(close * (1 - abs(rng.normal(0, 0.003))))
        closes.append(close)
        # Volume higher on up days when vol_on_up > 1
        is_up = ret > 0
        vol = rng.lognormal(mean=np.log(1e6), sigma=0.3)
        vol *= vol_on_up if is_up else vol_on_dn
        volumes.append(vol)
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def make_breakout_ohlcv(n: int = 200) -> pd.DataFrame:
    """Last bar breaks above 20-day high with 2× volume."""
    df = make_ohlcv(n=n, trend=0.0, tight_pct=0.005)
    # Set last close well above 20-day high
    prev_max = df["close"].iloc[-21:-1].max()
    df.loc[df.index[-1], "close"] = prev_max * 1.02
    df.loc[df.index[-1], "high"]  = prev_max * 1.025
    # Set last volume to 2× average
    avg_vol = df["volume"].iloc[-21:-1].mean()
    df.loc[df.index[-1], "volume"] = avg_vol * 2.5
    return df


# ── Basic sanity ─────────────────────────────────────────────────────────────

def test_returns_result_with_sufficient_data():
    df = make_ohlcv(n=200)
    r = compute_ias(df, india_vix=15.0)
    assert r is not None
    assert 0 <= r.ias <= 100
    assert 0 <= r.aps <= 100
    assert 0 <= r.bqs <= 100
    assert 0 <= r.rms <= 100


def test_returns_none_insufficient_data():
    df = make_ohlcv(n=50)
    assert compute_ias(df, india_vix=15.0) is None


def test_all_scores_in_range():
    df = make_ohlcv(n=200)
    r = compute_ias(df, india_vix=15.0)
    for score in [r.ias, r.aps, r.bqs, r.rms]:
        assert 0 <= score <= 100, f"Score out of range: {score}"


# ── AccumulationPattern component ────────────────────────────────────────────

def test_high_up_volume_boosts_aps():
    """Stocks where up-day volume >> down-day volume score higher on APS."""
    df_accum = make_ohlcv(n=200, vol_on_up=3.0, vol_on_dn=1.0, seed=1)
    df_distrib = make_ohlcv(n=200, vol_on_up=1.0, vol_on_dn=3.0, seed=1)
    r_accum  = compute_ias(df_accum,  india_vix=15.0)
    r_distrib = compute_ias(df_distrib, india_vix=15.0)
    assert r_accum.aps > r_distrib.aps, (
        f"Accumulation APS={r_accum.aps:.1f} should exceed distribution APS={r_distrib.aps:.1f}"
    )


def test_balanced_volume_gives_middling_aps():
    """Equal up/down volume → AR ~1.0 → APS near midpoint."""
    df = make_ohlcv(n=200, vol_on_up=1.0, vol_on_dn=1.0, seed=7)
    r = compute_ias(df, india_vix=15.0)
    assert r.aps < 60, f"Balanced volume APS should be moderate, got {r.aps:.1f}"


# ── BaseQuality component ────────────────────────────────────────────────────

def test_tight_range_gives_high_bqs():
    """Low-volatility consolidation should produce high BQS."""
    df = make_ohlcv(n=200, tight_pct=0.003, trend=0.0)
    r = compute_ias(df, india_vix=15.0)
    assert r.bqs > 40, f"Tight base BQS should be elevated, got {r.bqs:.1f}"


def test_wide_range_gives_low_bqs():
    """High-volatility chop should produce low BQS."""
    df = make_ohlcv(n=200, tight_pct=0.025, trend=0.0)
    r = compute_ias(df, india_vix=15.0)
    assert r.bqs < 50, f"Wide range BQS should be low, got {r.bqs:.1f}"


# ── RelativeMomentum component ───────────────────────────────────────────────

def test_outperforming_nifty_boosts_rms():
    df = make_ohlcv(n=200, trend=0.002, seed=3)
    r_out = compute_ias(df, india_vix=15.0, nifty_return_63d=-0.05)
    r_under = compute_ias(df, india_vix=15.0, nifty_return_63d=0.25)
    assert r_out.rms > r_under.rms


# ── Breakout trigger ─────────────────────────────────────────────────────────

def test_breakout_detected_on_high_vol_close_above_20d_high():
    df = make_breakout_ohlcv(n=200)
    r = compute_ias(df, india_vix=15.0)
    assert r.breakout is True, "Volume-confirmed close above 20d high should set breakout=True"


def test_no_breakout_without_volume():
    df = make_ohlcv(n=200, trend=0.0005)
    # Force close above 20d high but DO NOT increase volume
    prev_max = df["close"].iloc[-21:-1].max()
    df.loc[df.index[-1], "close"] = prev_max * 1.02
    df.loc[df.index[-1], "high"]  = prev_max * 1.025
    # Volume stays at normal level
    r = compute_ias(df, india_vix=15.0)
    assert r.breakout is False, "Breakout without volume confirmation should be False"


def test_no_breakout_with_volume_but_no_price_break():
    df = make_ohlcv(n=200, trend=0.0)
    # Pump volume but price stays within range
    avg_vol = df["volume"].mean()
    df.loc[df.index[-1], "volume"] = avg_vol * 3.0
    r = compute_ias(df, india_vix=15.0)
    assert r.breakout is False, "Volume without price breakout should be False"


# ── IAS composite weights ────────────────────────────────────────────────────

def test_weights_sum_to_one():
    assert abs(0.40 + 0.35 + 0.25 - 1.0) < 1e-9


def test_custom_weights_respected():
    df = make_ohlcv(n=200, seed=5)
    r1 = compute_ias(df, india_vix=15.0, w_aps=1.0, w_bqs=0.0, w_rms=0.0)
    r2 = compute_ias(df, india_vix=15.0, w_aps=0.0, w_bqs=1.0, w_rms=0.0)
    assert abs(r1.ias - r1.aps) < 0.01
    assert abs(r2.ias - r2.bqs) < 0.01


# ── classify_ias_signal ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ias,breakout,varb,regime,expected", [
    (70, True,  5,  "risk_on",   "BREAKOUT"),
    (70, True,  5,  "selective", "BREAKOUT"),
    (70, False, 5,  "risk_on",   "BUILDING"),  # quality but no breakout
    (60, True,  5,  "risk_on",   "BUILDING"),  # below ias_min but above watch
    (70, True,  20, "risk_on",   "BUILDING"),  # varb too high
    (45, True,  5,  "risk_on",   "AVOID"),     # below watch min
    (90, True,  5,  "risk_off",  "AVOID"),     # regime_off gates everything
])
def test_classify_signal_matrix(ias, breakout, varb, regime, expected):
    result = classify_ias_signal(ias, breakout, varb, regime)
    assert result == expected, f"ias={ias} breakout={breakout} varb={varb} regime={regime}: expected {expected}, got {result}"
