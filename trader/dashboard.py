"""
AI Trader — TradingView-Style Live Dashboard
============================================
Run: cd trader && streamlit run dashboard.py

3-second auto-refresh. Reads JSON files written every bot cycle.
"""
import json
import time
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

try:
    import streamlit as st
    import streamlit.components.v1
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import pandas as pd
except ImportError as e:
    print(f"Missing: {e}. Run: pip3 install streamlit plotly pandas")
    sys.exit(1)

TRADER_DIR = Path(__file__).parent

# ── TradingView colour palette ────────────────────────────────────────────────
TV = dict(
    bg          = "#131722",
    bg2         = "#1e222d",
    bg3         = "#2a2e39",
    border      = "#2a2e39",
    text        = "#d1d4dc",
    text2       = "#787b86",
    green       = "#26a69a",
    green_dim   = "rgba(38,166,154,0.15)",
    red         = "#ef5350",
    red_dim     = "rgba(239,83,80,0.15)",
    blue        = "#2962ff",
    yellow      = "#f7c948",
    purple      = "#9c27b0",
    grid        = "rgba(42,46,57,0.8)",
)

st.set_page_config(
    page_title="AI Trader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global styles ─────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

  html, body, .stApp, section.main, [data-testid="stAppViewContainer"],
  [data-testid="stMain"], div[class*="css"] {{
      background-color: {TV['bg']} !important;
      font-family: 'Inter', -apple-system, sans-serif !important;
      color: {TV['text']} !important;
  }}

  /* Remove padding */
  .block-container {{ padding: 0.5rem 1rem 1rem 1rem !important; max-width: 100% !important; }}
  [data-testid="column"] {{ padding: 0 4px !important; }}

  /* KPI cards */
  .kpi-card {{
      background: {TV['bg2']};
      border: 1px solid {TV['border']};
      border-radius: 6px;
      padding: 14px 16px;
  }}
  .kpi-label {{
      color: {TV['text2']};
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 1.2px;
      font-weight: 600;
      margin-bottom: 4px;
  }}
  .kpi-value {{
      color: {TV['text']};
      font-size: 26px;
      font-weight: 800;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
  }}
  .kpi-delta {{
      font-size: 12px;
      font-weight: 600;
      margin-top: 2px;
  }}

  /* Position card */
  .pos-card {{
      background: {TV['bg2']};
      border: 1px solid {TV['border']};
      border-radius: 6px;
      padding: 14px 16px;
      margin-bottom: 8px;
      position: relative;
  }}
  .pos-symbol {{
      font-size: 18px;
      font-weight: 800;
      color: {TV['text']};
  }}
  .pos-pnl-pct {{
      font-size: 28px;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
      line-height: 1;
  }}
  .pos-meta {{
      font-size: 11px;
      color: {TV['text2']};
      margin-top: 2px;
  }}
  .pos-price-row {{
      font-size: 12px;
      color: {TV['text2']};
      margin-top: 8px;
  }}

  /* Ticker bar */
  .ticker-bar {{
      background: {TV['bg2']};
      border-bottom: 1px solid {TV['border']};
      padding: 6px 16px;
      font-size: 12px;
      display: flex;
      gap: 28px;
      overflow: hidden;
      font-variant-numeric: tabular-nums;
  }}
  .ticker-item {{ display: flex; align-items: center; gap: 6px; }}
  .ticker-sym  {{ color: {TV['text']}; font-weight: 700; }}
  .ticker-px   {{ color: {TV['text']}; font-weight: 500; }}

  /* Section header */
  .tv-section {{
      color: {TV['text2']};
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 1.8px;
      font-weight: 700;
      margin: 14px 0 8px 0;
      padding-bottom: 6px;
      border-bottom: 1px solid {TV['border']};
  }}

  /* Trade row */
  .trade-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 7px 0;
      border-bottom: 1px solid {TV['bg3']};
      font-size: 12.5px;
      font-variant-numeric: tabular-nums;
  }}
  .trade-row:last-child {{ border-bottom: none; }}

  /* Activity feed */
  .feed-wrap {{
      background: {TV['bg2']};
      border: 1px solid {TV['border']};
      border-radius: 6px;
      padding: 10px 12px;
      height: 280px;
      overflow-y: auto;
      font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
      font-size: 11px;
  }}
  .feed-line {{
      padding: 2px 0;
      border-bottom: 1px solid {TV['bg3']};
      line-height: 1.5;
  }}
  .feed-line:last-child {{ border-bottom: none; }}

  /* Status bar */
  .status-bar {{
      background: {TV['bg2']};
      border: 1px solid {TV['border']};
      border-radius: 6px;
      padding: 10px 14px;
  }}
  .stat-row {{
      display: flex;
      justify-content: space-between;
      padding: 5px 0;
      border-bottom: 1px solid {TV['bg3']};
      font-size: 12.5px;
  }}
  .stat-row:last-child {{ border-bottom: none; }}
  .stat-label {{ color: {TV['text2']}; }}
  .stat-val   {{ font-weight: 600; font-variant-numeric: tabular-nums; }}

  /* Progress bar */
  .prog-bg {{
      background: {TV['bg3']};
      border-radius: 2px;
      height: 4px;
      margin-top: 8px;
      overflow: hidden;
  }}
  .prog-fill {{ height: 4px; border-radius: 2px; }}

  /* Badge */
  .badge {{
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-size: 10px;
      font-weight: 700;
      line-height: 1.6;
  }}

  /* Hide streamlit chrome */
  #MainMenu, footer, header, [data-testid="stToolbar"] {{ visibility: hidden; height: 0; }}
  .stDeployButton {{ display: none; }}
  [data-testid="stDecoration"] {{ display: none; }}

  /* Metric overrides */
  div[data-testid="metric-container"] {{ background: transparent !important; border: none !important; padding: 0 !important; }}

  /* Scrollbar */
  ::-webkit-scrollbar {{ width: 4px; height: 4px; }}
  ::-webkit-scrollbar-track {{ background: {TV['bg']}; }}
  ::-webkit-scrollbar-thumb {{ background: {TV['bg3']}; border-radius: 2px; }}

  /* Plotly transparent bg */
  .js-plotly-plot .plotly {{ background: transparent !important; }}

  /* Table */
  [data-testid="stDataFrame"] {{ background: {TV['bg2']} !important; }}
  [data-testid="stDataFrame"] table {{ background: {TV['bg2']} !important; }}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def load(filename: str, default):
    try:
        with open(TRADER_DIR / filename) as f:
            return json.load(f)
    except Exception:
        return default

