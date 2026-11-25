"""Interface language. The Italian text is the key; tr() gives it back in the chosen language.
Windows' own language unless one was picked with the button (lingua.json); read once, before the rest
of PDFlex is imported, so module-level labels come out translated too."""
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


def system() -> str:
    """Italian on an Italian Windows, English on any other."""
    try:
        import ctypes
        return "it" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x10 else "en"
    except (AttributeError, OSError):  # not Windows
        import locale
        return "it" if (locale.getlocale()[0] or "").lower().startswith("it") else "en"


def tr(text: str) -> str:
    return _TABLE.get(text, text)


use(os.environ.get("PDFLEX_LANG") or saved() or system())
