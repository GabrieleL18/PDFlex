import pymupdf

from pdflex import open_pdf
from pdflex.detect import detect_fields
from pdflex.fields import Field


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


def _tax_form(tmp_path):
    """Like the Agenzia delle Entrate forms: white boxes on a tinted band, rows split by lines."""
    doc = pymupdf.open()
    p = doc.new_page()
    p.draw_rect(pymupdf.Rect(100, 100, 560, 200), color=None, fill=(0.93, 0.89, 0.83))  # band
    for i, (code, label) in enumerate([("A01", "Dipendenti a tempo pieno"), ("A02", "Altro personale"),
                                       ("A03", "Apprendisti")]):
        y = 120 + 12 * i
        p.insert_text((105, y + 10), code, fontsize=8)
        p.insert_text((130, y + 10), label, fontsize=8)
        p.draw_line((105, y + 12), (555, y + 12), color=(0.67, 0.5, 0.15))  # row separator
    p.draw_rect(pymupdf.Rect(470, 120, 515, 156), color=None, fill=(1, 1, 1))  # one box over 3 rows
    p.insert_text((130, 170), "Barrare la casella", fontsize=8)
    p.draw_rect(pymupdf.Rect(470, 160, 484, 172), color=None, fill=(1, 1, 1))  # square: checkbox
    p.draw_rect(pymupdf.Rect(300, 180, 314, 192), color=None, fill=(1, 1, 1))  # box with a printed "1"
    p.insert_text((305, 189), "1", fontsize=8)
    p.insert_text((336, 60), "CODICE FISCALE", fontsize=7)
    for k in range(17):  # 16 cells marked by ticks
        x = 336 + 14.4 * k
        p.draw_line((x, 65), (x, 83), color=(0.67, 0.5, 0.15))
    p.draw_line((336, 83), (566.4, 83), color=(0.67, 0.5, 0.15))
    path = tmp_path / "isa.pdf"
    doc.save(path)
    return open_pdf(path)


def test_tax_form_white_boxes_rows_and_comb(tmp_path):
    found = {f.name: f for f in detect_fields(_tax_form(tmp_path)[0], [])}
    assert {"a01", "a02", "a03", "barrare_la_casella", "codice_fiscale"} <= set(found), found.keys()
    assert [round(found[n].rect[1]) for n in ("a01", "a02", "a03")] == [121, 133, 145]  # 1 pt inside the box
    assert found["barrare_la_casella"].kind == "checkbox"
    cf = found["codice_fiscale"]
    assert cf.comb == 16 and round(cf.rect[0]) == 336 and round(cf.rect[2]) == 566
    # the row lines have text right above them: separators, not fields; the box with "1" is a label
    assert len(found) == 5, sorted(found)


def test_comb_field_round_trip(tmp_path, make_pdf):
    from pdflex.fields import load_fields, save_with_fields
    out = tmp_path / "cf.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), [Field("text", 0, (10, 10, 240, 28), "cf", comb=16)], out)
    w = next(open_pdf(out)[0].widgets())
    assert w.text_maxlen == 16 and w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB
    f = load_fields(open_pdf(out))[0]
    assert f.comb == 16
