"""End-to-end smoke: a CSV goes in one end and a filed quarter comes out the other.

Runs against the live local Supabase stack::

    uv run --env-file ../.env pytest tests/e2e/

**Only the LLM call is stubbed.** Everything else is the real thing: the real
HTTP app, the real Supabase Storage upload, the real ``core.parser``, the real
:class:`~src.flows.categorise.CategoriseStatementFlow` (its input validation,
its prompt, and its output contract check all run), the real poller loop, the
real WeasyPrint render, and the real signed-URL download. The stub replaces
``Agent`` inside the flow, so ``_build_llm`` still constructs an LLM from
``CATEGORISER_MODEL`` -- what is skipped is the network call and nothing else.

This is deliberately *not* how ``tests/worker/test_poller.py`` stubs. That
suite replaces the whole flow, because its subject is the worker and it needs
to see the inputs the worker built. Here the flow is part of what is under
test, so the seam is one level deeper.

**The stub reads the prompt rather than answering positionally.** It maps
description to category and takes the property id out of the prompt's property
block, which means two things a positional list would not: the test cannot
silently mis-attach a proposal if row ordering ever changes, and
:func:`~src.flows.categorise._build_prompt` is proved to actually carry the
lines and the property list.

The figures
-----------
``tests/fixtures/statements/e2e_smoke.csv`` holds six lines, all inside the
Q2 2026-27 year-to-date window (6 Apr 2026 - 5 Oct 2026). The property is
owned 60/40, and each amount is chosen to exercise something that could
silently break:

======================  =========  ======================================
line                    amount     what it pins
======================  =========  ======================================
rent received           1350.01    a leftover penny going to the 60% owner
B&Q bathroom tap        -84.99     a leftover penny going to the 40% owner
plumbing refund         25.00      money *in* against an *expense*, which
                                   must reduce the expense, not add income
Spotify                 -9.99      ``personal_non_business``, dropped from
                                   the export entirely
council tax             -134.50    excluded by the user, so never counted
                                   even though it has a category and a
                                   property
accountant              -200.00    no property, so it falls back to the
                                   import's entity and reaches one owner
======================  =========  ======================================

**Adding a line to that fixture is not free.**
:func:`~src.core.splits.split_amount` breaks a remainder tie on ascending
owner UUID, and the entity ids here are random per run -- so any amount whose
two owners have *equal* fractional remainders **and** a penny left to hand out
would make this test pass about half the time. Every amount above either has
unequal remainders or nothing left over. A "harmless" round ``-100.00`` at
60/40 is fine (nothing left over); an amount like ``-0.05`` is not.

Hand-computed expectations, for 60/40:

* rent 1350.01 -> 810.01 / 540.00 (floors 810.00 + 540.00, the odd penny to
  the larger remainder, which is the 60% owner's 0.006)
* repairs 84.99 -> 50.99 / 34.00 (the odd penny to the 40% owner's 0.006)
* refund -25.00 -> -15.00 / -10.00 (exact, nothing to hand out)
* repairs net -> 35.99 / 24.00, and 35.99 + 24.00 == 84.99 - 25.00
* rent -> 810.01 + 540.00 == 1350.01
* accountant 200.00 -> 200.00 / nothing

:seealso: backend/src/core/splits.py (the apportionment);
    backend/src/core/export_pack.py (the sign rule and the refusal rules).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest

from src.core.categories import HmrcCategory
from src.core.export_pack import CATEGORY_COLUMNS
from src.flows import categorise as categorise_module
from src.flows.categorise import LineProposal, StatementProposals
from src.worker.main import poll_forever
from tests.api.conftest import OrgUser, as_user, db

FIXTURE = Path(__file__).parent.parent / "fixtures" / "statements" / "e2e_smoke.csv"

#: What the stubbed agent proposes for each statement line, keyed by the
#: description the prompt carries: ``(category, attach the property?,
#: confidence)``. Confidences vary because the review screen sorts by them.
_PROPOSALS: dict[str, tuple[HmrcCategory, bool, float]] = {
    "RENT RECEIVED 106 SAMPLE CRES": (HmrcCategory.RENT_INCOME, True, 0.96),
    "B&Q LUTON BATHROOM TAP": (HmrcCategory.REPAIRS_MAINTENANCE, True, 0.93),
    "SAMPLE PLUMBING REFUND": (HmrcCategory.REPAIRS_MAINTENANCE, True, 0.71),
    "SPOTIFY UK": (HmrcCategory.PERSONAL_NON_BUSINESS, False, 0.99),
    "LUTON BC COUNCIL TAX": (HmrcCategory.RATES_INSURANCE_GROUND, True, 0.62),
    "SAMPLE ACCOUNTANTS LTD": (HmrcCategory.LEGAL_PROFESSIONAL, False, 0.88),
}

#: Matches one numbered statement line in the prompt `_build_prompt` builds.
_LINE_RE = re.compile(r'^(\d+)\. date=(\S+) description="(.*)" amount=(\S+)$', re.MULTILINE)

#: Matches one entry of the prompt's "properties on file" block.
_PROPERTY_RE = re.compile(r"^- ([0-9a-fA-F-]{36}): (.+)$", re.MULTILINE)


class StubAgent:
    """Stands in for ``crewai.Agent``, answering from the prompt it is given.

    The one seam where the network would be. It still receives the real
    prompt and the real ``LLM`` object, and it still has to satisfy
    :func:`~src.flows.categorise._validate_proposals` -- one proposal per
    line, indices in range, and a property id drawn from the list offered.
    """

    #: Every prompt this stub was called with, newest last.
    prompts: ClassVar[list[str]] = []

    def __init__(self, *, config: dict, llm: object) -> None:
        self.config = config
        self.llm = llm

    def kickoff(self, prompt: str, response_format: type | None = None) -> SimpleNamespace:
        """Answer the prompt from :data:`_PROPOSALS`.

        :param prompt: the prompt the flow built.
        :param response_format: the structured-output contract the flow asks
            for; asserted rather than ignored, because a flow that stopped
            requesting one would still pass a stub that did not look.
        :returns: an object shaped like a CrewAI result, with ``.pydantic``.
        """
        StubAgent.prompts.append(prompt)
        assert response_format is StatementProposals

        offered = _PROPERTY_RE.findall(prompt)
        assert len(offered) == 1, f"expected one property in the prompt, got {offered}"
        property_id = uuid.UUID(offered[0][0])

        proposals = []
        for index, _date, description, _amount in _LINE_RE.findall(prompt):
            category, attach_property, confidence = _PROPOSALS[description]
            proposals.append(
                LineProposal(
                    line_index=int(index),
                    hmrc_category=category,
                    property_id=property_id if attach_property else None,
                    confidence=confidence,
                    rationale="stubbed",
                )
            )
        return SimpleNamespace(pydantic=StatementProposals(proposals=proposals))


@pytest.fixture
def stub_agent(monkeypatch: pytest.MonkeyPatch) -> type[StubAgent]:
    """Replace the flow's ``Agent`` and configure a model id.

    ``CATEGORISER_MODEL`` is set here rather than in ``.env`` because it is
    the one thing the flow needs that a developer machine has no business
    guessing -- see :class:`~src.flows.categorise.CategoriserConfigError`.
    Setting it is configuration, not mocking: ``_build_llm`` still runs and
    still builds a real ``LLM``.
    """
    StubAgent.prompts = []
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/e2e-stub")
    monkeypatch.setattr(categorise_module, "Agent", StubAgent)
    return StubAgent


# ---------------------------------------------------------------------------
# Helpers: reading state the API does not expose, and running the poller.
# ---------------------------------------------------------------------------
async def _terminal_job(org_id: uuid.UUID) -> dict | None:
    """Return the org's job once it has finished, or ``None`` while it runs."""
    async with db() as conn:
        row = await conn.fetchrow(
            "select * from job_queue where org_id = $1 and status in ('done', 'failed')",
            org_id,
        )
    return dict(row) if row else None


