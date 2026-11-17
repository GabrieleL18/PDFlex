import os
from pathlib import Path

import pymupdf


def open_pdf(path: str | Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Impossibile aprire il PDF: {e}") from e
    if doc.needs_pass:
        raise ValueError("Il PDF è protetto da password.")
    if doc.page_count == 0:
        raise ValueError("Impossibile aprire il PDF: nessuna pagina.")
    return doc


def write_atomic(path: str | Path, data: bytes) -> None:
    """Write next to the target, then swap: if writing fails half way, the old file is untouched."""
    out = Path(path)
    tmp = out.with_name(out.name + ".pdflex-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
