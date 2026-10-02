"""Sanity checks on config.py — the single source of truth for thresholds.
These guard against drift: bands must stay ordered, tiers monotonic, keys present.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as c


def _assert_band_descending(bands, name):
    thrs = [t for t, _ in bands]
    assert thrs == sorted(thrs, reverse=True), f"{name} thresholds must be HIGH->LOW"
    assert thrs[-1] == 0, f"{name} must end with a (0, ...) catch-all floor"
    labels = [l for _, l in bands]
    assert len(labels) == len(set(labels)), f"{name} labels must be unique"


def test_fear_bands_ordered():
    _assert_band_descending(c.VIX_BANDS, "VIX_BANDS")
    _assert_band_descending(c.MOVE_BANDS, "MOVE_BANDS")
    _assert_band_descending(c.CREDIT_STRESS_BANDS, "CREDIT_STRESS_BANDS")


def test_classify_behaviour():
    assert c.classify(31, c.VIX_BANDS) == "panic"
    assert c.classify(26, c.VIX_BANDS) == "stress"
    assert c.classify(22, c.VIX_BANDS) == "fear"
    assert c.classify(17, c.VIX_BANDS) == "elevated"
    assert c.classify(14, c.VIX_BANDS) == "normal"
    assert c.classify(12, c.VIX_BANDS) == "calm"
    assert c.classify(None, c.VIX_BANDS) is None


def test_ladder_tiers_monotonic():
    tiers = c.LADDER_TIERS
    assert len(tiers) == 5
    assert [t["tier"] for t in tiers] == [1, 2, 3, 4, 5]
    thr = [t["threshold"] for t in tiers]
    bmax = [t["breadth_max"] for t in tiers]
    assert thr == sorted(thr, reverse=True), "EMA thresholds must deepen each tier"
    assert bmax == sorted(bmax, reverse=True), "breadth ceilings must tighten each tier"
    assert all(t["deploy_pct"] > 0 for t in tiers)


def test_harvest_tiers_monotonic():
    pcts = [h["pct"] for h in c.HARVEST_TIERS]       # listed H4..H1
    assert pcts == sorted(pcts, reverse=True), "harvest pcts must be ordered high->low"
    line_pcts = [p for p, _ in c.HARVEST_LINES]
    assert line_pcts == sorted(line_pcts), "harvest lines must be ordered low->high"


def test_fear_multipliers():
    m = c.FEAR_MULTIPLIERS
    assert set(m.keys()) == {0, 1, 2, 3}
    vals = [m[k] for k in sorted(m)]
    assert vals == sorted(vals), "multipliers must be non-decreasing with signal count"
    assert m[0] == 1.0 and m[1] == 1.5 and m[2] == 2.0


def test_backtest_vix_gate():
    assert c.BACKTEST_VIX_GATE_T3PLUS_MIN >= c.BACKTEST_VIX_GATE_T1_T2_MIN


def test_capital_arch_sums_100():
    assert sum(a["pct"] for a in c.CAPITAL_ARCH) == 100
