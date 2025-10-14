"""
Tests for data schemas and validation.

Tests all Pydantic schema models (TradeSchema, MarkPriceSchema, FundingRateSchema)
to ensure proper validation, timezone handling, and conversion to Nautilus types.
"""

from datetime import UTC, datetime

import pandas as pd
import pytest
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from pydantic import ValidationError

from naut_hedgegrid.data.schemas import (
    FundingRateSchema,
    MarkPriceSchema,
    TradeSchema,
    convert_dataframe_to_nautilus,
    to_funding_rate_update,
    to_mark_price_update,
    to_trade_tick,
    validate_dataframe_schema,
)

# ============================================================================
# TradeSchema Tests
# ============================================================================


class TestTradeSchema:
    """Tests for TradeSchema validation."""

    def test_valid_trade_schema(self):
        """Test that valid trade data passes validation."""
        trade = TradeSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            price=50000.0,
            size=0.1,
            aggressor_side="BUY",
            trade_id="12345",
        )

        assert trade.price == 50000.0
        assert trade.size == 0.1
        assert trade.aggressor_side == "BUY"
        assert trade.trade_id == "12345"
        assert trade.timestamp.tzinfo == UTC

    def test_trade_schema_negative_price_fails(self):
        """Test that negative price raises validation error."""
        with pytest.raises(ValidationError, match="greater than 0"):
            TradeSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                price=-50000.0,  # Invalid
                size=0.1,
                aggressor_side="BUY",
                trade_id="12345",
            )

    def test_trade_schema_zero_price_fails(self):
        """Test that zero price raises validation error."""
        with pytest.raises(ValidationError, match="greater than 0"):
            TradeSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                price=0.0,  # Invalid
                size=0.1,
                aggressor_side="BUY",
                trade_id="12345",
            )

    def test_trade_schema_negative_size_fails(self):
        """Test that negative size raises validation error."""
        with pytest.raises(ValidationError, match="greater than 0"):
            TradeSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                price=50000.0,
                size=-0.1,  # Invalid
                aggressor_side="BUY",
                trade_id="12345",
            )

    def test_trade_schema_zero_size_fails(self):
        """Test that zero size raises validation error."""
        with pytest.raises(ValidationError, match="greater than 0"):
            TradeSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                price=50000.0,
                size=0.0,  # Invalid
                aggressor_side="BUY",
                trade_id="12345",
            )

    def test_trade_schema_invalid_aggressor_side(self):
        """Test that invalid aggressor_side raises validation error."""
        with pytest.raises(ValidationError, match="aggressor_side must be 'BUY' or 'SELL'"):
            TradeSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                price=50000.0,
                size=0.1,
                aggressor_side="INVALID",  # Invalid
                trade_id="12345",
            )

    def test_trade_schema_lowercase_side_normalized(self):
        """Test that lowercase aggressor_side is normalized to uppercase."""
        trade = TradeSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            price=50000.0,
            size=0.1,
            aggressor_side="buy",  # Lowercase
            trade_id="12345",
        )

        assert trade.aggressor_side == "BUY"

    def test_trade_schema_missing_field_fails(self):
        """Test that missing required field raises validation error."""
        with pytest.raises(ValidationError):
            TradeSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                price=50000.0,
                size=0.1,
                aggressor_side="BUY",
                # Missing trade_id
            )

    def test_trade_schema_utc_timezone_enforcement(self):
        """Test that naive timestamps are assumed UTC."""
        trade = TradeSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0),  # Naive (no timezone)
            price=50000.0,
            size=0.1,
            aggressor_side="BUY",
            trade_id="12345",
        )

        assert trade.timestamp.tzinfo == UTC

    def test_trade_schema_timezone_conversion(self):
        """Test that non-UTC timezones are converted to UTC."""
        import zoneinfo

        # Create timestamp in EST
        est = zoneinfo.ZoneInfo("America/New_York")
        est_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=est)

        trade = TradeSchema(
            timestamp=est_time,
            price=50000.0,
            size=0.1,
            aggressor_side="BUY",
            trade_id="12345",
        )

        # Should be converted to UTC
        assert trade.timestamp.tzinfo == UTC
        # 12:00 EST = 17:00 UTC (EST is UTC-5)
        assert trade.timestamp.hour == 17

    def test_trade_schema_immutable(self):
        """Test that TradeSchema is immutable (frozen)."""
        trade = TradeSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            price=50000.0,
            size=0.1,
            aggressor_side="BUY",
            trade_id="12345",
        )

        with pytest.raises(ValidationError):
            trade.price = 60000.0  # Should fail - schema is frozen


# ============================================================================
# MarkPriceSchema Tests
# ============================================================================


