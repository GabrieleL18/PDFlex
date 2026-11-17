"""Fill a PDF form once per row of an Excel/CSV table."""
import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

import pymupdf

from pdflex import open_pdf
from pdflex.editor import clean_value

TRUE_WORDS = {"1", "x", "s", "si", "sì", "yes", "y", "true", "vero", "ok"}


def _key(s: str) -> str:
    return clean_value(str(s).lower()).strip("_")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return f"{v:%d/%m/%Y}"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v).replace(".", ",")
    return str(v).strip()


def read_table(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    """Headers and non-empty rows of a .csv (any of ; , tab) or of the first sheet of an .xlsx."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = [[_cell(v) for v in r] for r in wb.worksheets[0].iter_rows(values_only=True)]
        wb.close()
    else:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")  # what Excel writes for "CSV" on Italian Windows
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
    if not rows:
        raise ValueError("Il file non contiene dati.")
    headers = [h or f"Colonna {i + 1}" for i, h in enumerate(rows[0])]
    data = [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in rows[1:] if any(r)]
    return headers, data


def match_columns(field_names: list[str], headers: list[str]) -> dict[str, str]:
    """Field name -> column with the same name (ignoring case, spaces, accents)."""
    by_key = {_key(h): h for h in headers}
    return {n: by_key[_key(n)] for n in field_names if _key(n) in by_key}


def _fill(doc, row: dict, mapping: dict) -> None:
    for page in doc:
        for w in page.widgets():
            col = mapping.get(w.field_name)
            if col is None:
                continue
            value = row.get(col, "")
            t = w.field_type
            if t == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                w.field_value = value.strip().lower() in TRUE_WORDS
            elif t == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
                on = w.on_state()
                w.field_value = on if value and clean_value(value) == on else False
            else:
                w.field_value = value
            w.update()


def _file_name(row, name_col, i, stem) -> str:
    raw = row.get(name_col, "") if name_col else ""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(" .")[:120]
    return name or f"{stem}_{i:03}"


def batch_fill(template: str | Path, rows: list[dict], mapping: dict[str, str], out_dir: str | Path,
               name_col: str | None = None, flatten: bool = False, progress=None) -> list[Path]:
    """One filled PDF per row. flatten=True turns fields into plain text (no longer editable)."""
    template = Path(template)
    data = open_pdf(template).tobytes()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made, used = [], set()
    for i, row in enumerate(rows, 1):
        doc = pymupdf.open("pdf", data)
        _fill(doc, row, mapping)
        if flatten:
            doc.bake()
        base = _file_name(row, name_col, i, template.stem)
        name, n = base, 2
        while name.lower() in used or (out_dir / f"{name}.pdf").exists():
            name, n = f"{base}_{n}", n + 1
        used.add(name.lower())
        path = out_dir / f"{name}.pdf"
        doc.save(path, garbage=3, deflate=True)
        doc.close()
        made.append(path)
        if progress and not progress(i, len(rows)):
            break
    return made
