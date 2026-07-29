# Progress Log

## Session 2026-07-29 (scoping continued)
- Design v2 approved by Mahmud (Flutter stack, validated data model, lean MVP)
- Repo created: /Users/mahmud/projects/landlord-compliance-agent (git, commit 681bc19)
- Spec written: docs/superpowers/specs/2026-07-28-landlord-compliance-agent-design.md
- Spec-reviewer subagent dispatched — awaiting result
- Spec review: Approved; 4 advisory clarifications folded in (commit 3e49c38)
- User approved spec ("continue") + requested design system with motion → M3 Expressive + flutter_animate added to spec
- Implementation plan written: docs/superpowers/plans/2026-07-29-mvp-iteration-1.md (24 tasks, 8 phases)
- Plan review round 1: 2 issues (portfolio setup path, cumulative decrease guard) — fixed
- Plan review round 2: 1 factual issue (MTD deadlines are 7th not 5th) + advisories — fixed
- Plan review round 3 dispatched — awaiting result
- Next: on approval → offer execution choice (subagent-driven vs inline)
- STILL NEEDED from Mahmud: bank list for parser fixtures; Anthropic API key for worker; Supabase cloud project later

## Execution (subagent-driven, branch iteration-1-mvp)
- Phase 0 COMPLETE (Tasks 1-4): backend scaffold, supabase local stack (live+verified), crewai scaffold, flutter+M3 theme (12/12 tests)
- Review loop caught: stray uv init files, ruff failure (plan snippet fixed too), Motion.of MediaQuery over-subscription, untested seed derivation, global gitignore lib/ hazard (neutralised repo-side)
- Incidents: Docker started by controller; reviewer touch/rm .env (nothing lost, .env recreated with local defaults)
- Task 5 COMPLETE: schema (13 tables), opus spec review found tax_year format + import_id nullability issues (fixed); quality review found ON DELETE CASCADE gap + craft items (fixed). 8 db tests.
- Task 6: RLS + composite FKs + search_path pin. Adversarial opus review: isolation held on 15 attack paths; 4 least-privilege defects + 2 test gaps found → fixed. Implementer sandbox hit classifier outage; controller verified+committed its uncommitted work directly (anon 401-vs-403 assertion corrected against live output). 18 tests green. Quality review in flight.
- Storage-bucket RLS requirement recorded as Task 14 Step 0.
- User directive: continue autonomously, check in at phase ends.
- Task 6 COMPLETE: final hardening round (catalog exact-set guards, privilege pins, reusable teardown, conftest extraction) — 19 tests.
- AUTH DECISION (user): Google OAuth only, no email/password UI; spec + Task 19 updated; needs Google Cloud OAuth client from Mahmud.
- Task 7 COMPLETE: models for 13 tables, HmrcCategory source of truth, 3-way enum drift guard (proven by mutation), session factory; quality approved; reviewer's stray ruff-format changes reverted (repo enforces check, not format). 24 tests green.
- PHASE 1 COMPLETE. Task 8 (parser) dispatched.
- Task 8 COMPLETE: parser, format registry, BOM + physical-row-number bugs caught pre-commit; adversarial review clean (silent-wrong-data probes all fail loudly). 37 tests.
- Task 9 COMPLETE: split_amount largest-remainder; 20k-trial stress clean; sub-penny guard proven necessary; loud postcondition added. 50 tests.
- Task 10 COMPLETE: quarters/format_tax_year/deadlines/cumulative_totals; opus review found (1) refund-vs-decrease-guard SPEC CONTRADICTION → Task 16 amended (recompute-earlier-quarters discriminator), (2) three-way sign-convention seam → derivation rule (positive iff income==in) pinned in quarters.py + Tasks 14/16, (3) doc overstatements fixed. 86 tests.
- LLM DECISION (user): local open-weight via Ollama (Kimi/GLM); CATEGORISER_MODEL env-driven; eval harness doubles as model selector; Anthropic key optional.
- PHASES 2-3 COMPLETE. Task 11 (categorisation flow) dispatched.
- Task 11 COMPLETE (commit 8cd3eb2): categorisation flow, env-driven model; empty-lines guard noted for worker (Task 18, commit 2df3264).
- Task 12 COMPLETE (commit d81889d): golden-set eval harness; CrewAI telemetry disabled for bank-data privacy (3c9cb28).
- PHASE 4 COMPLETE.

