import openpyxl
import pymupdf

from pdflex import open_pdf
from pdflex.batch import batch_fill, match_columns, read_table
from pdflex.editor import Field, save_with_fields


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
