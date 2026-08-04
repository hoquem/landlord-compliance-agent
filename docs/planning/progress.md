# Progress Log

## Session 2026-08-04 (cont.) — the categoriser works, and has a real number

**85.00% category accuracy (17/20)** on the golden set against
`ollama/glm-5.2:cloud`, above the 0.70 threshold. First real accuracy figure
this project has had. 472 backend tests, ruff clean.

### Why the flow stopped asking the SDK to parse

Mahmud's call, made against my recommendation and after I put the objection
in front of him. The objection was that tolerating fenced JSON trades a loud
failure for a lenient parser at the point where an LLM's output becomes a tax
figure. What changed my read of it was the measurement: **the enforcement
being given up was already fictional on this provider.**

| request | result |
|---|---|
| OpenAI-compat `response_format={"type":"json_schema","strict":true}` | prose with markdown headings |
| Ollama native `/api/chat` with `format=<schema>` | the same prose |
| Through CrewAI, which also puts the schema in the prompt | correct JSON, wrapped in a ```` ```json ```` fence |

Constrained decoding is a property of the *local* runner; a `:cloud` tag has
none, so the schema is dropped in transit. A **756-billion-parameter** model
failing at pure instruction-following is not a capability limit — nothing was
constraining it. Which also answers "why not a smaller, cheaper cloud model":
with nothing enforcing the schema, compliance rests entirely on
instruction-following, and that is exactly what degrades as models get
smaller.

### What the change actually is

`response_format` dropped; the reply read off `result.raw`, one whole-answer
markdown fence stripped, then `StatementProposals.model_validate_json` and
then `_validate_proposals` — both unchanged, both loud.

**Leniency stops at the wrapper.** The fence regex is anchored at both ends
and applied to already-stripped text, so it can only remove a wrapper — it
cannot pick a JSON-looking fragment out of a reply that also contains
commentary. A model that says "here are the proposals, but I was unsure about
line 3" is telling us something, and quietly extracting the JSON would
discard it.

`_build_prompt` became load-bearing for format, which is the real consequence:
dropping `response_format` also dropped the schema CrewAI injected with it, so
the prompt now has to name the JSON shape, forbid the fence, and list all
fifteen categories. Pinned by
`test_the_prompt_carries_the_shape_the_sdk_used_to_supply`.

**Two mutations, each killing what it should and nothing else:**
- `_unwrap` returns its input unchanged → the three fenced cases and the
  contract-check test die; bare JSON and whitespace-padded JSON survive.
- the `_validate_proposals` call deleted → five die, including
  `test_a_fenced_answer_still_faces_the_contract_check`. That one is the
  whole point: it proves leniency never reached the contract.

**Scope was three stub sites, not one.** `tests/flows`, `tests/evals` and
`tests/e2e` all asserted `response_format is StatementProposals` and returned
`.pydantic`. All now return `.raw` — better fidelity, since the stubs exercise
the parsing the flow owns instead of handing it an object that could never
have been malformed. The E2E stub returns *fenced* text specifically, because
that is what the real model does.

One test was removed rather than adapted:
`test_flow_raises_when_agent_does_not_return_structured_output` asserted on a
state that can no longer occur. Strictly superseded by
`test_the_flow_refuses_an_empty_answer` and
`test_the_flow_refuses_prose_and_quotes_what_came_back`, which cover a case
the old one could not reach at all. A comment stands where it was.

### Ignore the property-allocation number

The same run reports **11.76%** property allocation. It measures the golden
set, not the model: **no golden-set description names its property.**
"STANDING ORDER RENT - J SMITH" → "18 Sample Avenue" is not inferable from
the text — it needs the tenant-to-property history that production supplies as
up to 50 few-shot examples and the harness deliberately withholds (it is a
cold-start comparison by design, and says so). Fixing this means real
confirmed lines in the golden set, which is already the standing action.

### Still true

The category accuracy is against **synthetic** data, and `:cloud` still means
statement descriptions leave the machine. Both were known and chosen; neither
is fixed by this change.

## Session 2026-08-04 (cont.) — CATEGORISER_MODEL set, and what setting it revealed

`.env` now has `CATEGORISER_MODEL=ollama/glm-5.2:cloud`, Mahmud's choice after
being shown the trade-off. **The categorisation path still does not work**, and
the reason only surfaced because the value was verified rather than written and
declared done.

**`ollama/` does not mean local.** Every model on this machine is a `:cloud`
tag — ~320-byte pointer manifests, not weights — so despite the prefix,
statement descriptions go over the network to Ollama. That contradicts the
"local open-weight model for bank-data privacy" intent stated in
`src/flows/categorise.py`, `.env.example` and the spec. Chosen knowingly; the
fix is `ollama pull <a model with real weights>` and one line in `.env`.

**Four of the five installed tags do not answer at all.** One synthetic
golden-set line each (the golden set is invented, so nothing real was sent):

| tag | result |
|---|---|
| `glm-5.2:cloud` | reaches the model |
| `kimi-k3:cloud` | 402 — extra-usage balance empty |
| `qwen3-coder-next:cloud` | 410 — retired 2026-07-15 |
| `deepseek-v3.2:cloud` | 410 — retired 2026-07-15 |
| `minimax-m2.1:cloud` | 410 — retired 2026-07-15 |

`glm-4.5-air`, the value `.env.example` has carried since Task 11, is not
installed at all. Copying it in would have produced a config that looks right
and fails at first use.

**The one reachable model fences its JSON.** `glm-5.2` returns
` ```json {"proposals": [...]} ``` `, and the OpenAI structured-output parser
behind `Agent.kickoff(response_format=StatementProposals)` rejects it before
the flow's own validation runs. So the variable is correctly set and
reachable, and a `categorise` job would still fail.

**Deliberately not worked around.** Making the flow tolerate fenced output
means dropping `response_format` and parsing ourselves — a design change to
money-path code, and one that trades a loud failure for a lenient parser on
the one boundary where an LLM's output becomes a tax figure. That is Mahmud's
call, not a tidy-up. The alternative is a model that honours strict
`json_schema`.

`.env.example` now warns about all three traps (`:cloud` ≠ local, cloud models
get retired, fenced JSON), and names
`evals/run_eval.py --limit 1 --model <id>` as the cheapest way to find out
which you have.

## Session 2026-08-04 (cont.) — Task 24 COMPLETE, and the P0 is closed

**Iteration 1 is done.** 463 backend tests, 104 Flutter tests, ruff and
`flutter analyze` clean.

### The privacy P0 is closed: `external: []`

The app made one request to `https://fonts.gstatic.com/s/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2`
per load — Flutter's default font fallback, fetched eagerly even though every
glyph on screen comes from bundled Inter. Fixed with
`fontFallbackBaseUrl: "fallback-fonts/"` in `web/flutter_bootstrap.js` plus
the file vendored at exactly the path the engine appends.

**Nothing was downloaded.** The font is the Flutter SDK's own
`bin/cache/artifacts/material_fonts/Roboto-Regular.ttf`, copied in with its
licence. It is a TrueType font wearing a `.woff2` name, because the name has
to match what the engine appends and nothing inspects the extension.

**That last claim was proved, not assumed.** "The page still looks right"
could not have shown it — Inter covers everything on screen, so a rejected
fallback would look identical. The discriminating check was to ask Skia
directly, in the running page:

```js
const buf = await (await fetch('fallback-fonts/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2')).arrayBuffer();
const face = window.flutterCanvasKit.Typeface.MakeFreeTypeFaceFromData(buf);
// accepted: true, glyphIDs for "Aa1£—": [37, 69, 21, 101, 386]
```

Non-zero glyph ids for all five codepoints: a working typeface, not a stub.
Before/after in the browser, service worker unregistered and `flutter-app-cache`
cleared first: 15 resources, `external: []`.

