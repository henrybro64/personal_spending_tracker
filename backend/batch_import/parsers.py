from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .category import CategoryEngine
from .models import NormalizedTransaction


US_STATEMENT_PERIOD_PATTERN = re.compile(
    r"Opening/Closing Date\s+"
    r"(?P<start>\d{2}/\d{2}/\d{2,4})"
    r"\s*-\s*"
    r"(?P<end>\d{2}/\d{2}/\d{2,4})",
    re.IGNORECASE,
)
US_STATEMENT_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"Statement Date:\s*(?P<date>\d{2}/\d{2}/\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Closing Date\s*(?P<date>\d{2}/\d{2}/\d{2,4})",
        re.IGNORECASE,
    ),
)


def _normalize_text(text: str) -> str:
    return (
        text.replace("\ufeff", "")
        .replace("\xa0", " ")
        .replace("\u2009", " ")
        .replace("\u202f", " ")
    )


def _parse_us_date(date_text: str) -> date:
    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_text, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported US date format: {date_text}")


def _extract_statement_period(text: str) -> tuple[date, date] | None:
    match = US_STATEMENT_PERIOD_PATTERN.search(text)
    if not match:
        return None
    return (
        _parse_us_date(match.group("start")),
        _parse_us_date(match.group("end")),
    )


def _extract_statement_date(text: str) -> date | None:
    for pattern in US_STATEMENT_DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return _parse_us_date(match.group("date"))
    return None


def _infer_us_short_date(
    transaction_date_text: str,
    statement_period: tuple[date, date] | None,
    statement_date: date | None,
) -> str:
    if transaction_date_text.count("/") == 2:
        return _parse_us_date(transaction_date_text).isoformat()

    month_text, day_text = transaction_date_text.split("/")
    month = int(month_text)
    day = int(day_text)

    if statement_period:
        period_start, period_end = statement_period
        for candidate_year in (period_start.year, period_end.year):
            try:
                candidate = date(candidate_year, month, day)
            except ValueError:
                continue
            if period_start <= candidate <= period_end:
                return candidate.isoformat()

        fallback_year = (
            period_start.year
            if period_start.year != period_end.year and month >= period_start.month
            else period_end.year
        )
        return date(fallback_year, month, day).isoformat()

    if statement_date:
        inferred_year = (
            statement_date.year - 1
            if month > statement_date.month
            else statement_date.year
        )
        return date(inferred_year, month, day).isoformat()

    raise ValueError(f"Unable to infer year for US date: {transaction_date_text}")


def _parse_amount(amount_text: str) -> float:
    normalized = amount_text.replace("$", "").replace(",", "").strip()
    if normalized.startswith("(") and normalized.endswith(")"):
        return -float(normalized[1:-1])
    return float(normalized)


def _roc_to_iso(date_text: str) -> str:
    year_text, month_text, day_text = date_text.split("/")
    return date(int(year_text) + 1911, int(month_text), int(day_text)).isoformat()


