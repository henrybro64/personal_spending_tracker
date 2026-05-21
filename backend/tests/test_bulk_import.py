import json
import unittest

from backend.bulk_import import BulkImportParseError, parse_import_content


class BulkImportTests(unittest.TestCase):
    def test_json_wrapper_object(self) -> None:
        payload = {
            "source": "CTBC May",
            "bank": "CTBC",
            "currency": "TWD",
            "transactions": [
                {"date": "2026-05-13", "merchant": "IKEA", "amount": 813},
            ],
        }
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].bank, "CTBC")
        self.assertEqual(rows[0].currency, "TWD")
        self.assertEqual(rows[0].merchant_raw, "IKEA")
        self.assertEqual(rows[0].statement_file, "CTBC May.json")

    def test_json_array_shorthand(self) -> None:
        payload = [{"date": "2026-05-04", "merchant": "7-ELEVEN", "amount": 55}]
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(rows[0].transaction_date, "2026-05-04")
        self.assertEqual(rows[0].amount, 55.0)

    def test_text_tab_separated(self) -> None:
        content = "# comments ok\n2026-05-04\t全聯\t736.00\n"
        rows = parse_import_content(content, format="text", bank="CTBC", currency="TWD")
        self.assertEqual(rows[0].merchant_raw, "全聯")
        self.assertEqual(rows[0].amount, 736.0)

    def test_text_space_separated(self) -> None:
        content = "2026-05-13 IKEA 813.00\n"
        rows = parse_import_content(content, format="text")
        self.assertEqual(rows[0].merchant_raw, "IKEA")

    def test_rejects_invalid_date(self) -> None:
        payload = [{"date": "05/13/2026", "merchant": "IKEA", "amount": 10}]
        with self.assertRaises(BulkImportParseError):
            parse_import_content(json.dumps(payload))

    def test_explicit_category_skips_needs_review(self) -> None:
        payload = [
            {
                "date": "2026-05-13",
                "merchant": "IKEA",
                "amount": 813,
                "category": "Shopping / Online Retail",
            }
        ]
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(rows[0].category, "Shopping / Online Retail")
        self.assertFalse(rows[0].needs_review)


if __name__ == "__main__":
    unittest.main()
