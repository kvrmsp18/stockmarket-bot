from intraday_bot import research
from intraday_bot.research import framework_analysis, research_bundle, scrap_analysis, source_valuation, source_roce


def test_all_five_frameworks_are_exposed():
    result = framework_analysis({})
    assert set(result["frameworks"]) == {"Buffett", "Rakesh Jhunjhunwala", "Peter Lynch", "100 Baggers", "CANSLIM"}
    assert result["status"] == "DATA UNAVAILABLE"
    assert all(result["frameworks"][name]["missing_data"] for name in result["frameworks"])


def test_scrap_thresholds_are_strict():
    assert scrap_analysis("X", {"sector_weight_pct": 15}).status != "REJECTED"
    assert scrap_analysis("X", {"sector_weight_pct": 15.01}).rejection_reason == "SCRAP_REJECTION"
    assert scrap_analysis("X", {"company_weight_pct": 25}).status != "REJECTED"
    assert scrap_analysis("X", {"company_weight_pct": 25.01}).rejection_reason == "SCRAP_REJECTION"
    assert scrap_analysis("X", {"red_flags": ["fraud"]}).rejection_reason == "RED_FLAG"


def test_source_scoring_is_nonzero_for_valid_data():
    data = {
        "profit_growth": 12.97,
        "eps_growth": 22.81,
        "roce": 8.70,
        "roe": 11.62,
        "debt_to_equity": 0.30,
        "earnings_quality": 2.22,
        "pe": 28.41,
    }
    score = research.fundamental_score(data)
    assert score > 0
    assert score <= 10
    assert research.valuation_score(data) > 0


def test_financial_sector_does_not_overweight_leverage():
    industrial = {
        "sector": "Industrial",
        "profit_growth": 12.97,
        "eps_growth": 21.94,
        "roce": 8.70,
        "roe": 11.62,
        "earnings_quality": -9.71,
        "debt_to_equity": 4.959,
    }
    financial = dict(industrial, sector="Financial Services")
    assert research.fundamental_score(financial) > research.fundamental_score(industrial)
    assert research.fundamental_score(financial) > 0


def test_frameworks_distinguish_neutral_from_negative():
    result = framework_analysis({
        "profit_growth": 5.0,
        "eps_growth": 5.0,
        "roce": 8.0,
        "roe": 8.0,
        "predictability": 0.6,
        "earnings_quality": 0.3,
        "debt_to_equity": 2.0,
        "pe": 25.0,
    })
    buffett = result["frameworks"]["Buffett"]
    assert "roce" in buffett["neutral_factors"]
    assert "pe" in buffett["neutral_factors"]
    assert "debt_to_equity" in buffett["neutral_factors"]
    assert "earnings_quality" in buffett["neutral_factors"]
    assert "predictability" in buffett["neutral_factors"]
    assert "missing_data" in buffett
    assert result["status"] == "AVAILABLE"


def test_negative_growth_remains_negative():
    result = framework_analysis({"profit_growth": -6.4, "eps_growth": -24.5})
    for name in ("Buffett", "Rakesh Jhunjhunwala", "Peter Lynch", "100 Baggers", "CANSLIM"):
        assert "profit_growth" in result["frameworks"][name]["negative_factors"]
    assert result["frameworks"]["Rakesh Jhunjhunwala"]["score"] < 5


def test_source_formulas_remain_separate(monkeypatch):
    monkeypatch.setattr(
        research,
        "oi_context",
        lambda symbol: {"status": "DATA UNAVAILABLE", "symbol": symbol, "source": "NSE OI Spurts"},
    )
    monkeypatch.setattr(
        research,
        "market_context",
        lambda: {"status": "DATA UNAVAILABLE", "source": "NSE OI Spurts"},
    )
    monkeypatch.setattr(
        research,
        "preopen_stock_context",
        lambda symbol: {"status": "DATA UNAVAILABLE", "symbol": symbol, "source": "NSE Pre-Open Market"},
    )
    monkeypatch.setattr(
        research,
        "preopen_market_context",
        lambda: {"status": "DATA UNAVAILABLE", "source": "NSE Pre-Open Market"},
    )
    monkeypatch.setattr(
        research,
        "fetch_event_news",
        lambda symbol: {"source_status": "DATA UNAVAILABLE", "symbol": symbol, "items": [], "material_high_impact": []},
    )

    assert source_valuation(10, 20) == 200
    assert source_roce(20, 100) == 20
    bundle = research_bundle("X", {"eps": 10, "pe": 20, "profit": 20, "capital": 100})
    assert bundle["valuation_price_from_eps_pe"] == 200
    assert bundle["roce_from_profit_capital"] == 20
    assert bundle["derivatives"]["status"] == "DATA UNAVAILABLE"
    assert bundle["preopen"]["status"] == "DATA UNAVAILABLE"
    assert bundle["preopen_market_context"]["status"] == "DATA UNAVAILABLE"
    assert bundle["event_news"]["source_status"] == "DATA UNAVAILABLE"
    assert bundle["event_news_risk"]["risk_level"] == "UNKNOWN"


def test_event_news_is_attached_to_research_bundle(monkeypatch):
    monkeypatch.setattr(research, "oi_context", lambda symbol: {"status": "AVAILABLE", "symbol": symbol})
    monkeypatch.setattr(research, "market_context", lambda: {"status": "AVAILABLE"})
    monkeypatch.setattr(research, "preopen_stock_context", lambda symbol: {"status": "AVAILABLE", "symbol": symbol})
    monkeypatch.setattr(research, "preopen_market_context", lambda: {"status": "AVAILABLE"})
    monkeypatch.setattr(
        research,
        "fetch_event_news",
        lambda symbol: {
            "source_status": "AVAILABLE",
            "symbol": symbol,
            "items": [{"title": "Company investigation announced", "fresh": True}],
            "material_high_impact": [{"title": "Company investigation announced", "sentiment": "NEGATIVE", "materiality": 3}],
        },
    )
    bundle = research_bundle("ABC", {"profit_growth": 10, "roe": 12})
    assert bundle["event_news"]["source_status"] == "AVAILABLE"
    assert bundle["event_news_risk"]["risk_level"] == "HIGH"
    assert bundle["event_news_risk"]["action"] == "REVIEW_BEFORE_ENTRY"
