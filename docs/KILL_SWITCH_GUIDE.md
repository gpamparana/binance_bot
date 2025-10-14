# Kill Switch and Risk Management System

## Overview

The kill switch system provides automated emergency controls that protect your trading capital through continuous monitoring and automatic position flattening when safety thresholds are breached.

**Key Features:**
- **Automated Circuit Breakers**: Continuously monitor critical risk metrics
- **Immediate Position Flattening**: Emergency stop with position closure in seconds
- **Multi-Channel Alerts**: Slack and Telegram notifications for critical events
- **Thread-Safe Operation**: Safe concurrent access from live trading strategies
- **Comprehensive Testing**: 72 tests ensure reliability

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     HedgeGridV1 Strategy                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  get_operational_metrics() - Returns risk metrics     │  │
│  │  flatten_side() - Closes positions and cancels orders │  │
│  └───────────────────────────────────────────────────────┘  │
│                            ▲                                 │
└────────────────────────────┼─────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │   KillSwitch    │
                    │  (Background    │
                    │   Monitoring)   │
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │ Circuit Breakers│
                    │  - Drawdown     │
                    │  - Funding Cost │
                    │  - Margin Ratio │
                    │  - Loss Limits  │
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │ AlertManager    │
                    │  - Slack        │
                    │  - Telegram     │
                    └─────────────────┘
```

## Configuration

### Basic Configuration

Create a YAML configuration file or use environment variables:

```yaml
# config/operations.yaml
kill_switch:
  enabled: true
  check_interval_seconds: 5
  max_drawdown_pct: 5.0
  max_funding_cost_bps: 20.0
  max_margin_ratio: 0.80
  max_loss_amount_usdt: 1000.0
  daily_loss_limit_usdt: 500.0

alerts:
  enabled: true
  alert_on_flatten: true
  alert_on_circuit_breaker: true
  alert_on_large_loss: true
  large_loss_threshold_usdt: 100.0
  alert_on_high_funding: true
  high_funding_threshold_bps: 15.0
```

### Environment Variables

Set alert credentials as environment variables:

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
```

### Python Configuration

```python
from naut_hedgegrid.config.operations import KillSwitchConfig, AlertConfig, OperationsConfig

# Kill switch configuration
kill_switch_config = KillSwitchConfig(
    enabled=True,
    check_interval_seconds=5,
    max_drawdown_pct=5.0,          # Trigger at 5% drawdown
    max_funding_cost_bps=20.0,     # Trigger if funding > 20bps/8h
    max_margin_ratio=0.80,         # Trigger at 80% margin usage
    max_loss_amount_usdt=1000.0,   # Trigger at $1000 loss
    daily_loss_limit_usdt=500.0,   # Daily limit (resets at UTC midnight)
)

# Alert configuration
alert_config = AlertConfig(
    enabled=True,
    slack_webhook="https://hooks.slack.com/services/...",
    telegram_token="your_bot_token",
    telegram_chat_id="your_chat_id",
    alert_on_flatten=True,
    alert_on_circuit_breaker=True,
    large_loss_threshold_usdt=100.0,
)
```

## Circuit Breakers

### 1. Drawdown Circuit Breaker

Monitors total PnL (realized + unrealized) and triggers when drawdown exceeds threshold.

**Formula:**
```
drawdown_pct = ((peak_pnl - current_pnl) / abs(peak_pnl)) * 100
```

**Example:**
- Peak PnL: $1000
- Current PnL: -$50
- Drawdown: (1000 - (-50)) / 1000 * 100 = **5.25%**
- **Action**: Flatten positions (exceeds 5.0% threshold)

**Configuration:**
```python
max_drawdown_pct=5.0  # Trigger at 5% drawdown
```

### 2. Funding Cost Circuit Breaker

Monitors projected funding costs and triggers when 8h funding exceeds threshold.

**Formula:**
```
funding_cost_8h = funding_rate_1h * 8 * inventory
funding_cost_bps = (funding_cost_8h / total_inventory) * 10000
```

