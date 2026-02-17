"""Funding rate guard for managing exposure around funding timestamps."""

from datetime import datetime

from naut_hedgegrid.domain.types import Ladder, Rung, Side


class FundingGuard:
    """
    Funding rate guard for managing exposure around funding timestamps.

    Protects against excessive funding costs by shrinking or suspending
    counter-trend ladders when projected funding exceeds threshold within
    the configured time window before funding timestamp.

    Gradually scales down quantities as funding time approaches, reaching
    zero at the funding timestamp if cost exceeds maximum threshold.
    """

    # Standard funding interval used by most perpetual futures exchanges
    FUNDING_INTERVAL_HOURS = 8

    def __init__(self, window_minutes: int, max_cost_bps: float) -> None:
        """Initialize funding guard.

        Args:
            window_minutes: Time window before funding to start reducing exposure
            max_cost_bps: Maximum acceptable funding cost in basis points

        """
        if window_minutes <= 0:
            msg = f"Window minutes must be positive, got {window_minutes}"
            raise ValueError(msg)
        if max_cost_bps < 0:
            msg = f"Max cost bps must be non-negative, got {max_cost_bps}"
            raise ValueError(msg)

        self._window_minutes = window_minutes
        self._max_cost_bps = max_cost_bps
        self._current_rate: float | None = None
        self._next_funding_ts: datetime | None = None

    def on_funding_update(self, rate: float, next_ts: datetime) -> None:
        """Update funding rate and next funding timestamp.

        Args:
            rate: Funding rate as decimal (e.g., 0.0001 = 0.01%)
            next_ts: Datetime of next funding event

        """
        self._current_rate = rate
        self._next_funding_ts = next_ts

    def adjust_ladders(
        self,
        ladders: list[Ladder],
        now: datetime,
    ) -> list[Ladder]:
        """Adjust ladder quantities based on funding proximity and cost.

        Args:
            ladders: Ladders to potentially adjust (from Grid + Policy)
            now: Current datetime for time calculation

        Returns:
            Adjusted ladders with scaled quantities if funding cost high,
            otherwise unchanged ladders

        Notes:
            - Returns unchanged if no funding data available
            - Returns unchanged if outside time window
            - Returns unchanged if projected cost below threshold
            - Scales quantities linearly from 1.0 (at window edge) to 0.0 (at funding)
            - Only affects the side that pays funding (determined by rate sign)

        """
        # Check if funding data available
        if self._current_rate is None or self._next_funding_ts is None:
            return ladders

        # Calculate time until funding in minutes
        time_delta = self._next_funding_ts - now
        minutes_until = time_delta.total_seconds() / 60

        # Only adjust within time window (and not if funding already passed)
        if minutes_until > self._window_minutes or minutes_until < 0:
            return ladders

        # Calculate projected funding cost
        projected_cost = self._calculate_projected_cost()

        # If cost acceptable, no adjustment needed
        if projected_cost <= self._max_cost_bps:
            return ladders

        # Determine which side pays funding
        paying_side = self._get_paying_side()

        # Calculate scale factor: 1.0 at window edge, 0.0 at funding time
        scale_factor = max(0.0, minutes_until / self._window_minutes)

        # Apply scaling to paying side
        return self._scale_ladders(ladders, paying_side, scale_factor)

    def _calculate_projected_cost(self) -> float:
        """Calculate projected funding cost in bps over time window.

        Returns:
            Projected cost in basis points

        """
        if self._current_rate is None:
            return 0.0

        # Convert rate to bps (rate is decimal, bps = rate * 10000)
        rate_bps = abs(self._current_rate) * 10000

        # Calculate how many funding periods occur in the window
        hours_in_window = self._window_minutes / 60
        funding_periods = hours_in_window / self.FUNDING_INTERVAL_HOURS

        # Total projected cost
        return rate_bps * funding_periods

    def _get_paying_side(self) -> Side:
        """Determine which side pays funding based on rate sign.

        Uses Binance convention for funding rate sign:
        - Positive rate: longs pay shorts (most common in bull markets)
        - Negative rate: shorts pay longs (common in bear/low-interest markets)

        Returns:
            Side that pays funding:
            - Positive rate → LONG (longs pay)
            - Negative rate → SHORT (shorts pay)

        """
        if self._current_rate is None:
            return Side.LONG  # Default, shouldn't reach here

        # Binance convention: positive rate = longs pay, negative rate = shorts pay
        return Side.SHORT if self._current_rate < 0 else Side.LONG

    def _scale_ladders(
        self,
        ladders: list[Ladder],
        target_side: Side,
        scale_factor: float,
    ) -> list[Ladder]:
        """Scale quantities on target side by factor.

        Args:
            ladders: List of ladders to adjust
            target_side: Side to scale (paying side)
            scale_factor: Scaling factor (0.0-1.0)

        Returns:
            New list of ladders with target side scaled

        """
        result = []
        for ladder in ladders:
            if ladder.side == target_side:
                # Scale this ladder's quantities
                scaled = self._scale_ladder_quantities(ladder, scale_factor)
                result.append(scaled)
            else:
                # Keep unchanged
                result.append(ladder)
        return result

    def _scale_ladder_quantities(
        self,
        ladder: Ladder,
        factor: float,
    ) -> Ladder:
        """Create new ladder with scaled quantities.

        Args:
            ladder: Original ladder
            factor: Scaling factor to apply to quantities

        Returns:
            New Ladder with scaled quantities (immutable)

        Notes:
            Rungs with zero or negative quantity after scaling are filtered out
            since Rung validation requires positive quantities

        """
        scaled_rungs = []
        for rung in ladder.rungs:
            scaled_qty = rung.qty * factor
            # Filter out zero or negative quantities (Rung requires positive qty)
            if scaled_qty > 0:
                scaled_rungs.append(
                    Rung(
                        price=rung.price,
                        qty=scaled_qty,
                        side=rung.side,
                        tp=rung.tp,
                        sl=rung.sl,
                        tag=rung.tag,
                    )
                )
        return Ladder.from_list(ladder.side, scaled_rungs)

    @property
    def is_active(self) -> bool:
        """Check if guard has funding data and can operate.

        Returns:
            True if funding rate and timestamp are available

        """
        return self._current_rate is not None and self._next_funding_ts is not None

    @property
    def current_rate(self) -> float | None:
        """Get current funding rate.

        Returns:
            Current rate or None if not set

        """
        return self._current_rate

    @property
    def next_funding_ts(self) -> datetime | None:
        """Get next funding timestamp.

        Returns:
            Next funding datetime or None if not set

        """
        return self._next_funding_ts
