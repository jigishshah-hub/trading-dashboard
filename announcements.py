"""
Announcements monitor — fetches news for all active tickers and
writes new items to the news_raw table in Supabase.

Sources:
  1. NSE corporate announcements API (official exchange filings)
  2. Google News RSS (broader news coverage)

Designed to run on a schedule via GitHub Actions, same pattern as
sync.py. Deduplication is by (ticker + headline), so re-running is
safe — duplicates are silently skipped.

Materiality classification is rule-based with severity_reasoning
so the dashboard can show *why* something was flagged.
"""

import os
import json
from datetime import datetime, timezone

import feedparser
from supabase import create_client

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# ── Materiality classification ────────────────────────────────
# Each entry: (keyword, severity, reasoning)
THESIS_THREATENING_RULES = [
    ("fraud", "thesis-threatening", "Fraud allegation — potential thesis invalidation"),
    ("default", "thesis-threatening", "Debt default — solvency risk"),
    ("sebi order", "thesis-threatening", "SEBI regulatory order — compliance risk"),
    ("insider trading", "thesis-threatening", "Insider trading investigation"),
    ("delisting", "thesis-threatening", "Delisting — liquidity risk"),
    ("suspension", "thesis-threatening", "Trading suspension — severe regulatory action"),
    ("winding up", "thesis-threatening", "Winding up / insolvency proceedings"),
]

MATERIAL_RULES = [
    ("board meeting", "material change", "Board meeting outcome — may include results or corporate actions"),
    ("outcome of board", "material change", "Board meeting outcome — check for results or key decisions"),
    ("dividend", "material change", "Dividend announcement — impacts yield thesis"),
    ("bonus", "material change", "Bonus issue — share capital change"),
    ("split", "material change", "Stock split — share structure change"),
    ("merger", "material change", "Merger/acquisition — fundamental business change"),
    ("acquisition", "material change", "Acquisition — business expansion or diversification"),
    ("demerger", "material change", "Demerger — business restructuring"),
    ("buyback", "material change", "Share buyback — capital return to shareholders"),
    ("rights issue", "material change", "Rights issue — dilution risk"),
    ("preferential allotment", "material change", "Preferential allotment — potential dilution"),
    ("resignation", "material change", "Key management resignation"),
    ("appointment of", "material change", "New management appointment"),
    ("change in management", "material change", "Management change — leadership transition"),
    ("results", "material change", "Financial results announcement"),
    ("quarterly results", "material change", "Quarterly financial results"),
    ("annual results", "material change", "Annual financial results"),
    ("profit warning", "material change", "Profit warning — earnings miss"),
    ("downgrade", "material change", "Rating/analyst downgrade"),
    ("upgrade", "material change", "Rating/analyst upgrade"),
    ("credit rating", "material change", "Credit rating update — impacts debt cost/access"),
    ("order win", "material change", "New order win — revenue visibility"),
    ("contract", "material change", "Contract announcement — revenue impact"),
    ("record date", "material change", "Record date set — upcoming corporate action"),
]

# Headlines that are pure noise — skip entirely, don't even store
SKIP_HEADLINES = {
    "copy of newspaper publication",
    "general updates",
    "updates",
    "press release",  # unless body has content
}


def get_supabase_client():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)


def get_active_tickers(sb):
    """Fetch all active tickers from the stocks table."""
    result = sb.table("stocks").select("ticker").eq("status", "active").execute()
    return [row["ticker"] for row in result.data]


def classify_severity(headline, body=""):
    """
    Rule-based materiality classification.
    Returns (severity_tag, severity_reasoning).
    """
    combined = f"{headline} {body}".lower()

    for keyword, severity, reasoning in THESIS_THREATENING_RULES:
        if keyword.lower() in combined:
            return severity, reasoning

    for keyword, severity, reasoning in MATERIAL_RULES:
        if keyword.lower() in combined:
            return severity, reasoning

    return "routine update", ""


