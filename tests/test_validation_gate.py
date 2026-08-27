import json
from datetime import date

import pytest

from src.paper_trading_validation import ValidationSummary
from src.validation_gate import CriteriaConfigError, evaluate, load_criteria, render_scorecard
from src.validation_store import ValidationStore


def _summary(period_start, **overrides) -> ValidationSummary:
    """Build a weekly ValidationSummary with sane defaults, overriding what a test cares about."""
    defaults = dict(
        period_end=period_start,
        signals=20,
        target_count=12,
        stop_count=6,
        eod_close_count=1,
        open_count=0,
        ambiguous_count=1,
        no_data_count=0,
        resolved_count=18,
        win_rate_percent=66.67,
        gross_profit=12000.0,
        gross_loss=4000.0,
        net_pnl=7500.0,
        profit_factor=3.0,
        average_winner=1000.0,
        average_loser=666.67,
        max_drawdown=2500.0,
    )
    defaults.update(overrides)
    return ValidationSummary(period_start=period_start, **defaults)


def _write_criteria(path, **overrides):
    payload = {
        "version": 1,
        "locked_at": "2026-09-01T00:00:00+05:30",
        "validation_period_start": "2026-09-01",
        "criteria": {
            "min_sample_size": {"metric": "total_signals", "minimum": 30},
            "min_risk_adjusted_performance": {"metric": "profit_factor", "minimum": 1.5},
            "max_acceptable_drawdown": {"metric": "max_drawdown", "maximum": 5000},
        },
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload))
    return path


# ---- load_criteria ---------------------------------------------------------

def test_load_criteria_missing_file(tmp_path):
    with pytest.raises(CriteriaConfigError, match="not found"):
        load_criteria(tmp_path / "missing.json")


def test_load_criteria_malformed_json(tmp_path):
    path = tmp_path / "criteria.json"
    path.write_text("{not valid json")
    with pytest.raises(CriteriaConfigError, match="not valid JSON"):
        load_criteria(path)


def test_load_criteria_missing_required_field(tmp_path):
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps({"version": 1, "criteria": {}}))
    with pytest.raises(CriteriaConfigError, match="locked_at"):
        load_criteria(path)


def test_load_criteria_empty_criteria_block(tmp_path):
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps({
        "version": 1, "locked_at": "2026-01-01T00:00:00+05:30",
        "validation_period_start": "2026-01-01", "criteria": {},
    }))
    with pytest.raises(CriteriaConfigError, match="at least one criterion"):
        load_criteria(path)


# ---- evaluate: the anti-hindsight lock check -------------------------------

def test_evaluate_rejects_criteria_locked_after_validation_start(tmp_path):
    criteria_path = _write_criteria(
        tmp_path / "criteria.json",
        locked_at="2026-09-15T00:00:00+05:30",  # after the Sep 1 window start
    )
    store = ValidationStore(tmp_path / "journal.jsonl")
    with pytest.raises(CriteriaConfigError, match="cannot be judged"):
        evaluate(store, criteria_path=criteria_path)


def test_evaluate_accepts_criteria_locked_before_validation_start(tmp_path):
    criteria_path = _write_criteria(
        tmp_path / "criteria.json",
        locked_at="2026-08-25T00:00:00+05:30",  # before the Sep 1 window start
    )
    store = ValidationStore(tmp_path / "journal.jsonl")
    scorecard = evaluate(store, criteria_path=criteria_path)
    assert scorecard.validation_period_start == date(2026, 9, 1)


# ---- evaluate: scoring behaviour --------------------------------------------

def test_evaluate_with_no_data_yet_fails_every_criterion_cleanly(tmp_path):
    criteria_path = _write_criteria(tmp_path / "criteria.json")
    store = ValidationStore(tmp_path / "journal.jsonl")

    scorecard = evaluate(store, criteria_path=criteria_path)

    assert scorecard.total_signals == 0
    assert scorecard.all_passed is False
    assert len(scorecard.failed) == 3
    assert all("not yet available" in item.detail for item in scorecard.failed)


def test_evaluate_pools_multiple_weeks_and_passes_when_thresholds_are_met(tmp_path):
    criteria_path = _write_criteria(tmp_path / "criteria.json")
    store = ValidationStore(tmp_path / "journal.jsonl")
    store.append_summary(_summary(date(2026, 9, 1), signals=20), report_type="weekly")
    store.append_summary(_summary(date(2026, 9, 8), signals=20), report_type="weekly")

    scorecard = evaluate(store, criteria_path=criteria_path, as_of=date(2026, 9, 14))

    assert scorecard.total_signals == 40  # pooled across both weeks
    assert scorecard.all_passed is True
    assert scorecard.failed == ()


def test_evaluate_fails_only_the_criterion_that_misses(tmp_path):
    criteria_path = _write_criteria(tmp_path / "criteria.json")
    store = ValidationStore(tmp_path / "journal.jsonl")
    # Sample size and profit factor are healthy, but drawdown blows the ceiling.
    store.append_summary(
        _summary(date(2026, 9, 1), signals=40, max_drawdown=9000.0),
        report_type="weekly",
    )

    scorecard = evaluate(store, criteria_path=criteria_path)

    assert scorecard.all_passed is False
    failed_names = {item.name for item in scorecard.failed}
    assert failed_names == {"max_acceptable_drawdown"}


def test_evaluate_ignores_weeks_before_the_validation_window(tmp_path):
    criteria_path = _write_criteria(tmp_path / "criteria.json")
    store = ValidationStore(tmp_path / "journal.jsonl")
    # A strong week that happened BEFORE the locked validation window started
    # must not be used to satisfy the gate -- otherwise the window start date
    # is meaningless.
    store.append_summary(_summary(date(2026, 8, 18), signals=1000), report_type="weekly")

    scorecard = evaluate(store, criteria_path=criteria_path)

    assert scorecard.total_signals == 0
    assert scorecard.all_passed is False


def test_zero_losing_trades_counts_as_passing_profit_factor(tmp_path):
    criteria_path = _write_criteria(tmp_path / "criteria.json")
    store = ValidationStore(tmp_path / "journal.jsonl")
    store.append_summary(
        _summary(date(2026, 9, 1), signals=40, gross_loss=0.0, profit_factor=None),
        report_type="weekly",
    )

    scorecard = evaluate(store, criteria_path=criteria_path)

    pf_result = next(item for item in scorecard.results if item.name == "min_risk_adjusted_performance")
    assert pf_result.passed is True
    assert pf_result.actual == float("inf")


# ---- render_scorecard -------------------------------------------------------

def test_render_scorecard_never_prefills_approval(tmp_path):
    criteria_path = _write_criteria(tmp_path / "criteria.json")
    store = ValidationStore(tmp_path / "journal.jsonl")
    store.append_summary(_summary(date(2026, 9, 1), signals=40), report_type="weekly")

    scorecard = evaluate(store, criteria_path=criteria_path)
    rendered = render_scorecard(scorecard)

    assert "Left blank" in rendered
    assert "Approved" not in rendered
    assert "PASS" in rendered  # sanity check the table actually rendered
