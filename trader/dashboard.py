"""
AI Trader — Web Dashboard Server
=================================
Serves a real-time trading dashboard at http://localhost:8888

Endpoints:
  GET /              → Dashboard HTML
  GET /events        → SSE stream — pushes live equity/positions/ticker every 5s
  GET /api/status    → Bot status (running, mode, cycle)
  GET /api/portfolio → Full portfolio performance summary
  GET /api/trades    → Recent closed trades (last 50)
  GET /api/equity    → Equity curve history
  GET /api/growth    → Compound growth projections
  GET /api/alloc     → Market allocation breakdown
  GET /api/log       → Last 100 log lines

Live data flow:
  CoinCap API (real-time prices)
      ↓  every 5 seconds (background thread)
  _live_worker() → builds payload → _broadcast()
      ↓
  SSE /events → browser EventSource → updates equity + positions + ticker instantly
"""
import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# ── Shared live-bot reference ─────────────────────────────────────────────────
_trader = None


def set_trader(trader_instance):
    """Inject the live AITrader instance so we can read live in-memory state."""
    global _trader
    _trader = trader_instance


# ── Flask setup ───────────────────────────────────────────────────────────────
try:
    from flask import Flask, Response, jsonify, request, send_from_directory
    _flask_ok = True
except ImportError:
    _flask_ok = False
    Flask = None

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ── Live Price Feed ───────────────────────────────────────────────────────────
# {sym_lower: {symbol, price, pct_24h, rank}}
_price_cache: dict = {}
_price_cache_ts: float = 0.0
_price_lock = threading.Lock()

# SSE client queues
_sse_clients: list = []
_sse_lock = threading.Lock()

# Common symbol → CoinCap id mappings
_SYM_MAP = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana",
    "bnb": "bnb", "avax": "avalanche", "link": "chainlink",
    "dot": "polkadot", "ada": "cardano", "matic": "polygon",
    "arb": "arbitrum", "op": "optimism", "uni": "uniswap",
    "aave": "aave", "mkr": "maker", "atom": "cosmos",
    "near": "near-protocol", "ftm": "fantom", "algo": "algorand",
}


def _fetch_live_prices() -> dict:
    """Fetch top 50 crypto prices from CoinCap (cached 4s). Thread-safe."""
    global _price_cache, _price_cache_ts
    with _price_lock:
        if time.time() - _price_cache_ts < 4 and _price_cache:
            return dict(_price_cache)

    try:
        import requests as _req
        resp = _req.get(
            "https://api.coincap.io/v2/assets",
            params={"limit": 50},
            timeout=8,
            headers={"Accept": "application/json"},
        )
        assets = resp.json().get("data", [])
        result = {}
        for a in assets:
            sym = (a.get("symbol") or "").lower()
            result[sym] = {
                "symbol": (a.get("symbol") or "").upper(),
                "id":     a.get("id", ""),
                "price":  float(a.get("priceUsd") or 0),
                "pct_24h": round(float(a.get("changePercent24Hr") or 0), 2),
                "rank":   int(a.get("rank") or 99),
            }
        with _price_lock:
            _price_cache = result
            _price_cache_ts = time.time()
        return result
    except Exception as e:
        logger.debug("CoinCap fetch failed: %s", e)
        with _price_lock:
            return dict(_price_cache)


def _live_price_for(symbol: str, prices: dict) -> float | None:
    """Look up live price for a position symbol in the prices dict."""
    base = symbol.split("/")[0].split("-")[0].lower()
    # Direct match
    if base in prices:
        return prices[base]["price"]
    # Mapped name
    mapped = _SYM_MAP.get(base)
    if mapped and mapped in prices:
        return prices[mapped]["price"]
    return None


