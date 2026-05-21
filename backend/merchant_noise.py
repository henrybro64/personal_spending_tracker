"""Merchant string helpers shared by statement parsing and batch import."""

from __future__ import annotations

import re
import unicodedata

# Exact merchant strings that are bank/header/table fragments, not shops.
_MERCHANT_HEADER_NOISE: frozenset[str] = frozenset(
    {
        "CTBC",
        "CHINATRUST",
        "CHINA TRUST",
        "中國信託",
        "中國信託信用卡",
        "消費日",
        "入帳日",
        "DAILYSTMT",
        "(NO DESCRIPTION)",
    }
)

# Optional sign + digits (and optional .xx), e.g. -62, +12, 6,232.00 as merchant span.
_SIGNED_NUMERIC_MERCHANT = re.compile(
    r"^[+\-−－]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?$"
)


def _normalize_probe(merchant_raw: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", merchant_raw).upper().split())


def merchant_raw_is_parse_noise(merchant_raw: str) -> bool:
    """True when *merchant_raw* is not a real shop name (CTBC PDF/table junk).

    Covers digit-only spans (``500``), signed numeric fragments (``-62``),
    and bank/header tokens leaked into the merchant column (``CTBC``).
    """
    collapsed = unicodedata.normalize("NFKC", merchant_raw).strip()
    if not collapsed:
        return False

    if _normalize_probe(collapsed) in _MERCHANT_HEADER_NOISE:
        return True

    compact = re.sub(r"[\s,]+", "", collapsed)
    if not compact:
        return False

    if all(ch.isdigit() for ch in compact):
        return True

    if _SIGNED_NUMERIC_MERCHANT.fullmatch(compact):
        return True

    return False


def merchant_raw_is_digit_only_noise(merchant_raw: str) -> bool:
    """Backward-compatible alias; prefer :func:`merchant_raw_is_parse_noise`."""
    return merchant_raw_is_parse_noise(merchant_raw)
