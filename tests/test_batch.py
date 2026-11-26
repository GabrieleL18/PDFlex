import openpyxl
import pymupdf

from pdflex import open_pdf
from pdflex.batch import batch_fill, match_columns, read_table
from pdflex.fields import Field, save_with_fields


def _template(tmp_path, make_pdf):
    src = make_pdf(tmp_path, ["Modulo"])
    out = tmp_path / "modulo.pdf"
    save_with_fields(src, [Field("text", 0, (10, 10, 200, 30), "nome"), Field("checkbox", 0, (10, 40, 24, 54), "accetto"),
                           Field("radio", 0, (10, 60, 24, 74), "sesso", value="M"),
                           Field("radio", 0, (30, 60, 44, 74), "sesso", value="F"),
                           Field("dropdown", 0, (10, 80, 200, 100), "citta", options=["Roma", "Milano"])], out)
    return out


def _values(path):
    vals = {}
    for w in open_pdf(path)[0].widgets():
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            if w.field_value not in (False, "Off", None, ""):
                vals[w.field_name] = w.on_state()
        else:
            vals[w.field_name] = w.field_value
    return vals


def test_csv_semicolon_and_fill(make_pdf, tmp_path):
    csv = tmp_path / "dati.csv"
    csv.write_text("Nome;Accetto;Sesso;Città\nMario Rossi;sì;M;Milano\nAnna Bianchi;no;F;Roma\n", encoding="utf-8-sig")
    headers, rows = read_table(csv)
    assert headers == ["Nome", "Accetto", "Sesso", "Città"] and len(rows) == 2
    tpl = _template(tmp_path, make_pdf)
    mapping = match_columns(["nome", "accetto", "sesso", "citta"], headers)
    assert mapping == {"nome": "Nome", "accetto": "Accetto", "sesso": "Sesso", "citta": "Città"}
    out = batch_fill(tpl, rows, mapping, tmp_path / "out", name_col="Nome")
    assert [p.name for p in out] == ["Mario Rossi.pdf", "Anna Bianchi.pdf"]
    v = _values(out[0])
    assert v["nome"] == "Mario Rossi" and v["accetto"] in (True, "Yes") and v["sesso"] == "M" and v["citta"] == "Milano"
    v = _values(out[1])
    assert v["accetto"] in (False, "Off") and v["sesso"] == "F"


def test_xlsx_numbers_and_flatten(make_pdf, tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["nome", "citta"])
    ws.append([123, "Roma"])
    ws.append([None, None])  # empty rows are skipped
    x = tmp_path / "dati.xlsx"
    wb.save(x)
    headers, rows = read_table(x)
    assert rows == [{"nome": "123", "citta": "Roma"}]
    out = batch_fill(_template(tmp_path, make_pdf), rows, match_columns(["nome", "citta"], headers),
                     tmp_path / "out", flatten=True)
    assert out[0].name == "modulo_001.pdf"
    doc = open_pdf(out[0])
    assert doc[0].first_widget is None and "123" in doc[0].get_text()


def test_same_names_do_not_overwrite(make_pdf, tmp_path):
    rows = [{"nome": "Mario"}, {"nome": "Mario"}]
    out = batch_fill(_template(tmp_path, make_pdf), rows, {"nome": "nome"}, tmp_path / "out", name_col="nome")
    assert [p.name for p in out] == ["Mario.pdf", "Mario_2.pdf"]


def test_filled_copy_keeps_checkbox_mark(make_pdf, tmp_path):
    import re
    from pdflex import open_pdf
    from pdflex.fields import Field, save_with_fields
    from pdflex.fill import fill_values
    src = tmp_path / "m.pdf"
    save_with_fields(make_pdf(tmp_path, ["x"]), [Field("checkbox", 0, (10, 10, 30, 30), "c", mark="8")], src)
    doc = fill_values(src.read_bytes(), {"c": "1"})
    w = next(doc[0].widgets())
    t, v = doc.xref_get_key(w.xref, "AP/N")
    on = [int(x) for k, x in re.findall(r"/(\w+)\s+(\d+) 0 R", v) if k != "Off"]
    assert w.field_value not in (False, "Off") and b"(8) Tj" in doc.xref_stream(on[0])


def _batch_tab(qapp, make_pdf, tmp_path, fields, csv_text):
    from pdflex.batch import BatchTab
    out = tmp_path / "m.pdf"
    save_with_fields(make_pdf(tmp_path, ["Modulo"]), fields, out)
    table = tmp_path / "dati.csv"
    table.write_text(csv_text, encoding="utf-8")
    tab = BatchTab()
    tab.set_template(str(out))
    tab.set_table(str(table))
    return tab


def test_mapping_table_prefilled_and_editable(qapp, make_pdf, tmp_path):
    tab = _batch_tab(qapp, make_pdf, tmp_path, [Field("text", 0, (10, 10, 200, 30), "Nome"),
                                                Field("text", 0, (10, 40, 200, 60), "Codice")],
                     "nome;cod_cliente\nMario;C1\n")
    assert tab.column_box("Nome").currentText() == "nome"
    assert tab.column_box("Codice").currentData() is None
    assert "1 campi su 2" in tab.match.text()
    box = tab.column_box("Codice")
    box.setCurrentIndex(box.findData("cod_cliente"))
    assert tab.current_mapping() == {"Nome": "nome", "Codice": "cod_cliente"}
    assert "2 campi su 2" in tab.match.text() and tab.b_run.isEnabled()


def test_computed_fields_not_listed(qapp, make_pdf, tmp_path):
    from pdflex.fields import HELPER
    tab = _batch_tab(qapp, make_pdf, tmp_path,
                     [Field("text", 0, (10, 10, 200, 30), "Prezzo"),
                      Field("text", 0, (10, 40, 200, 60), "Totale", formula="[Prezzo] * 2"),
                      Field("text", 0, (10, 70, 200, 90), "Nota", show_if="[Prezzo] > 1")],
                     "Prezzo;Totale\n3;99\n")
    assert tab.field_names == ["Prezzo", "Nota"]
    assert tab.column_box("Totale") is None and tab.column_box(HELPER) is None
