# landlord-compliance-agent

Bookkeeping and MTD-ITSA compliance for a UK property portfolio. Bank
statements go in; per-entity quarterly export packs come out, with an agent
proposing HMRC SA105 categories and a human confirming every one of them.

Nothing is auto-confirmed at any confidence, and a period containing an
unreviewed line refuses to export. That refusal is the product, not a
limitation of it.

## Where to read next

| File | What it is |
|---|---|
| `ENGINEERING.md` | **The rules.** Test discipline, tenant isolation, commit shape. Read before writing code. |
| `docs/superpowers/specs/2026-07-28-*.md` | The spec, including the open questions and what closed them. |
| `docs/superpowers/plans/2026-07-29-mvp-iteration-1.md` | The plan. Canonical for what to build next. |
| `docs/planning/progress.md` | What happened and why, including every wrong turn. |
| `docs/domain/` | The glossary. **Names in code come from here.** |
| `PRODUCT.md` / `DESIGN.md` | Who it is for, and the design language the Flutter app implements. |
| `SECURITY.md` | What to report, what is deliberate, and how to report it privately. |

## Setup

Prerequisites: Python 3.12 and [`uv`](https://docs.astral.sh/uv/), the
[Supabase CLI](https://supabase.com/docs/guides/local-development), Docker
(the local stack runs in it), Flutter 3.32+, and on macOS `brew install pango`
for the PDF renderer.

```bash
cp .env.example .env      # then fill it in — see below
supabase start            # prints the keys .env needs
make dev                  # stack + API + worker + Flutter, Ctrl-C stops all
```

`supabase start` prints `ANON_KEY`, `SERVICE_ROLE_KEY` and `JWT_SECRET`; paste
them into `.env`. `.env.example` documents every variable and why it exists —
it is worth reading rather than skimming, because three of them fail loudly at
*import* time and one of them is a privacy control.

Two you must set yourself:

- **`CATEGORISER_MODEL`** — a LiteLLM/CrewAI model id. Required, and never
  defaults: bank statement descriptions must not reach a provider nobody
  chose. `ollama/glm-4.5-air` for a local model, or a hosted id.
- **`GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`** — Google is the only sign-in
  method. The client's redirect URI is the *Supabase* callback
  (`http://127.0.0.1:54321/auth/v1/callback`), not the app's URL.

Run `make` on its own for the full target list.

### Four things that will waste your time

1. **Run every project command from `backend/`.** From the repo root `uv run`
   finds no project and falls back to ambient Anaconda tooling — including
   ruff 0.12.0 instead of the pinned 0.16.0, which reports five false
   failures.
2. **A bare `pytest` runs zero tests.** `src/api/auth.py` and `src/db/models.py`
   raise at import when their environment is missing, and a collection error
   aborts the whole run. Use `uv run --env-file ../.env pytest` with the stack
   up. This is deliberate fail-loudly behaviour, not a bug — see *Testing*.
3. **Three database URLs, and mixing them up is silent.** `DATABASE_URL` is
   the superuser (tests, migrations, `seed_org.py`). `API_DATABASE_URL` is
   `app_api`, where row-level security applies — point it at the superuser and
   the tenant boundary disappears with every test still passing except
   `tests/db/test_rls_enforced.py`. `WORKER_DATABASE_URL` is `app_worker`,
   which bypasses RLS on purpose. Open API sessions with
   `org_session(auth.user_id)`, never the raw factory.
4. **`supabase/config.toml`'s `env()` reads `.env` only when the variable is
   absent from the process environment.** A shell export silently wins, and
   `supabase status` does not validate the auth block. Check the live
   redirect instead:
   `curl -s -o /dev/null -w '%{redirect_url}' "http://127.0.0.1:54321/auth/v1/authorize?provider=google"`.

## Architecture

```
CSV ──► POST /imports ──► core/parser ──► transactions (unclassified)
                │                                    │
                └──► Supabase Storage                └──► job_queue
                                                          │
                              worker/main (claim, run, record)
                                                          │
                              flows/categorise ──► one LLM call
                                                          │
                                     transactions (proposed, with confidence)
                                                          │
                     GET /transactions ──► review screen ──► POST /confirm-batch
                                                          │
                                     transactions (confirmed) or (excluded)
                                                          │
   POST /exports/quarter ──► core/export_pack ──► core/quarters ──► core/splits
                                                          │
                          mtd_quarters row + 3 documents (2 CSV, 1 PDF)
                                                          │
                              GET /documents/{id}/download ──► signed URL
```

Four ideas carry most of the weight:

- **`src/core/` is pure.** Parser, category enum, quarter windows, ownership
  splits, export pack: plain data in, plain data out, no database. Every
  refusal rule and every penny of arithmetic is testable without a stack, and
  `tests/core/` runs with no environment at all.
- **`org` is the tenant; `entity` is the tax filer.** One org has many
  entities. Conflating them writes a cross-tenant bug — though since the API
  connects as an RLS-enforced role, the database now catches a forgotten
  `org_id` filter rather than serving another customer's rows.
- **Money is attributed by ownership, never by bank account.** Per-entity
  totals derive exclusively from `property_ownership` (HMRC PIM1035);
  `transactions.entity_id` is only the fallback for a line with no property.
- **The worker is the one component with no authenticated caller.** Nothing
  bounds its queries but the `org_id` on the job row it claimed, and there is
  no RLS backstop. `tests/worker/`'s isolation section is the whole boundary.

Layout: `backend/src/{core,api,db,flows,worker}`, `backend/evals`,
`supabase/migrations`, `frontend/lib/{api,app,features,theme}`.

## Testing

```bash
cd backend
uv run --env-file ../.env pytest                    # full suite, needs the stack
uv run pytest tests/core tests/flows tests/evals    # env-free subset, no database
uv run ruff check src tests                         # the only lint gate
cd ../frontend && flutter test && flutter analyze
```

Both directory orderings must pass — `pytest tests/db tests/api` once passed
by ordering luck. `ENGINEERING.md` explains why, along with the mutation-testing
rule that has caught more real defects here than anything else.

`tests/e2e/test_smoke.py` walks the whole pipeline with **only the LLM call
stubbed** and checks the exported figures penny-exact, read back through the
signed-download path.

### CI

`.github/workflows/ci.yml`, three jobs, on push to `master` and on every PR.

The split is **not** about speed. As above, a bare `pytest` executes **zero**
tests because import-time environment checks abort collection — so a naive job
can go green having run nothing. Hence:

- **fast** — ruff, then `pytest tests/core tests/flows tests/evals` with no
  environment set at all (182 tests). It first asserts the *collected* count
  is at least 170: `pytest` exits non-zero on "collected nothing" but zero on
  "collected fewer than expected", which is the failure that would go unnoticed.
- **flutter** — `flutter analyze` and `flutter test`, from `frontend/`
  (`bootstrap_test.dart` opens files by relative path).
- **integration** — starts a real Supabase stack, then runs the full suite in
  **both** directory orderings, because `pytest tests/db tests/api` once passed
  by ordering luck.

The live golden eval is deliberately absent: it calls a real model.

## Adding a bank format

`src/core/parser.py` holds a registry keyed by bank name. **The caller names
the bank; the parser never guesses from content** — which is why HSBC, whose
export has no header row at all, can be supported.

1. Put a **sanitised** sample at `backend/tests/fixtures/statements/<bank>.csv`.
   Real column layout, dates and amounts; fake account numbers, balances and
   counterparty names. Fixtures are tracked.
2. Write the failing test in `tests/core/test_parser.py` first, and watch it
   fail for the expected reason.
3. Add a `StatementFormat` describing the shape. `header` is the normalised
   header row (stripped, casefolded) or `None` for a headerless export;
   `header_row` skips a preamble; `encoding` handles the ones that are not
   UTF-8 (Nationwide is `iso-8859-1`, because of the pound sign). For an
   ordinary one-signed-amount-column export, `_single_amount_row(...)` builds
   the row parser for you; anything stranger gets its own function.
4. Register it in `_FORMATS`. `GET /banks` and the upload form pick it up from
   there — the list has exactly one definition.

Sign convention: `ParsedLine.amount` is **signed**, negative for money out.
Nationwide splits money across two columns and gets collapsed; that direction
is load-bearing, because a whole statement arriving with the wrong sign fails
nothing and quietly proposes income for every expense.

`docs/planning/bank-formats.md` surveys the seven banks in the portfolio and
which exports are in hand.

## Running the golden eval

```bash
cd backend
uv run --env-file ../.env python evals/run_eval.py
uv run --env-file ../.env python evals/run_eval.py --model ollama/glm-4.5-air --json
uv run --env-file ../.env python evals/run_eval.py --limit 5 --threshold 0.8
```

Runs the real categorisation flow against `evals/golden_set.jsonl` and reports
per-category precision/recall plus accuracy, exiting non-zero below the
threshold. **This calls a real model** — it is manual or nightly, never part
of `pytest`. The suite only exercises the scoring maths and `main` wired to a
stubbed flow.

Two things to know before trusting a number from it:

- **The golden set is 20 synthetic lines**, each marked `"_synthetic": true`.
  They are realistic but invented, and must be **replaced wholesale** by real
  confirmed lines once those exist.
- **It is a cold-start comparison.** The harness passes no few-shot examples,
  while production supplies up to 50 confirmed transactions per org. A verdict
  here is zero-shot quality, not behaviour once an org has history.

Run it twice against different `--model` ids and diff the `--json` to choose a
production model on this project's own data rather than published benchmarks.

## Verifying the app makes no external requests

The built web app contacts **no host but its own origin**. That is a privacy
property, not a performance one: every outbound request from a page showing
someone's bank transactions carries their IP to whoever serves it. Flutter's
defaults do not give you this — CanvasKit and a Roboto fallback are both
fetched from Google unless you say otherwise, which `web/flutter_bootstrap.js`
does.

To check it after changing anything about the web build:

```bash
make web-build
cd frontend/build/web && python3 -m http.server 3000 --bind 127.0.0.1
```

Open `http://127.0.0.1:3000`, **unregister the service worker and clear
`flutter-app-cache`** — three consecutive "the fix didn't work" readings here
were the previous build being replayed — then in the console:

```js
const here = location.origin;
performance.getEntriesByType('resource').map(e => e.name)
  .filter(n => !n.startsWith(here) && !n.startsWith('data:') && !n.startsWith('blob:'));
```

An empty array is the pass. **Grepping `build/web` is not this check and must
not be substituted for it:** that output carries thirty-odd absolute URLs,
nearly all licence text in `NOTICES`, plus `www.gstatic.com` in `flutter.js`
as the branch our config short-circuits — so a grep reports failure on a clean
build, and a check that cries wolf gets deleted.
`frontend/test/bootstrap_test.dart` guards the *mechanism* automatically; the
browser is the only thing that can confirm the *outcome*.

## Status

Iteration 1 (MVP) is complete: parser, categorisation flow, job worker, the
seven API surfaces, and every Flutter screen. See the plan for what each task
covered and `docs/planning/progress.md` for the decisions behind them.

One caveat on reading the plan: Phases 1–6 have unticked *step* boxes. They
are not outstanding work — those phases predate task-level tracking and their
steps were never back-ticked. The plan's header says which artefacts to check
instead.

Known limits, all deliberate: the golden set is synthetic; there is no CI; the
app runs against the local Supabase stack only (a cloud project exists but is
parked); and no screen beyond sign-in has been exercised against a real Google
sign-in, which only Mahmud can perform.

## Licence

[AGPL-3.0](LICENSE). You may run, modify and self-host this freely. If you
offer it to others as a network service, the licence requires you to publish
your modifications — which is the point: the intent is that anyone can host
this for themselves, not that someone can take it closed.

Chosen over MIT deliberately. The moat here was never the code: it is the bank
format registry built from real exports, keeping pace with MTD, RRA and PRS
changes, and a golden set drawn from real confirmed lines. A fork inherits
none of that.
