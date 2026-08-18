-- Multi-account support: entities can have multiple bank accounts.
--
-- A business often has a current account and a savings account. Transfers
-- between them are balance sheet movements (excluded from P&L), but interest
-- earned on the savings account is taxable income.
--
-- This migration:
--   1. Creates an `accounts` table linking accounts to entities
--   2. Adds `account_id` to imports (which account the statement came from)
--   3. Adds `transfer_pair_id` to transactions to link both sides of a transfer
--   4. Adds `is_internal_transfer` flag to auto-exclude balance sheet movements

CREATE TABLE IF NOT EXISTS accounts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    account_ref TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'current',
    bank_name   TEXT NOT NULL DEFAULT 'unknown',
    label       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_accounts_entity ON accounts(entity_id);
CREATE INDEX IF NOT EXISTS idx_accounts_org ON accounts(org_id);

COMMENT ON TABLE accounts IS
'Bank accounts linked to entities. An entity can have multiple accounts '
'(current, savings, deposit). Used to detect internal transfers and '
'attribute interest income correctly.';

COMMENT ON COLUMN accounts.account_ref IS
'The bank account reference (last 8 digits, account number, or IBAN stub) '
'used to match transfer descriptions.';

COMMENT ON COLUMN accounts.account_type IS
'current, savings, deposit, or other. Interest from savings accounts is '
'taxable income; internal transfers between accounts are excluded from P&L.';

-- Link imports to a specific account (optional — defaults to entity-level)
ALTER TABLE imports ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id);

-- Link transactions to a specific account
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id);

-- Flag internal transfers (money moving between accounts of the same entity)
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_internal_transfer BOOLEAN DEFAULT false;

COMMENT ON COLUMN transactions.is_internal_transfer IS
'True when this transaction is a transfer between two accounts of the same '
'entity (e.g. current → savings). Internal transfers are excluded from P&L '
'exports but interest earned on the receiving account is taxable income.';

-- Backfill: mark existing excluded non_deductible_business transactions
-- that look like transfers
UPDATE transactions
SET is_internal_transfer = true
WHERE hmrc_category = 'non_deductible_business'
  AND status = 'excluded'
  AND (description ILIKE '%transfer%' OR description ILIKE '%A/C%');