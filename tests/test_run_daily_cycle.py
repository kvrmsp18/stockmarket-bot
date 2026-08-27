from datetime import date, datetime, timezone

from src.paper_trading_validation import PaperOutcome
from src.stock_monitor import StockMonitorRow, StockMonitorSnapshot
from src.validation_store import ValidationStore
from scripts.run_daily_cycle import _symbols_with_open_signal_today, signals_from_snapshot


def _row(symbol="RELIANCE", **overrides):
    defaults = dict(
        exchange="NSE", direction="BUY", status="ACTIONABLE", price=2900.0,
        entry=2900.0, stop_loss=2870.0, target=2960.0, confidence=0.88,
        risk_reward=2.0, potential_percent=2.07, ai_quantity=10, selected_quantity=10,
        maximum_safe_quantity=15, risk_amount=300.0, capital_required=29000.0,
        risk_percent=1.0, reason="test", chart_bars=(),
    )
    defaults.update(overrides)
    return StockMonitorRow(symbol=symbol, **defaults)


def _snapshot(rows):
    buy = sum(1 for r in rows if r.direction == "BUY")
    return StockMonitorSnapshot(
        filtered_count=len(rows), scanned_count=26, actionable_count=len(rows),
        buy_count=buy, sell_count=len(rows) - buy, rows=tuple(rows),
    )


# ---- signals_from_snapshot ---------------------------------------------

def test_freezes_one_signal_per_row_using_ai_quantity_not_selected():
    row = _row(ai_quantity=10, selected_quantity=25)  # simulates a dashboard override
    snapshot = _snapshot([row])
    when = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)

    signals = signals_from_snapshot(snapshot, generated_at=when)

    assert len(signals) == 1
    assert signals[0].quantity == 10  # AI quantity, not the dashboard's 25
    assert signals[0].symbol == "RELIANCE"
    assert signals[0].generated_at == when


def test_skips_symbols_already_open_today():
    snapshot = _snapshot([_row("RELIANCE"), _row("TCS")])
    when = datetime(2026, 9, 1, 9, 45, tzinfo=timezone.utc)

    signals = signals_from_snapshot(snapshot, generated_at=when, already_open={"RELIANCE"})

    assert {s.symbol for s in signals} == {"TCS"}


def test_generates_unique_signal_ids_for_the_same_symbol_on_different_days():
    snapshot = _snapshot([_row("RELIANCE")])
    day1 = signals_from_snapshot(snapshot, generated_at=datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc))
    day2 = signals_from_snapshot(snapshot, generated_at=datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc))
    assert day1[0].signal_id != day2[0].signal_id


# ---- _symbols_with_open_signal_today (real store, not mocked) ----------

def test_open_signal_from_earlier_today_is_detected(tmp_path):
    store = ValidationStore(tmp_path / "journal.jsonl")
    snapshot = _snapshot([_row("RELIANCE")])
    morning = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)

    for signal in signals_from_snapshot(snapshot, generated_at=morning):
        store.append_signal(signal)

    open_today = _symbols_with_open_signal_today(store, today=date(2026, 9, 1))
    assert open_today == {"RELIANCE"}


def test_resolved_signal_is_no_longer_open(tmp_path):
    store = ValidationStore(tmp_path / "journal.jsonl")
    snapshot = _snapshot([_row("RELIANCE")])
    morning = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    [signal] = signals_from_snapshot(snapshot, generated_at=morning)
    store.append_signal(signal)

    # Resolve it, mirroring what the (separate) EOD-close script will do.
    store.append_outcome(PaperOutcome(
        signal_id=signal.signal_id, symbol=signal.symbol, direction=signal.direction,
        generated_at=signal.generated_at, entry=signal.entry, stop_loss=signal.stop_loss,
        target=signal.target, quantity=signal.quantity, outcome="TARGET",
        exit_at=morning, exit_price=signal.target, pnl=600.0, estimated_charges=25.0,
        net_pnl=575.0, max_favourable_move=60.0, max_adverse_move=-5.0,
        bars_observed=3, reason="test resolution",
    ))

    open_today = _symbols_with_open_signal_today(store, today=date(2026, 9, 1))
    assert open_today == set()


def test_signal_from_a_previous_day_does_not_block_today(tmp_path):
    store = ValidationStore(tmp_path / "journal.jsonl")
    snapshot = _snapshot([_row("RELIANCE")])
    yesterday = datetime(2026, 8, 31, 9, 30, tzinfo=timezone.utc)
    for signal in signals_from_snapshot(snapshot, generated_at=yesterday):
        store.append_signal(signal)

    open_today = _symbols_with_open_signal_today(store, today=date(2026, 9, 1))
    assert open_today == set()
