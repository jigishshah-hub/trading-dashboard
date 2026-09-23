"""
Daily Price Snapshot: Yahoo Finance -> Supabase (daily_snapshots).

Fetches OHLCV data for all active (and recently exited) positions and
upserts into daily_snapshots. This powers the equity curve with real
daily data points instead of lot-event dates only.

Two modes:
  1. TODAY (default): captures today's closing data — designed to run
     via GitHub Actions at 3:45 PM IST (after NSE close)
  2. BACKFILL: pulls full history from each position's entry date —
     run once via `python snapshot_prices.py --backfill`

Credentials:
  SUPABASE_SERVICE_ROLE_KEY  — Supabase service_role key

Yahoo Finance ticker convention for NSE: append ".NS"
  e.g. ASTRAMICRO -> ASTRAMICRO.NS
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import yfinance as yf
from supabase import create_client

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Override yfinance symbols when NSE ticker differs from Yahoo symbol.
# Most NSE tickers work as-is with .NS suffix.
YF_SYMBOL_OVERRIDES = {
    # "SOMETICKER": "ALTNAME.NS",
}


def get_supabase_client():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)


def yf_symbol(ticker: str) -> str:
    """Convert our ticker to a Yahoo Finance symbol."""
    if ticker in YF_SYMBOL_OVERRIDES:
        return YF_SYMBOL_OVERRIDES[ticker]
    return f"{ticker}.NS"


def get_positions(sb):
    """Return all positions (active + exited) with trade_id, ticker,
    entry_date, status, and qty_open."""
    result = (
        sb.table("position_monitoring")
        .select("trade_id, ticker, entry_date, status, qty_open, quantity")
        .execute()
    )
    return result.data or []


def fetch_ohlcv(ticker: str, start_date: str, end_date: str = None):
    """Fetch daily OHLCV from Yahoo Finance.

    Returns list of dicts with: date, open, high, low, close, volume.
    """
    symbol = yf_symbol(ticker)

    # yfinance end date is exclusive, so add 1 day
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_str = end_dt.strftime("%Y-%m-%d")
    else:
        end_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        data = yf.download(
            symbol,
            start=start_date,
            end=end_str,
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"  ✗ {ticker} ({symbol}): yfinance error — {e}")
        return []

    if data.empty:
        print(f"  ✗ {ticker} ({symbol}): no data returned")
        return []

    rows = []
    for idx, row in data.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        rows.append({
            "date": date_str,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_int(row.get("Volume")),
        })

    return rows


def _safe_float(v):
    try:
        val = float(v)
        if val != val:  # NaN check
            return None
        return round(val, 2)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        val = int(float(v))
        return val if val >= 0 else None
    except (TypeError, ValueError):
        return None


def upsert_snapshots(sb, ticker: str, trade_id: str, qty: float, rows: list):
    """Upsert OHLCV rows into daily_snapshots."""
    if not rows:
        return 0

    records = []
    for r in rows:
        close = r["close"]
        if close is None:
            continue
        position_value = round(close * qty, 2) if qty else None
        records.append({
            "ticker": ticker,
            "trade_id": trade_id,
            "snapshot_date": r["date"],
            "open_price": r["open"],
            "high_price": r["high"],
            "low_price": r["low"],
            "close_price": close,
            "volume": r["volume"],
            "portfolio_qty": qty,
            "position_value": position_value,
        })

    if not records:
        return 0

    # Upsert in batches of 100
    count = 0
    for i in range(0, len(records), 100):
        batch = records[i:i+100]
        sb.table("daily_snapshots").upsert(
            batch,
            on_conflict="ticker,snapshot_date",
        ).execute()
        count += len(batch)

    return count


def snapshot_today(sb, positions):
    """Capture today's closing data for all active positions."""
    today = datetime.now().strftime("%Y-%m-%d")
    # Fetch yesterday too in case market just closed and today isn't in yfinance yet
    yesterday = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    active = [p for p in positions if p.get("status") == "active"]
    if not active:
        print("No active positions — nothing to snapshot.")
        return

    print(f"Snapshotting {len(active)} active positions for {today}")

    success = 0
    failed = 0

    for pos in active:
        ticker = pos["ticker"]
        trade_id = pos["trade_id"]
        qty = float(pos.get("qty_open") or pos.get("quantity") or 0)

        rows = fetch_ohlcv(ticker, yesterday, today)
        if rows:
            n = upsert_snapshots(sb, ticker, trade_id, qty, rows)
            latest = rows[-1]
            print(f"  ✓ {ticker}: ₹{latest['close']:,.2f} (vol {latest['volume']:,}) — {n} rows")
            success += 1
        else:
            failed += 1

    print(f"\nDone. {success} succeeded, {failed} failed.")


def backfill(sb, positions):
    """Pull full history from each position's entry date to today."""
    today = datetime.now().strftime("%Y-%m-%d")

    if not positions:
        print("No positions found.")
        return

    print(f"Backfilling {len(positions)} positions to {today}")

    total_rows = 0

    for pos in positions:
        ticker = pos["ticker"]
        trade_id = pos["trade_id"]
        status = pos.get("status", "active")
        entry_date = pos.get("entry_date")
        qty = float(pos.get("qty_open") or pos.get("quantity") or 0)

        # For exited positions, qty_open is 0 — use original quantity for
        # historical snapshots (the position was held during that period)
        if status == "exited" and qty == 0:
            qty = float(pos.get("quantity") or 0)

        if not entry_date:
            # No entry date — try 30 days back as fallback
            entry_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            print(f"  ⚠ {ticker}: no entry_date, using {entry_date}")

        print(f"  → {ticker} ({trade_id}): {entry_date} to {today} ...", end=" ")

        rows = fetch_ohlcv(ticker, entry_date, today)
        if rows:
            n = upsert_snapshots(sb, ticker, trade_id, qty, rows)
            print(f"{n} rows")
            total_rows += n
        else:
            print("FAILED")

    print(f"\nBackfill complete. {total_rows} total rows inserted.")


def main():
    parser = argparse.ArgumentParser(description="Daily price snapshots for portfolio")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill from entry dates instead of today-only")
    args = parser.parse_args()

    sb = get_supabase_client()
    positions = get_positions(sb)

    if args.backfill:
        backfill(sb, positions)
    else:
        snapshot_today(sb, positions)


if __name__ == "__main__":
    main()
