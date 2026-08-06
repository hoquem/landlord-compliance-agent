"""Compliance certificates: gas safety, EICR, EPC and the two licences.

**Every query still filters ``org_id``**, and since 2026-08-06 the database
enforces it too: the API connects as ``app_api``, a role with row-level
security applied, so an unfiltered query returns *this org's* rows rather than
everyone's. The filter is now the first of two defences instead of the only
one -- keep writing it (it is what makes the intent readable, and RLS is a
backstop, not a design), but a slip is no longer a leak. Proved by
``tests/db/test_rls_enforced.py``, which queries with no ``where`` at all.
:func:`~src.api.scoping.get_owned_or_404`. ``DATABASE_URL`` is the
``postgres`` superuser, so ``0002_rls.sql``'s policies are inert here.

Unlike the money routers, though, this one is **not** the only thing
standing between orgs. ``0002_rls.sql`` added composite foreign keys --
``(property_id, org_id)`` and ``(document_id, org_id)`` -- so a certificate
referencing another org's property or document cannot be written at all;
the database refuses it. The org-scoped lookups below therefore exist to
make that a **404 instead of a 500 carrying an IntegrityError**. Read them
as error handling, not as the tenant boundary, and do not let that
distinction rot: if those FKs were ever dropped, these lookups would
quietly become load-bearing.

Three things beyond ordinary CRUD:

* **Property is the aggregate root** (``docs/domain/compliance.md``), and
  its invariant is "a certificate belongs to exactly one property, in one
  org". Routes are flat -- one canonical URL per certificate, rather than
  two ways to name the same row -- and the invariant is upheld by the
  org-scoped property lookup on create and on any PATCH that moves one.
* **Status is derived on every read and never stored.** A cached
  "compliant" flag goes stale silently, and it goes stale exactly when it
  matters: the day a certificate lapses, nothing has changed to invalidate
  it. See :func:`~src.core.certificates.certificate_status`.
* **The date guard checks the resulting row, not the request.** Patching
  only ``issue_date`` has to be validated against the *stored*
  ``expiry_date``; a validator looking solely at the fields present would
  wave a transposed pair through, and a transposition makes a valid
  certificate read as expired.

**The wire name is ``certificate_type``, not ``type``.** The model maps the
column ``type`` to the attribute ``certificate_type`` (``type`` shadows the
builtin as a class attribute). A body field named ``type`` would make the
PATCH ``setattr`` loop write a plain Python attribute that never reaches
the database -- silently, with a 200 response. The awkward name is the
price of that loop staying honest.

**What this does not do: upload files.** ``document_id`` is settable and
validated, but nothing in this system creates a ``documents`` row for a
certificate -- the only writer is ``exports.py``, and the only buckets are
``statements`` and ``exports``. Certificate upload needs a bucket and an
endpoint, and is not part of Task 17.

:seealso: backend/src/core/certificates.py (the type enum and the status
    rule); docs/domain/compliance.md; supabase/migrations/0002_rls.sql.
"""

import datetime
import uuid
from typing import ClassVar

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select

from src.api.audit import audit
from src.api.auth import CurrentAuth
from src.api.scoping import get_owned_or_404
from src.core.certificates import (
    CertificateStatus,
    CertificateType,
    certificate_status,
    uk_today,
)
from src.db.models import ComplianceCertificate, Document, Property
from src.db.session import org_session

router = APIRouter(tags=["certificates"])


class _StrictBody(BaseModel):
    """Base for request bodies: unknown fields are refused, not ignored."""

    model_config = ConfigDict(extra="forbid")


