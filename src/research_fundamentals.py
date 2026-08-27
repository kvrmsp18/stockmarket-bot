"""Cached fundamental-quality research for the intraday stock monitor.

Fundamentals are a slower-moving BUY-quality filter. They must strengthen an
intraday decision without becoming the intraday timing signal.

The quality screen uses five business rules inspired by the project's agreed
checks:
    1. P/E valuation sanity
    2. ROE profitability quality
    3. Positive EPS
    4. Profit/earnings growth
    5. Institutional ownership / FII proxy

Important design choices:
- Fundamentals are cached and are NOT queried on every 15-minute scan.
- SELL candidates are not blocked by long-horizon fundamentals.
- Positive EPS and non-collapse in earnings are hard safety checks.
- The remaining checks contribute to a weighted quality score rather than
  requiring every field to be present. This prevents a missing third-party
  field from unnecessarily destroying otherwise strong opportunities.
- Yahoo's institutional ownership is explicitly a proxy; it is never labelled
  as exact NSE FII/FPI holding.
- P/E is growth-aware: a high P/E is tolerated only when earnings growth is
  sufficiently strong. This is safer than using a single universal P/E cap.
- The module is read-only and never places broker orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests


class FundamentalDataError(RuntimeError):
    """Raised when fundamental data cannot be safely consumed."""


@dataclass(frozen=True)
class FundamentalConfig:
    """Business-quality thresholds and scoring policy for BUY candidates."""

    enabled: bool = True
    require_for_buy: bool = True

    # Valuation. A high P/E is only acceptable when growth justifies it.
    preferred_max_pe: float = 35.0
    absolute_max_pe: float = 75.0
    high_pe_growth_floor_pct: float = 15.0

    # Profitability and earnings quality.
    min_roe_pct: float = 12.0
    min_eps: float = 0.0
    min_profit_growth_pct: float = 10.0
    hard_min_profit_growth_pct: float = -5.0

    # Yahoo field is institutional ownership, not exact FII/FPI holding.
    min_institutional_holding_pct: float = 5.0

    # Minimum weighted quality score for BUY approval.
    minimum_quality_score: float = 70.0

    cache_hours: float = 24.0
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> "FundamentalConfig":
        def boolean(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        def number(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=boolean("FUNDAMENTALS_ENABLED", True),
            require_for_buy=boolean("FUNDAMENTALS_REQUIRE_FOR_BUY", True),
            preferred_max_pe=number("FUNDAMENTALS_PREFERRED_MAX_PE", 35.0),
            absolute_max_pe=number("FUNDAMENTALS_ABSOLUTE_MAX_PE", 75.0),
            high_pe_growth_floor_pct=number("FUNDAMENTALS_HIGH_PE_GROWTH_FLOOR_PCT", 15.0),
            min_roe_pct=number("FUNDAMENTALS_MIN_ROE_PCT", 12.0),
            min_eps=number("FUNDAMENTALS_MIN_EPS", 0.0),
            min_profit_growth_pct=number("FUNDAMENTALS_MIN_PROFIT_GROWTH_PCT", 10.0),
            hard_min_profit_growth_pct=number("FUNDAMENTALS_HARD_MIN_PROFIT_GROWTH_PCT", -5.0),
            min_institutional_holding_pct=number(
                "FUNDAMENTALS_MIN_INSTITUTIONAL_HOLDING_PCT", 5.0
            ),
            minimum_quality_score=number("FUNDAMENTALS_MIN_QUALITY_SCORE", 70.0),
            cache_hours=max(1.0, number("FUNDAMENTALS_CACHE_HOURS", 24.0)),
            timeout_seconds=max(3.0, number("FUNDAMENTALS_TIMEOUT_SECONDS", 10.0)),
        )

    def validate(self) -> None:
        if self.preferred_max_pe <= 0:
            raise FundamentalDataError("FUNDAMENTALS_PREFERRED_MAX_PE must be positive.")
        if self.absolute_max_pe <= self.preferred_max_pe:
            raise FundamentalDataError(
                "FUNDAMENTALS_ABSOLUTE_MAX_PE must exceed FUNDAMENTALS_PREFERRED_MAX_PE."
            )
        if self.min_roe_pct < 0:
            raise FundamentalDataError("FUNDAMENTALS_MIN_ROE_PCT cannot be negative.")
        if self.hard_min_profit_growth_pct < -100:
            raise FundamentalDataError("FUNDAMENTALS_HARD_MIN_PROFIT_GROWTH_PCT is invalid.")
        if self.min_profit_growth_pct < self.hard_min_profit_growth_pct:
            raise FundamentalDataError(
                "FUNDAMENTALS_MIN_PROFIT_GROWTH_PCT cannot be below the hard floor."
            )
        if self.min_institutional_holding_pct < 0:
            raise FundamentalDataError("FUNDAMENTALS_MIN_INSTITUTIONAL_HOLDING_PCT cannot be negative.")
        if not 0 <= self.minimum_quality_score <= 100:
            raise FundamentalDataError("FUNDAMENTALS_MIN_QUALITY_SCORE must be 0..100.")


@dataclass(frozen=True)
class FundamentalMetrics:
    """Latest cached fundamental metrics for one stock."""

    symbol: str
    pe_ratio: float | None
    roe_pct: float | None
    eps: float | None
    profit_growth_pct: float | None
    institutional_holding_pct: float | None
    source: str
    as_of: str
    institutional_holding_is_proxy: bool = True


@dataclass(frozen=True)
class FundamentalAssessment:
    """Result of applying the BUY-quality fundamental gate."""

    required: bool
    passed: bool
    status: str
    score: float
    checks: tuple[str, ...]
    metrics: FundamentalMetrics | None


class YahooFundamentalsProvider:
    """Read Yahoo Finance quote fundamentals in one batched HTTP request."""

    BASE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
    SOURCE = "yahoo_finance_quote"

    def __init__(self, *, timeout: float = 10.0, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            result = float(value)
            return result if result == result else None
        except (TypeError, ValueError, OverflowError):
            return None

    def fetch(self, symbols: Sequence[str]) -> dict[str, FundamentalMetrics]:
        normalized = tuple(
            dict.fromkeys(s.strip().upper() for s in symbols if isinstance(s, str) and s.strip())
        )
        if not normalized:
            return {}

        try:
            response = self.session.get(
                self.BASE_URL,
                params={"symbols": ",".join(normalized)},
                headers={"User-Agent": "nse-bse-intraday-ai/2.1"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FundamentalDataError(f"Yahoo Finance fundamentals network error: {exc}") from exc

        if not response.ok:
            raise FundamentalDataError(
                f"Yahoo Finance fundamentals returned HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise FundamentalDataError("Yahoo Finance fundamentals returned invalid JSON.") from exc

        quote_response = payload.get("quoteResponse") if isinstance(payload, dict) else None
        rows = quote_response.get("result") if isinstance(quote_response, dict) else None
        if not isinstance(rows, list):
            raise FundamentalDataError("Yahoo Finance returned no fundamental quote results.")

        now = datetime.now(timezone.utc).isoformat()
        metrics: dict[str, FundamentalMetrics] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            earnings_growth = self._number(row.get("earningsGrowth"))
            quarterly_growth = self._number(row.get("earningsQuarterlyGrowth"))
            institutional = self._number(row.get("heldPercentInstitutions"))

            metrics[symbol] = FundamentalMetrics(
                symbol=symbol,
                pe_ratio=self._number(row.get("trailingPE")),
                roe_pct=(
                    self._number(row.get("returnOnEquity")) * 100.0
                    if self._number(row.get("returnOnEquity")) is not None
                    else None
                ),
                eps=self._number(row.get("epsTrailingTwelveMonths")),
                profit_growth_pct=(
                    earnings_growth * 100.0
                    if earnings_growth is not None
                    else quarterly_growth * 100.0
                    if quarterly_growth is not None
                    else None
                ),
                institutional_holding_pct=(
                    institutional * 100.0 if institutional is not None else None
                ),
                source=self.SOURCE,
                as_of=now,
                institutional_holding_is_proxy=True,
            )
        return metrics


class CachedFundamentals:
    """Daily cache around the Yahoo fundamentals endpoint."""

    def __init__(
        self,
        *,
        provider: YahooFundamentalsProvider | None = None,
        cache_path: Path | None = None,
        config: FundamentalConfig | None = None,
    ) -> None:
        self.config = config or FundamentalConfig.from_environment()
        self.config.validate()
        self.provider = provider or YahooFundamentalsProvider(timeout=self.config.timeout_seconds)
        self.cache_path = cache_path or Path(
            os.getenv("FUNDAMENTALS_CACHE_PATH", ".runtime/fundamentals_cache.json")
        )

    def _load(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self, payload: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.cache_path)

    @staticmethod
    def _from_payload(payload: Mapping[str, Any]) -> FundamentalMetrics | None:
        try:
            return FundamentalMetrics(
                symbol=str(payload["symbol"]),
                pe_ratio=payload.get("pe_ratio"),
                roe_pct=payload.get("roe_pct"),
                eps=payload.get("eps"),
                profit_growth_pct=payload.get("profit_growth_pct"),
                institutional_holding_pct=payload.get("institutional_holding_pct"),
                source=str(payload.get("source") or "unknown"),
                as_of=str(payload.get("as_of") or ""),
                institutional_holding_is_proxy=bool(
                    payload.get("institutional_holding_is_proxy", True)
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _fresh(as_of: str, cache_hours: float) -> bool:
        try:
            timestamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) - timestamp <= timedelta(hours=cache_hours)
        except (TypeError, ValueError):
            return False

    def get_many(self, symbols: Sequence[str]) -> dict[str, FundamentalMetrics]:
        if not self.config.enabled:
            return {}

        normalized = tuple(
            dict.fromkeys(s.strip().upper() for s in symbols if isinstance(s, str) and s.strip())
        )
        cache = self._load()
        result: dict[str, FundamentalMetrics] = {}
        missing: list[str] = []

        for symbol in normalized:
            cached = cache.get(symbol)
            if isinstance(cached, Mapping):
                metrics = self._from_payload(cached)
                if metrics is not None and self._fresh(metrics.as_of, self.config.cache_hours):
                    result[symbol] = metrics
                    continue
            missing.append(symbol)

        if missing:
            fetched = self.provider.fetch(missing)
            for symbol, metrics in fetched.items():
                result[symbol] = metrics
                cache[symbol] = asdict(metrics)
            self._save(cache)

        return result


def _score_pe(metrics: FundamentalMetrics, cfg: FundamentalConfig) -> tuple[float, str, bool]:
    """Return score, explanation and hard-safety result for valuation."""
    pe = metrics.pe_ratio
    growth = metrics.profit_growth_pct

    if pe is None:
        return 0.0, "P/E unavailable", False
    if pe <= 0:
        return 0.0, f"P/E {pe:.2f} is invalid/non-positive", False
    if pe > cfg.absolute_max_pe:
        return 0.0, f"P/E {pe:.2f} > absolute ceiling {cfg.absolute_max_pe:.2f}", False

    if pe <= cfg.preferred_max_pe:
        return 20.0, f"P/E {pe:.2f} within preferred range ≤ {cfg.preferred_max_pe:.2f} ✓", True

    # Expensive valuation is acceptable only when growth provides a reasonable
    # justification. It receives partial credit rather than a full pass.
    if growth is not None and growth >= cfg.high_pe_growth_floor_pct:
        return 14.0, (
            f"P/E {pe:.2f} above preferred range but growth {growth:.1f}% "
            f"supports the higher valuation ✓"
        ), True

    return 4.0, (
        f"P/E {pe:.2f} is expensive and growth is below "
        f"{cfg.high_pe_growth_floor_pct:.1f}%"
    ), False


def assess_buy_fundamentals(
    metrics: FundamentalMetrics | None,
    *,
    config: FundamentalConfig | None = None,
) -> FundamentalAssessment:
    """Apply the five business-quality checks to a BUY candidate.

    The score is deliberately weighted:
        P/E 20, ROE 20, EPS 15, profit growth 25, institutional holding 20.

    EPS <= 0, excessive P/E, or severe earnings deterioration are hard safety
    failures. Otherwise a candidate needs the configured quality score. Missing
    institutional ownership is not treated as proof of failure because the
    Yahoo field is a proxy and may be unavailable for valid Indian equities.
    """
    cfg = config or FundamentalConfig.from_environment()
    cfg.validate()

    if not cfg.enabled:
        return FundamentalAssessment(
            required=False,
            passed=True,
            status="FUNDAMENTALS_DISABLED",
            score=100.0,
            checks=("Fundamental gate disabled by configuration.",),
            metrics=metrics,
        )

    if metrics is None:
        required = cfg.require_for_buy
        return FundamentalAssessment(
            required=required,
            passed=not required,
            status="FUNDAMENTALS_UNAVAILABLE",
            score=0.0,
            checks=("Fundamental data unavailable; BUY is blocked when the gate is required.",),
            metrics=None,
        )

    checks: list[str] = []
    score = 0.0
    hard_failure = False

    pe_score, pe_text, pe_safe = _score_pe(metrics, cfg)
    score += pe_score
    checks.append(pe_text)
    if not pe_safe:
        hard_failure = True

    # ROE: 20 points. Positive but below target receives partial credit.
    if metrics.roe_pct is None:
        checks.append("ROE unavailable")
    elif metrics.roe_pct >= cfg.min_roe_pct:
        score += 20.0
        checks.append(f"ROE {metrics.roe_pct:.1f}% ≥ {cfg.min_roe_pct:.1f}% ✓")
    elif metrics.roe_pct > 0:
        score += 8.0
        checks.append(f"ROE {metrics.roe_pct:.1f}% below preferred {cfg.min_roe_pct:.1f}%")
    else:
        checks.append(f"ROE {metrics.roe_pct:.1f}% is non-positive")
        hard_failure = True

    # EPS: 15 points and a hard safety requirement.
    if metrics.eps is None:
        checks.append("EPS unavailable")
        hard_failure = True
    elif metrics.eps > cfg.min_eps:
        score += 15.0
        checks.append(f"EPS ₹{metrics.eps:.2f} > ₹{cfg.min_eps:.2f} ✓")
    else:
        checks.append(f"EPS ₹{metrics.eps:.2f} ≤ ₹{cfg.min_eps:.2f} — BUY blocked")
        hard_failure = True

    # Profit growth: 25 points. A severe contraction is a hard safety failure.
    if metrics.profit_growth_pct is None:
        checks.append("Profit growth unavailable")
        hard_failure = True
    elif metrics.profit_growth_pct >= cfg.min_profit_growth_pct:
        score += 25.0
        checks.append(
            f"Profit growth {metrics.profit_growth_pct:.1f}% ≥ "
            f"{cfg.min_profit_growth_pct:.1f}% ✓"
        )
    elif metrics.profit_growth_pct >= cfg.hard_min_profit_growth_pct:
        score += 10.0
        checks.append(
            f"Profit growth {metrics.profit_growth_pct:.1f}% below preferred "
            f"{cfg.min_profit_growth_pct:.1f}% but above hard floor"
        )
    else:
        checks.append(
            f"Profit growth {metrics.profit_growth_pct:.1f}% below hard floor "
            f"{cfg.hard_min_profit_growth_pct:.1f}% — BUY blocked"
        )
        hard_failure = True

    # Institutional ownership / FII proxy: 20 points.
    # Missing data gets no points but is not itself a hard failure.
    if metrics.institutional_holding_pct is None:
        checks.append("Institutional/FII-proxy holding unavailable — no points awarded")
    elif metrics.institutional_holding_pct >= cfg.min_institutional_holding_pct:
        score += 20.0
        checks.append(
            f"Institutional/FII-proxy holding {metrics.institutional_holding_pct:.1f}% ≥ "
            f"{cfg.min_institutional_holding_pct:.1f}% ✓"
        )
    else:
        score += 5.0
        checks.append(
            f"Institutional/FII-proxy holding {metrics.institutional_holding_pct:.1f}% < "
            f"{cfg.min_institutional_holding_pct:.1f}% — weak ownership signal"
        )

    score = round(min(100.0, score), 1)
    passed = not hard_failure and score >= cfg.minimum_quality_score

    if hard_failure:
        status = "FUNDAMENTALS_REJECTED_HARD"
    elif passed:
        status = "FUNDAMENTALS_PASSED"
    else:
        status = "FUNDAMENTALS_REJECTED_SCORE"

    return FundamentalAssessment(
        required=True,
        passed=passed,
        status=status,
        score=score,
        checks=tuple(checks),
        metrics=metrics,
    )
