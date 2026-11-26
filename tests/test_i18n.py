import re

from pdflex import i18n
from pdflex.i18n_en import EN


def test_english_keeps_every_placeholder():
    for it, en in EN.items():
        assert sorted(re.findall(r"\{[^}]*\}", it)) == sorted(re.findall(r"\{[^}]*\}", en)), it


def test_language_choice_is_saved_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "FILE", tmp_path / "lingua.json")
    assert i18n.saved() is None  # nothing picked with the button: Windows' language
    assert i18n.system() in ("it", "en")
    i18n.save("en")
    assert i18n.saved() == "en"
    (tmp_path / "lingua.json").write_bytes(b'\xef\xbb\xbf{"lingua": "it"}')  # written by PowerShell with a BOM
    assert i18n.saved() == "it"


def test_tr_translates_only_in_english(monkeypatch):
    monkeypatch.setattr(i18n, "_TABLE", EN)
    assert i18n.tr("Salva modulo") == "Save form"
    assert i18n.tr("testo che non c'è") == "testo che non c'è"
    monkeypatch.setattr(i18n, "_TABLE", {})
    assert i18n.tr("Salva modulo") == "Salva modulo"



def test_every_tr_string_has_english():
    """Every literal tr("...") in the app has an English entry; shortcuts and file filters read the same."""
    import ast
    import pathlib
    same = re.compile(r"^(Ctrl|Alt|Shift)|\(\*\.|^hh:mm|^the page's stream|^C:/|^ ?<span style=\"\{0\}\">\W*\{1\}</span>$")
    missing = set()
    for path in pathlib.Path(i18n.__file__).parent.glob("*.py"):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                text = n.args[0].value
                if text not in EN and not same.search(text):
                    missing.add(f"{path.name}: {text}")
    assert not missing, sorted(missing)
