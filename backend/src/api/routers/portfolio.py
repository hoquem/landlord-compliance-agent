"""Portfolio setup endpoints: entities, properties, and property ownership.

The foundational reference data everything else in the product hangs off --
an org's ownership entities, its properties, and the split that says which
entity owns what share of which property.

**Every query in this module filters ``org_id`` explicitly, and that is the
only tenant isolation there is here.** ``src/db/session.py`` connects as the
``postgres`` superuser, so the row-level-security policies in
``supabase/migrations/0002_rls.sql`` -- which protect the PostgREST path the
Flutter app uses directly -- are inert on everything below. There is no
backstop underneath a forgotten filter, only a silent cross-tenant leak, so:
reads filter ``org_id`` (a row in another org is a **404**, never a 403,
which would confirm it exists), writes filter ``org_id`` in the statement
that finds the row *before* anything is mutated, and an ``entity_id`` handed
in by a client is checked against the caller's org before it can be
referenced. ``backend/tests/api/test_portfolio.py``'s tenant-isolation
section is what holds this up.

Two things are load-bearing beyond ordinary CRUD:

* **Ownership percentages are money.** ``src/core/splits.py`` apportions
  every penny of income and expense by them (HMRC PIM1035), so they are
  parsed as :class:`decimal.Decimal` and must sum to *exactly* 100 -- never
  ``float``, where ``33.33 + 33.33 + 33.34`` is 99.99999999999999.
* **Replacing an ownership set is atomic.** ``PUT`` takes the whole set and
  validates all of it before deleting or inserting anything, inside one
  transaction. A partially applied replacement would silently corrupt the
  split that later exports are computed from.

Entity and ownership mutations write ``audit_log`` rows (the spec: audit
every state change to money data). Property mutations do not, which is a
line worth revisiting -- ``properties.finance_cost_classification`` drives
the Section 24 finance-cost restriction, so it is arguably money data too --
but the task specifies entities and ownership, so widening it is a decision
for whoever owns the audit scope, not a side effect of this router.

:seealso: backend/src/api/auth.py (the ``org_id`` this module filters on);
    backend/src/core/splits.py; supabase/migrations/0001_core.sql.
"""

import datetime
import uuid
from collections import Counter
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, null, select

from src.api.auth import CurrentAuth
from src.db.models import AuditLog, Entity, Property, PropertyOwnership
from src.db.session import async_session_factory

router = APIRouter(tags=["portfolio"])

#: The enum labels ``0001_core.sql`` defines for these columns. Spelled as
#: ``Literal`` rather than Python enums to match ``src/db/models.py``, which
#: maps all three columns as plain ``str``; FastAPI turns an unknown label
#: into a 422 instead of letting it reach Postgres as a 500.
TaxRegime = Literal["mtd_itsa", "corporation_tax"]
QuarterBasis = Literal["tax_year", "calendar_election"]
FinanceCostClassification = Literal["residential", "non_residential"]

#: What an ownership set has to add up to, exactly.
_WHOLE = Decimal(100)

#: The precision of ``property_ownership.ownership_percentage numeric(5,2)``.
_TWO_PLACES = Decimal("0.01")


# ---------------------------------------------------------------------------
# Request/response schemas.
# ---------------------------------------------------------------------------
class _StrictBody(BaseModel):
    """Base for request bodies: unknown fields are refused, not ignored.

    A misspelled field name is a client bug, and silently dropping it would
    let a caller believe it had set something it hadn't.
    """

    model_config = ConfigDict(extra="forbid")


class _PatchBody(_StrictBody):
    """Base for PATCH bodies: partial, but neither empty nor nulling.

    Every field is optional so that an absent one means "leave it alone".
    That makes two payloads ambiguous unless they are refused outright:

    * ``{}`` -- nothing to do. Returning 200 would tell the caller its
      (mistaken) update had been applied.
    * ``{"field": null}`` -- for the NOT NULL columns this would reach
      Postgres as an IntegrityError (a 500), and for the nullable ones it
      would mean "clear this field", an operation the MVP doesn't offer. A
      422 saying so beats guessing either way.
    """

    @model_validator(mode="after")
    def _reject_empty_and_null_updates(self) -> "_PatchBody":
        """Refuse an empty patch, or one asking to null a field.

        :raises ValueError: (a 422 through FastAPI) if no field was
            supplied, or if any supplied field was explicitly ``null``.
        :returns: the validated model.
        """
        if not self.model_fields_set:
            raise ValueError("Name at least one field to update.")
        nulled = sorted(field for field in self.model_fields_set if getattr(self, field) is None)
        if nulled:
            raise ValueError(
                f"Fields cannot be set to null: {', '.join(nulled)}. "
                "Omit a field to leave it unchanged."
            )
        return self


