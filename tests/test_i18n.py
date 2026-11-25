import re

from pdflex import i18n
from pdflex.i18n_en import EN


def test_english_keeps_every_placeholder():
    for it, en in EN.items():
        assert sorted(re.findall(r"\{[^}]*\}", it)) == sorted(re.findall(r"\{[^}]*\}", en)), it


def test_language_choice_is_saved_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "FILE", tmp_path / "lingua.json")
    assert i18n.saved() is None  # nothing chosen yet: PDFlex asks on first start
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

