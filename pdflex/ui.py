"""Look & feel and shared widgets: palette, stylesheet, icons, sharp page rendering, page stack view."""
import json
import os
from contextlib import contextmanager
import struct
import tempfile
import threading
from pathlib import Path

import pymupdf
from PySide6.QtCore import QBuffer, QByteArray, QEventLoop, QIODevice, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QIcon, QImage, QPainter, QPalette, QPen, QPixmap, QTextDocument,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QMessageBox, QGraphicsDropShadowEffect, QGraphicsScene, QGraphicsView, QLabel,
    QProgressDialog, QPushButton, QScrollBar, QStyle, QStyledItemDelegate, QStyleOptionSlider, QStyleOptionViewItem,
    QVBoxLayout,
)

INK = "#1D2633"  # on the page (always white paper) and as the light theme's text
MUTED = "#5B6675"
LINE = "#E3E7EE"
DESK = "#E9EDF2"
ACCENT = "#3B3FAE"
ACCENT_SOFT = "#ECEDFB"
RED = "#D64545"
GREEN = "#2E9E5B"
AMBER = "#C98A00"

THEMES = {
    "light": {
        "ink": INK, "muted": MUTED, "line": LINE, "desk": DESK, "bg": "#F6F7FA", "surface": "#FFFFFF",
        "hover": "#F1F3F7", "pressed": "#E4E7EE", "border": "#D9DEE7", "border_strong": "#C3C9D4",
        "disabled": "#A9B0BB", "disabled_bg": "#FAFBFC", "accent": ACCENT, "accent_bg": ACCENT,
        "accent_hover": "#3236A0", "accent_pressed": "#292D8A", "accent_disabled": "#AEB1DE",
        "accent_soft": ACCENT_SOFT, "accent_soft_border": "#C9CCF6", "danger": RED, "danger_bg": "#FDEEEE",
        "danger_border": "#F0C2C2", "tip_bg": INK, "tip_fg": "#FFFFFF", "hot_bg": "#FAFAFF", "row_hover": "#F5F6F9",
        "handle": "#C3C9D4", "marks_handle": "rgba(91, 102, 117, 90)", "green": GREEN, "loaded_bg": "#F3FBF6",
        "toggle_off": "#C5CCD6", "focus_ring": "#DCE0F7", "text_red": "#B42318", "text_green": "#1E7B45",
        "white": "#FFFFFF",
    },
    "dark": {
        "ink": "#E4E8EE", "muted": "#9AA4B2", "line": "#2F3540", "desk": "#14171C", "bg": "#1A1E24",
        "surface": "#21262D", "hover": "#2A3039", "pressed": "#333A45", "border": "#3A414D",
        "border_strong": "#4C5563", "disabled": "#5E6775", "disabled_bg": "#1E232A", "accent": "#A3A7F7",
        "accent_bg": "#4E53CC", "accent_hover": "#5A5FD9", "accent_pressed": "#4247B8", "accent_disabled": "#353966",
        "accent_soft": "#2C3057", "accent_soft_border": "#474C8F", "danger": "#F27272", "danger_bg": "#3A2427",
        "danger_border": "#6E363C", "tip_bg": "#E4E8EE", "tip_fg": "#1A1E24", "hot_bg": "#252A3B",
        "row_hover": "#272D35", "handle": "#4C5563", "marks_handle": "rgba(210, 215, 225, 70)", "green": "#4CB873",
        "loaded_bg": "#1B2D23", "toggle_off": "#4C5563", "focus_ring": "#3A3F73", "text_red": "#F97066",
        "text_green": "#5BD08E", "white": "#FFFFFF",
    },
}
THEME = "light"  # the one in use: apply_theme() changes it


def color(key: str) -> str:
    """A colour of the theme in use (window chrome; the PDF pages stay white paper)."""
    return THEMES[THEME][key]


