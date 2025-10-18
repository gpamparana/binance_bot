"""Shared domain types for the hedge grid trading system."""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Regime(Enum):
    """Market regime classification for strategy adaptation."""

    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"

    def __str__(self) -> str:
        """String representation."""
        return self.value

    @classmethod
    def from_string(cls, value: str) -> "Regime":
        """Create Regime from string value.

        Args:
            value: String value (case-insensitive)

        Returns:
            Regime enum value

        Raises:
            ValueError: If value is not a valid regime

        """
        try:
            return cls[value.upper()]
        except KeyError as e:
            msg = f"Invalid regime: {value}. Must be one of: UP, DOWN, SIDEWAYS"
            raise ValueError(msg) from e


class Side(Enum):
    """Trading side for positions and orders."""

    LONG = "LONG"
    SHORT = "SHORT"

    def __str__(self) -> str:
        """String representation."""
        return self.value

    @property
    def opposite(self) -> "Side":
        """Get the opposite side.

        Returns:
            Opposite trading side

        """
        return Side.SHORT if self == Side.LONG else Side.LONG

    @classmethod
    def from_string(cls, value: str) -> "Side":
        """Create Side from string value.

        Args:
            value: String value (case-insensitive)

        Returns:
            Side enum value

        Raises:
            ValueError: If value is not a valid side

        """
        try:
            return cls[value.upper()]
        except KeyError as e:
            msg = f"Invalid side: {value}. Must be one of: LONG, SHORT"
            raise ValueError(msg) from e


@dataclass(frozen=True)
class Rung:
    """
    A single rung (price level) in a grid ladder.

    Represents a specific price level where an order should be placed,
    along with its associated parameters like quantity, take profit,
    and stop loss levels.
    """

    price: float
    qty: float
    side: Side
    tp: float | None = None
    sl: float | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        """Validate rung parameters."""
        if self.price <= 0:
            msg = f"Price must be positive, got {self.price}"
            raise ValueError(msg)
        if self.qty <= 0:
            msg = f"Quantity must be positive, got {self.qty}"
            raise ValueError(msg)
        if self.tp is not None and self.tp <= 0:
            msg = f"Take profit must be positive, got {self.tp}"
            raise ValueError(msg)
        if self.sl is not None and self.sl <= 0:
            msg = f"Stop loss must be positive, got {self.sl}"
            raise ValueError(msg)

    def with_tag(self, tag: str) -> "Rung":
        """Create a new Rung with updated tag.

        Args:
            tag: New tag value

        Returns:
            New Rung instance with updated tag

        """
        return Rung(
            price=self.price,
            qty=self.qty,
            side=self.side,
            tp=self.tp,
            sl=self.sl,
            tag=tag,
        )

    def distance_from(self, price: float) -> float:
        """Calculate absolute distance from given price.

        Args:
            price: Reference price

        Returns:
            Absolute price distance

        """
        return abs(self.price - price)

    def distance_bps_from(self, price: float) -> float:
        """Calculate distance from given price in basis points.

        Args:
            price: Reference price

        Returns:
            Distance in basis points

        """
        if price == 0:
            return 0.0
        return (abs(self.price - price) / price) * 10000