class CertificateCreate(_StrictBody):
    """Body of ``POST /certificates``.

    :ivar property_id: the property this certificate belongs to; checked
        against the caller's org before use.
    :ivar certificate_type: one of the five in
        :class:`~src.core.certificates.CertificateType`.
    :ivar expiry_date: required -- a certificate without one cannot express
        the only question worth asking of it.
    :ivar issue_date: optional; often unknown for inherited paperwork.
    :ivar certificate_ref: the issuer's reference, if there is one.
    :ivar document_id: a stored document, in the caller's org.
    """

    property_id: uuid.UUID
    certificate_type: CertificateType
    expiry_date: datetime.date
    issue_date: datetime.date | None = None
    certificate_ref: str | None = None
    document_id: uuid.UUID | None = None


class CertificateUpdate(_StrictBody):
    """Body of ``PATCH /certificates/{id}``, JSON Merge Patch (RFC 7386).

    Three states per field, all three distinguishable: key absent leaves
    the stored value alone, key present and ``null`` clears it, key present
    with a value sets it. ``{}`` is refused rather than treated as a no-op,
    because a 200 would tell a caller its mistaken update had been applied.

    Nullability is written out rather than inferred: every field is
    uniformly ``X | None`` -- that is how "absent" is expressed -- so
    ``expiry_date`` and ``issue_date`` look identical to pydantic while one
    column is NOT NULL and the other is not.

    :cvar _NOT_NULLABLE: field names whose column is NOT NULL. Checked
        against the mapped columns by
        ``tests/api/conftest.py::assert_not_nullable_matches_schema``, so a
        name that is missing, superfluous or misspelled fails there rather
        than becoming a live 500.
    """

    property_id: uuid.UUID | None = None
    certificate_type: CertificateType | None = None
    expiry_date: datetime.date | None = None
    issue_date: datetime.date | None = None
    certificate_ref: str | None = None
    document_id: uuid.UUID | None = None

    _NOT_NULLABLE: ClassVar[frozenset[str]] = frozenset(
        {"property_id", "certificate_type", "expiry_date"}
    )

    @model_validator(mode="after")
    def _reject_empty_patch_and_illegal_nulls(self) -> "CertificateUpdate":
        """Refuse an empty patch, or a null aimed at a NOT NULL column.

        :raises ValueError: (a 422 through FastAPI) if no field was
            supplied, or a supplied field was explicitly ``null`` while its
            column is NOT NULL.
        :returns: the validated model.
        """
        if not self.model_fields_set:
            raise ValueError("Name at least one field to update.")
        nulled = sorted(
            field
            for field in self.model_fields_set
            if getattr(self, field) is None and field in self._NOT_NULLABLE
        )
        if nulled:
            raise ValueError(
                f"Fields cannot be set to null: {', '.join(nulled)}. "
                "Omit a field to leave it unchanged."
            )
        return self


class CertificateRead(BaseModel):
    """A certificate as the API returns it, including its derived status.

    :ivar status: ``expired``, ``expiring`` or ``valid``. Computed at read
        time from ``expiry_date``; no column holds it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    property_id: uuid.UUID
    certificate_type: CertificateType
    issue_date: datetime.date | None
    expiry_date: datetime.date
    certificate_ref: str | None
    document_id: uuid.UUID | None
    status: CertificateStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PropertyCertificates(BaseModel):
    """One property's certificates, for the grouped list.

    :ivar property_id: the property.
    :ivar certificates: its certificates, soonest expiry first.
    """

    property_id: uuid.UUID
    certificates: list[CertificateRead]


def _unprocessable(detail: str) -> HTTPException:
    """Build the 422 used when a request is well-formed but cannot be applied."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _read(certificate: ComplianceCertificate) -> CertificateRead:
    """Build the response for one certificate, deriving its status.

    The single place status is attached, so no endpoint can return a
    certificate without one or with a stale one.

    :param certificate: the mapped row.
    :returns: the response model.
    """
    return CertificateRead(
        id=certificate.id,
        org_id=certificate.org_id,
        property_id=certificate.property_id,
        certificate_type=certificate.certificate_type,
        issue_date=certificate.issue_date,
        expiry_date=certificate.expiry_date,
        certificate_ref=certificate.certificate_ref,
        document_id=certificate.document_id,
        status=certificate_status(certificate.expiry_date, today=uk_today()),
        created_at=certificate.created_at,
        updated_at=certificate.updated_at,
    )