def _build_live_payload(prices: dict) -> dict:
    """
    Assemble the full live payload:
      - equity & positions with real-time prices from CoinCap
      - ticker strip (top 20 crypto)
    """
    positions = []
    cash = 0.0
    initial = config.INITIAL_CAPITAL
    mode = "OFFLINE"
    cycle = 0

    # ── Source positions ──────────────────────────────────────────────────────
    if _trader:
        raw = _trader.portfolio.open_positions_summary()
        cash = _trader.portfolio.cash
        initial = _trader.portfolio.initial_capital
        mode = "LIVE" if _trader.live else "PAPER"
        cycle = _trader._cycle
    else:
        raw = []
        try:
            with open(config.TRADE_LOG_FILE) as f:
                state = json.load(f)
            cash = state.get("cash", config.INITIAL_CAPITAL)
            initial = state.get("initial_capital", config.INITIAL_CAPITAL)
            for pos in state.get("open_positions", {}).values():
                entry   = pos.get("entry_price", 0)
                current = pos.get("current_price", entry)
                qty     = pos.get("qty", 0)
                pnl     = (current - entry) * qty
                pnl_pct = (current / entry - 1) * 100 if entry > 0 else 0
                raw.append({
                    "asset_id":           pos.get("asset_id", ""),
                    "symbol":             pos.get("symbol", "?"),
                    "market":             pos.get("market", "?"),
                    "side":               pos.get("side", "long"),
                    "qty":                round(qty, 8),
                    "entry_price":        entry,
                    "current_price":      current,
                    "unrealized_pnl":     round(pnl, 2),
                    "unrealized_pnl_pct": round(pnl_pct, 2),
                    "stop_loss":          pos.get("stop_loss", 0),
                    "take_profit":        pos.get("take_profit", 0),
                    "opened_at":          pos.get("opened_at", ""),
                })
        except Exception:
            pass

    # ── Apply live prices to positions ────────────────────────────────────────
    live_pos_value = 0.0
    for p in raw:
        live_price = _live_price_for(p["symbol"], prices)
        if live_price and live_price > 0:
            entry   = p["entry_price"]
            qty     = p["qty"]
            pnl     = (live_price - entry) * qty if p["side"] == "long" else (entry - live_price) * qty
            pnl_pct = (live_price / entry - 1) * 100 if entry > 0 else 0
            p["current_price"]      = live_price
            p["unrealized_pnl"]     = round(pnl, 2)
            p["unrealized_pnl_pct"] = round(pnl_pct, 2)
        if p["side"] == "long":
            live_pos_value += p["qty"] * p["current_price"]
        positions.append(p)

    equity      = cash + live_pos_value
    return_usd  = equity - initial
    return_pct  = return_usd / initial * 100 if initial else 0
    open_pnl    = sum(p["unrealized_pnl"] for p in positions)

    # ── Ticker: top 20 by rank ────────────────────────────────────────────────
    ticker = sorted(
        [{"sym": v["symbol"], "price": v["price"], "pct": v["pct_24h"], "rank": v["rank"]}
         for v in prices.values()],
        key=lambda x: x["rank"],
    )[:20]

    return {
        "type":       "live",
        "ts":         datetime.now(timezone.utc).isoformat(),
        "mode":       mode,
        "cycle":      cycle,
        "equity":     round(equity, 2),
        "cash":       round(cash, 2),
        "initial":    initial,
        "return_pct": round(return_pct, 2),
        "return_usd": round(return_usd, 2),
        "open_pnl":   round(open_pnl, 2),
        "positions":  positions,
        "ticker":     ticker,
    }


def _broadcast(payload: dict):
    """Push JSON payload to every connected SSE client. Evict dead clients."""
    msg = "data: " + json.dumps(payload) + "\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _live_worker():
    """Background daemon: fetch live prices every 5s and push to SSE clients."""
    while True:
        try:
            prices = _fetch_live_prices()
            with _sse_lock:
                has_clients = bool(_sse_clients)
            if has_clients:
                payload = _build_live_payload(prices)
                _broadcast(payload)
        except Exception as e:
            logger.debug("Live worker error: %s", e)
        time.sleep(5)


# ── Flask App ─────────────────────────────────────────────────────────────────