class EntityCreate(_StrictBody):
    """Body of ``POST /entities``."""

    name: str = Field(min_length=1)
    tax_regime: TaxRegime
    #: Mirrors the column's own ``default 'tax_year'`` so that the value
    #: written is explicit here rather than dependent on where the row came
    #: from -- the schema default stays for direct PostgREST writes.
    quarter_basis: QuarterBasis = "tax_year"
    prs_registration_number: str | None = None
    prs_registered_at: datetime.date | None = None


class EntityUpdate(_PatchBody):
    """Body of ``PATCH /entities/{entity_id}``."""

    name: str | None = Field(default=None, min_length=1)
    tax_regime: TaxRegime | None = None
    quarter_basis: QuarterBasis | None = None
    prs_registration_number: str | None = None
    prs_registered_at: datetime.date | None = None


class EntityRead(BaseModel):
    """An entity as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    tax_regime: TaxRegime
    quarter_basis: QuarterBasis
    prs_registration_number: str | None
    prs_registered_at: datetime.date | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PropertyCreate(_StrictBody):
    """Body of ``POST /properties``."""

    address_line1: str = Field(min_length=1)
    address_line2: str | None = None
    city: str = Field(min_length=1)
    postcode: str = Field(min_length=1)
    #: Mirrors the column's own ``default 'GB'`` -- see ``quarter_basis``.
    country: str = Field(default="GB", min_length=1)
    finance_cost_classification: FinanceCostClassification
    epc_rating: str | None = None
    epc_expiry: datetime.date | None = None
    bedroom_count: int | None = Field(default=None, ge=0)
    #: Mirrors the column's own ``default false`` -- see ``quarter_basis``.
    licensing_flag: bool = False


class PropertyUpdate(_PatchBody):
    """Body of ``PATCH /properties/{property_id}``."""

    address_line1: str | None = Field(default=None, min_length=1)
    address_line2: str | None = None
    city: str | None = Field(default=None, min_length=1)
    postcode: str | None = Field(default=None, min_length=1)
    country: str | None = Field(default=None, min_length=1)
    finance_cost_classification: FinanceCostClassification | None = None
    epc_rating: str | None = None
    epc_expiry: datetime.date | None = None
    bedroom_count: int | None = Field(default=None, ge=0)
    licensing_flag: bool | None = None


class PropertyRead(BaseModel):
    """A property as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    address_line1: str
    address_line2: str | None
    city: str
    postcode: str
    country: str
    finance_cost_classification: FinanceCostClassification
    epc_rating: str | None
    epc_expiry: datetime.date | None
    bedroom_count: int | None
    licensing_flag: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class OwnershipShare(_StrictBody):
    """One ``{entity_id, percentage}`` element of an ownership set.

    ``percentage`` is a :class:`~decimal.Decimal` bounded exactly like
    ``property_ownership.ownership_percentage numeric(5,2) check
    (ownership_percentage > 0 and ownership_percentage <= 100)``, so that a
    0%, negative, or over-100 share is a 422 here rather than a CHECK
    violation surfacing as a 500. ``decimal_places=2`` refuses a third
    decimal place too: ``numeric(5,2)`` would round it into a *different*
    number, and rounding money silently is exactly what this project must
    not do.
    """

    entity_id: uuid.UUID
    percentage: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)

    @field_validator("percentage")
    @classmethod
    def _at_column_precision(cls, value: Decimal) -> Decimal:
        """Normalise a validated percentage to the column's two decimal places.

        ``100``, ``100.0`` and ``100.00`` are one share, but
        :class:`~decimal.Decimal` remembers which was typed -- so without
        this the API would echo back ``"100"`` next to a sibling's
        ``"40.00"``, and an audit payload built pre-flush would read as a
        change against a ``before`` that came from the database at 2dp. Runs
        after the field constraints above, so a third decimal place is
        already a 422 and nothing is ever rounded here.

        :param value: the validated percentage.
        :returns: the same number at 2dp.
        """
        return value.quantize(_TWO_PLACES)


