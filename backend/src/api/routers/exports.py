"""Generating a quarter's export pack -- the figures a return is filed from.

**Every query filters ``org_id``**, in the statement or through
:func:`~src.api.scoping.get_owned_or_404`. ``DATABASE_URL`` is the
``postgres`` superuser, so the RLS policies of ``0002_rls.sql`` and
``0004_exports_bucket.sql`` are inert here; the filters and the
server-built storage prefix are the entire tenant boundary. The failure a
missed filter produces on *this* endpoint is not a leak of ids but an
overstated tax return, which looks entirely plausible.

Only one of those filters is *pinned*:
``test_another_orgs_transactions_never_reach_the_totals`` dies if
:func:`_load_transactions` drops its ``org_id`` (proved by mutation), and
that is the one that could move money. The filters in
:func:`_load_ownerships` and :func:`_load_filed_quarters` cannot be killed
by mutation and are **not** covered -- ``entity_id`` and ``property_id``
are globally unique, and ``0002_rls.sql``'s composite foreign keys already
guarantee a ``property_ownership`` row's org matches its property's. They
are belt-and-braces, kept so that every query in this module reads the
same way, not because a test would notice their removal.

Four things here are not ordinary CRUD.

* **The maths is not in this module.** Totals, the sign rule, the refusal
  rules and the history guard all live in :mod:`src.core.export_pack` and
  :mod:`src.core.quarters`, where they are testable with plain data. This
  router's job is to load rows, hand them over, and record what comes back.
  Re-deriving any of it here is how "what the API filed" and "what the core
  computes" drift apart.
* **Every transaction in the org is a candidate, not just the entity's.**
  An unreviewed line has no category and no property yet, so there is no
  way to know whose figures it would land on; once reviewed it might be
  allocated to a jointly-owned property and move every co-owner's totals.
  Filtering to ``transactions.entity_id`` would filter on the one attribute
  that does *not* decide attribution -- ``cumulative_totals`` splits by
  ownership and falls back to ``entity_id`` only for lines with no property.
* **Nothing is overwritten.** A re-export inserts a new ``mtd_quarters``
  row with the next ``version``; ``0001_core.sql`` keeps every export so
  that what was filed on the day stays readable. The unique constraint on
  ``(entity_id, tax_year, quarter, version)`` is what makes two concurrent
  exports a loud 409 rather than a silent lost update.
* **Changed history is a 409, not a 422.** A 422 says "fix your request",
  and nothing about the request is wrong. What conflicts is previously
  filed state, and reconciling it is a decision about a tax return the user
  makes outside this call -- which is also why
  :class:`~src.core.export_pack.CumulativeDecreaseError` has no override
  flag.

:seealso: backend/src/core/export_pack.py (the pack and the guards);
    backend/src/core/export_html.py; backend/src/api/pdf.py;
    supabase/migrations/0004_exports_bucket.sql.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.api.audit import audit
from src.api.auth import CurrentAuth
from src.api.pdf import render_pdf
from src.api.scoping import get_owned_or_404
from src.api.storage import upload_export
from src.core.categories import HmrcCategory
from src.core.export_html import render_pack_html
from src.core.export_pack import (
    CATEGORY_COLUMNS,
    CumulativeDecreaseError,
    ExportBlockedError,
    ExportEntity,
    QuarterlyPack,
    SimplePnlPack,
    TxnRow,
    assert_history_intact,
    build_export_pack,
    quarter_label,
    quarter_number,
    totals_from_columns,
    totals_to_columns,
)
from src.core.quarters import MissingOwnershipError, format_tax_year
from src.core.splits import InvalidOwnershipError
from src.db.models import Document, Entity, MtdQuarter, PropertyOwnership, Transaction
from src.db.session import async_session_factory

router = APIRouter(tags=["exports"])


class ExportQuarterBody(BaseModel):
    """Body of ``POST /exports/quarter``."""

    model_config = ConfigDict(extra="forbid")

    entity_id: uuid.UUID
    #: Calendar year the tax year *starts* in -- 2026 means 2026-27. Bounded
    #: by ``format_tax_year``, not here, so there is one answer to "which
    #: years exist".
    tax_year: int
    quarter: int = Field(ge=1, le=4)


class DocumentRef(BaseModel):
    """One generated file, as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    storage_path: str


class ExportRead(BaseModel):
    """The outcome of one export."""

    entity_id: uuid.UUID
    tax_year: str
    quarter: str
    #: ``None`` for a company: outside MTD ITSA, so nothing is filed and
    #: there is no version to quote.
    version: int | None
    mtd_quarter_id: uuid.UUID | None
    documents: list[DocumentRef]


