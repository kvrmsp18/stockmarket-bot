from __future__ import annotations

import os
from typing import Any

import requests


def _openai(prompt: str) -> dict[str, Any]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return {"status": "DATA_UNAVAILABLE", "provider": "OPENAI", "reason": "OPENAI_API_KEY not configured"}
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": prompt, "temperature": 0},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        text = body.get("output_text", "")
        if not text:
            chunks = []
            for item in body.get("output", []) or []:
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("text"):
                        chunks.append(str(c["text"]))
            text = "\n".join(chunks)
        return {"status": "AVAILABLE", "provider": "OPENAI", "model": model, "text": text}
    except Exception as exc:
        return {"status": "ERROR", "provider": "OPENAI", "model": model, "reason": str(exc)}


def _anthropic(prompt: str) -> dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return {"status": "DATA_UNAVAILABLE", "provider": "ANTHROPIC", "reason": "ANTHROPIC_API_KEY not configured"}
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest").strip()
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 1200, "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        text = "\n".join(str(x.get("text", "")) for x in body.get("content", []) if isinstance(x, dict))
        return {"status": "AVAILABLE", "provider": "ANTHROPIC", "model": model, "text": text}
    except Exception as exc:
        return {"status": "ERROR", "provider": "ANTHROPIC", "model": model, "reason": str(exc)}


def advisory(prompt: str) -> dict[str, Any]:
    """AI is advisory only. It cannot create/approve an order or override deterministic gates."""
    if os.getenv("OPENAI_API_KEY", "").strip():
        return _openai(prompt)
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        return _anthropic(prompt)
    return {"status": "DATA_UNAVAILABLE", "provider": "NONE", "reason": "No AI provider key configured"}
