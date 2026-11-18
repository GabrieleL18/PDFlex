import os

import pymupdf
import pytest

from pdflex import design


def _png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def _add_field_and_button(page):
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "nome", pymupdf.Rect(72, 110, 200, 130)
    w.field_value = "ciao"
    page.add_widget(w)
    b = pymupdf.Widget()
    b.field_type, b.field_name, b.rect = pymupdf.PDF_WIDGET_TYPE_BUTTON, "btn", pymupdf.Rect(220, 110, 300, 130)
    b.button_caption, b.script = "Svuota", "this.resetForm();"
    page.add_widget(b)


def _page():
    """Text, a line under it, a box with a line inside, a field and a button."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo vicino alla linea")
    page.draw_line((72, 104), (300, 104))
    page.draw_rect((60, 60, 320, 160), color=(0, 0, 1))
    page.draw_line((72, 140), (300, 140), color=(1, 0, 0), width=2, dashes="[3] 0")
    _add_field_and_button(page)
    return doc, page


def test_objects_found():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line((72, 104), (300, 104))
    page.draw_rect((60, 60, 320, 160), color=(0, 0, 1))
    page.draw_rect((72, 200, 300, 200.8), color=None, fill=(0, 0, 0))  # a "line" drawn as a thin filled box
    page.insert_text((72, 100), "Testo vicino alla linea")
    page.insert_image((400, 400, 450, 450), stream=_png())
    _add_field_and_button(page)
    objs, warnings = design.page_objects(page)
    assert sorted(o.kind for o in objs) == ["image", "line", "line", "rect", "text"] and warnings == []
    assert all(o.state == "edit" for o in objs)
    line = next(o for o in objs if o.kind == "line" and o.rect[1] > 199)
    assert line.data["p1"][1] == pytest.approx(200.4) and line.data["p2"][0] == pytest.approx(300)
    text = next(o for o in objs if o.kind == "text")
    assert text.data["spans"][0]["text"] == "Testo vicino alla linea"
    # the field's and the button's own text are no page text
    assert not any(o.kind == "text" and o.data["spans"][0]["text"] in ("ciao", "Svuota") for o in objs)


def test_text_drawn_as_shapes_is_grouped_and_warned():
    doc = pymupdf.open()
    page = doc.new_page()
    for row in (100, 130):
        for i in range(20):
            x = 72 + i * 6  # glyph-like: small filled polygons, 1-2 pt apart
            page.draw_polyline([(x, row), (x + 4, row), (x + 2, row - 7), (x, row)], color=None, fill=(0, 0, 0))
    objs, warnings = design.page_objects(page)
    assert all(o.kind == "shape" and o.state == "move" for o in objs) and len(objs) <= 4
    assert warnings == ["Testo disegnato come forme: si sposta, ma le parole non si possono cambiare (40 forme)."]


def test_rotated_page_locked():
    doc, page = _page()
    page.set_rotation(90)
    objs, warnings = design.page_objects(page)
    assert objs and all(o.state == "locked" for o in objs)
    assert warnings == ["Pagina ruotata: per modificarla, raddrizzala prima nella sezione Pagine."]


def test_clipped_shape_locked():
    doc = pymupdf.open()
    page = doc.new_page()
    # a box only half visible: a clip cuts it
    page.insert_htmlbox((0, 0, 1, 1), "")  # makes sure the page has a content stream
    xref = page.get_contents()[0]
    doc.update_stream(xref, b"q 50 50 100 100 re W n 0 0 1 RG 20 20 200 200 re S Q")
    objs, warnings = design.page_objects(page)
    assert [o.state for o in objs] == ["locked"] and objs[0].reason
    assert warnings == ["1 oggetti restano come sono (sfumature, ritagli, testo ruotato o forme che PDFlex non riesce a staccare dalla pagina)."]


# ---------------------------------------------------------------- Task 2: changes made in place

def _find(page, kind, y=None):
    for o in design.page_objects(page)[0]:
        if o.kind != kind:
            continue
        if y is None or (kind == "line" and abs(o.data["p1"][1] - y) < 0.8) or (kind != "line" and abs(o.rect[1] - y) < 0.8):
            return o
    return None


def _button(page):
    return next(w for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_BUTTON)


def _word_rect(page, word):
    return next(pymupdf.Rect(w[:4]) for w in page.get_text("words") if w[4] == word)


def _dark(page, rect) -> bool:
    pix = page.get_pixmap(clip=rect, dpi=144, annots=False)
    return min(pix.samples) < 80  # some pixel is (almost) black: the text shows


def _fields_intact(page):
    assert [w.field_name for w in page.widgets()] == ["nome", "btn"]
    assert _button(page).script == "this.resetForm();"


def test_move_line_keeps_everything_else():
    doc, page = _page()
    words = page.get_text("words")
    line = _find(page, "line", y=104)
    design.move(page, [line], 0, 30)
    assert _find(page, "line", y=134) and not _find(page, "line", y=104)
    assert [w[4] for w in page.get_text("words")] == [w[4] for w in words]
    assert _find(page, "rect") and _find(page, "line", y=140)
    _fields_intact(page)


def test_move_box_redraws_what_was_inside():
    doc, page = _page()
    box, inner = _find(page, "rect"), _find(page, "line", y=140)
    design.move(page, [box], 10, 0)
    moved = _find(page, "rect")
    assert moved.rect[0] == pytest.approx(box.rect[0] + 10, abs=0.6)
    again = _find(page, "line", y=140)
    for key in ("color", "width", "dashes"):
        assert again.data["drawing"][key] == inner.data["drawing"][key]
    _fields_intact(page)


def test_moved_background_stays_under_text():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect((60, 80, 320, 110), color=None, fill=(0.6, 0.6, 0.9))
    page.insert_text((72, 100), "Testo sopra lo sfondo")
    band = _find(page, "rect")
    design.move(page, [band], 0, 2)
    assert _find(page, "rect").rect[1] == pytest.approx(82, abs=0.6)
    assert _dark(page, _word_rect(page, "Testo"))


def test_resize_restyle_delete():
    doc, page = _page()
    design.set_rect(page, _find(page, "line", y=104), (72, 104, 200, 104))
    line = _find(page, "line", y=104)
    assert line.data["p2"][0] == pytest.approx(200, abs=0.6)
    design.restyle(page, line, color=(1, 0, 0), width=2)
    line = _find(page, "line", y=104)
    assert line.data["drawing"]["color"] == pytest.approx((1, 0, 0)) and line.data["drawing"]["width"] == 2
    design.set_rect(page, _find(page, "rect"), (50, 50, 340, 170))
    assert _find(page, "rect").rect == pytest.approx((50, 50, 340, 170), abs=0.6)
    design.delete(page, [_find(page, "line", y=104)])
    assert not _find(page, "line", y=104) and _find(page, "line", y=140)
    _fields_intact(page)


def test_locked_object_refused():
    doc, page = _page()
    line = _find(page, "line", y=104)
    line.state = "locked"
    with pytest.raises(design.EditError):
        design.move(page, [line], 5, 5)


def test_text_moved_with_its_own_font():
    doc, page = _page()
    text = _find(page, "text")
    design.move(page, [text], 0, 50)
    assert _word_rect(page, "Testo").y0 == pytest.approx(text.rect[1] + 50, abs=1.5)
    assert len([w for w in page.get_text("words") if w[4] == "Testo"]) == 1
    assert _find(page, "line", y=104)
    _fields_intact(page)


def test_image_moved_and_resized():
    doc, page = _page()
    page.insert_image((400, 400, 450, 450), stream=_png())
    design.set_rect(page, _find(page, "image"), (400, 300, 480, 380))
    assert _find(page, "image").rect == pytest.approx((400, 300, 480, 380), abs=0.6)
    assert len([o for o in design.page_objects(page)[0] if o.kind == "image"]) == 1


# ---------------------------------------------------------------- Task 3: new words and new components

def _subset_page(subset=True):
    """Text written with an embedded font; subset keeps only the letters it uses and, like many PDFs, no map
    from letters to glyphs."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo vicino alla linea", fontfile="C:/Windows/Fonts/arial.ttf", fontname="F0")
    page.insert_text((72, 112), "Riga sotto", fontsize=11)
    xref = page.get_fonts()[0][0]
    doc.xref_set_key(xref, "BaseFont", "/ArialMT")  # the name real PDFs give it; PyMuPDF writes "Arial Regular"
    if subset:
        doc.subset_fonts()
    return doc, doc[0]


