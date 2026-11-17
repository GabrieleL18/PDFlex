import pymupdf

from pdflex import open_pdf
from pdflex.detect import detect_fields
from pdflex.editor import Field


def _form(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Nome: ____________________")
    p.insert_text((72, 150), "Indirizzo")
    p.draw_rect(pymupdf.Rect(160, 138, 400, 156), color=(0, 0, 0))  # empty box
    p.insert_text((72, 200), "Accetto i termini")
    p.draw_rect(pymupdf.Rect(180, 190, 191, 201), color=(0, 0, 0))  # small square
    p.insert_text((72, 250), "Firma")
    p.draw_line((130, 252), (330, 252), color=(0, 0, 0))  # signature line
    p.insert_text((72, 300), "Data di nascita")
    p.draw_line((170, 302), (300, 302), color=(0, 0, 0))
    path = tmp_path / "form.pdf"
    doc.save(path)
    return open_pdf(path)


def test_detects_common_patterns(tmp_path):
    doc = _form(tmp_path)
    found = {f.name: f for f in detect_fields(doc[0], [])}
    assert found["nome"].kind == "text"
    assert found["indirizzo"].kind == "text"
    assert found["accetto_i_termini"].kind == "checkbox"
    assert found["firma"].kind == "signature"
    assert found["data_di_nascita"].kind == "date"
    n = found["nome"].rect
    assert n[0] > 100 and n[3] <= 105  # sits on the underscores, after the label


def test_skips_areas_already_covered(tmp_path):
    doc = _form(tmp_path)
    first = detect_fields(doc[0], [])
    again = detect_fields(doc[0], first)
    assert again == []
    existing = [Field("text", 0, first[0].rect, "gia")]
    assert len(detect_fields(doc[0], existing)) == len(first) - 1


def test_blank_page_finds_nothing(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    assert detect_fields(doc[0], []) == []
