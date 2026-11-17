"""Look & feel and shared widgets: palette, stylesheet, icons, sharp page rendering, page stack view."""
import struct
from pathlib import Path

import pymupdf
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGraphicsDropShadowEffect, QGraphicsScene, QGraphicsView, QLabel, QPushButton,
    QVBoxLayout,
)

INK = "#1D2633"
MUTED = "#5B6675"
LINE = "#E3E7EE"
DESK = "#E9EDF2"
ACCENT = "#3B3FAE"
ACCENT_SOFT = "#ECEDFB"
RED = "#D64545"
GREEN = "#2E9E5B"
AMBER = "#C98A00"

QSS = f"""
QWidget {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; color: {INK}; }}
QMainWindow, #Root {{ background: #F6F7FA; }}
#Rail {{ background: #FFFFFF; border-right: 1px solid {LINE}; }}
#Rail QToolButton {{ border: none; border-radius: 10px; padding: 10px 4px 8px 4px; color: {MUTED}; font-size: 9pt; }}
#Rail QToolButton:hover:!checked {{ background: #F1F3F7; }}
#Rail QToolButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}
#Bar {{ background: #FFFFFF; border-bottom: 1px solid {LINE}; }}
#Panel {{ background: #FFFFFF; }}
#PanelLeft {{ background: #FFFFFF; border-right: 1px solid {LINE}; }}
#PanelRight {{ background: #FFFFFF; border-left: 1px solid {LINE}; }}
QPushButton {{ border: 1px solid #D9DEE7; border-radius: 8px; padding: 6px 12px; background: #FFFFFF; }}
QPushButton:hover {{ background: #F1F3F7; }}
QPushButton:disabled {{ color: #A9B0BB; border-color: {LINE}; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; padding: 7px 16px; }}
QPushButton#Primary:hover {{ background: #3236A0; }}
QPushButton#Primary:disabled {{ background: #AEB1DE; }}
QPushButton#Ghost {{ border: none; background: transparent; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ background: #F1F3F7; }}
QPushButton#Danger {{ color: {RED}; }}
#Palette QToolButton {{ border: none; border-radius: 8px; padding: 8px 10px; text-align: left; }}
#Palette QToolButton:hover:!checked {{ background: #F1F3F7; }}
#Palette QToolButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}
QGraphicsView {{ background: {DESK}; border: none; }}
QLineEdit, QPlainTextEdit {{ border: 1px solid #D9DEE7; border-radius: 6px; padding: 5px 8px; background: #FFFFFF; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QLabel#H1 {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 14pt; font-weight: 600; }}
QLabel#H2 {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 11pt; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
#DropZone {{ border: 2px dashed #C3C9D4; border-radius: 16px; background: #FFFFFF; }}
#DropZone:hover, #DropZone[hot="true"] {{ border-color: {ACCENT}; background: #FAFAFF; }}
QListWidget#Changes {{ border: none; background: #FFFFFF; outline: none; }}
QListWidget#Changes::item {{ padding: 8px 6px; border-radius: 8px; margin: 1px 6px; }}
QListWidget#Changes::item:selected {{ background: {ACCENT_SOFT}; color: {INK}; }}
QListWidget#Changes::item:hover:!selected {{ background: #F5F6F9; }}
QSplitter::handle {{ background: {LINE}; width: 1px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C3C9D4; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C3C9D4; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QMessageBox QPushButton {{ min-width: 70px; }}
"""

# 24x24 line icons, drawn for this app (stroke = currentColor).
_ICONS = {
    "open": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
    "save": '<path d="M12 4v10M7.5 9.5 12 14l4.5-4.5M5 19h14"/>',
    "export": '<path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 14v5h14v-5"/>',
    "zoom-in": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2M8.5 11h5M11 8.5v5"/>',
    "zoom-out": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2M8.5 11h5"/>',
    "prev": '<path d="m14.5 6-6 6 6 6"/>',
    "next": '<path d="m9.5 6 6 6-6 6"/>',
    "select": '<path d="M6 4l12 7.5-5.2 1.3L10.5 18z"/>',
    "text": '<path d="M6 6h12M12 6v12M9.5 18h5"/>',
    "multiline": '<path d="M4 6h16M4 10h16M4 14h16M4 18h9"/>',
    "checkbox": '<rect x="4" y="4" width="16" height="16" rx="3.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    "radio": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
    "dropdown": '<rect x="3" y="6.5" width="18" height="11" rx="2.5"/><path d="m14 11 2 2 2-2"/>',
    "date": '<rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10h16M9 3.5v4M15 3.5v4"/>',
    "signature": '<path d="M3 16.5c2.5-5 4.5-8 6.5-8 2.2 0-.8 7.5 1.5 7.5 1.8 0 2.7-3.5 4.6-3.5 1.6 0 1.9 2.5 5.4 2.5M3 20.5h18"/>',
    "form": '<path d="M6.5 3h8L19 7.5V19a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v5h5M8 13h8M8 17h5"/>',
    "compare": '<rect x="3" y="4" width="8" height="16" rx="1.8"/><rect x="13" y="4" width="8" height="16" rx="1.8"/><path d="M5.5 9h3M15.5 12.5h3"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.2"/><path d="M15.5 8.5V6A2 2 0 0 0 13.5 4H6A2 2 0 0 0 4 6v7.5a2 2 0 0 0 2 2h2.5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "refresh": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4v4.5H15"/>',
}

