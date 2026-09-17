"""
Trading Dashboard — India Master Portfolio
Live portfolio monitoring connected to Supabase Postgres.
"""

import streamlit as st
from supabase import create_client
import pandas as pd
from datetime import datetime, timezone, timedelta

# ── Config ──────────────────────────────────────────────────
st.set_page_config(page_title="Trading Dashboard", page_icon="📈", layout="wide")
SB_URL = "https://egfsjboyzajyemjqazot.supabase.co"


# ── Data layer ──────────────────────────────────────────────
@st.cache_resource
def _sb():
    return create_client(SB_URL, st.secrets["SUPABASE_SERVICE_ROLE_KEY"])


@st.cache_data(ttl=60)
def load():
    sb = _sb()
    return {
        "pos": sb.table("position_monitoring").select("*").order("trade_id").execute().data or [],
        "conds": sb.table("position_monitoring_conditions").select("*").execute().data or [],
        "lots": sb.table("lots").select("*").order("lot_id").execute().data or [],
        "stocks": sb.table("stocks").select("ticker, current_price, price_updated_at").execute().data or [],
        "closed": sb.table("trade_history").select("*").order("exit_date", desc=True).execute().data or [],
        "news": sb.table("news_raw").select("*").order("published_at", desc=True).limit(200).execute().data or [],
        "research": sb.table("research_log").select("*").order("created_at", desc=True).execute().data or [],
        "fundsnap": sb.table("fundamentals_snapshots").select("*").order("pulled_at", desc=True).execute().data or [],
    }


D = load()

active = [p for p in D["pos"] if p.get("status") == "active"]
exited = [p for p in D["pos"] if p.get("status") == "exited"]

# Build a price lookup: ticker -> (current_price, price_updated_at)
price_map = {}
for s in D["stocks"]:
    cp = s.get("current_price")
    pu = s.get("price_updated_at")
    if cp is not None:
        # Check freshness — consider stale after 24 hours
        is_fresh = True
        if pu:
            try:
                updated = datetime.fromisoformat(str(pu).replace("Z", "+00:00"))
                is_fresh = (datetime.now(timezone.utc) - updated) < timedelta(hours=24)
            except Exception:
                is_fresh = False
        if is_fresh:
            price_map[s["ticker"]] = float(cp)


