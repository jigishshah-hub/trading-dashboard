"""
Index History: Yahoo Finance -> Supabase (index_history).

Backfills and maintains daily closes for the index and volatility series the
Tactical Ladder backtest runs on. The Streamlit container has no market-data
egress, so history lives in Supabase and the backtest reads it from there
instead of re-downloading 11 years on every app load.

Two modes:
  1. DAILY (default): pulls the trailing ~10 sessions and upserts. Designed to
     run via GitHub Actions after NSE close, alongside snapshot_prices.py.
  2. BACKFILL: pulls full available history per symbol —
     run once via `python index_sync.py --backfill`

Credentials:
  SUPABASE_SERVICE_ROLE_KEY  — Supabase service_role key
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from supabase import create_client

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Symbols the backtest needs.
#   ^NSEI      Nifty 50 — drives the 200 EMA distance (the ladder's primary gate)
#   ^CRSMID    Nifty Midcap 150 — the asset tactical cash deploys into
#   ^INDIAVIX  India VIX — fear gauge 2
#   ^MOVE      ICE BofA MOVE index — fear gauge 1
# Credit stress (LQD/HYG) is derived in the app, not stored here.
SYMBOLS = ["^NSEI", "^CRSMID", "^INDIAVIX", "^MOVE"]

# Backfill floor. Breadth history in Supabase starts 2015-06-01; pulling a
# little earlier lets the 200 EMA warm up before the first backtested day.
BACKFILL_START = "2014-01-01"

CHUNK = 500


def get_supabase_client():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        sys.exit("SUPABASE_SERVICE_ROLE_KEY is not set")
    return create_client(SUPABASE_URL, key)


def fetch(symbol: str, start: str | None, period: str | None) -> pd.DataFrame:
    """Download one symbol. Returns an empty frame rather than raising, so one
    dead symbol cannot abort the whole run."""
    try:
        t = yf.Ticker(symbol)
        df = t.history(start=start) if start else t.history(period=period)
    except Exception as e:  # noqa: BLE001 — log and continue
        print(f"  {symbol}: fetch failed — {type(e).__name__}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        print(f"  {symbol}: no data returned")
        return pd.DataFrame()

    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["Close"])
    return df


def rows_for(symbol: str, df: pd.DataFrame) -> list[dict]:
    out = []
    for ts, r in df.iterrows():
        out.append({
            "symbol": symbol,
            "bar_date": ts.date().isoformat(),
            "close": round(float(r["Close"]), 4),
            "open_price": None if pd.isna(r.get("Open")) else round(float(r["Open"]), 4),
            "high_price": None if pd.isna(r.get("High")) else round(float(r["High"]), 4),
            "low_price": None if pd.isna(r.get("Low")) else round(float(r["Low"]), 4),
            "source": "yfinance",
        })
    return out


def upsert(sb, rows: list[dict]) -> int:
    """Upsert on (symbol, bar_date) so re-runs correct prior values in place."""
    written = 0
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        sb.table("index_history").upsert(batch, on_conflict="symbol,bar_date").execute()
        written += len(batch)
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="pull full history instead of the trailing window")
    args = ap.parse_args()

    sb = get_supabase_client()
    mode = "BACKFILL" if args.backfill else "DAILY"
    print(f"index_sync [{mode}] {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")

    total, failed = 0, []
    for sym in SYMBOLS:
        if args.backfill:
            df = fetch(sym, start=BACKFILL_START, period=None)
        else:
            df = fetch(sym, start=None, period="1mo")

        if df.empty:
            failed.append(sym)
            continue

        rows = rows_for(sym, df)
        n = upsert(sb, rows)
        total += n
        print(f"  {sym}: {n} rows  ({df.index[0].date()} -> {df.index[-1].date()})")

    print(f"done — {total} rows upserted")
    if failed:
        # Surface partial failure to the workflow without losing what did land.
        sys.exit(f"symbols with no data: {', '.join(failed)}")


if __name__ == "__main__":
    main()
