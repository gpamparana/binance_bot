#!/bin/bash
set -a
source .env
set +a

export RUST_BACKTRACE=1
export RUST_LOG=debug

uv run python -m naut_hedgegrid live \
    --venue-config configs/venues/binance_futures_testnet.yaml \
    2>&1 | tee live_test_debug.log

