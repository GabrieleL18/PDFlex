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
    assert w.tabs.count() == 4


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
    assert tab.ignore_case.isChecked() and tab.ignore_punct.isChecked() and not tab.formatting.isChecked()
    tab.ignore_case.setChecked(False)
    tab.ignore_punct.setChecked(False)
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
    tab.resize(1800, 700)  # wide: both columns get the same width
    tab.show()
    tab.load(str(make_pdf(tmp_path, ["uno"])), str(make_pdf(tmp_path, ["due"])))
    from PySide6.QtTest import QTest
    QTest.qWait(300)  # let the layout settle and the resize re-render run
    k = tab.old_view.k
    tab.zoom_by(2)
    assert tab.old_view.k == pytest.approx(2 * k) and tab.new_view.k == pytest.approx(tab.old_view.k, rel=0.01)
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


def test_close_recent_and_zoom_reset(qapp, make_pdf, tmp_path, monkeypatch):
    from pdflex import ui
    tab = EditorTab()
    src = make_pdf(tmp_path, ["uno"])
    tab.open(str(src))
    assert ui.recent_files() == [str(src.resolve())]
    tab.set_zoom(2.3)
    tab.zoom_label.click()
    assert tab.zoom == 1.0
    tab.add_field("text", 0, (10, 10, 100, 30))
    monkeypatch.setattr("pdflex.editor.message", lambda *a, **k: 1)  # "Non salvare"
    assert tab.close_doc() and tab.doc is None and tab.fields == []
    assert tab.recent_lay.count() >= 2  # title + the file


def test_compare_long_docs_render_lazily_and_scroll_sideways_together(qapp, make_pdf, tmp_path):
    from PySide6.QtTest import QTest
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1200, 700)
    tab.show()
    pages = [f"pagina {i}" for i in range(40)]
    tab.load(str(make_pdf(tmp_path, pages)), str(make_pdf(tmp_path, pages[:-1] + ["fine"])))
    QTest.qWait(300)
    assert 0 < len(tab.old_view._rendered) < 10  # only what is on screen
    tab.go_to(0)  # the change is on the last page: it gets drawn when reached
    assert 39 in tab.old_view._rendered
    tab.zoom_by(3)
    QTest.qWait(50)
    a, b = tab.old_view.horizontalScrollBar(), tab.new_view.horizontalScrollBar()
    a.setValue(a.maximum())
    assert b.value() == b.maximum() > 0


def test_blocks_saved_and_reused_in_another_form(qapp, make_pdf, tmp_path):
    tab = EditorTab()
    tab.open(str(make_pdf(tmp_path, ["primo"])))
    a = tab.add_field("text", 0, (100, 100, 250, 120))
    a.name, a.fmt = "importo", "amount"
    b = tab.add_field("text", 0, (100, 130, 250, 150))
    b.name, b.fmt, b.calc = "totale", "amount", ["importo"]
    tab.select_many([a, b])
    tab.save_block("Pagamento")
    tab.dirty = False
    tab.open(str(make_pdf(tmp_path, ["secondo"])))
    assert tab.block_combo.currentText() == "Pagamento"
    first = tab.insert_block("Pagamento")
    second = tab.insert_block("Pagamento")  # twice: names must not clash, sums follow
    assert [f.name for f in first] == ["importo", "totale"]
    assert [f.name for f in second] == ["importo_2", "totale_2"] and second[1].calc == ["importo_2"]
    assert round(first[1].rect[1] - first[0].rect[1]) == 30  # same layout
    tab.undo()
    assert len(tab.fields) == 2


def test_hand_signature_drawn_and_placed(qapp, make_pdf, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath
    from pdflex.editor import SignatureDialog
    tab = EditorTab()
    src = make_pdf(tmp_path, ["contratto"])
    tab.open(str(src))
    d = SignatureDialog(tab)
    path = QPainterPath(QPointF(20, 80))
    for x in range(20, 300, 10):
        path.lineTo(QPointF(x, 80 + (x % 40)))
    d.canvas.paths = [path]
    d._accept()
    assert d.png and d.png.startswith(b"\x89PNG")
    f = tab.add_signature(d.png)
    assert f.kind == "image" and tab.fields == [f]
    out = tmp_path / "firmato.pdf"
    tab.save(str(out))
    page = open_pdf(out)[0]
    assert len(page.get_images()) == 1 and page.first_widget is None  # a picture, not a field
    d2 = SignatureDialog(tab)
    assert d2.b_saved.isVisibleTo(d2)  # remembered for next time


def test_compare_marks_rows_and_page_view(qapp, make_pdf, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1300, 800)
    tab.show()
    pages = ["pagina %d testo" % i for i in range(6)]
    tab.load(str(make_pdf(tmp_path, pages)), str(make_pdf(tmp_path, pages[:4] + ["pagina 4 cambiata"] + pages[5:])))
    QTest.qWait(300)
    marks = tab.new_view.verticalScrollBar().marks
    assert len(marks) == len(tab.changes) == 1 and 0.6 < marks[0][0] < 0.85  # page 5 of 6
    row = tab.list.item(0).data(Qt.UserRole + 1)
    assert "line-through" in row and "testo" in row and "cambiata" in row
    tab.zoom_page()
    view = tab.old_view
    assert view.heights[0] <= view.viewport().height()  # a whole page fits on screen
