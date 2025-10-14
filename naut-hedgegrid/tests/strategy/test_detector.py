"""Tests for regime detector and technical indicators."""

import pytest

from naut_hedgegrid.domain.types import Regime
from naut_hedgegrid.strategy.detector import ADX, ATR, EMA, Bar, RegimeDetector

# Bar Tests


def test_bar_creation() -> None:
    """Test basic Bar creation."""
    bar = Bar(open=100.0, high=102.0, low=99.0, close=101.0, volume=1000.0)

    assert bar.open == 100.0
    assert bar.high == 102.0
    assert bar.low == 99.0
    assert bar.close == 101.0
    assert bar.volume == 1000.0


def test_bar_minimal_creation() -> None:
    """Test Bar creation without volume."""
    bar = Bar(open=100.0, high=102.0, low=99.0, close=101.0)

    assert bar.volume == 0.0


def test_bar_invalid_high_low() -> None:
    """Test Bar validation for invalid high/low."""
    with pytest.raises(ValueError, match=r"High .* cannot be less than low"):
        Bar(open=100.0, high=99.0, low=102.0, close=100.0)


def test_bar_invalid_close_range() -> None:
    """Test Bar validation for close outside range."""
    with pytest.raises(ValueError, match=r"Close .* must be between high .* and low"):
        Bar(open=100.0, high=102.0, low=99.0, close=103.0)

    with pytest.raises(ValueError, match=r"Close .* must be between high .* and low"):
        Bar(open=100.0, high=102.0, low=99.0, close=98.0)


def test_bar_invalid_open_range() -> None:
    """Test Bar validation for open outside range."""
    with pytest.raises(ValueError, match=r"Open .* must be between high .* and low"):
        Bar(open=103.0, high=102.0, low=99.0, close=100.0)

    with pytest.raises(ValueError, match=r"Open .* must be between high .* and low"):
        Bar(open=98.0, high=102.0, low=99.0, close=100.0)


# EMA Tests


def test_ema_creation() -> None:
    """Test EMA indicator creation."""
    ema = EMA(period=10)

    assert ema.period == 10
    assert ema.value is None
    assert not ema.is_warm


def test_ema_invalid_period() -> None:
    """Test EMA validation for invalid period."""
    with pytest.raises(ValueError, match="Period must be positive"):
        EMA(period=0)

    with pytest.raises(ValueError, match="Period must be positive"):
        EMA(period=-5)


def test_ema_warmup() -> None:
    """Test EMA warmup period."""
    ema = EMA(period=5)

    # Feed prices during warmup
    for i in range(4):
        ema.update(100.0 + i)
        assert not ema.is_warm
        assert ema.value is None

    # Fifth update should complete warmup with SMA
    ema.update(104.0)
    assert ema.is_warm
    assert ema.value == pytest.approx(102.0, rel=1e-6)  # (100+101+102+103+104)/5


def test_ema_calculation() -> None:
    """Test EMA calculation after warmup."""
    ema = EMA(period=3)

    # Warmup with 100, 100, 100 -> SMA = 100
    for _ in range(3):
        ema.update(100.0)

    assert ema.value == pytest.approx(100.0, rel=1e-6)

    # Update with 106 -> EMA should move toward 106
    # k = 2/(3+1) = 0.5
    # EMA = 106*0.5 + 100*0.5 = 103
    ema.update(106.0)
    assert ema.value == pytest.approx(103.0, rel=1e-6)

    # Update with 112 -> EMA = 112*0.5 + 103*0.5 = 107.5
    ema.update(112.0)
    assert ema.value == pytest.approx(107.5, rel=1e-6)


def test_ema_reset() -> None:
    """Test EMA reset."""
    ema = EMA(period=3)

    for _ in range(5):
        ema.update(100.0)

    assert ema.is_warm
    assert ema.value is not None

    ema.reset()

    assert not ema.is_warm
    assert ema.value is None


# ADX Tests


def test_adx_creation() -> None:
    """Test ADX indicator creation."""
    adx = ADX(period=14)

    assert adx.period == 14
    assert adx.value is None
    assert not adx.is_warm


def test_adx_invalid_period() -> None:
    """Test ADX validation for invalid period."""
    with pytest.raises(ValueError, match="Period must be positive"):
        ADX(period=0)


