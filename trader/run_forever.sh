#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_forever.sh — 24/7 bot watchdog (no systemd required)
#
# Usage:
#   chmod +x run_forever.sh
#   ./run_forever.sh           # paper mode (default)
#   ./run_forever.sh --live    # live trading (needs .env with API keys)
#   nohup ./run_forever.sh --live > trader.log 2>&1 &   # background
#
# The script:
#   1. Launches main.py with any args you pass
#   2. On crash / exit: waits 5s then relaunches
#   3. Logs restart events with timestamps
#   4. Press Ctrl+C to stop cleanly
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARGS="${*:---}"  # pass --live or leave blank for paper mode
[[ "$ARGS" == "--" ]] && ARGS=""

LOGFILE="trader.log"
RESTART_DELAY=5   # seconds between restarts
MAX_RESTARTS=1000 # safety cap (prevents infinite tight crash loops)
restart_count=0

echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] Starting AI Trader 24/7 | args: ${ARGS:-none}" | tee -a "$LOGFILE"

# Load .env if present
if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

while true; do
    python3 main.py $ARGS 2>&1 | tee -a "$LOGFILE" || true
    exit_code=$?

    restart_count=$((restart_count + 1))
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

    if [[ $restart_count -ge $MAX_RESTARTS ]]; then
        echo "$timestamp [WATCHDOG] Max restarts ($MAX_RESTARTS) reached. Stopping." | tee -a "$LOGFILE"
        exit 1
    fi

    echo "$timestamp [WATCHDOG] Bot exited (code=$exit_code). Restart #$restart_count in ${RESTART_DELAY}s..." | tee -a "$LOGFILE"
    sleep "$RESTART_DELAY"
done
