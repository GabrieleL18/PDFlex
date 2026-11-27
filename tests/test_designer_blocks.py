import pymupdf
import pytest

from pdflex import blocks as bl
from pdflex import design, ui
from pdflex.designer import DesignTab, DividerItem, _marks


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    """A warning would wait for a click: here it fails the test instead."""
    def warn(parent, text, *a, **k):
        raise AssertionError(text)
    monkeypatch.setattr(ui, "warn", warn)


def _tab(tmp_path, pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    path = tmp_path / "vuoto.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    return tab


def _block(tmp_path, pid, pages=1):
    tab = _tab(tmp_path, pages)
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    tab.use_preset(pid)
    return tab


def test_draw_rect_then_preset_makes_a_block(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    tab.use_preset("tabella")
    assert len(tab.blocks) == 1 and tab.selection and all(tab.block_of(o) for o in tab.selection)
    assert len(tab._undo) == 2  # rectangle, then block: two steps
    assert tab.blocks[0][1].rect == pytest.approx((100, 100, 400, 250), abs=0.5)


def test_block_edit_undo_restores_note(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    entry = tab.blocks[0]
    tab.apply_block(entry, bl.set_count(entry[1], "rows", 8))
    assert len(tab.blocks[0][1].rows) == 8
    tab.undo()
    assert len(tab.blocks) == 1 and len(tab.blocks[0][1].rows) == 6


def test_block_follows_move_and_resize(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.select(tab.blocks[0][2])
    tab.move_selected(20, 30)
    assert tab.blocks[0][1].rect == pytest.approx((120, 130, 420, 280), abs=1)
    tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "rows", 7))
    assert tab.blocks[0][1].rect == pytest.approx((120, 130, 420, 280), abs=1)
    assert len(tab.blocks[0][1].rows) == 7


def test_block_copy_paste_other_page(qapp, tmp_path):
    tab = _block(tmp_path, "griglia", pages=2)
    tab.select(tab.blocks[0][2])
    tab.copy_selected()
    tab.go_page(1)
    tab.paste()
    assert len(tab.blocks) == 1
    tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "cols", 3))
    assert len(tab.blocks[0][1].cols) == 3
    tab.go_page(0)
    assert len(tab.blocks[0][1].cols) == 9


