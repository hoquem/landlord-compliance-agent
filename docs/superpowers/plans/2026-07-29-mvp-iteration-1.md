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

- [ ] **Step 1:** `cd backend && uv init --python 3.12 && uv add fastapi 'uvicorn[standard]' sqlalchemy asyncpg pydantic-settings httpx && uv add --dev pytest pytest-asyncio ruff`. Later tasks add their own deps as needed (`supabase` storage client in Task 14, `weasyprint` in Task 16, `crewai` in Task 3) — add at point of use, not up front.
- [ ] **Step 2:** Write failing test `backend/tests/test_health.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

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

- [ ] **Step 1:** Write `0001_core.sql` exactly per spec data model: `orgs`, `users` (mirror of auth.users with org_id), `entities` (incl. `tax_regime` enum `mtd_itsa|corporation_tax`, `quarter_basis` enum default `tax_year`, PRS fields nullable), `properties` (incl. `finance_cost_classification`, `epc_rating`, `epc_expiry`, `bedroom_count`, `licensing_flag`), `property_ownership` (unique (property_id, entity_id), `ownership_percentage numeric(5,2)`), `tenancies` (schema-only in MVP — no API/UI until iteration 2's Section 13 work), `imports`, `transactions` (incl. `hmrc_category` enum with the 15 spec values, `status` enum `unclassified|proposed|confirmed|excluded`, `confidence numeric(3,2)`, `proposed_by`), `compliance_certificates` (type enum, `issue_date`, `expiry_date`, `certificate_ref`, `document_id`), `documents`, `mtd_quarters` (unique (entity_id, tax_year, quarter), one numeric column per HMRC category total, `version`, `generated_at`), `job_queue`, `audit_log`. Every table: `org_id uuid not null references orgs`, timestamps. Constraint: trigger enforcing `sum(ownership_percentage) = 100` per property on confirm-time validation is done in API (documented in SQL comment) — DB enforces `0 < pct <= 100`.
- [ ] **Step 2:** `supabase db reset` → applies cleanly.
- [ ] **Step 3:** Failing test `test_schema.py`: connects via `DATABASE_URL`, asserts all 13 tables exist and `transactions.hmrc_category` enum has exactly the 15 spec values (guards drift between SQL and Python enum).
- [ ] **Step 4:** PASS, commit `feat: core schema migration`.

### Task 6: RLS policies + cross-tenant test

**Files:**
- Create: `supabase/migrations/0002_rls.sql`
- Test: `backend/tests/db/test_rls.py`

- [ ] **Step 1:** `0002_rls.sql`: enable RLS on all tables; policy per table: `org_id = (select org_id from public.users where id = auth.uid())`; service role bypasses (worker/API use service connection but always filter by org_id in queries — belt and braces).
- [ ] **Step 1b (from Task 5 review — service-role writes bypass RLS, so constrain at the DB):** add `unique(id, org_id)` on parent tables and composite FKs (`(property_id, org_id)`, `(entity_id, org_id)`, `(import_id, org_id)` etc. referencing them) so a service-role write can never stitch rows across orgs. Also pin `search_path` on `set_updated_at()` (`set search_path = ''` style) to clear the Supabase `function_search_path_mutable` advisor lint. Test: inserting a property_ownership row whose property and entity belong to different orgs fails with an FK violation.
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
- Create: `backend/src/core/parser.py`, `backend/tests/fixtures/statements/generic.csv`, `backend/tests/fixtures/statements/malformed.csv`, `backend/tests/fixtures/statements/unknown_headers.csv`
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
- [ ] **Step 1a (from Task 5 review):** Failing tests for `format_tax_year(2026) == "2026-27"` (and century boundary `format_tax_year(2099) == "2099-00"`) — the single source of the DB's `mtd_quarters.tax_year` string format, which now carries a CHECK constraint (`^\d{4}-\d{2}$`).
- [ ] **Step 1b:** Also failing tests: `next_update_deadline(date)` returns the next statutory quarterly-update deadline — 7 Aug, 7 Nov, 7 Feb, 7 May (the 7th of the month following quarter-end); pin all four boundaries. Transactions with `property_id` null (e.g. `use_of_home_allowance`, `travel_vehicle`) attribute 100% to `transactions.entity_id` — no ownership split applies (spec: entity_id determines whose ledger unallocated lines sit on).
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
- [ ] **Step 2:** Implement `CategoriseStatementFlow` (CrewAI `Flow` with typed state): input = parsed lines + org property list + up to 50 most recent confirmed transactions (few-shot); one step calls `Agent.kickoff(prompt, response_format=StatementProposals)`. Agent config in `src/flows/crews/config/` YAML per CrewAI convention: role "UK landlord bookkeeping specialist", explicit instruction that uncertain lines get low confidence and `PERSONAL_NON_BUSINESS` is for non-business lines, never a guess. **LLM (decision 2026-07-29): fully env-driven — `CATEGORISER_MODEL` env var (LiteLLM id, e.g. `ollama/glm-4.5-air` or a hosted model) + optional `OLLAMA_BASE_URL`; add both to `.env.example`, `ANTHROPIC_API_KEY` becomes optional.** Mahmud intends a local open-weight model via Ollama (Kimi/GLM-class) for bank-data privacy; the flow must not hard-code any provider. Fail loudly at flow start if `CATEGORISER_MODEL` is unset. Verify the exact LiteLLM model string against whichever provider is configured at implementation time.
- [ ] **Step 3:** Unit test with mocked `Agent.kickoff` returning a fixture `StatementProposals`: flow maps proposals onto line indices, errors if the LLM returns indices out of range (loud). → PASS, commit.

### Task 12: Golden-set eval harness

**Files:**
- Create: `backend/evals/golden_set.jsonl` (seed with 20 synthetic labelled lines until Mahmud's real confirmed data exists), `backend/evals/run_eval.py`
- Test: `backend/tests/evals/test_eval_harness.py`

- [ ] **Step 1:** `run_eval.py`: runs the real flow against the golden set, prints per-category precision/recall + overall accuracy, exits non-zero below threshold (start 0.7; raise as the set grows). **Accepts `--model <litellm-id>` (default: `CATEGORISER_MODEL` env) so the harness doubles as a model-selection tool — head-to-head runs of local Ollama candidates (GLM-class) vs hosted models on the same golden set decide the production model empirically.** Harness unit-tested with a stubbed flow; the live eval is run manually/CI-nightly (`uv run python evals/run_eval.py`), not in the default test suite.
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
- [ ] **Step 2:** Implement router + repository, PASS, commit `feat: portfolio setup endpoints`. Ownership and entity mutations write `audit_log` rows (ownership percentages directly change money computation — spec: audit every state change to money data). Certificates CRUD (Task 17) likewise writes audit rows.
- [ ] **Step 3:** `scripts/seed_org.py`: idempotent CLI (`uv run python scripts/seed_org.py --email m.hoque@gmail.com --org "Hoque Portfolio"`) that creates the org and links the auth user (created beforehand via Supabase Studio or `supabase auth` CLI — Task 19 builds sign-in only, not sign-up), and prints next steps (add entities/properties via the UI or API). Unit-test the idempotency (second run = no duplicates). Commit.

### Task 14: Imports endpoint

**Files:**
- Create: `backend/src/api/routers/imports.py`
- Test: `backend/tests/api/test_imports.py`

- [ ] **Step 0 (from Task 6 review — spec §Security "storage buckets namespaced per org"):** creating the statements bucket requires `storage.objects` RLS policies enforcing the `{org_id}/` path prefix (the `documents` table cannot enforce path isolation — `storage_path` is free text). Add policies restricting authenticated access to paths starting with their org id; service connection used by the API bypasses. Test: authenticated user cannot read an object under another org's prefix.
- [ ] **Step 1:** Failing tests: `POST /imports` (multipart CSV + entity_id) stores file to Supabase Storage (`statements/{org_id}/...`), parses; on success creates `imports` row (status `parsed`) + `transactions` rows (status `unclassified`) + `job_queue` row (`type=categorise`); on `StatementParseError` creates `imports` row status `failed` with row-level error detail in response and DB — and NO transaction rows; `GET /imports` lists with status.
- [ ] **Step 1b (sign convention — from Task 10 review):** the parser emits SIGNED amounts (negative = money out); the DB stores MAGNITUDE + `direction`. Task 14 MUST store `amount = abs(ParsedLine.amount)` and `direction = 'out' if line.amount < 0 else 'in'`. Failing test: a -84.99 parsed line lands as amount 84.99 / direction 'out'. Manual claims (null import_id, e.g. use_of_home_allowance) get `direction = 'out'`.
- [ ] **Step 2:** Implement (repository layer in `src/db/`), PASS, commit.

### Task 15: Review & confirm endpoints

**Files:**
- Create: `backend/src/api/routers/transactions.py`
- Test: `backend/tests/api/test_transactions.py`

- [ ] **Step 1:** Failing tests: `GET /transactions?import_id=&status=` returns lines with proposal fields; `POST /transactions/{id}/confirm` body `{hmrc_category, property_id}` — sets status `confirmed`, writes `audit_log` row (actor=user, before/after JSON); confirming with a property whose ownership doesn't sum to 100 → 422 with explanation; `POST /transactions/{id}/exclude` for personal lines (writes an `audit_log` row, same as confirm); bulk `POST /transactions/confirm-batch` (all-or-nothing transaction).
- [ ] **Step 2:** Implement, PASS, commit.

### Task 16: Quarterly export endpoint

**Files:**
- Create: `backend/src/api/routers/exports.py`, `backend/src/core/export_pack.py`
- Test: `backend/tests/api/test_exports.py`, `backend/tests/core/test_export_pack.py`

- [ ] **Step 1 (BLOCKER CHECK — spec open question 1):** Before coding, verify aggregation level against HMRC Property Business API docs (https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/property-business-api/) — WebFetch the quarterly-submission schema; record the answer in the spec's open-questions section and proceed with per-entity aggregation (per-property detail as a supplementary sheet) if confirmed. Surface to Mahmud if the docs contradict the design. If the developer hub is unreachable at implementation time, proceed with per-entity aggregation (the spec-safe default) and leave the open question flagged for Mahmud rather than dead-ending the task.
- [ ] **Step 2:** Failing core tests: `build_export_pack(entity, tax_year, quarter, txns, ownerships)` → refuses (`ExportBlockedError` listing blocker transaction ids) if any txn in period is `unclassified`/`proposed`; produces CSV (one row per category, cumulative YTD) + per-property supplementary CSV; Ltd entities → `SimplePnlPack` instead (no mtd_quarters row). **Sign rule (from Task 10 review): when building `TxnForTotals` from a DB row, NEVER copy `amount` straight through — apply `signed = magnitude if (category in INCOME_CATEGORIES) == (direction == 'in') else -magnitude`. Failing test: a contractor-refund row (repairs, direction 'in') enters totals negative. Also needed: int-quarter ↔ `'Q1'..'Q4'` enum mapping helper for the mtd_quarters key.**
- [ ] **Step 2b (spec §Error handling — decrease guard; AMENDED after Task 10 review found the original spec self-contradictory):** a legitimate in-window refund (e.g. contractor refund of a Q1 repair landing in Q2) can lawfully make a cumulative category total DECREASE — the guard must not block it. The discriminator is **recomputation of the earlier quarter from current data**: on export of quarter N, recompute quarter N-1's totals (and earlier exported quarters) from today's confirmed transactions and compare against their latest stored `mtd_quarters` rows. If stored == recomputed for all earlier quarters, history is intact and any decrease in quarter N is refund-legitimate → allowed (new version row). If any earlier quarter's recomputation differs from what was exported, history was deleted/edited after export → `CumulativeDecreaseError` naming the quarter, category, stored total, and recomputed total; resolution is a human decision, not an override flag. Failing tests: (a) refund-decrease with intact history exports fine, (b) deleted-transaction history change raises, (c) legitimate re-export same quarter higher totals → new version row.
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
- [ ] **(Auth decision 2026-07-29: Google OAuth only — no email/password UI.)** Login screen is a single "Continue with Google" button calling `supabase.auth.signInWithOAuth(OAuthProvider.google)`; `[auth.external.google]` enabled in `supabase/config.toml` reading `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` from env (add to `.env.example`). **Input needed from Mahmud: Google Cloud OAuth client credentials** (redirect URI `http://127.0.0.1:54321/auth/v1/callback` for local). `go_router` guarded routes; app shell with nav rail (Dashboard, Imports, Review, Certificates); widget tests: unauthenticated → login screen shows the Google button (OAuth flow itself mocked — no live Google in tests). RLS-test plumbing keeps service-side password-grant users; that's internal only. Commit.

