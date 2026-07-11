ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS original_amount NUMERIC(12, 2);

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS original_currency TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS base_amount NUMERIC(14, 2);

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS base_currency TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(18, 8);

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS exchange_rate_date TEXT;

UPDATE transactions
SET
    original_amount = COALESCE(original_amount, amount),
    original_currency = COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD'),
    base_currency = COALESCE(NULLIF(base_currency, ''), 'TWD'),
    base_amount = CASE
        WHEN base_amount IS NOT NULL THEN base_amount
        WHEN COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
             = COALESCE(NULLIF(base_currency, ''), 'TWD')
            THEN COALESCE(original_amount, amount)
        ELSE NULL
    END,
    exchange_rate = CASE
        WHEN exchange_rate IS NOT NULL THEN exchange_rate
        WHEN COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
             = COALESCE(NULLIF(base_currency, ''), 'TWD')
            THEN 1
        ELSE NULL
    END,
    exchange_rate_date = CASE
        WHEN exchange_rate_date IS NOT NULL THEN exchange_rate_date
        WHEN COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
             = COALESCE(NULLIF(base_currency, ''), 'TWD')
            THEN date
        ELSE NULL
    END
WHERE original_amount IS NULL
   OR original_currency IS NULL
   OR base_currency IS NULL
   OR (
       base_amount IS NULL
       AND COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
           = COALESCE(NULLIF(base_currency, ''), 'TWD')
   )
   OR (
       exchange_rate IS NULL
       AND COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
           = COALESCE(NULLIF(base_currency, ''), 'TWD')
   )
   OR (
       exchange_rate_date IS NULL
       AND COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
           = COALESCE(NULLIF(base_currency, ''), 'TWD')
   );
