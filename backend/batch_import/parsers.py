from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.statement_parse_utils import (
    extract_us_statement_date,
    extract_us_statement_period,
    infer_esun_billing_period,
    infer_us_transaction_date,
    normalize_statement_text,
    parse_amount,
    ctbc_line_dates_to_iso,
    parse_ctbc_gregorian_statement_line,
    parse_ctbc_roc_statement_line,
    parse_esun_statement_line,
)
from backend.transaction_source import card_last4_from_statement

from .amounts import signed_amount
from .category import CategoryEngine
from .models import NormalizedTransaction


def _infer_us_short_date(
    transaction_date_text: str,
    statement_period: tuple[date, date] | None,
    statement_date: date | None,
) -> str:
    return infer_us_transaction_date(
        transaction_date_text,
        statement_period,
        statement_date,
        strict=True,
    )


def _card_last4(text: str, *, file_name: str = "") -> str | None:
    return card_last4_from_statement(text, file_name=file_name) or None


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
        return NormalizedTransaction(
            statement_file=source_path.name,
            bank=self.bank,
            card_last4=card_last4,
            transaction_date=transaction_date,
            posted_date=posted_date,
            merchant_raw=normalized_merchant,
            amount=signed_amount(
                raw_amount,
                category_match.transaction_type,
            ),
            currency=self.currency,
            transaction_type=category_match.transaction_type,
            category=category_match.category,
            exclude_from_spending=category_match.exclude_from_spending,
            needs_review=category_match.needs_review,
            statement_amount=raw_amount,
        )


class AmexStatementParser(BaseStatementParser):
    _line_pattern = re.compile(
        r"^(?P<transaction_date>\d{2}/\d{2}(?:/\d{2,4})?)\s+"
        r"(?:(?P<posted_date>\d{2}/\d{2}(?:/\d{2,4})?)\s+)?"
        r"(?P<merchant>.+?)\s+"
        r"(?P<amount>-?\$?\d[\d,]*(?:\.\d{2})?)$"
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
        normalized_text = normalize_statement_text(statement_text)
        statement_date = extract_us_statement_date(normalized_text)
        card_last4 = _card_last4(normalized_text, file_name=source_path.name)
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
                    raw_amount=parse_amount(match.group("amount")),
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
        normalized_text = normalize_statement_text(statement_text)
        statement_period = extract_us_statement_period(normalized_text)
        card_last4 = _card_last4(normalized_text, file_name=source_path.name)
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
                    raw_amount=parse_amount(match.group("amount")),
                    category_engine=category_engine,
                )
            )

        return transactions


class CtbcDailyStatementParser(BaseStatementParser):
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
        normalized_text = normalize_statement_text(statement_text)
        card_last4 = _card_last4(normalized_text, file_name=source_path.name)
        transactions: list[NormalizedTransaction] = []

        for raw_line in normalized_text.splitlines():
            line = _normalize_line(raw_line)
            if not line or any(token in line.upper() for token in self._skip_tokens):
                continue

            parsed_line = parse_ctbc_roc_statement_line(line)
            if parsed_line is None:
                parsed_line = parse_ctbc_gregorian_statement_line(line)
            if parsed_line is None:
                continue

            transaction_date, posted_date = ctbc_line_dates_to_iso(parsed_line)
            transactions.append(
                self._build_transaction(
                    source_path=source_path,
                    card_last4=card_last4,
                    transaction_date=transaction_date,
                    posted_date=posted_date,
                    merchant_raw=parsed_line.merchant,
                    raw_amount=parsed_line.amount,
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
        normalized_text = normalize_statement_text(statement_text)
        card_last4 = _card_last4(normalized_text, file_name=source_path.name)
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
                    raw_amount=parse_amount(match.group("amount")),
                    category_engine=category_engine,
                )
            )

        return transactions


class EsunStatementParser(BaseStatementParser):
    _skip_tokens = (
        "ESUN_ESTATEMENT",
        "玉山銀行",
        "消費日 入帳日",
        "本期合計",
        "上期應繳",
        "續下頁",
        "卡號",
        "信用額度",
        "總計",
        "合計",
    )

    def matches(self, file_name: str, statement_text: str) -> bool:
        upper_name = file_name.upper()
        if upper_name.startswith("ESUN_ESTATEMENT_"):
            return True
        return "玉山" in statement_text and "TWD" in statement_text

    def parse(
        self,
        source_path: Path,
        statement_text: str,
        category_engine: CategoryEngine,
    ) -> list[NormalizedTransaction]:
        normalized_text = normalize_statement_text(statement_text)
        billing_period = infer_esun_billing_period(
            normalized_text,
            source_path.name,
        )
        if billing_period is None:
            return []
        billing_year, billing_month = billing_period

        card_last4 = _card_last4(normalized_text, file_name=source_path.name)
        transactions: list[NormalizedTransaction] = []

        for raw_line in normalized_text.splitlines():
            line = _normalize_line(raw_line)
            if not line or any(token in line for token in self._skip_tokens):
                continue

            parsed_line = parse_esun_statement_line(
                line,
                billing_year,
                billing_month,
            )
            if parsed_line is None:
                continue

            transactions.append(
                self._build_transaction(
                    source_path=source_path,
                    card_last4=card_last4,
                    transaction_date=parsed_line.transaction_date,
                    posted_date=parsed_line.posted_date,
                    merchant_raw=parsed_line.merchant,
                    raw_amount=parsed_line.amount,
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
    EsunStatementParser(
        name="esun",
        bank="ESUN",
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
    normalized_text = normalize_statement_text(statement_text)
    for parser in _PARSERS:
        if parser.matches(file_name, normalized_text):
            return parser
    return None
