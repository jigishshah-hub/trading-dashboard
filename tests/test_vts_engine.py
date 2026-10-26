"""
Unit tests for vts_engine.py.

All tests use synthetic OHLCV with known properties so expected VTS values
can be verified by hand.
"""

import numpy as np
import pandas as pd
import pytest

from vts_engine import compute_vts, classify_signal, compute_exits


# ── Helpers ───────────────────────────────────────────────────────────────

def _flat_ohlcv(n: int = 60, price: float = 100.0, vol: int = 1_000_000) -> pd.DataFrame:
    """Flat price, constant volume — EMA slope ≈ 0, vol_ratio = 1.0."""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open":   price,
        "high":   price * 1.005,
        "low":    price * 0.995,
        "close":  price,
        "volume": float(vol),
    }, index=dates)


def _trending_ohlcv(n: int = 60, drift: float = 0.005) -> pd.DataFrame:
    """Steadily rising price with above-average volume — high VTS expected."""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = 100.0 * np.cumprod(1 + drift + np.random.default_rng(1).normal(0, 0.002, n))
    vol_base = 1_000_000
    volumes  = vol_base * np.random.default_rng(2).lognormal(0.7, 0.3, n)
    return pd.DataFrame({
        "open":   prices * 0.998,
        "high":   prices * 1.008,
        "low":    prices * 0.992,
        "close":  prices,
        "volume": volumes,
    }, index=dates)


def _falling_ohlcv(n: int = 60, drift: float = -0.004) -> pd.DataFrame:
    """Steadily falling price, low volume — low VTS expected."""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = 100.0 * np.cumprod(1 + drift + np.random.default_rng(3).normal(0, 0.002, n))
    volumes = 500_000 * np.ones(n)
    return pd.DataFrame({
        "open":   prices * 0.999,
        "high":   prices * 1.004,
        "low":    prices * 0.996,
        "close":  prices,
        "volume": volumes,
    }, index=dates)


# ── compute_vts ───────────────────────────────────────────────────────────

class TestComputeVTS:

    def test_returns_none_insufficient_data(self):
        df = _flat_ohlcv(n=20)
        assert compute_vts(df, india_vix=15.0) is None

    def test_flat_price_mid_score(self):
        df = _flat_ohlcv(n=60)
        r = compute_vts(df, india_vix=15.0)
        assert r is not None
        # Flat: slope ≈ 0 → ema_score ≈ 50; vol_ratio ≈ 1 → vol_score ≈ 20
        assert 20 <= r.vts <= 55, f"Expected mid VTS for flat price, got {r.vts}"

    def test_strong_uptrend_high_vts(self):
        df = _trending_ohlcv(n=60, drift=0.005)
        r = compute_vts(df, india_vix=15.0, w_ema=0.6)
        assert r is not None
        assert r.vts >= 60, f"Expected high VTS for strong uptrend, got {r.vts}"

    def test_downtrend_low_vts(self):
        df = _falling_ohlcv(n=60, drift=-0.004)
        r = compute_vts(df, india_vix=15.0, w_ema=0.6)
        assert r is not None
        assert r.vts <= 50, f"Expected low VTS for downtrend, got {r.vts}"

    def test_vts_bounded_0_100(self):
        for drift in [0.01, 0.005, 0.0, -0.005, -0.01]:
            df = _trending_ohlcv(n=60, drift=drift)
            r = compute_vts(df, india_vix=15.0)
            if r is not None:
                assert 0 <= r.vts <= 100, f"VTS out of range: {r.vts}"

    def test_varb_low_when_stock_calm(self):
        """Stock realvol < VIX → VARB spread is negative (ideal entry)."""
        df = _flat_ohlcv(n=60)
        r = compute_vts(df, india_vix=50.0)   # artificially high VIX
        assert r is not None
        assert r.varb_spread < 0, f"Expected negative spread, got {r.varb_spread}"

    def test_varb_high_when_stock_volatile(self):
        """Stock with high daily noise vs very low VIX → positive VARB spread."""
        # Realized vol = std(log_returns) * sqrt(252). Use high noise (3% daily
        # std → ~48% annualised) vs VIX=10 → spread ≈ +38, clearly positive.
        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        rng = np.random.default_rng(99)
        noise = rng.normal(0, 0.03, 60)   # 3% daily std
        prices = 100.0 * np.exp(np.cumsum(noise))
        df = pd.DataFrame({
            "open":   prices * 0.99,
            "high":   prices * 1.02,
            "low":    prices * 0.98,
            "close":  prices,
            "volume": 2_000_000.0,
        }, index=dates)
        r = compute_vts(df, india_vix=10.0)
        assert r is not None
        assert r.varb_spread > 0, f"Expected positive spread, got {r.varb_spread}"

    def test_date_column_accepted(self):
        """Accepts ohlcv with 'date' as a column instead of index."""
        df = _flat_ohlcv(n=60).reset_index().rename(columns={"index": "date"})
        r = compute_vts(df, india_vix=15.0)
        assert r is not None

    def test_ema_weight_changes_vts(self):
        df = _trending_ohlcv(n=60, drift=0.006)
        r_ema_heavy = compute_vts(df, india_vix=15.0, w_ema=0.8)
        r_vol_heavy = compute_vts(df, india_vix=15.0, w_ema=0.2)
        assert r_ema_heavy is not None and r_vol_heavy is not None
        # Different weights should give different scores
        assert r_ema_heavy.vts != r_vol_heavy.vts


# ── classify_signal ───────────────────────────────────────────────────────

class TestClassifySignal:

    @pytest.mark.parametrize("vts,varb,regime,expected", [
        (80, 3,  "risk_on",    "STRONG_BUY"),
        (80, 3,  "selective",  "BUY"),         # BUY in selective, not STRONG
        (75, 14, "risk_on",    "BUY"),          # varb > neutral_max
        (75, 20, "risk_on",    "WATCH"),        # varb > caution_max
        (65, 3,  "risk_on",    "WATCH"),        # vts < buy_min
        (50, 3,  "risk_on",    "AVOID"),        # vts < watch_min
        (90, 5,  "risk_off",   "AVOID"),        # regime kills signal
        (75, 5,  "risk_off",   "AVOID"),
    ])
    def test_signal_matrix(self, vts, varb, regime, expected):
        result = classify_signal(vts, varb, regime)
        assert result == expected, (
            f"VTS={vts} VARB={varb} regime={regime}: "
            f"expected {expected}, got {result}"
        )

    def test_custom_thresholds(self):
        r = classify_signal(65, 3, "risk_on", vts_buy_min=60, varb_neutral_max=10)
        assert r == "STRONG_BUY"


# ── compute_exits ─────────────────────────────────────────────────────────

class TestComputeExits:

    def test_stop_below_entry(self):
        e = compute_exits(close=100.0, atr20=2.0)
        assert e["stop"] < e["entry"]

    def test_target_above_entry(self):
        e = compute_exits(close=100.0, atr20=2.0)
        assert e["target_1"] > e["entry"]

    def test_rr_ratio_is_2(self):
        e = compute_exits(close=100.0, atr20=2.0)
        reward = e["target_1"] - e["entry"]
        risk   = e["entry"]    - e["stop"]
        assert abs(reward / risk - 2.0) < 0.001

    def test_values_within_01(self):
        """Values should match hand calculation within 0.1."""
        e = compute_exits(close=500.0, atr20=10.0)
        assert abs(e["stop"]     - 490.0) < 0.1
        assert abs(e["target_1"] - 520.0) < 0.1
        assert abs(e["risk_r"]   -  10.0) < 0.1
