import re
import pymupdf
import pytest

from pdflex import open_pdf
from pdflex.fields import Field, align_rects, fmt_parts, load_fields, sample_value, save_with_fields, snap


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
    assert got["a"].fmt == "amount|2|1|€|1" and got["b"].fmt == "number|2|1" and got["mail"].fmt == "email"
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
    assert load_fields(open_pdf(out))[0].fmt == "amount|2|1|€|1"


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


def test_changing_format_sum_boxes_keeps_other_properties(make_pdf, tmp_path):
    """Format, sum and boxes are edited in place: tooltip and colours set in another program survive."""
    import pymupdf
    src = tmp_path / "altro.pdf"
    doc = open_pdf(make_pdf(tmp_path, ["x"]))
    for name, y in (("a", 10), ("b", 40), ("tot", 70)):
        w = pymupdf.Widget()
        w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, name, pymupdf.Rect(10, y, 150, y + 20)
        w.field_label, w.fill_color = f"Suggerimento {name}", (1, 1, 0.8)
        doc[0].add_widget(w)
    doc.save(src)
    fields = {f.name: f for f in load_fields(open_pdf(src))}
    fields["a"].fmt = "amount"
    fields["tot"].fmt, fields["tot"].calc = "number", ["a", "b"]
    fields["b"].comb = 8
    out = tmp_path / "out.pdf"
    save_with_fields(src, list(fields.values()), out)
    got = {f.name: f for f in load_fields(open_pdf(out))}
    assert got["a"].fmt == "amount|2|1|€|1" and got["tot"].fmt == "number|2|1" and got["tot"].calc == ["a", "b"]
    assert got["b"].comb == 8
    widgets = {w.field_name: w for w in open_pdf(out)[0].widgets()}
    for name in ("a", "b", "tot"):
        assert widgets[name].field_label == f"Suggerimento {name}"
        assert widgets[name].fill_color == pytest.approx([1, 1, 0.8])
    assert widgets["tot"].field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY  # a total is computed, not typed
    # and back: no format, no sum
    fields = {f.name: f for f in load_fields(open_pdf(out))}
    fields["tot"].fmt, fields["tot"].calc, fields["b"].comb = "", [], 0
    save_with_fields(out, list(fields.values()), tmp_path / "back.pdf")
    got = {f.name: f for f in load_fields(open_pdf(tmp_path / "back.pdf"))}
    assert (got["tot"].fmt, got["tot"].calc, got["b"].comb) == ("", [], 0)
    w = {w.field_name: w for w in open_pdf(tmp_path / "back.pdf")[0].widgets()}["tot"]
    assert not w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY and w.field_label == "Suggerimento tot"


def test_field_written_by_other_programs_keeps_its_format(make_pdf, tmp_path):
    """Acrobat-style form: field and widget are one object, the format action written inside it.
    PDFlex reads that format, and moving the field does not wipe it."""
    src = tmp_path / "acrobat.pdf"
    d = open_pdf(make_pdf(tmp_path, ["x"]))
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "n", pymupdf.Rect(10, 10, 90, 30)
    x = d[0].add_widget(w).xref
    parent = int(d.xref_get_key(x, "Parent")[1].split()[0])
    d.xref_set_key(x, "T", "(n)")
    d.xref_set_key(x, "FT", "/Tx")
    d.xref_set_key(x, "DA", pymupdf.get_pdf_str(d.xref_get_key(parent, "DA")[1]))
    d.xref_set_key(x, "Parent", "null")
    d.xref_set_key(d.pdf_catalog(), "AcroForm/Fields", f"[{x} 0 R]")
    js = pymupdf.get_pdf_str('AFNumber_Format(2, 2, 0, 0, "", true);')
    d.xref_set_key(x, "AA", f"<</F<</S/JavaScript/JS {js}>>>>")
    d.save(src)
    f = load_fields(open_pdf(src))[0]
    assert f.fmt == "number|2|1"
    f.rect = (20, 20, 100, 40)
    save_with_fields(src, [f], tmp_path / "out.pdf")
    assert load_fields(open_pdf(tmp_path / "out.pdf"))[0].fmt == "number|2|1"


# currency amounts, calculation operations, min/max range
def test_amount_currency_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 10, 100, 30), "usd", fmt="amount|2|1|$|0"),
              Field("text", 0, (10, 40, 100, 60), "eur", fmt="amount"),
              Field("text", 0, (10, 70, 100, 90), "gbp", fmt="amount|0|0|£|1")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    js = _js(doc)
    assert 'AFNumber_Format(2, 2, 0, 0, " $", false)' in js
    assert r'"\u00A3 "' in js
    got = {f.name: f.fmt for f in load_fields(doc)}
    assert got == {"usd": "amount|2|1|$|0", "eur": "amount|2|1|€|1", "gbp": "amount|0|0|£|1"}


def test_calc_ops_and_range_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 10, 100, 30), "a", fmt="number", vmin="0", vmax="100.5"),
              Field("text", 0, (10, 40, 100, 60), "b", fmt="number", vmin="", vmax="10"),
              Field("text", 0, (10, 70, 100, 90), "m", fmt="number", calc=["a", "b"], calc_op="AVG")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    js = _js(doc)
    assert 'AFSimple_Calculate("AVG"' in js and "AFRange_Validate(true, 0, true, 100.5)" in js
    assert "AFRange_Validate(false, 0, true, 10)" in js
    got = {f.name: f for f in load_fields(doc)}
    assert (got["a"].vmin, got["a"].vmax, got["b"].vmin, got["b"].vmax) == ("0", "100.5", "", "10")
    assert got["m"].calc_op == "AVG" and got["a"].calc_op == "SUM"


