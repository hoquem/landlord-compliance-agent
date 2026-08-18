"""VAT split logic for VAT-registered entities.

When an entity is VAT-registered and an import is flagged as VAT-inclusive,
income transactions are auto-split into net + output VAT. The net amount
becomes the transaction's ``amount`` (used for tax computation), and the
VAT element is recorded in ``vat_amount`` with the original gross in
``gross_amount``.

Expense transactions from VAT-registered suppliers may also carry input VAT,
but detecting that from a bank statement alone is unreliable — the bank
description rarely says whether the supplier is VAT-registered. Input VAT
splitting is therefore a manual review action, not an auto-split.

:seealso: supabase/migrations/0007_vat_handling.sql
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


def split_vat(
    gross: Decimal,
    vat_rate: Decimal,
) -> tuple[Decimal, Decimal]:
    """Split a gross (VAT-inclusive) amount into (net, vat).

    Uses the standard UK formula: ``net = gross / (1 + rate/100)``.

    :param gross: the VAT-inclusive amount.
    :param vat_rate: the VAT rate as a percentage (20.00 for standard).
    :returns: ``(net, vat)`` where ``net + vat == gross``, each rounded
        to 2 decimal places using banker's rounding (ROUND_HALF_UP to
        match HMRC's penny-level convention).
    :raises ValueError: if gross or vat_rate is negative.
    """
    if gross < 0:
        raise ValueError(f"gross must be non-negative, got {gross}")
    if vat_rate < 0:
        raise ValueError(f"vat_rate must be non-negative, got {vat_rate}")
    if gross != gross.quantize(Decimal("0.01")):
        raise ValueError(
            f"gross must be at penny precision (max 2 decimal places), got {gross}"
        )

    divisor = Decimal(1) + vat_rate / Decimal(100)
    net = (gross / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    vat = gross - net
    return net, vat


@dataclass(frozen=True)
class VatSplitResult:
    """The result of splitting a transaction for VAT.

    :param amount: the net amount to store as the transaction's ``amount``.
    :param vat_amount: the VAT element.
    :param gross_amount: the original gross amount.
    """

    amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


def maybe_split_vat(
    amount: Decimal,
    direction: str,
    *,
    vat_registered: bool,
    vat_rate: Decimal,
    vat_inclusive: bool,
) -> VatSplitResult | None:
    """Conditionally split a transaction amount for VAT.

    Called after parsing, before inserting into the database. Returns
    ``None`` when no split applies — the caller keeps the original amount
    as-is.

    A split happens when **all** of:
    - The entity is VAT-registered
    - The import is flagged as VAT-inclusive
    - The direction is ``"in"`` (income — output VAT collected)

    Expense-side splitting (input VAT) is NOT automatic: bank statements
    don't indicate whether a supplier is VAT-registered, so auto-splitting
    expenses would create false VAT claims. Input VAT is a manual review
    action.

    :param amount: the parsed amount (positive magnitude).
    :param direction: ``"in"`` or ``"out"``.
    :param vat_registered: whether the owning entity is VAT-registered.
    :param vat_rate: the entity's VAT rate as a percentage.
    :param vat_inclusive: whether this import was flagged as VAT-inclusive.
    :returns: a :class:`VatSplitResult` with net/vat/gross, or ``None``
        if no split applies.
    """
    if not vat_registered or not vat_inclusive:
        return None

    if direction != "in":
        return None

    net, vat = split_vat(amount, vat_rate)
    return VatSplitResult(
        amount=net,
        vat_amount=vat,
        gross_amount=amount,
    )