import os
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from pdflex import open_pdf

RED = (1, 0, 0)
GREEN = (0, 0.7, 0)
OPACITY = 0.3
FONT = "C:/Windows/Fonts/arial.ttf"


@dataclass
class Word:
    page: int
    rect: tuple[float, float, float, float]
    text: str


@dataclass
class Change:
    kind: str  # "delete" | "insert" | "replace"
    old: list[Word]
    new: list[Word]
    old_at: Word | None
    new_at: Word | None

    def label(self) -> str:
        def short(words):
            s = " ".join(w.text for w in words)
            return s if len(s) <= 60 else s[:59] + "…"
        if self.kind == "delete":
            return f"Rimosso (p. {self.old[0].page + 1}): {short(self.old)}"
        if self.kind == "insert":
            return f"Aggiunto (p. {self.new[0].page + 1}): {short(self.new)}"
        return f"Modificato (p. {self.old[0].page + 1}): {short(self.old)} → {short(self.new)}"


def ocr_available() -> bool:
    """OCR needs Tesseract installed on the PC (with Italian language data)."""
    try:
        return bool(pymupdf.get_tessdata())
    except Exception:
        return False


def extract_words(doc: pymupdf.Document, ocr: bool = False) -> list[Word]:
    out = []
    for page in doc:
        words = page.get_text("words")
        if not words and ocr and ocr_available():  # a scanned page: read the image
            tp = page.get_textpage_ocr(language="ita+eng", dpi=300, full=True)
            words = page.get_text("words", textpage=tp)
        # get_text gives unrotated coordinates; turn them into what the reader sees
        out += [Word(page.number, tuple(pymupdf.Rect(w[:4]) * page.rotation_matrix), w[4]) for w in words]
    return out


def diff_words(old: list[Word], new: list[Word], ignore_case: bool = False,
               ignore_punct: bool = False) -> list[Change]:
    def key(w):
        t = w.text.lower() if ignore_case else w.text
        return re.sub(r"[^\w]", "", t) if ignore_punct else t
    if ignore_punct:  # a lone "-" or "," is not a word worth reporting
        old = [w for w in old if key(w)]
        new = [w for w in new if key(w)]
    a = [key(w) for w in old]
    b = [key(w) for w in new]
    # Trim the common head/tail first: typical revisions touch a few spots, and
    # SequenceMatcher is roughly quadratic on what remains.
    # ponytail: a 100+ page contract rewritten everywhere can still take seconds; move the diff to a thread if that bites.
    lo = 0
    while lo < min(len(a), len(b)) and a[lo] == b[lo]:
        lo += 1
    hi = 0
    while hi < min(len(a), len(b)) - lo and a[-1 - hi] == b[-1 - hi]:
        hi += 1
    sm = SequenceMatcher(None, a[lo:len(a) - hi], b[lo:len(b) - hi], autojunk=False)
    changes = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        i1, i2, j1, j2 = i1 + lo, i2 + lo, j1 + lo, j2 + lo
        old_at = old[min(i1, len(old) - 1)] if old else None
        new_at = new[min(j1, len(new) - 1)] if new else None
        changes.append(Change(tag, old[i1:i2], new[j1:j2], old_at, new_at))
    return changes


def _summary_pages(out, old_path, new_path, changes):
    font = {"fontname": "arial", "fontfile": FONT} if os.path.exists(FONT) else {"fontname": "helv"}
    lines = [
        ("Confronto PDF", 16),
        (f"Originale: {Path(old_path).name}", 10),
        (f"Nuova versione: {Path(new_path).name}", 10),
        (f"Data: {datetime.now():%d/%m/%Y %H:%M}", 10),
        (f"Modifiche: {len(changes)}", 10),
        ("", 10),
    ]
    for c in changes:
        lines += [(t, 10) for t in textwrap.wrap(c.label(), 95)]
    page, y = None, 0
    for text, size in lines:
        if page is None or y > 842 - 50:
            page, y = out.new_page(width=595, height=842), 60
        if text:
            page.insert_text((50, y), text, fontsize=size, **font)
        y += size + 6