**Example:**
- Long inventory: $10,000
- Short inventory: $10,000
- Funding rate: 0.03% per hour
- 8h cost: 0.03% * 8 * $10,000 = $24
- Basis points: ($24 / $20,000) * 10000 = **12 bps**
- **Action**: Continue (below 20 bps threshold)

**Configuration:**
```python
max_funding_cost_bps=20.0  # Trigger if funding > 20bps/8h
```

### 3. Margin Ratio Circuit Breaker

Monitors margin utilization and triggers when approaching exchange liquidation threshold.

**Formula:**
```
margin_ratio = margin_used / margin_available
```

**Example:**
- Margin used: $8,000
- Margin available: $10,000
- Margin ratio: 8000 / 10000 = **0.80 (80%)**
- **Action**: Flatten positions (at 80% threshold)

**Configuration:**
```python
max_margin_ratio=0.80  # Trigger at 80% margin usage
```

**⚠️ Warning**: Set below 0.90 to maintain safety buffer from forced liquidation.

### 4. Loss Limit Circuit Breakers

Two types of loss limits:

#### Session Loss Limit
Triggers when total loss exceeds threshold since strategy start.

**Example:**
- Session start PnL: $0
- Current PnL: -$1,200
- Loss: $1,200
- **Action**: Flatten positions (exceeds $1,000 threshold)

**Configuration:**
```python
max_loss_amount_usdt=1000.0  # Session loss limit
```

#### Daily Loss Limit (Optional)
Resets at UTC midnight. More aggressive than session limit.

**Example:**
- Daily start PnL: $0 (reset at midnight)
- Current PnL: -$600
- Loss: $600
- **Action**: Flatten positions (exceeds $500 daily limit)

**Configuration:**
```python
daily_loss_limit_usdt=500.0  # Daily limit (resets at UTC midnight)
```

## Integration with Strategy

### Step 1: Initialize Kill Switch

```python
from naut_hedgegrid.ops import KillSwitch, AlertManager
from naut_hedgegrid.config.operations import KillSwitchConfig, AlertConfig

# Create configurations
kill_switch_config = KillSwitchConfig(
    enabled=True,
    max_drawdown_pct=5.0,
    max_funding_cost_bps=20.0,
    max_margin_ratio=0.80,
    max_loss_amount_usdt=1000.0,
)

alert_config = AlertConfig(
    enabled=True,
    slack_webhook="https://hooks.slack.com/services/...",
)

# Create alert manager
alert_manager = AlertManager(alert_config)

# Create kill switch
kill_switch = KillSwitch(
    strategy=strategy_instance,
    config=kill_switch_config,
    alert_manager=alert_manager,
)

# Attach to strategy
strategy_instance.attach_kill_switch(kill_switch)

# Start monitoring
kill_switch.start_monitoring()
```

### Step 2: Implement Required Strategy Methods

Your strategy must implement these methods:

```python
class HedgeGridV1(Strategy):

    def get_operational_metrics(self) -> dict:
        """
        Return current risk metrics for monitoring.

        Called every check_interval_seconds by kill switch.
        """
        return {
            # Position metrics
            "long_inventory_usdt": self._calculate_inventory("long"),
            "short_inventory_usdt": self._calculate_inventory("short"),

            # Risk metrics
            "margin_ratio": self._get_margin_ratio(),
            "funding_cost_1h_projected_usdt": self._project_funding_cost_1h(),

            # PnL metrics
            "realized_pnl_usdt": self._get_realized_pnl(),
            "unrealized_pnl_usdt": self._get_unrealized_pnl(),
            "total_pnl_usdt": self._get_total_pnl(),
        }

    def flatten_side(self, side: str) -> dict:
        """
        Flatten positions for specified side(s).

        Args:
            side: "long", "short", or "both"

        Returns:
            dict with cancelled_orders and closing_positions
        """
        result = {
            "cancelled_orders": 0,
            "closing_positions": [],
        }

        sides = ["long", "short"] if side == "both" else [side]

        for s in sides:
            # Cancel all orders
            cancelled = self._cancel_side_orders(s)
            result["cancelled_orders"] += cancelled

            # Close position with market order
            position_info = self._close_side_position(s)
            if position_info:
                result["closing_positions"].append(position_info)

        return result
```

