"""
AI Trader — Professional Day-Trading Dashboard v4
==================================================
Run: cd trader && streamlit run dashboard.py
"""
import json
import time
import math
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

# ── Color palette ─────────────────────────────────────────────────────────────
TV = dict(
    bg          = "#0d1117",
    bg2         = "#161b22",
    bg3         = "#21262d",
    border      = "#30363d",
    text        = "#e6edf3",
    text2       = "#8b949e",
    green       = "#3fb950",
    green_dim   = "rgba(63,185,80,0.12)",
    red         = "#f85149",
    red_dim     = "rgba(248,81,73,0.12)",
    blue        = "#58a6ff",
    blue_dim    = "rgba(88,166,255,0.12)",
    yellow      = "#d29922",
    yellow_dim  = "rgba(210,153,34,0.12)",
    purple      = "#bc8cff",
    orange      = "#f0883e",
    grid        = "rgba(48,54,61,0.6)",
    live_green  = "#1f6feb",
)

st.set_page_config(
    page_title="AI Trader",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700;0,800;0,900;1,400&display=swap');

  html, body, .stApp, section.main, [data-testid="stAppViewContainer"],
  [data-testid="stMain"], div[class*="css"] {{
      background-color: {TV['bg']} !important;
      font-family: 'Inter', -apple-system, sans-serif !important;
      color: {TV['text']} !important;
  }}

  .block-container {{ padding: 0.4rem 0.8rem 1rem 0.8rem !important; max-width: 100% !important; }}
  [data-testid="column"] {{ padding: 0 3px !important; }}

  /* ── KPI cards ── */
  .kpi {{ background:{TV['bg2']};border:1px solid {TV['border']};border-radius:8px;padding:12px 14px; }}
  .kpi-label {{ color:{TV['text2']};font-size:10px;text-transform:uppercase;letter-spacing:1.3px;font-weight:600;margin-bottom:3px; }}
  .kpi-value {{ color:{TV['text']};font-size:22px;font-weight:800;line-height:1.15;font-variant-numeric:tabular-nums; }}
  .kpi-delta {{ font-size:11.5px;font-weight:600;margin-top:2px; }}

  /* ── Section headers ── */
  .sec-hdr {{
      color:{TV['text2']};font-size:9.5px;text-transform:uppercase;letter-spacing:2px;
      font-weight:700;margin:14px 0 8px 0;padding-bottom:5px;
      border-bottom:1px solid {TV['border']};
  }}

  /* ── Position cards ── */
  .pos-card {{
      background:{TV['bg2']};border:1px solid {TV['border']};border-radius:8px;
      padding:12px 14px;margin-bottom:6px;
  }}
  .pos-sym   {{ font-size:17px;font-weight:800;color:{TV['text']}; }}
  .pos-pct   {{ font-size:24px;font-weight:900;line-height:1;font-variant-numeric:tabular-nums; }}
  .pos-meta  {{ font-size:11px;color:{TV['text2']};margin-top:1px; }}
  .pos-row   {{ font-size:11.5px;color:{TV['text2']};margin-top:7px; }}

  /* Progress bar */
  .prog-bg   {{ background:{TV['bg3']};border-radius:2px;height:3px;margin-top:8px;overflow:hidden; }}
  .prog-fill {{ height:3px;border-radius:2px; }}

  /* ── Badges ── */
  .badge {{
      display:inline-block;padding:1px 7px;border-radius:4px;
      font-size:10px;font-weight:700;line-height:1.7;letter-spacing:0.5px;
  }}

  /* ── Trade rows ── */
  .trade-row {{
      display:flex;justify-content:space-between;align-items:center;
      padding:6px 0;border-bottom:1px solid {TV['bg3']};font-size:12px;
      font-variant-numeric:tabular-nums;
  }}
  .trade-row:last-child {{ border-bottom:none; }}

  /* ── Stat rows in sidebar ── */
  .stat-row {{
      display:flex;justify-content:space-between;padding:5px 0;
      border-bottom:1px solid {TV['bg3']};font-size:12px;
  }}
  .stat-row:last-child {{ border-bottom:none; }}
  .stat-label {{ color:{TV['text2']}; }}
  .stat-val   {{ font-weight:600;font-variant-numeric:tabular-nums; }}

  /* ── Control panel ── */
  .ctrl-panel {{
      background:{TV['bg2']};border:1px solid {TV['border']};
      border-radius:8px;padding:12px 14px;margin-bottom:10px;
  }}

  /* ── Wallet card ── */
  .wallet-card {{
      background:{TV['bg3']};border-radius:6px;padding:10px 12px;margin-top:8px;
  }}

  /* ── Feed ── */
  .feed-wrap {{
      background:{TV['bg2']};border:1px solid {TV['border']};border-radius:8px;
      padding:10px 12px;height:240px;overflow-y:auto;
      font-family:'JetBrains Mono','Fira Code','Courier New',monospace;font-size:10.5px;
  }}
  .feed-line {{ padding:2px 0;border-bottom:1px solid {TV['bg3']};line-height:1.5; }}
  .feed-line:last-child {{ border-bottom:none; }}

  /* ── Signal heatmap ── */
  .sig-row {{
      display:flex;align-items:center;justify-content:space-between;
      padding:4px 8px;border-radius:4px;margin-bottom:2px;font-size:11.5px;
  }}

  /* ── Top bar ── */
  .topbar {{
      display:flex;align-items:center;gap:14px;
      padding:6px 4px 4px 4px;border-bottom:1px solid {TV['border']};
      margin-bottom:8px;
  }}

  /* ── Mode indicator ── */
  .mode-live   {{ background:{TV['live_green']}22;color:{TV['blue']};border:1px solid {TV['blue']}55; }}
  .mode-paper  {{ background:{TV['yellow']}22;color:{TV['yellow']};border:1px solid {TV['yellow']}55; }}
  .mode-paused {{ background:{TV['red']}22;color:{TV['red']};border:1px solid {TV['red']}55; }}

  /* ── Streamlit widget overrides ── */
  div[data-testid="stToggle"] label {{ color:{TV['text']} !important;font-size:13px !important; }}
  .stButton > button {{
      background:{TV['bg3']} !important;border:1px solid {TV['border']} !important;
      color:{TV['text']} !important;border-radius:6px !important;
      font-size:12px !important;font-weight:600 !important;padding:4px 12px !important;
  }}
  .stButton > button:hover {{
      border-color:{TV['blue']} !important;color:{TV['blue']} !important;
  }}
  .stButton [data-testid="baseButton-primary"] {{
      background:{TV['live_green']} !important;color:white !important;
      border-color:{TV['live_green']} !important;
  }}

  /* Close position button — compact red */
  .close-btn > button {{
      background:{TV['red']}18 !important;border:1px solid {TV['red']}55 !important;
      color:{TV['red']} !important;border-radius:5px !important;
      font-size:11px !important;padding:2px 10px !important;font-weight:700 !important;
  }}
  .close-btn > button:hover {{ background:{TV['red']}35 !important; }}

  #MainMenu, footer, header, [data-testid="stToolbar"] {{ visibility:hidden;height:0; }}
  .stDeployButton, [data-testid="stDecoration"] {{ display:none; }}
  div[data-testid="metric-container"] {{ background:transparent !important;border:none !important;padding:0 !important; }}
  ::-webkit-scrollbar {{ width:4px;height:4px; }}
  ::-webkit-scrollbar-track {{ background:{TV['bg']}; }}
  ::-webkit-scrollbar-thumb {{ background:{TV['bg3']};border-radius:2px; }}
  .js-plotly-plot .plotly {{ background:transparent !important; }}
  [data-testid="stDataFrame"] {{ background:{TV['bg2']} !important; }}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def load(filename: str, default):
    try:
        with open(TRADER_DIR / filename) as f:
            return json.load(f)
    except Exception:
        return default

PAPER_INITIAL_CAPITAL = 100_000.0  # Fresh paper account balance

def _reset_paper_now():
    """
    Immediately wipe all paper trading state from disk.
    Works even if the bot is not running.
    Writes a clean trades.json with $100k so the dashboard shows it instantly.
    """
    import json, os
    from datetime import datetime, timezone

    # Files to delete outright
    for fname in ("dex_positions.json", "equity_curve.json", "bot_state.json"):
        try:
            (TRADER_DIR / fname).unlink()
        except FileNotFoundError:
            pass

    # Write a clean portfolio (trades.json) so dashboard shows $100k immediately
    clean_portfolio = {
        "cash":             PAPER_INITIAL_CAPITAL,
        "initial_capital":  PAPER_INITIAL_CAPITAL,
        "peak_equity":      PAPER_INITIAL_CAPITAL,
        "open_positions":   {},
        "closed_trades":    [],
        "saved_at":         datetime.now(timezone.utc).isoformat(),
    }
    tmp = TRADER_DIR / "trades.json.tmp"
    with open(tmp, "w") as f:
        json.dump(clean_portfolio, f, indent=2)
    tmp.replace(TRADER_DIR / "trades.json")

    # Also tell the bot to reset (in case it IS running)
    settings = load("settings.json", {"live_mode": False})
    settings["reset_paper"] = True
    save_settings(settings)

def save_settings(s: dict):
    tmp = TRADER_DIR / "settings.json.tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    tmp.replace(TRADER_DIR / "settings.json")

def queue_command(cmd: dict):
    """Append a command to commands.json for the bot to execute next cycle."""
    cmds = load("commands.json", {"pending": []})
    cmds["pending"].append(cmd)
    tmp = TRADER_DIR / "commands.json.tmp"
    with open(tmp, "w") as f:
        json.dump(cmds, f, indent=2)
    tmp.replace(TRADER_DIR / "commands.json")

def is_paused() -> bool:
    return (TRADER_DIR / "PAUSED").exists()

def fmt_price(p: float) -> str:
    if p == 0:     return "$0"
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
        return f"{h}h{m:02d}m"
    except Exception:
        return "—"

def kpi_html(label, value, delta="", pos=True) -> str:
    dc = TV["green"] if pos else TV["red"]
    dh = f'<div class="kpi-delta" style="color:{dc}">{delta}</div>' if delta else ""
    return (f'<div class="kpi">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'{dh}</div>')

def badge(text, style="") -> str:
    return f'<span class="badge" style="{style}">{text}</span>'


# ── Charts ────────────────────────────────────────────────────────────────────

def equity_chart(eq_curve: list, initial_cap: float, tf: str) -> go.Figure:
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
    fig.add_hline(y=initial_cap, line_dash="dot", line_color=TV["border"], line_width=1)
    fig.add_trace(go.Scatter(
        x=df_p["ts"], y=df_p["equity"],
        mode="lines",
        line=dict(color=lc, width=2, shape="spline", smoothing=0.3),
        fill="tozeroy", fillcolor=fc,
        hovertemplate=(
            f"<b style='color:{lc}'>$%{{y:,.2f}}</b><br>"
            f"<span style='color:{TV['text2']}'>%{{x|%b %d  %H:%M:%S}}</span>"
            "<extra></extra>"
        ),
        name="Equity",
    ))
    fig.add_annotation(
        x=df_p["ts"].iloc[-1], y=last,
        text=f"  ${last:,.2f}",
        showarrow=False, font=dict(size=12, color=lc, family="Inter"),
        xanchor="left", bgcolor=TV["bg2"], bordercolor=lc, borderwidth=1, borderpad=4,
    )
    fig.add_annotation(
        x=df_p["ts"].iloc[0], y=max(df_p["equity"]),
        text=f"  {'+' if pct_chg>=0 else ''}{pct_chg:.2f}%  ({tf})",
        showarrow=False, font=dict(size=11, color=lc, family="Inter"),
        xanchor="left", yanchor="top",
    )
    fig.update_layout(
        paper_bgcolor=TV["bg"], plot_bgcolor=TV["bg"],
        margin=dict(l=0, r=60, t=10, b=0), height=260,
        xaxis=dict(gridcolor=TV["grid"], color=TV["text2"], showgrid=True,
                   zeroline=False, showline=False, tickfont=dict(size=10, family="Inter"),
                   rangeslider=dict(visible=False)),
        yaxis=dict(gridcolor=TV["grid"], color=TV["text2"], showgrid=True,
                   zeroline=False, showline=False, tickprefix="$", tickformat=",.0f",
                   tickfont=dict(size=10, family="Inter"), side="right"),
        showlegend=False, hovermode="x unified",
        hoverlabel=dict(bgcolor=TV["bg2"], bordercolor=TV["border"],
                        font=dict(color=TV["text"], size=12, family="Inter")),
    )
    return fig


# ── Main render ───────────────────────────────────────────────────────────────

REFRESH_SEC = 1


def render():
    # ── Load all data ─────────────────────────────────────────────────────────
    state     = load("bot_state.json", {})
    portfolio = load("trades.json", {})
    eq_curve  = load("equity_curve.json", [])
    dex_pos   = load("dex_positions.json", {})
    settings  = load("settings.json", {"live_mode": False, "reset_paper": False})
    paused    = is_paused()

    # ── No data state ─────────────────────────────────────────────────────────
    if not state and not portfolio:
        st.markdown(f"""
        <div style="display:flex;flex-direction:column;align-items:center;
                    justify-content:center;height:80vh;gap:16px;">
          <div style="font-size:52px">⚡</div>
          <div style="font-size:24px;font-weight:900;color:{TV['text']}">AI Trader</div>
          <div style="color:{TV['text2']};font-size:14px">Waiting for bot to start...</div>
          <code style="background:{TV['bg2']};color:{TV['blue']};padding:10px 20px;
                       border-radius:6px;font-size:13px;border:1px solid {TV['border']}">
            cd ~/model-pep/trader &amp;&amp; python3 main.py
          </code>
        </div>""", unsafe_allow_html=True)
        time.sleep(5); st.rerun()

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
    all_closed  = portfolio.get("closed_trades", [])
    recent_trades = state.get("recent_trades", all_closed)
    mode        = state.get("mode", "paper")
    cycle       = state.get("cycle", 0)
    cycle_ms    = state.get("last_cycle_ms", 0)
    ws_ok       = state.get("ws_connected", False)
    fut_on      = state.get("futures_enabled", False)
    last_ts     = state.get("last_cycle_ts", 0)
    age_sec     = time.time() - last_ts if last_ts else 999
    scale_f     = state.get("scale_factor", 1.0)

    # Solana wallet info from bot_state
    wallet_ok      = state.get("wallet_connected", False)
    wallet_addr    = state.get("wallet_address", "")
    wallet_sol     = state.get("wallet_sol",     0.0)
    wallet_usdc    = state.get("wallet_usdc",    0.0)
    wallet_sol_usd = state.get("wallet_sol_usd", 0.0)
    wallet_addr_short = f"{wallet_addr[:4]}...{wallet_addr[-4:]}" if len(wallet_addr) > 8 else wallet_addr

    wins      = [t for t in all_closed if t.get("pnl_usd", 0) > 0]
    losses    = [t for t in all_closed if t.get("pnl_usd", 0) <= 0]
    wr        = len(wins) / len(all_closed) * 100 if all_closed else 0
    w_pnl     = sum(t.get("pnl_usd", 0) for t in wins)
    l_pnl     = abs(sum(t.get("pnl_usd", 0) for t in losses))
    total_pnl = sum(t.get("pnl_usd", 0) for t in all_closed)
    pf        = w_pnl / l_pnl if l_pnl > 0 else float("inf")
    pf_str    = "∞" if pf == float("inf") else f"{pf:.2f}x"
    deployed  = equity - cash
    total_open = len(open_pos) + len(dex_pos)

    now_str = datetime.now().strftime("%H:%M:%S")
    age_c   = TV["green"] if age_sec < 60 else TV["yellow"] if age_sec < 120 else TV["red"]

    # Always live — no paper mode
    if paused:
        mode_cls = "mode-paused"
        mode_txt = "⏸ PAUSED"
    else:
        mode_cls = "mode-live"
        mode_txt = "● LIVE"

    # ── Top bar ───────────────────────────────────────────────────────────────
    tb1, tb2, tb3 = st.columns([5, 3, 1])
    with tb1:
        st.markdown(
            f'<div class="topbar">'
            f'<span style="font-size:18px;font-weight:900;color:{TV["text"]};letter-spacing:-0.5px;">⚡ AI TRADER</span>'
            f'<span class="badge {mode_cls}" style="font-size:11px;">{mode_txt}</span>'
            f'<span style="color:{age_c};font-size:11.5px;">#{cycle} · {age_sec:.0f}s ago · {cycle_ms:.0f}ms</span>'
            f'<span style="color:{TV["text2"]};font-size:11px;">{"📡 WS" if ws_ok else "🌐 REST"}</span>'
            f'</div>', unsafe_allow_html=True)
    with tb2:
        if wallet_ok:
            sol_disp = f"{wallet_sol:.4f} SOL (${wallet_sol_usd:,.2f})"
        else:
            sol_disp = "○ No Wallet"
        st.markdown(
            f'<div style="padding:10px 4px 4px 4px;text-align:right;'
            f'color:{TV["text2"]};font-size:11.5px;">'
            f'🕐 {now_str} UTC &nbsp;·&nbsp; '
            f'<span style="color:{TV["green"] if wallet_ok else TV["red"]};">'
            f'{"● " + wallet_addr_short + " · " + sol_disp if wallet_ok else sol_disp}'
            f'</span></div>', unsafe_allow_html=True)
    with tb3:
        if st.button("⟳", help="Refresh", use_container_width=True):
            st.rerun()

    # ── KPI strip ─────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    # True equity = bot-tracked cash + live DEX position values.
    # wallet_sol_usd alone is wrong when tokens are held (it misses their value).
    display_equity = equity
    wallet_label   = "Portfolio"
    wallet_sub     = (f"{wallet_sol:.4f} SOL + positions"
                      if wallet_ok else f"${equity:,.2f} tracked")
    kpis = [
        (k1, kpi_html(wallet_label,   f"${display_equity:,.2f}", wallet_sub, True)),
        (k2, kpi_html("Today P&L",    fmt_usd(daily_pnl, True),
                      f"{'+' if daily_pct>=0 else ''}{daily_pct:.2f}%", daily_pnl >= 0)),
        (k3, kpi_html("Win Rate",     f"{wr:.1f}%",
                      f"{len(all_closed)} trades", wr >= 50)),
        (k4, kpi_html("Profit Factor",pf_str,
                      f"+${w_pnl:,.0f} / -${l_pnl:,.0f}", pf >= 1)),
        (k5, kpi_html("Drawdown",     f"{drawdown:.2f}%",
                      f"Peak ${peak_equity:,.0f}", drawdown < 5)),
        (k6, kpi_html("Deployed",     fmt_usd(deployed),
                      f"{deployed/equity*100:.0f}% of equity" if equity > 0 else "—", True)),
        (k7, kpi_html("Free Cash",    f"${cash:,.2f}",
                      f"{cash/equity*100:.0f}% available" if equity > 0 else "—", True)),
    ]
    for col, html in kpis:
        with col:
            st.markdown(html, unsafe_allow_html=True)

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # ── 2-column body: main (left) + control panel (right) ───────────────────
    main_col, ctrl_col = st.columns([7, 3])

    # ─────────────────────────────────────────────────────────────────────────
    # RIGHT: CONTROL PANEL
    # ─────────────────────────────────────────────────────────────────────────
    with ctrl_col:

        # ── PHANTOM WALLET ────────────────────────────────────────────────────
        st.markdown(f'<div class="sec-hdr">Phantom Wallet</div>', unsafe_allow_html=True)
        if wallet_ok:
            sol_color = TV["green"] if wallet_sol >= 0.05 else TV["yellow"]
            # Build USDC cell separately to avoid backslash-in-f-string syntax error
            usdc_cell = (
                f'<div><div style="color:{TV["text2"]};font-size:9.5px;text-transform:uppercase;'
                f'letter-spacing:1px;">USDC</div>'
                f'<div style="color:{TV["text"]};font-size:15px;font-weight:800;">'
                f'${wallet_usdc:,.2f}</div></div>'
            ) if wallet_usdc > 0.01 else ""
            st.markdown(
                f'<div class="wallet-card">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{TV["green"]};font-size:12px;font-weight:700;">● LIVE</span>'
                f'<span style="color:{TV["text2"]};font-size:10.5px;">{wallet_addr_short}</span>'
                f'</div>'
                f'<div style="display:flex;gap:12px;margin-top:8px;">'
                f'<div><div style="color:{TV["text2"]};font-size:9.5px;text-transform:uppercase;'
                f'letter-spacing:1px;">SOL</div>'
                f'<div style="color:{sol_color};font-size:15px;font-weight:800;">'
                f'{wallet_sol:.4f}</div></div>'
                f'<div><div style="color:{TV["text2"]};font-size:9.5px;text-transform:uppercase;'
                f'letter-spacing:1px;">USD VALUE</div>'
                f'<div style="color:{TV["text"]};font-size:15px;font-weight:800;">'
                f'${wallet_sol_usd:,.2f}</div></div>'
                f'{usdc_cell}'
                f'</div>'
                f'<div style="color:{TV["text2"]};font-size:10px;margin-top:6px;">'
                f'Solana Mainnet · Jupiter + Jito MEV</div>'
                f'</div>',
                unsafe_allow_html=True)
            if wallet_sol < 0.01:
                st.markdown(
                    f'<div style="background:{TV["red_dim"]};border:1px solid {TV["red"]}44;'
                    f'border-radius:6px;padding:8px 10px;font-size:11.5px;color:{TV["red"]};">'
                    f'⚠ Low SOL — need ≥0.01 SOL for transaction fees</div>',
                    unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="wallet-card">'
                f'<div style="color:{TV["red"]};font-size:12px;font-weight:700;">○ Not Connected</div>'
                f'<div style="color:{TV["text2"]};font-size:10.5px;margin-top:4px;">'
                f'Set PHANTOM_PRIVATE_KEY in .env</div>'
                f'</div>',
                unsafe_allow_html=True)

        # ── CONTROLS ──────────────────────────────────────────────────────────
        st.markdown(f'<div class="sec-hdr">Controls</div>', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1:
            if paused:
                if st.button("▶ Resume", use_container_width=True, type="primary"):
                    try: (TRADER_DIR / "PAUSED").unlink()
                    except: pass
                    st.rerun()
            else:
                if st.button("⏸ Pause", use_container_width=True):
                    (TRADER_DIR / "PAUSED").touch()
                    st.rerun()
        with c2:
            if st.button("⟳ Refresh", use_container_width=True, help="Force data refresh"):
                st.rerun()

        # ── BOT STATUS ────────────────────────────────────────────────────────
        st.markdown(f'<div class="sec-hdr">Bot Status</div>', unsafe_allow_html=True)

        win_rate_s = state.get("win_rate_pct", wr)
        pf_s       = state.get("profit_factor", pf)
        dd_s       = state.get("max_drawdown_pct", drawdown)
        pf_str_s   = "∞" if pf_s in (0, float("inf")) else f"{pf_s:.2f}x"
        cash_pct   = cash / equity * 100 if equity > 0 else 0

        def srow(label, val, vc=None):
            c = vc or TV["text"]
            return (f'<div class="stat-row">'
                    f'<span class="stat-label">{label}</span>'
                    f'<span class="stat-val" style="color:{c};">{val}</span>'
                    f'</div>')

        grid_state = load("grid_state.json", {})
        arb_state  = load("arb_state.json", {})

        status_html = (
            srow("Last Cycle",    f"{age_sec:.0f}s ago", age_c) +
            srow("Speed",         f"{cycle_ms:.0f} ms") +
            srow("WebSocket",     "Connected" if ws_ok else "REST",
                 TV["green"] if ws_ok else TV["yellow"]) +
            srow("Futures",       "On" if fut_on else "Off",
                 TV["blue"] if fut_on else TV["text2"]) +
            srow("Scale Factor",  f"{scale_f:.2f}x",
                 TV["blue"] if scale_f > 1 else TV["text2"]) +
            srow("Win Rate",      f"{win_rate_s:.1f}%",
                 TV["green"] if win_rate_s >= 50 else TV["red"]) +
            srow("Prof. Factor",  pf_str_s,
                 TV["green"] if pf_s >= 1.5 else TV["yellow"] if pf_s >= 1 else TV["red"]) +
            srow("Max Drawdown",  f"{dd_s:.1f}%",
                 TV["green"] if dd_s < 5 else TV["yellow"] if dd_s < 15 else TV["red"]) +
            srow("Cash %",        f"{cash_pct:.0f}%",
                 TV["text2"])
        )
        if grid_state.get("active_grids", 0) > 0:
            status_html += srow(
                "Grids",
                f'{grid_state.get("active_grids",0)} grids · +${grid_state.get("total_pnl_usd",0):.2f}',
                TV["green"])
        if arb_state.get("open_arbs", 0) > 0:
            status_html += srow(
                "Arb Funding",
                f'+${arb_state.get("total_funding_collected_usd",0):.2f}',
                TV["green"])

        st.markdown(
            f'<div style="background:{TV["bg2"]};border:1px solid {TV["border"]};'
            f'border-radius:8px;padding:8px 12px;">{status_html}</div>',
            unsafe_allow_html=True)

        # ── LIVE FEED ─────────────────────────────────────────────────────────
        st.markdown(f'<div class="sec-hdr">Live Activity</div>', unsafe_allow_html=True)

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
                    "Mode switched", "PAPER ACCOUNT RESET",
                ]
                filtered = [l.rstrip() for l in lines if any(k in l for k in keywords)][-40:]
                def fc(line):
                    if any(k in line for k in ["DEX BUY","EXECUTED BUY","FUTURES LONG","PYRAMID"]):
                        return TV["green"]
                    if any(k in line for k in ["Take profit","CLOSE","PARTIAL","MILESTONE"]):
                        return TV["blue"]
                    if any(k in line for k in ["Stop loss","CIRCUIT","WARNING","LIQUIDATION"]):
                        return TV["red"]
                    if any(k in line for k in ["Mode switched","RESET"]): return TV["yellow"]
                    if any(k in line for k in ["SCALP","GRID","ARB"]): return TV["purple"]
                    return TV["text2"]
                for line in reversed(filtered):
                    c_   = fc(line)
                    esc  = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    parts = esc.split(" - ", 1)
                    msg  = parts[-1] if len(parts) > 1 else esc
                    feed_html += f'<div class="feed-line" style="color:{c_};">{msg}</div>'
            except Exception:
                feed_html = f'<div style="color:{TV["text2"]}">Log unavailable</div>'
        else:
            feed_html = f'<div style="color:{TV["text2"]}">Waiting for log...</div>'

        st.markdown(f'<div class="feed-wrap">{feed_html}</div>', unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    # LEFT: MAIN CONTENT
    # ─────────────────────────────────────────────────────────────────────────
    with main_col:

        # ── EQUITY CHART ──────────────────────────────────────────────────────
        st.markdown(f'<div class="sec-hdr">Equity Curve</div>', unsafe_allow_html=True)

        if eq_curve and len(eq_curve) > 1:
            tf_col, _ = st.columns([4, 8])
            with tf_col:
                tf = st.radio(
                    "TF", ["5M","15M","1H","4H","1D","ALL"],
                    horizontal=True, index=5, label_visibility="collapsed", key="tf")
            st.plotly_chart(
                equity_chart(eq_curve, initial_cap, tf),
                use_container_width=True,
                config=dict(displayModeBar=False))
        else:
            st.markdown(
                f'<div style="background:{TV["bg2"]};border:1px dashed {TV["border"]};'
                f'border-radius:8px;padding:40px;text-align:center;color:{TV["text2"]};">'
                f'📊 Equity curve appears after the first bot cycle</div>',
                unsafe_allow_html=True)

        # ── OPEN POSITIONS ────────────────────────────────────────────────────
        st.markdown(
            f'<div class="sec-hdr">Open Positions '
            f'<span style="color:{TV["text"]};font-variant-numeric:tabular-nums;">'
            f'({total_open})</span></div>',
            unsafe_allow_html=True)

        # Build unified list of all positions
        all_positions = []
        for pid, pos in open_pos.items():
            entry   = pos.get("entry_price", 0)
            current = pos.get("current_price", entry)
            side    = pos.get("side", "long")
            qty     = pos.get("qty", 0)
            size_usd = pos.get("qty", 0) * entry if entry > 0 else 0
            unr_pnl  = pos.get("unrealized_pnl", 0)
            unr_pct  = pos.get("unrealized_pnl_pct", 0)
            pnl_usd  = unr_pnl if unr_pnl != 0 else size_usd * unr_pct / 100
            is_fut   = pos.get("is_futures", False)
            stop     = pos.get("stop_loss",  entry * (0.97 if side=="long" else 1.03))
            tp       = pos.get("take_profit",entry * (1.06 if side=="long" else 0.94))
            stop_pct = abs(entry - stop) / entry if entry > 0 else 0.03
            tp_pct   = abs(tp - entry) / entry if entry > 0 else 0.06
            all_positions.append(dict(
                id=pid, symbol=pos.get("symbol", pid),
                market="⚡ FUT" if is_fut else "◆ CEX",
                mkt_key="cex",
                side=side, pnl_pct=unr_pct, pnl_usd=pnl_usd,
                entry=entry, current=current, stop_pct=stop_pct, tp_pct=tp_pct,
                opened=pos.get("opened_at",""), leverage=pos.get("leverage",1),
                size_usd=size_usd, sort_key=unr_pct,
            ))
        for addr, pos in dex_pos.items():
            entry   = pos.get("entry_price", 0)
            current = pos.get("current_price", entry)
            raw_pct = pos.get("current_pnl_pct", (current-entry)/entry if entry > 0 else 0)
            pnl_pct = raw_pct * 100
            size_usd = pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
            pnl_usd  = size_usd * raw_pct
            chain    = pos.get("chain", "sol").upper()
            all_positions.append(dict(
                id=addr, symbol=pos.get("symbol", addr[:8]),
                market=f"🌊 {chain}",
                mkt_key="dex",
                side="long", pnl_pct=pnl_pct, pnl_usd=pnl_usd,
                entry=entry, current=current,
                stop_pct=pos.get("stop_pct", 0.25), tp_pct=pos.get("target_pct", 0.50),
                opened=pos.get("opened_at",""), leverage=1,
                size_usd=size_usd, sort_key=pnl_pct,
            ))

        if all_positions:
            all_positions.sort(key=lambda p: p["sort_key"], reverse=True)
            # Render 3-wide grid of cards
            cols = [st.columns(3)] * (math.ceil(len(all_positions) / 3))
            flat_cols = []
            for row_cols in cols:
                flat_cols.extend(row_cols)
            for i, pos in enumerate(all_positions):
                with flat_cols[i]:
                    c_  = color(pos["pnl_usd"])
                    bg_ = TV["green_dim"] if pos["pnl_usd"] >= 0 else TV["red_dim"]
                    sign = "+" if pos["pnl_usd"] >= 0 else ""
                    s_c  = TV["green"] if pos["side"] == "long" else TV["red"]

                    # Progress bar: where is current price between SL and TP?
                    stop_p   = pos["entry"] * (1 - pos["stop_pct"]) if pos["side"]=="long" else pos["entry"] * (1 + pos["stop_pct"])
                    tp_p     = pos["entry"] * (1 + pos["tp_pct"])   if pos["side"]=="long" else pos["entry"] * (1 - pos["tp_pct"])
                    pr       = abs(tp_p - stop_p)
                    raw_prog = (pos["current"] - stop_p) / pr * 100 if pr > 0 else 50
                    prog     = max(2, min(98, raw_prog))
                    bar_c    = TV["green"] if prog > 50 else TV["red"]

                    lev_b = (f'<span class="badge" style="background:{TV["blue"]}22;'
                             f'color:{TV["blue"]};margin-left:5px;">{pos["leverage"]}x</span>'
                             if pos["leverage"] > 1 else "")
                    mkt_b = (f'<span class="badge" style="background:{TV["bg3"]};'
                             f'color:{TV["text2"]};margin-left:5px;">{pos["market"]}</span>')
                    side_b = (f'<span class="badge" style="background:{s_c}22;color:{s_c};">'
                              f'{pos["side"].upper()}</span>')

                    sz_str = f' · {fmt_usd(pos["size_usd"])}' if pos["size_usd"] >= 1 else ""

                    st.markdown(
                        f'<div class="pos-card" style="border-left:3px solid {c_};background:{bg_};">'
                        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
                        f'<div>'
                        f'<span class="pos-sym">{pos["symbol"]}</span>{lev_b}{mkt_b}'
                        f'<div class="pos-meta">{side_b}'
                        f'<span style="margin-left:5px;">⏱ {age_str(pos["opened"])}{sz_str}</span>'
                        f'</div></div>'
                        f'<div style="text-align:right;">'
                        f'<div class="pos-pct" style="color:{c_}">{sign}{pos["pnl_pct"]:.2f}%</div>'
                        f'<div style="color:{c_};font-size:12px;font-weight:600;">{sign}{fmt_usd(pos["pnl_usd"])}</div>'
                        f'</div></div>'
                        f'<div class="pos-row">'
                        f'Entry <b style="color:{TV["text"]}">{fmt_price(pos["entry"])}</b>'
                        f' → Now <b style="color:{TV["text"]}">{fmt_price(pos["current"])}</b>'
                        f'&emsp;'
                        f'<span style="color:{TV["red"]}">▼ SL {pos["stop_pct"]*100:.0f}%</span>'
                        f'&nbsp;<span style="color:{TV["green"]}">▲ TP {pos["tp_pct"]*100:.0f}%</span>'
                        f'</div>'
                        f'<div class="prog-bg">'
                        f'<div class="prog-fill" style="width:{prog:.1f}%;background:{bar_c};"></div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True)

                    # Close button — queues command for bot to execute
                    st.markdown('<div class="close-btn">', unsafe_allow_html=True)
                    if st.button(
                        "✕ Close",
                        key=f"close_{pos['id']}",
                        use_container_width=True,
                        help=f"Close {pos['symbol']} at market — executes on next cycle",
                    ):
                        queue_command({
                            "action": "close",
                            "id":     pos["id"],
                            "market": pos["mkt_key"],
                            "reason": "Manual close from dashboard",
                        })
                        st.toast(f"Close queued for {pos['symbol']}", icon="✕")
                    st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="background:{TV["bg2"]};border:1px dashed {TV["border"]};'
                f'border-radius:8px;padding:28px;text-align:center;color:{TV["text2"]};">'
                f'No open positions</div>',
                unsafe_allow_html=True)

        # ── SIGNAL SCANNER ────────────────────────────────────────────────────
        signal_table = state.get("signal_table", [])
        if signal_table:
            st.markdown(f'<div class="sec-hdr">Signal Scanner</div>', unsafe_allow_html=True)
            sig_html = ""
            _MKT_ICONS = {
                "crypto": "◆", "cex": "◆", "dex": "🌊",
                "forex": "💱", "stocks": "📊", "futures": "⚡",
            }
            for s in signal_table[:12]:
                score = s.get("score", 0)
                bar_w = int(abs(score) * 100)
                bar_c = TV["green"] if score > 0 else TV["red"]
                sig   = s.get("signal", "HOLD")
                sig_c = TV["green"] if sig == "BUY" else TV["red"] if sig == "SELL" else TV["text2"]
                icon  = _MKT_ICONS.get(s.get("market",""), "·")
                regime = s.get("regime", "")
                regime_badge = ""
                if regime:
                    rc = TV["yellow"] if regime == "ranging" else TV["blue"] if regime == "trending" else TV["text2"]
                    regime_badge = f'<span style="color:{rc};font-size:10px;">{regime}</span>'
                sig_html += (
                    f'<div class="sig-row" style="background:{TV["bg2"]};">'
                    f'<span style="color:{TV["text2"]};font-size:11px;width:14px;">{icon}</span>'
                    f'<span style="font-weight:700;min-width:70px;">{s.get("symbol","")}</span>'
                    f'<span style="color:{sig_c};font-weight:700;min-width:42px;">{sig}</span>'
                    f'<div style="flex:1;background:{TV["bg3"]};border-radius:2px;height:4px;margin:0 8px;">'
                    f'<div style="width:{bar_w}%;background:{bar_c};height:4px;border-radius:2px;"></div></div>'
                    f'<span style="color:{bar_c};font-variant-numeric:tabular-nums;min-width:40px;text-align:right;">{score:+.2f}</span>'
                    f'&nbsp;&nbsp;{regime_badge}'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:{TV["bg2"]};border:1px solid {TV["border"]};'
                f'border-radius:8px;padding:8px 10px;">{sig_html}</div>',
                unsafe_allow_html=True)

        # ── CLOSED TRADES ─────────────────────────────────────────────────────
        display_trades = list(reversed(recent_trades))[:40]
        st.markdown(
            f'<div class="sec-hdr">Trade History '
            f'<span style="color:{TV["text"]}">{len(all_closed)} total</span></div>',
            unsafe_allow_html=True)

        if display_trades:
            _MKT_LABELS = {
                "crypto":"CEX","cex":"CEX","dex":"DEX","forex":"FX",
                "stocks":"STK","etf":"STK","futures":"FUT",
                "funding_arb":"ARB","polymarket":"POLY",
            }
            rows_html = ""
            for t in display_trades:
                pnl   = t.get("pnl_usd", 0)
                pct   = t.get("pnl_pct", 0)
                c_    = color(pnl)
                icon  = "▲" if pnl > 0 else "▼"
                sign  = "+" if pnl > 0 else ""
                sym   = t.get("symbol", "—")
                mkt   = _MKT_LABELS.get(t.get("market","").lower(), t.get("market","—").upper()[:4])
                rsn   = t.get("close_reason", t.get("reason", "—"))
                ts_raw= t.get("closed_at", t.get("exit_time",""))
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z","+00:00")).strftime("%m/%d %H:%M")
                except Exception:
                    ts = ts_raw[:16] if ts_raw else "—"
                rows_html += (
                    f'<div class="trade-row">'
                    f'<span style="color:{c_};font-weight:700;width:12px;">{icon}</span>'
                    f'<span style="font-weight:700;min-width:58px;">{sym}</span>'
                    f'<span style="color:{TV["text2"]};min-width:34px;font-size:10.5px;">{mkt}</span>'
                    f'<span style="color:{c_};font-weight:700;min-width:72px;">{sign}${abs(pnl):,.2f}</span>'
                    f'<span style="color:{c_};min-width:52px;">{sign}{pct:.1f}%</span>'
                    f'<span style="color:{TV["text2"]};font-size:10.5px;flex:1;">{rsn[:28]}</span>'
                    f'<span style="color:{TV["text2"]};font-size:10.5px;">{ts}</span>'
                    f'</div>'
                )
            pf_s2  = state.get("profit_factor", pf)
            pf_s2s = "∞" if pf_s2 in (0, float("inf")) else f"{pf_s2:.2f}x"
            summary = (
                f'<div style="display:flex;gap:18px;padding:7px 0 0 0;'
                f'font-size:11px;border-top:1px solid {TV["border"]};margin-top:3px;">'
                f'<span style="color:{TV["green"]}">✔ {len(wins)} wins +${w_pnl:,.0f}</span>'
                f'<span style="color:{TV["red"]}">✖ {len(losses)} losses -${l_pnl:,.0f}</span>'
                f'<span style="color:{TV["text2"]}">WR <b style="color:{TV["text"]}">{wr:.0f}%</b></span>'
                f'<span style="color:{TV["text2"]}">PF <b style="color:{TV["text"]}">{pf_s2s}</b></span>'
                f'<span style="color:{TV["text2"]}">Net <b style="color:{color(total_pnl)}">{fmt_usd(total_pnl, True)}</b></span>'
                f'</div>'
            )
            st.markdown(
                f'<div style="background:{TV["bg2"]};border:1px solid {TV["border"]};'
                f'border-radius:8px;padding:8px 12px;max-height:300px;overflow-y:auto;">'
                f'{rows_html}{summary}</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="background:{TV["bg2"]};border:1px dashed {TV["border"]};'
                f'border-radius:8px;padding:28px;text-align:center;color:{TV["text2"]};">'
                f'No completed trades yet</div>',
                unsafe_allow_html=True)

    # ── Footer ────────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="text-align:right;color:{TV["text2"]};font-size:10px;'
        f'margin-top:8px;padding-top:5px;border-top:1px solid {TV["border"]};">'
        f'AI Trader v4.0 · {now_str} · live · {REFRESH_SEC}s refresh'
        f'</div>',
        unsafe_allow_html=True)

    time.sleep(REFRESH_SEC)
    st.rerun()


render()
