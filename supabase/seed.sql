-- Seed landlord-compliance-agent database
-- Org + Entities + Properties + Ownership splits

DO $$
DECLARE
    v_org_id uuid;
    v_mahmud_entity uuid;
    v_alima_entity uuid;
BEGIN
    -- Get or create org
    SELECT id INTO v_org_id FROM orgs WHERE name = 'Hoque Property Portfolio' LIMIT 1;
    IF v_org_id IS NULL THEN
        INSERT INTO orgs (name) VALUES ('Hoque Property Portfolio') RETURNING id INTO v_org_id;
    END IF;

    -- Entities (tax filers)
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis)
    VALUES (v_org_id, 'Mahmudul Hoque (Sole Trader)', 'mtd_itsa', 'tax_year')
    ON CONFLICT DO NOTHING RETURNING id INTO v_mahmud_entity;
    IF v_mahmud_entity IS NULL THEN SELECT id INTO v_mahmud_entity FROM entities WHERE org_id = v_org_id AND name = 'Mahmudul Hoque (Sole Trader)'; END IF;

    INSERT INTO entities (org_id, name, tax_regime, quarter_basis)
    VALUES (v_org_id, 'Alima Hoque (Sole Trader)', 'mtd_itsa', 'tax_year')
    ON CONFLICT DO NOTHING RETURNING id INTO v_alima_entity;
    IF v_alima_entity IS NULL THEN SELECT id INTO v_alima_entity FROM entities WHERE org_id = v_org_id AND name = 'Alima Hoque (Sole Trader)'; END IF;

    -- Ltd companies
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Hudayfah Property Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Bangla Properties Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Carlton Investment Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Conway Union Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Cuckoos Property Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Equitable Investments Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Old Bedford Properties Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Roshina Properties Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;
    INSERT INTO entities (org_id, name, tax_regime, quarter_basis) VALUES (v_org_id, 'Saleha Properties Ltd', 'corporation_tax', 'calendar_election') ON CONFLICT DO NOTHING;

    -- Properties (excluding 22 Carlton Close - Ubaid's, 0% Mahmud/Alima)
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '36 Ludlow Avenue', 'Luton', 'LU1 3RW', 'residential', 'repayment') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '24 Lansdowne Road', 'Luton', 'LU3 1EE', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '18A Conway Road', 'Luton', 'LU4 8JA', 'residential', 'none') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '98/98A/98B Warwick Road West', 'Luton', 'LU4 8BJ', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '4/4A St Giles Terrace', 'Northampton', 'NN1 2BN', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '106 Carlton Crescent', 'Luton', 'LU3 1EW', 'residential', 'none') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '108 Carlton Crescent', 'Luton', 'LU3 1EW', 'residential', 'none') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '59 Russell Rise', 'Luton', 'LU1 5ET', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '12 Liverpool Road', 'Luton', 'LU1 3AJ', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '40 Thrales Close', 'Luton', 'LU3 3RS', 'residential', 'none') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '13 Cambridge Street', 'Luton', 'LU1 3QS', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, 'Flat 4, Cuckoos Nest', 'Luton', 'LU2 0QW', 'residential', 'interest_only') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '78A Castle Street', 'Luton', 'LU1 1RS', 'residential', 'none') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '22 Leonora Street', 'Stoke-on-Trent', 'ST6 3BS', 'residential', 'none') ON CONFLICT DO NOTHING;
    INSERT INTO properties (org_id, address_line1, city, postcode, finance_cost_classification, mortgage_type) VALUES (v_org_id, '105 Casablanca Beach', 'Hurghada', 'N/A', 'non_residential', 'none') ON CONFLICT DO NOTHING;

    -- Ownership splits
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '36 Ludlow Avenue' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '36 Ludlow Avenue' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '24 Lansdowne Road' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '24 Lansdowne Road' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 16.67 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '18A Conway Road' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '98/98A/98B Warwick Road West' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 25.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '4/4A St Giles Terrace' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '106 Carlton Crescent' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '106 Carlton Crescent' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '108 Carlton Crescent' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '108 Carlton Crescent' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '59 Russell Rise' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '12 Liverpool Road' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 100.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '40 Thrales Close' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '13 Cambridge Street' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = 'Flat 4, Cuckoos Nest' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '78A Castle Street' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_alima_entity, 100.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '22 Leonora Street' ON CONFLICT DO NOTHING;
    INSERT INTO property_ownership (org_id, property_id, entity_id, ownership_percentage) SELECT v_org_id, p.id, v_mahmud_entity, 50.00 FROM properties p WHERE p.org_id = v_org_id AND p.address_line1 = '105 Casablanca Beach' ON CONFLICT DO NOTHING;

    RAISE NOTICE 'Seed complete';
END $$;