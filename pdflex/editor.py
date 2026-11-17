import copy
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import pymupdf

from pdflex import open_pdf

FIELD_KINDS = ("text", "multiline", "checkbox", "radio", "dropdown", "date", "signature")
DATE_SCRIPT = 'AFDate_FormatEx("dd/mm/yyyy");'

_TYPES = {
    "text": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "multiline": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "date": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "checkbox": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "radio": pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
    "dropdown": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
}


@dataclass
class Field:
    kind: str
    page: int
    rect: tuple[float, float, float, float]
    name: str
    required: bool = False
    default: str = ""
    font_size: float = 0
    options: list[str] = dc_field(default_factory=list)
    value: str = ""
    fmt: str = ""  # "", "number", "amount", "email" (text fields)
    calc: list[str] = dc_field(default_factory=list)  # names of the fields this one sums
    # where it came from in the source PDF (0 = drawn here); not part of equality
    xref: int = dc_field(default=0, compare=False, repr=False)
    parent: int = dc_field(default=0, compare=False, repr=False)
    orig: "Field | None" = dc_field(default=None, compare=False, repr=False)


def to_view(page, rect) -> tuple:
    """PDF (unrotated) coordinates -> what the user sees on a possibly rotated page."""
    return tuple((pymupdf.Rect(rect) * page.rotation_matrix).normalize())


def from_view(page, rect) -> tuple:
    return tuple((pymupdf.Rect(rect) * page.derotation_matrix).normalize())


def suggest_save_path(path: str | Path) -> str:
    p = Path(path)
    return str(p if p.stem.endswith("_modulo") else p.with_name(p.stem + "_modulo.pdf"))


def clean_value(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9_-]", "_", s)
    return s or "opzione"


def _xref_ref(doc, xref, key):
    t, v = doc.xref_get_key(xref, key)
    return int(v.split()[0]) if t == "xref" else 0


FORMATS = {"": "Testo libero", "number": "Numero", "amount": "Importo in euro", "email": "Email"}
_FMT_JS = {  # (format, keystroke, validate); 2 decimals, Italian separators (1.234,56)
    "number": ('AFNumber_Format(2, 2, 0, 0, "", true);', 'AFNumber_Keystroke(2, 2, 0, 0, "", true);', None),
    # € stays a JS escape: viewers misread a raw UTF-8 euro sign inside scripts
    "amount": ('AFNumber_Format(2, 2, 0, 0, "\\u20AC ", true);', 'AFNumber_Keystroke(2, 2, 0, 0, "", true);', None),
    "email": (None, None, 'if (event.value && !/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(event.value)) '
                          '{ app.alert("Indirizzo email non valido"); event.rc = false; }'),
}


def _field_js(doc, w) -> dict[str, str]:
    """JavaScript of the field's actions (F format, K keystroke, V validate, C calculate).

    PyMuPDF writes them but does not read them back, so read the objects directly."""
    out = {}
    for holder in (w.xref, _xref_ref(doc, w.xref, "Parent")):
        if not holder:
            continue
        for key in ("F", "K", "V", "C"):
            js = _xref_ref(doc, holder, f"AA/{key}")
            if not js or key in out:
                continue
            t, v = doc.xref_get_key(js, "JS")
            if t == "xref":
                out[key] = (doc.xref_stream(int(v.split()[0])) or b"").decode("latin-1")
            elif t == "string":
                out[key] = v
    return out


def _is_date(doc, w) -> bool:
    return "AFDate" in _field_js(doc, w).get("F", "")


def load_fields(doc: pymupdf.Document) -> list[Field]:
    out = []
    for page in doc:
        for w in page.widgets():
            kind = next((k for k, t in _TYPES.items() if t == w.field_type), None)
            if kind is None:
                continue  # list box / push button: not editable here, kept untouched on save
            js = _field_js(doc, w) if kind == "text" else {}
            if kind == "text":
                if "AFDate" in js.get("F", ""):
                    kind = "date"
                elif w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
                    kind = "multiline"
            f = Field(kind, page.number, tuple(w.rect), w.field_name or "",
                      required=bool(w.field_flags & pymupdf.PDF_FIELD_IS_REQUIRED),
                      font_size=w.text_fontsize or 0,
                      xref=w.xref, parent=_xref_ref(doc, w.xref, "Parent"))
            if kind in ("checkbox", "radio"):
                on = w.on_state()
                f.value = on if kind == "radio" else ""
                f.default = "1" if w.field_value in (True, on, "Yes") else ""
            elif kind == "dropdown":
                f.options = [c if isinstance(c, str) else c[0] for c in (w.choice_values or [])]
                f.default = w.field_value or ""
            elif kind != "signature":
                f.default = w.field_value or ""
            if "AFNumber_Format" in js.get("F", ""):
                f.fmt = "amount" if "u20AC" in js["F"] else "number"
            elif "app.alert" in js.get("V", "") and "@" in js["V"]:
                f.fmt = "email"
            m = re.search(r"AFSimple_Calculate\(\s*\"SUM\"\s*,\s*new Array\((.*?)\)", js.get("C", ""))
            if m:
                f.calc = re.findall(r'"([^"]+)"', m.group(1))
            f.orig = copy.deepcopy(f)
            out.append(f)
    return out


def _check_names(fields):
    if any(not f.name.strip() for f in fields):
        raise ValueError("Ogni campo deve avere un nome.")
    by_name = {}
    for f in fields:
        by_name.setdefault(f.name, []).append(f)
    dup = []
    for name, group in by_name.items():
        kinds = {f.kind for f in group}
        if len(kinds) > 1 and "radio" in kinds:
            dup.append(name)
        elif kinds != {"radio"} and len(group) > 1:
            # the same field shown on several pages (one parent in the file) is fine
            parents = {f.parent for f in group}
            if 0 in parents or len(parents) > 1:
                dup.append(name)
    if dup:
        raise ValueError("Nomi di campo duplicati: " + ", ".join(sorted(dup)))
    missing = sorted({n for f in fields for n in f.calc if n not in by_name})
    if missing:
        raise ValueError("La somma usa campi che non esistono: " + ", ".join(missing))
    bad = []
    for name, group in by_name.items():
        values = [clean_value(f.value) for f in group if f.kind == "radio"]
        if len(values) != len(set(values)) or "Off" in values:
            bad.append(name)
    if bad:
        raise ValueError("Nel gruppo di scelta " + ", ".join(sorted(bad)) +
                         ' due opzioni hanno lo stesso valore, oppure una vale "Off" (riservato).')


