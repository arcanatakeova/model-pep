#!/usr/bin/env python3
"""Validate config.py parameters are within safe ranges.

Run: python scripts/validate_config.py
Returns exit code 0 if valid, 1 if issues found.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'trader'))

def validate():
    issues = []
    try:
        import config
    except ImportError:
        print("ERROR: Cannot import trader/config.py")
        return 1

    # Risk parameter bounds
    checks = [
        ("MAX_POSITION_PCT", 0.01, 0.30, "Position size"),
        ("STOP_LOSS_PCT", 0.01, 0.15, "Stop loss"),
        ("TAKE_PROFIT_PCT", 0.02, 0.30, "Take profit"),
        ("RISK_PER_TRADE_PCT", 0.005, 0.15, "Risk per trade"),
    ]

    for attr, lo, hi, label in checks:
        val = getattr(config, attr, None)
        if val is None:
            issues.append(f"MISSING: {attr} not found in config.py")
        elif not (lo <= val <= hi):
            issues.append(f"OUT OF RANGE: {label} ({attr}={val}) should be {lo}-{hi}")

    # Integer bounds
    max_pos = getattr(config, "MAX_OPEN_POSITIONS", None)
    if max_pos is not None and not (1 <= max_pos <= 50):
        issues.append(f"OUT OF RANGE: MAX_OPEN_POSITIONS={max_pos} should be 1-50")

    # Sanity: stop loss should be less than take profit
    sl = getattr(config, "STOP_LOSS_PCT", 0)
    tp = getattr(config, "TAKE_PROFIT_PCT", 0)
    if sl >= tp:
        issues.append(f"LOGIC ERROR: STOP_LOSS_PCT ({sl}) >= TAKE_PROFIT_PCT ({tp})")

    # DEX position size sanity
    dex_base = getattr(config, "DEX_BASE_POSITION_SIZE", None)
    dex_max = getattr(config, "DEX_MAX_POSITION_SIZE", None)
    if dex_base and dex_max and dex_base > dex_max:
        issues.append(f"LOGIC ERROR: DEX_BASE ({dex_base}) > DEX_MAX ({dex_max})")

    if issues:
        print(f"Config validation FAILED ({len(issues)} issues):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    else:
        print("Config validation PASSED - all parameters within safe ranges")
        return 0

if __name__ == "__main__":
    sys.exit(validate())
