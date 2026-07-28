# Landlord Compliance Agent — MVP (Iteration 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MTD-ready digital records for Mahmud's portfolio: CSV statement ingest → AI-proposed HMRC categorisation → human review/confirm → cumulative quarterly export pack, plus a static compliance-certificates page.

**Architecture:** Deterministic Postgres (Supabase) core owns all money/compliance data; CrewAI flows run in a background worker and only ever write `proposed` rows; FastAPI is the single write path with validation and audit logging; Flutter (web) frontend. Spec: `docs/superpowers/specs/2026-07-28-landlord-compliance-agent-design.md` — read it before starting.

**Tech Stack:** Python 3.12 + uv, FastAPI, SQLAlchemy 2 (async, asyncpg), Supabase (Postgres/Auth/Storage, CLI migrations), CrewAI (flows + `Agent.kickoff` with Pydantic `response_format`), pytest, Flutter (Material 3 + flutter_animate), Anthropic Claude via CrewAI LLM config.

**Repo layout (monorepo):**

```
backend/            # FastAPI app + worker + agent flows (single uv project)
  src/api/          #   FastAPI: routers, deps, auth
  src/core/         #   pure domain logic (parser, splits, quarters, categories) — no I/O
  src/db/           #   SQLAlchemy models, session, repositories
  src/worker/       #   job-queue poller running CrewAI flows
  src/flows/        #   CrewAI flow package (scaffolded by crewai CLI, then moved here)
  tests/
supabase/           # supabase CLI project: migrations/, config.toml
frontend/           # Flutter app (created with `flutter create`)
  lib/theme/        #   design tokens: M3 Expressive theme, motion durations/curves
  lib/features/     #   auth/, upload/, review/, certificates/, dashboard/
docs/
```

**Conventions for every task:** TDD (test first, watch it fail, minimal code, watch it pass, commit). Docstrings in reStructuredText. Fail loudly — no silent catches, no default fallbacks. Never mark a step done without showing the passing command output. Commit after every green.

**Security convention (spec §Security):** statement content (descriptions, amounts) must never appear in application logs at any level; agent prompts include only the fields categorisation needs. Applies to Tasks 11, 14, 18 — reviewers check for stray `logger.*(line...)` calls.

**Capital-vs-revenue note:** the spec's "capital flag" is represented by the `CAPITAL_EXPENSE` enum value, not a separate boolean. Do not add a redundant flag column.

---

## Phase 0 — Scaffolding

### Task 1: Backend project scaffold

**Files:**
- Create: `backend/pyproject.toml`, `backend/src/api/main.py`, `backend/tests/test_health.py`

- [ ] **Step 1:** `cd backend && uv init --python 3.12 && uv add fastapi 'uvicorn[standard]' sqlalchemy asyncpg pydantic-settings httpx && uv add --dev pytest pytest-asyncio ruff`
- [ ] **Step 2:** Write failing test `backend/tests/test_health.py`:

```python
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.main import app


@pytest.mark.asyncio
async def test_health() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

Run: `uv run pytest tests/test_health.py -v` → Expected: FAIL (no `src.api.main`).
- [ ] **Step 3:** Minimal `src/api/main.py` with FastAPI app + `/health`. Add `[tool.pytest.ini_options] asyncio_mode = "auto"` and `pythonpath = ["."]` to pyproject.
- [ ] **Step 4:** `uv run pytest -v` → PASS.
- [ ] **Step 5:** Add `backend/.gitignore` (`.venv/`, `__pycache__/`, `.env`), commit `feat: backend scaffold with health endpoint`.

### Task 2: Supabase project + local stack

**Files:**
- Create: `supabase/config.toml` (generated), `.env.example`

- [ ] **Step 1:** `supabase init` at repo root (installs `supabase/`). If CLI missing: `brew install supabase/tap/supabase`.
- [ ] **Step 2:** `supabase start` — verify local Postgres/Auth/Storage come up; note anon/service keys printed.
- [ ] **Step 3:** Create `.env.example` documenting `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `ANTHROPIC_API_KEY`. Real `.env` stays untracked.
- [ ] **Step 4:** Commit `chore: supabase local stack + env template`.

### Task 3: CrewAI flow scaffold

**Files:**
- Create: `backend/src/flows/` (scaffolded)