APP_ICON_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#5157D9"/><stop offset="1" stop-color="#262A86"/></linearGradient></defs>
<rect width="256" height="256" rx="58" fill="url(#g)"/>
<path d="M82 40h74l46 46v118a14 14 0 0 1-14 14H82a14 14 0 0 1-14-14V54a14 14 0 0 1 14-14z" fill="#FFFFFF"/>
<path d="M156 40v32a14 14 0 0 0 14 14h32z" fill="#C9CCF6"/>
<rect x="88" y="104" width="62" height="16" rx="8" fill="{RED}"/>
<rect x="88" y="132" width="90" height="16" rx="8" fill="{GREEN}"/>
<rect x="88" y="164" width="92" height="30" rx="7" fill="none" stroke="{ACCENT}" stroke-width="7"/>
<path d="M102 172v14" stroke="{ACCENT}" stroke-width="6" stroke-linecap="round"/>
</svg>"""


def _render(svg: str, size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def icon(name: str, color: str = INK) -> QIcon:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" color="{color}">'
           f'{_ICONS[name].replace("currentColor", color)}</svg>')
    return QIcon(QPixmap.fromImage(_render(svg, 48)))


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(QPixmap.fromImage(_render(APP_ICON_SVG, s)))
    return ic


def write_ico(path: str | Path) -> None:
    """Multi-size .ico with PNG entries (Windows Vista+), for the .exe."""
    pngs = []
    for s in (16, 24, 32, 48, 64, 128, 256):
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        _render(APP_ICON_SVG, s).save(buf, "PNG")
        pngs.append((s, bytes(buf.data())))
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    for s, data in pngs:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    Path(path).write_bytes(out + b"".join(d for _, d in pngs))


class DropZone(QFrame):
    """Big dashed target: click to choose a PDF, or drop one on it."""
    picked = Signal(str)

    def __init__(self, title: str, hint: str = "Trascina qui il PDF o clicca per sceglierlo", glyph: str = "open"):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(280, 220)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(8)
        pic = QLabel()
        pic.setPixmap(icon(glyph, ACCENT).pixmap(44, 44))
        pic.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title, objectName="H2", alignment=Qt.AlignCenter)
        self.hint = QLabel(hint, objectName="Muted", alignment=Qt.AlignCenter)
        self.hint.setWordWrap(True)
        for w in (pic, self.title, self.hint):
            lay.addWidget(w)

    def set_file(self, path: str):
        self.hint.setText(Path(path).name)

    def _hot(self, on):
        self.setProperty("hot", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(self, "Scegli un PDF", "", "PDF (*.pdf)")
        if path:
            self.picked.emit(path)

    def dragEnterEvent(self, e):
        if pdf_from_drop(e):
            e.acceptProposedAction()
            self._hot(True)

    def dragLeaveEvent(self, e):
        self._hot(False)

    def dropEvent(self, e):
        self._hot(False)
        path = pdf_from_drop(e)
        if path:
            self.picked.emit(path)


def pdf_from_drop(e) -> str | None:
    urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
    paths = [u.toLocalFile() for u in urls if u.toLocalFile().lower().endswith(".pdf")]
    return paths[0] if paths else None


def button(text, glyph=None, name=None, tip=None) -> QPushButton:
    b = QPushButton(text)
    if glyph:
        b.setIcon(icon(glyph, "#FFFFFF" if name == "Primary" else INK))
    if name:
        b.setObjectName(name)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def dot(color: str, size=10) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(2, 2, size * 2 - 4, size * 2 - 4)
    p.end()
    return QIcon(pm)


def render_page(page, zoom, annots=True, dpr=1.0) -> QPixmap:
    """Render at the screen's real pixel density so text stays sharp on HiDPI displays."""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom * dpr, zoom * dpr), annots=annots)
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    pm = QPixmap.fromImage(img.copy())
    pm.setDevicePixelRatio(dpr)
    return pm


