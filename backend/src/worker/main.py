"""The job poller: claim one job, run it, record what happened, repeat.

**Three transactions per job, and the split is the design, not an accident.**

1. *Claim* -- ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED
   LIMIT 1)``, committed **immediately**. Committing here is what makes
   ``SKIP LOCKED`` mean anything: hold the claim's transaction open for the
   duration of the job and the row stays locked the whole time, so a second
   worker skips it not because it is claimed but because it is *locked*, and
   a crash mid-job leaves it locked until the connection dies.
2. *Work* -- the handler's own transaction, committed on success.
3. *Failure* -- a **third**, fresh transaction. It has to be: transaction 2
   is already rolled back and poisoned by the exception, so marking the job
   failed inside it would itself be discarded and the job would sit in
   ``running`` forever, looking alive.

**No retries, anywhere.** A job that failed for a real reason fails again,
and a retry loop converts one visible failure into a quiet one repeated
forever. ``status='failed'`` plus the exception text is the product's error
UX -- and for ``categorise`` the failure is pushed onto the import too, so
``GET /imports`` shows ``categorisation_failed`` rather than a stuck import
that looks merely ``pending``.

**Telemetry is checked before CrewAI is imported anywhere.** See
:func:`~src.worker.jobs.assert_telemetry_disabled` -- CrewAI's own import
runs ``load_dotenv()``, which would make the check pass by its own side
effect.

Run it::

    cd backend && uv run --env-file ../.env python -m src.worker.main

:seealso: backend/src/worker/jobs.py (the handlers).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import UTC

from sqlalchemy import func, select, update

from src.db.models import Import, JobQueue
from src.db.session import worker_session_factory
from src.worker.jobs import (
    FAILURE_IMPORT_STATUS,
    HANDLERS,
    assert_telemetry_disabled,
)

logger = logging.getLogger(__name__)

#: Seconds to wait before looking again, when the queue was empty. Not a
#: latency budget -- a job enqueued the instant after a poll waits this long
#: -- but two seconds is well inside "feels immediate" for an upload that
#: has just been parsed.
POLL_INTERVAL_SECONDS = 2.0

#: Maximum time a single job handler may run before being killed.
#: The categorise handler makes a third-party LLM call that can hang
#: indefinitely; without a timeout, one stuck job deadlocks the entire
#: worker for all tenants. 5 minutes is generous for a batch categorise.
HANDLER_TIMEOUT_SECONDS = 300

#: A job stuck in ``running`` longer than this is assumed to be from a
#: crashed worker and is reset to ``queued`` by the reaper. Must exceed
#: HANDLER_TIMEOUT_SECONDS so a legitimately-running job is never reaped.
STALE_JOB_THRESHOLD_SECONDS = 600


async def reap_stale_jobs() -> int:
    """Reset jobs stuck in ``running`` longer than the stale threshold.

    If a worker crashes after claiming a job (``status='running'``), the
    job stays running forever with no heartbeat to revive it. This reaper
    runs on every poll cycle and resets stale jobs back to ``queued``
    so a live worker picks them up.

    :returns: number of jobs reset to ``queued``.
    """
    from datetime import datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_JOB_THRESHOLD_SECONDS)
    async with worker_session_factory() as session:
        result = await session.execute(
            update(JobQueue)
            .where(JobQueue.status == "running", JobQueue.updated_at < cutoff)
            .values(status="queued", error=None)
            .returning(JobQueue.id)
            .execution_options(synchronize_session=False)
        )
        rows = result.scalars().all()
        if rows:
            logger.warning("reaped %d stale job(s): %s", len(rows), rows)
        await session.commit()
        return len(rows)


async def claim_next_job() -> JobQueue | None:
    """Atomically claim the oldest queued job, or return ``None``.

    ``FOR UPDATE SKIP LOCKED`` inside the subquery is what lets several
    workers share one queue: a row another worker is claiming is skipped
    rather than waited for. Without it, concurrent workers serialise on the
    same row and both end up running the same job.

    The claim is committed before returning, so the row is ``running`` and
    unlocked by the time the handler starts.

    :returns: the claimed job, detached from its session, or ``None`` if the
        queue was empty.
    """
    async with worker_session_factory() as session:
        oldest_queued = (
            select(JobQueue.id)
            .where(JobQueue.status == "queued")
            .order_by(JobQueue.created_at, JobQueue.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        claimed = await session.scalar(
            update(JobQueue)
            .where(JobQueue.id == oldest_queued)
            .values(status="running", updated_at=func.now())
            .returning(JobQueue)
            .execution_options(synchronize_session=False)
        )
        if claimed is None:
            return None
        # Detached deliberately: the handler gets its own session, and an
        # object still attached to this one would lazy-load across a closed
        # transaction.
        session.expunge(claimed)
        await session.commit()
        return claimed


async def mark_failed(job: JobQueue, error: str) -> None:
    """Record a job's failure in a **fresh** transaction.

    The handler's transaction is already rolled back when this runs, so
    writing through it would discard the very record that says why. A job
    left in ``running`` because its failure was itself rolled back is the
    worst outcome available: it looks alive forever.

    :param job: the failed job.
    :param error: the exception text to store, for the imports screen.
    """
    async with worker_session_factory() as session:
        await session.execute(
            update(JobQueue).where(JobQueue.id == job.id).values(status="failed", error=error)
        )
        failure_status = FAILURE_IMPORT_STATUS.get(job.job_type)
        import_id = job.payload.get("import_id") if job.payload else None
        if failure_status is not None and import_id is not None:
            # Org-filtered like everything else the worker writes, even
            # though the id came from a job row we already trust: one idiom,
            # no exceptions to notice.
            await session.execute(
                update(Import)
                .where(Import.id == import_id, Import.org_id == job.org_id)
                .values(status=failure_status)
            )
        await session.commit()


async def mark_done(job: JobQueue) -> None:
    """Mark a job ``done``.

    :param job: the job that completed.
    """
    async with worker_session_factory() as session:
        await session.execute(
            update(JobQueue).where(JobQueue.id == job.id).values(status="done")
        )
        await session.commit()


async def run_one_job() -> bool:
    """Claim and run at most one job.

    :returns: ``True`` if a job was claimed (whatever its outcome), ``False``
        if the queue was empty -- which is what tells the loop to sleep.
    """
    job = await claim_next_job()
    if job is None:
        return False

    handler = HANDLERS.get(job.job_type)
    try:
        if handler is None:
            # An unhandled type is a deployment mismatch, not a no-op. A
            # worker that silently skips work it does not understand is
            # indistinguishable from one that has crashed.
            raise LookupError(
                f"no handler registered for job type {job.job_type!r}; "
                f"known: {', '.join(sorted(HANDLERS))}"
            )
        async with worker_session_factory() as session:
            await asyncio.wait_for(
                handler(session, job),
                timeout=HANDLER_TIMEOUT_SECONDS,
            )
            await session.commit()
    except TimeoutError:
        logger.error("job %s (%s) timed out after %ds", job.id, job.job_type, HANDLER_TIMEOUT_SECONDS)
        await mark_failed(
            job,
            f"TimeoutError: handler exceeded {HANDLER_TIMEOUT_SECONDS}s "
            f"(possible LLM hang or unresponsive provider)",
        )
    except Exception as exc:
        # The one deliberately broad except in this repo. A handler runs
        # arbitrary work including a third-party LLM call, and the whole
        # contract of the queue is that *any* failure is recorded and
        # visible rather than killing the worker. Narrowing it would mean
        # enumerating every exception CrewAI, httpx and asyncpg can raise,
        # and the ones missed would take the process down silently.
        logger.exception("job %s (%s) failed", job.id, job.job_type)
        await mark_failed(job, f"{type(exc).__name__}: {exc}")
    else:
        await mark_done(job)
    return True


async def poll_forever(
    stop: asyncio.Event, *, poll_interval: float = POLL_INTERVAL_SECONDS
) -> None:
    """Run jobs until ``stop`` is set.

    Between jobs, not during one: a job in flight runs to completion and
    records its outcome, because a SIGTERM that abandoned it mid-write would
    leave it ``running`` forever -- the same failure ``mark_failed``'s
    separate transaction exists to prevent.

    When the queue is empty the sleep is interruptible, so shutdown is
    immediate rather than up to ``poll_interval`` late.

    :param stop: set to ask the loop to finish after the current job.
    :param poll_interval: seconds to wait when the queue was empty.
    """
    while not stop.is_set():
        await reap_stale_jobs()
        worked = await run_one_job()
        if worked:
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)


async def amain() -> None:
    """Set up signal handling and run the loop until asked to stop."""
    # Before anything imports CrewAI. `src.worker.jobs` imports the flow
    # lazily inside its handler for exactly this reason.
    assert_telemetry_disabled()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("worker started; polling every %.1fs", POLL_INTERVAL_SECONDS)
    await poll_forever(stop)
    logger.info("worker stopped")


def main() -> None:
    """Console entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(amain())


if __name__ == "__main__":
    main()
