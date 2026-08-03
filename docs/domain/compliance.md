# Domain — Compliance context

The context that tracks whether a let dwelling is legally lettable: current
certificates, licensing, registration, and the audit trail proving when each
fact changed.

**Sibling context:** `money.md`. They share the word `property` and mean
different things by it — see *Context map*.

## Glossary

| Term | Definition | Is NOT | Synonyms in use | Related |
|---|---|---|---|---|
| **Property** *(compliance face)* | A **dwelling** with a physical address, an energy rating, a licensing status and a set of certificates that expire. | An apportionment key — that is `money.md`'s property. Here nobody cares who owns what share. | "unit", "dwelling" | Compliance certificate, EPC |
| **Compliance certificate** | A dated document proving one legal requirement is met. Type is one of exactly three: `gas_safety`, `eicr`, `epc`. | A warranty, insurance, or an inspection report. The enum is closed. | "cert" | Expiry date |
| **Expiry date** | When a certificate stops being valid. **Required** — a certificate without one cannot express the only question worth asking of it. | The issue date + a fixed term. Terms differ by type and change in law. | — | Compliance certificate |
| **Issue date** | When the certificate was granted. Optional — often unknown for inherited paperwork. | The expiry date. | — | Compliance certificate |
| **EPC rating** | The energy efficiency band (`A`–`G`) on a property's current EPC. | The EPC certificate itself. The rating is denormalised onto the property for fast filtering; the certificate row is the record. | "energy rating" | EPC expiry |
| **EPC expiry** | When the property's current EPC lapses. Denormalised onto the property alongside the rating. | A certificate row. Same denormalisation caveat. | — | EPC rating |
| **Licensing flag** | Whether the property requires a council licence (selective, additional, or HMO). Boolean, NOT NULL, defaults false. | The licence itself, its reference, or its expiry — none are modelled in the MVP. | "licensable" | — |
| **PRS registration** | Private Rented Sector registration held by an **entity**, not a property: a number and a date. | A property licence. PRS registration is the landlord's; licensing is the dwelling's. | — | Entity |
| **Tenancy** | An occupancy period for a property. Present in the schema; not yet exercised by the MVP. | A lease document or a rent schedule. | "let", "AST" | Property |
| **Audit log** | An append-only record of every state change to money **or compliance** data: actor, action, before, after, timestamp. | A debug log or an activity feed. It is evidence, and it is written in the same transaction as the change it records. | "trail" | — |
| **Actor** | Who caused a change: `user`, `agent`, or `system`. | The authenticated user only. An agent-proposed categorisation is an `agent` action, and the distinction is the point. | — | Audit log |

## Invariants

- **Every state change to compliance data writes an `audit_log` row**, in the
  same transaction as the change. Enforced in `backend/src/api/routers/portfolio.py`
  (`property.created`, `property.updated`, `entity.*`); pinned by
  `test_create_property_writes_an_audit_row` and
  `test_patch_property_writes_an_audit_row_with_before_and_after`.
- **A cleared field appears in the audit `after` as `null`, not omitted.** A
  reader reconstructing state cannot distinguish an omitted key from an
  unchanged one. Pinned by
  `test_clearing_a_property_field_writes_an_audit_row_showing_the_null`.
- **Compliance fields are clearable.** An explicit `null` wipes `epc_rating`,
  `epc_expiry`, `address_line2`, `bedroom_count`, `prs_registration_number`,
  `prs_registered_at`; omitting the key leaves them alone (RFC 7386). A
  mis-entered EPC expiry is itself a compliance problem and must be removable.
  NOT NULL columns still refuse `null` with a 422 — the `_NOT_NULLABLE`
  ClassVar on each PATCH body names them, and emptying it turns those 422s
  into 500s.
- **A certificate's expiry date is required.** `NOT NULL` in
  `supabase/migrations/0001_core.sql`.

## Aggregates

| Aggregate root | Owns | Entry point | Invariant it protects |
|---|---|---|---|
| **Property** *(candidate, not yet built)* | its `compliance_certificates` rows | Task 17's certificates CRUD | every certificate change is audited; certificates belong to exactly one property in one org |

Not yet built — Task 17. Recording it here so the router is designed as an
aggregate rather than discovered to need one afterwards, which is what
happened on the money side.

## Context map

| Neighbour | Relationship | Translation at the seam |
|---|---|---|
| **Money** (`money.md`) | Shared kernel on `Org`, `Entity`, `Property` *identity* only | Shared **id**, not shared meaning. Compliance reads `epc_*`, `licensing_flag`, certificates; money reads `finance_cost_classification` and ownership. A module reaching across is the seam being crossed. |
| **Documents / storage** | Customer/supplier | A certificate may reference a stored `document_id`. Storage paths and bucket policy stay behind that reference. |

**The seam that has already drawn blood.** `properties` is one table carrying
both faces. Task 13b Step 2 enumerated only the money reasons to audit a
property mutation, so the router shipped writing no audit rows for
`epc_expiry` or `licensing_flag` changes. The spec had always required
auditing "money **or** compliance" data; the plan was narrower than the spec,
and one table wearing two faces is why nobody noticed. **When you touch
`properties`, state which face you are working on.**

## Terms deliberately excluded

- **Ownership share, apportionment, HMRC category, quarter, tax year** — money
  terms. A compliance module computing a split is a design error.
- **"Compliant"** as a property-level boolean. It is a function of several
  certificates with different expiries and is never stored; deriving it and
  caching it would go stale silently.
- **"Renewal"** — implies an automated process the MVP does not have.
