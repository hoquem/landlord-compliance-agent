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
    """Duck-types the ``.pydantic`` attribute of ``LiteAgentOutput``."""

    def __init__(self, pydantic: Any) -> None:
        self.pydantic = pydantic


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
    def fake_kickoff(
        self: Any, messages: Any, response_format: Any = None, **kwargs: Any
    ) -> Any:
        assert response_format is StatementProposals
        if captured is not None:
            captured["prompt"] = messages
        return _FakeAgentResult(proposals)

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


def test_flow_raises_when_agent_does_not_return_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_kickoff(monkeypatch, None)

    flow = CategoriseStatementFlow()
    with pytest.raises(CategorisationResultError, match="structured"):
        flow.kickoff(
            inputs={
                "lines": [
                    {"date": "2026-07-01", "description": "A", "amount": "-1.00"}
                ],
            }
        )


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
