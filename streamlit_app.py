"""
Trading Dashboard — India Master Portfolio
==========================================
Streamlit dashboard connected to Supabase Postgres.
Matches Phase 2 prototype design with live data.
"""

import streamlit as st
from supabase import create_client
import pandas as pd
from datetime import datetime

st.set_page_config(
    page_title="Trading Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

@st.cache_resource
def get_sb():
    return create_client(SUPABASE_URL, st.secrets["SUPABASE_SERVICE_ROLE_KEY"])

@st.cache_data(ttl=60)
def load_data():
    sb = get_sb()
    pos = sb.table("position_monitoring").select("*").order("trade_id").execute().data or []
    conds = sb.table("position_monitoring_conditions").select("*").execute().data or []
    lots_data = sb.table("lots").select("*").order("lot_id").execute().data or []
    closed = sb.table("trade_history").select("*").order("exit_date", desc=True).execute().data or []
    news = sb.table("news_raw").select("*").order("published_at", desc=True).limit(100).execute().data or []
    research = sb.table("research_log").select("*").order("created_at", desc=True).execute().data or []
    return pos, conds, lots_data, closed, news, research

positions, conditions, lots, closed_trades, news, research_log = load_data()

active = [p for p in positions if p.get("status") == "active"]
material_news = [n for n in news if n.get("severity_tag") != "routine update"]

# --- Helper functions ---
def dist_pct(entry, stop):
    if not entry or not stop or float(entry) == 0:
        return None
    return round(((float(entry) - float(stop)) / float(entry)) * 100, 2)

def dist_to_target(entry, target):
    if not entry or not target or float(entry) == 0:
        return None
    return round(((float(target) - float(entry)) / float(entry)) * 100, 1)

def get_targets(trade_id):
    return [c for c in conditions if c.get("trade_id") == trade_id and c.get("condition_type") == "target"]

def get_trailing(trade_id):
    return [c for c in conditions if c.get("trade_id") == trade_id and c.get("condition_type") == "trailing_stop"]

def get_ticker_news(ticker, limit=5):
    return [n for n in news if n.get("ticker") == ticker][:limit]

def get_ticker_research(ticker, limit=5):
    return [r for r in research_log if r.get("ticker") == ticker][:limit]

def format_date(d):
    if not d:
        return ""
    try:
        dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except:
        return str(d)[:10]


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.title("📈 Trading Dashboard")
    st.caption("India Master Portfolio")
    st.divider()

    all_sleeves = sorted(set(p.get("sleeve") or "Unassigned" for p in active))
    selected_sleeves = st.multiselect("Sleeve", all_sleeves, default=all_sleeves)

    all_setups = sorted(set(p.get("setup") or "" for p in active if p.get("setup")))
    selected_setups = st.multiselect("Setup Type", all_setups, default=all_setups)

    st.divider()
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Auto-synced from Google Sheet every 30 min")
    st.caption("Announcements monitored hourly")


# ============================================================
# HEADER METRICS
# ============================================================
total_deployed = sum(
    (float(p.get("entry_price") or 0)) * (float(p.get("quantity") or 0))
    for p in active
)
wins = len([t for t in closed_trades if float(t.get("realized_return_pct") or t.get("exit_price", 0) or 0) >= float(t.get("entry_price", 1) or 1)])

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Active Positions", len(active))
c2.metric("Deployed Capital", f"₹{total_deployed/1000:,.0f}K")
c3.metric("Closed Trades", len(closed_trades))
c4.metric("Win Rate", f"{sum(1 for t in closed_trades if float(t.get('exit_price') or 0) > float(t.get('entry_price') or 0))}/{len(closed_trades)}" if closed_trades else "—")
c5.metric("Material Alerts", len(material_news))

st.divider()

# ============================================================
# TABS
# ============================================================
tab_pos, tab_news, tab_closed, tab_research = st.tabs([
    f"📊 Positions ({len(active)})",
    f"📰 Announcements ({len(news)})",
    f"📁 Closed Trades ({len(closed_trades)})",
    f"📝 Research Log ({len(research_log)})",
])

# ============================================================
# TAB 1 — POSITIONS
# ============================================================
with tab_pos:
    # Build table data
    rows = []
    for p in active:
        tid = p.get("trade_id", "")
        ticker = p.get("ticker", "")
        entry = float(p.get("entry_price") or 0)
        stop = float(p.get("stop_loss") or 0)
        sleeve = p.get("sleeve") or "Unassigned"
        setup = p.get("setup") or ""
        qty = float(p.get("quantity") or 0)
        pct = p.get("position_size_pct")

        targets = get_targets(tid)
        t1 = targets[0].get("description") if targets else None
        try:
            t1_num = float(t1)
        except (ValueError, TypeError):
            t1_num = None

        d_stop = dist_pct(entry, stop)
        d_target = dist_to_target(entry, t1_num) if t1_num else None
        value = round(entry * qty) if entry and qty else None

        rows.append({
            "trade_id": tid,
            "Ticker": ticker,
            "Setup": setup,
            "Entry": entry,
            "Stop": stop,
            "Dist to Stop %": d_stop,
            "Target": t1 or "—",
            "Dist to Target %": d_target,
            "Sleeve": sleeve,
            "Size %": f"{pct}%" if pct else "—",
            "Qty": int(qty) if qty else "—",
            "Value": f"₹{value:,}" if value else "—",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Dist to Stop %", ascending=True, na_position="last")

        # Apply filters
        filtered = df[df["Sleeve"].isin(selected_sleeves)]
        if selected_setups:
            filtered = filtered[filtered["Setup"].isin(selected_setups)]

        if filtered.empty:
            st.info("No positions match filters.")
        else:
            # Highlight distance to stop
            def hl(val):
                if val is None or pd.isna(val):
                    return ""
                if val <= 3:
                    return "background-color: rgba(248,113,113,0.25); color: #f87171; font-weight:700"
                if val <= 7:
                    return "background-color: rgba(251,191,36,0.15); color: #fbbf24; font-weight:600"
                return ""

            display = ["Ticker", "Setup", "Entry", "Stop", "Dist to Stop %", "Target", "Dist to Target %", "Sleeve", "Size %", "Value"]
            styled = (
                filtered[display].style
                .map(hl, subset=["Dist to Stop %"])
                .format({"Entry": "{:.0f}", "Stop": "{:.1f}", "Dist to Stop %": "{:.2f}", "Dist to Target %": "{:.1f}"}, na_rep="—")
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)

            # ---- DETAIL PANEL ----
            st.divider()
            selected = st.selectbox("Select position for detail", filtered["Ticker"].tolist())

            if selected:
                p = next((x for x in active if x.get("ticker") == selected), None)
                if p:
                    tid = p.get("trade_id")
                    entry = float(p.get("entry_price") or 0)
                    stop = float(p.get("stop_loss") or 0)
                    qty = float(p.get("quantity") or 0)
                    targets = get_targets(tid)
                    trailing = get_trailing(tid)

                    # Warning about no live price
                    st.warning("⚠️ Current price uses entry price — live price feed not yet connected (Phase 3).")

                    # Metrics row
                    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
                    mc1.metric("Current Price", f"₹{entry:,.2f}")

                    d = dist_pct(entry, stop)
                    mc2.metric("Stop-Loss", f"₹{stop:,.0f}", delta=f"{d:.1f}% away" if d else None, delta_color="inverse")

                    if targets:
                        t1_desc = targets[0].get("description", "")
                        try:
                            t1_val = float(t1_desc)
                            dt = dist_to_target(entry, t1_val)
                            mc3.metric("Target", f"₹{t1_val:,.0f}", delta=f"{dt:.1f}% to go" if dt else None)
                        except:
                            mc3.metric("Target", t1_desc)
                    else:
                        mc3.metric("Target", "—")

                    mc4.metric("Quantity", f"{int(qty)}")
                    mc5.metric("Position Value", f"₹{entry * qty:,.0f}")

                    # Target notes
                    if len(targets) > 1 or trailing:
                        notes = []
                        for t in targets:
                            notes.append(f"🎯 Target: {t.get('description')}")
                        for t in trailing:
                            notes.append(f"📐 Trailing: {t.get('description')}")
                        st.info("  \n".join(notes))

                    # Two columns: left = lots + conditions, right = news + research
                    col_l, col_r = st.columns(2)

                    with col_l:
                        # Lots
                        trade_lots = [l for l in lots if l.get("trade_id") == tid]
                        if trade_lots:
                            st.subheader("📦 Lots")
                            for lot in trade_lots:
                                lot_date = lot.get("lot_date") or "date not set"
                                st.text(f"{lot.get('qty')} shares @ ₹{lot.get('price')} — {lot_date}")

                        # Fundamentals from research log (parsed from text)
                        ticker_research = get_ticker_research(selected)
                        fund_entry = next((r for r in ticker_research if r.get("type") == "fundamentals check"), None)
                        if fund_entry:
                            st.subheader("📊 Last Fundamentals Check")
                            st.caption(f"Type: {fund_entry.get('type')} | Severity: {fund_entry.get('severity_tag', '')}")
                            st.text(fund_entry.get("summary", "")[:300])
                        else:
                            st.subheader("📊 Fundamentals")
                            st.caption("No fundamentals check in research log yet.")

                    with col_r:
                        # News
                        ticker_news = get_ticker_news(selected, 8)
                        if ticker_news:
                            st.subheader("📰 Recent Announcements")
                            for n in ticker_news:
                                sev = n.get("severity_tag", "routine update")
                                icon = "🔴" if sev == "thesis-threatening" else "🟡" if sev == "material change" else "⚪"
                                headline = n.get("headline", "")[:70]
                                source = n.get("source", "").upper()
                                date = format_date(n.get("published_at"))
                                st.text(f"{icon} [{source}] {headline} ({date})")
                        else:
                            st.caption("No recent announcements.")

                        # Research log
                        ticker_research = get_ticker_research(selected, 5)
                        if ticker_research:
                            st.subheader("📝 Research History")
                            for r in ticker_research:
                                rtype = r.get("type", "")
                                sev = r.get("severity_tag", "")
                                icon = "🔴" if sev == "thesis-threatening" else "🟡" if sev == "material change" else "📋"
                                summary = r.get("summary", "")[:120]
                                st.text(f"{icon} [{rtype}] {summary}...")


# ============================================================
# TAB 2 — ANNOUNCEMENTS
# ============================================================
with tab_news:
    fc1, fc2 = st.columns([1, 1])
    with fc1:
        sev_filter = st.radio("Show", ["All", "Material+"], horizontal=True)
    with fc2:
        news_tickers = sorted(set(n.get("ticker", "") for n in news))
        ticker_filter = st.selectbox("Ticker", ["All"] + news_tickers)

    filtered_news = news
    if sev_filter == "Material+":
        filtered_news = [n for n in filtered_news if n.get("severity_tag") != "routine update"]
    if ticker_filter != "All":
        filtered_news = [n for n in filtered_news if n.get("ticker") == ticker_filter]

    if not filtered_news:
        st.info("No announcements match filter.")
    else:
        for n in filtered_news:
            sev = n.get("severity_tag", "routine update")
            ticker = n.get("ticker", "")
            headline = n.get("headline", "")
            source = n.get("source", "").upper()
            verified = n.get("verified_status", "unverified")
            date = format_date(n.get("published_at"))

            if sev == "thesis-threatening":
                icon, border_col = "🔴", "#f87171"
            elif sev == "material change":
                icon, border_col = "🟡", "#fbbf24"
            else:
                icon, border_col = "⚪", "#e5e7eb"

            st.markdown(
                f'<div style="padding:8px 12px; margin:2px 0; border-left:3px solid {border_col}; border-radius:4px;">'
                f'<span style="font-size:13px">{icon} <strong>{ticker}</strong> [{source}] — {headline}</span>'
                f'<br/><span style="font-size:11px; color:#888">{date} · {sev} · {verified}</span></div>',
                unsafe_allow_html=True,
            )


# ============================================================
# TAB 3 — CLOSED TRADES
# ============================================================
with tab_closed:
    if not closed_trades:
        st.info("No closed trades yet.")
    else:
        for t in closed_trades:
            ticker = t.get("ticker", "")
            sleeve = t.get("sleeve", "")
            entry_p = float(t.get("entry_price") or 0)
            exit_p = float(t.get("exit_price") or 0)
            reason = t.get("exit_reason", "")
            exit_date = t.get("exit_date", "")

            ret = t.get("realized_return_pct")
            if ret is None and entry_p:
                ret = round((exit_p - entry_p) / entry_p * 100, 2)
            else:
                ret = float(ret) if ret else 0

            is_win = ret >= 0
            icon = "✅" if is_win else "❌"
            color = "#34d399" if is_win else "#f87171"
            ret_str = f"+{ret}%" if ret > 0 else f"{ret}%"

            st.markdown(
                f'<div style="padding:10px 12px; margin:4px 0; border-radius:6px; border:1px solid #ddd;">'
                f'<span style="font-size:15px; font-weight:700">{icon} {ticker}</span> '
                f'<span style="background:#818cf822; color:#818cf8; padding:2px 8px; border-radius:4px; font-size:11px">{sleeve}</span> '
                f'<span style="color:#888; font-size:12px; margin:0 8px">₹{entry_p:.0f} → ₹{exit_p:.0f}</span> '
                f'<span style="background:{color}22; color:{color}; padding:2px 8px; border-radius:4px; font-size:11px">{reason}</span> '
                f'<span style="color:{color}; font-weight:700; font-size:15px; float:right">{ret_str}</span> '
                f'<span style="color:#888; font-size:11px; float:right; margin-right:12px">{exit_date}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ============================================================
# TAB 4 — RESEARCH LOG
# ============================================================
with tab_research:
    r_tickers = sorted(set(r.get("ticker", "") for r in research_log))
    r_filter = st.selectbox("Filter by ticker", ["All"] + r_tickers, key="rlog_filter")

    filtered_r = research_log
    if r_filter != "All":
        filtered_r = [r for r in research_log if r.get("ticker") == r_filter]

    for r in filtered_r:
        ticker = r.get("ticker", "")
        rtype = r.get("type", "")
        summary = r.get("summary", "")
        sev = r.get("severity_tag", "")
        log_date = r.get("log_date", "")

        sev_icon = "🔴" if sev == "thesis-threatening" else "🟡" if sev == "material change" else "📋"
        type_color = "#fbbf24" if "trigger" in (rtype or "") else "#888"

        st.markdown(
            f'<div style="padding:10px 12px; margin:4px 0; border-bottom:1px solid #eee">'
            f'<span style="background:#4f8ff722; color:#4f8ff7; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600">{ticker}</span> '
            f'<span style="background:{type_color}22; color:{type_color}; padding:2px 8px; border-radius:4px; font-size:11px">{rtype}</span> '
            f'{sev_icon} '
            f'<span style="color:#888; font-size:11px; float:right">{log_date or ""}</span>'
            f'<p style="margin:6px 0 0; font-size:12px; color:#555; line-height:1.5">{summary}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