class TestMarkPriceSchema:
    """Tests for MarkPriceSchema validation."""

    def test_valid_mark_price_schema(self):
        """Test that valid mark price data passes validation."""
        mark = MarkPriceSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            mark_price=50001.0,
        )

        assert mark.mark_price == 50001.0
        assert mark.timestamp.tzinfo == UTC

    def test_mark_price_schema_negative_price_fails(self):
        """Test that negative mark price raises validation error."""
        with pytest.raises(ValidationError, match="greater than 0"):
            MarkPriceSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                mark_price=-50000.0,  # Invalid
            )

    def test_mark_price_schema_zero_price_fails(self):
        """Test that zero mark price raises validation error."""
        with pytest.raises(ValidationError, match="greater than 0"):
            MarkPriceSchema(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                mark_price=0.0,  # Invalid
            )

    def test_mark_price_schema_utc_enforcement(self):
        """Test that naive timestamps are assumed UTC."""
        mark = MarkPriceSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0),  # Naive
            mark_price=50001.0,
        )

        assert mark.timestamp.tzinfo == UTC

    def test_mark_price_schema_immutable(self):
        """Test that MarkPriceSchema is immutable."""
        mark = MarkPriceSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            mark_price=50001.0,
        )

        with pytest.raises(ValidationError):
            mark.mark_price = 60000.0


# ============================================================================
# FundingRateSchema Tests
# ============================================================================


class TestFundingRateSchema:
    """Tests for FundingRateSchema validation."""

    def test_valid_funding_rate_schema(self):
        """Test that valid funding rate data passes validation."""
        funding = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            funding_rate=0.0001,
            next_funding_time=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
        )

        assert funding.funding_rate == 0.0001
        assert funding.next_funding_time is not None
        assert funding.timestamp.tzinfo == UTC

    def test_funding_rate_can_be_negative(self):
        """Test that funding rate can be negative (short pays long)."""
        funding = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            funding_rate=-0.0002,  # Negative is valid
        )

        assert funding.funding_rate == -0.0002

    def test_funding_rate_can_be_zero(self):
        """Test that funding rate can be zero."""
        funding = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            funding_rate=0.0,  # Zero is valid
        )

        assert funding.funding_rate == 0.0

    def test_funding_rate_optional_next_funding_time(self):
        """Test that next_funding_time is optional."""
        funding = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            funding_rate=0.0001,
            # No next_funding_time
        )

        assert funding.next_funding_time is None

    def test_funding_rate_next_funding_utc_enforcement(self):
        """Test that next_funding_time is converted to UTC."""
        funding = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            funding_rate=0.0001,
            next_funding_time=datetime(2024, 1, 1, 8, 0, 0),  # Naive
        )

        assert funding.next_funding_time.tzinfo == UTC

    def test_funding_rate_schema_immutable(self):
        """Test that FundingRateSchema is immutable."""
        funding = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            funding_rate=0.0001,
        )

        with pytest.raises(ValidationError):
            funding.funding_rate = 0.0002


# ============================================================================
# Conversion to Nautilus Types Tests
# ============================================================================


class TestToTradeTick:
    """Tests for to_trade_tick conversion function."""

    def test_to_trade_tick_from_schema(self):
        """Test conversion from TradeSchema to TradeTick."""
        trade_schema = TradeSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            price=50000.0,
            size=0.1,
            aggressor_side="BUY",
            trade_id="12345",
        )

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        trade_tick = to_trade_tick(trade_schema, instrument_id)

        assert trade_tick.instrument_id == instrument_id
        assert float(trade_tick.price) == 50000.0
        assert float(trade_tick.size) == 0.1
        assert trade_tick.aggressor_side == AggressorSide.BUYER
        assert str(trade_tick.trade_id) == "12345"

    def test_to_trade_tick_from_dict(self):
        """Test conversion from dict to TradeTick."""
        trade_dict = {
            "timestamp": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            "price": 50000.0,
            "size": 0.1,
            "aggressor_side": "SELL",
            "trade_id": "12345",
        }

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        trade_tick = to_trade_tick(trade_dict, instrument_id)

        assert trade_tick.instrument_id == instrument_id
        assert trade_tick.aggressor_side == AggressorSide.SELLER

    def test_to_trade_tick_timestamp_precision(self):
        """Test that timestamp is converted to nanoseconds correctly."""
        trade_schema = TradeSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0, 123456, tzinfo=UTC),
            price=50000.0,
            size=0.1,
            aggressor_side="BUY",
            trade_id="12345",
        )

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        trade_tick = to_trade_tick(trade_schema, instrument_id)

        # Verify timestamp is in nanoseconds
        expected_ts = dt_to_unix_nanos(trade_schema.timestamp)
        assert trade_tick.ts_event == expected_ts
        assert trade_tick.ts_init == expected_ts


class TestToMarkPriceUpdate:
    """Tests for to_mark_price_update conversion function."""

    def test_to_mark_price_update_from_schema(self):
        """Test conversion from MarkPriceSchema to dict."""
        mark_schema = MarkPriceSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            mark_price=50001.0,
        )

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        mark_dict = to_mark_price_update(mark_schema, instrument_id)

        assert mark_dict["type"] == "MarkPrice"
        assert mark_dict["instrument_id"] == str(instrument_id)
        assert mark_dict["value"] == 50001.0
        assert "ts_event" in mark_dict
        assert "ts_init" in mark_dict

    def test_to_mark_price_update_from_dict(self):
        """Test conversion from dict to mark price update."""
        mark_dict_input = {
            "timestamp": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            "mark_price": 50001.0,
        }

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        mark_dict = to_mark_price_update(mark_dict_input, instrument_id)

        assert mark_dict["type"] == "MarkPrice"
        assert mark_dict["value"] == 50001.0


