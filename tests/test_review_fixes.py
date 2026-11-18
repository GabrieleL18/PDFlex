import pymupdf
import pytest

from pdflex import open_pdf
from pdflex.compare import diff_words, export_report, extract_words
from pdflex.fields import Field, from_view, load_fields, save_with_fields, suggest_save_path, to_view


def _rotated(tmp_path, make_pdf):
    return make_pdf(tmp_path, ["inizio\n" + "\n" * 40 + "prezzo 100 euro"], rotate=90)


# 1 rotated pages: fields land where they were drawn
def test_rotated_field_lands_where_drawn(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"], rotate=90)
    page = open_pdf(src)[0]
    drawn = (10, 10, 110, 30)  # as seen on screen
    f = Field("text", 0, from_view(page, drawn), "t")
    assert all(abs(a - b) < 0.01 for a, b in zip(to_view(page, f.rect), drawn))
    out = tmp_path / "o.pdf"
    save_with_fields(src, [f], out)
    doc = open_pdf(out)
    w = next(doc[0].widgets())
    shown = pymupdf.Rect(w.rect) * doc[0].rotation_matrix
    assert all(abs(a - b) < 0.5 for a, b in zip(shown, drawn))


# 2 compare on rotated pages
def test_rotated_words_in_display_coords(make_pdf, tmp_path):
    doc = open_pdf(_rotated(tmp_path, make_pdf))
    first = extract_words(doc)[0]
    assert first.text == "inizio"
    assert first.rect[0] > doc[0].rect.width / 2  # 90° turns the top line to the right edge


def test_rotated_export_keeps_all_text(make_pdf, tmp_path):
    old = make_pdf(tmp_path, ["inizio\n" + "\n" * 40 + "prezzo 100 euro"], rotate=90)
    new = make_pdf(tmp_path, ["inizio\n" + "\n" * 40 + "prezzo 120 euro"], rotate=90)
    ch = diff_words(extract_words(open_pdf(old)), extract_words(open_pdf(new)))
    out = tmp_path / "r.pdf"
    export_report(old, new, ch, out)
    text = open_pdf(out)[-1].get_text()
    for word in ("inizio", "100", "120", "euro"):
        assert word in text


# 3 existing forms keep properties this app does not edit
def _existing_form(tmp_path, make_pdf):
    src = make_pdf(tmp_path, ["x", "y"])
    doc = pymupdf.open(src)
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "codice", pymupdf.Rect(50, 50, 200, 70)
    w.text_maxlen = 16
    w.field_flags |= pymupdf.PDF_FIELD_IS_READ_ONLY
    w.field_label = "Codice fiscale"
    doc[0].add_widget(w)
    d = pymupdf.Widget()
    d.field_type, d.field_name, d.rect = pymupdf.PDF_WIDGET_TYPE_COMBOBOX, "paese", pymupdf.Rect(50, 80, 200, 100)
    d.choice_values = [["IT", "Italia"], ["FR", "Francia"]]
    doc[0].add_widget(d)
    for pno in (0, 1):  # same field mirrored on two pages
        m = pymupdf.Widget()
        m.field_type, m.field_name, m.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "nome", pymupdf.Rect(50, 120, 200, 140)
        doc[pno].add_widget(m)
    doc.saveIncr()
    doc.close()
    return src


def test_existing_properties_kept_when_adding_field(make_pdf, tmp_path):
    src = _existing_form(tmp_path, make_pdf)
    fields = load_fields(open_pdf(src)) + [Field("text", 0, (50, 200, 200, 220), "nuovo")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    ws = {w.field_name: w for w in open_pdf(out)[0].widgets()}
    assert ws["codice"].text_maxlen == 16
    assert ws["codice"].field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY
    assert ws["codice"].field_label == "Codice fiscale"
    assert [list(c) for c in ws["paese"].choice_values] == [["IT", "Italia"], ["FR", "Francia"]]
    assert "nuovo" in ws


def test_edited_existing_field_keeps_other_properties(make_pdf, tmp_path):
    src = _existing_form(tmp_path, make_pdf)
    fields = load_fields(open_pdf(src))
    f = next(f for f in fields if f.name == "codice")
    f.name, f.rect, f.required = "cf", (60, 50, 220, 72), True
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    w = next(w for w in open_pdf(out)[0].widgets() if w.field_name == "cf")
    assert w.text_maxlen == 16 and w.field_flags & pymupdf.PDF_FIELD_IS_REQUIRED
    assert all(abs(a - b) < 0.5 for a, b in zip(w.rect, (60, 50, 220, 72)))


def test_removed_existing_field_is_deleted(make_pdf, tmp_path):
    src = _existing_form(tmp_path, make_pdf)
    fields = [f for f in load_fields(open_pdf(src)) if f.name != "codice"]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    assert "codice" not in {f.name for f in load_fields(open_pdf(out))}


# 4 same field on several pages
def test_mirrored_field_saves(make_pdf, tmp_path):
    src = _existing_form(tmp_path, make_pdf)
    out = tmp_path / "o.pdf"
    save_with_fields(src, load_fields(open_pdf(src)), out)
    assert [f.page for f in load_fields(open_pdf(out)) if f.name == "nome"] == [0, 1]


def test_new_duplicate_still_refused(make_pdf, tmp_path):
    src = _existing_form(tmp_path, make_pdf)
    fields = load_fields(open_pdf(src)) + [Field("text", 0, (50, 200, 200, 220), "nome")]
    with pytest.raises(ValueError, match="nome"):
        save_with_fields(src, fields, tmp_path / "o.pdf")


# 5 radio values unique
def test_radio_duplicate_values_refused(make_pdf, tmp_path):
    fields = [Field("radio", 0, (10, 10, 24, 24), "g", value="Sì"), Field("radio", 0, (30, 10, 44, 24), "g", value="Si")]
    with pytest.raises(ValueError, match="g"):
        save_with_fields(make_pdf(tmp_path, ["x"]), fields, tmp_path / "o.pdf")
    off = [Field("radio", 0, (10, 10, 24, 24), "g", value="Off")]
    with pytest.raises(ValueError, match="Off"):
        save_with_fields(make_pdf(tmp_path, ["x"]), off, tmp_path / "o.pdf")


def test_new_radio_value_unique_after_delete(qapp, make_pdf, tmp_path):
    from pdflex.editor import EditorTab
    tab = EditorTab()
    tab.open(str(make_pdf(tmp_path, ["x"])))
    r = [tab.add_field("radio", 0, (10 + 20 * i, 10, 24 + 20 * i, 24)) for i in range(3)]
    tab.fields.remove(r[1])
    new = tab.add_field("radio", 0, (90, 10, 104, 24))
    assert len({f.value for f in tab.fields if f.kind == "radio"}) == 3 and new.value not in (r[0].value, r[2].value)


# 6 unsaved changes are tracked
def test_dirty_flag(qapp, make_pdf, tmp_path):
    from pdflex.editor import EditorTab
    tab = EditorTab()
    src = make_pdf(tmp_path, ["x"])
    tab.open(str(src))
    assert not tab.dirty
    tab.add_field("text", 0, (10, 10, 100, 30))
    assert tab.dirty
    tab.save(str(tmp_path / "o.pdf"))
    assert not tab.dirty


# save dialog never proposes the original
def test_suggest_save_path():
    assert suggest_save_path("C:/a/contratto.pdf").replace("\\", "/") == "C:/a/contratto_modulo.pdf"
    assert suggest_save_path("C:/a/contratto_modulo.pdf").replace("\\", "/") == "C:/a/contratto_modulo.pdf"


def test_edit_loaded_font_options_default(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    save_with_fields(src, [Field("text", 0, (10, 10, 100, 30), "t"),
                           Field("dropdown", 0, (10, 40, 100, 60), "d", options=["a", "b"])], src)
    fields = load_fields(open_pdf(src))
    t, d = fields
    t.font_size, t.default = 14, "ciao"
    d.options, d.default = ["x", "y"], "y"
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    got = {f.name: f for f in load_fields(open_pdf(out))}
    assert got["t"].font_size == 14 and got["t"].default == "ciao"
    assert got["d"].options == ["x", "y"] and got["d"].default == "y"


def test_preview_fills_sample_data(make_pdf, tmp_path):
    from pdflex.fields import build_pdf, fill_sample
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 10, 200, 30), "nome"), Field("text", 0, (10, 40, 200, 60), "x", default="Mio"),
              Field("checkbox", 0, (10, 70, 24, 84), "ok"), Field("dropdown", 0, (10, 90, 200, 110), "d", options=["a", "b"]),
              Field("radio", 0, (10, 120, 24, 134), "g", value="si"),
              Field("radio", 0, (30, 120, 44, 134), "g", value="no", default="1"),
              Field("checkbox", 0, (50, 70, 64, 84), "spunta", default="1"),
              Field("date", 0, (10, 140, 200, 160), "data")]
    doc = build_pdf(src, fields)
    fill_sample(doc)
    vals = {}
    for w in doc[0].widgets():
        vals.setdefault(w.field_name, w.field_value)
    assert vals["nome"] == "Mario Rossi" and vals["x"] == "Mio"
    assert vals["d"] == "a"
    # boxes and choices show their initial value, not an example that would hide it
    assert vals["ok"] in (False, "Off", "") and vals["spunta"] in (True, "Yes")
    chosen = [w.on_state() for w in doc[0].widgets() if w.field_name == "g" and w.field_value == w.on_state()]
    assert chosen == ["no"]
    assert vals["data"].count("/") == 2
    assert open_pdf(src)[0].first_widget is None  # the source file is untouched
