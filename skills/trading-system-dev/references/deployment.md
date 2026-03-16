# Deployment & Operations Reference

## Deployment Modes

### Paper Trading (development)
```bash
python trader/main.py
```
No real trades executed. Uses simulated fills at market price.

### Live Trading
```bash
python trader/main.py --live
```
Requires `.env` with valid API keys. Real money at risk.

### 24/7 Watchdog
```bash
cd trader && ./run_forever.sh --live
```
Auto-restarts on crash. PID tracked in `.pid` file. Max 1000 restarts.

### systemd Service
```bash
sudo cp trader/trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trader
sudo systemctl start trader
```

## Safe Update Procedure

The `update.sh` script handles everything:
```bash
./trader/update.sh              # Update + restart in same mode
./trader/update.sh --live       # Force live mode after update
./trader/update.sh --no-restart # Update without restarting
```

What it does:
1. Backs up `trades.json` and `dex_positions.json`
2. Gracefully stops running bot (SIGTERM, 30s timeout)
3. `git pull` latest code
4. `pip install -r requirements.txt --upgrade`
5. Syntax-checks `main.py`
6. Restarts in previous mode

## Monitoring

| Command | Purpose |
|---------|---------|
| `tail -f trader/trader.log` | Real-time logs |
| `python trader/main.py --status` | Portfolio snapshot |
| `python trader/main.py --scan` | Current market signals |
| `python trader/main.py --report` | Full JSON report |
| `streamlit run trader/dashboard.py` | Web UI |

## Environment Variables

### Required for Solana DEX
- `PHANTOM_PRIVATE_KEY` — Solana wallet private key

### Required for Polymarket
- `POLYMARKET_PRIVATE_KEY` — Polygon wallet private key

### Optional (enhances data quality)
- `BIRDEYE_API_KEY` — Real-time Solana token data
- `COINGECKO_API_KEY` — CoinGecko Pro for higher rate limits
- `ANTHROPIC_API_KEY` — Claude for Polymarket probability estimation
- `OPENAI_API_KEY` — Alternative LLM for probability
- `BINANCE_API_KEY` + `BINANCE_SECRET` — CEX trading (disabled)

### Secrets Vault (optional)
- `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` — Centralized secrets management

## Pre-Live Checklist

- [ ] All API keys set in `.env`
- [ ] Paper trading run completed successfully
- [ ] Risk parameters reviewed in `config.py`
- [ ] `--scan` shows reasonable signals
- [ ] Wallet funded with intended trading capital
- [ ] `run_forever.sh` or systemd configured
- [ ] Log rotation set up for `trader.log`
- [ ] Dashboard accessible for monitoring