def test_duplicate_block(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.select(tab.blocks[0][2])
    tab.duplicate_selected()
    assert len(tab.blocks) == 2


def test_broken_block_is_loose(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    line = next(o for o in tab.blocks[0][2] if o.kind == "line")
    tab._edit(design.delete, [line])  # as if changed by another program
    assert tab.blocks == []


def test_delete_block_and_rotate_asks(qapp, tmp_path, monkeypatch):
    tab = _block(tmp_path, "riquadro")
    monkeypatch.setattr(ui, "message", lambda *a, **k: 1)  # Annulla
    tab.select(tab.blocks[0][2])
    tab.rotate_selected(90)
    assert len(tab.blocks) == 1
    tab.select(tab.blocks[0][2])
    tab.delete_selected()
    assert tab.blocks == [] and _marks(tab.doc).get("blocks", {}).get("0", []) == []


def test_rotate_makes_loose_shapes(qapp, tmp_path, monkeypatch):
    tab = _block(tmp_path, "riquadro")
    monkeypatch.setattr(ui, "message", lambda *a, **k: 0)  # Continua
    tab.select(tab.blocks[0][2])
    tab.rotate_selected(90)
    assert tab.blocks == [] and len(tab.objects) >= 2


def test_find_replace_changes_cell_text(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.replace_all("Titolo", "Presenze")
    assert tab.blocks[0][1].texts["0,0"] == "Presenze" and "Presenze" in tab.doc[0].get_text()
    assert "Titolo" not in tab.doc[0].get_text()


def test_block_survives_save_and_reopen(qapp, tmp_path):
    tab = _block(tmp_path, "griglia")
    out = tmp_path / "salvato.pdf"
    tab.save(str(out))
    tab2 = DesignTab()
    tab2.open(str(out))
    assert len(tab2.blocks) == 1 and tab2.blocks[0][1].header_col


def test_gallery_after_drawing_a_rectangle(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.show()
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    assert tab.gallery.isVisible() and list(tab.gallery.buttons) == [p for p, _ in bl.PRESETS]
    assert not tab.gallery.buttons["tabella"].icon().isNull()
    tab.gallery.buttons["griglia"].click()
    assert not tab.gallery.isVisible() and tab.blocks[0][1].header_col
    tab.set_mode("rect")
    tab.drawn((10, 10, 25, 18))  # too small for a block: no gallery
    assert not tab.gallery.isVisible()


def test_block_panel_rows_cols_and_header(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.select(tab.blocks[0][2])
    assert tab.form.isRowVisible(tab.p_rows) and tab.p_cols.value() == 4
    tab.p_cols.setValue(6)
    assert len(tab.blocks[0][1].cols) == 6
    tab.p_head_col.setChecked(True)
    assert tab.blocks[0][1].header_col


def test_split_a_plain_rectangle(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.set_mode("rect")
    tab.drawn((100, 100, 300, 200))
    tab.gallery.close()
    tab.select([tab.last_drawn])
    assert tab.p_rows.value() == 1
    tab.p_rows.setValue(3)
    assert len(tab.blocks) == 1 and len(tab.blocks[0][1].rows) == 3


def test_cells_text_merge_split(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    e = tab.blocks[0]
    tab.enter_cells(e, 0, 1)
    tab.pick_cell(0, 2, add=True)
    tab.b_merge.click()
    assert tab.blocks[0][1].merges == [(0, 1, 0, 2)]
    tab.enter_cells(tab.blocks[0], 0, 1)
    tab.p_cell_text.setText("TOTALE")
    tab.p_cell_text.editingFinished.emit()
    assert tab.blocks[0][1].texts["0,1"] == "TOTALE"
    tab.b_split.click()
    assert tab.blocks[0][1].merges == []


def test_drag_divider(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.show()
    tab.select(tab.blocks[0][2])
    d = next(i for i in tab.scene.items() if isinstance(i, DividerItem) and i.axis == "cols" and i.index == 0)
    d.drop_at(130)  # what the mouse release calls, in PDF points
    assert tab.blocks[0][1].cols[0] * 300 == pytest.approx(30, abs=0.5)


def test_drag_outer_side_scales(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.show()
    tab.select(tab.blocks[0][2])
    d = next(i for i in tab.scene.items() if isinstance(i, DividerItem) and i.axis == "cols" and i.index == 3)
    d.drop_at(500)  # right side from 400 to 500 (an A4 page is 595 wide)
    b = tab.blocks[0][1]
    assert b.rect[2] == pytest.approx(500, abs=0.5) and b.cols == pytest.approx([0.25] * 4)


def test_unblock(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.select(tab.blocks[0][2])
    tab.b_unblock.click()
    assert tab.blocks == [] and len(tab.objects) > 5


def test_sample_text_on_screen_only(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")  # no text of its own
    assert not tab.b_sample.isChecked() and tab.samples == []
    tab.b_sample.click()
    assert tab.samples and all(i.scene() is tab.scene for i in tab.samples)
    assert len(tab.samples) == 4 * 6  # every empty cell: 4 headers and 20 body cells
    out = tmp_path / "prova.pdf"
    tab.save(str(out))
    assert pymupdf.open(out)[0].get_text().strip() == ""  # never written to the PDF
    tab.go_page(0)
    assert tab.samples  # still on after the page is drawn again
    tab.b_sample.click()
    assert tab.samples == []


def _shown(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.resize(1200, 900)
    tab.show()
    qapp.processEvents()
    return tab


def test_drawing_snaps_to_an_edge(qapp, tmp_path):
    from tests.test_designer import _drag
    tab = _shown(qapp, tmp_path)
    tab.set_mode("rect")
    tab.drawn((300, 100, 400, 200))  # a box to catch
    tab.gallery.close()
    tab.set_mode("rect")
    z, v = tab.zoom, tab.view
    start, end = v.mapFromScene(100 * z, 300 * z), v.mapFromScene(402.5 * z, 350 * z)  # 2.5 pt past its right edge
    _drag(v, start, end)
    new = max(tab.objects, key=lambda o: o.rect[1])
    assert new.rect[2] == pytest.approx(400, abs=0.3) and tab.guides == []


def test_size_tag_while_drawing(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    tab = _shown(qapp, tmp_path)
    tab.set_mode("rect")
    z, v = tab.zoom, tab.view
    start, end = v.mapFromScene(100 * z, 300 * z), v.mapFromScene(180 * z, 340 * z)
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(v.viewport(), end)
    assert tab.size_tag.isVisible() and "pt" in tab.size_tag.text() and "80" in tab.size_tag.text()
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    assert tab.size_tag is None or not tab.size_tag.isVisible()


def test_resizing_snaps_to_an_edge(qapp, tmp_path):
    from tests.test_designer import _drag
    tab = _shown(qapp, tmp_path)
    for r in ((300, 100, 400, 200), (100, 300, 200, 350)):
        tab.set_mode("rect")
        tab.drawn(r)
        tab.gallery.close()
    low = max(tab.objects, key=lambda o: o.rect[1])
    tab.select([low])
    z, v = tab.zoom, tab.view
    _drag(v, v.mapFromScene(200 * z, 325 * z), v.mapFromScene(402.5 * z, 325 * z))  # right side, near 400
    low = max(tab.objects, key=lambda o: o.rect[1])
    assert low.rect[2] == pytest.approx(400, abs=0.3)


def test_divider_dragged_with_the_mouse(qapp, tmp_path):
    from tests.test_designer import _drag
    tab = _shown(qapp, tmp_path)
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    tab.use_preset("tabella")
    z, v = tab.zoom, tab.view
    _drag(v, v.mapFromScene(175 * z, 200 * z), v.mapFromScene(140 * z, 200 * z))  # first column line, to 140
    qapp.processEvents()  # the change is made once the mouse is let go
    assert tab.blocks[0][1].cols[0] * 300 == pytest.approx(40, abs=1)


def test_every_editor_button_has_an_icon(qapp, tmp_path):
    from PySide6.QtWidgets import QAbstractButton
    tab = _block(tmp_path, "tabella")
    tab.select(tab.blocks[0][2])
    bare = [b.text() or b.toolTip() for b in tab.findChildren(QAbstractButton)
            if b.icon().isNull() and not isinstance(b, ui.Toggle)]
    assert bare == []


def test_block_font_from_the_panel(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.select(tab.blocks[0][2])
    assert tab.form.isRowVisible(tab.p_block_font) and tab.p_block_font.currentText() == "Helvetica"
    tab.p_block_font.setCurrentText("Times")
    tab.p_block_font.activated.emit(tab.p_block_font.currentIndex())
    assert tab.blocks[0][1].font == "Times" and tab.p_block_font.currentText() == "Times"


def test_block_over_an_identical_box(qapp, tmp_path):  # review: keys of the box and the block's border collided
    doc = pymupdf.open()
    doc.new_page().draw_rect((100, 100, 400, 250), color=(0, 0, 0))
    path = tmp_path / "box.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    tab.use_preset("tabella")
    assert len(tab.blocks) == 1
    assert tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "rows", 7))
    assert len(tab.blocks[0][1].rows) == 7


def test_paste_a_block_twice(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro", pages=2)
    tab.select(tab.blocks[0][2])
    tab.copy_selected()
    tab.go_page(1)
    tab.paste()
    tab.paste()
    assert len(tab.blocks) == 2
    for i in range(2):
        e = next(x for x in tab.blocks if x[0] == i)
        assert tab.apply_block(e, bl.set_count(e[1], "rows", 2 + i))
    assert sorted(len(x[1].rows) for x in tab.blocks) == [2, 3]


def test_align_moves_a_block_whole(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.select(tab.blocks[0][2])
    tab.align_selected("left")  # one object alone: to the page's left side
    assert len(tab.blocks) == 1 and tab.blocks[0][1].rect[0] == pytest.approx(0, abs=0.5)
    xs = sorted(round(o.rect[0]) for o in tab.objects if o.kind == "line" and o.rect[0] == o.rect[2])
    assert xs == [75, 150, 225]


def test_group_a_block_with_a_box(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.set_mode("rect")
    tab.drawn((450, 100, 500, 150))
    tab.gallery.hide()
    box = next(o for o in tab.objects if o.kind == "rect" and o.rect[0] > 440)
    tab.select(tab.blocks[0][2] + [box])
    assert tab.b_group.text() == "Raggruppa"
    tab.group_selected()
    assert len(_marks(tab.doc)["groups"]["0"]) == 1 and len(tab.blocks) == 1


def test_typing_a_count_waits_for_the_whole_number(qapp, tmp_path):
    tab = _tab(tmp_path)
    assert not tab.p_rows.keyboardTracking() and not tab.p_cols.keyboardTracking()


def test_cell_mode_ends_when_another_block_is_selected(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.set_mode("rect")
    tab.drawn((100, 400, 300, 500))
    tab.use_preset("riquadro")
    a = next(e for e in tab.blocks if e[1].rect[1] < 300)
    b = next(e for e in tab.blocks if e[1].rect[1] > 300)
    tab.enter_cells(a, 0, 0)
    tab.select(b[2])
    assert tab.cell_mode is None
