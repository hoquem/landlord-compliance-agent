"""Tests for the golden-set eval harness (``evals/run_eval.py``).

Every test here is offline: scoring-maths tests never touch the flow at
all, and the one end-to-end test monkeypatches ``categorise.Agent.kickoff``
(the same seam ``tests/flows/test_categorise_models.py`` stubs) so no
network/LLM call happens. This file must never be the thing that makes the
default ``pytest`` suite depend on Ollama or a live model being reachable.

:seealso: ``backend/evals/run_eval.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from evals import run_eval
from src.core.categories import HmrcCategory
from src.flows import categorise
from src.flows.categorise import LineProposal, StatementProposals

PROPERTY_1 = UUID("00000000-0000-0000-0000-000000000001")


def _proposal(
    index: int,
    category: HmrcCategory,
    property_id: UUID | None = None,
    confidence: float = 0.9,
) -> LineProposal:
    return LineProposal(
        line_index=index,
        hmrc_category=category,
        property_id=property_id,
        confidence=confidence,
        rationale="test rationale",
    )


# ---------------------------------------------------------------------------
# score(): pure scoring maths, no LLM, no flow
# ---------------------------------------------------------------------------


def test_score_hand_computed_toy_case() -> None:
    # 4 expected lines. Line 0 and 1 predicted correctly. Line 2 (expected
    # repairs_maintenance) mispredicted as capital_expense. Line 3 (expected
    # legal_professional) mispredicted as repairs_maintenance -- this makes
    # repairs_maintenance both under- and over-predicted in the same case,
    # and legal_professional is a category with ZERO predictions at all
    # (no proposal ever names it), exercising the precision 0/0 case.
    expected = [
        run_eval.ExpectedLine(category=HmrcCategory.RENT_INCOME, property_id=None),
        run_eval.ExpectedLine(
            category=HmrcCategory.REPAIRS_MAINTENANCE, property_id=None
        ),
        run_eval.ExpectedLine(
            category=HmrcCategory.REPAIRS_MAINTENANCE, property_id=None
        ),
        run_eval.ExpectedLine(
            category=HmrcCategory.LEGAL_PROFESSIONAL, property_id=None
        ),
    ]
    proposals = [
        _proposal(0, HmrcCategory.RENT_INCOME),
        _proposal(1, HmrcCategory.REPAIRS_MAINTENANCE),
        _proposal(2, HmrcCategory.CAPITAL_EXPENSE),
        _proposal(3, HmrcCategory.REPAIRS_MAINTENANCE),
    ]

    result = run_eval.score(proposals, expected)

    assert result.total == 4
    assert result.correct == 2
    assert result.accuracy == pytest.approx(0.5)

    rent = result.per_category[HmrcCategory.RENT_INCOME]
    assert (rent.tp, rent.fp, rent.fn, rent.support) == (1, 0, 0, 1)
    assert rent.precision == pytest.approx(1.0)
    assert rent.recall == pytest.approx(1.0)

    repairs = result.per_category[HmrcCategory.REPAIRS_MAINTENANCE]
    assert (repairs.tp, repairs.fp, repairs.fn, repairs.support) == (1, 1, 1, 2)
    assert repairs.precision == pytest.approx(0.5)
    assert repairs.recall == pytest.approx(0.5)

    capital = result.per_category[HmrcCategory.CAPITAL_EXPENSE]
    assert (capital.tp, capital.fp, capital.fn, capital.support) == (0, 1, 0, 0)
    assert capital.precision == pytest.approx(0.0)
    # No expected capital_expense lines -> recall is undefined-but-safe 0.0,
    # not a ZeroDivisionError.
    assert capital.recall == pytest.approx(0.0)

    legal = result.per_category[HmrcCategory.LEGAL_PROFESSIONAL]
    assert (legal.tp, legal.fp, legal.fn, legal.support) == (0, 0, 1, 1)
    # Zero predictions of this category anywhere -> precision 0/0 must not
    # raise ZeroDivisionError.
    assert legal.precision == pytest.approx(0.0)
    assert legal.recall == pytest.approx(0.0)


def test_score_reports_property_allocation_accuracy_separately() -> None:
    expected = [
        run_eval.ExpectedLine(
            category=HmrcCategory.REPAIRS_MAINTENANCE, property_id=PROPERTY_1
        ),
        run_eval.ExpectedLine(
            category=HmrcCategory.PERSONAL_NON_BUSINESS, property_id=None
        ),
    ]
    proposals = [
        _proposal(0, HmrcCategory.REPAIRS_MAINTENANCE, property_id=PROPERTY_1),
        _proposal(1, HmrcCategory.PERSONAL_NON_BUSINESS, property_id=None),
    ]

    result = run_eval.score(proposals, expected)

    # Only line 0 has an expected property attribution -- line 1's property
    # is None so it is excluded from the property-accuracy denominator.
    assert result.property_total == 1
    assert result.property_correct == 1
    assert result.property_accuracy == pytest.approx(1.0)


def test_score_handles_missing_proposal_for_a_line_without_crashing() -> None:
    # Only one proposal for two expected lines -- score() must not crash;
    # the missing line simply counts as incorrect / a false negative.
    expected = [
        run_eval.ExpectedLine(category=HmrcCategory.RENT_INCOME, property_id=None),
        run_eval.ExpectedLine(
            category=HmrcCategory.REPAIRS_MAINTENANCE, property_id=None
        ),
    ]
    proposals = [_proposal(0, HmrcCategory.RENT_INCOME)]

    result = run_eval.score(proposals, expected)

    assert result.total == 2
    assert result.correct == 1
    repairs = result.per_category[HmrcCategory.REPAIRS_MAINTENANCE]
    assert (repairs.tp, repairs.fp, repairs.fn, repairs.support) == (0, 0, 1, 1)


# ---------------------------------------------------------------------------
# load_golden_set(): loader validation
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )


def test_load_golden_set_parses_valid_lines(tmp_path: Path) -> None:
    golden_path = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden_path,
        [
            {
                "date": "2026-04-01",
                "description": "STANDING ORDER RENT",
                "amount": "950.00",
                "expected_category": "rent_income",
                "property_label": "18 Sample Avenue, Luton",
                "_synthetic": True,
            },
            {
                "date": "2026-04-02",
                "description": "AMAZON.CO.UK",
                "amount": "-23.99",
                "expected_category": "personal_non_business",
                "property_label": None,
                "_synthetic": True,
            },
        ],
    )

    lines = run_eval.load_golden_set(golden_path)

    assert len(lines) == 2
    assert lines[0].expected_category == HmrcCategory.RENT_INCOME
    assert lines[0].property_label == "18 Sample Avenue, Luton"
    assert lines[1].property_label is None


def test_load_golden_set_rejects_bad_category_naming_line_number(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden_path,
        [
            {
                "date": "2026-04-01",
                "description": "OK LINE",
                "amount": "1.00",
                "expected_category": "rent_income",
                "property_label": None,
            },
            {
                "date": "2026-04-02",
                "description": "BAD LINE",
                "amount": "1.00",
                "expected_category": "not_a_real_category",
                "property_label": None,
            },
        ],
    )

    with pytest.raises(run_eval.GoldenSetError, match=r"line 2"):
        run_eval.load_golden_set(golden_path)


def test_load_golden_set_rejects_missing_field_naming_line_number(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(
        json.dumps({"date": "2026-04-01", "description": "NO AMOUNT FIELD"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(run_eval.GoldenSetError, match=r"line 1"):
        run_eval.load_golden_set(golden_path)


def test_load_golden_set_ignores_blank_lines(tmp_path: Path) -> None:
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(
        json.dumps(
            {
                "date": "2026-04-01",
                "description": "OK",
                "amount": "1.00",
                "expected_category": "rent_income",
                "property_label": None,
            }
        )
        + "\n\n",
        encoding="utf-8",
    )

    lines = run_eval.load_golden_set(golden_path)

    assert len(lines) == 1


def test_real_golden_set_file_loads_and_has_at_least_twenty_lines() -> None:
    lines = run_eval.load_golden_set(run_eval.DEFAULT_GOLDEN_PATH)
    assert len(lines) >= 20
    assert all(line.synthetic for line in lines)


def test_load_golden_set_rejects_non_object_line_naming_line_number(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps([1, 2, 3]) + "\n", encoding="utf-8")

    with pytest.raises(run_eval.GoldenSetError, match=r"line 1"):
        run_eval.load_golden_set(golden_path)


def test_load_golden_set_rejects_bad_date_naming_line_number(tmp_path: Path) -> None:
    golden_path = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden_path,
        [
            {
                "date": "not-a-date",
                "description": "OK",
                "amount": "1.00",
                "expected_category": "rent_income",
                "property_label": None,
            }
        ],
    )

    with pytest.raises(run_eval.GoldenSetError, match=r"line 1"):
        run_eval.load_golden_set(golden_path)


def test_load_golden_set_rejects_bad_amount_naming_line_number(tmp_path: Path) -> None:
    golden_path = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden_path,
        [
            {
                "date": "2026-04-01",
                "description": "OK",
                "amount": "not-a-number",
                "expected_category": "rent_income",
                "property_label": None,
            }
        ],
    )

    with pytest.raises(run_eval.GoldenSetError, match=r"line 1"):
        run_eval.load_golden_set(golden_path)


# ---------------------------------------------------------------------------
# main(): CLI wiring -- stubbed flow only, never a live LLM
# ---------------------------------------------------------------------------


@pytest.fixture
def small_golden_set(tmp_path: Path) -> Path:
    golden_path = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden_path,
        [
            {
                "date": "2026-04-01",
                "description": "STANDING ORDER RENT",
                "amount": "950.00",
                "expected_category": "rent_income",
                "property_label": "18 Sample Avenue, Luton",
                "_synthetic": True,
            },
            {
                "date": "2026-04-02",
                "description": "B&Q LUTON",
                "amount": "-84.99",
                "expected_category": "repairs_maintenance",
                "property_label": "18 Sample Avenue, Luton",
                "_synthetic": True,
            },
            {
                "date": "2026-04-03",
                "description": "AMAZON.CO.UK",
                "amount": "-23.99",
                "expected_category": "personal_non_business",
                "property_label": None,
                "_synthetic": True,
            },
        ],
    )
    return golden_path


def _stub_kickoff_matching_expected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``Agent.kickoff`` to always return the correct category per line.

    Mirrors the seam ``tests/flows/test_categorise_models.py`` stubs
    (``categorise.Agent.kickoff``), not ``Flow.kickoff`` -- the flow's
    ``@start``/``@listen`` machinery runs for real, only the LLM call is
    faked.
    """

    def fake_kickoff(
        self: Any, messages: Any, response_format: Any = None, **kwargs: Any
    ) -> Any:
        assert response_format is StatementProposals
        # Cheat: parse the expected category straight back out of the
        # prompt's few-shot-free line list isn't available here, so instead
        # this fake is only used with golden sets the test itself controls
        # -- it always predicts correctly for the 3-line small_golden_set.
        proposals = StatementProposals(
            proposals=[
                LineProposal(
                    line_index=0,
                    hmrc_category=HmrcCategory.RENT_INCOME,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                ),
                LineProposal(
                    line_index=1,
                    hmrc_category=HmrcCategory.REPAIRS_MAINTENANCE,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                ),
                LineProposal(
                    line_index=2,
                    hmrc_category=HmrcCategory.PERSONAL_NON_BUSINESS,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                ),
            ]
        )

        class _FakeResult:
            pydantic = proposals

        return _FakeResult()

    monkeypatch.setattr(categorise.Agent, "kickoff", fake_kickoff)