async def _run_worker_until_finished(org_id: uuid.UUID) -> dict:
    """Run the real poller loop until the org's job reaches a terminal state.

    Waits on ``done`` **or** ``failed`` rather than on proposals appearing.
    Waiting for the happy state would turn every handler failure into a bare
    timeout, with the reason sitting unread in ``job_queue.error`` -- so the
    caller gets the row and can put that text in its assertion message.

    :param org_id: the org whose job to wait for.
    :returns: the finished ``job_queue`` row.
    """
    stop = asyncio.Event()

    async def wait_for_finish() -> dict:
        while True:
            row = await _terminal_job(org_id)
            if row is not None:
                stop.set()
                return row
            await asyncio.sleep(0.05)

    _, row = await asyncio.wait_for(
        asyncio.gather(poll_forever(stop, poll_interval=0.05), wait_for_finish()),
        timeout=120,
    )
    return row


async def _fetch(url: str) -> httpx.Response:
    """Fetch a signed URL the way a browser would -- no credentials at all."""
    async with httpx.AsyncClient() as client:
        return await client.get(url)


async def _download(org_user: OrgUser, documents: list[dict], kind: str) -> httpx.Response:
    """Mint a signed URL for one generated document and fetch its bytes.

    :param org_user: the caller.
    :param documents: the export's document refs.
    :param kind: which one to fetch.
    :returns: the raw storage response.
    """
    document = next(d for d in documents if d["kind"] == kind)
    minted = await as_user(org_user, "GET", f"/documents/{document['id']}/download")
    assert minted.status_code == 200, minted.text
    return await _fetch(minted.json()["url"])