QSS = """
QWidget {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; color: {ink}; }}
QMainWindow, #Root {{ background: {bg}; }}
QDialog {{ background: {surface}; }}
#Rail {{ background: {surface}; border-right: 1px solid {line}; }}
#Rail QToolButton {{ border: none; border-radius: 10px; padding: 10px 4px 8px 4px; color: {muted}; font-size: 9pt; }}
#Rail QToolButton:hover:!checked {{ background: {hover}; }}
#Rail QToolButton:checked {{ background: {accent_soft}; color: {accent}; font-weight: 600; }}
#Bar {{ background: {surface}; border-bottom: 1px solid {line}; }}
#Panel {{ background: {surface}; }}
#PanelLeft {{ background: {surface}; border-right: 1px solid {line}; }}
#PanelRight {{ background: {surface}; border-left: 1px solid {line}; }}
#Column {{ background: {surface}; padding: 8px 14px; font-weight: 600; }}
QFrame#Sep {{ background: {line}; margin: 6px 4px; }}
QPushButton {{ border: 1px solid {border}; border-radius: 8px; padding: 6px 12px; background: {surface}; }}
QPushButton:hover {{ background: {hover}; border-color: {border_strong}; }}
QPushButton:pressed {{ background: {pressed}; }}
QPushButton:checked {{ background: {accent_soft}; color: {accent}; border-color: {accent_soft_border}; font-weight: 600; }}
QPushButton:focus {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {disabled}; border-color: {line}; background: {disabled_bg}; }}
QPushButton#Primary {{ background: {accent_bg}; color: white; border: none; font-weight: 600; padding: 7px 16px; }}
QPushButton#Primary:hover {{ background: {accent_hover}; }}
QPushButton#Primary:pressed {{ background: {accent_pressed}; }}
QPushButton#Primary:disabled {{ background: {accent_disabled}; }}
QPushButton#Ghost {{ border: 1px solid transparent; background: transparent; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ background: {hover}; }}
QPushButton#Ghost:pressed {{ background: {pressed}; }}
QPushButton#Ghost:checked {{ background: {accent_soft}; }}
QPushButton#Ghost:disabled {{ background: transparent; }}
QPushButton#Danger {{ color: {danger}; }}
QPushButton#Danger:hover {{ background: {danger_bg}; border-color: {danger_border}; }}
QToolTip {{ background: {tip_bg}; color: {tip_fg}; border: none; padding: 5px 8px; }}
QProgressDialog {{ background: {surface}; }}
QProgressBar {{ border: none; background: {line}; border-radius: 4px; max-height: 8px; color: transparent; }}
QProgressBar::chunk {{ background: {accent_bg}; border-radius: 4px; }}
QScrollArea {{ border: none; background: {surface}; }}
#DropZone[loaded="true"] {{ border: 2px solid {green}; background: {loaded_bg}; }}
#Palette QToolButton {{ border: none; border-radius: 8px; padding: 8px 10px; text-align: left; }}
#Palette QToolButton:hover:!checked {{ background: {hover}; }}
#Palette QToolButton:checked {{ background: {accent_soft}; color: {accent}; font-weight: 600; }}
QGraphicsView {{ background: {desk}; border: none; }}
QLineEdit, QPlainTextEdit {{ border: 1px solid {border}; border-radius: 6px; padding: 5px 8px; background: {surface}; selection-background-color: {accent_bg}; selection-color: white; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QComboBox {{ border: 1px solid {border}; border-radius: 6px; padding: 5px 8px; background: {surface}; }}
QComboBox:hover {{ border-color: {border_strong}; }}
QComboBox:focus, QComboBox:on {{ border-color: {accent}; }}
QComboBox:disabled {{ color: {disabled}; background: {bg}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ border: 1px solid {line}; background: {surface}; outline: none; padding: 4px;
    selection-background-color: {accent_soft}; selection-color: {ink}; }}
#Desk {{ background: {desk}; }}
QPushButton#Recent {{ border: none; background: transparent; text-align: left; padding: 7px 10px; color: {ink}; }}
QPushButton#Recent:hover {{ background: {surface}; }}
QLabel#H1 {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 14pt; font-weight: 600; }}
QLabel#H2 {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 11pt; font-weight: 600; }}
QLabel#Muted {{ color: {muted}; }}
#DropZone {{ border: 2px dashed {border_strong}; border-radius: 16px; background: {surface}; }}
#DropZone:hover, #DropZone[hot="true"] {{ border-color: {accent}; background: {hot_bg}; }}
QListWidget#Changes {{ border: none; background: {surface}; outline: none; }}
QListWidget#Changes::item {{ padding: 8px 6px; border-radius: 8px; margin: 1px 6px; }}
QListWidget#Changes::item:selected {{ background: {accent_soft}; color: {ink}; }}
QListWidget#Changes::item:hover:!selected {{ background: {row_hover}; }}
QSplitter::handle {{ background: {line}; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}
QSplitter::handle:hover {{ background: {accent}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {handle}; border-radius: 4px; min-height: 30px; }}
QScrollBar#Marks:vertical {{ width: 16px; background: {hover}; }}
QScrollBar#Marks::handle:vertical {{ background: {marks_handle}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {handle}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {line}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent_bg}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {surface}; border: 1px solid {border_strong}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QMessageBox {{ background: {surface}; }}
QMessageBox QLabel#qt_msgbox_label {{ min-width: 320px; max-width: 460px; font-size: 10.5pt; padding: 4px 0 8px 6px; }}
QMessageBox QLabel#qt_msgbox_informativelabel {{ color: {muted}; padding-left: 6px; }}
QMessageBox QPushButton {{ min-width: 84px; padding: 7px 14px; }}
"""

