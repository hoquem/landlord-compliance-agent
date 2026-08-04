"""Building ``audit_log`` rows -- one shape, shared by every router.

The spec requires an audit row on **every state change to money or
compliance data**, so more than one router writes them, and a divergent
second copy of "what an audit row looks like" is how a trail stops being
comparable across actions.

Lived in ``routers/portfolio.py`` until Task 15 needed it too. Moved rather
than copied: Task 13b's Step 4a fix round exists because a rule about
auditing was restated in one place and read as exhaustive.

:seealso: backend/src/api/routers/portfolio.py, transactions.py (its callers).
"""

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