def test_missing_letters_then_fallback():
    doc, page = _subset_page()
    text = _find(page, "text")
    assert "€" in design.missing_letters(page, text, "Totale €")
    with pytest.raises(design.EditError):
        design.set_text(page, text, "Totale €")
    design.set_text(page, text, "Totale €", fallback=True)
    assert "Totale €" in page.get_text()


def test_text_without_letter_map_moves_unchanged():
    doc, page = _subset_page()
    text = _find(page, "text", y=_find(page, "text").rect[1])
    words = [w[4] for w in page.get_text("words")]
    design.move(page, [text], 0, 50)
    assert sorted(w[4] for w in page.get_text("words")) == sorted(words)
    assert _word_rect(page, "Testo").y0 == pytest.approx(text.rect[1] + 50, abs=1.5) and _dark(page, _word_rect(page, "Testo"))
    with pytest.raises(design.EditError):  # its colour cannot change: the letters cannot be written again
        design.restyle(page, _find(page, "text", y=text.rect[1] + 50), color=(1, 0, 0))
    design.restyle(page, _find(page, "text", y=text.rect[1] + 50), fontsize=22)
    assert _word_rect(page, "Testo").height > 20 and "Riga" in [w[4] for w in page.get_text("words")]


def test_set_text_keeps_font_when_letters_exist():
    doc, page = _subset_page(subset=False)
    text = _find(page, "text")
    font = text.data["spans"][0]["font"].split("+")[-1]
    assert design.missing_letters(page, text, "Testo alla linea") == ""
    design.set_text(page, text, "Testo alla linea")
    span = _find(page, "text", y=text.rect[1]).data["spans"][0]
    assert span["text"].replace("\xa0", " ") == "Testo alla linea" and span["font"].split("+")[-1] == font


