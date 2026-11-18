import base64
import ctypes
import faulthandler
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from pdflex import ui
from pdflex.batch import BatchTab
from pdflex.compare import CompareTab
from pdflex.editor import EditorTab
from pdflex.pages import PagesTab


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.setWindowTitle("PDFlex")
        self.setWindowIcon(ui.app_icon())
        self.resize(1400, 880)
        saved = ui.load_json("finestra.json", {}).get("geometria")
        if saved:  # size, place and maximized as when it was last closed
            self.restoreGeometry(QByteArray(base64.b64decode(saved)))
        self.editor = EditorTab()
        self.compare = CompareTab()
        self.batch = BatchTab()
        self.pages = PagesTab()
        self.tabs = QStackedWidget()
        for w in (self.editor, self.compare, self.batch, self.pages):
            self.tabs.addWidget(w)

        rail = QFrame(objectName="Rail")
        rail.setFixedWidth(92)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(8, 14, 8, 14)
        rl.setSpacing(6)
        logo = QLabel(alignment=Qt.AlignCenter)
        logo.setPixmap(ui.app_icon().pixmap(40, 40))
        rl.addWidget(logo)
        rl.addSpacing(14)
        group = QButtonGroup(self)
        for i, (glyph, label) in enumerate([("form", "Moduli"), ("compare", "Confronta"), ("batch", "In serie"), ("list", "Pagine")]):
            b = QToolButton()
            b.setText(label)
            ui.set_glyph(b, glyph, "accent")
            b.setIconSize(QSize(24, 24))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setFixedWidth(76)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.tabs.setCurrentIndex(i))
            group.addButton(b)
            rl.addWidget(b, alignment=Qt.AlignHCenter)
        rl.addStretch()
        self.b_theme = QToolButton()
        self.b_theme.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.b_theme.setIconSize(QSize(22, 22))
        self.b_theme.setFixedWidth(76)
        self.b_theme.setCursor(Qt.PointingHandCursor)
        self.b_theme.clicked.connect(self.toggle_theme)
        rl.addWidget(self.b_theme, alignment=Qt.AlignHCenter)
        self._theme_button()

        root = QWidget(objectName="Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(rail)
        h.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.editor.title_changed.connect(lambda name: self.setWindowTitle(f"{name} - PDFlex" if name else "PDFlex"))
        QShortcut(QKeySequence("F11"), self, lambda: ui.toggle_full_screen(self))
        self.esc = QShortcut(QKeySequence("Esc"), self, lambda: ui.toggle_full_screen(self))
        self.esc.setEnabled(False)  # only while full screen: elsewhere Esc belongs to the widgets
        if path:  # "Apri con PDFlex" or double click on a PDF
            self.editor.open(path)

    def _theme_button(self):
        dark = ui.THEME == "dark"
        ui.set_glyph(self.b_theme, "sun" if dark else "moon", "muted")
        self.b_theme.setText("Chiaro" if dark else "Scuro")
        self.b_theme.setToolTip("Passa al tema chiaro" if dark else "Passa al tema scuro")

    def toggle_theme(self):
        name = "light" if ui.THEME == "dark" else "dark"
        ui.apply_theme(QApplication.instance(), name)
        ui.save_json("tema.json", {"tema": name})  # chosen once: no longer follows Windows
        self._theme_button()

    def changeEvent(self, e):
        super().changeEvent(e)
        if hasattr(self, "esc"):  # changeEvent also comes while __init__ is still running
            self.esc.setEnabled(self.isFullScreen())

    def closeEvent(self, e):
        if self.editor.confirm_discard():
            ui.save_json("finestra.json", {"geometria": base64.b64encode(bytes(self.saveGeometry())).decode()})
            e.accept()
        else:
            e.ignore()


LOG = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFlex" / "errori.log"


def log_error(exc_type, exc, tb, log=LOG) -> str:
    """Append the error to the log; returns the full text."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%d/%m/%Y %H:%M:%S}\n{text}\n")
    except OSError:
        pass
    return text


def _show_error(exc_type, exc, tb):
    """Without a console an error would vanish and the button would just 'do nothing': say it."""
    log_error(exc_type, exc, tb)
    ui.message(QApplication.activeWindow(), f"Qualcosa non ha funzionato: {exc}", "error",
               note=f"I dettagli sono salvati in {LOG}")


def setup(app: QApplication) -> None:
    app.setStyle("Fusion")
    ui.apply_theme(app, ui.preferred_theme())
    app.setWindowIcon(ui.app_icon())


def main() -> None:
    if sys.platform == "win32":  # own taskbar entry and icon instead of python.exe's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFlex")
    app = QApplication.instance() or QApplication(sys.argv)
    setup(app)
    sys.excepthook = _show_error
    try:  # a hard crash (the window just vanishes) still leaves where it happened in the log
        LOG.parent.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(open(LOG, "a", encoding="utf-8"))  # kept open for the whole run on purpose
    except OSError:
        pass
    w = MainWindow(next((a for a in sys.argv[1:] if a.lower().endswith(".pdf")), None))
    w.show()
    sys.exit(app.exec())
