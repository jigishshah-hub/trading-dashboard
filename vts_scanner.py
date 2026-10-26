"""
vts_scanner.py — Daily VTS scanner for Nifty 200.

Fetches 3 months of OHLCV via yfinance, computes VTS + VARB for every stock,
generates signals, and upserts results to Supabase `vts_scores`.

Designed to run after NSE market close via GitHub Actions (see vts_scan.yml).
Can also be run manually:

    SUPABASE_SERVICE_ROLE_KEY=... python vts_scanner.py
    SUPABASE_SERVICE_ROLE_KEY=... python vts_scanner.py --date 2026-10-25

Supabase setup (run once):
    CREATE TABLE vts_scores (
        id          bigserial PRIMARY KEY,
        scan_date   date        NOT NULL,
        symbol      text        NOT NULL,
        vts         numeric(6,2) NOT NULL,
        varb_spread numeric(6,2) NOT NULL,
        signal      text        NOT NULL,
        entry_price numeric(12,4),
        stop_price  numeric(12,4),
        target_1    numeric(12,4),
        atr20       numeric(12,4),
        ema20       numeric(12,4),
        vol_ratio   numeric(6,2),
        regime      text        NOT NULL,
        created_at  timestamptz DEFAULT now(),
        UNIQUE(scan_date, symbol)
    );
    CREATE INDEX ON vts_scores (scan_date DESC, vts DESC);
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from supabase import create_client

import config as cfg
from vts_engine import compute_vts, classify_signal, compute_exits
from vts_universe import yf_symbols, NIFTY_200

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"
FETCH_PERIOD = "3mo"      # enough for 60-day indicator lookback
NIFTY_TICKER = "^NSEI"
VIX_FILE     = "series/vix.txt"


# ── Data helpers ───────────────────────────────────────────────────────────

def load_vix() -> pd.Series:
    raw = Path(VIX_FILE).read_text().strip()
    rows = [r.split(",") for r in raw.split(";") if "," in r]
    return pd.Series(
        {pd.Timestamp(d): float(v) for d, v in rows}, dtype=float
    ).sort_index()


def fetch_bulk(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download OHLCV for all symbols in one yfinance call."""
    log.info("Downloading %d symbols from yfinance…", len(symbols))
    raw = yf.download(
        symbols,
        period=FETCH_PERIOD,
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            if len(symbols) == 1:
                df = raw.copy()
            else:
                df = raw[sym].copy()
            df = df.dropna(subset=["Close"])
            if len(df) < 40:
                log.debug("skip %s: only %d rows", sym, len(df))
                continue
            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index)
            result[sym] = df
        except (KeyError, TypeError):
            log.debug("skip %s: not in download result", sym)
    log.info("Loaded %d / %d symbols", len(result), len(symbols))
    return result


def fetch_nifty() -> pd.Series:
    df = yf.download(NIFTY_TICKER, period="2y", auto_adjust=True, progress=False)
    if df.empty:
        raise RuntimeError("Failed to fetch Nifty 50 data")
    s = df["Close"].squeeze()
    s.index = pd.to_datetime(s.index)
    return s.dropna()


def nifty_regime(nifty: pd.Series, vix: pd.Series, scan_date: pd.Timestamp) -> str:
    hist = nifty.loc[:scan_date].dropna()
    if len(hist) < 200:
        return "selective"
    ema200 = hist.ewm(span=200, adjust=False).mean().iloc[-1]
    price  = float(hist.iloc[-1])
    vix_val = float(vix.asof(scan_date) or 15.0)
    if pd.isna(vix_val):
        vix_val = 15.0
    if price < ema200 or vix_val >= 25:
        return "risk_off"
    if price > ema200 and vix_val < 20:
        return "risk_on"
    return "selective"


# ── Scanner core ───────────────────────────────────────────────────────────

def scan_universe(
    ohlcv: dict[str, pd.DataFrame],
    vix: pd.Series,
    nifty: pd.Series,
    scan_date: pd.Timestamp,
) -> list[dict]:
    regime = nifty_regime(nifty, vix, scan_date)
    log.info("Market regime on %s: %s", scan_date.date(), regime)

    vix_val = float(vix.asof(scan_date) or 15.0)
    if pd.isna(vix_val):
        vix_val = 15.0

    rows = []
    for yf_sym, df in ohlcv.items():
        nse_sym = yf_sym.replace(".NS", "")

        # Use data up to scan_date only (avoids lookahead)
        hist = df.loc[:scan_date]
        if len(hist) < 40:
            continue

        # 6-month Nifty return for relative strength calculation
        nifty_hist = nifty.loc[:scan_date].dropna()
        nifty_ret_126d = 0.0
        if len(nifty_hist) >= 127:
            nifty_ret_126d = float(
                nifty_hist.iloc[-1] / nifty_hist.iloc[-127] - 1.0
            )

        result = compute_vts(
            hist,
            india_vix=vix_val,
            nifty_return_126d=nifty_ret_126d,
            w_rs=cfg.VTS_W_RS,
            w_ema=cfg.VTS_W_EMA,
            w_setup=cfg.VTS_W_SETUP,
        )
        if result is None:
            continue

        signal = classify_signal(
            result.vts,
            result.varb_spread,
            regime,
            near_high=result.near_high,
            vts_buy_min=cfg.VTS_BUY_MIN,
            vts_watch_min=cfg.VTS_WATCH_MIN,
            varb_neutral_max=cfg.VARB_NEUTRAL_MAX,
            varb_caution_max=cfg.VARB_CAUTION_MAX,
        )

        exits = compute_exits(result.close, result.atr20)

        rows.append({
            "scan_date":       str(scan_date.date()),
            "symbol":          nse_sym,
            "vts":             result.vts,
            "rs_score":        result.rs_score,
            "slope_score":     result.slope_score,
            "setup_score":     result.setup_score,
            "varb_spread":     result.varb_spread,
            "signal":          signal,
            "entry_price":     exits["entry"],
            "stop_price":      exits["stop"],
            "target_1":        exits["target_1"],
            "atr20":           result.atr20,
            "ema20":           result.ema20,
            "atr_compression": result.atr_compression,
            "near_high":       result.near_high,
            "vol_ratio":       result.vol_ratio,
            "regime":          regime,
        })

    rows.sort(key=lambda r: r["vts"], reverse=True)
    return rows


