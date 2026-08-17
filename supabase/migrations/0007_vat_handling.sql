-- VAT handling: entity registration, transaction VAT split, import flag.
--
-- Supports VAT-registered entities (Ltd companies, sole traders) by:
--   1. Marking entities as VAT-registered with a rate
--   2. Storing the VAT element per transaction (vat_amount, gross_amount)
--   3. Flagging imports as VAT-inclusive so the parser can auto-split
--
-- VAT payments/refunds to HMRC are categorised as vat_output / vat_input
-- (added to the hmrc_category enum) and excluded from quarterly exports
-- via EXCLUDED_FROM_EXPORT in categories.py.

-- ── Entity VAT registration ──────────────────────────────────────────
ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS vat_registered boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS vat_rate numeric(5,2) NOT NULL DEFAULT 20.00;

COMMENT ON COLUMN entities.vat_registered IS
    'Whether this entity is VAT-registered. When true, income imports flagged as VAT-inclusive are auto-split into net + VAT.';

COMMENT ON COLUMN entities.vat_rate IS
    'VAT rate as a percentage (20.00 = standard, 5.00 = reduced). Default 20%.';

-- ── Transaction VAT split ────────────────────────────────────────────
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS vat_amount numeric(12,2),
    ADD COLUMN IF NOT EXISTS gross_amount numeric(12,2);

COMMENT ON COLUMN transactions.vat_amount IS
    'The VAT element of a gross transaction. NULL for non-VAT transactions. For income: output VAT collected. For expenses: input VAT (potentially reclaimable).';

COMMENT ON COLUMN transactions.gross_amount IS
    'The original gross (VAT-inclusive) amount from the bank statement. NULL for non-VAT transactions. amount stores the net amount when this is populated.';

-- Constraint: if gross_amount is set, vat_amount must be too, and they must reconcile
ALTER TABLE transactions
    ADD CONSTRAINT vat_split_consistent
    CHECK (
        (gross_amount IS NULL AND vat_amount IS NULL)
        OR
        (gross_amount IS NOT NULL
         AND vat_amount IS NOT NULL
         AND gross_amount = amount + vat_amount
         AND vat_amount > 0)
    );

-- ── Import VAT flag ──────────────────────────────────────────────────
ALTER TABLE imports
    ADD COLUMN IF NOT EXISTS vat_inclusive boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN imports.vat_inclusive IS
    'When true, the parser auto-splits income amounts into net + output VAT for VAT-registered entities. Set per-import by the user at upload time.';

-- ── Mark Carlton Investment Ltd as VAT-registered ────────────────────
UPDATE entities SET vat_registered = true, vat_rate = 20.00
WHERE name = 'Carlton Investment Ltd';