def _new_widget(f: Field) -> pymupdf.Widget:
    w = pymupdf.Widget()
    w.field_type = _TYPES[f.kind]
    w.field_name = f.name
    w.rect = pymupdf.Rect(f.rect)
    w.text_fontsize = f.font_size
    w.border_width = 1
    w.border_color = (0.6, 0.6, 0.6)  # visible box for whoever fills the form
    if f.required:
        w.field_flags |= pymupdf.PDF_FIELD_IS_REQUIRED
    if f.kind == "multiline":
        w.field_flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
    if f.kind == "date":
        w.script_format = DATE_SCRIPT
    fmt, stroke, validate = _FMT_JS.get(f.fmt, (None, None, None))
    if f.kind in ("text", "multiline") and f.fmt:
        w.script_format, w.script_stroke, w.script_change = fmt, stroke, validate
    if f.calc:
        names = ", ".join(f'"{n}"' for n in f.calc)
        w.script_calc = f"AFSimple_Calculate(\"SUM\", new Array({names}));"
        w.field_flags |= pymupdf.PDF_FIELD_IS_READ_ONLY  # a total is computed, not typed
    if f.kind == "dropdown":
        w.choice_values = list(f.options)
    if f.kind in ("text", "multiline", "date") or (f.kind == "dropdown" and f.default in f.options):
        w.field_value = f.default
    if f.kind == "checkbox":
        w.field_value = f.default == "1"
    return w


def _inherited(doc, f: Field, key: str):
    """Field-level keys (Ff, DA, Opt) may sit on the widget or on its parent: read the one in use."""
    for x in (f.xref, f.parent):
        if x and doc.xref_get_key(x, key)[0] != "null":
            return doc.xref_get_key(x, key)[1]
    return None


def _set_inherited(doc, f: Field, key: str, value: str) -> None:
    for x in {f.xref, f.parent} - {0}:
        doc.xref_set_key(x, key, value)


def _update_widget(doc, f: Field) -> None:
    """Change only what the user edited, so properties PDFlex does not show survive."""
    o = f.orig
    page = doc[f.page]  # keep the page alive: the widget only holds a weak reference to it
    w = page.load_widget(f.xref)
    if f.rect != o.rect:
        w.rect = pymupdf.Rect(f.rect)
    if f.default != o.default:
        if f.kind == "checkbox":
            w.field_value = f.default == "1"
        elif f.kind != "radio":
            w.field_value = f.default
    w.update()
    # PyMuPDF 2.1 writes these on the widget only, but viewers read them from the parent field
    if f.name != o.name:
        doc.xref_set_key(f.parent or f.xref, "T", pymupdf.get_pdf_str(f.name))
    if f.required != o.required:
        flags = int(_inherited(doc, f, "Ff") or 0)
        _set_inherited(doc, f, "Ff", str(flags ^ pymupdf.PDF_FIELD_IS_REQUIRED))
    if f.font_size != o.font_size:
        da = _inherited(doc, f, "DA") or "0 0 0 rg /Helv 0 Tf"
        da = re.sub(r"[\d.]+ Tf", f"{f.font_size:g} Tf", da)
        _set_inherited(doc, f, "DA", pymupdf.get_pdf_str(da))
    if f.options != o.options:
        _set_inherited(doc, f, "Opt", "[" + "".join(pymupdf.get_pdf_str(x) for x in f.options) + "]")


def _set_radio_state(doc, xref, old, new, checked, was_checked):
    for key in ("AP/N", "AP/D"):
        t, obj = doc.xref_get_key(xref, key)
        if t == "dict":
            doc.xref_set_key(xref, key, obj.replace(f"/{old}", f"/{new}"))
    doc.xref_set_key(xref, "AS", f"/{new}" if checked else "/Off")
    parent = _xref_ref(doc, xref, "Parent")
    if parent and checked:
        doc.xref_set_key(parent, "V", f"/{new}")
    elif parent and (was_checked or doc.xref_get_key(parent, "V")[1] in ("/None", "null")):
        doc.xref_set_key(parent, "V", "/Off")


def build_pdf(src_path, fields: list[Field]) -> pymupdf.Document:
    _check_names(fields)
    doc = open_pdf(src_path)
    keep = {f.xref for f in fields if f.xref}
    for page in doc:
        for w in list(page.widgets()):
            # fields the user deleted go; list boxes / push buttons are kept as they are
            if w.field_type in _TYPES.values() and w.xref not in keep:
                page.delete_widget(w)
    placed = {}  # id(field) -> widget xref, for the tab order
    order = []
    for f in fields:
        if f.xref and (f.fmt != f.orig.fmt or f.calc != f.orig.calc):
            # ponytail: format/sum scripts can't be edited in place reliably; the widget is rebuilt,
            # which drops properties PDFlex doesn't show (max length, tooltip) for this one field
            page = doc[f.page]
            page.delete_widget(page.load_widget(f.xref))
            f = copy.copy(f)
            f.xref, f.orig, f.parent = 0, None, 0
            order.append(f)
        else:
            order.append(f)
        if f.xref:
            placed[id(f)] = f.xref
            if f == f.orig:
                continue
            _update_widget(doc, f)
            if f.kind == "radio" and (f.value != f.orig.value or f.default != f.orig.default):
                _set_radio_state(doc, f.xref, f.orig.value, clean_value(f.value), f.default == "1",
                                 f.orig.default == "1")
            continue
        annot = doc[f.page].add_widget(_new_widget(f))
        placed[id(f)] = annot.xref
        if f.kind == "radio":
            # ponytail: PyMuPDF 2.1 names every new radio "on" state /None, so all buttons in a group
            # act as one. Rename the state to the button's value; drop when PyMuPDF fixes it.
            _set_radio_state(doc, annot.xref, "None", clean_value(f.value), f.default == "1", False)
    _apply_tab_order(doc, [(f.page, placed[id(f)]) for f in order if id(f) in placed])
    return doc


