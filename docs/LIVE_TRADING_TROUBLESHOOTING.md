# Live Trading Troubleshooting Guide

## Issue Summary

You tried to run live trading with:
```bash
uv run python -m naut_hedgegrid live --enable-ops 2>&1 | tee live_logs.log
```

But encountered **403 Forbidden errors** and **no instruments loaded**. This happened because:

1. ❌ **Connected to PRODUCTION instead of TESTNET** - Your config was pointing to `https://fapi.binance.com` (real money) instead of `https://testnet.binancefuture.com` (fake money)
2. ❌ **Used PRODUCTION API keys** - These don't work on testnet
3. ⚠️  **Instrument loading warning** - InstrumentProvider didn't load instruments (due to 403 errors)

## Root Cause Analysis

### Error 1: 403 Forbidden from Binance

```
[ERROR] LIVE-001.ExecClient-BINANCE: Error on connect: <!DOCTYPE HTML...
<H1>403 ERROR</H1>
Request blocked.
```

**Why**: Your production API keys (`BINANCE_API_KEY`/`BINANCE_API_SECRET`) were being sent to production Binance servers. CloudFront blocked the requests, likely because:
- API keys are restricted by IP address
- Rate limiting
- Invalid permissions

### Error 2: No Instruments Loaded

```
[WARN] No loading configured: ensure either `load_all=True` or there are `load_ids`
[ERROR] Failed to load instruments
[ERROR] Instrument BTCUSDT-PERP.BINANCE not found in cache
```

**Why**: The instrument loading failed due to the 403 errors above. Without instruments, the strategy can't trade.

## Solution: Use Testnet

### Step 1: Get Testnet API Keys

1. Go to **https://testnet.binancefuture.com**
2. Click "Log in with GitHub" (or create account)
3. Once logged in, go to **API Keys** section
4. Generate new API key + secret
5. **Save both** - you won't see the secret again!

### Step 2: Configure Environment Variables

Update your `.env` file with testnet credentials:

```bash
# Edit .env file
nano .env

# Add testnet keys (replace with your actual keys from step 1)
BINANCE_TESTNET_API_KEY=your_testnet_key_here
BINANCE_TESTNET_API_SECRET=your_testnet_secret_here
```

**Load the environment**:
```bash
set -a; source .env; set +a
```

### Step 3: Run with Testnet Configuration

```bash
# Use the TESTNET venue config
uv run python -m naut_hedgegrid live \
    --venue-config configs/venues/binance_futures_testnet.yaml \
    --enable-ops \
    2>&1 | tee live_logs_testnet.log
```

**Important**: Notice the `--venue-config` flag pointing to `binance_futures_testnet.yaml`!

## Verification

You should see:
```
[INFO] Base url HTTP https://testnet.binancefuture.com/
[INFO] Base url WebSocket wss://fstream.binancefuture.com
[INFO] BinanceFuturesInstrumentProvider: Loading all instruments...
[INFO] Loaded X instruments
```

✅ **No 403 errors**
✅ **Instruments loaded successfully**
✅ **Strategy starts without errors**

## Common Mistakes

### ❌ Mistake 1: Using Production Config by Default

```bash
# WRONG - This uses production (real money)
uv run python -m naut_hedgegrid live --enable-ops
```

**Fix**: Always specify testnet config:
```bash
# RIGHT - This uses testnet (fake money)
uv run python -m naut_hedgegrid live \
    --venue-config configs/venues/binance_futures_testnet.yaml \
    --enable-ops
```

### ❌ Mistake 2: Using Production API Keys on Testnet

**Wrong `.env`**:
```
BINANCE_TESTNET_API_KEY=${BINANCE_API_KEY}  # NO! These are different!
```

**Correct `.env`**:
```
# Testnet keys (from testnet.binancefuture.com)
BINANCE_TESTNET_API_KEY=abc123...
BINANCE_TESTNET_API_SECRET=xyz789...

# Production keys (from binance.com) - DIFFERENT!
BINANCE_API_KEY=prod_key...
BINANCE_API_SECRET=prod_secret...
```

### ❌ Mistake 3: Forgetting to Source .env

