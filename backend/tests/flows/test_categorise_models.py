"""Tests for the categorisation flow's Pydantic contract and flow logic.

Pydantic tests cover the response-format contract the LLM is retried
against (:class:`StatementProposals`). Flow tests mock ``Agent.kickoff`` --
no live LLM calls happen anywhere in this file.

:seealso: ``backend/src/flows/categorise.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.core.categories import HmrcCategory
from src.core.parser import ParsedLine
from src.flows import categorise
from src.flows.categorise import (
    CategorisationResultError,
    CategoriserConfigError,
    CategoriseStatementFlow,
    ConfirmedExample,
    LineProposal,
    OrgProperty,
    StatementLineInput,
    StatementProposals,
)

PROPERTY_1 = UUID("00000000-0000-0000-0000-000000000001")
UNKNOWN_PROPERTY = UUID("00000000-0000-0000-0000-0000000000ff")


def _proposal(
    index: int,
    category: HmrcCategory = HmrcCategory.REPAIRS_MAINTENANCE,
    confidence: float = 0.9,
    property_id: UUID | None = PROPERTY_1,
) -> LineProposal:
    return LineProposal(
        line_index=index,
        hmrc_category=category,
        property_id=property_id,
        confidence=confidence,
        rationale="test rationale",
    )


class _FakeAgentResult:
    """Duck-types the ``.raw`` attribute of ``LiteAgentOutput``.

    ``.raw`` rather than ``.pydantic``: the flow no longer asks the SDK to
    parse, so the text is what it actually reads. Stubbing the text also
    means these tests exercise the parsing the flow now owns, instead of
    handing it an object that could never have been malformed.
    """

    def __init__(self, raw: Any) -> None:
        self.raw = raw


# ---------------------------------------------------------------------------
# Step 1: Pydantic contract tests
# ---------------------------------------------------------------------------


def test_line_proposal_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        LineProposal(
            line_index=0,
            hmrc_category=HmrcCategory.REPAIRS_MAINTENANCE,
            property_id=None,
            confidence=1.1,
            rationale="x",
        )


def test_line_proposal_rejects_confidence_below_zero() -> None:
    with pytest.raises(ValidationError):
        LineProposal(
            line_index=0,
            hmrc_category=HmrcCategory.REPAIRS_MAINTENANCE,
            property_id=None,
            confidence=-0.01,
            rationale="x",
        )


def test_line_proposal_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        LineProposal(
            line_index=0,
            hmrc_category="not_a_real_hmrc_category",
            property_id=None,
            confidence=0.5,
            rationale="x",
        )


def test_line_proposal_requires_rationale() -> None:
    with pytest.raises(ValidationError):
        LineProposal(
            line_index=0,
            hmrc_category=HmrcCategory.REPAIRS_MAINTENANCE,
            property_id=None,
            confidence=0.5,
        )  # type: ignore[call-arg]


def test_statement_proposals_accepts_valid_list() -> None:
    proposals = StatementProposals(proposals=[_proposal(0), _proposal(1)])
    assert len(proposals.proposals) == 2
    assert proposals.proposals[0].hmrc_category == HmrcCategory.REPAIRS_MAINTENANCE


def test_statement_proposals_rejects_raw_dict_with_unknown_category() -> None:
    # Simulates the LLM's parsed JSON output arriving as raw dicts through
    # the top-level container -- the actual shape CrewAI validates against.
    with pytest.raises(ValidationError):
        StatementProposals(
            proposals=[
                {
                    "line_index": 0,
                    "hmrc_category": "nonsense",
                    "property_id": None,
                    "confidence": 0.5,
                    "rationale": "x",
                }
            ]
        )


def test_statement_proposals_rejects_raw_dict_with_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        StatementProposals(
            proposals=[
                {
                    "line_index": 0,
                    "hmrc_category": "repairs_maintenance",
                    "property_id": None,
                    "confidence": 1.5,
                    "rationale": "x",
                }
            ]
        )


def test_statement_line_input_from_parsed_line_round_trips_fields() -> None:
    parsed = ParsedLine(
        date=date(2026, 7, 1), description="B&Q LUTON", amount=Decimal("-84.99")
    )
    line = StatementLineInput.from_parsed_line(parsed)
    assert line.date == parsed.date
    assert line.description == parsed.description
    assert line.amount == parsed.amount


# ---------------------------------------------------------------------------
# Step 3: Flow logic tests -- Agent.kickoff mocked, no live LLM calls
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _categoriser_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every flow test gets a deterministic, offline-safe model id by default.

    Individual tests override this by deleting the env var again.
    """
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)