# 24x24 line icons, drawn for this app (stroke = currentColor).
_ICONS = {
    "open": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
    "save": '<path d="M12 4v10M7.5 9.5 12 14l4.5-4.5M5 19h14"/>',
    "export": '<path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 14v5h14v-5"/>',
    "zoom-in": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2M8.5 11h5M11 8.5v5"/>',
    "zoom-out": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2M8.5 11h5"/>',
    "prev": '<path d="m14.5 6-6 6 6 6"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6h.01"/>',
    "alert": '<path d="M10.3 4.9 2.9 17.6A2 2 0 0 0 4.6 20.6h14.8a2 2 0 0 0 1.7-3L13.7 4.9a2 2 0 0 0-3.4 0z"/><path d="M12 10v4M12 17.2h.01"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.5v.4M12 17h.01"/>',
    "next": '<path d="m9.5 6 6 6-6 6"/>',
    "down": '<path d="m6 9.5 6 6 6-6"/>',
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
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.2"/><path d="M15.5 8.5V6A2 2 0 0 0 13.5 4H6A2 2 0 0 0 4 6v7.5a2 2 0 0 0 2 2h2.5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "wand": '<path d="m4 20 11-11M14 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 11l.6 1.4L21 13l-1.4.6L19 15l-.6-1.4L17 13l1.4-.6z"/>',
    "batch": '<rect x="7" y="3" width="13" height="16" rx="2"/><path d="M4 7v12a2 2 0 0 0 2 2h10M10.5 8h6M10.5 12h6M10.5 16h3"/>',
    "table": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10"/>',
    "rotate-left": '<path d="M4 4.5V9h4.5"/><path d="M4.8 9a7.5 7.5 0 1 1 1.4 7.6"/>',
    "rotate-right": '<path d="M20 4.5V9h-4.5"/><path d="M19.2 9a7.5 7.5 0 1 0-1.4 7.6"/>',
    "expand": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "page": '<rect x="6" y="3.5" width="12" height="17" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    "columns": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M12 4.5v15"/>',
    "rows": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 12h17"/>',
    "moon": '<path d="M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "button": '<rect x="3" y="7" width="18" height="10" rx="3.5"/><path d="M8 12h8"/>',
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


DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFlex"  # recent files, blocks, signature


def load_json(name: str, default):
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(name: str, data) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # a missing list of recent files is not worth an error


def load_bytes(name: str) -> bytes | None:
    try:
        return (DATA_DIR / name).read_bytes()
    except OSError:
        return None


def save_bytes(name: str, data: bytes) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / name).write_bytes(data)
    except OSError:
        pass


def recent_files() -> list[str]:
    return [p for p in load_json("recenti.json", []) if Path(p).exists()]


def add_recent(path: str) -> None:
    path = str(Path(path).resolve())
    save_json("recenti.json", ([path] + [p for p in recent_files() if p != path])[:8])


def stylesheet() -> str:
    """QSS of the theme in use, plus the combo arrow, which QSS can only take from a file."""
    arrow = Path(tempfile.gettempdir()) / f"pdflex-down-{THEME}.png"
    icon("down", color("muted")).pixmap(24, 24).save(str(arrow))
    return (QSS.format(**THEMES[THEME]) +
            f'QComboBox::down-arrow {{ image: url("{arrow.as_posix()}"); width: 12px; height: 12px; }}')


