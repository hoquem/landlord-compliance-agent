"""Supabase Storage uploads for statement and export files.

Two public functions -- :func:`upload_statement` and :func:`upload_export`
-- hiding the whole transport: bucket name, REST shape, service-role
credentials, and -- most importantly -- the construction of the object path.

**The path is a security boundary, which is why building it is not the
caller's job.** Objects live at ``{bucket}/{org_id}/...`` and the policies
in ``supabase/migrations/0003_storage.sql`` and ``0004_exports_bucket.sql``
key on that first segment. Those policies protect the direct-from-Flutter
path only: uploads here go through the service-role key, which bypasses them,
so on this path the prefix is exactly as trustworthy as the code below.

**Storage did not get the database's guarantee.** The ledger's tenant boundary
became a row-level-security policy on 2026-08-06; object paths have no
equivalent, so this module is still the whole of it. Hence the interface takes
``org_id`` and a *filename*, never a path, and reduces the filename to a
bare leaf name before using it. A caller cannot ask for a path, so a caller
cannot ask for the wrong one.

Statements and exports use separate buckets. The predicate is text-compared
so one bucket would work, but a generated return stored under a path saying
``statements/`` is a lie the next reader trips on -- and the two have
different lifecycles: a statement is input the user supplied, an export is
output this system produced and may regenerate.

No Supabase SDK: the upload is one authenticated POST, and ``httpx`` is
already a dependency. If signed URLs or resumable uploads are wanted later,
swapping a client in behind this function changes nothing above it -- which
is the point of it being one function rather than a leaked client object.

:seealso: supabase/migrations/0003_storage.sql (the policies this path
    convention exists to satisfy); backend/tests/api/test_imports.py (the
    filename-escape cases).
"""

import os
import re
import uuid

import httpx

#: The bucket created by ``0003_storage.sql``. Private: a public bucket
#: serves objects to anyone holding the URL, bypassing every policy.
BUCKET = "statements"

#: The bucket created by ``0004_exports_bucket.sql``, private for the same
#: reason -- a filed return is as sensitive as the statements behind it.
EXPORTS_BUCKET = "exports"

#: Everything not in this set is replaced in a client-supplied filename.
#: Deliberately an allowlist -- a denylist of "dangerous" characters is a
#: guess about an attacker's alphabet, and path separators, ``..``, control
#: characters and percent-encodings are all excluded by construction here.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageUploadError(RuntimeError):
    """Raised when Supabase Storage refuses or fails an upload.

    Loud by design: an import whose file did not store must not proceed to
    parsing and leave an ``imports`` row pointing at nothing.
    """


def safe_leaf_name(filename: str) -> str:
    """Reduce a client-supplied filename to a harmless leaf name.

    Takes the last path segment, then replaces every character outside
    ``[A-Za-z0-9._-]``. ``"../../etc/passwd"`` becomes ``"passwd"``;
    ``"..%2f..%2fx.csv"`` becomes ``".._.._x.csv"`` -- no separators, so it
    cannot climb. An empty or fully-stripped name falls back to
    ``"statement.csv"`` rather than producing a path ending in ``/``.

    :param filename: whatever the client sent.
    :returns: a single path segment safe to append to an org prefix.
    """
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE.sub("_", leaf)
    # Collapse dot runs. With separators already gone a residual ".." cannot
    # traverse anything -- but "..%2f..%2fx.csv" survives sanitising as
    # ".._2f.._2fx.csv", and a stored path containing ".." is impossible to
    # assert about cheaply and invites a reader to assume the worst. One dot
    # is all any real filename needs.
    cleaned = re.sub(r"\.{2,}", ".", cleaned).strip("._") or "statement.csv"
    return cleaned


def statement_object_path(org_id: uuid.UUID, filename: str) -> str:
    """Build the storage path for one uploaded statement.

    The org prefix comes from ``org_id`` -- which callers take from the
    authenticated context, never from the request body -- and the client's
    filename contributes only a sanitised leaf. A per-upload UUID segment
    keeps two uploads of ``statement.csv`` from colliding without needing to
    consult storage first.

    :param org_id: the owning org, from the authenticated caller.
    :param filename: the client-supplied filename.
    :returns: an object path of the form ``{org_id}/{uuid}/{leaf}``.
    """
    return f"{org_id}/{uuid.uuid4()}/{safe_leaf_name(filename)}"


