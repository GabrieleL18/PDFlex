import pymupdf

from pdflex import open_pdf
from pdflex.batch import fill_form
from pdflex.fields import HELPER, Field, fill_sample, save_with_fields
from pdflex.logic import form_logic


def _form(make_pdf, tmp_path, extra=()):
    fields = [Field("text", 0, (10, 10, 100, 30), "Quantità", fmt="number|0|1"),
              Field("text", 0, (10, 40, 100, 60), "Prezzo", fmt="amount|2|1|€|1"),
              Field("text", 0, (10, 70, 100, 90), "Totale", fmt="amount|2|1|€|1", formula="[Quantità] * [Prezzo]"),
              Field("checkbox", 0, (10, 100, 30, 120), "Altro"),
              Field("text", 0, (10, 130, 100, 150), "Dettaglio", show_if="[Altro] = SÌ"), *extra]
    out = tmp_path / "form.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), fields, out)
    return out


def _widget(doc, name):
    return next(w for p in doc for w in p.widgets() if w.field_name == name)


def test_run_computes_and_hides(make_pdf, tmp_path):
    logic = form_logic(open_pdf(_form(make_pdf, tmp_path)))
    values, hidden = logic.run({"Quantità": "2", "Prezzo": "1.234,50", "Altro": "", "Dettaglio": "x"})
    assert values["Totale"] == "€ 2.469,00"
    assert values["Dettaglio"] == "" and hidden == {"Dettaglio"}
    values, hidden = logic.run({"Quantità": "2", "Prezzo": "3", "Altro": "1", "Dettaglio": "x"})
    assert values["Dettaglio"] == "x" and not hidden


def test_quick_total_computed(make_pdf, tmp_path):
    out = tmp_path / "sum.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), [Field("text", 0, (10, 10, 100, 30), "a"),
                                                 Field("text", 0, (10, 40, 100, 60), "b"),
                                                 Field("text", 0, (10, 70, 100, 90), "t", calc=["a", "b"])], out)
    values, _ = form_logic(open_pdf(out)).run({"a": "1", "b": "2"})
    assert values["t"] == "3"


def test_fill_form_applies_logic(make_pdf, tmp_path):
    doc = open_pdf(_form(make_pdf, tmp_path))
    row = {"Quantità": "2", "Prezzo": "10", "Totale": "999", "Dettaglio": "x"}
    fill_form(doc, row, {n: n for n in row})
    assert _widget(doc, "Totale").field_value == "€ 20,00"
    w = _widget(doc, "Dettaglio")
    assert w.field_value == "" and doc.xref_get_key(w.xref, "F")[1] == "2"


def test_fill_form_shows_again(make_pdf, tmp_path):
    doc = open_pdf(_form(make_pdf, tmp_path))
    row = {"Altro": "sì", "Dettaglio": "x"}
    fill_form(doc, row, {n: n for n in row})
    w = _widget(doc, "Dettaglio")
    assert w.field_value == "x" and doc.xref_get_key(w.xref, "F")[1] == "4"


def test_fill_sample_computes(make_pdf, tmp_path):
    doc = open_pdf(_form(make_pdf, tmp_path))
    fill_sample(doc)
    assert _widget(doc, "Totale").field_value.startswith("€ ")


def test_foreign_script_not_evaluated(make_pdf, tmp_path):
    out = tmp_path / "f.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), [Field("text", 0, (10, 10, 100, 30), "x")], out)
    doc = open_pdf(out)
    w = _widget(doc, "x")
    doc.xref_set_key(w.xref, "AA", "<</C <</S/JavaScript/JS (event.value = 42;)>>>>")
    assert not form_logic(doc).computed
    fill_form(doc, {"x": "7"}, {"x": "x"})
    assert _widget(doc, "x").field_value == "7"


def test_helper_not_an_input(make_pdf, tmp_path):
    from pdflex.fill import form_inputs
    inputs = {i.name: i for i in form_inputs(open_pdf(_form(make_pdf, tmp_path)))}
    assert HELPER not in inputs
    assert inputs["Totale"].kind == "computed" and inputs["Dettaglio"].cond


def test_fill_dialog_live(qapp, make_pdf, tmp_path):
    from PySide6.QtWidgets import QLineEdit
    from pdflex.fill import FillDialog
    path = _form(make_pdf, tmp_path)
    dlg = FillDialog(None, path.read_bytes(), str(path))
    boxes = {n: w for n, w in dlg.inputs_by_name.items()}
    boxes["Quantità"].setText("2")
    boxes["Prezzo"].setText("3")
    dlg.redraw()
    assert boxes["Totale"].text() == "€ 6,00" and isinstance(boxes["Totale"], QLineEdit) and boxes["Totale"].isReadOnly()
    assert not dlg.form.isRowVisible(boxes["Dettaglio"])
    boxes["Altro"].setChecked(True)
    dlg.redraw()
    assert dlg.form.isRowVisible(boxes["Dettaglio"])
    dlg.done(0)
