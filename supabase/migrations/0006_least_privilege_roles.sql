-- Giving the API a database role that row-level security actually applies to.
--
-- **The problem this fixes.** Until now both the API and the worker connected
-- as `postgres`, a superuser, which bypasses RLS entirely. The policies in
-- 0002_rls.sql were written to protect the direct-from-Flutter PostgREST
-- path -- and the app never uses PostgREST. So the policies protected nothing
-- at all, and the manual `org_id` filter in each of ~26 query sites was not
-- the first of two defences but the only one. A single forgotten `where`
-- clause was one customer reading another's tax return, with nothing
-- underneath to catch it.
--
-- **Two roles, because the two processes need different things.**
--
--   app_api    -- RLS applies. Cannot see across orgs, whatever the query says.
--   app_worker -- BYPASSRLS, because it legitimately reads across orgs: it
--                 claims jobs from a queue it does not own and has no
--                 authenticated caller to scope it.
--
-- That asymmetry is deliberate and is where the risk now sits. The API is
-- many query sites across seven routers written by whoever adds a feature;
-- the worker is one file whose entire tenant boundary is a documented,
-- mutation-tested `org_id` taken from the claimed job row. Concentrating the
-- unguarded credential in the smaller, better-watched place is the trade.
--
-- **How the API tells the database who is asking.** It sets
-- `request.jwt.claims` per transaction, which is exactly what `auth.uid()`
-- already reads (verified against the local stack, not assumed) -- so every
-- policy written in 0002_rls.sql works unchanged. No second implementation of
-- "which org", which is the failure this whole codebase keeps guarding
-- against.
--
-- Passwords below are local-development values, in the same spirit as the
-- well-known `postgres:postgres` this stack already ships with. **A real
-- deployment must ALTER them**; see .env.example.

-- ---------------------------------------------------------------------------
-- The API's role. Deliberately NOT the table owner and without BYPASSRLS,
-- because an owner bypasses RLS unless the table also has FORCE ROW LEVEL
-- SECURITY -- and relying on that would make the guarantee depend on a
-- setting nobody looks at.
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_api') then
    create role app_api login password 'app_api_local_dev';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'app_worker') then
    create role app_worker login password 'app_worker_local_dev' bypassrls;
  end if;
end
$$;

grant usage on schema public to app_api, app_worker;

-- `auth.uid()` is called from inside `public.current_org_id()`, which is
-- `security definer` -- but the policy expression itself is evaluated as the
-- querying role, so that role must be able to execute the function.
grant execute on function public.current_org_id() to app_api, app_worker;
grant usage on schema auth to app_api, app_worker;

grant select, insert, update, delete
  on all tables in schema public to app_api, app_worker;

-- `on all tables` covers the tables that exist *now*. A later migration that
-- adds one grants nothing, and every query against it fails with a permission
-- error -- loud, but still a trap. This makes the grant apply to future
-- tables created by `postgres` as well.
--
-- **A new table still needs its RLS policy written by hand**; default
-- privileges grant access, they do not create policies, and a table with RLS
-- enabled and no policy denies everything.
alter default privileges in schema public
  grant select, insert, update, delete on tables to app_api, app_worker;

comment on role app_api is
  'The API''s connection. RLS applies: it sees only the org named in '
  'request.jwt.claims, which the API sets per transaction.';

comment on role app_worker is
  'The background worker''s connection. BYPASSRLS, because it reads across '
  'orgs by design. Its tenant boundary is the org_id on the claimed job row '
  'and nothing else -- see backend/src/worker/jobs.py.';
