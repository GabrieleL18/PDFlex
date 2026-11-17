import os
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


def extract_words(doc: pymupdf.Document) -> list[Word]:
    # get_text gives unrotated coordinates; turn them into what the reader sees
    return [Word(page.number, tuple(pymupdf.Rect(w[:4]) * page.rotation_matrix), w[4])
            for page in doc for w in page.get_text("words")]


def diff_words(old: list[Word], new: list[Word]) -> list[Change]:
    a = [w.text for w in old]
    b = [w.text for w in new]
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


def export_report(old_path, new_path, changes: list[Change], out_path) -> None:
    old, new = open_pdf(old_path), open_pdf(new_path)
    # show_pdf_page draws rotated pages wrong: derotate the source and rotate while placing
    ro, rn = [(p.rect, p.rotation) for p in old], [(p.rect, p.rotation) for p in new]
    for d in (old, new):
        for p in d:
            p.set_rotation(0)
    out = pymupdf.open()
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


# ---------------------------------------------------------------- UI

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from pdflex import ui  # noqa: E402
from pdflex.ui import DocView, button, dot  # noqa: E402

KIND_NAMES = {"delete": "Rimosso", "insert": "Aggiunto", "replace": "Modificato"}
KIND_COLORS = {"delete": ui.RED, "insert": ui.GREEN, "replace": ui.AMBER}
HINT = "Trascina qui il PDF o clicca per sceglierlo"


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
        self.sync = QCheckBox("Scorri insieme")
        self.sync.setChecked(True)
        self.sync.setToolTip("Scorrendo un documento, l'altro segue alla stessa pagina")
        self.b_prev = button("Precedente", "prev")
        self.b_next = button("Successiva", "next")
        self.b_export = button("Esporta PDF", "export", "Primary")
        self.b_prev.clicked.connect(lambda: self.go_to(self.current - 1))
        self.b_next.clicked.connect(lambda: self.go_to(self.current + 1))
        self.b_export.clicked.connect(self.ask_export)
        bl.addWidget(self.b_new)
        bl.addSpacing(10)
        bl.addWidget(self.summary)
        bl.addStretch()
        bl.addWidget(self.sync)
        bl.addSpacing(10)
        for w in (self.b_prev, self.b_next, self.b_export):
            bl.addWidget(w)

        # empty state: two drop zones
        self.drop_old = ui.DropZone("Originale", glyph="form")
        self.drop_new = ui.DropZone("Nuova versione", glyph="form")
        self.drop_old.picked.connect(lambda p: self._pick("old", p))
        self.drop_new.picked.connect(lambda p: self._pick("new", p))
        empty = QWidget()
        empty.setStyleSheet(f"background: {ui.DESK};")
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
            QMessageBox.warning(self, "PDFlex", f"{name}: {e}")
            return False
        if not any(page.get_text("words") for page in doc):
            QMessageBox.information(self, "PDFlex", f"{name} non contiene testo (forse è una scansione): "
                                                    "OCR non supportato. Scegli un altro file.")
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
        self.b_new.setVisible(loaded)
        self.sync.setVisible(loaded)
        self.b_export.setEnabled(loaded)
        self.b_prev.setEnabled(self.current > 0)
        self.b_next.setEnabled(0 <= self.current < len(self.changes) - 1)

    # ---- compare
    def load(self, old_path: str, new_path: str) -> None:
        try:
            old, new = open_pdf(old_path), open_pdf(new_path)
        except ValueError as e:
            QMessageBox.warning(self, "PDFlex", str(e))
            return
        old_words, new_words = extract_words(old), extract_words(new)
        empty = [Path(p).name for p, w in ((old_path, old_words), (new_path, new_words)) if not w]
        if empty:
            QMessageBox.information(self, "PDFlex", f"{', '.join(empty)} non contiene testo (forse è una "
                                                    "scansione): OCR non supportato.")
            return
        self.old_path, self.new_path = old_path, new_path
        self.changes, self.current = [], -1
        self.list.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.stack.setCurrentIndex(1)
            self.old_view.show_doc(old)
            self.new_view.show_doc(new)
            self.changes = diff_words(old_words, new_words)
        finally:
            QApplication.restoreOverrideCursor()
        self.old_label.setText(f"Originale  {Path(old_path).name}")
        self.new_label.setText(f"Nuova versione  {Path(new_path).name}")
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
            self.go_to(0)
        else:
            self.summary.setText("Nessuna differenza nel testo")
        self._buttons()

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
                QMessageBox.warning(self, "PDFlex", f"Esportazione non riuscita: {e}")
                return
            QMessageBox.information(self, "PDFlex", f"Confronto esportato in {path}")

    def export(self, path: str) -> None:
        export_report(self.old_path, self.new_path, self.changes, path)
