from __future__ import annotations
from intraday_bot.ai import AIEngine

def test_ai_engine_does_not_invent_score_without_provider(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = AIEngine().analyse({"symbol": "TEST"})
    assert result["status"] == "DATA_UNAVAILABLE"
    assert result["state"] == "NO_DECISION"
    assert result["score"] is None
    assert result["advisory_only"] is True

def test_ai_json_parser_rejects_non_object() -> None:
    assert AIEngine._parse_json("[1, 2, 3]") is None
    assert AIEngine._parse_json("not json") is None
    assert AIEngine._parse_json('{"score": 7}') == {"score": 7}

def test_ai_engine_marks_single_valid_provider(monkeypatch) -> None:
    monkeypatch.setattr(AIEngine, "_openai", lambda self, prompt: ("ChatGPT", {"status": "AVAILABLE", "score": 8.0, "decision": "BUY"}))
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = AIEngine().analyse({"symbol": "TEST"})
    assert result["state"] == "SINGLE_PROVIDER"
    assert result["score"] == 8.0
    assert result["valid_provider_count"] == 1

def test_ai_engine_detects_disagreement(monkeypatch) -> None:
    monkeypatch.setattr(AIEngine, "_openai", lambda self, prompt: ("ChatGPT", {"status": "AVAILABLE", "score": 9.0}))
    monkeypatch.setattr(AIEngine, "_claude", lambda self, prompt: ("Claude", {"status": "AVAILABLE", "score": 3.0}))
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    result = AIEngine().analyse({"symbol": "TEST"})
    assert result["state"] == "DISAGREE"
    assert result["score"] == 6.0
