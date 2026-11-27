import pytest

from pdflex import blocks as bl


def B(**kw):
    return bl.Block((100, 100, 400, 220), [0.25] * 4, [0.25] * 4, **kw)


def test_cells_and_merge():
    b = bl.merge(B(), [(0, 1), (0, 2)])
    assert b.merges == [(0, 1, 0, 2)]
    cs = bl.cells(b)
    assert len(cs) == 15 and cs[1][:4] == (0, 1, 0, 2) and cs[1][4] == pytest.approx((175, 100, 325, 130))
    assert bl.merge(B(), [(0, 0), (1, 1)]) is None  # not a rectangle
    assert bl.split(b, 0, 1).merges == []


def test_set_count_splits_last_and_trims():
    b = bl.set_count(B(texts={"3,3": "x", "0,0": "a"}), "cols", 5)
    assert b.cols == [0.25, 0.25, 0.25, 0.125, 0.125]
    b = bl.set_count(b, "cols", 2)
    assert b.cols == [0.25, 0.75] and b.texts == {"0,0": "a"}  # text of a removed column goes


def test_set_count_clips_merges():
    b = bl.merge(B(), [(0, 1), (0, 2), (0, 3)])
    assert bl.set_count(b, "cols", 3).merges == [(0, 1, 0, 2)]
    assert bl.set_count(b, "cols", 2).merges == []  # one cell left: no merge


def test_move_divider_and_min_size():
    b = bl.move_divider(B(), "cols", 0, 130)  # 100 + 30
    assert b.cols[0] == pytest.approx(0.1) and b.cols[1] == pytest.approx(0.4)
    b = bl.move_divider(B(), "cols", 0, 90)  # past the edge: cell kept >= 4 pt
    assert b.cols[0] * 300 == pytest.approx(4) and sum(b.cols) == pytest.approx(1)


def test_cell_at_merged_and_json_roundtrip():
    b = bl.merge(B(texts={"0,1": "TOTALE"}), [(0, 1), (0, 2)])
    assert bl.cell_at(b, 300, 110) == (0, 1) and bl.cell_at(b, 50, 50) is None
    assert bl.from_json(bl.to_json(b)) == b


def test_scaled_keeps_fractions():
    b = bl.scaled(B(), (0, 0, 600, 240))
    assert b.rect == (0, 0, 600, 240) and b.cols == [0.25] * 4


def test_presets():
    r = (0, 0, 300, 150)
    assert bl.preset("rettangolo", r) is None
    assert [p for p, _ in bl.PRESETS] == ["rettangolo", "riquadro", "tabella", "griglia", "sezione", "totali"]
    t = bl.preset("tabella", r)
    assert t.header_row and len(t.cols) == 4 and len(t.rows) == 6  # header + 5
    assert t.rows[0] * 150 == pytest.approx(18) and sum(t.rows) == pytest.approx(1)
    g = bl.preset("griglia", r)
    assert g.header_row and g.header_col and len(g.cols) == 9 and len(g.rows) == 5
    assert g.cols[0] * 300 == pytest.approx(70)
    assert bl.preset("riquadro", r).texts == {"0,0": "Titolo"} and bl.preset("sezione", r).border == ""