def _assert_dates_are_in_order(
    issue_date: datetime.date | None, expiry_date: datetime.date
) -> None:
    """Refuse a certificate issued after it expires.

    The most plausible data-entry error on this form, and the only one that
    silently inverts the answer the page exists to give: a transposed pair
    makes a valid certificate read as expired. Equal dates are allowed --
    degenerate, but refusing them would be a guess.

    :param issue_date: the issue date, or ``None``.
    :param expiry_date: the expiry date.
    :raises HTTPException: 422 if the issue date is after the expiry date.
    """
    if issue_date is not None and issue_date > expiry_date:
        raise _unprocessable(
            f"issue_date {issue_date} is after expiry_date {expiry_date}. "
            "Check the two dates have not been swapped."
        )


async def _assert_references_are_the_callers(
    session,
    auth: CurrentAuth,
    *,
    property_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
) -> None:
    """Check the property and document belong to the caller's org.

    ``None`` for either means "not being set", so it is not checked.

    These lookups are **not** the tenant boundary -- ``0002_rls.sql``'s
    composite foreign keys make a cross-org reference unwritable. They are
    what turns the database's refusal into a 404 rather than a 500 carrying
    an IntegrityError.

    :param session: the session to query in.
    :param auth: the authenticated caller.
    :param property_id: the property being referenced, if any.
    :param document_id: the document being referenced, if any.
    :raises HTTPException: 404 if either is not the caller's.
    """
    if property_id is not None:
        await get_owned_or_404(session, Property, property_id, auth, what="property")
    if document_id is not None:
        await get_owned_or_404(session, Document, document_id, auth, what="document")


@router.post("/certificates", status_code=status.HTTP_201_CREATED)
async def create_certificate(
    payload: CertificateCreate, auth: CurrentAuth
) -> CertificateRead:
    """Record one compliance certificate against a property.

    :param payload: the certificate to create.
    :param auth: the authenticated caller; the row is created in their org
        and nowhere else.
    :raises HTTPException: 404 if the property or document is not theirs;
        422 if the issue date is after the expiry date.
    :returns: the created certificate, with its derived status.
    """
    _assert_dates_are_in_order(payload.issue_date, payload.expiry_date)

    async with org_session(auth.user_id) as session:
        await _assert_references_are_the_callers(
            session,
            auth,
            property_id=payload.property_id,
            document_id=payload.document_id,
        )

        certificate = ComplianceCertificate(org_id=auth.org_id, **payload.model_dump())
        session.add(certificate)
        # Flush rather than commit, so the server-generated id and
        # timestamps exist inside this transaction alongside the audit row.
        await session.flush()
        await session.refresh(certificate)

        created = _read(certificate)
        session.add(
            audit(
                auth,
                "certificate.created",
                before=None,
                after=created.model_dump(mode="json"),
            )
        )
        await session.commit()
    return created


@router.get("/certificates")
async def list_certificates(auth: CurrentAuth) -> list[PropertyCertificates]:
    """List the caller's certificates, grouped by property.

    Only properties that **have** certificates appear. The endpoint's
    subject is certificates, and "which property is missing a gas safety
    certificate?" cannot be answered here at all, because nothing records
    which types a property requires -- that is a dashboard question, and it
    joins this with ``GET /properties``.

    Groups are in property creation order, matching ``GET /properties``;
    certificates within a group are soonest expiry first, which is the
    order a compliance screen reads in.

    :param auth: the authenticated caller.
    :returns: one group per property with certificates, in their org only.
    """
    async with org_session(auth.user_id) as session:
        rows = await session.scalars(
            select(ComplianceCertificate)
            .join(Property, Property.id == ComplianceCertificate.property_id)
            .where(ComplianceCertificate.org_id == auth.org_id)
            .order_by(
                Property.created_at,
                Property.id,
                ComplianceCertificate.expiry_date,
                ComplianceCertificate.id,
            )
        )
        grouped: dict[uuid.UUID, list[CertificateRead]] = {}
        for row in rows:
            # `dict` preserves insertion order, and the query is already
            # ordered by property then expiry -- so grouping needs no sort
            # of its own, and cannot disagree with the ORDER BY.
            grouped.setdefault(row.property_id, []).append(_read(row))
        return [
            PropertyCertificates(property_id=property_id, certificates=certificates)
            for property_id, certificates in grouped.items()
        ]


