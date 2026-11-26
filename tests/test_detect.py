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


def _modello_redditi(tmp_path):
    """Pieces of the Modello Redditi: what Rileva campi used to miss."""
    doc = pymupdf.open()
    p = doc.new_page()
    p.draw_rect(pymupdf.Rect(100, 100, 580, 400), color=None, fill=(0.72, 0.77, 0.84))  # band
    # amount box: column number in the corner, ",00" printed at the end
    p.draw_rect(pymupdf.Rect(200, 110, 272, 128), color=None, fill=(1, 1, 1))
    p.insert_text((200.5, 115.5), "3", fontsize=5)
    p.insert_text((261, 127), ",00", fontsize=7)
    # date split by separators, tiny labels inside
    p.insert_text((120, 150), "Data di nascita", fontsize=7)
    p.draw_rect(pymupdf.Rect(120, 152, 228, 176), color=None, fill=(1, 1, 1))
    for x, word in ((125, "giorno"), (160, "mese"), (195, "anno")):
        p.insert_text((x, 157), word, fontsize=5)
    for x in (156, 192):
        p.draw_line((x, 160), (x, 176), color=(0, 0, 0), width=0.6)
    # SÌ / NO boxes, the answer printed inside
    for x, word in ((300, "SI"), (330, "NO")):
        p.draw_rect(pymupdf.Rect(x, 200, x + 17, 217), color=(0, 0.26, 0.44), fill=(1, 1, 1))
        p.insert_text((x + 3, 212), word, fontsize=7)
    p.insert_text((120, 212), "RM51", fontsize=8)
    # partita IVA: 11 cells, ticks only between them
    p.insert_text((120, 245), "Partita IVA", fontsize=7)
    p.draw_rect(pymupdf.Rect(120, 250, 278.4, 268), color=None, fill=(1, 1, 1))
    for k in range(1, 11):
        p.draw_line((120 + 14.4 * k, 262), (120 + 14.4 * k, 268), color=(0, 0.26, 0.44))
    p.draw_line((120, 268), (278.4, 268), color=(0, 0.26, 0.44))
    # outside the band: "| COGNOME | NOME |" ticks rising from one line
    p.insert_text((60, 460), "COGNOME", fontsize=7)
    p.insert_text((204, 460), "NOME", fontsize=7)
    for x in (60, 204, 334):
        p.draw_line((x, 463), (x, 472), color=(0, 0.26, 0.44))
    p.draw_line((60, 472), (334, 472), color=(0, 0.26, 0.44))
    path = tmp_path / "redditi.pdf"
    doc.save(path)
    return open_pdf(path)


def test_modello_redditi_boxes(tmp_path):
    found = {f.name: f for f in detect_fields(_modello_redditi(tmp_path)[0], [])}
    amount = next(f for f in found.values() if 200 <= f.rect[0] < 210 and f.rect[1] < 130)
    assert amount.rect[0] > 202 and amount.rect[2] < 261 and amount.align == 2  # never over "3" or ",00"
    date = [found[f"data_di_nascita_{p}"] for p in ("giorno", "mese", "anno")]
    assert [f.comb for f in date] == [2, 2, 4] and all(f.rect[1] > 157 for f in date)  # under the tiny labels
    assert found["rm51_si"].kind == found["rm51_no"].kind == "checkbox"
    iva = next(f for f in found.values() if "iva" in f.name)
    assert iva.comb == 11 and round(iva.rect[0]) == 120 and round(iva.rect[2]) == 278
    cognome = next(f for f in found.values() if f.name.startswith("cognome"))
    nome = next(f for f in found.values() if f.name.startswith("nome"))
    assert round(cognome.rect[0]) == 60 and round(cognome.rect[2]) == 204 and round(nome.rect[2]) == 334


# paste into the next table row
from pdflex.detect import row_lines, table_step  # noqa: E402

H = [(50, 400, y) for y in (100, 120, 140, 160)]


def test_table_step_next_row():
    assert table_step(H, (60, 102, 200, 118), [], 800) == 20


def test_table_step_skips_taken_rows():
    assert table_step(H, (60, 102, 200, 118), [(60, 122, 200, 138)], 800) == 40


def test_table_step_none_at_last_row():
    assert table_step(H, (60, 142, 200, 158), [], 800) is None


def test_table_step_ignores_short_lines():
    assert table_step([(50, 80, y) for y in (100, 120, 140)], (60, 102, 200, 118), [], 800) is None


def test_table_step_block_of_two_rows():
    assert table_step(H + [(50, 400, 180)], (60, 102, 200, 138), [], 800) == 40


def test_row_lines_reads_cell_rectangles():
    doc = pymupdf.open()
    page = doc.new_page()
    for y in (100, 120):
        page.draw_rect((50, y, 400, y + 20))
    assert sorted({round(line[2]) for line in row_lines(page)}) == [100, 120, 140]


def test_table_step_rows_drawn_cell_by_cell():
    cells = [(x, x + 150, y) for y in (100, 120, 140) for x in (50, 200, 350)]
    assert table_step(cells, (60, 102, 490, 118), [], 800) == 20
