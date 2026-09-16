"""
Trading Dashboard — India Master Portfolio
Live portfolio monitoring connected to Supabase Postgres.
"""

import streamlit as st
from supabase import create_client
import pandas as pd
from datetime import datetime

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
        "closed": sb.table("trade_history").select("*").order("exit_date", desc=True).execute().data or [],
        "news": sb.table("news_raw").select("*").order("published_at", desc=True).limit(100).execute().data or [],
        "research": sb.table("research_log").select("*").order("created_at", desc=True).execute().data or [],
        "fundsnap": sb.table("fundamentals_snapshots").select("*").order("pulled_at", desc=True).execute().data or [],
    }


D = load()
active = [p for p in D["pos"] if p.get("status") == "active"]
exited = [p for p in D["pos"] if p.get("status") == "exited"]


# ── Helpers ─────────────────────────────────────────────────
def f(v):
    """Convert Supabase value to float, None-safe."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def pct_away(entry, ref):
    e, r = f(entry), f(ref)
    if not e or not r or e == 0:
        return None
    return round(abs(e - r) / e * 100, 2)


def pct_to_target(entry, target):
    e, t = f(entry), f(target)
    if not e or not t or e == 0:
        return None
    return round((t - e) / e * 100, 1)


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


def fund_check_for(ticker):
    return next((r for r in D["research"]
                 if r.get("ticker") == ticker and r.get("type") == "fundamentals check"), None)


def fund_snapshot_for(ticker):
    """Latest structured fundamentals snapshot (from fundamentals_snapshots table)."""
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


# ── Sidebar ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 Trading Dashboard")
    st.caption("India Master Portfolio · Live from Supabase")
    st.divider()

    sleeves = sorted(set(p.get("sleeve") or "Unassigned" for p in active))
    sel_sleeves = st.multiselect("Sleeve", sleeves, default=sleeves)

    setups = sorted(set(p.get("setup") or "" for p in active if p.get("setup")))
    sel_setups = st.multiselect("Setup type", setups, default=setups)

    st.divider()
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Sheet → Postgres sync: every 30 min")
    st.caption("Announcements monitor: hourly")


# ── Header metrics ──────────────────────────────────────────
total_val = sum((f(p.get("entry_price")) or 0) * (f(p.get("quantity")) or 0) for p in active)
mat_news = [n for n in D["news"] if n.get("severity_tag") != "routine update"]
wins = sum(1 for t in D["closed"]
           if (f(t.get("exit_price")) or 0) > (f(t.get("entry_price")) or 0))

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Active positions", len(active))
m2.metric("Deployed capital", f"₹{total_val / 1000:,.0f}K")
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
    rows = []
    for p in active:
        tid = p["trade_id"]
        entry = f(p.get("entry_price"))
        stop = f(p.get("stop_loss"))
        qty = f(p.get("quantity"))
        targets = conds_for(tid, "target")
        t1_desc = targets[0].get("description") if targets else None
        t1_num = f(t1_desc)

        rows.append({
            "trade_id": tid,
            "Ticker": p.get("ticker"),
            "Setup": p.get("setup") or "",
            "Entry": entry,
            "Stop": stop,
            "Dist to stop %": pct_away(entry, stop),
            "Target": t1_desc or "—",
            "Dist to target %": pct_to_target(entry, t1_num) if t1_num else None,
            "Sleeve": p.get("sleeve") or "Unassigned",
            "Size %": f"{p['position_size_pct']}%" if p.get("position_size_pct") else "—",
            "Value": f"₹{int(entry * qty):,}" if entry and qty else "—",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Dist to stop %", ascending=True, na_position="last")
        df = df[df["Sleeve"].isin(sel_sleeves)]
        if sel_setups:
            df = df[df["Setup"].isin(sel_setups)]

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

        show = ["Ticker", "Setup", "Entry", "Stop", "Dist to stop %",
                "Target", "Dist to target %", "Sleeve", "Size %", "Value"]
        styled = (
            df[show].style
            .map(hl_dist, subset=["Dist to stop %"])
            .format({
                "Entry": lambda x: f"₹{x:,.0f}" if x else "—",
                "Stop": lambda x: f"₹{x:,.1f}" if x else "—",
                "Dist to stop %": lambda x: f"{x:.2f}" if x and not pd.isna(x) else "—",
                "Dist to target %": lambda x: f"{x:.1f}" if x and not pd.isna(x) else "—",
            })
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

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

            st.caption("⚠️ Price shown is entry price — live price feed not yet connected.")

            # Metric cards
            mc = st.columns(5)
            mc[0].metric("Current price", f"₹{entry:,.2f}")
            d_stop = pct_away(entry, stop)
            mc[1].metric("Stop-loss", f"₹{stop:,.0f}",
                         delta=f"↑ {d_stop:.1f}% away" if d_stop else None,
                         delta_color="inverse")

            if targets:
                t1 = targets[0].get("description", "")
                t1v = f(t1)
                if t1v:
                    dt = pct_to_target(entry, t1v)
                    mc[2].metric("Target", f"₹{t1v:,.0f}",
                                delta=f"↑ {dt:.1f}% to go" if dt else None)
                else:
                    mc[2].metric("Target", t1)
            else:
                mc[2].metric("Target", "—")

            mc[3].metric("Quantity", f"{int(qty)}")
            mc[4].metric("Position value", f"₹{entry * qty:,.0f}")

            # Target / trailing notes
            if len(targets) > 1 or trailing:
                parts = [f"🎯 {t.get('description')}" for t in targets]
                parts += [f"📐 {t.get('description')}" for t in trailing]
                st.info("  \n".join(parts))

            # Two-column detail: lots + news | research
            left, right = st.columns(2)

            with left:
                # Lots
                tlots = [l for l in D["lots"] if l.get("trade_id") == tid]
                if tlots:
                    st.markdown("**📦 Lots**")
                    for lot in tlots:
                        ld = lot.get("lot_date") or "date not set"
                        st.text(f"  {lot.get('qty')} shares @ ₹{lot.get('price')} — {ld}")

            with right:
                # News
                tnews = news_for(sel_ticker)
                if tnews:
                    st.markdown("**📰 Recent announcements**")
                    for n in tnews:
                        sev = n.get("severity_tag", "routine update")
                        src = (n.get("source") or "").upper()
                        hl = (n.get("headline") or "")[:65]
                        dt = short_date(n.get("published_at"))
                        st.text(f"  {sev_icon(sev)} [{src}] {hl} ({dt})")
                else:
                    st.caption("No recent announcements for this ticker.")

                # Research history
                tres = research_for(sel_ticker)
                if tres:
                    st.markdown("**📝 Research history**")
                    for r in tres:
                        rtype = r.get("type", "")
                        sev = r.get("severity_tag", "")
                        summary = (r.get("summary") or "")[:100]
                        st.text(f"  {sev_icon(sev)} [{rtype}] {summary}…")

            # Fundamentals — FULL WIDTH below the two-column block
            fc = fund_check_for(sel_ticker)
            if fc:
                st.markdown(f"**Last research log:** {fc.get('type')} ({fc.get('severity_tag', '')})")

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

    # Sort: material/thesis-threatening first
    sev_order = {"thesis-threatening": 0, "material change": 1, "routine update": 2}
    feed.sort(key=lambda n: (sev_order.get(n.get("severity_tag", "routine update"), 2),
                              n.get("published_at") or ""), reverse=False)
    # Within same severity, most recent first
    feed.sort(key=lambda n: sev_order.get(n.get("severity_tag", "routine update"), 2))

    if not feed:
        st.info("No announcements match this filter.")
    else:
        for n in feed:
            sev = n.get("severity_tag", "routine update")
            ticker = n.get("ticker", "")
            headline = n.get("headline", "")
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
            if reasoning:
                html += f'<br/><span style="font-size:11px; color:#555">↳ {reasoning}</span>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

            # Verify button for material+ items
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
