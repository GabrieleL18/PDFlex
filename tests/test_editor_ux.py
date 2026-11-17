import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pdflex import open_pdf
from pdflex.editor import EditorTab, Field, load_fields, save_with_fields


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
