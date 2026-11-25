import io
import json
import zipfile

from pdflex import update


class Reply(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_newer_release_compares_versions_and_stays_quiet_offline(monkeypatch):
    assert update.version_parts("v1.10") > update.version_parts("1.9") > update.version_parts("1.3")
    reply = {"tag_name": "v1.4", "html_url": "https://github.com/x/releases/tag/v1.4",
             "assets": [{"name": "PDFlex-1.4.zip", "browser_download_url": "https://github.com/x/PDFlex-1.4.zip"}]}
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout: Reply(json.dumps(reply).encode()))
    assert update.newer_release("1.3") == ("1.4", reply["html_url"], "https://github.com/x/PDFlex-1.4.zip")
    assert update.newer_release("1.4") is None

    def offline(req, timeout):
        raise OSError("no network")
    monkeypatch.setattr(update.urllib.request, "urlopen", offline)
    assert update.newer_release("1.3") is None


def test_download_unpacks_and_the_installer_cleans_up(monkeypatch, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("install.ps1", "# install")
        z.writestr("dist/PDFlex/PDFlex.exe", "exe")
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout: Reply(data.getvalue()))
    seen = []
    folder = tmp_path / "PDFlex-aggiornamento"
    files = update.download("https://github.com/x/PDFlex-1.4.zip", seen.append, folder)
    assert (files / "install.ps1").exists() and (files / "dist/PDFlex/PDFlex.exe").exists()
    assert seen[-1] == 100 and not (folder / "PDFlex.zip").exists()  # the zip goes once unpacked
    monkeypatch.setattr(update.sys, "platform", "test")  # write the script, do not run it
    script = update.start_install(files, pid=1234, folder=folder).read_text(encoding="utf-8-sig")
    assert "Wait-Process -Id 1234" in script and "install.ps1" in script
    assert f"Remove-Item -LiteralPath '{folder}' -Recurse" in script  # the download is deleted afterwards


def test_update_button_shows_the_new_version(qapp):
    from pdflex.app import MainWindow
    w = MainWindow()
    w._show_update("1.4", "https://github.com/x/releases/tag/v1.4", "")
    assert not w.b_update.isHidden() and w.b_update.text() == "Aggiorna\nv1.4"
    w.close()
