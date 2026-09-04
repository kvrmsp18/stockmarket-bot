from __future__ import annotations

"""Verified event/news risk layer for intraday research.

This module is deliberately advisory. It does not create BUY/SELL signals and
never fabricates a headline when a provider returns no evidence.

Primary source currently used by the bot: Yahoo Finance news for the NSE
security's Yahoo symbol (<SYMBOL>.NS). Returned items are accepted only when
Yahoo's metadata or article text provides a deterministic link to the requested
security. Unrelated feed stories are discarded rather than presented as evidence.
"""

from datetime import datetime, timezone
from typing import Any


HIGH_IMPACT_TERMS = {
    "results": 3, "earnings": 3, "profit warning": 3, "fraud": 3,
    "investigation": 3, "regulatory": 3, "sebi": 3, "rbi": 3,
    "ed raid": 3, "income tax": 3, "default": 3, "downgrade": 2,
    "upgrade": 2, "rating": 2, "acquisition": 2, "merger": 2,
    "demerger": 2, "stake sale": 2, "block deal": 2, "bulk deal": 2,
    "order win": 2, "contract": 2, "guidance": 2, "dividend": 1,
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


def _related_tickers(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, (list, tuple, set)):
        for x in value:
            text = _text(x).upper()
            if text:
                result.add(text.removesuffix(".NS"))
    elif isinstance(value, str):
        for x in value.replace(",", " ").split():
            text = x.strip().upper()
            if text:
                result.add(text.removesuffix(".NS"))
    return result


def _article_relevance(symbol: str, content: dict[str, Any], item: dict[str, Any], title: str, summary: str, url: str) -> tuple[bool, str]:
    """Require deterministic security linkage before treating Yahoo news as evidence."""
    target = _text(symbol).upper().removesuffix(".NS")
    if not target:
        return False, "NEWS_SYMBOL_EMPTY"

    related = set()
    for key in ("relatedTickers", "related_tickers", "tickers", "symbols"):
        related |= _related_tickers(content.get(key))
        related |= _related_tickers(item.get(key))
    if target in related:
        return True, "YAHOO_RELATED_TICKER"

    haystack = f"{title} {summary} {url}".upper()
    exact_tokens = {target, f"{target}.NS", f"{target}:NS", f"NSE:{target}"}
    if any(token in haystack for token in exact_tokens):
        return True, "SYMBOL_TEXT_MATCH"

    return False, "UNRELATED_NEWS_ITEM"


def fetch_event_news(symbol: str, limit: int = 12) -> dict[str, Any]:
    symbol = _text(symbol).upper()
    result: dict[str, Any] = {
        "symbol": symbol, "source": "Yahoo Finance", "provider": "yahoo_finance",
        "source_symbol": f"{symbol}.NS", "source_status": "DATA UNAVAILABLE",
        "fetched_at": datetime.now(timezone.utc).isoformat(), "items": [],
        "material_high_impact": [], "errors": [], "discarded_unrelated_count": 0,
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
    max_items = max(1, int(limit))
    for item in raw_items[:max_items]:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = _text(content.get("title") or item.get("title"))
        summary = _text(content.get("summary") or item.get("summary") or item.get("description"))
        canonical = content.get("canonicalUrl")
        url = _text(canonical.get("url") if isinstance(canonical, dict) else content.get("link") or item.get("link"))
        publisher_data = content.get("provider")
        publisher = _text(publisher_data.get("displayName") if isinstance(publisher_data, dict) else item.get("publisher"))
        if not title:
            continue

        relevant, relevance_basis = _article_relevance(symbol, content, item, title, summary, url)
        if not relevant:
            result["discarded_unrelated_count"] += 1
            continue

        published = _published(content)
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
            "relevance_basis": relevance_basis,
        }
        normalized.append(item_out)
        if fresh and materiality >= 2:
            result["material_high_impact"].append(item_out)

    result["items"] = normalized
    result["source_status"] = "AVAILABLE" if normalized else "DATA UNAVAILABLE"
    result["fresh_high_impact_count"] = len(result["material_high_impact"])
    if not normalized and not result["errors"]:
        result["errors"].append("YAHOO_NEWS_NO_RELEVANT_ITEMS")
    return result


def event_news_gate(news: dict[str, Any] | None) -> dict[str, Any]:
    """Convert news evidence into a risk flag, never an entry signal."""
    n = news or {}
    items = n.get("items") or []
    high = n.get("material_high_impact") or []
    if n.get("source_status") != "AVAILABLE":
        return {
            "status": "DATA UNAVAILABLE",
            "action": "NO_NEWS_DECISION",
            "risk_level": "UNKNOWN",
            "reason": "No verified security-relevant news evidence available.",
            "fresh_high_impact_count": 0,
        }
    if not high:
        return {
            "status": "AVAILABLE",
            "action": "NO_MATERIAL_EVENT_FLAG",
            "risk_level": "LOW",
            "reason": f"Verified security-relevant Yahoo news feed returned {len(items)} item(s); no fresh high-impact event was detected by the deterministic event filter.",
            "fresh_high_impact_count": 0,
        }
    negative = sum(1 for x in high if x.get("sentiment") == "NEGATIVE")
    positive = sum(1 for x in high if x.get("sentiment") == "POSITIVE")
    risk_level = "HIGH" if negative and not positive else "ELEVATED"
    return {
        "status": "AVAILABLE",
        "action": "REVIEW_BEFORE_ENTRY",
        "risk_level": risk_level,
        "reason": f"{len(high)} fresh high-impact security-relevant news item(s) require review before an intraday entry.",
        "fresh_high_impact_count": len(high),
    }