def test_adx_warmup() -> None:
    """Test ADX warmup period."""
    adx = ADX(period=5)

    # ADX needs period*2 bars to warm up
    for i in range(9):
        adx.update(high=100.0 + i, low=99.0 + i, close=99.5 + i)
        assert not adx.is_warm

    # 10th bar should complete warmup
    adx.update(high=109.0, low=108.0, close=108.5)
    assert adx.is_warm


def test_adx_trending_up() -> None:
    """Test ADX detects uptrend."""
    adx = ADX(period=5)

    # Strong uptrend - prices consistently rising
    for i in range(20):
        adx.update(
            high=100.0 + i * 2,
            low=99.0 + i * 2,
            close=99.5 + i * 2,
        )

    assert adx.is_warm
    assert adx.value is not None
    # Strong trend should have ADX > 20
    assert adx.value > 15.0


def test_adx_sideways() -> None:
    """Test ADX detects sideways market."""
    adx = ADX(period=5)

    # Sideways - no clear trend
    for i in range(20):
        price = 100.0 + (i % 2) * 0.5  # Oscillating slightly
        adx.update(high=price + 1, low=price - 1, close=price)

    assert adx.is_warm
    assert adx.value is not None
    # Weak trend should have lower ADX
    assert adx.value < 30.0


def test_adx_reset() -> None:
    """Test ADX reset."""
    adx = ADX(period=5)

    for i in range(15):
        adx.update(high=100.0 + i, low=99.0 + i, close=99.5 + i)

    assert adx.is_warm
    assert adx.value is not None

    adx.reset()

    assert not adx.is_warm
    assert adx.value is None


# ATR Tests


def test_atr_creation() -> None:
    """Test ATR indicator creation."""
    atr = ATR(period=14)

    assert atr.period == 14
    assert atr.value is None
    assert not atr.is_warm


def test_atr_invalid_period() -> None:
    """Test ATR validation for invalid period."""
    with pytest.raises(ValueError, match="Period must be positive"):
        ATR(period=0)


def test_atr_warmup() -> None:
    """Test ATR warmup period."""
    atr = ATR(period=5)

    # ATR needs period bars to warm up
    for _ in range(4):
        atr.update(high=102.0, low=98.0, close=100.0)
        assert not atr.is_warm

    # 5th bar should complete warmup
    atr.update(high=102.0, low=98.0, close=100.0)
    assert atr.is_warm


def test_atr_calculation() -> None:
    """Test ATR calculation."""
    atr = ATR(period=3)

    # First bar establishes previous close
    atr.update(high=102.0, low=98.0, close=100.0)

    # Second bar: TR = max(104-96, |104-100|, |96-100|) = 8
    atr.update(high=104.0, low=96.0, close=102.0)

    # Third bar: TR = max(103-99, |103-102|, |99-102|) = 4
    atr.update(high=103.0, low=99.0, close=101.0)

    assert atr.is_warm
    assert atr.value is not None
    # ATR should be averaging the TRs with Wilder's smoothing
    assert atr.value > 0


def test_atr_reset() -> None:
    """Test ATR reset."""
    atr = ATR(period=3)

    for _ in range(5):
        atr.update(high=102.0, low=98.0, close=100.0)

    assert atr.is_warm
    assert atr.value is not None

    atr.reset()

    assert not atr.is_warm
    assert atr.value is None


# RegimeDetector Tests


def test_regime_detector_creation() -> None:
    """Test RegimeDetector creation."""
    detector = RegimeDetector(
        ema_fast=10,
        ema_slow=20,
        adx_len=14,
        atr_len=14,
        hysteresis_bps=50.0,
    )

    assert detector.current() == Regime.SIDEWAYS
    assert not detector.is_warm


def test_regime_detector_invalid_ema_periods() -> None:
    """Test RegimeDetector validation for invalid EMA periods."""
    with pytest.raises(ValueError, match=r"Fast EMA .* must be less than slow EMA"):
        RegimeDetector(
            ema_fast=20,
            ema_slow=10,
            adx_len=14,
            atr_len=14,
            hysteresis_bps=50.0,
        )


def test_regime_detector_invalid_hysteresis() -> None:
    """Test RegimeDetector validation for invalid hysteresis."""
    with pytest.raises(ValueError, match="Hysteresis must be non-negative"):
        RegimeDetector(
            ema_fast=10,
            ema_slow=20,
            adx_len=14,
            atr_len=14,
            hysteresis_bps=-10.0,
        )