```bash
# WRONG - env vars not loaded
uv run python -m naut_hedgegrid live --venue-config configs/venues/binance_futures_testnet.yaml

# RIGHT - env vars loaded first
set -a; source .env; set +a
uv run python -m naut_hedgegrid live --venue-config configs/venues/binance_futures_testnet.yaml
```

## Configuration Files

### Testnet Config (`configs/venues/binance_futures_testnet.yaml`)

```yaml
api:
  api_key: ${BINANCE_TESTNET_API_KEY}      # From testnet
  api_secret: ${BINANCE_TESTNET_API_SECRET}  # From testnet
  testnet: true                              # CRITICAL!
  base_url: https://testnet.binancefuture.com
```

### Production Config (`configs/venues/binance_futures.yaml`)

```yaml
api:
  api_key: ${BINANCE_API_KEY}            # From production
  api_secret: ${BINANCE_API_SECRET}      # From production
  testnet: false                          # Production mode
  base_url: https://fapi.binance.com
```

## Testing Your Setup

### 1. Check Environment Variables

```bash
echo "Testnet Key: ${BINANCE_TESTNET_API_KEY:0:10}..."
echo "Testnet Secret: ${BINANCE_TESTNET_API_SECRET:0:10}..."
```

Both should show your keys (first 10 chars). If empty, run `set -a; source .env; set +a`.

### 2. Test Testnet Connection

```bash
# This should connect without 403 errors
uv run python -m naut_hedgegrid live \
    --venue-config configs/venues/binance_futures_testnet.yaml \
    --enable-ops \
    2>&1 | tee test.log

# Check for success
grep "403 ERROR" test.log && echo "FAILED - Still getting 403" || echo "SUCCESS - No 403 errors"
grep "Instrument.*not found" test.log && echo "FAILED - Instruments not loaded" || echo "SUCCESS - Instruments loaded"
```

### 3. Verify Testnet Orders

1. Log in to **https://testnet.binancefuture.com**
2. Go to **Orders** section
3. You should see orders appear (with FAKE money)
4. Check **Positions** to see your strategy's positions

## When to Use Production

⚠️  **ONLY use production after**:

1. ✅ Thoroughly tested on testnet (weeks/months)
2. ✅ Strategy is profitable on testnet
3. ✅ Risk management verified (stop losses, position limits)
4. ✅ You understand all potential losses
5. ✅ You're ready to risk REAL money

**To use production**:
```bash
# Get production API keys from binance.com (NOT testnet)
# Update .env with production keys
# Run with production config
uv run python -m naut_hedgegrid live \
    --venue-config configs/venues/binance_futures.yaml \
    --enable-ops
```

## Quick Reference

| Mode | Config File | API Keys | URL | Money |
|------|-------------|----------|-----|-------|
| **Testnet** | `binance_futures_testnet.yaml` | `BINANCE_TESTNET_*` | `testnet.binancefuture.com` | Fake |
| **Production** | `binance_futures.yaml` | `BINANCE_API_*` | `fapi.binance.com` | **REAL** |

## Still Having Issues?

### Check Logs

```bash
# Look for these patterns
grep "ERROR\|403\|WARN" live_logs.log

# Check what URL is being used
grep "Base url" live_logs.log
```

**Should see**:
```
Base url HTTP https://testnet.binancefuture.com/  ← TESTNET
```

**NOT**:
```
Base url HTTP https://fapi.binance.com/  ← PRODUCTION!
```

### Instrument Loading Issues

If you see `[WARN] No loading configured`, check:

1. Testnet API keys are valid
2. No 403 errors in logs
3. Using correct venue config file

The `load_all=True` is already configured in `base_runner.py:276`, so this should work automatically once API authentication succeeds.

## Summary

**What you need to do**:

1. Get testnet API keys from https://testnet.binancefuture.com
2. Add them to `.env` as `BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET`
3. Source the env file: `set -a; source .env; set +a`
4. Run with testnet config: `uv run python -m naut_hedgegrid live --venue-config configs/venues/binance_futures_testnet.yaml --enable-ops`

**That's it!** You should now be able to test your strategy with fake money on Binance testnet.
