"""Alert notification system for critical trading events.

This module provides multi-channel alerting (Slack, Telegram) for circuit breakers,
position flattening, and other critical risk events.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

import aiohttp

from naut_hedgegrid.config.operations import AlertConfig


class AlertSeverity(Enum):
    """Alert severity levels for prioritization and formatting."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def emoji(self) -> str:
        """Get emoji prefix for severity level."""
        return {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨",
        }[self]

    @property
    def color(self) -> str:
        """Get color code for Slack attachments."""
        return {
            AlertSeverity.INFO: "#36a64f",  # Green
            AlertSeverity.WARNING: "#ff9900",  # Orange
            AlertSeverity.CRITICAL: "#ff0000",  # Red
        }[self]


class AlertManager:
    """
    Multi-channel alert notification system.

    Sends alerts to configured channels (Slack, Telegram) for critical trading events
    like circuit breaker triggers, position flattening, and large losses.

    Thread-safe and supports both synchronous and asynchronous usage patterns.

    Parameters
    ----------
    config : AlertConfig
        Alert configuration including channel credentials and thresholds

    Attributes
    ----------
    logger : logging.Logger
        Logger for alert system debugging

    Examples
    --------
    >>> from naut_hedgegrid.config.operations import AlertConfig
    >>> config = AlertConfig(
    ...     slack_webhook="https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
    ...     telegram_token="YOUR_BOT_TOKEN",
    ...     telegram_chat_id="YOUR_CHAT_ID",
    ... )
    >>> alert_manager = AlertManager(config)
    >>> alert_manager.send_alert(
    ...     message="Circuit breaker triggered: max drawdown exceeded",
    ...     severity=AlertSeverity.CRITICAL,
    ...     extra_data={"current_dd": 5.2, "threshold": 5.0},
    ... )

    """

    def __init__(self, config: AlertConfig) -> None:
        """
        Initialize alert manager with configuration.

        Args:
            config: Alert configuration with channel credentials

        """
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Validate configuration
        if config.enabled and not config.has_any_channel():
            self.logger.warning(
                "Alert system enabled but no channels configured. "
                "Set SLACK_WEBHOOK_URL or TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID environment variables."
            )

        # Log configured channels
        if config.enabled:
            channels = []
            if config.has_slack_configured():
                channels.append("Slack")
            if config.has_telegram_configured():
                channels.append("Telegram")

            if channels:
                self.logger.info(f"Alert manager initialized with channels: {', '.join(channels)}")

    def send_alert(
        self,
        message: str,
        severity: AlertSeverity | Literal["info", "warning", "critical"] = AlertSeverity.INFO,
        extra_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Send alert to all configured channels (synchronous wrapper).

        This method creates an event loop to run async alert sending. For async contexts,
        use send_alert_async directly.

        Args:
            message: Alert message text
            severity: Alert severity level (INFO, WARNING, or CRITICAL)
            extra_data: Optional additional data to include in alert

        """
        if not self.config.enabled:
            return

        # Convert string severity to enum
        if isinstance(severity, str):
            severity = AlertSeverity(severity)

        # Run async version — asyncio.run() always creates a fresh event loop
        try:
            asyncio.run(self.send_alert_async(message, severity, extra_data))
        except Exception as e:
            self.logger.error(f"Alert delivery failed: {e}")

    async def send_alert_async(
        self,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        extra_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Send alert to all configured channels (async version).

        Args:
            message: Alert message text
            severity: Alert severity level
            extra_data: Optional additional data to include in alert

        """
        if not self.config.enabled:
            return

        # Format message with severity and timestamp
        formatted_message = self._format_message(message, severity, extra_data)

        # Send to all configured channels concurrently
        tasks = []

        if self.config.has_slack_configured():
            tasks.append(self._send_slack(formatted_message, severity))

        if self.config.has_telegram_configured():
            tasks.append(self._send_telegram(formatted_message, severity))

        if tasks:
            # Wait for all sends to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log any errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    channel = "Slack" if i == 0 and self.config.has_slack_configured() else "Telegram"
                    self.logger.error(f"Failed to send alert to {channel}: {result}")

    def _format_message(
        self,
        message: str,
        severity: AlertSeverity,
        extra_data: dict[str, Any] | None,
    ) -> str:
        """
        Format alert message with severity prefix, timestamp, and extra data.

        Args:
            message: Base message text
            severity: Alert severity level
            extra_data: Optional additional data

        Returns:
            Formatted message string

        """
        lines = [
            f"{severity.emoji} [{severity.name}] {message}",
            "",
            f"Timestamp: {datetime.now(tz=UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ]

        if extra_data:
            lines.append("")
            lines.append("Additional Details:")
            for key, value in extra_data.items():
                # Format key as human-readable
                formatted_key = key.replace("_", " ").title()

                # Format value based on type
                if isinstance(value, float):
                    if abs(value) < 1000:
                        formatted_value = f"{value:.4f}"
                    else:
                        formatted_value = f"{value:,.2f}"
                elif isinstance(value, dict):
                    formatted_value = json.dumps(value, indent=2)
                else:
                    formatted_value = str(value)

                lines.append(f"  • {formatted_key}: {formatted_value}")

        return "\n".join(lines)

    async def _send_slack(self, message: str, severity: AlertSeverity) -> None:
        """
        Send alert to Slack via webhook.

        Args:
            message: Formatted message text
            severity: Alert severity level

        Raises:
            aiohttp.ClientError: If Slack API request fails

        """
        if not self.config.slack_webhook:
            return

        # Build Slack message payload
        payload = {
            "text": message,
            "attachments": [
                {
                    "color": severity.color,
                    "footer": "HedgeGrid Risk Management System",
                    "ts": int(datetime.now(tz=UTC).timestamp()),
                }
            ],
        }

        # Send webhook request
        timeout = aiohttp.ClientTimeout(total=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(
                self.config.slack_webhook,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response,
        ):
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Slack webhook returned status {response.status}: {error_text}")

            self.logger.debug(f"Alert sent to Slack: {severity.name}")

    async def _send_telegram(self, message: str, severity: AlertSeverity) -> None:
        """
        Send alert to Telegram via bot API.

        Args:
            message: Formatted message text
            severity: Alert severity level

        Raises:
            aiohttp.ClientError: If Telegram API request fails

        """
        if not self.config.telegram_token or not self.config.telegram_chat_id:
            return

        # Build Telegram API URL
        url = f"https://api.telegram.org/bot{self.config.telegram_token}/sendMessage"

        # Build request payload
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",  # Enable HTML formatting
            "disable_web_page_preview": True,
        }

        # Send API request
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_data = await response.json()
                    raise RuntimeError(f"Telegram API returned status {response.status}: {error_data}")

                self.logger.debug(f"Alert sent to Telegram: {severity.name}")

    def send_circuit_breaker_alert(
        self,
        breaker_type: str,
        current_value: float,
        threshold: float,
        action: str = "flatten positions",
    ) -> None:
        """
        Send alert for circuit breaker trigger.

        Args:
            breaker_type: Type of circuit breaker (e.g., "Drawdown", "Funding Cost")
            current_value: Current metric value that triggered breaker
            threshold: Configured threshold value
            action: Action taken (default: "flatten positions")

        """
        if not self.config.alert_on_circuit_breaker:
            return

        message = f"Circuit Breaker Triggered: {breaker_type} Exceeded"

        extra_data = {
            "breaker_type": breaker_type,
            "current_value": current_value,
            "threshold": threshold,
            "action_taken": action,
        }

        self.send_alert(message, AlertSeverity.CRITICAL, extra_data)

    def send_flatten_alert(
        self,
        reason: str,
        sides_flattened: list[str],
        cancelled_orders: int,
        positions_closed: list[dict[str, Any]],
    ) -> None:
        """
        Send alert for position flattening.

        Args:
            reason: Reason for flattening
            sides_flattened: List of sides flattened ("long", "short", or both)
            cancelled_orders: Number of orders cancelled
            positions_closed: List of positions closed with details

        """
        if not self.config.alert_on_flatten:
            return

        message = "Position Flattening Executed"

        extra_data = {
            "reason": reason,
            "sides_flattened": ", ".join(sides_flattened),
            "cancelled_orders": cancelled_orders,
            "positions_closed": len(positions_closed),
            "position_details": positions_closed,
        }

        self.send_alert(message, AlertSeverity.CRITICAL, extra_data)

    def send_large_loss_alert(
        self,
        loss_amount: float,
        instrument: str,
        side: str,
    ) -> None:
        """
        Send alert for large single loss.

        Args:
            loss_amount: Loss amount in USDT
            instrument: Instrument where loss occurred
            side: Position side (LONG or SHORT)

        """
        if not self.config.alert_on_large_loss:
            return

        if abs(loss_amount) < self.config.large_loss_threshold_usdt:
            return

        message = f"Large Loss Detected: ${abs(loss_amount):.2f}"

        extra_data = {
            "loss_amount_usdt": loss_amount,
            "instrument": instrument,
            "side": side,
            "threshold": self.config.large_loss_threshold_usdt,
        }

        self.send_alert(message, AlertSeverity.WARNING, extra_data)

    def send_high_funding_alert(
        self,
        funding_rate_bps: float,
        projected_cost_usdt: float,
        instrument: str,
    ) -> None:
        """
        Send alert for high funding rate.

        Args:
            funding_rate_bps: Current funding rate in basis points
            projected_cost_usdt: Projected 8h funding cost in USDT
            instrument: Instrument with high funding

        """
        if not self.config.alert_on_high_funding:
            return

        if abs(funding_rate_bps) < self.config.high_funding_threshold_bps:
            return

        message = f"High Funding Rate Alert: {funding_rate_bps:.2f} bps"

        extra_data = {
            "funding_rate_bps": funding_rate_bps,
            "projected_8h_cost_usdt": projected_cost_usdt,
            "instrument": instrument,
            "threshold_bps": self.config.high_funding_threshold_bps,
        }

        self.send_alert(message, AlertSeverity.WARNING, extra_data)

    def send_startup_alert(self, strategy_name: str, config_path: str) -> None:
        """
        Send alert when strategy starts.

        Args:
            strategy_name: Name of strategy starting
            config_path: Path to strategy configuration

        """
        message = f"Strategy Started: {strategy_name}"

        extra_data = {
            "strategy": strategy_name,
            "config_path": config_path,
        }

        self.send_alert(message, AlertSeverity.INFO, extra_data)

    def send_shutdown_alert(
        self,
        strategy_name: str,
        reason: str,
        final_pnl: float | None = None,
    ) -> None:
        """
        Send alert when strategy stops.

        Args:
            strategy_name: Name of strategy stopping
            reason: Reason for shutdown
            final_pnl: Final PnL if available

        """
        message = f"Strategy Stopped: {strategy_name}"

        extra_data = {
            "strategy": strategy_name,
            "reason": reason,
        }

        if final_pnl is not None:
            extra_data["final_pnl_usdt"] = final_pnl

        self.send_alert(message, AlertSeverity.INFO, extra_data)
