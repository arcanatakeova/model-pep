#!/usr/bin/env bash
# Basic health check script for incident triage.
# Customize the ENDPOINTS and SERVICES arrays for your environment.
#
# Usage: bash check-status.sh [--verbose]

set -euo pipefail

VERBOSE="${1:-}"

# Configure these for your environment
ENDPOINTS=(
    "http://localhost:3000/health"
    "http://localhost:8080/api/health"
)

SERVICES=(
    "nginx"
    "postgresql"
    "redis"
)

echo "=== Health Check Report ==="
echo "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# Check HTTP endpoints
echo "--- HTTP Endpoints ---"
for endpoint in "${ENDPOINTS[@]}"; do
    status=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$endpoint" 2>/dev/null || echo "UNREACHABLE")
    if [ "$status" = "200" ]; then
        echo "OK:   $endpoint (HTTP $status)"
    else
        echo "FAIL: $endpoint (HTTP $status)"
    fi
done

echo ""

# Check system services
echo "--- System Services ---"
for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service" 2>/dev/null; then
        echo "OK:   $service (running)"
    elif pgrep -x "$service" > /dev/null 2>&1; then
        echo "OK:   $service (process found)"
    else
        echo "FAIL: $service (not running)"
    fi
done

echo ""

# Basic system metrics
echo "--- System Metrics ---"
echo "Load average: $(cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}' || echo 'N/A')"
echo "Memory: $(free -h 2>/dev/null | awk '/^Mem:/{print $3 "/" $2 " used"}' || echo 'N/A')"
echo "Disk: $(df -h / 2>/dev/null | awk 'NR==2{print $3 "/" $2 " used (" $5 ")"}' || echo 'N/A')"

if [ "$VERBOSE" = "--verbose" ]; then
    echo ""
    echo "--- Recent Errors (last 50 lines of syslog) ---"
    tail -50 /var/log/syslog 2>/dev/null | grep -i "error\|fail\|crit" || echo "No recent errors found"
fi

echo ""
echo "=== End Health Check ==="
