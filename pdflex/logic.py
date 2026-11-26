"""Formulas, totals and conditions of a saved form, computed by PDFlex itself (Compila, Anteprima, Serie):
the same rules the form's scripts follow in Acrobat or Chrome (see pdflex.formula)."""
import re

import pymupdf

from pdflex.fields import (
    HELPER, NUMERIC, VISIBILITY, _field_js, _fmt_from_js, _from_simplified, _holder, _safe_update, _set_visibility,
    _unescape, fmt_number, fmt_parts, mupdf_text,
)
from pdflex.formula import FormulaError, _truthy, as_text, evaluate, parse

_OPS = {"SUM": "SUM", "AVG": "AVERAGE", "MIN": "MIN", "MAX": "MAX"}


def format_value(x, fmt: str) -> str:
    """A result as its field shows it: a number in the field's format; text as it is."""
    if fmt_parts(fmt)[0] in NUMERIC:
        return fmt_number(x, fmt) if isinstance(x, float) else ""
    return as_text(x)


def _kind(doc, w) -> str:
    t = w.field_type
    if t == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
        return "checkbox"
    if t == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
        return "radio"
    if t in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX):
        return "dropdown"
    return "number" if fmt_parts(_fmt_from_js(_field_js(doc, w)))[0] in NUMERIC else "text"


def _calc_node(script: str):
    """The calculation as a formula tree: PDFlex's, Acrobat's simplified notation or a quick total; None for
    any other script (another program's, never run here)."""
    m = re.search(r"/\*\* PDFlex: (.*?) \*\*/", script, re.S)
    text = _unescape(m.group(1)) if m else None
    m = re.search(r"BVCALC(.*?)EVCALC", script, re.S)
    if text is None and m:
        text = _from_simplified(m.group(1))
    if text is not None:
        try:
            return parse(text)
        except FormulaError:
            return None
    m = re.search(r'AFSimple_Calculate\(\s*"(SUM|PRD|AVG|MIN|MAX)"\s*,\s*new Array\((.*?)\)', script)
    if not m:
        return None
    names = [("ref", n) for n in re.findall(r'"([^"]+)"', m.group(2))]
    if not names:
        return None
    if m.group(1) != "PRD":
        return ("call", _OPS[m.group(1)], names)
    node = names[0]
    for n in names[1:]:
        node = ("bin", "*", node, n)
    return node


def _value(w) -> str:
    """A widget's value as a formula reads it ("1" for a ticked check box)."""
    t = w.field_type
    if t == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
        return "1" if w.field_value in (True, w.on_state(), "Yes") else ""
    if t == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
        on = w.on_state()
        return on if w.field_value in (True, on) else ""
    v = w.field_value
    return "" if v in (None, False) else str(v)


class Logic:
    def __init__(self):
        self.kinds: dict[str, str] = {}
        self.formats: dict[str, str] = {}
        self.computed: dict = {}  # name -> formula tree, in calculation order
        self.conditions: dict = {}  # name -> condition tree
        self.shown: dict[str, str] = {}  # the visibility a conditional field has while its condition holds

    def run(self, values: dict[str, str]) -> tuple[dict[str, str], set[str]]:
        """The values with the totals and formulas worked out and the hidden fields emptied, and the hidden ones.
        ponytail: repeats until nothing changes (about n² for a big form); a single pass in dependency order
        if that ever matters."""
        vals = dict(values)
        hidden: set[str] = set()
        for _ in range(len(self.kinds) + 1):
            before = dict(vals)
            hidden = {n for n, node in self.conditions.items() if not _truthy(evaluate(node, vals, self.kinds))}
            for n in hidden:
                vals[n] = ""
            for n, node in self.computed.items():
                vals[n] = format_value(evaluate(node, vals, self.kinds), self.formats.get(n, ""))
            for n in hidden:
                vals[n] = ""
            if vals == before:
                break
        return vals, hidden

    def apply(self, doc) -> None:
        """Work it all out on the filled document: computed values written, hidden fields emptied and hidden."""
        if not self.computed and not self.conditions:
            return
        values = {}
        for page in doc:
            for w in page.widgets():
                if w.field_name and w.field_name != HELPER:
                    v = _value(w)
                    if v or w.field_name not in values:
                        values[w.field_name] = v
        new, hidden = self.run(values)
        for page in doc:
            for w in list(page.widgets()):  # never change a widget while the page is still walking them
                name = w.field_name
                if name in self.conditions:
                    _set_visibility(doc, w.xref, "hidden" if name in hidden else self.shown.get(name, ""))
                if name not in self.computed and name not in hidden:
                    continue
                if _value(w) == new.get(name, ""):
                    continue
                t = w.field_type
                if t in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
                    w.field_value = False
                else:
                    w.field_value = mupdf_text(new.get(name, ""))
                    if not w.field_value:  # PyMuPDF skips an empty value: the old one would stay
                        for x in {w.xref, _holder(doc, w.xref)}:
                            doc.xref_set_key(x, "V", "()")
                _safe_update(doc, w)


def form_logic(doc) -> Logic:
    logic = Logic()
    computed = {}
    for page in doc:
        for w in page.widgets():
            name = w.field_name
            if not name or name == HELPER or name in logic.kinds:
                continue
            logic.kinds[name] = _kind(doc, w)
            js = _field_js(doc, w)
            logic.formats[name] = _fmt_from_js(js)
            node = _calc_node(js.get("C", "")) if js.get("C") else None
            if node is not None:
                computed[name] = (_holder(doc, w.xref), node)
            holder = _holder(doc, w.xref)
            t, cond = doc.xref_get_key(holder, "PDFlexIf")
            if t == "string" and cond:
                try:
                    logic.conditions[name] = parse(cond)
                except FormulaError:
                    continue
                vis = doc.xref_get_key(holder, "PDFlexVis")[1]
                logic.shown[name] = vis if vis in VISIBILITY else ""
    t, v = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/CO")
    order = [int(n) for n in re.findall(r"(\d+) 0 R", v)] if t == "array" else []
    rank = {x: i for i, x in enumerate(order)}
    for name in sorted(computed, key=lambda n: rank.get(computed[n][0], len(rank))):
        logic.computed[name] = computed[name][1]
    return logic
