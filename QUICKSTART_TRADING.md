# Quick Start Guide - Paper & Live Trading

## Prerequisites

1. **Python Environment**
   - Python 3.10+
   - `uv` package manager installed

2. **Configuration Files**
   - `configs/strategies/hedge_grid_v1.yaml` (strategy parameters)
   - `configs/venues/binance_futures.yaml` (venue settings)

3. **API Keys** (for live trading only)
   - Binance API key
   - Binance API secret
   - Enable Futures trading on account
   - Enable hedge mode (if desired)

## Paper Trading (Simulated Execution)

### Quick Start

```bash
# From project root
uv run python -m naut_hedgegrid.runners.run_paper
```

### With Custom Configs

```bash
uv run python -m naut_hedgegrid.runners.run_paper \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml
```

### Expected Output

```
═══════════════════════ Paper Trading Runner ═══════════════════════
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
│ Instrument: BTCUSDT-PERP... │
│ OMS Type: HEDGING            │
│ Bar Type: 1-MINUTE-LAST      │
│                              │
│ Press CTRL-C to shutdown     │
└──────────────────────────────┘

[Strategy logs will appear here...]
```

### Monitoring

Watch for these key events:
1. **Bar reception**: "Bar: close=XXX, regime=XXX"
2. **Warmup**: "Regime detector not warm yet"
3. **First orders**: "Built X ladder(s)"
4. **Order fills**: "Order filled: XXX"

### Shutdown

Press `CTRL-C` to stop:
```
Shutdown signal received
Shutting down...
✓ Node stopped
✓ Shutdown complete
```

## Live Trading (Real Execution)

### ⚠️ WARNING

**Live trading uses REAL MONEY. Test thoroughly in paper mode first.**

### Setup API Keys

```bash
# Add to ~/.bashrc or ~/.zshrc
export BINANCE_API_KEY=your_api_key_here
export BINANCE_API_SECRET=your_api_secret_here

# Reload shell
source ~/.bashrc  # or source ~/.zshrc
```

### Quick Start

```bash
# From project root
uv run python -m naut_hedgegrid.runners.run_live
```

### With Custom Configs

```bash
uv run python -m naut_hedgegrid.runners.run_live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml
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

## Testnet Trading

### Setup

1. **Create Binance Testnet Account**
   - Visit https://testnet.binancefuture.com
   - Create account and get testnet API keys

2. **Update Venue Config**
```yaml
# configs/venues/binance_futures.yaml
api:
  api_key: ${BINANCE_TESTNET_API_KEY}
  api_secret: ${BINANCE_TESTNET_API_SECRET}
  testnet: true  # Enable testnet
  base_url: https://testnet.binancefuture.com
```

3. **Set Environment Variables**
```bash
export BINANCE_API_KEY=testnet_api_key
export BINANCE_API_SECRET=testnet_api_secret
```

4. **Run**
```bash
uv run python -m naut_hedgegrid.runners.run_live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml
```

## Configuration Tuning

### Strategy Parameters

Edit `configs/strategies/hedge_grid_v1.yaml`:

```yaml
# Grid spacing (basis points)
grid:
  grid_step_bps: 25.0  # 0.25% between levels
  grid_levels_long: 10   # Levels below mid
  grid_levels_short: 10  # Levels above mid
  base_qty: 0.001       # Base order size
  qty_scale: 1.1        # Quantity multiplier per level

# Exit parameters
exit:
  tp_steps: 2  # Take profit after N steps
  sl_steps: 8  # Stop loss after N steps

# Regime detection
regime:
  adx_len: 14   # Trend strength period
  ema_fast: 12  # Fast EMA
  ema_slow: 26  # Slow EMA
  atr_len: 14   # Volatility period
```

### Venue Parameters

Edit `configs/venues/binance_futures.yaml`:

```yaml
# Trading settings
trading:
  hedge_mode: true  # Enable hedge mode
  leverage: 10      # Default leverage
  margin_type: CROSSED  # or ISOLATED

