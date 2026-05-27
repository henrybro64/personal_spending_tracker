from __future__ import annotations

import os
from typing import Literal

DisplayCurrency = Literal["USD", "TWD"]
SupportedCurrency = Literal["USD", "TWD"]

DEFAULT_USD_TWD_RATE = 32.0
RATE_SOURCE = "static_env"


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


def normalize_currency_code(currency_code: str | None) -> SupportedCurrency:
    return "TWD" if currency_code and currency_code.upper() == "TWD" else "USD"


def convert_amount(
    amount: float,
    source_currency: str,
    target_currency: DisplayCurrency,
    *,
    exchange_rate: float | None = None,
) -> float:
    normalized_source = normalize_currency_code(source_currency)
    if normalized_source == target_currency:
        return amount

    rate = exchange_rate if exchange_rate is not None else get_usd_twd_exchange_rate()
    if normalized_source == "USD" and target_currency == "TWD":
        return amount * rate
    if normalized_source == "TWD" and target_currency == "USD":
        return amount / rate
    return amount


def exchange_rate_metadata() -> dict[str, str | float]:
    return {
        "usd_twd_rate": get_usd_twd_exchange_rate(),
        "rate_source": RATE_SOURCE,
    }
