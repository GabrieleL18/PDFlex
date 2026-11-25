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


def test_update_check_compares_versions_and_stays_quiet_offline(qapp, monkeypatch):
    import io
    import json
    from pdflex import app
    assert app.version_parts("v1.10") > app.version_parts("1.9") > app.version_parts("1.3")
    reply = {"tag_name": "v1.4", "html_url": "https://github.com/x/releases/tag/v1.4"}
    monkeypatch.setattr(app.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(reply).encode()))
    assert app.newer_release("1.3") == ("1.4", reply["html_url"])
    assert app.newer_release("1.4") is None

    def offline(req, timeout):
        raise OSError("no network")
    monkeypatch.setattr(app.urllib.request, "urlopen", offline)
    assert app.newer_release("1.3") is None
    w = app.MainWindow()
    w._show_update("1.4", reply["html_url"])
    assert not w.b_update.isHidden() and w.b_update.text() == "Scarica\nv1.4"
    w.close()