def preferred_theme() -> str:
    """The theme chosen with the button, or else the one Windows uses."""
    saved = load_json("tema.json", {}).get("tema")
    if saved in THEMES:
        return saved
    return "dark" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"


def set_glyph(widget, glyph: str, tone: str = "ink", size: int = 0) -> None:
    """An icon that follows the theme: tone is a key of THEMES. Labels get a picture of `size` px."""
    widget.setProperty("glyph", glyph)
    widget.setProperty("tone", tone)
    widget.setProperty("glyph_size", size)
    pic = icon(glyph, color(tone))
    widget.setPixmap(pic.pixmap(size, size)) if size else widget.setIcon(pic)


def apply_theme(app, name: str) -> None:
    """Light or dark window chrome, applied live: colours, and the icons drawn in them."""
    global THEME
    THEME = name
    t = THEMES[name]
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "ink"), (QPalette.Base, "surface"),
                      (QPalette.AlternateBase, "hover"), (QPalette.Text, "ink"), (QPalette.Button, "surface"),
                      (QPalette.ButtonText, "ink"), (QPalette.ToolTipBase, "tip_bg"), (QPalette.ToolTipText, "tip_fg"),
                      (QPalette.PlaceholderText, "disabled"), (QPalette.Highlight, "accent_bg"),
                      (QPalette.HighlightedText, "white"), (QPalette.Mid, "border"), (QPalette.Light, "surface"),
                      (QPalette.Dark, "border_strong"), (QPalette.BrightText, "white")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(t["disabled"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["disabled"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t["disabled"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())
    for w in app.allWidgets():
        if w.property("glyph"):
            set_glyph(w, w.property("glyph"), w.property("tone") or "ink", w.property("glyph_size") or 0)
        w.update()


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


class Bar(QFrame):
    """Command bar at the top of a tab. When the window is too narrow for it, the buttons with an icon
    drop their text (it stays in the tooltip) instead of forcing the whole window wider."""

    def __init__(self):
        super().__init__(objectName="Bar")
        self.setMinimumWidth(360)
        self._compact = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _set_compact(self, on: bool):
        self._compact = on
        for b in self.findChildren(QPushButton):
            full = b.property("full")
            if full is None and b.icon().isNull():
                continue
            if full is None:
                full = b.text()
                b.setProperty("full", full)
                if full and not b.toolTip():
                    b.setToolTip(full)
            b.setText("" if on else full)

    def _fit(self):
        if self._compact:
            self._set_compact(False)  # measure with the words back
        self.layout().invalidate()
        if self.layout().sizeHint().width() > self.width():
            self._set_compact(True)


class DropZone(QFrame):
    """Big dashed target: click to choose a PDF, or drop one on it."""
    picked = Signal(str)

    def __init__(self, title: str, hint: str = "Trascina qui il PDF o clicca per sceglierlo", glyph: str = "open",
                 exts=(".pdf",), file_filter="PDF (*.pdf)"):
        super().__init__()
        self.exts, self.file_filter = exts, file_filter
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(280, 220)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(8)
        self.glyph, self.default_hint = glyph, hint
        self.pic = QLabel()
        set_glyph(self.pic, glyph, "accent", 44)
        self.pic.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title, objectName="H2", alignment=Qt.AlignCenter)
        self.hint = QLabel(hint, objectName="Muted", alignment=Qt.AlignCenter)
        self.hint.setWordWrap(True)
        for w in (self.pic, self.title, self.hint):
            lay.addWidget(w)

    def set_file(self, path: str | None):
        """Green frame, tick and the file name once a file is chosen; None goes back to empty."""
        self.setProperty("loaded", bool(path))
        self.style().unpolish(self)
        self.style().polish(self)
        set_glyph(self.pic, "checkbox" if path else self.glyph, "green" if path else "accent", 44)
        self.hint.setText(f"{Path(path).name}\nCaricato. Clicca per cambiarlo." if path else self.default_hint)
        self.hint.setStyleSheet(f"color: {color('green')}; font-weight: 600;" if path else "")

    def _hot(self, on):
        self.setProperty("hot", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(self, "Scegli un file", "", self.file_filter)
        if path:
            self.picked.emit(path)

    def dragEnterEvent(self, e):
        if pdf_from_drop(e, self.exts):
            e.acceptProposedAction()
            self._hot(True)

    def dragLeaveEvent(self, e):
        self._hot(False)

    def dropEvent(self, e):
        self._hot(False)
        path = pdf_from_drop(e, self.exts)
        if path:
            self.picked.emit(path)


def pdf_from_drop(e, exts=(".pdf",)) -> str | None:
    urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
    paths = [u.toLocalFile() for u in urls if u.toLocalFile().lower().endswith(exts)]
    return paths[0] if paths else None


def button(text, glyph=None, name=None, tip=None) -> QPushButton:
    b = QPushButton(text)
    if glyph:
        set_glyph(b, glyph, "white" if name == "Primary" else "ink")
    if name:
        b.setObjectName(name)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Toggle(QCheckBox):
    """On/off switch: same API as QCheckBox, drawn as a sliding pill."""
    W, H = 36, 20

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=120, valueChanged=self._moved)
        self.toggled.connect(lambda on: (self._anim.stop(), self._anim.setStartValue(self._pos),
                                         self._anim.setEndValue(1.0 if on else 0.0), self._anim.start()))

    def setChecked(self, on):  # programmatic changes jump, no animation
        super().setChecked(on)
        self._anim.stop()
        self._moved(1.0 if on else 0.0)

    def _moved(self, v):
        self._pos = float(v)
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(self.W + 10 + fm.horizontalAdvance(self.text()) + 2, max(self.H, fm.height()) + 6)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        y = (self.height() - self.H) / 2
        on = QColor(color("accent_bg"))
        off = QColor(color("toggle_off"))
        track = QColor.fromRgbF(*(a + (b - a) * self._pos for a, b in zip(off.getRgbF()[:3], on.getRgbF()[:3])))
        if self.hasFocus():
            p.setPen(QPen(QColor(color("accent_soft") if self.isChecked() else color("focus_ring")), 4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, y - 1, self.W, self.H + 2), self.H / 2 + 1, self.H / 2 + 1)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(1, y, self.W, self.H), self.H / 2, self.H / 2)
        d = self.H - 6
        x = 4 + self._pos * (self.W - d - 6)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, y + 3.6, d, d))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, y + 3, d, d))
        p.setPen(QColor(color("ink")))
        p.drawText(self.rect().adjusted(self.W + 10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())


def message(parent, text: str, kind: str = "info", buttons=("OK",), note: str = "") -> int:
    """App-styled, Italian message box. buttons[0] is the highlighted default, the last one answers Esc.

    Returns the index of the button pressed."""
    box = QMessageBox(parent)
    box.setWindowTitle("PDFlex")
    box.setText(text)
    if note:
        box.setInformativeText(note)
    glyph, tint = {"info": ("info", color("accent")), "ask": ("help", color("accent")), "warn": ("alert", AMBER),
                   "error": ("alert", RED)}[kind]
    box.setIconPixmap(icon(glyph, tint).pixmap(40, 40))
    made = []
    for i, label in enumerate(buttons):
        b = box.addButton(label, QMessageBox.AcceptRole if i == 0 else
                          QMessageBox.RejectRole if i == len(buttons) - 1 else QMessageBox.DestructiveRole)
        b.setCursor(Qt.PointingHandCursor)
        made.append(b)
    made[0].setObjectName("Primary")
    made[0].style().unpolish(made[0])  # the name changed after styling: restyle
    made[0].style().polish(made[0])
    box.setDefaultButton(made[0])
    box.setEscapeButton(made[-1])
    box.exec()
    return made.index(box.clickedButton()) if box.clickedButton() in made else len(made) - 1


def ask_text(parent, title: str, label: str, text: str = "") -> str | None:
    """One line of text from the user, with Italian buttons; None if cancelled."""
    from PySide6.QtWidgets import QInputDialog
    d = QInputDialog(parent)
    d.setWindowTitle(title)
    d.setLabelText(label)
    d.setTextValue(text)
    d.setOkButtonText("OK")
    d.setCancelButtonText("Annulla")
    return d.textValue().strip() if d.exec() else None


def info(parent, text: str) -> None:
    message(parent, text, "info")


def warn(parent, text: str) -> None:
    message(parent, text, "warn")


class Cancelled(Exception):
    """The user pressed Annulla on a progress bar."""


@contextmanager
def progress(parent, text: str, total: int, cancel: bool = False):
    """Bar for slow work on long documents, shown only if it lasts more than half a second.

    Yields set_value(done); on a modal dialog that also keeps the window painted. total=0: a moving bar.
    cancel=True adds an Annulla button: set_value then raises Cancelled once it is pressed."""
    d = QProgressDialog(text, "Annulla", 0, total, parent)
    if not cancel:
        d.setCancelButton(None)
    d.setWindowTitle("PDFlex")
    d.setWindowModality(Qt.WindowModal)
    d.setMinimumDuration(500)
    d.setMinimumWidth(360)

    def step(value):
        d.setValue(value)
        if d.wasCanceled():
            raise Cancelled
    try:
        yield step if cancel else d.setValue
    finally:
        d.reset()  # also stops the timer that would pop it up later
        d.deleteLater()


def run_busy(parent, text: str, fn):
    """fn() in a thread, so a long job doesn't freeze the window ("Non risponde"); returns its result.

    Meanwhile the window keeps painting but ignores clicks and keys: no second job can start."""
    out = {}

    def work():
        try:
            out["value"] = fn()
        except BaseException as e:  # handed back to the caller below
            out["error"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    with progress(parent, text, 0):
        while t.is_alive():
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            t.join(0.03)
    if "error" in out:
        raise out["error"]
    return out["value"]


def toggle_full_screen(widget) -> None:
    w = widget.window()
    w.setWindowState(w.windowState() ^ Qt.WindowFullScreen)  # back to maximized if it was


def show_doc(parent, doc, title: str, note: str = "") -> None:
    """A document in its own resizable window. Afterwards the window is deleted and the document closed,
    so nothing piles up in memory and the file is not left locked (it was making PDFlex crash)."""
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.setWindowFlag(Qt.WindowMaximizeButtonHint)
    d.resize(880, 940)
    lay = QVBoxLayout(d)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    if note:
        label = QLabel(note, objectName="Muted")
        label.setWordWrap(True)
        label.setContentsMargins(16, 10, 16, 10)
        lay.addWidget(label)
    view = DocView(annots=True)
    lay.addWidget(view, 1)
    view.show_doc(doc)
    d.exec()
    view.doc = None  # no late re-render of a closed document
    d.deleteLater()
    doc.close()


class HtmlDelegate(QStyledItemDelegate):
    """List rows drawn from the HTML stored in Qt.UserRole + 1 (colours, struck-through words)."""
    PAD = 14  # item margin + padding of QListWidget#Changes

    def _doc(self, option, index) -> QTextDocument:
        d = QTextDocument()
        d.setDefaultFont(option.font)
        d.setDocumentMargin(0)
        d.setDefaultStyleSheet(f"body {{ color: {color('ink')}; }}")  # else black, even on the dark theme
        d.setHtml(index.data(Qt.UserRole + 1) or "")
        d.setTextWidth(max(self.parent().viewport().width() - 2 * self.PAD - 4, 80))
        return d

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text, opt.icon = "", QIcon()
        opt.widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)  # hover/selection
        painter.save()
        painter.translate(opt.rect.left() + self.PAD, opt.rect.top() + 9)
        self._doc(option, index).drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(0, round(self._doc(option, index).size().height()) + 20)


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


class MarkBar(QScrollBar):
    """Vertical scrollbar with a coloured tick wherever there is a change; a click jumps there."""

    def __init__(self):
        super().__init__(Qt.Vertical)
        self.setObjectName("Marks")
        self.marks: list[tuple[float, str]] = []  # (0..1 down the document, colour)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.marks:
            return
        p = QPainter(self)
        h = self.height() - 6
        for frac, color in self.marks:
            p.fillRect(QRectF(2, 3 + frac * h - 1.5, self.width() - 4, 3), QColor(color))

    def mousePressEvent(self, e):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle = self.style().subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, self)
        if e.button() == Qt.LeftButton and not handle.contains(e.position().toPoint()):
            total = self.maximum() + self.pageStep()
            self.setValue(round(e.position().y() / max(self.height(), 1) * total - self.pageStep() / 2))
        else:
            super().mousePressEvent(e)