Costs 171 KB uncompressed against Google's ~15 KB subsetted woff2. Accepted —
one same-origin request against 5.6 MB of CanvasKit, versus telling Google who
is reading their bank statements.

### The check this task originally specified was wrong

The plan said: grep `frontend/build/web` for `gstatic.com` / `googleapis.com`.
**That check fails on a clean build.** The output carries thirty-odd absolute
URLs, nearly all licence text in `NOTICES`, plus `www.gstatic.com` sitting in
`flutter.js` as the branch `canvasKitBaseUrl` short-circuits. A check that
cries wolf gets deleted, so there is deliberately **no `make check-origins`**.

The honest check needs a browser —
`performance.getEntriesByType('resource')` filtered against `location.origin`,
which is what `flutter_bootstrap.js`'s own comment already said. It is written
into `README.md` as a manual procedure, with the service-worker trap attached.

`frontend/test/bootstrap_test.dart` (3 tests) guards the *mechanism*: both
config keys present, and the vendored font present with TrueType magic bytes
— the magic-byte check catches a placeholder or truncated copy that an
existence check would pass. Two mutations run, each killing exactly one test
and nothing else: deleting the `fontFallbackBaseUrl` line, and moving the font
file aside.

**Three files were asserting the old state** and are corrected:
`DESIGN.md`, `frontend/pubspec.yaml`, `frontend/lib/theme/tokens.dart`. That
is the same shape as the original P0 — a claim copied into four places without
being measured — so leaving them would have recreated it in reverse.

### README

New, and the front door rather than a fifth copy of the rules: setup, the four
traps, an architecture sketch, testing, adding a bank format, running the
golden eval, and the origin procedure. It points at `ENGINEERING.md` and the
spec instead of restating them.

Two things re-measured rather than copied forward:

- The env-free subset is **173** tests, not the "~100" the plan carried from
  Task 13. Verified under `env -u DATABASE_URL -u SUPABASE_JWT_SECRET`.
- The eval's CLI flags were taken from `--help`, not from its docstring.

**Spec open question 1 cross-checked and left alone.** It carries the
per-entity aggregation answer, the cumulative-YTD confirmation, the sourcing
caveat and the two enum gaps. Nothing to change; editing for the sake of
ticking a box would have been worse than not.

`make web-build` added (a `build` target rejects `--web-port`, so the defines
are split into `DART_DEFINES` and `FLUTTER_DEFINES`).

### Still true, and worth carrying forward

- **`.env` still has no `CATEGORISER_MODEL`.** `.env.example` documents it and
  the flow fails loudly without it; the suites set it themselves. Flagged
  since 2026-08-02 and still not silently patched — it is Mahmud's file.
- The golden set is 20 synthetic lines. Replace wholesale once real confirmed
  categorisations exist.
- No CI. The README says what shape it has to take and why a bare `pytest`
  runs zero tests.
- **No screen beyond sign-in has been seen in a browser** — reaching them
  needs a real Google sign-in, which only Mahmud can perform.

## Session 2026-08-04 (cont.) — Task 23 COMPLETE (E2E smoke)

One test, `backend/tests/e2e/test_smoke.py`, walks a CSV from upload to a
filed quarter and reads the money back out through the signed-download path.
463 backend tests, ruff clean. Run in both directory orderings, because a
single-directory run cannot show the pooled-connection asymmetry Task 13a was
bitten by.

**The seam is one level deeper than `tests/worker/test_poller.py`'s.** That
suite replaces the whole `CategoriseStatementFlow`, because its subject is the
worker and it needs to inspect the inputs the worker built. Here the flow is
part of what is under test, so only `crewai.Agent` is replaced: the flow's
input validation, its prompt, and its output-contract check all run for real,
and `_build_llm` still constructs a real `LLM` (verified offline-safe before
relying on it). `CATEGORISER_MODEL` is set by the fixture — that is
configuration, not mocking, and `.env` still lacks it (flagged in the
2026-08-02/03 entry and still true).

**The stub answers from the prompt, not positionally.** It maps description →
category and takes the property id out of the prompt's property block. Two
gains over a positional list: it cannot silently mis-attach if row ordering
changes, and it *proves* `_build_prompt` carries the lines and the property
list, which nothing asserted before.

**The fixture's amounts are chosen, not arbitrary.** 60/40 ownership, with a
leftover penny going to the 60% owner on the rent (1350.01 → 810.01/540.00)
and to the 40% owner on the repair (84.99 → 50.99/34.00) — opposite
directions, so a ranking bug cannot cancel out. A £25 refund *in* against an
expense category pins the sign rule. A `personal_non_business` line pins the
drop; an excluded line pins the exclusion; a line with no property pins the
fall back to `transactions.entity_id`.

**Latent flake avoided by construction, and written into the module docstring
so the next person does not reintroduce it:** `split_amount` breaks a
remainder tie on ascending owner UUID, and entity ids are random per run. Any
amount whose two owners have *equal* remainders **and** a penny to hand out
would pass about half the time. Every amount in the fixture either has
unequal remainders or nothing left over.

**Two mutations run, both die on the figures in the downloaded CSV** — which
is the point, since a test whose numbers are decorative would survive them:
- Reversing `split_amount`'s remainder ranking (`(-remainders[o], o)` →
  `(remainders[o], o)`): 810.01 → 810.00 and 35.99 → 36.00. The sum invariant
  still holds, so `split_amount`'s own postcondition does *not* catch this.
- Dropping the category from `signed_amount`: 35.99 → −35.99, 200.00 →
  −200.00. The refund stops reducing the expense.

Deleting the leftover-penny distribution entirely was *not* used as evidence:
`split_amount`'s postcondition raises `RuntimeError` first, so the test dies
as a 500 rather than on its figures, and it would prove nothing about them.

**The "no external origin" assertion I said would live here does not belong
here.** A backend pytest cannot observe the Flutter web bundle. It wants a
`make` target grepping `frontend/build/web` after a build — noted for Task 24,
not silently substituted with something weaker.

**Also asserted end to end:** exporting while lines are still `proposed` is a
422 naming the blocking ids (refusing to export is a feature); the PDF really
renders through WeasyPrint (`%PDF-` magic bytes, fetched from storage); the
two owners' totals sum back to the undivided amounts; and the `mtd_quarters`
row carries `capital_expense_total = 0.00` rather than leaving it absent.

**One trap the review caught before it was written:** `ConfirmBody.property_id`
defaults to `None` and `_apply_confirm` assigns it unconditionally, so a batch
item that omits it *wipes* the proposed attribution — silently moving money to
the wrong owner. The batch sends it explicitly, with a comment saying why.

## Session 2026-08-04 (cont.) — Phase 7 COMPLETE (Tasks 20, 21, 22)

Every screen in the MVP now exists. 462 backend tests, 101 Flutter tests,
ruff and `flutter analyze` clean, `flutter build web` succeeds.

**Infrastructure Phase 7 could not start without**, built as part of Task 20:
an `ApiClient` seam (abstract, so screens are testable with no network), wire
models, and the `StatusColors` `ThemeExtension` the plan had scheduled for
Task 22.

**Four deliberate deviations from the plan, all because DESIGN.md is newer
and Mahmud approved it.** Recorded here so nobody later reads the plan as
unimplemented:

1. **No staggered list entrance** (Task 20). Motion marks state changes, not
   arrivals; there is no page-load choreography.