### Step 3: Shutdown

```python
# Stop monitoring when strategy stops
kill_switch.stop_monitoring()
```

## Manual Operations

### Manual Flatten

Trigger manual flatten via API or code:

```python
# Flatten both sides
result = kill_switch.flatten_now("both", reason="Manual intervention")

# Flatten long only
result = kill_switch.flatten_now("long", reason="High funding on longs")

# Flatten short only
result = kill_switch.flatten_now("short", reason="Stop out shorts")

# Result structure
{
    "cancelled_orders": 10,
    "closing_positions": [
        {"side": "long", "size": 0.5, "order_id": "HG1-..."},
        {"side": "short", "size": 0.3, "order_id": "HG1-..."},
    ],
    "timestamp": datetime(...),
    "reason": "Manual intervention",
    "status": "completed"
}
```

### Check Status

```python
status = kill_switch.get_status()

# Returns:
{
    "enabled": True,
    "monitoring": True,
    "flatten_in_progress": False,
    "session_start_time": "2025-01-15T10:30:00+00:00",
    "session_start_pnl": 0.0,
    "session_peak_pnl": 1234.56,
    "daily_reset_time": "2025-01-16T00:00:00+00:00",
    "circuit_breakers_triggered": [],
    "config": {
        "max_drawdown_pct": 5.0,
        "max_funding_cost_bps": 20.0,
        # ... full config
    }
}
```

### Reset Circuit Breakers

After addressing issues, manually reset circuit breakers:

```python
kill_switch.reset_circuit_breakers()
```

**⚠️ Use with caution**: Only reset after thoroughly understanding and addressing the underlying issue that triggered the breaker.

## Alert Examples

### Circuit Breaker Alert

```
🚨 [CRITICAL] Circuit Breaker Triggered: Drawdown Exceeded

Timestamp: 2025-01-15 14:23:45 UTC

Additional Details:
  • Breaker Type: Drawdown (Session)
  • Current Value: 5.23%
  • Threshold: 5.00%
  • Action Taken: flatten positions
```

### Position Flatten Alert

```
🚨 [CRITICAL] Position Flattening Executed

Timestamp: 2025-01-15 14:23:46 UTC

Additional Details:
  • Reason: Drawdown (Session) circuit breaker triggered
  • Sides Flattened: long, short
  • Cancelled Orders: 15
  • Positions Closed: 2
  • Position Details: [...]
```

### High Funding Alert

```
⚠️ [WARNING] High Funding Rate Alert: 22.50 bps

Timestamp: 2025-01-15 14:00:00 UTC

Additional Details:
  • Funding Rate Bps: 22.50
  • Projected 8h Cost Usdt: 56.25
  • Instrument: BTCUSDT-PERP
  • Threshold Bps: 15.00
```

## Best Practices

### 1. Conservative Thresholds

Start with conservative thresholds and gradually adjust based on strategy performance:

```python
# Conservative (recommended for live trading)
KillSwitchConfig(
    max_drawdown_pct=3.0,      # 3% drawdown
    max_margin_ratio=0.70,     # 70% margin usage
    max_loss_amount_usdt=500.0, # $500 loss
)

# Moderate
KillSwitchConfig(
    max_drawdown_pct=5.0,      # 5% drawdown
    max_margin_ratio=0.80,     # 80% margin usage
    max_loss_amount_usdt=1000.0, # $1000 loss
)

# Aggressive (higher risk)
KillSwitchConfig(
    max_drawdown_pct=10.0,     # 10% drawdown
    max_margin_ratio=0.85,     # 85% margin usage
    max_loss_amount_usdt=2000.0, # $2000 loss
)
```

### 2. Test in Paper Trading

Always test kill switch configuration in paper trading before live deployment:

```python
# Paper trading configuration
paper_config = KillSwitchConfig(
    enabled=True,
    check_interval_seconds=5,
    max_drawdown_pct=10.0,  # More relaxed for testing
    # ... other params
)
```

### 3. Monitor Alert Channels

Ensure alerts are working before going live:

```python
# Send test alert
alert_manager.send_alert(
    message="Kill switch test - please confirm receipt",
    severity=AlertSeverity.INFO,
)
```

