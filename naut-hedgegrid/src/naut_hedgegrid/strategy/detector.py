"""Market regime detection using technical indicators with hysteresis."""

from collections import deque
from dataclasses import dataclass

from naut_hedgegrid.domain.types import Regime


@dataclass
class Bar:
    """
    OHLCV bar data for indicator calculations.

    Represents a single price bar with open, high, low, close prices
    and volume for a specific time period.
    """

    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        """Validate bar data."""
        if self.high < self.low:
            msg = f"High {self.high} cannot be less than low {self.low}"
            raise ValueError(msg)
        if self.high < self.close or self.low > self.close:
            msg = f"Close {self.close} must be between high {self.high} and low {self.low}"
            raise ValueError(msg)
        if self.high < self.open or self.low > self.open:
            msg = f"Open {self.open} must be between high {self.high} and low {self.low}"
            raise ValueError(msg)


class EMA:
    """
    Exponential Moving Average indicator.

    Uses Wilder's smoothing method with k = 2/(period+1).
    """

    def __init__(self, period: int) -> None:
        """Initialize EMA indicator.

        Args:
            period: Number of periods for EMA calculation

        """
        if period < 1:
            msg = f"Period must be positive, got {period}"
            raise ValueError(msg)

        self.period = period
        self.multiplier = 2.0 / (period + 1)
        self._value: float | None = None
        self._warmup_values: list[float] = []
        self._is_warm = False

    @property
    def value(self) -> float | None:
        """Get current EMA value."""
        return self._value

    @property
    def is_warm(self) -> bool:
        """Check if indicator has enough data."""
        return self._is_warm

    def update(self, price: float) -> None:
        """Update EMA with new price.

        Args:
            price: New price value

        """
        if not self._is_warm:
            self._warmup_values.append(price)
            if len(self._warmup_values) >= self.period:
                # Initialize with SMA
                self._value = sum(self._warmup_values) / self.period
                self._is_warm = True
                self._warmup_values.clear()
        elif self._value is None:
            # First calculation after warmup
            self._value = price
        else:
            # Standard EMA calculation
            self._value = (price * self.multiplier) + (self._value * (1 - self.multiplier))

    def reset(self) -> None:
        """Reset indicator state."""
        self._value = None
        self._warmup_values.clear()
        self._is_warm = False


class ADX:
    """
    Average Directional Index for trend strength.

    Measures trend strength on a scale of 0-100.
    Values < 20 indicate weak trend, > 40 indicate strong trend.
    """

    def __init__(self, period: int) -> None:
        """Initialize ADX indicator.

        Args:
            period: Number of periods for ADX calculation (typically 14)

        """
        if period < 1:
            msg = f"Period must be positive, got {period}"
            raise ValueError(msg)

        self.period = period
        self.multiplier = 1.0 / period
        self._adx_value: float | None = None
        self._plus_di: float | None = None
        self._minus_di: float | None = None
        self._smoothed_tr: float | None = None
        self._smoothed_plus_dm: float | None = None
        self._smoothed_minus_dm: float | None = None
        self._prev_close: float | None = None
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._dx_values: deque[float] = deque(maxlen=period)
        self._bar_count = 0

    @property
    def value(self) -> float | None:
        """Get current ADX value."""
        return self._adx_value

    @property
    def is_warm(self) -> bool:
        """Check if indicator has enough data."""
        return self._bar_count >= self.period * 2

    def update(self, high: float, low: float, close: float) -> None:
        """Update ADX with new bar data.

        Args:
            high: Bar high price
            low: Bar low price
            close: Bar close price

        """
        self._bar_count += 1

        # Need previous bar for calculations
        if self._prev_close is None:
            self._prev_close = close
            self._prev_high = high
            self._prev_low = low
            return

        # At this point, previous values are guaranteed to be set
        assert self._prev_high is not None
        assert self._prev_low is not None

        # Calculate True Range
        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

        # Calculate +DM and -DM
        up_move = high - self._prev_high
        down_move = self._prev_low - low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        # Smooth TR, +DM, -DM using Wilder's method
        if self._smoothed_tr is None:
            # First calculation - initialize
            self._smoothed_tr = tr
            self._smoothed_plus_dm = plus_dm
            self._smoothed_minus_dm = minus_dm
        else:
            # Subsequent calculations - Wilder's smoothing
            assert self._smoothed_plus_dm is not None
            assert self._smoothed_minus_dm is not None

            self._smoothed_tr = (self._smoothed_tr * (self.period - 1) + tr) / self.period
            self._smoothed_plus_dm = (
                self._smoothed_plus_dm * (self.period - 1) + plus_dm
            ) / self.period
            self._smoothed_minus_dm = (
                self._smoothed_minus_dm * (self.period - 1) + minus_dm
            ) / self.period

        # Calculate +DI and -DI
        if self._smoothed_tr > 0:
            self._plus_di = (self._smoothed_plus_dm / self._smoothed_tr) * 100
            self._minus_di = (self._smoothed_minus_dm / self._smoothed_tr) * 100

            # Calculate DX
            di_sum = self._plus_di + self._minus_di
            if di_sum > 0:
                dx = (abs(self._plus_di - self._minus_di) / di_sum) * 100
                self._dx_values.append(dx)

                # Calculate ADX once we have enough DX values
                if len(self._dx_values) >= self.period:
                    if self._adx_value is None:
                        # First ADX - simple average
                        self._adx_value = sum(self._dx_values) / len(self._dx_values)
                    else:
                        # Subsequent ADX - Wilder's smoothing
                        self._adx_value = (self._adx_value * (self.period - 1) + dx) / self.period

        # Store current values for next iteration
        self._prev_close = close
        self._prev_high = high
        self._prev_low = low

    def reset(self) -> None:
        """Reset indicator state."""
        self._adx_value = None
        self._plus_di = None
        self._minus_di = None
        self._smoothed_tr = None
        self._smoothed_plus_dm = None
        self._smoothed_minus_dm = None
        self._prev_close = None
        self._prev_high = None
        self._prev_low = None
        self._dx_values.clear()
        self._bar_count = 0


