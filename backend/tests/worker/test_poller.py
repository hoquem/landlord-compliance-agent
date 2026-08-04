"""Tests for the job poller and its handlers.

Runs against the live local Supabase stack::

    uv run --env-file ../.env pytest tests/worker/

**The LLM is never called.** ``CategoriseStatementFlow`` is replaced with a
recorder that captures the inputs it was given and returns proposals the
test chose. That makes the interesting assertions possible at all: what the
worker *sends* to the agent -- signed amounts, an org-scoped property list,
the right fifty few-shot examples -- is most of what can silently go wrong
here, and none of it is visible from the proposals that come back.

**The worker is the one component with no authenticated caller**, so nothing
bounds its queries except the ``org_id`` on the job row it claimed. There is
no RLS backstop either (``DATABASE_URL`` is the superuser). The isolation
section below is the whole of the tenant boundary on this path.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.core.categories import HmrcCategory
from src.flows import categorise as categorise_module
from src.flows.categorise import LineProposal, StatementProposals
from src.worker import jobs as jobs_module
from src.worker.main import claim_next_job, poll_forever, run_one_job
from tests.api.conftest import OrgUser, db


async def seed_import(org_user: OrgUser, *, status: str = "parsed") -> tuple[str, str]:
    """Create an entity and an import for it, returning both ids."""
    async with db() as conn:
        entity_id = await conn.fetchval(
            "insert into entities (org_id, name, tax_regime) "
            "values ($1, 'Owner', 'mtd_itsa') returning id",
            org_user.org_id,
        )
        import_id = await conn.fetchval(
            "insert into imports (org_id, entity_id, file_path, source_bank, status) "
            "values ($1, $2, 'seeded/stmt.csv', 'generic', $3::import_status) returning id",
            org_user.org_id,
            entity_id,
            status,
        )
    return str(entity_id), str(import_id)


async def seed_transaction(
    org_user: OrgUser,
    *,
    entity_id: str,
    import_id: str | None = None,
    amount: str = "84.99",
    direction: str = "out",
    description: str = "B&Q LUTON",
    status: str = "unclassified",
    category: str | None = None,
    when: str = "2026-07-01",
) -> str:
    """Insert one transaction directly, and return its id."""
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into transactions (org_id, entity_id, import_id, date, amount, "
                "direction, description, status, hmrc_category) "
                "values ($1, $2, $3, $4, $5, $6, $7, $8::transaction_status, "
                "$9::hmrc_category) returning id",
                org_user.org_id,
                uuid.UUID(entity_id),
                uuid.UUID(import_id) if import_id else None,
                datetime.date.fromisoformat(when),
                Decimal(amount),
                direction,
                description,
                status,
                category,
            )
        )


async def seed_property(org_user: OrgUser, line1: str = "106 Sample Cres") -> str:
    """Insert one property directly, and return its id."""
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into properties (org_id, address_line1, city, postcode, "
                "finance_cost_classification) values ($1, $2, 'Luton', 'LU1 1AA', "
                "'residential') returning id",
                org_user.org_id,
                line1,
            )
        )


async def enqueue(
    org_user: OrgUser, *, job_type: str = "categorise", payload: dict | None = None
) -> str:
    """Insert one queued job, and return its id."""
    import json

    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into job_queue (org_id, type, payload, status) "
                "values ($1, $2, $3::jsonb, 'queued') returning id",
                org_user.org_id,
                job_type,
                json.dumps(payload or {}),
            )
        )


async def job_row(job_id: str) -> dict:
    """Read one job straight from the database."""
    async with db() as conn:
        return dict(await conn.fetchrow("select * from job_queue where id = $1", uuid.UUID(job_id)))


async def txn_rows(org_id: uuid.UUID) -> list[dict]:
    """Read an org's transactions, oldest first."""
    async with db() as conn:
        return [
            dict(r)
            for r in await conn.fetch(
                "select * from transactions where org_id = $1 order by date, id", org_id
            )
        ]


