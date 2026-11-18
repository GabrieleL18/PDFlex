import pymupdf
import pytest

from pdflex import design, open_pdf, ui
from pdflex.designer import DesignTab


def _pdf(tmp_path, shapes_as_text=False):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo vicino alla linea")
    page.draw_line((72, 104), (300, 104))
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "nome", pymupdf.Rect(72, 110, 200, 130)
    page.add_widget(w)
    b = pymupdf.Widget()
    b.field_type, b.field_name, b.rect = pymupdf.PDF_WIDGET_TYPE_BUTTON, "btn", pymupdf.Rect(220, 110, 300, 130)
    b.button_caption = "Svuota"
    page.add_widget(b)
    if shapes_as_text:
        for i in range(20):
            x = 72 + i * 6
            page.draw_polyline([(x, 300), (x + 4, 300), (x + 2, 293), (x, 300)], color=None, fill=(0, 0, 0))
    path = tmp_path / "in.pdf"
    doc.save(path)
    return str(path)


def _line_y(tab):
    return round(next(o for o in tab.objects if o.kind == "line").data["p1"][1])


def _tab(tmp_path, **kw):
    tab = DesignTab()
    tab.open(_pdf(tmp_path, **kw))
    return tab


def test_open_shows_objects_not_fields(qapp, tmp_path):
    tab = _tab(tmp_path)
    assert {o.kind for o in tab.objects} == {"line", "text"} and tab.banner.isHidden()
    assert len(tab.items) == 2  # one box per object, none for the field and the button


def test_move_undo_redo_and_failed_edit_rolls_back(qapp, tmp_path, monkeypatch):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    tab.move_selected(0, 30)
    assert tab.dirty and _line_y(tab) == 134
    assert [o.kind for o in tab.selection] == ["line"]  # still selected after the change: arrows keep working
    tab.undo()
    assert _line_y(tab) == 104
    tab.redo()
    assert _line_y(tab) == 134
    tab.select([o for o in tab.objects if o.kind == "line"])
    snapshot = tab.doc.tobytes()
    monkeypatch.setattr(design, "move", lambda *a: (_ for _ in ()).throw(design.EditError("no")))
    warned = []
    monkeypatch.setattr(ui, "warn", lambda parent, text: warned.append(text))
    tab.move_selected(5, 5)
    def content(doc):  # tobytes() of a reopened copy differs in bookkeeping bytes: compare what the page holds
        return [d["rect"] for d in doc[0].get_drawings()], doc[0].get_text("words"), len(list(doc[0].widgets()))
    assert warned == ["no"] and content(tab.doc) == content(pymupdf.open("pdf", snapshot)) and _line_y(tab) == 134


def test_save_keeps_fields_and_opens_in_forms(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab.select([o for o in tab.objects if o.kind == "line"])
    tab.delete_selected()
    got = []
    tab.open_in_forms.connect(got.append)
    out = str(tmp_path / "in_modificato.pdf")
    tab.save(out)
    assert not tab.dirty
    assert [w.field_name for w in open_pdf(out)[0].widgets()] == ["nome", "btn"]
    tab.b_forms.click()
    assert got == [out]


def test_banner_for_text_as_shapes(qapp, tmp_path):
    tab = _tab(tmp_path, shapes_as_text=True)
    assert not tab.banner.isHidden() and "Testo disegnato come forme" in tab.banner.text()


def test_draw_components(qapp, tmp_path, monkeypatch):
    tab = _tab(tmp_path)
    tab.set_mode("rect")
    tab.drawn((100, 400, 200, 450))
    tab.set_mode("line")
    tab.drawn((100, 500, 300, 506), straight=True)  # Shift: stays horizontal
    monkeypatch.setattr(ui, "ask_text", lambda *a, **k: "Nuovo testo")
    tab.set_mode("text")
    tab.drawn((100, 600, 100, 600))
    kinds = sorted(o.kind for o in tab.objects)
    assert kinds == ["line", "line", "rect", "text", "text"]
    new_line = next(o for o in tab.objects if o.kind == "line" and o.data["p1"][1] > 400)
    assert new_line.data["p1"][1] == pytest.approx(new_line.data["p2"][1])
    assert "Nuovo testo" in tab.doc[0].get_text() and tab.mode == "select"


def test_text_change_asks_for_helvetica_when_letters_are_missing(qapp, tmp_path, monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo", fontfile="C:/Windows/Fonts/arial.ttf", fontname="F0")
    doc.xref_set_key(page.get_fonts()[0][0], "BaseFont", "/ArialMT")
    doc.subset_fonts()  # like most PDFs: no map from letters to glyphs
    path = tmp_path / "sub.pdf"
    doc.save(path)
    tab = DesignTab()
    tab.open(str(path))
    tab.select([o for o in tab.objects if o.kind == "text"])
    asked = []
    monkeypatch.setattr(ui, "message", lambda parent, text, *a, **k: asked.append(text) or 1)  # Annulla
    tab.set_text("Totale €")
    assert asked and "Uso Helvetica" in asked[0] and "Totale" not in tab.doc[0].get_text()
    monkeypatch.setattr(ui, "message", lambda *a, **k: 0)  # Usa Helvetica
    tab.select([o for o in tab.objects if o.kind == "text"])
    tab.set_text("Totale €")
    assert "Totale €" in tab.doc[0].get_text()
