"""Review, confirm and exclude parsed transactions.

The endpoints behind the review screen, and the last point at which a human
sees a figure before it can reach an export.

**Every query filters ``org_id``**, in the statement or through
:func:`~src.api.scoping.get_owned_or_404`. ``DATABASE_URL`` is the
``postgres`` superuser, so the RLS policies of ``0002_rls.sql`` are inert on
this path; the filters are the entire tenant boundary. See
``tests/api/test_transactions.py``'s isolation section.

Two things here are not ordinary CRUD:

* **Confirming against a property is a money decision.** Per-entity export
  totals derive *exclusively* from ``property_ownership`` (HMRC PIM1035), and
  ``0001_core.sql:228-234`` deliberately does **not** enforce "shares sum to
  100" in the database -- ownership is edited row by row through transiently
  invalid totals, so a DB sum check would fire mid-edit. A property whose
  shares sum to 50 is therefore perfectly insertable, and confirming against
  one is refused *here* or not at all. The check runs the real apportionment
  (:func:`~src.core.splits.split_amount`) rather than re-deriving the rule,
  so "can this be confirmed?" and "can this be exported?" cannot drift apart.
* **A batch is all-or-nothing.** A partially applied batch is the worst
  outcome available: the user believes they confirmed a screenful and some of
  it silently did not take. Every item is validated and written inside one
  transaction, and any failure abandons all of it.

:seealso: backend/src/core/splits.py (the apportionment this validates
    against); backend/src/api/audit.py; supabase/migrations/0001_core.sql.
"""

import datetime
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.api.audit import audit
from src.api.auth import CurrentAuth
from src.api.scoping import get_owned_or_404
from src.core.categories import HmrcCategory
from src.core.splits import InvalidOwnershipError, split_amount
from src.db.models import Property, PropertyOwnership, Transaction
from src.db.session import async_session_factory

router = APIRouter(tags=["transactions"])

TransactionStatus = Literal["unclassified", "proposed", "confirmed", "excluded"]
Direction = Literal["in", "out"]


class TransactionRead(BaseModel):
    """A transaction as the API returns it, including any agent proposal."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    import_id: uuid.UUID | None
    entity_id: uuid.UUID
    property_id: uuid.UUID | None
    date: datetime.date
    amount: Decimal
    direction: Direction
    description: str
    hmrc_category: HmrcCategory | None
    status: TransactionStatus
    confidence: Decimal | None
    proposed_by: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConfirmBody(BaseModel):
    """Body of ``POST /transactions/{id}/confirm``."""

    model_config = ConfigDict(extra="forbid")

    hmrc_category: HmrcCategory
    #: Optional because not every allowable cost belongs to one property --
    #: ``use_of_home_allowance`` is the standing example.
    property_id: uuid.UUID | None = None


class BatchItem(ConfirmBody):
    """One line of a batch confirm."""

    transaction_id: uuid.UUID


class ConfirmBatchBody(BaseModel):
    """Body of ``POST /transactions/confirm-batch``."""

    model_config = ConfigDict(extra="forbid")

    items: list[BatchItem] = Field(min_length=1)


def _unprocessable(detail: str) -> HTTPException:
    """Build the 422 used when a request is well-formed but cannot be applied."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


async def _assert_property_is_apportionable(
    session, property_id: uuid.UUID, auth: CurrentAuth, amount: Decimal
) -> None:
    """Refuse a property whose ownership cannot apportion the amount.

    Runs the **real** apportionment rather than re-checking the sum by hand,
    so that a rule tightened in :mod:`src.core.splits` cannot silently stop
    being enforced here. The property is looked up org-scoped first, so a
    cross-org id is a 404 before any of this runs.

    :param session: the session to query in.
    :param property_id: the property to attribute the line to.
    :param auth: the authenticated caller.
    :param amount: the transaction's magnitude, used as the real subject of
        the apportionment.
    :raises HTTPException: 404 if the property is not the caller's; 422 if
        its ownership set cannot take the amount, quoting the reason.
    """
    await get_owned_or_404(session, Property, property_id, auth, what="property")

    rows = await session.scalars(
        select(PropertyOwnership).where(
            PropertyOwnership.property_id == property_id,
            PropertyOwnership.org_id == auth.org_id,
        )
    )
    shares = {row.entity_id: row.ownership_percentage for row in rows}
    try:
        split_amount(amount, shares)
    except InvalidOwnershipError as exc:
        raise _unprocessable(
            f"Cannot confirm against this property: {exc}. "
            "Set its ownership so the shares sum to exactly 100 first."
        ) from exc