2. **Five-state vocabulary replaces RAG** (Task 22's prerequisite). Only
   `needs-you` and `wrong` carry chroma. A green "settled" would make a
   finished queue as loud as an unfinished one — the queue draining of
   colour *is* the reward.
3. **Category picker is a popover, not a bottom sheet** (Task 21), and
   **low confidence is weight and word, not an amber pulse**. A
   low-confidence proposal is not an error.
4. **Dashboard is an attention list, not cards** (Task 22). Identical card
   grids and the hero-metric template are banned, and "generic SaaS
   dashboard" is a named anti-reference.

**Four backend endpoints added, each closing a drift or a gap** — every one
because the frontend must not own what the backend owns:
`GET /banks` (parser registry), `GET /categories` (the fifteen),
`GET /dashboard` (the statutory deadline), `GET /documents/{id}/download`
(private buckets), `GET /properties/{id}/ownership` (PUT replaces the
complete set).

**Two tests that could not have worked, both found by mutation:**

- Asserting a rendered status colour equalled its token was tautological —
  it compared the token to itself, so a green `settled` passed. The claim
  that matters (only two of five states carry chroma) now lives in
  `theme_test.dart`, asserted against the palette's neutrals.
- The ownership editor's float test used 33.33/33.33/33.34, which sums to
  **exactly** 100.0, so replacing the 2dp comparison with `== 100` passed.
  Brute force found the real shape: **no two-way split fails at all** — all
  9,999 are exact — so only a three-way case can catch it.
  5.00 + 63.01 + 31.99 = 99.99999999999999 does.

**Caught myself mid-build**: the dashboard's first draft had a 2px coloured
bar down the left of each line — a side-stripe border, an absolute ban —
with a comment explaining why it was different. It wasn't. Third instance
today of writing a justification instead of removing the thing.

**Also fixed**: the ownership editor built a `TextEditingController` inside
`build`, which resets the cursor on every keystroke. Unusable in practice,
invisible in a screenshot, and caught only by thinking about the lifecycle.

**Environment note**: the local Supabase stack degraded after ~22 hours and
began timing out auth-admin calls, turning a 10s suite into 242s with
spurious ERRORs that looked exactly like code failures. `supabase stop &&
supabase start` fixed it.

**Not done, and worth knowing:** no screen beyond sign-in has been seen in a
browser. Reaching them needs a real Google sign-in, which is Mahmud's to
perform. Every screen is covered by widget tests, but the gstatic finding
earlier today is the reminder that tests and reality are different things.


## Session 2026-08-04 (cont.) — Phase 7 begun, and a privacy claim caught wrong

Design language decided (`PRODUCT.md`, `DESIGN.md`), theme rebuilt to it,
Task 19 landed: Google-only sign-in, go_router guard, nav rail, placeholders.
36 Flutter tests, analyze clean, `flutter build web` succeeds.

**The finding that matters, from running it rather than reading it.** A built
web app contacts Google three times on every load: `canvaskit.wasm` and
`canvaskit.js` from `www.gstatic.com`, and a Roboto `woff2` from
`fonts.gstatic.com`. Measured with
`performance.getEntriesByType('resource')` in Chrome against the real build.

I had reasoned my way to bundling Inter *specifically* to keep the user's IP
off Google's CDN on a page showing bank transactions — and then asserted that
outcome in **four places** (`DESIGN.md`, `pubspec.yaml`, `tokens.dart`, commit
`009da2d`) without ever loading the page. The reasoning was right and the
conclusion was incomplete: I closed one hole in a wall with three.

Cause is exact, not guessed: `flutter_tools/lib/src/build_system/targets/web.dart:111-119`
injects `FLUTTER_WEB_CANVASKIT_URL=https://www.gstatic.com/flutter-canvaskit/...`
unless `--dart-define=UseLocalCanvasKit=true` — while `flutter build web`
already copies CanvasKit into `build/web/canvaskit/`. The bytes ship and go
unused.

**FIXED, partially, same session** (Mahmud chose "now, before Task 20").
CanvasKit is self-hosted via a new `web/flutter_bootstrap.js` setting
`config.canvasKitBaseUrl`. Two of the three external requests are gone;
`fonts.gstatic.com` for Flutter's Roboto fallback remains, and its lever is
the bootstrap's `fontFallbackBaseUrl` (which would require vendoring the
fallback files at Flutter's expected path structure).

**Sign-in screen completed** after the critique scored it 1/4 on visibility
of status and 0/4 on error recovery. Composition anchored left with a 52px
wordmark instead of a 380px column centred in a void; the button now sizes to
its content, which also cuts the accent area from a gold slab to a fraction
of the surface. Three states, because an OAuth redirect is invisible:
`Opening Google` while pending, and a failure that quotes the reason and says
what to do. Six new tests; the narrow-viewport one was mutation-checked
(squeezing the inset to 185px on a 390px viewport fails it) because a test
that only asserts "did not throw" is worth nothing until you have seen it
throw. **The error state is unit-tested but never seen** — forcing a real
OAuth failure would need a live redirect, so it stays on trust.

**Both dart-defines I first reached for were wrong**, and I only knew because
I re-measured: neither `UseLocalCanvasKit=true` nor
`FLUTTER_WEB_CANVASKIT_URL=...` changes anything, because the *runtime*
bootstrap re-derives the URL from `engineRevision`. Only the runtime config
key works.

**And a trap that nearly produced a third wrong conclusion:** Flutter
registers a service worker caching the whole build, so three consecutive
"the fix didn't work" measurements were the service worker replaying the
first build. Unregister it and clear `flutter-app-cache` before trusting any
before/after network measurement. Had I stopped at the first re-measure I
would have concluded the fix was impossible.

**Where the test goes: Task 23's E2E smoke** — assert the app requests no
external origins. A widget test cannot see this; that is precisely why four
documents could claim it and be wrong.

**Also corrected: the weight framing was upside down.** Inter's 782 KB gzipped
was flagged in DESIGN.md; `canvaskit.wasm` is 5.6 MB and went unmeasured.

**Fourth instance today of the same pattern** — asserting a behaviour in a
comment without enforcing it. The others: the deep-link test that passed
because "Certificates" is also a nav label; the SKIP LOCKED test that passed
under plain FOR UPDATE; the status-boundary tests that moved with their own
constant. Mutation testing caught three of them. Only *running the app*
caught this one, which is the lesson: mutation testing proves a test
discriminates, not that the claim was ever checked against reality.


## Session 2026-08-04 — Barclays registered, Task 18 COMPLETE (worker)

**Barclays.** Mahmud supplied `data.csv`, closing the last real format gap.
It turned out to be the *simplest* format in the portfolio — headered, six
columns, one signed `Amount`, dd/mm/yyyy, UTF-8. Both awkward things are
whitespace: `Memo` is a fixed-width mainframe field with padding runs and
unquoted embedded tabs (new opt-in `collapse_whitespace`), and the file's
trailing line contains only a tab, which widened the EOF tolerance from "an
empty row" to "a row whose cells are all blank once stripped". Verified
against the real export: 10 rows, net −£282.82. Fixture sanitised.
Seven mutations killed.

**Task 18 — the worker.** Three transactions per job, and the split is the
design: the claim commits immediately (else the row stays locked for the
whole job and `SKIP LOCKED` protects against the wrong thing), the handler
gets its own, and **failure gets a third** because the handler's is already
rolled back and would discard the record of why it failed.

**Two measured findings that changed the design:**

- **`crewai/llm.py` calls `load_dotenv()` at module scope.** So a telemetry
  check made *after* importing CrewAI passes because CrewAI itself just set
  the variable from a `.env` on disk — proving a file exists, not that the
  deployed environment is configured. The guard therefore runs first, and
  `handle_categorise` imports the flow lazily to keep that ordering real.
  The lazy import is load-bearing, not style.
- **`CREWAI_TELEMETRY_OPT_OUT` is read by NO installed package.** Grepped
  the whole of site-packages. It is the CrewAI 0.x spelling; 1.15.8 reads
  `OTEL_SDK_DISABLED`, `CREWAI_DISABLE_TELEMETRY`, `CREWAI_DISABLE_TRACKING`.
  The plan and `.env` both name the dead one. `OTEL_SDK_DISABLED` was also
  set, so telemetry *was* off — but by one of the two variables, not both.
  `.env.example` now says which works and why the other is kept.

**A weak test of mine, found by mutation.** Dropping `SKIP LOCKED` did not
break the two-concurrent-claims test: plain `FOR UPDATE` also yields
different jobs, because the loser blocks and Postgres then re-evaluates onto
the next queued row. `SKIP LOCKED` is about *not blocking*, so the
replacement test holds a lock from a separate connection and asserts the
locked job is stepped over. Ten mutations now die.

**The `make dev` trap was measured, not assumed.** Claimed `kill 0` prevents
orphans; tested it with stand-in processes, killing make itself (a plain
Ctrl-C reaches the children directly and so does not discriminate). Without
the trap two children survived; with it, zero.

**Cross-directory ordering re-verified.** `tests/worker/conftest.py`
re-exports api fixtures including the autouse `_dispose_app_engine` — autouse
does *not* follow a plain import, and that asymmetry was Task 13a's bug. Ran
`pytest tests/worker tests/api`, the reverse, and `tests/db tests/worker`.

**Open question for Mahmud, not decided here:** after a successful
categorisation the import stays `parsed`, because `import_status` has no
`categorised` value. So the imports screen (Task 20) cannot distinguish
"parsed, awaiting categorisation" from "parsed, proposals ready". Adding an
enum value is a migration and a product decision, so it was flagged rather
than taken.


## Session 2026-08-04 — Task 17 COMPLETE (certificates CRUD)

`POST/GET/PATCH/DELETE /certificates` plus the grouped list. Both steps
ticked.

**Design decisions the one-line plan did not settle:**

- **Flat routes, not nested under the property.** `docs/domain/compliance.md`
  names Property the aggregate root, but nesting would give every
  certificate two URLs and put the property in PATCH/DELETE paths where it
  adds nothing. The invariant ("one property, one org") is upheld by the
  org-scoped property lookup on create and on any PATCH that moves one.
- **Wire name is `certificate_type`, not `type`.** The model maps the column
  `type` to the attribute `certificate_type`. A body field named `type`
  would make PATCH's `setattr` loop write a plain Python attribute that
  never reaches the database — **silently, with a 200 response.** The
  awkward name is the price of that loop staying honest.
- **The grouped list excludes properties with no certificates.** Considered
  including them so the screen could show gaps, and rejected it: nothing
  records which types a property *requires*, so an empty group cannot answer
  the question that would justify it. That is a dashboard join with
  `GET /properties` (Tasks 19–22).
- **Certificate status is derived on every read**, never stored, per the
  glossary. Recomputed on GET as well as on create — a status computed once
  would be right for a day and wrong every day after.
- **`issue_date <= expiry_date` is validated against the resulting row**, not
  the request: patching `issue_date` alone still has to agree with the
  stored `expiry_date`. A transposition is the one data-entry error that
  silently inverts the answer the page exists to give.

**Two pre-existing defects found while doing it:**

- **The glossary said three certificate types; the schema and spec both said
  five.** `compliance.md` was the wrong one. Nothing caught it because until
  Task 17 no code read the set. Fixed, plus `CertificateType` in
  `src/core/certificates.py` as the Python source of truth and
  `test_certificate_type_enum_matches_the_python_enum` comparing it to the
  live SQL enum — the guard `hmrc_category` already had and this did not.
- **`test_not_nullable_is_exactly_what_the_schema_says` had an unexercised
  broken path.** It looked up `__table__.columns[field_name]`, which keys on
  *column* names; every body it had ever run against happened to have no
  renamed columns. `ComplianceCertificate` is the first that does, and the
  table form raises `KeyError` there. Now keyed on `__mapper__.columns`
  (attribute names) and moved to `tests/api/conftest.py` as
  `assert_not_nullable_matches_schema`, shared by both router suites rather
  than copied.

**Nine mutations, all killed.** Notably one *found a weak test*: the status
boundary cases originally used `days(EXPIRING_WINDOW_DAYS)`, so changing the
constant to 59 or 61 moved input and expectation together and left them
green — the case restated the implementation instead of pinning it. Now
literal 60 and 61. The rest: expiring-today counted as expired; window
narrowed and widened; org filter dropped from the grouped list; property and
document ownership checks removed (each a 404-vs-500 case, since 0002's
composite FKs already make the write impossible); the date guard disabled;
the PATCH date guard reading only the request; `expiry_date` dropped from
`_NOT_NULLABLE` (which killed the relocated authority test — proving the
mapper-keyed lookup works on the renamed column).

**Scope explicitly NOT covered, and Mahmud should know:** `document_id` is
settable and validated, but **nothing in this system can create a
`documents` row for a certificate.** The only writer is `exports.py` and the
only buckets are `statements` and `exports`. Certificate *file upload* needs
a `certificates` bucket plus an endpoint, and is not in Task 17 — the spec's
MVP line is "manual entry of dates, expiry highlighting". Tests seed a
`documents` row directly to exercise the reference.


## Session 2026-08-04 — Task 16 COMPLETE (quarterly export endpoint)

`POST /exports/quarter` lands. Steps 2, 2b, 3 and 4 all ticked; 350 tests
green, ruff clean. Commits `2d4a08e`, `938818d`, `8b12a9b`, `9c09393`,
`b04f156`.

**Decisions taken here, because the plan did not settle them:**

- **Every transaction in the org is a candidate, not just the exporting
  entity's.** An unreviewed line has no category and no property, so there
  is no way to know whose figures it would land on; once reviewed it may
  attach to a jointly-owned property and move every co-owner's totals.
  Filtering by `transactions.entity_id` would filter on the one attribute
  that does *not* decide attribution. Costs the user a stricter block
  (anyone's unreviewed line stops everyone's export) and buys correctness.
- **Changed history is 409, not 422** — a new status idiom in this repo.
  Nothing about the request is wrong; what conflicts is previously filed
  state, and the UI has to distinguish "go review these lines" from
  "reconcile a filed period".
- **A separate `exports` storage bucket** (`0004_exports_bucket.sql`),
  mirroring 0003 including the do-not-cast-to-uuid trap. One bucket would
  work — the predicate is text-compared — but a filed return under a path
  saying `statements/` is a lie the next reader trips on.
- **`generated_document_id` points at the PDF.** It is a single FK and
  three files are produced; the CSVs come back in the response.

**Two gaps found by writing the tests, not by review:**

- `InvalidOwnershipError` did not name the property. Across twelve
  properties, "shares sum to 50" tells the user a return cannot be filed
  and nothing about how to fix it. `cumulative_totals` now re-raises with
  the property id — it is the innermost place that knows, since
  `split_amount` only ever sees a share map.
- History had to be compared against the **latest** version of each filed
  quarter (`DISTINCT ON (quarter) ORDER BY quarter, version DESC`).
  Comparing a superseded version would refuse a correct return: Q1 filed
  at 500 then re-filed at 1200 agrees with data that recomputes to 1200.

**Mutation results — six on the router, each killing exactly one test:**
drop the `assert_history_intact` call (without this the whole of Step 2b is
decorative and the core suite stays green); `version desc` → `asc`; filter
transactions by entity; drop the org filter from the transaction query
(the one that leaks *money*, not ids); pin version to 1; pre-sign the
amount instead of passing magnitude + direction. Four more on the new core
code. `__pycache__` cleared between every mutation and run — the stale-pyc
trap that invalidated two results earlier in this project.

**weasyprint needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on
macOS.** Homebrew installs libgobject/pango/cairo outside dyld's search
path, so `import weasyprint` raises `OSError` without it — pango being in
`brew list` is not sufficient. Now in `.env` and `.env.example`, and it
reaches the process through `uv run --env-file ../.env` (verified). The
import is at module scope on purpose: a missing rendering stack should
stop the API booting, not surface halfway through an export with CSVs
already in the bucket.

**Known limitation, not fixed:** an upload that succeeds followed by a
failed commit leaves orphaned objects in the bucket. `imports.py` has the
same characteristic. No reaper exists.

**Review round (advisor), four findings, all acted on:**

- **`except KeyError` was too wide.** It wrapped both `build_export_pack`
  and `assert_history_intact`, so any future dict-lookup bug anywhere in
  the core call tree would have become a confident 422 naming a "property"
  that might be any key at all — the exact pattern `imports.py` has a
  comment forbidding. The branch was also **written and never run**: no
  test covered a property with zero ownership rows. Both fixed:
  `MissingOwnershipError(KeyError)` raised at the lookup site, caught
  narrowly, and pinned at core and API level. Mutation: delete the raise so
  a plain `KeyError` escapes, and both new tests die.
- **The router docstring over-claimed coverage.** It said "every query
  filters `org_id` — see the isolation section", but only
  `_load_transactions`' filter is provable. The filters in
  `_load_ownerships` and `_load_filed_quarters` **cannot be killed by
  mutation** — ids are globally unique and 0002's composite FKs already
  guarantee the org matches — so they are belt-and-braces and the docstring
  now says so. House rule: name the test or say you did not check.
- **`export_status` had a dangling promise.** `0001_core.sql` deferred the
  closed set to Task 16, which is now ticked. Answer recorded in the SQL:
  the set is one value (`'generated'`), because a row records what *was*
  filed rather than tracking a submission through states — a re-export
  inserts a new version instead of moving an old row along. The states it
  was reserved for belong to the HMRC submission API, which iteration 1
  does not call. Left as `text`; an enum of one is a guess about the second.
- **The response was built after the session closed**, working only because
  `expire_on_commit=False`. `imports.py` and `transactions.py` both
  materialise before commit; exports now matches.

**Process failure worth recording — the same one, again.** `cd backend &&
cp src/core/quarters.py /tmp/q.bak` ran from inside `backend/`, so the `cd`
failed, the `&&` short-circuited, and **no backup was taken** — but the
heredoc that followed mutated the file anyway. Identical in shape to the
three verification-masking incidents earlier in this session: a command
whose result cannot gate what follows it. Repaired by re-applying the two
lines by hand and diffing against HEAD. The habit to build: run the
setup/verification step as its own command and read it, never chained.


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

**Task 13b spec review LANDED (after the handoff note was first written): ❌ ISSUES FOUND.** Method: 28 single-point mutations in a throwaway worktree against the live stack. Runtime behaviour correct at every probed point; 8 of 10 `org_id` filter sites each killed exactly one named guard test. **All gaps are in test discriminating power, not shipped behaviour** — the dangerous kind here, since Tasks 14–17 will copy this module as the "proved it" template. Full fix-round spec written into plan Task 13b **Step 4** (`4a`–`4d`), commit below. Highlights:
- **4b, highest severity:** `test_put_ownership_rejects_out_of_range_percentages` is a TAUTOLOGY — all four params send a *single* share, so each fails the sum-to-100 rule before the per-row bound is reached. Dropping `decimal_places=2`, `gt=0`, or `le=100` leaves all 76 api tests green. Measured: without `decimal_places=2`, `["33.333","66.667"]` returns **HTTP 200** and stores `33.33`/`66.67` — a **silent money mutation**, `numeric(5,2)` rounding it away with no error. Exact replacement params are in the plan. `le=100` is redundant-by-construction, not untested — noted so nobody "fixes" it.
- **4c:** the float-hazard test and a code comment both claim `33.33+33.33+33.34 == 99.99999999999999`. **CONTROLLER-VERIFIED FALSE — it is exactly `100.0`.** Reviewer's counterexample `0.01+64.04+35.95` → `100.00000000000001` also verified. Code is fine regardless (pydantic 2.12.5 parses JSON numbers to Decimal from raw text, no float intermediate); the *test* and *comment* are wrong. Fourth confident-wrong-detail of this project, and the first one found inside shipped code comments.
- **4a:** property-audit gap CONFIRMED by the reviewer, agreeing with the implementer and me. Plus a trap: `test_portfolio.py:512` reads `[-1]` under `order by created_at, action`, which `property.created` breaks on a `created_at` tie via the alphabetical secondary sort. Filter by action.
- **Concurrency DECIDED — pin and defer, not a spec violation.** Both race outcomes are LOUD: overlapping sets raise the unique violation; a 200%-summing set is refused by `core/splits.py:126` at the first apportionment. Money is never silently corrupted. One-line fix recorded for when it's wanted (`.with_for_update()` at `portfolio.py:541-543`, house-sanctioned since Task 18 mandates `FOR UPDATE SKIP LOCKED`).
- **NEW DECISION FOR MAHMUD (product):** `_PatchBody` null-rejection means `epc_rating`, `epc_expiry`, `address_line2`, and the PRS fields can be corrected but **never cleared** via the API. Spec silent. Plausibly wrong for a compliance app — a mis-entered EPC expiry should be removable.

**NEXT ACTIONS, in order:**
1. Task 13b **Step 4 fix round** — spec is fully written in the plan (4a–4d), no re-derivation needed. Also amend Step 2's wording, which caused 4a by reading as an exhaustive list.
2. Then stage-2 **code-quality review** of 13b. Do NOT tick 13b until both stages pass. (Stage 1 is ❌, so stage 2 has not been dispatched — order matters.)
   > **SUPERSEDED 2026-07-30 — do not read this line as current status.** Stage 2 *was* subsequently dispatched and returned ⚠️ APPROVED WITH NITS; its findings are plan Step 6 `6a`–`6g`, all since implemented (`f2ee07d`). **Both stages have passed.** Annotated on 2026-08-03 because I read this line, cached "stage 2 is outstanding", and repeated it to Mahmud four or five times across a session without re-checking the plan. Answer "what is still outstanding?" from the plan's checkboxes and outcome blocks, never from a handoff note — notes are true when written and are silently superseded by the next session's work.
3. Ask Mahmud the `_PatchBody` null-clearing question; it is a product call, not an engineering one.
4. Then Task 14 (imports endpoint). Step 0's storage groundwork is already researched and pinned (`ab2640b`) — measured baseline plus the `::uuid` policy-cast trap.

### DECIDED BY MAHMUD 2026-07-30: `_PatchBody` null-clearing → **allow null to clear**
An explicit `null` in a PATCH body wipes the field (`epc_rating`, `epc_expiry`, `address_line2`, `prs_registration_number`, `prs_registered_at`); **omitting** the key still leaves it untouched. Follows JSON Merge Patch (RFC 7386). Rationale: a mis-entered EPC expiry is a compliance problem and must be removable, not merely overwritable — the current 422 leaves a wrong value stuck short of direct DB access.
- **NOT part of the Step 4 fix round** (that agent was told explicitly not to touch `_PatchBody`). Land it as its own follow-up after Step 4 commits, with `Task 13b Step 5` added to the plan then — holding the plan edit until the agent finishes, since it was told the plan is canonical and could otherwise implement a requirement its brief excluded.
- Needs: `_PatchBody` distinguishing "key absent" from "key present and null" (pydantic `model_fields_set` / a sentinel, NOT `Optional` alone), tests for all three states per field (absent → unchanged, null → cleared, value → set), and an `audit_log` row on the clear since these are compliance fields (see 4a).
- Frontend note for Task 22's portfolio settings screen: the ownership/property editor must send `null` rather than omitting a key when the user empties a field.

**Deliberately NOT done overnight:** the fix round itself. Mahmud said "save for today, I need to sleep"; the shipped code is correct (the gaps are test-coverage), so nothing was urgent enough to justify working past an explicit stop.

**STILL NEEDED FROM MAHMUD (not blocking 14–18, blocking 19+):**
- Google Cloud OAuth client id/secret (Task 19, Flutter sign-in). Google-only auth, no email/password.
- The list of banks/accounts feeding the portfolio, personal and Ltd, so parser fixtures come from real statement exports (spec open question 3).
- Supabase cloud project when ready to leave the local stack. Anthropic key is optional now — categoriser runs on local Ollama.

**Working method that has been earning its keep:** ask subagents to push back with evidence rather than comply. Three confident-but-wrong claims surfaced today — one reviewer's mutation outcome (twice) and one of mine propagated into a plan constraint — and in every case the implementer who challenged it was right. Verify the discriminating command yourself before pinning anything. First task where the RLS-bypass constraint bites: brief requires an API-level two-org isolation test AND a filter-removal probe proving that test has teeth. Also pinned the three schema traps (0% ownership dies at the DB CHECK; duplicate entity_id hits the unique constraint; composite FKs firing means my filtering was wrong) and Decimal-not-float for the sum-to-100.

## Session 2026-08-04 — Task 8a COMPLETE (parser redesign for real bank formats)

**253 tests** (was 238), both orderings 137, env-free 115, ruff clean.

- **The design turned on something already in the schema.** HSBC's real export has **no header row**, and `_FORMATS` was keyed by header signature, so it could not be matched even in principle — and HSBC is 45% of allocated rows. The obvious move is to sniff content harder. The better one: `imports.source_bank` is **NOT NULL** (`0001_core.sql:293`), so the caller always knows the bank. The registry is now keyed by **name**, `parse_statement(path, *, bank)` requires it, and the header is demoted from *detection* to *verification*. Sniffing must guess; being told cannot. Uploading a Nationwide export under `bank="hsbc"` now raises `StatementFormatMismatchError` instead of feeding the wrong row parser and producing plausible, wrong money.
- Registered with real sanitised fixtures: **generic, hsbc, nationwide, starling, monzo, mettle**. Nationwide carries every awkwardness at once — `iso-8859-1`, a four-row preamble before its header, `£` inside amounts, `dd Mon yyyy` dates, and money split across `Paid out`/`Paid in`. Its pair collapses to **positive iff `Paid in`**, matching `quarters.py`; a row with neither or both raises rather than guessing.
- **Two of my own earlier claims corrected:** `_parse_generic_amount` already stripped thousands separators (the survey implied it was a gap), and two existing tests changed *meaning* rather than being weakened — an unrecognised header and an empty file used to mean "no format matches", and now mean "this file is not from the bank you named". Both renamed to say so.

### VERIFICATION TRAP, and it invalidated results before I noticed: stale `__pycache__` silently defeats mutation testing

Mutating `amount_index=4` → `amount_index=5` changes **one character, so the file size is unchanged**, and mutate-run-restore all happen **within the same second**. Python's bytecode cache keys on the source's **mtime truncated to whole seconds, plus its size** — so both were identical and the stale `.pyc` was reused. Two of four mutation results were therefore measured against unmutated bytecode and were **wrong**: they reported the *previous* mutation's failure, which looked plausible enough to accept.

**Always `find src tests -name '__pycache__' -type d -exec rm -rf {} +` between a mutation and its test run.** This matters more here than in most projects, because mutation testing is this project's primary evidence that a guard is real — a false mutation result is worse than none, since it certifies a test that does not discriminate.

**It immediately earned its keep.** Re-run cleanly, `monzo amount_index 7 → 9` left **all 28 tests green**: the fixture's `Amount` and `Local amount` columns held identical values, so reading the wrong one was undetectable — a test passing for the wrong reason, the same class the Step 5 reviewer found. The fixture's first row is now a genuine foreign-currency transaction (`-13.20` GBP against `-15.00` EUR) and the mutation dies. Four mutations now each kill exactly their target: Starling column index, Monzo column index, Nationwide sign flip, Nationwide preamble skip.

**Also, honestly: Starling/Monzo/Mettle were registered before their tests were written**, contrary to the test-first rule. Each assertion was then mutation-verified to establish the discrimination that test-first would have produced — but the order was wrong and is recorded rather than glossed.

## Session 2026-08-03 — Task 13b Step 5 COMPLETE, and a framework decision

- **Step 5 (nullable fields clearable) DONE**, TDD, `d9f3b4a`-ish. 236 tests, ruff clean, both orderings 136, env-free 100. Full detail and the three mutation results are in the plan's Step 5 outcome block. Headline: emptying `PropertyUpdate._NOT_NULLABLE` turns those 422s into **500s** (`IntegrityError`), so the new `ClassVar` set is proven load-bearing rather than assumed.
- **TASK 13b COMPLETE AND TICKED.** Both review stages passed (stage 1 → Step 4; stage 2 → Step 6, ⚠️ approved with nits), and Step 5 — written after both stages, so covered by neither — was reviewed independently on 2026-08-03.
  - **My own error, corrected:** I told Mahmud four or five times this session that stage 2 "had not been dispatched". It had, on 2026-07-30. I was repeating the `END OF DAY 2026-07-29` handoff note, which was true when written and superseded the next day, without ever re-reading the plan. The stale line is now annotated in place (`a96c19f`) rather than deleted, and the lesson is in memory as `recheck-status-claims-from-stale-notes`. **Answer "what is outstanding?" from the plan's checkboxes, never from a handoff note.**
  - **Step 5 review: ⚠️ approved with nits, no shipped bug.** `_NOT_NULLABLE` verified correct and complete on both models against the live catalog — the highest-risk item, clean. All three of my recorded mutation claims reproduced exactly, which is worth stating given this project's history of wrong mutation claims. The reviewer also caught a real error in my brief (I said HEAD was the commit under review; it was 12 commits back) and correctly showed it did not matter, because `git diff f296471 HEAD` on both files is empty.
  - **The finding worth remembering:** `_NOT_NULLABLE` and the tests' `NOT_NULL_*_FIELDS` were two hand-maintained copies. Misspelling `postcode` in **both at once** — the realistic error, since they are edited together — left the suite **fully green** while `PATCH {"postcode": null}` was a live 500, because `extra="forbid"` turns the unknown key into a 422 whose body still contains the field name, satisfying the assertion. Closed by `test_not_nullable_is_exactly_what_the_schema_says`, which derives the expected set from `Model.__table__.columns`; verified that the double-typo mutation now fails exactly that test and nothing else. 238 tests, ruff clean.
  - **Incident during the fix round, worth recording:** a `cd backend && cp ... && cp ...` chain short-circuited on a failed `cd`, so the intended scratchpad backups were never written — while the following `python3` heredoc mutated both files anyway. `git checkout` was not an option either, since the new test was uncommitted. Repaired by reversing the exact substitution and proving it by `git diff` on the router being empty. **Lesson: do not chain a backup behind a `cd` that can fail; and verify the backup exists before mutating.**
- **NEW DIRECTION FROM MAHMUD:** adopt his own paper, *A Disciplined Framework for AI-Assisted Software Development: DDD + TDD + Deep Modules + Small-Batch Delivery* (Hoque, Aug 2026), in this project. Decisions taken:
  - **Build the `/build-clean` skill** (three modes: new / refactor / audit) at `~/.claude/skills/`, with the `ENGINEERING.md`, `agent-briefing.md` and `audit-rubric.md` templates — §4 of the paper describes this tooling, and it **does not exist on this machine** (checked `~/.claude/skills/` and all of `~/projects`). So adoption means writing it, not invoking it.
  - **Sequencing:** Step 5 first (done), then the skill, then apply it here — using Step 5 as the worked example, so the framework arrives evidenced rather than theoretical.
  - **Scope discipline, from the paper's own §6.2:** no mass rename. The glossary binds Tasks 14–24 (new code); boy-scout renames only in files already being edited. The case study's 47-rename pass would churn the 236 tests that are this repo's main asset for zero behaviour change.
  - **Do not project the paper's §5.4 metrics** (41%→79% first-pass approval). That baseline was a 4,000-LOC TypeScript bridge at 12% coverage; this repo's is nothing like it. Adopt the mechanisms, measure separately.
  - **DONE (`a4bdfa2`):** skill written at `~/.claude/skills/build-clean/` (SKILL.md + `templates/ENGINEERING.md`, `templates/agent-briefing.md`, `templates/glossary-template.md` + `references/audit-rubric.md`, `references/deep-module-checklist.md`). Applied here: `docs/engineering-audit-2026-08-03.md` (**3.5/5**), `docs/domain/money.md`, `docs/domain/compliance.md`, repo-root `ENGINEERING.md` and `CLAUDE.md` (neither existed before).
    - **Audit's top finding — aggregates 2/5, the weakest dimension, in the hottest file.** The ownership-sums-to-100 invariant is enforced twice independently (`core/splits.py:113`, `routers/portfolio.py:650`) and owned by nothing. Deliberately not a DB constraint (`0001_core.sql:228-234`, reproduced not trusted). **Tasks 14–17 add four routers over the same shares and nothing stops them writing `property_ownership` directly.** Proposed fix: a `PropertyOwnershipSet` root in `src/core/` as the only way to obtain a valid set; the router keeps its own 422 shaping, only the *rule* moves.
    - Second fix: glossary before Task 14 (done — that is what `docs/domain/` is). Third: measure coverage; `pytest-cov` is absent (checked), so dimension 4 is scored on mutation evidence with no coverage number.
    - **Audit's honest gaps, recorded rather than hidden:** `src/flows/categorise.py` (370 lines, the product's core agent) was read only shallowly and should get its own pass before Task 18; `backend/evals/run_eval.py` (582 lines) unscored; `frontend/lib/` unscorable until Phase 7 exists.
    - **FIFTH confident-wrong detail of this project, and I wrote it into the glossary one commit after writing the rule against it.** `docs/domain/money.md` asserted the penny-sum invariant was "proven by a 20,000-trial stress test". **False.** That figure comes from this file's own Task 9 line — a one-off development run, never committed. The actual pinning test is `test_sum_invariant_holds_across_many_deterministic_combinations`: **60 combinations** (10 amounts × 6 share sets), deliberately deterministic. The runtime postcondition at `src/core/splits.py:104-108` is real and was verified. Corrected in `docs/domain/money.md`, with the 20k figure explicitly marked as not-coverage so it cannot be re-cited. **The lesson is not "be careful" — it is that rule 7 has to be applied to prose about tests, not only to prose about code, and that `progress.md` is a secondary source like any other.**
    - **Commit-granularity exception, stated rather than left implied:** `a4bdfa2` landed five documents as one commit, which `ENGINEERING.md` would refuse for source. The rule now carries an explicit carve-out for documents that only make sense read together, and an explicit refusal to extend it to anything under `src/`. An unenforced rule in a rules file teaches that the file is decorative.
    - **The skill's own methodology was not fully followed, deliberately.** `superpowers:writing-skills` requires baseline-testing a new skill by dispatching subagents *without* it and recording their rationalizations. Session instructions forbid spawning agents unless Mahmud asks, so it was validated instead by running `audit` mode against this repo for real. That is application evidence, not the pressure-testing the methodology wants. **Offer Mahmud the subagent baseline test if he wants the skill hardened.**
  - Assessment against the paper: TDD (§3.2.2) and small-batch delivery (§3.2.4) are **already ahead of the paper's baseline** here — mutation-testing every new guard is stronger than anything §3.2.2 specifies. The real gap is the **domain layer: there is no `docs/domain/`, no glossary at all.** Three live collisions for it to resolve: `entity` (ownership entity vs the generic sense in `scoping.get_owned_or_404`/`not_found(kind=...)`), `org` vs `entity` (tenant boundary vs tax filer — conflating them writes a cross-tenant bug that RLS will not catch on API paths), and `property`'s money face vs compliance face. **Task 13b Step 4a was caused by exactly that last collision** — a plan that enumerated money-only cases for a table carrying both. That is this repo's own evidence for the paper's §3.2.1, not the paper's.