- [ ] **Step 1:** `uv add crewai` in backend. In a temp dir run `uvx crewai create flow landlord_compliance` (underscores, per CrewAI CLI requirement), then move `src/landlord_compliance/` content into `backend/src/flows/` keeping the crews/config layout; delete the generated example crew content we don't need but keep the structure. Do NOT hand-write scaffold files from scratch.
- [ ] **Step 2:** Smoke: `uv run python -c "from src.flows.main import *"` imports cleanly.
- [ ] **Step 3:** Commit `feat: crewai flow scaffold`.

### Task 4: Flutter app + theme package

**Files:**
- Create: `frontend/` (via `flutter create`), `frontend/lib/theme/tokens.dart`, `frontend/lib/theme/app_theme.dart`, `frontend/test/theme_test.dart`

- [ ] **Step 1:** `flutter create frontend --platforms web --org uk.hoque --project-name landlord_compliance` then `cd frontend && flutter pub add flutter_animate supabase_flutter go_router`.
- [ ] **Step 2:** Failing test `frontend/test/theme_test.dart`: builds `MaterialApp` with `AppTheme.light()`/`AppTheme.dark()`; asserts `useMaterial3` true and motion tokens exposed (`Motion.fast == Duration(milliseconds: 150)`, `Motion.standard == Duration(milliseconds: 250)`, `Motion.emphasized == Duration(milliseconds: 350)`).
- [ ] **Step 3:** Implement `tokens.dart` (seed colour, spacing scale, radii, `Motion` durations + curves) and `app_theme.dart` (`ColorScheme.fromSeed`, both brightnesses, type scale). Respect `MediaQuery.disableAnimations` via a `Motion.of(context)` helper returning `Duration.zero` when disabled.
- [ ] **Step 4:** `flutter test` → PASS. Commit `feat: flutter scaffold with M3 theme + motion tokens`.

---

## Phase 1 — Schema & RLS

### Task 5: Core schema migration

**Files:**
- Create: `supabase/migrations/0001_core.sql`
- Test: `backend/tests/db/test_schema.py`

- [ ] **Step 1:** Write `0001_core.sql` exactly per spec data model: `orgs`, `users` (mirror of auth.users with org_id), `entities` (incl. `tax_regime` enum `mtd_itsa|corporation_tax`, `quarter_basis` enum default `tax_year`, PRS fields nullable), `properties` (incl. `finance_cost_classification`, `epc_rating`, `epc_expiry`, `bedroom_count`), `property_ownership` (unique (property_id, entity_id), `ownership_percentage numeric(5,2)`), `tenancies`, `imports`, `transactions` (incl. `hmrc_category` enum with the 15 spec values, `status` enum `unclassified|proposed|confirmed|excluded`, `confidence numeric(3,2)`, `proposed_by`), `compliance_certificates` (type enum, `issue_date`, `expiry_date`, `certificate_ref`, `document_id`), `documents`, `mtd_quarters` (unique (entity_id, tax_year, quarter), one numeric column per HMRC category total, `version`, `generated_at`), `job_queue`, `audit_log`. Every table: `org_id uuid not null references orgs`, timestamps. Constraint: trigger enforcing `sum(ownership_percentage) = 100` per property on confirm-time validation is done in API (documented in SQL comment) — DB enforces `0 < pct <= 100`.
- [ ] **Step 2:** `supabase db reset` → applies cleanly.
- [ ] **Step 3:** Failing test `test_schema.py`: connects via `DATABASE_URL`, asserts all 13 tables exist and `transactions.hmrc_category` enum has exactly the 15 spec values (guards drift between SQL and Python enum).
- [ ] **Step 4:** PASS, commit `feat: core schema migration`.

### Task 6: RLS policies + cross-tenant test

**Files:**
- Create: `supabase/migrations/0002_rls.sql`
- Test: `backend/tests/db/test_rls.py`

- [ ] **Step 1:** `0002_rls.sql`: enable RLS on all tables; policy per table: `org_id = (select org_id from public.users where id = auth.uid())`; service role bypasses (worker/API use service connection but always filter by org_id in queries — belt and braces).
- [ ] **Step 2:** Failing test: create two orgs + two auth users via service role; as user A (anon key + JWT) insert a property; as user B select properties → must see zero rows; attempt update of A's row as B → 0 rows affected.
- [ ] **Step 3:** PASS, commit `feat: RLS policies with cross-tenant test`.

### Task 7: SQLAlchemy models + session

