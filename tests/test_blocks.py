import pytest

from pdflex import blocks as bl


def B(**kw):
    return bl.Block((100, 100, 400, 220), [0.25] * 4, [0.25] * 4, **kw)


def test_cells_and_merge():
    b = bl.merge(B(), [(0, 1), (0, 2)])
    assert b.merges == [(0, 1, 0, 2)]
    cs = bl.cells(b)
    assert len(cs) == 15 and cs[1][:4] == (0, 1, 0, 2) and cs[1][4] == pytest.approx((175, 100, 325, 130))
    assert bl.merge(B(), [(0, 0), (1, 1)]) is None  # not a rectangle
    assert bl.split(b, 0, 1).merges == []


def test_set_count_spreads_evenly_and_trims():
    b = bl.set_count(B(texts={"3,3": "x", "0,0": "a"}), "cols", 5)
    assert b.cols == pytest.approx([0.2] * 5)  # a count typed in: all the same size
    b = bl.set_count(b, "cols", 2)
    assert b.cols == pytest.approx([0.5, 0.5]) and b.texts == {"0,0": "a"}  # text of a removed column goes


def test_set_count_keeps_the_header_size():
    b = bl.Block((0, 0, 300, 100), [0.25] * 4, [0.2, 0.8], header_row=True)
    assert bl.set_count(b, "rows", 5).rows == pytest.approx([0.2] * 5)
    assert bl.set_count(b, "rows", 3).rows == pytest.approx([0.2, 0.4, 0.4])


def test_set_count_clips_merges():
    b = bl.merge(B(), [(0, 1), (0, 2), (0, 3)])
    assert bl.set_count(b, "cols", 3).merges == [(0, 1, 0, 2)]
    assert bl.set_count(b, "cols", 2).merges == []  # one cell left: no merge


def test_move_divider_and_min_size():
    b = bl.move_divider(B(), "cols", 0, 130)  # 100 + 30
    assert b.cols[0] == pytest.approx(0.1) and b.cols[1] == pytest.approx(0.4)
    b = bl.move_divider(B(), "cols", 0, 90)  # past the edge: cell kept >= 4 pt
    assert b.cols[0] * 300 == pytest.approx(4) and sum(b.cols) == pytest.approx(1)


def test_cell_at_merged_and_json_roundtrip():
    b = bl.merge(B(texts={"0,1": "TOTALE"}), [(0, 1), (0, 2)])
    assert bl.cell_at(b, 300, 110) == (0, 1) and bl.cell_at(b, 50, 50) is None
    assert bl.from_json(bl.to_json(b)) == b


def test_scaled_keeps_fractions():
    b = bl.scaled(B(), (0, 0, 600, 240))
    assert b.rect == (0, 0, 600, 240) and b.cols == [0.25] * 4


def test_presets():
    r = (0, 0, 300, 150)
    assert bl.preset("rettangolo", r) is None
    assert [p for p, _ in bl.PRESETS] == ["rettangolo", "riquadro", "tabella", "griglia", "sezione", "totali"]
    t = bl.preset("tabella", r)
    assert t.header_row and len(t.cols) == 4 and len(t.rows) == 6  # header + 5
    assert t.rows[0] * 150 == pytest.approx(18) and sum(t.rows) == pytest.approx(1)
    g = bl.preset("griglia", r)
    assert g.header_row and g.header_col and len(g.cols) == 9 and len(g.rows) == 5
    assert g.cols[0] * 300 == pytest.approx(70)
    assert bl.preset("riquadro", r).texts == {"0,0": "Titolo"} and bl.preset("sezione", r).border == ""


import dataclasses  # noqa: E402
import pathlib  # noqa: E402

import pymupdf  # noqa: E402

from pdflex import design  # noqa: E402


def _page():
    doc = pymupdf.open()
    return doc, doc.new_page()


