import pytest

from backend.currency import (
    DEFAULT_BASE_CURRENCY,
    convert_amount,
    derive_base_money_fields,
    normalize_supported_currency_code,
)


def test_normalize_supported_currency_code_accepts_known_codes() -> None:
    assert normalize_supported_currency_code("usd") == "USD"
    assert normalize_supported_currency_code(" TWD ") == "TWD"


def test_normalize_supported_currency_code_uses_default() -> None:
    assert (
        normalize_supported_currency_code(None, default=DEFAULT_BASE_CURRENCY)
        == "TWD"
    )


def test_normalize_supported_currency_code_rejects_unknown_codes() -> None:
    with pytest.raises(ValueError, match="Currency must be one of"):
        normalize_supported_currency_code("NTD")


def test_derive_base_money_fields_same_currency() -> None:
    base_amount, base_currency, exchange_rate = derive_base_money_fields(
        original_amount=350.0,
        original_currency="TWD",
        base_currency="TWD",
    )

    assert base_amount == pytest.approx(350.0)
    assert base_currency == "TWD"
    assert exchange_rate == pytest.approx(1.0)


def test_derive_base_money_fields_with_explicit_rate() -> None:
    base_amount, base_currency, exchange_rate = derive_base_money_fields(
        original_amount=25.99,
        original_currency="USD",
        base_currency="TWD",
        exchange_rate=32.4,
    )

    assert base_amount == pytest.approx(842.08)
    assert base_currency == "TWD"
    assert exchange_rate == pytest.approx(32.4)


def test_derive_base_money_fields_without_rate_leaves_base_null() -> None:
    base_amount, base_currency, exchange_rate = derive_base_money_fields(
        original_amount=25.99,
        original_currency="USD",
        base_currency="TWD",
    )

    assert base_amount is None
    assert base_currency == "TWD"
    assert exchange_rate is None


def test_convert_amount_uses_static_rate_for_usd_and_twd() -> None:
    assert convert_amount(20, "USD", "TWD") == pytest.approx(640.0)
    assert convert_amount(640, "TWD", "USD") == pytest.approx(20.0)
    assert convert_amount(20, "usd", "USD") == pytest.approx(20.0)


def test_convert_amount_rejects_currencies_other_than_usd_and_twd() -> None:
    with pytest.raises(ValueError, match="USD and TWD"):
        convert_amount(1000, "JPY", "TWD")
    with pytest.raises(ValueError, match="USD and TWD"):
        convert_amount(10, "EUR", "USD")


def test_derive_base_money_fields_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError, match="Exchange rate must be greater than zero"):
        derive_base_money_fields(
            original_amount=25.99,
            original_currency="USD",
            base_currency="TWD",
            exchange_rate=0,
        )
