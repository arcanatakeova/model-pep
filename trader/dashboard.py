"""
AI Trader v3.0 — Live Visual Dashboard
=======================================
Run with:
    cd trader
    streamlit run dashboard.py

Refreshes every 5 seconds. Reads JSON files written by the bot every cycle.
"""
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

try:
    import streamlit as st
    import plotly.graph_objects as go
    import pandas as pd
except ImportError as e:
    print(f"Missing: {e}. Run: pip3 install streamlit plotly pandas")
    sys.exit(1)

TRADER_DIR = Path(__file__).parent

st.set_page_config(
    page_title="AI Trader v3.0",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    /* Dark base */
    .stApp, .main, section[data-testid="stSidebar"] {
        background-color: #0a0e1a !important;
    }
    .block-container { padding-top: 1rem !important; }

    /* KPI cards */
    div[data-testid="metric-container"] {
        background: linear-gradient(135deg, #111827 0%, #0f172a 100%);
        border: 1px solid #1e3a5f;
        border-radius: 12px;
        padding: 18px 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    }
    div[data-testid="metric-container"] label {
        color: #64748b !important;
        font-size: 11px !important;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        font-weight: 600;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        color: #f1f5f9 !important;
        font-size: 28px !important;
        font-weight: 800 !important;
    }
    div[data-testid="stMetricDelta"] svg { display: none; }
    div[data-testid="stMetricDelta"] > div { font-size: 13px !important; font-weight: 600; }

    /* Section labels */
    .section-label {
        color: #475569;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 2.5px;
        font-weight: 700;
        margin: 20px 0 10px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid #1e2d45;
    }

    /* Trade cards */
    .trade-card {
        border-radius: 12px;
        padding: 16px 18px;
        margin-bottom: 10px;
        transition: all 0.2s;
    }

    /* Signal feed */
    .feed {
        height: 340px;
        overflow-y: auto;
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 11.5px;
        background: #070b14;
        border: 1px solid #1e2d45;
        border-radius: 10px;
        padding: 12px 14px;
        color: #94a3b8;
    }
    .feed-line { padding: 3px 0; border-bottom: 1px solid #0d1526; }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 4px; }
    ::-webkit-scrollbar-track { background: #0a0e1a; }
    ::-webkit-scrollbar-thumb { background: #1e3a5f; border-radius: 2px; }

    /* Hide streamlit branding */
    #MainMenu, footer, header { visibility: hidden; }
    .stDeployButton { display: none; }
</style>
""", unsafe_allow_html=True)


# ─── Data Loading (no caching — always fresh) ─────────────────────────────────

def load(filename: str, default):
    try:
        with open(TRADER_DIR / filename) as f:
            return json.load(f)
    except Exception:
        return default

def is_paused() -> bool:
    return (TRADER_DIR / "PAUSED").exists()


# ─── HTML Helpers ─────────────────────────────────────────────────────────────

def trade_card_html(symbol: str, market: str, side: str, pnl_pct: float,
                    pnl_usd: float, entry: float, current: float,
                    stop_pct: float, target_pct: float, opened: str,
                    leverage: int = 1, size_usd: float = 0) -> str:
    """
    Returns HTML for a single trade card.
    IMPORTANT: Uses only ONE outer <div> + inline <span>/<br> inside.
    Streamlit's markdown renderer strips nested <div> blocks, so we
    avoid them entirely and rely on float + inline elements only.
    """
    green  = "#22c55e"
    red    = "#ef4444"
    color  = green if pnl_usd >= 0 else red
    border = "#14532d" if pnl_usd >= 0 else "#7f1d1d"
    bg     = "rgba(34,197,94,0.06)" if pnl_usd >= 0 else "rgba(239,68,68,0.06)"
    sign   = "+" if pnl_usd >= 0 else ""

    stop_price   = entry * (1 - stop_pct)
    target_price = entry * (1 + target_pct)
    price_range  = target_price - stop_price
    progress     = ((current - stop_price) / price_range * 100) if price_range > 0 else 50
    progress     = max(2, min(98, progress))
    bar_color    = green if progress > 50 else red

    def fmt_price(p):
        if p < 0.0001: return f"${p:.8f}"
        if p < 0.01:   return f"${p:.6f}"
        if p < 1:      return f"${p:.4f}"
        if p < 10000:  return f"${p:,.2f}"
        return f"${p:,.0f}"

    time_str = ""
    try:
        opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        age_s = (datetime.now(timezone.utc) - opened_dt).total_seconds()
        h, m  = divmod(int(age_s // 60), 60)
        time_str = f"{h}h {m}m" if h > 0 else f"{m}m"
    except Exception:
        time_str = ""

    lev   = f' <span style="background:#1e3a5f;color:#60a5fa;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700">{leverage}x</span>' if leverage > 1 else ""
    sz    = f' &middot; <span style="color:#475569">${size_usd:,.0f}</span>' if size_usd >= 1 else ""
    s_col = green if side.lower() == "long" else red

    # Single outer div, all inner content uses only span + br (no nested divs)
    return (
        f'<div style="border:1px solid {border};border-left:4px solid {color};'
        f'border-radius:12px;padding:16px 18px;background:{bg};margin-bottom:12px;">'
        # Row 1: symbol left, big P&L right
        f'<span style="color:#f1f5f9;font-size:22px;font-weight:800">{symbol}</span>{lev}'
        f'<span style="float:right;color:{color};font-size:30px;font-weight:900;line-height:1">{sign}{pnl_pct:.1f}%</span><br>'
        # Row 2: market / side / time, USD P&L right
        f'<span style="color:#475569;font-size:11px">{market} &middot; '
        f'<span style="color:{s_col};font-weight:600">{side.upper()}</span>'
        f' &middot; &#9201; {time_str}{sz}</span>'
        f'<span style="float:right;color:{color};font-size:13px;font-weight:600">{sign}${abs(pnl_usd):.2f}</span><br><br>'
        # Row 3: prices + levels
        f'<span style="color:#64748b;font-size:11px">'
        f'Entry <b style="color:#94a3b8">{fmt_price(entry)}</b>'
        f' &rarr; Now <b style="color:#f1f5f9">{fmt_price(current)}</b>'
        f' &nbsp;&nbsp; <span style="color:#ef4444">&#x2193; Stop {stop_pct*100:.0f}%</span>'
        f' &nbsp; <span style="color:#22c55e">&#x2191; Target {target_pct*100:.0f}%</span>'
        f'</span><br>'
        # Progress bar: span display:block trick (no nested div)
        f'<span style="display:block;background:#0d1526;border-radius:6px;height:5px;margin-top:8px;overflow:hidden">'
        f'<span style="display:block;background:{bar_color};width:{progress:.1f}%;height:5px;border-radius:6px"></span>'
        f'</span>'
        f'</div>'
    )


# ─── Main Render ──────────────────────────────────────────────────────────────

def render():
    state     = load("bot_state.json", {})
    portfolio = load("trades.json", {})
    eq_curve  = load("equity_curve.json", [])
    dex_pos   = load("dex_positions.json", {})
    paused    = is_paused()

    if not state and not portfolio:
        st.markdown("""
        <div style="text-align:center;margin-top:120px;">
            <div style="font-size:48px;">📈</div>
            <h2 style="color:#f1f5f9;">AI Trader Dashboard</h2>
            <p style="color:#64748b;font-size:16px;">Waiting for bot to start...</p>
            <code style="background:#111827;color:#60a5fa;padding:8px 16px;border-radius:6px;">
                cd ~/model-pep/trader && python3 main.py
            </code>
        </div>
        """, unsafe_allow_html=True)
        time.sleep(3)
        st.rerun()

    # ── Core values ───────────────────────────────────────────────────────────
    equity      = state.get("equity",         portfolio.get("cash", 10000))
    cash        = state.get("cash",           portfolio.get("cash", equity))
    initial_cap = state.get("initial_capital",portfolio.get("initial_capital", 10000))
    peak_equity = state.get("peak_equity",    portfolio.get("peak_equity", initial_cap))
    daily_pnl   = state.get("daily_pnl_usd", 0)
    daily_pct   = state.get("daily_pnl_pct", 0)
    total_ret   = (equity - initial_cap) / initial_cap * 100 if initial_cap else 0
    drawdown    = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
    open_pos    = portfolio.get("open_positions", {})
    closed      = portfolio.get("closed_trades", [])
    mode        = state.get("mode", "paper")
    cycle       = state.get("cycle", 0)
    total_open  = len(open_pos) + len(dex_pos)

    wins   = [t for t in closed if t.get("pnl_usd", 0) > 0]
    losses = [t for t in closed if t.get("pnl_usd", 0) <= 0]
    wr     = len(wins) / len(closed) * 100 if closed else 0
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)

    # ── Header ────────────────────────────────────────────────────────────────
    h1, h2, h3, h4 = st.columns([3, 1, 1, 1])
    with h1:
        st.markdown("## 📈 AI Trader v3.0")
    with h2:
        if paused:
            st.markdown('<div style="background:#450a0a;color:#f87171;padding:6px 14px;border-radius:8px;font-weight:700;text-align:center;">⏸ PAUSED</div>', unsafe_allow_html=True)
        elif mode == "live":
            st.markdown('<div style="background:#052e16;color:#4ade80;padding:6px 14px;border-radius:8px;font-weight:700;text-align:center;">🟢 LIVE</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="background:#1c1917;color:#fb923c;padding:6px 14px;border-radius:8px;font-weight:700;text-align:center;">📄 PAPER</div>', unsafe_allow_html=True)
    with h3:
        last_cycle = state.get("last_cycle_ts", 0)
        age = time.time() - last_cycle if last_cycle else 999
        status_color = "#4ade80" if age < 60 else "#fbbf24" if age < 120 else "#ef4444"
        st.markdown(f'<div style="color:{status_color};font-size:13px;margin-top:8px;">● Cycle #{cycle} · {age:.0f}s ago</div>', unsafe_allow_html=True)
    with h4:
        if st.button("⟳ Refresh Now"):
            st.rerun()

    # ── KPI Row ───────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        st.metric("Total Equity", f"${equity:,.2f}",
                  delta=f"{total_ret:+.2f}% all time",
                  delta_color="normal" if total_ret >= 0 else "inverse")
    with k2:
        st.metric("Today's P&L",
                  f"{'+'if daily_pnl>=0 else ''}${daily_pnl:,.2f}",
                  delta=f"{daily_pct:+.2f}%",
                  delta_color="normal" if daily_pnl >= 0 else "inverse")
    with k3:
        st.metric("Open Positions", str(total_open),
                  delta=f"{len(open_pos)} CEX · {len(dex_pos)} DEX")
    with k4:
        st.metric("Win Rate", f"{wr:.1f}%",
                  delta=f"{len(closed)} trades closed")
    with k5:
        st.metric("Drawdown", f"{drawdown:.2f}%",
                  delta=f"peak ${peak_equity:,.0f}",
                  delta_color="inverse" if drawdown > 0 else "off")
    with k6:
        cash_pct = cash / equity * 100 if equity > 0 else 0
        st.metric("Free Cash", f"${cash:,.2f}",
                  delta=f"{cash_pct:.0f}% deployed")

    # ── Equity Curve ──────────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Equity Curve</div>', unsafe_allow_html=True)

    if eq_curve and len(eq_curve) > 1:
        tf_col, _ = st.columns([2, 5])
        with tf_col:
            tf = st.radio("", ["1H", "6H", "1D", "ALL"], horizontal=True,
                          index=3, label_visibility="collapsed")

        df_eq = pd.DataFrame(eq_curve)
        df_eq["ts"] = pd.to_datetime(df_eq["ts"])
        now_utc = pd.Timestamp.now(tz="UTC")
        cuts = {"1H": now_utc - timedelta(hours=1),
                "6H": now_utc - timedelta(hours=6),
                "1D": now_utc - timedelta(days=1),
                "ALL": pd.Timestamp.min.tz_localize("UTC")}
        df_plot = df_eq[df_eq["ts"] >= cuts[tf]]
        if len(df_plot) < 2:
            df_plot = df_eq

        first_val = df_plot["equity"].iloc[0]
        last_val  = df_plot["equity"].iloc[-1]
        up        = last_val >= first_val
        line_col  = "#22c55e" if up else "#ef4444"
        fill_col  = "rgba(34,197,94,0.07)" if up else "rgba(239,68,68,0.07)"

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_plot["ts"], y=df_plot["equity"],
            mode="lines",
            line=dict(color=line_col, width=2.5),
            fill="tozeroy", fillcolor=fill_col,
            hovertemplate="<b>$%{y:,.2f}</b><br>%{x|%b %d %H:%M}<extra></extra>",
        ))
        fig.add_hline(y=initial_cap, line_dash="dot", line_color="#1e3a5f",
                      annotation_text=f"Start ${initial_cap:,.0f}",
                      annotation_font_color="#475569",
                      annotation_position="bottom right")
        fig.update_layout(
            paper_bgcolor="#0a0e1a", plot_bgcolor="#0a0e1a",
            margin=dict(l=0, r=0, t=8, b=0), height=260,
            xaxis=dict(gridcolor="#0d1526", color="#475569", showgrid=True,
                       zeroline=False),
            yaxis=dict(gridcolor="#0d1526", color="#475569", showgrid=True,
                       tickprefix="$", tickformat=",.0f", zeroline=False),
            showlegend=False, hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.markdown("""
        <div style="background:#0d1526;border:1px dashed #1e3a5f;border-radius:10px;
                    padding:32px;text-align:center;color:#475569;">
            📊 Equity curve populates after first bot cycle completes
        </div>""", unsafe_allow_html=True)

    # ── Open Positions — Visual Cards ─────────────────────────────────────────
    st.markdown('<div class="section-label">Open Positions</div>', unsafe_allow_html=True)

    all_cards = []

    # CEX / Futures
    for pid, pos in open_pos.items():
        entry   = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        side    = pos.get("side", "long")
        pnl_pct = ((current - entry) / entry * 100 if side == "long"
                   else (entry - current) / entry * 100) if entry > 0 else 0
        size_usd = pos.get("position_usd", pos.get("qty", 0) * entry)
        pnl_usd  = size_usd * pnl_pct / 100
        market   = "⚡ Futures" if pos.get("is_futures") else "📊 CEX"
        all_cards.append(dict(
            symbol   = pos.get("symbol", pid),
            market   = market,
            side     = side,
            pnl_pct  = pnl_pct,
            pnl_usd  = pnl_usd,
            entry    = entry,
            current  = current,
            stop_pct = pos.get("stop_pct", 0.03),
            target_pct = pos.get("take_profit_pct", 0.06),
            opened   = pos.get("opened_at", ""),
            leverage = pos.get("leverage", 1),
            size_usd = size_usd,
        ))

    # DEX
    for pair_addr, pos in dex_pos.items():
        entry   = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        size_usd = pos.get("size_usd", 0)
        pnl_usd  = size_usd * pnl_pct / 100
        all_cards.append(dict(
            symbol   = pos.get("symbol", pair_addr[:8]),
            market   = f"🌊 DEX {pos.get('chain','').upper()}",
            side     = "long",
            pnl_pct  = pnl_pct,
            pnl_usd  = pnl_usd,
            entry    = entry,
            current  = current,
            stop_pct = pos.get("stop_pct", 0.20),
            target_pct = pos.get("target_pct", 0.40),
            opened   = pos.get("opened_at", ""),
            leverage = 1,
            size_usd = size_usd,
        ))

    if all_cards:
        # Sort: biggest winners first, then biggest losers
        all_cards.sort(key=lambda c: c["pnl_pct"], reverse=True)
        cols = st.columns(min(len(all_cards), 3))
        for i, card in enumerate(all_cards):
            with cols[i % 3]:
                st.markdown(trade_card_html(**card), unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="background:#0d1526;border:1px dashed #1e3a5f;border-radius:10px;
                    padding:32px;text-align:center;color:#475569;">
            No open positions — bot is scanning for opportunities
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Bottom Row ────────────────────────────────────────────────────────────
    left, right = st.columns([2, 1])

    with left:
        # Recent trades
        st.markdown('<div class="section-label">Recent Trades</div>', unsafe_allow_html=True)
        if closed:
            rows = []
            for t in reversed(closed[-25:]):
                pnl = t.get("pnl_usd", 0)
                pct = t.get("pnl_pct", 0)
                emoji = "✅" if pnl > 0 else "❌"
                rows.append({
                    " ": emoji,
                    "Symbol": t.get("symbol", "—"),
                    "Market": t.get("market", "—").upper(),
                    "P&L": f"{'+'if pnl>=0 else ''}${pnl:,.2f}",
                    "%": f"{'+'if pct>=0 else ''}{pct:.1f}%",
                    "Reason": t.get("close_reason", "—")[:28],
                    "Closed": t.get("closed_at", "")[:16].replace("T", " "),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True, height=280)

            # Win/loss summary bar
            w_pnl = sum(t.get("pnl_usd",0) for t in wins)
            l_pnl = abs(sum(t.get("pnl_usd",0) for t in losses))
            pf    = w_pnl / l_pnl if l_pnl > 0 else float("inf")
            st.markdown(
                f'<div style="display:flex;gap:24px;font-size:12px;color:#64748b;margin-top:6px;">'
                f'<span>✅ {len(wins)} wins · <span style="color:#22c55e;">+${w_pnl:,.2f}</span></span>'
                f'<span>❌ {len(losses)} losses · <span style="color:#ef4444;">-${l_pnl:,.2f}</span></span>'
                f'<span>Profit factor: <span style="color:#f1f5f9;font-weight:700;">'
                f'{"∞" if pf == float("inf") else f"{pf:.2f}"}</span></span>'
                f'<span>Total: <span style="color:{"#22c55e" if total_pnl>=0 else "#ef4444"};font-weight:700;">'
                f'{"+"if total_pnl>=0 else ""}${total_pnl:,.2f}</span></span>'
                f'</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#475569;padding:20px;text-align:center;">No closed trades yet</div>',
                        unsafe_allow_html=True)

    with right:
        # Bot health + controls
        st.markdown('<div class="section-label">Bot Status</div>', unsafe_allow_html=True)

        cycle_ms = state.get("last_cycle_ms", 0)
        ws_ok    = state.get("ws_connected", False)
        fut_on   = state.get("futures_enabled", False)

        def status_row(icon, label, value, val_color="#94a3b8"):
            return (f'<div style="display:flex;justify-content:space-between;'
                    f'padding:7px 0;border-bottom:1px solid #0d1526;">'
                    f'<span style="color:#64748b;">{icon} {label}</span>'
                    f'<span style="color:{val_color};font-weight:600;">{value}</span></div>')

        age_color = "#4ade80" if age < 60 else "#fbbf24" if age < 120 else "#ef4444"
        html  = status_row("⏱", "Last cycle",  f"{age:.0f}s ago", age_color)
        html += status_row("🔄", "Cycle #",     str(cycle))
        html += status_row("⚡", "Cycle time",  f"{cycle_ms:.0f}ms")
        html += status_row("📡", "WebSocket",   "connected" if ws_ok else "REST fallback",
                           "#4ade80" if ws_ok else "#fbbf24")
        html += status_row("📈", "Futures",     "enabled" if fut_on else "disabled",
                           "#60a5fa" if fut_on else "#475569")
        html += status_row("💰", "Deployed",    f"${equity - cash:,.0f} ({100-cash/equity*100:.0f}%)" if equity > 0 else "—")
        st.markdown(f'<div style="font-size:13px;">{html}</div>', unsafe_allow_html=True)

        st.markdown("")
        if paused:
            if st.button("▶ Resume Bot", use_container_width=True, type="primary"):
                try: (TRADER_DIR / "PAUSED").unlink()
                except: pass
                st.rerun()
        else:
            if st.button("⏸ Pause Bot", use_container_width=True):
                (TRADER_DIR / "PAUSED").touch()
                st.rerun()

        # Grid/Arb quick stats
        grid_state = load("grid_state.json", {})
        arb_state  = load("arb_state.json", {})
        if grid_state or arb_state:
            st.markdown('<div class="section-label" style="margin-top:16px;">Grid & Arb</div>',
                        unsafe_allow_html=True)
            if grid_state.get("active_grids", 0) > 0:
                st.markdown(
                    f'<div style="font-size:12px;color:#64748b;">📊 {grid_state["active_grids"]} grids · '
                    f'{grid_state.get("total_fills",0)} fills · '
                    f'<span style="color:#22c55e;">+${grid_state.get("total_pnl_usd",0):.2f}</span></div>',
                    unsafe_allow_html=True)
            if arb_state.get("open_arbs", 0) > 0:
                st.markdown(
                    f'<div style="font-size:12px;color:#64748b;">⚡ {arb_state["open_arbs"]} arbs · '
                    f'collected <span style="color:#22c55e;">+${arb_state.get("total_funding_collected_usd",0):.2f}</span></div>',
                    unsafe_allow_html=True)

    # ── Signal Feed ───────────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Live Activity Feed</div>', unsafe_allow_html=True)

    log_path = TRADER_DIR / "trader.log"
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            keywords = ["DEX BUY", "DEX CLOSE", "EXECUTED", "CLOSED", "FUTURES",
                        "SCALP", "GRID", "ARB", "PARTIAL", "PYRAMID",
                        "CIRCUIT BREAKER", "WARNING", "Stop loss", "Take profit"]
            filtered = [l.rstrip() for l in lines if any(k in l for k in keywords)][-60:]

            def line_color(l):
                if any(k in l for k in ["DEX BUY", "EXECUTED BUY", "GRID OPEN", "ARB OPEN", "FUTURES LONG"]):
                    return "#4ade80"
                if any(k in l for k in ["Take profit", "CLOSE", "PARTIAL"]):
                    return "#60a5fa"
                if any(k in l for k in ["Stop loss", "CIRCUIT", "WARNING", "LIQUIDATION"]):
                    return "#f87171"
                return "#64748b"

            html = '<div class="feed">'
            for line in reversed(filtered):
                c = line_color(line)
                esc = line.replace("<", "&lt;").replace(">", "&gt;")
                html += f'<span style="display:block;color:{c};padding:3px 0;border-bottom:1px solid #0d1526">{esc}</span>'
            html += "</div>"
            st.markdown(html, unsafe_allow_html=True)
        except Exception as e:
            st.caption(f"Log error: {e}")

    # ── Footer + auto-refresh ─────────────────────────────────────────────────
    st.markdown(
        f'<div style="text-align:right;color:#1e3a5f;font-size:11px;margin-top:8px;">'
        f'⟳ refreshing every 5s · {datetime.now().strftime("%H:%M:%S")}'
        f'</div>', unsafe_allow_html=True)

    time.sleep(5)
    st.rerun()


render()
