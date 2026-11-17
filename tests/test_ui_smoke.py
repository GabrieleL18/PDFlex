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
    assert w.tabs.count() == 2


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
