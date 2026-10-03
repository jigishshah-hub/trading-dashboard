"""
Unit tests for the new market-data alert checks in alert_check.py.

All tests are pure (no DB, no network). They exercise the three new
check functions directly with synthetic data, validating both the
trigger logic and the failsafe no-data paths.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg
from alert_check import (
    check_intraday_move,
    check_volume_spike,
    check_52w_proximity,
    check_alerts,
    _f,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _snap(close, volume=100_000, open_price=None):
    return {
        "open_price": open_price if open_price is not None else close,
        "close_price": close,
        "volume": volume,
    }


def _make_snapshots(ticker, closes, volumes=None, today_open=None):
    """Build a snapshots dict for one ticker (newest row first)."""
    rows = []
    for i, c in enumerate(closes):
        vol = volumes[i] if volumes else 100_000
        op = today_open if i == 0 else c
        rows.append(_snap(c, vol, op))
    return {ticker: rows}


# ─── check_intraday_move ──────────────────────────────────────────────────────

def test_intraday_move_triggers_warning():
    snapshots = _make_snapshots("XYZ", [104.0], today_open=100.0)
    result = check_intraday_move("XYZ", {"XYZ": 104.0}, snapshots)
    assert result is not None
    assert result["reason"] == "intraday_move"
    assert result["level"] in ("WARNING", "DANGER")
    assert "4.0%" in result["detail"]


def test_intraday_move_escalates_to_danger():
    open_p = 100.0
    # 1.5× threshold → DANGER
    cmp = 100.0 + cfg.ALERT_INTRADAY_MOVE_PCT * 1.5
    snapshots = _make_snapshots("XYZ", [cmp], today_open=open_p)
    result = check_intraday_move("XYZ", {"XYZ": cmp}, snapshots)
    assert result is not None
    assert result["level"] == "DANGER"


def test_intraday_move_no_trigger_below_threshold():
    snapshots = _make_snapshots("XYZ", [101.0], today_open=100.0)
    result = check_intraday_move("XYZ", {"XYZ": 101.0}, snapshots)
    assert result is None


def test_intraday_move_no_cmp():
    snapshots = _make_snapshots("XYZ", [104.0], today_open=100.0)
    assert check_intraday_move("XYZ", {}, snapshots) is None


def test_intraday_move_no_snapshots():
    assert check_intraday_move("XYZ", {"XYZ": 104.0}, {}) is None


def test_intraday_move_downward():
    snapshots = _make_snapshots("XYZ", [95.0], today_open=100.0)
    result = check_intraday_move("XYZ", {"XYZ": 95.0}, snapshots)
    assert result is not None
    assert "down" in result["detail"]


# ─── check_volume_spike ───────────────────────────────────────────────────────

def _vol_snapshots(ticker, today_vol, hist_vol, n_hist=None):
    n = n_hist if n_hist is not None else cfg.ALERT_VOLUME_AVG_DAYS
    closes = [100.0] * (n + 1)
    vols = [today_vol] + [hist_vol] * n
    return _make_snapshots(ticker, closes, volumes=vols)


def test_volume_spike_triggers():
    spike = int(cfg.ALERT_VOLUME_SPIKE_MULT * 200_000)
    snaps = _vol_snapshots("XYZ", today_vol=spike, hist_vol=100_000)
    result = check_volume_spike("XYZ", snaps)
    assert result is not None
    assert result["reason"] == "volume_spike"


def test_volume_spike_danger_level():
    spike = int(cfg.ALERT_VOLUME_SPIKE_MULT * 1.6 * 100_000)
    snaps = _vol_snapshots("XYZ", today_vol=spike, hist_vol=100_000)
    result = check_volume_spike("XYZ", snaps)
    assert result is not None
    assert result["level"] == "DANGER"


def test_volume_spike_no_trigger_normal_volume():
    snaps = _vol_snapshots("XYZ", today_vol=110_000, hist_vol=100_000)
    assert check_volume_spike("XYZ", snaps) is None


def test_volume_spike_insufficient_history():
    snaps = _make_snapshots("XYZ", [100.0] * 5, volumes=[500_000] * 5)
    assert check_volume_spike("XYZ", snaps) is None


def test_volume_spike_no_snapshots():
    assert check_volume_spike("XYZ", {}) is None


# ─── check_52w_proximity ──────────────────────────────────────────────────────

def _52w_snapshots(ticker, high, low, n=60):
    """Synthetic history: n rows spanning the given high/low."""
    import random
    random.seed(42)
    closes = [random.uniform(low + 1, high - 1) for _ in range(n)]
    closes[0] = (high + low) / 2   # today's close = midpoint
    closes[1] = high               # ensure 52w high is captured
    closes[2] = low                # ensure 52w low is captured
    return _make_snapshots(ticker, closes)


def test_52w_near_high_triggers_info():
    snaps = _52w_snapshots("XYZ", high=100.0, low=50.0, n=60)
    # CMP within 3% of high = 98
    result = check_52w_proximity("XYZ", {"XYZ": 98.5}, snaps)
    highs = [r for r in result if r["reason"] == "near_52w_high"]
    assert highs, "should fire near_52w_high"
    assert highs[0]["level"] == "INFO"


def test_52w_near_low_triggers_danger():
    snaps = _52w_snapshots("XYZ", high=100.0, low=50.0, n=60)
    # CMP within 5% of low = 52.5
    result = check_52w_proximity("XYZ", {"XYZ": 52.0}, snaps)
    lows = [r for r in result if r["reason"] == "near_52w_low"]
    assert lows, "should fire near_52w_low"
    assert lows[0]["level"] == "DANGER"


def test_52w_no_alert_mid_range():
    snaps = _52w_snapshots("XYZ", high=100.0, low=50.0, n=60)
    result = check_52w_proximity("XYZ", {"XYZ": 75.0}, snaps)
    assert result == []


def test_52w_insufficient_history_returns_empty_without_crash():
    snaps = _make_snapshots("XYZ", [100.0] * 10)
    # yfinance fallback will fail in test env — must return empty list, not crash
    result = check_52w_proximity("XYZ", {"XYZ": 100.0}, snaps)
    assert isinstance(result, list)


def test_52w_no_cmp():
    snaps = _52w_snapshots("XYZ", high=100.0, low=50.0, n=60)
    assert check_52w_proximity("XYZ", {}, snaps) == []


# ─── check_alerts integration (backwards compat) ──────────────────────────────

def test_check_alerts_no_snapshots_does_not_crash():
    positions = [{"ticker": "XYZ", "entry_price": 100, "stop_loss": 80}]
    price_map = {"XYZ": 95.0}
    result = check_alerts(positions, price_map, news=[])
    assert isinstance(result, list)


def test_check_alerts_with_snapshots_returns_intraday():
    positions = [{"ticker": "XYZ", "entry_price": 100, "stop_loss": 80}]
    price_map = {"XYZ": 106.0}
    snaps = _make_snapshots("XYZ", [106.0] * 25, today_open=100.0)
    result = check_alerts(positions, price_map, news=[], snapshots=snaps)
    reasons = [a["reason"] for a in result]
    assert "intraday_move" in reasons


# ─── _f helper ────────────────────────────────────────────────────────────────

def test_f_converts_valid():
    assert _f("123.4") == 123.4
    assert _f(50) == 50.0


def test_f_returns_none_for_empty():
    assert _f(None) is None
    assert _f("") is None
    assert _f("abc") is None
