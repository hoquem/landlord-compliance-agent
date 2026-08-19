-- Entity ownership types: sole_trader, partnership, limited_company, llp
--
-- This matters because the ownership type determines:
--   - Tax treatment (income tax vs corporation tax)
--   - Mortgage interest relief (Section 24 vs full deduction)
--   - CGT exposure (yes for individuals/partners, no for companies)
--   - IHT exposure (yes for individuals, shares do for companies)
--   - Filing requirements (SA105 per partner vs CT600 for company)
--   - How ownership percentages work (shares vs partnership agreement)

ALTER TYPE tax_regime ADD VALUE IF NOT EXISTS 'partnership';

ALTER TABLE entities ADD COLUMN IF NOT EXISTS ownership_type TEXT NOT NULL DEFAULT 'sole_trader';

COMMENT ON COLUMN entities.ownership_type IS
'The legal structure of the entity: sole_trader, partnership, limited_company, or llp. '
'Determines tax treatment, mortgage relief, CGT, IHT, and filing requirements.';

-- Backfill from tax_regime: corporation_tax → limited_company, mtd_itsa → sole_trader
UPDATE entities SET ownership_type = 'limited_company' WHERE tax_regime = 'corporation_tax';
UPDATE entities SET ownership_type = 'sole_trader' WHERE tax_regime = 'mtd_itsa';

-- Add partnership metadata: partners share the same property with different %
-- This is already handled by property_ownership (multiple entities per property)
-- but we need to know if a sole_trader entity is actually a partnership

ALTER TABLE entities ADD COLUMN IF NOT EXISTS company_number TEXT;
COMMENT ON COLUMN entities.company_number IS
'Companies House registration number for limited companies and LLPs.';

ALTER TABLE entities ADD COLUMN IF NOT EXISTS utr TEXT;
COMMENT ON COLUMN entities.utr IS
'Unique Tax Reference number for sole traders and partnerships (SA105 filing).';