def _mock_kickoff(
    monkeypatch: pytest.MonkeyPatch,
    proposals: Any,
    captured: dict[str, Any] | None = None,
) -> None:
    """Stub the agent to answer with ``proposals`` serialised as model text.

    Takes a :class:`StatementProposals` (or ``None`` for "the model said
    nothing usable") and renders it the way a real answer arrives -- as JSON
    text on ``.raw``. The tests below were written against an object handed
    straight to the flow; going through the serialisation keeps them
    honest now that the flow parses.
    """

    def fake_kickoff(self: Any, messages: Any, **kwargs: Any) -> Any:
        assert "response_format" not in kwargs
        if captured is not None:
            captured["prompt"] = messages
        raw = "" if proposals is None else proposals.model_dump_json()
        return _FakeAgentResult(raw=raw)

    monkeypatch.setattr(categorise.Agent, "kickoff", fake_kickoff)


def test_flow_maps_proposals_onto_line_indices_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposals = StatementProposals(
        proposals=[
            _proposal(0, HmrcCategory.REPAIRS_MAINTENANCE, 0.95, PROPERTY_1),
            _proposal(1, HmrcCategory.PERSONAL_NON_BUSINESS, 0.2, None),
        ]
    )
    _mock_kickoff(monkeypatch, proposals)

    flow = CategoriseStatementFlow()
    flow.kickoff(
        inputs={
            "lines": [
                {"date": "2026-07-01", "description": "B&Q LUTON", "amount": "-84.99"},
                {"date": "2026-07-02", "description": "TESCO", "amount": "-12.50"},
            ],
            "properties": [{"id": str(PROPERTY_1), "label": "18 Sample Avenue"}],
        }
    )

    assert flow.state.proposals is not None
    assert flow.state.proposals.proposals[0].line_index == 0
    assert (
        flow.state.proposals.proposals[0].hmrc_category
        == HmrcCategory.REPAIRS_MAINTENANCE
    )
    assert (
        flow.state.proposals.proposals[1].hmrc_category
        == HmrcCategory.PERSONAL_NON_BUSINESS
    )
    assert flow.state.proposals.proposals[1].property_id is None


def test_flow_raises_on_out_of_range_line_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2 lines, 2 proposals (count matches) but one index is out of range --
    # isolates the range check from the count check.
    proposals = StatementProposals(proposals=[_proposal(0), _proposal(5)])
    _mock_kickoff(monkeypatch, proposals)

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="out of range"):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"},
                    {"date": "2026-07-02", "description": "B", "amount": "-2.00"},
                ],
                "properties": [{"id": str(PROPERTY_1), "label": "P1"}],
            }
        )


def test_flow_raises_on_duplicate_line_index(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2 lines, 2 proposals (count matches), both in range, but duplicated --
    # isolates the uniqueness check.
    proposals = StatementProposals(proposals=[_proposal(0), _proposal(0)])
    _mock_kickoff(monkeypatch, proposals)

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="duplicate"):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"},
                    {"date": "2026-07-02", "description": "B", "amount": "-2.00"},
                ],
                "properties": [{"id": str(PROPERTY_1), "label": "P1"}],
            }
        )


def test_flow_raises_on_unknown_property_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2 lines, 2 proposals, unique + in-range indices, but a property_id
    # that was never offered in the property list -- isolates that check.
    proposals = StatementProposals(
        proposals=[
            _proposal(0, property_id=PROPERTY_1),
            _proposal(1, property_id=UNKNOWN_PROPERTY),
        ]
    )
    _mock_kickoff(monkeypatch, proposals)

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="property"):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"},
                    {"date": "2026-07-02", "description": "B", "amount": "-2.00"},
                ],
                "properties": [{"id": str(PROPERTY_1), "label": "P1"}],
            }
        )


