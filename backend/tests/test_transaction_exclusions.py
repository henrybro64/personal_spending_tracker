import unittest

from backend.parser import categorize_merchant
from backend.transaction_exclusions import (
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

if __name__ == "__main__":
    unittest.main()
