"""SQLAlchemy 2.0 declarative models mirroring ``supabase/migrations/0001_core.sql``.

All 13 MVP tables. Mechanical column-for-column mapping -- no ORM
``relationship()`` attributes, no CHECK/UNIQUE constraints, no composite FKs
from ``0002_rls.sql`` (those are DB-enforced tenant-isolation belt-and-braces,
guarded by ``backend/tests/db/test_rls.py``, not something the ORM layer
needs to re-express). Postgres enum types already exist in the database
(created by the migration); columns map onto them with
``create_type=False`` rather than redefining them.

:seealso: supabase/migrations/0001_core.sql; backend/src/core/categories.py
    (the single source of truth ``hmrc_category`` mirrors).
"""

import datetime
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.categories import HmrcCategory

# ---------------------------------------------------------------------------
# Postgres enum types (already created by 0001_core.sql -- create_type=False
# so SQLAlchemy never tries to (re)create them via DDL).
# ---------------------------------------------------------------------------

tax_regime_enum = PGEnum("mtd_itsa", "corporation_tax", name="tax_regime", create_type=False)
quarter_basis_enum = PGEnum("tax_year", "calendar_election", name="quarter_basis", create_type=False)
#: Whether payments on a property's borrowing need an interest/capital split.
#: Three values rather than a boolean: "no borrowing" is not the same claim as
#: "borrowing where the whole payment is interest", and a boolean would force
#: one of them to be a lie. See supabase/migrations/0005_mortgage_split.sql.
mortgage_type_enum = PGEnum(
    "none", "interest_only", "repayment", name="mortgage_type", create_type=False
)

