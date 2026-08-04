"""Rendering an export pack as HTML -- the document a human actually reads.

The CSVs carry the figures a return is built from; this is the same figures
in a form an accountant can open. **Same** is the requirement, and the only
real risk in the module: a rendering that reformats, re-rounds or drops a
line produces a document that disagrees with the return it accompanies, and
nothing downstream compares the two.

Pure and dependency-free, exactly like :mod:`src.core.export_pack`. Turning
this HTML into a PDF is :mod:`src.api.pdf`'s job, and lives there because
WeasyPrint drags in Pango and Cairo -- keeping that out of core is what lets
the figures be tested without them.

**Two functions rather than one, deliberately.**
:func:`render_pack_main` is the figures; :func:`render_pack_html` is the
figures plus a document around them. The split exists so the money can be
pinned by exact string comparison without a stylesheet edit breaking the
test -- a snapshot test that breaks for cosmetic reasons gets its
expectation pasted over from the actual output, at which point it guards
nothing.

**No timestamps and no ids in the output.** Rendering is deterministic, so
two versions of the same quarter can be diffed to answer "did the figures
change?". ``generated_at`` lives on the ``mtd_quarters`` row, where it can
be read without disturbing that.

:seealso: backend/src/core/export_pack.py (which builds the packs rendered
    here); backend/src/api/pdf.py (the WeasyPrint adapter).
"""

from __future__ import annotations

import html
from decimal import Decimal

from src.core.categories import HmrcCategory
from src.core.export_pack import QuarterlyPack, SimplePnlPack
from src.core.quarters import format_tax_year

#: Minimal print styling. Kept small and separate from the figures so that
#: editing it cannot touch what :func:`render_pack_main` is tested against.
_STYLE = """
body { font-family: sans-serif; font-size: 11pt; margin: 2cm; }
h1 { font-size: 16pt; margin-bottom: 0.2em; }
p { color: #555; margin-top: 0; }
table { border-collapse: collapse; width: 100%; margin-top: 1.5em; }
th, td { border-bottom: 1px solid #ddd; padding: 0.4em 0.6em; text-align: left; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
"""


def _category_label(category: HmrcCategory) -> str:
    """Render a category name for a human reader.

    Derived from the enum value rather than looked up in a label map: a map
    is a second list of categories, and the failure mode of a category
    added to one and not the other is a blank cell in a tax document.

    :param category: the category to name.
    :returns: e.g. ``"Rates insurance ground"``.
    """
    return category.value.replace("_", " ").capitalize()


def _row(category: HmrcCategory, total: Decimal) -> str:
    """Render one category's table row.

    :param category: the category.
    :param total: its cumulative total, signed -- a refund that takes a
        category negative must read as negative, since the magnitude alone
        would turn money returned into money spent.
    :returns: one ``<tr>`` element.
    """
    return (
        f"<tr><td>{html.escape(_category_label(category))}</td>"
        f'<td class="num">{total:.2f}</td></tr>'
    )


def render_pack_main(pack: QuarterlyPack | SimplePnlPack, *, version: int | None) -> str:
    """Render the pack's figures, and nothing else.

    Categories appear in the same order and with the same values as
    ``pack.category_csv``: both are ``sorted(pack.totals.items())``
    formatted to two places.

    :param pack: the pack to render.
    :param version: the ``mtd_quarters`` version this document accompanies,
        or ``None`` for a company pack -- a Ltd entity has no MTD
        obligation, so there is no filed version to quote.
    :returns: a ``<main>`` element containing the heading and figures.
    """
    title = (
        "Profit and loss summary"
        if isinstance(pack, SimplePnlPack)
        else "MTD ITSA quarterly update"
    )
    subtitle = [
        html.escape(pack.entity.name),
        format_tax_year(pack.tax_year),
        f"Q{pack.quarter}",
    ]
    if version is not None:
        subtitle.append(f"version {version}")

    rows = "\n".join(_row(category, total) for category, total in sorted(pack.totals.items()))
    return (
        "<main>\n"
        f"<h1>{title}</h1>\n"
        f"<p>{' &middot; '.join(subtitle)}</p>\n"
        "<table>\n"
        "<thead><tr><th>Category</th><th>Cumulative total</th></tr></thead>\n"
        f"<tbody>\n{rows}\n</tbody>\n"
        "</table>\n"
        "</main>"
    )


def render_pack_html(pack: QuarterlyPack | SimplePnlPack, *, version: int | None) -> str:
    """Render the pack as a complete HTML document, ready for PDF conversion.

    :param pack: the pack to render.
    :param version: the accompanying ``mtd_quarters`` version, or ``None``
        for a company pack.
    :returns: a full HTML document wrapping :func:`render_pack_main`.
    """
    heading = (
        "Profit and loss summary"
        if isinstance(pack, SimplePnlPack)
        else "MTD ITSA quarterly update"
    )
    title = f"{heading} -- {pack.entity.name}, {format_tax_year(pack.tax_year)} Q{pack.quarter}"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{render_pack_main(pack, version=version)}\n"
        "</body>\n"
        "</html>\n"
    )
