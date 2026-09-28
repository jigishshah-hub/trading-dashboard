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
#   ^INDIAVIX  India VIX — fear gauge 2
#   ^MOVE      ICE BofA MOVE index — fear gauge 1
REQUIRED = ["^NSEI", "^INDIAVIX", "^MOVE"]

# The deploy asset. Yahoo has no ^CRSMID; the Nifty Midcap 150 index lists as
# NIFTYMIDCAP150.NS (confirmed by Jigish). The rest stay as fallbacks in case
# that symbol's history is shorter than the 2015 breadth series — the backtest
# picks the longest usable series and names it in its output.
#
# ^CNXMID removed 28 Sep 2026: Yahoo reports it delisted and it had been
# returning nothing (failing silently) on every run.
MIDCAP_CANDIDATES = [
    "NIFTYMIDCAP150.NS",  # Nifty Midcap 150 — preferred, matches the framework
    "^NSEMDCP50",         # Nifty Midcap 50 index
    "^NSMIDCP",           # alternate Yahoo code for the same
    "MID150BEES.NS",      # Nippon Midcap 150 ETF (short history)
    "MIDCAPETF.NS",
]

# Credit-stress inputs, stored (not derived live) so the backtest confirms fear
# on the full 3-of-3 (VIX + MOVE + LQD/HYG) rather than silently 2-of-3. The
# dashboard derives LQD/HYG live for the cockpit and never persisted it, so any
# historical backtest ran without the credit gauge. Storing both fixes that.
#   HYG  iShares High-Yield corporate bond ETF — first bar 2007-04-11
#   LQD  iShares Investment-Grade corporate bond ETF — first bar 2002-07-30
# The LQD/HYG ratio rising = investment grade beating junk = credit stress.
CREDIT_SYMBOLS = ["HYG", "LQD"]

# Simple symbols: fetched and upserted verbatim under their own Yahoo code.
SYMBOLS = REQUIRED + MIDCAP_CANDIDATES + CREDIT_SYMBOLS

# ── Synthetic INR gold ───────────────────────────────────────
# The tradeable gold instrument (GOLDBEES.NS) only lists from 2008-12-31 and so
# misses the entire 2008 crash. To give the gold sleeve a continuous history
# across the stress window we build a synthetic INR-gold level and splice the
# real ETF onto it:
#
#   before the splice : GC=F (COMEX gold, USD/oz) x USDINR=X  — INR/oz
#   from the splice    : GOLDBEES.NS close, rescaled by a single constant factor
#                        so the level is continuous at the splice date
#
# The rescale means post-splice *returns* are exactly the tradeable ETF's, and
# pre-splice returns are synthetic INR gold's; only the arbitrary absolute scale
# (INR/oz) carries across. Gold in INR rose sharply through 2008 partly on rupee
# weakness (USDINR ~39 → ~50), which is precisely the behaviour the stress test
# needs to capture and GOLDBEES alone cannot show.
GOLD_SYMBOL = "GOLD_INR_SYNTH"
GOLD_GC = "GC=F"           # COMEX gold future, USD/oz — covers 2000+
GOLD_FX = "USDINR=X"       # USD/INR — covers 2003+
GOLD_ETF = "GOLDBEES.NS"   # Nippon India Gold ETF, INR — the tradeable leg, 2008-12-31+
# Splice at GOLDBEES' first available bar; recorded in the report and in the
# rows' `source` field for auditability.
GOLD_SPLICE_ON = "2008-12-31"

# Backfill floor. Extended to 2007-01-01 (was 2014-01-01) so the run covers the
# 2008 crash. Yahoo's ^NSEI history begins 2007-09-17, so the 200 EMA does not
# warm until roughly mid-July 2008 — the first valid decision lands with the
# Nifty near 4,000, and the January 2008 peak (~6,357) is outside the testable
# window. See BACKTEST_SPEC_2008.md §4.
BACKFILL_START = "2007-01-01"

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


