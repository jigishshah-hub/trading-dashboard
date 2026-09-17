"""
Fundamentals Sync: Screener.in -> Supabase.

Fetches key financial ratios from Screener.in for every active ticker
in position_monitoring, then upserts them into fundamentals_snapshots.

Designed to run weekly via GitHub Actions (see
.github/workflows/fundamentals.yml). Can also be run manually:
    SUPABASE_SERVICE_ROLE_KEY=... python fundamentals_sync.py

Credentials:
    SUPABASE_SERVICE_ROLE_KEY  - Supabase's service_role / secret key
    (No Google credentials needed — Screener.in is public.)
"""

import os
import re
import time

import requests
from bs4 import BeautifulSoup
from supabase import create_client

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Map ticker -> Screener.in company slug when they differ.
# Most NSE tickers work directly; add overrides here as needed.
TICKER_SLUG_OVERRIDES = {
    # "SOMETICKER": "some-screener-slug",
}

# Headers to look like a real browser — Screener.in blocks bare requests.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Polite delay between requests (seconds).
REQUEST_DELAY = 2.0


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
    return [row["ticker"] for row in result.data]


def screener_slug(ticker: str) -> str:
    """Convert a ticker to its Screener.in URL slug."""
    return TICKER_SLUG_OVERRIDES.get(ticker, ticker)


def parse_top_ratio(soup, label: str):
    """
    Extract a value from the top-ratios section.
    Screener.in uses:
        <li>
            <span class="name">Stock P/E</span>
            <span class="number">30.7</span>
        </li>
    """
    for li in soup.select("#top-ratios li, .company-ratios li, .ratios-table li"):
        name_el = li.select_one(".name")
        num_el = li.select_one(".number, .value")
        if name_el and num_el:
            name_text = name_el.get_text(strip=True)
            if label.lower() in name_text.lower():
                raw = num_el.get_text(strip=True)
                return _parse_number(raw)
    return None


def parse_ratio_from_text(text: str, label: str):
    """
    Fallback: search the entire rendered page text for patterns like
    'Stock P/E 30.7' or 'ROCE 32.4 %'.
    """
    # Try pattern: Label ... number (optionally with % or ₹)
    pattern = rf"{re.escape(label)}\s*[:\s₹]*\s*([\d,]+\.?\d*)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return _parse_number(match.group(1))
    return None


def _parse_number(raw: str):
    """Convert '1,234.56', '30.7 %', '₹ 168' -> float or None."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace("₹", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_fundamentals(ticker: str) -> dict:
    """
    Fetch key ratios for a single ticker from Screener.in.
    Returns a dict with keys matching fundamentals_snapshots columns.
    """
    slug = screener_slug(ticker)
    url = f"https://www.screener.in/company/{slug}/"

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    full_text = soup.get_text(" ", strip=True)

    # --- Extract ratios ---
    # Try structured HTML first, fall back to text regex.

    pe = parse_top_ratio(soup, "Stock P/E")
    if pe is None:
        pe = parse_ratio_from_text(full_text, "Stock P/E")

    roce = parse_top_ratio(soup, "ROCE")
    if roce is None:
        roce = parse_ratio_from_text(full_text, "ROCE")

    roe = parse_top_ratio(soup, "ROE")
    if roe is None:
        roe = parse_ratio_from_text(full_text, "ROE")

    # Debt to equity — sometimes shown as "Debt / Equity" or just in
    # the balance-sheet section. The top-ratios area on newer Screener
    # doesn't always have it, so we also search the full text.
    de = parse_top_ratio(soup, "Debt")
    if de is None:
        de = parse_ratio_from_text(full_text, "Debt to equity")
    if de is None:
        de = parse_ratio_from_text(full_text, "Debt / Equity")

    # OPM (Operating Profit Margin) — Screener shows this in the
    # Profit & Loss section. We map it to ebit_margin.
    opm = None
    # Look in the quarterly/annual tables for the most recent OPM row.
    opm_match = re.search(r"OPM\s*%?\s*[\s:]*(\d+\.?\d*)\s*%?", full_text)
    if opm_match:
        opm = _parse_number(opm_match.group(1))

    # Revenue growth YoY — we look for "Sales Growth" or
    # "Revenue Growth" in the ratios section.
    rev_growth = None
    for label in ["Sales Growth", "Revenue Growth", "Sales growth"]:
        rev_growth = parse_ratio_from_text(full_text, label)
        if rev_growth is not None:
            break
    # If still not found, try to compute from the compounded numbers.
    if rev_growth is None:
        # Look for "Compounded Sales Growth" -> TTM value
        ttm_match = re.search(
            r"Compounded Sales Growth.*?TTM\s*[:\s]*(-?\d+\.?\d*)\s*%?",
            full_text,
            re.DOTALL,
        )
        if ttm_match:
            rev_growth = _parse_number(ttm_match.group(1))

    return {
        "ticker": ticker,
        "period": "TTM",
        "pe": pe,
        "roce": roce,
        "roe": roe,
        "ebit_margin": opm,
        "revenue_growth_yoy": rev_growth,
        "debt_to_equity": de,
        "source": "screener_scrape",
    }


def upsert_fundamentals(sb, data: dict):
    """
    Insert or update a fundamentals_snapshots row.
    We use a simple strategy: delete any existing row for this ticker +
    period, then insert the fresh one. This avoids needing a unique
    constraint on (ticker, period) while still giving us one row per
    ticker per period.
    """
    sb.table("fundamentals_snapshots").delete().eq(
        "ticker", data["ticker"]
    ).eq("period", data["period"]).execute()

    sb.table("fundamentals_snapshots").insert(data).execute()


def main():
    sb = get_supabase_client()
    tickers = get_active_tickers(sb)

    if not tickers:
        print("No active tickers found — nothing to sync.")
        return

    print(f"Syncing fundamentals for {len(tickers)} active tickers: {tickers}")

    success = []
    failed = []

    for ticker in tickers:
        try:
            data = fetch_fundamentals(ticker)
            upsert_fundamentals(sb, data)
            found = {k: v for k, v in data.items() if v is not None and k not in ("ticker", "period", "source")}
            print(f"  ✓ {ticker}: {found}")
            success.append(ticker)
        except requests.HTTPError as e:
            print(f"  ✗ {ticker}: HTTP {e.response.status_code} — {e}")
            failed.append(ticker)
        except Exception as e:
            print(f"  ✗ {ticker}: {e}")
            failed.append(ticker)

        # Be polite to Screener.in
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {len(success)} succeeded, {len(failed)} failed.")
    if failed:
        print(f"Failed tickers: {failed}")
        # Don't exit with error — partial success is fine for a sync job.


if __name__ == "__main__":
    main()
