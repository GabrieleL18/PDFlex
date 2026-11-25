@echo off
rem Crea i file da allegare a una Release di GitHub:
rem   PDFlex-Setup-VERSIONE.exe  l'installer da scaricare e aprire (e quello che usa l'aggiornamento automatico)
rem   PDFlex-VERSIONE.zip        per le copie 1.4, che si aggiornano dallo zip
rem Uso: release.bat 1.5   (serve Inno Setup 6: winget install JRSoftware.InnoSetup)
cd /d "%~dp0"
if "%~1"=="" (
  echo Uso: release.bat VERSIONE   ad esempio: release.bat 1.5
  exit /b 1
)
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo Non trovo Inno Setup 6: winget install JRSoftware.InnoSetup
  exit /b 1
)
call "%~dp0build.bat" || exit /b 1
"%ISCC%" /Q /DVersion=%~1 installer.iss || exit /b 1
set Z=PDFlex-%~1.zip
del /q "%Z%" 2>nul
rem install.ps1 cerca dist\PDFlex accanto a se': lo zip tiene la stessa struttura
"%SystemRoot%\System32\tar.exe" -a -c -f "%Z%" dist "Installa PDFlex.bat" install.ps1 uninstall.ps1 || exit /b 1
echo.
echo Pronti: PDFlex-Setup-%~1.exe e %Z%  (allegali entrambi alla Release su GitHub)