## Session 2026-08-03 — Supabase cloud project created (PARKED), and a blocker found for cut-over

**Project created** via the Supabase MCP, with cost confirmed at **$0/month** (Free tier) beforehand:
- **ref `yxaemjkhfjjajdyfxegu`**, name `landlord-compliance`, region **eu-west-2 (London)** for UK bank/HMRC data residency, org `zpijywqmtdjnzkwaccxu` ("Mahmudul Hoque's Org"), status `ACTIVE_HEALTHY`.
- **Deliberately parked.** Local stack remains the dev loop; no `supabase link`, no `db push`, no cloud credentials in `.env`. Nothing about the 236-test loop changed.
- Three pre-existing projects were found, all paused: `hoquem's Project` and `KICKAI` (eu-north-1), and **`Property Portfolio` (eu-west-2, Sep 2025)**. The last one is in the right region with a suggestive name, so reuse was offered — Mahmud chose a fresh project. **`Property Portfolio` was left untouched**: it was never restored, inspected, or migrated against, because running `0001_core`/`0002_rls` on top of unknown existing schema is not something to do on a guess.

### BLOCKER FOR CUT-OVER (not for now): the cloud project signs JWTs with **ES256**, the local stack with **HS256**

Measured, not assumed. `https://yxaemjkhfjjajdyfxegu.supabase.co/auth/v1/.well-known/jwks.json` publishes a single **ES256** (EC P-256) verification key. The local stack's anon key header decodes to `{"alg":"HS256","typ":"JWT"}` and its `JWT_SECRET` is the shared symmetric secret.

