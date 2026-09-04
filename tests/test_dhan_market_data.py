from __future__ import annotations

from intraday_bot.brokers import DhanBroker
from intraday_bot.config import classify_dhan_manual_credential


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


def test_eight_character_dhan_value_is_not_treated_as_access_token():
    assert classify_dhan_manual_credential("12345678") == "APP_KEY_NOT_ACCESS_TOKEN"


def test_long_manual_dhan_value_is_accepted_as_access_credential():
    assert classify_dhan_manual_credential("a" * 32) == "MANUAL_ACCESS_CREDENTIAL"


def test_empty_dhan_credential_is_unconfigured():
    assert classify_dhan_manual_credential("") == "NONE"


def test_index_historical_payload_omits_derivative_only_fields():
    payload = DhanBroker._daily_history_payload(
        "999", "NSE_IDX", "INDEX", "2026-01-01", "2026-09-04"
    )
    assert payload == {
        "securityId": "999",
        "exchangeSegment": "NSE_IDX",
        "instrument": "INDEX",
        "fromDate": "2026-01-01",
        "toDate": "2026-09-04",
    }


def test_equity_historical_payload_keeps_derivative_compatibility_fields_out():
    payload = DhanBroker._daily_history_payload(
        "1333", "NSE_EQ", "EQUITY", "2026-01-01", "2026-09-04"
    )
    assert "expiryCode" not in payload
    assert "oi" not in payload
