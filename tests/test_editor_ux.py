import copy
import dataclasses

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pdflex import open_pdf
from pdflex.editor import EditorTab
from pdflex.fields import Field, load_fields, save_with_fields


@pytest.fixture
def tab(qapp, make_pdf, tmp_path):
    t = EditorTab()
    t.resize(1300, 800)
    t.show()
    t.open(str(make_pdf(tmp_path, ["uno", "due"])))
    QTest.qWait(50)
    return t


def test_ctrl_y_redoes(tab):
    tab.add_field("text", 0, (10, 10, 100, 30))
    tab.undo()
    assert tab.fields == []
    tab.activateWindow()
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_Y, Qt.ControlModifier)
    assert [f.name for f in tab.fields] == ["text_1"]


def test_arrows_nudge_snap_and_scroll(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("text", 0, (150, 60, 250, 80))
    tab.select_many([a])
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_Right)
    assert a.rect[0] == pytest.approx(11)
    QTest.keyClick(tab.view, Qt.Key_Down, Qt.ShiftModifier)  # down to the nearest edge: a's bottom on b's top
    assert a.rect[3] == pytest.approx(60)
    QTest.keyClick(tab.view, Qt.Key_Right, Qt.ShiftModifier)  # right edge 101 -> b's left 150
    assert a.rect[2] == pytest.approx(150)
    tab.undo()
    assert a is not tab.fields[0] and tab.fields[0].rect[2] == pytest.approx(101)
    tab.select_many([tab.fields[0]])
    bar = tab.view.verticalScrollBar()
    bar.setValue(0)
    before = tab.fields[0].rect
    QTest.keyClick(tab.view, Qt.Key_Down, Qt.ControlModifier)  # Ctrl: the page scrolls, the field stays
    assert tab.fields[0].rect == before and bar.value() > 0


def test_drag_on_the_page_selects_several(tab):
    tab.add_field("text", 0, (20, 20, 100, 40))
    tab.add_field("text", 0, (20, 60, 100, 80))
    tab.add_field("text", 0, (300, 300, 380, 320))
    tab.refresh()
    z, vp = tab.zoom, tab.view.viewport()
    start = tab.view.mapFromScene(10 * z, 10 * z)
    end = tab.view.mapFromScene(120 * z, 90 * z)
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, 6):
        QTest.mouseMove(vp, start + (end - start) * i / 5)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, end)
    assert sorted(f.name for f in tab.selection) == ["text_1", "text_2"]


def test_list_filters(tab):
    tab.add_field("text", 0, (10, 10, 100, 30))
    tab.add_field("checkbox", 1, (10, 10, 24, 24))
    tab.refresh()
    tab.f_page.setCurrentIndex(tab.f_page.findData(1))
    shown = [i for i in range(tab.field_list.count()) if not tab.field_list.isRowHidden(i)]
    assert shown == [1]
    tab.f_page.setCurrentIndex(0)
    tab.f_kind.setCurrentIndex(tab.f_kind.findData("text"))
    shown = [i for i in range(tab.field_list.count()) if not tab.field_list.isRowHidden(i)]
    assert shown == [0]


def test_panels_stack_and_remember(tab):
    tab.set_stacked(True)
    assert tab.side.orientation() == Qt.Vertical
    assert EditorTab().side.orientation() == Qt.Vertical  # next time it opens the same way
    tab.set_stacked(False)


def test_font_saved_and_changed(make_pdf, tmp_path):
    src, out = make_pdf(tmp_path, ["x"]), tmp_path / "f.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 200, 30), "a", font="Cour", default="ABC")], out)
    f = load_fields(open_pdf(out))[0]
    assert f.font == "Cour"
    f.font, f.font_size = "TiRo", 11
    save_with_fields(out, [f], tmp_path / "g.pdf")
    g = load_fields(open_pdf(tmp_path / "g.pdf"))[0]
    assert (g.font, g.font_size, g.default) == ("TiRo", 11, "ABC")


def test_menu_initial_value(tab):
    f = tab.add_field("dropdown", 0, (10, 10, 150, 30))
    tab._select(f)
    tab.refresh()
    tab.p_options.setPlainText("Roma\nMilano")
    tab.p_default.setText("milano")
    tab.p_default.editingFinished.emit()
    assert f.default == "Milano"  # matched to the option, whatever the capitals
    f.default = "Torino"
    with pytest.raises(ValueError, match="opzioni"):
        save_with_fields(tab.path, tab.fields, tab.path + ".out.pdf")


