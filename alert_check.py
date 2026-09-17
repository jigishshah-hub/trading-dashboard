"""
Alert Monitor — checks active positions against thresholds and
sends notifications when action is needed.

Runs after price_sync.py via GitHub Actions. Checks:
  1. Distance to stop-loss ≤ 3% (DANGER) or ≤ 7% (WARNING)
  2. Thesis-threatening news (unverified)
  3. Overdue review dates
  4. Unverified material announcements

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

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# Alert thresholds
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


def check_alerts(positions, price_map, news):
    """
    Check all active positions and return list of alerts.
    Each alert: {ticker, level, reason, detail}
    """
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

    # Find all alerts
    all_alerts = check_alerts(positions, price_map, news)
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
