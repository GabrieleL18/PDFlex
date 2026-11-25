@echo off
rem Crea dist\PDFlex\PDFlex.exe (richiede il venv: py -m venv .venv && .venv\Scripts\pip install -r requirements.txt)
rem Cartella invece di un solo .exe: parte subito (un .exe unico si scompatta a ogni avvio).
cd /d "%~dp0"
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python -c "from PySide6.QtGui import QGuiApplication; a=QGuiApplication([]); from pdflex.ui import write_ico, write_wizard_bmp; write_ico('pdflex.ico'); write_wizard_bmp('wizard-small.bmp')" || exit /b 1
set QT_QPA_PLATFORM=
.venv\Scripts\pyinstaller --noconfirm --onedir --windowed --name PDFlex --icon pdflex.ico --paths . ^
  --exclude-module numpy --exclude-module pymupdf.layout --exclude-module tkinter ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtPdf --exclude-module ssl --exclude-module _ssl ^
  --exclude-module hashlib --exclude-module _hashlib pdflex\__main__.py || exit /b 1
rem ssl/hashlib: gli aggiornamenti si scaricano con curl.exe di Windows, niente OpenSSL (~8 MB)
rem pezzi non usati che PyInstaller copia comunque (modelli AI di PyMuPDF, OpenGL software, traduzioni di Qt)
set I=dist\PDFlex\_internal
rmdir /s /q "%I%\pymupdf\layout" "%I%\pymupdf\mupdf-devel" "%I%\numpy.libs" "%I%\PySide6\translations" 2>nul
del /q "%I%\PySide6\opengl32sw.dll" 2>nul
rem tastiera virtuale (si porta dietro Qt Quick/QML/OpenGL), anteprima PDF di Qt e touch TUIO: non usati
pushd "%I%\PySide6"
del /q Qt6VirtualKeyboard*.dll Qt6Quick*.dll Qt6Qml*.dll Qt6OpenGL.dll Qt6Pdf.dll Qt6Network.dll 2>nul
del /q plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll plugins\imageformats\qpdf.dll plugins\generic\qtuiotouchplugin.dll 2>nul
rem immagini: servono solo PNG (dentro Qt), JPG, SVG e ICO; finestre: solo qwindows; stile: Fusion (dentro Qt)
pushd plugins
del /q imageformats\qtiff.dll imageformats\qwebp.dll imageformats\qicns.dll imageformats\qtga.dll imageformats\qwbmp.dll imageformats\qgif.dll 2>nul
del /q platforms\qdirect2d.dll platforms\qminimal.dll platforms\qoffscreen.dll styles\qmodernwindowsstyle.dll 2>nul
popd
popd
echo.
echo Pronto: dist\PDFlex\PDFlex.exe  (per installarlo: install.ps1)