async def _upload(bucket: str, path: str, content: bytes, content_type: str) -> str:
    """PUT one object into a bucket as the service role, or fail loudly.

    The single place the transport lives. Both public upload functions build
    a path and then come here, so there is one answer to "what does an
    upload do when storage says no".

    :param bucket: the destination bucket.
    :param path: the object path, already built from a trusted org prefix.
    :param content: the raw bytes.
    :param content_type: MIME type to store the object as.
    :raises StorageUploadError: if Supabase Storage returns a non-2xx, or
        the request cannot be made at all.
    :returns: ``path``, so callers can record it.
    """
    base_url = os.environ["SUPABASE_URL"]
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/storage/v1/object/{bucket}/{path}",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": content_type,
                },
                content=content,
            )
    except httpx.HTTPError as exc:
        raise StorageUploadError(f"could not reach storage for {path!r}: {exc}") from exc

    if resp.status_code >= 300:
        raise StorageUploadError(
            f"storage refused {path!r}: {resp.status_code} {resp.text[:200]}"
        )
    return path


async def upload_statement(org_id: uuid.UUID, filename: str, content: bytes) -> str:
    """Store one statement file under the org's prefix and return its path.

    :param org_id: the owning org, from the authenticated caller. Never from
        client input -- it is the whole tenant boundary for stored objects.
    :param filename: the client-supplied filename; sanitised to a leaf.
    :param content: the raw file bytes.
    :raises StorageUploadError: if the upload fails; see :func:`_upload`.
    :returns: the object path the file was stored at, for ``imports.file_path``.
    """
    return await _upload(
        BUCKET, statement_object_path(org_id, filename), content, "text/csv"
    )


async def signed_download_url(bucket: str, path: str, *, expires_in: int = 300) -> str:
    """Mint a short-lived URL for one stored object.

    The buckets are private, which is the point -- a public bucket serves
    objects to anyone holding the URL, bypassing every policy. So a browser
    cannot fetch an export directly, and this is how it gets one: the API
    proves the caller owns the document, then asks storage for a URL that
    expires.

    Five minutes by default. Long enough to click, short enough that a URL
    copied out of a browser history is worthless by the time anyone tries it.

    :param bucket: the bucket the object lives in.
    :param path: the object path.
    :param expires_in: lifetime in seconds.
    :raises StorageUploadError: if storage refuses or cannot be reached.
    :returns: an absolute, time-limited URL.
    """
    base_url = os.environ["SUPABASE_URL"]
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/storage/v1/object/sign/{bucket}/{path}",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                },
                json={"expiresIn": expires_in},
            )
    except httpx.HTTPError as exc:
        raise StorageUploadError(f"could not reach storage for {path!r}: {exc}") from exc

    if resp.status_code >= 300:
        raise StorageUploadError(
            f"storage refused to sign {path!r}: {resp.status_code} {resp.text[:200]}"
        )
    # The API returns a path relative to /storage/v1, not an absolute URL.
    return f"{base_url}/storage/v1{resp.json()['signedURL']}"


async def upload_export(
    org_id: uuid.UUID, filename: str, content: bytes, content_type: str
) -> str:
    """Store one generated export file under the org's prefix.

    Unlike :func:`upload_statement` the filename is ours, not a client's --
    but it goes through the same sanitising path builder anyway. An export
    filename is assembled from an entity name and a period, and an entity
    name *is* client input.

    :param org_id: the owning org, from the authenticated caller.
    :param filename: the generated filename; sanitised to a leaf.
    :param content: the raw file bytes.
    :param content_type: ``text/csv`` or ``application/pdf``.
    :raises StorageUploadError: if the upload fails; see :func:`_upload`.
    :returns: the object path, for ``documents.storage_path``.
    """
    return await _upload(
        EXPORTS_BUCKET, statement_object_path(org_id, filename), content, content_type
    )
