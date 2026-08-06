"""Golden-set eval harness for :class:`~src.flows.categorise.CategoriseStatementFlow`.

Runs the real categorisation flow against a small hand-labelled set of
statement lines and reports per-category precision/recall plus overall
accuracy, exiting non-zero when accuracy falls below a threshold.

.. important::
   ``evals/golden_set.jsonl`` is 20 SYNTHETIC lines (each marked
   ``"_synthetic": true``), kept so this harness runs out of the box. **It
   flatters the model**, and measurably: on 2026-08-06 the same model scored
   85% against it and **66.67% against real confirmed lines** -- below the
   0.70 threshold, so the real run *fails*. Both misses were categories a
   human had corrected by hand during the first real quarter, which is the
   benchmark doing its job.

   The real set is built by ``evals/build_golden_set.py`` into
   ``evals/golden_set.real.jsonl``, which ``.gitignore`` excludes: it carries
   bank descriptions and property labels verbatim and this repository is
   public. Run against it explicitly::

       uv run --env-file ../.env python evals/build_golden_set.py --org "<org>"
       uv run --env-file ../.env python evals/run_eval.py --golden evals/golden_set.real.jsonl

   Treat a score against the synthetic set as a smoke test, never as a
   measurement.

This harness doubles as a model-selection tool: run it twice with
``--model`` pointed at different LiteLLM/CrewAI model ids (e.g. a local
Ollama GLM-class model vs. a hosted model) and diff the ``--json`` output
to decide the production model empirically, on this project's own data
rather than published benchmarks. Note this is a COLD-START comparison:
the harness never passes ``few_shot`` examples (production supplies up to
50 confirmed transactions per org), so a verdict here reflects each
model's zero-shot categorisation quality, not its behaviour once an org
has real history.

Usage::

    uv run python evals/run_eval.py
    uv run python evals/run_eval.py --model ollama/glm-4.5-air --json
    uv run python evals/run_eval.py --limit 5 --threshold 0.8

The live eval (this script run for real) talks to whatever
``CATEGORISER_MODEL`` points at and is therefore run manually or in a
nightly CI job -- never as part of the default ``pytest`` suite, which
only exercises :func:`load_golden_set` and :func:`score` plus
:func:`main` wired to a stubbed flow.

:seealso: ``backend/src/flows/categorise.py``; spec Task 12 -- golden-set
    eval harness.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Running this file directly (`uv run python evals/run_eval.py`, per the
# usage examples above) makes Python put this file's own directory --
# evals/, not backend/ -- at sys.path[0], so the "src.*" imports below would
# otherwise fail with ModuleNotFoundError. pytest sidesteps this via its own
# `pythonpath = ["."]` ini option (see pyproject.toml), which does not apply
# to a plain `python` invocation, hence this explicit, idempotent fix-up.
_BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from src.core.categories import HmrcCategory
from src.flows.categorise import (
    CategoriseStatementFlow,
    LineProposal,
    OrgProperty,
    StatementLineInput,
)

#: Default golden-set path, relative to this file (not the caller's cwd) so
#: the harness runs the same way regardless of where ``uv run`` is invoked
#: from.
DEFAULT_GOLDEN_PATH = Path(__file__).parent / "golden_set.jsonl"

#: Starting accuracy bar (per the plan: "start 0.7; raise as the set
#: grows"). A hard-coded floor, not tuned -- raising it is a deliberate,
#: reviewed change to this constant as the golden set gets bigger and more
#: representative, not something the harness infers on its own.
DEFAULT_THRESHOLD = 0.7

#: Namespace used to derive stable synthetic property UUIDs from property
#: labels (uuid5 is deterministic: the same label always yields the same
#: id, run to run, so a head-to-head diff across two ``--model`` runs
#: compares like-for-like property_ids).
_PROPERTY_UUID_NAMESPACE = uuid.UUID("6f6e5c4a-2b1d-4a3e-9c7f-1a2b3c4d5e6f")


class GoldenSetError(ValueError):
    """Raised when a golden-set JSONL line is missing or malformed.

    Always names the (1-based) line number so a bad line can be found and
    fixed without re-parsing the whole file by hand.
    """


@dataclass(frozen=True)
class GoldenLine:
    """One labelled line from the golden set.

    :ivar date: ISO ``YYYY-MM-DD`` transaction date.
    :ivar description: raw statement-line description.
    :ivar amount: signed decimal amount as a string (negative = money out).
    :ivar expected_category: the correct HMRC category for this line.
    :ivar property_label: the property this line should be attributed to,
        or ``None`` for lines with no property attribution.
    :ivar synthetic: ``True`` for the placeholder synthetic golden set;
        ``False`` once replaced with a real confirmed line.
    """

    date: str
    description: str
    amount: str
    expected_category: HmrcCategory
    property_label: str | None
    synthetic: bool


@dataclass(frozen=True)
class ExpectedLine:
    """The ground truth for one line, resolved to the flow's own id shape.

    Decoupled from :class:`GoldenLine` (which carries a human-readable
    ``property_label``) so :func:`score` stays a pure function over the
    same id space the flow's :class:`~src.flows.categorise.LineProposal`
    proposals use -- no string label comparisons inside the scorer.

    :ivar category: the correct HMRC category.
    :ivar property_id: the correct property id, or ``None``.
    """

    category: HmrcCategory
    property_id: uuid.UUID | None


@dataclass
class CategoryStats:
    """Confusion-matrix counts for one HMRC category.

    :ivar tp: lines where this category was both expected and predicted.
    :ivar fp: lines where this category was predicted but a different one
        was expected.
    :ivar fn: lines where this category was expected but a different one
        (or none) was predicted.
    :ivar support: number of lines where this category was expected
        (``tp + fn``).
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    support: int = 0

    @property
    def precision(self) -> float:
        """``tp / (tp + fp)``, or ``0.0`` if this category was never predicted."""
        denominator = self.tp + self.fp
        return self.tp / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        """``tp / (tp + fn)``, or ``0.0`` if this category was never expected."""
        denominator = self.tp + self.fn
        return self.tp / denominator if denominator else 0.0

    def to_dict(self) -> dict[str, float | int]:
        """Serialise for ``--json`` output."""
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "support": self.support,
            "precision": self.precision,
            "recall": self.recall,
        }