def test_value_being_typed_is_saved(tab, tmp_path):
    f = tab.add_field("text", 0, (10, 10, 150, 30))
    tab._select(f)
    tab.refresh()
    tab.p_default.setText("Mario")  # no Enter, no click elsewhere: then Ctrl+S
    tab._props_edited()  # what ask_save does before asking where
    out = tmp_path / "s.pdf"
    tab.save(str(out))
    assert load_fields(open_pdf(out))[0].default == "Mario"


def test_preview_leaves_nothing_behind(tab):
    tab.add_field("text", 0, (10, 10, 150, 30))
    shown = []

    def close():
        d = QApplication.activeModalWidget()
        shown.append(d)
        d.reject()
    QTimer.singleShot(300, close)
    tab.preview()
    QTest.qWait(50)
    assert shown and shown[0] is not None
    assert not tab.findChildren(QDialog)  # the window is gone with its document (memory and file lock)


def test_copy_paste_fields(tab):
    a = tab.add_field("text", 0, (20, 20, 120, 40))
    a.fmt = "amount"
    b = tab.add_field("checkbox", 0, (20, 50, 34, 64))
    tab.select_many([a, b])
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_C, Qt.ControlModifier)
    QTest.keyClick(tab.view, Qt.Key_V, Qt.ControlModifier)  # same page: a little lower-right
    new = tab.fields[2:]
    assert [f.name for f in new] == ["text_1_2", "checkbox_1_2"] and new[0].fmt == "amount"
    assert new[0].rect[0] == pytest.approx(30) and [f.name for f in tab.selection] == ["text_1_2", "checkbox_1_2"]
    tab.go_page(1)
    made = tab.paste()  # another page: the same place
    assert [f.page for f in made] == [1, 1] and made[0].rect[0] == pytest.approx(30)
    tab.undo()
    assert len(tab.fields) == 4


def test_format_colour_alignment_from_the_panel(tab, tmp_path):
    f = tab.add_field("text", 0, (10, 10, 200, 30))
    tab._select(f)
    tab.refresh()
    pick = lambda combo, value: combo.setCurrentIndex(combo.findData(value))
    pick(tab.p_fmt, "percent")
    pick(tab.p_dec, 1)
    tab.p_thousands.setChecked(False)
    assert f.fmt == "percent|1|0" and tab.form.isRowVisible(tab.p_dec)
    pick(tab.p_fmt, "date")  # a date makes it a "Data" field, with its layouts to choose from
    assert f.kind == "date" and f.fmt == "date|dd/mm/yyyy" and tab.form.isRowVisible(tab.p_pattern)
    assert tab.kind_label.text().startswith("Data")
    pick(tab.p_pattern, "dd/mm/yy")
    assert f.fmt == "date|dd/mm/yy"
    pick(tab.p_fmt, "time")
    assert f.fmt == "time|HH:MM"
    pick(tab.p_fmt, "cf")
    assert (f.kind, f.fmt) == ("text", "cf") and not tab.form.isRowVisible(tab.p_pattern)
    tab._show_color("#336699")
    tab._set_prop("color")  # what the colour picker does
    pick(tab.p_align, 1)
    assert (f.color, f.align) == ("#336699", 1)
    tab.undo()  # each step is one undo
    assert tab.fields[0].align == 0 and tab.fields[0].color == "#336699"
    out = tmp_path / "da_compilare.pdf"
    tab.export(str(out))
    got = load_fields(open_pdf(out))[0]
    assert (got.fmt, got.color, got.default) == ("cf", "#336699", "")
    assert tab.doc is not None and tab.path != str(out)  # the document being edited stays the same


def test_export_refuses_the_file_being_edited(tab, monkeypatch):
    from pdflex import editor
    warned = []
    monkeypatch.setattr(editor.QFileDialog, "getSaveFileName", lambda *a, **k: (tab.path, ""))
    monkeypatch.setattr(editor, "warn", lambda parent, text: warned.append(text))
    assert tab.ask_export() is False and "altro nome" in warned[0]