def splice_synthetic_gold(
    gc: dict, fx: dict, etf: dict, splice_on: str = GOLD_SPLICE_ON,
) -> dict:
    """Build a continuous INR-gold level from three date->close maps.

    Pure function (no network), so the splice arithmetic is unit-testable.

    - Pre-splice level  = GC=F (USD/oz) x USDINR=X, on dates present in both.
    - From the splice   = GOLDBEES.NS close x f, where f is chosen so the level
                          is continuous at the anchor: f = synth[anchor] /
                          etf[anchor]. The anchor is the first date on/after
                          `splice_on` present in BOTH the synthetic series and
                          the ETF. Post-anchor daily returns are therefore
                          exactly the ETF's; only the absolute INR/oz scale
                          carries across the join.

    Returns a {date: (close, leg)} map where `leg` is "synthetic" or "etf" so
    the caller can stamp provenance per row. If the ETF is unavailable the
    synthetic series is returned alone (every row tagged "synthetic").
    """
    synth = {d: gc[d] * fx[d] for d in (gc.keys() & fx.keys())
             if gc[d] is not None and fx[d] is not None and fx[d] > 0}
    splice_date = datetime.fromisoformat(splice_on).date()

    etf_on = {d: v for d, v in etf.items() if v is not None and v > 0}
    anchor_candidates = sorted(d for d in (synth.keys() & etf_on.keys())
                               if d >= splice_date)
    if not anchor_candidates:
        # No overlap to anchor on — cannot splice; keep the synthetic leg only.
        return {d: (synth[d], "synthetic") for d in synth}

    anchor = anchor_candidates[0]
    f = synth[anchor] / etf_on[anchor]

    out: dict = {}
    for d, v in synth.items():
        if d < anchor:
            out[d] = (v, "synthetic")
    for d, v in etf_on.items():
        if d >= anchor:
            out[d] = (v * f, "etf")
    return out


def gold_rows() -> list[dict]:
    """Download the three legs and build GOLD_INR_SYNTH rows.

    Returns [] (and logs) if the price legs are unavailable, so a gold outage
    cannot abort the whole index sync.
    """
    def closes(df: pd.DataFrame) -> dict:
        return {ts.date(): float(r["Close"]) for ts, r in df.iterrows()
                if not pd.isna(r["Close"])}

    gc_df = fetch(GOLD_GC, start=BACKFILL_START, period=None)
    fx_df = fetch(GOLD_FX, start=BACKFILL_START, period=None)
    etf_df = fetch(GOLD_ETF, start=BACKFILL_START, period=None)
    if gc_df.empty or fx_df.empty:
        print(f"  {GOLD_SYMBOL}: missing price leg "
              f"(GC=F empty={gc_df.empty}, USDINR=X empty={fx_df.empty}) — skipped")
        return []

    spliced = splice_synthetic_gold(closes(gc_df), closes(fx_df), closes(etf_df))
    if not spliced:
        print(f"  {GOLD_SYMBOL}: splice produced no rows — skipped")
        return []

    out = []
    for d in sorted(spliced):
        close, leg = spliced[d]
        out.append({
            "symbol": GOLD_SYMBOL,
            "bar_date": d.isoformat(),
            "close": round(close, 4),
            "open_price": None,
            "high_price": None,
            "low_price": None,
            # Provenance: which leg produced this bar, and the splice date.
            "source": f"synthetic:{leg}:GCxFX|GOLDBEES@{GOLD_SPLICE_ON}",
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

    # Synthetic INR gold is rebuilt in full on every run (both modes): the
    # splice's scale factor is anchored in 2008, so a trailing-window pull could
    # not reconstruct a continuous level. The three legs are cheap daily pulls
    # and the upsert is idempotent, so a full rebuild each day is the safe
    # choice over trying to append rescaled ETF bars incrementally.
    grows = gold_rows()
    if grows:
        n = upsert(sb, grows)
        total += n
        print(f"  {GOLD_SYMBOL}: {n} rows  ({grows[0]['bar_date']} -> {grows[-1]['bar_date']})")
    else:
        failed.append(GOLD_SYMBOL)

    print(f"done — {total} rows upserted")

    # Only a missing REQUIRED symbol is a failure. The midcap candidates are a
    # probe: several are expected to return nothing, and that must not fail the
    # run or mask the rows that did land.
    missing_required = [s for s in failed if s in REQUIRED]
    if failed:
        print(f"no data: {', '.join(failed)}")
    if missing_required:
        sys.exit(f"REQUIRED symbols returned no data: {', '.join(missing_required)}")


if __name__ == "__main__":
    main()