**Consequence:** `src/api/auth.py` verifies with an explicit **HS256-only allowlist** against `SUPABASE_JWT_SECRET`, and `test_hs512_token_signed_with_the_real_secret_is_401` (`tests/api/test_auth.py:72`) exists specifically to fail if that allowlist is widened — its point being that anyone who knows the shared secret can sign HS512 as easily as HS256, so pinning the *family* would not be pinning anything. Against this cloud project, **every valid user token would be rejected**. This is implementation work, not configuration — and it is precisely why "create and park" was the right call over cutting over.

**Options at cut-over, to be decided then, not now:**
1. **Verify asymmetrically against JWKS** — fetch and cache the project's public key, accept ES256, keep the `aud`/`exp`/`sub` requirements exactly as they are. Correct long-term direction and what Supabase now defaults to; needs key fetching, caching, and rotation handling that does not fail open.
2. **Check whether this project still exposes a legacy HS256 shared secret** and use it. Not determinable through the MCP tools — it needs the dashboard's API/JWT settings page. If available it is the smaller change, but it is the direction Supabase is moving away from.
- Either way, **`test_hs512_token_signed_with_the_real_secret_is_401` (`:72`) and `test_token_with_non_string_subject_is_401` (`:143`) are the tripwires**: widen the allowlist deliberately and re-pin it, never delete the tests. Both names verified against the file rather than recalled.
- **Do not treat local green as cloud green.** The entire `tests/api` suite mints HS256 tokens locally, so it will stay green while cloud auth is completely broken. Any cut-over needs a test against a real cloud-issued token.

