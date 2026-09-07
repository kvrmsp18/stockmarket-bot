from __future__ import annotations

import json
import os
from typing import Any

import requests


class AIEngine:
    """Independent advisory layer; never an order/risk authority."""

    def __init__(self) -> None:
        self.openai = os.getenv("OPENAI_API_KEY", "").strip()
        self.claude = os.getenv("ANTHROPIC_API_KEY", "").strip()

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        text = str(text or "").strip()
        if not text:
            return None
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    value = json.loads(text[start:end + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
        return None

    def _prompt(self, context: dict[str, Any]) -> str:
        return ("Return JSON only with keys score (0-10 or null), decision "
                "(BUY/SELL/HOLD/NO TRADE or null), confidence (0-1 or null), "
                "positives, negatives, risks. Use null when evidence is insufficient. "
                "Advisory only. Never override deterministic risk, funds, portfolio, "
                "market-data or live-order gates. Never invent market prices or fundamentals. "
                + json.dumps(context, default=str)[:10000])

    def _openai(self, prompt: str) -> tuple[str, dict[str, Any]]:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + self.openai, "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}, timeout=20)
            r.raise_for_status()
            text = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = self._parse_json(text)
            return "ChatGPT", ({"status": "AVAILABLE", "model": model, **parsed} if parsed is not None else {"status": "ERROR", "model": model, "reason": "model response was not valid JSON"})
        except Exception as exc:
            return "ChatGPT", {"status": "ERROR", "model": model, "reason": str(exc)}

    def _claude(self, prompt: str) -> tuple[str, dict[str, Any]]:
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest").strip()
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.claude, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 700, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}, timeout=20)
            r.raise_for_status()
            text = "\n".join(str(x.get("text", "")) for x in r.json().get("content", []) if isinstance(x, dict))
            parsed = self._parse_json(text)
            return "Claude", ({"status": "AVAILABLE", "model": model, **parsed} if parsed is not None else {"status": "ERROR", "model": model, "reason": "model response was not valid JSON"})
        except Exception as exc:
            return "Claude", {"status": "ERROR", "model": model, "reason": str(exc)}

    def analyse(self, context: dict[str, Any]) -> dict[str, Any]:
        prompt = self._prompt(context)
        responses: list[tuple[str, dict[str, Any]]] = []
        if self.openai:
            responses.append(self._openai(prompt))
        if self.claude:
            responses.append(self._claude(prompt))
        valid = [(provider, answer) for provider, answer in responses
                 if answer.get("status") == "AVAILABLE" and isinstance(answer.get("score"), (int, float)) and 0 <= float(answer["score"]) <= 10]
        if len(valid) >= 2:
            scores = [float(answer["score"]) for _, answer in valid]
            state, score = ("AGREE" if max(scores) - min(scores) <= 2 else "DISAGREE"), sum(scores) / len(scores)
        elif len(valid) == 1:
            state, score = "SINGLE_PROVIDER", float(valid[0][1]["score"])
        else:
            state, score = "NO_DECISION", None
        return {"status": "AVAILABLE" if valid else "DATA_UNAVAILABLE", "responses": responses,
                "state": state, "score": score, "providers_considered": [p for p, _ in responses],
                "valid_provider_count": len(valid), "advisory_only": True}