# Risk limits
risk:
  max_leverage: 20
  min_order_size_usdt: 5.0
  max_order_size_usdt: 100000.0
```

## Troubleshooting

### Issue: No bars received

**Check**:
1. Network connection to Binance
2. Instrument symbol is correct
3. Bar subscription in strategy logs

**Solution**:
```bash
# Verify instrument exists
curl https://fapi.binance.com/fapi/v1/exchangeInfo | grep BTCUSDT
```

### Issue: API key errors

**Check**:
1. Environment variables set correctly
2. API keys have futures trading permissions
3. IP whitelist configured (if applicable)

**Solution**:
```bash
# Verify env vars
echo $BINANCE_API_KEY
echo $BINANCE_API_SECRET

# Test API access
curl -H "X-MBX-APIKEY: $BINANCE_API_KEY" \
  https://fapi.binance.com/fapi/v2/account
```

### Issue: Orders rejected

**Check**:
1. Hedge mode enabled on Binance account
2. Sufficient margin available
3. Order size meets minimum notional

**Solution**:
- Enable hedge mode: Binance Futures → Settings → Position Mode → Hedge Mode
- Add margin to futures wallet
- Increase `base_qty` in strategy config

### Issue: Strategy not placing orders

**Check**:
1. Regime detector warmup complete
2. Grid recentering conditions met
3. Placement policy filters

**Solution**:
- Wait for detector warmup (log: "Regime detector not warm yet")
- Check mid price movement for recentering trigger
- Review placement policy settings in config

## Monitoring

### Key Metrics to Watch

1. **Bar Reception**
   - Logs: "Bar: close=XXX"
   - Frequency: Every 1 minute

2. **Regime Detection**
   - Logs: "regime=UP/DOWN/SIDEWAYS"
   - Warmup: ~26-50 bars (regime config dependent)

3. **Grid Updates**
   - Logs: "Built X ladder(s)"
   - Frequency: On price movement

4. **Order Activity**
   - Logs: "Order filled", "Order accepted", "Order canceled"
   - Check for rejections

5. **Position Tracking**
   - Live: Check Binance Futures UI
   - Paper: Check strategy logs

### Log Locations

- **Console**: Real-time output
- **Nautilus logs**: Check TradingNode log configuration

## Safety Checklist

Before live trading:
- [ ] Tested in paper mode for 24+ hours
- [ ] Tested on testnet with real orders
- [ ] Reviewed all strategy parameters
- [ ] Set appropriate position limits
- [ ] Configured risk management
- [ ] Verified hedge mode settings
- [ ] Set stop-loss parameters
- [ ] Prepared shutdown procedure
- [ ] Monitored funding rates
- [ ] Checked liquidation buffers

## Emergency Procedures

### Stop Trading Immediately

```bash
# Press CTRL-C in terminal
# Or send SIGTERM to process
kill -TERM <pid>
```

### Cancel All Orders

```python
# If node doesn't stop cleanly, use Binance API:
import requests

API_KEY = "your_key"
headers = {"X-MBX-APIKEY": API_KEY}

# Cancel all orders
response = requests.delete(
    "https://fapi.binance.com/fapi/v1/allOpenOrders",
    params={"symbol": "BTCUSDT"},
    headers=headers
)
```

### Close All Positions

Use Binance Futures UI:
1. Go to Binance Futures
2. Click "Positions" tab
3. Click "Close All Positions"

## Support

### Documentation
- **Integration Guide**: `TRADINGNODE_INTEGRATION.md`
- **API Reference**: `RUNNER_API_REFERENCE.md`
- **Implementation Details**: `IMPLEMENTATION_SUMMARY.md`

### Nautilus Trader
- Docs: https://nautilustrader.io
- Discord: https://discord.gg/nautilustrader
- GitHub: https://github.com/nautechsystems/nautilus_trader

### Binance
- Futures API: https://binance-docs.github.io/apidocs/futures/en/
- Testnet: https://testnet.binancefuture.com
- Support: https://www.binance.com/en/support

---

**Document Version**: 1.0
**Last Updated**: 2025-10-14
