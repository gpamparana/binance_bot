"""Configuration models for operational controls (kill switch, alerts, circuit breakers)."""

import os

from pydantic import BaseModel, Field, field_validator, model_validator


class KillSwitchConfig(BaseModel):
    """
    Kill switch and circuit breaker configuration.

    The kill switch monitors critical risk metrics and automatically flattens positions
    when safety thresholds are breached. All thresholds are configurable with sensible
    defaults based on industry best practices.

    Attributes
    ----------
    enabled : bool
        Master switch for kill switch monitoring (default: True)
    check_interval_seconds : int
        How frequently to check circuit breakers (default: 5 seconds)
    max_drawdown_pct : float
        Maximum drawdown before triggering flatten (default: 5.0%)
        Calculated from session start or daily reset
    max_funding_cost_bps : float
        Maximum acceptable funding cost per 8h period (default: 20.0 bps)
        Triggers if projected 8h funding exceeds this threshold
    max_margin_ratio : float
        Maximum margin utilization before triggering flatten (default: 0.80 = 80%)
        Safety buffer from exchange liquidation threshold (typically 100%)
    max_loss_amount_usdt : float
        Absolute loss limit in USDT (default: 1000.0)
        Triggers flatten if total PnL drops below negative threshold
    daily_loss_limit_usdt : Optional[float]
        Daily loss limit that resets at UTC midnight (default: None = disabled)
        More aggressive than session max_loss_amount_usdt

    """

    enabled: bool = Field(
        True,
        description="Enable kill switch monitoring and circuit breakers",
    )

    check_interval_seconds: int = Field(
        5,
        ge=1,
        le=60,
        description="Circuit breaker check interval in seconds (1-60)",
    )

    max_drawdown_pct: float = Field(
        5.0,
        ge=0.1,
        le=50.0,
        description="Maximum drawdown percentage before flatten (0.1-50.0)",
    )

    max_funding_cost_bps: float = Field(
        20.0,
        ge=1.0,
        le=100.0,
        description="Maximum funding cost per 8h in basis points (1-100)",
    )

    max_margin_ratio: float = Field(
        0.80,
        ge=0.5,
        le=0.95,
        description="Maximum margin utilization ratio before flatten (0.5-0.95)",
    )

    max_loss_amount_usdt: float = Field(
        1000.0,
        gt=0,
        description="Maximum absolute loss in USDT before flatten",
    )

    max_position_usdt: float | None = Field(
        None,
        gt=0,
        description="Maximum total position size in USDT before flatten (None = disabled)",
    )

    daily_loss_limit_usdt: float | None = Field(
        None,
        description="Optional daily loss limit that resets at UTC midnight",
    )

    @field_validator("max_drawdown_pct")
    @classmethod
    def validate_drawdown(cls, v: float) -> float:
        """Warn if drawdown threshold is too aggressive."""
        if v > 20.0:
            import warnings

            warnings.warn(
                f"High drawdown threshold ({v}%) configured. This may result in large losses.",
                stacklevel=2,
            )
        return v

    @field_validator("max_margin_ratio")
    @classmethod
    def validate_margin_ratio(cls, v: float) -> float:
        """Ensure margin ratio has adequate safety buffer."""
        if v > 0.90:
            raise ValueError(
                f"Margin ratio {v:.1%} is dangerously high. Maximum recommended is 90% to avoid forced liquidation"
            )
        return v

    @model_validator(mode="after")
    def validate_loss_limits(self) -> "KillSwitchConfig":
        """Ensure daily loss limit is not greater than session limit."""
        if self.daily_loss_limit_usdt is not None:
            if self.daily_loss_limit_usdt > self.max_loss_amount_usdt:
                raise ValueError(
                    f"Daily loss limit ({self.daily_loss_limit_usdt:.2f}) should not exceed "
                    f"session loss limit ({self.max_loss_amount_usdt:.2f})"
                )
        return self


