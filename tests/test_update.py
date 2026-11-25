import base64
import io
import json
import zipfile

from pdflex import update


def Reply(data: bytes):
    """A fake update._curl answering `data`."""
    def curl(url, timeout, out=None, progress=None):
        if out is None:
            return data
        out.write_bytes(data)
        if progress:
            progress(100)
        return b""
    return curl


def _offline(*a, **kw):
    raise OSError("no network")


def _install_command(script: str) -> str:
    return base64.b64decode(script.split("'-EncodedCommand', '")[1].split("'")[0]).decode("utf-16-le")


def test_newer_release_prefers_the_installer_and_stays_quiet_offline(monkeypatch):
    assert update.version_parts("v1.10") > update.version_parts("1.9") > update.version_parts("1.3")
    reply = {"tag_name": "v1.5", "html_url": "https://github.com/x/releases/tag/v1.5",
             "assets": [{"name": "PDFlex-1.5.zip", "browser_download_url": "https://github.com/x/PDFlex-1.5.zip"},
                        {"name": "PDFlex-Setup-1.5.exe", "browser_download_url": "https://github.com/x/PDFlex-Setup-1.5.exe"}]}
    monkeypatch.setattr(update, "_curl", lambda *a, **kw: json.dumps(reply).encode())  # read again: reply changes below
    assert update.newer_release("1.4") == ("1.5", reply["html_url"], "https://github.com/x/PDFlex-Setup-1.5.exe")
    assert update.newer_release("1.5") is None
    reply["assets"].pop()  # an older release: only the zip
    assert update.newer_release("1.4")[2] == "https://github.com/x/PDFlex-1.5.zip"
    monkeypatch.setattr(update, "_curl", _offline)
    assert update.newer_release("1.4") is None


def test_zip_download_unpacks_and_the_installer_cleans_up(monkeypatch, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("install.ps1", "# install")
        z.writestr("dist/PDFlex/PDFlex.exe", "exe")
    monkeypatch.setattr(update, "_curl", Reply(data.getvalue()))
    seen = []
    folder = tmp_path / "PDFlex-aggiornamento"
    installer = update.download("https://github.com/x/PDFlex-1.4.zip", seen.append, "1.4", folder)
    assert installer.name == "install.ps1" and (installer.parent / "dist/PDFlex/PDFlex.exe").exists()
    assert seen[-1] == 100 and not (folder / "PDFlex.zip").exists()  # the zip goes once unpacked
    monkeypatch.setattr(update, "_curl", _offline)
    assert update.download("https://github.com/x/PDFlex-1.4.zip", None, "1.4", folder) == installer  # put off: kept
    monkeypatch.setattr(update.sys, "platform", "test")  # write the script, do not run it
    script = update.start_install(installer, "Aggiornamento in corso", pid=1234, folder=folder).read_text(encoding="utf-8-sig")
    assert str(installer) in _install_command(script)
    assert "Wait-Process -Id 1234" in script and "'Aggiornamento in corso'" in script  # the window says what goes on
    assert f"Remove-Item -LiteralPath '{folder}' -Recurse" in script  # the download is deleted afterwards


def test_setup_download_runs_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_curl", Reply(b"MZ setup"))
    folder = tmp_path / "PDFlex-aggiornamento"
    installer = update.download("https://github.com/x/PDFlex-Setup-1.5.exe", None, "1.5", folder)
    assert installer.read_bytes() == b"MZ setup"
    monkeypatch.setattr(update.sys, "platform", "test")
    command = _install_command(update.start_install(installer, pid=1, folder=folder).read_text(encoding="utf-8-sig"))
    assert "Start-Process -Wait" in command and "'/VERYSILENT'" in command and str(installer) in command
    assert f"/DIR=\"{update.INSTALLED.parent}\"" in command  # over the running copy, wherever it is


def test_update_button_downloads_then_waits_to_install(qapp, monkeypatch, tmp_path):
    from pdflex import app
    w = app.MainWindow()
    w._show_update("1.4", "https://github.com/x/releases/tag/v1.4", "")  # nothing to download: opens the page
    assert not w.b_update.isHidden() and w.b_update.text() == "Aggiorna\nv1.4"
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    started = []
    monkeypatch.setattr(w, "_download_update", lambda: started.append(1))
    w._show_update("1.4", "https://github.com/x/releases/tag/v1.4", "https://github.com/x/PDFlex-Setup-1.4.exe")
    assert started  # found at start: downloaded right away
    w._downloaded(str(tmp_path / "PDFlex-Setup.exe"), "")
    assert w.b_update.text() == "Installa\nv1.4" and w.update_files  # ready: installs only when pressed
    w._downloaded("", "no network")
    assert w.b_update.text() == "Aggiorna\nv1.4" and "riprovare" in w.b_update.toolTip()
    w.close()