class OwnershipRead(BaseModel):
    """One stored ownership row as the API returns it.

    No timestamps: an ownership set is returned as a set, and its history
    lives in ``audit_log`` rather than in per-row ``created_at`` values.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    property_id: uuid.UUID
    entity_id: uuid.UUID
    percentage: Decimal


# ---------------------------------------------------------------------------
# Small shared helpers.
# ---------------------------------------------------------------------------
def _not_found(what: str, resource_id: uuid.UUID) -> HTTPException:
    """Build the 404 used for "no such row *in your org*".

    Deliberately the same response whether the row doesn't exist or belongs
    to another org: a 403 for the second case would confirm to the caller
    that the id is real.

    :param what: the resource kind, for the message.
    :param resource_id: the id that couldn't be used.
    :returns: a 404 :class:`~fastapi.HTTPException`.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No {what} with id {resource_id} in this org.",
    )


def _unprocessable(detail: str) -> HTTPException:
    """Build a 422 for a payload that parsed but can't be acted on.

    :param detail: what was wrong, naming the offending values -- these
        messages are the whole point of validating in the API rather than
        letting a constraint fire.
    :returns: a 422 :class:`~fastapi.HTTPException`.
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _audit(
    auth: CurrentAuth, action: str, *, before: object | None, after: object | None
) -> AuditLog:
    """Build the ``audit_log`` row for one state change by the caller.

    :param auth: the caller, recorded as ``actor_type='user'`` plus their
        ``actor_id``.
    :param action: the action name, e.g. ``entity.updated``.
    :param before: JSON-safe prior state, or ``None`` for a creation.
    :param after: JSON-safe new state.
    :returns: an unpersisted :class:`~src.db.models.AuditLog`, to be added
        to the same transaction as the change it describes.
    """
    return AuditLog(
        org_id=auth.org_id,
        actor_type="user",
        actor_id=auth.user_id,
        action=action,
        # `null()` rather than `None`: SQLAlchemy's JSONB type maps a Python
        # `None` to the JSON value `null`, which is a *present* value in a
        # jsonb column. "There was no prior state" is a SQL NULL -- the
        # difference is visible to anything querying `before is null`.
        before=null() if before is None else before,
        after=null() if after is None else after,
    )


def _share_payloads(rows: list[PropertyOwnership]) -> list[dict[str, str]]:
    """Render ownership rows as the JSON-safe shape the audit trail stores.

    :param rows: the ownership rows to render.
    :returns: ``{"entity_id", "percentage"}`` dicts ordered by entity id, so
        that two audit payloads for the same set compare equal.
    """
    return [
        {"entity_id": str(row.entity_id), "percentage": str(row.ownership_percentage)}
        for row in sorted(rows, key=lambda row: row.entity_id)
    ]


# ---------------------------------------------------------------------------
# Entities.
# ---------------------------------------------------------------------------
@router.post("/entities", status_code=status.HTTP_201_CREATED)
async def create_entity(payload: EntityCreate, auth: CurrentAuth) -> EntityRead:
    """Create an ownership entity in the caller's org.

    :param payload: the entity to create.
    :param auth: the authenticated caller; the entity is created in their
        org and nowhere else.
    :returns: the created entity.
    """
    async with async_session_factory() as session:
        entity = Entity(org_id=auth.org_id, **payload.model_dump())
        session.add(entity)
        # Flush (not commit) so the row -- and the server-generated id and
        # timestamps the response and audit payload both need -- exists
        # inside this transaction, alongside the audit row.
        await session.flush()
        await session.refresh(entity)
        created = EntityRead.model_validate(entity)
        session.add(_audit(auth, "entity.created", before=None, after=created.model_dump(mode="json")))
        await session.commit()
    return created


@router.get("/entities")
async def list_entities(auth: CurrentAuth) -> list[EntityRead]:
    """List the caller's org's entities, oldest first.

    :param auth: the authenticated caller.
    :returns: every entity in their org, and only those.
    """
    async with async_session_factory() as session:
        entities = await session.scalars(
            select(Entity).where(Entity.org_id == auth.org_id).order_by(Entity.created_at, Entity.id)
        )
        return [EntityRead.model_validate(entity) for entity in entities]


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: uuid.UUID, auth: CurrentAuth) -> EntityRead:
    """Read one entity from the caller's org.

    :param entity_id: the entity to read.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if no such entity exists *in their org*.
    :returns: the entity.
    """
    async with async_session_factory() as session:
        entity = await session.scalar(
            select(Entity).where(Entity.id == entity_id, Entity.org_id == auth.org_id)
        )
        if entity is None:
            raise _not_found("entity", entity_id)
        return EntityRead.model_validate(entity)


@router.patch("/entities/{entity_id}")
async def update_entity(entity_id: uuid.UUID, payload: EntityUpdate, auth: CurrentAuth) -> EntityRead:
    """Update the named fields of one entity in the caller's org.

    The org filter is on the statement that *finds* the row, so a
    cross-org id fails with a 404 before anything is written -- never a
    silent no-op 200.

    :param entity_id: the entity to update.
    :param payload: the fields to change.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if no such entity exists in their org.
    :returns: the updated entity.
    """
    async with async_session_factory() as session:
        entity = await session.scalar(
            select(Entity).where(Entity.id == entity_id, Entity.org_id == auth.org_id)
        )
        if entity is None:
            raise _not_found("entity", entity_id)

        before = EntityRead.model_validate(entity).model_dump(mode="json")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(entity, field, value)
        await session.flush()
        # `updated_at` is maintained by the trg_entities_set_updated_at
        # trigger, so the new value has to be read back, not assumed.
        await session.refresh(entity)

        updated = EntityRead.model_validate(entity)
        session.add(
            _audit(auth, "entity.updated", before=before, after=updated.model_dump(mode="json"))
        )
        await session.commit()
    return updated


# ---------------------------------------------------------------------------
# Properties.
# ---------------------------------------------------------------------------
@router.post("/properties", status_code=status.HTTP_201_CREATED)
async def create_property(payload: PropertyCreate, auth: CurrentAuth) -> PropertyRead:
    """Create a property in the caller's org.

    :param payload: the property to create.
    :param auth: the authenticated caller; the property is created in their
        org and nowhere else.
    :returns: the created property.
    """
    async with async_session_factory() as session:
        prop = Property(org_id=auth.org_id, **payload.model_dump())
        session.add(prop)
        await session.flush()
        await session.refresh(prop)
        await session.commit()
    return PropertyRead.model_validate(prop)


@router.get("/properties")
async def list_properties(auth: CurrentAuth) -> list[PropertyRead]:
    """List the caller's org's properties, oldest first.

    :param auth: the authenticated caller.
    :returns: every property in their org, and only those.
    """
    async with async_session_factory() as session:
        properties = await session.scalars(
            select(Property)
            .where(Property.org_id == auth.org_id)
            .order_by(Property.created_at, Property.id)
        )
        return [PropertyRead.model_validate(prop) for prop in properties]


@router.get("/properties/{property_id}")
async def get_property(property_id: uuid.UUID, auth: CurrentAuth) -> PropertyRead:
    """Read one property from the caller's org.

    :param property_id: the property to read.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if no such property exists *in their org*.
    :returns: the property.
    """
    async with async_session_factory() as session:
        prop = await session.scalar(
            select(Property).where(Property.id == property_id, Property.org_id == auth.org_id)
        )
        if prop is None:
            raise _not_found("property", property_id)
        return PropertyRead.model_validate(prop)


@router.patch("/properties/{property_id}")
async def update_property(
    property_id: uuid.UUID, payload: PropertyUpdate, auth: CurrentAuth
) -> PropertyRead:
    """Update the named fields of one property in the caller's org.

    :param property_id: the property to update.
    :param payload: the fields to change.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if no such property exists in their org --
        raised by the org-filtered lookup, before any write.
    :returns: the updated property.
    """
    async with async_session_factory() as session:
        prop = await session.scalar(
            select(Property).where(Property.id == property_id, Property.org_id == auth.org_id)
        )
        if prop is None:
            raise _not_found("property", property_id)

        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(prop, field, value)
        await session.flush()
        await session.refresh(prop)
        await session.commit()
    return PropertyRead.model_validate(prop)


# ---------------------------------------------------------------------------
# Ownership -- the money-critical endpoint.
# ---------------------------------------------------------------------------
@router.put("/properties/{property_id}/ownership")
async def replace_property_ownership(
    property_id: uuid.UUID,
    shares: Annotated[
        list[OwnershipShare],
        Body(min_length=1, description="The complete ownership set, summing to exactly 100%."),
    ],
    auth: CurrentAuth,
) -> list[OwnershipRead]:
    """Replace a property's entire ownership set, atomically.

    The body is the whole set, not a delta: what is stored afterwards is
    exactly what was sent. Everything is validated before anything is
    written, and the delete and the inserts share one transaction, so a
    rejected payload leaves the previous set byte-for-byte intact -- a
    half-applied change would corrupt the split ``src/core/splits.py``
    apportions money by.

    :param property_id: the property whose ownership is being set.
    :param shares: the complete ownership set. Must name each entity at
        most once, and the percentages must sum to exactly 100.
    :param auth: the authenticated caller. The property and every entity
        named must belong to their org.
    :raises HTTPException: 404 if the property isn't in the caller's org;
        422 if the set is empty, repeats an entity, doesn't sum to exactly
        100, or names an entity that isn't in the caller's org.
    :returns: the ownership set as stored.
    """
    async with async_session_factory() as session:
        property_exists = await session.scalar(
            select(Property.id).where(Property.id == property_id, Property.org_id == auth.org_id)
        )
        if property_exists is None:
            raise _not_found("property", property_id)

        # Duplicates first: they would also skew the total, and
        # "you named this entity twice" is the more useful of the two
        # messages. (uq_property_ownership_property_entity would refuse the
        # second row at the database -- as a 500.)
        repeated = sorted(
            str(entity_id)
            for entity_id, count in Counter(share.entity_id for share in shares).items()
            if count > 1
        )
        if repeated:
            raise _unprocessable(
                "Each entity may appear at most once in an ownership set; "
                f"repeated: {', '.join(repeated)}."
            )

        total = sum((share.percentage for share in shares), Decimal(0))
        if total != _WHOLE:
            raise _unprocessable(
                f"Ownership percentages must sum to exactly 100, got {total}."
            )

        # Which of the entities named are really the caller's. An entity in
        # another org is reported exactly like one that doesn't exist:
        # anything else would tell the caller the id is real. (The composite
        # FK fk_property_ownership_entity_org would refuse the write anyway
        # -- again, as a 500.)
        owned = set(
            await session.scalars(
                select(Entity.id).where(
                    Entity.org_id == auth.org_id,
                    Entity.id.in_([share.entity_id for share in shares]),
                )
            )
        )
        unusable = sorted(str(share.entity_id) for share in shares if share.entity_id not in owned)
        if unusable:
            raise _unprocessable(f"No entity with id in this org: {', '.join(unusable)}.")

        # Validation is done; from here on the transaction either applies
        # in full or (on any error) rolls back untouched.
        existing = list(
            await session.scalars(
                select(PropertyOwnership).where(
                    PropertyOwnership.property_id == property_id,
                    PropertyOwnership.org_id == auth.org_id,
                )
            )
        )
        before = _share_payloads(existing)

        await session.execute(
            delete(PropertyOwnership).where(
                PropertyOwnership.property_id == property_id,
                PropertyOwnership.org_id == auth.org_id,
            )
        )
        rows = [
            PropertyOwnership(
                org_id=auth.org_id,
                property_id=property_id,
                entity_id=share.entity_id,
                ownership_percentage=share.percentage,
            )
            for share in shares
        ]
        session.add_all(rows)
        await session.flush()

        session.add(
            _audit(auth, "ownership.replaced", before=before, after=_share_payloads(rows))
        )
        replaced = [
            OwnershipRead(
                id=row.id,
                org_id=row.org_id,
                property_id=row.property_id,
                entity_id=row.entity_id,
                percentage=row.ownership_percentage,
            )
            for row in rows
        ]
        await session.commit()
    return replaced