class DocView(QGraphicsView):
    """All pages of a document stacked, fitted to the width and re-rendered sharp on resize.

    Words/marks are anything with .page and .rect (display coordinates, points)."""
    GAP = 16
    scaled = Signal(float)  # Ctrl+wheel changed the zoom

    def __init__(self, annots=True):
        self._scene = QGraphicsScene()  # keep a Python reference or Qt frees it
        super().__init__(self._scene)
        self.annots = annots
        self.doc = None
        self.k = 1.0
        self.scale = 1.0  # zoom on top of "fit to width"
        self.offsets: list[float] = []
        self.heights: list[float] = []
        self.highlights = []  # (words, rgb, opacity)
        self.current = ([], None)
        self._marks = []
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBar(MarkBar())
        self.marks = []  # (word, colour) shown as ticks on the scrollbar
        self._timer = QTimer(self, singleShot=True, interval=120, timeout=self.render)
        self._rendered, self._dpr = {}, 1.0  # page number -> its picture on the scene
        self.verticalScrollBar().valueChanged.connect(self._render_visible)

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
        # width with the scrollbar always reserved: it comes and goes with the zoom, the fit must not move
        avail = self.width() - 2 * self.frameWidth() - self.verticalScrollBar().sizeHint().width()
        self.k = max(avail - 2 * self.GAP, 200) / widest * self.scale
        dpr = self.devicePixelRatioF()
        self.offsets, self.heights, y = [], [], self.GAP
        self._rendered, self._dpr = {}, dpr
        for page in self.doc:  # white sheets now, the picture only when the page comes into view
            w, h = page.rect.width * self.k, page.rect.height * self.k
            sheet = s.addRect(self.GAP, y, w, h, QPen(Qt.NoPen), QBrush(Qt.white))
            sheet.setZValue(-2)
            page_shadow(sheet)
            self.offsets.append(y)
            self.heights.append(h)
            y += h + self.GAP
        s.setSceneRect(0, 0, widest * self.k + 2 * self.GAP, y)
        for words, rgb, opacity in self.highlights:
            self._paint(words, rgb, opacity)
        self._paint_current()
        bar.setValue(round(frac * bar.maximum()))
        self._render_visible()
        self._update_marks()

    def set_marks(self, marks):
        self.marks = list(marks)
        self._update_marks()

    def _update_marks(self):
        total = self.scene().sceneRect().height() if self.offsets else 0
        bar = self.verticalScrollBar()
        bar.marks = [(self.word_rect(w)[1] / total, color) for w, color in self.marks] if total else []
        bar.update()

    def _render_visible(self):
        """Draw the pages on screen (and one screen above/below) and free the ones far away:
        long documents stay quick and memory stays flat however much you scroll."""
        if not self.doc or not self.offsets or self.scene() is None:  # None while the view is being destroyed
            return
        top = self.verticalScrollBar().value()
        span = self.viewport().height()
        for i, (off, h) in enumerate(zip(self.offsets, self.heights)):
            if i not in self._rendered and off + h >= top - span and off <= top + 2 * span:
                item = self.scene().addPixmap(render_page(self.doc[i], self.k, self.annots, self._dpr))
                item.setOffset(self.GAP, off)
                item.setZValue(-1)  # under the highlights
                self._rendered[i] = item
            elif i in self._rendered and (off + h < top - 3 * span or off > top + 4 * span):
                self.scene().removeItem(self._rendered.pop(i))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._timer.start()

    def page_fit_scale(self) -> float:
        """The zoom that shows the current page whole."""
        if not self.offsets:
            return self.scale
        i, _ = self.position()
        page_h = self.heights[i] / self.k  # points
        fit_width = self.k / self.scale
        return (self.viewport().height() - 2 * self.GAP) / (page_h * fit_width)

    def set_scale(self, scale: float):
        scale = min(max(scale, 0.2), 5.0)
        if abs(scale - self.scale) > 1e-6:
            self.scale = scale
            self.render()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.set_scale(self.scale * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15))
            self.scaled.emit(self.scale)
        else:
            super().wheelEvent(e)

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