def test_page_thumbnails_jump_and_count_fields(tab):
    assert tab.thumbs.isVisible() and tab.thumbs.count() == 2 and tab.thumbs.currentRow() == 0
    tab.add_field("text", 1, (10, 10, 100, 30))
    tab.thumbs.setCurrentRow(1)  # a click on the second thumbnail
    assert tab.page_no == 1 and tab.thumbs.item(1).text() == "2  ·  1 campo"
    tab.b_next.click() if tab.b_next.isEnabled() else tab.b_prev.click()
    assert tab.page_no == 0 and tab.thumbs.currentRow() == 0  # the strip follows the page shown
    tab.b_pages.click()
    assert not tab.thumbs.isVisible()


def test_resize_from_any_edge_resizes_all_selected(tab):
    from pdflex.editor import FieldItem
    a = tab.add_field("text", 0, (20, 20, 100, 40))   # 80 wide
    b = tab.add_field("text", 0, (20, 80, 60, 100))   # 40 wide
    tab.select_many([a, b])
    z, vp = tab.zoom, tab.view.viewport()
    item = next(i for i in tab.scene.items() if isinstance(i, FieldItem) and i.field is a)
    assert item.edges_at(item.rect().center()) == ""  # the middle moves
    assert item.edges_at(QPoint(0, 0)) == "lt" and item.edges_at(item.rect().bottomRight()) == "rb"
    start = tab.view.mapFromScene(19.5 * z, 30 * z)  # a's left edge
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, 5):
        QTest.mouseMove(vp, start + QPoint(-round(10 * z) * i // 4, 0))
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, start + QPoint(-round(10 * z), 0))
    assert a.rect[0] == pytest.approx(10, abs=0.5) and a.rect[2] == pytest.approx(100, abs=0.5)
    assert b.rect[0] == pytest.approx(10, abs=0.5) and b.rect[2] == pytest.approx(60, abs=0.5)  # each from its size
    assert len(tab.selection) == 2
    tab.undo()
    assert a.rect[0] == pytest.approx(20) or tab.fields[0].rect[0] == pytest.approx(20)


def test_copy_paste_counts_array_names_up(tab):
    f = tab.add_field("text", 0, (20, 20, 100, 40))
    f.name = "Nome[0]"
    g = tab.add_field("text", 0, (20, 60, 100, 80))
    g.name = "Voce (1)"
    tab.select_many([f, g])
    tab.copy_selected()
    assert sorted(x.name for x in tab.paste()) == ["Nome[1]", "Voce (2)"]
    assert sorted(x.name for x in tab.paste()) == ["Nome[2]", "Voce (3)"]
    tab.select_many([f])
    tab.duplicate_selected()
    assert tab.selected.name == "Nome[3]"


def test_dragging_a_field_catches_another_and_shows_the_guide(tab):
    a = tab.add_field("text", 0, (100, 100, 180, 120))
    b = tab.add_field("text", 0, (103, 200, 183, 220))  # 3 points off a's left edge
    tab.select_many([b])
    z, vp = tab.zoom, tab.view.viewport()
    start = tab.view.mapFromScene(140 * z, 210 * z)
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, 4):
        QTest.mouseMove(vp, start + QPoint(0, round(20 * z) * i // 3))
    assert tab.guides  # the line and the box of what it lines up with
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, start + QPoint(0, round(20 * z)))
    assert b.rect[0] == pytest.approx(a.rect[0], abs=0.5)
    assert tab.guides == []


def test_grid_toggle_is_drawn_on_the_page(tab):
    tab.c_grid.setChecked(True)
    assert tab.grid
    tab.view.viewport().repaint()  # drawForeground runs without errors
    tab.c_grid.setChecked(False)
    assert not tab.grid


def test_import_fields_from_another_pdf(tab, make_pdf, tmp_path):
    src = tmp_path / "altro"
    src.mkdir()
    other = EditorTab()
    other.open(str(make_pdf(src, ["a", "b", "c"])))
    other.add_field("text", 0, (20, 20, 100, 40)).name = "Nome"
    other.add_field("text", 2, (20, 20, 100, 40)).name = "Terza"  # page 3: this PDF has only 2
    s1 = other.add_field("text", 1, (20, 60, 100, 80))
    s1.name, s1.calc = "Totale", ["Nome"]
    out = tmp_path / "altro.pdf"
    save_with_fields(other.path, other.fields, str(out))
    other.close()
    tab.add_field("text", 0, (200, 200, 260, 220)).name = "Nome"  # already here: the import gets Nome_2
    made, left = tab.import_fields(str(out))
    assert left == 1
    names = {f.name: f for f in made}
    assert set(names) == {"Nome_2", "Totale"}
    assert names["Totale"].calc == ["Nome_2"] and names["Totale"].page == 1
    assert names["Nome_2"].rect == pytest.approx((20, 20, 100, 40), abs=0.5) and not names["Nome_2"].xref
    tab.undo()
    assert [f.name for f in tab.fields] == ["Nome"]


def test_paste_near_the_edge_keeps_the_fields_together(tab):
    pw, ph = tab.doc[0].rect.width, tab.doc[0].rect.height
    a = tab.add_field("text", 0, (pw - 120, ph - 50, pw - 20, ph - 30))  # 20 points from the right and bottom
    b = tab.add_field("text", 0, (pw - 120, ph - 25, pw - 60, ph - 5))
    tab.select_many([a, b])
    tab.copy_selected()
    first = tab.paste()  # 10 lower-right would leave the sheet: back over the copied ones
    assert sorted(f.rect for f in first) == sorted([a.rect, b.rect])
    second = tab.paste()  # still no room: again over them, never squeezed against the edge
    assert sorted(f.rect for f in second) == sorted([a.rect, b.rect])
    dx = second[0].rect[0] - first[0].rect[0]
    for old, new in zip(sorted(first, key=lambda f: f.rect), sorted(second, key=lambda f: f.rect)):
        assert new.rect[0] - old.rect[0] == pytest.approx(dx) and new.rect[2] - new.rect[0] == pytest.approx(old.rect[2] - old.rect[0])
        assert new.rect[1] - old.rect[1] == pytest.approx(new.rect[1] - old.rect[1])
    gap = lambda fs: sorted(f.rect[1] for f in fs)[1] - sorted(f.rect[1] for f in fs)[0]
    assert gap(second) == pytest.approx(gap([a, b]))  # same distance between them every time


def test_preview_opens_again_and_again(tab):
    """The second preview crashed PDFlex: the first one's page scene was freed too early."""
    from pdflex.ui import DocView
    tab.add_field("text", 0, (10, 10, 150, 30))

    def use_and_close():
        d = QApplication.activeModalWidget()
        for i in range(4):  # scrolled and resized, as a person does
            bar = d.findChild(DocView).verticalScrollBar()
            bar.setValue(bar.maximum() * i // 3)
            d.resize(700 + 60 * i, 800)
            QApplication.processEvents()
        QTimer.singleShot(150, d.reject)
    for n in range(3):
        tab.fields[0].rect = (10 + n * 5, 10, 150, 30)
        QTimer.singleShot(300, use_and_close)
        tab.preview()
        QTest.qWait(50)
    assert not tab.findChildren(QDialog)


def test_saved_blocks_can_be_deleted(tab):
    tab.select_many([tab.add_field("text", 0, (10, 10, 150, 30))])
    tab.save_block("Anagrafica")
    tab.save_block("Firma")
    tab.delete_block("Anagrafica")
    assert [tab.block_combo.itemText(i) for i in range(tab.block_combo.count())] == ["Firma"]
    tab.delete_block("Firma")
    assert tab.block_combo.count() == 0 and not tab.b_block_del.isEnabled()


def test_unsaved_fields_come_back_after_a_crash(tab, qapp):
    from pdflex import recovery
    tab.add_field("text", 0, (10, 10, 150, 30)).name = "Ripreso"
    tab.dirty = True
    tab.backup()
    path = tab.path
    found = recovery.pending()
    assert found["moduli"][0] == path
    again = EditorTab()
    again.restore(path, found["moduli"][1])
    assert "Ripreso" in [f.name for f in again.fields] and again.dirty
    tab.dirty = False
    tab.backup()
    assert "moduli" not in recovery.pending()


def test_pasted_array_keeps_its_order_and_its_rows(tab):
    def make(names):
        tab.fields.clear()
        out = []
        for i, n in enumerate(names):
            f = tab.add_field("text", 0, (20, 20 + 30 * i, 100, 40 + 30 * i))
            f.name = n
            out.append(f)
        return out
    column = make(["Nome[0]", "Nome[1]", "Nome[2]"])
    tab.select_many(column[::-1])  # selected from the bottom up
    tab.copy_selected()
    assert [f.name for f in sorted(tab.paste(), key=lambda f: f.rect[1])] == ["Nome[3]", "Nome[4]", "Nome[5]"]
    row = make(["Nome[0]", "Cognome[0]", "Nome[1]"])
    tab.select_many(row[:2])
    tab.copy_selected()
    assert sorted(f.name for f in tab.paste()) == ["Cognome[2]", "Nome[2]"]  # still one row


# one field: kind, size and the new properties
def test_kind_combo_switches_text_multiline_date(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30))
    tab.select_field(f)
    tab.p_kind.setCurrentIndex(tab.p_kind.findData("date"))
    assert f.kind == "date" and f.fmt.startswith("date|")
    tab.p_kind.setCurrentIndex(tab.p_kind.findData("multiline"))
    assert f.kind == "multiline" and f.fmt == ""
    c = tab.add_field("checkbox", 0, (10, 40, 30, 60))
    tab.select_field(c)
    assert not tab.form.isRowVisible(tab.p_kind)


def test_size_rows_keep_top_left(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30))
    tab.select_field(f)
    assert tab.p_w.text() == "90" and tab.p_h.text() == "20"
    tab.p_w.setText("50")
    tab.p_w.editingFinished.emit()
    tab.p_h.setText("15")
    tab.p_h.editingFinished.emit()
    assert f.rect == pytest.approx((10, 10, 60, 25))


