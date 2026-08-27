"""Objective pass/fail evaluation for the live-trading readiness gate.

This module answers exactly one question: given the paper-trading validation
already recorded in a ValidationStore, does the strategy meet criteria that
were locked in *before* those results existed? It never places an order and
never touches DHAN_LIVE_TRADING_ENABLED -- it only produces a scorecard for a
person to read before making that decision by hand.

Criteria live in a versioned JSON file with a `locked_at` timestamp. If that
timestamp falls after the validation period it is being used to judge, this
module refuses to produce a verdict at all, rather than silently letting a
threshold be tuned to fit a result that already happened.

Each weekly ValidationSummary already recorded by paper_trading_validation.py
carries every number this gate needs (signal count, win/loss counts, P&L,
drawdown). This module pools those stored weekly numbers across the
validation window rather than re-deriving them from raw signals/outcomes, so
the gate's verdict always agrees with the weekly reports a person already
read -- it is a second opinion on the same numbers, not a competing
calculation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .validation_store import ValidationStore

DEFAULT_CRITERIA_PATH = "config/validation_criteria.json"


class CriteriaConfigError(ValueError):
    """Raised when the criteria file is missing, malformed, or locked too late."""


@dataclass(frozen=True)
class CriterionResult:
    """One evaluated line of the scorecard."""

    name: str
    metric: str
    comparison: str  # "minimum" or "maximum"
    threshold: float
    actual: float | None
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReadinessScorecard:
    """One evaluation of the accumulated paper-trading record against locked criteria."""

    validation_period_start: date
    evaluated_as_of: date
    criteria_locked_at: datetime
    total_signals: int
    results: tuple[CriterionResult, ...]

    @property
    def all_passed(self) -> bool:
        """True only when every criterion passed and at least one was evaluated."""
        return bool(self.results) and all(item.passed for item in self.results)

    @property
    def failed(self) -> tuple[CriterionResult, ...]:
        return tuple(item for item in self.results if not item.passed)


def load_criteria(path: str | Path = DEFAULT_CRITERIA_PATH) -> dict[str, Any]:
    """Load the locked criteria file. Raises CriteriaConfigError if unusable."""
    config_path = Path(path)
    if not config_path.exists():
        raise CriteriaConfigError(f"Criteria config not found at {config_path}.")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CriteriaConfigError(f"Criteria config at {config_path} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise CriteriaConfigError("Criteria config must be a JSON object.")
    for required in ("version", "locked_at", "validation_period_start", "criteria"):
        if required not in payload:
            raise CriteriaConfigError(f"Criteria config is missing required field '{required}'.")
    if not isinstance(payload["criteria"], dict) or not payload["criteria"]:
        raise CriteriaConfigError("Criteria config must define at least one criterion.")
    return payload


def _parse_date(value: Any, *, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CriteriaConfigError(f"'{field}' must be an ISO date (YYYY-MM-DD).") from exc


def _parse_locked_at(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CriteriaConfigError("'locked_at' must be an ISO 8601 timestamp.") from exc


def _pooled_metrics(store: ValidationStore, *, since: date) -> dict[str, float]:
    """Pool every weekly ValidationSummary recorded on/after `since`.

    Uses the stored `resolved_count` / `target_count` / etc. as-is rather
    than recomputing from raw outcomes, so this never drifts from whatever
    definition of "resolved" or "win" summarize_outcomes() already applied.
    """
    weekly = [
        record["payload"]
        for record in store.summaries("weekly")
        if _parse_date(record["payload"]["period_start"], field="period_start") >= since
    ]
    if not weekly:
        return {}

    total_signals = sum(item["signals"] for item in weekly)
    resolved = sum(item["resolved_count"] for item in weekly)
    target = sum(item["target_count"] for item in weekly)
    no_data = sum(item["no_data_count"] for item in weekly)
    gross_profit = sum(item["gross_profit"] for item in weekly)
    gross_loss = sum(item["gross_loss"] for item in weekly)

    if gross_loss > 0:
        profit_factor: float | None = round(gross_profit / gross_loss, 4)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    return {
        "total_signals": float(total_signals),
        "win_rate_percent": round((target / resolved) * 100.0, 2) if resolved else 0.0,
        "net_pnl": round(sum(item["net_pnl"] for item in weekly), 2),
        "profit_factor": profit_factor,
        "max_drawdown": max((item["max_drawdown"] for item in weekly), default=0.0),
        "no_data_rate_percent": round((no_data / total_signals) * 100.0, 2) if total_signals else 0.0,
    }


def evaluate(
    store: ValidationStore,
    *,
    criteria_path: str | Path = DEFAULT_CRITERIA_PATH,
    as_of: date | None = None,
) -> ReadinessScorecard:
    """Evaluate the accumulated weekly record against locked criteria.

    Raises CriteriaConfigError if the criteria were locked after the
    validation period they are being used to judge -- the check that stops
    the bar being moved to fit a result that already happened.
    """
    payload = load_criteria(criteria_path)
    validation_start = _parse_date(payload["validation_period_start"], field="validation_period_start")
    locked_at = _parse_locked_at(payload["locked_at"])

    if locked_at.date() > validation_start:
        raise CriteriaConfigError(
            f"Criteria were locked on {locked_at.date().isoformat()}, which is after the "
            f"validation period start of {validation_start.isoformat()}. Results from this "
            "window cannot be judged against criteria set after the window began."
        )

    metrics = _pooled_metrics(store, since=validation_start)
    results: list[CriterionResult] = []

    for name, rule in payload["criteria"].items():
        metric_name = rule["metric"]
        actual = metrics.get(metric_name)

        if "minimum" in rule:
            threshold, comparison = float(rule["minimum"]), "minimum"
            passed = actual is not None and actual >= threshold
        elif "maximum" in rule:
            threshold, comparison = float(rule["maximum"]), "maximum"
            passed = actual is not None and actual <= threshold
        else:
            raise CriteriaConfigError(f"Criterion '{name}' must define a 'minimum' or 'maximum'.")

        if actual is None:
            detail = f"{metric_name} is not yet available."
        else:
            detail = f"{metric_name}={actual} vs {comparison} {threshold}"

        results.append(CriterionResult(
            name=name,
            metric=metric_name,
            comparison=comparison,
            threshold=threshold,
            actual=actual,
            passed=passed,
            detail=detail,
        ))

    return ReadinessScorecard(
        validation_period_start=validation_start,
        evaluated_as_of=as_of or date.today(),
        criteria_locked_at=locked_at,
        total_signals=int(metrics.get("total_signals", 0)),
        results=tuple(results),
    )


def _format_number(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _format_actual(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value == float("inf"):
        return "no losing trades"
    return _format_number(value)


def render_scorecard(scorecard: ReadinessScorecard, *, decider: str = "") -> str:
    """Render the readiness record for a person to review and sign.

    The Decision section is always left blank for manual entry -- this
    function never writes "Approved", regardless of how the scorecard came
    out.
    """
    lines = [
        f"# Live-Trading Readiness Decision — {scorecard.evaluated_as_of.isoformat()}",
        "",
        "**Status:** Draft (awaiting review)",
        f"**Decider:** {decider}",
        f"**Criteria locked:** {scorecard.criteria_locked_at.isoformat()}",
        f"**Validation window:** {scorecard.validation_period_start.isoformat()} "
        f"to {scorecard.evaluated_as_of.isoformat()} ({scorecard.total_signals} signals)",
        "",
        "## Criteria Scorecard",
        "| Criterion | Threshold | Actual | Result |",
        "|---|---|---|---|",
    ]
    for item in scorecard.results:
        lines.append(
            f"| {item.name} | {item.comparison} {_format_number(item.threshold)} | "
            f"{_format_actual(item.actual)} | {'PASS' if item.passed else 'FAIL'} |"
        )
    lines += [
        "",
        "## Decision",
        "_Left blank — manual entry only. A passing scorecard is evidence, not an approval._",
    ]
    return "\n".join(lines)