**Files:**
- Create: `backend/src/db/models.py`, `backend/src/db/session.py`, `backend/src/core/categories.py`
- Test: `backend/tests/db/test_models_roundtrip.py`

- [ ] **Step 1:** `src/core/categories.py` — single source of truth:

```python
"""HMRC category enum for MTD ITSA property submissions.

:seealso: spec §Data model; SA105 mapping validated 2026-07-28.
"""
from enum import StrEnum


class HmrcCategory(StrEnum):
    RENT_INCOME = "rent_income"
    OTHER_PROPERTY_INCOME = "other_property_income"
    RENT_PAID = "rent_paid"
    RATES_INSURANCE_GROUND = "rates_insurance_ground"
    REPAIRS_MAINTENANCE = "repairs_maintenance"
    FINANCE_COSTS_RESIDENTIAL = "finance_costs_residential"
    FINANCE_COSTS_NONRESIDENTIAL = "finance_costs_nonresidential"
    LEGAL_PROFESSIONAL = "legal_professional"
    SERVICE_COSTS = "service_costs"
    TRAVEL_VEHICLE = "travel_vehicle"
    OTHER_ALLOWABLE = "other_allowable"
    REPLACEMENT_DOMESTIC_ITEMS = "replacement_domestic_items"
    USE_OF_HOME_ALLOWANCE = "use_of_home_allowance"
    CAPITAL_EXPENSE = "capital_expense"
    PERSONAL_NON_BUSINESS = "personal_non_business"


INCOME_CATEGORIES = {HmrcCategory.RENT_INCOME, HmrcCategory.OTHER_PROPERTY_INCOME}
EXCLUDED_FROM_EXPORT = {HmrcCategory.CAPITAL_EXPENSE, HmrcCategory.PERSONAL_NON_BUSINESS}
```

- [ ] **Step 2:** Declarative models mirroring migration; async `session.py` from `DATABASE_URL`. Failing round-trip test: insert org → entity → property → ownership 100% → select back.
- [ ] **Step 3:** PASS, commit `feat: sqlalchemy models + hmrc category source of truth`.

---

## Phase 2 — CSV Parser (pure core logic)

### Task 8: Statement parser

**Files:**
- Create: `backend/src/core/parser.py`, `backend/tests/fixtures/statements/generic.csv`, `backend/tests/fixtures/statements/malformed.csv`
- Test: `backend/tests/core/test_parser.py`

**NOTE (spec open question 3):** real bank fixture files must be swapped in when Mahmud provides statement exports; start with a generic `Date,Description,Amount,Balance` format and a format-registry design so adding a bank = adding a `StatementFormat` entry + fixture.

- [ ] **Step 1:** Failing tests:

```python
def test_parses_generic_csv() -> None:
    lines = parse_statement(FIXTURES / "generic.csv")
    assert len(lines) == 4
    assert lines[0] == ParsedLine(
        date=date(2026, 7, 1), description="B&Q LUTON", amount=Decimal("-84.99")
    )

def test_malformed_row_fails_loudly() -> None:
    with pytest.raises(StatementParseError) as exc:
        parse_statement(FIXTURES / "malformed.csv")
    assert exc.value.row_number == 3          # exact failing row surfaced
    assert "unparseable date" in str(exc.value)

def test_unknown_format_fails_loudly() -> None:
    with pytest.raises(UnknownStatementFormatError):
        parse_statement(FIXTURES / "unknown_headers.csv")
```

- [ ] **Step 2:** FAIL → implement `parser.py`: `ParsedLine` (frozen dataclass), `StatementFormat` registry keyed by header signature, `parse_statement()` that never skips rows — first bad row raises with row number. → PASS.
- [ ] **Step 3:** Commit `feat: statement parser with loud failures`.

---

## Phase 3 — Ownership splits & quarters (pure core logic)

### Task 9: Ownership split maths

**Files:**
- Create: `backend/src/core/splits.py`
- Test: `backend/tests/core/test_splits.py`

- [ ] **Step 1:** Failing tests: 50/50 split of £1000.01 allocates £500.01/£500.00 (largest-remainder rounding, penny-exact — sum of shares MUST equal original); 100% single owner; percentages not summing to 100 → `InvalidOwnershipError`.
- [ ] **Step 2:** Implement `split_amount(amount: Decimal, shares: dict[UUID, Decimal]) -> dict[UUID, Decimal]`. → PASS, commit.