class TestToFundingRateUpdate:
    """Tests for to_funding_rate_update conversion function."""

    def test_to_funding_rate_update_from_schema(self):
        """Test conversion from FundingRateSchema to dict."""
        funding_schema = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            funding_rate=0.0001,
            next_funding_time=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
        )

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        funding_dict = to_funding_rate_update(funding_schema, instrument_id)

        assert funding_dict["type"] == "FundingRate"
        assert funding_dict["instrument_id"] == str(instrument_id)
        assert funding_dict["rate"] == 0.0001
        assert funding_dict["next_funding_ns"] is not None

    def test_to_funding_rate_update_no_next_funding(self):
        """Test conversion when next_funding_time is None."""
        funding_schema = FundingRateSchema(
            timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            funding_rate=0.0001,
            next_funding_time=None,
        )

        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        funding_dict = to_funding_rate_update(funding_schema, instrument_id)

        assert funding_dict["next_funding_ns"] is None


# ============================================================================
# DataFrame Validation Tests
# ============================================================================


class TestValidateDataFrameSchema:
    """Tests for validate_dataframe_schema function."""

    def test_validate_trade_dataframe_valid(self, sample_trades_df):
        """Test that valid trade DataFrame passes validation."""
        # Should not raise
        validate_dataframe_schema(sample_trades_df, "trade")

    def test_validate_trade_dataframe_missing_column(self):
        """Test that DataFrame with missing column fails validation."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01"], utc=True),
                "price": [50000.0],
                "size": [0.1],
                # Missing aggressor_side and trade_id
            }
        )

        with pytest.raises(ValueError, match="Missing required columns"):
            validate_dataframe_schema(df, "trade")

    def test_validate_trade_dataframe_invalid_data(self):
        """Test that DataFrame with invalid data fails validation."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01"], utc=True),
                "price": [-50000.0],  # Invalid
                "size": [0.1],
                "aggressor_side": ["BUY"],
                "trade_id": ["12345"],
            }
        )

        with pytest.raises(ValueError, match="validation failed"):
            validate_dataframe_schema(df, "trade")

    def test_validate_mark_dataframe_valid(self, sample_mark_prices_df):
        """Test that valid mark price DataFrame passes validation."""
        validate_dataframe_schema(sample_mark_prices_df, "mark")

    def test_validate_funding_dataframe_valid(self, sample_funding_rates_df):
        """Test that valid funding rate DataFrame passes validation."""
        validate_dataframe_schema(sample_funding_rates_df, "funding")

    def test_validate_unknown_schema_type(self):
        """Test that unknown schema type raises error."""
        df = pd.DataFrame()

        with pytest.raises(ValueError, match="Unknown schema type"):
            validate_dataframe_schema(df, "invalid_type")


# ============================================================================
# DataFrame Conversion Tests
# ============================================================================


class TestConvertDataFrameToNautilus:
    """Tests for convert_dataframe_to_nautilus function."""

    def test_convert_trades_dataframe(self, sample_trades_df):
        """Test conversion of trade DataFrame to TradeTick objects."""
        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")

        trade_ticks = convert_dataframe_to_nautilus(sample_trades_df, "trade", instrument_id)

        assert len(trade_ticks) == len(sample_trades_df)
        assert all(tick.instrument_id == instrument_id for tick in trade_ticks)

    def test_convert_mark_dataframe(self, sample_mark_prices_df):
        """Test conversion of mark price DataFrame to dict objects."""
        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")

        mark_updates = convert_dataframe_to_nautilus(sample_mark_prices_df, "mark", instrument_id)

        assert len(mark_updates) == len(sample_mark_prices_df)
        assert all(update["type"] == "MarkPrice" for update in mark_updates)

    def test_convert_funding_dataframe(self, sample_funding_rates_df):
        """Test conversion of funding rate DataFrame to dict objects."""
        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")

        funding_updates = convert_dataframe_to_nautilus(
            sample_funding_rates_df, "funding", instrument_id
        )

        assert len(funding_updates) == len(sample_funding_rates_df)
        assert all(update["type"] == "FundingRate" for update in funding_updates)

    def test_convert_empty_dataframe(self):
        """Test conversion of empty DataFrame."""
        df = pd.DataFrame(columns=["timestamp", "price", "size", "aggressor_side", "trade_id"])
        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")

        result = convert_dataframe_to_nautilus(df, "trade", instrument_id)

        assert len(result) == 0

    def test_convert_unknown_schema_type(self, sample_trades_df):
        """Test that unknown schema type raises error."""
        instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")

        with pytest.raises(ValueError, match="Unknown schema type"):
            convert_dataframe_to_nautilus(sample_trades_df, "invalid_type", instrument_id)
