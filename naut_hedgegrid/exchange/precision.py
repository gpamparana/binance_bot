"""Exchange precision guards for price and quantity clamping.

This module provides precision validation and clamping to ensure all orders
conform to exchange rules for tick size, step size, and minimum notional value.
"""

from dataclasses import dataclass
from typing import Any

from naut_hedgegrid.domain.types import Rung


@dataclass(frozen=True)
class InstrumentPrecision:
    """
    Precision parameters for an instrument.

    These values define the exchange's rules for valid price and quantity values,
    ensuring orders meet minimum requirements and proper increments.
    """

    price_tick: float  # Minimum price increment
    qty_step: float  # Minimum quantity increment
    min_notional: float  # Minimum order value (price * qty)
    min_qty: float  # Minimum order quantity
    max_qty: float  # Maximum order quantity

    def __post_init__(self) -> None:
        """Validate precision parameters."""
        if self.price_tick <= 0:
            msg = f"price_tick must be positive, got {self.price_tick}"
            raise ValueError(msg)
        if self.qty_step <= 0:
            msg = f"qty_step must be positive, got {self.qty_step}"
            raise ValueError(msg)
        if self.min_notional < 0:
            msg = f"min_notional must be non-negative, got {self.min_notional}"
            raise ValueError(msg)
        if self.min_qty < 0:
            msg = f"min_qty must be non-negative, got {self.min_qty}"
            raise ValueError(msg)
        if self.max_qty <= 0:
            msg = f"max_qty must be positive, got {self.max_qty}"
            raise ValueError(msg)
        if self.min_qty > self.max_qty:
            msg = f"min_qty ({self.min_qty}) cannot exceed max_qty ({self.max_qty})"
            raise ValueError(msg)


class PrecisionGuard:
    """
    Precision guard for clamping prices and quantities to exchange rules.

    Ensures all order parameters conform to exchange precision requirements
    by rounding prices to ticks, quantities to steps, and validating minimums.
    """

    def __init__(
        self, instrument: Any | None = None, precision: InstrumentPrecision | None = None
    ) -> None:
        """Initialize precision guard.

        Args:
            instrument: Nautilus Instrument object (if provided, extracts precision)
            precision: Manual InstrumentPrecision (if instrument not provided)

        Raises:
            ValueError: If neither instrument nor precision provided

        Notes:
            Exactly one of instrument or precision must be provided.
            When using instrument, precision is extracted from Nautilus metadata.

        """
        if instrument is not None and precision is not None:
            msg = "Cannot provide both instrument and precision"
            raise ValueError(msg)
        if instrument is None and precision is None:
            msg = "Must provide either instrument or precision"
            raise ValueError(msg)

        if instrument is not None:
            # Extract precision from Nautilus instrument
            self._precision = self._extract_precision_from_instrument(instrument)
        else:
            self._precision = precision  # type: ignore[assignment]

    def _extract_precision_from_instrument(self, instrument: Any) -> InstrumentPrecision:
        """Extract precision parameters from Nautilus Instrument.

        Args:
            instrument: Nautilus Instrument object

        Returns:
            InstrumentPrecision with extracted values

        Notes:
            Nautilus instruments provide:
            - price_increment: tick size
            - size_increment: quantity step
            - min_notional: minimum order value
            - min_quantity: minimum order size
            - max_quantity: maximum order size

        """
        return InstrumentPrecision(
            price_tick=float(instrument.price_increment),
            qty_step=float(instrument.size_increment),
            min_notional=float(getattr(instrument, "min_notional", 0.0)),
            min_qty=float(instrument.min_quantity),
            max_qty=float(instrument.max_quantity),
        )

    def clamp_price(self, price: float) -> float:
        """Round price to nearest valid tick.

        Args:
            price: Original price

        Returns:
            Price rounded to nearest tick increment

        Notes:
            Rounds to nearest tick (not down) to minimize distance from desired price.
            For example, with tick=0.01:
            - 100.123 → 100.12
            - 100.126 → 100.13

        """
        tick = self._precision.price_tick
        return round(price / tick) * tick

    def clamp_qty(self, qty: float) -> float:
        """Round quantity down to valid step and enforce min/max.

        Args:
            qty: Original quantity

        Returns:
            Quantity clamped to valid range and step

        Notes:
            - Rounds DOWN to nearest step (conservative for risk management)
            - Enforces min_qty (returns 0 if below minimum)
            - Enforces max_qty (caps at maximum)
            - Returns 0 if quantity becomes invalid after clamping

        """
        import math

        step = self._precision.qty_step

        # Round down to nearest step using floor
        clamped = math.floor(qty / step) * step

        # Check minimum
        if clamped < self._precision.min_qty:
            return 0.0

        # Check maximum
        if clamped > self._precision.max_qty:
            return self._precision.max_qty

        return clamped

    def validate_notional(self, price: float, qty: float) -> bool:
        """Check if order meets minimum notional value.

        Args:
            price: Order price
            qty: Order quantity

        Returns:
            True if price * qty >= min_notional, False otherwise

        """
        notional = price * qty
        return notional >= self._precision.min_notional

    def clamp_rung(self, rung: Rung) -> Rung | None:
        """Apply all precision guards to a rung.

        Args:
            rung: Original rung with desired price/qty

        Returns:
            New Rung with clamped values, or None if invalid

        Notes:
            Returns None if:
            - Quantity becomes zero after clamping
            - Notional value below minimum after clamping
            - Price/quantity cannot be adjusted to meet requirements

            Preserves all other rung attributes (side, tp, sl, tag).

        """
        # Clamp price to tick
        clamped_price = self.clamp_price(rung.price)

        # Clamp quantity to step and limits
        clamped_qty = self.clamp_qty(rung.qty)

        # Check if quantity is still valid
        if clamped_qty <= 0:
            return None

        # Check minimum notional
        if not self.validate_notional(clamped_price, clamped_qty):
            return None

        # Create new rung with clamped values
        return Rung(
            price=clamped_price,
            qty=clamped_qty,
            side=rung.side,
            tp=rung.tp,
            sl=rung.sl,
            tag=rung.tag,
        )

    @property
    def precision(self) -> InstrumentPrecision:
        """Get precision parameters.

        Returns:
            InstrumentPrecision with current settings

        """
        return self._precision
