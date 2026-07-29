# Landlord Compliance Agent — Design Spec

**Date:** 2026-07-28
**Status:** Approved by Mahmud (design v2), pending spec review
**Author:** Claude + Mahmud (brainstorming session)

## Context

UK landlord compliance is being forcibly digitised: Making Tax Digital for Income Tax applies from April 2026 to landlords with >£50k qualifying income (>£30k from 2027, >£20k from 2028); the Renters' Rights Act went live 1 May 2026 (Section 21 abolished, periodic tenancies, once-per-year Section 13 rent rises); the PRS database rolls out late 2026; EPC C is required by October 2030.

Mahmud holds a mixed personal + Ltd property portfolio, currently managed via spreadsheets and an annual accountant handoff — a workflow MTD breaks. He is an experienced Python/Flutter developer. The product is built for his own portfolio first, then productised for other small portfolio landlords (research: no incumbent combines enforced HMRC category mapping + RRA workflows + PRS-database fields; closest competitor Hammock lacks RRA/compliance workflows).

## Goals (MVP / iteration 1)

1. Replace the spreadsheet: digital income/expense records per property, compliant with MTD digital-record requirements.
2. Statement upload (CSV) → AI-proposed categorisation into exact HMRC categories with per-property allocation → human review/confirm → confirmed ledger.
3. Cumulative year-to-date quarterly export pack (CSV + PDF) per ownership entity, suitable for an accountant or bridging software to submit.
4. Static compliance-certificates page: manual entry of gas/EICR/EPC/licence dates, expiry highlighting.
5. Multi-tenant-ready foundations (org-scoped RLS) so pilot customers onboard without re-architecture.

## Non-goals (deferred)

- **Iteration 2:** Section 13 rent-review flow, weekly compliance-scan agent + email alerts, PDF statement parsing, structured deposit/PRS record completion.
- **Iteration 3+:** direct HMRC filing via MTD API (developer registration started in parallel during iteration 1), letting-agent statement reconciliation, HMO units, rent schedules/arrears, tenant contacts, ombudsman fields (mandatory 2028), mortgages/Section 24 computation.
- No payments, no tenant-facing portal, no marketing site.

## Architecture

**Approach: deterministic core, agents at the edges.** A typed Postgres ledger owns all money and compliance data. CrewAI agents perform judgment work (categorisation, drafting, scanning) and their outputs land as *proposals* with confidence scores that the user confirms — agents never write directly to confirmed records. Rationale: HMRC penalties make silent AI errors unacceptable; industry data shows most agent deployments fail on reliability.

### Components

| Component | Tech | Responsibility |
|---|---|---|
| Frontend | Flutter (web build now; iOS/Android later from same codebase) | Dashboard, review/confirm screens, uploads, exports |
| Design system | Material 3 Expressive + flutter_animate | See "Design system & motion" below |
| API | FastAPI (Python, uv) | Business logic, validation, export generation, auth via Supabase JWT |
| Worker | Python process running CrewAI Flows | Background agent jobs, triggered via job queue rows (Postgres) — never inline in web requests |
| Agent flows | CrewAI (scaffolded with `crewai create flow landlord_compliance`) | `CategoriseStatementFlow` (MVP); `RentReviewFlow`, `ComplianceScanFlow`, `QuarterlySummaryFlow` (iteration 2+) |
| Data/auth/storage | Supabase (Postgres, Auth, Storage) | Ledger, documents, RLS multi-tenancy keyed on `org_id` |

### Data flow (MVP happy path)

1. User uploads bank statement CSV → stored in Supabase Storage, `imports` row created.
2. Deterministic parser extracts lines → `transactions` rows with status `unclassified`. Parse failures mark the import failed and are surfaced in the UI — no silent line skipping.
3. Worker picks up the import job → `CategoriseStatementFlow` runs a CrewAI agent with structured Pydantic output: per line, proposes `hmrc_category`, property allocation, capital-vs-revenue flag, confidence. Prior confirmed transactions for the org are provided as few-shot context (learning from corrections).
4. Proposals land as status `proposed` with confidence. UI batch-review screen: confirm/correct each line (low-confidence lines flagged prominently). Confirmation sets status `confirmed` and writes `audit_log`.
5. Quarter-end (or on demand): API computes **cumulative year-to-date totals** per entity from confirmed transactions (weighted by ownership percentage for jointly-held properties) → export pack (CSV + PDF) → `mtd_quarters` row records totals and export status.