class FlowRecorder:
    """Stands in for ``CategoriseStatementFlow``, recording what it was given.

    Deliberately not a ``MagicMock``: the assertions that matter are about
    the *content* of the inputs, and a recorder that also returns a real
    :class:`StatementProposals` keeps the handler's own contract (one
    proposal per line, indices in range) genuinely exercised.
    """

    #: Inputs from every kickoff across the test, newest last.
    calls: ClassVar[list[dict]] = []
    #: What the next kickoff should leave on the flow's state.
    proposals: ClassVar[StatementProposals | None] = None
    #: Raised by the next kickoff instead of returning, when set.
    raises: ClassVar[Exception | None] = None

    def __init__(self) -> None:
        self.state = SimpleNamespace(proposals=None)

    async def kickoff_async(self, inputs: dict) -> None:
        """Record the inputs and publish the test's chosen proposals."""
        FlowRecorder.calls.append(inputs)
        if FlowRecorder.raises is not None:
            raise FlowRecorder.raises
        self.state.proposals = FlowRecorder.proposals


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch) -> type[FlowRecorder]:
    """Replace the real flow with :class:`FlowRecorder` for one test.

    Patched on ``src.flows.categorise`` rather than on the worker, because
    the handler imports the class lazily *inside* the function -- so the
    binding it resolves is the module attribute, at call time.
    """
    FlowRecorder.calls = []
    FlowRecorder.proposals = StatementProposals(proposals=[])
    FlowRecorder.raises = None
    monkeypatch.setattr(categorise_module, "CategoriseStatementFlow", FlowRecorder)
    return FlowRecorder


def one_proposal(
    index: int = 0,
    category: HmrcCategory = HmrcCategory.REPAIRS_MAINTENANCE,
    confidence: float = 0.91,
    property_id: uuid.UUID | None = None,
) -> LineProposal:
    """Build one proposal."""
    return LineProposal(
        line_index=index,
        hmrc_category=category,
        property_id=property_id,
        confidence=confidence,
        rationale="seeded",
    )


# ---------------------------------------------------------------------------
# Claiming.
# ---------------------------------------------------------------------------
async def test_claiming_an_empty_queue_returns_nothing() -> None:
    """No jobs means no work, not an error."""
    assert await claim_next_job() is None


async def test_claiming_marks_the_job_running(org_user: OrgUser) -> None:
    """The claim is committed before the handler starts.

    If it were not, the row would stay locked for the whole job and
    ``SKIP LOCKED`` would be protecting against the wrong thing -- other
    workers would skip it because it is *locked*, and a crash would leave it
    locked until the connection died.
    """
    job_id = await enqueue(org_user, job_type="noop")

    claimed = await claim_next_job()

    assert claimed is not None
    assert str(claimed.id) == job_id
    assert (await job_row(job_id))["status"] == "running"


async def test_two_concurrent_claims_take_different_jobs(org_user: OrgUser) -> None:
    """Two workers must never end up running the same job.

    A duplicate claim is a duplicate LLM call and a duplicate set of
    proposals, not merely wasted work.

    Note what this does **not** prove: it passes with a plain ``FOR UPDATE``
    too, because the second claim simply blocks and Postgres then
    re-evaluates onto the next queued row. Measured, not assumed --
    ``test_a_locked_job_is_skipped_not_waited_for`` is the one that pins
    ``SKIP LOCKED``.
    """
    first = await enqueue(org_user, job_type="noop")
    second = await enqueue(org_user, job_type="noop")

    claimed = await asyncio.gather(claim_next_job(), claim_next_job())

    ids = sorted(str(job.id) for job in claimed if job is not None)
    assert ids == sorted([first, second]), "both claims should take a different job"


async def test_a_locked_job_is_skipped_not_waited_for(org_user: OrgUser) -> None:
    """``SKIP LOCKED`` is about not blocking, so blocking is what to test.

    A row another worker is mid-claim on must be stepped over. Without
    ``skip_locked`` this call waits for the lock holder to finish -- here,
    forever -- and one slow job stalls every other worker in the fleet.

    The lock is held from a separate connection for the duration, which is
    the only way to observe the difference: two racing claims in the same
    process do not, because the loser merely waits and then succeeds.
    """
    locked = await enqueue(org_user, job_type="noop")
    free = await enqueue(org_user, job_type="noop")

    async with db() as holder:
        transaction = holder.transaction()
        await transaction.start()
        try:
            await holder.fetchval(
                "select id from job_queue where id = $1 for update", uuid.UUID(locked)
            )
            claimed = await asyncio.wait_for(claim_next_job(), timeout=5)
        finally:
            await transaction.rollback()

    assert claimed is not None
    assert str(claimed.id) == free, "the locked job should have been stepped over"


