"""Fill a form by hand: one box per field, the filled pages beside it."""
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import pymupdf

from pdflex import write_atomic
from pdflex.batch import fill_form
from pdflex.fields import _field_js, _fmt_from_js, sample_value
from pdflex.i18n import tr


@dataclass
class Input:
    name: str
    kind: str  # text, multiline, checkbox, choice
    choices: list[str] = dc_field(default_factory=list)
    value: str = ""
    hint: str = ""  # how a formatted value is written, e.g. "1.234,50"
    maxlen: int = 0  # boxes of a comb field (codice fiscale)


def form_inputs(doc: pymupdf.Document) -> list[Input]:
    """One input per field name, in page order. Totals (read only) and signatures are left out."""
    out: dict[str, Input] = {}
    for page in doc:
        for w in page.widgets():
            name, t = w.field_name or "", w.field_type
            if not name or w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY:
                continue
            if t == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:  # one entry for the group, a choice per button
                inp = out.setdefault(name, Input(name, "choice"))
                on = w.on_state()
                inp.choices.append(on)
                if w.field_value in (True, on):
                    inp.value = on
            elif name in out:
                continue  # the same field shown twice
            elif t == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                out[name] = Input(name, "checkbox", value="1" if w.field_value in (True, w.on_state(), "Yes") else "")
            elif t in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX):
                choices = [c if isinstance(c, str) else c[0] for c in (w.choice_values or [])]
                out[name] = Input(name, "choice", choices, w.field_value or "")
            elif t == pymupdf.PDF_WIDGET_TYPE_TEXT:
                multiline = bool(w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
                fmt = _fmt_from_js(_field_js(doc, w))
                comb = w.text_maxlen if w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB else 0
                out[name] = Input(name, "multiline" if multiline else "text", value=w.field_value or "",
                                  hint=sample_value(fmt, name) if fmt else "", maxlen=comb or 0)
    return list(out.values())


def fill_values(data: bytes, values: dict[str, str], flatten: bool = False) -> pymupdf.Document:
    """The form in `data` with these values; flatten: fields turned into plain text."""
    doc = pymupdf.open("pdf", data)
    fill_form(doc, values, {n: n for n in values})
    if flatten:
        doc.bake()
    return doc


# ---------------------------------------------------------------- UI

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from pdflex.ui import DocView, Toggle, button, info, warn  # noqa: E402


class FillDialog(QDialog):
    """The fields on the left, the filled form on the right, redrawn as you type."""

    def __init__(self, parent, data: bytes, src_path: str):
        super().__init__(parent)
        self.data, self.src = data, Path(src_path)
        doc = pymupdf.open("pdf", data)
        self.inputs = form_inputs(doc)
        doc.close()
        self.setWindowTitle(tr("Compila {0}").format(self.src.name))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint)
        self.resize(1200, 900)

        box = QWidget()
        form = QFormLayout(box)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setContentsMargins(16, 16, 16, 16)
        self.getters = {}
        self.timer = QTimer(self, singleShot=True, interval=300, timeout=self.redraw)
        for inp in self.inputs:
            form.addRow(inp.name, self._input(inp))
        scroll = QScrollArea()
        scroll.setWidget(box)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)
        self.view = DocView(annots=True)
        split = QSplitter()
        split.addWidget(scroll)
        split.addWidget(self.view)
        split.setSizes([360, 840])
        split.setChildrenCollapsible(False)

        self.flatten = Toggle(tr("Non modificabile (i campi diventano testo)"))
        b_cancel = button(tr("Annulla"))
        b_save = button(tr("Salva compilato"), "save", "Primary")
        b_cancel.clicked.connect(self.reject)
        b_save.clicked.connect(self.ask_save)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(16, 10, 16, 12)
        bottom.addWidget(self.flatten)
        bottom.addStretch()
        bottom.addWidget(b_cancel)
        bottom.addWidget(b_save)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        self.redraw()

    def _input(self, inp: Input) -> QWidget:
        if inp.kind == "checkbox":
            w = Toggle(tr("Sì"))
            w.setChecked(inp.value == "1")
            w.toggled.connect(self.timer.start)
            self.getters[inp.name] = lambda: "1" if w.isChecked() else ""
        elif inp.kind == "choice":
            w = QComboBox()
            w.addItem("(nessuna)", "")
            for c in inp.choices:
                w.addItem(c, c)
            w.setCurrentIndex(max(w.findData(inp.value), 0))
            w.currentIndexChanged.connect(self.timer.start)
            self.getters[inp.name] = w.currentData
        elif inp.kind == "multiline":
            w = QPlainTextEdit(inp.value)
            w.setFixedHeight(80)
            w.textChanged.connect(self.timer.start)
            self.getters[inp.name] = w.toPlainText
        else:
            w = QLineEdit(inp.value)
            if inp.hint:
                w.setPlaceholderText(f"es. {inp.hint}")
            if inp.maxlen:
                w.setMaxLength(inp.maxlen)
            w.textChanged.connect(self.timer.start)
            self.getters[inp.name] = w.text
        return w

    def values(self) -> dict[str, str]:
        return {name: get() for name, get in self.getters.items()}

    def redraw(self):
        old = self.view.doc
        self.view.show_doc(fill_values(self.data, self.values()))
        if old:
            old.close()

    def save(self, path: str) -> None:
        doc = fill_values(self.data, self.values(), self.flatten.isChecked())
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
        write_atomic(path, data)

    def ask_save(self):
        stem = self.src.stem.removesuffix("_modulo")
        path, _ = QFileDialog.getSaveFileName(self, tr("Salva il modulo compilato"),
                                              str(self.src.with_name(stem + "_compilato.pdf")), tr("PDF (*.pdf)"))
        if not path:
            return
        if Path(path).resolve() == self.src.resolve():
            warn(self, tr("Scegli un altro nome: questo è il modulo aperto in PDFlex, che resta vuoto."))
            return
        try:
            self.save(path)
        except (ValueError, OSError, RuntimeError) as e:
            warn(self, tr("Salvataggio non riuscito: {0}").format(e))
            return
        info(self, tr("Salvato in {0}").format(path))
        self.accept()

    def done(self, r):
        doc, self.view.doc = self.view.doc, None  # no late re-render of a closed document
        if doc:
            doc.close()
        super().done(r)
