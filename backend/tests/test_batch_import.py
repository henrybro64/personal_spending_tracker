import os
import tempfile
import unittest
from pathlib import Path

from backend.batch_import import (
    BatchImportStore,
    CategoryEngine,
    CategoryRule,
    StatementImporter,
    resolve_parser,
)
from backend.batch_import.parsers import ChaseStatementParser, CtbcDailyStatementParser
from backend.batch_import.upload_fallback import (
    upload_fallback_supported,
    transactions_from_upload_parser,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "batch_import"


class CategoryEngineTests(unittest.TestCase):
    def test_category_engine_is_deterministic_and_honors_priority(self) -> None:
        engine = CategoryEngine(
            [
                CategoryRule(
                    patterns=("AMAZON UDEMY",),
                    category="Education / Certification",
                    exclude_from_spending=False,
                    transaction_type="purchase",
                    priority=10,
                )
            ]
        )

        learned_match = engine.categorize("Amazon Udemy", 19.99)
        self.assertEqual("Education / Certification", learned_match.category)
        self.assertFalse(learned_match.exclude_from_spending)
        self.assertFalse(learned_match.needs_review)

        payment_match = engine.categorize("THANK YOU PAYMENT", 100.00)
        self.assertEqual("Payment / Credit", payment_match.category)
        self.assertTrue(payment_match.exclude_from_spending)
        self.assertEqual("payment_or_credit", payment_match.transaction_type)

        bill_payment_match = engine.categorize("ATM/網路繳款/本行帳戶繳款", 37628.16)
        self.assertEqual("Payment / Credit", bill_payment_match.category)
        self.assertTrue(bill_payment_match.exclude_from_spending)

        unknown_match = engine.categorize("MYSTERY MERCHANT", 12.34)
        self.assertEqual("Other Spending", unknown_match.category)
        self.assertTrue(unknown_match.needs_review)


class ParserFixtureTests(unittest.TestCase):
    def test_supported_bank_fixtures_parse_expected_transactions(self) -> None:
        cases = [
            (
                "amex_statement.txt",
                "2026-01-29.pdf",
                "AMEX",
                "1001",
                2,
                ["Dining / Food", "Payment / Credit"],
            ),
            (
                "chase_statement.txt",
                "20240123-statements-6814-.pdf",
                "Chase",
                "6814",
                2,
                ["Shopping / Online Retail", "Payment / Credit"],
            ),
            (
                "ctbc_daily_statement.txt",
                "CBGCC-DAILYSTMT_20240215.txt",
                "CTBC",
                "4321",
                3,
                ["Dining / Food", "Transportation", "Other Spending"],
            ),
            (
                "ctbc_card_statement.txt",
                "CTBC_card_Estatement_202403.txt",
                "CTBC",
                "8765",
                2,
                ["Groceries", "Payment / Credit"],
            ),
            (
                "dbs_statement.txt",
                "DBS-Estatement_202403.txt",
                "DBS",
                "5566",
                2,
                ["Transportation", "Bank Fees / Interest"],
            ),
        ]

        for fixture_name, statement_name, bank, last4, count, categories in cases:
            with self.subTest(statement_name=statement_name):
                statement_text = (FIXTURE_DIR / fixture_name).read_text(
                    encoding="utf-8"
                )
                parser = resolve_parser(statement_name, statement_text)
                self.assertIsNotNone(parser)
                transactions = parser.parse(
                    Path(statement_name),
                    statement_text,
                    CategoryEngine(),
                )

                self.assertEqual(count, len(transactions))
                self.assertTrue(all(item.statement_file == statement_name for item in transactions))
                self.assertTrue(all(item.bank == bank for item in transactions))
                self.assertTrue(all(item.card_last4 == last4 for item in transactions))
                self.assertEqual(categories, [item.category for item in transactions])


class StatementImporterTests(unittest.TestCase):
    @staticmethod
    def _batch_sandbox(temp_dir: Path) -> tuple[Path, Path, Path]:
        pdf_dir = temp_dir / "Credit_Card_PDF"
        text_dir = temp_dir / "credit_card_text"
        output_dir = temp_dir / "output"
        pdf_dir.mkdir()
        text_dir.mkdir()
        output_dir.mkdir()
        return pdf_dir, text_dir, output_dir

    @staticmethod
    def _apply_batch_env(overrides: dict[str, str]) -> dict[str, str | None]:
        previous_values = {key: os.environ.get(key) for key in overrides}
        os.environ.update(overrides)
        return previous_values

    @staticmethod
    def _restore_batch_env(previous_values: dict[str, str | None]) -> None:
        for key, value in previous_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_importer_walks_directories_exports_and_learns_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_dir, text_dir, output_dir = self._batch_sandbox(temp_dir)

            fixture_map = {
                pdf_dir / "2026-01-29.pdf": FIXTURE_DIR / "amex_statement.txt",
                pdf_dir / "20240123-statements-6814-.pdf": FIXTURE_DIR / "chase_statement.txt",
                text_dir / "CBGCC-DAILYSTMT_20240215.txt": FIXTURE_DIR / "ctbc_daily_statement.txt",
                text_dir / "CTBC_card_Estatement_202403.txt": FIXTURE_DIR / "ctbc_card_statement.txt",
                text_dir / "DBS-Estatement_202403.txt": FIXTURE_DIR / "dbs_statement.txt",
            }
            for target_path, fixture_path in fixture_map.items():
                target_path.write_text(
                    fixture_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            csv_path = output_dir / "batch_transactions.csv"
            sqlite_path = output_dir / "batch_imports.sqlite3"
            store = BatchImportStore(sqlite_path)
            env_overrides = {
                "BATCH_DATA_DIR": str(output_dir),
                "BATCH_PDF_SOURCE_DIR": str(pdf_dir),
                "BATCH_TEXT_SOURCE_DIR": str(text_dir),
            }
            previous_values = self._apply_batch_env(env_overrides)
            try:
                importer = StatementImporter(
                    pdf_source_dir=pdf_dir,
                    text_source_dir=text_dir,
                    csv_output_path=csv_path,
                    store=store,
                    pdf_text_extractor=lambda path: Path(path).read_text(
                        encoding="utf-8"
                    ),
                )
                result = importer.import_transactions()
            finally:
                self._restore_batch_env(previous_values)

            self.assertEqual(5, result.statement_count)
            self.assertEqual(10, result.transaction_count)
            self.assertEqual(1, result.needs_review_count)
            self.assertTrue(csv_path.exists())
            self.assertTrue(sqlite_path.exists())
            self.assertEqual(str(sqlite_path), result.sqlite_output_path)

            latest_run = store.get_latest_run()
            self.assertIsNotNone(latest_run)
            assert latest_run is not None
            self.assertEqual(10, latest_run["transaction_count"])

            review_queue = store.list_transactions(needs_review_only=True)
            self.assertEqual(1, len(review_queue))
            self.assertEqual("神祕商店", review_queue[0]["merchant_raw"])

            updated = store.update_transaction_review(
                review_queue[0]["id"],
                category="Shopping / Online Retail",
                exclude_from_spending=False,
                save_rule=True,
            )
            self.assertFalse(updated["needs_review"])
            self.assertEqual("Shopping / Online Retail", updated["category"])

            learned_engine = CategoryEngine(store.load_learned_rules())
            learned_match = learned_engine.categorize("神祕商店", 88.0)
            self.assertEqual("Shopping / Online Retail", learned_match.category)

            csv_lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(11, len(csv_lines))

    def test_importer_skips_password_protected_pdfs(self) -> None:
        from backend.pdf_utils import PdfPasswordProtectedError

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_dir, text_dir, output_dir = self._batch_sandbox(temp_dir)
            (pdf_dir / "locked.pdf").write_bytes(b"%PDF-1.4\n")

            store = BatchImportStore(output_dir / "batch.sqlite3")
            env_overrides = {
                "BATCH_DATA_DIR": str(output_dir),
                "BATCH_PDF_SOURCE_DIR": str(pdf_dir),
                "BATCH_TEXT_SOURCE_DIR": str(text_dir),
            }

            def fake_extractor(path: str | Path) -> str:
                if Path(path).name == "locked.pdf":
                    raise PdfPasswordProtectedError(had_password=False)
                return Path(path).read_text(encoding="utf-8")

            previous_values = self._apply_batch_env(env_overrides)
            try:
                importer = StatementImporter(
                    pdf_source_dir=pdf_dir,
                    text_source_dir=text_dir,
                    csv_output_path=output_dir / "batch.csv",
                    store=store,
                    pdf_text_extractor=fake_extractor,
                )
                result = importer.import_transactions()
            finally:
                self._restore_batch_env(previous_values)

            self.assertEqual(0, result.statement_count)
            self.assertEqual(1, len(result.skipped_files))
            self.assertEqual("locked.pdf", result.skipped_files[0]["statement_file"])
            self.assertEqual("password_required", result.skipped_files[0]["reason"])

    def test_importer_skips_parser_processing_errors(self) -> None:
        from unittest.mock import patch

        class BrokenParser:
            name = "broken"

            def parse(self, source_path, statement_text, category_engine):
                raise ValueError("Unsupported US date format: 99/99")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_dir, text_dir, output_dir = self._batch_sandbox(temp_dir)
            (pdf_dir / "20240123-statements-6814-.pdf").write_text(
                "synthetic",
                encoding="utf-8",
            )

            store = BatchImportStore(output_dir / "batch.sqlite3")
            env_overrides = {
                "BATCH_DATA_DIR": str(output_dir),
                "BATCH_PDF_SOURCE_DIR": str(pdf_dir),
                "BATCH_TEXT_SOURCE_DIR": str(text_dir),
            }
            previous_values = self._apply_batch_env(env_overrides)
            try:
                importer = StatementImporter(
                    pdf_source_dir=pdf_dir,
                    text_source_dir=text_dir,
                    csv_output_path=output_dir / "batch.csv",
                    store=store,
                    pdf_text_extractor=lambda path: "synthetic",
                )

                with patch(
                    "backend.batch_import.importer.resolve_parser",
                    return_value=BrokenParser(),
                ):
                    result = importer.import_transactions()
            finally:
                self._restore_batch_env(previous_values)

            self.assertEqual(0, result.statement_count)
            self.assertEqual(1, len(result.skipped_files))
            self.assertEqual("processing_error", result.skipped_files[0]["reason"])

    def test_importer_falls_back_to_upload_parser_when_batch_lines_empty(self) -> None:
        from unittest.mock import patch

        ctbc_text = (FIXTURE_DIR.parent / "ctbc_sample.txt").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_dir, text_dir, output_dir = self._batch_sandbox(temp_dir)
            (pdf_dir / "CBGCC-DAILYSTMT_20240215.pdf").write_text(
                ctbc_text,
                encoding="utf-8",
            )

            store = BatchImportStore(output_dir / "batch.sqlite3")
            env_overrides = {
                "BATCH_DATA_DIR": str(output_dir),
                "BATCH_PDF_SOURCE_DIR": str(pdf_dir),
                "BATCH_TEXT_SOURCE_DIR": str(text_dir),
            }
            previous_values = self._apply_batch_env(env_overrides)
            try:
                importer = StatementImporter(
                    pdf_source_dir=pdf_dir,
                    text_source_dir=text_dir,
                    csv_output_path=output_dir / "batch.csv",
                    store=store,
                    pdf_text_extractor=lambda path: Path(path).read_text(
                        encoding="utf-8"
                    ),
                )
                with patch.object(CtbcDailyStatementParser, "parse", return_value=[]):
                    result = importer.import_transactions()
            finally:
                self._restore_batch_env(previous_values)

            self.assertEqual(1, result.statement_count)
            self.assertEqual(2, result.transaction_count)
            self.assertEqual(
                "CBGCC-DAILYSTMT_20240215.pdf", result.parsed_files[0]
            )
            merchants = {
                row["merchant_raw"] for row in store.list_transactions(limit=10)
            }
            self.assertIn("FOODPANDA", merchants)

    def test_upload_fallback_supported_only_for_taiwan_pdf_parsers(self) -> None:
        self.assertTrue(upload_fallback_supported("ctbc_daily"))
        self.assertTrue(upload_fallback_supported("ctbc_card"))
        self.assertTrue(upload_fallback_supported("dbs"))
        self.assertFalse(upload_fallback_supported("chase"))
        self.assertFalse(upload_fallback_supported("amex"))

    def test_importer_does_not_fallback_for_chase_when_batch_lines_empty(self) -> None:
        from unittest.mock import patch

        chase_text = (FIXTURE_DIR / "chase_statement.txt").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_dir, text_dir, output_dir = self._batch_sandbox(temp_dir)
            (pdf_dir / "20240123-statements-6814-.pdf").write_text(
                chase_text,
                encoding="utf-8",
            )

            store = BatchImportStore(output_dir / "batch.sqlite3")
            env_overrides = {
                "BATCH_DATA_DIR": str(output_dir),
                "BATCH_PDF_SOURCE_DIR": str(pdf_dir),
                "BATCH_TEXT_SOURCE_DIR": str(text_dir),
            }
            previous_values = self._apply_batch_env(env_overrides)
            try:
                importer = StatementImporter(
                    pdf_source_dir=pdf_dir,
                    text_source_dir=text_dir,
                    csv_output_path=output_dir / "batch.csv",
                    store=store,
                    pdf_text_extractor=lambda path: Path(path).read_text(
                        encoding="utf-8"
                    ),
                )
                with patch.object(ChaseStatementParser, "parse", return_value=[]):
                    result = importer.import_transactions()
            finally:
                self._restore_batch_env(previous_values)

            self.assertEqual(0, result.statement_count)
            self.assertEqual(1, len(result.skipped_files))
            self.assertEqual("no_transactions_found", result.skipped_files[0]["reason"])

    def test_upload_fallback_skips_rows_without_iso_dates(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            path = temp_dir / "CBGCC-DAILYSTMT_test.pdf"
            path.write_text("placeholder", encoding="utf-8")

            with patch(
                "backend.batch_import.upload_fallback.parse_transactions",
                return_value=[
                    {
                        "date": "01/15",
                        "merchant": "BAD DATE",
                        "amount": 10.0,
                        "category": "Other",
                        "currency": "TWD",
                    },
                    {
                        "date": "2022-01-03",
                        "merchant": "GOOD",
                        "amount": 20.0,
                        "category": "Other",
                        "currency": "TWD",
                    },
                ],
            ):
                rows = transactions_from_upload_parser(
                    path,
                    "text",
                    CategoryEngine(),
                    bank="CTBC",
                )

            self.assertEqual(1, len(rows))
            self.assertEqual("GOOD", rows[0].merchant_raw)


if __name__ == "__main__":
    unittest.main()
