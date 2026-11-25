# Installa PDFlex per l'utente corrente (non servono permessi di amministratore).
# Prima crea il programma con build.bat, poi: doppio clic su "Installa PDFlex.bat".
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "dist\PDFlex"
if (-not (Test-Path (Join-Path $src "PDFlex.exe"))) { throw "Non trovo dist\PDFlex\PDFlex.exe: esegui prima build.bat." }

$dest = Join-Path $env:LOCALAPPDATA "Programs\PDFlex"
$exe = Join-Path $dest "PDFlex.exe"
if (Get-Process PDFlex -ErrorAction SilentlyContinue) { throw "Chiudi PDFlex e riprova. / Close PDFlex and try again." }

# lingua: chiesta solo alla prima installazione (poi si cambia dal pulsante in basso a sinistra in PDFlex)
$langFile = Join-Path $env:LOCALAPPDATA "PDFlex\lingua.json"
if (-not (Test-Path $langFile)) {
    Write-Host ""
    Write-Host "  1 = Italiano"
    Write-Host "  2 = English"
    $pick = Read-Host "Lingua / Language [1]"
    $lang = if ($pick.Trim() -eq "2") { "en" } else { "it" }
    New-Item (Split-Path $langFile) -ItemType Directory -Force | Out-Null
    [IO.File]::WriteAllText($langFile, "{""lingua"": ""$lang""}")  # UTF-8 without BOM
}
$en = (Get-Content $langFile -Raw) -match '"en"'
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Copy-Item $src $dest -Recurse
Copy-Item (Join-Path $PSScriptRoot "uninstall.ps1") $dest

# menu Start
$lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\PDFlex.lnk"
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sc.TargetPath = $exe
$sc.WorkingDirectory = $dest
$sc.Description = "Moduli PDF compilabili e confronto documenti"
$sc.Save()

# "Apri con PDFlex" sui file .pdf (non cambia il programma predefinito)
$classes = "HKCU:\Software\Classes"
$prog = "$classes\PDFlex.pdf"
New-Item "$prog\shell\open\command" -Force | Out-Null
Set-ItemProperty $prog "(default)" "Documento PDF"
Set-ItemProperty $prog "FriendlyTypeName" "Documento PDF"
New-Item "$prog\DefaultIcon" -Force | Set-ItemProperty -Name "(default)" -Value "`"$exe`",0"
Set-ItemProperty "$prog\shell\open\command" "(default)" "`"$exe`" `"%1`""
New-Item "$classes\.pdf\OpenWithProgids" -Force | New-ItemProperty -Name "PDFlex.pdf" -Value "" -Force | Out-Null
$app = "$classes\Applications\PDFlex.exe"
New-Item "$app\shell\open\command" -Force | Out-Null
Set-ItemProperty $app "FriendlyAppName" "PDFlex"
Set-ItemProperty "$app\shell\open\command" "(default)" "`"$exe`" `"%1`""
New-Item "$app\SupportedTypes" -Force | New-ItemProperty -Name ".pdf" -Value "" -Force | Out-Null

# voce in Impostazioni > App, per disinstallare
$un = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFlex"
New-Item $un -Force | Out-Null
$size = [int]((Get-ChildItem $dest -Recurse | Measure-Object Length -Sum).Sum / 1KB)
@{ DisplayName = "PDFlex"; DisplayIcon = $exe; Publisher = "PDFlex"; InstallLocation = $dest; EstimatedSize = $size
   UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`""
   NoModify = 1; NoRepair = 1 }.GetEnumerator() | ForEach-Object { New-ItemProperty $un -Name $_.Key -Value $_.Value -Force | Out-Null }

if ($en) { Write-Host "PDFlex installed in $dest. You find it in the Start menu and in 'Open with' on PDFs." }
else { Write-Host "PDFlex installato in $dest. Lo trovi nel menu Start e in 'Apri con' sui PDF." }
