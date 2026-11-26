import re

import pytest

from pdflex import open_pdf
from pdflex.fields import HELPER, Field, _field_js, load_fields, make_block, place_block, save_with_fields
from pdflex.formula import rename


def _save(make_pdf, tmp_path, fields, name="o.pdf", src=None):
    src = src or make_pdf(tmp_path, ["x"])
    out = tmp_path / name
    save_with_fields(src, fields, out)
    return out


def _widget(doc, name):
    return next(w for p in doc for w in p.widgets() if w.field_name == name)


def _co_names(doc):
    t, v = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/CO")
    names = []
    for x in re.findall(r"(\d+) 0 R", v):
        names.append(doc.xref_get_key(int(x), "T")[1])
    return names


def test_formula_round_trip(make_pdf, tmp_path):
    fields = [Field("text", 0, (10, 10, 100, 30), "Quantità", fmt="number|0|1"),
              Field("text", 0, (10, 40, 100, 60), "Prezzo", fmt="amount|2|1|€|1"),
              Field("text", 0, (10, 70, 100, 90), "Totale", fmt="amount|2|1|€|1",
                    formula="ARROTONDA([Quantità] * [Prezzo]; 2)")]
    doc = open_pdf(_save(make_pdf, tmp_path, fields))
    got = {f.name: f for f in load_fields(doc)}
    assert got["Totale"].formula == "ARROTONDA([Quantità] * [Prezzo]; 2)"
    assert got["Totale"].calc == [] and not got["Totale"].foreign_calc
    w = _widget(doc, "Totale")
    assert w.field_flags & 1  # read only: computed, not typed
    assert "PDFlex:" in _field_js(doc, w)["C"]
    assert got["Totale"].readonly is False  # read-only because computed, not because the user said so


def test_condition_round_trip_and_helper(make_pdf, tmp_path):
    fields = [Field("checkbox", 0, (10, 10, 30, 30), "Altro"),
              Field("text", 0, (10, 40, 100, 60), "Dettaglio", show_if="[Altro] = SÌ", visibility="print")]
    out = _save(make_pdf, tmp_path, fields)
    out2 = tmp_path / "o2.pdf"
    loaded = load_fields(open_pdf(out))
    assert [f.name for f in loaded] == ["Altro", "Dettaglio"]  # the helper is not a field to edit
    loaded[0].rect = (12, 10, 32, 30)  # something changed: the form is written again
    save_with_fields(out, loaded, out2)
    doc = open_pdf(out2)
    got = {f.name: f for f in load_fields(doc)}
    assert got["Dettaglio"].show_if == "[Altro] = SÌ"
    assert got["Dettaglio"].visibility == "print"  # what the user chose, not the hidden state of the moment
    assert sum(w.field_name == HELPER for p in doc for w in p.widgets()) == 1
    assert doc.xref_get_key(_widget(doc, "Dettaglio").xref, "F")[1] == "2"  # Altro starts unticked: hidden
    assert "Dettaglio" in _field_js(doc, _widget(doc, HELPER))["C"]


def test_condition_true_at_start_keeps_visibility(make_pdf, tmp_path):
    fields = [Field("checkbox", 0, (10, 10, 30, 30), "Altro", default="1"),
              Field("text", 0, (10, 40, 100, 60), "Dettaglio", show_if="[Altro] = SÌ")]
    doc = open_pdf(_save(make_pdf, tmp_path, fields))
    assert doc.xref_get_key(_widget(doc, "Dettaglio").xref, "F")[1] == "4"


def test_condition_removed(make_pdf, tmp_path):
    fields = [Field("checkbox", 0, (10, 10, 30, 30), "Altro"),
              Field("text", 0, (10, 40, 100, 60), "Dettaglio", show_if="[Altro] = SÌ")]
    out = _save(make_pdf, tmp_path, fields)
    loaded = load_fields(open_pdf(out))
    loaded[1].show_if = ""
    out2 = tmp_path / "o2.pdf"
    save_with_fields(out, loaded, out2)
    doc = open_pdf(out2)
    assert load_fields(doc)[1].show_if == "" and load_fields(doc)[1].visibility == ""
    assert not any(w.field_name == HELPER for p in doc for w in p.widgets())


def test_calc_order_follows_dependencies(make_pdf, tmp_path):
    fields = [Field("text", 0, (10, 10, 100, 30), "C", formula="[B] + 1"),
              Field("text", 0, (10, 40, 100, 60), "B", formula="[A] * 2"),
              Field("text", 0, (10, 70, 100, 90), "A"),
              Field("checkbox", 0, (10, 100, 30, 120), "X", show_if="[A] = 1")]
    names = _co_names(open_pdf(_save(make_pdf, tmp_path, fields)))
    assert names.index("B") < names.index("C") and names[-1] == HELPER


def test_cycle_rejected(make_pdf, tmp_path):
    fields = [Field("text", 0, (10, 10, 100, 30), "A", formula="[B] + 1"),
              Field("text", 0, (10, 40, 100, 60), "B", formula="[A] + 1")]
    with pytest.raises(ValueError, match="circolare"):
        _save(make_pdf, tmp_path, fields)