### Task 10: Tax-year quarters + cumulative totals

**Files:**
- Create: `backend/src/core/quarters.py`
- Test: `backend/tests/core/test_quarters.py`

- [ ] **Step 1:** Failing tests: `quarter_for(date(2026, 7, 5)) == (2026, 1)` and `quarter_for(date(2026, 7, 6)) == (2026, 2)` (UK tax-year quarters from 6 April); `cumulative_totals(confirmed_txns, entity_id, tax_year, quarter)` returns YTD per-category totals **from 6 April through quarter end**, ownership-weighted via `split_amount`, excluding `EXCLUDED_FROM_EXPORT` categories from allowable totals but reporting capital separately; Q2 totals ⊇ Q1 totals for same data (cumulative property test).
- [ ] **Step 2:** Implement; pure functions over passed-in data (no DB). → PASS, commit.

---

## Phase 4 — Categorisation flow (CrewAI)

### Task 11: Proposal models + flow

**Files:**
- Create: `backend/src/flows/categorise.py`
- Test: `backend/tests/flows/test_categorise_models.py`

- [ ] **Step 1:** Pydantic models:

```python
class LineProposal(BaseModel):
    line_index: int
    hmrc_category: HmrcCategory
    property_id: UUID | None
    confidence: float = Field(ge=0, le=1)
    rationale: str

class StatementProposals(BaseModel):
    proposals: list[LineProposal]
```

Failing test: `StatementProposals` rejects out-of-range confidence and unknown category (Pydantic validation is the contract the LLM is retried against).
- [ ] **Step 2:** Implement `CategoriseStatementFlow` (CrewAI `Flow` with typed state): input = parsed lines + org property list + up to 50 most recent confirmed transactions (few-shot); one step calls `Agent.kickoff(prompt, response_format=StatementProposals)`. Agent config in `src/flows/crews/config/` YAML per CrewAI convention: role "UK landlord bookkeeping specialist", explicit instruction that uncertain lines get low confidence and `PERSONAL_NON_BUSINESS` is for non-business lines, never a guess. LLM: `anthropic/claude-sonnet-5` (cost-effective for classification; model id in config, not code — verify the exact CrewAI/LiteLLM model string at implementation time against the claude-api skill/docs).
- [ ] **Step 3:** Unit test with mocked `Agent.kickoff` returning a fixture `StatementProposals`: flow maps proposals onto line indices, errors if the LLM returns indices out of range (loud). → PASS, commit.

### Task 12: Golden-set eval harness