## Session 2026-07-29 (later) — Phase 5 (API)
- Task 13 (auth dependency) dispatched; first attempt died on transient 529 (clean tree, nothing lost), re-dispatched.
- Task 16 Step 1 BLOCKER CHECK resolved early (out of order, read-only research): HMRC per-entity aggregation CONFIRMED, cumulative-YTD confirmed. Spec open question 1 closed; plan Step 1 ticked.
  - Flagged non-blocking gaps for iteration 2 (direct filing): no `residentialFinancialCostsCarriedForward` equivalent in our 15-category enum; no taxDeducted/lease-premium/rent-a-room categories; `ukNonFhlProperty` field paths may have changed post-FHL-abolition.
  - Sourcing caveat recorded: aggregation answer is service-guide wording + third-party corroboration, not one unambiguous primary statement.
- Task 13 implemented (commit 3ea7cd6): `require_auth` dependency + `CurrentAuth` alias; 401 (missing/bad/expired/wrong-aud token) vs 403 (valid token, no public.users row) deliberately distinct; org read from DB per request so de-provisioning is immediate. 13 new tests; controller independently verified 137 passed + ruff clean + clean tree.
  - Implementer mutation-tested its own guards (dropping aud/require → 3 fails; silent blank-org fallback → 2 fails). Engine-dispose test fixture proven load-bearing, not defensive.
  - CONTROLLER-VERIFIED SECURITY PROPERTY: the anon and service-role keys are JWTs signed with the SAME `SUPABASE_JWT_SECRET`, so they were a plausible impersonation vector. Empirically both are rejected twice over — `audience="authenticated"` alone rejects them (no `aud` claim), and `require:["exp","sub"]` alone rejects them (no `sub`). A crafted token with a valid `sub` but no `aud` is also rejected. Defence in depth confirmed; the module docstring's claim is accurate.
  - ARCHITECTURAL NOTE for Tasks 14-17: `DATABASE_URL` is the `postgres` superuser, which BYPASSES RLS. Task 6's policies do NOT protect API paths. Every router must filter `org_id` manually from `CurrentAuth`; the 403 plus that discipline is the whole tenant boundary on API paths. (Intended per plan Task 14 Step 0.)
  - LANDMINE for Tasks 14+ (**first stated wrong by me, corrected in 80555f6**): a `tests/api/conftest.py` does NOT silently break `tests/db/` — pytest clears the cached conftest module, so plain `pytest` passes all 137 with one present. Real hazard is order-dependent: three db test files import fixtures by BARE MODULE NAME, so `pytest tests/db tests/api` → 3 collection errors while `pytest tests/api tests/db` → 36 passed. I reproduced both before correcting the plan. Lesson: I propagated an implementer's confident-but-unverified claim into a plan constraint governing 5 tasks. Verify before pinning.
- Task 13 spec review: ✅ COMPLIANT (16 mutations, 14 caught; 2 survivors disclosed as non-issues on this FastAPI version). Confirmed the anon/service-role impersonation vector is closed twice over, and that `require:["sub"]` is what stops `claims["sub"]` → KeyError → 500.
- Task 13 quality review: ⚠️ APPROVED WITH NITS. Three security guards had ZERO regression coverage (HS256 allowlist — widening to HS512 returns 200 on mutated code; `exp` requirement; the dead-arm except tuple whose narrowing creates an unpinned upstream dependency). Fix round dispatched.
- DECISION (advisor-corrected) on the test-infra question: **Task 13a added to plan** (commit d6fa549) — `__init__.py` migration + package-qualified imports + shared `tests/api/conftest.py`, as an executable prerequisite before 13b. REJECTED: root `tests/conftest.py` (my first instinct — it loads under the same `conftest` module name, i.e. the very collision channel being closed, and was never verified: the same failure class I had just corrected the plan about) and session-scoped asyncio loop (verified working but trades isolation across 137 tests to delete a proven 12-line fixture; pytest-asyncio will force a revisit on its own schedule).
- CI consequence pinned to Task 24 (80555f6): no `.github/` exists; import-time env failures mean a bare `pytest` runs ZERO tests because collection aborts. CI must start the stack or split env-free (`tests/core tests/flows tests/evals` = 100 passed standalone) from integration.
- Task 13 fix round COMPLETE (`55e619e` + `1b2db15`): 4 new tests pinning the 3 unpinned guards; `except` narrowed to ValueError with its upstream assumption watched; comment corrections; `db()` ctx manager; `call_whoami` seam. 17 api / 141 suite / ruff clean.
  - CONTROLLER-VERIFIED each mutation kills exactly its intended test and no other (HS512 widening, `_REQUIRED_CLAIMS=["sub"]`, `verify_sub: False`). Tree restored clean after each.
  - Implementer PUSHED BACK on two reviewer numbers and was RIGHT on both; re-reviewer withdrew both. (a) HS512 mutation returns 403 not 200 (random sub, no users row) — still the stronger bypass proof, since reaching the org lookup means the signature was accepted. (b) `["HS256","none"]` is a 500 not a bypass: I verified `InvalidKeyError` descends from `PyJWTError` NOT `InvalidTokenError`, so it escapes the 401 handler. Every other attacker-reachable class IS caught. `verify_sub` default confirmed True in pyjwt 2.13.0.
  - Lesson reinforced: subagent pushback is worth soliciting explicitly — two of three reviewer claims about mutation *outcomes* were wrong while the underlying findings were right.
