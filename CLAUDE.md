# landlord-compliance-agent

A compliance and MTD-ITSA bookkeeping agent for a UK property portfolio:
parse bank statements, categorise them against HMRC's SA105 categories,
apportion by ownership share, and produce per-entity quarterly export packs.

## Read these first

| File | What it is |
|---|---|
| `ENGINEERING.md` | **The rules.** Test discipline, tenant isolation, commit shape, the traps that have already bitten someone here. |
| `docs/domain/money.md` | Glossary for the money context. **Names in code come from here.** |
| `docs/domain/compliance.md` | Glossary for the compliance context. |
| `docs/engineering-audit-2026-08-03.md` | Current conformance: 3.5/5, with the top 3 fixes. |
| `docs/superpowers/plans/2026-07-29-mvp-iteration-1.md` | The plan. Canonical for what to build next. |
| `docs/planning/progress.md` | What happened and why, including every decision and every wrong turn. |

## Method

This project follows the `build-clean` framework: DDD + TDD + deep modules +
small-batch delivery (Hoque 2026). The two rules that matter most, because
this repo has been bitten by both:

- **Prove a guard by mutation.** Break the code a test defends; confirm that
  test dies and no other does.
- **If a comment asserts a behaviour, name the test that enforces it.** If you
  cannot name one, say you did not check.

`ENGINEERING.md` has the rest. Read it before writing code.

## Three things that will waste your time if you don't know them

1. **Run project commands from `backend/`.** From the repo root `uv run` finds
   no project and silently falls back to ambient Anaconda tooling.
2. **A bare `pytest` runs zero tests.** Use `uv run --env-file ../.env pytest`
   with the local Supabase stack up (`supabase start` from the repo root).
3. **RLS does not protect the API.** `DATABASE_URL` is a superuser. Manual
   `org_id` filtering is the entire tenant boundary.
