DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'transactions_statement_upload_id_fkey'
    ) THEN
        ALTER TABLE transactions
        ADD CONSTRAINT transactions_statement_upload_id_fkey
        FOREIGN KEY (statement_upload_id)
        REFERENCES statement_uploads(id)
        ON DELETE SET NULL;
    END IF;
END $$;
