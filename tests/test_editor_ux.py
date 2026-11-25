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
    tab._props_edited()
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
