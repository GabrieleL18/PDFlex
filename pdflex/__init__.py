import os
from pathlib import Path

import pymupdf

__version__ = "1.3"  # also the GitHub release tag (v1.3): the update check compares the two
from pdflex.i18n import tr


def open_pdf(path: str | Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as e:
        raise ValueError(tr("Impossibile aprire il PDF: {0}").format(e)) from e
    if doc.needs_pass:
        raise ValueError(tr("Il PDF è protetto da password."))
    if doc.page_count == 0:
        raise ValueError(tr("Impossibile aprire il PDF: nessuna pagina."))
    return doc


def write_atomic(path: str | Path, data: bytes) -> None:
    """Write next to the target, then swap: if writing fails half way, the old file is untouched."""
    out = Path(path)
    tmp = out.with_name(out.name + ".pdflex-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
