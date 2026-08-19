"""Tests for the pattern-based pre-categoriser (``src/core/pattern_rules.py``).

Covers the substring semantics, ordering (specific-before-generic), the
``apply_rules`` split contract, and a coverage check against the real golden
set: the default rules must categorise the confirmed lines correctly without
miscategorising any of them.

:seealso: ``backend/src/core/pattern_rules.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.core.categories import HmrcCategory
from src.core.pattern_rules import (
    DEFAULT_RULES,
    PatternMatch,
    PatternRule,
    apply_rules,
    match_rules,
)
from src.flows.categorise import StatementLineInput


def _line(description: str, amount: str = "-10.00") -> StatementLineInput:
    return StatementLineInput(
        date=date(2026, 7, 1), description=description, amount=Decimal(amount)
    )


def _rule(pattern, category: HmrcCategory, description: str = "test") -> PatternRule:
    return PatternRule(pattern=pattern, category=category, description=description)


# ---------------------------------------------------------------------------
# match_rules: substring matching
# ---------------------------------------------------------------------------


def test_matching_is_case_insensitive() -> None:
    rule = _rule("PINPOINT ESTATES", HmrcCategory.RENT_INCOME)
    assert rule.matches("counter credit PinPoint Estates L 98 BGC")


def test_match_rules_returns_the_matching_rule() -> None:
    rule = match_rules("Counter Credit PINPOINT ESTATES L 98 WARWICK ROAD BGC")
    assert rule is not None
    assert rule.category is HmrcCategory.RENT_INCOME


def test_match_rules_returns_none_when_nothing_matches() -> None:
    assert match_rules("AD/10192 : Central UK Vehicle Leasing LTD") is None


def test_a_multi_term_rule_requires_every_term() -> None:
    rule = _rule(("hmr", "vat"), HmrcCategory.VAT_OUTPUT)
    assert rule.matches("162957186 : Hmrc Vat - Hmrc Vat")
    # Corporation tax mentions HMRC but not VAT -> no match for the VAT rule.
    assert not rule.matches("70207022180000200 : Hmrc Cotax - Hmrc Cotax")


def test_specific_rule_wins_over_a_generic_one() -> None:
    # "Hmrc Vat" must hit the VAT rule, not the generic HMRC fallback.
    rule = match_rules("0000694473 : Hmrc E Vat - Hmrc E Vat")
    assert rule is not None
    assert rule.category is HmrcCategory.VAT_OUTPUT


def test_custom_rules_are_supported() -> None:
    custom = [_rule("custom payee", HmrcCategory.REPAIRS_MAINTENANCE)]
    assert match_rules("Custom Payee Ltd", rules=custom) is not None
    # A description the custom set does not know is left unmatched even
    # though DEFAULT_RULES would have caught it.
    assert match_rules("PINPOINT ESTATES rent", rules=custom) is None


# ---------------------------------------------------------------------------
# apply_rules: splitting matched vs unmatched, preserving original order
# ---------------------------------------------------------------------------


def test_apply_rules_splits_matched_and_unmatched() -> None:
    lines = [
        _line("PINPOINT ESTATES L 98 BGC"),
        _line("WHATEVER TRADING CO LTD"),
        _line("BRITISH GAS BUSINE 000 DD"),
    ]
    matched, unmatched = apply_rules(lines)

    assert len(matched) == 2
    assert len(unmatched) == 1

    # Original indices preserved for mapping proposals back to rows.
    assert [m.index for m in matched] == [0, 2]
    assert matched[0].line.description == "PINPOINT ESTATES L 98 BGC"
    assert matched[0].rule.category is HmrcCategory.RENT_INCOME
    assert matched[1].rule.category is HmrcCategory.OTHER_ALLOWABLE

    # Unmatched residual stays in original order and is the LLM's problem.
    assert unmatched[0].description == "WHATEVER TRADING CO LTD"


def test_apply_rules_with_all_unmatched() -> None:
    lines = [_line("AD/10192 : Central UK Vehicle Leasing LTD")]
    matched, unmatched = apply_rules(lines)
    assert matched == []
    assert len(unmatched) == 1


def test_apply_rules_with_all_matched() -> None:
    lines = [_line("METRO PREPAID LIMI"), _line("CLAUDE - DEBIT CARD")]
    matched, unmatched = apply_rules(lines)
    assert len(matched) == 2
    assert unmatched == []


def test_apply_rules_with_empty_input() -> None:
    matched, unmatched = apply_rules([])
    assert matched == []
    assert unmatched == []


def test_pattern_match_is_a_frozen_value_object() -> None:
    line = _line("R FOX SALES - Automated Credit")
    rule = match_rules(line.description)
    assert rule is not None
    pm = PatternMatch(index=0, line=line, rule=rule)
    assert pm.index == 0
    assert pm.line is line
    assert pm.rule is rule


# ---------------------------------------------------------------------------
# Coverage against the real confirmed golden set
# ---------------------------------------------------------------------------


def test_default_rules_cover_the_confirmed_golden_set_without_miscategorising() -> None:
    """The default rules categorise the confirmed lines, never wrongly.

    Every line in ``evals/golden_set.real.jsonl`` is a hand-confirmed
    transaction. This is the accuracy contract the pre-categoriser exists
    for: it must catch the obvious lines (which the LLM gets wrong) and must
    not contradict a human confirmation on any line it does catch. The few
    genuinely ambiguous lines are expected to remain unmatched -- they are
    the LLM's job.
    """
    from pathlib import Path

    golden_path = (
        Path(__file__).resolve().parents[2] / "evals" / "golden_set.real.jsonl"
    )
    if not golden_path.exists():
        # The real golden set is gitignored (carries bank descriptions); if
        # it is absent on a fresh checkout, skip -- do not fail CI over a
        # file that does not exist.
        return

    import json

    correct = 0
    total = 0
    mismatches: list[tuple[str, str, str]] = []
    for raw in golden_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        obj = json.loads(raw)
        expected = HmrcCategory(obj["expected_category"])
        total += 1
        rule = match_rules(obj["description"])
        if rule is None:
            continue  # genuinely ambiguous -> left to the LLM
        if rule.category is expected:
            correct += 1
        else:
            mismatches.append((expected.value, rule.category.value, obj["description"]))

    assert not mismatches, f"rules misclassified confirmed lines: {mismatches}"
    # The LLM alone scored 45.6%; rules on top must clear the whole-point
    # improvement target. We assert on the caught-and-correct fraction: the
    # lines the rules claim must be at least ~2/3 of the set, so the
    # pre-categoriser actually cuts the LLM's workload.
    assert correct >= int(total * 0.80), (
        f"rules caught only {correct}/{total} confirmed lines -- below the "
        "80% floor"
    )
