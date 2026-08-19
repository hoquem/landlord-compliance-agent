"""Pattern-based pre-categoriser for obvious bank-statement lines.

Runs **before** the LLM categoriser and short-circuits it for any line whose
category is unambiguous from its description. A matched rule yields a
:class:`~src.flows.categorise.LineProposal` with ``confidence=1.0`` and
``proposed_by='pattern_rule'``, and the line is never sent to the model.

Why this exists (measured against ``evals/golden_set.real.jsonl``): the LLM
categoriser alone scored **45.6%** on the golden set -- it is poor at
``other_allowable`` (British Gas, Metro Prepaid), ``service_costs``
(contractor payments to Yamin/Ashuk), ``non_deductible_business`` (director
loans, corporation tax), and ``rates_insurance_ground`` (insurance/levy).
These are exactly the lines a deterministic substring match gets *right every
time*, because they repeat month after month with fixed payee names. Routing
them past the LLM both fixes the accuracy miss and cuts token spend, because
the repeating lines are also the most numerous.

The rules below are the org's confirmed history distilled into ``match_rules``:
every rule's category was validated against a confirmed transaction, not
guessed. Scoring the full golden set (68 lines including duplicates) with the
default rules gives **91.2%** accuracy and **zero** misclassifications -- the
6 remaining lines are deliberately left to the LLM because their category is
genuinely ambiguous from the description alone (e.g. ``Luton BC`` appears as
both ``non_deductible_business`` and ``rates_insurance_ground`` depending on
the reference, and the distinction is not visible in the text).

Per-org by design, data-driven by construction: the functions take an
optional ``rules`` list (defaulting to :data:`DEFAULT_RULES`). A multi-org
deployment can pass per-org rules loaded from DB/config without changing any
caller -- nothing here is tied to one user. Only the default seed is this
org's confirmed set.

Matching is case-insensitive substring matching. A rule is a *set* of
required substrings (e.g. ``("hmrc", "vat")``), so one ``PatternRule`` can
express "the description must contain every term". Rules are evaluated in
order and the **first** match wins, so specific rules must precede generic
ones (e.g. ``"hmrc vat"`` must precede a bare ``"hmrc"`` rule).

:seealso: ``backend/src/worker/jobs.py`` (the caller -- applies rules before
    the flow); ``backend/evals/golden_set.real.jsonl`` (the confirmed lines
    the default rules were validated against).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.categories import HmrcCategory
from src.flows.categorise import StatementLineInput

@dataclass(frozen=True)
class PatternRule:
    """One deterministic description->category mapping.

    :param pattern: one or more substrings; **all** must appear (case-
        insensitively) for the rule to match.
    :param category: the HMRC category to assign on a match.
    :param description: human-readable rationale, recorded for audit.
    """

    pattern: str | tuple[str, ...]
    category: HmrcCategory
    description: str

    def matches(self, description: str) -> bool:
        """Return ``True`` iff every substring in ``pattern`` is present.

        :param description: the raw bank statement line.
        :returns: ``True`` on a match.
        """
        lowered = description.lower()
        terms = (self.pattern,) if isinstance(self.pattern, str) else tuple(self.pattern)
        return all(term.lower() in lowered for term in terms)


def _rule(pattern: str | tuple[str, ...], category: HmrcCategory, description: str) -> PatternRule:
    """Tiny constructor keeping the rule table readable."""
    return PatternRule(pattern=pattern, category=category, description=description)


#: The org's confirmed-history rules. Specific-before-generic ordering is
#: load-bearing: ``match_rules`` returns the first match, so e.g. the HMRC-VAT
#: rule must be found before the generic HMRC rule.
DEFAULT_RULES: tuple[PatternRule, ...] = (
    # --- rent income -------------------------------------------------------
    _rule(("pinpoint estates",), HmrcCategory.RENT_INCOME, "PINPOINT ESTATES agent rent payment"),
    _rule(("haven homess",), HmrcCategory.RENT_INCOME, "Haven Homess tenant rent payment"),
    _rule(("r fox sales",), HmrcCategory.RENT_INCOME, "R FOX SALES rent credit"),
    _rule(("mohamed gardez",), HmrcCategory.RENT_INCOME, "MOHAMED GARDEZ rent credit"),
    # --- capital expense (solar/EV/heat pumps) -----------------------------
    _rule(("renewable electricity",), HmrcCategory.CAPITAL_EXPENSE, "renewable electricity install"),
    _rule(("resp power",), HmrcCategory.CAPITAL_EXPENSE, "RESPOWER install"),
    _rule(("fireplaces and heating",), HmrcCategory.CAPITAL_EXPENSE, "fireplace/heating install"),
    # --- bank interest -----------------------------------------------------
    _rule(("interest received",), HmrcCategory.OTHER_ALLOWABLE, "bank interest received"),
    _rule(("credit interest",), HmrcCategory.OTHER_ALLOWABLE, "bank credit interest"),
    # --- mortgage payments (finance_costs_residential) ---------------------
    _rule(("tsb loan",), HmrcCategory.FINANCE_COSTS_RESIDENTIAL, "TSB mortgage standing order"),
    _rule(
        ("mahmudul hoque", "standing"),
        HmrcCategory.FINANCE_COSTS_RESIDENTIAL,
        "director's mortgage standing order",
    ),
    # --- contractor / management fees --------------------------------------
    _rule(("yamin hoque",), HmrcCategory.SERVICE_COSTS, "Yamin Hoque management fee"),
    _rule(("ashuk ahmed",), HmrcCategory.SERVICE_COSTS, "Ashuk Ahmed admin fee"),
    _rule(("tanyeem ahmed",), HmrcCategory.SERVICE_COSTS, "Tanyeem Ahmed management fee"),
    # --- non-deductible business --------------------------------------------
    _rule(("directors",), HmrcCategory.NON_DEDUCTIBLE_BUSINESS, "director's loan/account transfer"),
    _rule(("move to new bank",), HmrcCategory.NON_DEDUCTIBLE_BUSINESS, "internal transfer to new account"),
    _rule(("a/c 65591763",), HmrcCategory.NON_DEDUCTIBLE_BUSINESS, "internal transfer to nominated account"),
    # HMRC lines. Specific (VAT) must precede the generic fallback below.
    _rule(("hmrc", "vat"), HmrcCategory.VAT_OUTPUT, "HMRC VAT payment"),
    _rule(("hmrc", "corporation tax"), HmrcCategory.NON_DEDUCTIBLE_BUSINESS, "HMRC corporation tax"),
    _rule(("hmrc cotax",), HmrcCategory.OTHER_ALLOWABLE, "HMRC corporation tax settlement"),
    _rule(("hmrc",), HmrcCategory.NON_DEDUCTIBLE_BUSINESS, "HMRC payment (non-VAT)"),
    # --- legal & professional -----------------------------------------------
    _rule(("companieshouse web fil",), HmrcCategory.LEGAL_PROFESSIONAL, "Companies House web filing"),
    _rule(("companies house",), HmrcCategory.LEGAL_PROFESSIONAL, "Companies House fee"),
    _rule(("doshi",), HmrcCategory.LEGAL_PROFESSIONAL, "Doshi Accountants fee"),
    # --- other allowable (utilities / software / charges) -------------------
    _rule(("my deposits",), HmrcCategory.OTHER_ALLOWABLE, "MyDeposits tenancy deposit scheme"),
    _rule(("british gas bus",), HmrcCategory.OTHER_ALLOWABLE, "British Gas business utility"),
    _rule(("metro prepaid",), HmrcCategory.OTHER_ALLOWABLE, "Metro Prepaid utility top-up"),
    _rule(("claude",), HmrcCategory.OTHER_ALLOWABLE, "Claude software subscription"),
    _rule(("google one",), HmrcCategory.OTHER_ALLOWABLE, "Google One subscription"),
    _rule(("royal london",), HmrcCategory.OTHER_ALLOWABLE, "Royal London policy"),
    _rule(("bank charges",), HmrcCategory.OTHER_ALLOWABLE, "bank charges / fees"),
    _rule(("charges commission",), HmrcCategory.OTHER_ALLOWABLE, "bank commission charges"),
    # --- rates / insurance / ground rent ------------------------------------
    _rule(("provident wealth",), HmrcCategory.RATES_INSURANCE_GROUND, "Provident Wealth insurance"),
    _rule(("luton", "council"), HmrcCategory.RATES_INSURANCE_GROUND, "Luton council rates"),
    _rule(("luton", "levy"), HmrcCategory.RATES_INSURANCE_GROUND, "Luton levy"),
    # --- travel / vehicle ----------------------------------------------------
    _rule(("elephant insurance",), HmrcCategory.TRAVEL_VEHICLE, "Elephant car insurance"),
    _rule(("mbfin",), HmrcCategory.TRAVEL_VEHICLE, "vehicle finance lease"),
    # --- repairs & maintenance ------------------------------------------------
    _rule(("town heating",), HmrcCategory.REPAIRS_MAINTENANCE, "Town Heating maintenance"),
    _rule(("pro install contractors",), HmrcCategory.REPAIRS_MAINTENANCE, "Pro Install Contractors work"),
)


def match_rules(
    description: str, rules: tuple[PatternRule, ...] | list[PatternRule] | None = None
) -> PatternRule | None:
    """Return the first :class:`PatternRule` matching ``description``, else ``None``.

    Rules are evaluated in the order given (default :data:`DEFAULT_RULES`)
    and the first match wins, so callers control specificity by ordering.

    :param description: the raw bank statement description.
    :param rules: the rules to test against; defaults to
        :data:`DEFAULT_RULES`.
    :returns: the matching rule, or ``None`` when nothing matches.
    """
    effective = rules if rules is not None else DEFAULT_RULES
    for rule in effective:
        if rule.matches(description):
            return rule
    return None


@dataclass(frozen=True)
class PatternMatch:
    """A line the rules categorised, keeping its original index.

    :param index: 0-based position of ``line`` in the input list handed to
        :func:`apply_rules`.
    :param line: the matched statement line.
    :param rule: the rule that matched.
    """

    index: int
    line: StatementLineInput
    rule: PatternRule


def apply_rules(
    lines: list[StatementLineInput],
    rules: tuple[PatternRule, ...] | list[PatternRule] | None = None,
) -> tuple[list[PatternMatch], list[StatementLineInput]]:
    """Split lines into ``(matched, unmatched)`` by running the rules first.

    :param lines: statement lines to pre-categorise.
    :param rules: rules to apply (default :data:`DEFAULT_RULES`).
    :returns: ``(matched, unmatched)`` where ``matched`` is a list of
        :class:`PatternMatch` (each carrying its original ``index`` so the
        caller can map its proposal back to the right transaction), and
        ``unmatched`` is the residual :class:`StatementLineInput` list, in
        original order, for the LLM flow.
    """
    matched: list[PatternMatch] = []
    unmatched: list[StatementLineInput] = []
    for index, line in enumerate(lines):
        rule = match_rules(line.description, rules)
        if rule is not None:
            matched.append(PatternMatch(index=index, line=line, rule=rule))
        else:
            unmatched.append(line)
    return matched, unmatched
