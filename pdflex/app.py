import base64
import ctypes
import faulthandler
import os
import shutil
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from pdflex import __version__, ui, update
from pdflex.batch import BatchTab
from pdflex.compare import CompareTab
from pdflex.designer import DesignTab
from pdflex.editor import EditorTab
from pdflex.pages import PagesTab
from pdflex import i18n
from pdflex.i18n import tr


class MainWindow(QMainWindow):
    update_found = Signal(str, str, str)  # version, release page, zip: from the checking thread
    update_progress = Signal(int)
    update_ready = Signal(str, str)  # unpacked folder, or "" and what went wrong

    def __init__(self, path: str | None = None):
        super().__init__()
        self.setWindowTitle("PDFlex")
        self.setWindowIcon(ui.app_icon())
        self.resize(1400, 880)
        saved = ui.load_json("finestra.json", {}).get("geometria")
        if saved:  # size, place and maximized as when it was last closed
            self.restoreGeometry(QByteArray(base64.b64decode(saved)))
        self.design = DesignTab()
        self.editor = EditorTab()
        self.compare = CompareTab()
        self.batch = BatchTab()
        self.pages = PagesTab()
        self.tabs = QStackedWidget()
        for w in (self.design, self.editor, self.compare, self.batch, self.pages):
            self.tabs.addWidget(w)
        self.tabs.setCurrentWidget(self.editor)  # Moduli stays the tab PDFlex opens on

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
        self.rail = {}
        for glyph, label, tab in [("page", tr("Editor"), self.design), ("form", tr("Moduli"), self.editor),
                                  ("compare", tr("Confronta"), self.compare), ("batch", tr("In serie"), self.batch),
                                  ("list", tr("Pagine"), self.pages)]:
            b = QToolButton()
            b.setText(label)
            ui.set_glyph(b, glyph, "accent")
            b.setIconSize(QSize(24, 24))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setChecked(tab is self.editor)
            b.setFixedWidth(76)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=tab: self.tabs.setCurrentWidget(t))
            group.addButton(b)
            self.rail[tab] = b
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
        other = "en" if i18n.LANG == "it" else "it"
        self.b_lang = QToolButton(text=i18n.LANGS[other], cursor=Qt.PointingHandCursor)
        self.b_lang.setFixedWidth(76)
        self.b_lang.setToolTip(tr("Usa PDFlex in un'altra lingua"))
        self.b_lang.clicked.connect(lambda: self.switch_language(other))
        rl.addWidget(self.b_lang, alignment=Qt.AlignHCenter)
        self.b_update = QToolButton(cursor=Qt.PointingHandCursor, objectName="Primary")
        self.b_update.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.b_update.setFixedWidth(76)
        ui.set_glyph(self.b_update, "down", "accent")
        self.b_update.hide()  # until GitHub has a newer version
        self.update_found.connect(self._show_update)
        self.update_progress.connect(lambda p: self.b_update.setText(tr("Scarico\n{0}%").format(p)))
        self.update_ready.connect(self._install_update)
        self.b_update.clicked.connect(self.get_update)
        self.version = QLabel(f"v{__version__}", objectName="Muted", alignment=Qt.AlignCenter)
        self.version.setToolTip(tr("Versione di PDFlex"))
        rl.addWidget(self.version)
        rl.addWidget(self.b_update, alignment=Qt.AlignHCenter)

        root = QWidget(objectName="Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(rail)
        h.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.design.open_in_forms.connect(self.open_in_forms)
        self.editor.title_changed.connect(lambda name: self.setWindowTitle(f"{name} - PDFlex" if name else "PDFlex"))
        QShortcut(QKeySequence("F11"), self, lambda: ui.toggle_full_screen(self))
        self.esc = QShortcut(QKeySequence("Esc"), self, lambda: ui.toggle_full_screen(self))
        self.esc.setEnabled(False)  # only while full screen: elsewhere Esc belongs to the widgets
        if path:  # "Apri con PDFlex" or double click on a PDF
            self.editor.open(path)

    def open_in_forms(self, path: str):
        """The Editor hands its saved PDF to Moduli, to fix the fields."""
        self.editor.open(path)
        if self.editor.path == path:
            self.tabs.setCurrentWidget(self.editor)
            self.rail[self.editor].setChecked(True)

    def _theme_button(self):
        dark = ui.THEME == "dark"
        ui.set_glyph(self.b_theme, "sun" if dark else "moon", "muted")
        self.b_theme.setText(tr("Chiaro") if dark else tr("Scuro"))
        self.b_theme.setToolTip(tr("Passa al tema chiaro") if dark else tr("Passa al tema scuro"))

    def toggle_theme(self):
        name = "light" if ui.THEME == "dark" else "dark"
        ui.apply_theme(QApplication.instance(), name)
        ui.save_json("tema.json", {"tema": name})  # chosen once: no longer follows Windows
        self._theme_button()

    def check_updates(self):
        """In the background: a slow or missing connection never holds up the start."""
        def run():
            found = update.newer_release()
            if found:
                self.update_found.emit(*found)
        threading.Thread(target=run, daemon=True).start()

    def _show_update(self, version: str, page: str, zip_url: str = ""):
        self.update_info = version, page, zip_url
        self.b_update.setText(tr("Aggiorna\nv{0}").format(version))
        self.b_update.setToolTip(tr("È uscito PDFlex {0}: clicca per installarlo").format(version))
        self.b_update.show()

    def get_update(self):
        """Download the new version, then close, install and reopen by itself."""
        version, page, zip_url = self.update_info
        if not zip_url or not getattr(sys, "frozen", False):  # no zip in the release, or run from the sources
            QDesktopServices.openUrl(QUrl(page))
            return
        if ui.message(self, tr("Installare PDFlex {0} adesso?").format(version), "ask",
                      (tr("Aggiorna"), tr("Più tardi")),
                      note=tr("PDFlex lo scarica, si chiude, si aggiorna e si riapre da solo. "
                              "I file scaricati vengono cancellati dopo l'installazione.")) != 0:
            return
        self.b_update.setEnabled(False)

        def run():
            try:
                self.update_ready.emit(str(update.download(zip_url, self.update_progress.emit)), "")
            except (OSError, ValueError) as e:
                self.update_ready.emit("", str(e))
        threading.Thread(target=run, daemon=True).start()

    def _install_update(self, files: str, error: str):
        version, page, _ = self.update_info
        self.b_update.setEnabled(True)
        self.b_update.setText(tr("Aggiorna\nv{0}").format(version))
        if not files:
            shutil.rmtree(update.FOLDER, ignore_errors=True)
            if ui.message(self, tr("Download non riuscito: {0}").format(error), "warn",
                          (tr("Apri la pagina della versione"), tr("Chiudi"))) == 0:
                QDesktopServices.openUrl(QUrl(page))
            return
        if i18n.saved() is None:
            i18n.save(i18n.LANG)  # so the installer does not stop to ask the language
        if not self.close():  # unsaved work and "Annulla": PDFlex stays open, the download goes
            shutil.rmtree(update.FOLDER, ignore_errors=True)
            return
        update.start_install(Path(files))
        QApplication.quit()

    def switch_language(self, lang: str):
        i18n.save(lang)  # the labels are made at start: the next start uses it
        ui.message(self, "PDFlex will be in English the next time you open it." if lang == "en"
                   else "PDFlex sarà in italiano la prossima volta che lo apri.")

    def changeEvent(self, e):
        super().changeEvent(e)
        if hasattr(self, "esc"):  # changeEvent also comes while __init__ is still running
            self.esc.setEnabled(self.isFullScreen())

    def closeEvent(self, e):
        if self.design.confirm_discard() and self.editor.confirm_discard():
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
    ui.message(QApplication.activeWindow(), tr("Qualcosa non ha funzionato: {0}").format(exc), "error",
               note=tr("I dettagli sono salvati in {0}").format(LOG))


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
    w.check_updates()
    sys.exit(app.exec())