def _extract_last4(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group("last4")
    return None


def _normalize_line(line: str) -> str:
    return " ".join(line.split())


@dataclass(slots=True)
class BaseStatementParser:
    name: str
    bank: str
    currency: str

    def matches(self, file_name: str, statement_text: str) -> bool:
        raise NotImplementedError

    def parse(
        self,
        source_path: Path,
        statement_text: str,
        category_engine: CategoryEngine,
    ) -> list[NormalizedTransaction]:
        raise NotImplementedError

    def _build_transaction(
        self,
        source_path: Path,
        card_last4: str | None,
        transaction_date: str,
        posted_date: str | None,
        merchant_raw: str,
        raw_amount: float,
        category_engine: CategoryEngine,
    ) -> NormalizedTransaction:
        normalized_merchant = _normalize_line(merchant_raw)
        category_match = category_engine.categorize(normalized_merchant, raw_amount)
        absolute_amount = abs(raw_amount)
        normalized_amount = (
            -absolute_amount
            if category_match.transaction_type == "payment_or_credit"
            else absolute_amount
        )
        return NormalizedTransaction(
            statement_file=source_path.name,
            bank=self.bank,
            card_last4=card_last4,
            transaction_date=transaction_date,
            posted_date=posted_date,
            merchant_raw=normalized_merchant,
            amount=normalized_amount,
            currency=self.currency,
            transaction_type=category_match.transaction_type,
            category=category_match.category,
            exclude_from_spending=category_match.exclude_from_spending,
            needs_review=category_match.needs_review,
        )


class AmexStatementParser(BaseStatementParser):
    _line_pattern = re.compile(
        r"^(?P<transaction_date>\d{2}/\d{2}(?:/\d{2,4})?)\s+"
        r"(?:(?P<posted_date>\d{2}/\d{2}(?:/\d{2,4})?)\s+)?"
        r"(?P<merchant>.+?)\s+"
        r"(?P<amount>-?\$?\d[\d,]*(?:\.\d{2})?)$"
    )
    _last4_patterns = (
        re.compile(r"Card ending\s+(?P<last4>\d{4})", re.IGNORECASE),
        re.compile(r"Account ending\s+(?P<last4>\d{4})", re.IGNORECASE),
    )
    _skip_tokens = (
        "AMERICAN EXPRESS",
        "DATE DESCRIPTION AMOUNT",
        "TOTAL",
        "NEW BALANCE",
        "PREVIOUS BALANCE",
        "ACCOUNT SUMMARY",
    )

    def matches(self, file_name: str, statement_text: str) -> bool:
        stem = Path(file_name).stem
        looks_like_generic_date_name = bool(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem)
        )
        text_upper = statement_text.upper()
        return looks_like_generic_date_name and (
            "AMERICAN EXPRESS" in text_upper or "AMEX" in text_upper
        )

    def parse(
        self,
        source_path: Path,
        statement_text: str,
        category_engine: CategoryEngine,
    ) -> list[NormalizedTransaction]:
        normalized_text = _normalize_text(statement_text)
        statement_date = _extract_statement_date(normalized_text)
        card_last4 = _extract_last4(normalized_text, self._last4_patterns)
        transactions: list[NormalizedTransaction] = []

        for raw_line in normalized_text.splitlines():
            line = _normalize_line(raw_line)
            if not line or any(token in line.upper() for token in self._skip_tokens):
                continue

            match = self._line_pattern.match(line)
            if not match or statement_date is None:
                continue

            transaction_date = _infer_us_short_date(
                match.group("transaction_date"),
                None,
                statement_date,
            )
            posted_text = match.group("posted_date")
            posted_date = (
                _infer_us_short_date(posted_text, None, statement_date)
                if posted_text
                else None
            )
            transactions.append(
                self._build_transaction(
                    source_path=source_path,
                    card_last4=card_last4,
                    transaction_date=transaction_date,
                    posted_date=posted_date,
                    merchant_raw=match.group("merchant"),
                    raw_amount=_parse_amount(match.group("amount")),
                    category_engine=category_engine,
                )
            )

        return transactions


class ChaseStatementParser(BaseStatementParser):
    _line_pattern = re.compile(
        r"^(?P<transaction_date>\d{2}/\d{2})\s+"
        r"(?P<posted_date>\d{2}/\d{2})\s+"
        r"(?P<merchant>.+?)\s+"
        r"(?P<amount>-?\$?\d[\d,]*(?:\.\d{2})?)$"
    )
    _last4_patterns = (
        re.compile(r"ending in\s+(?P<last4>\d{4})", re.IGNORECASE),
        re.compile(r"statements-(?P<last4>\d{4})-", re.IGNORECASE),
    )
    _skip_tokens = (
        "JPMORGAN CHASE",
        "TRANS DATE POST DATE DESCRIPTION AMOUNT",
        "OPENING/CLOSING DATE",
        "NEW BALANCE",
        "TOTAL",
        "PAYMENT DUE",
    )

    def matches(self, file_name: str, statement_text: str) -> bool:
        return bool(re.search(r"statements-\d{4}-", file_name, re.IGNORECASE)) or (
            "JPMORGAN CHASE" in statement_text.upper()
        )

    def parse(
        self,
        source_path: Path,
        statement_text: str,
        category_engine: CategoryEngine,
    ) -> list[NormalizedTransaction]:
        normalized_text = _normalize_text(statement_text)
        statement_period = _extract_statement_period(normalized_text)
        card_last4 = _extract_last4(
            f"{source_path.name}\n{normalized_text}", self._last4_patterns
        )
        transactions: list[NormalizedTransaction] = []

        for raw_line in normalized_text.splitlines():
            line = _normalize_line(raw_line)
            if not line or any(token in line.upper() for token in self._skip_tokens):
                continue

            match = self._line_pattern.match(line)
            if not match:
                continue

            transactions.append(
                self._build_transaction(
                    source_path=source_path,
                    card_last4=card_last4,
                    transaction_date=_infer_us_short_date(
                        match.group("transaction_date"),
                        statement_period,
                        None,
                    ),
                    posted_date=_infer_us_short_date(
                        match.group("posted_date"),
                        statement_period,
                        None,
                    ),
                    merchant_raw=match.group("merchant"),
                    raw_amount=_parse_amount(match.group("amount")),
                    category_engine=category_engine,
                )
            )

        return transactions