def test_new_rows_write_field(tab):
    c = tab.add_field("checkbox", 0, (10, 40, 30, 60))
    tab.select_field(c)
    tab.p_mark.setCurrentIndex(tab.p_mark.findData("H"))
    assert c.mark == "H"
    t = tab.add_field("text", 0, (10, 80, 100, 100))
    tab.select_field(t)
    tab.p_fmt.setCurrentIndex(tab.p_fmt.findData("amount"))
    assert tab.form.isRowVisible(tab.p_currency.parentWidget())
    tab.p_currency.setCurrentIndex(tab.p_currency.findData("$"))
    tab.p_before.setCurrentIndex(tab.p_before.findData(0))
    tab.p_vmin.setText("1,5")
    tab.p_vmin.editingFinished.emit()
    assert t.fmt == "amount|2|1|$|0" and t.vmin == "1.5"
    tab.p_dec.setCurrentIndex(tab.p_dec.findData(0))
    assert t.fmt == "amount|0|1|$|0"  # the currency stays when the decimals change
    tab.p_tooltip.setText("Totale in dollari")
    tab.p_tooltip.editingFinished.emit()
    tab.p_readonly.setChecked(True)
    tab.p_visibility.setCurrentIndex(tab.p_visibility.findData("print"))
    tab.p_maxlen.setText("8")
    tab.p_maxlen.editingFinished.emit()
    tab.p_calc_op.setCurrentIndex(tab.p_calc_op.findData("AVG"))
    assert (t.tooltip, t.readonly, t.visibility, t.maxlen, t.calc_op) == ("Totale in dollari", True, "print", 8, "AVG")


