import pymupdf
import pytest

from pdflex import open_pdf
from pdflex.editor import Field, align_rects, load_fields, save_with_fields, snap


def _js(doc):
    out = []
    for x in range(1, doc.xref_length()):
        try:
            out.append(doc.xref_object(x))
            s = doc.xref_stream(x)
            if s:
                out.append(s.decode("latin-1"))
        except Exception:
            pass
    return "\n".join(out)


# formats and sums
def test_formats_and_sum_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 10, 100, 30), "a", fmt="amount"),
              Field("text", 0, (10, 40, 100, 60), "b", fmt="number"),
              Field("text", 0, (10, 70, 100, 90), "mail", fmt="email"),
              Field("text", 0, (10, 100, 100, 120), "tot", fmt="amount", calc=["a", "b"])]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    js = _js(doc)
    assert "AFNumber_Format" in js and "AFSimple_Calculate" in js and "app.alert" in js
    assert "/CO" in doc.xref_get_key(doc.pdf_catalog(), "AcroForm")[1]
    got = {f.name: f for f in load_fields(doc)}
    assert got["a"].fmt == "amount" and got["b"].fmt == "number" and got["mail"].fmt == "email"
    assert got["tot"].calc == ["a", "b"] and got["a"].calc == []


def test_sum_of_unknown_field_refused(make_pdf, tmp_path):
    fields = [Field("text", 0, (10, 10, 100, 30), "tot", fmt="number", calc=["manca"])]
    with pytest.raises(ValueError, match="manca"):
        save_with_fields(make_pdf(tmp_path, ["x"]), fields, tmp_path / "o.pdf")


def test_change_format_of_loaded_field(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "a")], src)
    fields = load_fields(open_pdf(src))
    fields[0].fmt = "amount"
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    assert load_fields(open_pdf(out))[0].fmt == "amount"


# tab order = order of the field list
def test_tab_order_follows_list(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 100, 100, 120), "secondo"), Field("text", 0, (10, 10, 100, 30), "primo")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    assert [w.field_name for w in open_pdf(out)[0].widgets()] == ["secondo", "primo"]
    loaded = load_fields(open_pdf(out))
    loaded.reverse()
    out2 = tmp_path / "o2.pdf"
    save_with_fields(out, loaded, out2)
    assert [f.name for f in load_fields(open_pdf(out2))] == ["primo", "secondo"]


# alignment helpers (view coordinates)
def test_align_rects():
    r = [(10, 10, 50, 30), (20, 50, 80, 60), (5, 100, 25, 130)]
    assert [x[0] for x in align_rects(r, "left")] == [5, 5, 5]
    assert [x[1] for x in align_rects(r, "top")] == [10, 10, 10]
    assert {x[2] - x[0] for x in align_rects(r, "same_width")} == {40}
    d = align_rects(r, "distribute_v")
    gaps = [d[i + 1][1] - d[i][3] for i in range(2)]
    assert abs(gaps[0] - gaps[1]) < 1e-6
    assert align_rects([r[0]], "left") == [r[0]]


def test_snap():
    assert snap((11, 12.6, 49, 31)) == (10, 15, 50, 30)
