# Security

This project holds bank statement lines, HMRC category decisions and generated
tax figures. A defect here is somebody's return, so reports are welcome and
taken seriously.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: **Security → Report a
vulnerability** on this repository. It is private between you and the
maintainer, so please use it rather than opening a public issue.

There is one maintainer and no service-level agreement. Expect an
acknowledgement within a few days.

## What is in scope

The code in this repository, and in particular:

- **Tenant isolation.** Anything that lets one org see or write another's
  rows. The database enforces this via row-level security (`app_api` has RLS
  applied; see `supabase/migrations/0006_least_privilege_roles.sql`), and the
  API filters `org_id` on top. A way past *either* is worth reporting; a way
  past both is the most serious class of bug this project has.
- **Token verification.** `backend/src/api/auth.py` verifies ES256 against the
  project's published JWKS, with the algorithm bound to the key source rather
  than read from the token. Anything that gets a forged or wrong-key token
  accepted.
- **Object storage paths.** `backend/src/api/storage.py` builds every path
  from a server-side `org_id`. Storage has **no** row-level-security
  equivalent, so that module is the entire boundary for stored statements and
  exports — a path-traversal or prefix-confusion bug there has no backstop.
- **The export guards.** Anything that produces a figure from unreviewed data,
  or exports around `assert_history_intact` or the mortgage interest split.
  A wrong number on a tax return is a security problem in this product even
  when no attacker is involved.

## What is not

- The **local development stack**. `supabase start` ships well-known
  credentials (`postgres:postgres`, the demo anon and service-role JWTs) and
  `supabase/migrations/0006` creates the `app_api` / `app_worker` roles with
  local-development passwords. These are documented, deliberate, and only ever
  bound to `127.0.0.1`. A real deployment must change them.
- Anything requiring the attacker to already hold `DATABASE_URL`,
  `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_JWT_SECRET`. Those are unbounded by
  design — see the credential table in `docs/planning/progress.md`.
- Denial of service against a self-hosted instance.

## Known and deliberate

Recorded here rather than waiting to be reported:

- **The worker connects with `BYPASSRLS`.** It claims jobs from a queue shared
  across orgs and has no authenticated caller, so its entire tenant boundary
  is the `org_id` on the claimed row. `backend/src/worker/jobs.py` says so;
  `backend/tests/worker/` is what holds it up.
- **Statement descriptions reach whatever `CATEGORISER_MODEL` names.** If that
  is a hosted model, they leave the machine. The variable is required and
  never defaults, precisely so this is a choice somebody made.
- **`ollama/` does not mean local.** A `:cloud` tag is hosted inference. See
  `.env.example`.

## If you are running this yourself

Change every credential in `.env.example` that says local development, set
`CORS_ALLOWED_ORIGINS` to your own origin (`*` is refused), and point
`API_DATABASE_URL` at `app_api` rather than the superuser — that last one
silently removes the database's tenant boundary and every test still passes
except `backend/tests/db/test_rls_enforced.py`.
