import pymupdf
import pytest

from pdflex import open_pdf
from pdflex.fields import Field, clean_value, load_fields, save_with_fields


def all_fields():
    return [
        Field("text", 0, (50, 100, 250, 120), "nome", required=True, default="Mario", font_size=11),
        Field("multiline", 0, (50, 130, 250, 190), "note"),
        Field("checkbox", 0, (50, 200, 64, 214), "accetto", default="1"),
        Field("radio", 0, (50, 230, 64, 244), "scelta", value="si", default="1"),
        Field("radio", 0, (80, 230, 94, 244), "scelta", value="no"),
        Field("dropdown", 0, (50, 260, 200, 280), "citta", options=["Roma", "Milano"], default="Milano"),
        Field("date", 0, (50, 290, 150, 310), "data"),
        Field("signature", 0, (50, 320, 250, 360), "firma"),
    ]


def test_roundtrip_all_kinds(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["Modulo"])
    fields = all_fields()
    out = tmp_path / "out.pdf"
    save_with_fields(src, fields, out)
    got = load_fields(open_pdf(out))
    key = lambda f: (f.kind, f.name, f.value)
    assert sorted(map(key, got)) == sorted(map(key, fields))
    by = {(f.name, f.value): f for f in got}
    assert by[("nome", "")].required and by[("nome", "")].default == "Mario"
    assert by[("nome", "")].font_size == 11
    assert by[("citta", "")].options == ["Roma", "Milano"]
    assert by[("citta", "")].default == "Milano"
    assert by[("accetto", "")].default == "1"
    assert by[("scelta", "si")].default == "1" and by[("scelta", "no")].default == ""
    assert all(abs(a - b) < 0.5 for a, b in zip(by[("note", "")].rect, (50, 130, 250, 190)))


def test_radio_buttons_have_distinct_states(make_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), all_fields(), out)
    doc = pymupdf.open(out)
    page = doc[0]
    states = {w.on_state() for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON}
    assert states == {"si", "no"}


def test_date_has_format(make_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), all_fields(), out)
    got = {f.name: f.kind for f in load_fields(open_pdf(out))}
    assert got["data"] == "date" and got["nome"] == "text"


def test_save_twice_does_not_duplicate(make_pdf, tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), all_fields(), a)
    fields = load_fields(open_pdf(a))
    save_with_fields(a, fields, b)
    assert len(load_fields(open_pdf(b))) == len(all_fields())


def test_save_over_source(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    save_with_fields(src, all_fields(), src)
    assert len(load_fields(open_pdf(src))) == len(all_fields())


def test_fields_on_second_page(make_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    save_with_fields(make_pdf(tmp_path, ["a", "b"]), [Field("text", 1, (10, 10, 100, 30), "p2")], out)
    assert [(f.page, f.name) for f in load_fields(open_pdf(out))] == [(1, "p2")]


def test_duplicate_names_rejected(make_pdf, tmp_path):
    fields = [Field("text", 0, (10, 10, 100, 30), "a"), Field("text", 0, (10, 40, 100, 60), "a")]
    with pytest.raises(ValueError, match="a"):
        save_with_fields(make_pdf(tmp_path, ["x"]), fields, tmp_path / "o.pdf")
    mixed = [Field("radio", 0, (10, 10, 24, 24), "g", value="x"), Field("text", 0, (10, 40, 100, 60), "g")]
    with pytest.raises(ValueError, match="g"):
        save_with_fields(make_pdf(tmp_path, ["x"]), mixed, tmp_path / "o.pdf")


def test_clean_value():
    assert clean_value("Sì grazie") == "Si_grazie"
    assert clean_value("") == "opzione"


def test_open_pdf_errors(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="Impossibile aprire"):
        open_pdf(bad)
    doc = pymupdf.open()
    doc.new_page()
    enc = tmp_path / "enc.pdf"
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(ValueError, match="protetto da password"):
        open_pdf(enc)


def test_unsupported_widgets_are_kept(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    doc = pymupdf.open(src)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_LISTBOX
    w.field_name = "lista"
    w.rect = pymupdf.Rect(10, 10, 100, 60)
    w.choice_values = ["a", "b"]
    doc[0].add_widget(w)
    doc.saveIncr()
    doc.close()
    out = tmp_path / "o.pdf"
    save_with_fields(src, [Field("text", 0, (10, 80, 100, 100), "t")], out)
    names = {w.field_name for w in pymupdf.open(out)[0].widgets()}
    assert names == {"lista", "t"}
