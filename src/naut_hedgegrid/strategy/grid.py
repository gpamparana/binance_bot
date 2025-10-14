"""Grid construction and adaptive re-centering for hedge grid strategy."""

from decimal import ROUND_HALF_UP, Decimal

from naut_hedgegrid.config.strategy import HedgeGridConfig
from naut_hedgegrid.domain.types import Ladder, Regime, Rung, Side


class GridEngine:
    """
    Grid construction engine for building price ladders with adaptive re-centering.

    Constructs LONG and SHORT ladders with geometric quantity scaling,
    TP/SL levels, and inventory cap validation. Supports regime-based
    ladder selection for directional bias.
    """

    @staticmethod
    def build_ladders(
        mid: float,
        cfg: HedgeGridConfig,
        regime: Regime,
    ) -> list[Ladder]:
        """Build grid ladders around mid price based on config and regime.

        Args:
            mid: Mid price to center grid around
            cfg: Strategy configuration with grid parameters
            regime: Current market regime for directional bias

        Returns:
            List of Ladder instances (1-2 ladders depending on regime)
            - UP regime: [SHORT ladder] only
            - DOWN regime: [LONG ladder] only
            - SIDEWAYS regime: [LONG ladder, SHORT ladder]

        Raises:
            ValueError: If mid price invalid or inventory caps exceeded

        """
        if mid <= 0:
            msg = f"Mid price must be positive, got {mid}"
            raise ValueError(msg)

        # Calculate price step from basis points using Decimal for precision
        mid_decimal = Decimal(str(mid))
        step_bps = Decimal(str(cfg.grid.grid_step_bps))
        price_step = mid_decimal * (step_bps / Decimal("10000"))

        # Build LONG ladder (below mid price)
        long_ladder = GridEngine._build_long_ladder(mid, price_step, cfg)

        # Build SHORT ladder (above mid price)
        short_ladder = GridEngine._build_short_ladder(mid, price_step, cfg)

        # Validate inventory caps
        GridEngine._validate_inventory_caps(long_ladder, short_ladder, mid, cfg)

        # Select ladders based on regime
        return GridEngine._select_ladders_by_regime(long_ladder, short_ladder, regime)

    @staticmethod
    def _build_long_ladder(
        mid: float,
        price_step: Decimal,
        cfg: HedgeGridConfig,
    ) -> Ladder:
        """Build LONG ladder with levels below mid price.

        Args:
            mid: Mid price
            price_step: Price increment between levels (as Decimal)
            cfg: Strategy configuration

        Returns:
            Ladder with LONG side rungs

        """
        rungs = []
        mid_decimal = Decimal(str(mid))
        base_qty_decimal = Decimal(str(cfg.grid.base_qty))
        qty_scale_decimal = Decimal(str(cfg.grid.qty_scale))

        for level in range(1, cfg.grid.grid_levels_long + 1):
            # Price below mid (using Decimal for precision)
            price_decimal = mid_decimal - (Decimal(level) * price_step)
            price = float(price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            # Geometric quantity scaling (using Decimal)
            qty_decimal = base_qty_decimal * (qty_scale_decimal ** (level - 1))
            qty = float(qty_decimal.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))

            # TP above entry, SL below entry
            tp = None
            if cfg.exit.tp_steps > 0:
                tp_decimal = price_decimal + (Decimal(cfg.exit.tp_steps) * price_step)
                tp = float(tp_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            sl = None
            if cfg.exit.sl_steps > 0:
                sl_decimal = price_decimal - (Decimal(cfg.exit.sl_steps) * price_step)
                # Ensure SL is positive
                if sl_decimal <= 0:
                    sl_decimal = price_decimal * Decimal("0.01")  # Minimum 1% of entry price
                sl = float(sl_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            rung = Rung(
                price=price,
                qty=qty,
                side=Side.LONG,
                tp=tp,
                sl=sl,
            )
            rungs.append(rung)

        return Ladder.from_list(Side.LONG, rungs)

    @staticmethod
    def _build_short_ladder(
        mid: float,
        price_step: Decimal,
        cfg: HedgeGridConfig,
    ) -> Ladder:
        """Build SHORT ladder with levels above mid price.

        Args:
            mid: Mid price
            price_step: Price increment between levels (as Decimal)
            cfg: Strategy configuration

        Returns:
            Ladder with SHORT side rungs

        """
        rungs = []
        mid_decimal = Decimal(str(mid))
        base_qty_decimal = Decimal(str(cfg.grid.base_qty))
        qty_scale_decimal = Decimal(str(cfg.grid.qty_scale))

        for level in range(1, cfg.grid.grid_levels_short + 1):
            # Price above mid (using Decimal for precision)
            price_decimal = mid_decimal + (Decimal(level) * price_step)
            price = float(price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            # Geometric quantity scaling (using Decimal)
            qty_decimal = base_qty_decimal * (qty_scale_decimal ** (level - 1))
            qty = float(qty_decimal.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))

            # TP below entry, SL above entry
            tp = None
            if cfg.exit.tp_steps > 0:
                tp_decimal = price_decimal - (Decimal(cfg.exit.tp_steps) * price_step)
                # Ensure TP is positive
                if tp_decimal <= 0:
                    tp_decimal = price_decimal * Decimal("0.01")  # Minimum 1% of entry price
                tp = float(tp_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            sl = None
            if cfg.exit.sl_steps > 0:
                sl_decimal = price_decimal + (Decimal(cfg.exit.sl_steps) * price_step)
                sl = float(sl_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            rung = Rung(
                price=price,
                qty=qty,
                side=Side.SHORT,
                tp=tp,
                sl=sl,
            )
            rungs.append(rung)

        return Ladder.from_list(Side.SHORT, rungs)

    @staticmethod
    def _validate_inventory_caps(
        long_ladder: Ladder,
        short_ladder: Ladder,
        mid: float,
        cfg: HedgeGridConfig,
    ) -> None:
        """Validate that ladders respect inventory caps.

        Args:
            long_ladder: LONG side ladder
            short_ladder: SHORT side ladder
            mid: Mid price for notional calculation
            cfg: Strategy configuration with max_inventory_quote

        Raises:
            ValueError: If either ladder exceeds max inventory

        """
        max_inventory = cfg.rebalance.max_inventory_quote

        # Calculate notional values
        long_notional = long_ladder.total_qty() * mid
        short_notional = short_ladder.total_qty() * mid

        if long_notional > max_inventory:
            msg = (
                f"LONG ladder exceeds max inventory: "
                f"{long_notional:.2f} > {max_inventory:.2f} quote currency. "
                f"Reduce grid_levels_long, base_qty, or qty_scale."
            )
            raise ValueError(msg)

        if short_notional > max_inventory:
            msg = (
                f"SHORT ladder exceeds max inventory: "
                f"{short_notional:.2f} > {max_inventory:.2f} quote currency. "
                f"Reduce grid_levels_short, base_qty, or qty_scale."
            )
            raise ValueError(msg)

    @staticmethod
    def _select_ladders_by_regime(
        long_ladder: Ladder,
        short_ladder: Ladder,
        regime: Regime,
    ) -> list[Ladder]:
        """Select ladders based on market regime.

        Args:
            long_ladder: LONG side ladder
            short_ladder: SHORT side ladder
            regime: Current market regime

        Returns:
            List of selected ladders
            - UP: [short_ladder] (sell into strength)
            - DOWN: [long_ladder] (buy into weakness)
            - SIDEWAYS: [long_ladder, short_ladder] (range trading)

        """
        if regime == Regime.UP:
            # Uptrend: favor SHORT ladder (sell into strength)
            return [short_ladder]
        if regime == Regime.DOWN:
            # Downtrend: favor LONG ladder (buy into weakness)
            return [long_ladder]
        # SIDEWAYS: both sides for range trading
        return [long_ladder, short_ladder]

    @staticmethod
    def recenter_needed(
        mid: float,
        last_center: float,
        cfg: HedgeGridConfig,
    ) -> bool:
        """Determine if grid needs re-centering based on price deviation.

        Args:
            mid: Current mid price
            last_center: Previous center price
            cfg: Strategy configuration with recenter_trigger_bps

        Returns:
            True if deviation exceeds threshold, False otherwise

        """
        if last_center == 0:
            return True  # Always recenter if no previous center

        # Calculate deviation in basis points using Decimal for precision
        mid_decimal = Decimal(str(mid))
        last_center_decimal = Decimal(str(last_center))
        deviation_bps = abs((mid_decimal - last_center_decimal) / last_center_decimal) * Decimal(
            "10000"
        )

        # Check if exceeds threshold
        return float(deviation_bps) > cfg.rebalance.recenter_trigger_bps
