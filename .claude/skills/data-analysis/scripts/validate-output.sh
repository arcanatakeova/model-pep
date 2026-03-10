#!/usr/bin/env bash
# Validates that an analysis report contains all required sections.
# Usage: bash validate-output.sh <report-file>

set -euo pipefail

REPORT="${1:?Usage: validate-output.sh <report-file>}"

if [ ! -f "$REPORT" ]; then
    echo "ERROR: Report file not found: $REPORT"
    exit 1
fi

REQUIRED_SECTIONS=(
    "Executive Summary"
    "Data Overview"
    "Key Findings"
    "Methodology"
    "Recommendations"
    "Appendix"
)

MISSING=0
for section in "${REQUIRED_SECTIONS[@]}"; do
    if ! grep -qi "$section" "$REPORT"; then
        echo "MISSING: $section"
        MISSING=$((MISSING + 1))
    else
        echo "OK: $section"
    fi
done

echo ""
if [ "$MISSING" -eq 0 ]; then
    echo "PASS: All required sections present."
    exit 0
else
    echo "FAIL: $MISSING required section(s) missing."
    exit 1
fi