def is_paused() -> bool:
    return (TRADER_DIR / "PAUSED").exists()

def fmt_price(p: float) -> str:
    if p == 0: return "$0"
    if p < 0.0001: return f"${p:.8f}"
    if p < 0.01:   return f"${p:.6f}"
    if p < 1:      return f"${p:.4f}"
    if p < 10000:  return f"${p:,.2f}"
    return f"${p:,.0f}"

def fmt_usd(v: float, show_sign=False) -> str:
    sign = "+" if v >= 0 and show_sign else ""
    if abs(v) >= 1_000_000: return f"{sign}${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:     return f"{sign}${v:,.0f}"
    return f"{sign}${v:,.2f}"

def color(v: float) -> str:
    return TV["green"] if v >= 0 else TV["red"]

def age_str(opened: str) -> str:
    try:
        dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        s  = int((datetime.now(timezone.utc) - dt).total_seconds())
        if s < 60:   return f"{s}s"
        if s < 3600: return f"{s//60}m"
        h, m = divmod(s // 60, 60)
        return f"{h}h {m}m"
    except Exception:
        return "—"

def kpi_html(label: str, value: str, delta: str = "", delta_positive: bool = True) -> str:
    dc = TV["green"] if delta_positive else TV["red"]
    delta_html = f'<div class="kpi-delta" style="color:{dc}">{delta}</div>' if delta else ""
    return (
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'{delta_html}'
        f'</div>'
    )

def pos_card_html(symbol, market, side, pnl_pct, pnl_usd, entry, current,
                  stop_pct, target_pct, opened, leverage=1, size_usd=0) -> str:
    c        = color(pnl_usd)
    bg_tint  = TV["green_dim"] if pnl_usd >= 0 else TV["red_dim"]
    sign     = "+" if pnl_usd >= 0 else ""
    s_color  = TV["green"] if side.lower() == "long" else TV["red"]

    stop_price   = entry * (1 - stop_pct) if side.lower() == "long" else entry * (1 + stop_pct)
    target_price = entry * (1 + target_pct) if side.lower() == "long" else entry * (1 - target_pct)
    price_range  = abs(target_price - stop_price)
    if price_range > 0:
        if side.lower() == "long":
            raw_prog = (current - stop_price) / price_range * 100
        else:
            raw_prog = (stop_price - current) / price_range * 100
    else:
        raw_prog = 50
    prog = max(2, min(98, raw_prog))
    bar_c = TV["green"] if prog > 50 else TV["red"]

    lev_badge = ""
    if leverage > 1:
        lev_badge = (f'<span class="badge" style="background:{TV["blue"]}22;'
                     f'color:{TV["blue"]};margin-left:6px;">{leverage}x</span>')

    mkt_badge = (f'<span class="badge" style="background:{TV["bg3"]};'
                 f'color:{TV["text2"]};margin-left:6px;">{market}</span>')

    side_badge = (f'<span class="badge" style="background:{s_color}22;'
                  f'color:{s_color};">{side.upper()}</span>')

    sz_str = f' · {fmt_usd(size_usd)}' if size_usd >= 1 else ""
    age = age_str(opened)

    return (
        f'<div class="pos-card" style="border-left:3px solid {c};background:{bg_tint};">'
        # Row 1 — symbol + P&L %
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        f'<div>'
        f'<span class="pos-symbol">{symbol}</span>{lev_badge}{mkt_badge}'
        f'<div class="pos-meta">{side_badge}'
        f'<span style="margin-left:6px;">&#x23F1; {age}{sz_str}</span></div>'
        f'</div>'
        f'<div style="text-align:right;">'
        f'<div class="pos-pnl-pct" style="color:{c}">{sign}{pnl_pct:.2f}%</div>'
        f'<div style="color:{c};font-size:13px;font-weight:600;">{sign}{fmt_usd(pnl_usd)}</div>'
        f'</div>'
        f'</div>'
        # Row 2 — entry/current prices
        f'<div class="pos-price-row">'
        f'Entry <b style="color:{TV["text"]}">{fmt_price(entry)}</b>'
        f' &nbsp;&#x2192;&nbsp; '
        f'Now <b style="color:{TV["text"]}">{fmt_price(current)}</b>'
        f'&emsp;'
        f'<span style="color:{TV["red"]}">&#x25BC; SL {stop_pct*100:.0f}%</span>'
        f'&nbsp;&nbsp;'
        f'<span style="color:{TV["green"]}">&#x25B2; TP {target_pct*100:.0f}%</span>'
        f'</div>'
        # Progress bar
        f'<div class="prog-bg">'
        f'<div class="prog-fill" style="width:{prog:.1f}%;background:{bar_c};"></div>'
        f'</div>'
        f'</div>'
    )


def equity_chart(eq_curve: list, initial_cap: float, tf: str) -> go.Figure:
    """Full-featured TradingView-style equity chart."""
    df = pd.DataFrame(eq_curve)
    df["ts"] = pd.to_datetime(df["ts"])
    now_utc = pd.Timestamp.now(tz="UTC")
    cuts = {
        "5M":  now_utc - timedelta(minutes=5),
        "15M": now_utc - timedelta(minutes=15),
        "1H":  now_utc - timedelta(hours=1),
        "4H":  now_utc - timedelta(hours=4),
        "1D":  now_utc - timedelta(days=1),
        "ALL": pd.Timestamp.min.tz_localize("UTC"),
    }
    df_p = df[df["ts"] >= cuts[tf]]
    if len(df_p) < 2:
        df_p = df

    first = df_p["equity"].iloc[0]
    last  = df_p["equity"].iloc[-1]
    up    = last >= first
    lc    = TV["green"] if up else TV["red"]
    fc    = TV["green_dim"] if up else TV["red_dim"]

    pct_chg = (last - first) / first * 100 if first > 0 else 0

    fig = go.Figure()

    # Baseline reference line
    fig.add_hline(
        y=initial_cap,
        line_dash="dot",
        line_color=TV["border"],
        line_width=1,
    )

    # Main area fill
    fig.add_trace(go.Scatter(
        x=df_p["ts"],
        y=df_p["equity"],
        mode="lines",
        line=dict(color=lc, width=2, shape="spline", smoothing=0.3),
        fill="tozeroy",
        fillcolor=fc,
        hovertemplate=(
            "<b style='color:" + lc + "'>$%{y:,.2f}</b><br>"
            "<span style='color:" + TV["text2"] + "'>%{x|%b %d  %H:%M:%S}</span>"
            "<extra></extra>"
        ),
        name="Equity",
    ))

    # Annotation: current value callout
    fig.add_annotation(
        x=df_p["ts"].iloc[-1],
        y=last,
        text=f"  ${last:,.2f}",
        showarrow=False,
        font=dict(size=12, color=lc, family="Inter"),
        xanchor="left",
        bgcolor=TV["bg2"],
        bordercolor=lc,
        borderwidth=1,
        borderpad=4,
    )

    # % change label top-left
    fig.add_annotation(
        x=df_p["ts"].iloc[0],
        y=max(df_p["equity"]),
        text=f"  {'+' if pct_chg>=0 else ''}{pct_chg:.2f}%  ({tf})",
        showarrow=False,
        font=dict(size=11, color=lc, family="Inter"),
        xanchor="left", yanchor="top",
    )

    fig.update_layout(
        paper_bgcolor=TV["bg"],
        plot_bgcolor=TV["bg"],
        margin=dict(l=0, r=60, t=12, b=0),
        height=290,
        xaxis=dict(
            gridcolor=TV["grid"],
            color=TV["text2"],
            showgrid=True,
            zeroline=False,
            showline=False,
            tickfont=dict(size=10, family="Inter"),
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(
            gridcolor=TV["grid"],
            color=TV["text2"],
            showgrid=True,
            zeroline=False,
            showline=False,
            tickprefix="$",
            tickformat=",.0f",
            tickfont=dict(size=10, family="Inter"),
            side="right",
        ),
        showlegend=False,
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor=TV["bg2"],
            bordercolor=TV["border"],
            font=dict(color=TV["text"], size=12, family="Inter"),
        ),
    )
    return fig


def mini_sparkline(values: list, color_line: str) -> go.Figure:
    """Tiny sparkline for position card."""
    fig = go.Figure(go.Scatter(
        y=values,
        mode="lines",
        line=dict(color=color_line, width=1.5),
        fill="tozeroy",
        fillcolor=f"{color_line}22",
        hoverinfo="skip",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=40,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=False,
    )
    return fig


# ── Main render ───────────────────────────────────────────────────────────────

REFRESH_SEC = 5   # Dashboard auto-refresh interval in seconds


def render():
    # ── Inject JavaScript auto-refresh (browser-level, never breaks) ──────────
    # This is more reliable than time.sleep()+st.rerun() which can break after
    # a few cycles due to Streamlit WebSocket / session state issues.
    st.components.v1.html(
        f'<script>setTimeout(function(){{window.parent.location.reload();}},{REFRESH_SEC*1000});</script>',
        height=0,
    )

    state     = load("bot_state.json", {})
    portfolio = load("trades.json", {})
    eq_curve  = load("equity_curve.json", [])
    dex_pos   = load("dex_positions.json", {})
    paused    = is_paused()

    # ── No data state ─────────────────────────────────────────────────────────
    if not state and not portfolio:
        st.markdown(f"""
        <div style="display:flex;flex-direction:column;align-items:center;
                    justify-content:center;height:80vh;gap:16px;">
          <div style="font-size:56px;line-height:1;">📈</div>
          <div style="font-size:22px;font-weight:800;color:{TV['text']};">AI Trader</div>
          <div style="color:{TV['text2']};font-size:14px;">Waiting for bot to start...</div>
          <code style="background:{TV['bg2']};color:{TV['blue']};
                        padding:10px 20px;border-radius:6px;font-size:13px;
                        border:1px solid {TV['border']};">
            cd ~/model-pep/trader &amp;&amp; python3 main.py
          </code>
        </div>
        """, unsafe_allow_html=True)
        return  # JS refresh already injected above — will auto-reload in 5s

    # ── Core values ───────────────────────────────────────────────────────────
    equity      = state.get("equity",          portfolio.get("cash", 10000))
    cash        = state.get("cash",            portfolio.get("cash", equity))
    initial_cap = state.get("initial_capital", portfolio.get("initial_capital", 10000))
    peak_equity = state.get("peak_equity",     initial_cap)
    daily_pnl   = state.get("daily_pnl_usd",  0)
    daily_pct   = state.get("daily_pnl_pct",  0)
    total_ret   = (equity - initial_cap) / initial_cap * 100 if initial_cap else 0
    drawdown    = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
    open_pos    = portfolio.get("open_positions", {})
    closed      = portfolio.get("closed_trades", [])
    mode        = state.get("mode", "paper")
    cycle       = state.get("cycle", 0)
    cycle_ms    = state.get("last_cycle_ms", 0)
    ws_ok       = state.get("ws_connected", False)
    fut_on      = state.get("futures_enabled", False)
    last_ts     = state.get("last_cycle_ts", 0)
    age_sec     = time.time() - last_ts if last_ts else 999

    wins      = [t for t in closed if t.get("pnl_usd", 0) > 0]
    losses    = [t for t in closed if t.get("pnl_usd", 0) <= 0]
    wr        = len(wins) / len(closed) * 100 if closed else 0
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    w_pnl     = sum(t.get("pnl_usd", 0) for t in wins)
    l_pnl     = abs(sum(t.get("pnl_usd", 0) for t in losses))
    pf        = w_pnl / l_pnl if l_pnl > 0 else float("inf")
    deployed  = equity - cash

    total_open = len(open_pos) + len(dex_pos)

    # ── Top bar ───────────────────────────────────────────────────────────────
    now_str = datetime.now().strftime("%H:%M:%S")
    age_c   = TV["green"] if age_sec < 60 else TV["yellow"] if age_sec < 120 else TV["red"]

    if mode == "live":
        mode_badge = f'<span class="badge" style="background:{TV["green"]}22;color:{TV["green"]};font-size:12px;">● LIVE</span>'
    elif paused:
        mode_badge = f'<span class="badge" style="background:{TV["red"]}22;color:{TV["red"]};font-size:12px;">⏸ PAUSED</span>'
    else:
        mode_badge = f'<span class="badge" style="background:{TV["yellow"]}22;color:{TV["yellow"]};font-size:12px;">◎ PAPER</span>'

    tb1, tb2, tb3 = st.columns([4, 2, 1])
    with tb1:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;padding:4px 0;">'
            f'<span style="font-size:20px;font-weight:900;color:{TV["text"]};">AI Trader</span>'
            f'{mode_badge}'
            f'<span style="color:{age_c};font-size:12px;">Cycle #{cycle} &nbsp;·&nbsp; {age_sec:.0f}s ago</span>'
            f'<span style="color:{TV["text2"]};font-size:12px;">{cycle_ms:.0f}ms</span>'
            f'</div>',
            unsafe_allow_html=True)
    with tb2:
        # Live clock
        st.markdown(
            f'<div style="color:{TV["text2"]};font-size:12px;padding-top:6px;text-align:right;">'
            f'&#128336; {now_str} UTC &nbsp;·&nbsp; '
            f'{"📡 WS" if ws_ok else "🌐 REST"}</div>',
            unsafe_allow_html=True)
    with tb3:
        if st.button("⟳", help="Refresh now", use_container_width=True):
            st.rerun()

    # ── KPI Row ───────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    cells = [k1, k2, k3, k4, k5, k6]

    kpis = [
        kpi_html("Portfolio Value", f"${equity:,.2f}",
                 f"{'+' if total_ret>=0 else ''}{total_ret:.2f}% total",
                 total_ret >= 0),
        kpi_html("Today's P&L", fmt_usd(daily_pnl, show_sign=True),
                 f"{'+' if daily_pct>=0 else ''}{daily_pct:.2f}% today",
                 daily_pnl >= 0),
        kpi_html("Positions Open", str(total_open),
                 f"{len(open_pos)} CEX · {len(dex_pos)} DEX",
                 True),
        kpi_html("Win Rate", f"{wr:.1f}%",
                 f"{len(closed)} trades · PF {pf:.2f}" if pf != float('inf') else f"{len(closed)} trades",
                 wr >= 50),
        kpi_html("Drawdown", f"{drawdown:.2f}%",
                 f"Peak {fmt_usd(peak_equity)}",
                 drawdown < 5),
        kpi_html("Free Cash", f"${cash:,.2f}",
                 f"{cash/equity*100:.0f}% available" if equity > 0 else "—",
                 True),
    ]
    for cell, html in zip(cells, kpis):
        with cell:
            st.markdown(html, unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── Equity Chart ──────────────────────────────────────────────────────────
    st.markdown(f'<div class="tv-section">Equity Curve</div>', unsafe_allow_html=True)

    if eq_curve and len(eq_curve) > 1:
        tf_sel_col, _ = st.columns([3, 9])
        with tf_sel_col:
            tf = st.radio(
                "Timeframe", ["5M", "15M", "1H", "4H", "1D", "ALL"],
                horizontal=True, index=5,
                label_visibility="collapsed",
                key="tf_radio",
            )
        st.plotly_chart(
            equity_chart(eq_curve, initial_cap, tf),
            use_container_width=True,
            config=dict(displayModeBar=False, staticPlot=False),
        )
    else:
        st.markdown(
            f'<div style="background:{TV["bg2"]};border:1px dashed {TV["border"]};'
            f'border-radius:6px;padding:48px;text-align:center;color:{TV["text2"]};">'
            f'📊 Equity curve will appear after the first bot cycle</div>',
            unsafe_allow_html=True)

    # ── Open Positions ────────────────────────────────────────────────────────
    st.markdown(f'<div class="tv-section">Open Positions</div>', unsafe_allow_html=True)

    all_cards = []

    for pid, pos in open_pos.items():
        entry   = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        side    = pos.get("side", "long")
        qty     = pos.get("qty", 0)
        size_usd = pos.get("position_usd", qty * entry if qty and entry else 0)
        unr_pnl  = pos.get("unrealized_pnl", 0)
        unr_pct  = pos.get("unrealized_pnl_pct", 0)
        if entry > 0 and size_usd <= 0:
            size_usd = qty * entry
        pnl_usd = unr_pnl if unr_pnl != 0 else size_usd * unr_pct / 100
        is_fut  = pos.get("is_futures", False)
        stop    = pos.get("stop_loss", entry * 0.97 if side == "long" else entry * 1.03)
        tp      = pos.get("take_profit", entry * 1.06 if side == "long" else entry * 0.94)
        stop_pct   = abs(entry - stop) / entry if entry > 0 else 0.03
        target_pct = abs(tp - entry) / entry if entry > 0 else 0.06
        all_cards.append(dict(
            symbol     = pos.get("symbol", pid),
            market     = "⚡ FUT" if is_fut else "◆ CEX",
            side       = side,
            pnl_pct    = unr_pct,
            pnl_usd    = pnl_usd,
            entry      = entry,
            current    = current,
            stop_pct   = stop_pct,
            target_pct = target_pct,
            opened     = pos.get("opened_at", ""),
            leverage   = pos.get("leverage", 1),
            size_usd   = size_usd,
            sort_key   = unr_pct,
        ))

    for pair_addr, pos in dex_pos.items():
        entry   = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        pnl_pct = pos.get("current_pnl_pct", (current - entry) / entry if entry > 0 else 0) * 100
        size_usd = pos.get("size_usd", 0)
        pnl_usd  = size_usd * pnl_pct / 100
        all_cards.append(dict(
            symbol     = pos.get("symbol", pair_addr[:8]),
            market     = f'🌊 {pos.get("chain","").upper()}',
            side       = "long",
            pnl_pct    = pnl_pct,
            pnl_usd    = pnl_usd,
            entry      = entry,
            current    = current,
            stop_pct   = pos.get("stop_pct", 0.25),
            target_pct = pos.get("target_pct", 0.50),
            opened     = pos.get("opened_at", ""),
            leverage   = 1,
            size_usd   = size_usd * pos.get("remaining_fraction", 1.0),
            sort_key   = pnl_pct,
        ))

    if all_cards:
        all_cards.sort(key=lambda c: c["sort_key"], reverse=True)
        cols = st.columns(min(len(all_cards), 3))
        for i, card in enumerate(all_cards):
            card_args = {k: v for k, v in card.items() if k != "sort_key"}
            with cols[i % 3]:
                st.markdown(pos_card_html(**card_args), unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div style="background:{TV["bg2"]};border:1px dashed {TV["border"]};'
            f'border-radius:6px;padding:32px;text-align:center;color:{TV["text2"]};">'
            f'No open positions — scanning markets every 30s</div>',
            unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Signal Scanner Heatmap ────────────────────────────────────────────────
    signal_table = state.get("signal_table", [])
    if signal_table:
        st.markdown(f'<div class="tv-section">Signal Scanner — Top Market Scores</div>',
                    unsafe_allow_html=True)

        def score_cell(v: float) -> str:
            """Render an indicator score as a colored bar cell."""
            pct = int(abs(v) * 100)
            bg = TV["green_dim"] if v > 0 else TV["red_dim"]
            fg = TV["green"] if v > 0 else TV["red"]
            sign = "+" if v > 0 else ""
            return (
                f'<td style="padding:5px 8px;text-align:right;position:relative;">'
                f'<div style="position:absolute;top:0;bottom:0;'
                f'{"right" if v > 0 else "left"}:50%;width:{pct//2}%;'
                f'background:{bg};"></div>'
                f'<span style="position:relative;color:{fg};font-weight:600;'
                f'font-size:11px;">{sign}{v:.3f}</span></td>'
            )

        def signal_badge(sig: str) -> str:
            if sig == "BUY":
                return f'<span style="background:{TV["green"]}22;color:{TV["green"]};padding:1px 6px;border-radius:3px;font-weight:700;font-size:11px;">▲ BUY</span>'
            if sig == "SELL":
                return f'<span style="background:{TV["red"]}22;color:{TV["red"]};padding:1px 6px;border-radius:3px;font-weight:700;font-size:11px;">▼ SELL</span>'
            return f'<span style="color:{TV["text2"]};font-size:11px;">HOLD</span>'

        indicators = ["rsi", "macd", "bollinger", "ema_cross", "momentum", "volume"]
        ind_labels  = ["RSI", "MACD", "BB", "EMA✕", "MOM", "VOL"]

        hdr = (
            f'<tr style="border-bottom:1px solid {TV["border"]};">'
            f'<th style="padding:6px 8px;text-align:left;color:{TV["text2"]};font-size:10px;font-weight:600;letter-spacing:1px;">SYMBOL</th>'
            f'<th style="padding:6px 8px;color:{TV["text2"]};font-size:10px;">SIGNAL</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TV["text2"]};font-size:10px;">SCORE</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TV["text2"]};font-size:10px;">CONV</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TV["text2"]};font-size:10px;">PRICE</th>'
            f'<th style="padding:6px 8px;text-align:center;color:{TV["text2"]};font-size:10px;">REGIME</th>'
        )
        for lbl in ind_labels:
            hdr += f'<th style="padding:6px 8px;text-align:right;color:{TV["text2"]};font-size:10px;">{lbl}</th>'
        hdr += '</tr>'

        rows_html = ""
        for row in signal_table:
            sig   = row.get("signal", "HOLD")
            score = row.get("score", 0)
            conv  = row.get("conviction", 0)
            price = row.get("price", 0)
            comps = row.get("components", {})
            regime = row.get("regime", "—")
            trend  = row.get("trend", "neutral")
            score_c = TV["green"] if score > 0 else TV["red"] if score < 0 else TV["text2"]
            trend_icon = "↑" if trend == "up" else "↓" if trend == "down" else "→"
            reg_c = TV["blue"] if regime == "trending" else TV["yellow"] if regime == "volatile" else TV["text2"]
            row_bg = f'{TV["green"]}08' if sig == "BUY" else f'{TV["red"]}08' if sig == "SELL" else "transparent"
            comp_cells = "".join(score_cell(comps.get(ind, 0)) for ind in indicators)
            rows_html += (
                f'<tr style="border-bottom:1px solid {TV["bg3"]};background:{row_bg};">'
                f'<td style="padding:5px 8px;font-weight:700;font-size:12px;">{row.get("symbol","?")}'
                f'<span style="color:{TV["text2"]};font-size:10px;margin-left:4px;">{row.get("market","")}</span></td>'
                f'<td style="padding:5px 8px;">{signal_badge(sig)}</td>'
                f'<td style="padding:5px 8px;text-align:right;color:{score_c};font-weight:700;font-size:12px;">{score:+.4f}</td>'
                f'<td style="padding:5px 8px;text-align:right;color:{TV["text2"]};font-size:11px;">{conv:.2f}</td>'
                f'<td style="padding:5px 8px;text-align:right;font-size:11px;">{fmt_price(price)}</td>'
                f'<td style="padding:5px 8px;text-align:center;font-size:10px;">'
                f'<span style="color:{reg_c};">{regime}</span> '
                f'<span style="color:{TV["text2"]};">{trend_icon}</span></td>'
                f'{comp_cells}</tr>'
            )

        scanner_html = (
            f'<div style="background:{TV["bg2"]};border:1px solid {TV["border"]};'
            f'border-radius:6px;overflow:auto;max-height:320px;">'
            f'<table style="width:100%;border-collapse:collapse;font-family:Inter,sans-serif;">'
            f'<thead style="position:sticky;top:0;background:{TV["bg2"]};">{hdr}</thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(scanner_html, unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Market Sentiment ──────────────────────────────────────────────────────
    mkt_sentiment  = state.get("market_sentiment", "")
    btc_dom        = state.get("btc_dominance", 0)
    avg_24h        = state.get("avg_24h_change", 0)
    top_gainers    = state.get("top_gainers", [])
    top_losers     = state.get("top_losers", [])

    if mkt_sentiment or btc_dom or top_gainers:
        st.markdown(f'<div class="tv-section">Market Sentiment</div>', unsafe_allow_html=True)

        sent_color = TV["green"] if mkt_sentiment == "bullish" else TV["red"] if mkt_sentiment == "bearish" else TV["yellow"]
        sent_icon  = "▲ Bullish" if mkt_sentiment == "bullish" else "▼ Bearish" if mkt_sentiment == "bearish" else "→ Neutral"
        avg_color  = TV["green"] if avg_24h >= 0 else TV["red"]

        def _ticker_chips(items: list, positive: bool) -> str:
            chips = ""
            for item in items[:5]:
                sym = item.get("symbol", "?").upper()
                chg = item.get("change_pct", item.get("change_24h", item.get("pct_change", 0))) or 0
                fg = TV["green"] if positive else TV["red"]
                sign = "+" if chg >= 0 else ""
                chips += (
                    f'<span style="background:{fg}22;color:{fg};padding:2px 8px;'
                    f'border-radius:3px;font-size:11px;font-weight:600;margin-right:4px;">'
                    f'{sym} {sign}{chg:.1f}%</span>'
                )
            return chips or f'<span style="color:{TV["text2"]};font-size:11px;">—</span>'

        sentiment_html = (
            f'<div style="background:{TV["bg2"]};border:1px solid {TV["border"]};'
            f'border-radius:6px;padding:12px 16px;display:flex;gap:32px;align-items:center;flex-wrap:wrap;">'
            # Sentiment pill
            f'<div style="display:flex;flex-direction:column;gap:2px;min-width:100px;">'
            f'<span style="color:{TV["text2"]};font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Sentiment</span>'
            f'<span style="color:{sent_color};font-size:18px;font-weight:800;">{sent_icon}</span>'
            f'</div>'
            # BTC dominance
            f'<div style="display:flex;flex-direction:column;gap:2px;min-width:90px;">'
            f'<span style="color:{TV["text2"]};font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">BTC Dom</span>'
            f'<span style="color:{TV["text"]};font-size:18px;font-weight:800;">{btc_dom:.1f}%</span>'
            f'</div>'
            # Avg 24h
            f'<div style="display:flex;flex-direction:column;gap:2px;min-width:90px;">'
            f'<span style="color:{TV["text2"]};font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Avg 24h</span>'
            f'<span style="color:{avg_color};font-size:18px;font-weight:800;">{"+" if avg_24h>=0 else ""}{avg_24h:.2f}%</span>'
            f'</div>'
            # Gainers
            f'<div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:180px;">'
            f'<span style="color:{TV["text2"]};font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Top Gainers</span>'
            f'<div style="display:flex;flex-wrap:wrap;gap:4px;">{_ticker_chips(top_gainers, True)}</div>'
            f'</div>'
            # Losers
            f'<div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:180px;">'
            f'<span style="color:{TV["text2"]};font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Top Losers</span>'
            f'<div style="display:flex;flex-wrap:wrap;gap:4px;">{_ticker_chips(top_losers, False)}</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(sentiment_html, unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Bottom section: trades + status + feed ────────────────────────────────
    left, mid, right = st.columns([5, 4, 3])

    # ── Recent Trades ─────────────────────────────────────────────────────────
    with left:
        st.markdown(f'<div class="tv-section">Recent Closed Trades</div>', unsafe_allow_html=True)

        if closed:
            rows_html = ""
            for t in reversed(closed[-30:]):
                pnl  = t.get("pnl_usd", 0)
                pct  = t.get("pnl_pct", 0)
                sym  = t.get("symbol", "—")
                mkt  = t.get("market", "—").upper()[:3]
                rsn  = t.get("close_reason", "—")[:24]
                ts   = t.get("closed_at", "")[:16].replace("T", " ")
                c    = color(pnl)
                sign = "+" if pnl >= 0 else ""
                icon = "▲" if pnl > 0 else "▼"
                rows_html += (
                    f'<div class="trade-row">'
                    f'<span style="color:{c};font-weight:700;width:14px;">{icon}</span>'
                    f'<span style="font-weight:700;min-width:60px;">{sym}</span>'
                    f'<span style="color:{TV["text2"]};min-width:36px;font-size:11px;">{mkt}</span>'
                    f'<span style="color:{c};font-weight:700;min-width:80px;">{sign}${abs(pnl):,.2f}</span>'
                    f'<span style="color:{c};min-width:60px;">{sign}{pct:.1f}%</span>'
                    f'<span style="color:{TV["text2"]};font-size:11px;flex:1;">{rsn}</span>'
                    f'<span style="color:{TV["text2"]};font-size:11px;">{ts}</span>'
                    f'</div>'
                )

            # Summary row
            pf_str = "∞" if pf == float("inf") else f"{pf:.2f}x"
            summary = (
                f'<div style="display:flex;gap:20px;padding:8px 0 2px 0;'
                f'font-size:11.5px;border-top:1px solid {TV["border"]};margin-top:4px;">'
                f'<span style="color:{TV["green"]};">✔ {len(wins)} wins +${w_pnl:,.2f}</span>'
                f'<span style="color:{TV["red"]};">✖ {len(losses)} losses -${l_pnl:,.2f}</span>'
                f'<span style="color:{TV["text2"]};">Win rate <b style="color:{TV["text"]}">{wr:.0f}%</b></span>'
                f'<span style="color:{TV["text2"]};">PF <b style="color:{TV["text"]}">{pf_str}</b></span>'
                f'<span style="color:{TV["text2"]};">Total <b style="color:{color(total_pnl)}">{fmt_usd(total_pnl, True)}</b></span>'
                f'</div>'
            )

            st.markdown(
                f'<div style="background:{TV["bg2"]};border:1px solid {TV["border"]};'
                f'border-radius:6px;padding:8px 12px;max-height:320px;overflow-y:auto;">'
                f'{rows_html}{summary}</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="color:{TV["text2"]};padding:20px;text-align:center;'
                f'background:{TV["bg2"]};border:1px solid {TV["border"]};border-radius:6px;">'
                f'No closed trades yet</div>',
                unsafe_allow_html=True)

    # ── Bot Status ────────────────────────────────────────────────────────────
    with mid:
        st.markdown(f'<div class="tv-section">Bot Status</div>', unsafe_allow_html=True)

        def srow(label, val, val_color=None):
            vc = val_color or TV["text"]
            return (
                f'<div class="stat-row">'
                f'<span class="stat-label">{label}</span>'
                f'<span class="stat-val" style="color:{vc};">{val}</span>'
                f'</div>'
            )

        age_color  = TV["green"] if age_sec < 60 else TV["yellow"] if age_sec < 120 else TV["red"]
        cash_pct   = cash / equity * 100 if equity > 0 else 0
        deploy_pct = 100 - cash_pct
        scale_f    = state.get("scale_factor", 1.0)
        win_rate_s = state.get("win_rate_pct", wr)
        pf_s       = state.get("profit_factor", pf)
        dd_s       = state.get("max_drawdown_pct", drawdown)
        pf_str     = "∞" if pf_s == float("inf") or pf_s == 0 else f"{pf_s:.2f}x"

        status_html = (
            srow("Last Cycle",    f"{age_sec:.0f}s ago", age_color) +
            srow("Cycle Speed",   f"{cycle_ms:.0f} ms") +
            srow("WebSocket",     "Connected" if ws_ok else "REST fallback",
                 TV["green"] if ws_ok else TV["yellow"]) +
            srow("Futures",       "Enabled" if fut_on else "Disabled",
                 TV["blue"] if fut_on else TV["text2"]) +
            srow("Deployed",      f"{deploy_pct:.0f}% · {fmt_usd(deployed)}",
                 TV["yellow"] if deploy_pct > 80 else TV["text"]) +
            srow("Peak Equity",   f"${peak_equity:,.2f}") +
            srow("Max Drawdown",  f"{dd_s:.2f}%",
                 TV["red"] if dd_s > 15 else TV["yellow"] if dd_s > 7 else TV["text"]) +
            srow("Win Rate",      f"{win_rate_s:.1f}%",
                 TV["green"] if win_rate_s >= 50 else TV["red"]) +
            srow("Profit Factor", pf_str,
                 TV["green"] if pf_s >= 1.5 else TV["yellow"] if pf_s >= 1 else TV["red"]) +
            srow("Scale Factor",  f"{scale_f:.2f}x",
                 TV["blue"] if scale_f > 1 else TV["text2"]) +
            srow("Trades Total",  str(len(closed)))
        )

        grid_state = load("grid_state.json", {})
        arb_state  = load("arb_state.json", {})
        if grid_state.get("active_grids", 0) > 0:
            status_html += srow("Grid Fills",
                                f'{grid_state.get("total_fills",0)} · +${grid_state.get("total_pnl_usd",0):.2f}',
                                TV["green"])
        if arb_state.get("open_arbs", 0) > 0:
            status_html += srow("Arb Funding",
                                f'+${arb_state.get("total_funding_collected_usd",0):.2f}',
                                TV["green"])

        st.markdown(
            f'<div class="status-bar">{status_html}</div>',
            unsafe_allow_html=True)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if paused:
            if st.button("▶ Resume", use_container_width=True, type="primary"):
                try: (TRADER_DIR / "PAUSED").unlink()
                except: pass
                st.rerun()
        else:
            if st.button("⏸ Pause", use_container_width=True):
                (TRADER_DIR / "PAUSED").touch()
                st.rerun()

    # ── Activity Feed ─────────────────────────────────────────────────────────
    with right:
        st.markdown(f'<div class="tv-section">Live Activity</div>', unsafe_allow_html=True)

        log_path = TRADER_DIR / "trader.log"
        feed_html = ""
        if log_path.exists():
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                keywords = [
                    "DEX BUY", "DEX CLOSE", "EXECUTED", "CLOSED", "FUTURES",
                    "SCALP", "GRID", "ARB", "PARTIAL", "PYRAMID",
                    "CIRCUIT", "Stop loss", "Take profit", "MILESTONE",
                ]
                filtered = [l.rstrip() for l in lines if any(k in l for k in keywords)][-50:]

                def feed_color(line):
                    if any(k in line for k in ["DEX BUY", "EXECUTED BUY", "FUTURES LONG", "PYRAMID"]):
                        return TV["green"]
                    if any(k in line for k in ["Take profit", "CLOSE", "PARTIAL", "MILESTONE"]):
                        return TV["blue"]
                    if any(k in line for k in ["Stop loss", "CIRCUIT", "WARNING", "LIQUIDATION"]):
                        return TV["red"]
                    if any(k in line for k in ["SCALP", "GRID", "ARB"]):
                        return TV["yellow"]
                    return TV["text2"]

                for line in reversed(filtered):
                    c   = feed_color(line)
                    esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    # Extract just the message (after the log prefix)
                    parts = esc.split(" - ", 1)
                    msg = parts[-1] if len(parts) > 1 else esc
                    feed_html += (
                        f'<div class="feed-line" style="color:{c};">{msg}</div>'
                    )
            except Exception:
                feed_html = f'<div style="color:{TV["text2"]}">Log unavailable</div>'
        else:
            feed_html = f'<div style="color:{TV["text2"]}">Waiting for log...</div>'

        st.markdown(
            f'<div class="feed-wrap">{feed_html}</div>',
            unsafe_allow_html=True)

    # ── Footer ────────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="text-align:right;color:{TV["text2"]};font-size:10px;'
        f'margin-top:10px;padding-top:6px;border-top:1px solid {TV["border"]};">'
        f'AI Trader v3.0 &nbsp;·&nbsp; {now_str} &nbsp;·&nbsp; auto-refresh {REFRESH_SEC}s'
        f'</div>',
        unsafe_allow_html=True)
    # JS reload already injected at the top of render() — no sleep needed


render()