@router.get("/certificates/{certificate_id}")
async def get_certificate(
    certificate_id: uuid.UUID, auth: CurrentAuth
) -> CertificateRead:
    """Read one certificate from the caller's org.

    Status is recomputed here, not read back from the create response: a
    status computed once at creation would be right for a day and wrong
    every day after.

    :param certificate_id: the certificate to read.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if no such certificate exists in their org.
    :returns: the certificate, with its derived status.
    """
    async with org_session(auth.user_id) as session:
        certificate = await get_owned_or_404(
            session, ComplianceCertificate, certificate_id, auth, what="certificate"
        )
        return _read(certificate)


@router.patch("/certificates/{certificate_id}")
async def update_certificate(
    certificate_id: uuid.UUID, payload: CertificateUpdate, auth: CurrentAuth
) -> CertificateRead:
    """Update the named fields of one certificate.

    ``property_id`` may be changed: a certificate filed against the wrong
    address is a real correction across a portfolio of similar addresses.
    The org-scoped lookup runs **before** anything is written.

    :param certificate_id: the certificate to update.
    :param payload: the fields to change.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if the certificate, or a property or
        document it is being pointed at, is not theirs; 422 if the
        resulting row would have an issue date after its expiry date.
    :returns: the updated certificate.
    """
    async with org_session(auth.user_id) as session:
        certificate = await get_owned_or_404(
            session, ComplianceCertificate, certificate_id, auth, what="certificate"
        )
        changes = payload.model_dump(exclude_unset=True)

        await _assert_references_are_the_callers(
            session,
            auth,
            property_id=changes.get("property_id"),
            document_id=changes.get("document_id"),
        )
        # Validated against the row as it *will be*, not against the
        # request: patching one date alone still has to agree with the
        # other one already stored.
        _assert_dates_are_in_order(
            changes.get("issue_date", certificate.issue_date),
            changes.get("expiry_date", certificate.expiry_date),
        )

        before = _read(certificate).model_dump(mode="json")
        for field, value in changes.items():
            setattr(certificate, field, value)
        await session.flush()
        # `updated_at` is maintained by a trigger, so it is read back
        # rather than assumed.
        await session.refresh(certificate)

        updated = _read(certificate)
        session.add(
            audit(
                auth,
                "certificate.updated",
                before=before,
                after=updated.model_dump(mode="json"),
            )
        )
        await session.commit()
    return updated


@router.delete("/certificates/{certificate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_certificate(certificate_id: uuid.UUID, auth: CurrentAuth) -> Response:
    """Delete one certificate.

    A hard delete, unlike the org-level policy in ``0001_core.sql``: a
    certificate row is a record of paperwork, and a superseded or
    mis-entered one has no value to keep. What it was is preserved in the
    audit row's ``before``.

    :param certificate_id: the certificate to delete.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if no such certificate exists in their org.
    :returns: an empty 204.
    """
    async with org_session(auth.user_id) as session:
        certificate = await get_owned_or_404(
            session, ComplianceCertificate, certificate_id, auth, what="certificate"
        )
        before = _read(certificate).model_dump(mode="json")
        await session.delete(certificate)
        session.add(audit(auth, "certificate.deleted", before=before, after=None))
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
