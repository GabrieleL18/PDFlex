import pytest
from pdflex import open_pdf
from pdflex.editor import EditorTab
from pdflex.fields import load_fields


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
    assert w.tabs.count() == 5 and w.tabs.currentWidget() is w.editor


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
    for kind, b in tab.kind_buttons.items():  # only the additions
        b.setChecked(kind == "insert")
    assert tab.list.isRowHidden(0) and not tab.list.isRowHidden(1) and tab.current == 1
    out = tmp_path / "m.xlsx"
    tab.export_xlsx(str(out))
    rows = list(openpyxl.load_workbook(out).active.values)
    assert len(rows) == 3 and rows[2][1] == "Aggiunto" and rows[2][5] == "cinque"


def test_batch_tab(qapp, make_pdf, tmp_path):
    from pdflex.batch import BatchTab
    from pdflex.fields import Field, save_with_fields
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
    assert w.editor.doc is not None and w.tabs.currentWidget() is w.editor


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
    assert tab.recent_box.lay.count() >= 2  # title + the file


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
    from pdflex.signature import SignatureDialog
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
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1300, 800)
    tab.show()
    pages = ["pagina %d testo" % i for i in range(6)]
    tab.load(str(make_pdf(tmp_path, pages)), str(make_pdf(tmp_path, pages[:4] + ["pagina 4 cambiata"] + pages[5:])))
    QTest.qWait(300)
    ticks = tab.bar.ticks  # the one scrollbar between the two documents
    assert len(ticks) == len(tab.changes) == 1 and 0.6 < ticks[0][0] < 0.85  # page 5 of 6
    assert tab.old_view.verticalScrollBar().isVisible() and tab.new_view.verticalScrollBar().isVisible()  # each its own too
    # a click on the tick selects the change, dragging the bar scrolls both documents
    tab.list.setCurrentRow(-1)
    tab.current = -1
    QTest.mouseClick(tab.bar, Qt.LeftButton, pos=tab.bar.rect().topLeft() + QPoint(9, round(tab.bar._y(ticks[0][0]))))
    assert tab.current == 0 and tab.bar.current == 0
    tab.bar.setValue(0)
    assert tab.old_view.verticalScrollBar().value() == 0 and tab.new_view.position()[0] == 0
    tab.bar.setValue(tab.bar.maximum())
    assert tab.new_view.position()[0] == tab.old_view.position()[0] >= 4  # the new version followed to the end
    row = tab.list.item(0).data(Qt.UserRole + 1)
    assert "line-through" in row and "testo" in row and "cambiata" in row
    tab.zoom_page()
    view = tab.old_view
    assert view.heights[0] <= view.viewport().height()  # a whole page fits on screen


def test_run_busy_returns_result_and_errors(qapp):
    from pdflex import ui
    assert ui.run_busy(None, "x", lambda: sum(range(10))) == 45
    with pytest.raises(ZeroDivisionError):
        ui.run_busy(None, "x", lambda: 1 / 0)


def test_window_remembers_its_place_and_stays_on_screen(qapp):
    from pdflex import ui
    from pdflex.app import MainWindow
    w = MainWindow()
    w.show()
    w.close()
    assert ui.load_json("finestra.json", {}).get("geometria")
    w2 = MainWindow()  # saved larger than this test screen, as from a big monitor to a laptop
    assert qapp.primaryScreen().availableGeometry().contains(w2.frameGeometry())


def test_dark_theme_switch_and_remember(qapp):
    from PySide6.QtGui import QPalette
    from pdflex import ui
    from pdflex.app import MainWindow
    w = MainWindow()
    before = w.editor.b_open.icon().pixmap(24, 24).toImage()
    try:
        w.toggle_theme()
        assert ui.THEME == "dark" and ui.preferred_theme() == "dark"  # remembered
        assert qapp.palette().color(QPalette.Window).lightness() < 80
        assert w.editor.b_open.icon().pixmap(24, 24).toImage() != before  # icons redrawn in the light ink
        assert w.b_theme.text() == "Chiaro"
        w.toggle_theme()
        assert ui.THEME == "light" and w.b_theme.text() == "Scuro"
    finally:
        ui.apply_theme(qapp, "light")


def test_compare_middle_resize_moves_both(qapp, make_pdf, tmp_path):
    from PySide6.QtTest import QTest
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1300, 800)
    tab.show()
    tab.load(str(make_pdf(tmp_path, ["a"])), str(make_pdf(tmp_path, ["b"])))
    QTest.qWait(100)
    pair = tab.pair
    pair.parent().setSizes([1000, 300])  # room for the documents, whatever the side panel asked for
    QTest.qWait(50)
    assert pair.count() == 2 and tab.bar.parent() is not None and pair.handle(1).isAncestorOf(tab.bar)
    old, new = pair.sizes()
    pair.moveSplitter(pair.handle(1).x() + 120, 1)  # one divider: one PDF grows, the other shrinks
    o, n = pair.sizes()
    assert o > old and n < new and o + n == old + new


def test_editor_hands_its_pdf_to_moduli(qapp, make_pdf, tmp_path):
    from pdflex.app import MainWindow
    w = MainWindow()
    path = str(make_pdf(tmp_path, ["ciao"]))
    w.rail[w.design].click()
    assert w.tabs.currentWidget() is w.design
    w.design.open(path)
    w.design.b_forms.click()
    assert w.tabs.currentWidget() is w.editor and w.editor.path == path and w.rail[w.editor].isChecked()