### Task 20: Upload & imports screen

- Files: `frontend/lib/features/upload/`
- [ ] File-pick CSV → `POST /imports`; list imports with status chips; **failed imports show the row-level error prominently** (spec: import failure UX matters more than format coverage); staggered list entrance (`flutter_animate`, `Motion.standard`). Widget test with mocked API client. Commit.

### Task 21: Review screen (the core UX)

- Files: `frontend/lib/features/review/`
- [ ] Transaction list for an import: category chip + property + confidence; lines below confidence threshold flagged "needs attention" (amber, subtle pulse once — not looping); tap → edit category/property (bottom sheet, enum-driven); multi-select → confirm batch; confirmed rows animate to settled state. Widget tests: proposal renders, low-confidence flag at <0.8, batch confirm calls API once. Commit.

### Task 22: Certificates + dashboard

- Files: `frontend/lib/features/certificates/`, `frontend/lib/features/dashboard/`, `frontend/lib/features/portfolio/`
- [ ] **Prerequisite (from Task 4 review):** add RAG/status colours as a `ThemeExtension<StatusColors>` registered per-brightness inside `AppTheme._build()` — NOT bare `Color` constants in tokens.dart (bare constants have no light/dark variant). Screens read `Theme.of(context).extension<StatusColors>()`.
- [ ] Certificates: per-property table, add/edit form, expiry status colours (RAG via theme tokens). Dashboard: cards — unreviewed transaction count, next quarterly-update deadline (**7 Aug / 7 Nov / 7 Feb / 7 May** — the 7th of the month after quarter-end; quarter-ends are 5 Jul/5 Oct/5 Jan/5 Apr), expiring certificates; export button → `POST /exports/quarter` → download. Deadline computation lives in `backend/src/core/quarters.py` (`next_update_deadline(today) -> date`) with unit tests pinning all four statutory dates — the frontend renders it, never computes it. Widget tests. Commit.
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
