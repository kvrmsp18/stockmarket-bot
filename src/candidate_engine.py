"""Deterministic intraday trade-candidate generation.

Research-only candidate generation. No broker orders are placed, modified, or
cancelled by this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from .advanced_indicators import AdvancedTechnicalSnapshot
from .models import TradeCandidate
from .technical_analysis import TechnicalSnapshot


class CandidateEngineError(ValueError):
    """Raised when candidate generation cannot be performed safely."""


@dataclass(frozen=True)
class CandidateConfig:
    minimum_risk_reward: float = 2.0
    atr_stop_multiplier: float = 1.0
    atr_target_multiplier: float = 2.0
    minimum_confidence: float = 0.85
    minimum_volume_ratio: float = 1.0
    preferred_volume_ratio: float = 1.20
    minimum_directional_volume_score: float = 0.10
    minimum_entry_confirmation_score: float = 0.60
    require_vwap_alignment: bool = False
    require_entry_trigger: bool = True
    require_mtf_rsi_confirmation: bool = True
    minimum_mtf_rsi_agreement: float = 2.0 / 3.0
    mtf_partial_confidence_penalty: float = 0.05
    mtf_full_confidence_bonus: float = 0.03
    fresh_entry_trigger_bonus: float = 0.085
    require_advanced_confirmation: bool = True
    minimum_advanced_confirmation_score: float = 0.58
    advanced_confidence_weight: float = 0.15
    rvol_spike_bonus: float = 0.03


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _directional_ma_score(snapshot: TechnicalSnapshot, direction: str) -> float:
    checks: list[bool] = []
    for value in (
        getattr(snapshot, "price_vs_ema20_pct", None),
        getattr(snapshot, "ema20_vs_sma20_pct", None),
        getattr(snapshot, "sma20_vs_sma50_pct", None),
        getattr(snapshot, "ema20_slope_pct", None),
    ):
        if value is not None:
            checks.append(float(value) > 0 if direction == "BUY" else float(value) < 0)
    return sum(checks) / len(checks) if checks else 0.50


def _volume_score(snapshot: TechnicalSnapshot, cfg: CandidateConfig, direction: str) -> tuple[float, bool]:
    ratio = getattr(snapshot, "volume_ratio", None)
    directional = getattr(snapshot, "directional_volume_score", None)
    if ratio is None:
        return 0.50, False
    ratio = float(ratio)
    if ratio < cfg.minimum_volume_ratio:
        return 0.0, True
    magnitude = _clamp(ratio / max(cfg.preferred_volume_ratio, 1.0))
    if directional is None:
        return magnitude * 0.5, False
    directional = float(directional)
    if abs(directional) < cfg.minimum_directional_volume_score:
        return magnitude * 0.5, False
    aligned = directional >= cfg.minimum_directional_volume_score if direction == "BUY" else directional <= -cfg.minimum_directional_volume_score
    return _clamp(0.35 * magnitude + 0.65 * (_clamp(abs(directional)) if aligned else 0.0)), not aligned


def _rsi_score(snapshot: TechnicalSnapshot, direction: str) -> float:
    rsi = getattr(snapshot, "rsi_14", None)
    if rsi is None:
        return 0.50
    rsi = float(rsi)
    if direction == "BUY":
        if rsi < 30:
            return 0.35
        if rsi <= 55:
            return 0.65 + (rsi - 30.0) / 25.0 * 0.25
        if rsi <= 70:
            return 0.90
        return 0.55
    if rsi > 70:
        return 0.35
    if rsi >= 45:
        return 0.65 + (70.0 - rsi) / 25.0 * 0.25
    if rsi >= 30:
        return 0.90
    return 0.55


def _momentum_score(snapshot: TechnicalSnapshot, direction: str) -> float:
    momentum = getattr(snapshot, "momentum_5", None)
    if momentum is None:
        return 0.50
    momentum = float(momentum)
    aligned = momentum > 0 if direction == "BUY" else momentum < 0
    return 0.20 + 0.80 * _clamp(abs(momentum) / 2.0) if aligned else 0.05


def _vwap_score(snapshot: TechnicalSnapshot, direction: str) -> tuple[float, bool]:
    vwap = getattr(snapshot, "vwap", None)
    price_vs_vwap = getattr(snapshot, "price_vs_vwap_pct", None)
    if vwap is None or price_vs_vwap is None:
        return 0.50, False
    aligned = float(price_vs_vwap) > 0 if direction == "BUY" else float(price_vs_vwap) < 0
    return (1.0 if aligned else 0.0), not aligned


def _mtf_rsi_confirmation(snapshot: TechnicalSnapshot, exchange: str, direction: str, cfg: CandidateConfig) -> tuple[float, str, bool]:
    if not cfg.require_mtf_rsi_confirmation:
        return 1.0, "MTF RSI gate disabled", True
    try:
        from .mtf_rsi_display import calculate_display_mtf_rsi_history
        mtf = calculate_display_mtf_rsi_history(snapshot.symbol, exchange, provider=None).snapshot
    except Exception as exc:
        return 0.0, f"MTF RSI unavailable: {exc}", False
    values = (mtf.monthly_rsi_14, mtf.weekly_rsi_14, mtf.daily_rsi_14)
    if any(value is None for value in values):
        return 0.0, "MTF RSI insufficient history", False
    labels = "/".join(f"{value:.1f}" for value in values)
    if direction == "BUY":
        aligned = sum(1 for value in values if value >= 50.0)
        opposing = sum(1 for value in values if value < 45.0)
        side = "bullish" if aligned == 3 else "mixed bullish"
    else:
        aligned = sum(1 for value in values if value <= 50.0)
        opposing = sum(1 for value in values if value > 55.0)
        side = "bearish" if aligned == 3 else "mixed bearish"
    agreement = aligned / 3.0
    allowed = agreement >= cfg.minimum_mtf_rsi_agreement and opposing == 0
    return agreement, f"MTF RSI M/W/D={labels}; {side}; agreement={agreement:.0%}; opposing={opposing}", allowed


def _advanced_confirmation(advanced: AdvancedTechnicalSnapshot | None, direction: str, cfg: CandidateConfig) -> tuple[float, bool, str]:
    if advanced is None:
        return 0.50, not cfg.require_advanced_confirmation, "Advanced indicators unavailable"
    score = float(advanced.bullish_score if direction == "BUY" else advanced.bearish_score)
    aligned = advanced.direction == ("BULLISH" if direction == "BUY" else "BEARISH")
    threshold = _clamp(cfg.minimum_advanced_confirmation_score)
    allowed = score >= threshold and aligned
    rvol_text = f"{advanced.rvol:.2f}x" if advanced.rvol is not None else "NA"
    label = f"Advanced {direction}: score={score:.0%}; direction={advanced.direction}; RVOL={rvol_text}"
    if advanced.warnings:
        label += f"; warnings={','.join(advanced.warnings)}"
    return score, (allowed or not cfg.require_advanced_confirmation), label


def build_candidate(*, symbol: str, exchange: str, direction: str, entry: float, stop_loss: float, target: float, confidence: float, reason: str, liquidity_ok: bool = True, market_regime_ok: bool = True, news_status: str = "UNKNOWN", holding_window: str = "INTRADAY") -> TradeCandidate:
    if not isinstance(symbol, str) or not symbol.strip():
        raise CandidateEngineError("Symbol is required.")
    if not isinstance(exchange, str) or not exchange.strip():
        raise CandidateEngineError("Exchange is required.")
    direction = direction.strip().upper()
    if direction not in {"BUY", "SELL"}:
        raise CandidateEngineError("Direction must be either BUY or SELL.")
    if not all(isinstance(v, (int, float)) for v in (entry, stop_loss, target, confidence)):
        raise CandidateEngineError("Candidate numeric values must be numeric.")
    if entry <= 0 or stop_loss <= 0 or target <= 0 or not 0 <= confidence <= 1:
        raise CandidateEngineError("Invalid candidate numeric values.")
    if direction == "BUY":
        if stop_loss >= entry or target <= entry:
            raise CandidateEngineError("Invalid BUY levels.")
        risk_per_share, potential_per_share = entry - stop_loss, target - entry
    else:
        if stop_loss <= entry or target >= entry:
            raise CandidateEngineError("Invalid SELL levels.")
        risk_per_share, potential_per_share = stop_loss - entry, entry - target
    rr = potential_per_share / risk_per_share
    return TradeCandidate(symbol=symbol.strip().upper(), exchange=exchange.strip().upper(), direction=direction, entry=round(float(entry), 2), stop_loss=round(float(stop_loss), 2), target=round(float(target), 2), risk_per_share=round(float(risk_per_share), 2), potential_per_share=round(float(potential_per_share), 2), potential_percent=round(potential_per_share / entry * 100.0, 2), risk_reward=round(rr, 2), confidence=round(float(confidence), 2), reason=str(reason).strip(), liquidity_ok=bool(liquidity_ok), market_regime_ok=bool(market_regime_ok), news_status=str(news_status), holding_window=str(holding_window))


def generate_candidate(snapshot: TechnicalSnapshot, *, exchange: str, config: CandidateConfig | None = None, advanced: AdvancedTechnicalSnapshot | None = None) -> TradeCandidate | None:
    cfg = config or CandidateConfig()
    exchange_normalized = exchange.strip().upper()
    if not exchange_normalized:
        raise CandidateEngineError("Exchange is required.")
    if snapshot.close <= 0:
        raise CandidateEngineError("Snapshot close must be positive.")
    atr = getattr(snapshot, "atr_14", None)
    if atr is None or atr <= 0:
        return None
    bullish, bearish = snapshot.trend == "BULLISH", snapshot.trend == "BEARISH"
    if not bullish and not bearish:
        return None
    rsi, momentum = getattr(snapshot, "rsi_14", None), getattr(snapshot, "momentum_5", None)
    if rsi is None or momentum is None:
        return None
    direction = "BUY" if bullish else "SELL"
    if bullish and not (rsi >= 50 and momentum > 0):
        return None
    if bearish and not (rsi <= 50 and momentum < 0):
        return None

    volume_score, volume_conflict = _volume_score(snapshot, cfg, direction)
    if volume_conflict:
        return None
    vwap_score, vwap_conflict = _vwap_score(snapshot, direction)
    if cfg.require_vwap_alignment and vwap_conflict:
        return None

    if cfg.require_entry_trigger:
        required_entry_score = max(cfg.minimum_entry_confirmation_score, 0.60)
        if getattr(snapshot, "entry_confirmation_score", 0.0) < required_entry_score:
            return None
        if getattr(snapshot, "entry_confirmation", "UNAVAILABLE") != f"{direction}_TRIGGER":
            return None

    advanced_score, advanced_allowed, advanced_text = _advanced_confirmation(advanced, direction, cfg)
    if cfg.require_advanced_confirmation and advanced is not None and not advanced_allowed:
        return None

    entry = float(snapshot.close)
    risk_per_share = float(atr) * cfg.atr_stop_multiplier
    potential_per_share = float(atr) * cfg.atr_target_multiplier
    if risk_per_share <= 0 or potential_per_share <= 0:
        return None
    rr = potential_per_share / risk_per_share
    if rr < cfg.minimum_risk_reward:
        return None
    stop_loss = entry - risk_per_share if bullish else entry + risk_per_share
    target = entry + potential_per_share if bullish else entry - potential_per_share

    ma_score = _directional_ma_score(snapshot, direction)
    momentum_score = _momentum_score(snapshot, direction)
    rsi_score = _rsi_score(snapshot, direction)
    entry_score = float(getattr(snapshot, "entry_confirmation_score", 0.50))
    if getattr(snapshot, "entry_confirmation", "UNAVAILABLE") in {"UNAVAILABLE", "NO_TREND"}:
        entry_score = 0.50
    base_confidence = _clamp(0.10 + 0.25 * ma_score + 0.15 * momentum_score + 0.15 * rsi_score + 0.15 * volume_score + 0.10 * vwap_score + 0.10 * max(0.25, entry_score))
    if advanced is not None:
        weight = _clamp(cfg.advanced_confidence_weight, 0.0, 0.40)
        base_confidence = _clamp((1.0 - weight) * base_confidence + weight * advanced_score)

    mtf_agreement, mtf_text, mtf_allowed = _mtf_rsi_confirmation(snapshot, exchange_normalized, direction, cfg)
    if cfg.require_mtf_rsi_confirmation and not mtf_allowed:
        return None
    confidence = _clamp(base_confidence + (cfg.mtf_full_confidence_bonus if mtf_agreement >= 1.0 else -cfg.mtf_partial_confidence_penalty)) if cfg.require_mtf_rsi_confirmation else base_confidence
    if cfg.require_entry_trigger:
        confidence = _clamp(confidence + cfg.fresh_entry_trigger_bonus)
    rvol = getattr(advanced, "rvol", None) if advanced is not None else None
    if rvol is not None and rvol >= 2.0:
        confidence = _clamp(confidence + cfg.rvol_spike_bonus)
    if confidence < cfg.minimum_confidence:
        return None

    volume_ratio = getattr(snapshot, "volume_ratio", None)
    volume_direction = getattr(snapshot, "volume_direction", "UNKNOWN")
    vwap = getattr(snapshot, "vwap", None)
    price_vs_vwap = getattr(snapshot, "price_vs_vwap_pct", None)
    volume_text = f" volume={volume_ratio:.2f}x/{volume_direction};" if volume_ratio is not None else " volume=unavailable;"
    vwap_text = f" VWAP={vwap:.2f} ({price_vs_vwap:+.2f}%);" if vwap is not None and price_vs_vwap is not None else " VWAP=unavailable;"
    reason = f"{snapshot.trend}; MA confirmation={ma_score:.0%}; RSI14={rsi:.1f}; 5-bar momentum={momentum:+.2f}%;{volume_text}{vwap_text} entry={getattr(snapshot, 'entry_confirmation', 'UNAVAILABLE')}; entry score={entry_score:.0%}; ATR14={float(atr):.2f}; {advanced_text}; {mtf_text}; confidence base={base_confidence:.0%} final={confidence:.0%}."
    return TradeCandidate(symbol=snapshot.symbol, exchange=exchange_normalized, direction=direction, entry=round(entry, 2), stop_loss=round(stop_loss, 2), target=round(target, 2), risk_per_share=round(risk_per_share, 2), potential_per_share=round(potential_per_share, 2), potential_percent=round(potential_per_share / entry * 100.0, 2), risk_reward=round(rr, 2), confidence=round(confidence, 2), reason=reason, liquidity_ok=(volume_ratio is None or volume_ratio >= cfg.minimum_volume_ratio), market_regime_ok=snapshot.trend in {"BULLISH", "BEARISH"}, news_status="NOT_CHECKED", holding_window="INTRADAY")


def rank_candidates(candidates, minimum_confidence=0.85, minimum_rr=1.5):
    qualified = [c for c in candidates if c.confidence >= minimum_confidence and c.risk_reward >= minimum_rr and c.liquidity_ok and c.market_regime_ok]
    return sorted(qualified, key=lambda candidate: (candidate.confidence, candidate.risk_reward), reverse=True)
