"""Import guard — catch syntax/import breaks before deploy.
streamlit_app.py is intentionally NOT imported here (it executes the whole app,
hitting Supabase/yfinance). `compileall` in CI covers its syntax. backtest_engine
is imported if present (GitHub repo) and skipped if absent (partial local copy).
"""
import os, sys, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_config_imports():
    import config  # noqa: F401


def test_alert_check_imports():
    import alert_check  # noqa: F401


def test_backtest_engine_imports_if_present():
    import pytest
    if importlib.util.find_spec("backtest_engine") is None:
        pytest.skip("backtest_engine.py not in this checkout")
    import backtest_engine  # noqa: F401
