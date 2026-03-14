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
#   1. Writes its PID + mode to files (used by update.sh)
#   2. Launches main.py with any args you pass
#   3. On crash / exit: waits 5s then relaunches
#   4. Logs restart events with timestamps
#   5. Removes PID file on clean exit
#   6. Press Ctrl+C to stop cleanly
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARGS="${*:---}"  # pass --live or leave blank for paper mode
[[ "$ARGS" == "--" ]] && ARGS=""

LOGFILE="trader.log"
PIDFILE="trader.pid"
MODEFILE=".mode"
RESTART_DELAY=5   # seconds between restarts
MAX_RESTARTS=1000 # safety cap (prevents infinite tight crash loops)
restart_count=0

# ── Write PID and mode files so update.sh can find us ────────────────────────
echo $$ > "$PIDFILE"
if [[ "$ARGS" == *"--live"* ]]; then
    echo "live" > "$MODEFILE"
else
    echo "paper" > "$MODEFILE"
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
    rm -f "$PIDFILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] Stopped. PID file removed." | tee -a "$LOGFILE"
}
trap cleanup EXIT INT TERM

echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] Starting AI Trader 24/7 | PID=$$ | mode=${ARGS:+live} | args: ${ARGS:-none}" | tee -a "$LOGFILE"

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