async def _mtd_quarter_row(entity_id: str) -> dict:
    """Read the filed quarter straight from the database."""
    async with db() as conn:
        return dict(
            await conn.fetchrow(
                "select * from mtd_quarters where entity_id = $1", uuid.UUID(entity_id)
            )
        )


# ---------------------------------------------------------------------------
# The walk.
# ---------------------------------------------------------------------------
async def test_a_statement_becomes_a_filed_quarter(org_user: OrgUser, stub_agent) -> None:
    """Upload, categorise, review, export -- and check the pennies that come back.

    One test rather than several because the subject *is* the pipeline: what
    this is here to catch is two correct components disagreeing at their
    join, and a split-up version would stub exactly those joins away.
    """
    # -- Portfolio ----------------------------------------------------------
    mahmud = await as_user(
        org_user, "POST", "/entities", {"name": "Mahmud", "tax_regime": "mtd_itsa"}
    )
    alima = await as_user(
        org_user, "POST", "/entities", {"name": "Alima", "tax_regime": "mtd_itsa"}
    )
    assert (mahmud.status_code, alima.status_code) == (201, 201), (mahmud.text, alima.text)
    mahmud_id = mahmud.json()["id"]
    alima_id = alima.json()["id"]

    created_property = await as_user(
        org_user,
        "POST",
        "/properties",
        {
            "address_line1": "106 Sample Cres",
            "city": "Luton",
            "postcode": "LU1 1AA",
            "finance_cost_classification": "residential",
        },
    )
    assert created_property.status_code == 201, created_property.text
    property_id = created_property.json()["id"]

    ownership = await as_user(
        org_user,
        "PUT",
        f"/properties/{property_id}/ownership",
        [
            {"entity_id": mahmud_id, "percentage": "60.00"},
            {"entity_id": alima_id, "percentage": "40.00"},
        ],
    )
    assert ownership.status_code == 200, ownership.text

    # -- Upload -------------------------------------------------------------
    created_import = await as_user(
        org_user,
        "POST",
        "/imports",
        files={"file": ("e2e_smoke.csv", FIXTURE.read_bytes(), "text/csv")},
        data={"entity_id": mahmud_id, "source_bank": "generic"},
    )
    assert created_import.status_code == 201, created_import.text
    assert created_import.json()["status"] == "parsed", created_import.json()["error_detail"]
    import_id = created_import.json()["id"]

    listed = await as_user(org_user, "GET", "/transactions", params={"import_id": import_id})
    assert listed.status_code == 200, listed.text
    assert [t["status"] for t in listed.json()] == ["unclassified"] * 6

    # -- Categorise (the poller, the flow, the stubbed model) ---------------
    job = await _run_worker_until_finished(org_user.org_id)
    assert job["status"] == "done", f"categorise job failed: {job['error']}"

    # The prompt really carried the lines, with the parser's sign convention:
    # money out arrives negative, so the agent is not told a repair is income.
    amounts = {
        description: amount
        for _index, _date, description, amount in _LINE_RE.findall(stub_agent.prompts[0])
    }
    assert amounts["B&Q LUTON BATHROOM TAP"] == "-84.99"
    assert amounts["SAMPLE PLUMBING REFUND"] == "25.00"

    proposed = await as_user(
        org_user, "GET", "/transactions", params={"import_id": import_id, "status": "proposed"}
    )
    assert proposed.status_code == 200, proposed.text
    by_description = {t["description"]: t for t in proposed.json()}
    assert len(by_description) == 6
    assert by_description["SPOTIFY UK"]["hmrc_category"] == "personal_non_business"
    assert by_description["B&Q LUTON BATHROOM TAP"]["property_id"] == property_id

    # -- Refusing to export is a feature ------------------------------------
    # Every line is `proposed`: an agent's suggestion nobody accepted is not a
    # decision, and exporting around one would understate a return silently.
    premature = await as_user(
        org_user,
        "POST",
        "/exports/quarter",
        {"entity_id": mahmud_id, "tax_year": 2026, "quarter": 2},
    )
    assert premature.status_code == 422, premature.text
    assert by_description["SPOTIFY UK"]["id"] in premature.json()["detail"]

    # -- Review -------------------------------------------------------------
    excluded = await as_user(
        org_user,
        "POST",
        f"/transactions/{by_description['LUTON BC COUNCIL TAX']['id']}/exclude",
    )
    assert excluded.status_code == 200, excluded.text

    confirmed = await as_user(
        org_user,
        "POST",
        "/transactions/confirm-batch",
        {
            "items": [
                {
                    "transaction_id": txn["id"],
                    "hmrc_category": txn["hmrc_category"],
                    # Sent explicitly: `_apply_confirm` assigns `property_id`
                    # unconditionally, so omitting it would wipe the proposed
                    # attribution and quietly move money to the wrong owner.
                    "property_id": txn["property_id"],
                }
                for description, txn in by_description.items()
                if description != "LUTON BC COUNCIL TAX"
            ]
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert {t["status"] for t in confirmed.json()} == {"confirmed"}

    # -- Export, and read the figures back out through the download path ----
    export = await as_user(
        org_user,
        "POST",
        "/exports/quarter",
        {"entity_id": mahmud_id, "tax_year": 2026, "quarter": 2},
    )
    assert export.status_code == 201, export.text
    assert export.json()["tax_year"] == "2026-27"
    assert export.json()["quarter"] == "Q2"
    assert export.json()["version"] == 1
    documents = export.json()["documents"]
    assert {d["kind"] for d in documents} == {
        "export_category_csv",
        "export_property_csv",
        "export_pdf",
    }

    categories_csv = await _download(org_user, documents, "export_category_csv")
    assert categories_csv.status_code == 200, categories_csv.text
    assert categories_csv.text == (
        "hmrc_category,cumulative_total\n"
        "legal_professional,200.00\n"
        "rent_income,810.01\n"
        "repairs_maintenance,35.99\n"
    )

    properties_csv = await _download(org_user, documents, "export_property_csv")
    assert properties_csv.status_code == 200, properties_csv.text
    assert properties_csv.text == (
        "property_id,hmrc_category,cumulative_total\n"
        f"{property_id},rent_income,810.01\n"
        f"{property_id},repairs_maintenance,35.99\n"
    )

    pdf = await _download(org_user, documents, "export_pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-"), "the pack must be a real rendered PDF"

    # -- The other owner's share, and the two summing back to the whole -----
    co_owner = await as_user(
        org_user,
        "POST",
        "/exports/quarter",
        {"entity_id": alima_id, "tax_year": 2026, "quarter": 2},
    )
    assert co_owner.status_code == 201, co_owner.text

    co_owner_csv = await _download(org_user, co_owner.json()["documents"], "export_category_csv")
    assert co_owner_csv.text == (
        "hmrc_category,cumulative_total\n"
        "rent_income,540.00\n"
        "repairs_maintenance,24.00\n"
    )

    # The point of a penny-exact split: nothing is created and nothing is
    # lost between the two owners. Written as the sum rather than restated
    # from the numbers above, because that is the invariant.
    assert Decimal("810.01") + Decimal("540.00") == Decimal("1350.01")
    assert Decimal("35.99") + Decimal("24.00") == Decimal("84.99") - Decimal("25.00")

    # -- What was filed -----------------------------------------------------
    filed = await _mtd_quarter_row(mahmud_id)
    assert filed["tax_year"] == "2026-27"
    assert filed["quarter"] == "Q2"
    assert filed["version"] == 1
    assert filed["export_status"] == "generated"

    stored = {column: filed[column] for column in CATEGORY_COLUMNS.values()}
    assert {column: value for column, value in stored.items() if value} == {
        "legal_professional_total": Decimal("200.00"),
        "rent_income_total": Decimal("810.01"),
        "repairs_maintenance_total": Decimal("35.99"),
    }
    # Present and zero, not absent: an mtd_quarters row is a complete
    # statement of what was filed, so "nothing was spent" has to be
    # distinguishable from "this was left out".
    assert stored["capital_expense_total"] == Decimal("0.00")
