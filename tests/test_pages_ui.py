from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest


def _drop(grid, pos):
    grid.dragging = True  # as during a drag started on the grid
    mime = QMimeData()  # kept in a variable: Qt does not own it, Python would free it at once
    ev = QDropEvent(QPointF(pos), Qt.MoveAction | Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    grid.dropEvent(ev)
    grid.dragging = False
    return ev


def test_drag_reorders_pages(qapp, make_pdf, tmp_path):
    from pdflex.pages import PagesTab
    tab = PagesTab()
    tab.resize(1000, 600)
    tab.show()
    tab.add_files([str(make_pdf(tmp_path, ["a", "b", "c", "d"]))])
    g = tab.grid
    assert g.dragEnabled() and g.viewport().acceptDrops()  # a real drag lands on the viewport
    g.item(0).setSelected(True)
    last = g.visualItemRect(g.item(3))
    ev = _drop(g, QPoint(last.right() - 3, last.center().y()))  # right half of the last page: after it
    order = [r.index for r in tab.refs()]
    assert order == [1, 2, 3, 0]
    action = ev.dropAction()
    assert action == Qt.CopyAction  # never "move": Qt would then delete the dragged pages
    g.clearSelection()
    for i in (2, 3):  # the last two, dropped on the left half of the first page: before it
        g.item(i).setSelected(True)
    first = g.visualItemRect(g.item(0))
    _drop(g, QPoint(first.left() + 3, first.center().y()))
    assert [r.index for r in tab.refs()] == [3, 0, 1, 2]
    assert g.count() == 4


def test_drop_from_outside_is_left_to_the_tab(qapp, make_pdf, tmp_path):
    from pdflex.pages import PagesTab
    tab = PagesTab()
    tab.add_files([str(make_pdf(tmp_path, ["a", "b"]))])
    mime = QMimeData()
    ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    tab.grid.dropEvent(ev)
    assert not ev.isAccepted() and tab.grid.count() == 2


def test_undo_redo_pages(qapp, make_pdf, tmp_path):
    from pdflex.pages import PagesTab
    tab = PagesTab()
    tab.add_files([str(make_pdf(tmp_path, ["a", "b", "c"]))])
    order = lambda: [(r.index, r.rotation) for r in tab.refs()]
    tab.grid.item(0).setSelected(True)
    tab.rotate(90)
    tab.delete_selected()
    assert order() == [(1, 0), (2, 0)]
    tab.undo()
    assert order() == [(0, 90), (1, 0), (2, 0)]
    tab.undo()
    assert order() == [(0, 0), (1, 0), (2, 0)]  # the rotation, undone on its own
    tab.undo()
    assert order() == []  # adding the file
    tab.redo()
    tab.redo()
    assert order() == [(0, 90), (1, 0), (2, 0)] and tab.b_redo.isEnabled()


def test_pdf_dropped_between_thumbnails(qapp, make_pdf, tmp_path):
    from PySide6.QtCore import QUrl
    from pdflex.pages import PagesTab
    tab = PagesTab()
    tab.resize(1000, 600)
    tab.show()
    first, other = make_pdf(tmp_path, ["a", "b"]), make_pdf(tmp_path, ["X"])
    tab.add_files([str(first)])
    g = tab.grid
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(other))])
    r = g.visualItemRect(g.item(0))
    ev = QDropEvent(QPointF(r.right() - 3, r.center().y()), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    g.dropEvent(ev)  # right half of the first page: goes after it
    from pathlib import Path
    assert [(Path(r.path) == other, r.index) for r in tab.refs()] == [(False, 0), (True, 0), (False, 1)]


def test_ctrl_z_belongs_to_the_tab_on_screen(qapp, make_pdf, tmp_path):
    from pdflex.app import MainWindow
    w = MainWindow()
    w.show()
    w.editor.open(str(make_pdf(tmp_path, ["x"])))
    w.editor.add_field("text", 0, (10, 10, 100, 30))
    w.tabs.setCurrentWidget(w.pages)
    w.pages.add_files([str(make_pdf(tmp_path, ["a"]))])
    w.activateWindow()
    QTest.qWaitForWindowActive(w)
    QTest.keyClick(w.pages.grid, Qt.Key_Z, Qt.ControlModifier)
    assert w.pages.grid.count() == 0 and len(w.editor.fields) == 1


def test_right_click_moves_shows_and_deletes(qapp, make_pdf, tmp_path):
    from pdflex.pages import PagesTab
    tab = PagesTab()
    tab.resize(1000, 600)
    tab.show()
    tab.add_files([str(make_pdf(tmp_path, ["a", "b", "c", "d"]))])
    g = tab.grid
    order = lambda: [r.index for r in tab.refs()]
    menu = tab.page_menu(g.item(1))  # right click on page 2: it alone is selected
    shown = {a.text(): a for a in menu.actions() if a.text()}
    assert [i.text() for i in g.selectedItems()] == [g.item(1).text()]
    assert set(shown) == {"Sposta indietro", "Sposta avanti", "Sposta all'inizio", "Sposta alla fine",
                          "Ruota a destra	Ctrl+R", "Ruota a sinistra	Ctrl+Maiusc+R", "Capovolgi",
                          "Vedi la pagina", "Elimina"}
    shown["Ruota a destra	Ctrl+R"].trigger()
    shown["Capovolgi"].trigger()
    assert tab.refs()[1].rotation == 270
    tab.undo()
    tab.undo()
    menu = tab.page_menu(g.item(1))
    shown = {a.text(): a for a in menu.actions() if a.text()}
    shown["Sposta avanti"].trigger()
    assert order() == [0, 2, 1, 3]
    shown["Sposta indietro"].trigger()
    shown["Sposta indietro"].trigger()
    assert order() == [1, 0, 2, 3]
    tab.shift(-5)  # already first: nothing, not even an undo step
    shown["Sposta alla fine"].trigger()
    assert order() == [0, 2, 3, 1]
    tab.undo()
    assert order() == [1, 0, 2, 3]
    delete = next(a for a in tab.page_menu(g.item(1)).actions() if a.text() == "Elimina")  # undo rebuilt the grid
    delete.trigger()
    assert order() == [1, 2, 3]
