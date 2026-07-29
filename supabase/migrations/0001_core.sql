-- 0001_core.sql
-- Core schema for the Landlord Compliance Agent.
--
-- Source of truth: docs/superpowers/specs/2026-07-28-landlord-compliance-agent-design.md
-- ("Data model" section). This migration creates the 13 MVP tables plus the
-- enum types and helper trigger they depend on. Row Level Security is
-- deliberately NOT enabled here -- it lands in 0002_rls.sql (Task 6).
--
-- Conventions used throughout this file:
--   * Every table except `orgs` itself carries `org_id uuid not null
--     references orgs(id)` -- `orgs` is the tenant root and has no parent.
--   * Every table carries `created_at` / `updated_at timestamptz`, both
--     defaulting to `now()`. `updated_at` is kept current by the
--     `set_updated_at()` trigger attached to every table below.
--   * Primary keys are `uuid default gen_random_uuid()`. `gen_random_uuid()`
--     has been a native PostgreSQL 13+ function since it was folded into
--     core, so no extension is required on the Supabase Postgres 17 image
--     this project runs against.

-- ---------------------------------------------------------------------------
-- Helper: keep `updated_at` current on every UPDATE.
-- ---------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Enum types
-- ---------------------------------------------------------------------------

-- entities.tax_regime
create type tax_regime as enum (
  'mtd_itsa',
  'corporation_tax'
);

-- entities.quarter_basis -- HMRC permits a calendar-quarter election; MVP
-- ships tax_year basis only, but the column exists now so the election
-- doesn't force a later schema change.
create type quarter_basis as enum (
  'tax_year',
  'calendar_election'
);

-- properties.finance_cost_classification -- drives Section 24 finance-cost
-- restriction treatment (residential) vs full deduction (non_residential).
create type finance_cost_classification as enum (
  'residential',
  'non_residential'
);

-- imports.status -- `categorisation_failed` is required by Task 18
-- (failed-job visibility in the UI); added now so no later migration is
-- needed to introduce it.
create type import_status as enum (
  'pending',
  'parsed',
  'failed',
  'categorisation_failed'
);

-- transactions.hmrc_category -- the exact 15 spec values used for MTD ITSA
-- SA105 property-income categorisation. This is the single SQL-side source
-- of truth; backend/src/core/categories.py (Task 7) mirrors it and
-- backend/tests/db/test_schema.py guards against drift between the two.
create type hmrc_category as enum (
  'rent_income',
  'other_property_income',
  'rent_paid',
  'rates_insurance_ground',
  'repairs_maintenance',
  'finance_costs_residential',
  'finance_costs_nonresidential',
  'legal_professional',
  'service_costs',
  'travel_vehicle',
  'other_allowable',
  'replacement_domestic_items',
  'use_of_home_allowance',
  'capital_expense',
  'personal_non_business'
);

-- transactions.status -- agent proposals never write directly to
-- `confirmed`; only a human confirm action does (see spec "Architecture").
create type transaction_status as enum (
  'unclassified',
  'proposed',
  'confirmed',
  'excluded'
);

-- transactions.direction -- money movement direction on the underlying bank
-- statement line, independent of hmrc_category.
create type transaction_direction as enum (
  'in',
  'out'
);

-- compliance_certificates.type
create type certificate_type as enum (
  'gas_safety',
  'eicr',
  'epc',
  'hmo_licence',
  'selective_licence'
);

-- mtd_quarters.quarter -- UK tax-year quarters by default (6 Apr start);
-- see entities.quarter_basis for the calendar-election setting that will
-- eventually key an alternate computation.
create type mtd_quarter_number as enum (
  'Q1',
  'Q2',
  'Q3',
  'Q4'
);

-- job_queue.status
create type job_status as enum (
  'queued',
  'running',
  'done',
  'failed'
);

-- audit_log.actor_type
create type actor_type as enum (
  'user',
  'agent',
  'system'
);

