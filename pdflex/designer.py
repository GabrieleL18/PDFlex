"""Editor tab: move, resize and add the graphics of a PDF before its fields are made in Moduli.

The page on screen is always the real PDF (pdflex.design changes it in place); fields and buttons are not shown
and never touched, so they are all still there in the saved file."""
import json
import math
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDoubleValidator, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsItem, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from pdflex import design, open_pdf, ui, write_atomic
from pdflex.editor import PAGE_MARGIN, THUMB_W
from pdflex.ui import ACCENT, DropZone, button, page_shadow, pdf_from_drop, render_page

KINDS = {"line": "Linea", "rect": "Rettangolo", "shape": "Forma", "image": "Immagine", "text": "Testo"}
TOOLS = [("select", "Seleziona e sposta"), ("text", "Testo"), ("line", "Linea"), ("rect", "Rettangolo"),
         ("image", "Immagine")]
HANDLE = 10  # px
# a box's handles, as fractions of its width and height: corners and middles of the sides, clockwise from top left
SPOTS = [(0, 0), (0.5, 0), (1, 0), (1, 0.5), (1, 1), (0.5, 1), (0, 1), (0, 0.5)]
CURSORS = [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor] * 2
UNDO_MAX = 30  # copies of the document kept for Annulla
AS_SHAPES = ": testo disegnato come forme, si sposta e si ridimensiona ma le parole non si cambiano"
GLYPHS = {"line": "line", "rect": "rect", "shape": "rect", "image": "image", "text": "text"}
COPY_OFFSET = 10  # pt: where a copy goes, right and down from the original
HELVETICA = "Il carattere di questo testo non ha le lettere: {}. Uso Helvetica per questo testo?"
TURN = 24  # px: the turning handle, above the box
SNAP = 6  # px: how close an edge or a middle comes before it catches another
MARKS = "PDFlexEditor"  # the Editor's own notes in the PDF: groups and locked objects, by page
DASHES = ["Continuo", "Tratteggiato", "Punteggiato"]


def _marks(doc) -> dict:
    typ, val = doc.xref_get_key(doc.pdf_catalog(), MARKS)
    try:
        return json.loads(val) if typ == "string" else {}
    except ValueError:
        return {}


def _put_marks(doc, marks: dict) -> None:
    doc.xref_set_key(doc.pdf_catalog(), MARKS, pymupdf.get_pdf_str(json.dumps(marks)))


def _key(o: design.Obj) -> list:
    return [round(v, 1) for v in o.rect] + [o.kind]


def _is(o: design.Obj, key) -> bool:
    return bool(key) and o.kind == key[4] and all(abs(a - b) < 1.5 for a, b in zip(o.rect, key[:4]))


def _same_key(a, b) -> bool:
    return a[4] == b[4] and all(abs(x - y) < 0.15 for x, y in zip(a[:4], b[:4]))


def _words(o: design.Obj) -> str:
    return "".join(s["text"] for s in o.data["spans"])


def _muted(text: str) -> QLabel:
    label = QLabel(text, objectName="Muted")
    label.setWordWrap(True)  # a long one would widen the panel past its edge
    return label


def _kept(o: design.Obj, r: tuple) -> bool:
    """o is the object that was at r before the change: same box, or a text starting where it did (another font
    or size makes it wider or taller). r = (x0, y0, x1, y1, kind): turned, o of that kind fits in r around the
    same middle (its own box is smaller than the turned old box)."""
    if len(r) == 5:
        box, mid = pymupdf.Rect(r[:4]), pymupdf.Rect(o.rect)
        return (o.kind == r[4] and abs(box.x0 + box.x1 - mid.x0 - mid.x1) < 3 and abs(box.y0 + box.y1 - mid.y0 - mid.y1) < 3
                and (box + (-10, -10, 10, 10)).contains(mid))  # 10: an arrow's head sticks out of its line
    if all(abs(a - b) < 1.5 for a, b in zip(o.rect, r)):
        return True
    a, b = o.rect, r
    return o.kind == "text" and abs(a[0] - b[0]) < 1.5 and (
        b[1] <= (a[1] + a[3]) / 2 <= b[3] or a[1] <= (b[1] + b[3]) / 2 <= a[3])  # either one's middle in the other


def _label(o: design.Obj) -> str:
    """A row of the list of objects: what it is, and enough to tell it from the others."""
    if o.kind == "text":
        words = "".join(s["text"] for s in o.data["spans"]).strip()
        text = f'Testo "{words[:40]}{"…" if len(words) > 40 else ""}"'
    else:
        r = pymupdf.Rect(o.rect)
        text = f"{'Testo disegnato' if o.state == 'move' else KINDS[o.kind]}  {r.width:.0f} × {r.height:.0f} pt"
    return ("🔒 " if o.state == "locked" else "📌 " if o.data.get("pinned") else "") + text


def _hex(rgb) -> str:
    return QColor.fromRgbF(*rgb).name() if rgb else ""


