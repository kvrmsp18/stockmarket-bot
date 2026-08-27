"""Deterministic quality checks for historical research OHLCV data.

This module is provider-neutral and read-only. It validates the bars before
research, feature engineering, or backtesting so malformed or duplicated data
does not silently influence strategy results.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from typing import Sequence

from .research_market_data import ResearchBar


class ResearchDataQualityError(ValueError):
    """Raised when research data fails a mandatory quality check."""


@dataclass(frozen=True)
class ResearchDataQualityReport:
    """Quality assessment for one chronological OHLCV series."""

    symbol: str
    bar_count: int
    first_timestamp: object
    last_timestamp: object
    duplicate_timestamps: int
    chronological: bool
    invalid_bars: int
    non_positive_prices: int
    negative_volumes: int
    gap_count: int
    max_gap_seconds: float
    quality_score: float
    issues: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Return True when the series has no mandatory quality failures."""
        return not self.issues


def assess_research_data(
    bars: Sequence[ResearchBar],
    *,
    max_allowed_gap: timedelta | None = None,
) -> ResearchDataQualityReport:
    """Assess a historical OHLCV series without modifying the input.

    Mandatory checks cover timestamps, finite positive OHLC prices, OHLC
    relationships, and non-negative volume.  An optional ``max_allowed_gap``
    can flag unusually large gaps without assuming that weekends or market
    holidays are missing data.

    The score starts at 100 and applies transparent penalties for quality
    defects.  The score is informational; ``ready`` is controlled only by
    mandatory issues.
    """
    if not bars:
        raise ResearchDataQualityError("At least one research bar is required.")

    symbol = bars[0].symbol.strip().upper()
    if not symbol:
        raise ResearchDataQualityError("Research bars must contain a symbol.")

    issues: list[str] = []
    warnings: list[str] = []
    duplicate_timestamps = 0
    invalid_bars = 0
    non_positive_prices = 0
    negative_volumes = 0
    gap_count = 0
    max_gap_seconds = 0.0
    chronological = True

    seen_timestamps: set[object] = set()

    for index, bar in enumerate(bars):
        if bar.symbol.strip().upper() != symbol:
            issues.append(
                f"Mixed symbols detected at index {index}: {bar.symbol!r}."
            )

        if bar.timestamp in seen_timestamps:
            duplicate_timestamps += 1
        seen_timestamps.add(bar.timestamp)

        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(not isfinite(float(price)) for price in prices):
            invalid_bars += 1
            issues.append(f"Non-finite OHLC price at index {index}.")
        elif any(float(price) <= 0 for price in prices):
            non_positive_prices += 1
            invalid_bars += 1
            issues.append(f"Non-positive OHLC price at index {index}.")
        elif (
            bar.high < max(bar.open, bar.close, bar.low)
            or bar.low > min(bar.open, bar.close, bar.high)
        ):
            invalid_bars += 1
            issues.append(f"Invalid OHLC relationship at index {index}.")

        if bar.volume < 0:
            negative_volumes += 1
            invalid_bars += 1
            issues.append(f"Negative volume at index {index}.")

        if index > 0:
            previous = bars[index - 1]
            delta = bar.timestamp - previous.timestamp
            if delta.total_seconds() <= 0:
                chronological = False
                issues.append(
                    f"Timestamps are not strictly increasing at index {index}."
                )
            gap_seconds = max(delta.total_seconds(), 0.0)
            max_gap_seconds = max(max_gap_seconds, gap_seconds)

            if max_allowed_gap is not None and delta > max_allowed_gap:
                gap_count += 1
                warnings.append(
                    "Large timestamp gap between "
                    f"{previous.timestamp.isoformat()} and {bar.timestamp.isoformat()}."
                )

    if duplicate_timestamps:
        issues.append(f"Found {duplicate_timestamps} duplicate timestamp(s).")

    if not chronological:
        chronological = False

    if max_allowed_gap is not None and max_allowed_gap <= timedelta(0):
        raise ResearchDataQualityError("max_allowed_gap must be positive when supplied.")

    # Deduplicate repeated messages while preserving deterministic order.
    issues = list(dict.fromkeys(issues))
    warnings = list(dict.fromkeys(warnings))

    penalty = 0.0
    penalty += min(40.0, duplicate_timestamps * 20.0)
    penalty += min(40.0, invalid_bars * 10.0)
    penalty += min(20.0, len([issue for issue in issues if "symbol" in issue.lower()]) * 10.0)
    penalty += min(20.0, gap_count * 2.0)
    quality_score = round(max(0.0, 100.0 - penalty), 2)

    return ResearchDataQualityReport(
        symbol=symbol,
        bar_count=len(bars),
        first_timestamp=bars[0].timestamp,
        last_timestamp=bars[-1].timestamp,
        duplicate_timestamps=duplicate_timestamps,
        chronological=chronological,
        invalid_bars=invalid_bars,
        non_positive_prices=non_positive_prices,
        negative_volumes=negative_volumes,
        gap_count=gap_count,
        max_gap_seconds=round(max_gap_seconds, 3),
        quality_score=quality_score,
        issues=tuple(issues),
        warnings=tuple(warnings),
    )


def require_research_data_quality(
    bars: Sequence[ResearchBar],
    *,
    max_allowed_gap: timedelta | None = None,
) -> ResearchDataQualityReport:
    """Validate research bars and raise when mandatory quality checks fail."""
    report = assess_research_data(bars, max_allowed_gap=max_allowed_gap)
    if not report.ready:
        details = "; ".join(report.issues)
        raise ResearchDataQualityError(
            f"Research data quality check failed for {report.symbol}: {details}"
        )
    return report
