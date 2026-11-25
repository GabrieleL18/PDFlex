import os
import sys

from pdflex import i18n

if not os.environ.get("PDFLEX_LANG") and i18n.saved() is None:  # first start, and the installer did not ask
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    i18n.use(i18n.ask())
    i18n.save(i18n.LANG)

from pdflex.app import main  # noqa: E402  after the language: labels are made at import

main()
