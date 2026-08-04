"""Tests for ``src.core.export_html`` -- the human-readable face of a pack.

The PDF an accountant opens and the CSVs HMRC's figures come from must be
the *same* numbers. That is the whole risk in this module: a rendering that
quietly reformats, rounds, or omits a line would produce a document that
disagrees with the return it accompanies, and nothing downstream would
notice.

So the numbers are pinned by exact string comparison against
:func:`~src.core.export_html.render_pack_main`, which is separated from
:func:`~src.core.export_html.render_pack_html` for exactly this reason: the
surrounding document is chrome, and a stylesheet tweak must not be able to
break -- or worse, silently rewrite the expectation of -- a test about
money.
"""

import uuid
from decimal import Decimal

from src.core.categories import HmrcCategory
from src.core.export_html import render_pack_html, render_pack_main
from src.core.export_pack import ExportEntity, QuarterlyPack, SimplePnlPack

ENTITY = uuid.UUID("11111111-1111-1111-1111-111111111111")


def pack(
    *,
    kind: type[QuarterlyPack] | type[SimplePnlPack] = QuarterlyPack,
    name: str = "Mahmudul Hoque",
    regime: str = "mtd_itsa",
    totals: dict[HmrcCategory, Decimal] | None = None,
) -> QuarterlyPack | SimplePnlPack:
    """Build a pack to render.

    :param kind: which pack class to build.
    :param name: the entity's name, as it appears in the heading.
    :param regime: the entity's tax regime.
    :param totals: category totals; a rent + repairs pair by default.
    :returns: the constructed pack.
    """
    return kind(
        entity=ExportEntity(id=ENTITY, name=name, tax_regime=regime),
        tax_year=2026,
        quarter=2,
        totals=totals
        if totals is not None
        else {
            HmrcCategory.RENT_INCOME: Decimal("2400.00"),
            HmrcCategory.REPAIRS_MAINTENANCE: Decimal("315.50"),
        },
        category_csv="",
        property_csv="",
    )


def test_the_rendered_figures_are_exactly_the_packs_figures() -> None:
    """Snapshot the numbers, not the styling.

    Whole-document equality would break on a CSS edit, and a test that
    breaks for cosmetic reasons gets its expectation pasted over from the
    actual output -- which is how a snapshot stops guarding anything.
    """
    assert render_pack_main(pack(), version=1) == (
        "<main>\n"
        "<h1>MTD ITSA quarterly update</h1>\n"
        "<p>Mahmudul Hoque &middot; 2026-27 &middot; Q2 &middot; version 1</p>\n"
        "<table>\n"
        "<thead><tr><th>Category</th><th>Cumulative total</th></tr></thead>\n"
        "<tbody>\n"
        '<tr><td>Rent income</td><td class="num">2400.00</td></tr>\n'
        '<tr><td>Repairs maintenance</td><td class="num">315.50</td></tr>\n'
        "</tbody>\n"
        "</table>\n"
        "</main>"
    )


def test_a_company_pack_is_titled_as_a_p_and_l_with_no_version() -> None:
    """A Ltd entity has no MTD obligation, so no filed version to quote."""
    rendered = render_pack_main(
        pack(kind=SimplePnlPack, name="Sample Properties Ltd", regime="corporation_tax"),
        version=None,
    )

    assert "<h1>Profit and loss summary</h1>" in rendered
    assert "version" not in rendered
    assert "<p>Sample Properties Ltd &middot; 2026-27 &middot; Q2</p>" in rendered


def test_a_negative_total_keeps_its_sign() -> None:
    """A refund that takes a category negative must read as negative.

    Rendering the magnitude would turn "the contractor paid us back more
    than we spent" into an expense claim.
    """
    rendered = render_pack_main(
        pack(totals={HmrcCategory.REPAIRS_MAINTENANCE: Decimal("-84.99")}), version=1
    )

    assert '<td class="num">-84.99</td>' in rendered


def test_an_entity_name_cannot_inject_markup() -> None:
    """Entity names are user input and go through escaping.

    An unescaped ``&`` alone is enough to make the document invalid, and a
    ``<`` would let a name silently restructure the page.
    """
    rendered = render_pack_main(pack(name="Hoque & Sons <Ltd>"), version=1)

    assert "Hoque &amp; Sons &lt;Ltd&gt;" in rendered
    assert "<Ltd>" not in rendered


def test_the_document_wraps_the_same_figures() -> None:
    """The full document is the rendered figures plus chrome, nothing else."""
    document = render_pack_html(pack(), version=1)

    assert document.startswith("<!DOCTYPE html>")
    assert render_pack_main(pack(), version=1) in document
    assert "<title>" in document


def test_rendering_is_deterministic() -> None:
    """No timestamps, no ids: the same pack renders byte-identically.

    A document that embeds the moment it was made cannot be diffed against
    the previous version to answer "did the figures change?".
    """
    assert render_pack_html(pack(), version=1) == render_pack_html(pack(), version=1)
