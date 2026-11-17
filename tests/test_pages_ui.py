from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent


def _drop(grid, pos):
    grid.dragging = True  # as during a drag started on the grid
    ev = QDropEvent(QPointF(pos), Qt.MoveAction | Qt.CopyAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)
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
    ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)
    tab.grid.dropEvent(ev)
    assert not ev.isAccepted() and tab.grid.count() == 2