def test_unknown_name_rejected(make_pdf, tmp_path):
    with pytest.raises(ValueError, match="Nessuno"):
        _save(make_pdf, tmp_path, [Field("text", 0, (10, 10, 100, 30), "A", formula="[Nessuno] + 1")])
    with pytest.raises(ValueError, match="A"):
        _save(make_pdf, tmp_path, [Field("text", 0, (10, 10, 100, 30), "A", show_if="1 +")])


def _with_calc(make_pdf, tmp_path, script):
    out = _save(make_pdf, tmp_path, [Field("text", 0, (10, 10, 100, 30), "Quantita"),
                                     Field("text", 0, (10, 40, 100, 60), "Prezzo"),
                                     Field("text", 0, (10, 70, 100, 90), "Totale")])
    doc = open_pdf(out)
    import pymupdf
    w = _widget(doc, "Totale")
    holder = int(doc.xref_get_key(w.xref, "Parent")[1].split()[0]) if doc.xref_get_key(w.xref, "Parent")[0] == "xref" else w.xref
    doc.xref_set_key(holder, "AA", f"<</C <</S/JavaScript/JS {pymupdf.get_pdf_str(script)}>>>>")
    out2 = tmp_path / "foreign.pdf"
    doc.save(out2)
    return out2


def test_acrobat_simplified_notation_read(make_pdf, tmp_path):
    out = _with_calc(make_pdf, tmp_path, "/** BVCALC Quantita * Prezzo EVCALC **/ event.value = 1;")
    got = {f.name: f for f in load_fields(open_pdf(out))}
    assert got["Totale"].formula == "[Quantita] * [Prezzo]"


def test_foreign_calc_kept(make_pdf, tmp_path):
    out = _with_calc(make_pdf, tmp_path, "event.value = 42;")
    fields = load_fields(open_pdf(out))
    tot = next(f for f in fields if f.name == "Totale")
    assert tot.foreign_calc and tot.formula == ""
    tot.rect = (20, 70, 110, 90)
    out2 = tmp_path / "moved.pdf"
    save_with_fields(out, fields, out2)
    doc = open_pdf(out2)
    assert _field_js(doc, _widget(doc, "Totale"))["C"] == "event.value = 42;"


def test_block_rewrites_formula():
    p = Field("text", 0, (0, 0, 50, 20), "Prezzo[0]")
    t = Field("text", 0, (60, 0, 110, 20), "Totale[0]", formula="[Prezzo[0]]*2", show_if="[Prezzo[0]] > 0")
    placed = place_block(make_block([p, t], [p.rect, t.rect]), (0, 30), {"Prezzo[0]", "Totale[0]"})
    d = {x["name"]: x for x, _ in placed}
    assert d["Totale[1]"]["formula"] == "[Prezzo[1]]*2"
    assert d["Totale[1]"]["show_if"] == "[Prezzo[1]] > 0"


def test_rename_all_at_once():
    from pdflex.formula import rename_all
    assert rename_all("[A]+[A_2]", {"A": "A_2", "A_2": "A_2_2"}) == "[A_2]+[A_2_2]"
    assert rename("[A]", "A", "B") == "[B]"


def _run_js(script, values):
    """Run a saved calculation script in node, with a stand-in for the reader: returns event.value and the
    display of every field."""
    import json
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node.js not installed")
    js = ("var display = {visible: 0, hidden: 1, noPrint: 2, noView: 3}; var event = {value: ''};\n"
          "var _f = {}; var _vals = " + json.dumps(values) + ";\n"
          "for (var n in _vals) _f[n] = {value: _vals[n], valueAsString: _vals[n], display: 0};\n"
          "var doc = {getField: function(n) { return _f[n] || null; }};\n"
          "(function() {\n" + script + "\n}).call(doc);\n"
          "var out = {value: event.value, display: {}}; for (var k in _f) out.display[k] = _f[k].display;\n"
          "console.log(JSON.stringify(out));")
    run = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_saved_scripts_run(make_pdf, tmp_path):
    fields = [Field("text", 0, (10, 10, 100, 30), "Prezzo[0]", fmt="amount|2|1|€|1"),
              Field("text", 0, (10, 40, 100, 60), "Totale", fmt="amount|2|1|€|1", formula="[Prezzo[0]] * 2"),
              Field("text", 0, (10, 70, 100, 90), "Nota", formula='SE([Altro]; "sì"; "no")'),
              Field("checkbox", 0, (10, 100, 30, 120), "Altro"),
              Field("text", 0, (10, 130, 100, 150), "Dettaglio", show_if="[Altro] = SÌ", visibility="print")]
    doc = open_pdf(_save(make_pdf, tmp_path, fields))
    js = {n: _field_js(doc, _widget(doc, n))["C"] for n in ("Totale", "Nota", HELPER)}
    assert _run_js(js["Totale"], {"Prezzo[0]": "1.234,50"})["value"] == 2469
    assert _run_js(js["Nota"], {"Altro": "Yes"})["value"] == "sì"
    off = _run_js(js[HELPER], {"Altro": "Off", "Dettaglio": "testo"})
    assert off["display"]["Dettaglio"] == 1
    on = _run_js(js[HELPER], {"Altro": "Yes", "Dettaglio": "testo"})
    assert on["display"]["Dettaglio"] == 3  # shown again as the user chose it: print only
