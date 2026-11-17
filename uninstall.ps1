# Disinstalla PDFlex (installato con install.ps1). I tuoi PDF non vengono toccati.
$ErrorActionPreference = "Stop"
if (Get-Process PDFlex -ErrorAction SilentlyContinue) { throw "Chiudi PDFlex e riprova." }
$classes = "HKCU:\Software\Classes"
Remove-Item "$classes\PDFlex.pdf", "$classes\Applications\PDFlex.exe",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFlex" -Recurse -Force -ErrorAction SilentlyContinue
Remove-ItemProperty "$classes\.pdf\OpenWithProgids" -Name "PDFlex.pdf" -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\PDFlex.lnk") -ErrorAction SilentlyContinue
$dest = Join-Path $env:LOCALAPPDATA "Programs\PDFlex"
Set-Location $env:TEMP  # this script may live inside $dest
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "PDFlex disinstallato."
