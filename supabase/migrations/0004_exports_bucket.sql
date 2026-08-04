-- 0004_exports_bucket.sql
-- The `exports` bucket and its row-level security, per Task 16 Step 3.
--
-- Source of truth: docs/superpowers/plans/2026-07-29-mvp-iteration-1.md, Task 16.
--
-- A separate bucket rather than reusing `statements`. The text-compared
-- policy predicate below would work either way, but a generated quarterly
-- return stored under a path that says `statements/` is a lie the next
-- reader trips on -- and the two have genuinely different lifecycles: a
-- statement is input the user supplied, an export is output this system
-- produced and may need to regenerate.
--
-- Everything here mirrors 0003_storage.sql deliberately, including the
-- shape of the predicate. One policy idiom across the schema means a reader
-- who has understood one has understood all of them; read 0003's comments
-- for the reasoning, which applies unchanged. In particular:
--
--   **Do not "simplify" the predicate to a uuid cast.** The natural-looking
--   `(storage.foldername(name))[1]::uuid = (select public.current_org_id())`
--   raises `invalid input syntax for type uuid` for any object whose first
--   segment is not a UUID, and because this is a *policy predicate* that
--   error breaks access evaluation for every scan of the bucket -- one
--   mis-pathed upload would deny the whole org. Verified against this
--   database (2026-07-29, re-confirmed 2026-08-04).
--
-- And the same caveat about what these do NOT protect: the API connects as
-- `postgres` (see DATABASE_URL) and bypasses RLS entirely. These protect
-- the direct-from-Flutter path, so that the frontend can download an
-- export it owns and no other. On API paths the `{org_id}/` prefix must be
-- built server-side from the authenticated caller's org -- see
-- `src/api/storage.py`, whose interface takes an org id and a filename and
-- never a path, so that a caller cannot ask for the wrong one.

insert into storage.buckets (id, name, public)
values ('exports', 'exports', false)
on conflict (id) do nothing;

create policy exports_select_own_org on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );

create policy exports_insert_own_org on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );

create policy exports_update_own_org on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  )
  with check (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );

create policy exports_delete_own_org on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = (select public.current_org_id())::text
  );
