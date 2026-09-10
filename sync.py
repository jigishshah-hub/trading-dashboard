"""
Trade Capture Log -> Supabase sync.

Reads the "Trades" and "Lots" tabs from the Google Sheet and upserts
them into the matching Postgres tables. Designed to be run on a
schedule by GitHub Actions (see .github/workflows/sync.yml), but can
also be run manually: `python sync.py`

Credentials come from environment variables (set as GitHub Actions
secrets, never hardcoded here):
  GOOGLE_SERVICE_ACCOUNT_KEY  - full contents of the service account JSON key
  SUPABASE_SERVICE_ROLE_KEY  - Supabase's service_role / secret key

Note: research_log is NOT touched by this script. That table stays
manually curated (via chat) since the Sheet has no equivalent tab for
it yet. Everything else (stocks, position_monitoring,
position_monitoring_conditions, lots, trade_history) is derived
directly from the Trades and Lots tabs each run.
"""

import os
import json

import gspread
from google.oauth2.service_account import Credentials
from supabase import create_client

# Opening by ID (not by name) deliberately avoids needing Google Drive
# API access — gc.open("name") requires Drive scope to search for the
# file by title, but gc.open_by_key(id) only needs the Sheets scope
# already granted below.
SHEET_ID = "1XK8nj3wDn1FlMNXluqR1Nyq16FtFb-7CTV88GC6gW4o"
SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"
EXAMPLE_TICKER = "EXAMPLE"  # skip the template example row


def clean(value):
    """Google Sheets returns blank cells as "" - normalize to None."""
    if value == "" or value is None:
        return None
    return value


def clean_percent(value):
    """Percent-formatted cells (e.g. "11.90%") come back as text from
    Sheets - strip the % and convert to a plain number for Postgres."""
    value = clean(value)
    if value is None:
        return None
    if isinstance(value, str) and value.endswith("%"):
        try:
            return float(value[:-1])
        except ValueError:
            return None
    return value


def get_sheet_client():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)


def get_supabase_client():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)


def read_sheet_data(gc):
    sh = gc.open_by_key(SHEET_ID)
    trades = sh.worksheet("Trades").get_all_records()
    lots = sh.worksheet("Lots").get_all_records()
    return trades, lots


def sync_stocks(sb, trades):
    for row in trades:
        ticker = clean(row.get("ticker"))
        if not ticker or ticker == EXAMPLE_TICKER:
            continue
        sb.table("stocks").upsert(
            {
                "ticker": ticker,
                "sleeve": clean(row.get("sleeve")),
                "status": clean(row.get("status")) or "idea",
            }
        ).execute()


def sync_position_monitoring(sb, trades):
    for row in trades:
        trade_id = clean(row.get("trade_id"))
        ticker = clean(row.get("ticker"))
        if not trade_id or not ticker or ticker == EXAMPLE_TICKER:
            continue
        sb.table("position_monitoring").upsert(
            {
                "trade_id": trade_id,
                "ticker": ticker,
                "direction": clean(row.get("direction")),
                "setup": clean(row.get("setup")),
                "entry_price": clean(row.get("entry_price")),
                "quantity": clean(row.get("quantity")),
                "stop_loss": clean(row.get("stop_loss")),
                "review_date": clean(row.get("review_date")),
                "status": clean(row.get("status")) or "active",
                "sleeve": clean(row.get("sleeve")),
                "position_size_pct": clean_percent(row.get("position_size_pct")),
            }
        ).execute()


def sync_conditions(sb, trades):
    for row in trades:
        trade_id = clean(row.get("trade_id"))
        ticker = clean(row.get("ticker"))
        if not trade_id or not ticker or ticker == EXAMPLE_TICKER:
            continue

        # Simplest correct approach: clear this trade's conditions and
        # re-add from the current sheet state, rather than trying to
        # diff/match existing rows.
        sb.table("position_monitoring_conditions").delete().eq(
            "trade_id", trade_id
        ).execute()

        target_1 = clean(row.get("target_1"))
        target_2 = clean(row.get("target_2"))
        trailing_note = clean(row.get("trailing_stop_note"))

        if target_1:
            sb.table("position_monitoring_conditions").insert(
                {
                    "trade_id": trade_id,
                    "condition_type": "target",
                    "description": str(target_1),
                }
            ).execute()
        if target_2:
            sb.table("position_monitoring_conditions").insert(
                {
                    "trade_id": trade_id,
                    "condition_type": "target",
                    "description": str(target_2),
                }
            ).execute()
        if trailing_note:
            sb.table("position_monitoring_conditions").insert(
                {
                    "trade_id": trade_id,
                    "condition_type": "trailing_stop",
                    "description": trailing_note,
                }
            ).execute()


def sync_lots(sb, lots):
    for row in lots:
        lot_id = clean(row.get("lot_id"))
        trade_id = clean(row.get("trade_id"))
        ticker = clean(row.get("ticker"))
        if not lot_id or not trade_id or not ticker or ticker == EXAMPLE_TICKER:
            continue
        sb.table("lots").upsert(
            {
                "lot_id": lot_id,
                "trade_id": trade_id,
                "ticker": ticker,
                "lot_date": clean(row.get("lot_date")),
                "qty": clean(row.get("qty")),
                "price": clean(row.get("price")),
                "notes": clean(row.get("notes")),
            }
        ).execute()


def sync_trade_history(sb, trades):
    for row in trades:
        if clean(row.get("status")) != "exited":
            continue
        trade_id = clean(row.get("trade_id"))
        ticker = clean(row.get("ticker"))
        if not trade_id or not ticker:
            continue
        sb.table("trade_history").upsert(
            {
                "trade_id": trade_id,
                "ticker": ticker,
                "sleeve": clean(row.get("sleeve")),
                "entry_price": clean(row.get("entry_price")),
                "exit_date": clean(row.get("exit_date")),
                "exit_price": clean(row.get("exit_price")),
                "exit_reason": clean(row.get("exit_reason")),
            }
        ).execute()


def main():
    gc = get_sheet_client()
    sb = get_supabase_client()

    trades, lots = read_sheet_data(gc)

    sync_stocks(sb, trades)
    sync_position_monitoring(sb, trades)
    sync_conditions(sb, trades)
    sync_lots(sb, lots)
    sync_trade_history(sb, trades)

    print(f"Synced {len(trades)} trade rows and {len(lots)} lot rows.")


if __name__ == "__main__":
    main()
