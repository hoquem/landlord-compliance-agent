"""Categorisation flow: turns parsed bank-statement lines into HMRC-category
proposals via an LLM agent.

One :class:`CategoriseStatementFlow` run categorises one statement's worth of
:class:`~src.core.parser.ParsedLine` records. The flow itself does no I/O
beyond the single LLM call -- no DB access here (YAGNI; Task 18's worker
wires the DB in on both sides: building the flow's inputs and persisting its
output).

Model selection is fully env-driven (``CATEGORISER_MODEL``, optional
``OLLAMA_BASE_URL``) so this module never hard-codes a provider. Mahmud
intends to point this at a local open-weight model via Ollama for bank-data
privacy.

**This flow parses the model's text itself, and does not pass
``response_format``.** That is a deliberate loosening, decided 2026-08-04,
and the reason is that the enforcement it gave up was already fictional on
the configured provider. Measured against ``ollama/glm-5.2:cloud``:

* OpenAI-compatible ``response_format={"type": "json_schema", strict: true}``
  -> prose with markdown headings;
* Ollama's own ``format=<schema>`` on ``/api/chat`` -> the same prose.

Constrained decoding is a property of the *local* runner, and a ``:cloud``
tag has none -- the schema is dropped in transit. What actually produced
JSON was the prompt asking for it, and the model then wrapped it in a
`````json`` fence, which the OpenAI SDK's parser rejected before this
module saw a single byte.

So the trade is: give up a provider *refusing to emit* bad JSON (which this
one never did), keep this module *refusing to accept* it. Both guards that
matter still run, in this order, and both are loud:

1. :class:`StatementProposals` validation -- exactly what the schema
   encoded: known categories, confidence in range, required fields;
2. :func:`_validate_proposals` -- the part a schema never covered anyway:
   one proposal per line, indices in range and unique, property ids drawn
   from the list offered.

**Leniency stops at the wrapper.** A fence is stripped; nothing inside it is
forgiven. ``test_a_fenced_answer_still_faces_the_contract_check`` and
``test_a_fenced_answer_still_faces_pydantic`` are what hold that line.

The cost is real and worth naming: against a provider that *does* honour
strict structured output (Anthropic does), this gives up genuine
server-side enforcement in exchange for a parser. Point
``CATEGORISER_MODEL`` at such a provider and the guards above are the only
thing standing between a malformed answer and a tax figure -- which is why
they are tested harder than anything else in this module.

:seealso: spec Task 11 -- categorisation flow (CrewAI);
    ``docs/planning/progress.md`` for the measurements behind the decision.
"""

from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from crewai import LLM, Agent
from crewai.flow import Flow, listen, start
from pydantic import BaseModel, Field, ValidationError

from src.core.categories import HmrcCategory
from src.core.parser import ParsedLine

#: Config lives flat under crews/config/ (not nested under a crew-name
#: directory) because this flow drives a single Agent.kickoff() call
#: directly -- there is no Crew/Task here for @CrewBase to auto-load.
_AGENTS_YAML_PATH = Path(__file__).parent / "crews" / "config" / "agents.yaml"

#: Caller's contract, per the plan: at most the 50 most recent confirmed
#: transactions as few-shot examples. The flow does not fetch or trim this
#: itself (no DB access -- see module docstring); it only enforces the cap
#: loudly so a caller bug (e.g. forgetting to truncate) is never silently
#: absorbed into a bigger-than-intended prompt.
_MAX_FEW_SHOT = 50

#: Name of the environment variable carrying the LiteLLM/CrewAI model id
#: for the categoriser, e.g. "ollama/glm-4.5-air" or "anthropic/claude-...".
#: Read fresh (not module-level) so tests can monkeypatch it per-test.
_MODEL_ENV_VAR = "CATEGORISER_MODEL"

#: Optional base URL for a local Ollama server, e.g. "http://localhost:11434".
#: crewai's native Ollama provider (crewai.llms.providers.openai_compatible)
#: reads its own default from OLLAMA_HOST, not OLLAMA_BASE_URL -- this flow
#: reads OLLAMA_BASE_URL itself and passes it through as LLM(base_url=...)
#: so the plan's chosen env var name works regardless of crewai's internal
#: default. Verified against installed crewai==1.15.8: no litellm dependency
#: is present; passing base_url explicitly (with or without a trailing
#: "/v1") is normalised by crewai's OpenAI-compatible Ollama provider.
_OLLAMA_BASE_URL_ENV_VAR = "OLLAMA_BASE_URL"


