from __future__ import annotations

"""Verified event/news risk layer for intraday research.

This module is deliberately advisory. It does not create BUY/SELL signals and
never fabricates a headline when a provider returns no evidence.

Primary source currently used by the bot: Yahoo Finance news for the NSE
security's Yahoo symbol (<SYMBOL>.NS). Each returned item keeps its provider
URL/title/published timestamp so the UI can show where the evidence came from.
"""

from datetime import datetime, timezone
from typing import Any


HIGH_IMPACT_TERMS = {
    "results": 3,
    "earnings": 3,
    "profit warning": 3,
    "fraud": 3,
    "investigation": 3,
    "regulatory": 3,
    "sebi": 3,
    "rbi": 3,
    "ed raid": 3,
    "income tax": 3,
    "default": 3,
    "downgrade": 2,
    "upgrade": 2,
    "rating": 2,
    "acquisition": 2,
    "merger": 2,
    "demerger": 2,
    "stake sale": 2,
    "block deal": 2,
    "bulk deal": 2,
    "order win": 2,
    "contract": 2,
    "guidance": 2,
    "dividend": 1,
    "buyback": 1,
}

NEGATIVE_TERMS = {
    "fraud", "investigation", "default", "downgrade", "profit warning",
    "loss", "falls", "fall", "decline", "weak", "miss", "misses",
    "cut", "penalty", "probe", "resign", "resignation", "warning",
}

POSITIVE_TERMS = {
    "upgrade", "acquisition", "merger", "order win", "contract", "strong",
    "growth", "beats", "beat", "profit rises", "dividend", "buyback",
    "approval", "award",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _published(item: dict[str, Any]) -> datetime | None:
    raw = item.get("providerPublishTime") or item.get("published_at") or item.get("published")
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if raw:
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _classify(title: str, summary: str) -> tuple[str, int, list[str]]:
    text = f"{title} {summary}".lower()
    high_hits = [term for term in HIGH_IMPACT_TERMS if term in text]
    negative_hits = [term for term in NEGATIVE_TERMS if term in text]
    positive_hits = [term for term in POSITIVE_TERMS if term in text]
    materiality = min(3, max([HIGH_IMPACT_TERMS[t] for t in high_hits], default=0))
    if negative_hits and not positive_hits:
        sentiment = "NEGATIVE"
    elif positive_hits and not negative_hits:
        sentiment = "POSITIVE"
    elif positive_hits or negative_hits:
        sentiment = "MIXED"
    else:
        sentiment = "NEUTRAL"
    return sentiment, materiality, sorted(set(high_hits + negative_hits + positive_hits))


def fetch_event_news(symbol: str, limit: int = 12) -> dict[str, Any]:
    """Fetch recent source-backed news for an NSE symbol.

    The function returns a structured result even when the provider is
    unavailable. Missing news is DATA UNAVAILABLE, never neutralised into a
    fake/no-news claim.
    """
    symbol = _text(symbol).upper()
    result: dict[str, Any] = {
        "symbol": symbol,
        "source": "Yahoo Finance",
        "provider": "yahoo_finance",
        "source_symbol": f"{symbol}.NS",
        "source_status": "DATA UNAVAILABLE",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "items": [],
        "material_high_impact": [],
        "errors": [],
    }
    if not symbol:
        result["errors"].append("NEWS_SYMBOL_EMPTY")
        return result

    try:
        import yfinance as yf
    except Exception as exc:
        result["errors"].append(f"YAHOO_FINANCE_UNAVAILABLE: {exc}")
        return result

    try:
        raw_items = yf.Ticker(f"{symbol}.NS").news or []
    except Exception as exc:
        result["errors"].append(f"YAHOO_NEWS_REQUEST_FAILED: {exc}")
        return result

    now = datetime.now(timezone.utc)
    normalized: list[dict[str, Any]] = []
    for item in raw_items[: max(1, int(limit))]:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = _text(content.get("title") or item.get("title"))
        summary = _text(content.get("summary") or item.get("summary") or item.get("description"))
        url = _text(content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else content.get("link") or item.get("link"))
        publisher = _text(content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else item.get("publisher"))
        published = _published(content)
        if not title:
            continue
        sentiment, materiality, terms = _classify(title, summary)
        age_minutes = None
        if published is not None:
            age_minutes = max(0.0, (now - published).total_seconds() / 60.0)
        fresh = age_minutes is not None and age_minutes <= 24 * 60
        item_out = {
            "title": title,
            "summary": summary,
            "publisher": publisher or "Yahoo Finance",
            "url": url,
            "published_at": published.isoformat() if published else None,
            "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            "sentiment": sentiment,
            "materiality": materiality,
            "matched_terms": terms,
            "fresh": fresh,
        }
        normalized.append(item_out)
        if fresh and materiality >= 2:
            result["material_high_impact"].append(item_out)

    result["items"] = normalized
    result["source_status"] = "AVAILABLE" if normalized else "DATA UNAVAILABLE"
    result["fresh_high_impact_count"] = len(result["material_high_impact"])
    if not normalized and not result["errors"]:
        result["errors"].append("YAHOO_NEWS_NO_ITEMS")
    return result


def event_news_gate(news: dict[str, Any] | None) -> dict[str, Any]:
    """Convert news evidence into a risk flag, never an entry signal.

    Crucial policy: unavailable news is a missing-data condition, not a trade
    veto. Only verified high-impact evidence may block an entry.
    """
    n = news or {}
    items = n.get("items") or []
    high = n.get("material_high_impact") or []
    if n.get("source_status") != "AVAILABLE":
        return {
            "status": "DATA UNAVAILABLE",
            "action": "NO_NEWS_DECISION",
            "risk_level": "LOW",
            "reason": "No verified news evidence available; event/news layer did not veto the trade.",
            "fresh_high_impact_count": 0,
        }
    if not high:
        return {
            "status": "AVAILABLE",
            "action": "NO_MATERIAL_EVENT_FLAG",
            "risk_level": "LOW",
            "reason": f"Verified news feed returned {len(items)} item(s); no fresh high-impact event was detected by the deterministic event filter.",
            "fresh_high_impact_count": 0,
        }
    negative = sum(1 for x in high if x.get("sentiment") == "NEGATIVE")
    positive = sum(1 for x in high if x.get("sentiment") == "POSITIVE")
    if negative and not positive:
        risk_level = "HIGH"
    else:
        risk_level = "ELEVATED"
    return {
        "status": "AVAILABLE",
        "action": "REVIEW_BEFORE_ENTRY",
        "risk_level": risk_level,
        "reason": f"{len(high)} fresh high-impact news item(s) require review before an intraday entry.",
        "fresh_high_impact_count": len(high),
    }
