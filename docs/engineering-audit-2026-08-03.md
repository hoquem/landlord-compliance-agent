# Engineering Audit — landlord-compliance-agent

**Date:** 2026-08-03
**Auditor:** Claude Opus 5, `audit` mode of the `build-clean` skill
**Rubric:** `~/.claude/skills/build-clean/references/audit-rubric.md` (Hoque 2026, §3.3.3)
**Scope:** `backend/src/`, `backend/scripts/`, `backend/tests/`, `supabase/migrations/`. Exclusions in *Not Audited*.
**Repo state:** branch `iteration-1-mvp`, HEAD `f296471`, 236 tests green, 15 of 26 planned tasks complete.

Every score carries `file:line` evidence. Scores without evidence cannot be re-scored later, so they cannot show progress.

---

## Scorecard

| # | Dimension | Score | One-line basis |
|---|---|---|---|
| 1 | Domain language | 3/5 | Names are genuinely domain-sourced, but there is no glossary and `entity` already means two things |
| 2 | Boundaries | 4/5 | `src/core/` imports zero infrastructure — verified, not assumed |
| 3 | Aggregates | **2/5** | 13 peer tables, no roots; the money invariant is enforced in two places and owned by neither |
| 4 | Test coverage | 4/5 | Guards are mutation-proven, which is stronger than coverage — but coverage itself has never been measured |
| 5 | Testability | 4/5 | 100 tests run with no database at all; the integration subset is documented |
| 6 | Module depth | 4/5 | `get_owned_or_404` and `parse_statement` are textbook deep interfaces |
| 7 | Change amplification | 3/5 | Measured from git history, not estimated |
| 8 | Error design | 4/5 | Not one bare or broad `except` in `src/` or `scripts/` |

**Average: 3.5/5**

For calibration: the paper's own case study (`claude-telegram-bridge`) opened at 1.75/5. This repo is not that codebase and the comparison is not evidence of anything — it is here only so the number is read on the right scale.

---

### 1. Domain language — 3/5

**Good.** `src/core/categories.py:9` — `HmrcCategory` is the term HMRC itself uses, one enum member per real category, no translation layer. `finance_cost_classification`, `quarter_basis`, `tax_regime`, `cumulative_totals` all name things a tax accountant would recognise unprompted. This is not accidental; the spec was written in domain language first.

**Bad.** There is no `docs/domain/` and no glossary anywhere in the repo. Consistency therefore rests on whoever wrote each file, which is exactly the rubric's definition of 3.

**The live collision.** `entity` means two different things in adjacent code:
- `src/db/models.py:136` — `class Entity`, an *ownership entity*: the person or Ltd company that files a tax return.
- `src/api/scoping.py:112` — the `what` parameter, passed `"entity"` and `"property"` alike, meaning *any addressable resource*.

A second collision is more dangerous because it is silent: `org` (`models.py:102`) is the **tenant boundary**, `entity` (`models.py:136`) is the **tax filer**. One org holds many entities. A reader who conflates them writes a cross-tenant bug, and — per `src/api/scoping.py:3-8` — row-level security is inert on every API path, so nothing but discipline catches it.

**This has already cost the project once.** Task 13b Step 4a was a fix round caused by the third collision: `property` has a *money* face (`finance_cost_classification`, ownership splits) and a *compliance* face (`epc_expiry`, `licensing_flag`). The plan enumerated only the money cases, the router shipped writing no audit rows for compliance changes, and the gap was found in review rather than in design. That is the paper's §3.2.1 bounded-context example occurring in this codebase before the framework was applied to it.

### 2. Boundaries — 4/5

**Good, and measured.** Every import in `src/core/` is either stdlib or another `src.core` module — no SQLAlchemy, no FastAPI, no Supabase, no I/O. The pure domain layer is real, not aspirational, which is why `tests/core` runs with no database.

**Bad.** `src/api/routers/portfolio.py:650-653` re-implements the sum-to-100 rule that `src/core/splits.py:113` (`_validate_shares`) owns. This is a *deliberate* duplication — the router must answer 422 and name the offending entities rather than raise `InvalidOwnershipError` — and drift is caught by `test_an_api_accepted_ownership_set_is_usable_by_split_amount`. It is still a domain rule living in an adapter, which is what holds this at 4 rather than 5.

`portfolio.py` at 717 lines carries request schemas, validation, HTTP handlers and persistence in one file.

### 3. Aggregates — 2/5 — **the weakest dimension**