- Task 13 TICKED (`bddee8b`) after BOTH review stages passed. Recorded in the plan that Task 13 delivers a correct org_id and NOTHING about whether routes filter on it — "auth done" ≠ "tenant isolation proven". Residual risk pinned: pyjwt is floored not pinned; a 3.x flipping `verify_sub` would break the narrowed except, and `test_token_with_non_string_subject_is_401` is the tripwire (widen the except, don't delete the test).
- Task 13a COMPLETE (`1deab82`): `__init__.py` in tests/ + 5 subdirs (not `fixtures` — CSVs only); exactly 3 bare-name imports found and package-qualified; api fixtures + test `app`/`/whoami` route moved to `tests/api/conftest.py`. Implementer confirmed Step 2 is mandatory by running Step 1 alone (ModuleNotFoundError), and self-corrected an initial wrong conclusion that I001 was disabled.
- PRE-EXISTING BUG found by 13a's probe and FIXED (`67b9a34`): `pytest tests/db tests/api` was passing by ORDERING LUCK. `test_models_roundtrip.py` never disposed the module-level engine, leaving connections on a closed event loop; the command only survived because the first `tests/api` test collected 401s before touching the DB. Five new api modules would have resurrected it. Fixed by mirroring `_dispose_app_engine` into `tests/db/conftest.py`.
  - DISCRIMINATING CHECK (full suite + both orderings all passed BEFORE the fix, so they prove nothing here): `pytest tests/db/test_models_roundtrip.py "tests/api/test_auth.py::test_valid_token_resolves_user_and_org"` — was `1 failed` (Event loop is closed), now 6 passed. env-free subset re-confirmed at 100 passed.
  - Advisor VOIDED its own earlier root-conftest objection (the module-name collision is gone post-`__init__.py`) but rejected root anyway on a better ground: autouse across the env-free 100 would force either a DATABASE_URL dependency or a cleanup silently no-oping on `sys.modules` inspection — the latter being exactly the "no silent catches" house rule.
### Task 14 Step 0 prep (controller research while 13b ran — FOLD INTO THE PLAN once 13b has committed; held back deliberately so 13b's commit can't sweep an uncommitted plan edit)
- Storage baseline measured on the live local stack: **zero buckets exist**, **zero `storage.objects` policies exist**, but RLS **is** enabled on both `storage.objects` and `storage.buckets`. So storage is currently deny-by-default for authenticated users — a good starting point, and it means the `statements` bucket AND its policies are both still to be created.
- The org-derivation helper to mirror is `public.current_org_id()` (`0002_rls.sql:35`) — `security definer`, `stable`, `set search_path = ''`, body `select org_id from public.users where id = auth.uid()`. Table policies use the uniform shape `org_id = (select public.current_org_id())`; storage policies should match it.
- **VERIFIED TRAP — do not write the natural form.** `storage.foldername('<uuid>/2026/stmt.csv')` → `{<uuid>,2026}` (folder segments, filename stripped). The obvious policy predicate `(storage.foldername(name))[1]::uuid = (select public.current_org_id())` **ERRORS** on any object whose first segment isn't a UUID: `ERROR: invalid input syntax for type uuid: "personal"`. One mis-pathed object would break the predicate for every scan of the bucket. Compare as TEXT instead — `(storage.foldername(name))[1] = (select public.current_org_id())::text` — which returned false safely on the same input. Both measured, not assumed.
- Still to decide in Task 14: whether the bucket is created by migration (preferred — reproducible, and `supabase start` replays it) or by a script.

- Task 13b (portfolio endpoints) dispatched.
- Task 13b COMPLETE, code-wise (`de29829` router + `c4bbc12` seed_org + `a2f48ef` 2dp fix). 200 tests (141→200), 76 api, ruff clean, env-free 100, both orderings 99, clean tree — all controller-verified.
  - Seven-site filter-removal probe by the implementer, two reproduced by me. Sharpest: with the entity PATCH org filter gone, cross-org PATCH returns 200 and renames org B's entity. Given RLS is inert on API paths, this probe is the ONLY evidence the tenant boundary holds.
  - CONTROLLER ERROR, corrected: I reported `ruff check` failing with 5 E402 errors and nearly raised it against the implementer. Cause was my command — from the repo root `uv run` finds no project and falls back to ambient Anaconda with **ruff 0.12.0** (E402 in defaults) instead of the project's pinned **0.16.0** (not in defaults). ALWAYS run project commands from `backend/`. Pinned in the plan (`ab2640b`).
  - REAL SPEC GAP found by the implementer: property mutations write no audit rows. It built exactly what plan Step 2 named (entities + ownership) then flagged the line as wrong. Spec:70 requires audit "on every state change to money **or compliance** data"; `finance_cost_classification` drives Section 24 (money), `epc_expiry`/`licensing_flag` are compliance. **The plan was narrower than the spec; the spec governs.** → fix round owes property POST/PATCH audit rows.
  - DISCLOSED, ruling pending from spec reviewer: concurrent ownership PUTs on one property aren't serialized — two overlapping replacements under READ COMMITTED could leave a set summing to 200, or trip `uq_property_ownership_property_entity`. Single-user MVP makes it unlikely, but the figure drives penny-exact apportionment in `core/splits.py`. Decide fix-vs-pin.

---

## END OF DAY 2026-07-29 — handoff

**Position:** 15 of 26 tasks done (Phase 5 in progress). Phases 0–4 complete. HEAD `ab2640b`, tree clean, 200 tests green.

**IN FLIGHT when the session ended:** a read-only spec-compliance review of Task 13b (`de29829`/`c4bbc12`/`a2f48ef`). It was asked to (a) confirm the property-audit spec gap, (b) rule on the ownership-concurrency risk, (c) find gaps I had not already found. Its result may have landed after this note was written — check the task notification before assuming it didn't run.

**NEXT ACTIONS, in order:**
1. Read the Task 13b spec-review result. Then the code-quality review (stage 2) — do NOT tick 13b until both stages pass.
2. Fix round for Task 13b: **property POST/PATCH must write `audit_log` rows** (confirmed spec requirement, not optional), plus whatever the two reviews raise. Also amend plan Task 13b Step 2, whose wording caused the gap by naming only entities + ownership.
3. Decide the ownership-concurrency question: `SELECT ... FOR UPDATE` on the property row inside the ownership PUT, or pin it as a known deferral with the reasoning. Do not leave it undecided and unrecorded.
4. Then Task 14 (imports endpoint). Step 0's storage groundwork is already researched and pinned in the plan (`ab2640b`) — measured baseline plus the `::uuid` policy-cast trap.

**STILL NEEDED FROM MAHMUD (not blocking 14–18, blocking 19+):**
- Google Cloud OAuth client id/secret (Task 19, Flutter sign-in). Google-only auth, no email/password.
- The list of banks/accounts feeding the portfolio, personal and Ltd, so parser fixtures come from real statement exports (spec open question 3).
- Supabase cloud project when ready to leave the local stack. Anthropic key is optional now — categoriser runs on local Ollama.

**Working method that has been earning its keep:** ask subagents to push back with evidence rather than comply. Three confident-but-wrong claims surfaced today — one reviewer's mutation outcome (twice) and one of mine propagated into a plan constraint — and in every case the implementer who challenged it was right. Verify the discriminating command yourself before pinning anything. First task where the RLS-bypass constraint bites: brief requires an API-level two-org isolation test AND a filter-removal probe proving that test has teeth. Also pinned the three schema traps (0% ownership dies at the DB CHECK; duplicate entity_id hits the unique constraint; composite FKs firing means my filtering was wrong) and Decimal-not-float for the sum-to-100.

## Session 2026-07-28
- Created planning files
- Dispatched 3 parallel research agents (property, investments, AI business)
- Property findings saved to findings.md ✔
- AI-business findings saved to findings.md ✔
- Awaiting: investments agent
- Next: save investments findings, then synthesize ranked report
