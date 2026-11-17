import pymupdf
import pytest

from pdflex import open_pdf
from pdflex.editor import Field, load_fields, sample_value, save_with_fields

ALL = ["", "number|0|0", "number|3|1", "amount|2|1", "percent|1|1", "date|dd/mm/yy", "date|dd/mm/yyyy HH:MM",
       "time|HH:MM", "cap", "cf", "phone", "email"]


def test_every_format_saved_and_read_back(make_pdf, tmp_path):
    fields = [Field("date" if f.startswith(("date", "time")) else "text", 0, (10, 10 + 25 * i, 200, 30 + 25 * i),
                    f"f{i}", fmt=f) for i, f in enumerate(ALL)]
    out = tmp_path / "f.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), fields, out)
    got = {f.name: f for f in load_fields(open_pdf(out))}
    assert [got[f"f{i}"].fmt for i in range(len(ALL))] == ALL
    assert got["f5"].kind == "date" and got["f7"].kind == "date" and got["f1"].kind == "text"


def test_change_format_of_existing_field(make_pdf, tmp_path):
    out = tmp_path / "a.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), [Field("text", 0, (10, 10, 200, 30), "n", fmt="number|2|1")], out)
    f = load_fields(open_pdf(out))[0]
    f.fmt = "percent|0|0"
    save_with_fields(out, [f], tmp_path / "b.pdf")
    assert load_fields(open_pdf(tmp_path / "b.pdf"))[0].fmt == "percent|0|0"


def test_colour_and_alignment(make_pdf, tmp_path):
    out = tmp_path / "c.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]),
                     [Field("text", 0, (10, 10, 300, 30), "a", color="#cc0000", align=2, default="DESTRA")], out)
    f = load_fields(open_pdf(out))[0]
    assert (f.color, f.align) == ("#cc0000", 2)
    span = next(s for b in open_pdf(out)[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
                if s["text"] == "DESTRA")
    assert span["bbox"][0] > 150 and span["color"] == 0xCC0000  # drawn on the right, in red
    f.color, f.align = "#0000ff", 1  # and changed on the existing field
    save_with_fields(out, [f], tmp_path / "d.pdf")
    g = load_fields(open_pdf(tmp_path / "d.pdf"))[0]
    assert (g.color, g.align, g.default) == ("#0000ff", 1, "DESTRA")


def test_saved_for_filling_invisible_and_empty(make_pdf, tmp_path):
    src = tmp_path / "src.pdf"
    plain = make_pdf(tmp_path, ["Modulo"])
    doc = open_pdf(plain)
    w = pymupdf.Widget()  # a field already in the PDF, made elsewhere, with a value and a coloured box
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "vecchio", pymupdf.Rect(10, 200, 200, 220)
    w.field_value, w.border_color, w.fill_color = "valore di prova", (1, 0, 0), (1, 1, 0.8)
    doc[0].add_widget(w)
    doc.save(src)
    fields = load_fields(open_pdf(src)) + [
        Field("text", 0, (10, 10, 200, 30), "importo", default="1.234,00", fmt="amount|2|1", font="Cour", font_size=11),
        Field("checkbox", 0, (10, 40, 24, 54), "ok", default="1"),
        Field("dropdown", 0, (10, 60, 200, 80), "citta", options=["Roma", "Milano"], default="Roma")]
    out = tmp_path / "per_compilazione.pdf"
    save_with_fields(src, fields, out, blank=True)
    page = open_pdf(out)[0]
    assert page.get_text().strip() == "Modulo"  # nothing drawn but the page's own text
    # pixel for pixel the page without any field: no boxes, no fill, no values
    assert page.get_pixmap(dpi=60).samples == open_pdf(plain)[0].get_pixmap(dpi=60).samples
    got = {f.name: f for f in load_fields(open_pdf(out))}
    assert set(got) == {"vecchio", "importo", "ok", "citta"}  # all still there for the program that fills them
    assert all(f.default == "" for f in got.values())
    assert (got["importo"].fmt, got["importo"].font, got["importo"].font_size) == ("amount|2|1", "Cour", 11)
    assert got["citta"].options == ["Roma", "Milano"]
    # once a program writes a value, it shows
    filled = open_pdf(out)[0]  # kept in a variable: a widget only holds a weak reference to its page
    w = next(x for x in filled.widgets() if x.field_name == "importo")
    w.field_value = "99,00"
    w.update()
    assert "99,00" in filled.get_text()


@pytest.mark.parametrize("fmt, want", [
    ("number|2|1", "1.234,50"), ("number|0|0", "1234"), ("amount|2|1", "€ 1.234,50"), ("percent|1|1", "12,5%"),
    ("cap", "00100"), ("cf", "RSSMRA80A01H501U")])
def test_sample_values_follow_the_format(fmt, want):
    assert sample_value(fmt, "x") == want
    assert sample_value("date|dd/mm/yyyy", "x").count("/") == 2 and len(sample_value("time|HH:MM", "x")) == 5


def test_clearing_the_value_of_an_existing_field(make_pdf, tmp_path):
    out = tmp_path / "v.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), [Field("text", 0, (10, 10, 200, 30), "a", default="Mario")], out)
    f = load_fields(open_pdf(out))[0]
    f.default = ""
    save_with_fields(out, [f], tmp_path / "w.pdf")
    assert load_fields(open_pdf(tmp_path / "w.pdf"))[0].default == "" and "Mario" not in open_pdf(tmp_path / "w.pdf")[0].get_text()
