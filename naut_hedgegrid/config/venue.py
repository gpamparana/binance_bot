"""Venue configuration models."""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from naut_hedgegrid.config.base import BaseYamlConfigLoader


class VenueDetails(BaseModel):
    """Venue identification details."""

    name: str = Field(description="Venue name (e.g., BINANCE)")
    venue_type: Literal["spot", "futures", "options"] = Field(description="Type of venue")
    account_type: Literal["CASH", "MARGIN", "PERPETUAL_LINEAR", "PERPETUAL_INVERSE"] = Field(
        description="Account type for the venue"
    )


class APIConfig(BaseModel):
    """API connection configuration."""

    api_key: str = Field(description="API key (can use ${ENV_VAR} syntax)")
    api_secret: str = Field(description="API secret (can use ${ENV_VAR} syntax)")
    testnet: bool = Field(default=False, description="Use testnet/sandbox environment")
    base_url: HttpUrl | None = Field(default=None, description="Custom base URL for API")
    ws_url: str | None = Field(default=None, description="Custom WebSocket URL")


class TradingConfig(BaseModel):
    """Trading mode and parameters."""

    hedge_mode: bool = Field(
        default=False,
        description="Enable hedge mode (long/short positions simultaneously)",
    )
    leverage: int = Field(default=1, ge=1, le=125, description="Default leverage")
    margin_type: Literal["CROSSED", "ISOLATED"] = Field(default="CROSSED", description="Margin type")


class RiskConfig(BaseModel):
    """Risk management parameters."""

    max_leverage: int = Field(default=20, ge=1, le=125, description="Maximum allowed leverage")
    min_order_size_usdt: float = Field(default=5.0, gt=0, description="Minimum order size in USDT")
    max_order_size_usdt: float = Field(default=100000.0, gt=0, description="Maximum order size in USDT")


class PrecisionConfig(BaseModel):
    """Precision and notional guards."""

    price_precision: int = Field(default=2, ge=0, le=8, description="Price decimal precision")
    quantity_precision: int = Field(default=3, ge=0, le=8, description="Quantity decimal precision")
    min_notional: float = Field(default=5.0, gt=0, description="Minimum notional value for orders")


class RateLimitConfig(BaseModel):
    """API rate limit configuration."""

    orders_per_second: int = Field(default=5, ge=1, description="Max orders per second")
    orders_per_minute: int = Field(default=100, ge=1, description="Max orders per minute")
    weight_per_minute: int = Field(default=1200, ge=1, description="Max API weight per minute")


class WebSocketConfig(BaseModel):
    """WebSocket connection configuration."""

    ping_interval: int = Field(default=30, ge=5, description="WebSocket ping interval in seconds")
    reconnect_timeout: int = Field(default=60, ge=10, description="Reconnect timeout in seconds")
    max_reconnect_attempts: int = Field(default=10, ge=1, description="Maximum reconnection attempts")


class VenueConfig(BaseModel):
    """
    Complete venue configuration.

    This model represents all configuration needed to connect to and trade
    on a specific venue (exchange).
    """

    venue: VenueDetails = Field(description="Venue identification")
    api: APIConfig = Field(description="API configuration")
    trading: TradingConfig = Field(description="Trading configuration")
    risk: RiskConfig = Field(description="Risk parameters")
    precision: PrecisionConfig = Field(description="Precision guards")
    rate_limits: RateLimitConfig = Field(description="Rate limits")
    websocket: WebSocketConfig = Field(description="WebSocket configuration")


class VenueConfigLoader(BaseYamlConfigLoader):
    """Loader for venue configurations."""

    model_class = VenueConfig
