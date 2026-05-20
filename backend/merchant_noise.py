"""Merchant string helpers shared by statement parsing and batch import."""

from __future__ import annotations

import re
import unicodedata


def merchant_raw_is_digit_only_noise(merchant_raw: str) -> bool:
    """True when *merchant_raw* is only digits, grouping commas, and whitespace.

    CTBC PDF parsing sometimes turns table or installment fragments into
    transactions whose merchant column is a bare number (e.g. ``500``,
    ``2,585``). Those are not real merchant names.
    """
    collapsed = unicodedata.normalize("NFKC", merchant_raw)
    compact = re.sub(r"[\s,]+", "", collapsed)
    if not compact:
        return False
    return all(ch.isdigit() for ch in compact)
