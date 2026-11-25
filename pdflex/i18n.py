"""Interface language. The Italian text is the key; tr() gives it back in the chosen language.
The choice lives in lingua.json (written by install.ps1 or asked on the first start) and is read
once, before the rest of PDFlex is imported, so module-level labels come out translated too."""
import json
import os
from pathlib import Path

LANGS = {"it": "Italiano", "en": "English"}
FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFlex" / "lingua.json"


def saved() -> str | None:
    try:
        lang = json.loads(FILE.read_text(encoding="utf-8-sig")).get("lingua")
    except (OSError, ValueError, AttributeError):
        return None
    return lang if lang in LANGS else None


def save(lang: str) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps({"lingua": lang}), encoding="utf-8")


LANG, _TABLE = "it", {}


def use(lang: str) -> None:
    """Only before the rest of PDFlex is imported: labels made at import time keep the language they got."""
    global LANG, _TABLE
    LANG = lang if lang in LANGS else "it"
    if LANG == "en":
        from pdflex.i18n_en import EN
        _TABLE = EN
    else:
        _TABLE = {}


def ask() -> str:
    """First start without a choice (the installer asks too): one small window, both languages."""
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(QMessageBox.Question, "PDFlex", "Scegli la lingua di PDFlex\nChoose the language of PDFlex")
    buttons = {box.addButton(name, QMessageBox.AcceptRole): code for code, name in LANGS.items()}
    box.exec()
    return buttons.get(box.clickedButton(), "it")


def tr(text: str) -> str:
    return _TABLE.get(text, text)


use(os.environ.get("PDFLEX_LANG") or saved() or "it")