async def _apply_confirm(
    session, txn: Transaction, body: ConfirmBody, auth: CurrentAuth
) -> TransactionRead:
    """Validate and apply one confirmation inside the caller's transaction.

    Shared by the single and batch endpoints so that a batch cannot enforce
    a weaker rule than a single confirm -- the failure mode being a user who
    confirms one at a time, hits the guard, then confirms the same lines in
    bulk and slips past it.

    :param session: the open session; nothing is committed here.
    :param txn: the transaction row, already proven to be the caller's.
    :param body: the requested category and optional property.
    :param auth: the authenticated caller.
    :returns: the updated transaction, read back after flush.
    """
    before = TransactionRead.model_validate(txn).model_dump(mode="json")
    if body.property_id is not None:
        await _assert_property_is_apportionable(session, body.property_id, auth, txn.amount)

    txn.hmrc_category = body.hmrc_category
    txn.property_id = body.property_id
    txn.status = "confirmed"
    await session.flush()
    await session.refresh(txn)

    updated = TransactionRead.model_validate(txn)
    session.add(
        audit(auth, "transaction.confirmed", before=before, after=updated.model_dump(mode="json"))
    )
    return updated


@router.get("/transactions")
async def list_transactions(
    auth: CurrentAuth,
    import_id: uuid.UUID | None = None,
    # Aliased because `status` shadows the imported `fastapi.status` module
    # inside this function's scope. The wire name is what the review screen
    # sends, so it is the alias that has to be right.
    status_filter: Annotated[TransactionStatus | None, Query(alias="status")] = None,
) -> list[TransactionRead]:
    """List the caller's transactions, oldest first.

    :param auth: the authenticated caller.
    :param import_id: restrict to one import.
    :param status_filter: restrict to one status; sent as ``?status=``.
    :returns: matching transactions in their org, and only those.
    """
    async with async_session_factory() as session:
        stmt = select(Transaction).where(Transaction.org_id == auth.org_id)
        if import_id is not None:
            stmt = stmt.where(Transaction.import_id == import_id)
        if status_filter is not None:
            stmt = stmt.where(Transaction.status == status_filter)
        rows = await session.scalars(stmt.order_by(Transaction.date, Transaction.id))
        return [TransactionRead.model_validate(row) for row in rows]


@router.post("/transactions/confirm-batch")
async def confirm_batch(body: ConfirmBatchBody, auth: CurrentAuth) -> list[TransactionRead]:
    """Confirm many transactions atomically.

    All or nothing: every item is validated and applied inside one database
    transaction, and any failure abandons the whole batch. A partial apply
    would leave the user believing a screenful had been confirmed when some
    of it silently had not.

    Declared **before** ``/transactions/{transaction_id}/...`` so that
    ``confirm-batch`` is matched as a literal path rather than captured as a
    ``transaction_id``.

    :param body: the lines to confirm.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if any transaction or property is not the
        caller's; 422 if any line fails validation.
    :returns: the confirmed transactions, in request order.
    """
    async with async_session_factory() as session:
        updated: list[TransactionRead] = []
        for item in body.items:
            txn = await get_owned_or_404(
                session, Transaction, item.transaction_id, auth, what="transaction"
            )
            updated.append(await _apply_confirm(session, txn, item, auth))
        await session.commit()
    return updated


@router.post("/transactions/{transaction_id}/confirm")
async def confirm_transaction(
    transaction_id: uuid.UUID, body: ConfirmBody, auth: CurrentAuth
) -> TransactionRead:
    """Confirm one transaction's category and property.

    :param transaction_id: the transaction to confirm.
    :param body: the category, and the property to attribute it to.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if the transaction or property is not the
        caller's; 422 if the property's ownership cannot apportion it.
    :returns: the confirmed transaction.
    """
    async with async_session_factory() as session:
        txn = await get_owned_or_404(
            session, Transaction, transaction_id, auth, what="transaction"
        )
        updated = await _apply_confirm(session, txn, body, auth)
        await session.commit()
    return updated


@router.post("/transactions/{transaction_id}/exclude")
async def exclude_transaction(transaction_id: uuid.UUID, auth: CurrentAuth) -> TransactionRead:
    """Mark one transaction as not part of the property business.

    Audited exactly like a confirmation: excluding a line removes money from
    an export, which is as much a state change to money data as including it.

    :param transaction_id: the transaction to exclude.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if the transaction is not the caller's.
    :returns: the excluded transaction.
    """
    async with async_session_factory() as session:
        txn = await get_owned_or_404(
            session, Transaction, transaction_id, auth, what="transaction"
        )
        before = TransactionRead.model_validate(txn).model_dump(mode="json")
        txn.status = "excluded"
        await session.flush()
        await session.refresh(txn)

        updated = TransactionRead.model_validate(txn)
        session.add(
            audit(
                auth,
                "transaction.excluded",
                before=before,
                after=updated.model_dump(mode="json"),
            )
        )
        await session.commit()
    return updated
