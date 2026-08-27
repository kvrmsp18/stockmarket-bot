from datetime import datetime, timezone
import math

from .models import MarketSnapshot


def _is_finite_number(value: object) -> bool:
    """Return True only for finite real numeric values."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_snapshot(
    snapshot: MarketSnapshot,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 120,
    require_fresh: bool = False,
) -> list[str]:
    """
    Validate a market snapshot.

    Returns an empty list when valid.
    Returns descriptive validation errors when invalid.
    """

    errors: list[str] = []
    invalid_numeric_fields: set[str] = set()

    numeric_fields = (
        ("last_price", snapshot.last_price, "Last price"),
        ("previous_close", snapshot.previous_close, "Previous close"),
        ("volume", snapshot.volume, "Volume"),
        ("bid", snapshot.bid, "Bid"),
        ("ask", snapshot.ask, "Ask"),
    )

    for field_name, value, label in numeric_fields:
        if value is not None and not _is_finite_number(value):
            errors.append(f"{label} must be a finite number.")
            invalid_numeric_fields.add(field_name)

    if not snapshot.symbol or not snapshot.symbol.strip():
        errors.append("Symbol is required.")

    if not snapshot.exchange or not snapshot.exchange.strip():
        errors.append("Exchange is required.")

    if "last_price" not in invalid_numeric_fields and snapshot.last_price <= 0:
        errors.append("Last price must be greater than zero.")

    if (
        snapshot.previous_close is not None
        and "previous_close" not in invalid_numeric_fields
        and snapshot.previous_close <= 0
    ):
        errors.append("Previous close must be greater than zero.")

    if (
        snapshot.volume is not None
        and "volume" not in invalid_numeric_fields
        and snapshot.volume < 0
    ):
        errors.append("Volume cannot be negative.")

    if (
        snapshot.bid is not None
        and "bid" not in invalid_numeric_fields
        and snapshot.bid <= 0
    ):
        errors.append("Bid must be greater than zero.")

    if (
        snapshot.ask is not None
        and "ask" not in invalid_numeric_fields
        and snapshot.ask <= 0
    ):
        errors.append("Ask must be greater than zero.")

    if (
        snapshot.bid is not None
        and snapshot.ask is not None
        and "bid" not in invalid_numeric_fields
        and "ask" not in invalid_numeric_fields
        and snapshot.bid > snapshot.ask
    ):
        errors.append("Bid cannot be greater than ask.")

    if snapshot.timestamp.tzinfo is None:
        errors.append("Timestamp must include timezone information.")

    if require_fresh and snapshot.timestamp.tzinfo is not None:
        reference_time = now or datetime.now(timezone.utc)

        timestamp = snapshot.timestamp

        if timestamp > reference_time:
            errors.append("Timestamp cannot be in the future.")

        age = (reference_time - timestamp).total_seconds()

        if age > max_age_seconds:
            errors.append(
                f"Market data is stale: {age:.1f}s old "
                f"(maximum {max_age_seconds}s)."
            )

    return errors


def is_valid_snapshot(
    snapshot: MarketSnapshot,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 120,
    require_fresh: bool = False,
) -> bool:
    """Return True only when the market snapshot passes validation."""

    return not validate_snapshot(
        snapshot,
        now=now,
        max_age_seconds=max_age_seconds,
        require_fresh=require_fresh,
    )