class CategoriserConfigError(RuntimeError):
    """Raised when the categoriser's LLM is not configured.

    :ivar env_var: name of the missing environment variable.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"{env_var} is not set -- the categorisation flow requires an "
            "explicit model id (a LiteLLM/CrewAI id, e.g. "
            "'ollama/glm-4.5-air' for a local model or a hosted model id) "
            "and never falls back to a default provider."
        )


class CategorisationResultError(RuntimeError):
    """Raised when the agent's proposals fail post-validation.

    The flow never silently drops or patches a bad proposal set -- every
    violation of the line/property contract surfaces as a loud failure so
    the caller can retry or alert instead of persisting a wrong result.
    """


class LineProposal(BaseModel):
    """One line's proposed HMRC category.

    :ivar line_index: 0-based index into the statement lines given to the
        flow.
    :ivar hmrc_category: the proposed category.
    :ivar property_id: id of the property this line is attributed to, or
        ``None`` when the line has no property attribution.
    :ivar confidence: confidence in ``[0, 1]``; uncertain lines must score
        low rather than the agent guessing.
    :ivar rationale: short human-readable justification.
    """

    line_index: int
    hmrc_category: HmrcCategory
    property_id: UUID | None
    confidence: float = Field(ge=0, le=1)
    rationale: str


class StatementProposals(BaseModel):
    """The agent's full set of proposals for one statement.

    :ivar proposals: one :class:`LineProposal` per input line. This is the
        response-format contract the LLM is retried against by CrewAI --
        an out-of-range confidence or an unrecognised category name fails
        Pydantic validation before it ever reaches the flow's own
        post-validation.
    """

    proposals: list[LineProposal]


class StatementLineInput(BaseModel):
    """One statement line as given to the flow.

    Mirrors :class:`src.core.parser.ParsedLine` field-for-field; kept as a
    separate Pydantic model (rather than reusing the frozen dataclass
    directly) because Flow state must be a Pydantic model.
    """

    date: date
    description: str
    amount: Decimal

    @classmethod
    def from_parsed_line(cls, line: ParsedLine) -> StatementLineInput:
        """Build a :class:`StatementLineInput` from a parser :class:`ParsedLine`.

        :param line: a line produced by :func:`src.core.parser.parse_statement`.
        """
        return cls(date=line.date, description=line.description, amount=line.amount)


class OrgProperty(BaseModel):
    """A property the org owns, offered to the agent as a valid ``property_id``.

    :ivar id: property id.
    :ivar label: human-readable label (e.g. address) shown to the agent.
    :ivar mortgage_type: ``interest_only``, ``repayment``, or ``none``.
        The categoriser uses this to determine whether a finance-cost
        transaction needs an interest split (repayment) or is fully
        deductible (interest_only).
    """

    id: UUID
    label: str
    mortgage_type: str = "none"


class ConfirmedExample(BaseModel):
    """A previously confirmed categorisation, given to the agent as a few-shot example.

    :ivar description: the original statement line description.
    :ivar amount: the original statement line amount.
    :ivar hmrc_category: the confirmed category.
    :ivar property_id: the confirmed property attribution, if any.
    """

    description: str
    amount: Decimal
    hmrc_category: HmrcCategory
    property_id: UUID | None = None


class CategoriseState(BaseModel):
    """Typed state for :class:`CategoriseStatementFlow`.

    :ivar lines: parsed statement lines to categorise (flow input).
    :ivar properties: the org's properties -- the only valid non-``None``
        ``property_id`` values the agent may return (flow input).
    :ivar few_shot: up to :data:`_MAX_FEW_SHOT` most recent confirmed
        transactions, given to the agent as few-shot examples (flow
        input). Callers are responsible for truncating to the most recent
        50; the flow only enforces the cap, it does not select for you.
    :ivar proposals: the validated output, populated by the flow.
    """

    lines: list[StatementLineInput] = Field(default_factory=list)
    properties: list[OrgProperty] = Field(default_factory=list)
    few_shot: list[ConfirmedExample] = Field(default_factory=list)
    proposals: StatementProposals | None = None


def _load_categoriser_agent_config() -> dict[str, Any]:
    """Load the ``categoriser`` agent's role/goal/backstory from YAML.

    :raises FileNotFoundError: if the config file is missing.
    :raises KeyError: if the YAML has no top-level ``categoriser`` entry.
    """
    raw = yaml.safe_load(_AGENTS_YAML_PATH.read_text(encoding="utf-8"))
    return raw["categoriser"]


def _build_llm() -> LLM:
    """Build the categoriser's LLM strictly from environment configuration.

    :raises CategoriserConfigError: if ``CATEGORISER_MODEL`` is unset.
    """
    model = os.environ.get(_MODEL_ENV_VAR)
    if not model:
        raise CategoriserConfigError(_MODEL_ENV_VAR)
    base_url = os.environ.get(_OLLAMA_BASE_URL_ENV_VAR) or None
    return LLM(model=model, base_url=base_url)


def _sanitize_description(text: str) -> str:
    """Strip characters that could be used for prompt injection.

    Bank statement descriptions come from third parties (tenants,
    suppliers) who could craft payment references to manipulate the LLM
    into mis-categorising transactions. Since the output drives tax
    figures, this is an integrity attack on the ledger.

    Strips:
    - Double quotes (would break the ``description="..."`` format)
    - Newlines and carriage returns (would inject new lines into the prompt)
    - Control characters

    :param text: the raw bank description.
    :returns: a sanitised version safe to embed in an LLM prompt.
    """
    # Replace double quotes with single quotes to preserve meaning
    text = text.replace('"', "'")
    # Strip newlines and control characters
    text = text.replace("\n", " ").replace("\r", " ")
    # Collapse multiple spaces
    import re
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_prompt(
    lines: list[StatementLineInput],
    properties: list[OrgProperty],
    few_shot: list[ConfirmedExample],
) -> str:
    """Build the categorisation prompt.

    Contains: numbered statement lines, the property list with ids, a
    few-shot examples section (if any), and the explicit instruction set
    the plan requires (uncertainty -> low confidence, personal_non_business
    for non-business lines, never invent a property id, one proposal per
    line referencing a provided line_index).

    :param lines: statement lines to categorise, in the order they must be
        indexed (0-based) in the response.
    :param properties: the org's properties, the only valid property ids.
    :param few_shot: confirmed examples to steer the agent's judgement.
    """
    lines_block = "\n".join(
        f"{i}. date={line.date.isoformat()} "
        f'description="{_sanitize_description(line.description)}" amount={line.amount}'
        for i, line in enumerate(lines)
    )

    if properties:
        properties_block = "\n".join(
            f"- {p.id}: {p.label} (mortgage: {p.mortgage_type})"
            for p in properties
        )
    else:
        properties_block = "(no properties on file -- every property_id must be null)"

    if few_shot:
        few_shot_block = "\n".join(
            f'- description="{_sanitize_description(ex.description)}" amount={ex.amount} -> '
            f"category={ex.hmrc_category.value} property_id={ex.property_id}"
            for ex in few_shot
        )
    else:
        few_shot_block = "(none available)"

    categories_block = "\n".join(f"- {c.value}" for c in HmrcCategory)

    return f"""Categorise every statement line below into exactly one HMRC \