def _apply_tab_order(doc, page_xrefs) -> None:
    """Tab order = order of the page's /Annots array (what Acrobat and browsers follow)."""
    for page in doc:
        wanted = [x for pno, x in page_xrefs if pno == page.number]
        t, v = doc.xref_get_key(page.xref, "Annots")
        if not wanted or t not in ("array", "xref"):
            continue
        holder = int(v.split()[0]) if t == "xref" else None
        current = [int(n) for n in re.findall(r"(\d+) 0 R", doc.xref_object(holder) if holder else v)]
        rest = [x for x in current if x not in wanted]  # comments, kept list boxes...
        arr = "[" + " ".join(f"{x} 0 R" for x in rest + list(dict.fromkeys(wanted))) + "]"
        if holder:
            doc.update_object(holder, arr)
        else:
            doc.xref_set_key(page.xref, "Annots", arr)


def align_rects(rects: list[tuple], how: str) -> list[tuple]:
    """Align/size/distribute rectangles (x0, y0, x1, y1), all in the same coordinates."""
    if len(rects) < 2:
        return list(rects)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects)
    y1 = max(r[3] for r in rects)
    first = rects[0]
    out = []
    for r in rects:
        w, h = r[2] - r[0], r[3] - r[1]
        if how == "left":
            r = (x0, r[1], x0 + w, r[3])
        elif how == "right":
            r = (x1 - w, r[1], x1, r[3])
        elif how == "top":
            r = (r[0], y0, r[2], y0 + h)
        elif how == "bottom":
            r = (r[0], y1 - h, r[2], y1)
        elif how == "same_width":
            r = (r[0], r[1], r[0] + first[2] - first[0], r[3])
        elif how == "same_height":
            r = (r[0], r[1], r[2], r[1] + first[3] - first[1])
        out.append(r)
    if how in ("distribute_v", "distribute_h"):
        axis = 1 if how == "distribute_v" else 0
        idx = sorted(range(len(rects)), key=lambda i: rects[i][axis])
        sizes = [rects[i][axis + 2] - rects[i][axis] for i in idx]
        start, end = rects[idx[0]][axis], max(r[axis + 2] for r in rects)
        gap = (end - start - sum(sizes)) / (len(rects) - 1)
        pos = start
        for i, size in zip(idx, sizes):
            r = list(rects[i])
            r[axis], r[axis + 2] = pos, pos + size
            out[i] = tuple(r)
            pos += size + gap
    return out


def snap(rect, grid: float = 5) -> tuple:
    return tuple(round(v / grid) * grid for v in rect)


_SAMPLES = [  # (words in the field name, example value)
    (("nome", "name"), "Mario Rossi"), (("cognome", "surname"), "Rossi"), (("mail",), "mario.rossi@esempio.it"),
    (("tel", "cell", "phone"), "333 123 4567"), (("citt", "comune", "city"), "Roma"),
    (("indirizzo", "via", "address"), "Via Garibaldi 10"), (("cap", "zip"), "00100"),
    (("cf", "fiscale"), "RSSMRA80A01H501U"), (("iva",), "01234567890"), (("importo", "prezzo", "euro"), "1.250,00"),
]


def _sample_text(f_name: str, multiline: bool) -> str:
    n = f_name.lower()
    for keys, value in _SAMPLES:
        if any(k in n for k in keys):
            return value
    return "Testo di esempio su\npiù righe" if multiline else "Testo di esempio"


def fill_sample(doc: pymupdf.Document) -> None:
    """Fill every field with its default or a plausible example, for the preview."""
    from datetime import date
    groups = set()
    for page in doc:
        for w in page.widgets():
            t = w.field_type
            if t == pymupdf.PDF_WIDGET_TYPE_TEXT and not w.field_value:
                multiline = bool(w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
                w.field_value = (f"{date.today():%d/%m/%Y}" if _is_date(doc, w)
                                 else _sample_text(w.field_name or "", multiline))
            elif t == pymupdf.PDF_WIDGET_TYPE_CHECKBOX and w.field_value in (False, "Off", "", None):
                w.field_value = True
            elif t == pymupdf.PDF_WIDGET_TYPE_COMBOBOX and not w.field_value and w.choice_values:
                c = w.choice_values[0]
                w.field_value = c if isinstance(c, str) else c[0]
            elif t == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON and w.field_name not in groups:
                groups.add(w.field_name)  # first button of each group
                w.field_value = w.on_state()
            else:
                continue
            w.update()


def save_with_fields(src_path: str | Path, fields: list[Field], out_path: str | Path) -> None:
    doc = build_pdf(src_path, fields)
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    out = Path(out_path)
    tmp = out.with_name(out.name + ".pdflex-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)  # the original is untouched until the new file is complete


# ---------------------------------------------------------------- UI

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QFont, QIntValidator, QKeySequence, QPainter, QPen, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGraphicsItem, QGraphicsPixmapItem, QGridLayout,
    QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from pdflex.ui import (  # noqa: E402
    Toggle, info, message, warn,
    ACCENT, DocView, DropZone, button, dot, icon, page_shadow, pdf_from_drop, render_page,
)

KIND_LABELS = {"text": "Testo", "multiline": "Testo lungo", "checkbox": "Casella di spunta",
               "radio": "Scelta singola", "dropdown": "Menu a tendina", "date": "Data", "signature": "Firma"}
KIND_COLORS = {"text": "#2F6FDB", "multiline": "#0E8FA3", "checkbox": "#2E9E5B", "radio": "#7A4FD1",
               "dropdown": "#D9730D", "date": "#8A5A3C", "signature": "#C2357A"}
HANDLE = 10  # px, resize grip in the bottom-right corner
PAGE_MARGIN = 24
TAG_H = 20  # name tag above the selected field


class FieldItem(QGraphicsRectItem):
    def __init__(self, field: Field, view_rect, zoom: float, tab):
        x0, y0, x1, y1 = view_rect
        super().__init__(0, 0, (x1 - x0) * zoom, (y1 - y0) * zoom)
        self.setPos(x0 * zoom, y0 * zoom)
        self.field, self.tab, self.resizing = field, tab, False
        self.min_size = 8 * zoom
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.setAcceptHoverEvents(True)

    def boundingRect(self):  # room for the name tag and the outer selection ring
        return self.rect().adjusted(-6, -TAG_H - 8, 6, 6)

    def paint(self, painter, option, widget=None):
        r = self.rect()
        color = QColor(KIND_COLORS[self.field.kind])
        sel = self.isSelected()
        other_selected = self.tab.selected is not None and not sel
        painter.setRenderHint(QPainter.Antialiasing)
        fill = QColor(color)
        fill.setAlpha(80 if sel else 16 if other_selected else 38)
        line = QColor(color)
        line.setAlpha(110 if other_selected else 255)
        painter.setPen(QPen(line, 2.5 if sel else 1.3))
        painter.setBrush(fill)
        painter.drawRoundedRect(r, 3, 3)
        label = self.field.name + (f" = {self.field.value}" if self.field.kind == "radio" else "")
        if r.width() > 40 and not sel:
            painter.setPen(line.darker(130))
            painter.drawText(r.adjusted(5, 0, -4, 0), Qt.AlignLeft | Qt.AlignVCenter, label)
        if sel:
            # outer ring in the app accent so the selection reads at a glance
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(ACCENT), 2, Qt.DashLine))
            painter.drawRoundedRect(r.adjusted(-4, -4, 4, 4), 5, 5)
            # name tag
            f = QFont(painter.font())
            f.setBold(True)
            painter.setFont(f)
            text = f"{KIND_LABELS[self.field.kind]}: {label}"
            tw = painter.fontMetrics().horizontalAdvance(text) + 16
            tag = QRectF(r.left() - 4, r.top() - TAG_H - 6, tw, TAG_H)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(tag, 5, 5)
            painter.setPen(Qt.white)
            painter.drawText(tag, Qt.AlignCenter, text)
            # resize grip
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.white)
            painter.drawEllipse(QRectF(r.right() - HANDLE / 2, r.bottom() - HANDLE / 2, HANDLE, HANDLE))
        self.setToolTip(f"{KIND_LABELS[self.field.kind]}: {label}")

    def _on_handle(self, pos):
        r = self.rect()
        return abs(pos.x() - r.right()) <= HANDLE and abs(pos.y() - r.bottom()) <= HANDLE

    def hoverMoveEvent(self, e):
        self.setCursor(Qt.SizeFDiagCursor if self.isSelected() and self._on_handle(e.pos()) else Qt.SizeAllCursor)

    def mousePressEvent(self, e):
        self.resizing = self.isSelected() and self._on_handle(e.pos())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.resizing:
            self.prepareGeometryChange()
            self.setRect(0, 0, max(e.pos().x(), self.min_size), max(e.pos().y(), self.min_size))
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.resizing = False
        self.tab.item_moved(self)


