"""Updates from the GitHub releases: is there a newer one, download it, install it after PDFlex closes."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from pdflex import __version__

RELEASES = "https://api.github.com/repos/GabrieleL18/PDFlex/releases/latest"
FOLDER = Path(tempfile.gettempdir()) / "PDFlex-aggiornamento"  # everything downloaded; gone after installing
INSTALLED = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "PDFlex" / "PDFlex.exe"  # install.ps1
LOG = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFlex" / "aggiornamento.log"


def version_parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _get(url: str, timeout: float):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "PDFlex"}), timeout=timeout)


def newer_release(current: str = __version__) -> tuple[str, str, str] | None:
    """(version, release page, zip to download or "") of the latest release when newer than this one;
    None if not, or offline."""
    try:
        with _get(RELEASES, 5) as r:
            data = json.load(r)
        tag = data["tag_name"].lstrip("vV")
        if version_parts(tag) <= version_parts(current):
            return None
        zips = [a["browser_download_url"] for a in data.get("assets", []) if a["name"].lower().endswith(".zip")]
        return tag, data["html_url"], zips[0] if zips else ""
    except (OSError, ValueError, KeyError, TypeError):
        return None


def download(url: str, progress=None, folder: Path = FOLDER) -> Path:
    """The release zip, unpacked: the folder with install.ps1. progress(percent) as it comes in."""
    shutil.rmtree(folder, ignore_errors=True)  # what a failed attempt left behind
    folder.mkdir(parents=True)
    zip_path = folder / "PDFlex.zip"
    with _get(url, 30) as r, open(zip_path, "wb") as out:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    files = folder / "files"
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(files)
    zip_path.unlink()
    if not (files / "install.ps1").exists():
        raise ValueError("install.ps1 is not in the downloaded zip")
    return files


def _ps(text) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def start_install(files: Path, pid: int = os.getpid(), folder: Path = FOLDER) -> Path:
    """A hidden PowerShell that waits for PDFlex to close, installs, reopens it and deletes the download."""
    script = folder / "aggiorna.ps1"
    script.write_text(f"""
Wait-Process -Id {pid} -Timeout 120 -ErrorAction SilentlyContinue
& "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File {_ps(files / "install.ps1")} *> {_ps(LOG)}
if (Test-Path {_ps(INSTALLED)}) {{ Start-Process {_ps(INSTALLED)} }}
Set-Location $env:TEMP
Remove-Item -LiteralPath {_ps(folder)} -Recurse -Force -ErrorAction SilentlyContinue
""", encoding="utf-8-sig")  # with a BOM: Windows PowerShell reads the paths as UTF-8
    if sys.platform == "win32":
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-File", str(script)],
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
    return script
