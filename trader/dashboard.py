"""
AI Trader — Web Dashboard Server
=================================
Serves a real-time trading dashboard at http://localhost:8888

API Endpoints:
  GET /              → Dashboard HTML
  GET /api/status    → Bot status (running, mode, cycle)
  GET /api/portfolio → Portfolio performance summary
  GET /api/positions → Open positions
  GET /api/trades    → Recent closed trades (last 50)
  GET /api/equity    → Equity curve data
  GET /api/growth    → Compound growth projections
  GET /api/alloc     → Market allocation breakdown
  GET /api/log       → Last 100 log lines

Usage (standalone, reads from saved JSON files):
  python dashboard.py

Usage (integrated, started by main.py in background thread):
  from dashboard import start_dashboard_thread, set_trader
  set_trader(trader_instance)
  start_dashboard_thread(port=8888)
"""
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# ── Shared live-bot reference ─────────────────────────────────────────────────
_trader = None


def set_trader(trader_instance):
    """Inject the live AITrader instance so we can read live state."""
    global _trader
    _trader = trader_instance


# ── Flask setup ───────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, send_from_directory, request
    _flask_ok = True
except ImportError:
    _flask_ok = False
    Flask = None

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _make_app():
    app = Flask(__name__, static_folder=STATIC_DIR)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_trades_json():
        with open(config.TRADE_LOG_FILE) as f:
            return json.load(f)

    def _load_equity_json():
        with open("equity_curve.json") as f:
            return json.load(f)

    # ── routes ────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/api/status")
    def api_status():
        if _trader:
            equity = _trader.portfolio.equity()
            return jsonify({
                "running": _trader.running,
                "mode": "LIVE" if _trader.live else "PAPER",
                "cycle": _trader._cycle,
                "equity": round(equity, 2),
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        return jsonify({
            "running": False,
            "mode": "OFFLINE",
            "cycle": 0,
            "equity": 0,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/api/portfolio")
    def api_portfolio():
        if _trader:
            return jsonify(_trader.portfolio.performance_summary())
        try:
            state = _load_trades_json()
            cash = state.get("cash", 0.0)
            pos_value = sum(
                p.get("qty", 0) * p.get("current_price", p.get("entry_price", 0))
                for p in state.get("open_positions", {}).values()
                if p.get("side") == "long"
            )
            equity = cash + pos_value
            initial = state.get("initial_capital", config.INITIAL_CAPITAL)
            closed = state.get("closed_trades", [])
            winning = [t for t in closed if t.get("pnl_usd", 0) > 0]
            losing  = [t for t in closed if t.get("pnl_usd", 0) <= 0]
            win_rate = len(winning) / len(closed) * 100 if closed else 0
            total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
            total_return = (equity - initial) / initial * 100 if initial else 0
            gross_win = abs(sum(t.get("pnl_usd", 0) for t in winning))
            gross_loss = abs(sum(t.get("pnl_usd", 0) for t in losing))
            pf = gross_win / gross_loss if gross_loss else 0
            peak = state.get("peak_equity", equity)
            max_dd = (peak - equity) / peak * 100 if peak > 0 else 0
            avg_win = (sum(t.get("pnl_pct", 0) for t in winning) / len(winning)) if winning else 0
            avg_loss = (sum(t.get("pnl_pct", 0) for t in losing) / len(losing)) if losing else 0

            # Market breakdown from closed trades
            markets = {}
            for t in closed:
                m = t.get("market", "unknown")
                markets.setdefault(m, {"trades": 0, "pnl_usd": 0.0, "wins": 0})
                markets[m]["trades"] += 1
                markets[m]["pnl_usd"] += t.get("pnl_usd", 0)
                if t.get("pnl_usd", 0) > 0:
                    markets[m]["wins"] += 1
            for m in markets:
                n = markets[m]["trades"]
                markets[m]["win_rate_pct"] = round(markets[m]["wins"] / n * 100, 1) if n else 0
                markets[m]["pnl_usd"] = round(markets[m]["pnl_usd"], 2)

            return jsonify({
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "initial_capital": initial,
                "total_return_pct": round(total_return, 2),
                "total_pnl_usd": round(total_pnl, 2),
                "total_trades": len(closed),
                "winning_trades": len(winning),
                "losing_trades": len(losing),
                "win_rate_pct": round(win_rate, 2),
                "avg_win_pct": round(avg_win, 2),
                "avg_loss_pct": round(avg_loss, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "open_positions": len(state.get("open_positions", {})),
                "markets": markets,
            })
        except FileNotFoundError:
            return jsonify({"equity": config.INITIAL_CAPITAL, "cash": config.INITIAL_CAPITAL,
                            "total_return_pct": 0, "total_trades": 0, "open_positions": 0,
                            "win_rate_pct": 0, "profit_factor": 0, "max_drawdown_pct": 0,
                            "total_pnl_usd": 0, "markets": {}})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions")
    def api_positions():
        if _trader:
            return jsonify(_trader.portfolio.open_positions_summary())
        try:
            state = _load_trades_json()
            result = []
            for pos in state.get("open_positions", {}).values():
                entry = pos.get("entry_price", 0)
                current = pos.get("current_price", entry)
                qty = pos.get("qty", 0)
                pnl = (current - entry) * qty if pos.get("side") == "long" else (entry - current) * qty
                pnl_pct = (current / entry - 1) * 100 if entry > 0 else 0
                result.append({
                    "asset_id": pos.get("asset_id", ""),
                    "symbol": pos.get("symbol", "?"),
                    "market": pos.get("market", "?"),
                    "side": pos.get("side", "long"),
                    "qty": round(qty, 8),
                    "entry_price": entry,
                    "current_price": current,
                    "unrealized_pnl": round(pnl, 2),
                    "unrealized_pnl_pct": round(pnl_pct, 2),
                    "stop_loss": pos.get("stop_loss", 0),
                    "take_profit": pos.get("take_profit", 0),
                    "opened_at": pos.get("opened_at", ""),
                })
            return jsonify(result)
        except Exception:
            return jsonify([])

    @app.route("/api/trades")
    def api_trades():
        limit = int(request.args.get("limit", 50))
        try:
            if _trader:
                trades = list(reversed(_trader.portfolio.closed_trades[-limit:]))
            else:
                state = _load_trades_json()
                trades = list(reversed(state.get("closed_trades", [])[-limit:]))
            return jsonify(trades)
        except Exception:
            return jsonify([])

    @app.route("/api/equity")
    def api_equity():
        limit = int(request.args.get("limit", 500))
        try:
            if _trader and _trader._equity_curve:
                return jsonify(_trader._equity_curve[-limit:])
            curve = _load_equity_json()
            return jsonify(curve[-limit:])
        except Exception:
            return jsonify([])

    @app.route("/api/growth")
    def api_growth():
        try:
            if _trader:
                equity = _trader.portfolio.equity()
            else:
                state = _load_trades_json()
                cash = state.get("cash", config.INITIAL_CAPITAL)
                pos_val = sum(
                    p.get("qty", 0) * p.get("current_price", p.get("entry_price", 0))
                    for p in state.get("open_positions", {}).values()
                    if p.get("side") == "long"
                )
                equity = cash + pos_val
        except Exception:
            equity = config.INITIAL_CAPITAL

        projections = []
        for days in [7, 14, 30, 60, 90, 180, 365]:
            projections.append({
                "days": days,
                "conservative": round(equity * (1.003 ** days), 2),
                "target": round(equity * (1.005 ** days), 2),
                "aggressive": round(equity * (1.010 ** days), 2),
            })
        return jsonify({"current_equity": round(equity, 2), "projections": projections})

    @app.route("/api/alloc")
    def api_alloc():
        try:
            if _trader:
                growth = _trader.compounder.growth_summary()
                return jsonify({
                    "allocations": growth.get("allocations", {}),
                    "allocation_usd": growth.get("allocation_usd", {}),
                    "market_performance": growth.get("market_performance", {}),
                    "scale_factor": growth.get("scale_factor", 1.0),
                })
            with open("allocation_state.json") as f:
                return jsonify(json.load(f))
        except Exception:
            # Return defaults
            equity = config.INITIAL_CAPITAL
            default_alloc = {
                "crypto_cex": 0.35, "crypto_dex": 0.25,
                "polymarket": 0.15, "stocks": 0.15, "forex": 0.10,
            }
            return jsonify({
                "allocations": default_alloc,
                "allocation_usd": {k: round(v * equity, 2) for k, v in default_alloc.items()},
                "market_performance": {},
                "scale_factor": 1.0,
            })

    @app.route("/api/log")
    def api_log():
        limit = int(request.args.get("limit", 100))
        try:
            with open(config.LOG_FILE) as f:
                lines = f.readlines()[-limit:]
            return jsonify({"lines": [ln.rstrip() for ln in reversed(lines)]})
        except Exception:
            return jsonify({"lines": []})

    # CORS headers for dev use
    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    return app


# ── Public API ────────────────────────────────────────────────────────────────

def run_dashboard(host: str = "0.0.0.0", port: int = 8888, debug: bool = False):
    """Run the Flask dashboard server (blocking)."""
    if not _flask_ok:
        print("[dashboard] Flask not installed. Run: pip install flask")
        return
    os.makedirs(STATIC_DIR, exist_ok=True)
    app = _make_app()
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
    print(f"\n  Dashboard → http://localhost:{port}\n")
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


def start_dashboard_thread(port: int = 8888) -> threading.Thread | None:
    """Start the dashboard in a background daemon thread. Returns the thread."""
    if not _flask_ok:
        logger.warning("Flask not available — dashboard disabled. pip install flask")
        return None
    t = threading.Thread(
        target=run_dashboard,
        kwargs={"port": port},
        daemon=True,
        name="dashboard",
    )
    t.start()
    return t


# ── Standalone entry ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Add trader/ to path so config imports work
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    run_dashboard(port=port)