def test_add_components_on_top():
    doc, page = _page()
    design.add_rect(page, (50, 50, 200, 120), fill=(1, 1, 0))
    design.add_text(page, (60, 90), "Nuovo", fontsize=14)
    design.add_line(page, (50, 300), (250, 300), width=2)
    design.add_image(page, (300, 300, 340, 340), _png())
    kinds = [o.kind for o in design.page_objects(page)[0]]
    assert kinds.count("rect") >= 2 and "image" in kinds and "Nuovo" in page.get_text()
    assert _dark(page, _word_rect(page, "Nuovo"))  # on top of the yellow box, not under it
    _fields_intact(page)


def test_filled_and_stroked_box_is_one_object_and_moves(monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect((100, 100, 200, 150), color=(0, 0, 0), fill=(1, 1, 0), width=1.4)
    real = page.get_cdrawings

    def split(extended=False):  # as in the TFR PDF: extended mode gives the fill and the stroke apart
        if not extended:
            return real()
        (fs,) = real()
        return [{**fs, "type": "f", "color": None, "level": 0}, {**fs, "type": "s", "fill": None, "seqno": 99,
                                                                   "level": 0}]
    monkeypatch.setattr(page, "get_cdrawings", split)
    boxes = [o for o in design.page_objects(page)[0] if o.kind == "rect"]
    assert len(boxes) == 1 and boxes[0].data["drawing"]["type"] == "fs"
    monkeypatch.undo()
    design.move(page, boxes, 0, 20)
    moved = _find(page, "rect")
    assert moved.rect[1] == pytest.approx(120, abs=1) and moved.data["drawing"]["fill"] == pytest.approx((1, 1, 0))


def test_a_move_never_reads_the_page_drawings_the_slow_way(monkeypatch):
    # PyMuPDF's get_drawings turns every point into an object: on a busta paga (772 paths, 490 fields) each call
    # cost 0.4 s, and a move made 8 of them. The raw form (get_cdrawings) is read instead, and converted only for
    # the paths that become objects.
    doc, page = _page()
    calls = []
    real = pymupdf.Page.get_drawings
    monkeypatch.setattr(pymupdf.Page, "get_drawings", lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    objs, _ = design.page_objects(page)
    design.move(page, [o for o in objs if o.kind == "rect"], 10, 0)
    assert calls == []
    assert _find(page, "rect").rect[0] == pytest.approx(70, abs=0.6) and _find(page, "line", y=140)
    _fields_intact(page)


def _colour_at(page, point) -> tuple:
    pix = page.get_pixmap(clip=pymupdf.Rect(point, point) + (-0.5, -0.5, 0.5, 0.5), dpi=144, annots=False)
    return pix.pixel(pix.width // 2, pix.height // 2)


def test_moved_shape_keeps_its_place_among_the_drawings():
    # As in the busta paga: a white sheet drawn first, a band on it, a line drawn after the band. The band moved
    # used to go back under everything, the white sheet too: it vanished. It stays between the two instead.
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_rect((100, 100, 200, 150), color=None, fill=(0, 0, 1))
    page.draw_line((150, 160), (150, 220), color=(1, 0, 0), width=3)
    band = _find(page, "rect", y=100)
    design.move(page, [band], 0, 50)
    assert _colour_at(page, (120, 175)) == (0, 0, 255)  # the band shows at its new place
    assert _colour_at(page, (150, 175)) == (255, 0, 0)  # and the line drawn after it is still on top
    assert _colour_at(page, (120, 125)) == (255, 255, 255)  # nothing left where it was
    assert [d["fill"] or d["color"] for d in page.get_drawings()] == [(1, 1, 1), (0, 0, 1), (1, 0, 0)]


def test_restyle_and_delete_where_it_is_drawn():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_rect((100, 100, 200, 150), color=(0, 0, 0), fill=None, width=1)
    design.restyle(page, _find(page, "rect", y=100), fill=(0, 1, 0), width=4)
    box = _find(page, "rect", y=100)
    assert box.data["drawing"]["fill"] == pytest.approx((0, 1, 0)) and box.data["drawing"]["width"] == pytest.approx(4)
    assert _colour_at(page, (150, 125)) == (0, 255, 0)
    design.delete(page, [box])
    assert len(page.get_drawings()) == 1 and _colour_at(page, (150, 125)) == (255, 255, 255)


def test_shape_inside_a_form_still_moves():
    # paths inside a form XObject (here: a page shown on another) cannot be edited where they are: the old way
    src = pymupdf.open()
    src.new_page().draw_rect((100, 100, 200, 150), color=None, fill=(0, 0, 1))
    doc = pymupdf.open()
    page = doc.new_page()
    page.show_pdf_page(page.rect, src, 0)
    design.move(page, [_find(page, "rect", y=100)], 0, 50)
    assert _find(page, "rect", y=150) and _colour_at(page, (150, 175)) == (0, 0, 255)


def test_duplicate_draws_the_copy_just_above_the_original():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_rect((100, 100, 200, 150), color=None, fill=(0, 0, 1))
    page.draw_line((150, 130), (150, 220), color=(1, 0, 0), width=3)
    design.duplicate(page, [_find(page, "rect", y=100)], 10, 10)
    assert [d["fill"] or d["color"] for d in page.get_drawings()] == [(1, 1, 1), (0, 0, 1), (0, 0, 1), (1, 0, 0)]
    assert _find(page, "rect", y=110).rect == pytest.approx((110, 110, 210, 160), abs=0.6)
    assert _colour_at(page, (150, 155)) == (255, 0, 0)  # the line drawn after the box stays on top of the copy


def test_duplicate_text_image_and_shape_together():
    doc, page = _page()
    page.insert_image((400, 400, 450, 450), stream=_png())
    objs = design.page_objects(page)[0]
    picked = [o for o in objs if o.kind in ("text", "image") or (o.kind == "line" and abs(o.data["p1"][1] - 104) < 1)]
    design.duplicate(page, picked, 0, 200)
    assert [w[4] for w in page.get_text("words")].count("Testo") == 2
    assert len([o for o in design.page_objects(page)[0] if o.kind == "image"]) == 2
    assert _find(page, "line", y=304) and _find(page, "line", y=104)
    _fields_intact(page)


def _span(page, word):
    return next(s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]
                if word in s["text"])


def test_font_family_bold_and_italic():
    doc, page = _page()
    text = _find(page, "text")
    assert design.font_style(page, text) == ("Helvetica", False, False)
    design.restyle(page, text, fontname=design.font_spec("Times", bold=True, italic=True))
    assert design.font_style(page, _find(page, "text")) == ("Times", True, True)
    assert "Testo vicino alla linea" in page.get_text()
    _fields_intact(page)


@pytest.mark.skipif(not os.path.exists("C:/Windows/Fonts/arial.ttf"), reason="Windows fonts")
def test_windows_font_goes_in_small_and_the_text_moves_again():
    doc, page = _page()
    design.restyle(page, _find(page, "text"), fontname=design.font_spec("Arial"))
    assert design.font_style(page, _find(page, "text"))[0] == "Arial"
    assert len(doc.tobytes(garbage=3, deflate=True)) < 150_000  # only its letters, not the whole 1 MB font
    design.move(page, [_find(page, "text")], 0, 300)
    words = page.get_text("words")
    assert [round(w[3]) for w in words if w[4] == "Testo"] == [round(_word_rect(page, "Testo").y1)]
    assert _word_rect(page, "Testo").y0 > 350
