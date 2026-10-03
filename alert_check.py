"""
Alert Monitor — checks active positions against thresholds and
sends notifications when action is needed.

Runs after price_sync.py via GitHub Actions. Checks:
  1. Distance to stop-loss ≤ 3% (DANGER) or ≤ 7% (WARNING)
  2. Thesis-threatening news (unverified)
  3. Overdue review dates
  4. Unverified material announcements
  5. Intraday move > ALERT_INTRADAY_MOVE_PCT (WARNING/DANGER)
  6. Volume spike > ALERT_VOLUME_SPIKE_MULT × 20-day avg (INFO/WARNING)
  7. Price within ALERT_52W_HIGH/LOW_PROXIMITY_PCT of 52-week range

Data hierarchy (failsafe — each layer used only when the previous is
insufficient):
  1st  daily_snapshots (Supabase) — OHLCV history already stored; one
       bulk query for all tickers, no external calls.
  2nd  yfinance fast_info — single ticker call, gives year_high/year_low
       and three_month_average_volume; used only when Supabase history
       is too short (< ALERT_52W_MIN_ROWS rows).
  3rd  Skip gracefully — log warning, never crash the run.

Notifications are sent via email (Gmail SMTP).
Requires GitHub secrets: ALERT_EMAIL_TO, GMAIL_APP_PASSWORD

To avoid alert fatigue, each unique alert (ticker + reason) is
tracked in the `alert_log` table — the same alert won't fire again
within the cooldown window (default 6 hours).

    SUPABASE_SERVICE_ROLE_KEY=... python alert_check.py
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

from supabase import create_client
import config as cfg

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Stop-loss thresholds (kept here — position-specific, not in config)
STOP_DANGER_PCT = 3.0
STOP_WARNING_PCT = 7.0
ALERT_COOLDOWN_HOURS = 6

# Email config
GMAIL_SENDER = "tradingalerts.jigs@gmail.com"  # create a dedicated Gmail for alerts


def get_supabase_client():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)


def load_data(sb):
    """Load active positions, prices, news from Supabase."""
    positions = (
        sb.table("position_monitoring")
        .select("*")
        .eq("status", "active")
        .execute()
    ).data or []

    stocks = (
        sb.table("stocks")
        .select("ticker, current_price, price_updated_at")
        .execute()
    ).data or []

    news = (
        sb.table("news_raw")
        .select("ticker, headline, severity_tag, verified_status, published_at")
        .in_("severity_tag", ["thesis-threatening", "material change"])
        .order("published_at", desc=True)
        .limit(100)
        .execute()
    ).data or []

    return positions, stocks, news


def build_price_map(stocks):
    """Build ticker -> current_price lookup (fresh prices only)."""
    price_map = {}
    now = datetime.now(timezone.utc)
    for s in stocks:
        cp = s.get("current_price")
        pu = s.get("price_updated_at")
        if cp is not None:
            is_fresh = True
            if pu:
                try:
                    updated = datetime.fromisoformat(str(pu).replace("Z", "+00:00"))
                    is_fresh = (now - updated) < timedelta(hours=24)
                except Exception:
                    is_fresh = False
            if is_fresh:
                price_map[s["ticker"]] = float(cp)
    return price_map


def load_snapshots(sb, tickers):
    """Bulk-fetch daily_snapshots for all tickers in one query.

    Returns {ticker: [rows ordered date DESC]} where each row has
    open_price, close_price, volume. Rows are ordered newest-first so
    callers can slice with [:N] for a rolling window.
    """
    if not tickers:
        return {}
    try:
        rows = (
            sb.table("daily_snapshots")
            .select("ticker, date, open_price, close_price, volume")
            .in_("ticker", list(tickers))
            .order("date", desc=True)
            .limit(300 * len(tickers))   # ~1yr per ticker in one round trip
            .execute()
        ).data or []
    except Exception as e:
        print(f"  ⚠ load_snapshots failed: {e} — market-data checks skipped")
        return {}

    by_ticker = {}
    for r in rows:
        t = r.get("ticker")
        if t:
            by_ticker.setdefault(t, []).append(r)
    return by_ticker


def _yf_fallback(ticker):
    """Fetch year_high, year_low, avg_volume from yfinance fast_info.

    Returns dict or None on any failure — callers must handle None.
    """
    try:
        import yfinance as yf
        symbol = f"{ticker}.NS"
        fi = yf.Ticker(symbol).fast_info
        return {
            "year_high": fi.get("year_high"),
            "year_low":  fi.get("year_low"),
            "avg_volume": fi.get("three_month_average_volume"),
            "source": "yfinance",
        }
    except Exception as e:
        print(f"  ⚠ yfinance fallback failed for {ticker}: {e}")
        return None


def check_intraday_move(ticker, price_map, snapshots):
    """Return alert if today's intraday move exceeds threshold.

    Uses today's open_price from snapshots (most recent row). Falls
    back to price_map entry price if no snapshot open is available.
    """
    cmp = price_map.get(ticker)
    if not cmp:
        return None

    rows = snapshots.get(ticker, [])
    open_price = None
    if rows:
        open_price = _f(rows[0].get("open_price"))   # latest row = today

    if not open_price or open_price <= 0:
        return None

    move_pct = (cmp - open_price) / open_price * 100
    abs_move = abs(move_pct)
    if abs_move < cfg.ALERT_INTRADAY_MOVE_PCT:
        return None

    direction = "up" if move_pct > 0 else "down"
    level = "DANGER" if abs_move >= cfg.ALERT_INTRADAY_MOVE_PCT * 1.5 else "WARNING"
    return {
        "ticker": ticker,
        "level": level,
        "reason": "intraday_move",
        "detail": (
            f"Intraday move {move_pct:+.1f}% ({direction}) from open ₹{open_price:,.1f} "
            f"→ CMP ₹{cmp:,.1f}"
        ),
    }


def check_volume_spike(ticker, snapshots):
    """Return alert if today's volume is a spike vs rolling average.

    Uses daily_snapshots rows. Needs at least ALERT_VOLUME_AVG_DAYS + 1
    rows (today + history). Skips silently if data is insufficient.
    """
    rows = snapshots.get(ticker, [])
    if len(rows) < cfg.ALERT_VOLUME_AVG_DAYS + 1:
        return None

    today_vol = _f(rows[0].get("volume"))
    if not today_vol:
        return None

    # rows[1:] = historical rows (exclude today)
    hist_vols = [_f(r.get("volume")) for r in rows[1: cfg.ALERT_VOLUME_AVG_DAYS + 1]]
    hist_vols = [v for v in hist_vols if v and v > 0]
    if not hist_vols:
        return None

    avg_vol = sum(hist_vols) / len(hist_vols)
    if avg_vol <= 0:
        return None

    mult = today_vol / avg_vol
    if mult < cfg.ALERT_VOLUME_SPIKE_MULT:
        return None

    level = "DANGER" if mult >= cfg.ALERT_VOLUME_SPIKE_MULT * 1.5 else "WARNING"
    return {
        "ticker": ticker,
        "level": level,
        "reason": "volume_spike",
        "detail": (
            f"Volume spike: {mult:.1f}× avg ({int(today_vol):,} vs "
            f"{int(avg_vol):,} avg over {len(hist_vols)}d)"
        ),
    }


def check_52w_proximity(ticker, price_map, snapshots):
    """Return alert(s) if price is near the 52-week high or low.

    Primary: compute from daily_snapshots (up to 252 rows).
    Fallback: yfinance fast_info when history is too short.
    Returns a list (may have 0, 1 or 2 alerts — high + low simultaneously).
    """
    cmp = price_map.get(ticker)
    if not cmp:
        return []

    rows = snapshots.get(ticker, [])
    week52_high = week52_low = None

    if len(rows) >= cfg.ALERT_52W_MIN_ROWS:
        closes = [_f(r.get("close_price")) for r in rows[:252]]
        closes = [c for c in closes if c and c > 0]
        if closes:
            week52_high = max(closes)
            week52_low  = min(closes)
    else:
        fb = _yf_fallback(ticker)
        if fb:
            week52_high = fb.get("year_high")
            week52_low  = fb.get("year_low")

    alerts = []

    if week52_high and week52_high > 0:
        pct_from_high = (week52_high - cmp) / week52_high * 100
        if 0 <= pct_from_high <= cfg.ALERT_52W_HIGH_PROXIMITY_PCT:
            alerts.append({
                "ticker": ticker,
                "level": "INFO",
                "reason": "near_52w_high",
                "detail": (
                    f"Near 52-week high: CMP ₹{cmp:,.1f} is {pct_from_high:.1f}% "
                    f"below 52w high of ₹{week52_high:,.1f}"
                ),
            })

    if week52_low and week52_low > 0:
        pct_from_low = (cmp - week52_low) / week52_low * 100
        if 0 <= pct_from_low <= cfg.ALERT_52W_LOW_PROXIMITY_PCT:
            alerts.append({
                "ticker": ticker,
                "level": "DANGER",
                "reason": "near_52w_low",
                "detail": (
                    f"Near 52-week low: CMP ₹{cmp:,.1f} is only {pct_from_low:.1f}% "
                    f"above 52w low of ₹{week52_low:,.1f}"
                ),
            })

    return alerts


def check_alerts(positions, price_map, news, snapshots=None):
    """Check all active positions and return list of alerts.

    snapshots: {ticker: [rows]} from load_snapshots(). Pass None to
    skip market-data checks (backwards-compatible with existing callers).
    Each alert: {ticker, level, reason, detail}
    """
    if snapshots is None:
        snapshots = {}

    alerts = []

    for p in positions:
        ticker = p.get("ticker")
        entry = _f(p.get("entry_price"))
        stop = _f(p.get("stop_loss"))
        price = price_map.get(ticker) or entry

        # 1. Stop-loss proximity
        if price and stop and price > 0:
            dist = abs(price - stop) / price * 100
            if dist <= STOP_DANGER_PCT:
                alerts.append({
                    "ticker": ticker,
                    "level": "DANGER",
                    "reason": "stop_proximity",
                    "detail": f"Only {dist:.1f}% from stop-loss (₹{stop:,.0f}). "
                              f"CMP: ₹{price:,.1f}. Consider exit or tighten stop.",
                })
            elif dist <= STOP_WARNING_PCT:
                alerts.append({
                    "ticker": ticker,
                    "level": "WARNING",
                    "reason": "stop_proximity",
                    "detail": f"{dist:.1f}% from stop-loss (₹{stop:,.0f}). "
                              f"CMP: ₹{price:,.1f}. Monitor closely.",
                })

        # 2. Thesis-threatening news
        threats = [n for n in news
                   if n.get("ticker") == ticker
                   and n.get("severity_tag") == "thesis-threatening"
                   and n.get("verified_status") == "unverified"]
        for n in threats:
            alerts.append({
                "ticker": ticker,
                "level": "DANGER",
                "reason": "thesis_threat",
                "detail": f"Thesis-threatening: {n.get('headline', '')[:80]}",
            })

        # 3. Overdue review
        rd = p.get("review_date")
        if rd:
            try:
                review = datetime.fromisoformat(str(rd)).date()
                today = datetime.now().date()
                days = (review - today).days
                if days < 0:
                    alerts.append({
                        "ticker": ticker,
                        "level": "WARNING",
                        "reason": "review_overdue",
                        "detail": f"Review overdue by {abs(days)} day(s) "
                                  f"(was due {review.strftime('%d %b %Y')})",
                    })
            except Exception:
                pass

        # 4. Unverified material news
        mat_unverified = [n for n in news
                          if n.get("ticker") == ticker
                          and n.get("severity_tag") == "material change"
                          and n.get("verified_status") == "unverified"]
        if mat_unverified:
            alerts.append({
                "ticker": ticker,
                "level": "INFO",
                "reason": "material_unverified",
                "detail": f"{len(mat_unverified)} unverified material alert(s) — "
                          f"latest: {mat_unverified[0].get('headline', '')[:60]}",
            })

        # 5. Intraday move
        intraday = check_intraday_move(ticker, price_map, snapshots)
        if intraday:
            alerts.append(intraday)

        # 6. Volume spike
        vol_spike = check_volume_spike(ticker, snapshots)
        if vol_spike:
            alerts.append(vol_spike)

        # 7. 52-week proximity (returns list — can be 0, 1, or 2 alerts)
        alerts.extend(check_52w_proximity(ticker, price_map, snapshots))

    return alerts


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def filter_cooldown(sb, alerts):
    """
    Filter out alerts that were already sent within the cooldown window.
    Uses the alert_log table for deduplication.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ALERT_COOLDOWN_HOURS)).isoformat()
    filtered = []

    for a in alerts:
        key = f"{a['ticker']}:{a['reason']}"
        existing = (
            sb.table("alert_log")
            .select("id")
            .eq("alert_key", key)
            .gte("sent_at", cutoff)
            .execute()
        )
        if not existing.data:
            filtered.append(a)

    return filtered