**Bad.** `src/db/models.py` declares 13 tables as flat peers of `Base` (`models.py:69`). There is no aggregate root anywhere. `_uuid_pk()` (`:79`) and `_org_id_fk()` (`:84`) are column *factories*, not a mixin — so the models do not even share a structural base, which is why `src/api/scoping.py:53` had to introduce an `OrgScoped` Protocol to type against.

The one genuinely money-critical invariant — *a property's ownership shares sum to exactly 100* — is owned by nothing. It is enforced twice, independently (`src/core/splits.py:113` and `src/api/routers/portfolio.py:650`), and `supabase/migrations/0001_core.sql:228-234` documents that it is deliberately **not** a database constraint, because ownership is edited row-by-row through transiently-invalid totals. So the invariant exists only where someone remembered to check it.

**Good.** The `PUT /properties/{id}/ownership` handler is a de-facto aggregate operation: it takes the whole set, validates all of it, and only then deletes and inserts, inside one transaction. The behaviour is right. What is missing is a *root* that makes it the only way in — nothing stops a future Task 14–17 router from writing `property_ownership` rows directly.

**Known and accepted:** concurrent replacements are not serialized (recorded in the plan, with the one-line `.with_for_update()` fix). Both race outcomes are loud, so money is never silently corrupted.

### 4. Test coverage — 4/5

**Good, and unusually so.** 236 tests. More importantly the guards are *mutation-proven* rather than merely executed — the project's standing practice is to break the code a test defends and confirm that test dies and no other does. Recorded instances: making `get_owned_or_404` ignore `auth.org_id` fails exactly 8 named tests; rewording the cross-org 404 fails exactly 4; emptying `PropertyUpdate._NOT_NULLABLE` turns 6 tests from 422 to `IntegrityError`. That is a stronger claim than any coverage percentage.

**Bad.** Line coverage has never actually been measured — there is no `pytest-cov` in `pyproject.toml` and no coverage number anywhere in the repo. The rubric's 5 requires high coverage *and* mutation proof. The second half is met; the first is unknown, and unknown is not the same as high.

### 5. Testability — 4/5

**Good, and verified today.** `pytest tests/core tests/flows tests/evals` → **100 passed in 2.8s with no environment and no database**. The pure core needs no scaffolding at all. The integration subset (`tests/api`, `tests/db`) is documented and needs only the local Supabase stack — no mocking of Supabase, no fake HTTP layer; `tests/api` drives the real mounted app with real Supabase-shaped JWTs.

**Bad.** Without `DATABASE_URL`, collection *aborts* rather than skipping, so a bare `pytest` runs **zero** tests. That is correct fail-loudly behaviour but a live CI hazard; it is already pinned to Task 24, which is why it costs a point rather than two.

### 6. Module depth — 4/5

**Good.** `src/api/scoping.py:90` — `get_owned_or_404(session, model, resource_id, auth, *, what)` hides the org filter, the 404-vs-403 decision and the existence-oracle defence behind one call. It takes the whole `auth` context rather than a bare `org_id` specifically so that *the parameter which must be right is the one a caller cannot omit or mistype* (`scoping.py:14-18`). That is information hiding used as a safety mechanism, not decoration.

Also deep: `parse_statement(path) -> list[ParsedLine]` (`src/core/parser.py:164`) conceals format detection, per-bank row parsing, BOM handling and physical row numbering behind one argument. `split_amount(amount, shares)` (`src/core/splits.py:40`) conceals largest-remainder apportionment behind two.