def test_draw_table_shapes_and_text():
    doc, page = _page()
    b = dataclasses.replace(bl.preset("tabella", (100, 100, 400, 250)), texts={"0,0": "CODICE"})
    bl.draw(page, b)
    objs = design.page_objects(page)[0]
    assert "CODICE" in page.get_text()
    assert any(o.kind in ("rect", "shape") and pymupdf.Rect(o.rect).contains(pymupdf.Rect(101, 101, 399, 249))
               for o in objs)
    lines = [o for o in objs if o.kind == "line"]
    assert len(lines) == 3 + 5  # 3 inner column lines, 5 inner row lines
    own = bl.own_texts(objs, b)
    assert own and all(o.kind == "text" for o in own)


def test_merged_cell_has_no_line_inside():
    doc, page = _page()
    b = bl.merge(bl.preset("tabella", (100, 100, 400, 250)), [(0, 1), (0, 2)])
    bl.draw(page, b)
    x = 100 + 300 * 0.5  # the line between columns 1 and 2
    assert not any(o.kind == "line" and abs(o.rect[0] - x) < 1 and o.rect[1] < 117
                   for o in design.page_objects(page)[0])


def test_block_is_drawn_under_existing_text():
    doc, page = _page()
    page.insert_text((110, 120), "sopra")
    bl.draw(page, bl.preset("riquadro", (100, 100, 300, 200)))
    pix = page.get_pixmap(clip=(110, 112, 140, 121), dpi=144)
    assert any(pix.pixel(x, y)[0] < 100 for x in range(pix.width) for y in range(pix.height))  # dark letters show


def test_block_visible_over_page_background():
    doc, page = _page()
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))  # a white sheet under everything
    bl.draw(page, bl.preset("riquadro", (100, 100, 300, 200)))
    pix = page.get_pixmap(clip=(150, 150, 151, 151))
    assert pix.pixel(0, 0) != (255, 255, 255)  # the pale fill shows


def test_own_texts_leaves_other_text_alone():
    doc, page = _page()
    b = dataclasses.replace(bl.preset("riquadro", (100, 100, 300, 200)), texts={"0,0": "Presenze"})
    page.insert_text((120, 180), "altro")
    bl.draw(page, b)
    own = bl.own_texts(design.page_objects(page)[0], b)
    assert [design_words(o) for o in own] == ["Presenze"]


def design_words(o):
    return "".join(s["text"] for s in o.data["spans"]).strip()


def test_preview_pixmap(qapp):
    pm = bl.preview(bl.preset("griglia", (0, 0, 300, 150)), 96, 64)
    assert not pm.isNull() and pm.width() <= 96 and pm.height() <= 64


def _span_fonts(page):
    return {s["font"] for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]}


def test_block_text_in_another_font():
    doc, page = _page()
    b = dataclasses.replace(bl.preset("riquadro", (100, 100, 300, 200)), font="Times")
    bl.draw(page, b)
    assert any("Times" in f or "NimbusRoman" in f for f in _span_fonts(page))  # MuPDF's built-in Times
    assert [design_words(o) for o in bl.own_texts(design.page_objects(page)[0], b)] == ["Titolo"]
    assert bl.from_json({k: v for k, v in bl.to_json(b).items() if k != "font"}).font == "Helvetica"  # older notes


@pytest.mark.skipif("Georgia" not in design.FONTS, reason="a Windows font")
def test_block_text_in_a_windows_font():
    doc, page = _page()
    b = dataclasses.replace(bl.preset("riquadro", (100, 100, 300, 200)), font="Georgia")
    bl.draw(page, b)
    assert any("Georgia" in f for f in _span_fonts(page))
    assert [design_words(o) for o in bl.own_texts(design.page_objects(page)[0], b)] == ["Titolo"]


@pytest.mark.skipif(not (pathlib.Path("C:/Windows/Fonts/comic.ttf")).exists(), reason="Windows fonts")
def test_more_windows_fonts():
    assert {"Candara", "Comic Sans MS", "Palatino Linotype", "Century Gothic", "Book Antiqua"} <= set(design.FONTS)
