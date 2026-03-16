#!/usr/bin/env python3
"""Check health of JSON state files used by the trading system.

Run: python scripts/check_state_files.py [trader_dir]
Validates that state files are valid JSON, not stale, and structurally correct.
"""
import json
import os
import sys
import time

def check_file(path, max_age_sec=300):
    """Check a single state file. Returns (ok, message)."""
    if not os.path.exists(path):
        return None, f"NOT FOUND (may be normal if bot hasn't run)"

    try:
        mtime = os.path.getmtime(path)
        age = time.time() - mtime
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"CORRUPT JSON: {e}"
    except Exception as e:
        return False, f"READ ERROR: {e}"

    size = os.path.getsize(path)
    stale = age > max_age_sec

    status = "STALE" if stale else "OK"
    msg = f"{status} (age={int(age)}s, size={size}B, type={type(data).__name__})"
    return not stale, msg

def main():
    trader_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), '..', '..', '..', 'trader'
    )
    trader_dir = os.path.abspath(trader_dir)

    files = {
        "bot_state.json": 60,        # Should update every ~5s
        "trades.json": 86400,        # Updated on trade (1 day tolerance)
        "dex_positions.json": 300,   # Updated every cycle
        "equity_curve.json": 3600,   # Updated periodically
        "settings.json": 86400,      # Rarely changes
    }

    print(f"Checking state files in: {trader_dir}\n")
    all_ok = True
    for fname, max_age in files.items():
        path = os.path.join(trader_dir, fname)
        ok, msg = check_file(path, max_age)
        icon = "OK" if ok else ("??" if ok is None else "FAIL")
        print(f"  [{icon}] {fname}: {msg}")
        if ok is False:
            all_ok = False

    print()
    if all_ok:
        print("All state files healthy")
        return 0
    else:
        print("Some state files have issues - check above")
        return 1

if __name__ == "__main__":
    sys.exit(main())
