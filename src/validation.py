def validate_candidate(candidate):
    errors = []
    if candidate.exchange not in {"NSE", "BSE"}:
        errors.append("Unsupported exchange")
    if candidate.direction not in {"BUY", "SELL"}:
        errors.append("Invalid direction")
    if candidate.entry <= 0:
        errors.append("Invalid entry")
    if candidate.stop_loss <= 0:
        errors.append("Invalid stop loss")
    if candidate.target <= 0:
        errors.append("Invalid target")
    if candidate.risk_per_share <= 0:
        errors.append("Risk must be positive")
    if candidate.potential_per_share <= 0:
        errors.append("Potential must be positive")
    if candidate.risk_reward <= 0:
        errors.append("Risk/reward must be positive")
    if not 0 <= candidate.confidence <= 100:
        errors.append("Confidence must be 0-100")
    return errors

def validate_candidate_count(candidates, minimum=20):
    # This intentionally does not fabricate candidates.
    return {
        "count": len(candidates),
        "minimum": minimum,
        "target_met": len(candidates) >= minimum
    }
