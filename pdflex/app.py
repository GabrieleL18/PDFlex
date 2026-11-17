import ctypes
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from pdflex import ui
from pdflex.compare import CompareTab
from pdflex.editor import EditorTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDFlex")
        self.setWindowIcon(ui.app_icon())
        self.resize(1400, 880)
        self.editor = EditorTab()
        self.compare = CompareTab()
        self.tabs = QStackedWidget()
        self.tabs.addWidget(self.editor)
        self.tabs.addWidget(self.compare)

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
        for i, (glyph, label) in enumerate([("form", "Moduli"), ("compare", "Confronta")]):
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
        self.editor.title_changed.connect(lambda name: self.setWindowTitle(f"{name} - PDFlex"))

    def closeEvent(self, e):
        if self.editor.confirm_discard():
            e.accept()
        else:
            e.ignore()


def setup(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(ui.QSS)
    app.setWindowIcon(ui.app_icon())


def main() -> None:
    if sys.platform == "win32":  # own taskbar entry and icon instead of python.exe's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFlex")
    app = QApplication.instance() or QApplication(sys.argv)
    setup(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