async def test_jobs_are_claimed_oldest_first(org_user: OrgUser) -> None:
    """A queue that is not FIFO makes the newest upload jump the oldest."""
    first = await enqueue(org_user, job_type="noop")
    await enqueue(org_user, job_type="noop")

    claimed = await claim_next_job()

    assert claimed is not None
    assert str(claimed.id) == first


# ---------------------------------------------------------------------------
# Outcomes: done, failed, never retried.
# ---------------------------------------------------------------------------
async def test_a_successful_job_is_marked_done(org_user: OrgUser, flow) -> None:
    """The ordinary path: claimed, run, done."""
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.proposals = StatementProposals(proposals=[one_proposal()])
    job_id = await enqueue(org_user, payload={"import_id": import_id})

    assert await run_one_job() is True

    assert (await job_row(job_id))["status"] == "done"


async def test_a_failing_handler_marks_the_job_failed_and_stores_why(
    org_user: OrgUser, flow
) -> None:
    """The exception text is the product's error UX, not a log line.

    Recorded in a **fresh** transaction: the handler's own is already rolled
    back, so writing the failure through it would discard the record and
    leave the job in ``running`` forever, looking alive.
    """
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.raises = RuntimeError("the model refused")
    job_id = await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    row = await job_row(job_id)
    assert row["status"] == "failed"
    assert "the model refused" in row["error"]
    assert "RuntimeError" in row["error"]


async def test_a_failed_job_is_not_retried(org_user: OrgUser, flow) -> None:
    """A retry loop turns one visible failure into a quiet one, forever."""
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.raises = RuntimeError("boom")
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()
    assert await run_one_job() is False, "the failed job must not be picked up again"
    assert len(flow.calls) == 1


async def test_a_failing_categorise_job_marks_its_import(org_user: OrgUser, flow) -> None:
    """A stuck import must never look merely ``parsed``.

    ``GET /imports`` drives the imports screen, and an import left at
    ``parsed`` after its categorisation died shows as healthy while the user
    waits for proposals that are never coming.
    """
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.raises = RuntimeError("boom")
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    async with db() as conn:
        status = await conn.fetchval(
            "select status from imports where id = $1", uuid.UUID(import_id)
        )
    assert status == "categorisation_failed"


async def test_an_unknown_job_type_fails_loudly(org_user: OrgUser) -> None:
    """A worker that silently skips work looks exactly like a crashed one."""
    job_id = await enqueue(org_user, job_type="not-a-handler")

    await run_one_job()

    row = await job_row(job_id)
    assert row["status"] == "failed"
    assert "not-a-handler" in row["error"]


async def test_a_failure_does_not_write_proposals(org_user: OrgUser, flow) -> None:
    """A half-applied categorisation is worse than none: the rollback is real."""
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.raises = RuntimeError("boom")
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    assert [t["status"] for t in await txn_rows(org_user.org_id)] == ["unclassified"]


# ---------------------------------------------------------------------------
# The categorise handler.
# ---------------------------------------------------------------------------
async def test_an_import_with_nothing_unclassified_never_calls_the_flow(
    org_user: OrgUser, flow
) -> None:
    """Step 0: the flow accepts an empty line list and would bill us for it.

    Asserted on the call count, not on the absence of proposals -- an empty
    proposal set is exactly what a pointless call would also produce.
    """
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        import_id=import_id,
        status="confirmed",
        category="rent_income",
    )
    job_id = await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    assert flow.calls == [], "no unclassified lines means no LLM call"
    assert (await job_row(job_id))["status"] == "done"