# many fields at once
def test_multi_edit_touches_only_changed_prop(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    a.font_size, a.fmt = 9, "number|2|1"
    b = tab.add_field("checkbox", 0, (10, 40, 30, 60))
    b.mark = "8"
    tab.select_many([a, b])
    QTest.qWait(50)
    assert tab.props.isVisible() and not tab.form.isRowVisible(tab.p_name)
    assert not tab.form.isRowVisible(tab.p_fmt)  # not common to text + checkbox
    before_a, before_b = copy.deepcopy(a), copy.deepcopy(b)
    tab.p_required.setChecked(True)
    assert a.required and b.required
    assert dataclasses.replace(a, required=False) == before_a and dataclasses.replace(b, required=False) == before_b
    tab.undo()  # one undo step for all of them
    assert not tab.fields[0].required and not tab.fields[1].required


def test_multi_mixed_values_show_diversi(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    a.font_size = 9
    b = tab.add_field("text", 0, (10, 40, 100, 60))
    b.font_size = 12
    b.required = True
    tab.select_many([a, b])
    QTest.qWait(50)
    assert tab.p_size.text() == "" and tab.p_size.placeholderText() == "(diversi)"
    assert tab.p_required.checkState() == Qt.PartiallyChecked
    tab._props_edited()  # nothing typed: the mixed values stay as they are
    assert (a.font_size, b.font_size, a.required, b.required) == (9, 12, False, True)
    tab.p_size.setText("10")
    tab.p_size.editingFinished.emit()
    assert a.font_size == b.font_size == 10


def test_multi_kind_only_between_similar(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("date", 0, (10, 40, 100, 60))
    tab.select_many([a, b])
    QTest.qWait(50)
    assert tab.form.isRowVisible(tab.p_kind)
    c = tab.add_field("checkbox", 0, (10, 70, 30, 90))
    tab.select_many([a, c])
    QTest.qWait(50)
    assert not tab.form.isRowVisible(tab.p_kind)
    tab.select_many([a, b])
    QTest.qWait(50)
    tab.p_kind.setCurrentIndex(tab.p_kind.findData("multiline"))
    assert a.kind == b.kind == "multiline"


def test_multi_size_each_keeps_its_corner(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("text", 0, (200, 50, 260, 70))
    tab.select_many([a, b])
    QTest.qWait(50)
    tab.p_w.setText("40")
    tab.p_w.editingFinished.emit()
    assert a.rect == pytest.approx((10, 10, 50, 30)) and b.rect == pytest.approx((200, 50, 240, 70))


def test_editing_one_property_keeps_unshown_ones(tab):
    f = tab.add_field("date", 0, (10, 10, 100, 30))
    f.maxlen = 10  # a date from another program with a length limit: no box for it here
    tab.select_field(f)
    tab.p_required.setChecked(True)
    assert f.required and f.maxlen == 10


# tab order
def test_sort_by_columns(tab):
    a = tab.add_field("text", 0, (10, 100, 90, 120))
    b = tab.add_field("text", 0, (200, 10, 290, 30))
    c = tab.add_field("text", 0, (10, 10, 90, 30))
    tab.sort_by_columns()
    assert tab.fields == [c, a, b]


def test_tab_number_counts_radio_group_once(tab):
    r1 = tab.add_field("radio", 0, (10, 10, 25, 25))
    r2 = tab.add_field("radio", 0, (40, 10, 55, 25))
    r2.name = r1.name
    t = tab.add_field("text", 0, (10, 40, 100, 60))
    assert (tab.tab_number(r1), tab.tab_number(r2), tab.tab_number(t)) == (1, 1, 2)


def test_order_toggle_draws_numbers(tab):
    tab.add_field("text", 0, (10, 10, 100, 30))
    tab.c_order.setChecked(True)
    QTest.qWait(50)
    assert tab.c_order.isChecked() and tab._order_no
    tab.view.grab()  # paints every field with its number: must not fail


# paste: next table row, or the move the user made
def test_paste_goes_to_next_table_row(qapp, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    for y in (100, 120, 140, 160):
        page.draw_line((50, y), (500, y))
    doc.save(tmp_path / "tab.pdf")
    t = EditorTab()
    t.open(str(tmp_path / "tab.pdf"))
    a = t.add_field("text", 0, (60, 102, 200, 118))
    a.name = "Nome[0]"
    t.select_many([a])
    t.copy_selected()
    p1 = t.paste()[0]
    p2 = t.paste()[0]
    assert (p1.name, p2.name) == ("Nome[1]", "Nome[2]")
    assert p1.rect == pytest.approx((60, 122, 200, 138)) and p2.rect == pytest.approx((60, 142, 200, 158))
    p3 = t.paste()[0]  # no row left: a little lower-right, as before
    assert p3.rect[1] != pytest.approx(162) and p3.rect[0] == pytest.approx(70)


def test_paste_repeats_manual_move(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30))
    tab.select_many([f])
    tab.copy_selected()
    p1 = tab.paste()[0]
    tab._move([p1], -10, 30)  # the user puts it 40 below the copied one
    p2 = tab.paste()[0]
    p3 = tab.paste()[0]
    assert p2.rect[:2] == pytest.approx((10, 90)) and p3.rect[:2] == pytest.approx((10, 130))


# review fixes
def test_other_property_keeps_total_without_format(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    t = tab.add_field("text", 0, (10, 40, 100, 60))
    t.calc, t.vmax = ["text_1"], "5"  # a total from another program: a sum, no number format
    tab.select_many([a, t])
    QTest.qWait(50)
    tab.p_required.setChecked(True)
    assert t.required and t.calc == ["text_1"] and t.vmax == "5"


def test_one_click_on_mixed_toggle_sets_all(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("text", 0, (10, 40, 100, 60))
    b.required = True
    tab.select_many([a, b])
    QTest.qWait(50)
    tab.p_required.click()
    assert a.required and b.required and "(diversi)" not in tab.p_required.text()


def test_save_keeps_values_the_panel_cannot_show(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30))
    f.fmt, f.vmax = "amount|6|1|¥|1", "100.50"  # from another program: yen, 6 decimals
    c = tab.add_field("checkbox", 0, (10, 40, 30, 60))
    c.mark = "5"
    for g in (f, c):
        tab.select_field(g)
        before = copy.deepcopy(g)
        tab._props_edited()  # what Ctrl+S, preview and export do first
        assert g == before


def test_colour_from_the_picker_is_applied(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30))
    tab.select_field(f)
    tab._show_color("#336699")
    tab._set_prop("color")
    assert f.color == "#336699"


def test_percent_has_no_min_max(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30))
    tab.select_field(f)
    tab.p_fmt.setCurrentIndex(tab.p_fmt.findData("number"))
    tab.p_vmin.setText("5")
    tab.p_vmin.editingFinished.emit()
    tab.p_fmt.setCurrentIndex(tab.p_fmt.findData("percent"))
    assert f.vmin == "" and not tab.form.isRowVisible(tab.p_vmin.parentWidget())


def test_repeated_move_goes_back_home_at_the_page_edge(tab):
    f = tab.add_field("text", 0, (10, 700, 100, 720))
    tab.select_many([f])
    tab.copy_selected()
    p1 = tab.paste()[0]
    tab._move([p1], -10, 30)  # 40 below the copied one
    tops = [tab.paste()[0].rect[1] for _ in range(3)]
    assert tops[:2] == pytest.approx([780, 820]) and tops[2] == pytest.approx(700)


def _grid(tmp_path, xs, ys):
    doc = pymupdf.open()
    page = doc.new_page()
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            page.draw_rect((x0, y0, x1, y1))
    doc.save(tmp_path / "grid.pdf")
    t = EditorTab()
    t.open(str(tmp_path / "grid.pdf"))
    return t


def test_paste_goes_right_in_column_table(qapp, tmp_path):
    t = _grid(tmp_path, (100, 150, 200, 250, 300), (100, 130, 160, 190))
    column = [t.add_field("text", 0, (102, y + 2, 148, y + 28)) for y in (100, 130, 160)]
    t.select_many(column)
    t.copy_selected()
    made = t.paste()
    assert [f.rect for f in made] == [pytest.approx((152, y + 2, 198, y + 28)) for y in (100, 130, 160)]
    made = t.paste()
    assert [f.rect[0] for f in made] == [pytest.approx(202)] * 3


def test_paste_wide_field_in_grid_goes_down(qapp, tmp_path):
    t = _grid(tmp_path, (100, 250, 400), (100, 130, 160, 190))
    f = t.add_field("text", 0, (102, 102, 248, 128))
    t.select_many([f])
    t.copy_selected()
    assert t.paste()[0].rect == pytest.approx((102, 132, 248, 158))


def test_paste_nearly_square_checkbox_goes_down(qapp, tmp_path):
    """Detected check boxes are rarely exactly square: one a little taller than wide is still copied down a list."""
    t = _grid(tmp_path, (100, 150, 200, 250), (100, 130, 160, 190))
    f = t.add_field("checkbox", 0, (110, 105, 120, 115.2))
    t.select_many([f])
    t.copy_selected()
    assert t.paste()[0].rect == pytest.approx((110, 135, 120, 145.2))
