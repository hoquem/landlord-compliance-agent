"""Statement import: upload a bank CSV, store it, parse it, queue categorisation.

**Every query here filters ``org_id``**, in the statement itself or through
:func:`~src.api.scoping.get_owned_or_404`. ``src/db/session.py`` connects as
the ``postgres`` superuser (as ``DATABASE_URL`` is configured), so the
row-level-security policies of ``0002_rls.sql`` and ``0003_storage.sql`` are
inert on everything below -- they protect the direct-from-Flutter PostgREST
and storage paths, not this one. A forgotten filter here is a silent
cross-tenant leak with no backstop, so ``test_imports.py``'s isolation
section is what holds this up.

Three things are load-bearing beyond ordinary CRUD:

* **The stored object path is a tenant boundary.** It is built by
  :func:`~src.api.storage.statement_object_path` from the authenticated
  caller's ``org_id`` and a *sanitised* leaf of the client's filename. The
  client never supplies a path. See ``src/api/storage.py``.
* **A parse failure is recorded, not rolled back.** The ``imports`` row
  survives with ``status='failed'`` and the offending row number in
  ``error_detail``, because "which row broke, and where" is the product's
  failure UX -- rolling back would leave the user nothing to look at. No
  ``transactions`` are written, so there is never a partial ingest.
* **The sign convention changes representation here.** ``core.parser``
  emits a **signed** amount (negative means money out); ``transactions``
  stores a **magnitude** plus a ``direction`` enum. This module is the only
  place the two meet, so an inversion would look correct at both ends --
  the CSV would still read -84.99 and the row would still read 84.99.
  Pinned by ``test_signed_parser_amounts_become_magnitude_plus_direction``.

:seealso: backend/src/core/parser.py (the format registry, keyed by the
    ``source_bank`` this endpoint requires); backend/src/api/storage.py;
    supabase/migrations/0003_storage.sql.
"""

import datetime
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from src.api.auth import CurrentAuth
from src.api.scoping import get_owned_or_404
from src.api.storage import upload_statement
from src.core.parser import (
    ParsedLine,
    ParserError,
    StatementParseError,
    UnknownStatementFormatError,
    is_registered_bank,
    parse_statement,
    registered_banks,
)
from src.db.models import Entity, Import, JobQueue, Transaction
from src.db.session import async_session_factory

router = APIRouter(tags=["imports"])

ImportStatus = Literal["pending", "parsed", "failed", "categorisation_failed"]