**Still to do on the Google client** (Mahmud, one console edit): add `https://yxaemjkhfjjajdyfxegu.supabase.co/auth/v1/callback` to the same OAuth client's Authorised redirect URIs, alongside the existing local one.

**Free-tier note:** projects auto-pause after ~1 week idle; the other three are all paused now. Resuming is one click, but a paused project will refuse connections, which looks like an outage if you have forgotten.

## Session 2026-08-02/03 — unblocking Task 19 prerequisites (PAUSED mid-flight)

Mahmud chose to clear the three long-standing inputs (Google OAuth, bank list, Supabase cloud) ahead of Task 13b Step 5. **Step 5 remains unticked and 13b remains unticked** — this is a deliberate detour, not a completion.

**Decisions taken:**
- Supabase cloud: **create and park** (project exists, local stack stays the dev loop), **Mahmud creates it in the dashboard**, region **London / eu-west-2** for UK bank + HMRC data residency. Cut-over is a separate future task. Project ref not yet supplied.
- Google OAuth credentials live in the **repo `.env` only**; the two exports are to be removed from `~/.zshrc`.
- The ambient client is **another project's** — a fresh client is to be created for this app.

**Config landed (uncommitted at time of writing):**
- `supabase/config.toml`: `[auth.external.google]` enabled, `client_id`/`secret` via `env()`, `skip_nonce_check = true` (required for local Google sign-in, per the stock `[auth.external.apple]` comment).
- `additional_redirect_urls` corrected from the stock `https://127.0.0.1:3000` — **no local Flutter dev server serves https**, so Task 19's redirect would have failed. Now lists `http://127.0.0.1:3000` and `http://localhost:3000` (matched exactly; Chrome launches on `localhost`, `site_url` uses `127.0.0.1`).
- `.env.example` + `.env` gained `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.
- 208 tests green after the change and a stack restart.

**GOOGLE OAUTH CLIENT LANDED 2026-08-03.** Dedicated GCP project `landlord-compliance`, client type **Web application**, client id `<redacted>.apps.googleusercontent.com` (distinct from the ambient `<another project>-…`, which belongs to another project). One redirect URI registered: `http://127.0.0.1:54321/auth/v1/callback`. No JavaScript origins — the browser never talks to Google here; Supabase does the exchange server-side. Credentials written to the untracked `.env`.
- **Verified against the live GoTrue, not assumed:** `/auth/v1/authorize?provider=google` returns 302 to `accounts.google.com` carrying **our** client id, the matching `redirect_uri`, and `scope=email profile` (non-sensitive only, so Google requires no verification review). 236 tests still green after the restart.
- **The stack was restarted under `env -u GOOGLE_OAUTH_CLIENT_ID -u GOOGLE_OAUTH_CLIENT_SECRET`.** It had to be: `~/.zshrc` still exports the other project's client, and the process environment outranks the repo `.env` (see the trap below). **Until those exports are gone, a `supabase start` from Mahmud's normal shell silently reverts the stack to the wrong client.** Cleanup is his to do — the exports must first move into the other project's own `.env`, or deleting them breaks it.
- **Consent screen PUBLISHED (In production) 2026-08-03**, on Mahmud's report — *not* independently verified, because publishing status is not observable from the authorize redirect or any unauthenticated endpoint. Scopes are non-sensitive only (`email`, `profile`), so Google required no verification review. This removes Testing mode's 7-day refresh-token expiry, which would otherwise have forced a weekly re-sign-in and looked exactly like a bug.
  - **"In production" is not an exposure change.** Any Google account can now complete the *Google* half of sign-in, but authentication and authorization are separate here: `require_auth` returns **403** for a valid token whose user has no `public.users` row (Task 13, pinned by test). A stranger gets a 403 and sees nothing.
