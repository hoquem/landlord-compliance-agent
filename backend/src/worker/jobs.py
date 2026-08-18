"""Job handlers -- what the poller actually runs.

One handler per ``job_queue.type``, registered in :data:`HANDLERS`. A handler
receives an open session and the claimed job row, does its work, and returns;
raising is how it reports failure, and :mod:`src.worker.main` turns that into
``status='failed'`` with the exception text. **No handler retries.** A job
that failed for a real reason would fail again, and a retry loop turns one
visible failure into a quiet one repeated forever.

**The worker is the one component that legitimately reads across orgs**, and
that makes it the one place where a missing filter has no backstop at all --
there is no authenticated caller whose ``org_id`` bounds the query.

**It also holds the one credential row-level security does not apply to.**
Since 2026-08-06 the API connects as ``app_api``, where an unfiltered query
returns only the caller's org; the worker connects as ``app_worker``, which
has ``BYPASSRLS``, because claiming a job from a shared queue means reading
rows belonging to orgs it is not acting for. So the guarantee the rest of the
codebase gained does not extend here, deliberately, and the filters below are
still the entire boundary. That asymmetry was the trade: many query sites
across seven routers get the database's help, and the one file that cannot is
small enough to watch. Every
statement below is filtered by ``job.org_id``, taken from the claimed row and
never from the payload: the payload is client-influenced data (it comes from
whatever enqueued the job), while the row's ``org_id`` was written by the
API alongside the import it describes.

:seealso: backend/src/flows/categorise.py (the flow this drives);
    backend/src/worker/main.py (the loop that calls these).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.audit import agent_audit
from src.db.models import Import, JobQueue, Property, Transaction

#: The environment variable that actually disables CrewAI's telemetry, as
#: measured against the installed version rather than taken from its docs.
#:
#: ``crewai.telemetry.Telemetry._is_telemetry_disabled`` reads exactly three
#: names -- ``OTEL_SDK_DISABLED``, ``CREWAI_DISABLE_TELEMETRY`` and
#: ``CREWAI_DISABLE_TRACKING``. **``CREWAI_TELEMETRY_OPT_OUT``, which this
#: repo's ``.env`` sets and the plan names, is read by no installed package
#: at all** (verified by grepping the whole of ``site-packages``); it is the
#: CrewAI 0.x spelling and is inert in 1.15.8. So this is the one to check.
TELEMETRY_OFF_ENV_VAR = "OTEL_SDK_DISABLED"

#: Most recent confirmed transactions offered to the agent as few-shot
#: examples. The flow *enforces* this cap but deliberately does not select
#: for you (``CategoriseStatementFlow.validate_inputs`` raises above it), so
#: choosing which 50 is the worker's job.
MAX_FEW_SHOT = 50


class TelemetryNotDisabledError(RuntimeError):
    """Raised when the worker would run with CrewAI telemetry still live.

    Bank statement descriptions are the most sensitive data in this system,
    and the categoriser sends them to an LLM by design -- but nothing should
    also be shipping spans to a third party as a side effect. Refusing to
    start is the only honest response; running and hoping is not.
    """

    def __init__(self) -> None:
        super().__init__(
            f"{TELEMETRY_OFF_ENV_VAR} is not 'true'. Refusing to start: the categoriser "
            "handles bank data. Run the worker with `uv run --env-file ../.env`, which "
            "sets it."
        )


def assert_telemetry_disabled() -> None:
    """Refuse to proceed unless CrewAI telemetry is switched off.

    **Must be called before anything imports CrewAI**, and that ordering is
    the entire reason :func:`handle_categorise` imports the flow lazily.
    ``crewai/llm.py`` calls ``load_dotenv()`` at module scope (measured, line
    71 of the installed 1.15.8), which walks up from the working directory
    and injects the repo's ``.env`` into the process. Import CrewAI first and
    this check passes because *CrewAI itself* just set the variable --
    proving only that a ``.env`` file exists on disk, not that the deployed
    environment was configured. Checking first makes the requirement real.

    :raises TelemetryNotDisabledError: if the variable is not ``true``.
    """
    if os.environ.get(TELEMETRY_OFF_ENV_VAR, "").strip().lower() != "true":
        raise TelemetryNotDisabledError


def _property_label(prop: Property) -> str:
    """Build the human-readable label the agent sees for a property.

    :param prop: the property.
    :returns: address line 1 plus postcode, e.g. ``"106 Sample Cres, LU1 1AA"``.
    """
    return f"{prop.address_line1}, {prop.postcode}"


def _signed_for_flow(txn: Transaction) -> Decimal:
    """Re-sign a stored transaction the way the *parser* signs one.

    The flow's ``StatementLineInput`` mirrors
    :class:`~src.core.parser.ParsedLine`, whose ``amount`` is negative for
    money out. A ``transactions`` row instead carries a magnitude plus a
    ``direction``, so the sign has to be put back.

    **This is not the export sign rule**, and confusing the two is the
    subtlest mistake available here.
    :func:`~src.core.export_pack.signed_amount` derives the sign from the
    *category* as well as the direction, so that a refund reduces an
    expense. That rule cannot apply at this point in the pipeline: these
    transactions are ``unclassified``, which is the whole reason they are
    being sent to the categoriser. Direction is all there is.

    Getting it backwards would not fail. Every description would simply
    arrive attached to the wrong sign, and the agent would quietly propose
    income for expenses across the board.

    :param txn: an unclassified transaction row.
    :returns: the signed amount, negative for money out.
    """
    return txn.amount if txn.direction == "in" else -txn.amount


async def handle_categorise(session: AsyncSession, job: JobQueue) -> None:
    """Propose HMRC categories for one import's unclassified transactions.

    Writes nothing but proposals: every touched row becomes ``proposed``,
    never ``confirmed``. The spec permits no auto-confirmation at any
    confidence, and :mod:`src.core.export_pack` refuses to export while a
    ``proposed`` line remains -- so a human still stands between the agent
    and a filed return.

    :param session: the open session; the caller commits.
    :param job: the claimed ``categorise`` job. Its ``org_id`` scopes every
        query here.
    :raises KeyError: if the payload has no ``import_id`` -- an enqueuer bug,
        and loud beats a job that silently does nothing.
    :raises LookupError: if the import is not in the job's org.
    """
    # Imported here, not at module scope, so that `assert_telemetry_disabled`
    # in main.py runs before CrewAI's import-time `load_dotenv()`. See that
    # function's docstring -- this lazy import is load-bearing, not style.
    from src.flows.categorise import (
        CategoriseStatementFlow,
        ConfirmedExample,
        OrgProperty,
        StatementLineInput,
    )

    import_id = uuid.UUID(str(job.payload["import_id"]))
    record = await session.scalar(
        select(Import).where(Import.id == import_id, Import.org_id == job.org_id)
    )
    if record is None:
        raise LookupError(f"import {import_id} is not in org {job.org_id}")

    # Ordered deterministically because the position in this list *is* the
    # `line_index` the agent proposes against. An unstable order would
    # attach proposals to the wrong transactions, plausibly.
    pending = list(
        await session.scalars(
            select(Transaction)
            .where(
                Transaction.org_id == job.org_id,
                Transaction.import_id == import_id,
                Transaction.status == "unclassified",
            )
            .order_by(Transaction.date, Transaction.id)
        )
    )
    if not pending:
        # Task 11's review: the flow accepts an empty line list and would
        # make a paid LLM call that can only return an empty proposal set.
        # Nothing to proposeis a completed job, not a failed one.
        return

    properties = list(
        await session.scalars(
            select(Property)
            .where(Property.org_id == job.org_id)
            .order_by(Property.created_at, Property.id)
        )
    )
    # Org-scoped **by construction** -- filtered in the query, not checked
    # afterwards. The flow validates proposed property ids against exactly
    # this list, so an unfiltered one would let the agent attach another
    # org's property to this org's transaction and pass every check. That is
    # the cross-tenant leak on this path, and it would look entirely
    # plausible in the review screen.
    few_shot = list(
        await session.scalars(
            select(Transaction)
            .where(
                Transaction.org_id == job.org_id,
                Transaction.status == "confirmed",
                Transaction.hmrc_category.is_not(None),
            )
            # By transaction date rather than by when it was confirmed:
            # `date` is immutable, so the example set is reproducible, while
            # `updated_at` shifts under any later edit.
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(MAX_FEW_SHOT)
        )
    )

    flow = CategoriseStatementFlow()
    # `kickoff_async` rather than `kickoff`: the sync entry point detects a
    # running loop and hops to a ThreadPoolExecutor, blocking this one on
    # `.result()`. Awaiting the async form skips the hop entirely.
    await flow.kickoff_async(
        {
            "lines": [
                StatementLineInput(
                    date=txn.date, description=txn.description, amount=_signed_for_flow(txn)
                ).model_dump(mode="json")
                for txn in pending
            ],
            "properties": [
                OrgProperty(
                    id=prop.id,
                    label=_property_label(prop),
                    mortgage_type=prop.mortgage_type or "none",
                ).model_dump(mode="json")
                for prop in properties
            ],
            "few_shot": [
                ConfirmedExample(
                    description=txn.description,
                    amount=txn.amount,
                    hmrc_category=txn.hmrc_category,
                    property_id=txn.property_id,
                ).model_dump(mode="json")
                for txn in few_shot
            ],
        }
    )

    proposals = flow.state.proposals
    if proposals is None:
        raise RuntimeError("flow completed without producing proposals")

    for proposal in proposals.proposals:
        txn = pending[proposal.line_index]
        before = {
            "hmrc_category": None,
            "property_id": None,
            "confidence": None,
            "proposed_by": None,
            "status": txn.status,
        }
        txn.hmrc_category = proposal.hmrc_category
        txn.property_id = proposal.property_id
        # `str(Decimal(float))` would carry the float's full binary
        # expansion into a numeric(3,2) column; via `str` it does not.
        txn.confidence = Decimal(str(proposal.confidence))
        txn.proposed_by = str(job.id)
        txn.status = "proposed"
        session.add(
            agent_audit(
                job.org_id,
                "transaction.proposed",
                before=before,
                after={
                    "hmrc_category": txn.hmrc_category.value,
                    "property_id": str(txn.property_id) if txn.property_id else None,
                    "confidence": str(txn.confidence),
                    "proposed_by": txn.proposed_by,
                    "status": txn.status,
                },
            )
        )


#: Handler per ``job_queue.type``. A job whose type is absent fails loudly
#: rather than being skipped -- an unhandled type is a deployment mismatch,
#: and a worker that quietly ignores work is indistinguishable from one that
#: has crashed.
HANDLERS: dict[str, Callable[[AsyncSession, JobQueue], Awaitable[None]]] = {
    "categorise": handle_categorise,
}

#: Import status to set when a job of a given type fails. A failed
#: categorisation must not leave its import looking merely ``parsed``: the
#: imports screen would show it as healthy and the user would wait forever
#: for proposals that are never coming.
FAILURE_IMPORT_STATUS: dict[str, str] = {
    "categorise": "categorisation_failed",
}