def log_alerts(sb, alerts):
    """Record sent alerts in alert_log for cooldown tracking."""
    now = datetime.now(timezone.utc).isoformat()
    for a in alerts:
        sb.table("alert_log").insert({
            "alert_key": f"{a['ticker']}:{a['reason']}",
            "ticker": a["ticker"],
            "level": a["level"],
            "reason": a["reason"],
            "detail": a["detail"][:500],
            "sent_at": now,
        }).execute()


def send_email(alerts, recipient):
    """Send alert email via Gmail SMTP."""
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_password:
        print("  GMAIL_APP_PASSWORD not set — skipping email")
        return False

    danger = [a for a in alerts if a["level"] == "DANGER"]
    warning = [a for a in alerts if a["level"] == "WARNING"]
    info = [a for a in alerts if a["level"] == "INFO"]

    subject_parts = []
    if danger:
        subject_parts.append(f"🚨 {len(danger)} DANGER")
    if warning:
        subject_parts.append(f"⚡ {len(warning)} WARNING")
    if info:
        subject_parts.append(f"ℹ️ {len(info)} INFO")
    subject = f"Portfolio Alert: {', '.join(subject_parts)}"

    # Build HTML email body
    html = """
    <html><body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color: #1a1a1a; border-bottom: 2px solid #ef4444; padding-bottom: 8px">
        📈 Portfolio Alert</h2>
    <p style="color: #666; font-size: 13px">
        {timestamp} IST</p>
    """.format(timestamp=datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %I:%M %p"))

    if danger:
        html += '<div style="background: #fef2f2; border: 1px solid #ef4444; border-radius: 8px; padding: 12px; margin: 12px 0">'
        html += '<h3 style="color: #dc2626; margin: 0 0 8px">🚨 ACTION REQUIRED</h3>'
        for a in danger:
            html += f'<p style="margin: 4px 0; font-size: 14px"><strong>{a["ticker"]}</strong> — {a["detail"]}</p>'
        html += '</div>'

    if warning:
        html += '<div style="background: #fffbeb; border: 1px solid #f59e0b; border-radius: 8px; padding: 12px; margin: 12px 0">'
        html += '<h3 style="color: #d97706; margin: 0 0 8px">⚡ WATCH CLOSELY</h3>'
        for a in warning:
            html += f'<p style="margin: 4px 0; font-size: 14px"><strong>{a["ticker"]}</strong> — {a["detail"]}</p>'
        html += '</div>'

    if info:
        html += '<div style="background: #f0f9ff; border: 1px solid #3b82f6; border-radius: 8px; padding: 12px; margin: 12px 0">'
        html += '<h3 style="color: #2563eb; margin: 0 0 8px">ℹ️ FOR REVIEW</h3>'
        for a in info:
            html += f'<p style="margin: 4px 0; font-size: 14px"><strong>{a["ticker"]}</strong> — {a["detail"]}</p>'
        html += '</div>'

    html += """
    <p style="color: #999; font-size: 11px; margin-top: 20px; border-top: 1px solid #eee; padding-top: 8px">
        <a href="https://trading-dashboard-em8aois3kndxfqhnswwudu.streamlit.app" style="color: #3b82f6">
        Open Dashboard →</a></p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, gmail_password)
            server.send_message(msg)
        print(f"  ✉ Email sent to {recipient}")
        return True
    except Exception as e:
        print(f"  ✗ Email failed: {e}")
        return False


def main():
    sb = get_supabase_client()
    positions, stocks, news = load_data(sb)

    if not positions:
        print("No active positions — nothing to check.")
        return

    price_map = build_price_map(stocks)
    print(f"Checking {len(positions)} positions "
          f"({len(price_map)} with live prices)")

    # Bulk-load OHLCV history for all active tickers (one DB round trip)
    active_tickers = [p.get("ticker") for p in positions if p.get("ticker")]
    snapshots = load_snapshots(sb, active_tickers)
    print(f"  Snapshots loaded: {len(snapshots)} ticker(s)")

    # Find all alerts
    all_alerts = check_alerts(positions, price_map, news, snapshots)
    print(f"  Raw alerts: {len(all_alerts)}")

    if not all_alerts:
        print("  All clear — no alerts triggered.")
        return

    # Filter out recently sent (cooldown)
    try:
        new_alerts = filter_cooldown(sb, all_alerts)
    except Exception as e:
        # alert_log table might not exist yet — send all
        print(f"  Cooldown check failed ({e}) — sending all alerts")
        new_alerts = all_alerts

    if not new_alerts:
        print(f"  {len(all_alerts)} alert(s) already sent within "
              f"{ALERT_COOLDOWN_HOURS}h cooldown — skipping.")
        return

    # Print summary
    for a in new_alerts:
        print(f"  [{a['level']}] {a['ticker']}: {a['detail'][:80]}")

    # Send email
    recipient = os.environ.get("ALERT_EMAIL_TO", "")
    if recipient:
        send_email(new_alerts, recipient)
    else:
        print("  ALERT_EMAIL_TO not set — skipping email (alerts printed above)")

    # Log to prevent re-sending
    try:
        log_alerts(sb, new_alerts)
    except Exception as e:
        print(f"  Could not log alerts: {e}")

    print(f"\nDone. {len(new_alerts)} new alert(s) processed.")


if __name__ == "__main__":
    main()
