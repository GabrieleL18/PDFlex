@echo off
rem Crea PDFlex-VERSIONE.zip da allegare a una Release di GitHub.
rem Uso: release.bat 1.0
rem Chi lo scarica: estrae lo zip e fa doppio clic su "Installa PDFlex.bat".
cd /d "%~dp0"
if "%~1"=="" (
  echo Uso: release.bat VERSIONE   ad esempio: release.bat 1.0
  exit /b 1
)
call "%~dp0build.bat" || exit /b 1
set Z=PDFlex-%~1.zip
del /q "%Z%" 2>nul
rem install.ps1 cerca dist\PDFlex accanto a se': lo zip tiene la stessa struttura
"%SystemRoot%\System32\tar.exe" -a -c -f "%Z%" dist "Installa PDFlex.bat" install.ps1 uninstall.ps1 || exit /b 1
echo.
echo Pronto: %Z%  (allegalo a una nuova Release su GitHub)