## Data model

All tables carry `org_id` (RLS) plus `created_at`/`updated_at`. Validated against HMRC MTD ITSA requirements and comparable products (Hammock, Landlord Studio, APARI, Landlord Vision) — see findings, 2026-07-28.

### Core (MVP)

- **orgs** — tenant. **users** — Supabase auth, belong to org.
- **entities** — ownership entities: individual (tax regime MTD-ITSA) or Ltd company (corporation tax). Fields for PRS registration (number, registered_at) present but optional in MVP.
- **properties** — address, EPC rating + expiry, finance_cost_classification (residential / non_residential), bedroom_count, licensing flag.
- **property_ownership** — junction: property_id, entity_id, ownership_percentage. Joint ownership is MVP-critical: each beneficial owner reports their share on their own return (HMRC PIM1035).
- **tenancies** — property_id, rent amount + frequency, start date, periodic status, last_rent_increase_date (drives Section 13 eligibility in iteration 2), deposit summary fields (structured `deposits` table lands iteration 2).
- **imports** — uploaded statement file ref, source bank, period, status (pending/parsed/failed), error detail.
- **transactions** — import_id, entity_id, property_id (nullable until allocated), date, amount, direction, description, **hmrc_category** (enum: rent_income, other_property_income, rent_paid, rates_insurance_ground, repairs_maintenance, finance_costs_residential, finance_costs_nonresidential, legal_professional, service_costs, travel_vehicle, other_allowable, replacement_domestic_items, use_of_home_allowance, **capital_expense**, personal_non_business), status (unclassified/proposed/confirmed/excluded), confidence, proposed_by (agent run id).
  - **`entity_id` semantics:** `entity_id` records the bank-account owner from the import — provenance, not tax attribution. For property-allocated transactions, per-entity export totals derive **exclusively from the `property_ownership` percentage split** (PIM1035), never from `transactions.entity_id`; there is one correct computation, not two. For transactions with `property_id` null (unallocated or non-property), `entity_id` determines whose ledger the line sits on pending allocation or exclusion.
- **compliance_certificates** — property_id, type (gas_safety / eicr / epc / hmo_licence / selective_licence), issue date, **expiry date**, certificate ref, document_id, derived status (valid/expiring/expired).
- **documents** — Supabase Storage refs: uploaded certificates; generated notices/exports.
- **mtd_quarters** — entity_id, tax_year, quarter, **cumulative YTD totals per HMRC category**, export status, generated document_id. Submissions are cumulative — each quarterly submission replaces the prior; store a version/generated_at per export.
  - **Quarter basis:** default is UK tax-year quarters (from 6 April: Q1 to 5 Jul, Q2 to 5 Oct, Q3 to 5 Jan, Q4 to 5 Apr). HMRC permits a calendar-quarter election; modelled as a per-entity setting (`quarter_basis`: tax_year | calendar_election) that keys the cumulative computation. MVP ships tax-year basis only; the setting exists so the election doesn't force a schema change.
  - **Scope:** only entities with tax regime MTD-ITSA get `mtd_quarters` rows. Ltd entities get a separate simple P&L export artifact (see Open question 2) — Goal 3's "export pack per ownership entity" means MTD quarters for individuals, P&L for companies.
- **job_queue** — worker jobs: type, payload, status (queued/running/done/failed), error detail. Failed jobs are visible in the UI.
- **audit_log** — actor (user/agent/system), action, before/after, timestamp — on every state change to money or compliance data.

### Iteration 2+ tables (designed, not built in MVP)

`deposits` (scheme, reference, protected_date, prescribed_info_date, status), `notices` (Section 13 et al: type, served date, method, effective date), `mortgages`, `letting_agents` + `agent_statements`, `property_units` (HMO), `rent_schedules`, `tenant_contacts`, ombudsman fields on entities.

## Agent design (MVP: CategoriseStatementFlow)

- CrewAI **Flow** with structured Pydantic state; the categorisation step uses `Agent.kickoff()` with `response_format` (typed output, retried on validation failure at the framework layer).
- Inputs: parsed lines, org's property list, org's previously confirmed transactions (few-shot).
- Output per line: hmrc_category, property_id or null, capital flag, confidence 0–1, one-line rationale.
- Confidence threshold (initial 0.8, tunable): below it, the line is visually flagged "needs attention" in review. **No auto-confirmation at any confidence in MVP.**
- Flow errors mark the job failed with the error surfaced — no partial silent results.

