"""
Data schemas for market data ingestion and validation.

This module defines strict schemas for different market data types
and provides conversion functions to NautilusTrader data objects.
"""

from datetime import UTC, datetime
from typing import Any

import pandas as pd
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.data import Bar, BarType, TradeTick
from nautilus_trader.model.enums import AggressorSide, BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity
from pydantic import BaseModel, Field, field_validator


class TradeSchema(BaseModel):
    """
    Schema for trade tick data.

    Attributes
    ----------
    timestamp : datetime
        Trade timestamp in UTC
    price : float
        Trade price (must be positive)
    size : float
        Trade size/quantity (must be positive)
    aggressor_side : str
        Side of the aggressor ("BUY" or "SELL")
    trade_id : str
        Unique trade identifier

    """

    timestamp: datetime = Field(..., description="Trade timestamp in UTC")
    price: float = Field(..., gt=0, description="Trade price (must be positive)")
    size: float = Field(..., gt=0, description="Trade size (must be positive)")
    aggressor_side: str = Field(..., description='Aggressor side ("BUY" or "SELL")')
    trade_id: str = Field(..., description="Unique trade identifier")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware and in UTC."""
        if v.tzinfo is None:
            # Assume UTC if no timezone
            return v.replace(tzinfo=UTC)
        # Convert to UTC if in different timezone
        return v.astimezone(UTC)

    @field_validator("aggressor_side")
    @classmethod
    def validate_aggressor_side(cls, v: str) -> str:
        """Validate aggressor side is BUY or SELL."""
        v_upper = v.upper()
        if v_upper not in ("BUY", "SELL"):
            raise ValueError(f"aggressor_side must be 'BUY' or 'SELL', got: {v}")
        return v_upper

    model_config = {"frozen": True}


class MarkPriceSchema(BaseModel):
    """
    Schema for mark price updates.

    Mark prices are used by exchanges to calculate unrealized PnL
    and are typically derived from spot index prices.

    Attributes
    ----------
    timestamp : datetime
        Mark price timestamp in UTC
    mark_price : float
        Mark price value (must be positive)

    """

    timestamp: datetime = Field(..., description="Mark price timestamp in UTC")
    mark_price: float = Field(..., gt=0, description="Mark price (must be positive)")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware and in UTC."""
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    model_config = {"frozen": True}


class FundingRateSchema(BaseModel):
    """
    Schema for funding rate updates.

    Funding rates are periodic payments between long and short positions
    in perpetual futures contracts.

    Attributes
    ----------
    timestamp : datetime
        Funding rate timestamp in UTC
    funding_rate : float
        Funding rate (can be positive or negative)
    next_funding_time : datetime | None
        Next funding timestamp (optional)

    """

    timestamp: datetime = Field(..., description="Funding rate timestamp in UTC")
    funding_rate: float = Field(..., description="Funding rate (can be +/-)")
    next_funding_time: datetime | None = Field(None, description="Next funding timestamp in UTC")

    @field_validator("timestamp", "next_funding_time")
    @classmethod
    def validate_timestamp_utc(cls, v: datetime | None) -> datetime | None:
        """Ensure timestamp is timezone-aware and in UTC."""
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    model_config = {"frozen": True}


# Conversion functions to NautilusTrader types


def to_trade_tick(row: TradeSchema | dict[str, Any], instrument_id: InstrumentId) -> TradeTick:
    """
    Convert TradeSchema to NautilusTrader TradeTick.

    Parameters
    ----------
    row : TradeSchema | dict
        Trade data (either Pydantic model or dict)
    instrument_id : InstrumentId
        Nautilus instrument identifier

    Returns
    -------
    TradeTick
        Nautilus TradeTick object

    Raises
    ------
    ValueError
        If data validation fails

    """
    # Convert dict to schema if needed
    if isinstance(row, dict):
        row = TradeSchema(**row)

    # Convert timestamp to nanoseconds
    ts_event = dt_to_unix_nanos(row.timestamp)
    ts_init = ts_event  # Use same timestamp for initialization

    # Convert aggressor side
    aggressor_side = AggressorSide.BUYER if row.aggressor_side == "BUY" else AggressorSide.SELLER

    # Create Nautilus objects
    price = Price.from_str(f"{row.price:.8f}")
    size = Quantity.from_str(f"{row.size:.8f}")
    trade_id = TradeId(row.trade_id)

    return TradeTick(
        instrument_id=instrument_id,
        price=price,
        size=size,
        aggressor_side=aggressor_side,
        trade_id=trade_id,
        ts_event=ts_event,
        ts_init=ts_init,
    )


