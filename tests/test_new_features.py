import pymupdf

from pdflex import open_pdf
from pdflex.batch import merged_fill
from pdflex.fields import Field, save_with_fields
from pdflex.fill import fill_values, form_inputs
from pdflex.pages import Extras, PageRef, assemble


def _form(tmp_path, make_pdf):
    out = tmp_path / "modulo.pdf"
    save_with_fields(make_pdf(tmp_path, ["Modulo"]), [
        Field("text", 0, (10, 10, 200, 30), "nome"), Field("checkbox", 0, (10, 40, 24, 54), "accetto"),
        Field("radio", 0, (10, 60, 24, 74), "sesso", value="M"), Field("radio", 0, (30, 60, 44, 74), "sesso", value="F"),
        Field("dropdown", 0, (10, 80, 200, 100), "citta", options=["Roma", "Milano"]),
        Field("text", 0, (10, 110, 200, 130), "importo", fmt="amount"),
        Field("text", 0, (10, 140, 200, 160), "totale", calc=["importo"])], out)
    return out


def _words(path):
    return " ".join(w[4] for page in open_pdf(path) for w in page.get_text("words"))


def test_compare_can_be_cancelled(qapp, make_pdf, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QProgressDialog
    from pdflex.compare import CompareTab
    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)  # Annulla pressed at once
    tab = CompareTab()
    tab.load(str(make_pdf(tmp_path, ["uno"])), str(make_pdf(tmp_path, ["due"])))
    assert tab.stack.currentIndex() == 0 and "annullato" in tab.summary.text() and not tab.changes


def test_merged_fill_one_printable_pdf(make_pdf, tmp_path):
    rows = [{"Nome": "Mario Rossi"}, {"Nome": "Anna Bianchi"}, {"Nome": "Luca Verdi"}]
    out = tmp_path / "tutti.pdf"
    assert merged_fill(_form(tmp_path, make_pdf), rows, {"nome": "Nome"}, out) == 3
    doc = open_pdf(out)
    assert doc.page_count == 3 and not any(w for page in doc for w in page.widgets())  # fields turned into text
    assert [doc[i].get_text().count(r["Nome"].split()[0]) for i, r in enumerate(rows)] == [1, 1, 1]


def test_merged_fill_stopped_writes_nothing(make_pdf, tmp_path):
    out = tmp_path / "tutti.pdf"
    assert merged_fill(_form(tmp_path, make_pdf), [{"Nome": "a"}, {"Nome": "b"}], {"nome": "Nome"}, out,
                       progress=lambda i, n: False) == 0
    assert not out.exists()


def test_extras_written_on_every_page(make_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    src = make_pdf(tmp_path, ["a", "b"], rotate=90)
    assemble([PageRef(str(src), 0), PageRef(str(src), 1)], out,
             Extras(numbers=True, header="Studio Rossi", watermark="BOZZA"))
    doc = open_pdf(out)
    for i, page in enumerate(doc):
        text = page.get_text()
        assert "Studio Rossi" in text and "BOZZA" in text and f"Pagina {i + 1} di 2" in text.replace("\n", " ")
    assert "Pagina" not in _words(make_pdf(tmp_path, ["a"]))  # nothing added unless asked


def test_compress_shrinks_big_pictures(tmp_path):
    d = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2000, 2000), 0)
    for x in range(0, 2000, 5):
        pix.set_rect(pymupdf.IRect(x, 0, x + 2, 2000), ((x * 13) % 256, (x * 7) % 256, 90))
    d.new_page().insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pix)
    src = tmp_path / "big.pdf"
    d.save(src)
    plain, small = tmp_path / "plain.pdf", tmp_path / "small.pdf"
    assemble([PageRef(str(src), 0)], plain)
    assemble([PageRef(str(src), 0)], small, Extras(compress=True))
    assert small.stat().st_size < plain.stat().st_size * 0.8
    assert open_pdf(small).page_count == 1


def test_form_inputs_and_fill(make_pdf, tmp_path):
    data = open_pdf(_form(tmp_path, make_pdf)).tobytes()
    inputs = {i.name: i for i in form_inputs(pymupdf.open("pdf", data))}
    assert "totale" not in inputs  # a computed total is not typed
    assert inputs["sesso"].kind == "choice" and inputs["sesso"].choices == ["M", "F"]
    assert inputs["citta"].choices == ["Roma", "Milano"] and inputs["accetto"].kind == "checkbox"
    assert inputs["importo"].hint.startswith("€")
    doc = fill_values(data, {"nome": "Mario", "accetto": "1", "sesso": "F", "citta": "Milano"})
    got = {i.name: i.value for i in form_inputs(doc)}
    assert (got["nome"], got["accetto"], got["sesso"], got["citta"]) == ("Mario", "1", "F", "Milano")
    flat = fill_values(data, {"nome": "Mario"}, flatten=True)
    assert not any(flat[0].widgets()) and "Mario" in flat[0].get_text()


def test_fill_dialog_from_editor(qapp, make_pdf, tmp_path, monkeypatch):
    from pdflex import editor, fill
    from pdflex.editor import EditorTab
    tab = EditorTab()
    tab.open(str(_form(tmp_path, make_pdf)))
    tab.add_field("text", 0, (10, 200, 200, 220))  # not saved yet: still offered
    seen = {}

    def run(d):
        seen["names"] = list(d.getters)
        d.findChild(fill.QLineEdit).setText("Mario")
        out = tmp_path / "compilato.pdf"
        monkeypatch.setattr(fill.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
        monkeypatch.setattr(fill, "info", lambda *a: None)
        d.ask_save()
        seen["out"] = out
    monkeypatch.setattr(fill.FillDialog, "exec", run)
    tab.fill()
    assert seen["names"][0] == "nome" and len(seen["names"]) == 6
    assert {i.name: i.value for i in form_inputs(open_pdf(seen["out"]))}["nome"] == "Mario"
    monkeypatch.setattr(editor, "info", lambda parent, text: seen.setdefault("info", text))
    tab.fields = []
    tab.fill()
    assert "non ha campi" in seen["info"]
