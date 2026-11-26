from pdflex import open_pdf
from pdflex.fields import Field, load_fields, make_block, place_block, save_with_fields

LOOK = ("border_color", "fill_color", "border_width", "border_style")


def _look(f):
    return tuple(getattr(f, k) for k in LOOK)


def test_new_field_keeps_old_look(make_pdf, tmp_path):
    src, out = make_pdf(tmp_path, ["x"]), tmp_path / "o.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "t")], out)
    doc = open_pdf(out)
    w = next(doc[0].widgets())
    assert [round(c, 2) for c in w.border_color] == [0.6, 0.6, 0.6]
    assert w.border_width == 1 and not w.fill_color
    assert _look(load_fields(doc)[0]) == ("", "", 1, "")


def test_look_round_trip(make_pdf, tmp_path):
    src, out = make_pdf(tmp_path, ["x"]), tmp_path / "o.pdf"
    fields = [Field("text", 0, (10, 10, 100, 30), "t", border_color="#000000", fill_color="#ffeecc",
                    border_width=2, border_style="dashed"),
              Field("checkbox", 0, (10, 40, 30, 60), "c", border_color="none"),
              Field("text", 0, (10, 70, 100, 90), "u", border_color="#3366cc", border_style="underline",
                    border_width=3),
              Field("text", 0, (10, 100, 100, 120), "b", border_style="beveled"),
              Field("text", 0, (10, 130, 100, 150), "i", border_style="inset")]
    save_with_fields(src, fields, out)
    got = {f.name: f for f in load_fields(open_pdf(out))}
    assert _look(got["t"]) == ("#000000", "#ffeecc", 2, "dashed")
    assert _look(got["c"]) == ("none", "", 1, "")
    assert _look(got["u"]) == ("#3366cc", "", 3, "underline")
    assert got["b"].border_style == "beveled" and got["i"].border_style == "inset"


def test_look_change_on_saved_field(make_pdf, tmp_path):
    src, out, out2 = make_pdf(tmp_path, ["x"]), tmp_path / "o.pdf", tmp_path / "o2.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "imp", fmt="amount|2|1|€|1")], out)
    fields = load_fields(open_pdf(out))
    fields[0].border_style, fields[0].fill_color = "underline", "#ddeeff"
    save_with_fields(out, fields, out2)
    f = load_fields(open_pdf(out2))[0]
    assert (f.border_style, f.fill_color) == ("underline", "#ddeeff")
    assert f.fmt == "amount|2|1|€|1"  # its format script is still there


def test_export_strips_look(make_pdf, tmp_path):
    src, out = make_pdf(tmp_path, ["x"]), tmp_path / "o.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "t", fill_color="#ffeecc", border_width=3)], out,
                     blank=True)
    doc = open_pdf(out)
    w = next(doc[0].widgets())
    assert not w.fill_color and not w.border_color
    assert doc.xref_get_key(w.xref, "BS/W")[1] == "0"  # PyMuPDF reads a width of 0 back as 1


def test_block_keeps_look():
    f = Field("text", 0, (10, 10, 100, 30), "t", fill_color="#ffeecc", border_style="dashed")
    (d, _), = place_block(make_block([f], [f.rect]), (0, 0), set())
    assert d["fill_color"] == "#ffeecc" and d["border_style"] == "dashed"