# ── Helpers ─────────────────────────────────────────────────
def f(v):
    """Convert Supabase value to float, None-safe."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def get_cmp(ticker, entry_price=None):
    """Get current market price. Returns (price, is_live) tuple."""
    if ticker in price_map:
        return price_map[ticker], True
    if entry_price is not None:
        return entry_price, False
    return None, False


def pct_change(base, current):
    b, c = f(base), f(current)
    if not b or not c or b == 0:
        return None
    return round((c - b) / b * 100, 2)


def pct_away(price, ref):
    p, r = f(price), f(ref)
    if not p or not r or p == 0:
        return None
    return round(abs(p - r) / p * 100, 2)


def pct_to_target(price, target):
    p, t = f(price), f(target)
    if not p or not t or p == 0:
        return None
    return round((t - p) / p * 100, 1)


def fmt_date(d):
    if not d:
        return ""
    try:
        return datetime.fromisoformat(str(d).replace("Z", "+00:00")).strftime("%d %b %Y")
    except Exception:
        return str(d)[:10]


def short_date(d):
    if not d:
        return ""
    try:
        return datetime.fromisoformat(str(d).replace("Z", "+00:00")).strftime("%d %b")
    except Exception:
        return str(d)[:10]


def conds_for(trade_id, ctype=None):
    return [c for c in D["conds"]
            if c.get("trade_id") == trade_id
            and (ctype is None or c.get("condition_type") == ctype)]


def news_for(ticker, limit=8):
    return [n for n in D["news"] if n.get("ticker") == ticker][:limit]


def research_for(ticker, limit=5):
    return [r for r in D["research"] if r.get("ticker") == ticker][:limit]


def fund_snapshot_for(ticker):
    """Latest structured fundamentals snapshot."""
    return next((s for s in D["fundsnap"] if s.get("ticker") == ticker), None)


def sev_icon(tag):
    if tag == "thesis-threatening":
        return "🔴"
    if tag == "material change":
        return "🟡"
    return "⚪"


def sev_border(tag):
    if tag == "thesis-threatening":
        return "#ef4444"
    if tag == "material change":
        return "#f59e0b"
    return "#d1d5db"


def review_urgency(review_date):
    """Return urgency indicator for review dates."""
    if not review_date:
        return ""
    try:
        rd = datetime.fromisoformat(str(review_date)).date()
        today = datetime.now().date()
        days_until = (rd - today).days
        if days_until < 0:
            return "🔴 overdue"
        if days_until <= 3:
            return "⏰ soon"
        return ""
    except Exception:
        return ""


def thesis_status(ticker):
    """Derive thesis status from latest news severity for a ticker."""
    ticker_news = [n for n in D["news"] if n.get("ticker") == ticker]
    has_thesis_threat = any(n.get("severity_tag") == "thesis-threatening" for n in ticker_news)
    has_material = any(n.get("severity_tag") == "material change" for n in ticker_news)
    if has_thesis_threat:
        return "Thesis threatened"
    if has_material:
        return "Under review"
    return "Thesis intact"


# ── Sidebar ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 Trading Dashboard")
    st.caption("India Master Portfolio · Live from Supabase")
    st.divider()

    sleeves = sorted(set(p.get("sleeve") or "Unassigned" for p in active))
    sel_sleeves = st.multiselect("Sleeve", sleeves, default=sleeves)

    setups = sorted(set(p.get("setup") or "" for p in active if p.get("setup")))
    sel_setups = st.multiselect("Setup type", setups, default=setups)

    thesis_statuses = ["Thesis intact", "Under review", "Thesis threatened"]
    sel_thesis = st.multiselect("Thesis status", thesis_statuses, default=thesis_statuses)

    st.divider()
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    has_live = len(price_map) > 0
    if has_live:
        st.caption(f"✅ Live prices: {len(price_map)} tickers")
    else:
        st.caption("⚠️ No live prices — showing entry prices")
    st.caption("Sheet → Postgres sync: every 30 min")
    st.caption("Price sync: every 30 min (market hours)")
    st.caption("Announcements monitor: hourly")


# ── Header metrics ──────────────────────────────────────────
deployed_cost = sum((f(p.get("entry_price")) or 0) * (f(p.get("quantity")) or 0) for p in active)

# Market value uses CMP where available
market_val = 0
for p in active:
    ticker = p.get("ticker")
    qty = f(p.get("quantity")) or 0
    cmp, is_live = get_cmp(ticker, f(p.get("entry_price")))
    market_val += (cmp or 0) * qty

mat_news = [n for n in D["news"] if n.get("severity_tag") != "routine update"]
wins = sum(1 for t in D["closed"]
           if (f(t.get("exit_price")) or 0) > (f(t.get("entry_price")) or 0))

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Active positions", len(active))
if has_live:
    pnl = market_val - deployed_cost
    pnl_pct = round(pnl / deployed_cost * 100, 1) if deployed_cost else 0
    m2.metric("Market value", f"₹{market_val / 1000:,.0f}K",
              delta=f"{'+' if pnl_pct >= 0 else ''}{pnl_pct}% P&L")
else:
    m2.metric("Deployed cost", f"₹{deployed_cost / 1000:,.0f}K")
m3.metric("Closed trades", len(D["closed"]))
m4.metric("Win rate", f"{wins}/{len(D['closed'])}" if D["closed"] else "—")
m5.metric("Material alerts", len(mat_news))
st.divider()


# ── Tabs ────────────────────────────────────────────────────
tab_pos, tab_ann, tab_cl, tab_res = st.tabs([
    f"📊 Positions ({len(active)})",
    f"📰 Announcements ({len(D['news'])})",
    f"📁 Closed trades ({len(D['closed'])})",
    f"📝 Research log ({len(D['research'])})",
])


# ── TAB 1 — Watchlist ───────────────────────────────────────
with tab_pos:
    if not has_live:
        st.info("💡 Live prices not yet connected — distances and P&L are calculated from entry price. "
                "Push `price_sync.py` to GitHub to enable 30-min price updates.")

    rows = []
    for p in active:
        tid = p["trade_id"]
        ticker = p.get("ticker")
        entry = f(p.get("entry_price"))
        stop = f(p.get("stop_loss"))
        qty = f(p.get("quantity"))
        targets = conds_for(tid, "target")
        t1_desc = targets[0].get("description") if targets else None
        t1_num = f(t1_desc)

        cmp, is_live = get_cmp(ticker, entry)
        cmp_display = f"₹{cmp:,.1f}" if cmp else "—"
        if not is_live and cmp:
            cmp_display += " (E)"  # mark as entry-based

        pnl = pct_change(entry, cmp) if cmp and entry else None
        dist_stop = pct_away(cmp, stop) if cmp and stop else pct_away(entry, stop)
        dist_target = pct_to_target(cmp, t1_num) if cmp and t1_num else pct_to_target(entry, t1_num) if t1_num else None

        t_status = thesis_status(ticker)
        r_urgency = review_urgency(p.get("review_date"))

        rows.append({
            "trade_id": tid,
            "Ticker": ticker,
            "CMP": cmp or 0,
            "CMP_display": cmp_display,
            "is_live": is_live,
            "Setup": p.get("setup") or "",
            "Entry": entry,
            "Stop": stop,
            "Dist to stop %": dist_stop,
            "P&L %": pnl,
            "Target": t1_desc or "—",
            "Dist to target %": dist_target,
            "Sleeve": p.get("sleeve") or "Unassigned",
            "Size %": f"{p['position_size_pct']}%" if p.get("position_size_pct") else "—",
            "Review": r_urgency,
            "thesis_status": t_status,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Dist to stop %", ascending=True, na_position="last")
        df = df[df["Sleeve"].isin(sel_sleeves)]
        if sel_setups:
            df = df[df["Setup"].isin(sel_setups)]
        df = df[df["thesis_status"].isin(sel_thesis)]

    if df.empty:
        st.info("No active positions match filters.")
    else:
        def hl_dist(v):
            if v is None or pd.isna(v):
                return ""
            if v <= 3:
                return "background-color: rgba(239,68,68,.2); color: #dc2626; font-weight: 700"
            if v <= 7:
                return "background-color: rgba(245,158,11,.15); color: #d97706; font-weight: 600"
            return ""

        def hl_pnl(v):
            if v is None or pd.isna(v):
                return ""
            if v >= 0:
                return "color: #16a34a; font-weight: 600"
            return "color: #dc2626; font-weight: 600"

        show = ["Ticker", "CMP_display", "Entry", "Stop", "Dist to stop %",
                "P&L %", "Target", "Dist to target %", "Sleeve", "Size %", "Review"]
        styled = (
            df[show].style
            .map(hl_dist, subset=["Dist to stop %"])
            .map(hl_pnl, subset=["P&L %"])
            .format({
                "Entry": lambda x: f"₹{x:,.0f}" if x else "—",
                "Stop": lambda x: f"₹{x:,.1f}" if x else "—",
                "Dist to stop %": lambda x: f"{x:.2f}" if x and not pd.isna(x) else "—",
                "P&L %": lambda x: f"{x:+.2f}" if x and not pd.isna(x) else "—",
                "Dist to target %": lambda x: f"{x:.1f}" if x and not pd.isna(x) else "—",
            })
        )
        st.dataframe(styled, use_container_width=True, hide_index=True,
                      column_config={"CMP_display": st.column_config.Column("CMP")})

        # ── Detail panel ────────────────────────────────────
        st.divider()
        sel_ticker = st.selectbox("Select position for detail", df["Ticker"].tolist())

        if sel_ticker:
            pos = next(p for p in active if p.get("ticker") == sel_ticker)
            tid = pos["trade_id"]
            entry = f(pos.get("entry_price")) or 0
            stop = f(pos.get("stop_loss")) or 0
            qty = f(pos.get("quantity")) or 0
            targets = conds_for(tid, "target")
            trailing = conds_for(tid, "trailing_stop")

            cmp, is_live = get_cmp(sel_ticker, entry)
            price_label = "CMP" if is_live else "Entry price"

            # Metric cards
            mc = st.columns(5)
            mc[0].metric(price_label, f"₹{cmp:,.2f}" if cmp else "—")

            d_stop = pct_away(cmp, stop)
            stop_delta = f"↑ {d_stop:.1f}% away" if d_stop else None
            mc[1].metric("Stop-loss", f"₹{stop:,.0f}",
                         delta=stop_delta, delta_color="inverse")

            if targets:
                t1 = targets[0].get("description", "")
                t1v = f(t1)
                if t1v:
                    dt = pct_to_target(cmp, t1v)
                    mc[2].metric("Target", f"₹{t1v:,.0f}",
                                delta=f"↑ {dt:.1f}% to go" if dt else None)
                else:
                    mc[2].metric("Target", t1)
            else:
                mc[2].metric("Target", "—")

            mc[3].metric("Quantity", f"{int(qty)}")

            if is_live and cmp:
                unrealized = (cmp - entry) * qty
                mc[4].metric("Unrealized P&L",
                             f"₹{unrealized:,.0f}",
                             delta=f"{pct_change(entry, cmp):+.1f}%" if pct_change(entry, cmp) is not None else None)
            else:
                mc[4].metric("Position cost", f"₹{entry * qty:,.0f}")

            # Review date warning
            r_urg = review_urgency(pos.get("review_date"))
            if r_urg:
                st.warning(f"Review date: {fmt_date(pos.get('review_date'))} — {r_urg}")

            # Target / trailing notes
            if len(targets) > 1 or trailing:
                parts = [f"🎯 {t.get('description')}" for t in targets]
                parts += [f"📐 {t.get('description')}" for t in trailing]
                st.info("  \n".join(parts))

            # Two-column detail: lots + news | research
            left, right = st.columns(2)

            with left:
                tlots = [l for l in D["lots"] if l.get("trade_id") == tid]
                if tlots:
                    st.markdown("**📦 Lots**")
                    for lot in tlots:
                        ld = lot.get("lot_date") or "date not set"
                        st.text(f"  {lot.get('qty')} shares @ ₹{lot.get('price')} — {ld}")

            with right:
                tnews = news_for(sel_ticker)
                if tnews:
                    st.markdown("**📰 Recent announcements**")
                    for n in tnews:
                        sev = n.get("severity_tag", "routine update")
                        src = (n.get("source") or "").upper()
                        hl = (n.get("headline") or "")[:80]
                        snippet = (n.get("body_snippet") or "")[:120]
                        dt = short_date(n.get("published_at"))
                        line = f"  {sev_icon(sev)} [{src}] {hl} ({dt})"
                        st.text(line)
                        if snippet:
                            st.caption(f"    ↳ {snippet}")
                else:
                    st.caption("No recent announcements for this ticker.")

                tres = research_for(sel_ticker)
                if tres:
                    st.markdown("**📝 Research history**")
                    for r in tres:
                        rtype = r.get("type", "")
                        sev = r.get("severity_tag", "")
                        summary = (r.get("summary") or "")[:100]
                        st.text(f"  {sev_icon(sev)} [{rtype}] {summary}…")

            # Fundamentals — full width
            snap = fund_snapshot_for(sel_ticker)
            st.markdown("**📊 Fundamentals (latest snapshot)**")
            if snap:
                st.caption(f"Period: {snap.get('period', '—')}")
                fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                pe = f(snap.get("pe"))
                roe = f(snap.get("roe"))
                ebit = f(snap.get("ebit_margin"))
                rev = f(snap.get("revenue_growth_yoy"))
                de = f(snap.get("debt_to_equity"))
                fc1.metric("PE", f"{pe:.1f}x" if pe else "—")
                fc2.metric("ROE", f"{roe:.1f}%" if roe else "—")
                fc3.metric("EBIT margin", f"{ebit:.1f}%" if ebit else "—")
                fc4.metric("Rev growth YoY", f"{rev:.1f}%" if rev else "—")
                fc5.metric("D/E", f"{de:.2f}x" if de else "—")
            else:
                st.caption("No fundamentals snapshot yet for this ticker.")


# ── TAB 2 — Announcements ──────────────────────────────────
with tab_ann:
    fc1, fc2 = st.columns([1, 1])
    with fc1:
        sev_mode = st.radio("Show", ["All", "Material+"], horizontal=True, key="ann_sev")
    with fc2:
        ann_tickers = sorted(set(n.get("ticker", "") for n in D["news"]))
        ann_ticker = st.selectbox("Ticker", ["All"] + ann_tickers, key="ann_tk")

    feed = D["news"]
    if sev_mode == "Material+":
        feed = [n for n in feed if n.get("severity_tag") != "routine update"]
    if ann_ticker != "All":
        feed = [n for n in feed if n.get("ticker") == ann_ticker]

    # Sort: latest first (published_at DESC), simple and predictable
    feed.sort(key=lambda n: n.get("published_at") or "", reverse=True)

    if not feed:
        st.info("No announcements match this filter.")
    else:
        for n in feed:
            sev = n.get("severity_tag", "routine update")
            ticker = n.get("ticker", "")
            headline = n.get("headline", "")
            snippet = n.get("body_snippet") or ""
            source = (n.get("source") or "").upper()
            verified = n.get("verified_status", "unverified")
            date = short_date(n.get("published_at"))
            reasoning = n.get("severity_reasoning") or ""
            border = sev_border(sev)
            bg = "rgba(239,68,68,.05)" if sev == "thesis-threatening" else \
                 "rgba(245,158,11,.04)" if sev == "material change" else "transparent"

            html = (
                f'<div style="padding:8px 12px; margin:3px 0; background:{bg}; '
                f'border-left:3px solid {border}; border-radius:3px">'
                f'<span style="font-size:13px">{sev_icon(sev)} '
                f'<strong>{ticker}</strong> [{source}] — {headline}</span><br/>'
                f'<span style="font-size:11px; color:#777">'
                f'{date} · {sev} · {verified}</span>'
            )
            if snippet:
                html += f'<br/><span style="font-size:12px; color:#444; line-height:1.5">↳ {snippet[:200]}</span>'
            if reasoning:
                html += f'<br/><span style="font-size:11px; color:#555; font-style:italic">⚡ {reasoning}</span>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

            if sev != "routine update" and verified == "unverified":
                if st.button(f"🔍 Verify across sources", key=f"verify_{n.get('id')}"):
                    st.info(f"Verification for \"{headline}\" — manual cross-check needed. "
                            f"Update verified_status in news_raw once confirmed/refuted.")


# ── TAB 3 — Closed trades ──────────────────────────────────
with tab_cl:
    if not D["closed"]:
        st.info("No closed trades yet.")
    else:
        for t in D["closed"]:
            ticker = t.get("ticker", "")
            sleeve = t.get("sleeve", "")
            ep = f(t.get("entry_price")) or 0
            xp = f(t.get("exit_price")) or 0
            reason = t.get("exit_reason", "")
            xdate = t.get("exit_date", "")

            ret = f(t.get("realized_return_pct"))
            if ret is None and ep:
                ret = round((xp - ep) / ep * 100, 2)

            win = (ret or 0) >= 0
            icon = "✅" if win else "❌"
            col = "#16a34a" if win else "#dc2626"
            ret_s = f"+{ret:.1f}%" if ret and ret > 0 else f"{ret:.1f}%" if ret else "—"

            st.markdown(
                f'<div style="padding:10px 14px; margin:4px 0; border-radius:6px; '
                f'border:1px solid #e5e7eb; display:flex; align-items:center; gap:14px; flex-wrap:wrap">'
                f'<span style="font-size:15px; font-weight:700; min-width:90px">{icon} {ticker}</span>'
                f'<span style="background:#818cf822; color:#818cf8; padding:2px 8px; '
                f'border-radius:4px; font-size:11px">{sleeve}</span>'
                f'<span style="color:#666; font-size:12px">₹{ep:,.0f} → ₹{xp:,.0f}</span>'
                f'<span style="background:{col}18; color:{col}; padding:2px 8px; '
                f'border-radius:4px; font-size:11px">{reason}</span>'
                f'<span style="color:{col}; font-weight:700; font-size:16px; '
                f'margin-left:auto">{ret_s}</span>'
                f'<span style="color:#888; font-size:11px">{xdate}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── TAB 4 — Research log ───────────────────────────────────
with tab_res:
    r_tickers = sorted(set(r.get("ticker", "") for r in D["research"]))
    r_filt = st.selectbox("Filter by ticker", ["All"] + r_tickers, key="res_filt")

    items = D["research"]
    if r_filt != "All":
        items = [r for r in items if r.get("ticker") == r_filt]

    for r in items:
        ticker = r.get("ticker", "")
        rtype = r.get("type", "")
        summary = r.get("summary", "")
        sev = r.get("severity_tag", "")
        conv = r.get("conviction_change", "")
        ldate = r.get("log_date", "")
        type_col = "#d97706" if "trigger" in rtype else "#6b7280"

        st.markdown(
            f'<div style="padding:10px 12px; margin:3px 0; border-bottom:1px solid #e5e7eb">'
            f'<span style="background:#3b82f622; color:#3b82f6; padding:2px 8px; '
            f'border-radius:4px; font-size:11px; font-weight:600">{ticker}</span> '
            f'<span style="background:{type_col}18; color:{type_col}; padding:2px 8px; '
            f'border-radius:4px; font-size:11px">{rtype}</span> '
            f'{sev_icon(sev)} '
            f'<span style="color:#888; font-size:11px; float:right">{ldate or ""}</span>'
            f'<p style="margin:6px 0 2px; font-size:12px; color:#555; line-height:1.6">{summary}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
