"""
Trading Dashboard — India Master Portfolio
==========================================
Full Streamlit dashboard connected to Supabase Postgres.

Design principles (from project README):
- Dense, sortable watchlist table as the home screen — not cards
- Default sort: distance-to-stop ascending (highest risk surfaces first)
- Conditional highlighting: red ≤3% to stop, amber ≤7%
- Click row → detail section (lots, targets, fundamentals, news)
- Sidebar filters: sleeve, thesis status
- Announcements feed: severity-sorted, ticker+source badges, Material+ filter
- Closed trades with entry→exit flow, return %, color-coded

Deploy to Streamlit Cloud: connect GitHub repo, set secrets in the
Streamlit Cloud dashboard (same SUPABASE_SERVICE_ROLE_KEY as GitHub).
"""

import streamlit as st
from supabase import create_client
import pandas as pd

# --- Config ---
st.set_page_config(
    page_title="Trading Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

SUPABASE_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# --- Supabase connection ---
@st.cache_resource
def get_supabase():
    key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(SUPABASE_URL, key)

@st.cache_data(ttl=60)  # refresh every 60 seconds
def load_data():
    sb = get_supabase()
    positions = sb.table("position_monitoring").select("*").order("trade_id").execute().data
    conditions = sb.table("position_monitoring_conditions").select("*").execute().data
    lots = sb.table("lots").select("*").order("lot_id").execute().data
    closed = sb.table("trade_history").select("*").order("exit_date", desc=True).execute().data
    news = sb.table("news_raw").select("*").order("published_at", desc=True).limit(100).execute().data
    research = sb.table("research_log").select("*").order("created_at", desc=True).execute().data
    return positions, conditions, lots, closed, news, research

positions, conditions, lots, closed_trades, news, research_log = load_data()

# --- Derived data ---
active = [p for p in positions if p.get("status") == "active"]
exited = [p for p in positions if p.get("status") == "exited"]

def dist_to_stop(entry, stop):
    if not entry or not stop or entry == 0:
        return None
    return round(((entry - stop) / entry) * 100, 2)

def dist_to_target(entry, target):
    if not entry or not target or entry == 0:
        return None
    return round(((target - entry) / entry) * 100, 2)

# Build watchlist dataframe
watchlist_rows = []
for p in active:
    trade_id = p.get("trade_id", "")
    ticker = p.get("ticker", "")
    entry = p.get("entry_price")
    stop = p.get("stop_loss")
    sleeve = p.get("sleeve") or "Unassigned"
    setup = p.get("setup") or ""
    qty = p.get("quantity")
    pct = p.get("position_size_pct")

    # Get targets for this trade
    trade_conds = [c for c in conditions if c.get("trade_id") == trade_id]
    targets = [c for c in trade_conds if c.get("condition_type") == "target"]
    trailing = [c for c in trade_conds if c.get("condition_type") == "trailing_stop"]
    target_1 = targets[0].get("description") if targets else None
    try:
        target_1_num = float(target_1) if target_1 else None
    except (ValueError, TypeError):
        target_1_num = None

    d_stop = dist_to_stop(entry, stop)
    d_target = dist_to_target(entry, target_1_num) if target_1_num else None
    value = round(entry * qty) if entry and qty else None

    watchlist_rows.append({
        "trade_id": trade_id,
        "Ticker": ticker,
        "Setup": setup,
        "Entry": entry,
        "Stop": stop,
        "Dist to Stop %": d_stop,
        "Target": target_1 or "—",
        "Dist to Target %": d_target,
        "Sleeve": sleeve,
        "Size %": f"{pct}%" if pct else "—",
        "Qty": qty,
        "Value": f"₹{value:,}" if value else "—",
        "Trailing": trailing[0].get("description") if trailing else "",
    })

df = pd.DataFrame(watchlist_rows)
if not df.empty:
    df = df.sort_values("Dist to Stop %", ascending=True, na_position="last")

# --- Custom CSS ---
st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    .metric-card {
        background: #1e2430;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .metric-value { font-size: 1.5rem; font-weight: 700; }
    .metric-label { font-size: 0.75rem; color: #8b92a0; }
    .severity-red { color: #f87171; font-weight: 600; }
    .severity-amber { color: #fbbf24; font-weight: 600; }
    .severity-green { color: #34d399; }
    .severity-muted { color: #8b92a0; }
    div[data-testid="stDataFrame"] td { font-size: 13px; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.title("📈 Trading Dashboard")
    st.caption("India Master Portfolio")
    st.divider()

    # Sleeve filter
    all_sleeves = sorted(set(p.get("sleeve") or "Unassigned" for p in active))
    selected_sleeves = st.multiselect("Sleeve", all_sleeves, default=all_sleeves)

    # Setup filter
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
# MAIN CONTENT — TABS
# ============================================================
# Portfolio overview metrics
total_deployed = sum((p.get("entry_price", 0) or 0) * (p.get("quantity", 0) or 0) for p in active)
material_news = [n for n in news if n.get("severity_tag") != "routine update"]

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Active Positions", len(active))
with col2:
    st.metric("Deployed Capital", f"₹{total_deployed/1000:.0f}K")
with col3:
    st.metric("Closed Trades", len(closed_trades))
with col4:
    wins = len([t for t in closed_trades if (t.get("realized_return_pct") or 0) >= 0])
    st.metric("Win Rate", f"{wins}/{len(closed_trades)}" if closed_trades else "—")
with col5:
    st.metric("Material Alerts", len(material_news), delta_color="inverse")

st.divider()

tab_positions, tab_news, tab_closed, tab_research = st.tabs([
    f"📊 Positions ({len(active)})",
    f"📰 Announcements ({len(news)})",
    f"📁 Closed Trades ({len(closed_trades)})",
    f"📝 Research Log ({len(research_log)})",
])


# ============================================================
# TAB 1 — POSITIONS (Watchlist Table)
# ============================================================
with tab_positions:
    # Apply filters
    filtered_df = df.copy()
    if not filtered_df.empty:
        filtered_df = filtered_df[filtered_df["Sleeve"].isin(selected_sleeves)]
        if selected_setups:
            filtered_df = filtered_df[filtered_df["Setup"].isin(selected_setups)]

    if filtered_df.empty:
        st.info("No active positions match the current filters.")
    else:
        # Color-code distance to stop
        def highlight_dist(val):
            if val is None or pd.isna(val):
                return ""
            if val <= 3:
                return "background-color: rgba(248, 113, 113, 0.25); color: #f87171; font-weight: 700"
            elif val <= 7:
                return "background-color: rgba(251, 191, 36, 0.15); color: #fbbf24; font-weight: 600"
            return ""

        display_cols = ["Ticker", "Setup", "Entry", "Stop", "Dist to Stop %", "Target", "Dist to Target %", "Sleeve", "Size %", "Value"]
        styled = (
            filtered_df[display_cols]
            .style
            .map(highlight_dist, subset=["Dist to Stop %"])
            .format({"Entry": "{:.0f}", "Stop": "{:.1f}", "Dist to Stop %": "{:.2f}", "Dist to Target %": "{:.1f}"}, na_rep="—")
        )
        st.dataframe(styled, use_container_width=True, hide_index=True, height=35 * len(filtered_df) + 38)

    # --- Detail Panel ---
    st.divider()
    if not filtered_df.empty:
        selected_ticker = st.selectbox("Select position for detail", filtered_df["Ticker"].tolist())

        if selected_ticker:
            sel_row = next((r for r in watchlist_rows if r["Ticker"] == selected_ticker), None)
            sel_pos = next((p for p in active if p.get("ticker") == selected_ticker), None)
            if sel_row and sel_pos:
                trade_id = sel_pos.get("trade_id")

                # Metrics row
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    st.metric("Entry Price", f"₹{sel_pos.get('entry_price', 0):,.0f}")
                with c2:
                    d = sel_row["Dist to Stop %"]
                    st.metric("Stop Loss", f"₹{sel_pos.get('stop_loss', 0):,.1f}",
                              delta=f"{d:.1f}% away" if d else None,
                              delta_color="inverse")
                with c3:
                    st.metric("Target", sel_row["Target"])
                with c4:
                    st.metric("Quantity", sel_pos.get("quantity", "—"))
                with c5:
                    st.metric("Position Value", sel_row["Value"])

                # Sub-sections in columns
                col_left, col_right = st.columns(2)

                with col_left:
                    # Lots
                    trade_lots = [l for l in lots if l.get("trade_id") == trade_id]
                    if trade_lots:
                        st.subheader("📦 Lots")
                        for lot in trade_lots:
                            st.text(f"{lot.get('qty')} shares @ ₹{lot.get('price')} — {lot.get('lot_date') or 'date not set'}")

                    # Conditions (targets + trailing stops)
                    trade_conds = [c for c in conditions if c.get("trade_id") == trade_id]
                    if trade_conds:
                        st.subheader("🎯 Targets & Conditions")
                        for cond in trade_conds:
                            ctype = cond.get("condition_type", "")
                            icon = "🎯" if ctype == "target" else "📐"
                            st.text(f"{icon} {ctype}: {cond.get('description', '')}")

                    # Trailing stop note
                    if sel_row.get("Trailing"):
                        st.info(f"📐 Trailing plan: {sel_row['Trailing']}")

                with col_right:
                    # Recent news for this ticker
                    ticker_news = [n for n in news if n.get("ticker") == selected_ticker][:8]
                    if ticker_news:
                        st.subheader("📰 Recent Announcements")
                        for n in ticker_news:
                            sev = n.get("severity_tag", "routine update")
                            icon = "🔴" if sev == "thesis-threatening" else "🟡" if sev == "material change" else "⚪"
                            date_str = ""
                            if n.get("published_at"):
                                try:
                                    from datetime import datetime
                                    dt = datetime.fromisoformat(n["published_at"].replace("Z", "+00:00"))
                                    date_str = dt.strftime("%d %b")
                                except:
                                    date_str = str(n["published_at"])[:10]
                            headline = n.get("headline", "")[:80]
                            source = n.get("source", "")
                            st.text(f"{icon} [{source}] {headline} ({date_str})")
                    else:
                        st.caption("No recent announcements for this ticker.")

                    # Research log entries for this ticker
                    ticker_research = [r for r in research_log if r.get("ticker") == selected_ticker][:5]
                    if ticker_research:
                        st.subheader("📝 Research History")
                        for r in ticker_research:
                            rtype = r.get("type", "")
                            summary = r.get("summary", "")[:150]
                            sev = r.get("severity_tag", "")
                            icon = "🔴" if sev == "thesis-threatening" else "🟡" if sev == "material change" else "📋"
                            st.text(f"{icon} [{rtype}] {summary}...")


# ============================================================
# TAB 2 — ANNOUNCEMENTS FEED
# ============================================================
with tab_news:
    # Filter controls
    col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
    with col_f1:
        severity_filter = st.radio("Show", ["All", "Material+"], horizontal=True)
    with col_f2:
        all_news_tickers = sorted(set(n.get("ticker", "") for n in news))
        ticker_filter = st.selectbox("Ticker", ["All"] + all_news_tickers)

    filtered_news = news
    if severity_filter == "Material+":
        filtered_news = [n for n in filtered_news if n.get("severity_tag") != "routine update"]
    if ticker_filter != "All":
        filtered_news = [n for n in filtered_news if n.get("ticker") == ticker_filter]

    if not filtered_news:
        st.info("No announcements match the current filter.")
    else:
        for n in filtered_news:
            sev = n.get("severity_tag", "routine update")
            ticker = n.get("ticker", "")
            headline = n.get("headline", "")
            source = n.get("source", "")
            url = n.get("url", "")
            verified = n.get("verified_status", "unverified")

            # Severity styling
            if sev == "thesis-threatening":
                icon = "🔴"
                bg = "rgba(248, 113, 113, 0.08)"
            elif sev == "material change":
                icon = "🟡"
                bg = "rgba(251, 191, 36, 0.06)"
            else:
                icon = "⚪"
                bg = "transparent"

            date_str = ""
            if n.get("published_at"):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(n["published_at"].replace("Z", "+00:00"))
                    date_str = dt.strftime("%d %b %Y")
                except:
                    date_str = str(n["published_at"])[:10]

            source_badge = f"[{source.upper()}]" if source else ""

            with st.container():
                st.markdown(
                    f'<div style="padding: 8px 12px; margin: 2px 0; background: {bg}; border-radius: 4px; border-left: 3px solid {"#f87171" if sev == "thesis-threatening" else "#fbbf24" if sev == "material change" else "#2a2f3a"}">'
                    f'<span style="font-size: 13px">{icon} <strong>{ticker}</strong> {source_badge} — {headline}</span>'
                    f'<br/><span style="font-size: 11px; color: #8b92a0">{date_str} · {sev} · {verified}</span>'
                    f'</div>',
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
            entry_p = t.get("entry_price")
            exit_p = t.get("exit_price")
            reason = t.get("exit_reason", "")
            exit_date = t.get("exit_date", "")
            ret = t.get("realized_return_pct")

            if ret is None and entry_p and exit_p:
                ret = round((exit_p - entry_p) / entry_p * 100, 2)

            is_win = (ret or 0) >= 0
            color = "#34d399" if is_win else "#f87171"
            icon = "✅" if is_win else "❌"
            ret_str = f"+{ret}%" if ret and ret > 0 else f"{ret}%" if ret else "—"

            st.markdown(
                f'<div style="padding: 10px 12px; margin: 4px 0; border-radius: 6px; border: 1px solid #2a2f3a; display: flex; align-items: center; gap: 16px;">'
                f'<span style="font-size: 15px; font-weight: 700; width: 100px">{icon} {ticker}</span>'
                f'<span style="background: #818cf822; color: #818cf8; padding: 2px 8px; border-radius: 4px; font-size: 11px">{sleeve}</span>'
                f'<span style="color: #8b92a0; font-size: 12px">₹{entry_p} → ₹{exit_p}</span>'
                f'<span style="background: {color}22; color: {color}; padding: 2px 8px; border-radius: 4px; font-size: 11px">{reason}</span>'
                f'<span style="color: {color}; font-weight: 700; font-size: 15px; margin-left: auto">{ret_str}</span>'
                f'<span style="color: #8b92a0; font-size: 11px">{exit_date}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ============================================================
# TAB 4 — RESEARCH LOG
# ============================================================
with tab_research:
    # Filter by ticker
    all_research_tickers = sorted(set(r.get("ticker", "") for r in research_log))
    research_ticker_filter = st.selectbox("Filter by ticker", ["All"] + all_research_tickers, key="research_filter")

    filtered_research = research_log
    if research_ticker_filter != "All":
        filtered_research = [r for r in research_log if r.get("ticker") == research_ticker_filter]

    for r in filtered_research:
        ticker = r.get("ticker", "")
        rtype = r.get("type", "")
        summary = r.get("summary", "")
        sev = r.get("severity_tag", "")
        conv = r.get("conviction_change", "")
        log_date = r.get("log_date", "")

        sev_icon = "🔴" if sev == "thesis-threatening" else "🟡" if sev == "material change" else "📋"
        type_color = "#fbbf24" if "trigger" in (rtype or "") else "#8b92a0"

        st.markdown(
            f'<div style="padding: 10px 12px; margin: 4px 0; border-bottom: 1px solid #2a2f3a">'
            f'<span style="background: #4f8ff722; color: #4f8ff7; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600">{ticker}</span> '
            f'<span style="background: {type_color}22; color: {type_color}; padding: 2px 8px; border-radius: 4px; font-size: 11px">{rtype}</span> '
            f'{sev_icon} '
            f'<span style="color: #8b92a0; font-size: 11px; float: right">{log_date or ""}</span>'
            f'<p style="margin: 6px 0 0; font-size: 12px; color: #8b92a0; line-height: 1.5">{summary}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
