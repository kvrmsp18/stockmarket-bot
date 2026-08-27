"""Small dependency-free JSONL store for paper-trading validation records."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .paper_trading_validation import PaperOutcome, PaperSignal, ValidationSummary


class ValidationStore:
    """Append-only local journal for frozen signals and evaluated outcomes."""

    def __init__(self, path: str | Path = "data/paper_trading_journal.jsonl") -> None:
        self.path = Path(path)

    def _append(self, record_type: str, payload: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"type": record_type, "payload": asdict(payload)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")

    def append_signal(self, signal: PaperSignal) -> None:
        self._append("signal", signal)

    def append_outcome(self, outcome: PaperOutcome) -> None:
        self._append("outcome", outcome)

    def append_summary(self, summary: ValidationSummary, *, report_type: str) -> None:
        if report_type not in {"daily", "weekly"}:
            raise ValueError("report_type must be daily or weekly")
        self._append(report_type, summary)

    def read_records(self, record_type: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        records: list[dict] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record_type is None or record.get("type") == record_type:
                    records.append(record)
        return records

    def signals(self) -> list[dict]:
        return self.read_records("signal")

    def outcomes(self) -> list[dict]:
        return self.read_records("outcome")

    def summaries(self, report_type: str | None = None) -> list[dict]:
        allowed = {"daily", "weekly"}
        if report_type is not None and report_type not in allowed:
            raise ValueError("report_type must be daily or weekly")
        if report_type is None:
            return self.read_records("daily") + self.read_records("weekly")
        return self.read_records(report_type)