def page_shadow(item):
    fx = QGraphicsDropShadowEffect()
    fx.setBlurRadius(24)
    fx.setOffset(0, 3)
    fx.setColor(QColor(29, 38, 51, 60))
    item.setGraphicsEffect(fx)


class DocView(QGraphicsView):
    """All pages of a document stacked, fitted to the width and re-rendered sharp on resize.

    Words/marks are anything with .page and .rect (display coordinates, points)."""
    GAP = 16

    def __init__(self, annots=True):
        self._scene = QGraphicsScene()  # keep a Python reference or Qt frees it
        super().__init__(self._scene)
        self.annots = annots
        self.doc = None
        self.k = 1.0
        self.offsets: list[float] = []
        self.heights: list[float] = []
        self.highlights = []  # (words, rgb, opacity)
        self.current = ([], None)
        self._marks = []
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._timer = QTimer(self, singleShot=True, interval=120, timeout=self.render)

    def show_doc(self, doc):
        self.doc = doc
        self.highlights, self.current = [], ([], None)
        self.render()

    def render(self):
        if not self.doc:
            return
        bar = self.verticalScrollBar()
        frac = bar.value() / bar.maximum() if bar.maximum() else 0
        s = self.scene()
        s.clear()
        self._marks = []
        widest = max(p.rect.width for p in self.doc)
        self.k = max(self.viewport().width() - 2 * self.GAP, 200) / widest
        dpr = self.devicePixelRatioF()
        self.offsets, self.heights, y = [], [], self.GAP
        # ponytail: renders every page on each resize; very long documents are slow/heavy, render lazily if it bites
        for page in self.doc:
            item = s.addPixmap(render_page(page, self.k, self.annots, dpr))
            item.setOffset(self.GAP, y)
            page_shadow(item)
            self.offsets.append(y)
            self.heights.append(page.rect.height * self.k)
            y += page.rect.height * self.k + self.GAP
        s.setSceneRect(0, 0, widest * self.k + 2 * self.GAP, y)
        for words, rgb, opacity in self.highlights:
            self._paint(words, rgb, opacity)
        self._paint_current()
        bar.setValue(round(frac * bar.maximum()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._timer.start()

    def word_rect(self, w):
        x0, y0, x1, y1 = w.rect
        k = self.k
        return self.GAP + x0 * k, self.offsets[w.page] + y0 * k, (x1 - x0) * k, (y1 - y0) * k

    def _paint(self, words, rgb, opacity):
        color = QColor.fromRgbF(*rgb, opacity)
        for w in words:
            self.scene().addRect(*self.word_rect(w), QPen(Qt.NoPen), QBrush(color))

    def highlight(self, words, rgb, opacity=0.3):
        self.highlights.append((words, rgb, opacity))
        self._paint(words, rgb, opacity)

    def _paint_current(self):
        for item in self._marks:
            self.scene().removeItem(item)
        pen = QPen(QColor(ACCENT), 2.5)
        self._marks = []
        for w in self.current[0]:
            x, y, wd, h = self.word_rect(w)
            self._marks.append(self.scene().addRect(x - 2, y - 2, wd + 4, h + 4, pen))

    def mark(self, words, at=None):
        self.current = (words, at)
        self._paint_current()
        if at:
            x, y, wd, h = self.word_rect(at)
            self.centerOn(x + wd / 2, y + h / 2)

    # page-relative scroll position, so two documents can scroll together
    def position(self) -> tuple[int, float]:
        y = self.verticalScrollBar().value() + self.GAP
        i = max((n for n, off in enumerate(self.offsets) if off <= y + 1), default=0)  # +1: scroll values are ints
        return i, (y - self.offsets[i]) / self.heights[i] if self.heights else 0

    def scroll_to(self, page: int, frac: float):
        if self.offsets:
            i = min(page, len(self.offsets) - 1)
            self.verticalScrollBar().setValue(round(self.offsets[i] + frac * self.heights[i] - self.GAP))
