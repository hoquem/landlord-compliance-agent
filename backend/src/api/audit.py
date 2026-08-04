"""Building ``audit_log`` rows -- one shape, shared by every writer.

The spec requires an audit row on **every state change to money or
compliance data**, so more than one module writes them, and a divergent
second copy of "what an audit row looks like" is how a trail stops being
comparable across actions.

Lived in ``routers/portfolio.py`` until Task 15 needed it too. Moved rather
than copied: Task 13b's Step 4a fix round exists because a rule about
auditing was restated in one place and read as exhaustive.

**Despite the path, this is not an HTTP module and the worker is a first-class
caller.** ``src.worker.jobs`` imports :func:`agent_audit` from here; that is
not the worker reaching into the API, it is both of them using the one
audit-row shape. The module stays under ``src/api/`` because moving it would
churn three working routers to fix a filename, which is not a reason.

Two actors, two functions, because they carry genuinely different facts. A
user action has an ``actor_id`` -- the person answerable for it. An agent
proposal has none: no human decided it, which is exactly why a proposal is
``status='proposed'`` and needs confirming before it can reach an export.
Recording the agent as though it were a user would erase that distinction in
the one record that exists to preserve it.

:seealso: backend/src/api/routers/portfolio.py, transactions.py,
    certificates.py, exports.py, and backend/src/worker/jobs.py (its callers).
"""

import uuid

from sqlalchemy import null

from src.api.auth import CurrentAuth
from src.db.models import AuditLog


def audit(
    auth: CurrentAuth, action: str, *, before: object | None, after: object | None
) -> AuditLog:
    """Build the ``audit_log`` row for one state change by the caller.

    The returned row is unpersisted: add it to the **same** session and
    transaction as the change it describes, so a rolled-back change cannot
    leave a record claiming it happened.

    :param auth: the caller, recorded as ``actor_type='user'`` plus their
        ``actor_id``.
    :param action: the action name, e.g. ``transaction.confirmed``.
    :param before: JSON-safe prior state, or ``None`` for a creation.
    :param after: JSON-safe new state, or ``None`` for a deletion.
    :returns: an unpersisted :class:`~src.db.models.AuditLog`.
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


def agent_audit(
    org_id: uuid.UUID, action: str, *, before: object | None, after: object | None
) -> AuditLog:
    """Build the ``audit_log`` row for one state change made by an agent.

    The same shape as :func:`audit`, differing only in the actor: the row is
    written as ``actor_type='agent'`` with **no** ``actor_id``, because there
    is no person to attribute it to. That absence is the point -- it is what
    distinguishes a machine's proposal from a human's decision when someone
    later asks who changed a figure.

    As with :func:`audit`, the returned row is unpersisted: add it to the
    same session and transaction as the change it describes.

    :param org_id: the tenant the change belongs to. Taken from the job row
        the worker claimed, never from a job payload -- the payload is data,
        and the worker is the one component that legitimately reads across
        orgs, so this is the only thing scoping the write.
    :param action: the action name, e.g. ``transaction.proposed``.
    :param before: JSON-safe prior state, or ``None``.
    :param after: JSON-safe new state, or ``None``.
    :returns: an unpersisted :class:`~src.db.models.AuditLog`.
    """
    return AuditLog(
        org_id=org_id,
        actor_type="agent",
        # No actor_id: `audit_log.actor_id` is nullable precisely so that a
        # non-human actor can be recorded without inventing an identity for
        # it. See this module's docstring.
        actor_id=None,
        action=action,
        # `null()` rather than `None` -- see `audit`, same jsonb reason.
        before=null() if before is None else before,
        after=null() if after is None else after,
    )