def should_skip(headline):
    """Return True if this headline is pure noise with no informational value."""
    return headline.strip().lower() in SKIP_HEADLINES


def fetch_nse_announcements(ticker):
    """Fetch corporate announcements from NSE API."""
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={ticker}"
    items = []

    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for item in data[:15]:  # latest 15
            # NSE API fields:
            #   desc    = filing category ("General Updates", "Board Meeting")
            #   subject = actual description (often more informative)
            #   an_dt   = announcement date
            #   attchmntFile = PDF attachment URL
            desc = (item.get("desc") or "").strip()
            subject = (item.get("subject") or "").strip()
            an_dt = item.get("an_dt", "")
            attachment_url = item.get("attchmntFile", "")
            if attachment_url and not attachment_url.startswith("http"):
                attachment_url = f"https://www.nseindia.com{attachment_url}"

            # Use subject as headline when it's more informative than desc.
            # desc is usually just a category; subject has the real content.
            headline = subject if subject and len(subject) > len(desc) else desc
            if not headline:
                continue

            # Build body_snippet from whichever field wasn't used as headline
            body_snippet = ""
            if subject and headline != subject:
                body_snippet = subject
            elif desc and headline != desc:
                body_snippet = f"Filing type: {desc}"

            # Skip pure noise — but only if body_snippet doesn't rescue it
            if should_skip(headline) and not body_snippet:
                continue

            items.append({
                "headline": headline,
                "body_snippet": body_snippet[:500] if body_snippet else None,
                "published_at": an_dt,
                "url": attachment_url,
                "source": "nse",
            })
    except Exception as e:
        print(f"  NSE fetch failed for {ticker}: {e}")

    return items


def fetch_google_news(ticker):
    """Fetch recent news from Google News RSS."""
    query = f"{ticker} NSE stock"
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    items = []

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]:
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = datetime(
                    *entry.published_parsed[:6], tzinfo=timezone.utc
                ).isoformat()

            headline = entry.get("title", "").strip()
            # Google News titles often end with " - Source Name"
            # Keep it — the source attribution is useful context
            summary = entry.get("summary", "").strip()

            if not headline:
                continue

            items.append({
                "headline": headline,
                "body_snippet": summary[:500] if summary else None,
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

        # Check for existing item with same ticker + headline
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

        severity, reasoning = classify_severity(
            headline, item.get("body_snippet") or ""
        )

        row = {
            "ticker": ticker,
            "source": item["source"],
            "headline": headline,
            "body_snippet": item.get("body_snippet"),
            "url": item.get("url"),
            "severity_tag": severity,
            "severity_reasoning": reasoning if reasoning else None,
            "verified_status": "unverified",
        }

        if item.get("published_at"):
            row["published_at"] = item["published_at"]

        sb.table("news_raw").insert(row).execute()
        stored += 1

        if severity != "routine update":
            print(f"  [{severity.upper()}] {ticker}: {headline}")
            if reasoning:
                print(f"    Reason: {reasoning}")

    return stored, skipped


def main():
    sb = get_supabase_client()
    tickers = get_active_tickers(sb)

    print(f"Monitoring {len(tickers)} active tickers: {', '.join(tickers)}")

    total_stored = 0
    total_skipped = 0

    for ticker in tickers:
        print(f"\n--- {ticker} ---")

        nse_items = fetch_nse_announcements(ticker)
        google_items = fetch_google_news(ticker)

        all_items = nse_items + google_items
        print(f"  Fetched {len(nse_items)} NSE + {len(google_items)} Google News items")

        stored, skipped = store_news_items(sb, ticker, all_items)
        total_stored += stored
        total_skipped += skipped
        print(f"  Stored {stored}, skipped {skipped} duplicates")

    print(f"\nDone. Total: {total_stored} new items stored, {total_skipped} duplicates skipped.")


if __name__ == "__main__":
    main()
