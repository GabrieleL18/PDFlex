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
