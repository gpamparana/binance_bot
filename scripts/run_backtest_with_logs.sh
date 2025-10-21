#!/bin/bash
# Wrapper script to run backtests with automatic log capture
#
# Usage:
#   ./scripts/run_backtest_with_logs.sh
#   ./scripts/run_backtest_with_logs.sh custom_backtest.yaml custom_strategy.yaml

set -e

# Generate run ID
RUN_ID=$(date +%Y%m%d_%H%M%S)
REPORTS_DIR="./reports"
LOG_FILE="${REPORTS_DIR}/${RUN_ID}_backtest.log"

# Create reports directory if it doesn't exist
mkdir -p "${REPORTS_DIR}"

# Default configs
BACKTEST_CONFIG="${1:-configs/backtest/btcusdt_mark_trades_funding.yaml}"
STRATEGY_CONFIG="${2:-configs/strategies/hedge_grid_v1.yaml}"

echo "=========================================="
echo "Running Backtest with Log Capture"
echo "=========================================="
echo "Backtest Config: ${BACKTEST_CONFIG}"
echo "Strategy Config: ${STRATEGY_CONFIG}"
echo "Log File: ${LOG_FILE}"
echo "=========================================="
echo ""

# Run backtest with tee to capture logs
uv run python -m naut_hedgegrid backtest \
    --backtest-config "${BACKTEST_CONFIG}" \
    --strategy-config "${STRATEGY_CONFIG}" \
    2>&1 | tee "${LOG_FILE}"

echo ""
echo "=========================================="
echo "Backtest Complete!"
echo "Logs saved to: ${LOG_FILE}"
echo "=========================================="
