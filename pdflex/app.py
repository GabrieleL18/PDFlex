import ctypes
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
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
            b.setIcon(ui.icon(glyph, ui.ACCENT))
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

        root = QWidget(objectName="Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(rail)
        h.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.editor.title_changed.connect(lambda name: self.setWindowTitle(f"{name} - PDFlex" if name else "PDFlex"))
        if path:  # "Apri con PDFlex" or double click on a PDF
            self.editor.open(path)

    def closeEvent(self, e):
        if self.editor.confirm_discard():
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
    app.setStyleSheet(ui.stylesheet())
    app.setWindowIcon(ui.app_icon())


def main() -> None:
    if sys.platform == "win32":  # own taskbar entry and icon instead of python.exe's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFlex")
    app = QApplication.instance() or QApplication(sys.argv)
    setup(app)
    sys.excepthook = _show_error
    w = MainWindow(next((a for a in sys.argv[1:] if a.lower().endswith(".pdf")), None))
    w.show()
    sys.exit(app.exec())
