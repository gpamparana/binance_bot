"""
Example integration of kill switch and alert system with HedgeGridV1 strategy.

This example demonstrates how to:
1. Configure kill switch and alert system
2. Integrate with live trading strategy
3. Monitor risk metrics
4. Handle manual interventions
"""

from naut_hedgegrid.config.operations import AlertConfig, KillSwitchConfig
from naut_hedgegrid.ops import AlertManager
from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config


def create_kill_switch_config() -> KillSwitchConfig:
    """
    Create kill switch configuration with conservative thresholds.

    These are recommended starting values for live trading. Adjust based on:
    - Account size
    - Risk tolerance
    - Strategy characteristics
    - Market volatility
    """
    return KillSwitchConfig(
        enabled=True,
        # Check circuit breakers every 5 seconds
        check_interval_seconds=5,
        # Flatten if drawdown exceeds 5%
        max_drawdown_pct=5.0,
        # Flatten if projected 8h funding cost exceeds 20 bps
        max_funding_cost_bps=20.0,
        # Flatten if margin usage exceeds 80% (20% buffer from liquidation)
        max_margin_ratio=0.80,
        # Flatten if session loss exceeds $1000
        max_loss_amount_usdt=1000.0,
        # Flatten if daily loss exceeds $500 (resets at UTC midnight)
        daily_loss_limit_usdt=500.0,
    )


def create_alert_config() -> AlertConfig:
    """
    Create alert configuration with multi-channel notifications.

    Environment variables required:
    - SLACK_WEBHOOK_URL: Slack incoming webhook URL
    - TELEGRAM_BOT_TOKEN: Telegram bot API token
    - TELEGRAM_CHAT_ID: Telegram chat/channel ID
    """
    return AlertConfig(
        enabled=True,
        # Credentials loaded from environment variables
        slack_webhook=None,  # Reads SLACK_WEBHOOK_URL
        telegram_token=None,  # Reads TELEGRAM_BOT_TOKEN
        telegram_chat_id=None,  # Reads TELEGRAM_CHAT_ID
        # Alert configuration
        alert_on_flatten=True,
        alert_on_circuit_breaker=True,
        alert_on_large_loss=True,
        large_loss_threshold_usdt=100.0,
        alert_on_high_funding=True,
        high_funding_threshold_bps=15.0,
    )


