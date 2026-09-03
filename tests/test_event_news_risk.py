from intraday_bot.event_news_risk import event_news_gate


def test_news_gate_is_not_a_trade_signal_when_feed_unavailable():
    result = event_news_gate({"source_status": "DATA UNAVAILABLE", "items": []})
    assert result["status"] == "DATA UNAVAILABLE"
    assert result["action"] == "NO_NEWS_DECISION"
    assert result["risk_level"] == "UNKNOWN"


def test_news_gate_flags_fresh_high_impact_event():
    result = event_news_gate(
        {
            "source_status": "AVAILABLE",
            "items": [{"title": "Example", "fresh": True}],
            "material_high_impact": [
                {"title": "Company investigation announced", "sentiment": "NEGATIVE", "materiality": 3}
            ],
        }
    )
    assert result["action"] == "REVIEW_BEFORE_ENTRY"
    assert result["risk_level"] == "HIGH"
    assert result["fresh_high_impact_count"] == 1


def test_news_gate_does_not_flag_old_items_as_fresh_event():
    result = event_news_gate(
        {
            "source_status": "AVAILABLE",
            "items": [{"title": "Old story", "fresh": False}],
            "material_high_impact": [],
        }
    )
    assert result["risk_level"] == "LOW"
    assert result["action"] == "NO_MATERIAL_EVENT_FLAG"
