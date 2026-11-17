@echo off
rem Crea dist\PDFlex.exe (richiede il venv: py -m venv .venv && .venv\Scripts\pip install -r requirements.txt)
cd /d "%~dp0"
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python -c "from PySide6.QtGui import QGuiApplication; a=QGuiApplication([]); from pdflex.ui import write_ico; write_ico('pdflex.ico')" || exit /b 1
set QT_QPA_PLATFORM=
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name PDFlex --icon pdflex.ico --paths . pdflex\__main__.py || exit /b 1
echo.
echo Pronto: dist\PDFlex.exe
