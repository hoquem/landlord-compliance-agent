"""Downloading a generated document.

The export buckets are **private**, deliberately: a public bucket serves
objects to anyone holding the URL, and the objects here are somebody's tax
figures. So a browser cannot fetch one directly. This router is the way in --
it proves the caller owns the document, then mints a short-lived signed URL.

Ownership is the whole job. ``documents.storage_path`` is free text and the
storage policies of ``0004_exports_bucket.sql`` protect only the
direct-from-Flutter path. **Storage has no equivalent of row-level security**,
so unlike every database query in this codebase the lookup below has no
backstop: it is what stops one org signing a URL for another org's export, and
if it were dropped nothing else would notice. The ledger got a database-level
guarantee on 2026-08-06; object storage did not, and cannot.

:seealso: backend/src/api/storage.py; backend/src/api/routers/exports.py.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.auth import CurrentAuth
from src.api.scoping import get_owned_or_404
from src.api.storage import EXPORTS_BUCKET, signed_download_url
from src.db.models import Document
from src.db.session import org_session

router = APIRouter(tags=["documents"])


class DownloadRead(BaseModel):
    """A time-limited URL for one document.

    :ivar url: absolute and short-lived; not worth copying anywhere.
    :ivar expires_in: seconds the URL remains valid.
    """

    url: str
    expires_in: int


#: How long a download link lives. Long enough to click, short enough that
#: one left in a browser history is useless.
DOWNLOAD_TTL_SECONDS = 300


@router.get("/documents/{document_id}/download")
async def download_document(document_id: uuid.UUID, auth: CurrentAuth) -> DownloadRead:
    """Mint a signed URL for one of the caller's documents.

    :param document_id: the document to download.
    :param auth: the authenticated caller.
    :raises HTTPException: 404 if the document is not theirs -- which is the
        only thing preventing a cross-org download -- storage policies do not
        apply to a service-role upload, and there is no second check.
    :returns: the URL and its lifetime.
    """
    async with org_session(auth.user_id) as session:
        document = await get_owned_or_404(
            session, Document, document_id, auth, what="document"
        )
        path = document.storage_path

    return DownloadRead(
        url=await signed_download_url(
            EXPORTS_BUCKET, path, expires_in=DOWNLOAD_TTL_SECONDS
        ),
        expires_in=DOWNLOAD_TTL_SECONDS,
    )
