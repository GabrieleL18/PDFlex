from pdflex import open_pdf
from pdflex.pages import PageRef, assemble, split


def test_merge_reorder_rotate(make_pdf, tmp_path):
    a = make_pdf(tmp_path, ["A1", "A2"])
    b = make_pdf(tmp_path, ["B1"])
    out = tmp_path / "unito.pdf"
    assemble([PageRef(str(b), 0), PageRef(str(a), 1, 90), PageRef(str(a), 0)], out)
    doc = open_pdf(out)
    assert [p.get_text().strip() for p in doc] == ["B1", "A2", "A1"]
    assert [p.rotation for p in doc] == [0, 90, 0]


def test_split_one_file_per_page(make_pdf, tmp_path):
    a = make_pdf(tmp_path, ["uno", "due", "tre"])
    files = split([PageRef(str(a), i) for i in range(3)], tmp_path / "pezzi", "contratto")
    assert [f.name for f in files] == ["contratto_p1.pdf", "contratto_p2.pdf", "contratto_p3.pdf"]
    assert open_pdf(files[1])[0].get_text().strip() == "due"


def test_pages_tab(qapp, make_pdf, tmp_path):
    from pdflex.pages import PagesTab
    tab = PagesTab()
    tab.add_files([str(make_pdf(tmp_path, ["A1", "A2"])), str(make_pdf(tmp_path, ["B1"]))])
    assert tab.grid.count() == 3
    tab.grid.item(0).setSelected(True)
    tab.rotate(90)
    tab.grid.clearSelection()
    tab.grid.item(1).setSelected(True)
    tab.delete_selected()
    assert [r.index for r in tab.refs()] == [0, 0] and tab.refs()[0].rotation == 90
    out = tmp_path / "out.pdf"
    tab.save(str(out))
    assert [p.get_text().strip() for p in open_pdf(out)] == ["A1", "B1"]


def test_save_over_a_source_file(make_pdf, tmp_path):
    a = make_pdf(tmp_path, ["uno", "due", "tre"])
    assemble([PageRef(str(a), 2), PageRef(str(a), 0)], a)
    assert [p.get_text().strip() for p in open_pdf(a)] == ["tre", "uno"]
    assert not list(tmp_path.glob("*.pdflex-tmp"))