## Design system & motion

Requirement from Mahmud: a polished design system with motion.

- **Foundation: Material 3 Expressive** — Flutter-native, seed-based `ColorScheme` with adaptive light/dark, and M3's motion system (spring physics, emphasized/standard easing) for navigation transitions and state changes.
- **Micro-interactions: `flutter_animate`** — staggered list entrances on the review screen, confidence-flag pulses, confirm-action feedback, dashboard status transitions.
- **Tokens centralised** in a single theme package (colours, type scale, spacing, radii, durations/curves) so productisation-era branding is a token swap, not a rewrite.
- **Motion principles:** purposeful and fast (150–350ms; nothing decorative blocking input), consistent easing from the token set, and `MediaQuery.disableAnimations` respected for reduced-motion accessibility.
- UI implementation work loads the `impeccable` frontend-design skill for hierarchy/polish decisions.

## Error handling

Fail loudly and early throughout (house rule):
- Parser: unknown CSV format or malformed rows → import marked failed with row-level detail; never skip lines silently.
- Agent flow: exceptions propagate to job failure, visible in UI with error detail.
- Export: refuses to generate if any transaction in the period is `unclassified` or `proposed` — lists the blockers instead.
- Validation: ownership percentages per property must sum to 100; cumulative totals must never decrease quarter-over-quarter (guard against data deletion after export).

## Testing

- **pytest** on all deterministic logic: parser (fixture CSVs from real banks), category totals, ownership-split maths, cumulative quarter computation, export refusal rules.
- **Golden-set eval** for the categorisation agent: labelled set built from Mahmud's own confirmed statements; accuracy and per-category confusion tracked run-over-run in CI. This agent is the product core; regressions must be visible.
- **RLS tests**: second dummy org; assert zero cross-tenant reads/writes.
- **Snapshot tests** on export pack formats.

## Security & multi-tenancy

**Sign-in method (decision 2026-07-29):** Google OAuth only, via Supabase Auth (`signInWithOAuth`, google provider). No email/password UI. Requires a Google Cloud OAuth client (ID + secret) configured in `supabase/config.toml` `[auth.external.google]` — input needed from Mahmud at Task 19. The email provider stays enabled internally for service-side test plumbing (auth-admin-created users in RLS tests) — it is never exposed in the app UI. When the iOS app ships, App Store rules require adding Apple sign-in alongside Google (tracked, out of MVP scope).

Supabase RLS on `org_id` for every table; storage buckets namespaced per org; API validates Supabase JWT and scopes all queries; agent flows receive only the calling org's data. Bank statements are sensitive — no statement content in logs; agent prompts include only what categorisation needs.

## Rollout

1. **Iteration 1 (~2–3 weeks):** scaffold (CrewAI CLI + Flutter + FastAPI + Supabase), core tables, CSV ingest → categorise → review → confirm, cumulative quarterly export, static certificates page. Mahmud's portfolio is the pilot dataset.
2. **In parallel:** begin HMRC developer-hub registration (sandbox) toward recognised-software listing for iteration-3 direct filing.
3. **Iteration 2:** Section 13 flow, compliance-scan agent + email alerts, PDF parsing, deposits/PRS completion.
4. **Validation gate:** run a full real quarter on Mahmud's own portfolio before approaching 2–3 pilot landlords.

## Open questions / risks

1. **Per-property vs per-business HMRC reporting:** validation research claimed each property reports independently; standard treatment is one "UK property business" per person with aggregated totals. Design keeps per-property allocation internally and aggregates per entity for export (correct under either reading). **Verify against HMRC Property Business API docs before building the export format.**
2. **Ltd-side scope:** MVP gives the Ltd entity the same ledger + a simple P&L export (no CT600 ambitions). Accountant remains in the loop.
3. **CSV variance across banks:** start with the banks Mahmud actually uses; add formats on demand; import failure UX matters more than format coverage. **Day-one input needed from Mahmud: the list of banks/accounts feeding the portfolio (personal and Ltd), so parser fixtures can be built from real statement exports.**
4. **CrewAI conversational surface** deliberately deferred — chat UX may layer on in a later iteration once the deterministic core is trusted.
