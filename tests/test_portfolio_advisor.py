from intraday_bot.portfolio_advisor import (
    benchmark_relative_return,
    basket_return,
    build_basket,
    normalize_symbols,
    rebalance_advice,
)


def test_normalize_and_build_basket_do_not_invent_symbols():
    assert normalize_symbols([" swiggy ", "SWIGGY", "", "PCJEWELLER"]) == ("SWIGGY", "PCJEWELLER")
    basket = build_basket("User Basket", ["swiggy", "SWIGGY"], benchmark="NIFTY50")
    assert basket.symbols == ("SWIGGY",)
    assert basket.benchmark == "NIFTY50"


def test_basket_return_and_benchmark_are_source_data_only():
    result = basket_return({"AAA": [100, 110], "BBB": [200, 180]})
    assert result["status"] == "AVAILABLE"
    assert result["basket_return_pct"] == 0.0
    assert benchmark_relative_return(result["basket_return_pct"], 2.0)["relative_return_pct"] == -2.0


def test_missing_basket_prices_are_not_treated_as_zero_return():
    result = basket_return({"AAA": [100], "BBB": []})
    assert result["status"] == "DATA UNAVAILABLE"
    assert result["basket_return_pct"] is None


def test_rebalance_uses_actual_market_values_and_source_limits():
    positions = [
        {"symbol": "AAA", "sector": "IT", "market_value": 300},
        {"symbol": "BBB", "sector": "IT", "market_value": 100},
        {"symbol": "CCC", "sector": "BANK", "market_value": 100},
    ]
    result = rebalance_advice(positions)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["company_weights_pct"]["AAA"] == 60.0
    assert result["sector_weights_pct"]["IT"] == 80.0
    assert any(x["action"] == "REDUCE_CONCENTRATION" for x in result["advice"])
    assert any(x["action"] == "REDUCE_SECTOR_CONCENTRATION" for x in result["advice"])
    assert result["advisory_only"] is True