async def test_proposals_are_written_with_confidence_and_the_job_id(
    org_user: OrgUser, flow
) -> None:
    """Proposals, never confirmations -- no auto-confirm at any confidence."""
    entity_id, import_id = await seed_import(org_user)
    property_id = await seed_property(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.proposals = StatementProposals(
        proposals=[
            one_proposal(
                category=HmrcCategory.REPAIRS_MAINTENANCE,
                confidence=0.93,
                property_id=uuid.UUID(property_id),
            )
        ]
    )
    job_id = await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    txn = (await txn_rows(org_user.org_id))[0]
    assert txn["status"] == "proposed"
    assert txn["hmrc_category"] == "repairs_maintenance"
    assert str(txn["property_id"]) == property_id
    assert txn["confidence"] == Decimal("0.93")
    assert txn["proposed_by"] == job_id


async def test_the_flow_is_given_parser_signed_amounts(org_user: OrgUser, flow) -> None:
    """Money out must arrive negative.

    This is the parser's sign convention, not the export's: an unclassified
    transaction has no category, so the category-aware rule in
    ``export_pack.signed_amount`` cannot apply and direction is all there is.

    Getting it backwards would not fail anything. Every description would
    simply reach the agent with the wrong sign, and it would propose income
    for expenses across the board -- plausibly, and at scale.
    """
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(
        org_user, entity_id=entity_id, import_id=import_id, amount="84.99", direction="out"
    )
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        import_id=import_id,
        amount="1350.00",
        direction="in",
        when="2026-07-02",
    )
    flow.proposals = StatementProposals(proposals=[one_proposal(0), one_proposal(1)])
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    amounts = [line["amount"] for line in flow.calls[0]["lines"]]
    assert [Decimal(a) for a in amounts] == [Decimal("-84.99"), Decimal("1350.00")]


async def test_the_property_list_is_org_scoped(org_user: OrgUser, make_org_user, flow) -> None:
    """Filtered in the query, not checked afterwards.

    The flow validates proposed property ids against exactly the list it was
    handed, so an unfiltered list would let the agent attach another org's
    property to this org's transaction and pass every check downstream.
    """
    stranger = await make_org_user()
    strangers_property = await seed_property(stranger, "999 Elsewhere Road")
    mine = await seed_property(org_user, "106 Sample Cres")
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.proposals = StatementProposals(proposals=[one_proposal()])
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    offered = [p["id"] for p in flow.calls[0]["properties"]]
    assert offered == [mine]
    assert strangers_property not in offered


async def test_few_shot_is_the_fifty_most_recent_confirmed(org_user: OrgUser, flow) -> None:
    """The flow enforces the cap but does not select -- choosing is the worker's job.

    ``CategoriseStatementFlow.validate_inputs`` raises above fifty rather
    than truncating, so sending fifty-two would fail the whole job.
    """
    entity_id, import_id = await seed_import(org_user)
    async with db() as conn:
        await conn.execute(
            "insert into transactions (org_id, entity_id, date, amount, direction, "
            "description, status, hmrc_category) "
            "select $1, $2, date '2026-01-01' + g, 10.00, 'in', 'CONFIRMED ' || g, "
            "'confirmed', 'rent_income' from generate_series(1, 52) g",
            org_user.org_id,
            uuid.UUID(entity_id),
        )
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.proposals = StatementProposals(proposals=[one_proposal()])
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    examples = flow.calls[0]["few_shot"]
    assert len(examples) == 50
    # Most recent first: day 52 is in, days 1 and 2 are the two dropped.
    assert examples[0]["description"] == "CONFIRMED 52"
    descriptions = {e["description"] for e in examples}
    assert "CONFIRMED 1" not in descriptions
    assert "CONFIRMED 2" not in descriptions


async def test_proposals_are_audited_as_agent_actions(org_user: OrgUser, flow) -> None:
    """``actor_type='agent'`` with no ``actor_id``.

    The absence is the fact worth recording: no human decided this, which is
    precisely why the row is ``proposed`` and still needs confirming.
    """
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.proposals = StatementProposals(proposals=[one_proposal()])
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    async with db() as conn:
        row = await conn.fetchrow(
            "select * from audit_log where org_id = $1 and action = 'transaction.proposed'",
            org_user.org_id,
        )
    assert row is not None
    assert row["actor_type"] == "agent"
    assert row["actor_id"] is None
    assert "repairs_maintenance" in row["after"]


