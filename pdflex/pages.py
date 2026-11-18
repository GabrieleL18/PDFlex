"""Merge PDFs, reorder / rotate / delete pages, split into single pages."""
import copy
import math
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdflex import open_pdf, write_atomic


@dataclass
class PageRef:
    path: str
    index: int
    rotation: int = 0  # added on top of the page's own rotation


def _docs(refs):
    cache = {}
    for r in refs:
        if r.path not in cache:
            cache[r.path] = open_pdf(r.path)
    return cache


@dataclass
class Extras:
    """What is added while saving: written on every page as the reader sees it, rotated pages too."""
    numbers: bool = False  # "Pagina 1 di 5" at the bottom
    header: str = ""  # a line at the top
    watermark: str = ""  # big, light, across the page (BOZZA, COPIA)
    compress: bool = False  # pictures resampled to 150 dpi and recompressed

    def count(self) -> int:
        return sum(map(bool, (self.numbers, self.header, self.watermark, self.compress)))


def _write(page, text: str, center: tuple, size: float, color=(0.25, 0.25, 0.25), opacity=1.0, angle=0) -> None:
    """text centred on `center` (as seen on screen), turned by `angle` degrees (counter-clockwise)."""
    half = pymupdf.get_text_length(text, "helv", size) / 2
    a = math.radians(angle)
    # baseline start: half the width back along the text's direction, a third of the size down
    x = center[0] - half * math.cos(a) + size / 3 * math.sin(a)
    y = center[1] + half * math.sin(a) + size / 3 * math.cos(a)
    start = pymupdf.Point(x, y) * page.derotation_matrix
    page.insert_text(start, text, fontsize=size, fontname="helv", color=color, fill_opacity=opacity,
                     rotate=page.rotation, morph=(start, pymupdf.Matrix(angle)) if angle else None)


def add_extras(doc: pymupdf.Document, extras: Extras) -> None:
    n = doc.page_count
    for page in doc:
        r = page.rect  # as shown, after rotation
        if extras.watermark:
            size = min(0.6 * math.hypot(r.width, r.height) / pymupdf.get_text_length(extras.watermark, "helv", 1),
                       r.height / 4)
            angle = math.degrees(math.atan2(r.height, r.width))
            _write(page, extras.watermark, (r.width / 2, r.height / 2), size, (0.5, 0.5, 0.5), 0.18, angle)
        if extras.header:
            _write(page, extras.header, (r.width / 2, 24), 9)
        if extras.numbers:
            _write(page, f"Pagina {page.number + 1} di {n}", (r.width / 2, r.height - 20), 9)


def _bytes(doc: pymupdf.Document, extras: Extras | None) -> bytes:
    if extras:
        add_extras(doc, extras)
        if extras.compress:
            doc.rewrite_images(dpi_threshold=200, dpi_target=150, quality=70)
            doc.subset_fonts()  # only the letters in use
            return doc.tobytes(garbage=4, deflate=True, use_objstms=1)
    return doc.tobytes(garbage=3, deflate=True)


def _build(refs) -> pymupdf.Document:
    cache, out = _docs(refs), pymupdf.open()
    for r in refs:
        src = cache[r.path]
        out.insert_pdf(src, from_page=r.index, to_page=r.index)
        out[-1].set_rotation((src[r.index].rotation + r.rotation) % 360)
    return out


def assemble(refs: list[PageRef], out_path, extras: Extras | None = None) -> None:
    # bytes first, file last: saving over one of the source PDFs can't leave it half written
    write_atomic(out_path, _bytes(_build(refs), extras))


def split(refs: list[PageRef], out_dir, stem: str, extras: Extras | None = None) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for n, r in enumerate(refs, 1):
        path = out_dir / f"{stem}_p{n}.pdf"
        assemble([r], path, extras)
        made.append(path)
    return made


# ---------------------------------------------------------------- UI

from PySide6.QtCore import QSize, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut, QTransform  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView,
    QListWidget, QListWidgetItem, QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from pdflex import ui  # noqa: E402
from pdflex.ui import button, info, render_page, warn  # noqa: E402

THUMB = 320  # px, longest side of a rendered thumbnail; the size slider only scales it down


