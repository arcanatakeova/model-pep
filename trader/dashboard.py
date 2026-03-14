"""
AI Trader v3.0 — Real-Time Trading Dashboard
=============================================
Run with:
    cd trader
    streamlit run dashboard.py

Opens in browser at http://localhost:8501
Auto-refreshes every 15 seconds.
Reads live data from trader JSON files — zero impact on bot performance.

Features:
  • Live equity curve with Plotly (interactive zoom/pan)
  • Open positions table with unrealized P&L, leverage, liq price
  • Signal feed — last signals that fired and why
  • Performance analytics — win rate, Sharpe, profit factor, max DD
  • Market heatmap — 24h performance by asset
  • Risk gauges — daily loss, drawdown, cash utilization
  • Funding arb tracker
  • Grid trading positions
  • Bot health — last cycle time, API status
  • One-click controls — pause/resume bot
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Graceful import of streamlit/plotly ───────────────────────────────────────
try:
    import streamlit as st
    import plotly.graph_objects as go
    import plotly.express as px
    import pandas as pd
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install streamlit plotly pandas")
    sys.exit(1)

# ── Path setup ─────────────────────────────────────────────────────────────────
TRADER_DIR = Path(__file__).parent
sys.path.insert(0, str(TRADER_DIR))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Trader v3.0",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS for dark professional look ─────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .stApp { background-color: #0e1117; }

    /* Metric card */
    div[data-testid="metric-container"] {
        background: linear-gradient(135deg, #1a1f2e 0%, #16213e 100%);
        border: 1px solid #2a3550;
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    div[data-testid="metric-container"] label {
        color: #7b8cb5 !important;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        color: #e8eaf0 !important;
        font-size: 26px;
        font-weight: 700;
    }

    /* Section headers */
    .section-header {
        color: #7b8cb5;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 2px;
        font-weight: 600;
        margin: 24px 0 12px 0;
        padding-bottom: 6px;
        border-bottom: 1px solid #1e2740;
    }

    /* Position table rows */
    .pos-row-profit { background-color: rgba(0, 200, 83, 0.08) !important; }
    .pos-row-loss   { background-color: rgba(239, 68, 68, 0.08) !important; }

    /* Status badge */
    .badge-live   { background:#0d4f1c; color:#34d058; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:700; }
    .badge-paper  { background:#4a3000; color:#f5a623; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:700; }
    .badge-paused { background:#3d0000; color:#ef4444; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:700; }

    /* Override Streamlit dataframe styles */
    .stDataFrame { border-radius: 8px; overflow: hidden; }

    /* Scrollable signal feed */
    .signal-feed {
        max-height: 350px;
        overflow-y: auto;
        font-family: 'Courier New', monospace;
        font-size: 12px;
        background: #0d1117;
        border: 1px solid #1e2740;
        border-radius: 8px;
        padding: 12px;
        color: #c9d1d9;
    }
</style>
""", unsafe_allow_html=True)


# ─── Data Loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=5)
def load_portfolio() -> dict:
    path = TRADER_DIR / "trades.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

@st.cache_data(ttl=5)
def load_equity_curve() -> list[dict]:
    path = TRADER_DIR / "equity_curve.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []

@st.cache_data(ttl=5)
def load_dex_positions() -> dict:
    path = TRADER_DIR / "dex_positions.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

@st.cache_data(ttl=10)
def load_bot_state() -> dict:
    """Read lightweight bot health state file."""
    path = TRADER_DIR / "bot_state.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def is_bot_paused() -> bool:
    return (TRADER_DIR / "PAUSED").exists()

def pause_bot():
    (TRADER_DIR / "PAUSED").touch()

def resume_bot():
    try:
        (TRADER_DIR / "PAUSED").unlink()
    except FileNotFoundError:
        pass


# ─── Derived Analytics ────────────────────────────────────────────────────────

def compute_sharpe(equity_curve: list[dict], risk_free_daily: float = 0.00013) -> float:
    """Annualised Sharpe ratio from daily equity curve."""
    if len(equity_curve) < 10:
        return 0.0
    df = pd.DataFrame(equity_curve)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").resample("1D")["equity"].last().dropna()
    if len(df) < 3:
        return 0.0
    rets = df.pct_change().dropna()
    excess = rets - risk_free_daily
    std = excess.std()
    if std == 0:
        return 0.0
    return float((excess.mean() / std) * (252 ** 0.5))