SA105 category and, where the evidence supports it, attribute it to one of \
the listed properties.

Statement lines (0-based index, date, description, amount; negative amount \
means money out):
{lines_block}

Properties on file (the ONLY valid property_id values -- never invent one; \
use null when a line has no clear property attribution):
{properties_block}

Previously confirmed categorisations for this org, for reference:
{few_shot_block}

The ONLY valid hmrc_category values:
{categories_block}

Rules:
- Return exactly one proposal per statement line above, referencing its \
line_index exactly as given -- do not skip, merge, or invent lines.
- If a line's category or property is genuinely ambiguous from the \
description, still choose your single best category but give it a LOW \
confidence score. Never invent certainty you do not have.
- Use the personal_non_business category for any line that is not clearly \
landlord income or an allowable/capital property expense -- do not force a \
personal line into a business category.
- property_id must be either null or exactly one of the ids listed above.
- For finance_costs_residential on a property with mortgage type \
"interest_only", the full payment is deductible interest.
- For finance_costs_residential on a property with mortgage type \
"repayment", only the interest portion is deductible. Categorise as \
finance_costs_residential but use LOW confidence (0.5) so the review \
screen flags it for manual interest splitting.
- For finance_costs_residential on a property with mortgage type "none", \
check whether this is actually a loan payment -- if no mortgage is on file, \
the payment may be personal_non_business.

Reply with raw JSON and nothing else. No ``` fence, no explanation before \
or after it, no markdown. Exactly this shape:
{{"proposals": [{{"line_index": 0, "hmrc_category": "repairs_maintenance", \
"property_id": null, "confidence": 0.9, "rationale": "why, in one short \
sentence"}}]}}
"""


#: A markdown code fence around the whole answer, with an optional language
#: tag. Anchored at both ends and applied to already-stripped text, so it can
#: only remove a wrapper -- it cannot pick a JSON-looking fragment out of a
#: reply that also contains commentary. That restraint is the point: a model
#: that says "here are the proposals, but I was unsure about line 3" is
#: telling us something, and quietly extracting the JSON would discard it.
_CODE_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*\s*\n(?P<body>.*?)\n?```\Z", re.DOTALL)