def test_changing_calc_op_and_range_of_saved_field(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    out = tmp_path / "o.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "a", fmt="number"),
                           Field("text", 0, (10, 40, 100, 60), "t", fmt="number", calc=["a"])], out)
    fields = load_fields(open_pdf(out))
    fields[0].vmax = "5"
    fields[1].calc_op = "MAX"
    out2 = tmp_path / "o2.pdf"
    save_with_fields(out, fields, out2)
    got = {f.name: f for f in load_fields(open_pdf(out2))}
    assert got["a"].vmax == "5" and got["t"].calc_op == "MAX"


def test_foreign_amount_and_range_read(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "x", pymupdf.Rect(10, 10, 100, 30)
    w.script_format = 'AFNumber_Format(2, 0, 0, 0, "$", false);'
    w.script_change = "AFRange_Validate(true, 1, false, 0);"
    page.add_widget(w)
    doc.save(tmp_path / "f.pdf")
    f = load_fields(open_pdf(tmp_path / "f.pdf"))[0]
    assert f.fmt == "amount|2|1|$|0" and f.vmin == "1" and f.vmax == ""


def test_old_amount_format_still_euro_before():
    assert fmt_parts("amount|2|1") == ("amount", ["2", "1", "€", "1"])
    assert fmt_parts("number|2|1") == ("number", ["2", "1"])
    assert sample_value("amount|2|1|$|0", "x") == "1.234,50 $"
    assert sample_value("amount", "x") == "€ 1.234,50"


# checkbox mark, tooltip, read-only, visibility, max length
def _on_stream(doc, xref) -> bytes:
    t, v = doc.xref_get_key(xref, "AP/N")
    keys = re.findall(r"/(\w+)\s+(\d+) 0 R", v)
    return next(doc.xref_stream(int(x)) for k, x in keys if k != "Off")


def test_widget_props_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("checkbox", 0, (10, 10, 30, 30), "c", default="1", mark="8"),
              Field("text", 0, (10, 40, 100, 60), "t", tooltip="Il tuo nome", readonly=True, maxlen=12,
                    visibility="print"),
              Field("text", 0, (10, 70, 100, 90), "h", visibility="hidden"),
              Field("text", 0, (10, 100, 100, 120), "s", visibility="screen"),
              Field("text", 0, (10, 130, 100, 150), "v")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    got = {f.name: f for f in load_fields(doc)}
    assert got["c"].mark == "8" and b"(8) Tj" in _on_stream(doc, got["c"].xref)
    t = got["t"]
    assert (t.tooltip, t.readonly, t.maxlen, t.visibility) == ("Il tuo nome", True, 12, "print")
    assert got["h"].visibility == "hidden" and got["s"].visibility == "screen" and got["v"].visibility == ""
    assert doc.xref_get_key(t.xref, "F")[1] == "36"  # NoView + Print
    assert (got["v"].tooltip, got["v"].readonly, got["v"].maxlen) == ("", False, 0)


def test_widget_props_changed_on_saved_field(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    out = tmp_path / "o.pdf"
    save_with_fields(src, [Field("checkbox", 0, (10, 10, 30, 30), "c", default="1"),
                           Field("text", 0, (10, 40, 100, 60), "t")], out)
    fields = load_fields(open_pdf(out))
    fields[0].mark = "H"
    t = fields[1]
    t.tooltip, t.readonly, t.maxlen, t.visibility = "Aiuto", True, 5, "hidden"
    out2 = tmp_path / "o2.pdf"
    save_with_fields(out, fields, out2)
    doc = open_pdf(out2)
    got = {f.name: f for f in load_fields(doc)}
    assert got["c"].mark == "H" and b"(H) Tj" in _on_stream(doc, got["c"].xref)
    assert (got["t"].tooltip, got["t"].readonly, got["t"].maxlen, got["t"].visibility) == ("Aiuto", True, 5, "hidden")


def test_mark_survives_move(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    out = tmp_path / "o.pdf"
    save_with_fields(src, [Field("checkbox", 0, (10, 10, 30, 30), "c", default="1", mark="H")], out)
    f = load_fields(open_pdf(out))[0]
    f.rect = (50, 50, 70, 70)
    out2 = tmp_path / "o2.pdf"
    save_with_fields(out, [f], out2)
    doc = open_pdf(out2)
    assert b"(H) Tj" in _on_stream(doc, load_fields(doc)[0].xref)


def test_calc_field_stays_readonly_without_flag(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    out = tmp_path / "o.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "a", fmt="number"),
                           Field("text", 0, (10, 40, 100, 60), "t", fmt="number", calc=["a"])], out)
    doc = open_pdf(out)
    got = {f.name: f for f in load_fields(doc)}
    assert got["t"].readonly is False
    assert int(doc.xref_get_key(got["t"].xref, "Ff")[1] if doc.xref_get_key(got["t"].xref, "Ff")[0] != "null"
               else doc.xref_get_key(got["t"].parent, "Ff")[1]) & 1


def test_old_checkbox_reads_as_check(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    out = tmp_path / "o.pdf"
    save_with_fields(src, [Field("checkbox", 0, (10, 10, 30, 30), "c", default="1")], out)
    f = load_fields(open_pdf(out))[0]
    assert f.mark == "" and f == f.orig