class CtbcDailyStatementParser(BaseStatementParser):
    _line_pattern = re.compile(
        r"^(?P<transaction_date>\d{2,3}/\d{1,2}/\d{1,2})\s+"
        r"(?P<posted_date>\d{2,3}/\d{1,2}/\d{1,2})\s+"
        r"(?P<merchant>.+?)\s+"
        r"(?P<amount>-?[\d,]+(?:\.\d{2})?)"
        r"(?:\s+\d{4})?$"
    )
    _last4_patterns = (
        re.compile(r"末四碼\s*(?P<last4>\d{4})"),
        re.compile(r"卡號末四碼\s*(?P<last4>\d{4})"),
    )
    _skip_tokens = (
        "DAILYSTMT",
        "中國信託",
        "消費日 入帳日",
        "總計",
        "合計",
    )

    def matches(self, file_name: str, statement_text: str) -> bool:
        upper_text = statement_text.upper()
        return file_name.upper().startswith("CBGCC-DAILYSTMT_") or (
            "DAILYSTMT" in upper_text and "CTBC" in upper_text
        )

    def parse(
        self,
        source_path: Path,
        statement_text: str,
        category_engine: CategoryEngine,
    ) -> list[NormalizedTransaction]:
        normalized_text = _normalize_text(statement_text)
        card_last4 = _extract_last4(normalized_text, self._last4_patterns)
        transactions: list[NormalizedTransaction] = []

        for raw_line in normalized_text.splitlines():
            line = _normalize_line(raw_line)
            if not line or any(token in line.upper() for token in self._skip_tokens):
                continue

            match = self._line_pattern.match(line)
            if not match:
                continue

            transactions.append(
                self._build_transaction(
                    source_path=source_path,
                    card_last4=card_last4,
                    transaction_date=_roc_to_iso(match.group("transaction_date")),
                    posted_date=_roc_to_iso(match.group("posted_date")),
                    merchant_raw=match.group("merchant"),
                    raw_amount=_parse_amount(match.group("amount")),
                    category_engine=category_engine,
                )
            )

        return transactions


class CtbcCardStatementParser(CtbcDailyStatementParser):
    _skip_tokens = (
        "CTBC_CARD_ESTATEMENT",
        "中國信託信用卡",
        "消費日 入帳日",
        "本期應繳總額",
        "總計",
        "合計",
    )

    def matches(self, file_name: str, statement_text: str) -> bool:
        return file_name.upper().startswith("CTBC_CARD_ESTATEMENT_") or (
            "中國信託信用卡" in statement_text and "帳單" in statement_text
        )


class DbsStatementParser(BaseStatementParser):
    _line_pattern = re.compile(
        r"^(?P<transaction_date>\d{4}/\d{2}/\d{2})\s+"
        r"(?P<posted_date>\d{4}/\d{2}/\d{2})\s+"
        r"(?P<merchant>.+?)\s+"
        r"(?P<amount>-?[\d,]+(?:\.\d{2})?)$"
    )
    _last4_patterns = (
        re.compile(r"Ending\s+(?P<last4>\d{4})", re.IGNORECASE),
        re.compile(r"末四碼\s*(?P<last4>\d{4})"),
    )
    _skip_tokens = (
        "DBS-ESTATEMENT",
        "CARD NUMBER",
        "消費日 入帳日",
        "信用額度",
        "總計",
        "合計",
    )

    def matches(self, file_name: str, statement_text: str) -> bool:
        return file_name.upper().startswith("DBS-ESTATEMENT_") or (
            "DBS" in statement_text.upper()
        )

    def parse(
        self,
        source_path: Path,
        statement_text: str,
        category_engine: CategoryEngine,
    ) -> list[NormalizedTransaction]:
        normalized_text = _normalize_text(statement_text)
        card_last4 = _extract_last4(normalized_text, self._last4_patterns)
        transactions: list[NormalizedTransaction] = []

        for raw_line in normalized_text.splitlines():
            line = _normalize_line(raw_line)
            if not line or any(token in line.upper() for token in self._skip_tokens):
                continue
            if "20,000/20,000" in line or "7.99%" in line:
                continue

            match = self._line_pattern.match(line)
            if not match:
                continue

            transactions.append(
                self._build_transaction(
                    source_path=source_path,
                    card_last4=card_last4,
                    transaction_date=datetime.strptime(
                        match.group("transaction_date"), "%Y/%m/%d"
                    ).date().isoformat(),
                    posted_date=datetime.strptime(
                        match.group("posted_date"), "%Y/%m/%d"
                    ).date().isoformat(),
                    merchant_raw=match.group("merchant"),
                    raw_amount=_parse_amount(match.group("amount")),
                    category_engine=category_engine,
                )
            )

        return transactions


_PARSERS: tuple[BaseStatementParser, ...] = (
    CtbcDailyStatementParser(
        name="ctbc_daily",
        bank="CTBC",
        currency="TWD",
    ),
    CtbcCardStatementParser(
        name="ctbc_card",
        bank="CTBC",
        currency="TWD",
    ),
    DbsStatementParser(
        name="dbs",
        bank="DBS",
        currency="TWD",
    ),
    ChaseStatementParser(
        name="chase",
        bank="Chase",
        currency="USD",
    ),
    AmexStatementParser(
        name="amex",
        bank="AMEX",
        currency="USD",
    ),
)


def resolve_parser(
    file_name: str, statement_text: str
) -> BaseStatementParser | None:
    normalized_text = _normalize_text(statement_text)
    for parser in _PARSERS:
        if parser.matches(file_name, normalized_text):
            return parser
    return None
