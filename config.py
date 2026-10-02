"""
config.py — Single source of truth for all tunable thresholds.

Every magic number that defines the trading system's behaviour lives HERE and
is imported by streamlit_app.py, backtest_engine.py, and the test suite. Do not
hard-code these values anywhere else — if a threshold needs to change, change it
in this file only, and it propagates everywhere (dashboard, backtest, tests).

Pure data module: no streamlit / pandas / network imports, so it is safe to
import from anything (app, engine, CI tests).

Values below were migrated verbatim from streamlit_app.py on 2026-10-02
(step 1.1a) — this introduced NO behaviour change. Subsequent tuning (e.g. the
VIX-band reconciliation, step 1.3) happens by editing this file.
"""

# ─────────────────────────────────────────────────────────────────────────────
# FEAR GAUGE BANDS
# Each band list is ordered HIGH -> LOW. classify(value, bands) returns the
# label of the first band whose threshold the value meets or exceeds.
# The final (0, ...) entry is the catch-all floor.
# ─────────────────────────────────────────────────────────────────────────────

# India VIX (^INDIAVIX) — Nifty options implied volatility (local fear gauge).
# NOTE (step 1.3): these are the ORIGINAL bands; pending reconciliation with the
# stats-run update. Change here only.
VIX_BANDS = [
    (25, "extreme"),
    (20, "elevated"),
    (15, "watch"),
    (0,  "calm"),
]

# MOVE Index (^MOVE) — US Treasury implied volatility (global risk-off).
MOVE_BANDS = [
    (100, "extreme"),
    (80,  "elevated"),
    (70,  "moderate"),
    (0,   "calm"),
]

# Credit stress — LQD/HYG ratio expressed as a 6-month percentile (0-100).
CREDIT_STRESS_BANDS = [
    (80, "elevated"),
    (60, "watch"),
    (0,  "calm"),
]

# Which gauge statuses count as "confirming" a fear signal (signals_on tally).
FEAR_CONFIRMING_STATUSES = ("elevated", "extreme")


def classify(value, bands):
    """Return the label of the first band whose threshold `value` meets.

    `bands` is an ordered HIGH->LOW list of (threshold, label). Returns None if
    value is None. The (0, label) floor guarantees a match for any real number.
    """
    if value is None:
        return None
    for thr, label in bands:
        if value >= thr:
            return label
    return bands[-1][1]


# ─────────────────────────────────────────────────────────────────────────────
# FEAR MULTIPLIER MODEL (deployment sizing response to confirmed fear signals)
# 1 of 3 signals -> 1.5x tranche size; 2+ of 3 -> 2x tranche size and the next
# tier fires EARLY_FIRE_PCT earlier (e.g. T2 at -9% instead of -10%).
# ─────────────────────────────────────────────────────────────────────────────
FEAR_MULTIPLIERS = {
    0: 1.0,
    1: 1.5,
    2: 2.0,
    3: 2.0,
}
FEAR_EARLY_FIRE_PCT = 1.0        # tiers fire this many % earlier when 2+ signals on
FEAR_EARLY_FIRE_MIN_SIGNALS = 2  # min confirmed signals to trigger early fire

# Thresholds that mark a single fear signal "on" in the BACKTEST (historical,
# India VIX + MOVE only; credit-stress gauge is live-only).
FEAR_SIGNAL_VIX_MIN = 20
FEAR_SIGNAL_MOVE_MIN = 80


# ─────────────────────────────────────────────────────────────────────────────
# TACTICAL DEPLOYMENT LADDER (dual condition: Nifty vs 200 EMA + breadth filter)
# threshold = % distance below 200 EMA; breadth_max = P&F 1% breadth ceiling;
# deploy_pct = tranche size as % of tactical reserve.
# ─────────────────────────────────────────────────────────────────────────────
LADDER_TIERS = [
    {"tier": 1, "threshold":  -8.0, "breadth_max": 40, "deploy_pct": 8, "label": "Tier 1 — Light correction"},
    {"tier": 2, "threshold": -10.0, "breadth_max": 35, "deploy_pct": 8, "label": "Tier 2 — Moderate correction"},
    {"tier": 3, "threshold": -15.0, "breadth_max": 30, "deploy_pct": 8, "label": "Tier 3 — Deep correction"},
    {"tier": 4, "threshold": -20.0, "breadth_max": 25, "deploy_pct": 8, "label": "Tier 4 — Severe correction"},
    {"tier": 5, "threshold": -25.0, "breadth_max": 20, "deploy_pct": 8, "label": "Tier 5 — Panic lows"},
]

# Regime labels above the EMA (profit-booking side).
# pct = % distance ABOVE 200 EMA that arms each harvest tier.
HARVEST_TIERS = [
    {"id": "H4", "pct": 20, "action": "Book 75%", "note": "euphoria zone"},
    {"id": "H3", "pct": 17, "action": "Book 50%", "note": "90th pctile"},
    {"id": "H2", "pct": 12, "action": "Book 25%", "note": "75th pctile"},
    {"id": "H1", "pct": 5,  "action": "Trail 7%", "note": "set stops"},
]
# Harvest reference lines drawn on the Nifty chart (pct above EMA, label).
HARVEST_LINES = [(5, "H1"), (10, "H2"), (15, "H3"), (20, "H4")]

# Overextended / extended regime triggers (above EMA).
OVEREXTENDED_EMA_PCT = 20     # >= this % above EMA ...
OVEREXTENDED_BREADTH = 80     # ... AND breadth >= this -> OVEREXTENDED / harvest
EXTENDED_EMA_PCT = 15         # >= this % above EMA -> EXTENDED (approaching harvest)
CAUTION_EMA_PCT = -5          # <= this % below EMA with breadth not confirmed -> CAUTION
CAUTION_BREADTH_CEIL = 40     # breadth above this = "not confirmed" for a dip


# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL ARCHITECTURE (static allocation blocks)
# ─────────────────────────────────────────────────────────────────────────────
CAPITAL_ARCH = [
    {"name": "Core NiftyBees", "pct": 35, "desc": "Large-cap anchor"},
    {"name": "Core Nifty Midcap 150", "pct": 25, "desc": "Growth anchor"},
    {"name": "Tactical Cash Reserve", "pct": 40, "desc": "Liquid/arb funds ~6.5% p.a."},
]


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST VIX GATE (V2 model — require a minimum VIX before a tier deploys)
# T1/T2 need VIX >= T1_T2_MIN; T3 and deeper need VIX >= T3PLUS_MIN.
# ─────────────────────────────────────────────────────────────────────────────
BACKTEST_VIX_GATE_T1_T2_MIN = 20
BACKTEST_VIX_GATE_T3PLUS_MIN = 25