class ObjItem(QGraphicsRectItem):
    """An invisible box over an object of the page: hover shows it, a click selects it, a drag moves it,
    the handles (on every corner and side; the two ends, for a line) resize it."""

    def __init__(self, obj: design.Obj, zoom: float, tab, rotation: pymupdf.Matrix = pymupdf.Identity,
                 big: bool = False):
        r = pymupdf.Rect(obj.rect) * rotation * zoom  # a turned page shows its objects turned (they are locked)
        pad = 3 if obj.kind == "line" else 0  # a hairline still has something to grab
        super().__init__(-pad, -pad, max(r.width, 1) + 2 * pad, max(r.height, 1) + 2 * pad)
        self.setPos(r.x0, r.y0)
        self.home = QPointF(r.x0, r.y0)  # where it is on the page: a drag moves it from here
        self.obj, self.tab, self.zoom, self.grab = obj, tab, zoom, None
        self.hover = False
        # a background as big as the page lets clicks through: a drag "on the empty page" draws the selection
        # box instead of moving it; it is selected with a box drawn all around it
        self.setAcceptHoverEvents(not big)
        if big:
            self.setAcceptedMouseButtons(Qt.NoButton)
        if obj.state == "locked":
            self.setToolTip(f"🔒 {obj.reason}")
            if not big:
                self.setCursor(Qt.ForbiddenCursor)
        elif obj.data.get("pinned"):  # locked by the user: it can be selected, to unlock it
            self.setFlags(QGraphicsItem.ItemIsSelectable)
            self.setToolTip("📌 Bloccato: sbloccalo per spostarlo o cambiarlo")
        else:
            self.setFlags(QGraphicsItem.ItemIsSelectable | (QGraphicsItem.GraphicsItemFlag(0) if big
                                                            else QGraphicsItem.ItemIsMovable))
            self.setToolTip(KINDS[obj.kind] + ("" if obj.state == "edit" else AS_SHAPES))
            if not big:
                self.setCursor(Qt.SizeAllCursor)

    def handles(self) -> list[QPointF]:
        """Local points the user can drag to resize: a line's ends, a box's corners and sides."""
        if self.obj.state == "locked" or self.obj.kind == "text" or self.obj.data.get("pinned"):
            return []  # text grows from its size in the properties, not stretched
        if self.obj.kind == "line":
            return [QPointF(*self.obj.data[k]) * self.zoom - self.pos() for k in ("p1", "p2")]
        r = self.rect()
        return [QPointF(r.left() + fx * r.width(), r.top() + fy * r.height()) for fx, fy in SPOTS]

    def knob(self) -> QPointF | None:
        """The round handle above the box that turns it: only when it is the one object selected."""
        if (not self.isSelected() or self.obj.state == "locked" or self.obj.data.get("pinned")
                or len(self.tab.selection) != 1):
            return None
        return QPointF(self.rect().center().x(), self.rect().top() - TURN)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        if self.obj.state == "locked":
            if self.hover:
                p.setPen(QPen(QColor("#8A94A6"), 1, Qt.DotLine))
                p.setBrush(QColor(138, 148, 166, 40))
                p.drawRect(self.rect())
            return
        if self.isSelected():
            p.setPen(QPen(QColor(ACCENT), 1.5, Qt.DashLine))
            p.setBrush(QColor(59, 63, 174, 25))
            p.drawRect(self.rect())
            p.setPen(QPen(QColor(ACCENT), 1.5))
            p.setBrush(Qt.white)
            for h in self.handles():
                p.drawEllipse(h, HANDLE / 2, HANDLE / 2)
            k = self.knob()
            if k is not None:
                p.drawLine(QPointF(k.x(), self.rect().top()), k + QPointF(0, HANDLE / 2))
                p.drawEllipse(k, HANDLE / 2, HANDLE / 2)
        elif self.hover:
            p.setPen(QPen(QColor(ACCENT), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.rect())

    def boundingRect(self):  # the turning handle only when selected: a box drawn around it must still hold it
        return self.rect().adjusted(-HANDLE, -HANDLE - (TURN if self.isSelected() else 0), HANDLE, HANDLE)

    def shape(self):  # the knob above the box gets the click too
        path = super().shape()
        k = self.knob()
        if k is not None:
            path.addEllipse(k, HANDLE, HANDLE)
        return path

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedChange:
            self.prepareGeometryChange()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def _handle_at(self, pos) -> int | None:
        for i, h in enumerate(self.handles()):
            if (h - pos).manhattanLength() <= HANDLE:
                return i
        return None

    def _on_knob(self, pos) -> bool:
        k = self.knob()
        return k is not None and (k - pos).manhattanLength() <= HANDLE

    def hoverMoveEvent(self, e):
        i = self._handle_at(e.pos()) if self.isSelected() else None
        if self._on_knob(e.pos()):
            self.setCursor(Qt.PointingHandCursor)
        elif i is not None:
            self.setCursor(Qt.CrossCursor if self.obj.kind == "line" else CURSORS[i])
        elif self.obj.state != "locked" and not self.obj.data.get("pinned"):
            self.setCursor(Qt.SizeAllCursor)

    def _angle(self, scene_pos) -> float:
        c = self.turn_centre
        return math.degrees(math.atan2(scene_pos.y() - c.y(), scene_pos.x() - c.x()))

    def mousePressEvent(self, e):
        if self._on_knob(e.pos()):  # turning: shown on screen, made on the page when the button goes up
            self.grab = "turn"
            self.setTransformOriginPoint(self.rect().center())
            self.turn_centre = self.mapToScene(self.rect().center())
            self.turn_from = self._angle(e.scenePos())
            e.accept()
            return
        self.grab = self._handle_at(e.pos()) if self.isSelected() else None
        self.start, self.start_rect = self.pos(), QRectF(self.rect())
        super().mousePressEvent(e)
        if self.grab is None:
            self.tab.start_drag()

    def mouseMoveEvent(self, e):
        if self.grab == "turn":
            a = (self._angle(e.scenePos()) - self.turn_from + 180) % 360 - 180
            if e.modifiers() & Qt.ShiftModifier:  # by 15 degrees
                a = round(a / 15) * 15
            self.setRotation(a)
            self.tab.kind_label.setText(f"Rotazione: {a:.0f}° (Maiusc: a scatti di 15°)")
            return
        if self.grab is None:
            super().mouseMoveEvent(e)
            return self.tab.snap_drag(self)
        self.prepareGeometryChange()
        if self.obj.kind == "line":  # the band shows the line from the other end to the mouse
            other = self.handles()[1 - self.grab]
            self.setRect(QRectF(other, e.pos()).normalized())
            return
        r, (fx, fy), x, y = QRectF(self.start_rect), SPOTS[self.grab], e.pos().x(), e.pos().y()
        if fx == 0:  # only the sides this handle is on move; the box never turns inside out
            r.setLeft(min(x, r.right() - 4))
        elif fx == 1:
            r.setRight(max(x, r.left() + 4))
        if fy == 0:
            r.setTop(min(y, r.bottom() - 4))
        elif fy == 1:
            r.setBottom(max(y, r.top() + 4))
        self.setRect(r)

    def mouseReleaseEvent(self, e):
        if self.grab == "turn":
            a, self.grab = self.rotation(), None
            self.setRotation(0)
            if abs(a) > 0.05:
                self.tab.rotate_selected(round(a, 1))
            else:
                self.tab.refresh()
            return
        super().mouseReleaseEvent(e)
        self.tab.end_drag()
        z = self.zoom
        if self.grab is not None:
            if self.obj.kind == "line":
                ends = [self.handles()[i] if i != self.grab else e.pos() for i in (0, 1)]
                a, b = (pt + self.pos() for pt in ends)
                self.tab.resize_object(self.obj, (a.x() / z, a.y() / z, b.x() / z, b.y() / z))
            else:
                r = self.rect().translated(self.pos())
                self.tab.resize_object(self.obj, (r.left() / z, r.top() / z, r.right() / z, r.bottom() / z))
        elif self.pos() != self.start:
            d = self.pos() - self.start
            self.tab.move_selected(d.x() / z, d.y() / z)


class PageView(QGraphicsView):
    def __init__(self, scene, tab):
        super().__init__(scene)
        self.tab, self.start, self.band = tab, None, None
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.ContainsItemShape)  # what is all inside the box, not the background
        # arrows: the selection moves on screen at once, the page changes once the presses stop (a change of a
        # big page takes a moment: held down, the arrow would queue them up)
        self.nudge = [0, 0]
        self.nudge_timer = QTimer(self, singleShot=True, interval=350, timeout=self.flush_nudge)

    def keyPressEvent(self, e):
        d = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}.get(e.key())
        items = [i for i in self.tab.items if i.isSelected() and i.obj.state != "locked" and not i.obj.data.get("pinned")]
        if d is None or not items:
            return super().keyPressEvent(e)
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
        self.nudge = [self.nudge[0] + d[0] * step, self.nudge[1] + d[1] * step]
        for i in items:
            i.moveBy(d[0] * step * self.tab.zoom, d[1] * step * self.tab.zoom)
        self.nudge_timer.start()

    def flush_nudge(self):
        """Make the move the arrows showed: before any other change, too."""
        self.nudge_timer.stop()
        (dx, dy), self.nudge = self.nudge, [0, 0]
        if dx or dy:
            self.tab.move_selected(dx, dy)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier and self.tab.doc:
            self.tab.set_zoom(self.tab.zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15))
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        self.flush_nudge()  # before the press reaches the boxes: they are made again by the change
        if self.tab.mode == "select" or e.button() != Qt.LeftButton or not self.tab.doc:
            return super().mousePressEvent(e)
        self.start = self.mapToScene(e.position().toPoint())
        self.band = self.scene().addRect(QRectF(self.start, self.start), QPen(QColor(ACCENT), 1.5, Qt.DashLine),
                                         QBrush(QColor(59, 63, 174, 30)))

    def mouseMoveEvent(self, e):
        if self.band:
            self.band.setRect(QRectF(self.start, self.mapToScene(e.position().toPoint())).normalized())
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if not self.band:
            return super().mouseReleaseEvent(e)
        self.scene().removeItem(self.band)
        self.band = None
        end, z = self.mapToScene(e.position().toPoint()), self.tab.zoom
        self.tab.drawn((self.start.x() / z, self.start.y() / z, end.x() / z, end.y() / z),
                       straight=bool(e.modifiers() & Qt.ShiftModifier))


