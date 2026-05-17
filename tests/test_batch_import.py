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

        unknown_match = engine.categorize("MYSTERY MERCHANT", 12.34)
        self.assertEqual("Unknown / Needs Review", unknown_match.category)
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
                2,
                ["Dining / Food", "Unknown / Needs Review"],
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
    def test_importer_walks_directories_exports_and_learns_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_dir = temp_dir / "Credit_Card_PDF"
            text_dir = temp_dir / "credit_card_text"
            output_dir = temp_dir / "output"
            pdf_dir.mkdir()
            text_dir.mkdir()
            output_dir.mkdir()

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
            importer = StatementImporter(
                pdf_source_dir=pdf_dir,
                text_source_dir=text_dir,
                csv_output_path=csv_path,
                store=store,
                pdf_text_extractor=lambda path: Path(path).read_text(encoding="utf-8"),
            )

            result = importer.import_transactions()

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


if __name__ == "__main__":
    unittest.main()
