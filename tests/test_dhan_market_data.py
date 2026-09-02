from __future__ import annotations

from intraday_bot.brokers import DhanBroker


def test_quote_rows_normalizes_dhan_quote_payload():
    payload = {
        "data": {
            "NSE_EQ": {
                "1333": {"last_price": 1750.25, "volume": 123456},
                "11536": {"ltp": 42.5},
            }
        },
        "status": "success",
    }
    rows = DhanBroker._quote_rows(payload, "NSE_EQ")
    assert rows["1333"]["last_price"] == 1750.25
    assert rows["11536"]["ltp"] == 42.5


def test_ltp_scalar_payload_is_usable_as_price_row():
    payload = {"data": {"NSE_EQ": {"1333": 1750.25}}}
    rows = DhanBroker._quote_rows(payload, "NSE_EQ")
    assert rows == {"1333": {"last_price": 1750.25, "ltp": 1750.25}}
