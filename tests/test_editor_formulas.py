import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from pdflex import open_pdf
from pdflex.editor import EditorTab, FieldItem
from pdflex.fields import Field, load_fields, save_with_fields


@pytest.fixture
def tab(qapp, make_pdf, tmp_path):
    t = EditorTab()
    t.resize(1300, 800)
    t.show()
    t.open(str(make_pdf(tmp_path, ["uno"])))
    QTest.qWait(50)
    return t


def _type(box, text):
    box.setText(text)
    box.editingFinished.emit()


def test_formula_row_and_validation(tab):
    f = tab.add_field("text", 0, (10, 10, 200, 30))
    c = tab.add_field("checkbox", 0, (10, 50, 24, 64))
    tab.select_many([c])
    assert not tab.form.isRowVisible(tab._row_of["formula"]) and tab.form.isRowVisible(tab._row_of["show_if"])
    tab.select_many([f])
    assert tab.form.isRowVisible(tab._row_of["formula"])
    _type(tab.p_formula, "[Nessuno]+1")
    assert f.formula == "[Nessuno]+1"
    assert "Nessuno" in tab.p_formula_err.text() and not tab.p_formula_err.isHidden()
    _type(tab.p_formula, f"[{c.name}] * 2")
    assert tab.p_formula_err.isHidden()
    assert not tab.form.isRowVisible(tab._row_of["calc"])  # a formula replaces the quick total


def test_rename_rewrites_formulas_and_totals(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("text", 0, (10, 40, 100, 60))
    c = tab.add_field("text", 0, (10, 70, 100, 90))
    b.formula, c.calc, c.show_if = f"[{a.name}]*2", [a.name], f"[{a.name}] > 1"
    old = a.name
    tab.select_many([a])
    _type(tab.p_name, "Quantità")
    assert b.formula == "[Quantità]*2" and c.calc == ["Quantità"] and c.show_if == "[Quantità] > 1"
    tab.undo()
    assert tab.fields[1].formula == f"[{old}]*2"


def test_multi_select_look(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("text", 0, (10, 40, 100, 60))
    b.border_style = "dashed"
    tab.select_many([a, b])
    assert tab.p_bstyle.currentText() == "(diversi)"
    tab._show_color("#ffeecc", tab.p_fill)
    tab._set_prop("fill_color")
    assert a.fill_color == b.fill_color == "#ffeecc"
    tab.p_bstyle.setCurrentIndex(tab.p_bstyle.findData("underline"))
    assert a.border_style == b.border_style == "underline"
    tab.p_bwidth.setCurrentIndex(tab.p_bwidth.findData(3))
    assert a.border_width == b.border_width == 3
    tab.set_color_choice(tab.p_border, "border_color", "none")
    assert a.border_color == b.border_color == "none" and tab.p_border.text() == "Nessuno"


def test_click_inserts_name(tab):
    f = tab.add_field("text", 0, (10, 10, 200, 30))
    p = tab.add_field("text", 0, (10, 100, 200, 120))
    p.name = "Prezzo"
    tab.refresh()
    tab.select_many([f])
    tab.p_formula.setText("2 * ")
    tab.p_formula.setCursorPosition(4)
    tab._insert_into = tab.p_formula
    item = next(i for i in tab.scene.items() if isinstance(i, FieldItem) and i.field is p)
    pos = tab.view.mapFromScene(item.mapToScene(item.rect().center()))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    assert tab.p_formula.text() == "2 * [Prezzo]"
    assert tab.selection == [f] and p.rect == (10, 100, 200, 120)


def test_show_if_on_radio_group_copies(tab):
    r1 = tab.add_field("radio", 0, (10, 10, 24, 24))
    r2 = tab.add_field("radio", 0, (10, 40, 24, 54))
    r2.name = r1.name
    c = tab.add_field("checkbox", 0, (10, 80, 24, 94))
    tab.select_many([r1])
    _type(tab.p_show_if, f"[{c.name}]")
    assert r1.show_if == r2.show_if == f"[{c.name}]"


def test_foreign_calc_disabled(tab, make_pdf, tmp_path):
    src, out = make_pdf(tmp_path, ["x"]), tmp_path / "f.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "x")], out)
    doc = open_pdf(out)
    w = next(doc[0].widgets())
    doc.xref_set_key(w.xref, "AA", "<</C <</S/JavaScript/JS (event.value = 42;)>>>>")
    out2 = tmp_path / "f2.pdf"
    doc.save(out2)
    tab.open(str(out2))
    tab.select_many([tab.fields[0]])
    assert tab.form.isRowVisible(tab._row_of["formula"]) and not tab.p_formula.isEnabled()
    assert tab.p_formula.placeholderText() == "Script da un altro programma"


def test_look_painted_and_details(tab):
    f = tab.add_field("text", 0, (10, 10, 200, 40))
    f.formula, f.show_if = "1+1", "1=1"
    tab.refresh()
    item = next(i for i in tab.scene.items() if isinstance(i, FieldItem) and i.field is f)
    assert "formula" in item.details() and "a condizione" in item.details()
    f.fill_color, f.border_style = "#ffeecc", "dashed"
    item.update()
    QTest.qWait(20)  # painting with a look must not fail