class PageGrid(QListWidget):
    """Thumbnails reordered by dragging.

    Qt's icon mode only shifts the pictures on screen when you drop, the order stays the same:
    the drop is handled here, with a marker line showing where the pages will land.
    PDF files dragged in from Explorer land where they are dropped too."""
    about_to_move = Signal()  # before a drag reorders the pages (for undo)
    files_dropped = Signal(list, int)  # PDF paths, row they go before

    def __init__(self):
        super().__init__(objectName="Changes")
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        # Static movement also turns off drops on the viewport, the widget a drag actually lands on:
        # without this the drag starts but nothing receives it
        self.viewport().setAcceptDrops(True)
        self.marker = None  # (row, x, top, bottom) while dragging
        self.dragging = False  # a drag started here (files dragged in from Explorer are not ours)
        self.set_thumb(150)

    def set_thumb(self, size: int):
        self.setIconSize(QSize(size, round(size * 1.33)))
        self.setGridSize(QSize(size + 34, round(size * 1.33) + 44))

    def target(self, pos) -> tuple:
        """(row the dragged pages go before, x, top, bottom of the marker) for the mouse at pos."""
        rects = [self.visualItemRect(self.item(i)) for i in range(self.count())]
        line = [i for i, r in enumerate(rects) if r.top() <= pos.y() <= r.bottom()]
        if not line:
            i = len(rects) - 1 if not rects or pos.y() > rects[-1].bottom() else 0
            after = i == len(rects) - 1
        else:
            i = min(line, key=lambda i: abs(rects[i].center().x() - pos.x()))
            after = pos.x() > rects[i].center().x()
        r = rects[i]
        return i + after, (r.right() + 2 if after else r.left() - 2), r.top() + 6, r.bottom() - 6

    def move_selected(self, row: int) -> None:
        items = sorted(self.selectedItems(), key=self.row)
        row -= sum(self.row(i) < row for i in items)
        taken = [self.takeItem(self.row(i)) for i in items]
        for n, item in enumerate(taken):
            self.insertItem(row + n, item)
            item.setSelected(True)

    def startDrag(self, actions):
        self.dragging = True
        try:
            QAbstractItemView.startDrag(self, actions)  # skip icon mode's own drag, which draws stray copies
        finally:
            self.dragging = False
            self.marker = None

    @staticmethod
    def _pdfs(e) -> list[str]:
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        return [u.toLocalFile() for u in urls if u.toLocalFile().lower().endswith(".pdf")]

    def dragEnterEvent(self, e):
        if self.dragging or self._pdfs(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if not self.dragging and not self._pdfs(e):
            return e.ignore()
        e.acceptProposedAction()
        self.marker = self.target(e.position().toPoint())
        self.viewport().update()

    def dragLeaveEvent(self, e):
        self.marker = None
        self.viewport().update()

    def dropEvent(self, e):
        row = self.target(e.position().toPoint())[0]
        self.marker = None
        self.viewport().update()
        if not self.dragging:
            paths = self._pdfs(e)
            if not paths:
                return e.ignore()
            e.acceptProposedAction()
            self.files_dropped.emit(paths, row)
            return
        self.about_to_move.emit()
        self.move_selected(row)
        e.setDropAction(Qt.CopyAction)  # already moved: a "move" result would make Qt delete the originals
        e.accept()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.marker and self.count():
            _, x, top, bottom = self.marker
            p = QPainter(self.viewport())
            p.setPen(QPen(QColor(ui.color("accent")), 4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(x, top, x, bottom)


class PagesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        bar = ui.Bar()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_add = button("Aggiungi PDF", "open", tip="Aggiungi le pagine di uno o più PDF in fondo")
        self.b_left = button("", "rotate-left", "Ghost", "Ruota a sinistra")
        self.b_right = button("", "rotate-right", "Ghost", "Ruota a destra")
        self.b_del = button("Elimina", "trash", "Danger", "Togli le pagine selezionate (Canc)")
        self.b_view = button("Anteprima", "eye", tip="Il PDF come verrà salvato, pagina per pagina")
        self.b_extras = button("Opzioni", "page", tip="Numeri di pagina, intestazione, filigrana e dimensione del file")
        self.b_split = button("Dividi", "batch", tip="Salva ogni pagina in un PDF a sé")
        self.b_extract = button("Estrai selezionate", "export", tip="Salva solo le pagine selezionate")
        self.b_save = button("Salva PDF", "save", "Primary")
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(90, THUMB)
        self.size.setValue(150)
        self.size.setFixedWidth(110)
        self.size.setToolTip("Dimensione delle miniature")
        self.b_add.clicked.connect(self.ask_add)
        self.b_left.clicked.connect(lambda: self.rotate(-90))
        self.b_right.clicked.connect(lambda: self.rotate(90))
        self.b_del.clicked.connect(self.delete_selected)
        self.b_view.clicked.connect(self.preview)
        self.b_extras.clicked.connect(self.ask_extras)
        self.extras = Extras()
        self.b_split.clicked.connect(self.ask_split)
        self.b_extract.clicked.connect(lambda: self.ask_save(selected=True))
        self.b_save.clicked.connect(lambda: self.ask_save())
        self.size.valueChanged.connect(lambda v: self.grid.set_thumb(v))
        hint = QLabel("Trascina per riordinare", objectName="Muted")
        hint.setToolTip("Trascina le miniature per riordinarle, o un PDF da Esplora risorse per inserirlo "
                        "in quel punto. Doppio clic per vedere una pagina in grande.")
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # the buttons keep their words first
        self.b_undo = button("", "undo", "Ghost", "Annulla (Ctrl+Z)")
        self.b_redo = button("", "redo", "Ghost", "Ripeti (Ctrl+Y)")
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        bl.addWidget(self.b_add)
        bl.addWidget(self.b_undo)
        bl.addWidget(self.b_redo)
        bl.addSpacing(8)
        bl.addWidget(hint, 1)
        bl.addWidget(QLabel("Miniature", objectName="Muted"))
        bl.addWidget(self.size)
        bl.addSpacing(8)
        for w in (self.b_left, self.b_right, self.b_del, self.b_extras, self.b_view, self.b_split, self.b_extract,
                  self.b_save):
            bl.addWidget(w)

        self.grid = PageGrid()
        self._undo, self._redo = [], []
        self.grid.itemSelectionChanged.connect(self._buttons)
        self.grid.about_to_move.connect(self._push)
        self.grid.files_dropped.connect(self.add_files)
        self.grid.itemDoubleClicked.connect(self.show_page)
        drop = ui.DropZone("Unisci, riordina o dividi PDF", "Trascina qui uno o più PDF o clicca per sceglierli", "batch")
        drop.picked.connect(lambda p: self.add_files([p]))
        empty = QWidget(objectName="Desk")
        el = QVBoxLayout(empty)
        el.setContentsMargins(80, 60, 80, 60)
        el.addWidget(drop)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.grid)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.stack, 1)
        QShortcut(QKeySequence.Delete, self.grid, self.delete_selected)
        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        self._buttons()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".pdf")]
        if paths:
            self.add_files(paths)

    def refs(self) -> list[PageRef]:
        return [self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]

    @staticmethod
    def _icon(page, rotation: int):
        pix = render_page(page, THUMB / max(page.rect.width, page.rect.height))
        pix = pix.transformed(QTransform().rotate(rotation)) if rotation else pix
        p = QPainter(pix)  # an edge, or a white page vanishes on the white background
        p.setPen(QPen(QColor("#B8C0CC"), 2))
        p.drawRect(pix.rect().adjusted(1, 1, -1, -1))
        p.end()
        return pix

    # ---- undo / redo: the whole list of pages (pictures included, nothing to re-render)
    def _snapshot(self):
        items = [self.grid.item(i) for i in range(self.grid.count())]
        return [(copy.copy(it.data(Qt.UserRole)), it.icon(), it.text(), it.toolTip()) for it in items]

    def _push(self):
        self._undo.append(self._snapshot())
        del self._undo[:-30]
        self._redo.clear()

    def _restore(self, src, dst):
        if not src:
            return
        dst.append(self._snapshot())
        self.grid.clear()
        for ref, pic, text, tip in src.pop():
            item = QListWidgetItem(pic, text)
            item.setData(Qt.UserRole, ref)
            item.setToolTip(tip)
            self.grid.addItem(item)
        self._buttons()

    def undo(self):
        self._restore(self._undo, self._redo)

    def redo(self):
        self._restore(self._redo, self._undo)

    def add_files(self, paths, at: int | None = None):
        """Pages of these PDFs at the end, or before row `at` (dropped between two thumbnails)."""
        docs = []
        for p in paths:
            try:
                docs.append((p, open_pdf(p)))
            except ValueError as e:
                warn(self, f"{Path(p).name}: {e}")
        if not docs:
            return
        self._push()
        row = self.grid.count() if at is None else at
        total, done = sum(d.page_count for _, d in docs), 0
        with ui.progress(self, "Preparo le miniature...", total) as step:
            for p, doc in docs:
                for page in doc:
                    ref = PageRef(p, page.number)
                    item = QListWidgetItem(self._icon(page, 0), f"{Path(p).stem[:18]}  p. {page.number + 1}")
                    item.setData(Qt.UserRole, ref)
                    item.setToolTip(f"{Path(p).name}, pagina {page.number + 1}")
                    self.grid.insertItem(row + done, item)
                    done += 1
                    step(done)
                doc.close()
        self._buttons()

    def ask_add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Aggiungi PDF", "", "PDF (*.pdf)")
        self.add_files(paths)

    def rotate(self, degrees: int):
        if self.grid.selectedItems():
            self._push()
        for item in self.grid.selectedItems():
            ref = item.data(Qt.UserRole)
            ref.rotation = (ref.rotation + degrees) % 360
            item.setData(Qt.UserRole, ref)
            item.setIcon(self._icon(open_pdf(ref.path)[ref.index], ref.rotation))
        self._buttons()

    def delete_selected(self):
        if self.grid.selectedItems():
            self._push()
        for item in self.grid.selectedItems():
            self.grid.takeItem(self.grid.row(item))
        self._buttons()

    def show_page(self, item):
        ref = item.data(Qt.UserRole)
        ui.show_doc(self, _build([ref]), f"{Path(ref.path).name}, pagina {ref.index + 1}")

    def ask_extras(self):
        """What to add while saving; kept until PDFlex is closed."""
        e = self.extras
        d = QDialog(self)
        d.setWindowTitle("Opzioni di salvataggio")
        d.setMinimumWidth(420)
        form = QFormLayout(d)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        numbers = ui.Toggle("Numeri di pagina in basso (Pagina 1 di 5)")
        numbers.setChecked(e.numbers)
        header = QLineEdit(e.header, placeholderText="Nessuna")
        mark = QLineEdit(e.watermark, placeholderText="Nessuna, es. BOZZA o COPIA")
        compress = ui.Toggle("Riduci la dimensione del file")
        compress.setChecked(e.compress)
        compress.setToolTip("Le immagini oltre 200 dpi scendono a 150 e vengono ricompresse: "
                            "a schermo e in stampa non cambia quasi nulla")
        note = QLabel("Valgono per Salva, Estrai, Dividi e Anteprima.", objectName="Muted")
        ok, cancel = button("OK", None, "Primary"), button("Annulla")
        ok.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(ok)
        form.addRow(numbers)
        form.addRow("Intestazione in alto", header)
        form.addRow("Filigrana", mark)
        form.addRow(compress)
        form.addRow(note)
        form.addRow(row)
        if d.exec():
            self.extras = Extras(numbers.isChecked(), header.text().strip(), mark.text().strip(), compress.isChecked())
            self._buttons()
        d.deleteLater()

    def preview(self):
        if self.grid.count():
            doc = _build(self.refs())
            add_extras(doc, self.extras)
            ui.show_doc(self, doc, "Anteprima del PDF da salvare",
                        f"{self.grid.count()} pagine nell'ordine attuale. Ctrl+rotellina per ingrandire.")

    def _buttons(self):
        has, sel = self.grid.count() > 0, bool(self.grid.selectedItems())
        self.stack.setCurrentIndex(1 if has else 0)
        for w in (self.b_save, self.b_split, self.b_view):
            w.setEnabled(has)
        for w in (self.b_left, self.b_right, self.b_del, self.b_extract):
            w.setEnabled(sel)
        n = self.extras.count()
        self.b_extras.setText(f"Opzioni ({n})" if n else "Opzioni")
        self.b_undo.setEnabled(bool(self._undo))
        self.b_redo.setEnabled(bool(self._redo))

    def save(self, path: str, selected: bool = False) -> None:
        refs = [i.data(Qt.UserRole) for i in self.grid.selectedItems()] if selected else self.refs()
        if selected:  # keep the order on screen
            order = {id(r): n for n, r in enumerate(self.refs())}
            refs.sort(key=lambda r: order[id(r)])
        assemble(refs, path, self.extras)

    def ask_save(self, selected: bool = False):
        first = Path(self.refs()[0].path)
        default = str(first.with_name(first.stem + ("_estratte.pdf" if selected else "_unito.pdf")))
        path, _ = QFileDialog.getSaveFileName(self, "Salva PDF", default, "PDF (*.pdf)")
        if path:
            try:
                self.save(path, selected)
            except (ValueError, OSError, RuntimeError) as e:
                warn(self, f"Salvataggio non riuscito: {e}")
                return
            info(self, f"Salvato in {path}")

    def ask_split(self):
        first = Path(self.refs()[0].path)
        out = QFileDialog.getExistingDirectory(self, "Cartella per le pagine singole", str(first.parent))
        if out:
            made = split(self.refs(), out, first.stem, self.extras)
            info(self, f"Creati {len(made)} PDF in {out}")
