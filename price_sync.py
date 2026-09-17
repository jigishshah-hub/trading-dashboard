"""
Price Sync: Screener.in -> Supabase (stocks.current_price).

Fetches the current market price (CMP) from Screener.in for every
active ticker and updates the stocks table. This gives the dashboard
a reasonably fresh price without needing a real-time feed.

Designed to run every 30 minutes during NSE hours via GitHub Actions
(see .github/workflows/price_sync.yml). Can also be run manually:
    SUPABASE_SERVICE_ROLE_KEY=... python price_sync.py

Once OpenAlgo / a real-time feed is connected, it writes to the same
columns and this script becomes redundant — no dashboard changes needed.
"""

import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from supabase import create_client

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Override screener.in slugs when they differ from the NSE ticker.
TICKER_SLUG_OVERRIDES = {
    # "SOMETICKER": "some-screener-slug",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 2.0  # seconds between requests


def get_supabase_client():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)


def get_active_tickers(sb):
    """Return list of active tickers from position_monitoring."""
    result = (
        sb.table("position_monitoring")
        .select("ticker")
        .eq("status", "active")
        .execute()
    )
    return list(set(row["ticker"] for row in result.data))


def screener_slug(ticker: str) -> str:
    return TICKER_SLUG_OVERRIDES.get(ticker, ticker)


def fetch_current_price(ticker: str) -> float | None:
    """
    Fetch the current market price from Screener.in.

    Screener shows CMP in several places:
      1. <span id="number" class="number">₹ 1,234.56</span>  (top header)
      2. The top-ratios section: "Current Price" -> value
      3. Plain text: "Current Price ₹ 1,234"
    We try all three, first match wins.
    """
    slug = screener_slug(ticker)
    url = f"https://www.screener.in/company/{slug}/consolidated/"

    resp = requests.get(url, headers=HEADERS, timeout=30)
    # Consolidated page may 404 for some companies — fall back to standalone
    if resp.status_code == 404:
        url = f"https://www.screener.in/company/{slug}/"
        resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Method 1: top header price element
    # Screener typically has: <span class="number" id="number">₹ 1,234.56</span>
    # or inside the company-header area
    price_el = soup.select_one("#number.number, .company-header .number")
    if price_el:
        val = _parse_price(price_el.get_text())
        if val:
            return val

    # Method 2: top-ratios section "Current Price" label
    for li in soup.select("#top-ratios li, .company-ratios li"):
        name_el = li.select_one(".name")
        num_el = li.select_one(".number, .value")
        if name_el and num_el:
            if "current price" in name_el.get_text(strip=True).lower():
                val = _parse_price(num_el.get_text())
                if val:
                    return val

    # Method 3: regex fallback on full page text
    text = soup.get_text(" ", strip=True)
    match = re.search(r"Current Price\s*[₹:\s]*([\d,]+\.?\d*)", text, re.IGNORECASE)
    if match:
        return _parse_price(match.group(1))

    # Method 4: the big price number at top (some pages have it differently)
    match = re.search(r"₹\s*([\d,]+\.?\d*)", text)
    if match:
        val = _parse_price(match.group(1))
        if val and val > 1:  # sanity check — skip tiny numbers
            return val

    return None


def _parse_price(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace("₹", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def update_price(sb, ticker: str, price: float):
    """Write current_price and price_updated_at to the stocks table."""
    now = datetime.now(timezone.utc).isoformat()
    sb.table("stocks").update({
        "current_price": price,
        "price_updated_at": now,
    }).eq("ticker", ticker).execute()


def main():
    sb = get_supabase_client()
    tickers = get_active_tickers(sb)

    if not tickers:
        print("No active tickers found — nothing to sync.")
        return

    print(f"Syncing prices for {len(tickers)} active tickers: {tickers}")

    success = []
    failed = []

    for ticker in tickers:
        try:
            price = fetch_current_price(ticker)
            if price:
                update_price(sb, ticker, price)
                print(f"  ✓ {ticker}: ₹{price:,.2f}")
                success.append(ticker)
            else:
                print(f"  ✗ {ticker}: could not parse price from page")
                failed.append(ticker)
        except requests.HTTPError as e:
            print(f"  ✗ {ticker}: HTTP {e.response.status_code}")
            failed.append(ticker)
        except Exception as e:
            print(f"  ✗ {ticker}: {e}")
            failed.append(ticker)

        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {len(success)} succeeded, {len(failed)} failed.")
    if failed:
        print(f"Failed tickers: {failed}")


if __name__ == "__main__":
    main()
