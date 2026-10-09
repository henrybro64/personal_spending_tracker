"""Statement sign rules shared by batch, upload fallback, and bulk import."""


def signed_amount(statement_amount: float, transaction_type: str) -> float:
    """Keep the printed sign. Card payments are stored negative.

    A refund printed as -25 stays -25. A payment printed as 100.00, such as
    Amex ``THANK YOU PAYMENT``, is stored as -100 and excluded elsewhere.
    """
    amount = float(statement_amount)
    if transaction_type == "payment_or_credit":
        return -abs(amount)
    return amount
