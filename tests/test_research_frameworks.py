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


def test_source_formulas_remain_separate():
    assert source_valuation(10, 20) == 200
    assert source_roce(20, 100) == 20
    bundle = research_bundle("X", {"eps": 10, "pe": 20, "profit": 20, "capital": 100})
    assert bundle["valuation_price_from_eps_pe"] == 200
    assert bundle["roce_from_profit_capital"] == 20