# ---------------------------------------------------------------------------
# Tenant isolation -- no authenticated caller, so nothing else bounds these.
# ---------------------------------------------------------------------------
async def test_a_job_cannot_categorise_another_orgs_import(
    org_user: OrgUser, make_org_user, flow
) -> None:
    """The import is looked up by id **and** the claimed job's org."""
    stranger = await make_org_user()
    entity_id, strangers_import = await seed_import(stranger)
    await seed_transaction(stranger, entity_id=entity_id, import_id=strangers_import)
    job_id = await enqueue(org_user, payload={"import_id": strangers_import})

    await run_one_job()

    row = await job_row(job_id)
    assert row["status"] == "failed"
    assert flow.calls == []
    assert [t["status"] for t in await txn_rows(stranger.org_id)] == ["unclassified"]


async def test_only_the_jobs_own_import_is_categorised(org_user: OrgUser, flow) -> None:
    """A second import in the same org must be left alone."""
    entity_id, import_id = await seed_import(org_user)
    _, other_import = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        import_id=other_import,
        description="OTHER IMPORT",
        when="2026-07-05",
    )
    flow.proposals = StatementProposals(proposals=[one_proposal()])
    await enqueue(org_user, payload={"import_id": import_id})

    await run_one_job()

    assert len(flow.calls[0]["lines"]) == 1
    statuses = {t["description"]: t["status"] for t in await txn_rows(org_user.org_id)}
    assert statuses["OTHER IMPORT"] == "unclassified"


# ---------------------------------------------------------------------------
# Startup guard and shutdown.
# ---------------------------------------------------------------------------
def test_the_telemetry_guard_refuses_when_the_variable_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bank data reaches an LLM by design; it must not also reach a tracer.

    Checked **before** anything imports CrewAI. ``crewai/llm.py`` calls
    ``load_dotenv()`` at module scope, so a check made afterwards would pass
    because CrewAI itself had just set the variable from a ``.env`` on disk
    -- proving nothing about the deployed environment.
    """
    monkeypatch.delenv(jobs_module.TELEMETRY_OFF_ENV_VAR, raising=False)

    with pytest.raises(jobs_module.TelemetryNotDisabledError):
        jobs_module.assert_telemetry_disabled()


@pytest.mark.parametrize("value", ["false", "0", "", "TRUE-ish"])
def test_the_telemetry_guard_accepts_only_true(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Anything that is not ``true`` leaves telemetry on, so it is refused."""
    monkeypatch.setenv(jobs_module.TELEMETRY_OFF_ENV_VAR, value)

    with pytest.raises(jobs_module.TelemetryNotDisabledError):
        jobs_module.assert_telemetry_disabled()


def test_the_telemetry_guard_passes_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Case-insensitive, whitespace-tolerant -- env files are hand-edited."""
    monkeypatch.setenv(jobs_module.TELEMETRY_OFF_ENV_VAR, " True ")

    jobs_module.assert_telemetry_disabled()


async def test_the_loop_stops_when_asked(org_user: OrgUser, flow) -> None:
    """SIGTERM finishes the current job, then exits.

    Abandoning a job mid-write would leave it ``running`` forever -- the same
    failure ``mark_failed``'s separate transaction exists to prevent.
    """
    entity_id, import_id = await seed_import(org_user)
    await seed_transaction(org_user, entity_id=entity_id, import_id=import_id)
    flow.proposals = StatementProposals(proposals=[one_proposal()])
    job_id = await enqueue(org_user, payload={"import_id": import_id})

    stop = asyncio.Event()

    async def ask_to_stop() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(poll_forever(stop, poll_interval=0.01), ask_to_stop()), timeout=10
    )

    assert (await job_row(job_id))["status"] == "done"


async def test_an_empty_queue_does_not_spin(org_user: OrgUser) -> None:
    """With nothing to do the loop waits on the stop event, not on a busy sleep."""
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(poll_forever(stop, poll_interval=30), timeout=5)