**Bad.** `portfolio.py` is 717 lines and growing; its request schemas restate column knowledge that `models.py` already holds, so a nullability change touches both (this audit's own Step 5 did exactly that).

### 7. Change amplification — 3/5

**Measured from `git log`, not estimated.**

| Conceptual change | Files touched |
|---|---|
| Add a bank statement format | 1 source (`parser.py`: a `parse_row` + a `_FORMATS` entry) + fixture + test |
| Add an org-scoped router | router + `src/api/main.py` registration + tests (+ copy the route-table auth parametrization) |
| Change a column's nullability | `models.py` + `portfolio.py` schema + migration + tests |

`src/api/main.py` has been edited 3 times, tracking router additions — the registration seam is real but small. The parser's docstring claims adding a bank needs nothing else to change, and that claim is **true** as written.

### 8. Error design — 4/5

**Good.** Zero `except:` and zero `except Exception` across `src/` and `scripts/` — verified by grep, not by impression. The parser raises `StatementParseError` carrying the physical row number and never skips a row (`src/core/parser.py:41`). Auth distinguishes 401 from 403 deliberately and its one `except` is narrowed to `ValueError` with a tripwire test watching the upstream assumption. `CATEGORISER_MODEL` unset fails at startup rather than defaulting to a provider, so bank data cannot reach an LLM nobody chose.

One genuine instance of *defining an error out of existence*: `not_found` (`scoping.py:70`) makes "missing" and "someone else's" indistinguishable **by construction**, so there is no 403 branch left for a future router to get wrong.

**Bad.** That instance is the only one; the practice is not systematic, and the rubric's 5 asks for it as a habit.

---

## Top 3 Fixes — ranked by score × change frequency

Ranking uses `(5 − score) × edits`, so a weak dimension in cold code loses to a middling one in hot code.

### 1. Give the ownership set an aggregate root — score 2/5, hottest file in the repo

`src/api/routers/portfolio.py` has 5 edits, the most of any source file, and `tests/api/test_portfolio.py` has 6, the most of any file at all. The money invariant is enforced in two places and owned by neither.

**Why highest-leverage:** Tasks 14–17 add four more routers that will touch transactions and exports derived from these same shares. Every one is an opportunity to write `property_ownership` without going through the validating path. The invariant is not merely untidy — `src/core/splits.py` apportions *every penny of income and expense* by it.

**Effort:** M. **First concrete step:** extract `PropertyOwnershipSet` into `src/core/` as the single constructor that can produce a valid share set — non-empty, each share > 0, summing to exactly 100 — and have both `splits.py` and the router obtain sets only through it. The router keeps its own 422 message shaping; what moves is the *rule*, not the response.

### 2. Write the glossary before Task 14 — score 3/5, affects every file

**Why:** the `entity` / `org` / `property` collisions are cheap to fix now and expensive after four more routers copy them. `portfolio.py` is explicitly designated the template Tasks 14–17 will copy.

**Effort:** S. **First concrete step:** `docs/domain/` with two context files — money and compliance — splitting `property` at the seam that already caused Step 4a. Binding on new code only; boy-scout renames elsewhere.

### 3. Measure coverage, so dimension 4 stops being an estimate — score 4/5, cheap

**Why:** it is the only dimension scored on absent data. Mutation proof is the stronger signal and it is already there; the number just closes the gap, and it will also show whether `src/flows/` (370 lines, the least-tested area) is a hole.

**Effort:** S. **First concrete step:** add `pytest-cov`, run against the env-free subset and the full suite separately, and record both — the difference is itself informative.

---

## Incremental roadmap

Strangler and boy-scout only. **No rewrite is proposed anywhere in this document**, and none should be inferred: the tests are this repo's principal asset and a rewrite discards the domain knowledge that produced them.

1. **Now, before Task 14:** glossary (fix 2). Task 14 is the first router to copy `portfolio.py`, so this is the last cheap moment.
2. **During Tasks 14–17:** ownership aggregate (fix 1), introduced beside the existing path — the router keeps working while call sites migrate. Boy-scout renames toward the glossary in files being edited anyway.
3. **With Task 24 (CI):** coverage measurement (fix 3), alongside the already-pinned env-free/integration split.
4. **Not before Phase 7:** frontend dimensions are unscorable until there is a frontend.

---

## Not Audited

No silent gaps. The following were **not** scored, and their absence from the scorecard is not a pass:

- **`frontend/lib/`** — 3 Dart files (`main.dart`, `theme/tokens.dart`, `theme/app_theme.dart`) and 1 test. Phase 7 (Tasks 19–22) has not started; there is no application code to score. Dimensions 1, 3, 6 and 7 would all be meaningless here today.
- **`src/flows/`** (370 lines, `categorise.py`) — **read only shallowly.** It is the product's core agent and it was not given the same line-by-line reading as the API and core layers. Its scores are folded into the averages above on partial evidence, which is a real weakness of this audit. It should be audited on its own before Task 18 (the worker) builds on it.
- **`backend/evals/`** — `run_eval.py` (582 lines) and the golden set. Not scored; it is measurement tooling rather than product code, but it is large enough to deserve its own pass.
- **`src/flows/crews/placeholder_crew/`, `src/flows/tools/custom_tool.py`** — CrewAI scaffold remnants, not written by this project.
- **`supabase/migrations/`** — read for evidence (cited above) but not scored as a dimension; SQL schema quality needs a different rubric than the eight here.
- **Generated/vendored:** `.venv/`, `frontend/build/`, `__pycache__/`, `uv.lock`, `pubspec.lock`.

**Method caveat.** Scores 1, 2, 6 and 8 rest on evidence I ran and reproduced (import graphs, `except` greps, the mutation results, the env-free test run). Scores 3, 4, 5 and 7 rest partly on the project's own recorded history in `docs/planning/progress.md` — which is unusually detailed and has been reliable, but is not the same as independent verification.
