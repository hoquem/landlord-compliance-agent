-- 0002_rls.sql
-- Row Level Security for every table created in 0001_core.sql, plus the
-- DB-level cross-org "belt and braces" constraints called for in the
-- Task 5 review (service-role writes bypass RLS entirely, so tenant
-- isolation for those paths has to be enforced by constraints, not policies).
--
-- Source of truth: docs/superpowers/plans/2026-07-29-mvp-iteration-1.md, Task 6.

-- ---------------------------------------------------------------------------
-- Helper: current_org_id() -- resolves the calling user's org_id.
--
-- The plan's policy shorthand is `org_id = (select org_id from public.users
-- where id = auth.uid())`. Written directly as a per-table policy
-- expression, that shorthand is fine for every table EXCEPT `users` itself:
-- a policy on `users` that subqueries `users` makes Postgres re-evaluate the
-- very policy it's in the middle of evaluating, which raises
-- `42P17 infinite recursion detected in policy for relation "users"`
-- (confirmed against the local stack while building this migration).
--
-- The fix is the standard Supabase pattern: wrap the lookup in a
-- `security definer` function owned by a role that bypasses RLS (here,
-- `postgres`, which has `rolbypassrls = true` on this stack -- confirmed via
-- `select rolname, rolbypassrls from pg_roles`). The function's internal
-- `select` never re-enters the `users` policy, so there is no recursion, and
-- it's used uniformly on every table (including `users`) for one consistent
-- policy shape instead of a special case just for `users`.
--
-- `set search_path = ''` pins the search path so name resolution can't be
-- hijacked by a search_path change -- this also clears the Supabase
-- `function_search_path_mutable` advisor lint for this function, alongside
-- the same fix applied to `set_updated_at()` below. `stable` (not
-- `immutable`) because the result depends on the current session's
-- `auth.uid()` and on the contents of `public.users`.
-- ---------------------------------------------------------------------------
create or replace function public.current_org_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select org_id from public.users where id = auth.uid()
$$;

-- ---------------------------------------------------------------------------
-- Re-pin search_path on set_updated_at() (defined in 0001_core.sql).
--
-- Same `function_search_path_mutable` advisor lint as above. The function
-- body only touches `new.updated_at` (a row variable, not a name lookup) and
-- `now()` (always resolved via the implicit, un-droppable `pg_catalog`
-- entry at the front of every search path, so `search_path = ''` doesn't
-- break it). This is a `create or replace` of the exact same
-- `public.set_updated_at()` object 0001 created (verified via
-- `pg_proc.pronamespace` below) -- it replaces the function in place, it
-- does not create a second one, so every `trg_*_set_updated_at` trigger
-- from 0001 picks up the pinned search_path automatically.
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Step 1b: unique(id, org_id) on every parent table that a composite FK
-- below references, plus the additive composite FKs themselves.
--
-- Why additive rather than replacing the simple FKs: the simple FKs
-- (`property_id references properties(id)`, etc.) carry the ON DELETE
-- semantics decided in 0001 -- notably `transactions.import_id ... on
-- delete cascade`. Composite FKs are added alongside them purely to close
-- the cross-org stitching hole: without them, a service-role write (which
-- bypasses RLS) could set e.g. `property_ownership.property_id` to a
-- property in org A while `property_ownership.org_id` is org B, and the
-- simple FK alone would happily allow it. Requiring `(property_id, org_id)`
-- to jointly exist in `properties(id, org_id)` makes that impossible.
--
-- `orgs` itself is excluded from the `unique(id, org_id)` treatment -- it
-- IS the org, it has no org_id column.
-- ---------------------------------------------------------------------------

alter table public.properties
  add constraint uq_properties_id_org_id unique (id, org_id);

alter table public.entities
  add constraint uq_entities_id_org_id unique (id, org_id);

alter table public.imports
  add constraint uq_imports_id_org_id unique (id, org_id);

alter table public.documents
  add constraint uq_documents_id_org_id unique (id, org_id);

-- property_ownership: property and entity must belong to the SAME org as
-- the ownership row itself.
alter table public.property_ownership
  add constraint fk_property_ownership_property_org
    foreign key (property_id, org_id) references public.properties (id, org_id);

alter table public.property_ownership
  add constraint fk_property_ownership_entity_org
    foreign key (entity_id, org_id) references public.entities (id, org_id);

-- imports.entity_id -- not in the plan's enumerated composite-FK list, but
-- it is the same stitching vector as every other org-scoped FK in this file
-- (a service-role write could otherwise set imports.entity_id to an entity
-- in a different org than imports.org_id), so it gets the same treatment.
-- entity_id is nullable (see 0001: "Bank-account owner for this statement");
-- per Postgres's default MATCH SIMPLE, a composite FK with any NULL
-- component is not checked, which is fine -- a null entity_id has nothing
-- to stitch across orgs.
alter table public.imports
  add constraint fk_imports_entity_org
    foreign key (entity_id, org_id) references public.entities (id, org_id);

-- transactions: import_id and property_id are nullable (see 0001 comments
-- on both columns); as above, MATCH SIMPLE means the composite FK is
-- simply not checked when the referencing column is null, which is
-- correct here -- there's nothing to stitch when the pointer itself is
-- absent. entity_id is NOT NULL, so that composite FK is always enforced.
--
-- transactions.import_id already carries `on delete cascade` on its simple
-- FK (0001: deleting a failed import takes its parsed lines with it). The
-- composite FK below must agree, or `delete from imports` fires two RI
-- checks with different actions and the NO ACTION check on the composite
-- FK can see the transaction row before the CASCADE from the simple FK has
-- removed it -- so this composite FK also cascades, matching the simple
-- one exactly rather than introducing a second, conflicting delete rule.
alter table public.transactions
  add constraint fk_transactions_import_org
    foreign key (import_id, org_id) references public.imports (id, org_id)
    on delete cascade;

alter table public.transactions
  add constraint fk_transactions_entity_org
    foreign key (entity_id, org_id) references public.entities (id, org_id);

alter table public.transactions
  add constraint fk_transactions_property_org
    foreign key (property_id, org_id) references public.properties (id, org_id);

-- tenancies
alter table public.tenancies
  add constraint fk_tenancies_property_org
    foreign key (property_id, org_id) references public.properties (id, org_id);

-- compliance_certificates: document_id is nullable (0001), MATCH SIMPLE
-- applies as above.
alter table public.compliance_certificates
  add constraint fk_compliance_certificates_property_org
    foreign key (property_id, org_id) references public.properties (id, org_id);

alter table public.compliance_certificates
  add constraint fk_compliance_certificates_document_org
    foreign key (document_id, org_id) references public.documents (id, org_id);

-- mtd_quarters: generated_document_id is nullable (0001), MATCH SIMPLE
-- applies as above.
alter table public.mtd_quarters
  add constraint fk_mtd_quarters_entity_org
    foreign key (entity_id, org_id) references public.entities (id, org_id);

alter table public.mtd_quarters
  add constraint fk_mtd_quarters_document_org
    foreign key (generated_document_id, org_id) references public.documents (id, org_id);

-- users(org_id) -- plain FK to orgs(id) from 0001 is sufficient. users
-- doesn't reference any other org-scoped parent table, so there's no
-- composite-FK stitching vector to close here.

-- ---------------------------------------------------------------------------
-- Step 1: enable RLS + tenant-isolation policy on every table.
--
-- Policy shape is uniform: `org_id = (select public.current_org_id())`
-- (wrapped in a `select` so Postgres evaluates it once per query as an
-- InitPlan, not once per row -- the documented Supabase performance
-- pattern for RLS subqueries). `orgs` is the one exception: it has no
-- org_id column, so its own `id` is compared instead.
--
-- `for all` covers SELECT/INSERT/UPDATE/DELETE with one policy: the
-- `using` clause governs which existing rows are visible (SELECT, and the
-- pre-image for UPDATE/DELETE); the `with check` clause governs which rows
-- may be written (INSERT, and the post-image for UPDATE). A cross-org
-- INSERT or UPDATE (org_id set to any org other than the caller's own) is
-- rejected by `with check`; a cross-org SELECT/UPDATE/DELETE target simply
-- isn't visible under `using`, so UPDATE/DELETE against another org's row
-- affects 0 rows rather than erroring.
--
-- Service role bypasses all of this by design: `service_role` (and this
-- stack's `postgres` role, confirmed via `pg_roles.rolbypassrls`) has
-- BYPASSRLS, and the backend/worker's DATABASE_URL connects as `postgres`
-- directly -- so these policies constrain PostgREST access (anon key + a
-- real user JWT, which is how the Flutter frontend and this migration's
-- test talk to the API) but never the service-side write path. That write
-- path still must filter every query by org_id itself (belt and braces);
-- the composite FKs above are the DB-level backstop for when it doesn't.
--
-- Grants: this stack's `auto_expose_new_tables` is off (Supabase's current
-- default -- see supabase/config.toml), so tables created in 0001 carry no
-- SELECT/INSERT/UPDATE/DELETE grants for `anon`/`authenticated`/
-- `service_role` at all (verified: `\dp public.properties` showed only
-- TRUNCATE/REFERENCES/TRIGGER/MAINTAIN for those roles). RLS policies
-- alone don't restore access -- the underlying SQL privilege is checked
-- first and fails closed with "permission denied" before a policy is even
-- consulted. `authenticated` gets explicit grants below so PostgREST
-- access (the only path these policies police) is actually reachable;
-- `anon` gets none, so unauthenticated requests are refused outright, not
-- merely filtered to zero rows. `service_role` gets none either -- this
-- project's service-role usage is the direct `postgres` DATABASE_URL
-- connection, not PostgREST with the service-role key, so there is no
-- grant to add today; a future task adding a PostgREST-via-service-role
-- path (e.g. an Edge Function) would need to add it then.
-- ---------------------------------------------------------------------------

-- orgs
alter table public.orgs enable row level security;

create policy orgs_tenant_isolation on public.orgs
  for all
  using (id = (select public.current_org_id()))
  with check (id = (select public.current_org_id()));

grant select, insert, update, delete on public.orgs to authenticated;

-- users
alter table public.users enable row level security;

create policy users_tenant_isolation on public.users
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.users to authenticated;

-- entities
alter table public.entities enable row level security;

create policy entities_tenant_isolation on public.entities
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.entities to authenticated;

-- properties
alter table public.properties enable row level security;

create policy properties_tenant_isolation on public.properties
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.properties to authenticated;

-- property_ownership
alter table public.property_ownership enable row level security;

create policy property_ownership_tenant_isolation on public.property_ownership
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.property_ownership to authenticated;

-- tenancies
alter table public.tenancies enable row level security;

create policy tenancies_tenant_isolation on public.tenancies
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.tenancies to authenticated;

-- imports
alter table public.imports enable row level security;

create policy imports_tenant_isolation on public.imports
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.imports to authenticated;

-- transactions
alter table public.transactions enable row level security;

create policy transactions_tenant_isolation on public.transactions
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.transactions to authenticated;

-- documents
alter table public.documents enable row level security;

create policy documents_tenant_isolation on public.documents
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.documents to authenticated;

-- compliance_certificates
alter table public.compliance_certificates enable row level security;

create policy compliance_certificates_tenant_isolation on public.compliance_certificates
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.compliance_certificates to authenticated;

-- mtd_quarters
alter table public.mtd_quarters enable row level security;

create policy mtd_quarters_tenant_isolation on public.mtd_quarters
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.mtd_quarters to authenticated;

-- job_queue
alter table public.job_queue enable row level security;

create policy job_queue_tenant_isolation on public.job_queue
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert, update, delete on public.job_queue to authenticated;

-- audit_log -- append-only by design (see 0001: no updated_at/update
-- trigger). Grant select + insert only; deliberately no update/delete, so
-- an authenticated user can never edit or erase the audit trail via
-- PostgREST even though the RLS policy shape is the same as every other
-- table. Nothing in the API design (spec) ever updates or deletes an
-- audit_log row.
alter table public.audit_log enable row level security;

create policy audit_log_tenant_isolation on public.audit_log
  for all
  using (org_id = (select public.current_org_id()))
  with check (org_id = (select public.current_org_id()));

grant select, insert on public.audit_log to authenticated;