@dataclass
class EvalResult:
    """The full scoring output of one golden-set run.

    :ivar total: number of scored lines.
    :ivar correct: number of lines whose predicted category exactly
        matched the expected category.
    :ivar per_category: :class:`CategoryStats` keyed by every category
        that appeared as either expected or predicted at least once.
    :ivar property_total: number of lines with a non-``None`` expected
        property.
    :ivar property_correct: of those, how many the flow attributed to the
        correct property.
    """

    total: int
    correct: int
    per_category: dict[HmrcCategory, CategoryStats] = field(default_factory=dict)
    property_total: int = 0
    property_correct: int = 0

    @property
    def accuracy(self) -> float:
        """Overall category accuracy, or ``0.0`` for an empty run."""
        return self.correct / self.total if self.total else 0.0

    @property
    def property_accuracy(self) -> float | None:
        """Property-allocation accuracy, or ``None`` if no line had an expected property."""
        if self.property_total == 0:
            return None
        return self.property_correct / self.property_total

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``--json`` output (machine-readable, for head-to-head diffing)."""
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "property_total": self.property_total,
            "property_correct": self.property_correct,
            "property_accuracy": self.property_accuracy,
            "per_category": {
                category.value: stats.to_dict()
                for category, stats in sorted(
                    self.per_category.items(), key=lambda kv: kv[0].value
                )
            },
        }


def load_golden_set(path: Path) -> list[GoldenLine]:
    """Load and validate a golden-set JSONL file.

    Blank lines are skipped (harmless formatting, e.g. a trailing newline).
    Every other line must be a JSON object with ``date``, ``description``,
    ``amount``, ``expected_category`` (a valid :class:`HmrcCategory` value)
    and ``property_label`` (a string or ``null``). ``_synthetic`` is
    optional and defaults to ``False``.

    :param path: path to the JSONL file.
    Every field that later feeds :class:`~src.flows.categorise.StatementLineInput`
    (``date``, ``amount``) is validated here too, not left to fail later
    inside :func:`build_flow_inputs` -- a bad line must be caught with its
    line number while the file is still in hand, not surface as an opaque
    ``decimal.InvalidOperation`` or pydantic error deep in a later call.

    :param path: path to the JSONL file.
    :raises GoldenSetError: on invalid JSON, a line that is not a JSON
        object, a missing required field, an unparseable ``date`` or
        ``amount``, or an ``expected_category`` that is not a recognised
        :class:`HmrcCategory` value -- always naming the offending
        (1-based) line number.
    """
    lines: list[GoldenLine] = []
    raw_text = path.read_text(encoding="utf-8")
    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue

        def fail(message: str, _line_number: int = line_number) -> GoldenSetError:
            return GoldenSetError(f"{path}:line {_line_number}: {message}")

        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise fail(f"invalid JSON: {exc}") from exc

        if not isinstance(obj, dict):
            raise fail(f"expected a JSON object, got {type(obj).__name__}")

        try:
            category = HmrcCategory(obj["expected_category"])
        except KeyError as exc:
            raise fail(f"missing field {exc}") from exc
        except ValueError as exc:
            raise fail(
                f"invalid expected_category {obj['expected_category']!r}: {exc}"
            ) from exc

        try:
            raw_date = obj["date"]
            raw_description = obj["description"]
            raw_amount = obj["amount"]
        except KeyError as exc:
            raise fail(f"missing field {exc}") from exc

        try:
            _date.fromisoformat(raw_date)
        except (TypeError, ValueError) as exc:
            raise fail(f"invalid date {raw_date!r}: {exc}") from exc

        try:
            Decimal(str(raw_amount))
        except InvalidOperation as exc:
            raise fail(f"invalid amount {raw_amount!r}: {exc}") from exc

        lines.append(
            GoldenLine(
                date=raw_date,
                description=raw_description,
                amount=str(raw_amount),
                expected_category=category,
                property_label=obj.get("property_label"),
                synthetic=bool(obj.get("_synthetic", False)),
            )
        )

    return lines


def property_id_for_label(label: str) -> uuid.UUID:
    """Derive a stable synthetic property UUID from a property label.

    Deterministic (uuid5) so the same label always maps to the same id
    across runs -- required for a head-to-head ``--model`` diff to compare
    property attributions like-for-like, and for property-allocation
    scoring to work at all without a real properties table.

    :param label: the golden-set line's ``property_label``.
    """
    return uuid.uuid5(_PROPERTY_UUID_NAMESPACE, label)


def build_flow_inputs(
    golden_lines: list[GoldenLine],
) -> tuple[list[StatementLineInput], list[OrgProperty], list[ExpectedLine]]:
    """Turn golden-set lines into the flow's input shape plus ground truth.

    :param golden_lines: lines loaded by :func:`load_golden_set` (already
        sliced to ``--limit`` by the caller, if requested).
    :return: ``(lines, properties, expected)`` where ``lines``/``properties``
        match :class:`~src.flows.categorise.CategoriseStatementFlow`'s
        kickoff-input shape, and ``expected[i]`` is the ground truth for
        ``lines[i]``.
    """
    labels = sorted({gl.property_label for gl in golden_lines if gl.property_label})
    properties = [
        OrgProperty(id=property_id_for_label(label), label=label) for label in labels
    ]

    lines = [
        StatementLineInput(
            date=gl.date, description=gl.description, amount=Decimal(gl.amount)
        )
        for gl in golden_lines
    ]
    expected = [
        ExpectedLine(
            category=gl.expected_category,
            property_id=property_id_for_label(gl.property_label)
            if gl.property_label
            else None,
        )
        for gl in golden_lines
    ]
    return lines, properties, expected


def score(proposals: list[LineProposal], expected: list[ExpectedLine]) -> EvalResult:
    """Score a flow's proposals against ground truth. Pure, no I/O.

    Proposals are matched to expected lines by ``line_index`` (0-based,
    aligned with ``expected``'s own index). A line with no matching
    proposal is not a crash: it simply counts as incorrect and contributes
    a false negative to its expected category, so a flow that under-returns
    proposals still gets a (bad, honest) score rather than an exception.

    :param proposals: the flow's :class:`LineProposal` list.
    :param expected: ground truth, one entry per line, in line order.
    """
    proposals_by_index = {p.line_index: p for p in proposals}

    per_category: dict[HmrcCategory, CategoryStats] = {}

    def stats_for(category: HmrcCategory) -> CategoryStats:
        return per_category.setdefault(category, CategoryStats())

    correct = 0
    property_total = 0
    property_correct = 0

    for index, exp in enumerate(expected):
        predicted = proposals_by_index.get(index)
        expected_stats = stats_for(exp.category)
        expected_stats.support += 1

        is_correct = predicted is not None and predicted.hmrc_category == exp.category
        if is_correct:
            correct += 1
            expected_stats.tp += 1
        else:
            expected_stats.fn += 1
            if predicted is not None:
                stats_for(predicted.hmrc_category).fp += 1

        if exp.property_id is not None:
            property_total += 1
            if predicted is not None and predicted.property_id == exp.property_id:
                property_correct += 1

    return EvalResult(
        total=len(expected),
        correct=correct,
        per_category=per_category,
        property_total=property_total,
        property_correct=property_correct,
    )


def format_report(result: EvalResult, threshold: float) -> str:
    """Render a human-readable report table.

    :param result: the scoring result to render.
    :param threshold: the accuracy threshold this run was checked against
        (shown in the summary line, not used for formatting decisions).
    """
    lines = ["Category                       Precision  Recall  Support"]
    lines.append("-" * len(lines[0]))
    for category in sorted(result.per_category, key=lambda c: c.value):
        stats = result.per_category[category]
        lines.append(
            f"{category.value:<30}  {stats.precision:>8.2%}  {stats.recall:>6.2%}  "
            f"{stats.support:>7}"
        )
    lines.append("")
    lines.append(
        f"Overall accuracy: {result.accuracy:.2%} ({result.correct}/{result.total})"
    )
    if result.property_accuracy is not None:
        lines.append(
            f"Property-allocation accuracy: {result.property_accuracy:.2%} "
            f"({result.property_correct}/{result.property_total})"
        )
    else:
        lines.append(
            "Property-allocation accuracy: n/a (no lines had an expected property)"
        )
    lines.append(f"Threshold: {threshold:.2%}")
    return "\n".join(lines)


def run_flow(
    lines: list[StatementLineInput], properties: list[OrgProperty]
) -> list[LineProposal]:
    """Run :class:`CategoriseStatementFlow` once, in production shape.

    CrewAI's global event listener prints Rich "Flow Execution" banners to
    stdout regardless of this flow's own verbosity setting; those are
    swallowed here so the harness's own report (optionally ``--json``) is
    the only thing on stdout. Exceptions still propagate normally -- only
    the banner *printing* is suppressed, not error handling. This is also
    a privacy choice, not just tidiness: those banners can echo the prompt,
    which carries statement descriptions and amounts -- do not change this
    to dump the swallowed buffer to stderr "for debuggability" on failure.

    :param lines: statement lines to categorise.
    :param properties: the org's properties (offered to the agent).
    :return: the flow's validated proposals.
    """
    flow = CategoriseStatementFlow()
    with contextlib.redirect_stdout(io.StringIO()):
        flow.kickoff(
            inputs={
                "lines": [line.model_dump(mode="json") for line in lines],
                "properties": [prop.model_dump(mode="json") for prop in properties],
            }
        )
    if flow.state.proposals is None:
        raise RuntimeError(
            "flow completed without raising but state.proposals is None -- "
            "this should be unreachable (CategoriseStatementFlow.categorise "
            "always sets it or raises)"
        )
    return flow.state.proposals.proposals


def _positive_int(value: str) -> int:
    """``argparse`` type for ``--limit``: reject anything less than 1, loudly.

    ``--limit 0`` would otherwise silently score an empty run (accuracy
    0.0, a misleading exit 1); ``--limit -1`` would silently drop the last
    golden-set line via Python slice semantics. Both fail argument parsing
    itself instead.

    :raises argparse.ArgumentTypeError: if ``value`` is not an integer >= 1.
    """
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"--limit must be >= 1, got {parsed}")
    return parsed


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the categorisation flow against the golden set and report "
            "accuracy. Also doubles as a model-selection tool via --model."
        )
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "LiteLLM/CrewAI model id to categorise with (e.g. "
            "'ollama/glm-4.5-air'). Sets CATEGORISER_MODEL for this run. "
            "Defaults to the current CATEGORISER_MODEL env var."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help=f"path to the golden-set JSONL file (default: {DEFAULT_GOLDEN_PATH}).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"minimum overall accuracy required to exit 0 (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="run only the first N (>= 1) golden-set lines (quick smoke test).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON instead of the human-readable table.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    :param argv: argument list (defaults to ``sys.argv[1:]``); overridable
        for tests.
    :raises SystemExit: always -- code ``0`` when accuracy meets
        ``--threshold``, code ``1`` otherwise. This is the harness's
        CI-nightly gate contract.
    """
    args = _build_arg_parser().parse_args(argv)

    if args.model:
        os.environ["CATEGORISER_MODEL"] = args.model

    golden_lines = load_golden_set(args.golden)
    if args.limit is not None:
        golden_lines = golden_lines[: args.limit]

    lines, properties, expected = build_flow_inputs(golden_lines)
    proposals = run_flow(lines, properties)
    result = score(proposals, expected)

    if args.json:
        print(json.dumps(result.to_dict()))
    else:
        print(format_report(result, args.threshold))

    if result.accuracy < args.threshold:
        # Always to stderr, even with --json: stdout must stay pure JSON for
        # machine parsing, but a CI-nightly run still needs a human-readable
        # reason for the failure somewhere in its logs.
        print(
            f"FAILED: accuracy {result.accuracy:.2%} is below threshold "
            f"{args.threshold:.2%}",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