- **Still to add when the cloud project exists:** `https://<project-ref>.supabase.co/auth/v1/callback` on the same client. Editing a client's redirect URIs is a trivial console edit, so this was deliberately not treated as a blocker (an earlier note in this file said hold the client until the ref existed — that was over-cautious).
- **Secret hygiene: DONE.** Mahmud deleted the downloaded `client_secret_*.json` from `~/Downloads`. Verified afterwards that `.env` still carries both values and the stack still returns 302 with the right client, so nothing was lost. The secret remains viewable in the Cloud Console under Credentials if it is ever needed again.
- **`~/.zshrc` CLEANED 2026-08-03** (backup: `~/.zshrc.backup-20260803-230549`). Both `GOOGLE_OAUTH_*` exports removed, so a fresh terminal now picks up this repo's `.env` naturally — no more `env -u` workaround.
  - **My earlier warning was wrong and is retracted:** I said deleting them would break another project. Before deleting I actually checked, and **nothing on this machine reads those variables from the environment** except this repo's `supabase/config.toml`. The `~/projects/me` / `gws` tooling that shares the same client reads `~/.config/gws/client_secret.json` — the file, not the env. Lesson repeats: I asserted a dependency without checking, exactly the pattern this project keeps catching.
  - **`OAUTHLIB_INSECURE_TRANSPORT=1` deliberately kept.** `hoque-property/scripts/auth_drive.py` needs it: `run_local_server()` gets an `http://localhost` redirect and oauthlib's `is_secure_transport()` (read in the installed source at `.venv/.../oauthlib/oauth2/rfc6749/utils.py:79-83`) accepts non-https **only** when this is set — there is no localhost exemption. Verified, not assumed. Outstanding wart: at global scope it disables that check for every Python process on the machine. One-line fix available (`os.environ.setdefault` inside `auth_drive.py`), in a different repo, not done.
  - Also fixed an unrelated real bug found while reading the file: `$ANDROID_HOME` was used on four PATH lines *before* it was defined, putting literal `/platform-tools` and `/cmdline-tools/latest/bin` on PATH. Confirmed present before (2 entries) and zero after, with `adb` still resolving.