def test_regime_detector_warmup() -> None:
    """Test RegimeDetector warmup period."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Feed bars during warmup
    for _ in range(15):
        bar = Bar(open=100.0, high=101.0, low=99.0, close=100.0)
        detector.update_from_bar(bar)

    # Should be warm after enough bars
    assert detector.is_warm


def test_regime_detector_uptrend() -> None:
    """Test RegimeDetector detects uptrend."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Start with stable prices to warm up
    for _ in range(10):
        bar = Bar(open=100.0, high=101.0, low=99.0, close=100.0)
        detector.update_from_bar(bar)

    # Strong uptrend - fast EMA will rise above slow EMA
    for i in range(15):
        price = 100.0 + i * 2
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    # Should detect UP regime
    assert detector.current() == Regime.UP


def test_regime_detector_downtrend() -> None:
    """Test RegimeDetector detects downtrend."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Start with stable prices
    for _ in range(10):
        bar = Bar(open=100.0, high=101.0, low=99.0, close=100.0)
        detector.update_from_bar(bar)

    # Strong downtrend - fast EMA will fall below slow EMA
    for i in range(15):
        price = 100.0 - i * 2
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    # Should detect DOWN regime
    assert detector.current() == Regime.DOWN


def test_regime_detector_sideways() -> None:
    """Test RegimeDetector detects sideways market."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Sideways - oscillating prices
    for i in range(30):
        price = 100.0 + (i % 2) * 0.5
        bar = Bar(open=price, high=price + 0.5, low=price - 0.5, close=price)
        detector.update_from_bar(bar)

    # Should detect SIDEWAYS regime (ADX < 20 or within hysteresis)
    assert detector.current() == Regime.SIDEWAYS


def test_regime_detector_hysteresis() -> None:
    """Test RegimeDetector hysteresis prevents rapid switching."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=500.0,  # Large hysteresis band
    )

    # Start with uptrend
    for i in range(10):
        price = 100.0 + i
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    initial_regime = detector.current()

    # Small price movements should not change regime due to hysteresis
    for i in range(5):
        price = 109.0 + (i % 2) * 0.5
        bar = Bar(open=price, high=price + 0.5, low=price - 0.5, close=price)
        detector.update_from_bar(bar)

    # Regime should stay the same due to hysteresis
    assert detector.current() == initial_regime


def test_regime_detector_transition_up_to_down() -> None:
    """Test RegimeDetector transitions from UP to DOWN."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Uptrend
    for i in range(15):
        price = 100.0 + i * 2
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    assert detector.current() in [Regime.UP, Regime.SIDEWAYS]

    # Reversal to downtrend
    for i in range(20):
        price = 130.0 - i * 2
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    # Should transition to DOWN
    assert detector.current() == Regime.DOWN


def test_regime_detector_transition_down_to_up() -> None:
    """Test RegimeDetector transitions from DOWN to UP."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Downtrend
    for i in range(15):
        price = 100.0 - i * 2
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    assert detector.current() in [Regime.DOWN, Regime.SIDEWAYS]

    # Reversal to uptrend
    for i in range(20):
        price = 70.0 + i * 2
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    # Should transition to UP
    assert detector.current() == Regime.UP


def test_regime_detector_reset() -> None:
    """Test RegimeDetector reset."""
    detector = RegimeDetector(
        ema_fast=3,
        ema_slow=5,
        adx_len=3,
        atr_len=3,
        hysteresis_bps=50.0,
    )

    # Feed some bars
    for i in range(20):
        price = 100.0 + i
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    assert detector.is_warm

    detector.reset()

    assert not detector.is_warm
    assert detector.current() == Regime.SIDEWAYS


def test_regime_detector_with_config_params() -> None:
    """Test RegimeDetector with realistic config parameters."""
    # Use typical config values
    detector = RegimeDetector(
        ema_fast=20,
        ema_slow=50,
        adx_len=14,
        atr_len=14,
        hysteresis_bps=25.0,
    )

    # Warm up with stable prices
    for _ in range(60):
        bar = Bar(open=100.0, high=101.0, low=99.0, close=100.0)
        detector.update_from_bar(bar)

    assert detector.is_warm
    assert detector.current() == Regime.SIDEWAYS

    # Strong uptrend
    for i in range(30):
        price = 100.0 + i
        bar = Bar(open=price, high=price + 1, low=price - 1, close=price)
        detector.update_from_bar(bar)

    # Should detect trend
    assert detector.current() in [Regime.UP, Regime.SIDEWAYS]
