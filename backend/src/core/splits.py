"""Penny-exact ownership split maths.

This is the tax-attribution primitive: per-entity export totals for
jointly-held properties derive *exclusively* from the
``property_ownership`` split (see PIM1035), so every penny of every
amount must land on exactly one owner and the shares must sum back to
the original amount with no rounding drift.

:seealso: spec -- HMRC PIM1035 joint ownership attribution;
    ``backend/src/api/routers/portfolio.py`` (``PUT
    /properties/{id}/ownership``), which independently enforces the same
    three rules :func:`_validate_shares` applies -- non-empty, every share
    above zero, summing to exactly 100 -- because it has to answer 422 and
    name the offending entities rather than raise
    :class:`InvalidOwnershipError`. The two are kept in step by
    ``test_an_api_accepted_ownership_set_is_usable_by_split_amount``, so
    tightening anything here without tightening there fails loudly.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from uuid import UUID

_PENNY = Decimal("0.01")
_HUNDRED = Decimal(100)


class InvalidOwnershipError(Exception):
    """Raised when a share map fails ownership validation.

    :ivar shares: the raw share map that was rejected.
    """

    def __init__(self, message: str, shares: dict[UUID, Decimal]) -> None:
        self.shares = shares
        super().__init__(message)


def split_amount(amount: Decimal, shares: dict[UUID, Decimal]) -> dict[UUID, Decimal]:
    """Split ``amount`` between owners according to ``shares``, penny-exact.

    Uses the largest-remainder (Hamilton) method: each owner's raw share
    is floored to the penny, then the pennies left over from flooring
    (``amount`` minus the sum of floored shares) are handed out one each
    to the owners with the largest fractional remainder. Ties in the
    remainder are broken deterministically by ascending owner UUID, so
    given two owners with an identical remainder the lower UUID receives
    the extra penny.

    Negative amounts (e.g. expenses) are handled by splitting the
    absolute value with the method above and then re-applying the
    original sign to every owner's share -- this keeps the tie-break and
    rounding behaviour identical for a debit and its matching credit.

    :param amount: total amount to split, at penny (2dp) precision. May
        be positive, negative, or zero.
    :param shares: mapping of owner id to ownership percentage (e.g.
        ``Decimal("50.00")`` for 50%). Must be non-empty, every
        percentage must be strictly greater than zero, and the
        percentages must sum to exactly ``Decimal("100")``.
    :returns: mapping of owner id to their penny-exact share. The values
        sum to exactly ``amount``.
    :raises InvalidOwnershipError: if ``shares`` is empty, contains a
        non-positive percentage, or the percentages do not sum to
        exactly 100.
    :raises ValueError: if ``amount`` has more than 2 decimal places --
        finer-grained amounts are out of scope (the DB column is 2dp)
        and silently truncating them would violate the penny-exact
        invariant, so this fails loudly instead.
    """
    if amount != amount.quantize(_PENNY):
        raise ValueError(f"amount must be at penny (2dp) precision, got {amount}")

    _validate_shares(shares)

    negative = amount < 0
    remaining_amount = -amount if negative else amount

    raw_shares = {
        owner: remaining_amount * pct / _HUNDRED for owner, pct in shares.items()
    }
    floor_shares = {
        owner: raw.quantize(_PENNY, rounding=ROUND_DOWN)
        for owner, raw in raw_shares.items()
    }
    remainders = {owner: raw_shares[owner] - floor_shares[owner] for owner in shares}

    leftover = remaining_amount - sum(floor_shares.values(), start=Decimal(0))
    pennies_to_distribute = int((leftover / _PENNY).to_integral_value())

    ranked_owners = sorted(shares, key=lambda owner: (-remainders[owner], owner))

    result = dict(floor_shares)
    for owner in ranked_owners[:pennies_to_distribute]:
        result[owner] += _PENNY

    if negative:
        result = {owner: -value for owner, value in result.items()}

    # Postcondition on the tax-attribution primitive: every downstream tax
    # figure trusts this sum. A future edit that breaks the invariant must
    # fail loudly here, not surface as a quiet penny drift in an export.
    allocated = sum(result.values(), start=Decimal(0))
    if allocated != amount:
        raise RuntimeError(
            f"split_amount invariant broken: allocated {allocated} != amount {amount}"
        )

    return result


def _validate_shares(shares: dict[UUID, Decimal]) -> None:
    """Validate a share map, raising :class:`InvalidOwnershipError` on failure.

    :param shares: mapping of owner id to ownership percentage.
    :raises InvalidOwnershipError: if ``shares`` is empty, contains a
        non-positive percentage, or the percentages do not sum to
        exactly 100.
    """
    if not shares:
        raise InvalidOwnershipError("ownership shares must not be empty", shares)

    for owner, pct in shares.items():
        if pct <= 0:
            raise InvalidOwnershipError(
                f"ownership percentage for {owner} must be greater than zero, got {pct}",
                shares,
            )

    total = sum(shares.values(), start=Decimal(0))
    if total != _HUNDRED:
        raise InvalidOwnershipError(
            f"ownership percentages must sum to 100, got {total}", shares
        )