def export_report(old_path, new_path, changes: list[Change], out_path, summary: bool = True) -> None:
    old, new = open_pdf(old_path), open_pdf(new_path)
    # show_pdf_page draws rotated pages wrong: derotate the source and rotate while placing
    ro, rn = [(p.rect, p.rotation) for p in old], [(p.rect, p.rotation) for p in new]
    for d in (old, new):
        for p in d:
            p.set_rotation(0)
    out = pymupdf.open()
    if summary:
        _summary_pages(out, old_path, new_path, changes)
    for i in range(max(old.page_count, new.page_count)):
        r_old = ro[min(i, len(ro) - 1)][0]  # a missing page leaves a blank of the last page's size
        r_new = rn[min(i, len(rn) - 1)][0]
        page = out.new_page(width=r_old.width + r_new.width, height=max(r_old.height, r_new.height))
        if i < old.page_count:
            page.show_pdf_page(pymupdf.Rect(0, 0, r_old.width, r_old.height), old, i, rotate=-ro[i][1])
        if i < new.page_count:
            page.show_pdf_page(pymupdf.Rect(r_old.width, 0, r_old.width + r_new.width, r_new.height), new, i,
                               rotate=-rn[i][1])
        for c in changes:
            for w in c.old:
                if w.page == i:
                    page.draw_rect(pymupdf.Rect(w.rect), color=None, fill=RED, fill_opacity=OPACITY)
            for w in c.new:
                if w.page == i:
                    page.draw_rect(pymupdf.Rect(w.rect) + (r_old.width, 0, r_old.width, 0),
                                   color=None, fill=GREEN, fill_opacity=OPACITY)
    out.save(out_path, garbage=3, deflate=True)


def export_changes_xlsx(changes: list[Change], path, old_name: str, new_name: str) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Modifiche"
    ws.append(["N.", "Tipo", "Pagina originale", "Pagina nuova", f"Testo in {old_name}", f"Testo in {new_name}"])
    names = {"delete": "Rimosso", "insert": "Aggiunto", "replace": "Modificato"}
    fills = {"delete": "FBE3E3", "insert": "E2F4E8", "replace": "FFF3D6"}
    for i, c in enumerate(changes, 1):
        ws.append([i, names[c.kind],
                   c.old[0].page + 1 if c.old else (c.old_at.page + 1 if c.old_at else None),
                   c.new[0].page + 1 if c.new else (c.new_at.page + 1 if c.new_at else None),
                   " ".join(w.text for w in c.old), " ".join(w.text for w in c.new)])
        ws.cell(ws.max_row, 2).fill = PatternFill("solid", fgColor=fills[c.kind])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEF", (6, 12, 16, 14, 60, 60)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)


# ---------------------------------------------------------------- UI

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from pdflex import ui  # noqa: E402
from pdflex.ui import DocView, button, dot, info, warn  # noqa: E402

KIND_NAMES = {"delete": "Rimosso", "insert": "Aggiunto", "replace": "Modificato"}
KIND_COLORS = {"delete": ui.RED, "insert": ui.GREEN, "replace": ui.AMBER}
HINT = "Trascina qui il PDF o clicca per sceglierlo"
NO_OCR = ("{} non contiene testo: è una scansione. Per leggerla serve l'OCR: installa Tesseract "
          "(github.com/UB-Mannheim/tesseract/wiki) spuntando la lingua italiana, poi riapri PDFlex.")