finance_cost_classification_enum = PGEnum(
    "residential", "non_residential", name="finance_cost_classification", create_type=False
)
import_status_enum = PGEnum(
    "pending", "parsed", "failed", "categorisation_failed", name="import_status", create_type=False
)
#: The single Python-side source of truth (``HmrcCategory``) drives this
#: column's DB-side labels via ``values_callable`` -- a plain
#: ``PGEnum("rent_income", ...)`` listing would be a second place the 15
#: values could drift from ``src/core/categories.py``.
hmrc_category_enum = PGEnum(
    HmrcCategory,
    name="hmrc_category",
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
transaction_status_enum = PGEnum(
    "unclassified", "proposed", "confirmed", "excluded", name="transaction_status", create_type=False
)
transaction_direction_enum = PGEnum("in", "out", name="transaction_direction", create_type=False)
certificate_type_enum = PGEnum(
    "gas_safety",
    "eicr",
    "epc",
    "hmo_licence",
    "selective_licence",
    name="certificate_type",
    create_type=False,
)
mtd_quarter_number_enum = PGEnum("Q1", "Q2", "Q3", "Q4", name="mtd_quarter_number", create_type=False)
job_status_enum = PGEnum("queued", "running", "done", "failed", name="job_status", create_type=False)
actor_type_enum = PGEnum("user", "agent", "system", name="actor_type", create_type=False)


class Base(DeclarativeBase):
    """Shared declarative base for every model in this module."""


# ---------------------------------------------------------------------------
# Column factories -- reduce boilerplate for the patterns repeated across
# (almost) every one of the 13 tables. Purely mechanical; no business logic.
# ---------------------------------------------------------------------------


def _uuid_pk() -> Any:
    """Primary key column: ``uuid primary key default gen_random_uuid()``."""
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _org_id_fk() -> Any:
    """``org_id uuid not null references orgs(id)`` -- present on every table but ``orgs``."""
    return mapped_column(PGUUID(as_uuid=True), ForeignKey("orgs.id"), nullable=False)


def _created_at() -> Any:
    """``created_at timestamptz not null default now()`` -- present on every table."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


def _updated_at() -> Any:
    """``updated_at timestamptz not null default now()`` -- absent only on ``audit_log``."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---------------------------------------------------------------------------
# orgs -- tenant root. No org_id column: this table IS the tenant.
# ---------------------------------------------------------------------------
class Org(Base):
    """Mirrors ``public.orgs``."""

    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# users -- mirror of auth.users, scoped to an org.
# ---------------------------------------------------------------------------
class User(Base):
    """Mirrors ``public.users``.

    ``id`` deliberately has no ``server_default`` -- unlike every other
    table's primary key, it FKs ``auth.users(id)`` and is never
    DB-generated.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# entities -- ownership entities (individual / Ltd company).
# ---------------------------------------------------------------------------
class Entity(Base):
    """Mirrors ``public.entities``."""

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tax_regime: Mapped[str] = mapped_column(tax_regime_enum, nullable=False)
    quarter_basis: Mapped[str] = mapped_column(
        quarter_basis_enum, nullable=False, server_default=text("'tax_year'::quarter_basis")
    )
    prs_registration_number: Mapped[str | None] = mapped_column(Text)
    prs_registered_at: Mapped[datetime.date | None] = mapped_column(Date)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------
class Property(Base):
    """Mirrors ``public.properties``."""

    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    address_line1: Mapped[str] = mapped_column(Text, nullable=False)
    address_line2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    postcode: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'GB'"))
    mortgage_type: Mapped[str] = mapped_column(
        mortgage_type_enum, nullable=False, server_default="none"
    )
    finance_cost_classification: Mapped[str] = mapped_column(
        finance_cost_classification_enum, nullable=False
    )
    epc_rating: Mapped[str | None] = mapped_column(Text)
    epc_expiry: Mapped[datetime.date | None] = mapped_column(Date)
    bedroom_count: Mapped[int | None] = mapped_column(Integer)
    licensing_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# property_ownership -- junction: who owns what share of which property.
# ---------------------------------------------------------------------------
class PropertyOwnership(Base):
    """Mirrors ``public.property_ownership``."""

    __tablename__ = "property_ownership"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    ownership_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# tenancies -- schema-only in MVP.
# ---------------------------------------------------------------------------
class Tenancy(Base):
    """Mirrors ``public.tenancies``."""

    __tablename__ = "tenancies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )
    rent_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    rent_frequency: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[datetime.date | None] = mapped_column(Date)
    is_periodic: Mapped[bool | None] = mapped_column(Boolean)
    last_rent_increase_date: Mapped[datetime.date | None] = mapped_column(Date)
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    deposit_scheme: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# imports -- uploaded bank-statement files.
# ---------------------------------------------------------------------------
class Import(Base):
    """Mirrors ``public.imports``."""

    __tablename__ = "imports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("entities.id"))
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_bank: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime.date | None] = mapped_column(Date)
    period_end: Mapped[datetime.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        import_status_enum, nullable=False, server_default=text("'pending'::import_status")
    )
    error_detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# transactions -- the ledger.
# ---------------------------------------------------------------------------
class Transaction(Base):
    """Mirrors ``public.transactions``.

    ``date`` is annotated ``Mapped[datetime.date]`` (via the ``datetime``
    module, not a bare ``date`` import) so the column name and the stdlib
    type name never collide in this class body.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("imports.id", ondelete="CASCADE")
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("properties.id"))
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: Portion of ``amount`` that is allowable, or ``None`` for all of it. A
    #: magnitude like ``amount``; the category-aware sign rule and the
    #: ownership split are both applied to it downstream.
    allowable_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    direction: Mapped[str] = mapped_column(transaction_direction_enum, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hmrc_category: Mapped[HmrcCategory | None] = mapped_column(hmrc_category_enum)
    status: Mapped[str] = mapped_column(
        transaction_status_enum,
        nullable=False,
        server_default=text("'unclassified'::transaction_status"),
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    proposed_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# documents -- Supabase Storage refs.
# ---------------------------------------------------------------------------
class Document(Base):
    """Mirrors ``public.documents``.

    ``metadata`` is DB-side but ``Base.metadata`` (the ``MetaData``
    instance) owns that Python attribute name on every declarative model --
    mapped here as ``doc_metadata`` with an explicit DB column name instead.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# compliance_certificates
# ---------------------------------------------------------------------------
class ComplianceCertificate(Base):
    """Mirrors ``public.compliance_certificates``.

    ``type`` is mapped here as ``certificate_type`` (explicit DB column name
    ``"type"``) to avoid shadowing the ``type`` builtin as a class
    attribute.
    """

    __tablename__ = "compliance_certificates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )
    certificate_type: Mapped[str] = mapped_column("type", certificate_type_enum, nullable=False)
    issue_date: Mapped[datetime.date | None] = mapped_column(Date)
    expiry_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    certificate_ref: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("documents.id"))
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# mtd_quarters -- cumulative year-to-date totals per entity/tax-year/quarter.
# ---------------------------------------------------------------------------
class MtdQuarter(Base):
    """Mirrors ``public.mtd_quarters``.

    One ``numeric(12,2)`` column per HMRC category that can appear on an MTD
    ITSA quarterly update -- ``personal_non_business`` is deliberately
    absent (see 0001_core.sql: never reported to HMRC).
    """

    __tablename__ = "mtd_quarters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    tax_year: Mapped[str] = mapped_column(Text, nullable=False)
    quarter: Mapped[str] = mapped_column(mtd_quarter_number_enum, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    rent_income_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    other_property_income_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    rent_paid_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    rates_insurance_ground_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    repairs_maintenance_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    finance_costs_residential_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    finance_costs_nonresidential_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    legal_professional_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    service_costs_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    travel_vehicle_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    other_allowable_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    replacement_domestic_items_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    use_of_home_allowance_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    capital_expense_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    export_status: Mapped[str | None] = mapped_column(Text)
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id")
    )
    generated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# job_queue -- background worker jobs.
# ---------------------------------------------------------------------------
class JobQueue(Base):
    """Mirrors ``public.job_queue``.

    ``type`` is mapped here as ``job_type`` (explicit DB column name
    ``"type"``) for the same builtin-shadowing reason as
    ``ComplianceCertificate.certificate_type``.
    """

    __tablename__ = "job_queue"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    job_type: Mapped[str] = mapped_column("type", Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(
        job_status_enum, nullable=False, server_default=text("'queued'::job_status")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()


# ---------------------------------------------------------------------------
# audit_log -- append-only record of every state change to money or
# compliance data. No updated_at (see 0001_core.sql): audit rows are
# append-only, so there is nothing to keep current.
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """Mirrors ``public.audit_log``."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_id_fk()
    actor_type: Mapped[str] = mapped_column(actor_type_enum, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = _created_at()
