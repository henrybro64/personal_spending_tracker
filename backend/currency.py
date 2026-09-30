from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, cast

DisplayCurrency = Literal["USD", "TWD"]
SupportedCurrency = Literal[
    "TWD",
    "USD",
    "JPY",
    "EUR",
    "CNY",
    "HKD",
    "PHP",
    "SGD",
    "KRW",
    "GBP",
]

SUPPORTED_CURRENCY_CODES: tuple[SupportedCurrency, ...] = (
    "TWD",
    "USD",
    "JPY",
    "EUR",
    "CNY",
    "HKD",
    "PHP",
    "SGD",
    "KRW",
    "GBP",
)
DEFAULT_BASE_CURRENCY: SupportedCurrency = "TWD"

DEFAULT_USD_TWD_RATE = 32.0
RATE_SOURCE = "static_env"
MONEY_QUANT = Decimal("0.01")


def get_usd_twd_exchange_rate() -> float:
    configured = os.getenv("USD_TWD_EXCHANGE_RATE", "").strip()
    if not configured:
        return DEFAULT_USD_TWD_RATE
    try:
        parsed = float(configured)
    except ValueError as exc:
        raise RuntimeError(
            "USD_TWD_EXCHANGE_RATE must be a positive number when set."
        ) from exc
    if parsed <= 0:
        raise RuntimeError("USD_TWD_EXCHANGE_RATE must be greater than zero.")
    return parsed


def normalize_currency_code(currency_code: str | None) -> DisplayCurrency:
    """Collapse a code to the USD/TWD pair used by AI insight bucketing.

    Anything other than TWD becomes USD. Dashboard totals must not use this:
    they convert only real USD and TWD amounts.
    """
    return "TWD" if currency_code and currency_code.upper() == "TWD" else "USD"


def normalize_supported_currency_code(
    currency_code: str | None,
    *,
    default: SupportedCurrency | None = None,
) -> SupportedCurrency:
    requested_code = (currency_code or "").strip().upper()
    if not requested_code and default is not None:
        return default
    if requested_code in SUPPORTED_CURRENCY_CODES:
        return cast(SupportedCurrency, requested_code)
    raise ValueError(
        "Currency must be one of: " + ", ".join(SUPPORTED_CURRENCY_CODES)
    )


def _decimal_from_float(value: float) -> Decimal:
    return Decimal(str(value))


def _round_money(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))


def derive_base_money_fields(
    *,
    original_amount: float,
    original_currency: str,
    base_currency: str = DEFAULT_BASE_CURRENCY,
    exchange_rate: float | None = None,
) -> tuple[float | None, str, float | None]:
    normalized_original_currency = normalize_supported_currency_code(original_currency)
    normalized_base_currency = normalize_supported_currency_code(
        base_currency,
        default=DEFAULT_BASE_CURRENCY,
    )

    if normalized_original_currency == normalized_base_currency:
        return (
            _round_money(_decimal_from_float(original_amount)),
            normalized_base_currency,
            1.0,
        )

    if exchange_rate is None:
        return None, normalized_base_currency, None

    if exchange_rate <= 0:
        raise ValueError("Exchange rate must be greater than zero when provided.")

    base_amount = _decimal_from_float(original_amount) * _decimal_from_float(
        exchange_rate
    )
    return _round_money(base_amount), normalized_base_currency, float(exchange_rate)


def convert_amount(
    amount: float,
    source_currency: str,
    target_currency: DisplayCurrency,
    *,
    exchange_rate: float | None = None,
) -> float:
    normalized_source = (source_currency or "").strip().upper()
    if normalized_source not in ("USD", "TWD"):
        raise ValueError(
            "Display conversion only supports USD and TWD. "
            f"Got {normalized_source or 'empty'}."
        )
    if normalized_source == target_currency:
        return amount

    rate = exchange_rate if exchange_rate is not None else get_usd_twd_exchange_rate()
    if normalized_source == "USD" and target_currency == "TWD":
        return amount * rate
    return amount / rate


def exchange_rate_metadata() -> dict[str, str | float]:
    return {
        "usd_twd_rate": get_usd_twd_exchange_rate(),
        "rate_source": RATE_SOURCE,
    }
