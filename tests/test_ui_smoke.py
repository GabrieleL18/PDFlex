import pytest
from pdflex import open_pdf
from pdflex.editor import EditorTab, load_fields


def test_editor_add_and_save(qapp, make_pdf, tmp_path):
    tab = EditorTab()
    src = make_pdf(tmp_path, ["x"])
    tab.open(str(src))
    f = tab.add_field("text", 0, (50, 50, 200, 70))
    assert f.name == "text_1"
    assert tab.add_field("text", 0, (50, 80, 200, 100)).name == "text_2"
    r1 = tab.add_field("radio", 0, (50, 110, 64, 124))
    r2 = tab.add_field("radio", 0, (70, 110, 84, 124))
    assert r1.name == r2.name and r1.value != r2.value
    tab.refresh()
    tab.save(str(src))  # save over the open file
    assert {g.name for g in load_fields(open_pdf(src))} == {"text_1", "text_2", r1.name}


def test_compare_tab(qapp, make_pdf, tmp_path):
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.load(str(make_pdf(tmp_path, ["prezzo 100 euro"])), str(make_pdf(tmp_path, ["prezzo 120 euro"])))
    assert len(tab.changes) == 1
    tab.go_to(0)
    out = tmp_path / "r.pdf"
    tab.export(str(out))
    assert open_pdf(out).page_count == 2


def test_main_window(qapp):
    from pdflex.app import MainWindow
    w = MainWindow()
    assert w.tabs.count() == 3


def test_compare_scrolls_together(qapp, make_pdf, tmp_path):
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1200, 700)
    tab.show()
    pages = ["pagina %d testo" % i for i in range(4)]
    tab.load(str(make_pdf(tmp_path, pages)), str(make_pdf(tmp_path, pages[:2] + ["nuovo"] + pages[2:])))
    from PySide6.QtTest import QTest
    QTest.qWait(300)  # let the views re-render at their real width
    assert tab.old_view.verticalScrollBar().maximum() > 0
    tab.old_view.scroll_to(2, 0.0)
    page, _ = tab.new_view.position()
    assert page == 2
    tab.sync.setChecked(False)
    tab.old_view.scroll_to(0, 0.0)
    assert tab.new_view.position()[0] == 2


def test_editor_undo_redo_align_order_detect(qapp, make_pdf, tmp_path):
    tab = EditorTab()
    src = make_pdf(tmp_path, ["Nome: ____________________"])
    tab.open(str(src))
    a = tab.add_field("text", 0, (10, 10, 100, 30))
    b = tab.add_field("text", 0, (40, 60, 90, 80))
    assert len(tab.fields) == 2
    tab.undo()
    assert [f.name for f in tab.fields] == ["text_1"]
    tab.redo()
    assert [f.name for f in tab.fields] == ["text_1", "text_2"]
    a, b = tab.fields
    tab.select_many([a, b])
    tab.align("left")
    assert tab.fields[0].rect[0] == tab.fields[1].rect[0] == 10
    tab.undo()
    assert tab.fields[1].rect[0] == 40
    tab.select_many(tab.fields)
    tab.delete_selected()
    assert tab.fields == []
    tab.undo()
    tab.set_order(list(reversed(tab.fields)))
    assert [f.name for f in tab.fields] == ["text_2", "text_1"]
    n = tab.detect()
    assert n == 1 and tab.fields[-1].name == "nome"


def test_compare_options_filter_excel(qapp, make_pdf, tmp_path):
    import openpyxl
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.load(str(make_pdf(tmp_path, ["Il Prezzo resta 100 euro."])), str(make_pdf(tmp_path, ["il prezzo resta 120 euro"])))
    assert len(tab.changes) == 2
    tab.ignore_case.setChecked(True)
    assert len(tab.changes) == 1
    tab.ignore_case.setChecked(False)
    tab.ignore_punct.setChecked(True)
    assert len(tab.changes) == 2
    tab.load(str(make_pdf(tmp_path, ["uno due tre quattro"])), str(make_pdf(tmp_path, ["uno DUE tre quattro cinque"])))
    tab.ignore_punct.setChecked(False)
    assert [c.kind for c in tab.changes] == ["replace", "insert"]
    tab.kind_filter.setCurrentIndex(tab.kind_filter.findData("insert"))
    assert tab.list.isRowHidden(0) and not tab.list.isRowHidden(1) and tab.current == 1
    out = tmp_path / "m.xlsx"
    tab.export_xlsx(str(out))
    rows = list(openpyxl.load_workbook(out).active.values)
    assert len(rows) == 3 and rows[2][1] == "Aggiunto" and rows[2][5] == "cinque"


def test_batch_tab(qapp, make_pdf, tmp_path):
    from pdflex.batch import BatchTab
    from pdflex.editor import Field, save_with_fields
    tpl = tmp_path / "modulo.pdf"
    save_with_fields(make_pdf(tmp_path, ["Modulo"]), [Field("text", 0, (10, 10, 200, 30), "nome")], tpl)
    data = tmp_path / "dati.csv"
    data.write_text("Nome;Altro\nMario;x\nAnna;y\n", encoding="utf-8")
    tab = BatchTab()
    tab.set_template(str(tpl))
    tab.set_table(str(data))
    assert tab.mapping == {"nome": "Nome"} and tab.b_run.isEnabled()
    tab.name_col.setCurrentText("Nome")
    out = tab.run(str(tmp_path / "out"))
    assert sorted(p.name for p in out) == ["Anna.pdf", "Mario.pdf"]


def test_open_from_command_line(qapp, make_pdf, tmp_path):
    from pdflex.app import MainWindow
    w = MainWindow(str(make_pdf(tmp_path, ["ciao"])))
    assert w.editor.doc is not None and w.tabs.currentIndex() == 0


def test_compare_zoom_both_sides(qapp, make_pdf, tmp_path):
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1200, 700)
    tab.show()
    tab.load(str(make_pdf(tmp_path, ["uno"])), str(make_pdf(tmp_path, ["due"])))
    from PySide6.QtTest import QTest
    QTest.qWait(300)  # let the layout settle and the resize re-render run
    k = tab.old_view.k
    tab.zoom_by(2)
    assert tab.old_view.k == tab.new_view.k == pytest.approx(2 * k)
    assert tab.zoom_label.text() == "200%"
    tab.zoom_fit()
    assert tab.old_view.k == pytest.approx(k)
    tab.include_list.setChecked(False)
    out = tmp_path / "c.pdf"
    tab.export(str(out))
    assert open_pdf(out).page_count == 1


def test_errors_are_logged(tmp_path):
    from pdflex.app import log_error
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        text = log_error(type(e), e, e.__traceback__, tmp_path / "errori.log")
    assert "boom" in text and "boom" in (tmp_path / "errori.log").read_text(encoding="utf-8")