class DesignTab(QWidget):
    open_in_forms = Signal(str)  # path of the saved PDF, for the window to open in Moduli

    def __init__(self):
        super().__init__()
        self.doc, self.path, self.page_no, self.zoom, self.mode = None, None, 0, 1.5, "select"
        self.objects: list[design.Obj] = []
        self.selection: list[design.Obj] = []
        self.items: list[ObjItem] = []
        self.dirty = False
        self._undo: list[tuple[int, bytes]] = []  # (page changed, the document before)
        self._redo: list[tuple[int, bytes]] = []
        self.groups: list[list[design.Obj]] = []  # on this page, from the PDF's own notes (see MARKS)
        self.clip, self.pastes = None, 0  # copied objects: (a copy of their page, their keys, its number)
        self.guides, self.snap_rects = [], []
        self.setAcceptDrops(True)

        # left: tools
        palette = QFrame(objectName="PanelLeft")
        palette.setMinimumWidth(180)
        pl = QVBoxLayout(palette)
        pl.setContentsMargins(12, 16, 12, 16)
        pl.addWidget(QLabel("Strumenti", objectName="H2"))
        hint = QLabel("Prendi e sposta linee, forme, immagini e testi della pagina, o aggiungine di nuovi.",
                      objectName="Muted")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        pl.addSpacing(6)
        tools = QWidget(objectName="Palette")
        tl = QVBoxLayout(tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        group = QButtonGroup(self)
        self.mode_buttons = {}
        for mode, label in TOOLS:
            b = QToolButton()
            b.setText(label)
            ui.set_glyph(b, mode)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            group.addButton(b)
            tl.addWidget(b)
            self.mode_buttons[mode] = b
        self.mode_buttons["select"].setChecked(True)
        pl.addWidget(tools)
        pl.addSpacing(10)
        pl.addWidget(QLabel("Oggetti della pagina", objectName="H2"))
        self.layers = QListWidget(objectName="Layers")  # every object, top to bottom: also the small or hidden ones
        self.layers.setSelectionMode(QListWidget.ExtendedSelection)
        self.layers.setIconSize(QSize(16, 16))
        self.layers.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # a long row ends in "…", no sideways scroll
        self.layers.itemSelectionChanged.connect(self._layers_picked)
        self.layers.setMouseTracking(True)
        self.layers.itemEntered.connect(self._layer_hovered)
        self.layers.viewport().installEventFilter(self)  # the mouse leaving the list: no object shown
        self.rows: list[design.Obj] = []
        pl.addWidget(self.layers, 1)
        pl.addWidget(ui.keys_button([
            ("Selezionare", [("Clic", "un oggetto"), ("Ctrl+Clic", "aggiungi o togli un oggetto dalla selezione"),
                             ("Trascina", "sul vuoto: tutto quello che sta nel riquadro")]),
            ("Spostare e disegnare", [("Frecce", "sposta di 1 pt"), ("Maiusc+Frecce", "sposta di 10 pt"),
                                      ("Alt", "tenuto mentre trascini: senza guide"),
                                      ("Maiusc", "tenuto mentre disegni: linea dritta"),
                                      ("Maiusc", "tenuto mentre ruoti: a scatti di 15°"),
                                      ("Ctrl+Rotellina", "ingrandisci o riduci la pagina")]),
            ("Modificare", [("Ctrl+C / Ctrl+V", "copia e incolla, anche su un'altra pagina"), ("Ctrl+D", "duplica"),
                            ("Canc", "elimina"), ("Ctrl+G", "raggruppa o separa"), ("Ctrl+L", "blocca o sblocca"),
                            ("Ctrl+H", "trova e sostituisci in tutto il PDF")]),
            ("File", [("Ctrl+O", "apri un PDF"), ("Ctrl+S", "salva il PDF"), ("Ctrl+Z", "annulla"),
                   ("Ctrl+Y", "ripeti"), ("F11", "schermo intero"), ("F1", "questi tasti rapidi")]),
        ]), alignment=Qt.AlignLeft)

        # center: bar, warnings, page
        bar = ui.Bar()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_open = button("Apri", "open", tip="Apri un PDF (Ctrl+O)")
        self.b_save = button("Salva PDF", "save", "Primary", "Salva il PDF modificato in un nuovo file (Ctrl+S). "
                                                             "Campi e pulsanti restano come erano.")
        self.b_forms = button("Apri in Moduli", "form", tip="Passa il PDF a Moduli per sistemare i campi")
        self.b_undo = button("", "undo", "Ghost", "Annulla (Ctrl+Z)")
        self.b_redo = button("", "redo", "Ghost", "Ripeti (Ctrl+Y)")
        self.file_label = ui.Elided(objectName="Muted")
        self.b_prev = button("", "prev", "Ghost", "Pagina precedente")
        self.b_next = button("", "next", "Ghost", "Pagina successiva")
        self.page_label = QLabel("")
        self.page_label.setMinimumWidth(self.page_label.fontMetrics().horizontalAdvance("Pag. 999/999") + 4)
        self.b_zout = button("", "zoom-out", "Ghost", "Riduci")
        self.b_zin = button("", "zoom-in", "Ghost", "Ingrandisci")
        self.b_open.clicked.connect(self.ask_open)
        self.b_save.clicked.connect(self.ask_save)
        self.b_forms.clicked.connect(self.to_forms)
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        self.b_prev.clicked.connect(lambda: self.go_page(self.page_no - 1))
        self.b_next.clicked.connect(lambda: self.go_page(self.page_no + 1))
        self.b_zout.clicked.connect(lambda: self.set_zoom(self.zoom / 1.2))
        self.b_zin.clicked.connect(lambda: self.set_zoom(self.zoom * 1.2))
        self.b_paste = button("", "paste", "Ghost", "Incolla gli oggetti copiati (Ctrl+V)")
        self.b_paste.clicked.connect(self.paste)
        self.b_find = button("", "search", "Ghost", "Trova e sostituisci un testo in tutto il PDF (Ctrl+H)")
        self.b_find.clicked.connect(self.find_replace)
        for w in (self.b_open, self.b_save, self.b_forms, self.b_undo, self.b_redo, self.b_paste, self.b_find):
            bl.addWidget(w)
        bl.addWidget(self.file_label, 1)
        for w in (self.b_prev, self.page_label, self.b_next, self.b_zout, self.b_zin):
            bl.addWidget(w)
        self.banner = QLabel(objectName="DesignBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()

        self.scene = QGraphicsScene(self)
        self.scene.selectionChanged.connect(self._selection_changed)
        self.view = PageView(self.scene, self)
        self.drop = DropZone("Apri un PDF per modificarne la grafica", glyph="page")
        self.drop.picked.connect(self.open)
        empty = QWidget(objectName="Desk")
        el = QVBoxLayout(empty)
        el.setContentsMargins(80, 60, 80, 60)
        el.addWidget(self.drop, 3)
        self.recent_box = ui.RecentFiles()
        self.recent_box.picked.connect(self.open)
        el.addWidget(self.recent_box, 2)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.view)
        self.thumbs = QListWidget(objectName="Thumbs")
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setFlow(QListWidget.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setIconSize(QSize(THUMB_W, round(THUMB_W * 1.42)))
        self.thumbs.setSpacing(4)
        self.thumbs.setFixedWidth(THUMB_W + 40)
        self.thumbs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumbs.currentRowChanged.connect(self.go_page)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(bar)
        cl.addWidget(self.banner)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self.thumbs)
        body.addWidget(self.stack, 1)
        cl.addLayout(body, 1)

        # right: properties of the selected object
        props = QFrame(objectName="Panel")
        pr = QVBoxLayout(props)
        pr.setContentsMargins(16, 16, 16, 16)
        pr.addWidget(QLabel("Proprietà", objectName="H2"))
        self.kind_label = QLabel(objectName="Muted")
        self.kind_label.setWordWrap(True)
        pr.addWidget(self.kind_label)
        pr.addSpacing(8)
        self.props = QWidget()
        self.form = QFormLayout(self.props)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.p_box = [QLineEdit() for _ in range(4)]  # x, y, width, height; a line's two ends instead
        for e in self.p_box:
            e.setValidator(QDoubleValidator(-10000, 10000, 1, e))
            e.editingFinished.connect(self._box_edited)
        row = QHBoxLayout()
        row2 = QHBoxLayout()
        self.p_labels = [QLabel() for _ in range(4)]
        for i, (lab, e) in enumerate(zip(self.p_labels, self.p_box)):
            (row if i < 2 else row2).addWidget(lab)
            (row if i < 2 else row2).addWidget(e)
        self.form.addRow(row)
        self.form.addRow(row2)
        self.p_rows = (row, row2)
        self.p_text = QLineEdit()
        self.p_text.editingFinished.connect(lambda: self.set_text(self.p_text.text()))
        self.p_size = QLineEdit()
        self.p_size.setValidator(QDoubleValidator(1, 400, 1, self.p_size))
        self.p_size.editingFinished.connect(self._size_edited)
        self.p_font = QComboBox()
        self.p_font.addItems(list(design.FONTS))
        self.p_font.setPlaceholderText("Font del PDF")  # shown when the text's own font is none of these
        self.p_font.activated.connect(self._font_edited)
        self.p_bold = button("Grassetto", None, tip="Grassetto")
        self.p_italic = button("Corsivo", None, tip="Corsivo")
        self.p_looks = QHBoxLayout()
        for b in (self.p_bold, self.p_italic):
            b.setCheckable(True)
            b.clicked.connect(self._font_edited)
            self.p_looks.addWidget(b)
        self.p_width = QLineEdit()
        self.p_width.setValidator(QDoubleValidator(0, 100, 1, self.p_width))
        self.p_width.editingFinished.connect(self._width_edited)
        self.p_color = button("", None, tip="Colore del tratto o del testo")
        self.p_fill = button("", None, tip="Colore di riempimento")
        self.p_color.clicked.connect(lambda: self._pick("color"))
        self.p_fill.clicked.connect(lambda: self._pick("fill"))
        self.form.addRow("Testo", self.p_text)
        self.form.addRow("Dimensione (pt)", self.p_size)
        self.form.addRow("Font", self.p_font)
        self.form.addRow("Stile", self.p_looks)
        self.p_anchor = QHBoxLayout()
        self.anchor = 0.0  # what stays in place when a text gets wider or narrower: 0 left edge, 0.5 middle, 1 right
        anchors = QButtonGroup(self)
        for k, glyph, tip in ((0.0, "text-left", "A sinistra"), (0.5, "text-center", "Al centro"),
                              (1.0, "text-right", "A destra")):
            b = button("", glyph, "Ghost", tip + ": quando cambi parole, font o dimensione resta fermo questo lato")
            b.setCheckable(True)
            b.setChecked(k == 0)
            b.clicked.connect(lambda _=False, k=k: setattr(self, "anchor", k))
            anchors.addButton(b)
            self.p_anchor.addWidget(b)
        self.p_anchor.addStretch()
        self.form.addRow("Allineamento", self.p_anchor)
        self.form.addRow("Spessore (pt)", self.p_width)
        self.form.addRow("Colore", self.p_color)
        self.form.addRow("Riempimento", self.p_fill)
        self.p_alpha = QLineEdit()
        self.p_alpha.setValidator(QDoubleValidator(0, 100, 0, self.p_alpha))
        self.p_alpha.editingFinished.connect(self._alpha_edited)
        self.form.addRow("Opacità (%)", self.p_alpha)
        self.p_dash = QComboBox()
        self.p_dash.addItems(DASHES)
        self.p_dash.activated.connect(self._dash_edited)
        self.form.addRow("Tratto", self.p_dash)
        self.p_heads = QHBoxLayout()
        self.p_head0 = button("All'inizio", None, tip="Una punta di freccia sul primo estremo")
        self.p_head1 = button("Alla fine", None, tip="Una punta di freccia sul secondo estremo")
        for b in (self.p_head0, self.p_head1):
            b.setCheckable(True)
            b.clicked.connect(self._heads_edited)
            self.p_heads.addWidget(b)
        self.form.addRow("Frecce", self.p_heads)
        self.p_round = QLineEdit()
        self.p_round.setValidator(QDoubleValidator(0, 1000, 1, self.p_round))
        self.p_round.editingFinished.connect(self._round_edited)
        self.form.addRow("Angoli arrotondati (pt)", self.p_round)
        self.p_image = button("Sostituisci immagine…", "image", tip="Un'altra immagine al suo posto, nelle sue "
                                                                  "proporzioni")
        self.p_image.clicked.connect(self.replace_image)
        self.form.addRow(self.p_image)
        pr.addWidget(self.props)
        # arrangement: for one object or more
        self.arrange = QWidget()
        al = QVBoxLayout(self.arrange)
        al.setContentsMargins(0, 8, 0, 0)
        al.addWidget(_muted("Allinea"))
        aligns = QHBoxLayout()
        for how, tip in (("left", "A sinistra"), ("hcenter", "Al centro"), ("right", "A destra"),
                         ("top", "In alto"), ("vcenter", "In mezzo"), ("bottom", "In basso")):
            b = button("", "align-" + how, "Ghost", tip + " (un oggetto solo: rispetto alla pagina)")
            b.clicked.connect(lambda _=False, how=how: self.align_selected(how))
            aligns.addWidget(b)
        aligns.addStretch()
        al.addLayout(aligns)
        self.spread = QWidget()
        sl = QHBoxLayout(self.spread)
        sl.setContentsMargins(0, 0, 0, 0)
        for axis, tip in (("h", "Stessa distanza tra loro, in orizzontale"), ("v", "Stessa distanza tra loro, "
                                                                                    "in verticale")):
            b = button("", "distribute-" + axis, "Ghost", tip + " (tre oggetti o più)")
            b.clicked.connect(lambda _=False, axis=axis: self.distribute_selected(axis))
            sl.addWidget(b)
        sl.addStretch()
        al.addWidget(self.spread)
        al.addWidget(_muted("Ruota (gradi, in senso orario)"))
        turns = QHBoxLayout()
        self.p_turn = QLineEdit()
        self.p_turn.setValidator(QDoubleValidator(-360, 360, 1, self.p_turn))
        self.p_turn.setPlaceholderText("es. 15")
        self.p_turn.setMaximumWidth(64)
        self.p_turn.returnPressed.connect(self._turn_edited)
        b_turn = button("Ruota", None, tip="Ruota della quantità scritta (negativa: in senso antiorario)")
        b_turn.clicked.connect(self._turn_edited)
        b_left = button("", "rotate-left", "Ghost", "Ruota di 90° in senso antiorario")
        b_right = button("", "rotate-right", "Ghost", "Ruota di 90° in senso orario")
        b_left.clicked.connect(lambda: self.rotate_selected(-90))
        b_right.clicked.connect(lambda: self.rotate_selected(90))
        for w in (self.p_turn, b_turn, b_left, b_right):
            turns.addWidget(w)
        turns.addStretch()
        al.addLayout(turns)
        self.layers_box = QWidget()
        ll = QVBoxLayout(self.layers_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(_muted("Sopra o sotto gli altri"))
        for row in ((("front", "Primo piano", "In primo piano: sopra a tutto"),
                     ("forward", "Avanti", "Porta avanti: sopra all'oggetto che gli sta sopra")),
                    (("backward", "Indietro", "Porta indietro: sotto all'oggetto che gli sta sotto"),
                     ("back", "In fondo", "Sotto a tutto (anche allo sfondo bianco, se la pagina ne ha uno)"))):
            line = QHBoxLayout()
            for where, text, tip in row:
                b = button(text, None, tip=tip)
                b.clicked.connect(lambda _=False, where=where: self.restack_selected(where))
                line.addWidget(b)
            ll.addLayout(line)
        al.addWidget(self.layers_box)
        marks = QHBoxLayout()
        self.b_group = button("Raggruppa", None, tip="Si selezionano, si spostano e si copiano insieme (Ctrl+G)")
        self.b_group.clicked.connect(self.group_selected)
        self.b_pin = button("Blocca", None, tip="Non si sposta e non si cambia per sbaglio (Ctrl+L)")
        self.b_pin.clicked.connect(self.pin_selected)
        marks.addWidget(self.b_group)
        marks.addWidget(self.b_pin)
        al.addLayout(marks)
        pr.addWidget(self.arrange)
        self.b_dup = button("Duplica", "copy", tip="Una copia accanto all'originale (Ctrl+D)")
        self.b_dup.clicked.connect(self.duplicate_selected)
        self.b_del = button("Elimina", "trash", "Danger")
        self.b_del.clicked.connect(self.delete_selected)
        acts = QHBoxLayout()
        self.b_copy = button("Copia", "copy", tip="Copia, per incollarlo anche su un'altra pagina (Ctrl+C)")
        self.b_copy.clicked.connect(self.copy_selected)
        acts.addWidget(self.b_dup)
        acts.addWidget(self.b_copy)
        acts.addWidget(self.b_del)
        pr.addLayout(acts)
        pr.addStretch()
        side = QScrollArea(objectName="PanelRight")
        self.panel = props
        side.setWidget(props)
        side.setWidgetResizable(True)
        side.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side.setMinimumWidth(250)

        main = QSplitter(Qt.Horizontal)
        for w in (palette, center, side):
            main.addWidget(w)
        main.setChildrenCollapsible(False)
        main.setStretchFactor(1, 1)
        main.setSizes([210, 900, 280])
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(main)

        QShortcut(QKeySequence.Delete, self.view, self.delete_selected)
        QShortcut(QKeySequence("Ctrl+D"), self, self.duplicate_selected)
        QShortcut(QKeySequence.Copy, self, self.copy_selected)
        QShortcut(QKeySequence.Paste, self, self.paste)
        QShortcut(QKeySequence("Ctrl+G"), self, self.group_selected)
        QShortcut(QKeySequence("Ctrl+L"), self, self.pin_selected)
        QShortcut(QKeySequence("Ctrl+H"), self, self.find_replace)
        QShortcut(QKeySequence.Open, self, self.ask_open)
        QShortcut(QKeySequence.Save, self, self.ask_save)
        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        self.refresh()

    # ---- drag & drop anywhere on the tab
    def dragEnterEvent(self, e):
        if pdf_from_drop(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        path = pdf_from_drop(e)
        if path:
            self.open(path)

    # ---- file
    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = ui.message(self, "Nell'Editor ci sono modifiche non salvate. Vuoi salvarle prima?", "ask",
                       ("Salva", "Non salvare", "Annulla"))
        return self.ask_save() if r == 0 else r == 1

    def ask_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Apri PDF", "", "PDF (*.pdf)")
        if path:
            self.open(path)

    def open(self, path: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        if not self.confirm_discard():
            return
        try:
            doc = open_pdf(path)
        except ValueError as e:
            ui.warn(self, str(e))
            return
        if self.doc:
            self.doc.close()  # else the old file stays locked
        self.doc, self.path, self.page_no, self.dirty = doc, path, 0, False
        self._undo.clear()
        self._redo.clear()
        self.selection = []
        ui.add_recent(path)
        self._fill_thumbs()
        self._load_page()

    def ask_save(self) -> bool:
        if not self.doc:
            return False
        p = Path(self.path)
        suggested = str(p if p.stem.endswith("_modificato") else p.with_name(f"{p.stem}_modificato.pdf"))
        path, _ = QFileDialog.getSaveFileName(self, "Salva PDF", suggested, "PDF (*.pdf)")
        if not path:
            return False
        try:
            self.save(path)
        except OSError as e:
            ui.warn(self, f"Salvataggio non riuscito: {e}")
            return False
        return True

    def save(self, path: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        write_atomic(path, self.doc.tobytes(garbage=3, deflate=True))
        self.path, self.dirty = path, False
        self.refresh()

    def to_forms(self):
        if self.doc and (not self.dirty or self.ask_save()):
            self.open_in_forms.emit(self.path)

    # ---- pages
    def _fill_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for page in self.doc or []:
            self.thumbs.addItem(QListWidgetItem(self._thumb(page), str(page.number + 1)))
        self.thumbs.blockSignals(False)

    def _thumb(self, page) -> QIcon:
        pix = render_page(page, THUMB_W / page.rect.width, annots=False, dpr=self.devicePixelRatioF())
        ic = QIcon(pix)
        ic.addPixmap(pix, QIcon.Selected)
        return ic

    def go_page(self, n: int) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        if self.doc and 0 <= n < self.doc.page_count and n != self.page_no:
            self.page_no = n
            self.selection = []
            self._load_page()
            self.view.verticalScrollBar().setValue(0)

    def set_zoom(self, z: float):
        self.zoom = min(max(z, 0.4), 5)
        self.refresh()

    def set_mode(self, mode: str):
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        self.view.setDragMode(QGraphicsView.RubberBandDrag if mode == "select" else QGraphicsView.NoDrag)
        self.view.viewport().setCursor(Qt.ArrowCursor if mode == "select" else Qt.CrossCursor)

    def _load_page(self, keep: list[tuple] = ()):
        """Read the page's objects again; keep: rects (PDF points) of objects to select again afterwards."""
        self.objects, warnings = design.page_objects(self.doc[self.page_no]) if self.doc else ([], [])
        self._apply_marks()
        self.selection = [o for o in self.objects if o.state != "locked" and any(_kept(o, r) for r in keep)]
        self.banner.setText(f"Pagina {self.page_no + 1}: " + " ".join(warnings) if warnings else "")
        self.banner.setVisible(bool(warnings))
        self.refresh()

    # ---- what the user does
    def select(self, objs: list[design.Obj]) -> None:
        self.selection = list(objs)
        self.refresh()

    def _apply_marks(self):
        """The groups and the locked objects of this page, from the notes in the PDF."""
        marks, p = (_marks(self.doc), str(self.page_no)) if self.doc else ({}, "")
        for o in self.objects:
            o.data.pop("pinned", None)
            if o.state != "locked" and any(_is(o, k) for k in marks.get("pinned", {}).get(p, [])):
                o.data["pinned"] = True
        found = ([o for o in self.objects if any(_is(o, k) for k in keys)] for keys in marks.get("groups", {}).get(p, []))
        self.groups = [g for g in found if len(g) > 1]

    def _follow(self, old: list, keep):
        """Groups and locks go with their objects: old = their keys before the change, keep = where they went
        (shorter: the others are gone)."""
        marks, p = _marks(self.doc), str(self.page_no)
        moved = []
        for k, r in zip(old, list(keep) + [None] * len(old)):
            o = next((o for o in self.objects if _kept(o, r)), None) if r is not None else None
            moved.append((k, _key(o) if o else None))

        def new(key):
            hit = next((n for k, n in moved if k[4] == key[4] and all(abs(a - b) < 0.15 for a, b in zip(k, key[:4]))), key)
            return hit

        groups = [[n for n in map(new, g) if n] for g in marks.get("groups", {}).get(p, [])]
        pinned = [n for n in map(new, marks.get("pinned", {}).get(p, [])) if n]
        if groups != marks.get("groups", {}).get(p, []) or pinned != marks.get("pinned", {}).get(p, []):
            marks.setdefault("groups", {})[p] = [g for g in groups if len(g) > 1]
            marks.setdefault("pinned", {})[p] = pinned
            _put_marks(self.doc, marks)
            self._apply_marks()
            self.refresh()

    def _edit(self, fn, *args, keep: list[tuple] = (), follow: list = ()) -> bool:
        """Every change goes through here: a copy for Annulla first; if the change fails, the copy comes back.
        follow: the objects changed, in the order of keep, so their groups and locks go with them."""
        snapshot = self.doc.tobytes()
        old = [_key(o) for o in follow]
        try:
            fn(self.doc[self.page_no], *args)
        except design.EditError as e:
            self._restore(snapshot)
            ui.warn(self, str(e))
            self._load_page()
            return False
        self._undo = (self._undo + [(self.page_no, snapshot)])[-UNDO_MAX:]
        self._redo.clear()
        self.dirty = True
        self.thumbs.item(self.page_no).setIcon(self._thumb(self.doc[self.page_no]))
        self._load_page(keep)
        if old:
            self._follow(old, keep)
        return True

    def _restore(self, data: bytes):
        self.doc.close()
        self.doc = pymupdf.open("pdf", data)

    def _step(self, src: list, dst: list):
        """Annulla / Ripeti: back to the copy on top of src, on the page it changed (only that thumbnail is new)."""
        self.view.flush_nudge()
        if not src:
            return
        page_no, data = src.pop()
        dst.append((page_no, self.doc.tobytes()))
        self._restore(data)
        self.dirty, self.page_no = True, page_no
        self.thumbs.item(page_no).setIcon(self._thumb(self.doc[page_no]))
        self._load_page()

    def undo(self):
        self._step(self._undo, self._redo)

    def redo(self):
        self._step(self._redo, self._undo)

    def _free(self) -> list[design.Obj]:
        """The selected objects that can change: not locked by the page, not locked by the user."""
        return [o for o in self.selection if o.state != "locked" and not o.data.get("pinned")]

    def move_selected(self, dx: float, dy: float) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._free()
        if objs:
            self._edit(design.move, objs, dx, dy, keep=[tuple(pymupdf.Rect(o.rect) + (dx, dy, dx, dy)) for o in objs],
                       follow=objs)

    def resize_object(self, obj: design.Obj, rect: tuple) -> None:
        box = pymupdf.Rect(rect).normalize() if obj.kind == "line" else pymupdf.Rect(rect)
        keep = (*box, "line") if "arrows" in obj.data else tuple(box)  # the heads stick out of the line's ends
        self._edit(design.set_rect, obj, rect, keep=[keep], follow=[obj])

    def duplicate_selected(self) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._free()
        d = COPY_OFFSET
        if objs:  # the copies are selected afterwards: they are where the originals are, moved by d
            self._edit(design.duplicate, objs, d, d, keep=[tuple(pymupdf.Rect(o.rect) + (d, d, d, d)) for o in objs])

    def copy_selected(self) -> None:
        self.view.flush_nudge()
        objs = [o for o in self.selection if o.state != "locked"]
        if not objs:
            return
        page = pymupdf.open()  # their page as it is now: what is pasted later is what was copied
        page.insert_pdf(self.doc, from_page=self.page_no, to_page=self.page_no, annots=False)
        self.clip, self.pastes = (page.tobytes(), [_key(o) for o in objs], self.page_no), 0
        self.refresh()

    def paste(self) -> None:
        self.view.flush_nudge()
        if not self.doc or not self.clip:
            return
        data, keys, page_no = self.clip
        src = pymupdf.open("pdf", data)
        objs = [o for o in design.page_objects(src[0])[0] if any(_is(o, k) for k in keys)]
        same = page_no == self.page_no  # on their own page: a little further each time, not on themselves
        self.pastes = self.pastes + 1 if same else 0
        d = COPY_OFFSET * self.pastes
        if objs:
            self._edit(design.paste, src[0], objs, d, d, keep=[tuple(pymupdf.Rect(o.rect) + (d, d, d, d)) for o in objs])

    def _lined_up(self, fn, moves: list, *args) -> None:
        objs = self._free()
        keep = {id(o): tuple(pymupdf.Rect(o.rect) + (dx, dy, dx, dy)) for o, dx, dy in moves}
        self._edit(fn, objs, *args, keep=[keep.get(id(o), o.rect) for o in objs], follow=objs)

    def align_selected(self, how: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._free()
        moves = design.alignment(self.doc[self.page_no], objs, how) if objs else []
        if moves:
            self._lined_up(design.align, moves, how)

    def distribute_selected(self, axis: str) -> None:
        self.view.flush_nudge()
        moves = design.distribution(self._free(), axis)
        if moves:
            self._lined_up(design.distribute, moves, axis)

    def rotate_selected(self, degrees: float) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._free()
        if objs and degrees % 360:
            m = design.turning(objs, degrees)
            self._edit(design.rotate, objs, degrees, keep=[(*(pymupdf.Rect(o.rect) * m), o.kind) for o in objs],
                       follow=objs)

    def _turn_edited(self):
        try:
            degrees = float(self.p_turn.text().replace(",", "."))
        except ValueError:
            return
        self.rotate_selected(degrees)

    def restack_selected(self, where: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._free()
        if len(objs) == 1 and len(self.selection) == 1:
            self._edit(design.restack, objs[0], where, keep=[objs[0].rect], follow=objs)

    def delete_selected(self) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._free()
        if objs:
            self._edit(design.delete, objs, follow=objs)

    def _set_marks(self, change) -> None:
        """Change the notes of this page (groups, locks) as one step of Annulla."""
        def apply(page):
            marks = _marks(page.parent)
            change(marks, str(self.page_no))
            _put_marks(page.parent, marks)
        self._edit(apply, keep=[o.rect for o in self.selection])

    def group_selected(self) -> None:
        self.view.flush_nudge()
        objs = [o for o in self.selection if o.state != "locked"]
        mine = [g for g in self.groups if any(o is s for o in g for s in objs)]
        if mine:  # Separa: the groups the selection is made of come apart
            gone = [_key(o) for g in mine for o in g]

            def change(marks, p):
                marks.setdefault("groups", {})[p] = [g for g in marks["groups"].get(p, [])
                                                     if not any(_same_key(k, x) for k in g for x in gone)]
        elif len(objs) > 1:
            def change(marks, p):
                marks.setdefault("groups", {}).setdefault(p, []).append([_key(o) for o in objs])
        else:
            return
        self._set_marks(change)

    def pin_selected(self) -> None:
        self.view.flush_nudge()
        objs = [o for o in self.selection if o.state != "locked"]
        if not objs:
            return
        unpin = all(o.data.get("pinned") for o in objs)
        keys = [_key(o) for o in objs]

        def change(marks, p):
            now = [k for k in marks.setdefault("pinned", {}).get(p, []) if not any(_same_key(k, x) for x in keys)]
            marks["pinned"][p] = now if unpin else now + keys
        self._set_marks(change)

    def replace_image(self) -> None:
        self.view.flush_nudge()
        objs = self._targets()
        if len(objs) != 1 or objs[0].kind != "image":
            return
        path, _ = QFileDialog.getOpenFileName(self, "Scegli un'immagine", "", "Immagini (*.png *.jpg *.jpeg)")
        if path:
            o = objs[0]
            self._edit(design.replace_image, o, Path(path).read_bytes(), keep=[(*o.rect, "image")], follow=[o])

    def find_replace(self) -> None:
        self.view.flush_nudge()
        if not self.doc:
            return
        find = ui.ask_text(self, "Trova e sostituisci", "Testo da cercare in tutto il PDF:")
        if not find:
            return
        repl = ui.ask_text(self, "Trova e sostituisci", f'Sostituisci "{find}" con:')
        if repl is None:
            return
        done, failed = self.replace_all(find, repl)
        if not done and not failed:
            return ui.info(self, f'"{find}" non c\'è nel testo del PDF.')
        ui.info(self, f"Righe cambiate: {done}." + (f" Non cambiate: {failed} (PDFlex non riesce a riscriverle)."
                                                     if failed else ""))

    def replace_all(self, find: str, repl: str) -> tuple[int, int]:
        """find replaced by repl in every line of text of the PDF (missing letters: in Helvetica), as one step of
        Annulla. Returns the lines changed and those that could not be."""
        snapshot, done, failed = self.doc.tobytes(), 0, 0
        for n in range(self.doc.page_count):
            targets = [o.rect for o in design.page_objects(self.doc[n])[0]
                       if o.kind == "text" and o.state == "edit" and find in _words(o)]
            for r in targets:  # read again each time: the line before changed the page
                page = self.doc[n]
                o = next((x for x in design.page_objects(page)[0] if x.kind == "text" and _is(x, [*r, "text"])), None)
                if o is None:
                    failed += 1
                    continue
                before = self.doc.tobytes()
                try:
                    text = _words(o).replace(find, repl)
                    if text.strip():
                        design.set_text(page, o, text, fallback=True, anchor=self.anchor)
                    else:
                        design.delete(page, [o])
                    done += 1
                except design.EditError:
                    self._restore(before)
                    failed += 1
        if done:
            self._undo = (self._undo + [(self.page_no, snapshot)])[-UNDO_MAX:]
            self._redo.clear()
            self.dirty = True
            for n in range(self.doc.page_count):
                self.thumbs.item(n).setIcon(self._thumb(self.doc[n]))
        self._load_page()
        return done, failed

    # ---- guides while dragging
    def start_drag(self) -> None:
        """What a drag can catch: edges and middles of the other objects and of the page (screen units)."""
        z = self.zoom
        chosen = {id(s) for s in self.selection}
        self.snap_rects = [QRectF(o.rect[0] * z, o.rect[1] * z, (o.rect[2] - o.rect[0]) * z, (o.rect[3] - o.rect[1]) * z)
                           for o in self.objects if id(o) not in chosen]

    def snap_drag(self, item) -> None:
        """While dragging: the selection's edges and middle catch the nearest ones in reach, a guide shows it."""
        self.end_drag()
        moving = [i for i in self.items if i.isSelected() and i.flags() & QGraphicsItem.ItemIsMovable]
        if not moving or QApplication.keyboardModifiers() & Qt.AltModifier:
            return
        d = item.pos() - item.home
        box = pymupdf.Rect(moving[0].obj.rect)
        for i in moving[1:]:
            box |= i.obj.rect
        box = box * self.zoom + (d.x(), d.y(), d.x(), d.y())
        page = self.doc[self.page_no].rect
        cx, cy, guides = ui.snap_box(QRectF(box.x0, box.y0, box.width, box.height), self.snap_rects,
                                     QRectF(0, 0, page.width * self.zoom, page.height * self.zoom), SNAP)
        for i in moving:
            i.moveBy(cx, cy)
        self.guides = ui.draw_guides(self.scene, guides)

    def end_drag(self) -> None:
        for g in self.guides:
            if g.scene() is self.scene:
                self.scene.removeItem(g)
        self.guides = []

    def _restyle(self, **style):
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._targets()
        if not objs:
            return
        page = self.doc[self.page_no]
        if "fontname" not in style and set(style) & {"color", "opacity"}:  # text written again, in its own font
            missing = "".join(dict.fromkeys("".join(design.missing_letters(page, o, _words(o)) for o in objs
                                                     if o.kind == "text")))
            if missing:
                if ui.message(self, HELVETICA.format(missing), "ask", ["Usa Helvetica", "Annulla"]) != 0:
                    return self.refresh()
                style["fontname"] = ""
        self._edit(lambda page: design.restyle(page, objs, **style), keep=[o.rect for o in objs], follow=objs)

    def set_text(self, text: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = self._targets()
        o = objs[0] if len(objs) == 1 else None
        if not o or o.kind != "text" or not text or text == _words(o):
            return
        missing = design.missing_letters(self.doc[self.page_no], o, text)
        if missing and ui.message(self, HELVETICA.format(missing), "ask", ["Usa Helvetica", "Annulla"]) != 0:
            return self.refresh()
        self._edit(lambda page: design.set_text(page, o, text, fallback=bool(missing), anchor=self.anchor),
                   keep=[o.rect], follow=[o])

    def drawn(self, rect: tuple, straight: bool = False) -> None:
        """A component drawn on the page in the current mode (rect in PDF points, as dragged)."""
        x0, y0, x1, y1 = rect
        mode = self.mode
        self.set_mode("select")
        if mode == "line":
            if straight:  # Shift: horizontal or vertical, whichever is closer
                x1, y1 = (x1, y0) if abs(x1 - x0) >= abs(y1 - y0) else (x0, y1)
            if abs(x1 - x0) + abs(y1 - y0) >= 2:
                self._edit(design.add_line, (x0, y0), (x1, y1))
        elif mode == "rect":
            r = pymupdf.Rect(rect).normalize()
            if r.width >= 2 and r.height >= 2:
                self._edit(design.add_rect, tuple(r))
        elif mode == "text":
            text = ui.ask_text(self, "Testo", "Scrivi il testo da aggiungere:")
            if text:
                self._edit(design.add_text, (x0, y0 + 11), text)
        elif mode == "image":
            path, _ = QFileDialog.getOpenFileName(self, "Scegli un'immagine", "", "Immagini (*.png *.jpg *.jpeg)")
            if path:
                r = pymupdf.Rect(rect).normalize()
                if r.width < 10 or r.height < 10:  # a click: 120 pt wide, in its own proportions
                    pix = pymupdf.Pixmap(path)
                    r = pymupdf.Rect(x0, y0, x0 + 120, y0 + 120 * pix.height / pix.width)
                self._edit(design.add_image, tuple(r), Path(path).read_bytes())

    # ---- properties
    def _box_edited(self):
        objs = self._targets()
        if len(objs) != 1 or len(self.selection) != 1:
            return
        try:
            v = [float(e.text().replace(",", ".")) for e in self.p_box]
        except ValueError:
            return
        o = objs[0]
        if o.kind == "line":
            if (v[0], v[1], v[2], v[3]) != (*o.data["p1"], *o.data["p2"]):
                self.resize_object(o, tuple(v))
        else:
            r = (v[0], v[1], v[0] + v[2], v[1] + v[3])
            if any(abs(a - b) > 0.05 for a, b in zip(r, o.rect)):
                self._edit(design.set_rect, o, r, keep=[r], follow=[o])

    def _number(self, edit: QLineEdit) -> float | None:
        try:
            return float(edit.text().replace(",", "."))
        except ValueError:
            return None

    def _size_edited(self):
        size, objs = self._number(self.p_size), self._targets()
        if size is not None and objs and all(o.kind == "text" for o in objs) and any(
                abs(size - o.data["spans"][0]["size"]) > 0.05 for o in objs):
            self._restyle(fontsize=size, anchor=self.anchor)

    def _font_edited(self):
        objs = self._targets()
        if not objs or not all(o.kind == "text" for o in objs):
            return
        family = self.p_font.currentText() if self.p_font.currentIndex() >= 0 else None
        family = family or design.font_style(self.doc[self.page_no], objs[0])[0] or "Helvetica"
        self._restyle(fontname=design.font_spec(family, self.p_bold.isChecked(), self.p_italic.isChecked()),
                      anchor=self.anchor)

    def _width_edited(self):
        width, objs = self._number(self.p_width), self._targets()
        if width is not None and objs and any(abs(width - (o.data.get("drawing", {}).get("width") or 0)) > 0.05
                                              for o in objs):
            self._restyle(width=width)

    def _alpha_edited(self):
        a, objs = self._number(self.p_alpha), self._targets()
        if a is None or not objs:
            return
        a = min(max(a, 0), 100) / 100
        page = self.doc[self.page_no]
        if any(abs(a - design.opacity(page, o)) > 0.005 for o in objs):
            self._restyle(opacity=a)

    def _dash_edited(self):
        objs = self._targets()
        if not objs:
            return
        w = max(objs[0].data.get("drawing", {}).get("width") or 1, 0.5)
        i = self.p_dash.currentIndex()
        self._restyle(dashes=() if i == 0 else (max(3, 3 * w), max(2, 2 * w)) if i == 1 else (w, max(2, 2 * w)))

    def _heads_edited(self):
        objs = self._targets()
        if len(objs) == 1 and objs[0].kind == "line":
            o = objs[0]
            self._edit(design.set_arrows, o, self.p_head0.isChecked(), self.p_head1.isChecked(),
                       keep=[(*o.rect, "line")], follow=[o])

    def _round_edited(self):
        r, objs = self._number(self.p_round), self._targets()
        if r is not None and len(objs) == 1 and objs[0].kind == "rect" and abs(r - objs[0].data.get("round", 0)) > 0.05:
            self._edit(design.set_round, objs[0], r, keep=[objs[0].rect], follow=objs)

    def _pick(self, key: str):
        objs = self._targets()
        if not objs:
            return
        o = objs[0]
        now = (pymupdf.sRGB_to_pdf(o.data["spans"][0]["color"]) if o.kind == "text"
               else o.data.get("drawing", {}).get(key)) or (0, 0, 0)
        c = QColorDialog.getColor(QColor.fromRgbF(*now), self, "Scegli il colore")
        if c.isValid():
            self._restyle(**{key: (c.redF(), c.greenF(), c.blueF())})

    def _targets(self) -> list[design.Obj]:
        """What the properties change: every selected object, when none is locked or text drawn as shapes."""
        objs = [o for o in self.selection if o.state == "edit" and not o.data.get("pinned")]
        return objs if len(objs) == len(self.selection) else []

    def _show_props(self):
        objs, free = self._targets(), self._free()
        one = objs[0] if len(objs) == 1 else None
        self.props.setVisible(bool(objs))
        for b in (self.b_del, self.b_dup, self.b_copy):
            b.setVisible(bool(free))
        self.arrange.setVisible(bool(self.selection) and all(o.state != "locked" for o in self.selection))
        self.layers_box.setVisible(len(free) == 1 and len(self.selection) == 1)
        self.spread.setVisible(len(free) >= 3)
        grouped = any(o is s for g in self.groups for o in g for s in self.selection)
        self.b_group.setText("Separa" if grouped else "Raggruppa")
        self.b_group.setEnabled(grouped or len(self.selection) > 1)
        self.b_pin.setText("Sblocca" if self.selection and all(o.data.get("pinned") for o in self.selection)
                           else "Blocca")
        if not self.selection:
            self.kind_label.setText("Seleziona un oggetto sulla pagina, o scegli uno strumento per aggiungerne uno.")
            return
        if any(o.data.get("pinned") for o in self.selection):
            self.kind_label.setText("Bloccato: sbloccalo per spostarlo o cambiarlo.")
        elif len(self.selection) > 1:
            self.kind_label.setText(f"{len(self.selection)} oggetti selezionati: trascinali o usa le frecce."
                                    + (" Le proprietà qui sotto cambiano tutti insieme." if objs else ""))
        else:
            o = self.selection[0]
            self.kind_label.setText(KINDS[o.kind] + ("" if o.state == "edit" else AS_SHAPES))
        if not objs:
            return
        first, page = objs[0], self.doc[self.page_no]
        texts = all(o.kind == "text" for o in objs)
        drawings = all("drawing" in o.data for o in objs)
        stroked = drawings and all(o.data["drawing"].get("color") is not None for o in objs)
        for row in self.p_rows:
            self.form.setRowVisible(row, one is not None)
        self.form.setRowVisible(self.p_text, one is not None and texts)
        for w in (self.p_size, self.p_font, self.p_looks, self.p_anchor):
            self.form.setRowVisible(w, texts)
        self.form.setRowVisible(self.p_width, stroked)
        self.form.setRowVisible(self.p_dash, stroked)
        self.form.setRowVisible(self.p_color, texts or drawings)
        self.form.setRowVisible(self.p_fill, drawings and all(o.kind in ("rect", "shape") for o in objs))
        self.form.setRowVisible(self.p_heads, one is not None and one.kind == "line" and drawings)
        self.form.setRowVisible(self.p_round, one is not None and one.kind == "rect" and drawings)
        self.form.setRowVisible(self.p_image, one is not None and one.kind == "image")
        self.p_alpha.setText(f"{design.opacity(page, first) * 100:.0f}")
        if one is not None:
            line = one.kind == "line"
            r = pymupdf.Rect(one.rect)
            values = [*one.data["p1"], *one.data["p2"]] if line else [r.x0, r.y0, r.width, r.height]
            for lab, text in zip(self.p_labels, ("X1", "Y1", "X2", "Y2") if line else ("X", "Y", "Larg.", "Alt.")):
                lab.setText(text)
            for e, v in zip(self.p_box, values):
                e.setText(f"{v:.1f}")
            for e in (self.p_box[2], self.p_box[3]):  # text grows from its size, not from its box
                e.setEnabled(one.kind != "text")
            heads = one.data.get("arrows", (False, False))
            self.p_head0.setChecked(heads[0])
            self.p_head1.setChecked(heads[1])
            self.p_round.setText(f"{one.data.get('round', 0):.1f}")
        if texts:
            if one is not None:
                self.p_text.setText(_words(one).replace("\xa0", " "))
            self.p_size.setText(f"{first.data['spans'][0]['size']:.1f}")
            family, bold, italic = design.font_style(page, first)
            self.p_font.setCurrentIndex(self.p_font.findText(family) if family else -1)
            self.p_bold.setChecked(bold)
            self.p_italic.setChecked(italic)
            self._swatch(self.p_color, _hex(pymupdf.sRGB_to_pdf(first.data["spans"][0]["color"])))
        elif drawings:
            drawing = first.data["drawing"]
            self.p_width.setText(f"{drawing.get('width') or 0:.1f}")
            self._swatch(self.p_color, _hex(drawing.get("color")))
            self._swatch(self.p_fill, _hex(drawing.get("fill")))
            dashes, w = design.dash_style(first), drawing.get("width") or 1
            self.p_dash.setCurrentIndex(0 if not dashes else 2 if dashes[0] <= 1.5 * w else 1)

    @staticmethod
    def _swatch(b, hex_color: str):
        b.setText(hex_color.upper() if hex_color else "Nessuno")
        b.setIcon(ui.dot(hex_color) if hex_color else QIcon())

    # ---- drawing the page
    def _whole_groups(self, objs: list) -> list:
        """objs with the rest of their groups, also selected on the page."""
        more = [o for g in self.groups if any(o is s for o in g for s in objs) for o in g
                if not any(o is s for s in objs)]
        if more:
            self.scene.blockSignals(True)
            for i in self.items:
                if any(i.obj is o for o in more):
                    i.setSelected(True)
            self.scene.blockSignals(False)
        return list(objs) + more

    def _selection_changed(self):
        self.selection = self._whole_groups([i.obj for i in self.items if i.isSelected()])
        self._mark_layers()
        self._show_props()

    def _fill_layers(self):
        self.layers.blockSignals(True)
        self.layers.clear()
        self.rows = sorted(self.objects, key=lambda o: (round(o.rect[1]), o.rect[0]))  # as they are read
        for o in self.rows:
            look = o.data.get("drawing", {})
            tint = look.get("fill") or look.get("color")
            it = QListWidgetItem(ui.dot(_hex(tint)) if tint and o.state != "move" else ui.icon(GLYPHS[o.kind]),
                                 _label(o))
            if o.state == "locked":
                it.setFlags(it.flags() & ~Qt.ItemIsSelectable)
                it.setToolTip(f"🔒 {o.reason}")
            elif o.state == "move":
                it.setToolTip("Testo disegnato come forme: si sposta, ma le parole non si possono cambiare")
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        self._mark_layers()

    def _layer_hovered(self, it):
        """The row under the mouse shows its object on the page, as when the mouse is on the object itself."""
        o = self.rows[self.layers.row(it)] if it is not None else None
        for i in self.items:
            on = i.obj is o
            if i.hover != on:
                i.hover = on
                i.update()

    def eventFilter(self, obj, e):
        if obj is self.layers.viewport() and e.type() == QEvent.Leave:
            self._layer_hovered(None)
        return super().eventFilter(obj, e)

    def showEvent(self, e):
        super().showEvent(e)
        self.panel.layout().invalidate()  # laid out while hidden, at another width: Qt would keep that one

    def _mark_layers(self):
        """The list shows the selection made on the page."""
        self.layers.blockSignals(True)
        first = None
        for i, o in enumerate(self.rows):
            on = any(o is s for s in self.selection)
            self.layers.item(i).setSelected(on)
            first = i if on and first is None else first
        if first is not None:
            self.layers.scrollToItem(self.layers.item(first))
        self.layers.blockSignals(False)

    def _layers_picked(self):
        """Objects picked in the list: selected on the page, and shown."""
        picked = [self.rows[self.layers.row(i)] for i in self.layers.selectedItems()]
        self.scene.blockSignals(True)
        for item in self.items:
            item.setSelected(any(item.obj is o for o in picked))
        self.scene.blockSignals(False)
        self.selection = self._whole_groups(picked)
        shown = next((i for i in self.items if i.isSelected()), None)
        if shown:
            self.view.ensureVisible(shown, 60, 60)
        self._show_props()

    def refresh(self):
        self.scene.blockSignals(True)
        self.scene.clear()
        self.items = []
        has = self.doc is not None
        self.stack.setCurrentIndex(1 if has else 0)
        self.thumbs.setVisible(has)
        for w in (self.b_save, self.b_forms, self.b_prev, self.b_next, self.b_zin, self.b_zout):
            w.setEnabled(has)
        self.b_undo.setEnabled(bool(self._undo))
        self.b_paste.setEnabled(has and self.clip is not None)
        self.b_find.setEnabled(has)
        self.b_redo.setEnabled(bool(self._redo))
        if has:
            self.file_label.setText(Path(self.path).name + ("  (modificato)" if self.dirty else ""))
            self.page_label.setText(f"Pag. {self.page_no + 1}/{self.doc.page_count}")
            self.b_prev.setEnabled(self.page_no > 0)
            self.b_next.setEnabled(self.page_no < self.doc.page_count - 1)
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(self.page_no)
            self.thumbs.blockSignals(False)
            page = self.doc[self.page_no]
            pix = QGraphicsPixmapItem(render_page(page, self.zoom, annots=False, dpr=self.devicePixelRatioF()))
            page_shadow(pix)
            self.scene.addItem(pix)
            w, h = page.rect.width * self.zoom, page.rect.height * self.zoom
            self.scene.setSceneRect(-PAGE_MARGIN, -PAGE_MARGIN, w + 2 * PAGE_MARGIN, h + 2 * PAGE_MARGIN)
            self.view.setSceneRect(self.scene.sceneRect())
            # big objects first, so a line on a box or a word on a band is on top and gets the click
            for o in sorted(self.objects, key=lambda o: -pymupdf.Rect(o.rect).get_area()):
                item = ObjItem(o, self.zoom, self, page.rotation_matrix,
                               big=pymupdf.Rect(o.rect).get_area() > 0.4 * page.rect.get_area())
                self.scene.addItem(item)
                item.setSelected(any(o is s for s in self.selection))
                self.items.append(item)
        else:
            self.file_label.setText("")
            self.page_label.setText("")
            self.recent_box.fill()
        self.scene.blockSignals(False)
        self._fill_layers()
        self._show_props()
