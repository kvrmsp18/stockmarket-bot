from dataclasses import asdict
from .validation import validate_candidate_count

def daily_candidate_report(candidates, minimum=20):
    ranked = sorted(candidates, key=lambda c: (c.confidence, c.risk_reward), reverse=True)
    count_status = validate_candidate_count(ranked, minimum)
    return {
        "candidate_count": len(ranked),
        "minimum_requested": minimum,
        "minimum_met": count_status["target_met"],
        "candidates": [asdict(c) for c in ranked],
        "real_money_trading": False,
        "broker_orders_enabled": False,
    }
