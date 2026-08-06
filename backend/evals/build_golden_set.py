"""Build a golden set from confirmed transactions in the local database.

**The output is deliberately not committed.** A golden set carries bank
descriptions and property labels verbatim -- counterparty names, references,
addresses -- and this repository is public. ``evals/golden_set.jsonl`` stays
synthetic so the harness has something to run against out of the box; the real
one is written to ``evals/golden_set.real.jsonl``, which ``.gitignore``
excludes. This script is the committed part, because a script that reads a
local database contains no portfolio data at all.

**One row per distinct description, not per transaction.** A statement holds
the same rent credit nine times and the same direct debit nine times, so
scoring every row would let one correct answer bank nine marks and report an
accuracy that means nothing. Each entry records how many transactions it
stands for, so the skew stays visible rather than being averaged away.

Only ``confirmed`` transactions are used: a human decided them, which is the
whole definition of ground truth here. ``proposed`` rows are the agent's own
opinion, and grading a model against its own past answers measures nothing.

**``--org`` is required, and that is not bureaucracy.** A local database ends
up holding demo and scratch orgs whose categories were chosen by a seeding
script rather than by a person. Sweeping those in produces a benchmark that
looks four times larger and grades the model against invented answers. The
first run of this script did exactly that -- 22 entries, of which 16 were
fictional.

Run it from ``backend`` with the local stack up::

    uv run --env-file ../.env python evals/build_golden_set.py
    uv run --env-file ../.env python evals/run_eval.py --golden evals/golden_set.real.jsonl

:seealso: backend/evals/run_eval.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

DEFAULT_OUT = Path(__file__).parent / "golden_set.real.jsonl"

QUERY = """
select
    t.description,
    min(t.date)::text                                as first_seen,
    to_char(min(t.amount), 'FM999999990.00')         as amount,
    t.hmrc_category::text                            as expected_category,
    max(p.address_line1 || ', ' || p.city)           as property_label,
    count(*)                                         as occurrences
from transactions t
left join properties p on p.id = t.property_id
join orgs o on o.id = t.org_id
where t.status = 'confirmed'
  and t.hmrc_category is not null
  and o.name = $1
group by t.description, t.hmrc_category
order by count(*) desc, t.description
"""


async def list_orgs() -> list[tuple[str, int]]:
    """Every org with confirmed transactions, and how many distinct ones."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        rows = await conn.fetch(
            "select o.name, count(distinct t.description) as n "
            "from transactions t join orgs o on o.id = t.org_id "
            "where t.status = 'confirmed' and t.hmrc_category is not null "
            "group by o.name order by 1"
        )
    finally:
        await conn.close()
    return [(r["name"], r["n"]) for r in rows]


async def build(out: Path, org: str) -> int:
    """Write one golden-set line per distinct confirmed description.

    :param out: where to write the JSONL.
    :param org: the org whose confirmed lines are ground truth.
    :returns: the number of entries written.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is not set. Run with `uv run --env-file ../.env ...`.")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(QUERY, org)
    finally:
        await conn.close()

    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "date": row["first_seen"],
                        "description": row["description"],
                        "amount": row["amount"],
                        "expected_category": row["expected_category"],
                        "property_label": row["property_label"],
                        # Not read by the harness. Here so a human reading the
                        # file can see that "one line" may stand for nine
                        # identical direct debits, and weight the score
                        # accordingly.
                        "_occurrences": row["occurrences"],
                    }
                )
                + "\n"
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--org",
        help="org whose confirmed lines are ground truth. Required: a local "
        "database also holds demo orgs whose categories a script invented.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"where to write (default: {DEFAULT_OUT.name}, which is gitignored)",
    )
    args = parser.parse_args()

    if not args.org:
        print("--org is required. Orgs with confirmed transactions:\n")
        for name, n in asyncio.run(list_orgs()):
            print(f"  {n:>4} distinct descriptions   {name}")
        sys.exit("\nPick the one whose categories a human actually decided.")

    written = asyncio.run(build(args.out, args.org))
    print(f"wrote {written} entries to {args.out}")
    if written < 30:
        print(
            f"\n  {written} distinct descriptions is a small benchmark. It covers "
            "whatever\n  accounts have been imported and confirmed so far; importing "
            "more banks\n  is what makes this number mean something."
        )


if __name__ == "__main__":
    main()
