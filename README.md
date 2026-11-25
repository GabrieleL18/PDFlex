# PDFlex

A Windows app to make fillable PDF forms, compare two versions of a document and fill a form in bulk from Excel. In English and Italian (it follows the Windows language; change it with the globe button).

![Forms: fields drawn on the page, with their properties on the right](docs/images/forms.png)

## What it does

- **Forms**: draw fields (text, date, check boxes, choices, menus, signature) or press *Detect fields* to make them from `____` lines and empty boxes. Also:
  - formats (number, amount, percentage with the decimals you choose; date and time; postcode, tax code, phone, email), automatic sums, font, size, colour and alignment of the text;
  - *Save form*: the PDF with the fields as you see them, to reopen in PDFlex or to have someone fill in;
  - *Export for software*: a copy with empty fields, no borders and no buttons, for a program that fills them in by itself;
  - align several fields (Ctrl+click to select them), guides while dragging and a 5-point grid;
  - copy and paste fields, saved blocks of fields to reuse in other forms, fields imported from another PDF;
  - drag-and-drop Tab order and undo/redo (Ctrl+Z / Ctrl+Y);
  - preview with sample data;
  - *Fill in*: fill the fields by hand, with the filled form beside them, and save a copy.
- **Compare**: shows the differences side by side, scrolling together. You can:
  - ignore upper/lower case and punctuation;
  - filter by kind of change;
  - export to PDF (with the list of changes at the start) or to Excel.

  Scanned documents can be compared only if [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) is installed.

  ![Compare: the two versions side by side, with the list of changes](docs/images/compare.png)
- **Editor**: tidies up the PDF's graphics before making it a form. Pick up and move the page's lines, rectangles, shapes, images and texts, change their size, colours and words, or add text, lines, rectangles and images. Fields and buttons stay hidden and untouched. A yellow strip warns about what cannot be edited (text drawn as shapes, gradients, clipping), and an edit that would touch anything else is undone. *Save PDF* saves it as a new file (`<name>_modificato.pdf`); *Open in Forms* hands it to Forms to set up the fields.

  ![Editor: the objects on the page, listed on the left](docs/images/editor.png)
- **Batch**: a PDF form + an Excel/CSV file gives one filled PDF per row (columns go to the fields with the same name), or a single PDF with all the forms, ready to print.
- **Pages**: merge PDFs, reorder, rotate, delete, extract and split pages. In *Options* you can add page numbers, a header and a watermark and make the file smaller.

If PDFlex closes unexpectedly, the next time it opens it offers to pick up where you left off. A new version is downloaded by itself; it installs when you press the button, and PDFlex reopens.

## Install

Download `PDFlex-Setup-<version>.exe` from [Releases](https://github.com/GabrieleL18/PDFlex/releases) and run it. It installs for your user (no administrator rights), adds PDFlex to the Start menu and to "Open with" for PDF files. Uninstall it from Settings > Apps.

## Build from source

1. `py -m venv .venv` and `.venv\Scripts\pip install -r requirements.txt`
2. `build.bat` makes `dist\PDFlex\PDFlex.exe`; `release.bat <version>` also makes the installer (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)).

Run from source: `.venv\Scripts\python -m pdflex [file.pdf]`. Tests: `.venv\Scripts\python -m pytest -q`

License: PDFlex uses PyMuPDF (AGPL). Fine for personal/internal use; to distribute or sell it you must publish the source code or buy Artifex's commercial license.