# ── Supabase upsert ────────────────────────────────────────────────────────

def upsert_scores(rows: list[dict], sb) -> None:
    if not rows:
        log.warning("No rows to upsert.")
        return
    # Batch upsert in chunks of 50 (Supabase row limit per call)
    chunk_size = 50
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i: i + chunk_size]
        sb.table("vts_scores").upsert(
            chunk, on_conflict="scan_date,symbol"
        ).execute()
        total += len(chunk)
    log.info("Upserted %d rows to vts_scores.", total)


# ── Summary logging ────────────────────────────────────────────────────────

def log_summary(rows: list[dict], scan_date: pd.Timestamp) -> None:
    if not rows:
        log.warning("No results for %s", scan_date.date())
        return

    by_signal: dict[str, list] = {}
    for r in rows:
        by_signal.setdefault(r["signal"], []).append(r)

    log.info("── VTS Scan Results %s ──────────────────────────", scan_date.date())
    for sig in ("STRONG_BUY", "BUY", "WATCH", "AVOID"):
        bucket = by_signal.get(sig, [])
        if not bucket:
            continue
        top = bucket[:5]
        names = ", ".join(
            f"{r['symbol']}({r['vts']:.0f})" for r in top
        )
        log.info("%-12s %3d  top: %s", sig, len(bucket), names)
    log.info("────────────────────────────────────────────────────────────")


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VTS daily scanner")
    parser.add_argument(
        "--date",
        default=None,
        help="Scan date yyyy-mm-dd (default: today / latest trading day)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and log results but do not write to Supabase",
    )
    args = parser.parse_args()

    scan_date = (
        pd.Timestamp(args.date)
        if args.date
        else pd.Timestamp(date.today())
    )

    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key and not args.dry_run:
        log.error("SUPABASE_SERVICE_ROLE_KEY not set. Use --dry-run or set the env var.")
        sys.exit(1)

    log.info("VTS Scanner starting — scan_date=%s  dry_run=%s", scan_date.date(), args.dry_run)

    # ── Fetch data ─────────────────────────────────────────────
    vix   = load_vix()
    log.info("VIX loaded: %d days, last=%s %.2f", len(vix), vix.index[-1].date(), vix.iloc[-1])

    try:
        nifty = fetch_nifty()
        log.info("Nifty loaded: %d days, last=%.0f", len(nifty), nifty.iloc[-1])
    except Exception as e:
        log.error("Failed to fetch Nifty: %s", e)
        sys.exit(1)

    symbols = yf_symbols()
    ohlcv   = fetch_bulk(symbols)
    if not ohlcv:
        log.error("No stock data fetched. Aborting.")
        sys.exit(1)

    # ── Scan ───────────────────────────────────────────────────
    rows = scan_universe(ohlcv, vix, nifty, scan_date)
    log_summary(rows, scan_date)
    log.info("Total stocks scanned: %d", len(rows))

    # ── Persist ────────────────────────────────────────────────
    if args.dry_run:
        log.info("Dry run — skipping Supabase write.")
        strong = [r for r in rows if r["signal"] == "STRONG_BUY"]
        buy    = [r for r in rows if r["signal"] == "BUY"]
        print("\n── STRONG_BUY ──")
        for r in strong:
            print(f"  {r['symbol']:15s}  VTS={r['vts']:5.1f}  VARB={r['varb_spread']:+.1f}"
                  f"  entry={r['entry_price']:.2f}  stop={r['stop_price']:.2f}"
                  f"  t1={r['target_1']:.2f}")
        print("\n── BUY ──")
        for r in buy[:10]:
            print(f"  {r['symbol']:15s}  VTS={r['vts']:5.1f}  VARB={r['varb_spread']:+.1f}"
                  f"  entry={r['entry_price']:.2f}  stop={r['stop_price']:.2f}"
                  f"  t1={r['target_1']:.2f}")
        return

    sb = create_client(SUPABASE_URL, key)
    upsert_scores(rows, sb)
    log.info("Done.")


if __name__ == "__main__":
    main()