class AlertConfig(BaseModel):
    """
    Alert notification configuration for critical events.

    Supports multiple notification channels (Slack, Telegram) with environment variable
    integration for secure credential management.

    Environment Variables:
        SLACK_WEBHOOK_URL: Slack incoming webhook URL
        TELEGRAM_BOT_TOKEN: Telegram bot API token
        TELEGRAM_CHAT_ID: Telegram chat/channel ID

    Attributes
    ----------
    enabled : bool
        Master switch for alert system (default: True)
    slack_webhook : Optional[str]
        Slack webhook URL (reads from SLACK_WEBHOOK_URL env var if None)
    telegram_token : Optional[str]
        Telegram bot token (reads from TELEGRAM_BOT_TOKEN env var if None)
    telegram_chat_id : Optional[str]
        Telegram chat ID (reads from TELEGRAM_CHAT_ID env var if None)
    alert_on_flatten : bool
        Send alert when positions are flattened (default: True)
    alert_on_circuit_breaker : bool
        Send alert when circuit breaker triggers (default: True)
    alert_on_large_loss : bool
        Send alert on large single loss (default: True)
    large_loss_threshold_usdt : float
        Threshold for "large loss" alert in USDT (default: 100.0)
    alert_on_high_funding : bool
        Send alert when funding rate exceeds threshold (default: True)
    high_funding_threshold_bps : float
        Threshold for "high funding" alert in basis points (default: 15.0)

    """

    enabled: bool = Field(
        True,
        description="Enable alert notifications",
    )

    slack_webhook: str | None = Field(
        None,
        description="Slack webhook URL (or set SLACK_WEBHOOK_URL env var)",
    )

    telegram_token: str | None = Field(
        None,
        description="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)",
    )

    telegram_chat_id: str | None = Field(
        None,
        description="Telegram chat ID (or set TELEGRAM_CHAT_ID env var)",
    )

    alert_on_flatten: bool = Field(
        True,
        description="Alert when positions are flattened",
    )

    alert_on_circuit_breaker: bool = Field(
        True,
        description="Alert when circuit breaker triggers",
    )

    alert_on_large_loss: bool = Field(
        True,
        description="Alert on large single loss",
    )

    large_loss_threshold_usdt: float = Field(
        100.0,
        gt=0,
        description="Threshold for large loss alert in USDT",
    )

    alert_on_high_funding: bool = Field(
        True,
        description="Alert when funding rate is high",
    )

    high_funding_threshold_bps: float = Field(
        15.0,
        ge=1.0,
        le=100.0,
        description="Threshold for high funding alert in basis points",
    )

    @model_validator(mode="after")
    def load_from_env(self) -> "AlertConfig":
        """Load credentials from environment variables if not provided."""
        if self.slack_webhook is None:
            self.slack_webhook = os.getenv("SLACK_WEBHOOK_URL")

        if self.telegram_token is None:
            self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

        if self.telegram_chat_id is None:
            self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        return self

    def has_slack_configured(self) -> bool:
        """Check if Slack is properly configured."""
        return self.slack_webhook is not None and len(self.slack_webhook) > 0

    def has_telegram_configured(self) -> bool:
        """Check if Telegram is properly configured."""
        return (
            self.telegram_token is not None
            and len(self.telegram_token) > 0
            and self.telegram_chat_id is not None
            and len(self.telegram_chat_id) > 0
        )

    def has_any_channel(self) -> bool:
        """Check if at least one alert channel is configured."""
        return self.has_slack_configured() or self.has_telegram_configured()


class OperationsConfig(BaseModel):
    """
    Complete operational controls configuration.

    Combines kill switch and alert configurations for risk management and monitoring.

    Attributes
    ----------
    kill_switch : KillSwitchConfig
        Kill switch and circuit breaker configuration
    alerts : AlertConfig
        Alert notification configuration

    """

    kill_switch: KillSwitchConfig = Field(
        default_factory=KillSwitchConfig,
        description="Kill switch and circuit breaker configuration",
    )

    alerts: AlertConfig = Field(
        default_factory=AlertConfig,
        description="Alert notification configuration",
    )