def main():
    """
    Main execution example showing kill switch integration.

    This demonstrates a complete setup from configuration to monitoring.
    """
    print("=" * 70)
    print("Kill Switch Integration Example")
    print("=" * 70)
    print()

    # =========================================================================
    # STEP 1: Create configurations
    # =========================================================================
    print("Step 1: Creating configurations...")

    kill_switch_config = create_kill_switch_config()
    alert_config = create_alert_config()

    print(f"  ✓ Kill switch enabled: {kill_switch_config.enabled}")
    print(f"  ✓ Max drawdown: {kill_switch_config.max_drawdown_pct}%")
    print(f"  ✓ Max funding cost: {kill_switch_config.max_funding_cost_bps} bps")
    print(f"  ✓ Max margin ratio: {kill_switch_config.max_margin_ratio:.0%}")
    print()

    # Check alert channels
    if alert_config.has_slack_configured():
        print("  ✓ Slack alerts configured")
    else:
        print("  ⚠ Slack not configured (set SLACK_WEBHOOK_URL)")

    if alert_config.has_telegram_configured():
        print("  ✓ Telegram alerts configured")
    else:
        print("  ⚠ Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

    print()

    # =========================================================================
    # STEP 2: Create strategy configuration
    # =========================================================================
    print("Step 2: Creating strategy configuration...")

    strategy_config = HedgeGridV1Config(
        instrument_id="BTCUSDT-PERP.BINANCE",
        bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
        hedge_grid_config_path="config/hedge_grid.yaml",
    )

    print(f"  ✓ Instrument: {strategy_config.instrument_id}")
    print(f"  ✓ Bar type: {strategy_config.bar_type}")
    print()

    # =========================================================================
    # STEP 3: Initialize components
    # =========================================================================
    print("Step 3: Initializing components...")

    # Create alert manager
    alert_manager = AlertManager(alert_config)
    print("  ✓ Alert manager initialized")

    # In production, you would create the strategy via TradingNode
    # For this example, we'll show the integration pattern

    """
    # Create trading node
    node = TradingNode(config=node_config)

    # Get strategy instance
    strategy = node.trader.strategy_states()[0]

    # Create kill switch
    kill_switch = KillSwitch(
        strategy=strategy,
        config=kill_switch_config,
        alert_manager=alert_manager,
    )

    # Attach to strategy
    strategy.attach_kill_switch(kill_switch)

    # Start monitoring
    kill_switch.start_monitoring()
    print("  ✓ Kill switch monitoring started")
    """

    print("  ✓ Components initialized (see code comments for full setup)")
    print()

    # =========================================================================
    # STEP 4: Send startup alert
    # =========================================================================
    print("Step 4: Sending startup alert...")

    if alert_config.enabled and alert_config.has_any_channel():
        alert_manager.send_startup_alert(
            strategy_name="HedgeGridV1",
            config_path="config/hedge_grid.yaml",
        )
        print("  ✓ Startup alert sent to configured channels")
    else:
        print("  ⚠ Alerts not configured, skipping startup notification")

    print()

    # =========================================================================
    # STEP 5: Monitor and control examples
    # =========================================================================
    print("Step 5: Monitoring and control examples...")
    print()

    print("Manual Flatten Example:")
    print("  # Flatten both sides")
    print("  result = kill_switch.flatten_now('both', reason='Manual intervention')")
    print()

    print("Status Check Example:")
    print("  # Check kill switch status")
    print("  status = kill_switch.get_status()")
    print("  print(f\"Monitoring: {status['monitoring']}\")")
    print("  print(f\"Peak PnL: ${status['session_peak_pnl']:.2f}\")")
    print()

    print("Circuit Breaker Reset Example:")
    print("  # After fixing issues, reset breakers")
    print("  kill_switch.reset_circuit_breakers()")
    print()

    # =========================================================================
    # Summary
    # =========================================================================
    print("=" * 70)
    print("Integration Complete!")
    print("=" * 70)
    print()
    print("Next Steps:")
    print("  1. Configure environment variables for alerts")
    print("  2. Test in paper trading with relaxed thresholds")
    print("  3. Review kill switch behavior in backtest")
    print("  4. Gradually tighten thresholds based on strategy performance")
    print("  5. Monitor circuit breaker triggers and adjust as needed")
    print()
    print("Documentation:")
    print("  - Full guide: docs/KILL_SWITCH_GUIDE.md")
    print("  - Configuration: src/naut_hedgegrid/config/operations.py")
    print("  - Tests: tests/ops/test_kill_switch.py")
    print()


def example_custom_circuit_breaker():
    """
    Example of creating custom circuit breaker logic.

    You can extend the KillSwitch class or create custom monitors.
    """
    print("Custom Circuit Breaker Example")
    print("-" * 70)
    print()

    print("Scenario: Trigger flatten if BTC price drops >5% in 5 minutes")
    print()

    code_example = """
class CustomKillSwitch(KillSwitch):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._price_history = []
        self._price_check_interval = 60  # Check every minute

    def _check_safety_circuits(self):
        # Call parent circuit breakers first
        super()._check_safety_circuits()

        # Add custom price drop circuit
        self._check_price_drop_circuit()

    def _check_price_drop_circuit(self):
        current_price = self._get_current_price()
        self._price_history.append({
            'price': current_price,
            'timestamp': datetime.now(tz=UTC),
        })

        # Keep only last 5 minutes
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=5)
        self._price_history = [
            p for p in self._price_history
            if p['timestamp'] > cutoff
        ]

        if len(self._price_history) < 2:
            return

        # Calculate price drop
        oldest_price = self._price_history[0]['price']
        price_drop_pct = ((oldest_price - current_price) / oldest_price) * 100

        if price_drop_pct > 5.0:
            self._trigger_circuit_breaker(
                breaker_type="Price Drop (5min)",
                current_value=price_drop_pct,
                threshold=5.0,
                unit="%",
            )
    """

    print(code_example)
    print()


def example_alert_customization():
    """
    Example of customizing alert messages and channels.
    """
    print("Alert Customization Example")
    print("-" * 70)
    print()

    print("Custom alert with rich data:")
    print()

    code_example = """
# Create alert manager
alert_manager = AlertManager(alert_config)

# Send custom alert with detailed data
alert_manager.send_alert(
    message="High volatility detected - monitoring closely",
    severity=AlertSeverity.WARNING,
    extra_data={
        "current_volatility": 45.2,
        "normal_volatility": 25.0,
        "symbol": "BTCUSDT-PERP",
        "action": "Reduced position size by 50%",
        "timestamp": datetime.now(tz=UTC).isoformat(),
    },
)

# Result: Formatted alert sent to all configured channels with:
# - Emoji based on severity (⚠️ for WARNING)
# - Structured data display
# - Automatic timestamp
# - Channel-specific formatting (Slack attachments, Telegram HTML)
    """

    print(code_example)
    print()


if __name__ == "__main__":
    # Run main example
    main()

    print()
    print("=" * 70)
    print()

    # Show additional examples
    example_custom_circuit_breaker()
    print()
    example_alert_customization()