class PageView(QGraphicsView):
    drawn = Signal(tuple)  # (x0, y0, x1, y1) in scene coords

    def __init__(self, scene, tab):
        super().__init__(scene)
        self.tab, self.start, self.band = tab, None, None
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setRenderHint(QPainter.Antialiasing)

    def mousePressEvent(self, e):
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
        r = self.band.rect()
        self.scene().removeItem(self.band)
        self.band = None
        self.drawn.emit((r.left(), r.top(), r.right(), r.bottom()))


class PreviewDialog(QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anteprima con dati di esempio")
        self.resize(820, 900)
        self.doc = doc  # keep it alive while shown
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        note = QLabel("Così vedrà il modulo chi lo compila. I dati sono di esempio e non vengono salvati.",
                      objectName="Muted")
        note.setContentsMargins(16, 10, 16, 10)
        lay.addWidget(note)
        view = DocView(annots=True)
        lay.addWidget(view, 1)
        view.show_doc(doc)


class EditorTab(QWidget):
    title_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.doc, self.path, self.fields = None, None, []
        self.page_no, self.zoom, self.mode = 0, 1.5, "select"
        self.selected: Field | None = None  # the field shown in the properties panel
        self.selection: list[Field] = []  # every selected field (Ctrl+click for more)
        self.dirty = False
        self.grid = False
        self._undo: list[list[Field]] = []
        self._redo: list[list[Field]] = []
        self._loading = False
        self.setAcceptDrops(True)

        # left: field palette
        palette = QFrame(objectName="PanelLeft")
        palette.setFixedWidth(210)
        pl = QVBoxLayout(palette)
        pl.setContentsMargins(12, 16, 12, 16)
        pl.addWidget(QLabel("Campi", objectName="H2"))
        hint = QLabel("Scegli un tipo, poi trascina sulla pagina per disegnarlo.", objectName="Muted")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        pl.addSpacing(6)
        tools = QWidget(objectName="Palette")
        tl = QVBoxLayout(tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for mode, label in [("select", "Seleziona e sposta"), *KIND_LABELS.items()]:
            b = QToolButton()
            b.setText(label)
            b.setIcon(icon(mode, KIND_COLORS.get(mode, "#1D2633")))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_group.addButton(b)
            tl.addWidget(b)
            self.mode_buttons[mode] = b
            if mode == "select":
                sep = QFrame()
                sep.setFixedHeight(1)
                sep.setStyleSheet("background:#E3E7EE; margin: 6px 4px;")
                tl.addWidget(sep)
        self.mode_buttons["select"].setChecked(True)
        pl.addWidget(tools)
        pl.addSpacing(12)
        self.b_detect = button("Rileva campi", "wand", tip="Cerca righe ____, caselle e spazi vuoti e crea i campi")
        self.b_detect.clicked.connect(self._detect_clicked)
        pl.addWidget(self.b_detect)
        self.c_grid = Toggle("Griglia magnetica")
        self.c_grid.setToolTip("Allinea posizione e misure dei campi a passi di 5 punti")
        self.c_grid.toggled.connect(lambda on: setattr(self, "grid", on))
        pl.addWidget(self.c_grid)
        pl.addStretch()
        pl.addWidget(QLabel("Ctrl+clic seleziona più campi\nCanc elimina, Ctrl+D duplica\n"
                            "Ctrl+Z annulla, Ctrl+Y ripete", objectName="Muted"))

        # center: command bar + page
        bar = QFrame(objectName="Bar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_open = button("Apri", "open")
        self.b_save = button("Salva con nome", "save", "Primary")
        self.b_list = button("Elenco campi", "list", tip="Mostra o nascondi l'elenco di tutti i campi")
        self.b_list.setCheckable(True)
        self.b_preview = button("Anteprima", "eye", tip="Vedi il modulo compilato con dati di esempio")
        self.b_open.clicked.connect(self.ask_open)
        self.b_save.clicked.connect(self.ask_save)
        self.b_list.toggled.connect(lambda on: self.list_panel.setVisible(on))
        self.b_preview.clicked.connect(self.preview)
        self.file_label = QLabel("", objectName="Muted")
        self.b_prev = button("", "prev", "Ghost", "Pagina precedente")
        self.b_next = button("", "next", "Ghost", "Pagina successiva")
        self.page_label = QLabel("")
        self.b_zout = button("", "zoom-out", "Ghost", "Riduci")
        self.b_zin = button("", "zoom-in", "Ghost", "Ingrandisci")
        self.zoom_label = QLabel("")
        self.zoom_label.setMinimumWidth(42)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.b_prev.clicked.connect(lambda: self.go_page(self.page_no - 1))
        self.b_next.clicked.connect(lambda: self.go_page(self.page_no + 1))
        self.b_zout.clicked.connect(lambda: self.set_zoom(self.zoom / 1.2))
        self.b_zin.clicked.connect(lambda: self.set_zoom(self.zoom * 1.2))
        self.b_undo = button("", "undo", "Ghost", "Annulla (Ctrl+Z)")
        self.b_redo = button("", "redo", "Ghost", "Ripeti (Ctrl+Y)")
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        for w in (self.b_open, self.b_save, self.b_undo, self.b_redo, self.file_label):
            bl.addWidget(w)
        bl.addStretch()
        for w in (self.b_list, self.b_preview):
            bl.addWidget(w)
        bl.addSpacing(12)
        for w in (self.b_prev, self.page_label, self.b_next, self.b_zout, self.zoom_label, self.b_zin):
            bl.addWidget(w)

        self.scene = QGraphicsScene(self)
        self.scene.selectionChanged.connect(self._selection_changed)
        self.view = PageView(self.scene, self)
        self.view.drawn.connect(self._drawn)
        self.drop = DropZone("Apri un PDF per aggiungere i campi", glyph="form")
        self.drop.picked.connect(self.open)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(80, 60, 80, 60)
        el.addWidget(self.drop)
        empty.setStyleSheet("background: #E9EDF2;")
        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.view)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(bar)
        cl.addWidget(self.stack, 1)

        # field list (toggled from the bar)
        self.list_panel = QFrame(objectName="PanelRight")
        self.list_panel.setFixedWidth(250)
        lp = QVBoxLayout(self.list_panel)
        lp.setContentsMargins(0, 16, 0, 0)
        self.list_title = QLabel("Campi e ordine Tab", objectName="H2")
        self.list_title.setContentsMargins(14, 0, 14, 0)
        lp.addWidget(self.list_title)
        tip = QLabel("Chi compila passa da un campo al successivo con Tab in questo ordine. "
                     "Trascina per cambiarlo.", objectName="Muted")
        tip.setWordWrap(True)
        tip.setContentsMargins(14, 0, 14, 4)
        lp.addWidget(tip)
        self.b_sort = button("Ordina per posizione", "list", "Ghost")
        self.b_sort.clicked.connect(self.sort_by_position)
        sort_row = QHBoxLayout()
        sort_row.setContentsMargins(8, 0, 8, 4)
        sort_row.addWidget(self.b_sort)
        sort_row.addStretch()
        lp.addLayout(sort_row)
        self.field_list = QListWidget(objectName="Changes")
        self.field_list.setIconSize(QSize(10, 10))
        self.field_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.field_list.itemClicked.connect(self._list_clicked)
        self.field_list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._list_reordered))
        lp.addWidget(self.field_list)
        self.list_panel.hide()

        # right: properties
        props = QFrame(objectName="PanelRight")
        props.setFixedWidth(290)
        pr = QVBoxLayout(props)
        pr.setContentsMargins(16, 16, 16, 16)
        pr.addWidget(QLabel("Proprietà", objectName="H2"))
        self.kind_label = QLabel("Seleziona un campo sulla pagina per modificarlo.", objectName="Muted")
        self.kind_label.setWordWrap(True)
        pr.addWidget(self.kind_label)
        pr.addSpacing(8)
        self.props = QWidget()
        form = self.form = QFormLayout(self.props)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setVerticalSpacing(6)
        self.p_name, self.p_required, self.p_default = QLineEdit(), Toggle("Obbligatorio"), QLineEdit()
        self.p_checked, self.p_size = Toggle("Selezionata all'apertura"), QLineEdit()
        self.p_options, self.p_value = QPlainTextEdit(), QLineEdit()
        self.p_fmt, self.p_calc = QComboBox(), QLineEdit()
        for key, label in FORMATS.items():
            self.p_fmt.addItem(label, key)
        self.p_calc.setPlaceholderText("es. importo_1, importo_2")
        self.p_calc.setToolTip("Il campo mostra la somma di questi campi e non si può scrivere a mano")
        self.p_options.setPlaceholderText("Roma\nMilano\nNapoli")
        self.p_default.setPlaceholderText("Vuoto")
        self.p_size.setPlaceholderText("Automatica")
        self.p_size.setValidator(QIntValidator(0, 72, self.p_size))
        form.addRow("Nome del campo", self.p_name)
        form.addRow("Valore iniziale", self.p_default)
        form.addRow("Opzioni, una per riga", self.p_options)
        form.addRow("Valore di questa scelta", self.p_value)
        form.addRow("Formato", self.p_fmt)
        form.addRow("Somma dei campi (facoltativo)", self.p_calc)
        form.addRow("Dimensione testo (pt)", self.p_size)
        form.addRow(self.p_checked)
        form.addRow(self.p_required)
        for w in (self.p_name, self.p_default, self.p_value, self.p_size, self.p_calc):
            w.editingFinished.connect(self._props_edited)
        self.p_fmt.currentIndexChanged.connect(self._props_edited)
        self.p_required.toggled.connect(self._props_edited)
        self.p_checked.toggled.connect(self._props_edited)
        self.p_options.textChanged.connect(self._props_edited)
        pr.addWidget(self.props)
        actions = QHBoxLayout()
        self.b_dup = button("Duplica", "copy")
        self.b_del = button("Elimina", "trash", "Danger")
        self.b_dup.clicked.connect(self.duplicate_selected)
        self.b_del.clicked.connect(self.delete_selected)
        actions.addWidget(self.b_dup)
        actions.addWidget(self.b_del)
        self.actions_box = QWidget()
        self.actions_box.setLayout(actions)
        actions.setContentsMargins(0, 8, 0, 0)
        pr.addWidget(self.actions_box)
        self.multi_box = QWidget()
        grid = QGridLayout(self.multi_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        aligns = [("Allinea a sinistra", "left"), ("Allinea a destra", "right"), ("Allinea in alto", "top"),
                  ("Allinea in basso", "bottom"), ("Stessa larghezza", "same_width"),
                  ("Stessa altezza", "same_height"), ("Distribuisci in verticale", "distribute_v"),
                  ("Distribuisci in orizzontale", "distribute_h")]
        for i, (label, how) in enumerate(aligns):
            b = button(label)
            b.clicked.connect(lambda _=False, h=how: self.align(h))
            grid.addWidget(b, *((i // 2, i % 2) if i < 6 else (i - 3, 0, 1, 2)))  # long labels: full row
        b_del_all = button("Elimina selezionati", "trash", "Danger")
        b_del_all.clicked.connect(self.delete_selected)
        grid.addWidget(b_del_all, len(aligns) - 3, 0, 1, 2)
        pr.addWidget(self.multi_box)
        pr.addStretch()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(palette)
        root.addWidget(center, 1)
        root.addWidget(self.list_panel)
        root.addWidget(props)

        QShortcut(QKeySequence.Delete, self.view, self.delete_selected)
        QShortcut(QKeySequence("Ctrl+D"), self, self.duplicate_selected)
        QShortcut(QKeySequence.Open, self, self.ask_open)
        QShortcut(QKeySequence.Save, self, self.ask_save)
        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
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
        """True when it is fine to drop the current fields (saved, discarded, or nothing to lose)."""
        if not self.dirty:
            return True
        r = message(self, "Hai modifiche non salvate. Vuoi salvarle prima?", "ask",
                    ("Salva", "Non salvare", "Annulla"), note="Se non salvi, i campi aggiunti o cambiati vanno persi.")
        if r == 0:
            return self.ask_save()
        return r == 1

    def ask_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Apri PDF", "", "PDF (*.pdf)")
        if path:
            self.open(path)

    def open(self, path: str) -> None:
        if not self.confirm_discard():
            return
        try:
            doc = open_pdf(path)
        except ValueError as e:
            warn(self, str(e))
            return
        if self.doc:
            self.doc.close()
        self.doc, self.path, self.fields = doc, path, load_fields(doc)
        self.page_no, self.dirty = 0, False
        self._select(None)
        self._undo.clear()
        self._redo.clear()
        self.title_changed.emit(Path(path).name)
        self.fit_width()

    def ask_save(self) -> bool:
        if not self.doc:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Salva con nome", suggest_save_path(self.path), "PDF (*.pdf)")
        if not path:
            return False
        try:
            self.save(path)
        except (ValueError, OSError) as e:
            warn(self, f"Salvataggio non riuscito: {e}")
            return False
        return True

    def save(self, path: str) -> None:
        self.doc.close()  # Windows will not replace a file that is still open
        try:
            save_with_fields(self.path, self.fields, path)
        except Exception:
            self.doc = open_pdf(self.path)
            raise
        self.doc, self.path = open_pdf(path), path
        self.fields = load_fields(self.doc)  # now they point at the saved widgets
        self._select(None)
        self.dirty = False
        self._undo.clear()  # snapshots point at widgets of the previous file
        self._redo.clear()
        self.title_changed.emit(Path(path).name)
        self.refresh()

    def preview(self):
        if not self.doc:
            return
        try:
            doc = build_pdf(self.path, self.fields)
        except ValueError as e:
            warn(self, str(e))
            return
        fill_sample(doc)
        PreviewDialog(doc, self).exec()

    # ---- undo / redo: whole snapshots of the field list (forms have tens of fields, not thousands)
    def _push(self, snapshot=None):
        self._undo.append(snapshot if snapshot is not None else copy.deepcopy(self.fields))
        del self._undo[:-50]
        self._redo.clear()

    def _restore(self, src, dst):
        if not src:
            return
        dst.append(copy.deepcopy(self.fields))
        self.fields = src.pop()
        self._select(None)
        self.dirty = True
        self.refresh()

    def undo(self):
        self._restore(self._undo, self._redo)

    def redo(self):
        self._restore(self._redo, self._undo)

    def _select(self, f: Field | None):
        self.selected = f
        self.selection = [f] if f else []

    def select_many(self, fields: list[Field]):
        self.selection = list(fields)
        self.selected = fields[0] if fields else None
        self.refresh()

    # ---- fields
    def add_field(self, kind: str, page: int, rect, push: bool = True) -> Field:
        if push:
            self._push()
        if kind == "radio":
            groups = [f.name for f in self.fields if f.kind == "radio"]
            name = groups[-1] if groups else self._free_name("radio")
            f = Field(kind, page, tuple(rect), name, value=self._free_value(name))
        else:
            f = Field(kind, page, tuple(rect), self._free_name(kind))
        self.fields.append(f)
        self.dirty = True
        return f

    def _free_name(self, kind):
        used = {f.name for f in self.fields}
        n = 1
        while f"{kind}_{n}" in used:
            n += 1
        return f"{kind}_{n}"

    def _free_value(self, group):
        used = {clean_value(f.value) for f in self.fields if f.kind == "radio" and f.name == group}
        n = 1
        while f"opzione_{n}" in used:
            n += 1
        return f"opzione_{n}"

    def _clamp(self, r):
        """Keep a rectangle (view coordinates, points) inside the visible page."""
        pr = self.doc[self.page_no].rect
        x0, y0, x1, y1 = r
        w, h = min(x1 - x0, pr.width), min(y1 - y0, pr.height)
        x0, y0 = min(max(x0, 0), pr.width - w), min(max(y0, 0), pr.height - h)
        return x0, y0, x0 + w, y0 + h

    def _snap(self, r):
        if not self.grid:
            return r
        x0, y0, x1, y1 = snap(r)
        return x0, y0, max(x1, x0 + 5), max(y1, y0 + 5)

    def _drawn(self, r):
        x0, y0, x1, y1 = (v / self.zoom for v in r)
        if x1 - x0 < 8 or y1 - y0 < 8:  # a click, not a drag: default size
            w, h = {"checkbox": (14, 14), "radio": (14, 14), "multiline": (200, 60),
                    "signature": (180, 50)}.get(self.mode, (150, 20))
            x1, y1 = x0 + w, y0 + h
        page = self.doc[self.page_no]
        f = self.add_field(self.mode, self.page_no, from_view(page, self._clamp(self._snap((x0, y0, x1, y1)))))
        self.mode_buttons["select"].click()
        self._select(f)
        self.refresh()

    def item_moved(self, item: FieldItem):
        """Qt moved every selected item together; copy their new places into the fields."""
        items = [i for i in self.scene.selectedItems() if isinstance(i, FieldItem)] or [item]
        page, z = self.doc[self.page_no], self.zoom
        moves = []
        for i in items:
            p, r = i.pos(), i.rect()
            shown = (p.x() / z, p.y() / z, (p.x() + r.width()) / z, (p.y() + r.height()) / z)
            new = from_view(page, self._clamp(self._snap(shown)))
            if any(abs(a - b) > 0.01 for a, b in zip(new, i.field.rect)):
                moves.append((i.field, new))
        if moves:
            self._push()
            for f, new in moves:
                f.rect = new
            self.dirty = True
        self.refresh()

    def delete_selected(self):
        if self.selection:
            self._push()
            gone = {id(f) for f in self.selection}
            self.fields = [f for f in self.fields if id(f) not in gone]
            self._select(None)
            self.dirty = True
            self.refresh()

    def align(self, how: str):
        chosen = [f for f in self.selection if f.page == self.page_no]
        if len(chosen) < 2:
            return
        page = self.doc[self.page_no]
        new = align_rects([to_view(page, f.rect) for f in chosen], how)
        self._push()
        for f, r in zip(chosen, new):
            f.rect = from_view(page, self._clamp(r))
        self.dirty = True
        self.refresh()

    def set_order(self, fields: list[Field]):
        """The list order is the Tab order of the saved form."""
        self._push()
        self.fields = list(fields)
        self.dirty = True
        self.refresh()

    def sort_by_position(self):
        def key(f):
            r = to_view(self.doc[f.page], f.rect)
            return f.page, round(r[1] / 6), r[0]  # same line (within ~6 pt), then left to right
        self.set_order(sorted(self.fields, key=key))

    def _list_reordered(self):
        by_id = {id(f): f for f in self.fields}
        order = [by_id[self.field_list.item(i).data(Qt.UserRole)] for i in range(self.field_list.count())]
        if order != self.fields:
            self.set_order(order)

    def detect(self) -> int:
        """Add the fields detect_fields finds on every page; returns how many."""
        from pdflex.detect import detect_fields
        found = []
        for page in self.doc:
            found += detect_fields(page, self.fields + found)
        if found:
            self._push()
            self.fields += found
            self.dirty = True
            self.refresh()
        return len(found)

    def _detect_clicked(self):
        if not self.doc:
            return
        n = self.detect()
        info(self, f"Trovati {n} campi. Controlla nomi e posizioni; Ctrl+Z li toglie."
                                if n else "Nessun nuovo campo trovato: disegnali dalla tavolozza.")

    def duplicate_selected(self):
        if not self.selected:
            return
        s = self.selected
        page = self.doc[s.page]
        x0, y0, x1, y1 = to_view(page, s.rect)
        f = self.add_field(s.kind, s.page, from_view(page, self._clamp((x0 + 10, y0 + 10, x1 + 10, y1 + 10))))
        f.required, f.font_size, f.options, f.fmt = s.required, s.font_size, list(s.options), s.fmt
        if s.kind == "radio":
            f.name = s.name
            f.value = self._free_value(s.name)
        self._select(f)
        self.refresh()

    def select_field(self, f: Field):
        self.page_no = f.page
        self._select(f)
        self.refresh()
        for item in self.scene.items():
            if isinstance(item, FieldItem) and item.field is f:
                self.view.ensureVisible(item, 40, 80)

    # ---- view
    def set_mode(self, mode):
        self.mode = mode
        self.view.viewport().setCursor(Qt.ArrowCursor if mode == "select" else Qt.CrossCursor)

    def set_zoom(self, z):
        self.zoom = min(max(z, 0.4), 5)
        self.refresh()

    def fit_width(self):
        if self.doc:
            # the view may still be hidden behind the drop zone: measure the stack instead
            sb = self.view.verticalScrollBar().sizeHint().width()
            avail = max(self.stack.width() - sb, 500) - 2 * PAGE_MARGIN - 16
            self.set_zoom(avail / self.doc[self.page_no].rect.width)

    def go_page(self, n):
        if self.doc and 0 <= n < self.doc.page_count:
            self.page_no = n
            self._select(None)
            self.refresh()
            self.view.verticalScrollBar().setValue(0)

    def refresh(self):
        self.scene.blockSignals(True)
        self.scene.clear()
        has = self.doc is not None
        self.stack.setCurrentIndex(1 if has else 0)
        for w in (self.b_save, self.b_prev, self.b_next, self.b_zin, self.b_zout, self.b_list, self.b_preview,
                  self.b_detect):
            w.setEnabled(has)
        self.b_undo.setEnabled(bool(self._undo))
        self.b_redo.setEnabled(bool(self._redo))
        if has:
            self.file_label.setText(Path(self.path).name + ("  (modificato)" if self.dirty else ""))
            self.page_label.setText(f"Pagina {self.page_no + 1} di {self.doc.page_count}")
            self.zoom_label.setText(f"{round(self.zoom * 100)}%")
            self.b_prev.setEnabled(self.page_no > 0)
            self.b_next.setEnabled(self.page_no < self.doc.page_count - 1)
            page = self.doc[self.page_no]
            # form fields are drawn by us as overlays; other annotations stay visible
            pix = QGraphicsPixmapItem(render_page(page, self.zoom, annots=False, dpr=self.devicePixelRatioF()))
            page_shadow(pix)
            self.scene.addItem(pix)
            self._draw_other_annots(page)
            w, h = page.rect.width * self.zoom, page.rect.height * self.zoom
            self.scene.setSceneRect(-PAGE_MARGIN, -PAGE_MARGIN - TAG_H, w + 2 * PAGE_MARGIN,
                                    h + 2 * PAGE_MARGIN + TAG_H)
            self.view.setSceneRect(self.scene.sceneRect())  # re-anchor even while the view was hidden
            for f in self.fields:
                if f.page == self.page_no:
                    item = FieldItem(f, to_view(page, f.rect), self.zoom, self)
                    chosen = any(f is s for s in self.selection)
                    item.setZValue(10 if chosen else 0)
                    self.scene.addItem(item)
                    item.setSelected(chosen)
        self.scene.blockSignals(False)
        self._fill_list()
        self._show_props()

    def _draw_other_annots(self, page):
        """Comments, stamps and fields PDFlex can't edit (list boxes, buttons): show them, dimmed."""
        z = self.zoom
        for a in page.annots():
            r = pymupdf.Rect(a.rect) * page.rotation_matrix
            self.scene.addRect(r.x0 * z, r.y0 * z, r.width * z, r.height * z,
                               QPen(QColor("#8A94A6"), 1, Qt.DotLine), QBrush(QColor(138, 148, 166, 30)))
        for w in page.widgets():
            if w.field_type not in _TYPES.values():
                r = pymupdf.Rect(w.rect) * page.rotation_matrix
                item = self.scene.addRect(r.x0 * z, r.y0 * z, r.width * z, r.height * z,
                                          QPen(QColor("#8A94A6"), 1, Qt.DotLine), QBrush(QColor(138, 148, 166, 40)))
                item.setToolTip(f"{w.field_name}: campo non modificabile con PDFlex, resta com'è")

    def _fill_list(self):
        self.field_list.clear()
        self.list_title.setText(f"Campi e ordine Tab ({len(self.fields)})")
        self.b_list.setText(f"Elenco campi ({len(self.fields)})" if self.doc else "Elenco campi")
        for n, f in enumerate(self.fields, 1):
            label = f.name + (f" = {f.value}" if f.kind == "radio" else "")
            item = QListWidgetItem(dot(KIND_COLORS[f.kind]),
                                   f"{n}. {label}\n{KIND_LABELS[f.kind]}, pagina {f.page + 1}")
            item.setData(Qt.UserRole, id(f))
            self.field_list.addItem(item)
            if any(f is s for s in self.selection):
                item.setSelected(True)

    def _list_clicked(self, item):
        f = next((f for f in self.fields if id(f) == item.data(Qt.UserRole)), None)
        if f:
            self.select_field(f)

    def _selection_changed(self):
        items = [i for i in self.scene.selectedItems() if isinstance(i, FieldItem)]
        self.selection = [i.field for i in items]
        if not any(self.selected is f for f in self.selection):
            self.selected = self.selection[0] if self.selection else None
        for i in self.scene.items():
            if isinstance(i, FieldItem):
                i.setZValue(10 if i.field is self.selected else 0)
        self.scene.update()
        self._fill_list()
        self._show_props()

    def _show_props(self):
        many = len(self.selection) > 1
        f = None if many else self.selected
        self.multi_box.setVisible(many)
        self.props.setVisible(f is not None)
        self.actions_box.setVisible(f is not None)
        if many:
            self.kind_label.setText(f"{len(self.selection)} campi selezionati")
            self.kind_label.setStyleSheet(f"color: white; background: {ACCENT}; border-radius: 6px;"
                                          "padding: 4px 8px; font-weight: 600;")
            return
        if not f:
            self.kind_label.setText("Seleziona un campo sulla pagina per modificarlo.")
            self.kind_label.setStyleSheet("")
            return
        self.kind_label.setText(f"{KIND_LABELS[f.kind]}, pagina {f.page + 1}")
        self.kind_label.setStyleSheet(f"color: white; background: {KIND_COLORS[f.kind]}; border-radius: 6px;"
                                      "padding: 4px 8px; font-weight: 600;")
        self._loading = True
        self.p_name.setText(f.name)
        self.p_required.setChecked(f.required)
        self.p_default.setText(f.default)
        self.p_checked.setChecked(f.default == "1")
        self.p_size.setText(str(int(f.font_size)) if f.font_size else "")
        self.p_options.setPlainText("\n".join(f.options))
        self.p_value.setText(f.value)
        self.p_fmt.setCurrentIndex(max(self.p_fmt.findData(f.fmt), 0))
        self.p_calc.setText(", ".join(f.calc))
        self._update_rows(f)
        self._loading = False

    def _update_rows(self, f: Field):
        k = f.kind
        self.form.setRowVisible(self.p_default, k in ("text", "multiline", "date", "dropdown"))
        self.form.setRowVisible(self.p_checked, k in ("checkbox", "radio"))
        self.form.setRowVisible(self.p_size, k in ("text", "multiline", "date", "dropdown"))
        self.form.setRowVisible(self.p_options, k == "dropdown")
        self.form.setRowVisible(self.p_value, k == "radio")
        self.form.setRowVisible(self.p_required, k != "signature")
        self.form.setRowVisible(self.p_fmt, k in ("text", "multiline"))
        self.form.setRowVisible(self.p_calc, k == "text" and f.fmt in ("number", "amount"))
        self.form.labelForField(self.p_name).setText("Nome del gruppo" if k == "radio" else "Nome del campo")
        self.p_default.setPlaceholderText("gg/mm/aaaa" if k == "date" else "Vuoto")

    def _props_edited(self, *_):
        f = self.selected
        if not f or self._loading:
            return
        snapshot = copy.deepcopy(self.fields)
        before = (f.name, f.required, f.font_size, list(f.options), f.value, f.default, f.fmt, list(f.calc))
        new_name = self.p_name.text().strip()
        if f.kind == "radio" and new_name != f.name:  # renaming the group renames all its buttons
            for o in self.fields:
                if o.kind == "radio" and o.name == f.name and o is not f:
                    o.name = new_name
        f.name = new_name
        f.required = self.p_required.isChecked()
        f.font_size = float(self.p_size.text() or 0)
        f.options = [o.strip() for o in self.p_options.toPlainText().splitlines() if o.strip()]
        if f.kind in ("text", "multiline"):
            f.fmt = self.p_fmt.currentData() or ""
        f.calc = ([n.strip() for n in self.p_calc.text().split(",") if n.strip()]
                  if f.kind == "text" and f.fmt in ("number", "amount") else [])
        if f.kind == "radio":
            f.value = clean_value(self.p_value.text())
            if self.p_value.text() != f.value:
                self.p_value.setText(f.value)
        if f.kind in ("checkbox", "radio"):
            f.default = "1" if self.p_checked.isChecked() else ""
            if f.default and f.kind == "radio":  # only one button per group starts selected
                for o in self.fields:
                    if o.kind == "radio" and o.name == f.name and o is not f:
                        o.default = ""
        else:
            f.default = self.p_default.text()
        if before != (f.name, f.required, f.font_size, f.options, f.value, f.default, f.fmt, f.calc):
            self._push(snapshot)
            self.b_undo.setEnabled(True)
            self._update_rows(f)
            self.dirty = True
            self.file_label.setText(Path(self.path).name + "  (modificato)")
            self._fill_list()
        self.scene.update()
