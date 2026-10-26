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

def _flat_ohlcv(n: int = 200, price: float = 100.0, vol: int = 1_000_000) -> pd.DataFrame:
    """Flat price, constant volume — EMA slope ≈ 0, vol_ratio = 1.0."""
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open":   price,
        "high":   price * 1.005,
        "low":    price * 0.995,
        "close":  price,
        "volume": float(vol),
    }, index=dates)


def _trending_ohlcv(n: int = 200, drift: float = 0.005) -> pd.DataFrame:
    """Steadily rising price with above-average volume — high VTS expected."""
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
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


def _falling_ohlcv(n: int = 200, drift: float = -0.004) -> pd.DataFrame:
    """Steadily falling price, low volume — low VTS expected."""
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
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
        """Fewer than 130 rows → None (needs 127 for 6m RS)."""
        df = _flat_ohlcv(n=50)
        assert compute_vts(df, india_vix=15.0) is None

    def test_flat_price_mid_score(self):
        """Flat price → RS≈50, slope≈50, setup variable — total in mid range."""
        df = _flat_ohlcv(n=200)
        r = compute_vts(df, india_vix=15.0)
        assert r is not None
        assert 20 <= r.vts <= 65, f"Expected mid VTS for flat price, got {r.vts}"

    def test_strong_uptrend_high_vts(self):
        """Strong uptrend: positive RS + rising EMA → high VTS."""
        df = _trending_ohlcv(n=200, drift=0.005)
        r = compute_vts(df, india_vix=15.0, nifty_return_126d=0.0)
        assert r is not None
        assert r.vts >= 60, f"Expected high VTS for strong uptrend, got {r.vts}"

    def test_downtrend_low_vts(self):
        """Downtrend: negative RS + falling EMA → low VTS."""
        df = _falling_ohlcv(n=200, drift=-0.004)
        r = compute_vts(df, india_vix=15.0, nifty_return_126d=0.0)
        assert r is not None
        assert r.vts <= 50, f"Expected low VTS for downtrend, got {r.vts}"

    def test_outperforming_nifty_boosts_vts(self):
        """Stock +30% while Nifty -10% → RS score high → VTS higher than if Nifty flat."""
        df = _trending_ohlcv(n=200, drift=0.003)
        r_vs_down_nifty = compute_vts(df, india_vix=15.0, nifty_return_126d=-0.10)
        r_vs_up_nifty   = compute_vts(df, india_vix=15.0, nifty_return_126d=+0.25)
        assert r_vs_down_nifty is not None and r_vs_up_nifty is not None
        assert r_vs_down_nifty.vts > r_vs_up_nifty.vts, (
            f"Outperforming Nifty should give higher VTS: "
            f"{r_vs_down_nifty.vts} vs {r_vs_up_nifty.vts}"
        )

    def test_atr_compression_near_1_for_tight_consolidation(self):
        """Tight consolidation (recent ATR well below 60d max) → atr_compression near 1."""
        dates = pd.date_range("2023-01-01", periods=200, freq="B")
        rng = np.random.default_rng(42)
        # First 140 days: high volatility (2% daily noise)
        noisy  = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 140)))
        # Last 60 days: very tight (0.1% daily noise, flat)
        tight  = noisy[-1] * np.exp(np.cumsum(rng.normal(0, 0.001, 60)))
        prices = np.concatenate([noisy, tight])
        df = pd.DataFrame({
            "open": prices * 0.999, "high": prices * 1.003,
            "low":  prices * 0.997, "close": prices,
            "volume": 1_000_000.0,
        }, index=dates)
        r = compute_vts(df, india_vix=15.0)
        assert r is not None
        assert r.atr_compression >= 0.5, (
            f"Tight consolidation should have high compression, got {r.atr_compression}"
        )

    def test_vts_bounded_0_100(self):
        for drift in [0.01, 0.005, 0.0, -0.005, -0.01]:
            df = _trending_ohlcv(n=200, drift=drift)
            r = compute_vts(df, india_vix=15.0)
            if r is not None:
                assert 0 <= r.vts <= 100, f"VTS out of range: {r.vts}"
                assert 0 <= r.rs_score    <= 100
                assert 0 <= r.slope_score <= 100
                assert 0 <= r.setup_score <= 100

    def test_varb_low_when_stock_calm(self):
        """Stock realvol < VIX → VARB spread is negative (ideal entry)."""
        df = _flat_ohlcv(n=200)
        r = compute_vts(df, india_vix=50.0)
        assert r is not None
        assert r.varb_spread < 0, f"Expected negative spread, got {r.varb_spread}"

    def test_varb_high_when_stock_volatile(self):
        """High daily noise vs very low VIX → positive VARB spread."""
        dates = pd.date_range("2023-01-01", periods=200, freq="B")
        rng = np.random.default_rng(99)
        noise  = rng.normal(0, 0.03, 200)   # 3% daily std → ~48% annualised
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
        df = _flat_ohlcv(n=200).reset_index().rename(columns={"index": "date"})
        r = compute_vts(df, india_vix=15.0)
        assert r is not None

    def test_weight_sum_independence(self):
        """Different w_ema values give different scores on the same trending data."""
        df = _trending_ohlcv(n=200, drift=0.006)
        r1 = compute_vts(df, india_vix=15.0, w_ema=0.70, w_rs=0.20, w_setup=0.10)
        r2 = compute_vts(df, india_vix=15.0, w_ema=0.10, w_rs=0.70, w_setup=0.20)
        assert r1 is not None and r2 is not None
        assert r1.vts != r2.vts

    def test_near_high_flag_set_when_price_near_60d_high(self):
        """Flat price is always at its 60-day high → near_high should be True."""
        df = _flat_ohlcv(n=200)
        r = compute_vts(df, india_vix=15.0)
        assert r is not None
        assert r.near_high is True

    def test_near_high_false_when_price_well_below_60d_high(self):
        """Strongly falling price: close far below its 60-day high → near_high=False."""
        df = _falling_ohlcv(n=200, drift=-0.008)
        r = compute_vts(df, india_vix=15.0)
        assert r is not None
        assert r.near_high is False


# ── classify_signal ───────────────────────────────────────────────────────

class TestClassifySignal:

    @pytest.mark.parametrize("vts,varb,regime,near_high,expected", [
        (80, 3,  "risk_on",   True,  "STRONG_BUY"),  # all conditions met
        (80, 3,  "risk_on",   False, "BUY"),          # near_high gate blocks STRONG_BUY
        (80, 3,  "selective", True,  "BUY"),           # regime=selective → no STRONG_BUY
        (75, 14, "risk_on",   True,  "BUY"),           # varb > neutral_max → BUY not STRONG
        (75, 20, "risk_on",   True,  "WATCH"),         # varb > caution_max → WATCH
        (65, 3,  "risk_on",   True,  "WATCH"),         # vts < buy_min
        (50, 3,  "risk_on",   True,  "AVOID"),         # vts < watch_min
        (90, 5,  "risk_off",  True,  "AVOID"),         # regime kills signal
        (75, 5,  "risk_off",  False, "AVOID"),
    ])
    def test_signal_matrix(self, vts, varb, regime, near_high, expected):
        result = classify_signal(vts, varb, regime, near_high=near_high)
        assert result == expected, (
            f"VTS={vts} VARB={varb} regime={regime} near_high={near_high}: "
            f"expected {expected}, got {result}"
        )

    def test_custom_thresholds(self):
        r = classify_signal(
            65, 3, "risk_on", near_high=True,
            vts_buy_min=60, varb_neutral_max=10
        )
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
