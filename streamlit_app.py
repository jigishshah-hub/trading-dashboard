"""
India Master Portfolio — V2 Analytics & Decision Dashboard
Merged dashboard: V2 decision architecture + interactive analytics.
Live from Supabase Postgres.
"""

import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
import math

# ── Config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="India Master Portfolio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SB_URL = "https://egfsjboyzajyemjqazot.supabase.co"

# ── Theme colors ────────────────────────────────────────────
SLEEVE_COLORS = {
    "Anchor": "#355ec9",
    "Power Sleeve": "#eb6834",
    "Tracker": "#1baf7a",
    "Multibagger List": "#eda100",
}
SECTOR_COLORS = ["#355ec9", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6d4bc3", "#d95926"]
GREEN = "#237a35"
RED = "#c83b3b"
AMBER = "#a76a00"
MUTED = "#667085"


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
        "stocks": sb.table("stocks").select("ticker, sector, current_price, price_updated_at").execute().data or [],
        "closed": sb.table("trade_history").select("*").order("exit_date", desc=True).execute().data or [],
        "news": sb.table("news_raw").select("*").order("published_at", desc=True).limit(200).execute().data or [],
        "research": sb.table("research_log").select("*").order("created_at", desc=True).execute().data or [],
        "fundsnap": sb.table("fundamentals_snapshots").select("*").order("pulled_at", desc=True).execute().data or [],
    }


D = load()

active = [p for p in D["pos"] if p.get("status") == "active"]
exited = [p for p in D["pos"] if p.get("status") == "exited"]

# Build price + sector lookups
price_map = {}
sector_map = {}
for s in D["stocks"]:
    sector_map[s["ticker"]] = s.get("sector") or "Unknown"
    cp = s.get("current_price")
    pu = s.get("price_updated_at")
    if cp is not None:
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
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def get_cmp(ticker, entry_price=None):
    if ticker in price_map:
        return price_map[ticker], True
    if entry_price is not None:
        return entry_price, False
    return None, False


def fmt(n):
    if n is None:
        return "—"
    if abs(n) >= 100000:
        return f"₹{n / 100000:.2f}L"
    return f"₹{n:,.0f}"


def fmt_pct(n):
    if n is None:
        return "—"
    return f"{'+' if n >= 0 else ''}{n:.2f}%"


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


def days_since(date_str):
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(str(date_str)).date()
        return (datetime.now().date() - d).days
    except Exception:
        return None


def sev_icon(tag):
    if tag == "thesis-threatening":
        return "🔴"
    if tag == "material change":
        return "🟡"
    return "⚪"


def review_urgency(review_date):
    if not review_date:
        return ""
    try:
        rd = datetime.fromisoformat(str(review_date)).date()
        days_until = (rd - datetime.now().date()).days
        if days_until < 0:
            return "🔴 overdue"
        if days_until <= 3:
            return "⏰ soon"
        return ""
    except Exception:
        return ""


# ── Compute enriched positions ──────────────────────────────
positions = []
for p in active:
    ticker = p.get("ticker")
    entry = f(p.get("entry_price")) or 0
    stop = f(p.get("stop_loss")) or 0
    qty = f(p.get("quantity")) or 0
    cmp, is_live = get_cmp(ticker, entry)
    price = cmp or entry

    cost_basis = entry * qty
    current_value = price * qty
    pnl = current_value - cost_basis
    pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0
    risk_per_share = entry - stop if stop else 0
    capital_at_risk = risk_per_share * qty
    stop_dist_pct = ((price - stop) / (entry - stop) * 100) if (entry - stop) != 0 else 100
    r_multiple = ((price - entry) / risk_per_share) if risk_per_share > 0 else 0
    days_in = days_since(p.get("entry_date"))
    sector = sector_map.get(ticker, p.get("sector") or "Unknown")
    sleeve = p.get("sleeve") or "Unassigned"

    targets = conds_for(p["trade_id"], "target")
    t1 = f(targets[0].get("description")) if targets else None

    # Thesis status from news
    ticker_news = [n for n in D["news"] if n.get("ticker") == ticker]
    has_threat = any(n.get("severity_tag") == "thesis-threatening" for n in ticker_news)
    has_material = any(n.get("severity_tag") == "material change" for n in ticker_news)
    thesis = "Threatened" if has_threat else "Under review" if has_material else "Intact"

    positions.append({
        "trade_id": p["trade_id"],
        "ticker": ticker,
        "sleeve": sleeve,
        "sector": sector,
        "entry": entry,
        "stop": stop,
        "qty": qty,
        "cmp": price,
        "is_live": is_live,
        "cost_basis": cost_basis,
        "current_value": current_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "risk_per_share": risk_per_share,
        "capital_at_risk": capital_at_risk,
        "stop_dist_pct": stop_dist_pct,
        "stop_dist_abs": price - stop,
        "r_multiple": r_multiple,
        "days_in": days_in,
        "target": t1,
        "entry_date": p.get("entry_date"),
        "thesis": thesis,
        "thesis_note": p.get("thesis_note") or "",
        "review_date": p.get("review_date"),
        "setup": p.get("setup") or "",
        "position_size_pct": f(p.get("position_size_pct")),
    })

# Portfolio totals
total_invested = sum(p["cost_basis"] for p in positions)
total_value = sum(p["current_value"] for p in positions)
unrealized_pnl = total_value - total_invested
realized_pnl = sum(f(t.get("realized_return_pct") or 0) / 100 * (f(t.get("entry_price")) or 0) * (f(t.get("exit_price")) or 1)
                   for t in D["closed"])
# More accurate realized P&L
realized_pnl = 0
for t in D["closed"]:
    ep = f(t.get("entry_price")) or 0
    xp = f(t.get("exit_price")) or 0
    # Use trade_history qty - we need to infer from cost if not stored
    # Best estimate from entry/exit and return %
    ret_pct = f(t.get("realized_return_pct"))
    if ret_pct is not None:
        # P&L = entry_cost * return_pct / 100
        # But we don't have qty in trade_history directly
        # Use lots to find qty for this trade
        trade_lots = [l for l in D["lots"] if l.get("trade_id") == t.get("trade_id")]
        trade_qty = sum(f(l.get("qty")) or 0 for l in trade_lots)
        if trade_qty > 0:
            realized_pnl += (xp - ep) * trade_qty
        else:
            realized_pnl += ep * (ret_pct / 100)  # fallback

net_pnl = unrealized_pnl + realized_pnl

# Weight per position
for p in positions:
    p["weight"] = (p["current_value"] / total_value * 100) if total_value else 0

has_live = len(price_map) > 0
wins = sum(1 for t in D["closed"] if (f(t.get("exit_price")) or 0) > (f(t.get("entry_price")) or 0))

