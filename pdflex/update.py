"""Updates from the GitHub releases: is there a newer one, download it, install it after PDFlex closes."""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from pdflex import __version__

RELEASES = "https://api.github.com/repos/GabrieleL18/PDFlex/releases/latest"
FOLDER = Path(tempfile.gettempdir()) / "PDFlex-aggiornamento"  # everything downloaded; gone after installing
# reopened after updating: where this copy runs (the setup installs over it), else install.ps1's place
INSTALLED = Path(sys.executable) if getattr(sys, "frozen", False) else \
    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "PDFlex" / "PDFlex.exe"
LOG = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFlex" / "aggiornamento.log"


def version_parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


CURL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"


def _curl(url: str, timeout: float, out: Path | None = None, progress=None) -> bytes:
    """url's content, or saved to `out` with progress(percent). curl.exe comes with Windows and uses its
    certificates: PDFlex ships no OpenSSL (~8 MB less). OSError if offline or not found."""
    cmd = [str(CURL), "-fL", "-A", "PDFlex", "--connect-timeout", str(timeout), url]
    cmd += ["-#", "-o", str(out)] if out else ["-s", "-m", str(timeout)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def report(line):
        percent = re.findall(rb"(\d+)[.,]\d%", line)
        if percent and progress:
            progress(int(percent[-1]))
    if out:
        rest = b""  # the progress bar, redrawn after each \r: "#####    42.0%"
        while chunk := p.stderr.read1(256):
            *lines, rest = (rest + chunk).split(b"\r")
            for line in lines:
                report(line)
        report(rest)
    data, _ = p.communicate()
    if p.returncode:
        raise OSError(f"curl: error {p.returncode}")
    return data


def newer_release(current: str = __version__) -> tuple[str, str, str] | None:
    """(version, release page, what to download or "") of the latest release when newer than this one;
    None if not, or offline. The installer (PDFlex-Setup-X.Y.exe) if the release has it, else the zip."""
    try:
        data = json.loads(_curl(RELEASES, 5))
        tag = data["tag_name"].lstrip("vV")
        if version_parts(tag) <= version_parts(current):
            return None
        assets = {a["name"].lower(): a["browser_download_url"] for a in data.get("assets", [])}
        pick = next((u for n, u in assets.items() if n.startswith("pdflex-setup") and n.endswith(".exe")), "") \
            or next((u for n, u in assets.items() if n.endswith(".zip")), "")
        return tag, data["html_url"], pick
    except (OSError, ValueError, KeyError, TypeError):
        return None


def download(url: str, progress=None, version: str = "", folder: Path = FOLDER) -> Path:
    """What installs the new version: the setup .exe, or install.ps1 from the unpacked zip.
    progress(percent) as it comes in. Already downloaded for this version (installing was put off): kept."""
    mark = folder / "versione.txt"
    setup, script = folder / "PDFlex-Setup.exe", folder / "files" / "install.ps1"
    if version and mark.exists() and mark.read_text() == version:
        for ready in (setup, script):
            if ready.exists():
                if progress:
                    progress(100)
                return ready
    shutil.rmtree(folder, ignore_errors=True)  # an older version, or what a failed attempt left behind
    folder.mkdir(parents=True)
    is_setup = url.lower().endswith(".exe")
    target = setup if is_setup else folder / "PDFlex.zip"
    _curl(url, 30, target, progress)
    if not is_setup:
        with zipfile.ZipFile(target) as z:
            z.extractall(folder / "files")
        target.unlink()
        if not script.exists():
            raise ValueError("install.ps1 is not in the downloaded zip")
    mark.write_text(version)
    return setup if is_setup else script


def _ps(text) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def start_install(installer: Path, message: str = "Aggiornamento di PDFlex in corso…", pid: int = os.getpid(),
                  folder: Path = FOLDER) -> Path:
    """A PowerShell that waits for PDFlex to close and installs, showing a small window with a moving bar
    meanwhile; then reopens PDFlex and deletes the download."""
    script = folder / "aggiorna.ps1"
    if installer.suffix.lower() == ".exe":  # Inno Setup, without its pages: same folder, same choices as before
        install = (f"Start-Process -Wait -FilePath {_ps(installer)} -ArgumentList '/VERYSILENT', "
                   f"'/SUPPRESSMSGBOXES', '/NORESTART', {_ps('/LOG=' + chr(34) + str(LOG) + chr(34))}")
    else:
        install = f"& {_ps(installer)} *> {_ps(LOG)}"
    encoded = base64.b64encode(install.encode("utf-16-le")).decode()  # no quoting troubles with any path
    script.write_text(f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'PDFlex'; $form.ClientSize = New-Object System.Drawing.Size(400, 90)
$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; $form.ControlBox = $false
$form.TopMost = $true; $form.ShowInTaskbar = $true
$label = New-Object System.Windows.Forms.Label
$label.Text = {_ps(message)}; $label.SetBounds(20, 18, 360, 22)
$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Style = 'Marquee'; $bar.MarqueeAnimationSpeed = 30; $bar.SetBounds(20, 48, 360, 18)
$form.Controls.Add($label); $form.Controls.Add($bar)
$form.Show(); [System.Windows.Forms.Application]::DoEvents()
Wait-Process -Id {pid} -Timeout 120 -ErrorAction SilentlyContinue
$p = Start-Process "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -WindowStyle Hidden -PassThru `
    -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', '{encoded}'
while (-not $p.HasExited) {{ [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 40 }}
$form.Close()
if (Test-Path {_ps(INSTALLED)}) {{ Start-Process {_ps(INSTALLED)} }}
Set-Location $env:TEMP
Remove-Item -LiteralPath {_ps(folder)} -Recurse -Force -ErrorAction SilentlyContinue
""", encoding="utf-8-sig")  # with a BOM: Windows PowerShell reads the text and paths as UTF-8
    if sys.platform == "win32":
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-STA", "-File", str(script)],
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
    return script
