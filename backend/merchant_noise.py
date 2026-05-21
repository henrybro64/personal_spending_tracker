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

_SIGN_PREFIXES = "+-−－"

# CTBC installment rows: zero-padded period pairs (03-03) or explicit CTBC suffix.
# Do not use bare \d{1,2}-\d{1,2} — that would drop real merchants like 7-11.
_INSTALLMENT_MERCHANT = re.compile(
    r"^(?:\d{2}[-/]\d{2}(?:\s+CTBC)?|\d{1,2}[-/]\d{1,2}\s+CTBC)$",
    re.IGNORECASE,
)


def _normalize_probe(merchant_raw: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", merchant_raw).upper().split())


def _compact_merchant(merchant_raw: str) -> str:
    return re.sub(r"[\s,]+", "", unicodedata.normalize("NFKC", merchant_raw))


def _is_numeric_merchant_token(compact: str) -> bool:
    """True when compact text is only an optional sign and a number."""
    if not compact:
        return False

    body = compact
    if body[0] in _SIGN_PREFIXES:
        body = body[1:]
    if not body:
        return False

    if body.count(".") > 1:
        return False
    if "." in body:
        whole, fraction = body.split(".", 1)
        return whole.isdigit() and fraction.isdigit()
    return body.isdigit()


def merchant_raw_is_parse_noise(merchant_raw: str) -> bool:
    """True when *merchant_raw* is not a real shop name (CTBC PDF/table junk).

    Covers bare numbers (``500``), signed amounts used as merchants
    (``-62``, ``-1,117``, ``-74,457``), installment markers (``03-03 CTBC``),
    and bank/header tokens (``CTBC``).
    """
    collapsed = unicodedata.normalize("NFKC", merchant_raw).strip()
    if not collapsed:
        return False

    if _normalize_probe(collapsed) in _MERCHANT_HEADER_NOISE:
        return True

    if _INSTALLMENT_MERCHANT.match(collapsed):
        return True

    return _is_numeric_merchant_token(_compact_merchant(collapsed))


def merchant_raw_is_digit_only_noise(merchant_raw: str) -> bool:
    """Backward-compatible alias; prefer :func:`merchant_raw_is_parse_noise`."""
    return merchant_raw_is_parse_noise(merchant_raw)