-- ---------------------------------------------------------------------------
-- orgs -- tenant root. No org_id column: this table IS the tenant.
--
-- Org deletion policy: every `org_id` FK in this file deliberately uses
-- the default NO ACTION (i.e. deleting an org is blocked while any
-- dependent row exists), not CASCADE. Tenant financial and compliance
-- data must never be hard-deleted as a side effect of deleting the
-- parent org. Org offboarding is a soft-delete/archival concern and is
-- out of MVP scope.
-- ---------------------------------------------------------------------------
create table public.orgs (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_orgs_set_updated_at
before update on public.orgs
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- users -- mirror of auth.users, scoped to an org.
-- ---------------------------------------------------------------------------
create table public.users (
  id uuid primary key references auth.users (id) on delete cascade,
  org_id uuid not null references public.orgs (id),
  email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_users_org_id on public.users (org_id);

create trigger trg_users_set_updated_at
before update on public.users
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- entities -- ownership entities (individual / Ltd company).
-- ---------------------------------------------------------------------------
create table public.entities (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  name text not null,
  tax_regime tax_regime not null,
  quarter_basis quarter_basis not null default 'tax_year',
  prs_registration_number text,
  prs_registered_at date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_entities_org_id on public.entities (org_id);

create trigger trg_entities_set_updated_at
before update on public.entities
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- properties
-- ---------------------------------------------------------------------------
create table public.properties (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  address_line1 text not null,
  address_line2 text,
  city text not null,
  postcode text not null,
  country text not null default 'GB',
  finance_cost_classification finance_cost_classification not null,
  epc_rating text,
  epc_expiry date,
  bedroom_count integer,
  licensing_flag boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_properties_org_id on public.properties (org_id);

create trigger trg_properties_set_updated_at
before update on public.properties
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- property_ownership -- junction: who owns what share of which property.
--
-- IMPORTANT: the DB only enforces 0 < ownership_percentage <= 100 per row.
-- The invariant that sum(ownership_percentage) = 100 across all owners of a
-- given property is validated in the API at confirm time (see spec "Error
-- handling"), not by a DB constraint/trigger, because ownership can be
-- edited row-by-row (add/remove an owner) and a DB-level sum check would
-- have to run mid-edit against a transiently-invalid total.
-- ---------------------------------------------------------------------------
create table public.property_ownership (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  property_id uuid not null references public.properties (id),
  entity_id uuid not null references public.entities (id),
  ownership_percentage numeric(5, 2) not null
    check (ownership_percentage > 0 and ownership_percentage <= 100),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_property_ownership_property_entity unique (property_id, entity_id)
);

create index idx_property_ownership_org_id on public.property_ownership (org_id);
-- No idx_property_ownership_property_id: property_id is the leading column
-- of uq_property_ownership_property_entity, which already covers it.
create index idx_property_ownership_entity_id on public.property_ownership (entity_id);

create trigger trg_property_ownership_set_updated_at
before update on public.property_ownership
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- tenancies -- schema-only in MVP: no API/UI until iteration 2's Section 13
-- rent-review work. Deposit detail stays as flat summary fields here; a
-- structured `deposits` table lands in iteration 2.
-- ---------------------------------------------------------------------------
create table public.tenancies (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  property_id uuid not null references public.properties (id),
  rent_amount numeric(12, 2),
  rent_frequency text,
  start_date date,
  is_periodic boolean,
  last_rent_increase_date date,
  deposit_amount numeric(12, 2),
  deposit_scheme text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_tenancies_org_id on public.tenancies (org_id);
create index idx_tenancies_property_id on public.tenancies (property_id);

create trigger trg_tenancies_set_updated_at
before update on public.tenancies
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- imports -- uploaded bank-statement files.
-- ---------------------------------------------------------------------------
create table public.imports (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  -- Bank-account owner for this statement; becomes the default
  -- transactions.entity_id for every line parsed out of this import.
  entity_id uuid references public.entities (id),
  file_path text not null,
  source_bank text not null,
  period_start date,
  period_end date,
  status import_status not null default 'pending',
  error_detail jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_imports_org_id on public.imports (org_id);
create index idx_imports_entity_id on public.imports (entity_id);
create index idx_imports_status on public.imports (status);

create trigger trg_imports_set_updated_at
before update on public.imports
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- transactions -- the ledger.
--
-- entity_id semantics: entity_id records the bank-account owner from the
-- import -- PROVENANCE, not tax attribution. For property-allocated
-- transactions, per-entity export totals derive EXCLUSIVELY from the
-- property_ownership percentage split (HMRC PIM1035), never from
-- transactions.entity_id -- there is one correct computation, not two.
-- For transactions with property_id null (unallocated or non-property),
-- entity_id determines whose ledger the line sits on pending allocation
-- or exclusion.
--
-- import_id is nullable: null = a manually-entered claim with no bank
-- statement line (e.g. flat-rate use_of_home_allowance). The MVP write
-- path (imports endpoint) always sets it; manual-claim entry arrives in
-- a later iteration.
--
-- import_id cascades ON DELETE (unlike every other FK in this file, which
-- defaults to NO ACTION -- see the note on `orgs` above): deleting a
-- failed import should take its parsed lines with it. SET NULL was
-- rejected because null import_id already has a distinct, documented
-- meaning (manual claim) -- silently reclassifying bank-derived lines as
-- manual claims on import deletion would be wrong.
-- ---------------------------------------------------------------------------
create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  import_id uuid references public.imports (id) on delete cascade,
  entity_id uuid not null references public.entities (id),
  property_id uuid references public.properties (id),
  date date not null,
  amount numeric(12, 2) not null,
  direction transaction_direction not null,
  description text not null,
  hmrc_category hmrc_category,
  status transaction_status not null default 'unclassified',
  confidence numeric(3, 2) check (confidence is null or (confidence >= 0 and confidence <= 1)),
  proposed_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_transactions_org_id on public.transactions (org_id);
create index idx_transactions_import_id on public.transactions (import_id);
create index idx_transactions_entity_id on public.transactions (entity_id);
create index idx_transactions_property_id on public.transactions (property_id);
create index idx_transactions_status on public.transactions (status);

create trigger trg_transactions_set_updated_at
before update on public.transactions
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- documents -- Supabase Storage refs (uploaded certificates, generated
-- notices/exports). Declared before compliance_certificates and
-- mtd_quarters, which reference it.
-- ---------------------------------------------------------------------------
create table public.documents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  storage_path text not null,
  kind text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_documents_org_id on public.documents (org_id);

create trigger trg_documents_set_updated_at
before update on public.documents
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- compliance_certificates
--
-- `status` (valid/expiring/expired) is DERIVED from expiry_date at query
-- time, not stored -- storing it would let it drift out of sync silently.
-- ---------------------------------------------------------------------------
create table public.compliance_certificates (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  property_id uuid not null references public.properties (id),
  type certificate_type not null,
  issue_date date,
  expiry_date date not null,
  certificate_ref text,
  document_id uuid references public.documents (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_compliance_certificates_org_id on public.compliance_certificates (org_id);
create index idx_compliance_certificates_property_id on public.compliance_certificates (property_id);

create trigger trg_compliance_certificates_set_updated_at
before update on public.compliance_certificates
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- mtd_quarters -- cumulative year-to-date totals per entity/tax-year/quarter.
--
-- Scope: only entities with tax_regime = 'mtd_itsa' get rows here (enforced
-- in the API, not the DB, since Ltd entities still need to exist without a
-- mtd_quarters row). Ltd entities get a separate simple P&L export
-- artifact instead (see spec Open question 2).
--
-- Submissions are cumulative -- each quarterly submission replaces the
-- prior. We keep every export as its own row (`version` increments per
-- re-export for the same entity/tax_year/quarter) rather than overwriting,
-- so history/audit is preserved.
--
-- One numeric(12,2) column per HMRC category that can appear on an MTD
-- ITSA quarterly update. `personal_non_business` is deliberately excluded:
-- by definition it is never reported to HMRC, so it has no place in a
-- submission total.
--
-- tax_year format is pinned to UK tax year notation, e.g. '2026-27'
-- (4-digit start year, hyphen, 2-digit end year). Row identity (the
-- entity/tax_year/quarter/version unique constraint below) and the
-- Task 16 cumulative-decrease guard both key on this exact string, so the
-- format must be single-sourced -- Task 10's format_tax_year() is the one
-- place that produces it.
-- ---------------------------------------------------------------------------
create table public.mtd_quarters (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  entity_id uuid not null references public.entities (id),
  tax_year text not null check (tax_year ~ '^\d{4}-\d{2}$'),
  quarter mtd_quarter_number not null,
  version integer not null default 1,
  rent_income_total numeric(12, 2) not null default 0,
  other_property_income_total numeric(12, 2) not null default 0,
  rent_paid_total numeric(12, 2) not null default 0,
  rates_insurance_ground_total numeric(12, 2) not null default 0,
  repairs_maintenance_total numeric(12, 2) not null default 0,
  finance_costs_residential_total numeric(12, 2) not null default 0,
  finance_costs_nonresidential_total numeric(12, 2) not null default 0,
  legal_professional_total numeric(12, 2) not null default 0,
  service_costs_total numeric(12, 2) not null default 0,
  travel_vehicle_total numeric(12, 2) not null default 0,
  other_allowable_total numeric(12, 2) not null default 0,
  replacement_domestic_items_total numeric(12, 2) not null default 0,
  use_of_home_allowance_total numeric(12, 2) not null default 0,
  capital_expense_total numeric(12, 2) not null default 0,
  -- Free-form until Task 16 pins the export lifecycle; deliberately not an
  -- enum yet -- the one status column in this file where the closed set
  -- isn't known.
  export_status text,
  generated_document_id uuid references public.documents (id),
  generated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_mtd_quarters_entity_taxyear_quarter_version
    unique (entity_id, tax_year, quarter, version)
);

create index idx_mtd_quarters_org_id on public.mtd_quarters (org_id);
-- No idx_mtd_quarters_entity_id: entity_id is the leading column of
-- uq_mtd_quarters_entity_taxyear_quarter_version, which already covers it.

create trigger trg_mtd_quarters_set_updated_at
before update on public.mtd_quarters
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- job_queue -- background worker jobs (Postgres-backed, polled by the
-- Python worker). Never processed inline in web requests.
-- ---------------------------------------------------------------------------
create table public.job_queue (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  type text not null,
  payload jsonb not null default '{}'::jsonb,
  status job_status not null default 'queued',
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_job_queue_org_id on public.job_queue (org_id);
create index idx_job_queue_status on public.job_queue (status);

create trigger trg_job_queue_set_updated_at
before update on public.job_queue
for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- audit_log -- append-only record of every state change to money or
-- compliance data.
--
-- No `updated_at` / update trigger here (unlike every other table in this
-- file): audit rows are append-only, no UPDATEs are ever expected against
-- them, so `updated_at` would be dead weight -- `created_at` alone records
-- when the event happened.
-- ---------------------------------------------------------------------------
create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id),
  actor_type actor_type not null,
  actor_id uuid,
  action text not null,
  before jsonb,
  after jsonb,
  created_at timestamptz not null default now()
);

create index idx_audit_log_org_id on public.audit_log (org_id);
