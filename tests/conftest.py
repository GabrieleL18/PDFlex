import os
import itertools

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest

_n = itertools.count()


@pytest.fixture
def make_pdf():
    def make(tmp_path, pages, rotate=0):
        doc = pymupdf.open()
        for text in pages:
            page = doc.new_page()
            y = 72
            for line in text.split("\n"):
                page.insert_text((72, y), line)
                y += 16
            page.set_rotation(rotate)
        path = tmp_path / f"doc{next(_n)}.pdf"
        doc.save(path)
        return path
    return make


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
