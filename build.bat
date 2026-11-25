@echo off
rem Crea dist\PDFlex\PDFlex.exe (richiede il venv: py -m venv .venv && .venv\Scripts\pip install -r requirements.txt)
rem Cartella invece di un solo .exe: parte subito (un .exe unico si scompatta a ogni avvio).
cd /d "%~dp0"
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python -c "from PySide6.QtGui import QGuiApplication; a=QGuiApplication([]); from pdflex.ui import write_ico; write_ico('pdflex.ico')" || exit /b 1
set QT_QPA_PLATFORM=
.venv\Scripts\pyinstaller --noconfirm --onedir --windowed --name PDFlex --icon pdflex.ico --paths . ^
  --exclude-module numpy --exclude-module pymupdf.layout --exclude-module tkinter ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtPdf pdflex\__main__.py || exit /b 1
rem pezzi non usati che PyInstaller copia comunque (modelli AI di PyMuPDF, OpenGL software, traduzioni di Qt)
set I=dist\PDFlex\_internal
rmdir /s /q "%I%\pymupdf\layout" "%I%\pymupdf\mupdf-devel" "%I%\numpy.libs" "%I%\PySide6\translations" 2>nul
del /q "%I%\PySide6\opengl32sw.dll" 2>nul
rem tastiera virtuale (si porta dietro Qt Quick/QML/OpenGL), anteprima PDF di Qt e touch TUIO: non usati
pushd "%I%\PySide6"
del /q Qt6VirtualKeyboard*.dll Qt6Quick*.dll Qt6Qml*.dll Qt6OpenGL.dll Qt6Pdf.dll Qt6Network.dll 2>nul
del /q plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll plugins\imageformats\qpdf.dll plugins\generic\qtuiotouchplugin.dll 2>nul
popd
echo.
echo Pronto: dist\PDFlex\PDFlex.exe  (per installarlo: install.ps1)
