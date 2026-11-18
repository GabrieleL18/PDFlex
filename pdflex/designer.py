"""Editor tab: move, resize and add the graphics of a PDF before its fields are made in Moduli.

The page on screen is always the real PDF (pdflex.design changes it in place); fields and buttons are not shown
and never touched, so they are all still there in the saved file."""
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDoubleValidator, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsItem, QGraphicsPixmapItem,
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
                and (box + (-1.5, -1.5, 1.5, 1.5)).contains(mid))
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
    elif o.state == "move":
        text = "Testo disegnato come forme"
    else:
        r = pymupdf.Rect(o.rect)
        text = f"{KINDS[o.kind]}  {r.width:.0f} × {r.height:.0f} pt"
    return ("🔒 " if o.state == "locked" else "") + text


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
        else:
            self.setFlags(QGraphicsItem.ItemIsSelectable | (QGraphicsItem.GraphicsItemFlag(0) if big
                                                            else QGraphicsItem.ItemIsMovable))
            self.setToolTip(KINDS[obj.kind] + ("" if obj.state == "edit" else AS_SHAPES))
            if not big:
                self.setCursor(Qt.SizeAllCursor)

    def handles(self) -> list[QPointF]:
        """Local points the user can drag to resize: a line's ends, a box's corners and sides."""
        if self.obj.state == "locked" or self.obj.kind == "text":
            return []  # text grows from its size in the properties, not stretched
        if self.obj.kind == "line":
            return [QPointF(*self.obj.data[k]) * self.zoom - self.pos() for k in ("p1", "p2")]
        r = self.rect()
        return [QPointF(r.left() + fx * r.width(), r.top() + fy * r.height()) for fx, fy in SPOTS]

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
        elif self.hover:
            p.setPen(QPen(QColor(ACCENT), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.rect())

    def boundingRect(self):
        return self.rect().adjusted(-HANDLE, -HANDLE, HANDLE, HANDLE)

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

    def hoverMoveEvent(self, e):
        i = self._handle_at(e.pos()) if self.isSelected() else None
        if i is not None:
            self.setCursor(Qt.CrossCursor if self.obj.kind == "line" else CURSORS[i])
        elif self.obj.state != "locked":
            self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, e):
        self.grab = self._handle_at(e.pos()) if self.isSelected() else None
        self.start, self.start_rect = self.pos(), QRectF(self.rect())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.grab is None:
            return super().mouseMoveEvent(e)
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
        super().mouseReleaseEvent(e)
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
        items = [i for i in self.tab.items if i.isSelected() and i.obj.state != "locked"]
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
        self.layers.itemSelectionChanged.connect(self._layers_picked)
        self.rows: list[design.Obj] = []
        pl.addWidget(self.layers, 1)
        keys = QLabel("Trascina sul vuoto o Ctrl+clic: più oggetti (quelli tutti dentro il riquadro, "
                      "anche uno sfondo grande)\nFrecce: sposta di 1 pt, Maiusc+frecce di 10\n"
                      "Maiusc mentre disegni: linea dritta\nCanc elimina\nCtrl+Z annulla, Ctrl+Y ripete",
                      objectName="Muted")
        keys.setWordWrap(True)
        pl.addWidget(keys)

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
        self.file_label = QLabel("", objectName="Muted")
        self.file_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
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
        for w in (self.b_open, self.b_save, self.b_forms, self.b_undo, self.b_redo):
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
        el.addWidget(self.drop)
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
        pr.addWidget(self.arrange)
        self.b_dup = button("Duplica", "copy", tip="Una copia accanto all'originale (Ctrl+D)")
        self.b_dup.clicked.connect(self.duplicate_selected)
        self.b_del = button("Elimina", "trash", "Danger")
        self.b_del.clicked.connect(self.delete_selected)
        acts = QHBoxLayout()
        acts.addWidget(self.b_dup)
        acts.addWidget(self.b_del)
        pr.addLayout(acts)
        pr.addStretch()
        side = QScrollArea(objectName="PanelRight")
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
        self.selection = [o for o in self.objects if o.state != "locked" and any(_kept(o, r) for r in keep)]
        self.banner.setText(f"Pagina {self.page_no + 1}: " + " ".join(warnings) if warnings else "")
        self.banner.setVisible(bool(warnings))
        self.refresh()

    # ---- what the user does
    def select(self, objs: list[design.Obj]) -> None:
        self.selection = list(objs)
        self.refresh()

    def _edit(self, fn, *args, keep: list[tuple] = ()) -> bool:
        """Every change goes through here: a copy for Annulla first; if the change fails, the copy comes back."""
        snapshot = self.doc.tobytes()
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

    def move_selected(self, dx: float, dy: float) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = [o for o in self.selection if o.state != "locked"]
        if objs:
            self._edit(design.move, objs, dx, dy,
                       keep=[tuple(pymupdf.Rect(o.rect) + (dx, dy, dx, dy)) for o in objs])

    def resize_object(self, obj: design.Obj, rect: tuple) -> None:
        box = pymupdf.Rect(rect).normalize() if obj.kind == "line" else pymupdf.Rect(rect)
        self._edit(design.set_rect, obj, rect, keep=[tuple(box)])

    def duplicate_selected(self) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = [o for o in self.selection if o.state != "locked"]
        d = COPY_OFFSET
        if objs:  # the copies are selected afterwards: they are where the originals are, moved by d
            self._edit(design.duplicate, objs, d, d, keep=[tuple(pymupdf.Rect(o.rect) + (d, d, d, d)) for o in objs])

    def align_selected(self, how: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = [o for o in self.selection if o.state != "locked"]
        moves = design.alignment(self.doc[self.page_no], objs, how) if objs else []
        if moves:
            moved = {id(o) for o, _, _ in moves}
            keep = [tuple(pymupdf.Rect(o.rect) + (dx, dy, dx, dy)) for o, dx, dy in moves]
            self._edit(design.align, objs, how, keep=keep + [o.rect for o in objs if id(o) not in moved])

    def rotate_selected(self, degrees: float) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = [o for o in self.selection if o.state != "locked"]
        if objs and degrees % 360:
            m = design.turning(objs, degrees)
            self._edit(design.rotate, objs, degrees, keep=[(*(pymupdf.Rect(o.rect) * m), o.kind) for o in objs])

    def _turn_edited(self):
        try:
            degrees = float(self.p_turn.text().replace(",", "."))
        except ValueError:
            return
        self.rotate_selected(degrees)

    def restack_selected(self, where: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        if len(self.selection) == 1 and self.selection[0].state != "locked":
            o = self.selection[0]
            self._edit(design.restack, o, where, keep=[o.rect])

    def delete_selected(self) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        objs = [o for o in self.selection if o.state != "locked"]
        if objs:
            self._edit(design.delete, objs)

    def _restyle(self, **style):
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        o = self.selection[0]
        if o.kind == "text" and "fontname" not in style and set(style) & {"color"}:
            words = "".join(s["text"] for s in o.data["spans"])
            if design.missing_letters(self.doc[self.page_no], o, words):
                if ui.message(self, HELVETICA.format(design.missing_letters(self.doc[self.page_no], o, words)),
                              "ask", ["Usa Helvetica", "Annulla"]) != 0:
                    return self.refresh()
                style["fontname"] = ""
        self._edit(lambda page: design.restyle(page, o, **style), keep=[o.rect])

    def set_text(self, text: str) -> None:
        self.view.flush_nudge()  # the arrows' move first: what follows is worked out on the page after it
        o = self.selection[0] if len(self.selection) == 1 else None
        if not o or o.kind != "text" or not text or text == "".join(s["text"] for s in o.data["spans"]):
            return
        missing = design.missing_letters(self.doc[self.page_no], o, text)
        if missing and ui.message(self, HELVETICA.format(missing), "ask", ["Usa Helvetica", "Annulla"]) != 0:
            return self.refresh()
        self._edit(lambda page: design.set_text(page, o, text, fallback=bool(missing), anchor=self.anchor),
                   keep=[o.rect])

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
        if len(self.selection) != 1:
            return
        try:
            v = [float(e.text().replace(",", ".")) for e in self.p_box]
        except ValueError:
            return
        o = self.selection[0]
        if o.kind == "line":
            if (v[0], v[1], v[2], v[3]) != (*o.data["p1"], *o.data["p2"]):
                self.resize_object(o, tuple(v))
        else:
            r = (v[0], v[1], v[0] + v[2], v[1] + v[3])
            if any(abs(a - b) > 0.05 for a, b in zip(r, o.rect)):
                self._edit(design.set_rect, o, r, keep=[r])

    def _size_edited(self):
        try:
            size = float(self.p_size.text().replace(",", "."))
        except ValueError:
            return
        o = self.selection[0] if len(self.selection) == 1 else None
        if o and o.kind == "text" and abs(size - o.data["spans"][0]["size"]) > 0.05:
            self._restyle(fontsize=size, anchor=self.anchor)

    def _font_edited(self):
        o = self.selection[0] if len(self.selection) == 1 else None
        if not o or o.kind != "text":
            return
        family = self.p_font.currentText() if self.p_font.currentIndex() >= 0 else None
        family = family or design.font_style(self.doc[self.page_no], o)[0] or "Helvetica"
        self._restyle(fontname=design.font_spec(family, self.p_bold.isChecked(), self.p_italic.isChecked()),
                      anchor=self.anchor)

    def _width_edited(self):
        try:
            width = float(self.p_width.text().replace(",", "."))
        except ValueError:
            return
        o = self.selection[0] if len(self.selection) == 1 else None
        if o and "drawing" in o.data and abs(width - (o.data["drawing"].get("width") or 0)) > 0.05:
            self._restyle(width=width)

    def _pick(self, key: str):
        o = self.selection[0]
        now = (pymupdf.sRGB_to_pdf(o.data["spans"][0]["color"]) if o.kind == "text"
               else o.data["drawing"].get(key)) or (0, 0, 0)
        c = QColorDialog.getColor(QColor.fromRgbF(*now), self, "Scegli il colore")
        if c.isValid():
            self._restyle(**{key: (c.redF(), c.greenF(), c.blueF())})

    def _show_props(self):
        one = self.selection[0] if len(self.selection) == 1 else None
        editable = one is not None and one.state == "edit"
        self.props.setVisible(editable)
        self.b_del.setVisible(bool(self.selection))
        self.b_dup.setVisible(bool(self.selection))
        self.arrange.setVisible(bool(self.selection) and all(o.state != "locked" for o in self.selection))
        self.layers_box.setVisible(len(self.selection) == 1)
        if not self.selection:
            self.kind_label.setText("Seleziona un oggetto sulla pagina, o scegli uno strumento per aggiungerne uno.")
            return
        if not one:
            self.kind_label.setText(f"{len(self.selection)} oggetti selezionati: trascinali o usa le frecce.")
            return
        self.kind_label.setText(KINDS[one.kind] + ("" if editable else AS_SHAPES))
        if not editable:
            return
        line = one.kind == "line"
        r = pymupdf.Rect(one.rect)
        values = [*one.data["p1"], *one.data["p2"]] if line else [r.x0, r.y0, r.width, r.height]
        for lab, text in zip(self.p_labels, ("X1", "Y1", "X2", "Y2") if line else ("X", "Y", "Larg.", "Alt.")):
            lab.setText(text)
        for e, v in zip(self.p_box, values):
            e.setText(f"{v:.1f}")
        text = one.kind == "text"
        drawing = one.data.get("drawing", {})
        self.form.setRowVisible(self.p_text, text)
        self.form.setRowVisible(self.p_size, text)
        self.form.setRowVisible(self.p_font, text)
        self.form.setRowVisible(self.p_looks, text)
        self.form.setRowVisible(self.p_anchor, text)
        self.form.setRowVisible(self.p_width, "drawing" in one.data and drawing.get("color") is not None)
        self.form.setRowVisible(self.p_color, text or "drawing" in one.data)
        self.form.setRowVisible(self.p_fill, one.kind in ("rect", "shape") and "drawing" in one.data)
        for e in (self.p_box[2], self.p_box[3]):  # text grows from its size, not from its box
            e.setEnabled(not text)
        if text:
            self.p_text.setText("".join(s["text"] for s in one.data["spans"]).replace("\xa0", " "))
            self.p_size.setText(f"{one.data['spans'][0]['size']:.1f}")
            family, bold, italic = design.font_style(self.doc[self.page_no], one)
            self.p_font.setCurrentIndex(self.p_font.findText(family) if family else -1)
            self.p_bold.setChecked(bold)
            self.p_italic.setChecked(italic)
            self._swatch(self.p_color, _hex(pymupdf.sRGB_to_pdf(one.data["spans"][0]["color"])))
        else:
            self.p_width.setText(f"{drawing.get('width') or 0:.1f}")
            self._swatch(self.p_color, _hex(drawing.get("color")))
            self._swatch(self.p_fill, _hex(drawing.get("fill")))

    @staticmethod
    def _swatch(b, hex_color: str):
        b.setText(hex_color.upper() if hex_color else "Nessuno")
        b.setIcon(ui.dot(hex_color) if hex_color else QIcon())

    # ---- drawing the page
    def _selection_changed(self):
        self.selection = [i.obj for i in self.items if i.isSelected()]
        self._mark_layers()
        self._show_props()

    def _fill_layers(self):
        self.layers.blockSignals(True)
        self.layers.clear()
        self.rows = sorted(self.objects, key=lambda o: (round(o.rect[1]), o.rect[0]))  # as they are read
        for o in self.rows:
            it = QListWidgetItem(ui.icon(GLYPHS[o.kind]), _label(o))
            if o.state == "locked":
                it.setFlags(it.flags() & ~Qt.ItemIsSelectable)
                it.setToolTip(f"🔒 {o.reason}")
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        self._mark_layers()

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
        self.selection = picked
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
        self.scene.blockSignals(False)
        self._fill_layers()
        self._show_props()
