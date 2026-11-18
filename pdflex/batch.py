"""Fill a PDF form once per row of an Excel/CSV table."""
import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

import pymupdf

from pdflex import open_pdf, write_atomic
from pdflex.fields import clean_value

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


def fill_form(doc, row: dict, mapping: dict) -> None:
    """Field values from row[mapping[field name]]; check boxes from words like "sì", "x", "1"."""
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


def merged_fill(template: str | Path, rows: list[dict], mapping: dict[str, str], out_path: str | Path,
                progress=None) -> int:
    """All the filled forms one after the other in a single PDF, ready to print; returns how many.

    The fields become text: the same field name on every copy would otherwise share one value."""
    data = open_pdf(template).tobytes()
    out = pymupdf.open()
    for i, row in enumerate(rows, 1):
        doc = pymupdf.open("pdf", data)
        fill_form(doc, row, mapping)
        doc.bake()
        out.insert_pdf(doc)
        doc.close()
        if progress and not progress(i, len(rows)):
            return 0  # stopped: no half-made file
    write_atomic(out_path, out.tobytes(garbage=3, deflate=True))
    return len(rows)


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
        fill_form(doc, row, mapping)
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


# ---------------------------------------------------------------- UI

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QProgressDialog,
    QVBoxLayout, QWidget,
)

from pdflex import ui  # noqa: E402
from pdflex.ui import button, info, warn  # noqa: E402


class BatchTab(QWidget):
    """One form + one table = one filled PDF per row."""

    def __init__(self):
        super().__init__()
        self.template = None
        self.field_names: list[str] = []
        self.headers: list[str] = []
        self.rows: list[dict] = []
        self.mapping: dict[str, str] = {}

        bar = ui.Bar()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.addWidget(QLabel("Compila in serie", objectName="H2"))
        bl.addSpacing(10)
        bl.addWidget(QLabel("Un PDF compilato per ogni riga del foglio Excel o CSV", objectName="Muted"))
        bl.addStretch()

        self.drop_pdf = ui.DropZone("Modulo PDF", "Trascina qui il modulo con i campi o clicca per sceglierlo", "form")
        self.drop_table = ui.DropZone("Dati", "Trascina qui il file Excel o CSV o clicca per sceglierlo", "table",
                                      exts=(".xlsx", ".xlsm", ".csv"), file_filter="Excel o CSV (*.xlsx *.xlsm *.csv)")
        self.drop_pdf.picked.connect(self._guard(self.set_template))
        self.drop_table.picked.connect(self._guard(self.set_table))
        zones = QHBoxLayout()
        zones.setSpacing(24)
        zones.addWidget(self.drop_pdf)
        zones.addWidget(self.drop_table)

        self.match = QLabel("Le colonne vengono abbinate ai campi con lo stesso nome (maiuscole e spazi non contano).",
                            objectName="Muted")
        self.match.setWordWrap(True)
        self.name_col = QComboBox()
        self.flatten = ui.Toggle("Rendi i PDF non modificabili (i campi diventano testo)")
        self.merge = ui.Toggle("Un solo PDF con tutti i moduli, uno dopo l'altro (da stampare)")
        self.merge.setToolTip("I campi diventano testo: nello stesso PDF due campi con lo stesso nome "
                              "avrebbero lo stesso valore")
        self.merge.toggled.connect(self._update)
        self.b_run = button("Crea i PDF", "batch", "Primary")
        self.b_run.clicked.connect(self.ask_run)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nome dei file dalla colonna"))
        name_row.addWidget(self.name_col, 1)

        panel = QFrame(objectName="Panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(20, 16, 20, 16)
        pl.setSpacing(10)
        pl.addWidget(self.match)
        pl.addLayout(name_row)
        pl.addWidget(self.merge)
        pl.addWidget(self.flatten)
        pl.addWidget(self.b_run, alignment=Qt.AlignRight)

        body = QWidget()
        body.setObjectName("Desk")
        v = QVBoxLayout(body)
        v.setContentsMargins(60, 30, 60, 30)
        v.setSpacing(20)
        v.addLayout(zones)
        v.addWidget(panel)
        v.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        layout.addWidget(body, 1)
        self._update()

    def _guard(self, fn):
        def run(path):
            try:
                fn(path)
            except (ValueError, OSError) as e:
                warn(self, f"{Path(path).name}: {e}")
        return run

    def set_template(self, path: str):
        doc = open_pdf(path)
        names = list(dict.fromkeys(w.field_name for page in doc for w in page.widgets()
                                   if w.field_type not in (pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
                                                           pymupdf.PDF_WIDGET_TYPE_BUTTON)))
        if not names:
            raise ValueError("questo PDF non ha campi da compilare. Aggiungili prima nella sezione Moduli.")
        self.template, self.field_names = path, names
        self.drop_pdf.set_file(path)
        self._update()

    def set_table(self, path: str):
        self.headers, self.rows = read_table(path)
        self.drop_table.set_file(path)
        self.name_col.clear()
        self.name_col.addItem("(numerati: modulo_001, modulo_002...)", None)
        for h in self.headers:
            self.name_col.addItem(h, h)
        self._update()

    def _update(self):
        self.mapping = match_columns(self.field_names, self.headers) if self.template and self.headers else {}
        ready = bool(self.mapping and self.rows)
        if self.template and self.headers:
            missing = [n for n in self.field_names if n not in self.mapping]
            text = f"{len(self.mapping)} campi su {len(self.field_names)} trovati tra le colonne, {len(self.rows)} righe."
            if missing:
                text += " Restano vuoti: " + ", ".join(missing[:12]) + ("..." if len(missing) > 12 else "")
            if not self.mapping:
                text = ("Nessuna colonna ha il nome di un campo. Rinomina le colonne come i campi: "
                        + ", ".join(self.field_names[:12]))
            self.match.setText(text)
        merge = self.merge.isChecked()
        self.b_run.setText("Crea il PDF" if merge else f"Crea {len(self.rows)} PDF" if ready else "Crea i PDF")
        self.b_run.setEnabled(ready)
        self.name_col.setEnabled(bool(self.headers) and not merge)
        self.flatten.setEnabled(not merge)

    def ask_run(self):
        t = Path(self.template)
        if self.merge.isChecked():
            out, _ = QFileDialog.getSaveFileName(self, "Salva il PDF con tutti i moduli",
                                                 str(t.with_name(t.stem + "_tutti.pdf")), "PDF (*.pdf)")
        else:
            out = QFileDialog.getExistingDirectory(self, "Cartella dove salvare i PDF", str(t.parent))
        if not out:
            return
        try:
            made = self.run(out)
        except (ValueError, OSError, RuntimeError) as e:
            warn(self, f"Creazione non riuscita: {e}")
            return
        if self.merge.isChecked():
            if made:
                info(self, f"Salvato {out}: {len(self.rows)} moduli compilati, uno dopo l'altro.")
        else:
            info(self, f"Creati {len(made)} PDF in {out}")

    def run(self, out: str) -> list[Path]:
        """Make the PDFs into the folder `out`, or the single merged file `out`; returns the files made."""
        dlg = QProgressDialog("Creo i PDF...", "Ferma", 0, len(self.rows), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(400)

        def progress(i, n):
            dlg.setValue(i)
            QApplication.processEvents()
            return not dlg.wasCanceled()
        try:
            if self.merge.isChecked():
                return [Path(out)] if merged_fill(self.template, self.rows, self.mapping, out, progress) else []
            return batch_fill(self.template, self.rows, self.mapping, out, self.name_col.currentData(),
                              self.flatten.isChecked(), progress)
        finally:
            dlg.close()