def test_flow_raises_on_proposal_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2 lines but only 1 proposal returned -- isolates the count check
    # (index 0 alone is in-range and unique).
    proposals = StatementProposals(proposals=[_proposal(0)])
    _mock_kickoff(monkeypatch, proposals)

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="expected 2 proposals"):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"},
                    {"date": "2026-07-02", "description": "B", "amount": "-2.00"},
                ],
                "properties": [{"id": str(PROPERTY_1), "label": "P1"}],
            }
        )


def test_flow_raises_when_categoriser_model_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATEGORISER_MODEL", raising=False)

    flow = CategoriseStatementFlow()
    with pytest.raises(CategoriserConfigError):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"}
                ],
            }
        )


def test_flow_raises_when_few_shot_exceeds_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    too_many = [
        {
            "description": f"line {i}",
            "amount": "-1.00",
            "hmrc_category": HmrcCategory.REPAIRS_MAINTENANCE.value,
        }
        for i in range(51)
    ]

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="few_shot"):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"}
                ],
                "few_shot": too_many,
            }
        )


def test_flow_prompt_contains_numbered_lines_properties_and_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposals = StatementProposals(proposals=[_proposal(0), _proposal(1)])
    captured: dict[str, Any] = {}
    _mock_kickoff(monkeypatch, proposals, captured)

    flow = CategoriseStatementFlow()
    flow.kickoff(
        inputs={
            "lines": [
                {"date": "2026-07-01", "description": "B&Q LUTON", "amount": "-84.99"},
                {"date": "2026-07-02", "description": "TESCO", "amount": "-12.50"},
            ],
            "properties": [{"id": str(PROPERTY_1), "label": "18 Sample Avenue"}],
            "few_shot": [
                {
                    "description": "SCREWFIX",
                    "amount": "-30.00",
                    "hmrc_category": HmrcCategory.REPAIRS_MAINTENANCE.value,
                    "property_id": str(PROPERTY_1),
                }
            ],
        }
    )

    prompt = captured["prompt"]
    # Numbered lines with index, description, amount.
    assert "0. " in prompt and "B&Q LUTON" in prompt and "-84.99" in prompt
    assert "1. " in prompt and "TESCO" in prompt and "-12.50" in prompt
    # Property list with ids.
    assert str(PROPERTY_1) in prompt
    # Few-shot examples section.
    assert "SCREWFIX" in prompt
    # Explicit instruction set from the plan.
    assert "personal_non_business" in prompt
    assert "LOW" in prompt and "confidence" in prompt
    assert "never invent one" in prompt


# `test_flow_raises_when_agent_does_not_return_structured_output` stood here.
# It asserted that a `None` on `result.pydantic` raised -- a state that can no
# longer occur, because the flow stopped asking the SDK to parse. Superseded
# strictly by the two below it: `test_the_flow_refuses_an_empty_answer` covers
# the agent saying nothing, and `test_the_flow_refuses_prose_and_quotes_what_
# came_back` covers it saying something unparseable, which the old test could
# not reach at all.


# ---------------------------------------------------------------------------
# Reading the model's text: fences tolerated, content never.
#
# The flow stopped passing ``response_format`` (see the module docstring in
# ``categorise.py``), so these pin the whole of what replaced it. The line
# worth keeping straight: **leniency stops at the wrapper.** A fence is
# whitespace with delusions of grandeur; everything inside it still has to
# satisfy ``StatementProposals`` and then ``_validate_proposals``.
# ---------------------------------------------------------------------------
ONE_LINE = {"lines": [{"date": "2026-07-01", "description": "A", "amount": "-1.00"}]}

#: A minimal well-formed answer for a single line, as raw model text.
ONE_PROPOSAL_JSON = (
    '{"proposals": [{"line_index": 0, "hmrc_category": "repairs_maintenance", '
    '"property_id": null, "confidence": 0.9, "rationale": "hardware shop"}]}'
)