def _unprocessable(detail: str) -> HTTPException:
    """Build the 422 used when a request is well-formed but cannot be applied."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


async def _load_transactions(session, org_id: uuid.UUID) -> list[TxnRow]:
    """Load every transaction in the org as a core :class:`TxnRow`.

    Deliberately unfiltered by entity and by date -- see this module's
    docstring, and :func:`~src.core.export_pack.build_export_pack`, which
    windows and attributes them.

    :param session: the session to query in.
    :param org_id: the caller's org.
    :returns: one :class:`TxnRow` per transaction.
    """
    rows = await session.scalars(select(Transaction).where(Transaction.org_id == org_id))
    return [
        TxnRow(
            id=row.id,
            date=row.date,
            # Magnitude and direction stay separate all the way into the
            # core, which derives the sign from the category too. A
            # pre-signed amount built here would be wrong for refunds and
            # right for everything anyone checks by hand.
            amount=row.amount,
            direction=row.direction,
            hmrc_category=row.hmrc_category,
            status=row.status,
            entity_id=row.entity_id,
            property_id=row.property_id,
        )
        for row in rows
    ]


async def _load_ownerships(
    session, org_id: uuid.UUID
) -> dict[uuid.UUID, dict[uuid.UUID, Decimal]]:
    """Load the org's ownership shares as ``property_id -> {entity_id: pct}``.

    :param session: the session to query in.
    :param org_id: the caller's org.
    :returns: the mapping :func:`build_export_pack` attributes by.
    """
    rows = await session.scalars(
        select(PropertyOwnership).where(PropertyOwnership.org_id == org_id)
    )
    ownerships: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = {}
    for row in rows:
        ownerships.setdefault(row.property_id, {})[row.entity_id] = row.ownership_percentage
    return ownerships


async def _load_filed_quarters(
    session, org_id: uuid.UUID, entity_id: uuid.UUID, tax_year_label: str
) -> dict[int, dict[HmrcCategory, Decimal]]:
    """Load the **latest version** of each quarter already filed this tax year.

    ``DISTINCT ON (quarter) ... ORDER BY quarter, version DESC`` is the
    whole point: a superseded version is not what history should be checked
    against. Q1 filed at 500 and re-filed at 1200 agrees with data that
    recomputes to 1200; comparing against version 1 would refuse a correct
    return.

    :param session: the session to query in.
    :param org_id: the caller's org.
    :param entity_id: the entity being exported.
    :param tax_year_label: the tax year in ``2026-27`` notation.
    :returns: ``quarter number -> filed totals``, for
        :func:`~src.core.export_pack.assert_history_intact`, which compares
        only the quarters earlier than the one being exported.
    """
    rows = await session.scalars(
        select(MtdQuarter)
        .where(
            MtdQuarter.org_id == org_id,
            MtdQuarter.entity_id == entity_id,
            MtdQuarter.tax_year == tax_year_label,
        )
        .distinct(MtdQuarter.quarter)
        .order_by(MtdQuarter.quarter, MtdQuarter.version.desc())
    )
    return {
        quarter_number(row.quarter): totals_from_columns(
            {column: getattr(row, column) for column in CATEGORY_COLUMNS.values()}
        )
        for row in rows
    }


async def _next_version(
    session, org_id: uuid.UUID, entity_id: uuid.UUID, tax_year_label: str, quarter: str
) -> int:
    """Return the version number this export should be filed as.

    :param session: the session to query in.
    :param org_id: the caller's org.
    :param entity_id: the entity being exported.
    :param tax_year_label: the tax year in ``2026-27`` notation.
    :param quarter: the quarter label.
    :returns: one more than the highest existing version, or 1.
    """
    highest = await session.scalar(
        select(MtdQuarter.version)
        .where(
            MtdQuarter.org_id == org_id,
            MtdQuarter.entity_id == entity_id,
            MtdQuarter.tax_year == tax_year_label,
            MtdQuarter.quarter == quarter,
        )
        .order_by(MtdQuarter.version.desc())
        .limit(1)
    )
    return (highest or 0) + 1


async def _store_files(
    org_id: uuid.UUID,
    pack: QuarterlyPack | SimplePnlPack,
    *,
    tax_year_label: str,
    quarter: str,
    version: int | None,
) -> list[tuple[str, str]]:
    """Render and upload the three generated files.

    :param org_id: the owning org; the storage prefix comes from here and
        never from anything client-supplied.
    :param pack: the built pack.
    :param tax_year_label: the tax year in ``2026-27`` notation.
    :param quarter: the quarter label.
    :param version: the version being filed, or ``None`` for a company.
    :raises StorageUploadError: if any upload fails -- loudly, because a
        ``documents`` row pointing at nothing is worse than no row.
    :returns: ``(kind, storage_path)`` per file, in a stable order.
    """
    stem = f"{tax_year_label}-{quarter}"
    files = (
        ("export_category_csv", f"{stem}-categories.csv", pack.category_csv.encode(), "text/csv"),
        ("export_property_csv", f"{stem}-properties.csv", pack.property_csv.encode(), "text/csv"),
        (
            "export_pdf",
            f"{stem}.pdf",
            render_pdf(render_pack_html(pack, version=version)),
            "application/pdf",
        ),
    )
    return [
        (kind, await upload_export(org_id, name, content, content_type))
        for kind, name, content, content_type in files
    ]


@router.post("/exports/quarter", status_code=status.HTTP_201_CREATED)
async def export_quarter(body: ExportQuarterBody, auth: CurrentAuth) -> ExportRead:
    """Generate one entity's export pack for a quarter, and file the totals.

    :param body: the entity and period to export.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if the entity is not the caller's; 422 if the
        tax year is unsupported, if any transaction in the period is still
        unreviewed (naming them), or if a property's ownership can no longer
        apportion its transactions; 409 if an earlier filed quarter no longer
        recomputes to what was filed, or if a concurrent export already took
        this version number.
    :returns: the filed quarter and the generated files' download refs.
    """
    quarter = quarter_label(body.quarter)
    try:
        tax_year_label = format_tax_year(body.tax_year)
    except ValueError as exc:
        raise _unprocessable(str(exc)) from exc

    async with async_session_factory() as session:
        entity = await get_owned_or_404(session, Entity, body.entity_id, auth, what="entity")
        export_entity = ExportEntity(
            id=entity.id, name=entity.name, tax_regime=entity.tax_regime
        )
        transactions = await _load_transactions(session, auth.org_id)
        ownerships = await _load_ownerships(session, auth.org_id)
        filed = await _load_filed_quarters(
            session, auth.org_id, entity.id, tax_year_label
        )

        try:
            pack = build_export_pack(
                export_entity,
                tax_year=body.tax_year,
                quarter=body.quarter,
                transactions=transactions,
                ownerships=ownerships,
            )
            # After the pack, so "go and review these lines" wins over any
            # other complaint -- it is the actionable one, and a period with
            # unreviewed lines has no settled figures to compare anyway.
            assert_history_intact(
                entity_id=entity.id,
                tax_year=body.tax_year,
                quarter=body.quarter,
                transactions=transactions,
                ownerships=ownerships,
                stored_by_quarter=filed,
            )
        except ExportBlockedError as exc:
            raise _unprocessable(str(exc)) from exc
        except CumulativeDecreaseError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except InvalidOwnershipError as exc:
            raise _unprocessable(
                f"Cannot export: {exc}. Fix the property's ownership shares first."
            ) from exc
        except MissingOwnershipError as exc:
            # A property-allocated transaction whose property has no
            # ownership rows at all. Deliberately not a bare ``KeyError``:
            # that would catch every dict miss in the core call tree and
            # answer 422 as though a future bug in `quarters.py` were the
            # user's data problem -- the same reason `imports.py` catches
            # `ParserError` rather than `Exception`.
            raise _unprocessable(
                f"Cannot export: {exc.property_id} has no ownership shares. "
                "Set them before exporting."
            ) from exc

        version = (
            await _next_version(session, auth.org_id, entity.id, tax_year_label, quarter)
            if pack.writes_mtd_quarter
            else None
        )
        stored = await _store_files(
            auth.org_id,
            pack,
            tax_year_label=tax_year_label,
            quarter=quarter,
            version=version,
        )

        documents = [
            Document(
                org_id=auth.org_id,
                storage_path=path,
                kind=kind,
                doc_metadata={
                    "entity_id": str(entity.id),
                    "tax_year": tax_year_label,
                    "quarter": quarter,
                    "version": version,
                },
            )
            for kind, path in stored
        ]
        session.add_all(documents)
        await session.flush()
        refs = [DocumentRef.model_validate(d) for d in documents]
        pdf_id = next(d.id for d in documents if d.kind == "export_pdf")

        filed_row: MtdQuarter | None = None
        if pack.writes_mtd_quarter:
            filed_row = MtdQuarter(
                org_id=auth.org_id,
                entity_id=entity.id,
                tax_year=tax_year_label,
                quarter=quarter,
                version=version,
                export_status="generated",
                generated_document_id=pdf_id,
                **{
                    column: value
                    for column, value in totals_to_columns(pack.totals).items()
                },
            )
            session.add(filed_row)
            await session.flush()

        session.add(
            audit(
                auth,
                "export.generated",
                before=None,
                after={
                    "entity_id": str(entity.id),
                    "tax_year": tax_year_label,
                    "quarter": quarter,
                    "version": version,
                    "document_ids": [str(r.id) for r in refs],
                },
            )
        )

        # Built before the commit, while the session is certainly open.
        # `imports.py` and `transactions.py` both materialise their response
        # model here for the same reason: reading ORM attributes afterwards
        # works only because `expire_on_commit=False`, and this router
        # should not be the one that breaks if that ever flips.
        result = ExportRead(
            entity_id=entity.id,
            tax_year=tax_year_label,
            quarter=quarter,
            version=version,
            mtd_quarter_id=filed_row.id if filed_row is not None else None,
            documents=refs,
        )

        try:
            await session.commit()
        except IntegrityError as exc:
            # The unique constraint on (entity_id, tax_year, quarter,
            # version). Two exports raced and both read the same highest
            # version; one of them has to be told, because the alternative
            # is one silently replacing the other's figures.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"Another export of {tax_year_label} {quarter} completed while this "
                    "one was running. Retry to file the next version."
                ),
            ) from exc

    return result
