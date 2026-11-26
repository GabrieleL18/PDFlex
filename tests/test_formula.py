import json
import shutil
import subprocess

import pytest

from pdflex.formula import (
    FormulaError, as_text, check, evaluate, js_prelude, parse, plain_number, read_number, refs, rename, to_js,
)


def ev(text, values=None, kinds=None):
    return evaluate(parse(text), values or {}, kinds or {})


def test_parse_precedence():
    assert ev("1 + 2 * 3") == 7
    assert ev("NON 1 > 2 E 3 = 3 O 0") is True
    assert ev("2 × 3 ÷ 4") == 1.5
    assert ev("(1 + 2) * 3") == 9
    assert ev("-2 * 3") == -6


def test_decimal_comma_and_semicolons():
    assert ev("ARROTONDA(1,005; 2)") == 1.01
    assert ev("MAX(1,5; 2.5)") == 2.5
    assert ev("SOMMA(1;2;3)") == 6
    assert ev(",5 + 1") == 1.5


def test_english_names_and_case():
    assert ev("IF(TRUE; ROUND(2.25; 1); 0)") == 2.3
    assert ev("se(vero; 1; 2)") == 1
    assert ev("Media(2; 4)") == 3


def test_refs_with_odd_names():
    assert refs('[Prezzo unitario] * [Q.tà] + [a\\]b]') == {"Prezzo unitario", "Q.tà", "a]b"}


def test_rename_keeps_text():
    assert rename("[A]*2 + [AB]", "A", "Quantità") == "[Quantità]*2 + [AB]"
    assert rename("[A]", "A", "x]y") == "[x\\]y]"
    assert refs(rename("[A]", "A", "x]y")) == {"x]y"}


def test_read_number():
    assert read_number("1.234,50") == 1234.5
    assert read_number("1234,5") == 1234.5
    assert read_number("1234.5") == 1234.5
    assert read_number("1.234") == 1234.0
    assert read_number("€ 1.234,50") == 1234.5
    assert read_number("1.234,50 €") == 1234.5
    assert read_number("12,5%") == 0.125
    assert read_number("-3") == -3
    assert read_number("") is None
    assert read_number("abc") is None


def test_text_result_and_plain_number():
    assert ev('SE([Tipo] = "ditta"; "Partita IVA"; "Codice fiscale")', {"Tipo": "Ditta"},
              {"Tipo": "dropdown"}) == "Partita IVA"
    assert plain_number(2.5) == "2,5"
    assert plain_number(3.0) == "3"
    assert plain_number(-0.125) == "-0,13"
    assert as_text(None) == "" and as_text(True) == "Sì" and as_text(1234.5) == "1234,5"


def test_checkbox_and_radio_values():
    k = {"Altro": "checkbox"}
    assert ev("[Altro] = SÌ", {"Altro": "1"}, k) is True
    assert ev("[Altro] = SI", {"Altro": "Yes"}, k) is True
    assert ev("[Altro] = SÌ", {"Altro": ""}, k) is False
    assert ev("[Altro] = SÌ", {"Altro": "Off"}, k) is False
    assert ev('[Tipo] = "Persona fisica"', {"Tipo": "Persona_fisica"}, {"Tipo": "radio"}) is True


def test_numbers_from_fields_and_empty():
    k = {"A": "number", "B": "number"}
    assert ev("[A] * [B]", {"A": "2", "B": "1.234,50"}, k) == 2469
    assert ev("[A] + [B]", {"A": "", "B": "2"}, k) == 2
    assert ev("MEDIA([A]; [B])", {"A": "", "B": "4"}, k) == 4
    assert ev('[A] * "x"', {"A": "2"}, k) is None


def test_division_by_zero_is_empty():
    assert ev("1/0") is None


def test_errors():
    assert "Nessuno" in check("[Nessuno] + 1", {"A": "number"}, {})
    assert check("1 +", {}, {})
    assert check('[Tipo] = "Altro"', {"Tipo": "radio"}, {"Tipo": ["Ditta"]})
    assert check('[Tipo] = "Ditta"', {"Tipo": "radio"}, {"Tipo": ["Ditta"]}) == ""
    assert check("[A] * 2", {"A": "number"}, {}) == ""
    with pytest.raises(FormulaError):
        parse("PIPPO(1)")
    with pytest.raises(FormulaError):
        parse("[A")
    with pytest.raises(FormulaError):
        parse('"abc')


KINDS = {"Q": "number", "P": "number", "S": "number", "Pc": "number", "Altro": "checkbox", "Tipo": "radio",
         "Menu": "dropdown", "Nome": "text"}
CASES = [
    ("[Q] * [P] - [S]", {"Q": "2", "P": "1.234,50", "S": "10"}),
    ("ARROTONDA([P] / 3; 2)", {"P": "€ 1.000,00"}),
    ("[Pc] * 200", {"Pc": "12,5%"}),
    ("[Q] + [S]", {"Q": "", "S": "3"}),
    ("SE([Altro] = SÌ; \"sì\"; \"no\")", {"Altro": "Yes"}),
    ("SE([Altro]; 1; 2)", {"Altro": ""}),
    ('[Tipo] = "Persona fisica"', {"Tipo": "Persona_fisica"}),
    ('SE([Menu] = "roma"; "Lazio"; "Altro")', {"Menu": "Roma"}),
    ("MEDIA([Q]; [P]; [S])", {"Q": "", "P": "4", "S": "2"}),
    ("[Q] / [S]", {"Q": "1", "S": "0"}),
    ('[Nome] = "mario" E [Q] >= 2', {"Nome": "Mario", "Q": "2"}),
    ("ASS(-[Q]) + MIN(3; [P])", {"Q": "1,5", "P": "2"}),
    ("[Q] > [P]", {"Q": "10", "P": "9"}),
    ('[Nome] > "b"', {"Nome": "a"}),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not installed")
@pytest.mark.parametrize("text,values", CASES)
def test_js_agrees_with_python(text, values):
    py = as_text(evaluate(parse(text), values, KINDS))
    stub = {n: ("Off" if KINDS[n] == "checkbox" and not v else v) for n, v in values.items()}
    js = ("var _vals = " + json.dumps(stub) + ";\n"
          "var self = {getField: function(n) { return {value: (n in _vals) ? _vals[n] : ''}; }};\n"
          "(function() {\n" + js_prelude() + "\nvar r = " + to_js(parse(text), KINDS) + ";\n"
          "console.log(_fmt(r)); }).call(self);")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == py


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not installed")
@pytest.mark.parametrize("text,raw,expected", [("[Q] * 100", 0.125, "12,5"), ("[Q] * 2", 3.125, "6,25"),
                                               ("[Q] + 1", -0.125, "0,88")])
def test_js_reads_raw_reader_numbers(text, raw, expected):
    """Readers keep a number field's value as a plain number, and valueAsString as "0.125": never 125."""
    js = ("var self = {getField: function(n) { return {value: " + repr(raw) + ", valueAsString: '" + repr(raw) + "'}; }};\n"
          "(function() {\n" + js_prelude() + "\nvar r = " + to_js(parse(text), KINDS) + ";\n"
          "console.log(_fmt(r)); }).call(self);")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected == as_text(evaluate(parse(text), {"Q": raw}, KINDS))


def test_read_number_leading_zero_is_decimal():
    assert read_number("0.125") == 0.125 and read_number("-0.125") == -0.125
    assert read_number("1.234") == 1234  # Italian thousands still
