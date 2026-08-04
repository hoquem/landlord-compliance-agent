-- 0003_storage.sql
-- The `statements` bucket and its row-level security, per spec §Security
-- ("storage buckets namespaced per org") and Task 14 Step 0.
--
-- Source of truth: docs/superpowers/plans/2026-07-29-mvp-iteration-1.md, Task 14.
--
-- Why this is a migration and not a script: `supabase start` replays
-- migrations, so the bucket and its policies come back with the stack on any
-- machine. A one-off script creates a local-only artefact that CI and every
-- future developer silently lack.
--
-- Baseline before this migration, measured on the live local stack rather
-- than assumed (re-confirmed 2026-08-04): zero buckets, zero policies on
-- `storage.objects`, but RLS already **enabled** on `storage.objects` and
-- `storage.buckets`. So storage started deny-by-default for the
-- `authenticated` role, which is the right direction to be wrong in -- this
-- migration opens exactly one path rather than closing an open one.

-- ---------------------------------------------------------------------------
-- The bucket.
--
-- Private (`public = false`): a public bucket serves objects to anyone with
-- the URL, bypassing every policy below. Bank statements are the most
-- sensitive data in this system.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('statements', 'statements', false)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Path convention: `statements/{org_id}/{...}`.
--
-- The org id is the FIRST path segment, and these policies are the only
-- thing enforcing that. `public.documents.storage_path` is free text, so the
-- table cannot enforce path isolation -- a row claiming another org's path
-- would be a plain string, constrained by nothing.
--
-- **VERIFIED TRAP -- do not "simplify" the predicate to a uuid cast.**
-- `storage.foldername('<uuid>/2026/stmt.csv')` returns `{<uuid>,2026}`: the
-- folder segments, with the filename stripped. The natural-looking form
--
--     (storage.foldername(name))[1]::uuid = (select public.current_org_id())
--
-- raises `invalid input syntax for type uuid: "personal"` for any object
-- whose first segment is not a UUID. Because this is a *policy predicate*,
-- that error breaks access evaluation for every scan of the bucket, not just
-- for the offending object -- one mis-pathed upload would deny the whole org.
-- Comparing as text returns false safely instead. Both forms were executed
-- against this database (2026-07-29, re-confirmed 2026-08-04); the cast
-- errors, the text comparison returns `f`.
--
-- `current_org_id()` is the same `security definer`, `stable`,
-- `search_path = ''` helper the thirteen table policies in 0002_rls.sql use,
-- and the `org_id = (select public.current_org_id())` shape is deliberately
-- identical to theirs -- one policy idiom across the schema, so a reader who
-- has understood one has understood all of them.
--
-- Note what these policies do NOT protect. The API connects as `postgres`
-- (see `DATABASE_URL`), which bypasses RLS entirely, exactly as it does for
-- the table policies. These protect the direct-from-Flutter PostgREST/storage
-- path only. On API paths the `{org_id}/` prefix must be constructed
-- server-side from the authenticated caller's org and never from client
-- input, or the isolation below is decorative.
-- ---------------------------------------------------------------------------
create policy statements_select_own_org on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'statements'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );

create policy statements_insert_own_org on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'statements'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );

create policy statements_update_own_org on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'statements'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  )
  with check (
    bucket_id = 'statements'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );

create policy statements_delete_own_org on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'statements'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );
