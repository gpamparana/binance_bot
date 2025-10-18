# Quick Start Guide - Trading with NautilusTrader

This guide covers paper trading, live trading, and backtesting with the HedgeGridV1 strategy.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Backtest Trading (Historical Simulation)](#backtest-trading-historical-simulation)
3. [Paper Trading (Simulated Execution)](#paper-trading-simulated-execution)
4. [Live Trading (Real Execution)](#live-trading-real-execution)
5. [Operational Controls](#operational-controls)
6. [Configuration Tuning](#configuration-tuning)
7. [Troubleshooting](#troubleshooting)
8. [Emergency Procedures](#emergency-procedures)

## Prerequisites

### 1. Python Environment
- Python 3.11+
- `uv` package manager installed

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. API Keys

**Important**: Binance requires API authentication even for paper trading to fetch instrument definitions (metadata). API keys are used ONLY to load instrument specifications - no real orders are placed in paper or backtest modes.

```bash
# Create .env file from template
cp .env.example .env

# Edit .env and add your Binance API keys
# Get keys from: https://www.binance.com/en/my/settings/api-management
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Source the environment variables
set -a; source .env; set +a
```

### 3. Configuration Files

The project includes default configurations:
- `configs/strategies/hedge_grid_v1.yaml` - Strategy parameters
- `configs/venues/binance_futures.yaml` - Venue settings
- `configs/backtest/btcusdt_mark_trades_funding.yaml` - Backtest configuration

---

## Backtest Trading (Historical Simulation)

Run backtests on historical data to test strategy performance before deploying to paper or live trading.

### Step 1: Prepare Historical Data

**Option A: Download Sample Data (Recommended for First-Time Users)**

The backtest requires historical market data in Parquet format stored in a Nautilus data catalog. You can use Tardis or other data providers to download historical data.

```bash
# Example: Download 1 month of BTCUSDT data using Tardis (if available)
# This is a placeholder - actual data download depends on your data provider
# See: https://docs.nautilustrader.io/concepts/data for data catalog setup
```

**Option B: Use Existing Data Catalog**

If you already have a Nautilus data catalog:

```bash
# Ensure catalog path in backtest config matches your data location
# Edit: configs/backtest/btcusdt_mark_trades_funding.yaml
# Set: catalog_path: /path/to/your/data/catalog
```

### Step 2: Review Backtest Configuration

Open `configs/backtest/btcusdt_mark_trades_funding.yaml`:

```yaml
# Time range for backtest
start_time: "2024-01-01T00:00:00Z"
end_time: "2024-12-31T23:59:59Z"

# Data catalog location
catalog:
  catalog_path: "./data/catalog"  # Update this to your catalog path

# Instruments and data types to load
instruments:
  - instrument_id: "BTCUSDT-PERP.BINANCE"
    data_types:
      - OrderBookDelta  # Order book updates
      - TradeTick       # Market trades
      - QuoteTick       # Best bid/ask updates
      - MarkPrice       # Mark price updates (futures)
      - FundingRate     # Funding rate updates (futures)

# Venue configuration
venues:
  - name: BINANCE
    venue_type: EXCHANGE
    account_type: MARGIN
    base_currency: USDT
    starting_balances:
      - USDT=10000  # Starting capital

# Execution simulation
execution:
  # Latency modeling (ms)
  latency:
    base: 5
    insert: 2
    update: 2
    cancel: 2

  # Fill modeling
  fill_model:
    prob_fill_on_limit: 0.7  # 70% chance of limit order fill
    prob_fill_on_stop: 0.9   # 90% chance of stop fill
    prob_slippage: 0.3       # 30% chance of slippage
    random_seed: 42

  # Fee structure (Binance Futures rates)
  fees:
    maker_fee: 0.0002  # 0.02% maker fee
    taker_fee: 0.0004  # 0.04% taker fee
```

Key sections explained:
- **Time Range**: Set `start_time` and `end_time` for your backtest period
- **Catalog Path**: Must point to valid Nautilus ParquetDataCatalog
- **Data Types**: Include all required data types (OrderBookDelta, TradeTick, etc.)
- **Starting Balance**: Set initial capital (e.g., 10,000 USDT)
- **Execution Simulation**: Models realistic latency, fills, and fees
- **Risk Controls**: Position limits, max leverage, drawdown limits

### Step 3: Run Backtest

```bash
# From project root
uv run python -m naut_hedgegrid backtest

# Or with custom config
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml
```

### Step 4: Expected Output

```
════════════════════════ Backtest Runner ════════════════════════
Loading configurations...
✓ Backtest config: btcusdt_mark_trades_funding.yaml
✓ Strategy config: hedge_grid_v1.yaml

Setting up data catalog...
✓ Catalog path: ./data/catalog
✓ Found instruments: BTCUSDT-PERP.BINANCE

Loading instruments...
✓ Loaded 1 instrument(s)

Loading historical data...
✓ Loaded 125,432 OrderBookDelta records
✓ Loaded 45,123 TradeTick records
✓ Loaded 89,234 QuoteTick records
✓ Loaded 8,760 MarkPrice records
✓ Loaded 8,760 FundingRate records

Setting up backtest engine...
✓ Venue: BINANCE (USDT_FUTURE)
✓ Starting balance: 10,000 USDT
✓ Execution simulation: Enabled
✓ Latency modeling: Enabled
✓ Fee structure: Maker 0.02%, Taker 0.04%

Running backtest...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:02:15
✓ Backtest complete

Performance Summary:
┌─────────────────────────────────────────┐
│ Total PnL:           +1,250.00 USDT     │
│ Total Return:        +12.5%             │
│ Sharpe Ratio:        1.85               │
│ Max Drawdown:        -3.2%              │
│ Win Rate:            67.8%              │
│ Total Trades:        1,234              │
│ Total Fees:          -45.67 USDT        │
│ Avg Trade PnL:       +1.01 USDT         │
└─────────────────────────────────────────┘

Results saved to: ./backtests/results/btcusdt_2024_<timestamp>/
```

### Step 5: Analyze Results

Backtest artifacts are saved in `./backtests/results/` with:

```
backtests/results/btcusdt_2024_20250117_143022/
├── config.yaml              # Full backtest configuration
├── performance_report.json  # Detailed metrics
├── trades.csv              # All executed trades
├── positions.csv           # Position history
├── orders.csv              # Order history
├── account_summary.json    # Final account state
└── equity_curve.png        # Equity curve visualization (if plotting enabled)
```

**Key Metrics to Review**:

1. **Total PnL**: Net profit/loss after fees
2. **Total Return**: Percentage return on starting capital
3. **Sharpe Ratio**: Risk-adjusted return (>1.0 is good, >2.0 is excellent)
4. **Max Drawdown**: Largest peak-to-trough decline (keep <10% for safety)
5. **Win Rate**: Percentage of profitable trades (>55% is typical for grid strategies)
6. **Total Fees**: Transaction costs (should be <20% of gross PnL)

### Step 6: Iterate and Refine

Based on backtest results:

1. **If performance is poor**:
   - Adjust grid spacing (`grid_step_bps`) in strategy config
   - Tune exit parameters (`tp_steps`, `sl_steps`)
   - Review regime detection settings
   - Check funding rate impact on performance

2. **If drawdown is too high**:
   - Reduce position sizes (`base_qty`, `qty_scale`)
   - Tighten stop-loss (`sl_steps`)
   - Add volatility-based position sizing

3. **If too many trades**:
   - Increase grid spacing
   - Add placement policy filters

**Example**: Adjust grid spacing

```yaml
# configs/strategies/hedge_grid_v1.yaml
grid:
  grid_step_bps: 35.0  # Increase from 25.0 to reduce trade frequency
  grid_levels_long: 8   # Reduce from 10 to limit exposure
  grid_levels_short: 8
```

### Common Backtest Issues

**Issue**: "No data found in catalog"

**Solution**:
```bash
# Check catalog path
ls -la ./data/catalog/

# Verify instrument data exists
# You need to populate the catalog with historical data first
# See NautilusTrader docs: https://docs.nautilustrader.io/concepts/data
```

**Issue**: "Missing data types"

**Solution**: Ensure all required data types are available in catalog:
- OrderBookDelta (required for order book simulation)
- TradeTick (required for fills)
- MarkPrice (required for futures)
- FundingRate (required for funding calculations)

**Issue**: "Backtest runs but no trades executed"

**Solution**:
- Check regime detector warmup requirements (needs ~50 bars)
- Verify grid recentering threshold
- Review placement policy filters
- Check if strategy is skipping bars due to uninitialized components

---

## Paper Trading (Simulated Execution)

Test your strategy in real-time with simulated execution before risking real capital.

### Quick Start

```bash
# From project root
uv run python -m naut_hedgegrid paper
```

### With Custom Configs

```bash
uv run python -m naut_hedgegrid paper \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml
```

### With Operational Controls

```bash
# Enable Prometheus metrics and REST API for monitoring
uv run python -m naut_hedgegrid paper --enable-ops
```

This starts:
- **Prometheus metrics** on port 8000 (`http://localhost:8000/metrics`)
- **REST API** on port 8080 (`http://localhost:8080/api/v1/`)

### Expected Output

```
═══════════════════════ Paper Trading Runner ═══════════════════════
Validating environment variables...
✓ BINANCE_API_KEY found
✓ BINANCE_API_SECRET found
  Note: Paper trading uses simulated execution - API keys are only
        used to fetch instrument specifications, no real orders will be placed.

Loading configurations...
✓ Strategy config: hedge_grid_v1.yaml
✓ Venue config: binance_futures.yaml
Instrument: BTCUSDT-PERP.BINANCE
OMS Type: HEDGING

Configuring paper trading node...
✓ Data client configured: BINANCE (USDT_FUTURE)
✓ Instrument subscription: BTCUSDT
✓ Execution mode: PAPER (simulated fills)

Starting trading node...
✓ Node built successfully
✓ Node started, waiting for bars...

┌─────────── Status ───────────┐
│ Paper Trading Active         │
│                              │
│ Strategy: hedge_grid_v1      │
│ Instrument: BTCUSDT-PERP     │
│ OMS Type: HEDGING            │
│ Bar Type: 1-MINUTE-LAST      │
│                              │
│ Press CTRL-C to shutdown     │
└──────────────────────────────┘

[Strategy logs will appear here...]
```

### Monitoring Paper Trading

Watch for these key events:

1. **Bar Reception**:
   ```
   Bar: close=45123.50, regime=SIDEWAYS
   ```

2. **Warmup Phase**:
   ```
   Regime detector not warm yet (bars: 23/50)
   ```

3. **Grid Building**:
   ```
   Built 2 ladder(s): LONG(5 rungs), SHORT(5 rungs)
   ```

4. **Order Activity**:
   ```
   Order accepted: BTCUSDT-PERP-LONG BUY 0.001 @ 45000.00
   Order filled: BTCUSDT-PERP-LONG BUY 0.001 @ 45000.00
   ```

### Shutdown Paper Trading

```bash
# Press CTRL-C in terminal
^C
Shutdown signal received
Shutting down...
✓ Node stopped
✓ Shutdown complete
```

---

## Live Trading (Real Execution)

### ⚠️ CRITICAL WARNING

**LIVE TRADING USES REAL MONEY. ALL ORDERS ARE EXECUTED WITH REAL FUNDS.**

Before running live trading:
- ✅ Test thoroughly in backtest mode (1+ month of historical data)
- ✅ Test in paper mode for at least 24-48 hours
- ✅ Test on Binance Testnet with real orders (see Testnet section)
- ✅ Review all strategy parameters carefully
- ✅ Set appropriate position limits and stop-losses
- ✅ Prepare emergency shutdown procedures
- ✅ Monitor funding rates and liquidation buffers

### Setup API Keys

```bash
# Ensure environment variables are set
export BINANCE_API_KEY=your_api_key_here
export BINANCE_API_SECRET=your_api_secret_here

# Or use .env file
set -a; source .env; set +a

# Verify
echo $BINANCE_API_KEY
```

**API Key Requirements**:
- ✅ Futures trading enabled
- ✅ IP whitelist configured (if applicable)
- ✅ Hedge mode enabled on Binance account (Settings → Position Mode → Hedge Mode)

### Quick Start

```bash
# From project root
uv run python -m naut_hedgegrid live
```

### With Custom Configs

```bash
uv run python -m naut_hedgegrid live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml
```

### With Operational Controls

```bash
# Enable monitoring and runtime controls
uv run python -m naut_hedgegrid live --enable-ops
```

### Expected Output

```
═══════════════════════ Live Trading Runner ═══════════════════════
Validating environment variables...
✓ BINANCE_API_KEY found
✓ BINANCE_API_SECRET found

Loading configurations...
✓ Strategy config: hedge_grid_v1.yaml
✓ Venue config: binance_futures.yaml
Instrument: BTCUSDT-PERP.BINANCE
OMS Type: HEDGING

┌──────────────── DANGER ─────────────────┐
│ WARNING: LIVE TRADING WITH REAL FUNDS   │
│                                          │
│ This mode will place REAL ORDERS on     │
│ Binance Futures. All trades will        │
│ execute with REAL MONEY.                 │
│                                          │
│ Ensure your strategy is thoroughly      │
│ tested before proceeding.                │
└──────────────────────────────────────────┘

Configuring live trading node...
✓ Data client configured: BINANCE (USDT_FUTURE)
✓ Instrument subscription: BTCUSDT
✓ Execution client configured: BINANCE (USDT_FUTURE)
✓ use_reduce_only: False (hedge mode enabled)

Starting trading node...
✓ Node built successfully
✓ Node started, waiting for bars...

┌───────────── Status ─────────────┐
│ LIVE TRADING ACTIVE              │
│                                  │
│ Strategy: hedge_grid_v1          │
│ Instrument: BTCUSDT-PERP.BINANCE │
│ OMS Type: HEDGING                │
│ Hedge Mode: Enabled              │
│ Leverage: 10x                    │
│ Testnet: No                      │
│ Bar Type: 1-MINUTE-LAST          │
│                                  │
│ Press CTRL-C to shutdown         │
└──────────────────────────────────┘

[Strategy logs will appear here...]
```

---

## Operational Controls

When running with `--enable-ops`, the system exposes metrics and runtime controls.

### Prometheus Metrics

Metrics are exposed at `http://localhost:8000/metrics`:

```bash
# View all metrics
curl http://localhost:8000/metrics

# Key metrics include:
# - hedge_grid_long_inventory_usdt
# - hedge_grid_short_inventory_usdt
# - hedge_grid_net_position_usdt
# - hedge_grid_funding_1h_usdt
# - hedge_grid_funding_8h_usdt
# - hedge_grid_total_fills
# - hedge_grid_maker_fills
# - hedge_grid_taker_fills
# - hedge_grid_maker_fill_rate
```

### REST API Endpoints

API is available at `http://localhost:8080/api/v1/`:

**Get Operational Metrics**:
```bash
curl http://localhost:8080/api/v1/metrics

# Response:
{
  "long_inventory_usdt": 1250.50,
  "short_inventory_usdt": -800.30,
  "net_position_usdt": 450.20,
  "funding_1h_usdt": -0.15,
  "funding_8h_usdt": -1.20,
  "total_fills": 145,
  "maker_fills": 98,
  "taker_fills": 47,
  "maker_fill_rate": 0.676
}
```

**Get Current Ladders**:
```bash
curl http://localhost:8080/api/v1/ladders

# Response:
{
  "timestamp": "2025-01-17T14:30:45.123456",
  "long_ladder": {
    "side": "LONG",
    "rungs": [
      {"price": 45000.0, "qty": 0.001, "rung_index": 0},
      {"price": 44900.0, "qty": 0.0011, "rung_index": 1},
      ...
    ]
  },
  "short_ladder": {
    "side": "SHORT",
    "rungs": [...]
  }
}
```

**Set Throttle** (Limit order rate):
```bash
# Set throttle to 50% (half normal order quantity)
curl -X POST http://localhost:8080/api/v1/throttle \
  -H "Content-Type: application/json" \
  -d '{"throttle": 0.5}'

# Disable throttle (100% normal)
curl -X POST http://localhost:8080/api/v1/throttle \
  -H "Content-Type: application/json" \
  -d '{"throttle": 1.0}'
```

**Flatten Position** (Emergency close):
```bash
# Flatten LONG side
curl -X POST http://localhost:8080/api/v1/flatten \
  -H "Content-Type: application/json" \
  -d '{"side": "LONG"}'

# Flatten SHORT side
curl -X POST http://localhost:8080/api/v1/flatten \
  -H "Content-Type: application/json" \
  -d '{"side": "SHORT"}'

# Flatten both sides
curl -X POST http://localhost:8080/api/v1/flatten \
  -H "Content-Type: application/json" \
  -d '{"side": "BOTH"}'
```

### Additional CLI Commands

**Get Strategy Status**:
```bash
uv run python -m naut_hedgegrid status
```

**Query Metrics**:
```bash
uv run python -m naut_hedgegrid metrics
```

**Emergency Flatten**:
```bash
# Flatten specific side
uv run python -m naut_hedgegrid flatten --side LONG
uv run python -m naut_hedgegrid flatten --side SHORT

# Flatten both sides
uv run python -m naut_hedgegrid flatten --side BOTH
```

---

## Testnet Trading

Test live order execution on Binance Testnet (fake money, real order matching).

### Setup

1. **Create Binance Testnet Account**
   - Visit https://testnet.binancefuture.com
   - Create account and generate testnet API keys

2. **Update Venue Config**

Create `configs/venues/binance_testnet.yaml`:

```yaml
name: BINANCE
venue_type: EXCHANGE
account_type: MARGIN
base_currency: USDT

api:
  api_key: ${BINANCE_API_KEY}
  api_secret: ${BINANCE_API_SECRET}
  testnet: true
  base_url: "https://testnet.binancefuture.com"

trading:
  hedge_mode: true
  leverage: 10
  margin_type: CROSSED

instruments:
  - symbol: BTCUSDT
    instrument_type: PERPETUAL
```

3. **Set Environment Variables**

```bash
export BINANCE_API_KEY=testnet_api_key_here
export BINANCE_API_SECRET=testnet_api_secret_here
```

4. **Run Live Mode with Testnet Config**

```bash
uv run python -m naut_hedgegrid live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_testnet.yaml
```

Output will show `Testnet: Yes` in the status panel.

---

## Configuration Tuning

### Strategy Parameters

Edit `configs/strategies/hedge_grid_v1.yaml`:

```yaml
# Grid spacing and levels
grid:
  grid_step_bps: 25.0       # 0.25% between levels (adjust for volatility)
  grid_levels_long: 10       # Levels below mid price
  grid_levels_short: 10      # Levels above mid price
  base_qty: 0.001           # Base order size (BTC)
  qty_scale: 1.1            # Quantity multiplier per level

# Exit parameters
exit:
  tp_steps: 2    # Take profit after 2 grid steps (50 bps for 25bp grid)
  sl_steps: 8    # Stop loss after 8 grid steps (200 bps for 25bp grid)

# Regime detection
regime:
  adx_len: 14              # ADX period for trend strength
  adx_smooth: 14           # ADX smoothing
  adx_threshold: 25.0      # Trend vs sideways threshold
  ema_fast: 12             # Fast EMA period
  ema_slow: 26             # Slow EMA period
  atr_len: 14              # ATR period
  atr_multiplier: 1.5      # ATR multiplier for volatility bands

# Placement policy
placement:
  max_open_orders_per_side: 20   # Max open orders per side
  min_edge_bps: 5.0              # Minimum edge required (bps)
  max_inventory_usdt: 50000.0    # Max inventory per side

# Funding rate guard
funding:
  threshold_1h_bps: 10.0     # 1h funding threshold (bps)
  threshold_8h_bps: 100.0    # 8h funding threshold (bps)
  mode: "warn"               # "warn", "reduce", or "halt"
```

### Venue Parameters

Edit `configs/venues/binance_futures.yaml`:

```yaml
trading:
  hedge_mode: true          # Enable hedge mode (separate LONG/SHORT positions)
  leverage: 10              # Default leverage (1-125x)
  margin_type: CROSSED      # CROSSED or ISOLATED

risk:
  max_leverage: 20          # Hard limit on leverage
  min_order_size_usdt: 5.0  # Minimum order notional
  max_order_size_usdt: 100000.0
  max_position_size_usdt: 500000.0
```

---

## Troubleshooting

### Issue: No bars received

**Symptoms**: Strategy starts but no "Bar:" log messages appear.

**Possible Causes**:
1. Network connection to Binance interrupted
2. Instrument symbol incorrect
3. Subscription not configured properly

**Solutions**:
```bash
# 1. Verify network connectivity
curl https://fapi.binance.com/fapi/v1/ping

# 2. Check instrument exists
curl https://fapi.binance.com/fapi/v1/exchangeInfo | grep BTCUSDT

# 3. Review strategy logs for subscription errors
# Look for: "Subscribed to bars" or "Failed to subscribe"

# 4. Restart with verbose logging
uv run python -m naut_hedgegrid paper --enable-ops
```

### Issue: API key errors

**Symptoms**:
- "API key invalid"
- "Signature invalid"
- "IP not whitelisted"

**Solutions**:
```bash
# 1. Verify environment variables
echo $BINANCE_API_KEY
echo $BINANCE_API_SECRET

# 2. Test API access directly
curl -H "X-MBX-APIKEY: $BINANCE_API_KEY" \
  https://fapi.binance.com/fapi/v2/account

# 3. Check API key permissions
# Go to: https://www.binance.com/en/my/settings/api-management
# Ensure: "Enable Futures" is checked

# 4. Check IP whitelist (if enabled)
# Add your IP or disable restriction

# 5. Regenerate API keys if needed
```

### Issue: Orders rejected

**Symptoms**:
- "Order rejected: insufficient margin"
- "Order rejected: post-only would cross"
- "Order rejected: notional too small"

**Solutions**:

**Insufficient Margin**:
```bash
# Check account balance on Binance Futures UI
# Transfer funds from Spot to Futures wallet
# Reduce position sizes in strategy config
```

**Post-Only Rejections** (Expected behavior):
```
# This is NORMAL - strategy uses post-only orders with retry logic
# Orders are automatically adjusted and retried
# Look for: "Retry attempt X/3" in logs
```

**Notional Too Small**:
```yaml
# Increase base_qty in strategy config
# configs/strategies/hedge_grid_v1.yaml
grid:
  base_qty: 0.002  # Increase from 0.001
```

### Issue: Hedge mode not enabled

**Symptoms**:
- "Hedge mode not enabled on account"
- "Position mode mismatch"

**Solution**:
```
1. Go to Binance Futures
2. Click user icon → Settings
3. Position Mode → Hedge Mode
4. Confirm change
5. Restart strategy
```

### Issue: Strategy not placing orders

**Symptoms**: Strategy receives bars but doesn't place orders.

**Possible Causes**:
1. Regime detector not warm yet
2. Grid recentering threshold not met
3. Placement policy filters blocking orders
4. Components not initialized

**Solutions**:

**1. Wait for warmup**:
```
# Check logs for:
Regime detector not warm yet (bars: 23/50)

# Wait until:
Regime detector warm (bars: 50/50)
```

**2. Check grid recentering**:
```
# Grid only recenters when mid price moves significantly
# Check logs for:
Mid price moved X%, recentering grid

# If no movement, wait for price action
```

**3. Review placement policy**:
```yaml
# configs/strategies/hedge_grid_v1.yaml
placement:
  max_open_orders_per_side: 20   # Increase if too restrictive
  min_edge_bps: 5.0              # Reduce if too strict
```

**4. Check initialization**:
```
# Look for warning:
Strategy not fully initialized, skipping bar

# This indicates missing components - restart strategy
```

### Issue: High funding costs

**Symptoms**: Funding payments are eroding profits.

**Solution**:
```yaml
# Tighten funding guard settings
# configs/strategies/hedge_grid_v1.yaml
funding:
  threshold_1h_bps: 5.0      # Reduce from 10.0
  threshold_8h_bps: 50.0     # Reduce from 100.0
  mode: "reduce"             # Change from "warn" to "reduce"

# Or halt trading during high funding:
funding:
  mode: "halt"
```

---

## Emergency Procedures

### Stop Trading Immediately

**Method 1: Graceful Shutdown (Recommended)**
```bash
# Press CTRL-C in terminal running the strategy
^C
# Wait for "Shutdown complete" message
```

**Method 2: Force Kill**
```bash
# Find process ID
ps aux | grep naut_hedgegrid

# Send SIGTERM
kill <pid>

# If unresponsive, force kill (use as last resort)
kill -9 <pid>
```

**Method 3: API Shutdown** (if `--enable-ops` is running)
```bash
# Use flatten endpoint to close all positions
curl -X POST http://localhost:8080/api/v1/flatten \
  -H "Content-Type: application/json" \
  -d '{"side": "BOTH"}'

# Then stop the process with CTRL-C
```

### Cancel All Orders

If strategy doesn't stop cleanly, cancel orders via Binance API:

```bash
# Using curl
curl -X DELETE "https://fapi.binance.com/fapi/v1/allOpenOrders?symbol=BTCUSDT" \
  -H "X-MBX-APIKEY: $BINANCE_API_KEY"

# Or use Binance UI:
# 1. Go to Binance Futures
# 2. Click "Open Orders" tab
# 3. Click "Cancel All"
```

### Close All Positions

**Method 1: Binance UI (Fastest)**
```
1. Go to Binance Futures
2. Click "Positions" tab
3. Click "Close All Positions"
4. Confirm with market orders
```

**Method 2: Via CLI** (if strategy is still running)
```bash
uv run python -m naut_hedgegrid flatten --side BOTH
```

**Method 3: Manual Market Orders**
```
1. Check position sizes on Binance Futures
2. Place opposite market orders to close
   - Long position: Place SELL market order
   - Short position: Place BUY market order
```

### Circuit Breaker

If experiencing abnormal losses:

1. **Immediate**: Stop strategy (CTRL-C)
2. **Cancel**: Cancel all open orders (Binance UI)
3. **Assess**: Review positions and PnL
4. **Decide**:
   - Close positions if abnormal
   - Keep positions if within strategy expectations
5. **Investigate**: Review logs to understand what happened
6. **Fix**: Address root cause before restarting

---

## Safety Checklist

Before going live with real money:

- [ ] **Backtest**: Run 1+ month of historical data with realistic fees
- [ ] **Paper Trade**: Run 24-48 hours without issues
- [ ] **Testnet**: Test on Binance Testnet with real order execution
- [ ] **Parameters**: Reviewed and understand all strategy parameters
- [ ] **Position Limits**: Set appropriate `max_inventory_usdt` and `max_position_size_usdt`
- [ ] **Risk Controls**: Configured stop-loss (`sl_steps`) and take-profit (`tp_steps`)
- [ ] **Hedge Mode**: Enabled on Binance account
- [ ] **Leverage**: Set conservatively (start with 5-10x, not 125x)
- [ ] **Funding**: Understand funding rate impact and set appropriate guards
- [ ] **Liquidation**: Calculated liquidation price and buffer
- [ ] **Monitoring**: Set up Prometheus/Grafana or similar
- [ ] **Alerts**: Configured alerts for high drawdown, funding, liquidation risk
- [ ] **Emergency Plan**: Practiced emergency shutdown procedures
- [ ] **Capital**: Only trading with capital you can afford to lose

---

## Support

### Documentation
- **Project Guide**: `CLAUDE.md` - Comprehensive project overview
- **Architecture**: `README.md` - System architecture
- **Code Review**: `CODE_REVIEW_REPORT.md` - Production readiness assessment
- **Fixes Applied**: `FIXES_APPLIED.md` - Critical fixes and improvements

### NautilusTrader
- **Docs**: https://docs.nautilustrader.io
- **Discord**: https://discord.gg/nautilustrader
- **GitHub**: https://github.com/nautechsystems/nautilus_trader

### Binance
- **Futures API**: https://binance-docs.github.io/apidocs/futures/en/
- **Testnet**: https://testnet.binancefuture.com
- **Support**: https://www.binance.com/en/support

---

**Document Version**: 2.0
**Last Updated**: 2025-01-17
**Changes**:
- Updated all commands to use unified CLI (`python -m naut_hedgegrid`)
- Added comprehensive backtest guide with data preparation
- Updated API key requirements (now required for paper trading)
- Added operational controls section (`--enable-ops`)
- Added additional CLI commands (flatten, status, metrics)
- Expanded troubleshooting with more scenarios
- Enhanced safety checklist and emergency procedures
