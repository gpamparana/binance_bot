"""
Schema normalization for market data.

Handles timestamp conversion, timezone normalization, and data validation
to ensure all data conforms to standard schemas before conversion to Nautilus.
"""

import logging

import pandas as pd

from naut_hedgegrid.data.schemas import validate_dataframe_schema

logger = logging.getLogger(__name__)


def normalize_trades(df: pd.DataFrame, source_type: str = "unknown") -> pd.DataFrame:
    """
    Normalize trade data to TradeSchema format.

    Handles:
    - Timestamp conversion to UTC datetime64[ns]
    - Price and size validation (must be positive)
    - Aggressor side normalization to BUY/SELL
    - Sorting by timestamp
    - Duplicate removal

    Parameters
    ----------
    df : pd.DataFrame
        Raw trade data
    source_type : str, default "unknown"
        Source type for logging context

    Returns
    -------
    pd.DataFrame
        Normalized trade data

    Raises
    ------
    ValueError
        If data validation fails

    """
    if df.empty:
        logger.warning(f"Empty DataFrame for {source_type} trades")
        return df

    logger.info(f"Normalizing {len(df):,} trades from {source_type}")

    # Make a copy to avoid modifying original
    df = df.copy()

    # Normalize timestamp
    df["timestamp"] = _normalize_timestamp(df["timestamp"])

    # Validate and normalize price/size
    if (df["price"] <= 0).any():
        invalid_count = (df["price"] <= 0).sum()
        logger.warning(f"Found {invalid_count} trades with invalid price, removing")
        df = df[df["price"] > 0]

    if (df["size"] <= 0).any():
        invalid_count = (df["size"] <= 0).sum()
        logger.warning(f"Found {invalid_count} trades with invalid size, removing")
        df = df[df["size"] > 0]

    # Normalize aggressor side
    df["aggressor_side"] = df["aggressor_side"].str.upper()
    valid_sides = df["aggressor_side"].isin(["BUY", "SELL"])
    if not valid_sides.all():
        invalid_count = (~valid_sides).sum()
        logger.warning(f"Found {invalid_count} trades with invalid side, removing")
        df = df[valid_sides]

    # Ensure trade_id is string
    df["trade_id"] = df["trade_id"].astype(str)

    # Sort by timestamp (required for Nautilus)
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Remove duplicates (keep first occurrence)
    initial_len = len(df)
    df = df.drop_duplicates(subset=["timestamp", "trade_id"], keep="first")
    if len(df) < initial_len:
        logger.info(f"Removed {initial_len - len(df)} duplicate trades")

    # Validate against schema
    validate_dataframe_schema(df, "trade")

    logger.info(f"Normalized {len(df):,} trades successfully")
    return df


def normalize_mark_prices(df: pd.DataFrame, source_type: str = "unknown") -> pd.DataFrame:
    """
    Normalize mark price data to MarkPriceSchema format.

    Handles:
    - Timestamp conversion to UTC
    - Price validation (must be positive)
    - Sorting by timestamp
    - Duplicate removal

    Parameters
    ----------
    df : pd.DataFrame
        Raw mark price data
    source_type : str, default "unknown"
        Source type for logging

    Returns
    -------
    pd.DataFrame
        Normalized mark price data

    """
    if df.empty:
        logger.warning(f"Empty DataFrame for {source_type} mark prices")
        return df

    logger.info(f"Normalizing {len(df):,} mark prices from {source_type}")

    df = df.copy()

    # Normalize timestamp
    df["timestamp"] = _normalize_timestamp(df["timestamp"])

    # Validate mark price
    if (df["mark_price"] <= 0).any():
        invalid_count = (df["mark_price"] <= 0).sum()
        logger.warning(f"Found {invalid_count} invalid mark prices, removing")
        df = df[df["mark_price"] > 0]

    # Sort by timestamp
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Remove duplicates (keep last occurrence for mark prices)
    initial_len = len(df)
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    if len(df) < initial_len:
        logger.info(f"Removed {initial_len - len(df)} duplicate mark prices")

    # Validate schema
    validate_dataframe_schema(df, "mark")

    logger.info(f"Normalized {len(df):,} mark prices successfully")
    return df


