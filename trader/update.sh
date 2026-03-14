#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# update.sh — One-command bot updater
#
# Pulls latest code, upgrades packages, and restarts the bot gracefully.
# Safe to run while the bot is running or stopped.
#
# Usage:
#   ./update.sh                # update + restart in same mode as before
#   ./update.sh --live         # update + force restart in live mode
#   ./update.sh --paper        # update + force restart in paper mode
#   ./update.sh --no-restart   # update only, don't restart
#
# What it does:
#   1. Backs up trades.json + dex_positions.json (safety)
#   2. If bot is running: graceful shutdown (SIGTERM, wait up to 30s)
#   3. git pull origin <current-branch> (retries on network failure)
#   4. pip install -r requirements.txt --upgrade
#   5. Syntax check main.py
#   6. Show what changed (git log)
#   7. Restart bot (unless --no-restart)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PIDFILE="trader.pid"
MODEFILE=".mode"
LOGFILE="trader.log"
BACKUP_DIR="backups"

# ── Parse args ────────────────────────────────────────────────────────────────
RESTART=true
FORCE_MODE=""

for arg in "$@"; do
    case "$arg" in
        --no-restart) RESTART=false ;;
        --live)       FORCE_MODE="--live" ;;
        --paper)      FORCE_MODE="" ;;
    esac
done

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(timestamp) [UPDATE] $*" | tee -a "$LOGFILE"; }

log "════════════════════════════════════════════════════════"
log " AI Trader Update Script"
log "════════════════════════════════════════════════════════"

# ── Step 1: Backup state files ─────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
BACKUP_TS="$(date '+%Y%m%d_%H%M%S')"
for file in trades.json dex_positions.json equity_curve.json; do
    if [[ -f "$file" ]]; then
        cp "$file" "$BACKUP_DIR/${file%.json}_${BACKUP_TS}.json"
        log "Backed up $file → $BACKUP_DIR/${file%.json}_${BACKUP_TS}.json"
    fi
done

# ── Step 2: Stop bot if running ───────────────────────────────────────────────
BOT_WAS_RUNNING=false
if [[ -f "$PIDFILE" ]]; then
    BOT_PID="$(cat "$PIDFILE")"
    if kill -0 "$BOT_PID" 2>/dev/null; then
        BOT_WAS_RUNNING=true
        log "Bot is running (PID $BOT_PID) — sending graceful shutdown..."
        kill -TERM "$BOT_PID" 2>/dev/null || true

        # Wait up to 30s for clean shutdown
        waited=0
        while kill -0 "$BOT_PID" 2>/dev/null && [[ $waited -lt 30 ]]; do
            sleep 1
            waited=$((waited + 1))
        done

        if kill -0 "$BOT_PID" 2>/dev/null; then
            log "Bot didn't stop cleanly after 30s — force killing..."
            kill -9 "$BOT_PID" 2>/dev/null || true
        else
            log "Bot stopped cleanly (took ${waited}s)."
        fi
        rm -f "$PIDFILE"
    else
        log "PID file found but process $BOT_PID is gone — removing stale PID file."
        rm -f "$PIDFILE"
    fi
else
    log "Bot is not running (no PID file)."
fi

# ── Step 3: git pull (with retries) ───────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
BEFORE_HASH="$(git rev-parse --short HEAD)"
log "Current branch: $BRANCH | commit: $BEFORE_HASH"
log "Pulling latest code..."

MAX_RETRIES=4
RETRY_DELAY=2
pulled=false
for attempt in $(seq 1 $MAX_RETRIES); do
    if git pull origin "$BRANCH" 2>&1 | tee -a "$LOGFILE"; then
        pulled=true
        break
    fi
    log "Pull attempt $attempt failed. Retrying in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
    RETRY_DELAY=$((RETRY_DELAY * 2))
done

if [[ "$pulled" != "true" ]]; then
    log "ERROR: git pull failed after $MAX_RETRIES attempts. Aborting update."
    # Restart bot if it was running, even if update failed
    if [[ "$BOT_WAS_RUNNING" == "true" && "$RESTART" == "true" ]]; then
        log "Restarting bot with previous code..."
        _do_restart "$FORCE_MODE" || true
    fi
    exit 1
fi

AFTER_HASH="$(git rev-parse --short HEAD)"
if [[ "$BEFORE_HASH" == "$AFTER_HASH" ]]; then
    log "Already up to date (commit $AFTER_HASH). No code changes."
else
    log "Updated: $BEFORE_HASH → $AFTER_HASH"
    log "Changes:"
    git log --oneline "${BEFORE_HASH}..${AFTER_HASH}" 2>/dev/null | while read -r line; do
        log "  $line"
    done
fi

# ── Step 4: Upgrade Python packages ───────────────────────────────────────────
REQUIREMENTS="../requirements.txt"
if [[ ! -f "$REQUIREMENTS" ]]; then
    REQUIREMENTS="requirements.txt"
fi

if [[ -f "$REQUIREMENTS" ]]; then
    log "Upgrading Python packages..."
    pip install -r "$REQUIREMENTS" --upgrade --quiet 2>&1 | tee -a "$LOGFILE" || {
        log "WARNING: pip upgrade had errors (bot may still work with existing packages)"
    }
    log "Packages up to date."
else
    log "WARNING: requirements.txt not found — skipping package upgrade."
fi

# ── Step 5: Syntax check ──────────────────────────────────────────────────────
log "Checking syntax..."
if python3 -m py_compile main.py; then
    log "Syntax OK."
else
    log "ERROR: main.py has syntax errors! Bot will NOT be restarted."
    log "Fix the error and re-run update.sh."
    exit 1
fi

# ── Step 6: Summary ───────────────────────────────────────────────────────────
log "Update complete. Branch: $BRANCH | Commit: $(git rev-parse --short HEAD)"

# ── Step 7: Restart ───────────────────────────────────────────────────────────
if [[ "$RESTART" != "true" ]]; then
    log "Skipping restart (--no-restart). Run ./run_forever.sh to start."
    exit 0
fi

# Determine mode to restart in
if [[ -n "$FORCE_MODE" ]]; then
    START_ARGS="$FORCE_MODE"
elif [[ -f "$MODEFILE" ]]; then
    SAVED_MODE="$(cat "$MODEFILE")"
    START_ARGS="$([[ "$SAVED_MODE" == "live" ]] && echo "--live" || echo "")"
elif [[ "$BOT_WAS_RUNNING" == "false" ]]; then
    # Bot wasn't running before — don't auto-start unless --live/--paper was passed
    log "Bot was not running before update — not auto-starting."
    log "To start: ./run_forever.sh [--live]"
    exit 0
else
    START_ARGS=""
fi

log "Restarting bot (mode: ${START_ARGS:-paper})..."
nohup ./run_forever.sh $START_ARGS >> "$LOGFILE" 2>&1 &
NEWPID=$!
sleep 2

if kill -0 "$NEWPID" 2>/dev/null; then
    log "Bot restarted successfully (watchdog PID $NEWPID)."
    log "Monitor with: tail -f $LOGFILE"
else
    log "WARNING: Bot may not have started — check $LOGFILE for errors."
fi

log "════════════════════════════════════════════════════════"
log " Done."
log "════════════════════════════════════════════════════════"