def _make_app():
    app = Flask(__name__, static_folder=STATIC_DIR)

    # helpers
    def _load_trades_json():
        with open(config.TRADE_LOG_FILE) as f:
            return json.load(f)

    def _load_equity_json():
        with open("equity_curve.json") as f:
            return json.load(f)

    # ── SSE ───────────────────────────────────────────────────────────────────

    @app.route("/events")
    def sse_events():
        """Server-Sent Events stream — pushes live data every 5 seconds."""
        q = queue.Queue(maxsize=12)
        with _sse_lock:
            _sse_clients.append(q)

        def generate():
            # Send one immediate snapshot so the page loads live data right away
            try:
                prices  = _fetch_live_prices()
                payload = _build_live_payload(prices)
                yield "data: " + json.dumps(payload) + "\n\n"
            except Exception:
                pass

            try:
                while True:
                    try:
                        msg = q.get(timeout=28)
                        yield msg
                    except queue.Empty:
                        yield ": heartbeat\n\n"   # keep TCP alive
            except GeneratorExit:
                pass
            finally:
                with _sse_lock:
                    if q in _sse_clients:
                        _sse_clients.remove(q)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control":    "no-cache",
                "X-Accel-Buffering": "no",
                "Connection":       "keep-alive",
            },
        )

    # ── REST fallbacks (for slower-changing data) ─────────────────────────────

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/api/status")
    def api_status():
        if _trader:
            return jsonify({
                "running": _trader.running,
                "mode":    "LIVE" if _trader.live else "PAPER",
                "cycle":   _trader._cycle,
                "equity":  round(_trader.portfolio.equity(), 2),
                "ts":      datetime.now(timezone.utc).isoformat(),
            })
        return jsonify({"running": False, "mode": "OFFLINE", "cycle": 0,
                        "equity": 0, "ts": datetime.now(timezone.utc).isoformat()})

    @app.route("/api/portfolio")
    def api_portfolio():
        if _trader:
            return jsonify(_trader.portfolio.performance_summary())
        try:
            state   = _load_trades_json()
            cash    = state.get("cash", 0.0)
            pos_val = sum(
                p.get("qty", 0) * p.get("current_price", p.get("entry_price", 0))
                for p in state.get("open_positions", {}).values()
                if p.get("side") == "long"
            )
            equity  = cash + pos_val
            initial = state.get("initial_capital", config.INITIAL_CAPITAL)
            closed  = state.get("closed_trades", [])
            winning = [t for t in closed if t.get("pnl_usd", 0) > 0]
            losing  = [t for t in closed if t.get("pnl_usd", 0) <= 0]
            win_rate    = len(winning) / len(closed) * 100 if closed else 0
            total_pnl   = sum(t.get("pnl_usd", 0) for t in closed)
            total_return = (equity - initial) / initial * 100 if initial else 0
            gross_win   = abs(sum(t.get("pnl_usd", 0) for t in winning))
            gross_loss  = abs(sum(t.get("pnl_usd", 0) for t in losing))
            pf          = gross_win / gross_loss if gross_loss else 0
            peak        = state.get("peak_equity", equity)
            max_dd      = (peak - equity) / peak * 100 if peak > 0 else 0
            avg_win     = sum(t.get("pnl_pct", 0) for t in winning) / len(winning) if winning else 0
            avg_loss    = sum(t.get("pnl_pct", 0) for t in losing) / len(losing) if losing else 0
            markets     = {}
            for t in closed:
                m = t.get("market", "unknown")
                markets.setdefault(m, {"trades": 0, "pnl_usd": 0.0, "wins": 0})
                markets[m]["trades"]  += 1
                markets[m]["pnl_usd"] += t.get("pnl_usd", 0)
                if t.get("pnl_usd", 0) > 0:
                    markets[m]["wins"] += 1
            for m in markets:
                n = markets[m]["trades"]
                markets[m]["win_rate_pct"] = round(markets[m]["wins"] / n * 100, 1) if n else 0
                markets[m]["pnl_usd"]      = round(markets[m]["pnl_usd"], 2)
            return jsonify({
                "equity": round(equity, 2), "cash": round(cash, 2),
                "initial_capital": initial,
                "total_return_pct": round(total_return, 2),
                "total_pnl_usd": round(total_pnl, 2),
                "total_trades": len(closed),
                "winning_trades": len(winning), "losing_trades": len(losing),
                "win_rate_pct": round(win_rate, 2),
                "avg_win_pct": round(avg_win, 2), "avg_loss_pct": round(avg_loss, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "open_positions": len(state.get("open_positions", {})),
                "markets": markets,
            })
        except FileNotFoundError:
            return jsonify({"equity": config.INITIAL_CAPITAL, "cash": config.INITIAL_CAPITAL,
                            "total_return_pct": 0, "total_trades": 0, "open_positions": 0,
                            "win_rate_pct": 0, "profit_factor": 0, "max_drawdown_pct": 0,
                            "total_pnl_usd": 0, "avg_win_pct": 0, "avg_loss_pct": 0, "markets": {}})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/trades")
    def api_trades():
        limit = int(request.args.get("limit", 50))
        try:
            if _trader:
                trades = list(reversed(_trader.portfolio.closed_trades[-limit:]))
            else:
                trades = list(reversed(_load_trades_json().get("closed_trades", [])[-limit:]))
            return jsonify(trades)
        except Exception:
            return jsonify([])

    @app.route("/api/equity")
    def api_equity():
        limit = int(request.args.get("limit", 500))
        try:
            if _trader and _trader._equity_curve:
                return jsonify(_trader._equity_curve[-limit:])
            return jsonify(_load_equity_json()[-limit:])
        except Exception:
            return jsonify([])

    @app.route("/api/growth")
    def api_growth():
        try:
            equity = _trader.portfolio.equity() if _trader else (
                lambda s: s.get("cash", config.INITIAL_CAPITAL) + sum(
                    p.get("qty", 0) * p.get("current_price", p.get("entry_price", 0))
                    for p in s.get("open_positions", {}).values() if p.get("side") == "long"
                )
            )(_load_trades_json())
        except Exception:
            equity = config.INITIAL_CAPITAL
        projections = [
            {"days": d,
             "conservative": round(equity * (1.003 ** d), 2),
             "target":       round(equity * (1.005 ** d), 2),
             "aggressive":   round(equity * (1.010 ** d), 2)}
            for d in [7, 14, 30, 60, 90, 180, 365]
        ]
        return jsonify({"current_equity": round(equity, 2), "projections": projections})

    @app.route("/api/alloc")
    def api_alloc():
        try:
            if _trader:
                g = _trader.compounder.growth_summary()
                return jsonify({
                    "allocations":        g.get("allocations", {}),
                    "allocation_usd":     g.get("allocation_usd", {}),
                    "market_performance": g.get("market_performance", {}),
                    "scale_factor":       g.get("scale_factor", 1.0),
                })
            with open("allocation_state.json") as f:
                return jsonify(json.load(f))
        except Exception:
            equity = config.INITIAL_CAPITAL
            da = {"crypto_cex": 0.35, "crypto_dex": 0.25, "polymarket": 0.15, "stocks": 0.15, "forex": 0.10}
            return jsonify({
                "allocations":    da,
                "allocation_usd": {k: round(v * equity, 2) for k, v in da.items()},
                "market_performance": {}, "scale_factor": 1.0,
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

    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    return app


# ── Public API ────────────────────────────────────────────────────────────────

def run_dashboard(host: str = "0.0.0.0", port: int = 8888, debug: bool = False):
    """Run the Flask dashboard server (blocking). Starts live-price worker thread."""
    if not _flask_ok:
        print("[dashboard] Flask not installed. Run: pip3 install flask")
        return
    os.makedirs(STATIC_DIR, exist_ok=True)

    # Start live price background worker
    worker = threading.Thread(target=_live_worker, daemon=True, name="live-prices")
    worker.start()

    app = _make_app()
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
    print(f"\n  Dashboard → http://localhost:{port}\n")
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


def start_dashboard_thread(port: int = 8888) -> threading.Thread | None:
    """Start the dashboard in a background daemon thread. Returns the thread."""
    if not _flask_ok:
        logger.warning("Flask not available — dashboard disabled. pip3 install flask")
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
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    run_dashboard(port=port)
