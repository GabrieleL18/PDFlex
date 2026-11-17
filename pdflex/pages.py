"""Merge PDFs, reorder / rotate / delete pages, split into single pages."""
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdflex import open_pdf


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


def _build(refs) -> pymupdf.Document:
    cache, out = _docs(refs), pymupdf.open()
    for r in refs:
        src = cache[r.path]
        out.insert_pdf(src, from_page=r.index, to_page=r.index)
        out[-1].set_rotation((src[r.index].rotation + r.rotation) % 360)
    return out


def assemble(refs: list[PageRef], out_path) -> None:
    _build(refs).save(out_path, garbage=3, deflate=True)


def split(refs: list[PageRef], out_dir, stem: str) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for n, r in enumerate(refs, 1):
        path = out_dir / f"{stem}_p{n}.pdf"
        assemble([r], path)
        made.append(path)
    return made


# ---------------------------------------------------------------- UI

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QTransform  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
    QStackedWidget, QVBoxLayout, QWidget,
)

from pdflex import ui  # noqa: E402
from pdflex.ui import button, info, render_page, warn  # noqa: E402


class PagesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        bar = QFrame(objectName="Bar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_add = button("Aggiungi PDF", "open")
        self.b_left = button("", "undo", "Ghost", "Ruota a sinistra")
        self.b_right = button("", "redo", "Ghost", "Ruota a destra")
        self.b_del = button("Elimina", "trash", "Danger")
        self.b_split = button("Dividi in pagine singole", "batch")
        self.b_extract = button("Estrai selezionate", "export")
        self.b_save = button("Salva PDF", "save", "Primary")
        self.b_add.clicked.connect(self.ask_add)
        self.b_left.clicked.connect(lambda: self.rotate(-90))
        self.b_right.clicked.connect(lambda: self.rotate(90))
        self.b_del.clicked.connect(self.delete_selected)
        self.b_split.clicked.connect(self.ask_split)
        self.b_extract.clicked.connect(lambda: self.ask_save(selected=True))
        self.b_save.clicked.connect(lambda: self.ask_save())
        bl.addWidget(self.b_add)
        bl.addWidget(QLabel("Trascina le pagine per riordinarle, Ctrl+clic per sceglierne più d'una", objectName="Muted"))
        bl.addStretch()
        for w in (self.b_left, self.b_right, self.b_del, self.b_split, self.b_extract, self.b_save):
            bl.addWidget(w)

        self.grid = QListWidget(objectName="Changes")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(150, 200))
        self.grid.setGridSize(QSize(180, 240))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Snap)
        self.grid.setDragDropMode(QAbstractItemView.InternalMove)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.itemSelectionChanged.connect(self._buttons)
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

    def _icon(self, ref: PageRef):
        page = open_pdf(ref.path)[ref.index]
        pix = render_page(page, 150 / max(page.rect.width, page.rect.height) * 1.3, dpr=1.5)
        return pix.transformed(QTransform().rotate(ref.rotation)) if ref.rotation else pix

    def add_files(self, paths):
        for p in paths:
            try:
                n = open_pdf(p).page_count
            except ValueError as e:
                warn(self, f"{Path(p).name}: {e}")
                continue
            for i in range(n):
                ref = PageRef(p, i)
                item = QListWidgetItem(self._icon(ref), f"{Path(p).stem[:18]}  p. {i + 1}")
                item.setData(Qt.UserRole, ref)
                self.grid.addItem(item)
        self._buttons()

    def ask_add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Aggiungi PDF", "", "PDF (*.pdf)")
        self.add_files(paths)

    def rotate(self, degrees: int):
        for item in self.grid.selectedItems():
            ref = item.data(Qt.UserRole)
            ref.rotation = (ref.rotation + degrees) % 360
            item.setData(Qt.UserRole, ref)
            item.setIcon(self._icon(ref))

    def delete_selected(self):
        for item in self.grid.selectedItems():
            self.grid.takeItem(self.grid.row(item))
        self._buttons()

    def _buttons(self):
        has, sel = self.grid.count() > 0, bool(self.grid.selectedItems())
        self.stack.setCurrentIndex(1 if has else 0)
        for w in (self.b_save, self.b_split):
            w.setEnabled(has)
        for w in (self.b_left, self.b_right, self.b_del, self.b_extract):
            w.setEnabled(sel)

    def save(self, path: str, selected: bool = False) -> None:
        refs = [i.data(Qt.UserRole) for i in self.grid.selectedItems()] if selected else self.refs()
        if selected:  # keep the order on screen
            order = {id(r): n for n, r in enumerate(self.refs())}
            refs.sort(key=lambda r: order[id(r)])
        assemble(refs, path)

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
            made = split(self.refs(), out, first.stem)
            info(self, f"Creati {len(made)} PDF in {out}")
