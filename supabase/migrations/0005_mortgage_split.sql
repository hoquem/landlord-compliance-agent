-- Separating the interest from the capital in a mortgage payment.
--
-- **Why this exists.** A repayment mortgage arrives as one direct debit. Only
-- the *interest* is an allowable finance cost; the capital portion repays a
-- loan and is not deductible at all. The bank line does not distinguish them,
-- the parser cannot, and until this migration nothing downstream asked -- so
-- the whole payment was counted as an expense, overstating costs and
-- understating profit on a real return.
--
-- Found 2026-08-06 on Mahmud's own first real quarter. That account happened
-- to be interest-only so its figures were right; others are not.
--
-- Two columns, and the pairing is the point:
--
--   properties.mortgage_type  -- says a payment on this property NEEDS splitting
--   transactions.allowable_amount -- says what the split IS
--
-- Neither alone is enough. The flag without the amount is a refusal (see
-- `assert_mortgage_interest_separated`); the amount without the flag is
-- allowed, because a user who supplies a figure has clearly thought about it.

-- Deliberately three values rather than a boolean. `none` and `interest_only`
-- behave identically today, but they say different things -- "no borrowing" is
-- not "borrowing where the whole payment is interest" -- and a boolean would
-- force one of them to be a lie.
create type mortgage_type as enum (
  'none',
  'interest_only',
  'repayment'
);

-- Defaults to `none` so this migration changes no existing figure. A property
-- opts in, and only then does anything start refusing.
alter table public.properties
  add column mortgage_type mortgage_type not null default 'none';

comment on column public.properties.mortgage_type is
  'Whether payments on this property''s borrowing need an interest/capital '
  'split. `repayment` makes transactions.allowable_amount required on finance '
  'costs before the period can be exported.';

-- The portion of `amount` that is an allowable expense. NULL means "all of
-- it", which is the ordinary case and keeps every existing row correct.
--
-- A magnitude, exactly like `amount`, and signed by the same category-aware
-- rule downstream -- so a refund against a finance cost still reduces the
-- expense rather than becoming income. See core/quarters.py's TxnForTotals.
alter table public.transactions
  add column allowable_amount numeric(12, 2)
    check (allowable_amount is null or (allowable_amount >= 0 and allowable_amount <= amount));

comment on column public.transactions.allowable_amount is
  'Portion of `amount` that is allowable, or NULL for all of it. Set on a '
  'repayment-mortgage payment to record the interest; the remainder is capital '
  'and is not deductible. Apportioned by ownership like any other figure.';