**MEASURED TRAP — process env silently outranks the repo `.env`.** The plan assumed the CLI reads the repo-root `.env` for `env()` substitution. It does — but only when the variable is *absent* from the process environment. Discriminating test, both halves run: with `~/.zshrc`'s exports live, `/auth/v1/authorize?provider=google` redirected to Google with the **ambient** client id; re-running `supabase start` under `env -u GOOGLE_OAUTH_CLIENT_ID -u GOOGLE_OAUTH_CLIENT_SECRET` redirected with the **`.env`** value. So a correctly-filled repo `.env` can be silently overridden by a dotfile, and `supabase status` does **not** validate the auth block at all — it reported healthy in both states. The live-GoTrue check is the only honest one: `curl -s -o /dev/null -w '%{redirect_url}' "http://127.0.0.1:54321/auth/v1/authorize?provider=google"`.
- **Consequence for Mahmud:** deleting the exports from `~/.zshrc` will break whichever other project depends on them; move them into that project's own `.env` first.
- **Current live-stack state:** the running GoTrue still holds the throwaway probe client id. Harmless (no Google sign-in exists yet) and deliberately not "fixed" — restarting with `enabled = true` and an empty `client_id` risks the stack failing to boot, which was not worth gambling a green suite on for cosmetics. Next restart with real creds settles it.

**Also spotted, not acted on:** `.env` has no `CATEGORISER_MODEL`, though `.env.example` documents it as required and the flow fails loudly without it. The suite passes because tests set it themselves. Flagged rather than silently patched.

**Still outstanding on this detour:** Supabase project ref; the Google client itself (consent screen walkthrough delivered, client creation waits on the ref so both redirect URIs land on one client); the bank list (bank, owner, exact CSV header row, 3–5 sanitised rows per account — `backend/tests/fixtures/statements/` is tracked, so real account numbers/balances/counterparties must not go in).

## Session 2026-07-28
- Created planning files
- Dispatched 3 parallel research agents (property, investments, AI business)
- Property findings saved to findings.md ✔
- AI-business findings saved to findings.md ✔
- Awaiting: investments agent
- Next: save investments findings, then synthesize ranked report