def _raw_kickoff(
    monkeypatch: pytest.MonkeyPatch, raw: str, captured: dict[str, Any] | None = None
) -> None:
    """Stub the agent to return ``raw`` as its text, the way a real one does."""

    def fake_kickoff(self: Any, messages: Any, **kwargs: Any) -> Any:
        assert "response_format" not in kwargs, (
            "the flow must not ask the SDK to parse; it parses the text itself"
        )
        if captured is not None:
            captured["prompt"] = messages
        return _FakeAgentResult(raw=raw)

    monkeypatch.setattr(categorise.Agent, "kickoff", fake_kickoff)


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("bare", ONE_PROPOSAL_JSON),
        ("fenced with a language tag", f"```json\n{ONE_PROPOSAL_JSON}\n```"),
        ("fenced without one", f"```\n{ONE_PROPOSAL_JSON}\n```"),
        ("surrounded by whitespace", f"\n\n  {ONE_PROPOSAL_JSON}  \n"),
        ("fenced and padded", f"  ```json\n{ONE_PROPOSAL_JSON}\n```  \n"),
    ],
)
def test_the_flow_reads_proposals_however_the_model_wrapped_them(
    monkeypatch: pytest.MonkeyPatch, label: str, raw: str
) -> None:
    """A markdown fence is packaging, not content.

    Measured 2026-08-04 against ``ollama/glm-5.2:cloud``, which returns
    exactly the second case: Ollama's cloud path does not constrain decoding,
    so nothing stops a model dressing its JSON up.
    """
    _raw_kickoff(monkeypatch, raw)

    flow = CategoriseStatementFlow()
    flow.kickoff(inputs=ONE_LINE)

    assert flow.state.proposals is not None, label
    assert flow.state.proposals.proposals[0].hmrc_category is HmrcCategory.REPAIRS_MAINTENANCE


def test_the_flow_refuses_prose_and_quotes_what_came_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error message is now the whole debugging surface.

    Without ``response_format`` there is no SDK error naming the offending
    field, so a model that answers in sentences -- which the same model does
    when the prompt does not spell the format out -- has to fail here, loudly
    and quoting itself.
    """
    _raw_kickoff(monkeypatch, "**Category:** Home Improvement / DIY. Hope that helps!")

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="Home Improvement"):
        flow.kickoff(inputs=ONE_LINE)


def test_the_flow_refuses_an_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty string is not an empty proposal set; it is no answer at all."""
    _raw_kickoff(monkeypatch, "   \n  ")

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError):
        flow.kickoff(inputs=ONE_LINE)


def test_a_fenced_answer_still_faces_the_contract_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The test this whole change stands on.**

    Tolerating the wrapper must not tolerate the contents. A `line_index` of
    7 against a one-line statement is the failure that would attach a
    proposal to the wrong transaction, and it has to die exactly as it did
    when the SDK was parsing.
    """
    out_of_range = ONE_PROPOSAL_JSON.replace('"line_index": 0', '"line_index": 7')
    _raw_kickoff(monkeypatch, f"```json\n{out_of_range}\n```")

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="out of range"):
        flow.kickoff(inputs=ONE_LINE)


def test_a_fenced_answer_still_faces_pydantic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confidence above 1 was refused by the schema; it must still be refused."""
    impossible = ONE_PROPOSAL_JSON.replace('"confidence": 0.9', '"confidence": 4.2')
    _raw_kickoff(monkeypatch, f"```json\n{impossible}\n```")

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError):
        flow.kickoff(inputs=ONE_LINE)


def test_the_prompt_carries_the_shape_the_sdk_used_to_supply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_prompt`` is now the only thing producing parseable output.

    Dropping ``response_format`` dropped the schema CrewAI injected with it.
    Measured: the same model asked without a format instruction answers in
    prose with markdown headings. So the prompt has to name the JSON shape,
    forbid the fence, and list the categories -- nothing else will.
    """
    captured: dict[str, Any] = {}
    _raw_kickoff(monkeypatch, ONE_PROPOSAL_JSON, captured)

    flow = CategoriseStatementFlow()
    flow.kickoff(inputs=ONE_LINE)

    prompt = captured["prompt"]
    assert '"proposals"' in prompt
    assert "line_index" in prompt
    assert "raw JSON" in prompt
    assert "```" in prompt, "the prompt must name the fence in order to forbid it"
    # Every category, because the model can no longer be handed the enum.
    for category in HmrcCategory:
        assert category.value in prompt


def test_confirmed_example_defaults_property_id_to_none() -> None:
    example = ConfirmedExample(
        description="x",
        amount=Decimal("-1.00"),
        hmrc_category=HmrcCategory.REPAIRS_MAINTENANCE,
    )
    assert example.property_id is None


def test_org_property_round_trips_uuid() -> None:
    prop = OrgProperty(id=PROPERTY_1, label="18 Sample Avenue")
    assert prop.id == PROPERTY_1