**Files:**
- Create: `backend/evals/golden_set.jsonl` (seed with 20 synthetic labelled lines until Mahmud's real confirmed data exists), `backend/evals/run_eval.py`
- Test: `backend/tests/evals/test_eval_harness.py`

- [ ] **Step 1:** `run_eval.py`: runs the real flow against the golden set, prints per-category precision/recall + overall accuracy, exits non-zero below threshold (start 0.7; raise as the set grows). Harness unit-tested with a stubbed flow; the live eval is run manually/CI-nightly (`uv run python evals/run_eval.py`), not in the default test suite.
- [ ] **Step 2:** Commit `feat: categorisation golden-set eval harness`.

---

## Phase 5 — API

### Task 13: Auth dependency

**Files:**
- Create: `backend/src/api/auth.py`
- Test: `backend/tests/api/test_auth.py`

- [ ] **Step 1:** Failing tests: request without bearer → 401; with valid Supabase JWT (HS256, `SUPABASE_JWT_SECRET`) → dependency yields `(user_id, org_id)`; JWT for user with no org row → 403 (loud, not silent org-less access).
- [ ] **Step 2:** Implement, PASS, commit.

### Task 13b: Portfolio setup — entities, properties, ownership (foundational reference data)

Without this the delivered MVP cannot be used: every later task assumes orgs/entities/properties exist.

**Files:**
- Create: `backend/src/api/routers/portfolio.py`, `backend/scripts/seed_org.py`
- Test: `backend/tests/api/test_portfolio.py`

- [ ] **Step 1:** Failing tests: `POST/GET/PATCH /entities` (name, tax_regime, quarter_basis); `POST/GET/PATCH /properties` (address, finance_cost_classification, epc fields, bedroom_count); `PUT /properties/{id}/ownership` accepts a full list of `{entity_id, percentage}` replacing prior rows atomically — rejects with 422 if percentages don't sum to 100 (loud, names the sum it got); all scoped to caller's org.
- [ ] **Step 2:** Implement router + repository, PASS, commit `feat: portfolio setup endpoints`.
- [ ] **Step 3:** `scripts/seed_org.py`: idempotent CLI (`uv run python scripts/seed_org.py --email m.hoque@gmail.com --org "Hoque Portfolio"`) that creates the org, links the auth user, and prints next steps (add entities/properties via the UI or API). Unit-test the idempotency (second run = no duplicates). Commit.

### Task 14: Imports endpoint

**Files:**
- Create: `backend/src/api/routers/imports.py`
- Test: `backend/tests/api/test_imports.py`

- [ ] **Step 1:** Failing tests: `POST /imports` (multipart CSV + entity_id) stores file to Supabase Storage (`statements/{org_id}/...`), parses; on success creates `imports` row (status `parsed`) + `transactions` rows (status `unclassified`) + `job_queue` row (`type=categorise`); on `StatementParseError` creates `imports` row status `failed` with row-level error detail in response and DB — and NO transaction rows; `GET /imports` lists with status.
- [ ] **Step 2:** Implement (repository layer in `src/db/`), PASS, commit.

### Task 15: Review & confirm endpoints

**Files:**
- Create: `backend/src/api/routers/transactions.py`
- Test: `backend/tests/api/test_transactions.py`

- [ ] **Step 1:** Failing tests: `GET /transactions?import_id=&status=` returns lines with proposal fields; `POST /transactions/{id}/confirm` body `{hmrc_category, property_id}` — sets status `confirmed`, writes `audit_log` row (actor=user, before/after JSON); confirming with a property whose ownership doesn't sum to 100 → 422 with explanation; `POST /transactions/{id}/exclude` for personal lines; bulk `POST /transactions/confirm-batch` (all-or-nothing transaction).
- [ ] **Step 2:** Implement, PASS, commit.

### Task 16: Quarterly export endpoint

**Files:**
- Create: `backend/src/api/routers/exports.py`, `backend/src/core/export_pack.py`
- Test: `backend/tests/api/test_exports.py`, `backend/tests/core/test_export_pack.py`

- [ ] **Step 1 (BLOCKER CHECK — spec open question 1):** Before coding, verify aggregation level against HMRC Property Business API docs (https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/property-business-api/) — WebFetch the quarterly-submission schema; record the answer in the spec's open-questions section and proceed with per-entity aggregation (per-property detail as a supplementary sheet) if confirmed. Surface to Mahmud if the docs contradict the design.
- [ ] **Step 2:** Failing core tests: `build_export_pack(entity, tax_year, quarter, txns, ownerships)` → refuses (`ExportBlockedError` listing blocker transaction ids) if any txn in period is `unclassified`/`proposed`; produces CSV (one row per category, cumulative YTD) + per-property supplementary CSV; Ltd entities → `SimplePnlPack` instead (no mtd_quarters row).
- [ ] **Step 2b (spec §Error handling — decrease guard):** Failing test: exporting a quarter whose cumulative totals are LOWER in any category than the latest stored `mtd_quarters` row for an earlier-or-equal quarter of the same entity/tax-year raises `CumulativeDecreaseError` naming the category, prior total, and new total — catches transactions deleted/edited after a prior export. Test both the legitimate re-export (same quarter, higher totals → new version row, OK) and the decrease case (→ loud error; resolution is a human decision, not an override flag).
- [ ] **Step 3:** API tests: `POST /exports/quarter` creates/increments `mtd_quarters` version row, stores generated files as `documents`, returns download refs; PDF rendering = simple HTML→PDF (weasyprint) of the same numbers (snapshot test on HTML, not PDF bytes).
- [ ] **Step 4:** Implement, PASS, commit.

### Task 17: Certificates CRUD

**Files:**
- Create: `backend/src/api/routers/certificates.py`
- Test: `backend/tests/api/test_certificates.py`

- [ ] **Step 1:** Failing tests: CRUD for `compliance_certificates` incl. document upload ref; derived `status` in responses: `expired` (past), `expiring` (≤60 days), `valid`; list endpoint groups by property.
- [ ] **Step 2:** Implement, PASS, commit.

---

## Phase 6 — Worker

### Task 18: Job queue poller

**Files:**
- Create: `backend/src/worker/main.py`, `backend/src/worker/jobs.py`
- Test: `backend/tests/worker/test_poller.py`

- [ ] **Step 1:** Failing tests: poller claims queued jobs with `FOR UPDATE SKIP LOCKED`, marks `running`→`done`; a job whose handler raises marks `failed` with the exception string stored and NO retry loop (fail loudly, visible in UI); `categorise` handler: loads unclassified transactions for the import, runs `CategoriseStatementFlow` (mocked in test), writes proposals (status `proposed`, confidence, `proposed_by`=job id) + audit rows. **Failed-job visibility:** a failed `categorise` job also sets its import's status to `categorisation_failed` (add enum value in migration if not present), so `GET /imports` — and the imports screen (Task 20) — surface it; a stuck import must never look merely "pending".
- [ ] **Step 2:** Implement (`asyncio` loop, `poll_interval=2s`, graceful SIGTERM), PASS, commit.
- [ ] **Step 3:** Add `make dev` (or `justfile`): `supabase start`, API `uvicorn`, worker, `flutter run -d chrome`. Commit.

---

## Phase 7 — Flutter UI

**All UI tasks:** load the `impeccable` skill before layout/polish decisions; use theme tokens only (no inline colours/durations); every list/state change animates via `flutter_animate` with `Motion` tokens; widget tests per screen.

### Task 19: Auth + app shell

- Files: `frontend/lib/features/auth/`, `frontend/lib/app.dart`, `frontend/lib/main.dart`
- [ ] Supabase email/password sign-in; `go_router` guarded routes; app shell with nav rail (Dashboard, Imports, Review, Certificates); widget test: unauthenticated → login screen. Commit.

### Task 20: Upload & imports screen

- Files: `frontend/lib/features/upload/`
- [ ] File-pick CSV → `POST /imports`; list imports with status chips; **failed imports show the row-level error prominently** (spec: import failure UX matters more than format coverage); staggered list entrance (`flutter_animate`, `Motion.standard`). Widget test with mocked API client. Commit.

### Task 21: Review screen (the core UX)

- Files: `frontend/lib/features/review/`
- [ ] Transaction list for an import: category chip + property + confidence; lines below confidence threshold flagged "needs attention" (amber, subtle pulse once — not looping); tap → edit category/property (bottom sheet, enum-driven); multi-select → confirm batch; confirmed rows animate to settled state. Widget tests: proposal renders, low-confidence flag at <0.8, batch confirm calls API once. Commit.

### Task 22: Certificates + dashboard

- Files: `frontend/lib/features/certificates/`, `frontend/lib/features/dashboard/`, `frontend/lib/features/portfolio/`
- [ ] Certificates: per-property table, add/edit form, expiry status colours (RAG via theme tokens). Dashboard: cards — unreviewed transaction count, next quarter deadline (5 Aug/5 Nov/5 Feb/5 May submission windows), expiring certificates; export button → `POST /exports/quarter` → download. Widget tests. Commit.
- [ ] Portfolio settings screen (uses Task 13b endpoints): list/add/edit entities and properties; ownership editor with live sum-to-100 validation mirroring the API rule. Widget test: ownership form blocks save at ≠100%. Commit.

---

## Phase 8 — End-to-end & wrap-up

### Task 23: E2E smoke

- Test: `backend/tests/e2e/test_smoke.py`
- [ ] Against local supabase + API + worker (real flow with **mocked LLM call only**): upload fixture CSV → poll until proposals exist → confirm all → export quarter → assert CSV totals match hand-computed fixture numbers penny-exact. Commit.

### Task 24: Docs & handoff

- [ ] `README.md`: setup (supabase start, .env, make dev), architecture sketch, how to add a bank format, how to run the golden eval. Update spec open-question 1 with the verified HMRC answer. Commit.
- [ ] **REQUIRED SUB-SKILL** at finish: superpowers:verification-before-completion — full `uv run pytest`, `flutter test`, `ruff check`, show outputs.

---

## Out of scope (do not build, per spec)

Section 13 flow, compliance-scan agent, email alerts, PDF statement parsing, deposits/PRS tables, HMRC direct filing, letting-agent reconciliation, HMO units, rent schedules, tenant contacts, chat surface.

## Known inputs still needed from Mahmud

1. Bank list (personal + Ltd accounts) → real parser fixtures (Task 8 swaps generic fixtures).
2. Anthropic API key for the worker env.
3. Supabase cloud project (local-only until iteration 1 works; then `supabase link` + `db push`).