@dataclass(frozen=True)
class Ladder:
    """
    A collection of rungs forming a grid ladder on one side.

    Represents all the price levels for either LONG or SHORT positions
    in the grid strategy.
    """

    side: Side
    rungs: tuple[Rung, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate ladder structure."""
        # Check all rungs have same side
        if self.rungs:
            for rung in self.rungs:
                if rung.side != self.side:
                    msg = f"All rungs must have side {self.side}, found rung with side {rung.side}"
                    raise ValueError(msg)

    @classmethod
    def from_list(cls, side: Side, rungs: list[Rung]) -> "Ladder":
        """Create Ladder from list of rungs.

        Args:
            side: Trading side for the ladder
            rungs: List of rung instances

        Returns:
            New Ladder instance

        """
        return cls(side=side, rungs=tuple(rungs))

    def __len__(self) -> int:
        """Return number of rungs in ladder."""
        return len(self.rungs)

    def __iter__(self) -> Iterator[Rung]:
        """Iterate over rungs."""
        return iter(self.rungs)

    def __getitem__(self, index: int) -> Rung:
        """Get rung by index."""
        return self.rungs[index]

    @property
    def is_empty(self) -> bool:
        """Check if ladder has no rungs."""
        return len(self.rungs) == 0

    def sorted_by_price(self, *, ascending: bool = True) -> "Ladder":
        """Return new Ladder with rungs sorted by price.

        Args:
            ascending: Sort ascending if True, descending if False

        Returns:
            New Ladder instance with sorted rungs

        """
        sorted_rungs = sorted(self.rungs, key=lambda r: r.price, reverse=not ascending)
        return Ladder(side=self.side, rungs=tuple(sorted_rungs))

    def filter_by_tag(self, tag: str) -> "Ladder":
        """Return new Ladder with only rungs matching tag.

        Args:
            tag: Tag to filter by

        Returns:
            New Ladder instance with filtered rungs

        """
        filtered = [r for r in self.rungs if r.tag == tag]
        return Ladder(side=self.side, rungs=tuple(filtered))

    def total_qty(self) -> float:
        """Calculate total quantity across all rungs.

        Returns:
            Sum of all rung quantities

        """
        return sum(r.qty for r in self.rungs)


@dataclass(frozen=True)
class OrderIntent:
    """
    Intent to create, cancel, or replace an order.

    Uses idempotent client_order_id to ensure operations can be safely retried.
    """

    action: Literal["CREATE", "CANCEL", "REPLACE"]
    client_order_id: str
    side: Side | None = None
    price: float | None = None
    qty: float | None = None
    replace_with: str | None = None  # New client_order_id for REPLACE action
    metadata: dict[str, str] = field(default_factory=dict)
    retry_count: int = 0  # Number of retries attempted so far
    original_price: float | None = None  # Original price before any adjustments

    def __post_init__(self) -> None:
        """Validate order intent parameters."""
        if not self.client_order_id:
            msg = "client_order_id is required"
            raise ValueError(msg)

        if self.action == "CREATE":
            if self.side is None:
                msg = "side is required for CREATE action"
                raise ValueError(msg)
            if self.price is None or self.price <= 0:
                msg = f"Valid price is required for CREATE action, got {self.price}"
                raise ValueError(msg)
            if self.qty is None or self.qty <= 0:
                msg = f"Valid qty is required for CREATE action, got {self.qty}"
                raise ValueError(msg)

        if self.action == "REPLACE" and not self.replace_with:
            msg = "replace_with is required for REPLACE action"
            raise ValueError(msg)

    @classmethod
    def create(
        cls,
        client_order_id: str,
        side: Side,
        price: float,
        qty: float,
        metadata: dict[str, str] | None = None,
    ) -> "OrderIntent":
        """Create an order creation intent.

        Args:
            client_order_id: Unique client order ID
            side: Trading side
            price: Order price
            qty: Order quantity
            metadata: Optional metadata

        Returns:
            OrderIntent for creating an order

        """
        return cls(
            action="CREATE",
            client_order_id=client_order_id,
            side=side,
            price=price,
            qty=qty,
            metadata=metadata or {},
        )

    @classmethod
    def cancel(
        cls,
        client_order_id: str,
        metadata: dict[str, str] | None = None,
    ) -> "OrderIntent":
        """Create an order cancellation intent.

        Args:
            client_order_id: Client order ID to cancel
            metadata: Optional metadata

        Returns:
            OrderIntent for canceling an order

        """
        return cls(
            action="CANCEL",
            client_order_id=client_order_id,
            metadata=metadata or {},
        )

    @classmethod
    def replace(
        cls,
        client_order_id: str,
        replace_with: str,
        side: Side,
        price: float,
        qty: float,
        metadata: dict[str, str] | None = None,
    ) -> "OrderIntent":
        """Create an order replacement intent.

        Args:
            client_order_id: Original client order ID to replace
            replace_with: New client order ID
            side: Trading side for new order
            price: New order price
            qty: New order quantity
            metadata: Optional metadata

        Returns:
            OrderIntent for replacing an order

        """
        return cls(
            action="REPLACE",
            client_order_id=client_order_id,
            replace_with=replace_with,
            side=side,
            price=price,
            qty=qty,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class DiffResult:
    """
    Result of comparing desired state vs current state for grid orders.

    Contains lists of operations needed to reconcile the difference:
    - adds: New orders to create
    - cancels: Existing orders to cancel
    - replaces: Orders to replace (cancel + create)
    """

    adds: tuple[OrderIntent, ...] = field(default_factory=tuple)
    cancels: tuple[OrderIntent, ...] = field(default_factory=tuple)
    replaces: tuple[OrderIntent, ...] = field(default_factory=tuple)

    @classmethod
    def from_lists(
        cls,
        adds: list[OrderIntent],
        cancels: list[OrderIntent],
        replaces: list[OrderIntent],
    ) -> "DiffResult":
        """Create DiffResult from lists.

        Args:
            adds: List of orders to add
            cancels: List of orders to cancel
            replaces: List of orders to replace

        Returns:
            New DiffResult instance

        """
        return cls(
            adds=tuple(adds),
            cancels=tuple(cancels),
            replaces=tuple(replaces),
        )

    @property
    def is_empty(self) -> bool:
        """Check if diff result has no operations."""
        return not (self.adds or self.cancels or self.replaces)

    @property
    def total_operations(self) -> int:
        """Count total number of operations."""
        return len(self.adds) + len(self.cancels) + len(self.replaces)

    def __len__(self) -> int:
        """Return total number of operations."""
        return self.total_operations


# Client Order ID Formatting Helpers


def format_client_order_id(
    strategy: str,
    side: Side,
    level: int,
    timestamp: int | None = None,
) -> str:
    """
    Format a client order ID for idempotent order management.

    Format: {strategy}-{side}-{level}-{timestamp}
    Example: HG1-LONG-05-1234567890123

    Args:
        strategy: Strategy identifier (e.g., "HG1" for HedgeGrid v1)
        side: Trading side
        level: Grid level number
        timestamp: Unix timestamp in milliseconds (defaults to current time)

    Returns:
        Formatted client order ID

    """
    if timestamp is None:
        timestamp = int(time.time() * 1000)

    return f"{strategy}-{side.value}-{level:02d}-{timestamp}"


def parse_client_order_id(client_order_id: str) -> dict[str, str | int | Side]:
    """
    Parse a client order ID into its components.

    Args:
        client_order_id: Client order ID to parse

    Returns:
        Dictionary with keys: strategy, side, level, timestamp

    Raises:
        ValueError: If client order ID format is invalid

    """
    EXPECTED_PARTS = 4  # noqa: N806
    parts = client_order_id.split("-")
    if len(parts) != EXPECTED_PARTS:
        msg = (
            f"Invalid client order ID format: {client_order_id}. "
            f"Expected format: STRATEGY-SIDE-LEVEL-TIMESTAMP"
        )
        raise ValueError(msg)

    strategy, side_str, level_str, timestamp_str = parts

    try:
        side = Side.from_string(side_str)
        level = int(level_str)
        timestamp = int(timestamp_str)
    except (ValueError, KeyError) as e:
        msg = f"Failed to parse client order ID {client_order_id}: {e}"
        raise ValueError(msg) from e

    return {
        "strategy": strategy,
        "side": side,
        "level": level,
        "timestamp": timestamp,
    }
