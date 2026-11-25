import pymupdf
import pytest

from pdflex import design, open_pdf, ui
from pdflex.designer import DesignTab


def _pdf(tmp_path, shapes_as_text=False):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo vicino alla linea")
    page.draw_line((72, 104), (300, 104))
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "nome", pymupdf.Rect(72, 110, 200, 130)
    page.add_widget(w)
    b = pymupdf.Widget()
    b.field_type, b.field_name, b.rect = pymupdf.PDF_WIDGET_TYPE_BUTTON, "btn", pymupdf.Rect(220, 110, 300, 130)
    b.button_caption = "Svuota"
    page.add_widget(b)
    if shapes_as_text:
        for i in range(20):
            x = 72 + i * 6
            page.draw_polyline([(x, 300), (x + 4, 300), (x + 2, 293), (x, 300)], color=None, fill=(0, 0, 0))
    path = tmp_path / "in.pdf"
    doc.save(path)
    return str(path)


def _line_y(tab):
    return round(next(o for o in tab.objects if o.kind == "line").data["p1"][1])


def _tab(tmp_path, **kw):
    tab = DesignTab()
    tab.open(_pdf(tmp_path, **kw))
    return tab


def test_open_shows_objects_not_fields(qapp, tmp_path):
    tab = _tab(tmp_path)
    assert {o.kind for o in tab.objects} == {"line", "text"} and tab.banner.isHidden()
    assert len(tab.items) == 2  # one box per object, none for the field and the button


def test_move_undo_redo_and_failed_edit_rolls_back(qapp, tmp_path, monkeypatch):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    tab.move_selected(0, 30)
    assert tab.dirty and _line_y(tab) == 134
    assert [o.kind for o in tab.selection] == ["line"]  # still selected after the change: arrows keep working
    tab.undo()
    assert _line_y(tab) == 104
    tab.redo()
    assert _line_y(tab) == 134
    tab.select([o for o in tab.objects if o.kind == "line"])
    snapshot = tab.doc.tobytes()
    monkeypatch.setattr(design, "move", lambda *a: (_ for _ in ()).throw(design.EditError("no")))
    warned = []
    monkeypatch.setattr(ui, "warn", lambda parent, text: warned.append(text))
    tab.move_selected(5, 5)
    def content(doc):  # tobytes() of a reopened copy differs in bookkeeping bytes: compare what the page holds
        return [d["rect"] for d in doc[0].get_drawings()], doc[0].get_text("words"), len(list(doc[0].widgets()))
    assert warned == ["no"] and content(tab.doc) == content(pymupdf.open("pdf", snapshot)) and _line_y(tab) == 134


