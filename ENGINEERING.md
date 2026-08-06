# Engineering rules

This project follows DDD + TDD + deep modules + small-batch delivery
(Hoque 2026, *A Disciplined Framework for AI-Assisted Software Development*).
Tooling: the `build-clean` skill. Current conformance: `docs/engineering-audit-2026-08-03.md` (3.5/5).

None of the rules below are ceremony. Each one is here because its absence
produced a specific failure in this repo, and most name the incident.

## Domain

- The glossary is `docs/domain/`. **Names in code come from it.**
- A new concept means proposing a glossary entry in the same change.
- `org` is the **tenant boundary**. `entity` is the **tax filer**. One org has
  many entities. Conflating them writes a cross-tenant bug that nothing will
  catch — see *Tenant isolation* below.
- `property` has a **money** face and a **compliance** face. Treating it as
  one thing is what caused Task 13b Step 4a: property mutations shipped
  writing no audit rows because the plan enumerated only the money cases.

## Tests

Run from `backend/`:

```bash
uv run --env-file ../.env pytest          # full suite (needs `supabase start`)
uv run pytest tests/core tests/flows tests/evals   # env-free subset, no database
uv run --env-file ../.env pytest tests/db tests/api   # both orderings must pass
uv run --env-file ../.env pytest tests/api tests/db
uv run ruff check
```

- **No production code without a failing test that demands it.** Watch it fail
  first, and check it fails for the *expected reason*.
- **Never weaken, delete or skip a test to reach green.** A test change is a
  behaviour change: get explicit approval and say so in the commit. (Removing
  a test that a new one strictly supersedes is allowed — prove the superset
  and leave a comment where it stood.)
- **Prove every guard by mutation.** Break the code the test defends; confirm
  that test dies and no unrelated test dies with it. Coverage says a line ran.
  Mutation says the test would notice if it were wrong. This practice has
  caught more real defects here than any other.
- Test names read as behaviour sentences.
- Before changing untested code, write characterization tests first.

**Both directory orderings must pass.** `pytest tests/db tests/api` once passed
by ordering luck: a module-level engine was never disposed, leaving connections
on a closed event loop, and the command only survived because the first API
test collected happened to 401 before touching the database.

## Tenant isolation — read this before writing any router

**The database enforces this now, and it did not until 2026-08-06.** The API
connects as `app_api`, a role with row-level security applied, and sets
`request.jwt.claims` per transaction. An unfiltered query returns the caller's
org's rows, not everyone's. `tests/db/test_rls_enforced.py` proves it by
querying with no `where` clause at all.

Before that, both processes connected as the `postgres` superuser, which
bypasses RLS. The policies in `0002_rls.sql` were written for the
direct-from-Flutter PostgREST path — which the app never uses — so they
protected nothing, and the manual filter was the only defence. That is worth
remembering as a pattern: a control that exists, is correct and is tested can
still be in force nowhere.

- **Keep filtering `org_id` in every query anyway**, explicitly or via
  `src/api/scoping.py`. RLS is a backstop, not a design: the filter is what
  makes the intent readable, and it is the thing a reviewer can see.
- A row in another org is a **404, never a 403**. A 403 confirms the id exists
  and turns the endpoint into an existence oracle over other tenants' ids.
- **Open sessions with `org_session(auth.user_id)`**, never the raw factory.
  The raw one sets no claim, and a session without a claim sees nothing —
  which fails closed, but looks like "no data" rather than "broken".
- Every new router still needs a two-org isolation test.

Two things RLS does **not** cover, so they remain entirely on the code:

- **The worker.** It connects as `app_worker`, which has `BYPASSRLS`, because
  it claims jobs across orgs and has no authenticated caller. Its whole
  boundary is the `org_id` on the claimed row. See `src/worker/jobs.py`.
- **Object storage.** Statement and export paths are built from `org_id` in
  `src/api/storage.py`, and there is no database-level equivalent. That module
  is the whole of it.

## Interfaces

- Sketch the interface before implementing, for any module over ~100 lines or
  with more than 3 call sites.
- Depth test: is the interface much simpler than what it hides? `src/api/scoping.py`
  is the local model — it takes the whole `auth` context rather than a bare
  `org_id` so that the parameter which must be right is the one a caller
  cannot omit or mistype.
- Hide decisions likely to change; pull complexity downward.

## Commits

- One increment = one verified behaviour. **Every commit green.**
- Behaviour changes and refactoring in separate commits.
- A large diff is rejected for re-slicing even when the code is correct.
- **The rule is about reviewability, so it binds source changes.** A set of
  documents that only makes sense read together (a glossary's two context
  files; an audit and the rules it produced) may land as one commit — but say
  so in the message. If you find yourself claiming that exemption for anything
  under `src/`, you are re-slicing instead.

## Style

- Docstrings are reStructuredText (`:param:`, `:returns:`, `:raises:`,
  `:seealso:`), matching the existing modules.
- Line length and lint are whatever `uv run ruff check` says, run from
  `backend/`. The repo enforces `check`, not `format` — do not run
  `ruff format` across files you did not otherwise touch.

## Prose

- **If a comment asserts a behaviour, name the test that enforces it. If you
  cannot name one, write that you did not check.**
- This codebase's comment density is an asset *only* while every claim is
  pinned. Unpinned confident prose is worse than none — it gets believed. A
  review of `portfolio.py` found 27 claims true and 1 false; the false one had
  been copied into a test docstring and a code comment.

## Errors

- Fail loudly and early. No fallbacks, no defaults, no silent catches. There
  is currently **not one** bare or broad `except` in `src/` or `scripts/`;
  keep it that way.
- Money is `Decimal`, never `float`, end to end.

## Working with agents

- Brief with the `build-clean` template; empty sections block dispatch.
- **Ask for pushback with evidence, not agreement.** Three confident-but-wrong
  claims have been caught here — two from reviewers about what a mutation
  does, one of mine propagated into a plan constraint governing five tasks. In
  every case the party who challenged it was right.
- **Verify before pinning.** Reproduce the discriminating command yourself
  before writing a claim into a plan, spec, or comment.

## Project specifics

- Backend: Python 3.12, `uv`, FastAPI, SQLAlchemy async, CrewAI. Frontend:
  Flutter (web), Material 3 Expressive. Database: Supabase (local stack).
- **Run every project command from `backend/`.** From the repo root `uv run`
  finds no project and falls back to ambient Anaconda — ruff 0.12.0 instead of
  the pinned 0.16.0, which reports 5 false E402 failures.
- A bare `pytest` runs **zero** tests: import-time env checks abort collection.
  That is intended fail-loudly behaviour; `README.md` says what CI must do.
- Three database URLs, on purpose: `DATABASE_URL` (superuser — tests,
  migrations, `seed_org.py`), `API_DATABASE_URL` (`app_api`, RLS enforced) and
  `WORKER_DATABASE_URL` (`app_worker`, BYPASSRLS). Pointing the API one at the
  superuser silently disables the tenant boundary and every test still passes
  except `tests/db/test_rls_enforced.py`.
- Categorisation runs on a local Ollama model by default; `CATEGORISER_MODEL`
  is required and never defaults, so bank data cannot reach a provider nobody
  chose. CrewAI telemetry is disabled in every environment.
- `supabase/config.toml`'s `env()` substitution reads the repo `.env` **only
  when the variable is absent from the process environment** — a shell export
  silently wins. `supabase status` does not validate the auth block; check the
  live `/auth/v1/authorize` redirect instead.