def to_mark_price_update(
    row: MarkPriceSchema | dict[str, Any], instrument_id: InstrumentId
) -> dict[str, Any]:
    """
    Convert MarkPriceSchema to dictionary for custom data storage.

    Note: NautilusTrader doesn't have a built-in MarkPriceUpdate class in all versions,
    so we return a dict that can be stored as generic data or custom data.

    Parameters
    ----------
    row : MarkPriceSchema | dict
        Mark price data
    instrument_id : InstrumentId
        Nautilus instrument identifier

    Returns
    -------
    dict
        Mark price update dictionary with Nautilus-compatible structure

    """
    # Convert dict to schema if needed
    if isinstance(row, dict):
        row = MarkPriceSchema(**row)

    ts_event = dt_to_unix_nanos(row.timestamp)
    ts_init = ts_event

    return {
        "type": "MarkPrice",
        "instrument_id": str(instrument_id),
        "value": row.mark_price,
        "ts_event": ts_event,
        "ts_init": ts_init,
    }


def to_funding_rate_update(
    row: FundingRateSchema | dict[str, Any], instrument_id: InstrumentId
) -> dict[str, Any]:
    """
    Convert FundingRateSchema to dictionary for custom data storage.

    Parameters
    ----------
    row : FundingRateSchema | dict
        Funding rate data
    instrument_id : InstrumentId
        Nautilus instrument identifier

    Returns
    -------
    dict
        Funding rate update dictionary with Nautilus-compatible structure

    """
    # Convert dict to schema if needed
    if isinstance(row, dict):
        row = FundingRateSchema(**row)

    ts_event = dt_to_unix_nanos(row.timestamp)
    ts_init = ts_event

    # Convert next funding time if present
    next_funding_ns = None
    if row.next_funding_time:
        next_funding_ns = dt_to_unix_nanos(row.next_funding_time)

    return {
        "type": "FundingRate",
        "instrument_id": str(instrument_id),
        "rate": row.funding_rate,
        "ts_event": ts_event,
        "ts_init": ts_init,
        "next_funding_ns": next_funding_ns,
    }


def validate_dataframe_schema(df: pd.DataFrame, schema_type: str) -> None:
    """
    Validate DataFrame against schema requirements.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame to validate
    schema_type : str
        Schema type ("trade", "mark", or "funding")

    Raises
    ------
    ValueError
        If DataFrame doesn't match schema requirements

    """
    if schema_type == "trade":
        required_cols = {"timestamp", "price", "size", "aggressor_side", "trade_id"}
        schema_cls = TradeSchema
    elif schema_type == "mark":
        required_cols = {"timestamp", "mark_price"}
        schema_cls = MarkPriceSchema
    elif schema_type == "funding":
        required_cols = {"timestamp", "funding_rate"}
        schema_cls = FundingRateSchema
    else:
        raise ValueError(f"Unknown schema type: {schema_type}")

    # Check required columns
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns for {schema_type}: {missing}")

    # Validate each row (sample first 10 for performance)
    sample = df.head(min(10, len(df)))
    for idx, row in sample.iterrows():
        try:
            schema_cls(**row.to_dict())
        except Exception as e:
            raise ValueError(f"Row {idx} validation failed for {schema_type}: {e}") from e


def convert_dataframe_to_nautilus(
    df: pd.DataFrame, schema_type: str, instrument_id: InstrumentId
) -> list[Any]:
    """
    Convert entire DataFrame to Nautilus objects.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with validated schema
    schema_type : str
        Schema type ("trade", "mark", or "funding")
    instrument_id : InstrumentId
        Nautilus instrument identifier

    Returns
    -------
    list[Any]
        List of Nautilus data objects

    """
    results = []

    for row in df.itertuples(index=False):
        row_dict = row._asdict()

        if schema_type == "trade":
            obj = to_trade_tick(row_dict, instrument_id)
        elif schema_type == "mark":
            obj = to_mark_price_update(row_dict, instrument_id)
        elif schema_type == "funding":
            obj = to_funding_rate_update(row_dict, instrument_id)
        else:
            raise ValueError(f"Unknown schema type: {schema_type}")

        results.append(obj)

    return results


def mark_prices_to_bars(
    df: pd.DataFrame, bar_type: BarType
) -> list[Bar]:
    """
    Convert mark price OHLCV data to Nautilus Bar objects.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with columns: timestamp, open, high, low, close, volume
    bar_type : BarType
        Nautilus BarType for the bars

    Returns
    -------
    list[Bar]
        List of Nautilus Bar objects

    """
    required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns for bars: {missing}")

    bars = []
    for row in df.itertuples(index=False):
        ts_event = dt_to_unix_nanos(row.timestamp)
        ts_init = ts_event

        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{row.open:.2f}"),
            high=Price.from_str(f"{row.high:.2f}"),
            low=Price.from_str(f"{row.low:.2f}"),
            close=Price.from_str(f"{row.close:.2f}"),
            volume=Quantity.from_str(f"{row.volume:.3f}"),
            ts_event=ts_event,
            ts_init=ts_init,
        )
        bars.append(bar)

    return bars