def test_save_keeps_fields_and_opens_in_forms(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    tab.delete_selected()
    got = []
    tab.open_in_forms.connect(got.append)
    out = str(tmp_path / "in_modificato.pdf")
    tab.save(out)
    assert not tab.dirty
    assert [w.field_name for w in open_pdf(out)[0].widgets()] == ["nome", "btn"]
    tab.b_forms.click()
    assert got == [out]


def test_banner_for_text_as_shapes(qapp, tmp_path):
    tab = _tab(tmp_path, shapes_as_text=True)
    assert not tab.banner.isHidden() and "Testo disegnato come forme" in tab.banner.text()


def test_draw_components(qapp, tmp_path, monkeypatch):
    tab = _tab(tmp_path)
    tab.set_mode("rect")
    tab.drawn((100, 400, 200, 450))
    tab.set_mode("line")
    tab.drawn((100, 500, 300, 506), straight=True)  # Shift: stays horizontal
    monkeypatch.setattr(ui, "ask_text", lambda *a, **k: "Nuovo testo")
    tab.set_mode("text")
    tab.drawn((100, 600, 100, 600))
    kinds = sorted(o.kind for o in tab.objects)
    assert kinds == ["line", "line", "rect", "text", "text"]
    new_line = next(o for o in tab.objects if o.kind == "line" and o.data["p1"][1] > 400)
    assert new_line.data["p1"][1] == pytest.approx(new_line.data["p2"][1])
    assert "Nuovo testo" in tab.doc[0].get_text() and tab.mode == "select"


def test_text_change_asks_for_helvetica_when_letters_are_missing(qapp, tmp_path, monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo", fontfile="C:/Windows/Fonts/arial.ttf", fontname="F0")
    doc.xref_set_key(page.get_fonts()[0][0], "BaseFont", "/ArialMT")
    doc.subset_fonts()  # like most PDFs: no map from letters to glyphs
    path = tmp_path / "sub.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    tab.select([o for o in tab.objects if o.kind == "text"])
    asked = []
    monkeypatch.setattr(ui, "message", lambda parent, text, *a, **k: asked.append(text) or 1)  # Annulla
    tab.set_text("Totale €")
    assert asked and "Uso Helvetica" in asked[0] and "Totale" not in tab.doc[0].get_text()
    monkeypatch.setattr(ui, "message", lambda *a, **k: 0)  # Usa Helvetica
    tab.select([o for o in tab.objects if o.kind == "text"])
    tab.set_text("Totale €")
    assert "Totale €" in tab.doc[0].get_text()


def test_undo_goes_back_to_the_page_it_changed(qapp, tmp_path):
    doc = pymupdf.open()
    for y in (100, 200):
        doc.new_page().draw_line((72, y), (300, y))
    path = tmp_path / "two.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    tab.select(tab.objects)
    tab.move_selected(0, 30)
    tab.go_page(1)
    tab.undo()
    assert tab.page_no == 0 and _line_y(tab) == 100
    tab.go_page(1)
    tab.redo()
    assert tab.page_no == 0 and _line_y(tab) == 130


def test_drag_on_a_page_size_background_draws_the_selection_box(qapp, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(page.rect + (10, 10, -10, -10), color=None, fill=(0.9, 0.9, 1))  # the background
    page.draw_line((100, 300), (200, 300))
    path = tmp_path / "bg.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.resize(1300, 900)
    tab.show()
    tab.open(str(path))
    qapp.processEvents()
    v, z = tab.view, tab.zoom
    start = v.mapFromScene(80 * z, 280 * z)
    end = v.mapFromScene(220 * z, 320 * z)
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(v.viewport(), QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    QTest.mouseMove(v.viewport(), end)
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    assert [o.kind for o in tab.selection] == ["line"]
    assert next(o for o in tab.objects if o.kind == "rect").rect[1] == pytest.approx(10)  # not moved


def test_arrow_presses_make_one_change(qapp, tmp_path):
    # each change of a big page takes a moment: held down, the arrow would queue them up. The selection moves on
    # screen at once, and the page changes once, when the presses stop.
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    item = next(i for i in tab.items if i.isSelected())
    y = item.pos().y()
    for _ in range(3):
        QTest.keyClick(tab.view, Qt.Key_Down)
    assert _line_y(tab) == 104 and item.pos().y() - y == pytest.approx(3 * tab.zoom)  # shown, not made yet
    QTest.qWait(600)
    assert _line_y(tab) == 107 and len(tab._undo) == 1
    tab.undo()
    assert _line_y(tab) == 104


def _drag(v, start, end):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(v.viewport(), QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    QTest.mouseMove(v.viewport(), end)
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, end)


def test_a_box_grows_from_every_corner_and_side(qapp, tmp_path):
    doc = pymupdf.open()
    doc.new_page().draw_rect((100, 100, 200, 160), color=(0, 0, 1), width=1)
    path = tmp_path / "box.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.resize(1300, 900)
    tab.show()
    tab.open(str(path))
    qapp.processEvents()
    box = lambda: next(o for o in tab.objects if o.kind == "rect")
    v, z = tab.view, tab.zoom
    at = lambda x, y: v.mapFromScene(x * z, y * z)
    tab.select([box()])
    _drag(v, at(100, 100), at(80, 90))  # top-left corner
    assert box().rect == pytest.approx((80, 90, 200, 160), abs=1)
    tab.select([box()])
    _drag(v, at(80, 125), at(60, 140))  # middle of the left side: only that side moves
    assert box().rect == pytest.approx((60, 90, 200, 160), abs=1)
    tab.select([box()])
    _drag(v, at(130, 160), at(150, 180))  # middle of the bottom side
    assert box().rect == pytest.approx((60, 90, 200, 180), abs=1)


def test_object_list_and_page_select_together(qapp, tmp_path):
    tab = _tab(tmp_path)
    rows = [tab.layers.item(i).text() for i in range(tab.layers.count())]
    assert len(rows) == 2 and any("Testo vicino" in r for r in rows) and any(r.startswith("Linea") for r in rows)
    line_row = next(i for i, r in enumerate(rows) if r.startswith("Linea"))
    tab.layers.item(line_row).setSelected(True)  # picked in the list: selected on the page
    assert [o.kind for o in tab.selection] == ["line"]
    assert [i.obj.kind for i in tab.items if i.isSelected()] == ["line"]
    tab.select([o for o in tab.objects if o.kind == "text"])  # picked on the page: selected in the list
    assert [tab.layers.row(i) for i in tab.layers.selectedItems()] == [1 - line_row]


def test_ctrl_d_duplicates_and_selects_the_copy(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    tab = _tab(tmp_path)
    tab.show()
    tab.activateWindow()
    QTest.qWaitForWindowActive(tab)
    tab.select([o for o in tab.objects if o.kind == "line"])
    QTest.keyClick(tab.view, Qt.Key_D, Qt.ControlModifier)
    lines = sorted(round(o.data["p1"][1]) for o in tab.objects if o.kind == "line")
    assert lines == [104, 114] and [round(o.data["p1"][1]) for o in tab.selection] == [114]
    tab.undo()
    assert [o.kind for o in tab.objects].count("line") == 1


def test_text_font_bold_and_italic_from_the_properties(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "text"])
    assert tab.p_font.currentText() == "Helvetica" and not tab.p_bold.isChecked()
    tab.p_font.setCurrentIndex(tab.p_font.findText("Times"))
    tab.p_font.activated.emit(tab.p_font.currentIndex())
    tab.p_bold.click()
    text = next(o for o in tab.objects if o.kind == "text")
    assert design.font_style(tab.doc[0], text) == ("Times", True, False)
    assert tab.p_font.currentText() == "Times" and tab.p_bold.isChecked()  # still selected, shown as it is now
    tab.p_size.setText("40")
    tab.p_size.editingFinished.emit()
    assert len(tab.selection) == 1  # bigger, taller: still the one selected


def test_rotate_align_and_stack_keep_the_selection(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "text"])
    assert tab.arrange.isVisibleTo(tab) and tab.layers_box.isVisibleTo(tab)
    tab.p_turn.setText("30")
    tab._turn_edited()
    assert len(tab.selection) == 1 and tab.selection[0].kind == "text"
    assert tab.selection[0].data["dir"] == pytest.approx((0.866, 0.5), abs=0.01)
    tab.rotate_selected(-30)
    tab.select(tab.objects)
    assert not tab.layers_box.isVisibleTo(tab)  # one at a time
    tab.align_selected("left")
    assert len(tab.selection) == 2
    assert tab.selection[0].rect[0] == pytest.approx(tab.selection[1].rect[0], abs=0.6)
    line = next(o for o in tab.objects if o.kind == "line")
    tab.select([line])
    tab.restack_selected("front")
    assert tab.selection and tab.selection[0].kind == "line"


def _boxes(tmp_path, n=3):
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
    page = doc[0]
    for i in range(n):
        page.draw_rect((72 + i * 90 + i * i * 10, 200, 132 + i * 90 + i * i * 10, 240), color=(0, 0, 0), fill=(1, 0.8, 0.8))
    page.insert_text((72, 100), "Totale dovuto: 10")
    page.insert_text((72, 130), "Altro testo")
    path = tmp_path / "boxes.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    return tab


def _rects(tab):
    return sorted((o for o in tab.objects if o.kind == "rect"), key=lambda o: o.rect[0])


def test_group_selects_together_and_follows_a_move(qapp, tmp_path):
    tab = _boxes(tmp_path)
    a, b, _ = _rects(tab)
    tab.select([a, b])
    tab.group_selected()
    assert len(tab.groups) == 1 and tab.b_group.text() == "Separa"
    tab.select([_rects(tab)[0]])
    tab._selection_changed()
    assert len(tab.selection) == 2  # the whole group
    tab.move_selected(0, 30)
    assert len(tab.groups) == 1 and all(o.rect[1] == pytest.approx(230, abs=1) for o in tab.groups[0])
    tab.undo()
    tab.undo()
    assert not tab.groups


def test_pinned_object_does_not_move(qapp, tmp_path):
    tab = _boxes(tmp_path)
    a = _rects(tab)[0]
    tab.select([a])
    tab.pin_selected()
    a = _rects(tab)[0]
    assert a.data.get("pinned") and tab.b_pin.text() == "Sblocca"
    tab.select([a])
    tab.move_selected(0, 30)
    tab.delete_selected()
    assert _rects(tab)[0].rect[1] == pytest.approx(200, abs=1) and len(_rects(tab)) == 3
    assert not tab.props.isVisibleTo(tab)
    tab.select([_rects(tab)[0]])
    tab.pin_selected()
    assert not _rects(tab)[0].data.get("pinned")


def test_distribute_and_many_at_once(qapp, tmp_path):
    tab = _boxes(tmp_path)
    tab.select(_rects(tab))
    assert tab.spread.isVisibleTo(tab) and tab.props.isVisibleTo(tab)
    tab.distribute_selected("h")
    r = _rects(tab)
    assert r[1].rect[0] - r[0].rect[2] == pytest.approx(r[2].rect[0] - r[1].rect[2], abs=0.6)
    tab.select(r)
    tab.p_alpha.setText("50")
    tab._alpha_edited()
    page = tab.doc[0]
    assert all(design.opacity(page, o) == pytest.approx(0.5, abs=0.02) for o in _rects(tab))
    tab.select(_rects(tab))
    tab.p_dash.setCurrentIndex(1)
    tab._dash_edited()
    assert all(design.dash_style(o) for o in _rects(tab))


def test_copy_paste_on_another_page(qapp, tmp_path):
    tab = _boxes(tmp_path)
    tab.select(_rects(tab)[:1])
    tab.copy_selected()
    tab.thumbs.setCurrentRow(1)
    assert tab.page_no == 1 and not _rects(tab)
    tab.paste()
    assert [o.rect[1] for o in _rects(tab)] == [pytest.approx(200, abs=1)]
    tab.paste()  # on another page: the same place again
    tab.thumbs.setCurrentRow(0)
    tab.paste()  # on its own page: moved, not on itself
    assert len(_rects(tab)) == 4


def test_find_and_replace_everywhere(qapp, tmp_path, monkeypatch):
    tab = _boxes(tmp_path)
    answers = iter(["Totale", "Somma"])
    monkeypatch.setattr(ui, "ask_text", lambda *a, **k: next(answers))
    monkeypatch.setattr(ui, "info", lambda *a, **k: None)
    tab.find_replace()
    words = "".join(s["text"] for o in tab.objects if o.kind == "text" for s in o.data["spans"])
    assert "Somma dovuto" in words and "Totale" not in words
    tab.undo()
    words = "".join(s["text"] for o in tab.objects if o.kind == "text" for s in o.data["spans"])
    assert "Totale" in words


def test_arrows_and_round_corners_from_the_panel(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    assert tab.form.isRowVisible(tab.p_heads)
    tab.p_head1.setChecked(True)
    tab._heads_edited()
    line = next(o for o in tab.objects if o.kind == "line")
    assert line.data.get("arrows") == (False, True) and tab.selection == [line]
    tab2 = _boxes(tmp_path)
    tab2.select(_rects(tab2)[:1])
    tab2.p_round.setText("8")
    tab2._round_edited()
    assert _rects(tab2)[0].data.get("round") == pytest.approx(8, abs=0.5)


def test_drag_catches_an_edge_and_the_knob_turns(qapp, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tab = _boxes(tmp_path)
    tab.resize(1300, 900)
    tab.show()
    qapp.processEvents()
    v, z = tab.view, tab.zoom
    a, b = _rects(tab)[:2]  # b starts at x 162: dragged near a's left edge (72), it lands on it
    start = v.mapFromScene((b.rect[0] + 10) * z, 220 * z)
    end = v.mapFromScene((72 + 10 + 2) * z, 290 * z)
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(v.viewport(), QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    QTest.mouseMove(v.viewport(), end)
    assert tab.guides  # the guide shows while dragging
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    assert not tab.guides
    moved = next(o for o in _rects(tab) if o.rect[1] > 250)
    assert moved.rect[0] == pytest.approx(72, abs=0.6)
    tab.select([moved])
    item = next(i for i in tab.items if i.obj is tab.selection[0])
    knob = v.mapFromScene(item.mapToScene(item.knob()))
    side = v.mapFromScene(item.mapToScene(item.rect().topRight()))
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, knob)
    QTest.mouseMove(v.viewport(), side + QPoint(40, 0))
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, side + QPoint(40, 0))
    turned = next(o for o in tab.objects if o.rect[1] > 230 and o.kind != "text")
    assert pymupdf.Rect(turned.rect).height > 45  # a 60x40 box turned stands taller than before


def test_list_shows_colours_and_the_hovered_object(qapp, tmp_path):
    tab = _boxes(tmp_path)
    row = next(i for i, o in enumerate(tab.rows) if o.kind == "rect")
    tab._layer_hovered(tab.layers.item(row))
    assert [i.hover for i in tab.items if i.obj is tab.rows[row]] == [True]
    assert sum(i.hover for i in tab.items) == 1
    tab._layer_hovered(None)
    assert not any(i.hover for i in tab.items)


def test_editor_empty_screen_lists_recent_files(qapp, tmp_path):
    tab = _boxes(tmp_path)
    tab.doc = None
    tab.refresh()
    assert tab.recent_box.lay.count() >= 2  # title + the file opened


def test_bar_drops_words_one_at_a_time(qapp):
    from PySide6.QtWidgets import QHBoxLayout
    bar = ui.Bar()
    lay = QHBoxLayout(bar)
    save = ui.button("Salva modulo", "save", "Primary")
    plain = [ui.button(t, "open") for t in ("Apri", "Esporta per gestionale", "Anteprima")]
    for b in [plain[0], save] + plain[1:]:
        lay.addWidget(b)
    bar.resize(lay.sizeHint().width() - 20, 40)
    bar.show()
    qapp.processEvents()
    assert [b.text() for b in plain] == ["Apri", "Esporta per gestionale", ""]  # the rightmost first
    assert save.text() == "Salva modulo"
    bar.resize(200, 40)
    qapp.processEvents()
    assert all(not b.text() for b in plain)  # the main one keeps its words while it fits
    bar.resize(2000, 40)
    qapp.processEvents()
    assert save.text() == "Salva modulo" and plain[2].text() == "Anteprima"


def test_unsaved_pdf_comes_back_after_a_crash(qapp, tmp_path):
    from pdflex import recovery
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    tab.move_selected(0, 30)
    tab.backup()
    path, copy = recovery.pending()["editor"]
    again = DesignTab()
    again.restore(path, copy)
    assert again.path == path and again.dirty and _line_y(again) == 134
