"""HTML to PDF, and nothing else.

One function over WeasyPrint. It lives here rather than in
:mod:`src.core.export_html` because WeasyPrint dlopens Pango, Cairo and
GObject -- keeping that out of core is what lets the export figures be
tested with no system libraries at all.

**The import is at module scope on purpose.** If the rendering stack is
missing, the API fails to start rather than accepting an export request and
failing halfway through, after the CSVs are already in the bucket and a
``documents`` row already exists. A missing dependency is a deployment
problem, and deployment is when it should surface.

**macOS needs ``DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib``.** Homebrew
installs libgobject/pango/cairo outside dyld's default search path, so the
import below raises ``OSError: cannot load library 'libgobject-2.0-0'``
without it. It is in ``.env`` and ``.env.example`` and reaches the process
through ``uv run --env-file ../.env``. Linux and Docker images need nothing.

:seealso: backend/src/core/export_html.py (what produces the HTML).
"""

from weasyprint import HTML


def render_pdf(html: str) -> bytes:
    """Render an HTML document to PDF bytes.

    :param html: a complete HTML document, e.g. from
        :func:`~src.core.export_html.render_pack_html`.
    :returns: the PDF file's bytes.
    """
    return HTML(string=html).write_pdf()
