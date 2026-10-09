import unittest

from backend.batch_import.category import CategoryEngine
from backend.parser import categorize_merchant
from backend.transaction_exclusions import (
    is_card_payment_merchant,
    is_credit_card_bill_payment,
    should_exclude_from_spending,
)


class TransactionExclusionTests(unittest.TestCase):
    def test_detects_taiwan_bill_payment_merchants(self) -> None:
        self.assertTrue(is_credit_card_bill_payment("ATM/網路繳款"))
        self.assertTrue(
            is_credit_card_bill_payment("ATM/網路繳款/本行帳戶繳款")
        )
        self.assertFalse(is_credit_card_bill_payment("STARBUCKS STORE 1234"))

    def test_should_exclude_bill_payment_or_payment_category(self) -> None:
        self.assertTrue(
            should_exclude_from_spending(
                "ATM/網路繳款",
                category="Cash Advance / ATM",
            )
        )
        self.assertTrue(
            should_exclude_from_spending(
                "THANK YOU PAYMENT",
                category="Payment / Credit",
            )
        )
        self.assertFalse(
            should_exclude_from_spending(
                "STARBUCKS",
                category="Dining",
            )
        )

    def test_upload_parser_categorizes_bill_payments_as_payment(self) -> None:
        self.assertEqual(
            categorize_merchant("ATM/網路繳款/本行帳戶繳款", 37628.16),
            "Payment / Credit",
        )

    def test_us_payment_phrases_exclude_from_spending(self) -> None:
        for merchant in (
            "Payment Thank You-Mobile",
            "AUTOMATIC PAYMENT - THANK YOU",
            "PAYMENT RECEIVED",
            "ONLINE PAYMENT 1234",
        ):
            self.assertTrue(is_card_payment_merchant(merchant), merchant)
            self.assertTrue(
                should_exclude_from_spending(merchant, category="Dining"),
                merchant,
            )

    def test_payment_substrings_are_not_payments(self) -> None:
        for merchant in ("APPLE PAYMENT SERVICE", "PAYMENTUS"):
            self.assertFalse(is_card_payment_merchant(merchant), merchant)
            self.assertFalse(
                should_exclude_from_spending(merchant, category="Other Spending"),
                merchant,
            )

    def test_batch_and_upload_agree_on_payment_phrases(self) -> None:
        engine = CategoryEngine()
        for merchant in (
            "THANK YOU PAYMENT",
            "Payment Thank You-Mobile",
            "ATM/網路繳款",
        ):
            batch = engine.categorize(merchant, 100.0)
            self.assertEqual(batch.category, "Payment / Credit", merchant)
            self.assertEqual(
                categorize_merchant(merchant, 100.0),
                "Payment / Credit",
                merchant,
            )

        for merchant in ("APPLE PAYMENT SERVICE", "PAYMENTUS", "MYSTERY REFUND"):
            batch = engine.categorize(merchant, -25.0)
            self.assertNotEqual(batch.category, "Payment / Credit", merchant)
            self.assertNotEqual(
                categorize_merchant(merchant, -25.0),
                "Payment / Credit",
                merchant,
            )


if __name__ == "__main__":
    unittest.main()