def test_keys_window_and_f1_of_the_tab_shown(qapp, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from pdflex import ui
    from pdflex.app import MainWindow
    d = ui.keys_dialog(None, [("Modificare", [("Ctrl+C / Ctrl+V", "copia e incolla")])])
    assert [l.text() for l in d.findChildren(QLabel) if l.objectName() == "Key"] == ["Ctrl", "C", "Ctrl", "V"]
    shown = []
    monkeypatch.setattr(ui, "show_keys", lambda parent, groups: shown.append(groups[0][1][0][1]))
    w = MainWindow()
    w.show()
    for tab, first in ((w.design, "un oggetto"), (w.editor, "un campo")):
        w.tabs.setCurrentWidget(tab)
        qapp.processEvents()
        QTest.keyClick(w, Qt.Key_F1)
        assert shown[-1] == first
    w.close()


def test_compare_click_finds_counterpart(qapp, make_pdf, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1200, 700)
    tab.show()
    tab.load(str(make_pdf(tmp_path, ["prezzo 100 euro\ncontratto annuale"])),
             str(make_pdf(tmp_path, ["premessa nuova\nprezzo 120 euro\ncontratto annuale"])))
    QTest.qWait(300)
    v = tab.old_view

    def click(text):
        w = next(w for w in tab.words[0] if w.text == text)
        x, y, wd, h = v.word_rect(w)
        QTest.mouseClick(v.viewport(), Qt.LeftButton, pos=v.mapFromScene(x + wd / 2, y + h / 2))
    click("contratto")  # unchanged: outlined on both sides
    assert len(v._outlined) == 1 and len(tab.new_view._outlined) == 1
    tab.go_to(0)
    click("100")  # changed: its change gets selected
    assert "100" in tab.changes[tab.current].label() and not tab.new_view._outlined


def test_compare_search_same_scale_moves_and_page_labels(qapp, make_pdf, tmp_path):
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1200, 700)
    tab.show()
    tab.load(str(make_pdf(tmp_path, ["alfa beta gamma\ndelta prezzo", "seconda pagina"])),
             str(make_pdf(tmp_path, ["delta prezzo\nalfa beta gamma", "seconda pagina nuova"])))
    assert abs(tab.old_view.k / tab.new_view.k - 1) < 0.01  # one scale for both (columns may differ by a pixel)
    assert "pagina 1 di 2" in tab.old_label.text()
    assert any(c.kind == "move" for c in tab.changes)
    tab.moves.setChecked(False)  # a move becomes a removal and an addition
    assert not any(c.kind == "move" for c in tab.changes)
    tab.search.setText("gamma")
    tab.find_text(1)
    assert tab.found.text() == "1 di 1"  # unchanged in both: found once, outlined on both sides
    assert tab.old_view._outlined and tab.new_view._outlined
    tab.search.setText("nuova")  # only in the new version
    tab.find_text(1)
    assert tab.found.text() == "1 di 1" and tab.hits[0][0] == 1
    tab.search.setText("zzz")
    tab.find_text(1)
    assert tab.found.text() == "Nessun risultato"


def test_compare_fit_same_size_side_zoom_and_hover(qapp, make_pdf, tmp_path):
    from PySide6.QtTest import QTest
    from pdflex.compare import CompareTab
    tab = CompareTab()
    tab.resize(1400, 700)
    tab.show()
    tab.load(str(make_pdf(tmp_path, ["prezzo 100 euro\ncontratto annuale"])),
             str(make_pdf(tmp_path, ["prezzo 120 euro\ncontratto annuale"])))
    tab.pair.setSizes([700, 300])  # columns of different widths: same pages, still the same size
    QTest.qWait(400)
    old, new = tab.old_view, tab.new_view
    assert old.k == new.k
    tab.side_zoom[new].click()  # "fit" on one side: nothing changes
    assert old.k == new.k
    plus = [b for b in new.parent().findChildren(type(tab.b_zin)) if "Ingrandisci" in b.toolTip()][0]
    plus.click()  # only the new version grows
    assert old.scale == 1.0 and new.scale == 1.25 and tab.zoom_label.text() == "100% · 125%"
    tab.zoom_fit()
    assert old.k == new.k and tab.zoom_label.text() == "100%"
    QTest.qWait(300)  # let pending re-renders run: they would drop the outline
    w = next(w for w in tab.words[0] if w.text == "contratto")
    x, y, wd, h = old.word_rect(w)
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    at = QPointF(old.mapFromScene(x + wd / 2, y + h / 2))  # sent straight to it: other tests' windows linger
    qapp.sendEvent(old.viewport(), QMouseEvent(QEvent.MouseMove, at, old.viewport().mapToGlobal(at),
                                               Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    assert len(old._hovering) == 1 and len(new._hovering) == 1  # it and its counterpart
    assert not old._outlined  # hovering is not clicking


def test_compare_remembers_last_15(qapp, make_pdf, tmp_path):
    from pathlib import Path
    from pdflex.compare import CompareTab, recent_comparisons
    tab = CompareTab()
    pdfs =[str(make_pdf(tmp_path, [f"testo {i}"])) for i in range(17)]
    for i in range(16):
        tab.load(pdfs[i], pdfs[i + 1])
    tab.formatting.setChecked(True)  # read again: the last one is remembered with it
    saved = recent_comparisons()
    assert len(saved) == 15 and saved[0]["new"].endswith(Path(pdfs[16]).name)
    assert saved[0]["options"]["formatting"] and not saved[1]["options"]["formatting"]
    tab.reset()
    assert tab.recent.count() == 15 and tab.recent.isVisibleTo(tab)
    tab.reopen(saved[1])  # opens it again, with its options
    assert tab.stack.currentIndex() == 1 and not tab.formatting.isChecked()
    assert Path(tab.new_path).name == Path(pdfs[15]).name
    Path(pdfs[1]).unlink()  # in the oldest one kept (the first comparison was dropped: 16 made, 15 kept)
    assert len(recent_comparisons()) == 14  # a file gone: left out
