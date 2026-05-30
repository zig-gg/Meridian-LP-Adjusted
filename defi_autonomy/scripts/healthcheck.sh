#!/bin/bash
# healthcheck.sh — Read-only health check for Hermes DeFi Autonomy.
# Usage: bash defi_autonomy/scripts/healthcheck.sh
#
# SAFETY: This script is READ-ONLY.
#   - Does NOT modify any files
#   - Does NOT start/stop/restart PM2
#   - Does NOT write to ledgers
#   - Does NOT require private keys or RPC

set -e

BASE_DIR="${HERMES_DEFI_MODULE_ROOT:-/home/ubuntu/hermes-agent/defi_autonomy}"
DATA_DIR="$BASE_DIR/data"
POLICY="$DATA_DIR/risk_policy.json"

echo "=== Hermes DeFi Autonomy — Health Check ==="
echo "Base: $BASE_DIR"
echo "Time: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo ""

# PM2 status
echo "--- PM2 Status ---"
pm2 list 2>/dev/null | grep -i "hermes-defi" || echo "  PM2 process not found or PM2 not installed"
echo ""

# Risk policy
echo "--- Risk Policy ---"
if [ -f "$POLICY" ]; then
    echo "  autonomy_level: $(python3 -c "import json; print(json.load(open('$POLICY')).get('autonomy_level', 'MISSING'))" 2>/dev/null || echo "ERROR")"
    echo "  allow_level2_broadcast: $(python3 -c "import json; print(json.load(open('$POLICY')).get('allow_level2_broadcast', 'NOT SET (false)'))" 2>/dev/null || echo "ERROR")"
    echo "  allow_autonomous_broadcast: $(python3 -c "import json; print(json.load(open('$POLICY')).get('allow_autonomous_broadcast', 'NOT SET (false)'))" 2>/dev/null || echo "ERROR")"
else
    echo "  WARNING: risk_policy.json not found"
fi
echo ""

# Kill switch
echo "--- Kill Switch ---"
KILL_SWITCH_FILE=$(python3 -c "import json; print(json.load(open('$POLICY')).get('kill_switch_file', ''))" 2>/dev/null || echo "")
if [ -z "$KILL_SWITCH_FILE" ] || [ "$KILL_SWITCH_FILE" = "None" ] || [ "$KILL_SWITCH_FILE" = "null" ]; then
    echo "  Configured path: (not set)"
    echo "  STATUS: inactive (no kill_switch_file configured)"
else
    echo "  Configured path: $KILL_SWITCH_FILE"
    if [ -f "$KILL_SWITCH_FILE" ]; then
        echo "  STATUS: ACTIVE (STOP file exists)"
    else
        echo "  STATUS: inactive (file does not exist)"
    fi
fi
echo ""

# Last cycle
echo "--- Last Cycle ---"
CYCLE_REPORT="$DATA_DIR/cycle_report.json"
if [ -f "$CYCLE_REPORT" ]; then
    python3 -c "
import json
r = json.load(open('$CYCLE_REPORT'))
print(f\"  status: {r.get('status', '?')}\")
print(f\"  candidates: {r.get('candidate_count', 0)}\")
print(f\"  approved: {r.get('approved_count', 0)}\")
print(f\"  denied: {r.get('denied_count', 0)}\")
print(f\"  signing_prepared: {r.get('signing_prepared_count', 0)}\")
print(f\"  learning_events: {r.get('learning_events_loaded', 0)}\")
print(f\"  finished: {r.get('finished_at_utc', '?')}\")
" 2>/dev/null || echo "  ERROR reading cycle report"
else
    echo "  No cycle report found"
fi
echo ""

# Safety ledgers
echo "--- Safety Ledger Check ---"
for f in wallet_execution_ledger.jsonl broadcast_ledger.jsonl operator_approvals.jsonl; do
    if [ -f "$DATA_DIR/$f" ]; then
        echo "  WARNING: $f EXISTS ($(wc -l < "$DATA_DIR/$f") lines)"
    else
        echo "  [OK] $f absent (no signing/broadcast)"
    fi
done
echo ""

# System resources
echo "--- System Resources ---"
echo "  Disk: $(df -h "$BASE_DIR" 2>/dev/null | tail -1 | awk '{print $3 "/" $2 " (" $5 " used)"}' || echo "unknown")"
echo "  RAM: $(free -h 2>/dev/null | grep Mem | awk '{print $3 "/" $2}' || echo "unknown")"
echo ""

# Recent errors
echo "--- Recent Errors ---"
CYCLE_REPORT="$DATA_DIR/cycle_report.json"
if [ -f "$CYCLE_REPORT" ]; then
    ERRORS=$(python3 -c "import json; r=json.load(open('$CYCLE_REPORT')); print(len(r.get('errors',[])))" 2>/dev/null || echo "?")
    if [ "$ERRORS" = "0" ]; then
        echo "  No errors in last cycle"
    else
        echo "  $ERRORS error(s) in last cycle"
    fi
else
    echo "  No cycle report"
fi
echo ""
echo "=== Health Check Complete ==="