def test_main_exits_zero_when_accuracy_meets_threshold(
    monkeypatch: pytest.MonkeyPatch, small_golden_set: Path
) -> None:
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    _stub_kickoff_matching_expected(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        run_eval.main(["--golden", str(small_golden_set), "--threshold", "0.5"])

    assert exc_info.value.code == 0


def test_main_exits_one_when_accuracy_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
    small_golden_set: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    def fake_kickoff(
        self: Any, messages: Any, response_format: Any = None, **kwargs: Any
    ) -> Any:
        # Line 1 (B&Q -> repairs_maintenance) is deliberately mispredicted,
        # so this run exercises score()'s fp/fn accounting through main()
        # end-to-end: 2/3 correct = 0.667, below the real default 0.7
        # threshold -- a genuine gate trip, not just a threshold set above 1.0.
        proposals = StatementProposals(
            proposals=[
                LineProposal(
                    line_index=0,
                    hmrc_category=HmrcCategory.RENT_INCOME,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                ),
                LineProposal(
                    line_index=1,
                    hmrc_category=HmrcCategory.CAPITAL_EXPENSE,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                ),
                LineProposal(
                    line_index=2,
                    hmrc_category=HmrcCategory.PERSONAL_NON_BUSINESS,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                ),
            ]
        )

        class _FakeResult:
            pydantic = proposals

        return _FakeResult()

    monkeypatch.setattr(categorise.Agent, "kickoff", fake_kickoff)

    with pytest.raises(SystemExit) as exc_info:
        run_eval.main(["--golden", str(small_golden_set)])

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "66.67%" in err


def test_main_prints_failure_message_to_stderr_even_with_json(
    monkeypatch: pytest.MonkeyPatch,
    small_golden_set: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The --json flag governs stdout's payload shape only -- the failure
    # message must still land on stderr so a CI-nightly run using --json
    # for machine parsing isn't left with a bare, unexplained exit 1.
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    _stub_kickoff_matching_expected(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        run_eval.main(
            ["--golden", str(small_golden_set), "--threshold", "1.01", "--json"]
        )

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    # stdout must still be pure JSON, unpolluted by the failure message.
    json.loads(captured.out)


def test_main_respects_limit(
    monkeypatch: pytest.MonkeyPatch, small_golden_set: Path
) -> None:
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    captured: dict[str, Any] = {}

    def fake_kickoff(
        self: Any, messages: Any, response_format: Any = None, **kwargs: Any
    ) -> Any:
        captured["prompt"] = messages
        proposals = StatementProposals(
            proposals=[
                LineProposal(
                    line_index=0,
                    hmrc_category=HmrcCategory.RENT_INCOME,
                    property_id=None,
                    confidence=0.9,
                    rationale="stub",
                )
            ]
        )

        class _FakeResult:
            pydantic = proposals

        return _FakeResult()

    monkeypatch.setattr(categorise.Agent, "kickoff", fake_kickoff)

    with pytest.raises(SystemExit) as exc_info:
        run_eval.main(
            ["--golden", str(small_golden_set), "--threshold", "0.5", "--limit", "1"]
        )

    assert exc_info.value.code == 0
    # Only line 0's description should have made it into the prompt.
    assert "B&Q LUTON" not in captured["prompt"]
    assert "STANDING ORDER RENT" in captured["prompt"]


@pytest.mark.parametrize("bad_limit", ["0", "-1"])
def test_main_rejects_non_positive_limit(
    monkeypatch: pytest.MonkeyPatch, small_golden_set: Path, bad_limit: str
) -> None:
    # --limit 0 would silently score an empty run (accuracy 0.0, exit 1
    # with no lines shown); --limit -1 would silently drop the last line.
    # Both are surprising, not loud -- argparse must reject them outright
    # (exit code 2, its own convention for a bad CLI argument) before the
    # flow ever runs.
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")

    with pytest.raises(SystemExit) as exc_info:
        run_eval.main(["--golden", str(small_golden_set), "--limit", bad_limit])

    assert exc_info.value.code == 2


def test_main_sets_categoriser_model_env_from_cli_flag(
    monkeypatch: pytest.MonkeyPatch, small_golden_set: Path
) -> None:
    monkeypatch.delenv("CATEGORISER_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    _stub_kickoff_matching_expected(monkeypatch)

    with pytest.raises(SystemExit):
        run_eval.main(
            [
                "--golden",
                str(small_golden_set),
                "--threshold",
                "0.5",
                "--model",
                "ollama/glm-4.5-air",
            ]
        )

    assert __import__("os").environ["CATEGORISER_MODEL"] == "ollama/glm-4.5-air"


def test_main_json_output_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
    small_golden_set: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CATEGORISER_MODEL", "ollama/test-model")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    _stub_kickoff_matching_expected(monkeypatch)

    with pytest.raises(SystemExit):
        run_eval.main(
            ["--golden", str(small_golden_set), "--threshold", "0.5", "--json"]
        )

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["accuracy"] == pytest.approx(1.0)
    assert payload["total"] == 3