# ── Build alerts ────────────────────────────────────────────
alerts = []
for p in positions:
    # Stop breach
    if p["cmp"] <= p["stop"] and p["stop"] > 0:
        alerts.append({"ticker": p["ticker"], "level": "danger", "type": "STOP BREACH",
                        "msg": f"Price ₹{p['cmp']:,.0f} vs stop ₹{p['stop']:,.0f}"})
    elif p["stop_dist_pct"] < 80 and p["cmp"] < p["entry"]:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "NEAR STOP",
                        "msg": f"₹{p['stop_dist_abs']:.0f} from stop ({p['stop_dist_pct']:.0f}% buffer)"})

    # Concentration
    if p["weight"] > 40:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "CONCENTRATION",
                        "msg": f"{p['weight']:.1f}% portfolio weight"})

    # R = 0 (stop = entry)
    if p["risk_per_share"] == 0 and p["stop"] > 0:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "DATA REVIEW",
                        "msg": "Stop equals entry — R basis needs review"})

    # Thesis threat
    if p["thesis"] == "Threatened":
        alerts.append({"ticker": p["ticker"], "level": "danger", "type": "THESIS THREAT",
                        "msg": "Thesis-threatening news detected"})

    # Review overdue
    urg = review_urgency(p["review_date"])
    if "overdue" in urg:
        alerts.append({"ticker": p["ticker"], "level": "warning", "type": "REVIEW",
                        "msg": f"Review overdue since {fmt_date(p['review_date'])}"})

danger_alerts = [a for a in alerts if a["level"] == "danger"]
warning_alerts = [a for a in alerts if a["level"] == "warning"]


# ── Header ──────────────────────────────────────────────────
st.markdown("""
<style>
    .stApp header {visibility: hidden;}
    div[data-testid="stMetric"] {background: #f8f9fb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 16px;}
    div[data-testid="stMetric"] label {font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: #667085;}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {font-size: 24px; font-weight: 700;}
    .action-card {padding: 10px 14px; margin: 4px 0; border-radius: 8px; border: 1px solid #e5e7eb;
                  display: flex; align-items: center; justify-content: space-between; gap: 12px;}
    .action-card .dot {width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 8px;}
    .danger-dot {background: #c83b3b;} .warning-dot {background: #c58b19;}
    .badge {display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 700;}
    .badge-red {background: #feecec; color: #a92e2e;}
    .badge-amber {background: #fff6dd; color: #8a5b00;}
    .badge-green {background: #eaf7ed; color: #216c30;}
    .r-badge {display: inline-block; padding: 2px 8px; border-radius: 5px; font-weight: 700; font-size: 13px;}
    .r-pos {background: #eaf7ed; color: #216c30;} .r-neg {background: #feecec; color: #a92e2e;}
    .stop-bar {height: 8px; border-radius: 4px; background: #eef1f5; overflow: hidden; margin: 4px 0;}
    .stop-fill {height: 100%; border-radius: 4px;}
</style>
""", unsafe_allow_html=True)

# Title row
c1, c2 = st.columns([4, 1])
with c1:
    st.markdown("# India Master Portfolio")
    st.caption(f"V2 Analytics & Decision Dashboard · {datetime.now().strftime('%d %b %Y')}")
with c2:
    state_label = "CAUTION" if danger_alerts else "NORMAL" if not warning_alerts else "WATCH"
    state_color = "#a92e2e" if danger_alerts else "#216c30" if not warning_alerts else "#8a5b00"
    state_bg = "#feecec" if danger_alerts else "#eaf7ed" if not warning_alerts else "#fff6dd"
    st.markdown(f'<div style="text-align:right;margin-top:20px"><span style="background:{state_bg};color:{state_color};'
                f'padding:8px 14px;border-radius:999px;font-weight:700;font-size:13px">● {state_label}</span></div>',
                unsafe_allow_html=True)

# ── Tabs ────────────────────────────────────────────────────
tab_cockpit, tab_positions, tab_risk, tab_perf, tab_thesis, tab_system = st.tabs([
    "🎯 Cockpit", "📊 Positions", "⚡ Risk", "📈 Performance", "🔬 Thesis Monitor", "⚙️ System & Data"
])


