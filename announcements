"""
Announcements monitor — fetches news for all active tickers and
writes new items to the news_raw table in Supabase.

Sources:
  1. NSE corporate announcements RSS (official exchange filings)
  2. Google News RSS (broader news coverage)

Designed to run on a schedule via GitHub Actions, same pattern as
sync.py. Deduplication is by hash of (ticker + headline + date),
so re-running is safe — duplicates are silently skipped.

Materiality classification is rule-based v1 (keyword matching).
A future upgrade could use an LLM for "why it matters" reasoning.
"""

import os
import json
import hashlib
from datetime import datetime, timezone

import feedparser
from supabase import create_client

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Keywords that signal material news (case-insensitive matching)
MATERIAL_KEYWORDS = [
    "board meeting", "dividend", "bonus", "split", "merger",
    "acquisition", "demerger", "buyback", "delisting", "rights issue",
    "preferential allotment", "fraud", "default", "resignation",
    "appointment of", "change in management", "SEBI order",
    "insider trading", "pledge", "results", "quarterly results",
    "annual results", "profit warning", "downgrade", "upgrade",
]

THESIS_THREATENING_KEYWORDS = [
    "fraud", "default", "SEBI order", "insider trading",
    "delisting", "suspension",
]


def get_supabase_client():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)


def get_active_tickers(sb):
    """Fetch all active tickers from the stocks table."""
    result = sb.table("stocks").select("ticker").eq("status", "active").execute()
    return [row["ticker"] for row in result.data]


def classify_severity(headline):
    """Rule-based v1 materiality classification."""
    lower = headline.lower()
    for kw in THESIS_THREATENING_KEYWORDS:
        if kw.lower() in lower:
            return "thesis-threatening"
    for kw in MATERIAL_KEYWORDS:
        if kw.lower() in lower:
            return "material change"
    return "routine update"


def make_dedup_hash(ticker, headline, date_str):
    """Deterministic hash for deduplication across runs."""
    key = f"{ticker}|{headline}|{date_str}".lower().strip()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def fetch_nse_announcements(ticker):
    """Fetch corporate announcements from NSE RSS feed."""
    # NSE provides RSS feeds for corporate announcements per company
    # The symbol parameter matches BSE/NSE ticker symbols
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={ticker}"
    items = []

    try:
        # NSE's API returns JSON, not RSS — parse accordingly
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        for item in data[:10]:  # latest 10 only
            headline = item.get("desc", "") or item.get("subject", "")
            pub_date = item.get("an_dt", "")
            attachment_url = item.get("attchmntFile", "")
            if attachment_url and not attachment_url.startswith("http"):
                attachment_url = f"https://www.nseindia.com{attachment_url}"

            if headline:
                items.append({
                    "headline": headline.strip(),
                    "published_at": pub_date,
                    "url": attachment_url,
                    "source": "nse",
                })
    except Exception as e:
        print(f"  NSE fetch failed for {ticker}: {e}")

    return items


def fetch_google_news(ticker):
    """Fetch recent news from Google News RSS."""
    # Add "NSE" or "BSE" to improve relevance for Indian stocks
    query = f"{ticker} NSE stock"
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    items = []

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]:  # latest 5 only
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()

            items.append({
                "headline": entry.get("title", "").strip(),
                "published_at": pub_date,
                "url": entry.get("link", ""),
                "source": "google_news",
            })
    except Exception as e:
        print(f"  Google News fetch failed for {ticker}: {e}")

    return items


def store_news_items(sb, ticker, items):
    """Write news items to news_raw, skipping duplicates."""
    stored = 0
    skipped = 0

    for item in items:
        headline = item["headline"]
        pub_date = item.get("published_at", "")
        dedup_hash = make_dedup_hash(ticker, headline, pub_date or "")

        # Check if this item already exists (by checking url + headline combo)
        # Using a simple approach: try insert, skip on conflict
        severity = classify_severity(headline)

        row = {
            "ticker": ticker,
            "source": item["source"],
            "headline": headline,
            "url": item.get("url"),
            "severity_tag": severity,
            "verified_status": "unverified",
        }

        if pub_date:
            row["published_at"] = pub_date

        # Check for existing item with same ticker + headline to avoid dupes
        existing = (
            sb.table("news_raw")
            .select("id")
            .eq("ticker", ticker)
            .eq("headline", headline)
            .execute()
        )

        if existing.data:
            skipped += 1
            continue

        sb.table("news_raw").insert(row).execute()
        stored += 1

        # Log material+ items
        if severity != "routine update":
            print(f"  [{severity.upper()}] {ticker}: {headline}")

    return stored, skipped


def main():
    sb = get_supabase_client()
    tickers = get_active_tickers(sb)

    print(f"Monitoring {len(tickers)} active tickers: {', '.join(tickers)}")

    total_stored = 0
    total_skipped = 0

    for ticker in tickers:
        print(f"\n--- {ticker} ---")

        # Fetch from both sources
        nse_items = fetch_nse_announcements(ticker)
        google_items = fetch_google_news(ticker)

        all_items = nse_items + google_items
        print(f"  Fetched {len(nse_items)} NSE + {len(google_items)} Google News items")

        # Store, deduplicating
        stored, skipped = store_news_items(sb, ticker, all_items)
        total_stored += stored
        total_skipped += skipped
        print(f"  Stored {stored}, skipped {skipped} duplicates")

    print(f"\nDone. Total: {total_stored} new items stored, {total_skipped} duplicates skipped.")


if __name__ == "__main__":
    main()