def compute_sortino(equity_curve: list[dict]) -> float:
    if len(equity_curve) < 10:
        return 0.0
    df = pd.DataFrame(equity_curve)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").resample("1D")["equity"].last().dropna()
    if len(df) < 3:
        return 0.0
    rets = df.pct_change().dropna()
    downside = rets[rets < 0]
    down_std = downside.std()
    if down_std == 0 or pd.isna(down_std):
        return float("inf")
    return float((rets.mean() / down_std) * (252 ** 0.5))

def compute_max_drawdown(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0.0
    equities = [e["equity"] for e in equity_curve]
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        peak = max(peak, e)
        dd = (peak - e) / peak
        max_dd = max(max_dd, dd)
    return max_dd * 100

def pnl_color(val: float) -> str:
    return "#34d058" if val >= 0 else "#ef4444"

def fmt_usd(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}${val:,.2f}"

def fmt_pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


# ─── Main Layout ──────────────────────────────────────────────────────────────

def render():
    portfolio  = load_portfolio()
    eq_curve   = load_equity_curve()
    dex_pos    = load_dex_positions()
    bot_state  = load_bot_state()
    paused     = is_bot_paused()

    if not portfolio:
        st.warning("⚠️  No portfolio data yet. Start the bot with: `python main.py`")
        st.stop()

    # ── Extract core values ────────────────────────────────────────────────────
    cash         = portfolio.get("cash", 0)
    open_pos     = portfolio.get("open_positions", {})
    closed       = portfolio.get("closed_trades", [])
    initial_cap  = portfolio.get("initial_capital", 10000)
    peak_equity  = portfolio.get("peak_equity", initial_cap)

    # Calculate equity (cash + unrealized P&L of open positions)
    unrealized = sum(
        (p.get("current_price", p.get("entry_price", 0)) - p.get("entry_price", 0))
        * p.get("qty", 0)
        for p in open_pos.values()
        if p.get("side") == "long"
    )
    equity  = cash + unrealized
    ret_pct = (equity - initial_cap) / initial_cap * 100 if initial_cap > 0 else 0

    # ── Header row ────────────────────────────────────────────────────────────
    col_title, col_mode, col_refresh = st.columns([4, 1, 1])
    with col_title:
        st.markdown("## 📈 AI Trader v3.0 — Live Dashboard")
    with col_mode:
        mode = bot_state.get("mode", "paper")
        badge = "badge-paused" if paused else ("badge-live" if mode == "live" else "badge-paper")
        label = "PAUSED" if paused else mode.upper()
        st.markdown(f'<span class="{badge}">{label}</span>', unsafe_allow_html=True)
        st.caption(f"Cycle #{bot_state.get('cycle', '—')}")
    with col_refresh:
        if st.button("⟳ Refresh"):
            st.cache_data.clear()
            st.rerun()

    # ── Top KPI metrics ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Portfolio Overview</div>', unsafe_allow_html=True)

    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6 = st.columns(6)

    with kpi1:
        st.metric("Total Equity", f"${equity:,.2f}",
                  delta=fmt_pct(ret_pct),
                  delta_color="normal")
    with kpi2:
        daily_pnl = bot_state.get("daily_pnl_usd", 0)
        daily_pct = bot_state.get("daily_pnl_pct", 0)
        st.metric("Today's P&L", fmt_usd(daily_pnl), delta=fmt_pct(daily_pct))
    with kpi3:
        st.metric("Open Positions", len(open_pos) + len(dex_pos))
    with kpi4:
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
        wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
        wr   = wins / len(closed) * 100 if closed else 0
        st.metric("Win Rate", f"{wr:.1f}%", delta=f"{len(closed)} trades")
    with kpi5:
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        st.metric("Drawdown", f"{dd:.2f}%", delta=f"peak ${peak_equity:,.0f}")
    with kpi6:
        cash_pct = cash / equity * 100 if equity > 0 else 0
        st.metric("Free Cash", f"${cash:,.2f}", delta=f"{cash_pct:.0f}% of equity")

    st.divider()

    # ── Equity Curve ──────────────────────────────────────────────────────────
    left, right = st.columns([3, 1])

    with left:
        st.markdown('<div class="section-header">Equity Curve</div>', unsafe_allow_html=True)

        if eq_curve:
            df_eq = pd.DataFrame(eq_curve)
            df_eq["ts"] = pd.to_datetime(df_eq["ts"])

            # Timeframe selector
            tf = st.radio("Timeframe", ["1H", "6H", "1D", "7D", "ALL"],
                          horizontal=True, index=4, label_visibility="collapsed")
            now = pd.Timestamp.now(tz="UTC")
            cutoffs = {"1H": now - timedelta(hours=1),
                       "6H": now - timedelta(hours=6),
                       "1D": now - timedelta(days=1),
                       "7D": now - timedelta(days=7),
                       "ALL": pd.Timestamp.min.tz_localize("UTC")}
            df_plot = df_eq[df_eq["ts"] >= cutoffs[tf]]

            fig = go.Figure()
            is_profit = df_plot["equity"].iloc[-1] >= df_plot["equity"].iloc[0] \
                        if len(df_plot) > 1 else True
            line_color = "#34d058" if is_profit else "#ef4444"
            fill_color = "rgba(52,208,88,0.08)" if is_profit else "rgba(239,68,68,0.08)"

            fig.add_trace(go.Scatter(
                x=df_plot["ts"], y=df_plot["equity"],
                mode="lines",
                line=dict(color=line_color, width=2),
                fill="tozeroy", fillcolor=fill_color,
                name="Equity",
                hovertemplate="<b>$%{y:,.2f}</b><br>%{x|%b %d %H:%M}<extra></extra>",
            ))

            # Mark initial capital as reference line
            fig.add_hline(y=initial_cap, line_dash="dot",
                          line_color="#3a4556", annotation_text=f"Initial ${initial_cap:,.0f}",
                          annotation_position="bottom right")

            fig.update_layout(
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                margin=dict(l=0, r=0, t=10, b=0),
                height=320,
                xaxis=dict(gridcolor="#1e2740", color="#7b8cb5", showgrid=True),
                yaxis=dict(gridcolor="#1e2740", color="#7b8cb5", showgrid=True,
                           tickprefix="$", tickformat=",.0f"),
                showlegend=False,
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Equity curve will appear after the first bot cycle.")

    with right:
        st.markdown('<div class="section-header">Performance</div>', unsafe_allow_html=True)

        sharpe  = compute_sharpe(eq_curve)
        sortino = compute_sortino(eq_curve)
        max_dd  = compute_max_drawdown(eq_curve)

        wins    = [t for t in closed if t.get("pnl_usd", 0) > 0]
        losses  = [t for t in closed if t.get("pnl_usd", 0) <= 0]
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss= sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        gross_w = sum(t["pnl_usd"] for t in wins)
        gross_l = abs(sum(t["pnl_usd"] for t in losses))
        pf      = gross_w / gross_l if gross_l > 0 else float("inf")

        st.metric("Sharpe Ratio",  f"{sharpe:.2f}")
        st.metric("Sortino Ratio", f"{sortino:.2f}" if sortino != float("inf") else "∞")
        st.metric("Max Drawdown",  f"{max_dd:.2f}%")
        st.metric("Profit Factor", f"{min(pf, 99):.2f}" if pf != float("inf") else "∞")
        st.metric("Avg Win",  fmt_pct(avg_win))
        st.metric("Avg Loss", fmt_pct(avg_loss))
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
        st.metric("Total Realised", fmt_usd(total_pnl))

    st.divider()

    # ── Open Positions ────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Open Positions</div>', unsafe_allow_html=True)

    all_positions = []

    # CEX / Futures positions
    for pid, pos in open_pos.items():
        entry   = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        side    = pos.get("side", "long")
        if side == "long":
            pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        else:
            pnl_pct = (entry - current) / entry * 100 if entry > 0 else 0

        lev     = pos.get("leverage", 1)
        liq     = pos.get("liq_price")
        is_fut  = pos.get("is_futures", False)
        market  = ("⚡ Futures" if is_fut else "📊 CEX") + f" {pos.get('market','')}"

        all_positions.append({
            "Symbol": pos.get("symbol", pid),
            "Market": market,
            "Side": "🟢 LONG" if side == "long" else "🔴 SHORT",
            "Leverage": f"{lev}x" if lev > 1 else "—",
            "Entry": f"${entry:,.4f}",
            "Current": f"${current:,.4f}",
            "P&L %": fmt_pct(pnl_pct),
            "Liq Price": f"${liq:,.4f}" if liq else "—",
            "Opened": pos.get("opened_at", "")[:16].replace("T", " "),
        })

    # DEX positions
    for pair_addr, pos in dex_pos.items():
        entry   = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        all_positions.append({
            "Symbol": pos.get("symbol", pair_addr[:8]),
            "Market": f"🌊 DEX {pos.get('chain','').upper()}",
            "Side": "🟢 LONG",
            "Leverage": "—",
            "Entry": f"${entry:.8f}",
            "Current": f"${current:.8f}",
            "P&L %": fmt_pct(pnl_pct),
            "Liq Price": "—",
            "Opened": pos.get("opened_at", "")[:16].replace("T", " "),
        })

    if all_positions:
        df_pos = pd.DataFrame(all_positions)
        st.dataframe(df_pos, use_container_width=True, hide_index=True,
                     column_config={
                         "P&L %": st.column_config.TextColumn("P&L %"),
                     })
    else:
        st.info("No open positions. Bot is scanning for opportunities...")

    st.divider()

    # ── Bottom row: Trade History | Market Breakdown | Grid/Arb | Controls ────
    col_hist, col_mkt, col_special, col_ctrl = st.columns([2, 1, 1, 1])

    # Trade History
    with col_hist:
        st.markdown('<div class="section-header">Recent Trades</div>', unsafe_allow_html=True)
        if closed:
            recent = closed[-20:][::-1]  # Last 20 trades, newest first
            rows = []
            for t in recent:
                pnl = t.get("pnl_usd", 0)
                rows.append({
                    "Time": t.get("closed_at", "")[:16].replace("T", " "),
                    "Symbol": t.get("symbol", "—"),
                    "Side": t.get("side", "—").upper(),
                    "P&L $": fmt_usd(pnl),
                    "P&L %": fmt_pct(t.get("pnl_pct", 0)),
                    "Reason": t.get("close_reason", "—")[:30],
                })
            df_trades = pd.DataFrame(rows)
            st.dataframe(df_trades, use_container_width=True, hide_index=True, height=300)
        else:
            st.info("No closed trades yet.")

    # Market Breakdown
    with col_mkt:
        st.markdown('<div class="section-header">By Market</div>', unsafe_allow_html=True)
        if closed:
            by_market = {}
            for t in closed:
                m = t.get("market", "unknown")
                if m not in by_market:
                    by_market[m] = {"pnl": 0, "trades": 0, "wins": 0}
                by_market[m]["pnl"]    += t.get("pnl_usd", 0)
                by_market[m]["trades"] += 1
                if t.get("pnl_usd", 0) > 0:
                    by_market[m]["wins"] += 1

            rows = []
            for m, stats in sorted(by_market.items(), key=lambda x: -x[1]["pnl"]):
                wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
                rows.append({
                    "Market": m.upper(),
                    "Trades": stats["trades"],
                    "Win %": f"{wr:.0f}%",
                    "P&L": fmt_usd(stats["pnl"]),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True, height=300)

            # Mini pie chart
            fig_pie = px.pie(
                pd.DataFrame([{"Market": k, "PnL": max(v["pnl"], 0)}
                               for k, v in by_market.items() if v["pnl"] > 0]),
                names="Market", values="PnL",
                color_discrete_sequence=px.colors.qualitative.Dark24,
            )
            fig_pie.update_layout(
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                margin=dict(l=0, r=0, t=10, b=0), height=180,
                legend=dict(font=dict(color="#7b8cb5"), bgcolor="rgba(0,0,0,0)"),
                showlegend=True,
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    # Grid + Arb status
    with col_special:
        st.markdown('<div class="section-header">Grid & Arb</div>', unsafe_allow_html=True)

        # Load grid/arb state if available
        grid_state_path = TRADER_DIR / "grid_state.json"
        arb_state_path  = TRADER_DIR / "arb_state.json"

        try:
            with open(grid_state_path) as f:
                grid_state = json.load(f)
            st.caption("📊 Grid Trading")
            st.metric("Active Grids",   grid_state.get("active_grids", 0))
            st.metric("Grid Fills",     grid_state.get("total_fills", 0))
            st.metric("Grid P&L",       fmt_usd(grid_state.get("total_pnl_usd", 0)))
        except Exception:
            st.caption("📊 Grid Trading")
            st.info("No grid data yet.")

        st.divider()

        try:
            with open(arb_state_path) as f:
                arb_state = json.load(f)
            st.caption("⚡ Funding Arb")
            st.metric("Open Arbs",      arb_state.get("open_arbs", 0))
            collected = arb_state.get("total_funding_collected_usd", 0)
            st.metric("Collected",      fmt_usd(collected))
        except Exception:
            st.caption("⚡ Funding Arb")
            st.info("No arb data yet.")

    # Controls
    with col_ctrl:
        st.markdown('<div class="section-header">Bot Controls</div>', unsafe_allow_html=True)

        if paused:
            st.error("🔴 Bot is PAUSED")
            if st.button("▶ Resume Bot", use_container_width=True, type="primary"):
                resume_bot()
                st.success("Bot resumed!")
                time.sleep(1)
                st.rerun()
        else:
            st.success("🟢 Bot is RUNNING")
            if st.button("⏸ Pause Bot", use_container_width=True):
                pause_bot()
                st.warning("Bot will pause after current cycle.")
                time.sleep(1)
                st.rerun()

        st.divider()

        # Health
        last_cycle = bot_state.get("last_cycle_ts", 0)
        if last_cycle:
            age = time.time() - last_cycle
            color = "🟢" if age < 60 else "🟡" if age < 120 else "🔴"
            st.caption(f"{color} Last cycle: {age:.0f}s ago")
        else:
            st.caption("⚪ Bot not running")

        cycle_ms = bot_state.get("last_cycle_ms", 0)
        if cycle_ms:
            st.caption(f"⏱ Cycle time: {cycle_ms:.0f}ms")

        ws_connected = bot_state.get("ws_connected", False)
        st.caption(f"{'🟢' if ws_connected else '🔴'} WebSocket: {'connected' if ws_connected else 'offline'}")

        futures_on = bot_state.get("futures_enabled", False)
        st.caption(f"{'⚡' if futures_on else '⚫'} Futures: {'on' if futures_on else 'off'}")

        st.divider()
        if st.button("📋 Full Report (JSON)", use_container_width=True):
            try:
                import config as cfg
                perf = {"equity": equity, "cash": cash, "initial_capital": initial_cap,
                        "total_return_pct": ret_pct, "total_trades": len(closed),
                        "win_rate_pct": wr if closed else 0}
                st.json(perf)
            except Exception:
                st.error("Load bot first")

    st.divider()

    # ── Signal Feed ───────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Signal Log (last 50 events)</div>',
                unsafe_allow_html=True)

    log_path = TRADER_DIR / "trader.log"
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            # Filter to signal/trade lines
            keywords = ["EXECUTED", "CLOSED", "FUTURES", "SCALP", "GRID", "ARB",
                        "DEX BUY", "DEX CLOSE", "PARTIAL", "PYRAMID", "CIRCUIT", "WARNING"]
            signal_lines = [
                l.rstrip() for l in lines
                if any(k in l for k in keywords)
            ][-50:]

            feed_html = '<div class="signal-feed">'
            for line in reversed(signal_lines):
                color = "#34d058" if any(w in line for w in ["EXECUTED BUY", "FUTURES LONG", "SCALP", "ARB OPEN", "GRID OPEN", "DEX BUY"]) \
                    else "#ef4444" if any(w in line for w in ["STOP LOSS", "CIRCUIT", "LIQUIDATION", "WARNING"]) \
                    else "#c9d1d9"
                escaped = line.replace("<", "&lt;").replace(">", "&gt;")
                feed_html += f'<div style="color:{color}; margin:2px 0">{escaped}</div>'
            feed_html += "</div>"
            st.markdown(feed_html, unsafe_allow_html=True)
        except Exception as e:
            st.caption(f"Could not load log: {e}")
    else:
        st.info("Log file not found. Start the bot to see signals here.")

    # ── Auto-refresh ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(f"⟳ Auto-refreshing every 15s | Last update: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(15)
    st.rerun()


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    render()
else:
    # Called by streamlit run dashboard.py
    render()
