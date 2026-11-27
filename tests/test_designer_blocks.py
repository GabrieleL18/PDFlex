import pymupdf
import pytest

from pdflex import blocks as bl
from pdflex import design, ui
from pdflex.designer import DesignTab, _marks


def _tab(tmp_path, pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    path = tmp_path / "vuoto.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    return tab


def _block(tmp_path, pid, pages=1):
    tab = _tab(tmp_path, pages)
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    tab.use_preset(pid)
    return tab


def test_draw_rect_then_preset_makes_a_block(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.set_mode("rect")
    tab.drawn((100, 100, 400, 250))
    tab.use_preset("tabella")
    assert len(tab.blocks) == 1 and tab.selection and all(tab.block_of(o) for o in tab.selection)
    assert len(tab._undo) == 2  # rectangle, then block: two steps
    assert tab.blocks[0][1].rect == pytest.approx((100, 100, 400, 250), abs=0.5)


def test_block_edit_undo_restores_note(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    entry = tab.blocks[0]
    tab.apply_block(entry, bl.set_count(entry[1], "rows", 8))
    assert len(tab.blocks[0][1].rows) == 8
    tab.undo()
    assert len(tab.blocks) == 1 and len(tab.blocks[0][1].rows) == 6


def test_block_follows_move_and_resize(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    tab.select(tab.blocks[0][2])
    tab.move_selected(20, 30)
    assert tab.blocks[0][1].rect == pytest.approx((120, 130, 420, 280), abs=1)
    tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "rows", 7))
    assert tab.blocks[0][1].rect == pytest.approx((120, 130, 420, 280), abs=1)
    assert len(tab.blocks[0][1].rows) == 7


def test_block_copy_paste_other_page(qapp, tmp_path):
    tab = _block(tmp_path, "griglia", pages=2)
    tab.select(tab.blocks[0][2])
    tab.copy_selected()
    tab.go_page(1)
    tab.paste()
    assert len(tab.blocks) == 1
    tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "cols", 3))
    assert len(tab.blocks[0][1].cols) == 3
    tab.go_page(0)
    assert len(tab.blocks[0][1].cols) == 9


def test_duplicate_block(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.select(tab.blocks[0][2])
    tab.duplicate_selected()
    assert len(tab.blocks) == 2


def test_broken_block_is_loose(qapp, tmp_path):
    tab = _block(tmp_path, "tabella")
    line = next(o for o in tab.blocks[0][2] if o.kind == "line")
    tab._edit(design.delete, [line])  # as if changed by another program
    assert tab.blocks == []


def test_delete_block_and_rotate_asks(qapp, tmp_path, monkeypatch):
    tab = _block(tmp_path, "riquadro")
    monkeypatch.setattr(ui, "message", lambda *a, **k: 1)  # Annulla
    tab.select(tab.blocks[0][2])
    tab.rotate_selected(90)
    assert len(tab.blocks) == 1
    tab.select(tab.blocks[0][2])
    tab.delete_selected()
    assert tab.blocks == [] and _marks(tab.doc).get("blocks", {}).get("0", []) == []


def test_rotate_makes_loose_shapes(qapp, tmp_path, monkeypatch):
    tab = _block(tmp_path, "riquadro")
    monkeypatch.setattr(ui, "message", lambda *a, **k: 0)  # Continua
    tab.select(tab.blocks[0][2])
    tab.rotate_selected(90)
    assert tab.blocks == [] and len(tab.objects) >= 2


def test_find_replace_changes_cell_text(qapp, tmp_path):
    tab = _block(tmp_path, "riquadro")
    tab.replace_all("Titolo", "Presenze")
    assert tab.blocks[0][1].texts["0,0"] == "Presenze" and "Presenze" in tab.doc[0].get_text()
    assert "Titolo" not in tab.doc[0].get_text()


def test_block_survives_save_and_reopen(qapp, tmp_path):
    tab = _block(tmp_path, "griglia")
    out = tmp_path / "salvato.pdf"
    tab.save(str(out))
    tab2 = DesignTab()
    tab2.open(str(out))
    assert len(tab2.blocks) == 1 and tab2.blocks[0][1].header_col