class ImportRead(BaseModel):
    """An import as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    entity_id: uuid.UUID | None
    file_path: str
    source_bank: str
    period_start: datetime.date | None
    period_end: datetime.date | None
    status: ImportStatus
    error_detail: dict | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def _unprocessable(detail: str) -> HTTPException:
    """Build the 422 used for a request this endpoint cannot act on."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _to_row(line: ParsedLine, *, org_id: uuid.UUID, entity_id: uuid.UUID, import_id: uuid.UUID
            ) -> Transaction:
    """Convert one parsed line into an unclassified transaction row.

    **The representation changes here**: ``line.amount`` is signed, the
    column is a magnitude with a separate ``direction``. ``abs()`` and the
    sign test are the whole conversion, and reversing either is invisible
    downstream -- see this module's docstring.

    :param line: one parsed statement line.
    :param org_id: the owning org.
    :param entity_id: the entity this import was filed against.
    :param import_id: the import that produced this line.
    :returns: an unattached :class:`~src.db.models.Transaction`.
    """
    return Transaction(
        org_id=org_id,
        import_id=import_id,
        entity_id=entity_id,
        date=line.date,
        amount=abs(line.amount),
        direction="out" if line.amount < 0 else "in",
        description=line.description,
        status="unclassified",
    )


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def create_import(
    auth: CurrentAuth,
    file: Annotated[UploadFile, File()],
    entity_id: Annotated[uuid.UUID, Form()],
    source_bank: Annotated[str, Form()],
) -> ImportRead:
    """Upload, store and parse one bank statement.

    Returns **201 whether or not parsing succeeded**: a refused *file* is a
    recorded outcome, not a rejected request. The distinction that does
    produce a 4xx is a refused *request* -- an unknown ``source_bank``, or an
    ``entity_id`` outside the caller's org -- because in those cases we never
    accepted a file at all.

    :param auth: the authenticated caller; the org prefix and every row
        written below come from here, never from the request.
    :param file: the statement CSV.
    :param entity_id: which of the caller's entities this statement belongs
        to. Client input, so checked against their org before use.
    :param source_bank: which registered format to parse as. The parser is
        told, never left to guess -- see ``src/core/parser.py``.
    :raises HTTPException: 404 if the entity is not the caller's; 422 if
        ``source_bank`` is not a registered format.
    :returns: the created import, with ``status`` and any ``error_detail``.
    """
    content = await file.read()

    async with async_session_factory() as session:
        # entity_id is client input; this is what stops one org filing a
        # statement against another org's entity.
        await get_owned_or_404(session, Entity, entity_id, auth, what="entity")

    # Refuse an unknown bank before storing anything: accepting a file we
    # could never read would leave an orphaned object behind.
    if not is_registered_bank(source_bank):
        raise _unprocessable(str(UnknownStatementFormatError(source_bank)))

    file_path = await upload_statement(auth.org_id, file.filename or "statement.csv", content)

    parsed: list[ParsedLine] = []
    error_detail: dict | None = None
    try:
        # `parse_statement` takes a path, and the file lives in object
        # storage rather than on disk, so it is staged through a temporary
        # file. Deleted on exit either way.
        with tempfile.NamedTemporaryFile(suffix=".csv") as tmp:
            tmp.write(content)
            tmp.flush()
            parsed = parse_statement(Path(tmp.name), bank=source_bank)
    except StatementParseError as exc:
        error_detail = {"row_number": exc.row_number, "message": exc.message}
    except ParserError as exc:
        # Format mismatch and decode failure. Both are problems with the
        # *file*, so they are recorded on the import like any bad row rather
        # than raised, which would discard the record of the attempt.
        #
        # Deliberately `ParserError` and not `Exception`: this repo has no
        # broad excepts, and catching one here would swallow a bug in this
        # module as though the user's file were at fault.
        error_detail = {"row_number": None, "message": str(exc)}

    async with async_session_factory() as session:
        record = Import(
            org_id=auth.org_id,
            entity_id=entity_id,
            file_path=file_path,
            source_bank=source_bank,
            status="failed" if error_detail else "parsed",
            error_detail=error_detail,
        )
        session.add(record)
        await session.flush()

        if error_detail is None:
            session.add_all(
                [
                    _to_row(
                        line, org_id=auth.org_id, entity_id=entity_id, import_id=record.id
                    )
                    for line in parsed
                ]
            )
            session.add(
                JobQueue(
                    org_id=auth.org_id,
                    job_type="categorise",
                    payload={"import_id": str(record.id)},
                    status="queued",
                )
            )

        await session.refresh(record)
        created = ImportRead.model_validate(record)
        await session.commit()
    return created


@router.get("/banks")
async def list_banks(auth: CurrentAuth) -> list[str]:
    """List the statement formats the parser can read.

    Exists so the upload form does not hard-code the list. ``core.parser``'s
    registry is the source of truth for which banks can be parsed, and a
    second copy in Dart would drift the moment a format is added -- offering
    the user a bank the parser would then refuse.

    Not org-scoped: the registry is a property of the software, not of a
    tenant. Still behind auth, because an unauthenticated caller has no
    business enumerating anything.

    :param auth: the authenticated caller.
    :returns: registered bank names, alphabetically.
    """
    return registered_banks()


@router.get("/imports")
async def list_imports(auth: CurrentAuth) -> list[ImportRead]:
    """List the caller's org's imports, oldest first.

    :param auth: the authenticated caller.
    :returns: every import in their org, and only those.
    """
    async with async_session_factory() as session:
        records = await session.scalars(
            select(Import).where(Import.org_id == auth.org_id).order_by(Import.created_at, Import.id)
        )
        return [ImportRead.model_validate(r) for r in records]