class ATR:
    """
    Average True Range for volatility measurement.

    Measures market volatility using Wilder's smoothing.
    """

    def __init__(self, period: int) -> None:
        """Initialize ATR indicator.

        Args:
            period: Number of periods for ATR calculation (typically 14)

        """
        if period < 1:
            msg = f"Period must be positive, got {period}"
            raise ValueError(msg)

        self.period = period
        self._atr_value: float | None = None
        self._prev_close: float | None = None
        self._bar_count = 0

    @property
    def value(self) -> float | None:
        """Get current ATR value."""
        return self._atr_value

    @property
    def is_warm(self) -> bool:
        """Check if indicator has enough data."""
        return self._bar_count >= self.period

    def update(self, high: float, low: float, close: float) -> None:
        """Update ATR with new bar data.

        Args:
            high: Bar high price
            low: Bar low price
            close: Bar close price

        """
        self._bar_count += 1

        # Need previous bar for TR calculation
        if self._prev_close is None:
            self._prev_close = close
            return

        # Calculate True Range
        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

        # Update ATR using Wilder's smoothing
        if self._atr_value is None:
            self._atr_value = tr
        else:
            self._atr_value = (self._atr_value * (self.period - 1) + tr) / self.period

        self._prev_close = close

    def reset(self) -> None:
        """Reset indicator state."""
        self._atr_value = None
        self._prev_close = None
        self._bar_count = 0


class RegimeDetector:
    """
    Market regime detector using EMA, ADX, and hysteresis.

    Classifies market into UP, DOWN, or SIDEWAYS regimes based on:
    - Trend direction: EMA(fast) vs EMA(slow)
    - Trend strength: ADX threshold
    - Stability: Hysteresis band to prevent rapid switching
    """

    # ADX threshold for trend strength
    ADX_TREND_THRESHOLD = 20.0

    def __init__(
        self,
        ema_fast: int,
        ema_slow: int,
        adx_len: int,
        atr_len: int,
        hysteresis_bps: float,
    ) -> None:
        """Initialize regime detector.

        Args:
            ema_fast: Fast EMA period (e.g., 20)
            ema_slow: Slow EMA period (e.g., 50)
            adx_len: ADX period for trend strength (e.g., 14)
            atr_len: ATR period for volatility (e.g., 14)
            hysteresis_bps: Hysteresis band in basis points to prevent flipping

        Raises:
            ValueError: If parameters are invalid

        """
        if ema_fast >= ema_slow:
            msg = f"Fast EMA ({ema_fast}) must be less than slow EMA ({ema_slow})"
            raise ValueError(msg)
        if hysteresis_bps < 0:
            msg = f"Hysteresis must be non-negative, got {hysteresis_bps}"
            raise ValueError(msg)

        self.ema_fast = EMA(ema_fast)
        self.ema_slow = EMA(ema_slow)
        self.adx = ADX(adx_len)
        self.atr = ATR(atr_len)
        self.hysteresis_bps = hysteresis_bps

        self._current_regime = Regime.SIDEWAYS
        self._bar_count = 0

    def update_from_bar(self, bar: Bar) -> None:
        """Update all indicators with new bar data.

        Args:
            bar: OHLCV bar data

        """
        self._bar_count += 1

        # Update all indicators
        self.ema_fast.update(bar.close)
        self.ema_slow.update(bar.close)
        self.adx.update(bar.high, bar.low, bar.close)
        self.atr.update(bar.high, bar.low, bar.close)

        # Update regime classification
        self._update_regime()

    def current(self) -> Regime:
        """Get current market regime.

        Returns:
            Current regime classification (UP, DOWN, or SIDEWAYS)

        """
        return self._current_regime

    def _update_regime(self) -> None:
        """Update regime classification based on current indicators."""
        # Wait for indicators to warm up
        if not self.ema_fast.is_warm or not self.ema_slow.is_warm:
            self._current_regime = Regime.SIDEWAYS
            return

        fast_value = self.ema_fast.value
        slow_value = self.ema_slow.value

        # Safety checks
        if fast_value is None or slow_value is None or slow_value == 0:
            self._current_regime = Regime.SIDEWAYS
            return

        # Calculate EMA spread in basis points
        spread_bps = ((fast_value - slow_value) / slow_value) * 10000

        # Check ADX for trend strength (if available)
        if (
            self.adx.is_warm
            and self.adx.value is not None
            and self.adx.value < self.ADX_TREND_THRESHOLD
        ):
            # Weak trend - classify as SIDEWAYS
            self._current_regime = Regime.SIDEWAYS
            return

        # Apply hysteresis to prevent rapid switching
        if abs(spread_bps) < self.hysteresis_bps:
            # Within hysteresis band - stay in current regime
            return

        # Determine new regime based on spread
        if spread_bps > 0:
            self._current_regime = Regime.UP
        else:
            self._current_regime = Regime.DOWN

    @property
    def is_warm(self) -> bool:
        """Check if detector has enough data for reliable regime detection.

        Returns:
            True if all indicators are warmed up

        """
        return (
            self.ema_fast.is_warm
            and self.ema_slow.is_warm
            and self.adx.is_warm
            and self.atr.is_warm
        )

    def reset(self) -> None:
        """Reset all indicators and regime state."""
        self.ema_fast.reset()
        self.ema_slow.reset()
        self.adx.reset()
        self.atr.reset()
        self._current_regime = Regime.SIDEWAYS
        self._bar_count = 0
