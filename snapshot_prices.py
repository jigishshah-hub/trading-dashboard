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

import pandas as pd
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

    # yfinance >= 0.2.31 returns multi-level columns like ('Close', 'AAPL.NS')
    # for single-ticker downloads. Flatten to plain column names.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

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
    """Capture the latest closing data for all active positions.

    Fetches a short trailing window (not just today) because yfinance
    may not have published today's close yet right after market close,
    or "today" may be a non-trading day — but only the single most
    recent row from that window is ever upserted.

    This used to upsert every row in the window, using TODAY's current
    qty_open for all of them. Since this function runs daily and the
    window overlapped the last several calendar days every time, any
    partial exit would retroactively overwrite the prior days' stored
    position_value with the post-exit quantity — silently understating
    what was actually held on those earlier dates. Confirmed happening
    live for ASTRAMICRO (TR-0003) around its 2026-10-05/07 partial
    exits. Fixed 2026-10-08 — see the architecture doc in the Claude
    project for the incident. Only ever write the latest trading day
    from here; use --backfill (with its own date-range handling) for
    historical data.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    # Look back a few days to find the latest published close even
    # across a long weekend/holiday — but only the single most recent
    # row found is ever upserted (see docstring above).
    lookback = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

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

        rows = fetch_ohlcv(ticker, lookback, today)
        if rows:
            latest_rows = rows[-1:]  # only the most recent trading day —
                                      # never touch earlier dates from here
            n = upsert_snapshots(sb, ticker, trade_id, qty, latest_rows)
            latest = latest_rows[-1]
            print(f"  ✓ {ticker}: ₹{latest['close']:,.2f} (vol {latest['volume']:,}) — {n} rows")
            success += 1
        else:
            failed += 1

    print(f"\nDone. {success} succeeded, {failed} failed.")


def get_lots(sb):
    """Return all lots (trade_id, lot_type, qty, lot_date), for replaying
    qty_open day-by-day during backfill."""
    result = sb.table("lots").select("trade_id, lot_type, qty, lot_date").execute()
    return result.data or []


def qty_open_asof(lots_for_trade, date_str):
    """qty_open for one trade as of the close of `date_str` — every entry
    lot on or before that date adds its qty, every exit lot subtracts.
    `lots_for_trade` is this trade's lots, any order."""
    q = 0.0
    for lot in lots_for_trade:
        ld = lot.get("lot_date")
        if not ld or ld > date_str:
            continue
        qty = float(lot.get("qty") or 0)
        q += qty if lot.get("lot_type") == "entry" else -qty
    return q


def backfill(sb, positions):
    """Pull full history from each position's entry date to exit date (or today
    for active positions).  Exited positions should NOT have snapshots after
    their exit date — that was the root cause of incorrect drawdown numbers.

    Replays qty_open day-by-day from the `lots` table rather than applying
    one scalar qty across the whole range (fixed 2026-10-08 — the old
    scalar approach corrupted every date before a partial exit with the
    post-exit qty whenever backfill was run after that exit; confirmed on
    ACE/TR-0007's entire Sep 9–21 history, repaired via direct SQL — see
    architecture doc). Rows are grouped into contiguous same-qty chunks
    (one chunk per interval between lot events) so this still costs one
    Yahoo Finance fetch per ticker, not one per day.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if not positions:
        print("No positions found.")
        return

    # Build exit-date lookup from trade_history so exited positions
    # only get snapshots up to their exit date (not through today).
    exit_dates = {}
    try:
        th = sb.table("trade_history").select("trade_id, exit_date").execute()
        for row in (th.data or []):
            tid = row.get("trade_id")
            ed = row.get("exit_date")
            if tid and ed:
                exit_dates[tid] = ed
    except Exception as e:
        print(f"  ⚠ Could not load trade_history for exit dates: {e}")

    lots_by_trade = {}
    for lot in get_lots(sb):
        lots_by_trade.setdefault(lot.get("trade_id"), []).append(lot)

    print(f"Backfilling {len(positions)} positions to {today}")

    total_rows = 0

    for pos in positions:
        ticker = pos["ticker"]
        trade_id = pos["trade_id"]
        status = pos.get("status", "active")
        entry_date = pos.get("entry_date")

        if not entry_date:
            # No entry date — try 30 days back as fallback
            entry_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            print(f"  ⚠ {ticker}: no entry_date, using {entry_date}")

        # Exited positions: stop at exit date, not today
        end_date = today
        if status == "exited" and trade_id in exit_dates:
            end_date = exit_dates[trade_id]

        print(f"  → {ticker} ({trade_id}): {entry_date} to {end_date} ...", end=" ")

        rows = fetch_ohlcv(ticker, entry_date, end_date)
        if not rows:
            print("FAILED")
            continue

        trade_lots = lots_by_trade.get(trade_id, [])
        if not trade_lots:
            # No lots on record for this trade — can't replay qty_open.
            # Fall back to the old scalar behavior rather than silently
            # zeroing out this position's history.
            fallback_qty = float(pos.get("qty_open") or pos.get("quantity") or 0)
            if status == "exited" and fallback_qty == 0:
                fallback_qty = float(pos.get("quantity") or 0)
            n = upsert_snapshots(sb, ticker, trade_id, fallback_qty, rows)
            print(f"{n} rows (no lots found — used flat qty {fallback_qty:g})")
            total_rows += n
            continue

        n = 0
        # Group consecutive rows that share the same historical qty_open
        # into one upsert call each, instead of a separate call per day.
        chunk_qty = None
        chunk_rows = []
        for row in rows:
            q = qty_open_asof(trade_lots, row["date"])
            if chunk_qty is not None and q != chunk_qty and chunk_rows:
                n += upsert_snapshots(sb, ticker, trade_id, chunk_qty, chunk_rows)
                chunk_rows = []
            chunk_qty = q
            chunk_rows.append(row)
        if chunk_rows:
            n += upsert_snapshots(sb, ticker, trade_id, chunk_qty, chunk_rows)

        print(f"{n} rows")
        total_rows += n

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