# ═══════════════════════════════════════════════════════════
# TAB 1 — COCKPIT
# ═══════════════════════════════════════════════════════════
with tab_cockpit:
    # Alert banner
    if danger_alerts:
        html = "".join(
            f'<div style="padding:5px 12px;font-size:13px"><span class="dot danger-dot"></span>'
            f'<strong>{a["ticker"]}</strong> — {a["msg"]}'
            f'<span class="badge badge-red" style="float:right">{a["type"]}</span></div>'
            for a in danger_alerts
        )
        st.markdown(
            f'<div style="background:rgba(239,68,68,.06);border:1px solid #ef4444;border-radius:10px;padding:8px 4px;margin-bottom:12px">'
            f'<div style="padding:4px 12px;font-weight:700;color:#dc2626;font-size:14px">🚨 {len(danger_alerts)} Hard Alert(s)</div>'
            f'{html}</div>', unsafe_allow_html=True)

    if warning_alerts:
        html = "".join(
            f'<div style="padding:4px 12px;font-size:12px"><span class="dot warning-dot"></span>'
            f'<strong>{a["ticker"]}</strong> — {a["msg"]}'
            f'<span class="badge badge-amber" style="float:right">{a["type"]}</span></div>'
            for a in warning_alerts
        )
        st.markdown(
            f'<div style="background:rgba(245,158,11,.05);border:1px solid #f59e0b;border-radius:10px;padding:6px 4px;margin-bottom:12px">'
            f'<div style="padding:4px 12px;font-weight:600;color:#d97706;font-size:13px">⚡ {len(warning_alerts)} Watch Item(s)</div>'
            f'{html}</div>', unsafe_allow_html=True)

    # KPIs
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Portfolio Value", fmt(total_value),
              delta=f"{unrealized_pnl / total_invested * 100:+.1f}% unrealized" if total_invested else None)
    k2.metric("Invested", fmt(total_invested))
    k3.metric("Net P&L", fmt(net_pnl),
              delta="realized + unrealized")
    k4.metric("Active Positions", len(positions), delta=f"{len(set(p['sector'] for p in positions))} sectors")
    largest = max(positions, key=lambda p: p["weight"]) if positions else None
    k5.metric("Largest Weight", f"{largest['weight']:.1f}%" if largest else "—",
              delta=largest["ticker"] if largest else None)
    k6.metric("Closed Trades", len(D["closed"]),
              delta=f"Win rate {wins}/{len(D['closed'])}" if D["closed"] else None)

    st.divider()

    # Two columns: Action Queue + Sector Allocation
    col_left, col_right = st.columns([1.3, 1])

    with col_left:
        st.markdown("#### 🚨 Action Queue")
        st.caption("Exception-first view — items that need your decision")
        all_alerts = danger_alerts + warning_alerts
        if all_alerts:
            for a in all_alerts:
                dot_cls = "danger-dot" if a["level"] == "danger" else "warning-dot"
                badge_cls = "badge-red" if a["level"] == "danger" else "badge-amber"
                st.markdown(
                    f'<div class="action-card">'
                    f'<div><span class="dot {dot_cls}"></span><strong>{a["ticker"]}</strong><br/>'
                    f'<small style="color:#667085">{a["msg"]}</small></div>'
                    f'<span class="badge {badge_cls}">{a["type"]}</span></div>',
                    unsafe_allow_html=True)
        else:
            st.success("No action items — all positions within parameters.")

    with col_right:
        st.markdown("#### Sector Allocation")
        st.caption("By current portfolio value")
        sector_vals = {}
        for p in positions:
            sector_vals[p["sector"]] = sector_vals.get(p["sector"], 0) + p["current_value"]
        sectors_sorted = sorted(sector_vals.items(), key=lambda x: x[1], reverse=True)

        fig_sector = go.Figure()
        names = [s[0] for s in sectors_sorted]
        vals = [s[1] for s in sectors_sorted]
        pcts = [v / total_value * 100 for v in vals]
        colors = SECTOR_COLORS[:len(names)]

        fig_sector.add_trace(go.Bar(
            y=names[::-1], x=pcts[::-1],
            orientation='h',
            marker_color=colors[:len(names)][::-1],
            text=[f"{p:.1f}%" for p in pcts[::-1]],
            textposition='outside',
            hovertemplate='%{y}: %{x:.1f}%<extra></extra>',
        ))
        fig_sector.update_layout(
            height=280, margin=dict(l=0, r=40, t=10, b=10),
            xaxis=dict(showgrid=True, gridcolor="#eef0f3", title=None, showticklabels=False),
            yaxis=dict(showgrid=False, title=None),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_sector, use_container_width=True)

    st.divider()

    # Market / Deployment State + Equity Curve
    md_left, md_right = st.columns(2)

    with md_left:
        st.markdown("#### 📡 Market & Deployment State")
        # Deployment ratio
        # We don't have a "total capital" field — use invested as deployed, estimate cash from sleeve rules
        sleeve_counts = {}
        for p in positions:
            sleeve_counts[p["sleeve"]] = sleeve_counts.get(p["sleeve"], 0) + 1

        anchor_val = sum(p["current_value"] for p in positions if p["sleeve"] == "Anchor")
        power_val = sum(p["current_value"] for p in positions if p["sleeve"] == "Power Sleeve")
        tracker_val = sum(p["current_value"] for p in positions if p["sleeve"] == "Tracker")
        multi_val = sum(p["current_value"] for p in positions if p["sleeve"] == "Multibagger List")

        # State heuristic: if >70% in Anchor → defensive; if Power > 30% → aggressive
        anchor_pct = (anchor_val / total_value * 100) if total_value else 0
        power_pct = (power_val / total_value * 100) if total_value else 0

        if anchor_pct > 70:
            deploy_state = "Defensive"
            deploy_icon = "🛡️"
        elif power_pct > 30:
            deploy_state = "Aggressive"
            deploy_icon = "⚡"
        else:
            deploy_state = "Balanced"
            deploy_icon = "⚖️"

        st.markdown(f"**Deployment Stance:** {deploy_icon} {deploy_state}")
        st.markdown("")

        # Sleeve deployment bars
        sleeve_deploy = [
            ("Anchor", anchor_val, anchor_pct, SLEEVE_COLORS["Anchor"]),
            ("Power Sleeve", power_val, power_pct, SLEEVE_COLORS["Power Sleeve"]),
            ("Multibagger List", multi_val, (multi_val / total_value * 100) if total_value else 0, SLEEVE_COLORS["Multibagger List"]),
            ("Tracker", tracker_val, (tracker_val / total_value * 100) if total_value else 0, SLEEVE_COLORS["Tracker"]),
        ]
        for sl_name, sl_val, sl_pct, sl_col in sleeve_deploy:
            if sl_val > 0:
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'
                    f'<span style="min-width:110px;font-size:13px">{sl_name}</span>'
                    f'<div style="flex:1;height:10px;background:#eef1f5;border-radius:5px;overflow:hidden">'
                    f'<div style="width:{sl_pct}%;height:100%;background:{sl_col};border-radius:5px"></div></div>'
                    f'<span style="min-width:50px;text-align:right;font-size:12px;font-weight:600">{sl_pct:.1f}%</span>'
                    f'</div>', unsafe_allow_html=True)

        st.markdown("")
        st.markdown(f"**Active Sectors:** {len(set(p['sector'] for p in positions))} · "
                    f"**Positions:** {len(positions)} · "
                    f"**Avg Weight:** {100/len(positions):.1f}%" if positions else "")

    with md_right:
        st.markdown("#### 📈 Portfolio Equity Curve")
        st.caption("Constructed from entry dates and current values")

        # Build equity curve from lots sorted by date
        all_lots = sorted(D["lots"], key=lambda l: l.get("lot_date") or "9999")
        if all_lots:
            cumulative = 0
            curve_dates = []
            curve_invested = []
            curve_value = []
            lot_events = {}  # date → total cost added

            for lot in all_lots:
                ld = lot.get("lot_date")
                if not ld:
                    continue
                lqty = f(lot.get("qty")) or 0
                lprice = f(lot.get("price")) or 0
                lot_cost = lqty * lprice
                lot_events[ld] = lot_events.get(ld, 0) + lot_cost

            running = 0
            for date_str in sorted(lot_events.keys()):
                running += lot_events[date_str]
                curve_dates.append(date_str)
                curve_invested.append(running)

            # Add today as last point with current value
            today_str = datetime.now().strftime("%Y-%m-%d")
            curve_dates.append(today_str)
            curve_invested.append(total_invested)

            fig_equity = go.Figure()
            fig_equity.add_trace(go.Scatter(
                x=curve_dates, y=curve_invested,
                mode='lines+markers',
                name='Invested',
                line=dict(color=MUTED, width=2, dash='dot'),
                marker=dict(size=5),
                hovertemplate='%{x}<br>Invested: ₹%{y:,.0f}<extra></extra>',
            ))
            # Current value line (from invested to today's value)
            fig_equity.add_trace(go.Scatter(
                x=[curve_dates[-1]], y=[total_value],
                mode='markers',
                name=f'Current Value ({fmt(total_value)})',
                marker=dict(size=12, color=GREEN if total_value >= total_invested else RED,
                           symbol='diamond'),
                hovertemplate='Today<br>Value: ₹%{y:,.0f}<extra></extra>',
            ))
            fig_equity.update_layout(
                height=280, margin=dict(l=0, r=0, t=10, b=10),
                xaxis=dict(showgrid=False, title=None),
                yaxis=dict(showgrid=True, gridcolor="#eef0f3", title="₹"),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                showlegend=True,
            )
            st.plotly_chart(fig_equity, use_container_width=True)
        else:
            st.caption("No lot data yet — equity curve needs lot entries with dates.")

    st.divider()

    # Sleeve P&L — expandable
    st.markdown("#### Sleeve Performance")
    st.caption("Click a sleeve to expand individual positions")

    sleeve_groups = {}
    for p in positions:
        sl = p["sleeve"]
        if sl not in sleeve_groups:
            sleeve_groups[sl] = []
        sleeve_groups[sl].append(p)

    for sleeve, pos_list in sorted(sleeve_groups.items(), key=lambda x: sum(p["cost_basis"] for p in x[1]), reverse=True):
        sl_invested = sum(p["cost_basis"] for p in pos_list)
        sl_value = sum(p["current_value"] for p in pos_list)
        sl_pnl = sl_value - sl_invested
        sl_ret = (sl_pnl / sl_invested * 100) if sl_invested else 0
        sl_color = SLEEVE_COLORS.get(sleeve, "#666")

        with st.expander(f"🔵 **{sleeve}** ({len(pos_list)}) — Invested: {fmt(sl_invested)} · Value: {fmt(sl_value)} · P&L: {fmt_pct(sl_ret)}", expanded=False):
            rows = []
            for p in sorted(pos_list, key=lambda x: x["cost_basis"], reverse=True):
                r_text = f"{p['r_multiple']:+.1f}R"
                rows.append({
                    "Ticker": p["ticker"],
                    "Sector": p["sector"],
                    "Entry": f"₹{p['entry']:,.0f}",
                    "CMP": f"₹{p['cmp']:,.1f}",
                    "Stop": f"₹{p['stop']:,.0f}",
                    "Qty": int(p["qty"]),
                    "Invested": fmt(p["cost_basis"]),
                    "Value": fmt(p["current_value"]),
                    "P&L": fmt(p["pnl"]),
                    "Return": fmt_pct(p["pnl_pct"]),
                    "R": r_text,
                    "Days": p["days_in"] or "—",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Total row
    total_ret = (unrealized_pnl / total_invested * 100) if total_invested else 0
    st.markdown(f"**Total Active** — Invested: {fmt(total_invested)} · Value: {fmt(total_value)} · "
                f"P&L: **{fmt_pct(total_ret)}** ({fmt(unrealized_pnl)})")


# ═══════════════════════════════════════════════════════════
# TAB 2 — POSITIONS
# ═══════════════════════════════════════════════════════════
with tab_positions:
    if not has_live:
        st.info("💡 Live prices not yet connected — distances and P&L use entry price. "
                "Price sync runs every 30 min during market hours.")

    st.markdown("#### Position Detail")
    st.caption("Decision-oriented view: exposure + stop + R + thesis + action")

    # Build display dataframe
    pos_rows = []
    for p in sorted(positions, key=lambda x: x["weight"], reverse=True):
        # Action recommendation
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            action = "Review stop breach"
            risk_badge = "Critical"
        elif p["stop_dist_pct"] < 80 and p["cmp"] < p["entry"]:
            action = "Monitor — near stop"
            risk_badge = "Watch"
        elif p["risk_per_share"] == 0:
            action = "Review stop / R basis"
            risk_badge = "Review"
        else:
            action = "Hold / monitor"
            risk_badge = "Normal"

        pos_rows.append({
            "Ticker": p["ticker"],
            "Sleeve": p["sleeve"],
            "Sector": p["sector"],
            "Weight %": round(p["weight"], 1),
            "Entry": p["entry"],
            "CMP": p["cmp"],
            "Stop": p["stop"],
            "R": round(p["r_multiple"], 1),
            "Days": p["days_in"] or 0,
            "P&L %": round(p["pnl_pct"], 2),
            "P&L ₹": round(p["pnl"]),
            "Thesis": p["thesis"],
            "Risk": risk_badge,
            "Action": action,
        })

    df_pos = pd.DataFrame(pos_rows)
    if not df_pos.empty:
        def color_r(val):
            if val >= 0:
                return f"color: {GREEN}; font-weight: 700"
            return f"color: {RED}; font-weight: 700"

        def color_pnl(val):
            if val >= 0:
                return f"color: {GREEN}; font-weight: 600"
            return f"color: {RED}; font-weight: 600"

        def color_risk(val):
            if val == "Critical":
                return f"background-color: #feecec; color: #a92e2e; font-weight: 700"
            if val in ("Watch", "Review"):
                return f"background-color: #fff6dd; color: #8a5b00; font-weight: 600"
            return f"background-color: #eaf7ed; color: #216c30"

        styled = (
            df_pos.style
            .map(color_r, subset=["R"])
            .map(color_pnl, subset=["P&L %", "P&L ₹"])
            .map(color_risk, subset=["Risk"])
            .format({
                "Entry": "₹{:,.0f}",
                "CMP": "₹{:,.1f}",
                "Stop": "₹{:,.0f}",
                "R": "{:+.1f}R",
                "Weight %": "{:.1f}%",
                "P&L %": "{:+.2f}%",
                "P&L ₹": "₹{:,.0f}",
            })
        )
        event = st.dataframe(styled, use_container_width=True, hide_index=True,
                             selection_mode="single-row", on_select="rerun", key="pos_table")

    # Summary cards below table
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.markdown("##### Exposure")
        if largest:
            st.metric("Largest position", f"{largest['weight']:.1f}%", delta=largest["ticker"])
            st.progress(min(largest["weight"] / 100, 1.0))
    with sc2:
        st.markdown("##### Stop Status")
        breached = sum(1 for p in positions if p["cmp"] <= p["stop"] and p["stop"] > 0)
        sc2a, sc2b = st.columns(2)
        sc2a.metric("Breached", breached)
        sc2b.metric("Clear", len(positions) - breached)
    with sc3:
        st.markdown("##### Holding Period")
        days_list = [p["days_in"] for p in positions if p["days_in"] is not None]
        if days_list:
            sc3a, sc3b = st.columns(2)
            sc3a.metric("Max", f"{max(days_list)}d")
            sc3b.metric("Min", f"{min(days_list)}d")

    # Detail panel on row click
    st.divider()
    selected_rows = event.selection.rows if (not df_pos.empty and event.selection) else []
    if selected_rows:
        sel_ticker = df_pos.iloc[selected_rows[0]]["Ticker"]
    elif positions:
        sel_ticker = positions[0]["ticker"]
        st.caption("👆 Click any row above to view its details")
    else:
        sel_ticker = None

    if sel_ticker:
        pos = next((p for p in positions if p["ticker"] == sel_ticker), None)
        if pos:
            st.markdown(f"### {sel_ticker} — {pos['sleeve']} · {pos['sector']}")

            mc = st.columns(6)
            mc[0].metric("CMP" if pos["is_live"] else "Entry", f"₹{pos['cmp']:,.2f}")
            mc[1].metric("Stop-loss", f"₹{pos['stop']:,.0f}",
                         delta=f"₹{pos['stop_dist_abs']:.0f} away" if pos['stop'] else None,
                         delta_color="inverse")
            if pos["target"]:
                pct_to = ((pos["target"] - pos["cmp"]) / pos["cmp"] * 100) if pos["cmp"] else 0
                mc[2].metric("Target", f"₹{pos['target']:,.0f}", delta=f"{pct_to:.1f}% to go")
            else:
                mc[2].metric("Target", "—")
            mc[3].metric("R-Multiple", f"{pos['r_multiple']:+.1f}R")
            mc[4].metric("Qty", f"{int(pos['qty'])}")
            mc[5].metric("Unrealized P&L", fmt(pos["pnl"]),
                         delta=fmt_pct(pos["pnl_pct"]))

            # Lots + News
            dl, dr = st.columns(2)
            with dl:
                tlots = [l for l in D["lots"] if l.get("trade_id") == pos["trade_id"]]
                if tlots:
                    st.markdown("**📦 Lots**")
                    for lot in tlots:
                        st.text(f"  {lot.get('qty')} shares @ ₹{lot.get('price')} — {lot.get('lot_date') or 'date not set'}")

            with dr:
                tnews = [n for n in D["news"] if n.get("ticker") == sel_ticker][:8]
                if tnews:
                    st.markdown("**📰 Recent announcements**")
                    for n in tnews:
                        sev = n.get("severity_tag", "routine update")
                        st.text(f"  {sev_icon(sev)} [{(n.get('source') or '').upper()}] {(n.get('headline') or '')[:70]} ({short_date(n.get('published_at'))})")

            # Fundamentals
            snap = next((s for s in D["fundsnap"] if s.get("ticker") == sel_ticker), None)
            st.markdown("**📊 Fundamentals**")
            if snap:
                fc = st.columns(6)
                pe = f(snap.get("pe"))
                roe = f(snap.get("roe"))
                roce = f(snap.get("roce"))
                ebit = f(snap.get("ebit_margin"))
                rev = f(snap.get("revenue_growth_yoy"))
                de = f(snap.get("debt_to_equity"))
                fc[0].metric("PE", f"{pe:.1f}x" if pe else "—")
                fc[1].metric("ROE", f"{roe:.1f}%" if roe else "—")
                fc[2].metric("ROCE", f"{roce:.1f}%" if roce else "—")
                fc[3].metric("EBIT Margin", f"{ebit:.1f}%" if ebit else "—")
                fc[4].metric("Rev Growth", f"{rev:.1f}%" if rev else "—")
                fc[5].metric("D/E", f"{de:.2f}x" if de else "—")
            else:
                st.caption("No fundamentals snapshot yet.")


# ═══════════════════════════════════════════════════════════
# TAB 3 — RISK
# ═══════════════════════════════════════════════════════════
with tab_risk:
    # Top-level risk KPIs
    r1, r2, r3 = st.columns(3)
    total_car = sum(p["capital_at_risk"] for p in positions)
    r1.metric("Capital Deployed", fmt(total_invested), delta=f"{len(positions)} positions")
    r2.metric("Capital at Risk (to stop)", fmt(total_car),
              delta=f"{total_car / total_invested * 100:.1f}% of invested" if total_invested else None)
    r3.metric("Thesis Breaks", sum(1 for p in positions if p["thesis"] == "Threatened"),
              delta="positions with thesis-threatening news")

    st.divider()

    # Stop proximity visualization
    st.markdown("#### Stop Proximity")
    st.caption("How much buffer each position has before stop-loss")

    sorted_by_stop = sorted(positions, key=lambda p: p["stop_dist_pct"])
    for p in sorted_by_stop:
        buf_pct = max(0, min(100, p["stop_dist_pct"]))
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            bar_color = RED
            label = "BREACHED"
        elif buf_pct < 50:
            bar_color = AMBER
            label = f"₹{p['stop_dist_abs']:.0f} ({(p['cmp'] - p['stop']) / p['cmp'] * 100:.1f}%)"
        else:
            bar_color = GREEN
            label = f"₹{p['stop_dist_abs']:.0f} ({(p['cmp'] - p['stop']) / p['cmp'] * 100:.1f}%)"

        c1, c2, c3 = st.columns([1.5, 4, 1.5])
        c1.markdown(f"**{p['ticker']}**")
        c2.markdown(
            f'<div class="stop-bar"><div class="stop-fill" style="width:{buf_pct}%;background:{bar_color}"></div></div>',
            unsafe_allow_html=True)
        c3.markdown(f"<small>{label}</small>", unsafe_allow_html=True)

    st.divider()

    # Risk concentration heatmap
    st.markdown("#### Risk Concentration — Sector × Sleeve")
    st.caption("Capital at risk by sector and sleeve")

    sleeves = sorted(set(p["sleeve"] for p in positions))
    sectors = sorted(set(p["sector"] for p in positions))

    matrix = {}
    max_risk = 0
    for p in positions:
        key = (p["sector"], p["sleeve"])
        matrix[key] = matrix.get(key, 0) + p["capital_at_risk"]
        max_risk = max(max_risk, matrix[key])

    z_data = []
    text_data = []
    for sector in sectors:
        row = []
        text_row = []
        for sleeve in sleeves:
            val = matrix.get((sector, sleeve), 0)
            row.append(val)
            text_row.append(fmt(val) if val > 0 else "—")
        z_data.append(row)
        text_data.append(text_row)

    fig_heat = go.Figure(data=go.Heatmap(
        z=z_data, x=sleeves, y=sectors,
        text=text_data, texttemplate="%{text}",
        colorscale=[[0, "#eaf0fb"], [0.3, "#bfcff2"], [0.6, "#5d7bd0"], [1, "#2a4a8f"]],
        showscale=True, colorbar=dict(title="Risk ₹"),
        hovertemplate='%{y} × %{x}: %{text}<extra></extra>',
    ))
    fig_heat.update_layout(
        height=max(200, 50 * len(sectors)),
        margin=dict(l=0, r=0, t=10, b=10),
        xaxis=dict(side="top"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()
    st.markdown("#### Risk Rules — Future Implementation")
    rc1, rc2 = st.columns(2)
    with rc1:
        st.markdown("""
        | Rule | Threshold |
        |------|-----------|
        | Position concentration alert | > configurable limit |
        | Sector concentration alert | > configurable limit |
        | Portfolio risk budget | configurable % |
        | Stop breach | Hard alert |
        """)
    with rc2:
        st.markdown("""
        | Rule | Threshold |
        |------|-----------|
        | Liquidity check | ADV / position size |
        | Correlation | 30/60/120d |
        | Drawdown | Portfolio + sleeve |
        | Stress test | Nifty / sector shock |
        """)


# ═══════════════════════════════════════════════════════════
# TAB 4 — PERFORMANCE
# ═══════════════════════════════════════════════════════════
with tab_perf:
    # KPI row
    pk1, pk2, pk3, pk4, pk5, pk6 = st.columns(6)
    pk1.metric("Net P&L", fmt(net_pnl))
    pk2.metric("Realized P&L", fmt(realized_pnl))
    pk3.metric("Unrealized P&L", fmt(unrealized_pnl))
    pk4.metric("Closed Trades", len(D["closed"]))
    pk5.metric("Win Rate", f"{wins}/{len(D['closed'])}" if D["closed"] else "—")
    # Avg holding period of closed trades
    hold_days = [f(t.get("holding_period_days")) for t in D["closed"] if f(t.get("holding_period_days"))]
    pk6.metric("Avg Hold", f"{sum(hold_days) / len(hold_days):.0f}d" if hold_days else "—")

    st.divider()

    pc1, pc2 = st.columns(2)

    with pc1:
        st.markdown("#### P&L Attribution")
        st.caption("Current P&L contribution by position")

        sorted_pos = sorted(positions, key=lambda p: p["pnl"])
        fig_attr = go.Figure()
        fig_attr.add_trace(go.Bar(
            y=[p["ticker"] for p in sorted_pos],
            x=[p["pnl"] for p in sorted_pos],
            orientation='h',
            marker_color=[GREEN if p["pnl"] >= 0 else RED for p in sorted_pos],
            text=[fmt(p["pnl"]) for p in sorted_pos],
            textposition='outside',
            hovertemplate='%{y}: %{x:,.0f}<extra></extra>',
        ))
        fig_attr.update_layout(
            height=max(200, 45 * len(sorted_pos)),
            margin=dict(l=0, r=60, t=10, b=10),
            xaxis=dict(showgrid=True, gridcolor="#eef0f3", title="P&L (₹)", zeroline=True, zerolinecolor="#999"),
            yaxis=dict(showgrid=False),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_attr, use_container_width=True)

    with pc2:
        st.markdown("#### Closed Trades")
        if D["closed"]:
            for t in D["closed"]:
                ep = f(t.get("entry_price")) or 0
                xp = f(t.get("exit_price")) or 0
                ret = f(t.get("realized_return_pct"))
                win = (ret or 0) >= 0
                icon = "✅" if win else "❌"
                col = GREEN if win else RED
                ret_s = f"+{ret:.1f}%" if ret and ret > 0 else f"{ret:.1f}%" if ret else "—"
                reason = t.get("exit_reason", "")
                ticker = t.get("ticker", "")
                sleeve = t.get("sleeve", "")

                st.markdown(
                    f'<div style="padding:8px 12px;margin:3px 0;border-radius:6px;border:1px solid #e5e7eb;'
                    f'display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
                    f'<span style="font-weight:700;min-width:85px">{icon} {ticker}</span>'
                    f'<span class="badge" style="background:#818cf822;color:#818cf8">{sleeve}</span>'
                    f'<span style="color:#666;font-size:12px">₹{ep:,.0f} → ₹{xp:,.0f}</span>'
                    f'<span class="badge" style="background:{col}18;color:{col}">{reason}</span>'
                    f'<span style="color:{col};font-weight:700;font-size:16px;margin-left:auto">{ret_s}</span>'
                    f'</div>', unsafe_allow_html=True)
        else:
            st.caption("No closed trades yet.")

    st.divider()

    # Sleeve P&L breakdown chart
    st.markdown("#### Sleeve P&L Breakdown")
    sleeve_pnl = {}
    for p in positions:
        sl = p["sleeve"]
        sleeve_pnl[sl] = sleeve_pnl.get(sl, 0) + p["pnl"]
    # Add closed trades
    for t in D["closed"]:
        sl = t.get("sleeve", "Unknown")
        ep = f(t.get("entry_price")) or 0
        xp = f(t.get("exit_price")) or 0
        trade_lots = [l for l in D["lots"] if l.get("trade_id") == t.get("trade_id")]
        trade_qty = sum(f(l.get("qty")) or 0 for l in trade_lots)
        if trade_qty > 0:
            sleeve_pnl[sl] = sleeve_pnl.get(sl, 0) + (xp - ep) * trade_qty

    fig_sleeve = go.Figure()
    sl_names = sorted(sleeve_pnl.keys())
    sl_vals = [sleeve_pnl[s] for s in sl_names]
    fig_sleeve.add_trace(go.Bar(
        x=sl_names, y=sl_vals,
        marker_color=[SLEEVE_COLORS.get(s, "#666") for s in sl_names],
        text=[fmt(v) for v in sl_vals],
        textposition='outside',
        hovertemplate='%{x}: %{y:,.0f}<extra></extra>',
    ))
    fig_sleeve.update_layout(
        height=280, margin=dict(l=0, r=0, t=10, b=10),
        yaxis=dict(showgrid=True, gridcolor="#eef0f3", title="P&L (₹)", zeroline=True, zerolinecolor="#999"),
        xaxis=dict(showgrid=False),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_sleeve, use_container_width=True)

    st.divider()

    # Trading System Statistics
    ts_left, ts_right = st.columns(2)

    with ts_left:
        st.markdown("#### 🎯 Trading System Statistics")
        st.caption("Computed from closed trades — builds as your track record grows")

        closed = D["closed"]
        if closed:
            # Core stats
            total_trades = len(closed)
            win_trades = [t for t in closed if (f(t.get("realized_return_pct")) or 0) > 0]
            loss_trades = [t for t in closed if (f(t.get("realized_return_pct")) or 0) <= 0]
            win_count = len(win_trades)
            loss_count = len(loss_trades)
            win_rate = (win_count / total_trades * 100) if total_trades else 0

            win_returns = [f(t.get("realized_return_pct")) or 0 for t in win_trades]
            loss_returns = [f(t.get("realized_return_pct")) or 0 for t in loss_trades]

            avg_win = (sum(win_returns) / len(win_returns)) if win_returns else 0
            avg_loss = (sum(loss_returns) / len(loss_returns)) if loss_returns else 0
            # Payoff ratio (avg win / avg loss magnitude)
            payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else float('inf')
            # Expectancy = (win% × avg_win) - (loss% × avg_loss_magnitude)
            expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * abs(avg_loss))

            # Largest win / loss
            all_returns = [f(t.get("realized_return_pct")) or 0 for t in closed]
            max_win = max(all_returns) if all_returns else 0
            max_loss = min(all_returns) if all_returns else 0

            # Exit reason breakdown
            exit_reasons = {}
            for t in closed:
                reason = t.get("exit_reason") or "Unknown"
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

            # Display
            stat_rows = [
                {"Metric": "Total Trades", "Value": str(total_trades)},
                {"Metric": "Win Rate", "Value": f"{win_rate:.0f}% ({win_count}W / {loss_count}L)"},
                {"Metric": "Avg Win", "Value": f"+{avg_win:.1f}%"},
                {"Metric": "Avg Loss", "Value": f"{avg_loss:.1f}%"},
                {"Metric": "Payoff Ratio", "Value": f"{payoff:.2f}x" if payoff != float('inf') else "∞ (no losses)"},
                {"Metric": "Expectancy", "Value": f"{expectancy:+.2f}% per trade"},
                {"Metric": "Largest Win", "Value": f"+{max_win:.1f}%"},
                {"Metric": "Largest Loss", "Value": f"{max_loss:.1f}%"},
                {"Metric": "Avg Hold Period", "Value": f"{sum(hold_days)/len(hold_days):.0f} days" if hold_days else "—"},
            ]
            st.dataframe(pd.DataFrame(stat_rows), use_container_width=True, hide_index=True)

            # Exit reason pie
            if exit_reasons:
                st.markdown("**Exit Reasons**")
                fig_exit = go.Figure(data=go.Pie(
                    labels=list(exit_reasons.keys()),
                    values=list(exit_reasons.values()),
                    hole=0.4,
                    marker_colors=SECTOR_COLORS[:len(exit_reasons)],
                    textinfo='label+value',
                    hovertemplate='%{label}: %{value} trades<extra></extra>',
                ))
                fig_exit.update_layout(
                    height=200, margin=dict(l=0, r=0, t=10, b=10),
                    showlegend=False,
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_exit, use_container_width=True)
        else:
            st.info("No closed trades yet — statistics build as you close positions.")

    with ts_right:
        st.markdown("#### 📊 Benchmark & Drawdown")
        st.caption("Portfolio vs Nifty 50 — requires price history feed")

        # Current portfolio drawdown from peak (estimated from positions)
        # Peak = sum of max(current_value, cost_basis) per position as rough proxy
        peak_estimate = sum(max(p["current_value"], p["cost_basis"]) for p in positions)
        if peak_estimate > 0:
            dd_from_peak = ((total_value - peak_estimate) / peak_estimate * 100)
            dd_color = RED if dd_from_peak < -5 else AMBER if dd_from_peak < 0 else GREEN

            m1, m2 = st.columns(2)
            m1.metric("Portfolio Value", fmt(total_value))
            m2.metric("Est. Peak", fmt(peak_estimate))

            st.markdown(
                f'<div style="text-align:center;padding:16px;margin:8px 0;border-radius:10px;'
                f'background:{dd_color}10;border:1px solid {dd_color}30">'
                f'<div style="font-size:11px;text-transform:uppercase;color:{MUTED};letter-spacing:0.04em">Drawdown from Peak</div>'
                f'<div style="font-size:28px;font-weight:800;color:{dd_color}">{dd_from_peak:+.1f}%</div>'
                f'<div style="font-size:11px;color:{MUTED}">Estimated from position cost basis vs current</div>'
                f'</div>', unsafe_allow_html=True)

            st.markdown("")

            # Sleeve-level drawdowns
            st.markdown("**Sleeve Drawdowns**")
            for sleeve_name in sorted(set(p["sleeve"] for p in positions)):
                sl_pos = [p for p in positions if p["sleeve"] == sleeve_name]
                sl_val = sum(p["current_value"] for p in sl_pos)
                sl_peak = sum(max(p["current_value"], p["cost_basis"]) for p in sl_pos)
                sl_dd = ((sl_val - sl_peak) / sl_peak * 100) if sl_peak else 0
                sl_dd_col = RED if sl_dd < -5 else AMBER if sl_dd < 0 else GREEN
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                    f'<span style="min-width:110px;font-size:13px">{sleeve_name}</span>'
                    f'<span style="color:{sl_dd_col};font-weight:700;font-size:14px">{sl_dd:+.1f}%</span>'
                    f'<span style="color:{MUTED};font-size:12px">({fmt(sl_val)} / {fmt(sl_peak)})</span>'
                    f'</div>', unsafe_allow_html=True)

        st.markdown("")
        st.info("💡 **For full benchmark tracking** (portfolio vs Nifty 50 over time), connect a daily NAV/snapshot job. "
                "This will enable time-series comparison, rolling alpha, and max drawdown history.")


# ═══════════════════════════════════════════════════════════
# TAB 5 — THESIS MONITOR
# ═══════════════════════════════════════════════════════════
with tab_thesis:
    st.markdown("#### Investment Thesis Monitor")
    st.caption("Separate business thesis from price movement. Anchor positions deserve deepest monitoring.")

    # Build thesis table
    thesis_rows = []
    for p in sorted(positions, key=lambda x: x["weight"], reverse=True):
        ticker = p["ticker"]

        # Check news categories
        ticker_news = [n for n in D["news"] if n.get("ticker") == ticker]
        has_threat = any(n.get("severity_tag") == "thesis-threatening" for n in ticker_news)
        has_material = any(n.get("severity_tag") == "material change" for n in ticker_news)

        # Fundamentals snapshot
        snap = next((s for s in D["fundsnap"] if s.get("ticker") == ticker), None)

        # Technical: based on price vs stop
        if p["cmp"] <= p["stop"] and p["stop"] > 0:
            tech = "🔴 STOP"
        elif p["cmp"] < p["entry"]:
            tech = "🟡 Below entry"
        else:
            tech = "🟢 Positive"

        # Overall
        if has_threat:
            overall = "🔴 Review"
        elif has_material or (p["cmp"] <= p["stop"] and p["stop"] > 0):
            overall = "🟡 Review"
        else:
            overall = "🟢 Intact"

        urg = review_urgency(p["review_date"])

        thesis_rows.append({
            "Ticker": ticker,
            "Sleeve": p["sleeve"],
            "Fundamentals": "📊 Available" if snap else "⏳ Pending",
            "News": f"🔴 Threat" if has_threat else (f"🟡 Material" if has_material else "🟢 Clear"),
            "Technical": tech,
            "Overall": overall,
            "Next Review": f"{fmt_date(p['review_date'])} {urg}" if p["review_date"] else "Set date",
            "Thesis Note": p["thesis_note"][:50] + "…" if len(p["thesis_note"]) > 50 else p["thesis_note"],
        })

    st.dataframe(pd.DataFrame(thesis_rows), use_container_width=True, hide_index=True)

    st.divider()

    # Per-ticker thesis detail
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        st.markdown("##### Fundamental Alerts")
        st.caption("Automate from filings/results")
        st.markdown("Revenue / EBITDA miss → **Monitor**")
        st.markdown("Cash-flow deterioration → **Monitor**")
        st.markdown("Debt / dilution → **Monitor**")
    with tc2:
        st.markdown("##### Governance Alerts")
        st.caption("High-impact exceptions")
        st.markdown("Promoter selling / pledge → **Monitor**")
        st.markdown("RPT / related-party changes → **Monitor**")
        st.markdown("Credit rating → **Monitor**")
    with tc3:
        st.markdown("##### Upcoming Events")
        st.caption("Next 30 days")
        st.markdown("Results → **Connect calendar**")
        st.markdown("Concall / presentation → **Connect filings**")
        st.markdown("Corporate actions → **Connect filings**")

    # News feed for thesis
    st.divider()
    st.markdown("#### Material News Feed")
    mat_news = [n for n in D["news"] if n.get("severity_tag") != "routine update"]
    if mat_news:
        for n in mat_news[:15]:
            sev = n.get("severity_tag", "routine update")
            ticker = n.get("ticker", "")
            headline = n.get("headline", "")
            reasoning = n.get("severity_reasoning") or ""
            border = "#ef4444" if sev == "thesis-threatening" else "#f59e0b"
            bg = "rgba(239,68,68,.04)" if sev == "thesis-threatening" else "rgba(245,158,11,.03)"
            st.markdown(
                f'<div style="padding:8px 12px;margin:3px 0;background:{bg};border-left:3px solid {border};border-radius:3px">'
                f'<span style="font-size:13px">{sev_icon(sev)} <strong>{ticker}</strong> — {headline}</span><br/>'
                f'<span style="font-size:11px;color:#777">{short_date(n.get("published_at"))} · {sev}</span>'
                f'{"<br/><span style=font-size:11px;color:#555;font-style:italic>⚡ " + reasoning + "</span>" if reasoning else ""}'
                f'</div>', unsafe_allow_html=True)
    else:
        st.success("No material news alerts.")


# ═══════════════════════════════════════════════════════════
# TAB 6 — SYSTEM & DATA
# ═══════════════════════════════════════════════════════════
with tab_system:
    st.markdown("#### Data Feed Status")

    ds1, ds2, ds3 = st.columns(3)
    with ds1:
        if has_live:
            st.success(f"**Prices** — ✅ Connected ({len(price_map)} tickers)")
        else:
            st.warning("**Prices** — ⚠️ No fresh prices")
    with ds2:
        fund_count = len(D["fundsnap"])
        if fund_count > 0:
            st.success(f"**Fundamentals** — ✅ {fund_count} snapshots")
        else:
            st.warning("**Fundamentals** — ⏳ Pending feed")
    with ds3:
        news_count = len(D["news"])
        if news_count > 0:
            latest_news = D["news"][0].get("published_at", "")
            st.success(f"**News** — ✅ {news_count} items (latest: {short_date(latest_news)})")
        else:
            st.warning("**News** — ⏳ Pending feed")

    st.divider()
    st.markdown("#### Automation Health")
    st.caption("The dashboard should never look healthy when the underlying data is stale.")

    auto_rows = [
        {"Feed / Job": "Market prices", "Status": "✅ Healthy" if has_live else "⚠️ Pending",
         "Last Update": short_date(D["stocks"][0].get("price_updated_at")) if D["stocks"] and D["stocks"][0].get("price_updated_at") else "—",
         "Cadence": "Every 30 min (market hours)", "Failure Action": "Alert"},
        {"Feed / Job": "Portfolio transactions", "Status": "✅ Healthy",
         "Last Update": "Live from Supabase", "Cadence": "On change", "Failure Action": "Alert"},
        {"Feed / Job": "Announcements scan", "Status": "✅ Active" if news_count > 0 else "⚠️ Pending",
         "Last Update": short_date(D["news"][0].get("fetched_at")) if D["news"] else "—",
         "Cadence": "Hourly", "Failure Action": "Alert + retry"},
        {"Feed / Job": "Fundamentals sync", "Status": "✅ Active" if fund_count > 0 else "⚠️ Pending",
         "Last Update": short_date(D["fundsnap"][0].get("pulled_at")) if D["fundsnap"] else "—",
         "Cadence": "Quarterly / event", "Failure Action": "Flag stale"},
    ]
    st.dataframe(pd.DataFrame(auto_rows), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Core Data Objects")
    do1, do2, do3 = st.columns(3)
    do1.markdown("**1. Security Master** — `stocks` table\n\nTicker, exchange, sector, identifiers")
    do1.markdown("**2. Position Ledger** — `position_monitoring` + `lots`\n\nTransactions, quantity, cost, stops")
    do2.markdown("**3. Thesis Record** — `research_log` + `news_raw`\n\nDrivers, risks, valuation, invalidation")
    do2.markdown("**4. Trade History** — `trade_history`\n\nClosed trades with full entry/exit details")
    do3.markdown("**5. Fundamentals** — `fundamentals_snapshots`\n\nPE, ROE, ROCE, margins, growth")
    do3.markdown("**6. Alert Log** — `alert_log`\n\nSent alerts for cooldown deduplication")

    st.divider()

    # Refresh + sidebar info
    col_r1, col_r2 = st.columns([1, 3])
    with col_r1:
        if st.button("🔄 Refresh all data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_r2:
        st.caption("Data refreshes automatically every 60 seconds. Prices sync every 30 min during market hours. "
                    "Announcements scan hourly.")


# ── Footer ──────────────────────────────────────────────────
st.divider()
st.caption("India Master Portfolio V2 · Live from Supabase · Dashboard auto-refreshes every 60s")