def normalize_funding_rates(df: pd.DataFrame, source_type: str = "unknown") -> pd.DataFrame:
    """
    Normalize funding rate data to FundingRateSchema format.

    Handles:
    - Timestamp conversion to UTC
    - Funding rate validation (can be positive or negative)
    - Optional next_funding_time conversion
    - Sorting by timestamp
    - Duplicate removal

    Parameters
    ----------
    df : pd.DataFrame
        Raw funding rate data
    source_type : str, default "unknown"
        Source type for logging

    Returns
    -------
    pd.DataFrame
        Normalized funding rate data

    """
    if df.empty:
        logger.warning(f"Empty DataFrame for {source_type} funding rates")
        return df

    logger.info(f"Normalizing {len(df):,} funding rates from {source_type}")

    df = df.copy()

    # Normalize timestamp
    df["timestamp"] = _normalize_timestamp(df["timestamp"])

    # Normalize next_funding_time if present
    if "next_funding_time" in df.columns:
        # Handle None/NaN values
        mask = df["next_funding_time"].notna()
        if mask.any():
            df.loc[mask, "next_funding_time"] = _normalize_timestamp(
                df.loc[mask, "next_funding_time"]
            )
    else:
        df["next_funding_time"] = None

    # Funding rate can be positive or negative, just check it's not NaN
    if df["funding_rate"].isna().any():
        invalid_count = df["funding_rate"].isna().sum()
        logger.warning(f"Found {invalid_count} NaN funding rates, removing")
        df = df[df["funding_rate"].notna()]

    # Sort by timestamp
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Remove duplicates (keep last occurrence)
    initial_len = len(df)
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    if len(df) < initial_len:
        logger.info(f"Removed {initial_len - len(df)} duplicate funding rates")

    # Validate schema
    validate_dataframe_schema(df, "funding")

    logger.info(f"Normalized {len(df):,} funding rates successfully")
    return df


def _normalize_timestamp(ts_series: pd.Series) -> pd.Series:
    """
    Normalize timestamp series to UTC datetime64[ns].

    Handles multiple timestamp formats:
    - ISO strings
    - Unix seconds (float/int)
    - Unix milliseconds (int)
    - Unix microseconds (int)
    - Unix nanoseconds (int)
    - Already datetime64

    Parameters
    ----------
    ts_series : pd.Series
        Timestamp series

    Returns
    -------
    pd.Series
        Normalized timestamps as datetime64[ns, UTC]

    """
    # Convert DatetimeIndex to Series if needed
    if isinstance(ts_series, pd.DatetimeIndex):
        ts_series = pd.Series(ts_series)

    # If already datetime, just ensure UTC
    if pd.api.types.is_datetime64_any_dtype(ts_series):
        if ts_series.dt.tz is None:
            return ts_series.dt.tz_localize("UTC")
        return ts_series.dt.tz_convert("UTC")

    # Try parsing as string first
    if ts_series.dtype == object:
        try:
            result = pd.to_datetime(ts_series, utc=True)
            return result
        except Exception:
            pass

    # Try numeric formats
    if pd.api.types.is_numeric_dtype(ts_series):
        # Detect format based on magnitude
        sample = ts_series.iloc[0] if len(ts_series) > 0 else 0

        if sample < 1e10:
            # Unix seconds
            unit = "s"
        elif sample < 1e13:
            # Unix milliseconds
            unit = "ms"
        elif sample < 1e16:
            # Unix microseconds
            unit = "us"
        else:
            # Unix nanoseconds
            unit = "ns"

        result = pd.to_datetime(ts_series, unit=unit, utc=True)
        return result

    # Fallback: try generic parsing
    try:
        result = pd.to_datetime(ts_series, utc=True)
        return result
    except Exception as e:
        raise ValueError(f"Failed to parse timestamps: {e}") from e