class CompareTab(QWidget):
    def __init__(self):
        super().__init__()
        self.old_path = self.new_path = None
        self.changes: list[Change] = []
        self.current = -1
        self._syncing = False
        self.setAcceptDrops(True)

        bar = QFrame(objectName="Bar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_new = button("Nuovo confronto", "refresh")
        self.b_new.clicked.connect(self.reset)
        self.summary = QLabel("Scegli i due PDF da confrontare", objectName="Muted")
        self.sync = ui.Toggle("Scorri insieme")
        self.sync.setChecked(True)
        self.sync.setToolTip("Scorrendo un documento, l'altro segue alla stessa pagina")
        self.b_zout = button("", "zoom-out", "Ghost", "Rimpicciolisci (Ctrl+rotellina)")
        self.b_zin = button("", "zoom-in", "Ghost", "Ingrandisci (Ctrl+rotellina)")
        self.zoom_label = button("100%", None, "Ghost", "Adatta alla larghezza")
        self.b_zout.clicked.connect(lambda: self.zoom_by(1 / 1.25))
        self.b_zin.clicked.connect(lambda: self.zoom_by(1.25))
        self.zoom_label.clicked.connect(self.zoom_fit)
        self.b_prev = button("Precedente", "prev")
        self.b_next = button("Successiva", "next")
        self.b_export = button("Esporta PDF", "export", "Primary")
        self.b_xlsx = button("Esporta Excel", "table", tip="Elenco delle modifiche in un foglio Excel")
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next.clicked.connect(lambda: self.step(1))
        self.b_export.clicked.connect(self.ask_export)
        self.b_xlsx.clicked.connect(self.ask_export_xlsx)
        bl.addWidget(self.b_new)
        bl.addSpacing(10)
        bl.addWidget(self.summary)
        bl.addStretch()
        for w in (self.b_zout, self.zoom_label, self.b_zin):
            bl.addWidget(w)
        bl.addSpacing(10)
        bl.addWidget(self.sync)
        bl.addSpacing(10)
        for w in (self.b_prev, self.b_next, self.b_xlsx, self.b_export):
            bl.addWidget(w)

        # empty state: two drop zones
        self.drop_old = ui.DropZone("Originale", glyph="form")
        self.drop_new = ui.DropZone("Nuova versione", glyph="form")
        self.drop_old.picked.connect(lambda p: self._pick("old", p))
        self.drop_new.picked.connect(lambda p: self._pick("new", p))
        empty = QWidget()
        empty.setObjectName("Desk")
        el = QVBoxLayout(empty)
        el.setContentsMargins(60, 40, 60, 40)
        head = QLabel("Confronta due versioni di un documento", objectName="H1", alignment=Qt.AlignCenter)
        sub = QLabel("Le parole tolte appaiono in rosso sull'originale, quelle aggiunte in verde sulla nuova versione.",
                     objectName="Muted", alignment=Qt.AlignCenter)
        sub.setWordWrap(True)
        zones = QHBoxLayout()
        zones.setSpacing(24)
        zones.addWidget(self.drop_old)
        zones.addWidget(self.drop_new)
        el.addStretch()
        el.addWidget(head)
        el.addWidget(sub)
        el.addSpacing(20)
        el.addLayout(zones)
        el.addStretch(2)

        # result
        self.old_view, self.new_view = DocView(), DocView()
        self.old_view.verticalScrollBar().valueChanged.connect(lambda: self._follow(self.old_view, self.new_view))
        self.new_view.verticalScrollBar().valueChanged.connect(lambda: self._follow(self.new_view, self.old_view))
        self.old_view.horizontalScrollBar().valueChanged.connect(lambda: self._follow_x(self.old_view, self.new_view))
        self.new_view.horizontalScrollBar().valueChanged.connect(lambda: self._follow_x(self.new_view, self.old_view))
        self.old_view.scaled.connect(self._zoom_to)
        self.new_view.scaled.connect(self._zoom_to)
        self.old_label, self.new_label = QLabel(), QLabel()
        self.list = QListWidget(objectName="Changes")
        self.list.setIconSize(QSize(10, 10))
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._row_changed)

        def column(label, view, color):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            label.setStyleSheet(f"background:#FFFFFF; padding: 8px 14px; border-bottom: 3px solid {color};"
                                "font-weight: 600;")
            v.addWidget(label)
            v.addWidget(view)
            return w

        side = QFrame(objectName="Panel")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 12, 0, 0)
        title = QLabel("Modifiche", objectName="H2")
        title.setContentsMargins(14, 0, 14, 4)
        sl.addWidget(title)
        opts = QVBoxLayout()
        opts.setContentsMargins(14, 0, 14, 8)
        self.kind_filter = QComboBox()
        for label, kind in (("Tutte le modifiche", ""), ("Solo aggiunte", "insert"), ("Solo rimozioni", "delete"),
                            ("Solo sostituzioni", "replace")):
            self.kind_filter.addItem(label, kind)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        self.ignore_case = ui.Toggle("Ignora maiuscole e minuscole")
        self.ignore_punct = ui.Toggle("Ignora la punteggiatura")
        self.include_list = ui.Toggle("Elenco modifiche nel PDF esportato")
        self.include_list.setChecked(True)
        self.include_list.setToolTip("Spento: il PDF esportato contiene solo le pagine affiancate")
        for c in (self.ignore_case, self.ignore_punct):
            c.toggled.connect(self._rediff)
            opts.addWidget(c)
        opts.addWidget(self.include_list)
        opts.addWidget(self.kind_filter)
        sl.addLayout(opts)
        sl.addWidget(self.list)
        split = QSplitter()
        split.addWidget(column(self.old_label, self.old_view, ui.RED))
        split.addWidget(column(self.new_label, self.new_view, ui.GREEN))
        split.addWidget(side)
        split.setSizes([520, 520, 300])
        split.setChildrenCollapsible(False)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(split)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        layout.addWidget(self.stack, 1)
        self.reset()

    # ---- choosing files
    def dragEnterEvent(self, e):
        if ui.pdf_from_drop(e):
            e.acceptProposedAction()

    def dropEvent(self, e):  # dropped outside the zones: fill the first empty slot
        path = ui.pdf_from_drop(e)
        if path:
            self._pick("new" if self.old_path else "old", path)

    def _check(self, path) -> bool:
        """Open the file and make sure it has text; on failure say which file and why."""
        name = Path(path).name
        try:
            doc = open_pdf(path)
        except ValueError as e:
            warn(self, f"{name}: {e}")
            return False
        if not any(page.get_text("words") for page in doc) and not ocr_available():
            info(self, NO_OCR.format(name))
            return False
        return True

    def _pick(self, side, path):
        if not self._check(path):
            return  # the other side keeps its file
        if side == "old":
            self.old_path = path
            self.drop_old.set_file(path)
        else:
            self.new_path = path
            self.drop_new.set_file(path)
        if self.old_path and self.new_path:
            self.load(self.old_path, self.new_path)

    def reset(self):
        self.old_path = self.new_path = None
        self.changes, self.current = [], -1
        self.list.clear()
        self.drop_old.hint.setText(HINT)
        self.drop_new.hint.setText(HINT)
        self.summary.setText("Scegli i due PDF da confrontare")
        self.stack.setCurrentIndex(0)
        self._buttons()

    def _buttons(self):
        loaded = self.stack.currentIndex() == 1
        for w in (self.b_new, self.sync, self.b_zout, self.b_zin, self.zoom_label):
            w.setVisible(loaded)
        self.b_export.setEnabled(loaded)
        self.b_xlsx.setEnabled(bool(self.changes))
        self.b_prev.setEnabled(self._next_visible(-1) is not None)
        self.b_next.setEnabled(self._next_visible(1) is not None)

    # ---- compare
    def load(self, old_path: str, new_path: str) -> None:
        try:
            old, new = open_pdf(old_path), open_pdf(new_path)
        except ValueError as e:
            warn(self, str(e))
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)  # OCR of a scan can take a while
        try:
            old_words, new_words = extract_words(old, ocr=True), extract_words(new, ocr=True)
        finally:
            QApplication.restoreOverrideCursor()
        empty = [Path(p).name for p, w in ((old_path, old_words), (new_path, new_words)) if not w]
        if empty:
            info(self, NO_OCR.format(", ".join(empty)))
            return
        self.old_path, self.new_path = old_path, new_path
        self.docs, self.words = (old, new), (old_words, new_words)
        self.old_label.setText(f"Originale  {Path(old_path).name}")
        self.new_label.setText(f"Nuova versione  {Path(new_path).name}")
        self.stack.setCurrentIndex(1)
        self._rediff()

    def _rediff(self):
        """Diff again with the current options and redraw the highlights."""
        if self.stack.currentIndex() != 1:
            return
        self.changes, self.current = [], -1
        self.list.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.old_view.show_doc(self.docs[0])
            self.new_view.show_doc(self.docs[1])
            self.changes = diff_words(*self.words, ignore_case=self.ignore_case.isChecked(),
                                      ignore_punct=self.ignore_punct.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
        counts = {k: sum(c.kind == k for c in self.changes) for k in KIND_NAMES}
        for c in self.changes:
            self.old_view.highlight(c.old, RED, OPACITY)
            self.new_view.highlight(c.new, GREEN, OPACITY)
            body = c.label().split(": ", 1)[1]
            page = (c.old or c.new)[0].page + 1
            self.list.addItem(QListWidgetItem(dot(KIND_COLORS[c.kind]),
                                              f"{body}\n{KIND_NAMES[c.kind]}, pagina {page}"))
        if self.changes:
            parts = [f"{n} {word}" for n, word in ((counts["insert"], "aggiunte"), (counts["delete"], "rimozioni"),
                                                    (counts["replace"], "sostituzioni")) if n]
            self.summary.setText(f"{len(self.changes)} modifiche: " + ", ".join(parts))
        else:
            self.summary.setText("Nessuna differenza nel testo")
        self._apply_filter()

    def _hidden(self, i) -> bool:
        kind = self.kind_filter.currentData()
        return bool(kind) and self.changes[i].kind != kind

    def _apply_filter(self):
        for i in range(len(self.changes)):
            self.list.setRowHidden(i, self._hidden(i))
        if self.current < 0 or self._hidden(self.current):
            self.current = -1
            self.step(1)
        self._buttons()

    def _next_visible(self, d):
        i = self.current + d
        while 0 <= i < len(self.changes):
            if not self._hidden(i):
                return i
            i += d
        return None

    def step(self, d):
        i = self._next_visible(d)
        if i is not None:
            self.go_to(i)

    def _zoom_to(self, scale: float):
        """Both documents always share the same zoom, keeping the reader on the same spot."""
        pos = self.old_view.position() if self.old_view.offsets else None
        self._syncing = True
        try:
            self.old_view.set_scale(scale)
            self.new_view.set_scale(scale)
            if pos:
                self.old_view.scroll_to(*pos)
                self.new_view.scroll_to(*pos)
        finally:
            self._syncing = False
        self.zoom_label.setText(f"{round(self.old_view.scale * 100)}%")

    def zoom_by(self, factor: float):
        self._zoom_to(self.old_view.scale * factor)

    def zoom_fit(self):
        self._zoom_to(1.0)

    def _follow_x(self, src: DocView, dst: DocView):
        """Zoomed in, moving sideways on one document moves the other the same way."""
        if self._syncing or not self.sync.isChecked():
            return
        a, b = src.horizontalScrollBar(), dst.horizontalScrollBar()
        self._syncing = True
        try:
            b.setValue(round(a.value() / a.maximum() * b.maximum()) if a.maximum() else 0)
        finally:
            self._syncing = False

    def _follow(self, src: DocView, dst: DocView):
        if self._syncing or not self.sync.isChecked() or not src.offsets or not dst.offsets:
            return
        self._syncing = True
        try:
            dst.scroll_to(*src.position())
        finally:
            self._syncing = False

    def go_to(self, index: int) -> None:
        if 0 <= index < len(self.changes):
            self.list.setCurrentRow(index)  # → _row_changed

    def _row_changed(self, index):
        if not 0 <= index < len(self.changes):
            return
        self.current = index
        c = self.changes[index]
        self._syncing = True  # each side centres on its own copy of the change
        try:
            self.old_view.mark(c.old, c.old_at)
            self.new_view.mark(c.new, c.new_at)
        finally:
            self._syncing = False
        self._buttons()

    def ask_export(self):
        default = str(Path(self.new_path).with_name(Path(self.new_path).stem + "_confronto.pdf"))
        path, _ = QFileDialog.getSaveFileName(self, "Esporta confronto", default, "PDF (*.pdf)")
        if path:
            try:
                self.export(path)
            except (ValueError, OSError, RuntimeError) as e:
                warn(self, f"Esportazione non riuscita: {e}")
                return
            info(self, f"Confronto esportato in {path}")

    def ask_export_xlsx(self):
        default = str(Path(self.new_path).with_name(Path(self.new_path).stem + "_modifiche.xlsx"))
        path, _ = QFileDialog.getSaveFileName(self, "Esporta elenco modifiche", default, "Excel (*.xlsx)")
        if path:
            try:
                self.export_xlsx(path)
            except OSError as e:
                warn(self, f"Esportazione non riuscita (il file è aperto in Excel?): {e}")
                return
            info(self, f"Elenco esportato in {path}")

    def export_xlsx(self, path: str) -> None:
        export_changes_xlsx(self.changes, path, Path(self.old_path).name, Path(self.new_path).name)

    def export(self, path: str) -> None:
        export_report(self.old_path, self.new_path, self.changes, path, summary=self.include_list.isChecked())

