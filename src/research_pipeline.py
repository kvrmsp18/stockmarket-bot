"""End-to-end, read-only research screening pipeline.

The production scanner deliberately separates the complete-market pass from
expensive confirmation work. Every cycle still receives the complete dynamic
NSE universe, but network-bound 5-minute OHLCV requests are fetched in a
bounded worker pool instead of one stock at a time. M/W/D history is fetched
only after a stock has passed the initial technical/context candidate gate.

No order placement occurs in this module.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from .advanced_indicators import AdvancedTechnicalSnapshot, analyze_advanced
from .candidate_engine import CandidateConfig, generate_candidate, rank_candidates
from .market_context import build_market_context, candidate_context_allowed
from .research_data_quality import ResearchDataQualityError, ResearchDataQualityReport, assess_research_data
from .research_fundamentals import CachedFundamentals, FundamentalAssessment, FundamentalConfig, FundamentalDataError, FundamentalMetrics, assess_buy_fundamentals
from .research_market_data import ResearchBar, ResearchMarketDataError, ResearchMarketDataProvider, ResilientMarketDataProvider, indian_equity_symbol
from .risk_management import RiskAssessment, RiskConfig, assess_candidate
from .technical_analysis import MultiTimeframeRSI, TechnicalAnalysisError, TechnicalSnapshot, analyze, analyze_multi_timeframe_rsi


@dataclass(frozen=True)
class ResearchResult:
    symbol: str
    exchange: str
    bars: tuple[ResearchBar, ...]
    technical: TechnicalSnapshot
    risk: RiskAssessment | None


@dataclass(frozen=True)
class SymbolResearchResult:
    requested_symbol: str
    yahoo_symbol: str
    data_status: str
    bars: tuple[ResearchBar, ...]
    quality: ResearchDataQualityReport | None
    technical: TechnicalSnapshot | None
    candidate: object | None
    risk: RiskAssessment | None
    reason: str
    data_source: str | None = None
    fundamental_status: str = "NOT_CHECKED"
    fundamental_score: float | None = None
    fundamental_checks: tuple[str, ...] = ()
    fundamentals: FundamentalMetrics | None = None
    advanced: AdvancedTechnicalSnapshot | None = None
    mtf: MultiTimeframeRSI | None = None


@dataclass(frozen=True)
class ResearchScanResult:
    requested_count: int
    scanned_count: int
    data_error_count: int
    quality_failure_count: int
    technical_rejection_count: int
    fundamental_error_count: int
    candidate_count: int
    actionable_count: int
    buy_count: int
    sell_count: int
    results: tuple[SymbolResearchResult, ...]
    actionable_candidates: tuple[object, ...]


class ResearchPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class ResearchPipelineConfig:
    exchange: str = "NSE"
    period: str = "5d"
    interval: str = "5m"
    mtf_period: str = "2y"
    mtf_interval: str = "1d"
    require_mtf_confirmation: bool = True
    minimum_quality_score: float = 90.0
    maximum_gap: timedelta | None = None
    maximum_actionable_candidates: int = 10
    account_equity: float = 100000.0
    candidate_config: CandidateConfig = CandidateConfig()
    risk_config: RiskConfig = RiskConfig()
    minimum_rank_confidence: float = 0.70
    minimum_rank_rr: float = 1.5
    fundamental_config: FundamentalConfig = FundamentalConfig()

    def validate(self) -> None:
        if self.exchange.strip().upper() not in {"NSE", "BSE"}:
            raise ResearchPipelineError("exchange must be NSE or BSE.")
        if not self.period.strip() or not self.interval.strip():
            raise ResearchPipelineError("period and interval must not be empty.")
        if not self.mtf_period.strip() or self.mtf_interval.strip().lower() not in {"1d", "1D"}:
            raise ResearchPipelineError("mtf_period must be set and mtf_interval must be 1d.")
        if not 0.0 <= self.minimum_quality_score <= 100.0:
            raise ResearchPipelineError("minimum_quality_score must be 0..100.")
        if self.maximum_actionable_candidates <= 0:
            raise ResearchPipelineError("maximum_actionable_candidates must be positive.")
        if self.account_equity <= 0:
            raise ResearchPipelineError("account_equity must be positive.")
        if not 0.0 <= self.minimum_rank_confidence <= 1.0:
            raise ResearchPipelineError("minimum_rank_confidence must be 0..1.")
        if self.minimum_rank_rr <= 0:
            raise ResearchPipelineError("minimum_rank_rr must be positive.")
        self.fundamental_config.validate()


def _aggregate_bars(bars: Sequence[ResearchBar], timeframe: str) -> tuple[ResearchBar, ...]:
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    if timeframe == "daily":
        return ordered
    groups: dict[tuple[int, int], list[ResearchBar]] = {}
    for bar in ordered:
        if timeframe == "weekly":
            iso = bar.timestamp.isocalendar()
            key = (int(iso.year), int(iso.week))
        elif timeframe == "monthly":
            key = (bar.timestamp.year, bar.timestamp.month)
        else:
            raise ValueError(f"Unsupported aggregation timeframe: {timeframe}")
        groups.setdefault(key, []).append(bar)
    aggregated: list[ResearchBar] = []
    for group in groups.values():
        first, last = group[0], group[-1]
        aggregated.append(
            ResearchBar(
                symbol=last.symbol,
                timestamp=last.timestamp,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=last.close,
                volume=sum(max(0, int(bar.volume)) for bar in group),
            )
        )
    return tuple(aggregated)


def _mtf_reason(mtf: MultiTimeframeRSI | None) -> str:
    if mtf is None:
        return "MTF confirmation unavailable."

    def fmt(value: float | None) -> str:
        return "NA" if value is None else f"{value:.1f}"

    return (
        f"MTF RSI M/W/D={fmt(mtf.monthly_rsi_14)}/{fmt(mtf.weekly_rsi_14)}/"
        f"{fmt(mtf.daily_rsi_14)}; alignment={mtf.alignment}; "
        f"all_three_agree={mtf.all_three_agree}; "
        f"all_three_overbought={mtf.all_three_overbought}."
    )


def _mtf_gate(candidate_direction: str, mtf: MultiTimeframeRSI) -> tuple[bool, str]:
    if not mtf.all_three_agree:
        return False, "MTF directional conflict: monthly, weekly and daily RSI do not agree."
    expected_alignment = {"BUY": "BULLISH", "SELL": "BEARISH"}.get(candidate_direction)
    if expected_alignment is None:
        return False, f"MTF directional conflict: unsupported candidate direction {candidate_direction}."
    if mtf.alignment != expected_alignment:
        return False, (
            f"MTF directional conflict: candidate is {candidate_direction}, "
            f"but M/W/D RSI regime is {mtf.alignment}."
        )
    return True, f"MTF confirmation passed: M/W/D RSI all align {candidate_direction}."


def analyze_history(
    bars: tuple[ResearchBar, ...],
    *,
    exchange: str = "NSE",
    account_equity: float = 100000.0,
    candidate_config: CandidateConfig | None = None,
    risk_config: RiskConfig | None = None,
) -> ResearchResult:
    if not bars:
        raise ValueError("At least one market bar is required.")
    quality = assess_research_data(bars)
    if not quality.ready:
        raise ResearchDataQualityError(
            f"Research data quality check failed for {quality.symbol}: "
            + "; ".join(quality.issues)
        )
    technical = analyze(bars)
    advanced = analyze_advanced(bars)
    candidate = generate_candidate(
        technical,
        exchange=exchange,
        config=candidate_config,
        advanced=advanced,
    )
    risk = assess_candidate(candidate, account_equity, config=risk_config) if candidate else None
    return ResearchResult(
        symbol=bars[-1].symbol,
        exchange=exchange.upper(),
        bars=tuple(bars),
        technical=technical,
        risk=risk,
    )


def research_symbol(
    symbol: str,
    *,
    exchange: str = "NSE",
    period: str = "6mo",
    interval: str = "1d",
    account_equity: float = 100000.0,
) -> ResearchResult:
    market_symbol = indian_equity_symbol(symbol, exchange)
    provider = ResilientMarketDataProvider()
    bars = provider.history(market_symbol, period=period, interval=interval)
    return analyze_history(bars, exchange=exchange, account_equity=account_equity)


def _validate_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ResearchPipelineError("Every stock symbol must be a non-empty string.")
        value = symbol.strip().upper()
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        raise ResearchPipelineError("At least one stock symbol is required.")
    return tuple(normalized)


def _result(
    *,
    requested_symbol: str,
    yahoo_symbol: str,
    data_status: str,
    bars: tuple[ResearchBar, ...] = (),
    quality: ResearchDataQualityReport | None = None,
    technical: TechnicalSnapshot | None = None,
    candidate: object | None = None,
    risk: RiskAssessment | None = None,
    reason: str,
    data_source: str | None = None,
    fundamental_status: str = "NOT_CHECKED",
    fundamental_score: float | None = None,
    fundamental_checks: tuple[str, ...] = (),
    fundamentals: FundamentalMetrics | None = None,
    advanced: AdvancedTechnicalSnapshot | None = None,
    mtf: MultiTimeframeRSI | None = None,
) -> SymbolResearchResult:
    return SymbolResearchResult(
        requested_symbol=requested_symbol,
        yahoo_symbol=yahoo_symbol,
        data_status=data_status,
        bars=bars,
        quality=quality,
        technical=technical,
        candidate=candidate,
        risk=risk,
        reason=reason,
        data_source=data_source,
        fundamental_status=fundamental_status,
        fundamental_score=fundamental_score,
        fundamental_checks=fundamental_checks,
        fundamentals=fundamentals,
        advanced=advanced,
        mtf=mtf,
    )


def _fundamental_context(
    assessment: FundamentalAssessment,
) -> tuple[str, float | None, tuple[str, ...], FundamentalMetrics | None]:
    return assessment.status, assessment.score, assessment.checks, assessment.metrics


def _load_mtf(
    data_provider: ResearchMarketDataProvider,
    market_symbol: str,
    cfg: ResearchPipelineConfig,
) -> tuple[MultiTimeframeRSI | None, str | None, str | None]:
    """Load slow M/W/D confirmation only for an already qualified candidate."""
    try:
        daily = tuple(
            data_provider.history(
                market_symbol,
                period=cfg.mtf_period,
                interval=cfg.mtf_interval,
            )
        )
        quality = assess_research_data(daily, max_allowed_gap=cfg.maximum_gap)
        if not quality.ready or quality.quality_score < cfg.minimum_quality_score:
            reason = (
                "; ".join(quality.issues)
                if quality.issues
                else f"MTF daily quality score {quality.quality_score:.2f} is below minimum "
                f"{cfg.minimum_quality_score:.2f}."
            )
            return None, "MTF_DATA_REJECTED", reason
        weekly = _aggregate_bars(daily, "weekly")
        monthly = _aggregate_bars(daily, "monthly")
        mtf = analyze_multi_timeframe_rsi(monthly, weekly, daily)
        if mtf.alignment == "INSUFFICIENT_HISTORY":
            return mtf, "MTF_INSUFFICIENT_HISTORY", (
                "Monthly/weekly/daily RSI could not be calculated from the available daily history."
            )
        return mtf, None, None
    except (ResearchMarketDataError, TechnicalAnalysisError, ValueError, TypeError) as exc:
        return None, "MTF_DATA_ERROR", str(exc)


def _prepare_symbol(
    requested_symbol: str,
    *,
    cfg: ResearchPipelineConfig,
    provider: ResearchMarketDataProvider,
) -> tuple[str, str, tuple[ResearchBar, ...], ResearchDataQualityReport, TechnicalSnapshot, AdvancedTechnicalSnapshot, str | None] | SymbolResearchResult:
    """Fetch and technically prepare one symbol. Safe to execute in a worker."""
    market_symbol = indian_equity_symbol(requested_symbol, cfg.exchange)
    try:
        bars = tuple(provider.history(market_symbol, period=cfg.period, interval=cfg.interval))
    except (ResearchMarketDataError, ValueError, TypeError) as exc:
        return _result(
            requested_symbol=requested_symbol,
            yahoo_symbol=market_symbol,
            data_status="DATA_ERROR",
            reason=str(exc),
            data_source=getattr(provider, "last_source", None),
        )

    source = getattr(provider, "last_source", None) or getattr(provider, "name", None)
    quality = assess_research_data(bars, max_allowed_gap=cfg.maximum_gap)
    if not quality.ready or quality.quality_score < cfg.minimum_quality_score:
        reason = "; ".join(quality.issues) if quality.issues else (
            f"Quality score {quality.quality_score:.2f} is below minimum "
            f"{cfg.minimum_quality_score:.2f}."
        )
        return _result(
            requested_symbol=requested_symbol,
            yahoo_symbol=market_symbol,
            data_status="QUALITY_REJECTED",
            bars=bars,
            quality=quality,
            reason=reason,
            data_source=source,
        )

    try:
        snapshot = analyze(bars)
        advanced = analyze_advanced(bars)
    except (TechnicalAnalysisError, ValueError, TypeError) as exc:
        return _result(
            requested_symbol=requested_symbol,
            yahoo_symbol=market_symbol,
            data_status="TECHNICAL_REJECTED",
            bars=bars,
            quality=quality,
            reason=str(exc),
            data_source=source,
        )
    return (requested_symbol, market_symbol, bars, quality, snapshot, advanced, source)


def _scan_workers() -> int:
    """Bound network concurrency; configurable without code changes."""
    raw = os.getenv("DHAN_SCAN_WORKERS", "6").strip()
    try:
        return max(1, min(12, int(raw)))
    except (TypeError, ValueError):
        return 6


def scan_symbols(
    symbols: Sequence[str],
    *,
    provider: ResearchMarketDataProvider | None = None,
    config: ResearchPipelineConfig | None = None,
) -> ResearchScanResult:
    """Scan the complete supplied universe with bounded parallel market I/O."""
    cfg = config or ResearchPipelineConfig(
        fundamental_config=FundamentalConfig.from_environment()
    )
    cfg.validate()
    requested_symbols = _validate_symbols(symbols)

    fundamentals_by_symbol: dict[str, FundamentalMetrics] = {}
    fundamental_error: str | None = None
    if cfg.fundamental_config.enabled:
        try:
            fundamentals_by_symbol = CachedFundamentals(
                config=cfg.fundamental_config
            ).get_many(requested_symbols)
        except (FundamentalDataError, ValueError, TypeError) as exc:
            fundamental_error = str(exc)

    results: list[SymbolResearchResult] = []
    prepared: list[tuple[str, str, tuple[ResearchBar, ...], ResearchDataQualityReport, TechnicalSnapshot, AdvancedTechnicalSnapshot, str | None]] = []

    if provider is not None:
        prepared_provider_results = [
            _prepare_symbol(symbol, cfg=cfg, provider=provider)
            for symbol in requested_symbols
        ]
    else:
        def worker(symbol: str):
            worker_provider = ResilientMarketDataProvider(timeout=12.0)
            return _prepare_symbol(symbol, cfg=cfg, provider=worker_provider)

        prepared_provider_results = []
        with ThreadPoolExecutor(max_workers=_scan_workers(), thread_name_prefix="market-scan") as executor:
            future_map = {executor.submit(worker, symbol): symbol for symbol in requested_symbols}
            for future in as_completed(future_map):
                symbol = future_map[future]
                try:
                    prepared_provider_results.append(future.result())
                except Exception as exc:
                    market_symbol = indian_equity_symbol(symbol, cfg.exchange)
                    prepared_provider_results.append(
                        _result(
                            requested_symbol=symbol,
                            yahoo_symbol=market_symbol,
                            data_status="DATA_ERROR",
                            reason=f"Worker failure: {exc}",
                        )
                    )

    order = {symbol: index for index, symbol in enumerate(requested_symbols)}
    prepared_provider_results.sort(
        key=lambda item: order.get(item[0] if isinstance(item, tuple) else item.requested_symbol, 10**9)
    )

    for item in prepared_provider_results:
        if isinstance(item, SymbolResearchResult):
            results.append(item)
        else:
            prepared.append(item)

    data_error_count = sum(1 for result in results if result.data_status == "DATA_ERROR")
    quality_failure_count = sum(1 for result in results if result.data_status == "QUALITY_REJECTED")
    technical_rejection_count = sum(1 for result in results if result.data_status == "TECHNICAL_REJECTED")
    fundamental_error_count = 0
    candidate_count = 0
    approved_candidates: list[object] = []

    context_rows = {item[0]: item[2] for item in prepared}
    try:
        market_context = build_market_context(context_rows)
    except Exception as exc:
        for requested_symbol, market_symbol, bars, quality, snapshot, advanced, source in prepared:
            results.append(
                _result(
                    requested_symbol=requested_symbol,
                    yahoo_symbol=market_symbol,
                    data_status="MARKET_CONTEXT_UNAVAILABLE",
                    bars=bars,
                    quality=quality,
                    technical=snapshot,
                    advanced=advanced,
                    reason=(
                        "Market regime/sector context unavailable; candidate generation blocked: "
                        + str(exc)
                    ),
                    data_source=source,
                )
            )
        results.sort(key=lambda result: order.get(result.requested_symbol, 10**9))
        return ResearchScanResult(
            requested_count=len(requested_symbols),
            scanned_count=len(requested_symbols),
            data_error_count=data_error_count,
            quality_failure_count=quality_failure_count,
            technical_rejection_count=technical_rejection_count + len(prepared),
            fundamental_error_count=fundamental_error_count,
            candidate_count=0,
            actionable_count=0,
            buy_count=0,
            sell_count=0,
            results=tuple(results),
            actionable_candidates=(),
        )

    for requested_symbol, market_symbol, bars, quality, snapshot, advanced, source in prepared:
        inferred_direction = (
            "BUY" if snapshot.trend == "BULLISH"
            else "SELL" if snapshot.trend == "BEARISH"
            else None
        )
        sector = market_context.symbol_sector.get(
            market_symbol,
            market_context.symbol_sector.get(requested_symbol, "OTHER"),
        )
        if inferred_direction is None:
            technical_rejection_count += 1
            results.append(_result(
                requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
                data_status="NO_CANDIDATE", bars=bars, quality=quality,
                technical=snapshot, advanced=advanced,
                reason="Technical trend is not directional.", data_source=source,
            ))
            continue

        context_allowed, context_reason = candidate_context_allowed(
            inferred_direction, market_context, sector
        )
        context_detail = (
            f"{context_reason} NIFTY={market_context.nifty.regime}; "
            f"BANKNIFTY={market_context.banknifty.regime}; sector={sector}."
        )
        if not context_allowed:
            technical_rejection_count += 1
            results.append(_result(
                requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
                data_status="MARKET_CONTEXT_REJECTED", bars=bars, quality=quality,
                technical=snapshot, advanced=advanced, reason=context_detail,
                data_source=source,
            ))
            continue

        candidate = generate_candidate(
            snapshot, exchange=cfg.exchange, config=cfg.candidate_config, advanced=advanced
        )
        if candidate is None:
            technical_rejection_count += 1
            results.append(_result(
                requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
                data_status="NO_CANDIDATE", bars=bars, quality=quality,
                technical=snapshot, advanced=advanced,
                reason="Technical/advanced confirmation did not produce a candidate. " + context_detail,
                data_source=source,
            ))
            continue
        candidate_count += 1

        candidate_provider = ResilientMarketDataProvider(timeout=12.0)
        mtf, mtf_status, mtf_error = _load_mtf(candidate_provider, market_symbol, cfg)
        if cfg.require_mtf_confirmation:
            if mtf_status is not None:
                technical_rejection_count += 1
                results.append(_result(
                    requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
                    data_status=mtf_status, bars=bars, quality=quality,
                    technical=snapshot, advanced=advanced, candidate=candidate,
                    reason=f"MTF gate blocked candidate: {mtf_error or 'confirmation unavailable'}. {_mtf_reason(mtf)} {context_detail}",
                    data_source=source, mtf=mtf,
                ))
                continue
            allowed, gate_reason = _mtf_gate(candidate.direction, mtf)
            if not allowed:
                technical_rejection_count += 1
                results.append(_result(
                    requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
                    data_status="MTF_REJECTED", bars=bars, quality=quality,
                    technical=snapshot, advanced=advanced, candidate=candidate,
                    reason=f"{gate_reason} {_mtf_reason(mtf)} {context_detail}",
                    data_source=source, mtf=mtf,
                ))
                continue
        elif mtf is None:
            mtf_status = "MTF_NOT_AVAILABLE"

        if candidate.direction == "BUY":
            if fundamental_error is not None:
                fundamental_error_count += 1
                assessment = FundamentalAssessment(
                    required=cfg.fundamental_config.require_for_buy,
                    passed=not cfg.fundamental_config.require_for_buy,
                    status="FUNDAMENTALS_UNAVAILABLE", score=0.0,
                    checks=("Fundamental provider failed: " + fundamental_error,), metrics=None,
                )
            else:
                assessment = assess_buy_fundamentals(
                    fundamentals_by_symbol.get(requested_symbol), config=cfg.fundamental_config
                )
            f_status, f_score, f_checks, f_metrics = _fundamental_context(assessment)
            if not assessment.passed:
                results.append(_result(
                    requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
                    data_status="FUNDAMENTALS_REJECTED", bars=bars, quality=quality,
                    technical=snapshot, advanced=advanced, candidate=candidate,
                    reason="BUY blocked by fundamental quality gate: " + "; ".join(f_checks) + f". {_mtf_reason(mtf)} {context_detail}",
                    data_source=source, fundamental_status=f_status,
                    fundamental_score=f_score, fundamental_checks=f_checks,
                    fundamentals=f_metrics, mtf=mtf,
                ))
                continue
        else:
            assessment = FundamentalAssessment(
                required=False, passed=True, status="FUNDAMENTALS_NOT_REQUIRED",
                score=None if not cfg.fundamental_config.enabled else 100.0,
                checks=("Fundamental gate not used for intraday SELL candidates.",),
                metrics=fundamentals_by_symbol.get(requested_symbol),
            )
            f_status, f_score, f_checks, f_metrics = _fundamental_context(assessment)

        risk = assess_candidate(candidate, cfg.account_equity, config=cfg.risk_config)
        if risk.approved:
            approved_candidates.append(candidate)
            status = "ACTIONABLE"
            reason = (
                "Technical, market context, M/W/D confirmation, advanced, fundamental and risk checks passed"
                if candidate.direction == "BUY"
                else "Technical, market context, M/W/D confirmation, advanced and risk checks passed"
            )
        else:
            status = "RISK_REJECTED"
            reason = ", ".join(risk.reasons) or "Risk checks failed"
        results.append(_result(
            requested_symbol=requested_symbol, yahoo_symbol=market_symbol,
            data_status=status, bars=bars, quality=quality,
            technical=snapshot, advanced=advanced, candidate=candidate, risk=risk,
            reason=reason + f". {_mtf_reason(mtf)} {context_detail}", data_source=source,
            fundamental_status=f_status, fundamental_score=f_score,
            fundamental_checks=f_checks, fundamentals=f_metrics, mtf=mtf,
        ))

    ranked = rank_candidates(
        approved_candidates,
        minimum_confidence=cfg.minimum_rank_confidence,
        minimum_rr=cfg.minimum_rank_rr,
    )
    actionable = tuple(ranked[: cfg.maximum_actionable_candidates])
    buy_count = sum(1 for candidate in actionable if candidate.direction == "BUY")
    sell_count = sum(1 for candidate in actionable if candidate.direction == "SELL")
    results.sort(key=lambda result: order.get(result.requested_symbol, 10**9))
    return ResearchScanResult(
        requested_count=len(requested_symbols),
        scanned_count=len(requested_symbols),
        data_error_count=data_error_count,
        quality_failure_count=quality_failure_count,
        technical_rejection_count=technical_rejection_count,
        fundamental_error_count=fundamental_error_count,
        candidate_count=candidate_count,
        actionable_count=len(actionable),
        buy_count=buy_count,
        sell_count=sell_count,
        results=tuple(results),
        actionable_candidates=tuple(actionable),
    )


def rank_research_results(results: list[ResearchResult]):
    assessments = [
        result.risk for result in results if result.risk is not None and result.risk.approved
    ]
    return sorted(
        assessments, key=lambda item: (item.confidence, item.risk_reward), reverse=True
    )