def _unwrap(raw: str) -> str:
    """Strip surrounding whitespace and one whole-answer markdown fence.

    The only tolerance this module extends to a model's output. See the
    module docstring for why it exists and where the line is drawn.

    :param raw: the model's reply, verbatim.
    :returns: the same text with padding and an enclosing fence removed.
    """
    stripped = raw.strip()
    match = _CODE_FENCE.match(stripped)
    return match.group("body").strip() if match else stripped


def _parse_proposals(raw: object) -> StatementProposals:
    """Read the model's reply into a validated :class:`StatementProposals`.

    :param raw: whatever the agent put on ``result.raw``.
    :raises CategorisationResultError: if the reply is empty, or is not JSON
        matching the contract. The message quotes the reply, because without
        ``response_format`` there is no SDK error naming the offending field
        and this is the only thing a human debugging a failed job will see.
    :returns: the parsed proposals, before the line/property contract check.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CategorisationResultError(
            f"agent returned no usable text (got {raw!r})"
        )
    body = _unwrap(raw)
    try:
        return StatementProposals.model_validate_json(body)
    except ValidationError as exc:
        # Truncated: a reply can be long, and the first 500 characters have
        # always been enough to see whether it is prose, a fence this regex
        # did not match, or genuinely malformed JSON.
        raise CategorisationResultError(
            f"agent did not return a structured StatementProposals output. "
            f"Reply was: {body[:500]!r} -- {exc}"
        ) from exc


def _validate_proposals(
    proposals: StatementProposals,
    lines: list[StatementLineInput],
    properties: list[OrgProperty],
) -> None:
    """Validate the agent's proposals against the input contract, loudly.

    :param proposals: the agent's (Pydantic-valid) proposal set.
    :param lines: the statement lines the proposals must map onto.
    :param properties: the org's properties the proposals' property_ids
        must be drawn from.
    :raises CategorisationResultError: on any contract violation --
        proposal count not matching the line count, a line_index out of
        range, a duplicate line_index, or a property_id not present in
        ``properties``.
    """
    if len(proposals.proposals) != len(lines):
        raise CategorisationResultError(
            f"expected {len(lines)} proposals (one per line), got "
            f"{len(proposals.proposals)}"
        )

    seen_indices: set[int] = set()
    valid_property_ids = {p.id for p in properties}
    for proposal in proposals.proposals:
        if not 0 <= proposal.line_index < len(lines):
            raise CategorisationResultError(
                f"proposal line_index {proposal.line_index} is out of range "
                f"for {len(lines)} lines"
            )
        if proposal.line_index in seen_indices:
            raise CategorisationResultError(
                f"duplicate proposal for line_index {proposal.line_index}"
            )
        seen_indices.add(proposal.line_index)

        if (
            proposal.property_id is not None
            and proposal.property_id not in valid_property_ids
        ):
            raise CategorisationResultError(
                f"proposal for line_index {proposal.line_index} references "
                f"property_id {proposal.property_id}, which is not in the "
                "provided property list"
            )


class CategoriseStatementFlow(Flow[CategoriseState]):
    """CrewAI Flow that categorises a parsed statement's lines via one LLM call.

    Two steps: :meth:`validate_inputs` fails loudly on missing/invalid
    configuration before any LLM call is made; :meth:`categorise` runs the
    categoriser agent and validates its output against the line/property
    contract.
    """

    @start()
    def validate_inputs(self) -> None:
        """Validate environment and input contract before calling the LLM.

        :raises CategoriserConfigError: if ``CATEGORISER_MODEL`` is unset.
        :raises CategorisationResultError: if more than
            :data:`_MAX_FEW_SHOT` few-shot examples were supplied.
        """
        if not os.environ.get(_MODEL_ENV_VAR):
            raise CategoriserConfigError(_MODEL_ENV_VAR)
        if len(self.state.few_shot) > _MAX_FEW_SHOT:
            raise CategorisationResultError(
                f"few_shot must be at most {_MAX_FEW_SHOT} examples, got "
                f"{len(self.state.few_shot)}"
            )

    @listen(validate_inputs)
    def categorise(self) -> None:
        """Run the categoriser agent and validate its output.

        **No ``response_format``** -- see the module docstring. The reply is
        read off ``result.raw`` and parsed here, which is why
        :func:`_build_prompt` has to state the JSON shape and list the
        categories: nothing else supplies them any more.

        :raises CategorisationResultError: if the reply is not parseable as
            :class:`StatementProposals`, or its proposals fail
            :func:`_validate_proposals`.
        """
        agent_config = _load_categoriser_agent_config()
        agent = Agent(config=agent_config, llm=_build_llm())

        prompt = _build_prompt(
            self.state.lines, self.state.properties, self.state.few_shot
        )
        result = agent.kickoff(prompt)

        proposals = _parse_proposals(result.raw)
        _validate_proposals(proposals, self.state.lines, self.state.properties)
        self.state.proposals = proposals
