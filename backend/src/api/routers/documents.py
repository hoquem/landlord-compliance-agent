"""Downloading a generated document.

The export buckets are **private**, deliberately: a public bucket serves
objects to anyone holding the URL, and the objects here are somebody's tax
figures. So a browser cannot fetch one directly. This router is the way in --
it proves the caller owns the document, then mints a short-lived signed URL.

Ownership is the whole job. ``documents.storage_path`` is free text and the
storage policies of ``0004_exports_bucket.sql`` protect only the
direct-from-Flutter path; the API connects as ``postgres`` and bypasses RLS.
So the org-scoped lookup below is what stops one org signing a URL for
another's export, and there is no backstop behind it.

:seealso: backend/src/api/storage.py; backend/src/api/routers/exports.py.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.auth import CurrentAuth
from src.api.scoping import get_owned_or_404
from src.api.storage import EXPORTS_BUCKET, signed_download_url
from src.db.models import Document
from src.db.session import async_session_factory

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
        only thing preventing a cross-org download, since RLS is inert on
        this connection.
    :returns: the URL and its lifetime.
    """
    async with async_session_factory() as session:
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