### 4. Daily Review

Review kill switch status daily:

```python
status = kill_switch.get_status()
print(f"Triggered breakers: {status['circuit_breakers_triggered']}")
print(f"Session peak: ${status['session_peak_pnl']:.2f}")
print(f"Daily peak: ${status['daily_peak_pnl']:.2f}")
```

### 5. Backtest Validation

Validate kill switch behavior using historical data:

```python
from naut_hedgegrid.runners import BacktestRunner

# Run backtest with kill switch enabled
runner = BacktestRunner(
    strategy_config=strategy_config,
    ops_config=ops_config,  # Include kill switch config
    # ... other params
)

results = runner.run()

# Analyze how often circuit breakers would have triggered
print(f"Circuit breaker triggers: {results.circuit_breaker_count}")
print(f"Max drawdown: {results.max_drawdown:.2%}")
```

## Troubleshooting

### Circuit Breaker Triggers Too Frequently

**Problem**: Circuit breakers trigger multiple times per day.

**Solutions**:
1. Increase thresholds (e.g., `max_drawdown_pct` from 5.0% to 7.0%)
2. Review strategy parameters (may be too aggressive)
3. Check market conditions (high volatility periods require wider thresholds)

### Alerts Not Received

**Problem**: No alerts when circuit breakers trigger.

**Solutions**:
1. Verify environment variables are set:
   ```bash
   echo $SLACK_WEBHOOK_URL
   echo $TELEGRAM_BOT_TOKEN
   echo $TELEGRAM_CHAT_ID
   ```

2. Test alert channels:
   ```python
   alert_manager.send_alert("Test alert", AlertSeverity.INFO)
   ```

3. Check logs for alert errors:
   ```python
   tail -f logs/kill_switch.log | grep -i "alert"
   ```

### Flatten Fails to Execute

**Problem**: Circuit breaker triggers but positions not closed.

**Solutions**:
1. Check strategy implements `flatten_side()` correctly
2. Verify exchange connectivity
3. Review logs for order submission errors
4. Test flatten manually:
   ```python
   result = strategy.flatten_side("both")
   print(result)
   ```

### High Memory Usage

**Problem**: Kill switch monitoring thread uses excessive memory.

**Solutions**:
1. Increase `check_interval_seconds` (default: 5s)
2. Reduce metric collection frequency
3. Monitor with:
   ```python
   import tracemalloc
   tracemalloc.start()
   # ... run strategy ...
   snapshot = tracemalloc.take_snapshot()
   top_stats = snapshot.statistics('lineno')
   for stat in top_stats[:10]:
       print(stat)
   ```

## Testing

Run comprehensive test suite:

```bash
# Run all ops tests
pytest tests/ops/ -v

# Run only kill switch tests
pytest tests/ops/test_kill_switch.py -v

# Run with coverage
pytest tests/ops/ --cov=naut_hedgegrid.ops --cov-report=html

# Run integration tests
pytest tests/ops/test_kill_switch.py::TestIntegration -v
```

## File Structure

```
src/naut_hedgegrid/
├── ops/
│   ├── __init__.py           # Package exports
│   ├── kill_switch.py        # KillSwitch class (500+ lines)
│   ├── alerts.py             # AlertManager class (400+ lines)
│   └── prometheus.py         # Metrics exporter (existing)
├── config/
│   └── operations.py         # Configuration models (200+ lines)
└── strategies/
    └── hedge_grid_v1/
        └── strategy.py       # Strategy integration (1000+ lines)

tests/
└── ops/
    ├── test_kill_switch.py   # Kill switch tests (27 tests)
    ├── test_alerts.py        # Alert tests (25 tests)
    └── test_prometheus.py    # Prometheus tests (existing)
```

## References

- **Source Code**: `src/naut_hedgegrid/ops/`
- **Configuration**: `src/naut_hedgegrid/config/operations.py`
- **Tests**: `tests/ops/`
- **Strategy Integration**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py` (lines 747-1061)

---

**Version**: 1.0.0
**Last Updated**: 2025-01-15
**Maintainer**: naut-hedgegrid project
