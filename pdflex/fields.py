"""Form fields: the model, reading them from a PDF and writing them back."""
import base64
import copy
import dataclasses
import json
import re
import unicodedata
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import pymupdf

from pdflex import open_pdf, write_atomic
from pdflex.formula import (
    _truthy, check as check_formula, evaluate as eval_formula, js_prelude, parse as parse_formula, ref_text,
    refs as formula_refs, rename_all, to_js,
)
from pdflex.i18n import tr

FIELD_KINDS = ("text", "multiline", "checkbox", "radio", "dropdown", "date", "signature", "button")

_TYPES = {
    "text": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "multiline": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "date": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "checkbox": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "radio": pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
    "dropdown": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
    "button": pymupdf.PDF_WIDGET_TYPE_BUTTON,
}

# Push buttons (made by another program): shown, moved and deleted; Field.default is the caption,
# their script stays as it is


@dataclass
class Field:
    kind: str
    page: int
    rect: tuple[float, float, float, float]
    name: str
    required: bool = False
    default: str = ""
    font_size: float = 0
    options: list[str] = dc_field(default_factory=list)
    value: str = ""
    fmt: str = ""  # see FORMATS; with options after "|": "number|2|1" (decimals, thousands), "date|dd/mm/yy"
    calc: list[str] = dc_field(default_factory=list)  # names of the fields this one sums
    calc_op: str = "SUM"  # see CALC_OPS
    vmin: str = ""  # numeric fields: smallest / largest value accepted ("" no limit; decimal point)
    vmax: str = ""
    comb: int = 0  # text split into this many boxes, one character each (codice fiscale); 0 = normal
    image: str = dc_field(default="", repr=False)  # kind "image": PNG as base64 (a hand-drawn signature)
    font: str = ""  # "" Helvetica, "TiRo" Times, "Cour" Courier (same width for every letter)
    color: str = ""  # text colour "#rrggbb"; "" black
    align: int = 0  # 0 left, 1 centre, 2 right
    mark: str = ""  # check box: the ZapfDingbats character drawn when ticked (see MARKS); "" the check mark
    tooltip: str = ""  # shown when the mouse is over the field
    readonly: bool = False  # a computed total is read-only anyway, whatever this says
    maxlen: int = 0  # most characters a text field takes (0 no limit; with comb the boxes are the limit)
    visibility: str = ""  # see VISIBILITY
    border_color: str = ""  # "" the usual grey, "none" no border, else "#rrggbb"
    fill_color: str = ""  # "" no background, else "#rrggbb"
    border_width: int = 1  # 1-3 pt (see BORDER_WIDTHS)
    border_style: str = ""  # see BORDER_STYLES
    formula: str = ""  # text field: its value is this formula's result (pdflex.formula); "" typed by hand
    show_if: str = ""  # shown only while this condition is true, emptied when it is not; "" always shown
    # where it came from in the source PDF (0 = drawn here); not part of equality
    xref: int = dc_field(default=0, compare=False, repr=False)
    parent: int = dc_field(default=0, compare=False, repr=False)
    orig: "Field | None" = dc_field(default=None, compare=False, repr=False)
    # a calculation written by another program (not PDFlex's, not Acrobat's simplified notation): kept untouched
    foreign_calc: bool = dc_field(default=False, compare=False, repr=False)


HELPER = "PDFlex_logica"  # hidden field whose calculation shows and hides the fields with a condition


def formula_kind(f: Field) -> str:
    """How a formula reads this field: a number, a text, ticked or not, a choice."""
    if f.kind in ("checkbox", "radio", "dropdown"):
        return f.kind
    return "number" if f.kind == "text" and fmt_parts(f.fmt, f.kind)[0] in NUMERIC else "text"


def _formula_info(fields) -> tuple[dict, dict]:
    """kinds and radio choices, what formulas need to know about the form's fields."""
    kinds, choices = {}, {}
    for f in fields:
        if f.kind not in ("image", "button", "signature"):
            kinds[f.name] = formula_kind(f)
        if f.kind == "radio":
            choices.setdefault(f.name, []).append(clean_value(f.value))
    return kinds, choices


def _unescape(text: str) -> str:
    return re.sub(r"\\u([0-9A-Fa-f]{4})", lambda u: chr(int(u.group(1), 16)), text)


def _formula_js(f: Field, kinds: dict) -> str:
    """The calculation script: the formula's text kept in a comment (so it reopens as typed), then its code."""
    numeric = fmt_parts(f.fmt, f.kind)[0] in NUMERIC
    put = 'event.value = (typeof r == "number") ? r : "";' if numeric else "event.value = _fmt(r);"
    return (f"/** PDFlex: {_js_escape(f.formula).replace('*/', '* /')} **/\n(function() {{\n{js_prelude()}"
            f"var r = {to_js(parse_formula(f.formula), kinds)};\n{put}\n}}).call(this);")


def _from_simplified(body: str) -> str:
    """Acrobat's "simplified field notation" (Quantita * Prezzo) as a formula: names in brackets."""
    return re.sub(r"(?:\\.|[^\W\d])(?:\\.|[\w.])*",
                  lambda m: ref_text(re.sub(r"\\(.)", r"\1", m.group())), body.strip())


def to_view(page, rect) -> tuple:
    """PDF (unrotated) coordinates -> what the user sees on a possibly rotated page."""
    return tuple((pymupdf.Rect(rect) * page.rotation_matrix).normalize())


def from_view(page, rect) -> tuple:
    return tuple((pymupdf.Rect(rect) * page.derotation_matrix).normalize())


def suggest_save_path(path: str | Path) -> str:
    p = Path(path)
    return str(p if p.stem.endswith("_modulo") else p.with_name(p.stem + "_modulo.pdf"))


def clean_value(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9_-]", "_", s)
    return s or "opzione"


def _xref_ref(doc, xref, key):
    t, v = doc.xref_get_key(xref, key)
    return int(v.split()[0]) if t == "xref" else 0


FORMATS = {"": tr("Testo libero"), "number": tr("Numero"), "amount": tr("Importo"), "percent": tr("Percentuale"),
           "date": tr("Data"), "time": tr("Ora"), "cap": "CAP", "cf": tr("Codice fiscale"), "phone": tr("Telefono"), "email": tr("Email")}
NUMERIC = ("number", "amount", "percent")
CURRENCIES = ("€", "$", "£", "CHF", "")  # "" no symbol
CALC_OPS = {"SUM": tr("Somma"), "AVG": tr("Media"), "MIN": tr("Minimo"), "MAX": tr("Massimo"), "PRD": tr("Prodotto")}
DATE_PATTERNS = {"date": {"dd/mm/yyyy": tr("gg/mm/aaaa"), "dd/mm/yy": tr("gg/mm/aa"), "mm/yyyy": tr("mm/aaaa"),
                          "dd/mm/yyyy HH:MM": tr("gg/mm/aaaa hh:mm")},
                 "time": {"HH:MM": tr("hh:mm"), "HH:MM:ss": tr("hh:mm:ss")}}
FONTS = {"": "Helvetica", "TiRo": "Times", "Cour": tr("Courier (larghezza fissa)")}
ALIGN = {0: tr("Sinistra"), 1: tr("Centro"), 2: tr("Destra")}
MARKS = {"": tr("Spunta"), "8": tr("Croce"), "l": tr("Punto"), "n": tr("Quadrato"), "u": tr("Rombo"), "H": tr("Stella")}
VISIBILITY = {"": tr("Visibile"), "hidden": tr("Nascosto"), "print": tr("Solo in stampa"), "screen": tr("Solo a schermo")}
_VIS_FLAGS = {"": 4, "hidden": 2, "print": 36, "screen": 0}  # annotation flags: Hidden 2, Print 4, NoView 32
BORDER_STYLES = {"": tr("Pieno"), "dashed": tr("Tratteggiato"), "beveled": tr("In rilievo"), "inset": tr("Incassato"),
                 "underline": tr("Sottolineato")}
BORDER_WIDTHS = {1: tr("Sottile"), 2: tr("Medio"), 3: tr("Spesso")}
_BS = {"": "S", "dashed": "D", "beveled": "B", "inset": "I", "underline": "U"}  # PDF border styles
_GREY = (0.6, 0.6, 0.6)  # the border every field gets unless chosen otherwise
LOOK_KEYS = ("border_color", "fill_color", "border_width", "border_style")


def _hex_any(rgb) -> str:
    """"#rrggbb" from PyMuPDF's 0..1 floats, black included; "" when there is no colour."""
    rgb = list(rgb or [])[:3]
    if len(rgb) == 1:
        rgb *= 3
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb) if len(rgb) == 3 else ""


def _apply_look(doc, w, f) -> None:
    """Border and background of the widget `w` from the field `f` (drawn by the update that follows)."""
    if f.border_color == "none":
        w.border_width, w.border_color = 0, None
    else:
        w.border_width = f.border_width or 1
        w.border_color = _rgb(f.border_color) if f.border_color else _GREY
    w.fill_color = _rgb(f.fill_color) if f.fill_color else None
    w.border_style = _BS.get(f.border_style, "S")
    w.border_dashes = [3, 3] if f.border_style == "dashed" else None
    if doc is not None:  # PyMuPDF leaves an old colour in place when it is taken away
        for key, gone in (("MK/BC", f.border_color == "none"), ("MK/BG", not f.fill_color)):
            if gone and doc.xref_get_key(w.xref, key)[0] != "null":
                doc.xref_set_key(w.xref, key, "null")
        if f.border_style != "dashed" and doc.xref_get_key(w.xref, "BS/D")[0] != "null":
            doc.xref_set_key(w.xref, "BS/D", "null")


def _look_of(w) -> dict:
    bc, width = list(w.border_color or []), w.border_width or 0
    if len(bc) == 1:
        bc *= 3
    if not bc or not width:
        border = "none"
    elif all(abs(c - g) < 0.01 for c, g in zip(bc, _GREY)):
        border = ""
    else:
        border = _hex_any(bc)
    style = {v: k for k, v in _BS.items()}.get((w.border_style or "S")[:1].upper(), "")
    return {"border_color": border, "fill_color": _hex_any(w.fill_color), "border_style": style,
            "border_width": min(max(round(width), 1), 3)}


def _visibility(flags: int) -> str:
    if flags & 2:
        return "hidden"
    if flags & 32:
        return "print"
    return "" if flags & 4 else "screen"


def _set_visibility(doc, xref: int, vis: str) -> None:
    """Only the Hidden / Print / NoView bits change: the others (locked...) stay."""
    t, v = doc.xref_get_key(xref, "F")
    flags = int(v) if t == "int" else 0
    doc.xref_set_key(xref, "F", str(flags & ~(2 | 4 | 32) | _VIS_FLAGS[vis]))


def _mark_of(doc, xref: int) -> str:
    t, v = doc.xref_get_key(xref, "MK/CA")
    return v if t == "string" and v not in ("3", "4") else ""  # 3 and 4: the check marks


def _apply_mark(doc, xref: int, mark: str) -> None:
    """The check box's character: /MK/CA for viewers that redraw it, and the drawn look. PyMuPDF 2.1 always
    draws the check (3), whatever /MK/CA says, so the character is swapped in the look it made."""
    doc.xref_set_key(xref, "MK/CA", pymupdf.get_pdf_str(mark))
    t, v = doc.xref_get_key(xref, "AP/N")
    for state, x in re.findall(r"/(\w+)\s+(\d+) 0 R", v if t == "dict" else ""):
        if state != "Off":
            x = int(x)
            doc.update_stream(x, re.sub(rb"\((.)\) Tj", b"(" + mark.encode() + b") Tj", doc.xref_stream(x)))
# the scripts are the standard Acrobat ones (AFNumber_Format...), the ones every PDF form library recognises
_EMAIL_V = ('if (event.value && !/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(event.value)) '
            '{ app.alert("Indirizzo email non valido"); event.rc = false; }')
_CF_V = ('if (event.value && !/^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$/i'
         '.test(event.value)) { app.alert("Codice fiscale non valido"); event.rc = false; }')
_UPPER_K = "event.change = event.change.toUpperCase();"
_PHONE_K = "event.rc = /^[0-9 +.\\/-]*$/.test(event.change);"
_CAP_K = 'AFSpecial_KeystrokeEx("99999");'


def fmt_parts(fmt: str, kind: str = "text") -> tuple[str, list[str]]:
    """("number", ["2", "1"]) from "number|2|1". A date field without a format is dd/mm/yyyy.
    An amount also has its currency and whether it goes before the number: "amount|2|1|€|1"."""
    base, *args = fmt.split("|")
    if kind == "date" and base not in DATE_PATTERNS:
        return "date", ["dd/mm/yyyy"]
    if base in DATE_PATTERNS and not args:
        args = [next(iter(DATE_PATTERNS[base]))]
    if base in NUMERIC:
        default = ["2", "1", "€", "1"] if base == "amount" else ["2", "1"]  # amounts saved before: euro, before
        args = (args + default[len(args):])[:len(default)]  # 2 decimals, thousands separator: "number" alone
    return base, args


def fmt_label(fmt: str, kind: str = "text") -> str:
    base, args = fmt_parts(fmt, kind)
    if base in NUMERIC:
        return tr("{0}, {1} decimali").format(FORMATS[base], args[0])
    if base in DATE_PATTERNS:
        return DATE_PATTERNS[base].get(args[0], args[0])
    return FORMATS.get(base, "")


def _js_escape(text: str) -> str:
    """Non-ASCII as \\uXXXX: viewers misread raw UTF-8 (a euro sign) inside scripts."""
    return "".join(c if ord(c) < 128 else f"\\u{ord(c):04X}" for c in text)


def _range_js(vmin: str, vmax: str) -> str | None:
    if not vmin and not vmax:
        return None
    return f"AFRange_Validate({'true' if vmin else 'false'}, {vmin or 0}, {'true' if vmax else 'false'}, {vmax or 0});"


def _fmt_js(fmt: str, kind: str = "text", vmin: str = "", vmax: str = "") -> tuple:
    """(format, keystroke, validate) JavaScript for a field's format; Italian separators (1.234,56)."""
    base, args = fmt_parts(fmt, kind)
    if base in NUMERIC:
        dec, sep = int(args[0]), 2 if args[1] != "0" else 3  # 2: 1.234,56  3: 1234,56
        if base == "percent":
            return f"AFPercent_Format({dec}, {sep});", f"AFPercent_Keystroke({dec}, {sep});", _range_js(vmin, vmax)
        cur, before = "", "true"
        if base == "amount" and args[2]:
            before = "true" if args[3] != "0" else "false"
            cur = _js_escape(args[2] + " " if before == "true" else " " + args[2])
        return (f'AFNumber_Format({dec}, {sep}, 0, 0, "{cur}", {before});',
                f'AFNumber_Keystroke({dec}, {sep}, 0, 0, "", true);', _range_js(vmin, vmax))
    if base in DATE_PATTERNS:
        fn = "AFDate" if base == "date" else "AFTime"
        return f'{fn}_FormatEx("{args[0]}");', f'{fn}_KeystrokeEx("{args[0]}");', None
    return {"cap": (None, _CAP_K, None), "cf": (None, _UPPER_K, _CF_V), "phone": (None, _PHONE_K, None),
            "email": (None, None, _EMAIL_V)}.get(base, (None, None, None))


def _fmt_from_js(js: dict) -> str:
    """The format back from the scripts, PDFlex's own or other programs' (Acrobat writes the same ones)."""
    f, k, v = js.get("F", ""), js.get("K", ""), js.get("V", "")
    m = re.search(r'AFNumber_Format\(\s*(\d+)\s*,\s*(\d+)\s*,[^,]*,[^,]*,\s*"([^"]*)"\s*(?:,\s*(\w+))?', f)
    if m:
        dec, sep, cur, before = m.groups()
        cur = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda u: chr(int(u.group(1), 16)), cur).strip()
        if not cur:
            return f"number|{dec}|{0 if sep in '13' else 1}"
        return f"amount|{dec}|{0 if sep in '13' else 1}|{cur}|{0 if before == 'false' else 1}"
    m = re.search(r"AFPercent_Format\(\s*(\d+)\s*,\s*(\d+)", f)
    if m:
        return f"percent|{m.group(1)}|{0 if m.group(2) in '13' else 1}"
    m = re.search(r'AF(Date|Time)_FormatEx\(\s*"([^"]*)"', f)
    if m:
        return f"{m.group(1).lower()}|{m.group(2)}"
    if "AFDate_Format" in f:  # the older numbered form: keep it a date
        return "date|dd/mm/yyyy"
    if _CAP_K in k:
        return "cap"
    if "Codice fiscale" in v:
        return "cf"
    if _PHONE_K in k:
        return "phone"
    if "app.alert" in v and "@" in v:
        return "email"
    return ""


def _hex(rgb) -> str:
    """"#rrggbb" from PyMuPDF's 0..1 floats; "" for black (the default)."""
    rgb = list(rgb or [])[:3]
    if len(rgb) == 1:  # grey
        rgb *= 3
    return "" if len(rgb) != 3 or not any(rgb) else "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def _rgb(color: str) -> tuple:
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)) if color else (0, 0, 0)


def _field_js(doc, w) -> dict[str, str]:
    """JavaScript of the field's actions (F format, K keystroke, V validate, C calculate).

    PyMuPDF writes them but does not read them back, so read the objects directly. The action may be
    its own object or written inside the field (Acrobat and others): the key path follows both."""
    out = {}
    for holder in (w.xref, _xref_ref(doc, w.xref, "Parent")):
        if not holder:
            continue
        for key in ("F", "K", "V", "C"):
            if key in out:
                continue
            t, v = doc.xref_get_key(holder, f"AA/{key}/JS")
            if t == "xref":
                out[key] = (doc.xref_stream(int(v.split()[0])) or b"").decode("latin-1")
            elif t == "string":
                out[key] = v
    return out


def load_fields(doc: pymupdf.Document) -> list[Field]:
    out = []
    for page in doc:
        for w in page.widgets():
            kind = next((k for k, t in _TYPES.items() if t == w.field_type), None)
            if kind is None or w.field_name == HELPER:
                continue  # list box / push button: not editable here, kept untouched on save
            js = _field_js(doc, w) if kind == "text" else {}
            fmt = _fmt_from_js(js)
            if kind == "text":
                if fmt.split("|")[0] in DATE_PATTERNS:
                    kind = "date"
                elif w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
                    kind = "multiline"
            f = Field(kind, page.number, tuple(w.rect), w.field_name or "",
                      required=bool(w.field_flags & pymupdf.PDF_FIELD_IS_REQUIRED),
                      font_size=w.text_fontsize or 0, font=w.text_font if w.text_font in FONTS else "",
                      color=_hex(w.text_color), fmt=fmt,
                      xref=w.xref, parent=_xref_ref(doc, w.xref, "Parent"))
            q = _inherited(doc, f, "Q")
            f.align = int(q) if q in ("1", "2") else 0
            if kind in ("checkbox", "radio"):
                on = w.on_state()
                f.value = on if kind == "radio" else ""
                f.default = "1" if w.field_value in (True, on, "Yes") else ""
            elif kind == "dropdown":
                f.options = [c if isinstance(c, str) else c[0] for c in (w.choice_values or [])]
                f.default = w.field_value or ""
            elif kind == "button":
                f.default = w.button_caption or ""
            elif kind != "signature":
                f.default = w.field_value or ""
            if kind == "text" and w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB:
                f.comb = w.text_maxlen or 0
            elif kind in ("text", "multiline", "date"):
                f.maxlen = w.text_maxlen or 0
            if kind == "checkbox":
                f.mark = _mark_of(doc, w.xref)
            f.tooltip = w.field_label or ""
            t, v = doc.xref_get_key(w.xref, "F")
            f.visibility = _visibility(int(v) if t == "int" else 0)
            for key, value in _look_of(w).items():
                setattr(f, key, value)
            c = js.get("C", "")
            m = re.search(r"AFSimple_Calculate\(\s*\"(SUM|PRD|AVG|MIN|MAX)\"\s*,\s*new Array\((.*?)\)", c)
            mine, simple = re.search(r"/\*\* PDFlex: (.*?) \*\*/", c, re.S), re.search(r"BVCALC(.*?)EVCALC", c, re.S)
            if mine:
                f.formula = _unescape(mine.group(1))
            elif simple:
                f.formula = _from_simplified(simple.group(1))
            elif m:
                f.calc_op, f.calc = m.group(1), re.findall(r'"([^"]+)"', m.group(2))
            elif c.strip():
                f.foreign_calc = True
            f.show_if = _inherited(doc, f, "PDFlexIf") or ""
            if f.show_if:  # the Hidden flag of the moment is the condition's doing: the user's choice is kept apart
                vis = _inherited(doc, f, "PDFlexVis") or ""
                f.visibility = vis if vis in VISIBILITY else ""
            m = re.search(r"AFRange_Validate\(\s*(true|false)\s*,\s*([-\d.]+)\s*,\s*(true|false)\s*,\s*([-\d.]+)",
                          js.get("V", ""))
            if m:
                f.vmin = m.group(2) if m.group(1) == "true" else ""
                f.vmax = m.group(4) if m.group(3) == "true" else ""
            f.readonly = bool(w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY) and not f.calc and not f.formula
            f.orig = copy.deepcopy(f)
            out.append(f)
    return out


def _check_names(fields):
    if any(not f.name.strip() for f in fields):
        raise ValueError(tr("Ogni campo deve avere un nome."))
    by_name = {}
    for f in fields:
        by_name.setdefault(f.name, []).append(f)
    dup = []
    for name, group in by_name.items():
        kinds = {f.kind for f in group}
        if len(kinds) > 1 and "radio" in kinds:
            dup.append(name)
        elif kinds != {"radio"} and len(group) > 1:
            # the same field shown on several pages (one parent in the file) is fine
            parents = {f.parent for f in group}
            if 0 in parents or len(parents) > 1:
                dup.append(name)
    if dup:
        raise ValueError(tr("Nomi di campo duplicati: ") + ", ".join(sorted(dup)))
    menus = sorted(f.name for f in fields if f.kind == "dropdown" and f.default and f.default not in f.options
                   and (not f.xref or f.default != f.orig.default))
    if menus:
        raise ValueError(tr("Il valore iniziale deve essere una delle opzioni del menu: ") + ", ".join(menus))
    missing = sorted({n for f in fields for n in f.calc if n not in by_name})
    if missing:
        raise ValueError(tr("La somma usa campi che non esistono: ") + ", ".join(missing))
    kinds, choices = _formula_info(fields)
    deps = {}
    for f in fields:
        deps.setdefault(f.name, set()).update(f.calc)
        for text in (f.formula, f.show_if):
            if text:
                msg = check_formula(text, kinds, choices)
                if msg:
                    raise ValueError(f"{f.name}: {msg}")
                deps[f.name] |= formula_refs(text)
    path = _cycle(deps)
    if path:
        raise ValueError(tr("Calcolo circolare: ") + " → ".join(path))
    bad = []
    for name, group in by_name.items():
        values = [clean_value(f.value) for f in group if f.kind == "radio"]
        if len(values) != len(set(values)) or "Off" in values:
            bad.append(name)
    if bad:
        raise ValueError(tr("Nel gruppo di scelta ") + ", ".join(sorted(bad)) +
                         tr(' due opzioni hanno lo stesso valore, oppure una vale "Off" (riservato).'))


def _cycle(deps: dict[str, set[str]]) -> list[str]:
    """A chain of names that comes back to its start (A → B → A), or []."""
    state = {}

    def visit(n, path):
        if state.get(n) == 1:
            return path[path.index(n):] + [n]
        if state.get(n) == 2 or n not in deps:
            return []
        state[n] = 1
        for m in sorted(deps[n]):
            found = visit(m, path + [n])
            if found:
                return found
        state[n] = 2
        return []
    for n in deps:
        found = visit(n, [])
        if found:
            return found
    return []


def _new_widget(f: Field, kinds: dict | None = None) -> pymupdf.Widget:
    w = pymupdf.Widget()
    w.field_type = _TYPES[f.kind]
    w.field_name = f.name
    w.rect = pymupdf.Rect(f.rect)
    w.text_fontsize = f.font_size
    w.text_font = f.font or "Helv"
    w.text_color = _rgb(f.color)
    _apply_look(None, w, f)  # by default a grey box, visible for whoever fills the form
    if f.required:
        w.field_flags |= pymupdf.PDF_FIELD_IS_REQUIRED
    if f.kind == "multiline":
        w.field_flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
    if f.kind == "text" and f.comb:
        w.text_maxlen = f.comb
        w.field_flags |= pymupdf.PDF_TX_FIELD_IS_COMB
    elif f.kind in ("text", "multiline") and f.maxlen:
        w.text_maxlen = f.maxlen
    if f.tooltip:
        w.field_label = f.tooltip
    js = _scripts(f, kinds)
    if f.kind in ("text", "multiline", "date"):
        w.script_format, w.script_stroke, w.script_change = js["F"], js["K"], js["V"]
    if js["C"]:
        w.script_calc = js["C"]
    if f.calc or f.formula or f.readonly:  # a total is computed, not typed
        w.field_flags |= pymupdf.PDF_FIELD_IS_READ_ONLY
    if f.kind == "dropdown":
        w.choice_values = list(f.options)
    if f.kind in ("text", "multiline", "date") or (f.kind == "dropdown" and f.default in f.options):
        w.field_value = mupdf_text(f.default)
    if f.kind == "checkbox":
        w.field_value = f.default == "1"
    return w


def _inherited(doc, f: Field, key: str):
    """Field-level keys (Ff, DA, Opt) may sit on the widget or on its parent: read the one in use."""
    for x in (f.xref, f.parent):
        if x and doc.xref_get_key(x, key)[0] != "null":
            return doc.xref_get_key(x, key)[1]
    return None


def _set_inherited(doc, f: Field, key: str, value: str) -> None:
    for x in {f.xref, f.parent} - {0}:
        doc.xref_set_key(x, key, value)


def _scripts(f: Field, kinds: dict | None = None) -> dict[str, str | None]:
    """The field's JavaScript by action key (None: no script), as PDFlex writes it."""
    fmt, stroke, validate = (_fmt_js(f.fmt, f.kind, f.vmin, f.vmax) if f.kind in ("text", "multiline", "date")
                             else (None,) * 3)
    calc = None
    if f.formula:
        calc = _formula_js(f, kinds or {})
    elif f.calc:
        calc = f"AFSimple_Calculate(\"{f.calc_op}\", new Array(" + ", ".join(f'"{n}"' for n in f.calc) + "));"
    return {"F": fmt, "K": stroke, "V": validate, "C": calc}


def _set_scripts(doc, f: Field, keys, kinds: dict | None = None) -> None:
    """Write format / sum scripts into the field itself: every other property stays as it was.
    The calculation order is written once for the whole form, by build_pdf."""
    holder = f.parent or f.xref
    scripts = _scripts(f, kinds)
    for key in keys:
        for x in {f.xref, f.parent} - {0}:  # the old one, wherever it was
            if doc.xref_get_key(x, f"AA/{key}")[0] != "null":
                doc.xref_set_key(x, f"AA/{key}", "null")
        if scripts[key]:
            doc.xref_set_key(holder, f"AA/{key}", f"<</S/JavaScript/JS {pymupdf.get_pdf_str(scripts[key])}>>")


def _holder(doc, xref: int) -> int:
    return _xref_ref(doc, xref, "Parent") or xref


def _start_values(fields) -> dict[str, str]:
    """The values the form opens with, as formulas read them."""
    out = {}
    for f in fields:
        if f.kind == "checkbox":
            out[f.name] = "1" if f.default == "1" else ""
        elif f.kind == "radio":
            if f.default == "1":
                out[f.name] = clean_value(f.value)
            else:
                out.setdefault(f.name, "")
        elif f.kind not in ("image", "button", "signature"):
            out[f.name] = f.default
    return out


def _write_conditions(doc, fields, placed: dict, kinds: dict, array) -> int:
    """Each condition into its field (/PDFlexIf, with the user's own visibility in /PDFlexVis), the fields
    hidden or shown as the form opens, and the hidden helper field whose calculation keeps doing it while
    the form is filled in (a check box has no calculation of its own). Returns the helper's xref, 0 if none."""
    start = _start_values(fields)
    conds = []
    for f in fields:
        if id(f) not in placed:
            continue
        x = placed[id(f)]
        holder = _holder(doc, x)
        if f.show_if:
            doc.xref_set_key(holder, "PDFlexIf", pymupdf.get_pdf_str(f.show_if))
            doc.xref_set_key(holder, "PDFlexVis", pymupdf.get_pdf_str(f.visibility) if f.visibility else "null")
            on = _truthy(eval_formula(parse_formula(f.show_if), start, kinds))
            _set_visibility(doc, x, f.visibility if on else "hidden")
            if f.name not in [c.name for c in conds]:
                conds.append(f)
        elif doc.xref_get_key(holder, "PDFlexIf")[0] != "null":  # the condition was taken away
            for key in ("PDFlexIf", "PDFlexVis"):
                doc.xref_set_key(holder, key, "null")
            _set_visibility(doc, x, f.visibility)
    if not conds:
        return 0
    shown = {"": "display.visible", "print": "display.noView", "screen": "display.noPrint", "hidden": "display.hidden"}
    lines = []
    for f in conds:
        empty = '"Off"' if f.kind in ("checkbox", "radio") else '""'
        lines.append(f"(function() {{ var on = _t({to_js(parse_formula(f.show_if), kinds)}); "
                     f"var f = _doc.getField({json.dumps(f.name)}); if (!f) return; "
                     f"f.display = on ? {shown[f.visibility]} : display.hidden; "
                     f"if (!on && f.valueAsString != {empty}) f.value = {empty}; }})();")
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = HELPER
    w.rect = pymupdf.Rect(0, 0, 1, 1)
    w.field_flags |= pymupdf.PDF_FIELD_IS_READ_ONLY
    w.border_width, w.border_color = 0, None
    w.script_calc = "(function() {\n" + js_prelude() + "\n".join(lines) + "\n}).call(this);"
    annot = array.add(doc[0], w)
    _set_visibility(doc, annot.xref, "hidden")
    return annot.xref


def _write_calc_order(doc, fields, placed: dict, helper: int) -> None:
    """Readers recompute only the fields in the form's calculation order (/CO), in that order: each formula
    after the fields it reads, the conditions last. Calculations of other programs keep their place first."""
    computed = {f.name: f for f in fields if (f.formula or f.calc) and id(f) in placed}
    order, seen = [], set()

    def visit(n):
        if n in seen or n not in computed:
            return
        seen.add(n)
        f = computed[n]
        for r in sorted(formula_refs(f.formula)) if f.formula else f.calc:
            visit(r)
        order.append(_holder(doc, placed[id(f)]))
    for n in computed:
        visit(n)
    if helper:
        order.append(_holder(doc, helper))
    cat = doc.pdf_catalog()
    t, v = doc.xref_get_key(cat, "AcroForm/CO")
    old = [int(n) for n in re.findall(r"(\d+) 0 R", v)] if t == "array" else []
    alive = {_holder(doc, w.xref) for page in doc for w in page.widgets()}
    keep = [x for x in old if x in alive and x not in order]
    new = list(dict.fromkeys(keep + order))
    if new or t != "null":
        doc.xref_set_key(cat, "AcroForm/CO", "[" + " ".join(f"{n} 0 R" for n in new) + "]")


def _update_widget(doc, f: Field, kinds: dict | None = None) -> None:
    """Change only what the user edited, so properties PDFlex does not show survive."""
    o = f.orig
    # PyMuPDF 2.1 writes these on the widget only, but viewers read them from the parent field.
    # Written first: update() below redraws the field's look from them (font, size, options)
    if f.name != o.name:
        doc.xref_set_key(f.parent or f.xref, "T", pymupdf.get_pdf_str(f.name))
    locked = bool(f.readonly or f.calc or f.formula)  # a total is computed, not typed
    was_locked = bool(o.readonly or o.calc or o.formula)
    multi, was_multi = f.kind == "multiline", o.kind == "multiline"
    if f.required != o.required or f.comb != o.comb or locked != was_locked or multi != was_multi:
        flags = int(_inherited(doc, f, "Ff") or 0)
        for bit, on, was in ((pymupdf.PDF_FIELD_IS_REQUIRED, f.required, o.required),
                             (pymupdf.PDF_TX_FIELD_IS_COMB, f.comb, o.comb),
                             (pymupdf.PDF_TX_FIELD_IS_MULTILINE, multi, was_multi),
                             (pymupdf.PDF_FIELD_IS_READ_ONLY, locked, was_locked)):
            if bool(on) != bool(was):
                flags = flags | bit if on else flags & ~bit
        _set_inherited(doc, f, "Ff", str(flags))
    if f.comb != o.comb or f.maxlen != o.maxlen:
        limit = f.comb or f.maxlen
        _set_inherited(doc, f, "MaxLen", str(limit) if limit else "null")
    if f.tooltip != o.tooltip:
        _set_inherited(doc, f, "TU", pymupdf.get_pdf_str(f.tooltip) if f.tooltip else "null")
    if f.kind == "checkbox" and f.mark != o.mark:  # drawn by _safe_update below
        doc.xref_set_key(f.xref, "MK/CA", pymupdf.get_pdf_str(f.mark or "3"))
    if f.font_size != o.font_size or f.font != o.font or f.color != o.color:
        da = _inherited(doc, f, "DA") or "0 0 0 rg /Helv 0 Tf"
        if f.color != o.color:
            ink = " ".join(f"{c:g}" for c in _rgb(f.color)) + " rg"
            da, n = re.subn(r"(?:[\d.]+\s+){2}[\d.]+\s+rg|[\d.]+\s+g(?=\s)", ink, da, count=1)
            da = da if n else f"{ink} {da}"
        if f.font_size != o.font_size:
            da = re.sub(r"[\d.]+ Tf", f"{f.font_size:g} Tf", da)
        if f.font != o.font:
            da = re.sub(r"/\S+(\s+[\d.]+ Tf)", rf"/{f.font or 'Helv'}\1", da)
        _set_inherited(doc, f, "DA", pymupdf.get_pdf_str(da))
    if f.options != o.options:
        _set_inherited(doc, f, "Opt", "[" + "".join(pymupdf.get_pdf_str(x) for x in f.options) + "]")
    if f.align != o.align:
        _set_inherited(doc, f, "Q", str(f.align))
    if f.default != o.default and not f.default and f.kind not in ("checkbox", "radio", "button"):
        _set_inherited(doc, f, "V", "()")  # PyMuPDF skips an empty value: the old one would stay
    page = doc[f.page]  # keep the page alive: the widget only holds a weak reference to it
    w = page.load_widget(f.xref)
    if f.rect != o.rect:
        w.rect = pymupdf.Rect(f.rect)
    if any(getattr(f, k) != getattr(o, k) for k in LOOK_KEYS):
        _apply_look(doc, w, f)
    if f.default != o.default:
        if f.kind == "checkbox":
            w.field_value = f.default == "1"
        elif f.kind != "radio" and f.default:
            w.field_value = mupdf_text(f.default)
    _safe_update(doc, w)
    if f.visibility != o.visibility:
        _set_visibility(doc, f.xref, f.visibility)
    changed = [k for k, (a, b) in {"F": (f.fmt, o.fmt), "K": (f.fmt, o.fmt),
                                   "V": ((f.fmt, f.vmin, f.vmax), (o.fmt, o.vmin, o.vmax)),
                                   "C": ((f.calc, f.calc_op, f.formula), (o.calc, o.calc_op, o.formula))
                                   }.items() if a != b]
    if changed:
        _set_scripts(doc, f, changed, kinds)


def _safe_update(doc, w) -> None:
    """w.update(), keeping the widget's own actions. update() rewrites them from what PyMuPDF read, and it
    reads none of them: without this, moving a field made in another program would wipe its format script.
    A check box keeps its mark too (update() always draws the check)."""
    saved = {}
    for key in ("AA", "A"):
        t, v = doc.xref_get_key(w.xref, key)
        saved[key] = (t, v, doc.xref_object(int(v.split()[0])) if t == "xref" else None)
    mark = _mark_of(doc, w.xref) if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX else ""
    w.update()
    if mark:
        _apply_mark(doc, w.xref, mark)
    for key, (t, v, obj) in saved.items():
        if t == "dict":
            doc.xref_set_key(w.xref, key, v)
        elif t == "xref":
            doc.update_object(int(v.split()[0]), obj)
            doc.xref_set_key(w.xref, key, v)


def _hide_fields(doc) -> None:
    """No border, no background: on paper the form looks as if it had no fields. They are all still there,
    names, formats, fonts included, for the program that fills them in."""
    for page in doc:
        for w in list(page.widgets()):
            if w.field_type not in _TYPES.values() or w.field_type == pymupdf.PDF_WIDGET_TYPE_BUTTON:
                continue
            for key in ("MK/BC", "MK/BG"):
                if doc.xref_get_key(w.xref, key)[0] != "null":
                    doc.xref_set_key(w.xref, key, "null")
            w = page.load_widget(w.xref)
            w.border_width, w.border_color, w.fill_color = 0, None, None
            _safe_update(doc, w)


def _set_radio_state(doc, xref, old, new, checked, was_checked):
    for key in ("AP/N", "AP/D"):
        t, obj = doc.xref_get_key(xref, key)
        if t == "dict":
            doc.xref_set_key(xref, key, obj.replace(f"/{old}", f"/{new}"))
    doc.xref_set_key(xref, "AS", f"/{new}" if checked else "/Off")
    parent = _xref_ref(doc, xref, "Parent")
    if parent and checked:
        doc.xref_set_key(parent, "V", f"/{new}")
    elif parent and (was_checked or doc.xref_get_key(parent, "V")[1] in ("/None", "null")):
        doc.xref_set_key(parent, "V", "/Off")


class _FieldsArray:
    """ponytail: PyMuPDF 2.1 looks for each new widget's name through the whole /AcroForm/Fields array
    (O(n²): 80 s for 4,700 fields). While widgets are added the array is kept empty and its entries gathered
    here; a radio button, which must find its group, and changes to existing fields see the whole array. Drop when PyMuPDF stops scanning."""

    def __init__(self, doc):
        self.doc, self.items, self.ok = doc, [], True

    def _slot(self):
        cat = self.doc.pdf_catalog()
        t, v = self.doc.xref_get_key(cat, "AcroForm")
        return (int(v.split()[0]), "Fields") if t == "xref" else (cat, "AcroForm/Fields")

    def _take(self) -> list[str]:
        if not self.ok:
            return []
        xref, key = self._slot()
        t, v = self.doc.xref_get_key(xref, key)
        if t == "xref":
            v = self.doc.xref_object(int(v.split()[0]), compressed=True)
        elif t != "array":
            return []
        refs = re.findall(r"\d+ \d+ R", v)
        if re.sub(r"\d+ \d+ R|[\s\[\]]", "", v):  # something else than references: leave it all alone
            self.ok = False
            return []
        self.doc.xref_set_key(xref, key, "[]")
        return refs

    def add(self, page, widget):
        if widget.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            self.put_back()
        elif not self.items:
            self.items = self._take()
        annot = page.add_widget(widget)
        self.items += self._take()
        return annot

    def put_back(self):
        if self.ok and self.items:
            xref, key = self._slot()
            have = self._take()  # anything added since
            self.doc.xref_set_key(xref, key, "[" + " ".join(self.items + have) + "]")
            self.items = []


def build_pdf(src_path, fields: list[Field], blank: bool = False, step=None) -> pymupdf.Document:
    """The form with these fields. blank: for a program that fills it in later, fields empty and invisible.
    step(n): called as fields are placed, n of len(fields), for a progress bar."""
    _check_names(fields)
    kinds = _formula_info(fields)[0]
    if blank:  # push buttons go too: the program that fills it in has no use for "Popola test"
        fields = [dataclasses.replace(f, default="") for f in fields if f.kind != "button"]
    doc = open_pdf(src_path)
    keep = {f.xref for f in fields if f.xref}
    for page in doc:
        for w in list(page.widgets()):
            # fields the user deleted go; list boxes / push buttons are kept as they are
            if w.field_type in _TYPES.values() and w.xref not in keep:
                page.delete_widget(w)
    placed = {}  # id(field) -> widget xref, for the tab order
    order = []
    array = _FieldsArray(doc)
    for n, f in enumerate(fields):
        if step and n % 25 == 0:
            step(n)
        order.append(f)
        if f.xref:
            placed[id(f)] = f.xref
            if f == f.orig:
                continue
            array.put_back()  # existing fields are changed with the whole array in place, as PyMuPDF expects
            _update_widget(doc, f, kinds)
            if f.kind == "radio" and (f.value != f.orig.value or f.default != f.orig.default):
                _set_radio_state(doc, f.xref, f.orig.value, clean_value(f.value), f.default == "1",
                                 f.orig.default == "1")
            continue
        if f.kind == "image":  # not a field: the picture becomes part of the page
            doc[f.page].insert_image(pymupdf.Rect(f.rect), stream=base64.b64decode(f.image), keep_proportion=True)
            continue
        page = doc[f.page]
        annot = array.add(page, _new_widget(f, kinds))
        placed[id(f)] = annot.xref
        if f.align:  # PyMuPDF has no setting for it: written into the field, then the look redrawn
            _set_inherited(doc, dataclasses.replace(f, xref=annot.xref, parent=_xref_ref(doc, annot.xref, "Parent")),
                           "Q", str(f.align))
            _safe_update(doc, page.load_widget(annot.xref))
        _set_visibility(doc, annot.xref, f.visibility)
        if f.kind == "checkbox" and f.mark:
            _apply_mark(doc, annot.xref, f.mark)
        if f.kind == "radio":
            # ponytail: PyMuPDF 2.1 names every new radio "on" state /None, so all buttons in a group
            # act as one. Rename the state to the button's value; drop when PyMuPDF fixes it.
            _set_radio_state(doc, annot.xref, "None", clean_value(f.value), f.default == "1", False)
    for f in fields:  # a formula reads the other fields by their kind: rewritten in case one changed
        if f.formula and f.xref:
            _set_scripts(doc, f, ["C"], kinds)
    helper = _write_conditions(doc, fields, placed, kinds, array)
    array.put_back()
    _write_calc_order(doc, fields, placed, helper)
    _apply_tab_order(doc, [(f.page, placed[id(f)]) for f in order if id(f) in placed])
    if blank:
        _hide_fields(doc)
    return doc


def _apply_tab_order(doc, page_xrefs) -> None:
    """Tab order = order of the page's /Annots array (what Acrobat and browsers follow)."""
    for page in doc:
        wanted = [x for pno, x in page_xrefs if pno == page.number]
        t, v = doc.xref_get_key(page.xref, "Annots")
        if not wanted or t not in ("array", "xref"):
            continue
        holder = int(v.split()[0]) if t == "xref" else None
        current = [int(n) for n in re.findall(r"(\d+) 0 R", doc.xref_object(holder) if holder else v)]
        rest = [x for x in current if x not in wanted]  # comments, kept list boxes...
        arr = "[" + " ".join(f"{x} 0 R" for x in rest + list(dict.fromkeys(wanted))) + "]"
        if holder:
            doc.update_object(holder, arr)
        else:
            doc.xref_set_key(page.xref, "Annots", arr)


def align_rects(rects: list[tuple], how: str) -> list[tuple]:
    """Align/size/distribute rectangles (x0, y0, x1, y1), all in the same coordinates."""
    if len(rects) < 2:
        return list(rects)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects)
    y1 = max(r[3] for r in rects)
    first = rects[0]
    out = []
    for r in rects:
        w, h = r[2] - r[0], r[3] - r[1]
        if how == "left":
            r = (x0, r[1], x0 + w, r[3])
        elif how == "right":
            r = (x1 - w, r[1], x1, r[3])
        elif how == "top":
            r = (r[0], y0, r[2], y0 + h)
        elif how == "bottom":
            r = (r[0], y1 - h, r[2], y1)
        elif how == "hcenter":
            r = ((x0 + x1 - w) / 2, r[1], (x0 + x1 + w) / 2, r[3])
        elif how == "vcenter":
            r = (r[0], (y0 + y1 - h) / 2, r[2], (y0 + y1 + h) / 2)
        elif how == "same_width":
            r = (r[0], r[1], r[0] + first[2] - first[0], r[3])
        elif how == "same_height":
            r = (r[0], r[1], r[2], r[1] + first[3] - first[1])
        out.append(r)
    if how in ("distribute_v", "distribute_h"):
        axis = 1 if how == "distribute_v" else 0
        idx = sorted(range(len(rects)), key=lambda i: rects[i][axis])
        sizes = [rects[i][axis + 2] - rects[i][axis] for i in idx]
        start, end = rects[idx[0]][axis], max(r[axis + 2] for r in rects)
        gap = (end - start - sum(sizes)) / (len(rects) - 1)
        pos = start
        for i, size in zip(idx, sizes):
            r = list(rects[i])
            r[axis], r[axis + 2] = pos, pos + size
            out[i] = tuple(r)
            pos += size + gap
    return out


def snap(rect, grid: float = 5) -> tuple:
    return tuple(round(v / grid) * grid for v in rect)


_SAMPLES = [  # (words in the field name, example value)
    (("nome", "name"), "Mario Rossi"), (("cognome", "surname"), "Rossi"), (("mail",), "mario.rossi@esempio.it"),
    (("tel", "cell", "phone"), "333 123 4567"), (("citt", "comune", "city"), "Roma"),
    (("indirizzo", "via", "address"), "Via Garibaldi 10"), (("cap", "zip"), "00100"),
    (("cf", "fiscale"), "RSSMRA80A01H501U"), (("iva",), "01234567890"), (("importo", "prezzo", "euro"), "1.250,00"),
]


def _sample_text(f_name: str, multiline: bool) -> str:
    n = f_name.lower()
    for keys, value in _SAMPLES:
        if any(k in n for k in keys):
            return value
    return tr("Testo di esempio su\npiù righe") if multiline else tr("Testo di esempio")


def fmt_number(n: float, fmt: str) -> str:
    """A number as a numeric format shows it: "€ 1.234,50", "12,50%" (a percentage is kept as 0.125)."""
    base, args = fmt_parts(fmt)
    if base == "percent":
        n *= 100
    s = f"{n:,.{int(args[0])}f}".replace(",", " ").replace(".", ",").replace(" ", "." if args[1] != "0" else "")
    if base == "amount" and args[2]:
        return f"{args[2]} {s}" if args[3] != "0" else f"{s} {args[2]}"
    return s + "%" if base == "percent" else s


def sample_value(fmt: str, name: str, multiline: bool = False) -> str:
    """An example already written as the format shows it (PyMuPDF does not run the format scripts)."""
    from datetime import datetime
    base, args = fmt_parts(fmt)
    if base in NUMERIC:
        return fmt_number(0.125 if base == "percent" else 1234.5, fmt)
    if base in DATE_PATTERNS:
        py = {"dd": "%d", "mm": "%m", "yyyy": "%Y", "yy": "%y", "HH": "%H", "MM": "%M", "ss": "%S"}
        return datetime.now().strftime(re.sub(r"yyyy|yy|dd|mm|HH|MM|ss", lambda m: py[m.group()], args[0]))
    return {"cap": "00100", "cf": "RSSMRA80A01H501U", "phone": "333 123 4567",
            "email": "mario.rossi@esempio.it"}.get(base) or _sample_text(name, multiline)


def mupdf_text(value) -> str:
    """A field value as MuPDF takes it: plain text it can encode. Anything else (a number from Excel,
    a [value, label] pair, a stray surrogate) raised inside MuPDF and left it broken: PDFlex crashed
    at the next drawing of the page, the preview's 'access violation'."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).encode("utf-8", "replace").decode("utf-8")


def fill_sample(doc: pymupdf.Document) -> None:
    """Fill the empty text fields and menus with a plausible example, for the preview.

    Check boxes and choices stay as they start (their initial value): ticking them all
    made the preview contradict what was set in "Selezionata all'apertura"."""
    for page in doc:
        for w in list(page.widgets()):  # never change a widget while the page is still walking them
            t = w.field_type
            if t == pymupdf.PDF_WIDGET_TYPE_TEXT and not w.field_value:
                multiline = bool(w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
                if w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB and w.text_maxlen:
                    n = w.text_maxlen
                    w.field_value = "RSSMRA80A01H501U"[:n] if "fisc" in (w.field_name or "") else ("1234567890" * 10)[:n]
                else:
                    w.field_value = mupdf_text(sample_value(_fmt_from_js(_field_js(doc, w)), w.field_name or "",
                                                            multiline))
            elif t == pymupdf.PDF_WIDGET_TYPE_COMBOBOX and not w.field_value and w.choice_values:
                w.field_value = mupdf_text(w.choice_values[0])
            else:
                continue
            w.update()
    from pdflex.logic import form_logic  # totals, formulas and conditions, as on the filled form
    form_logic(doc).apply(doc)


_BLOCK_KEYS = ("kind", "name", "required", "default", "font_size", "options", "value", "fmt", "calc", "calc_op", "vmin",
               "vmax", "comb", "image", "font", "color", "align", "mark", "tooltip", "readonly", "maxlen", "visibility",
               "border_color", "fill_color", "border_width", "border_style", "formula", "show_if")


def make_block(fields: list[Field], rects: list[tuple]) -> list[dict]:
    """Fields (with their on-screen rects) -> a reusable block, positions relative to its top-left corner."""
    kept = [(f, r) for f, r in zip(fields, rects) if f.kind != "button"]  # a button's script can't be copied
    if not kept:
        return []
    x0, y0 = min(r[0] for _, r in kept), min(r[1] for _, r in kept)
    return [{**{k: copy.deepcopy(getattr(f, k)) for k in _BLOCK_KEYS},
             "rect": [r[0] - x0, r[1] - y0, r[2] - x0, r[3] - y0]} for f, r in kept]


NUMBERED = re.compile(r"^(.*?)(\[(\d+)\]|\((\d+)\))$")  # "Nome[0]" (an array) or "Voce (1)"


def next_name(name: str, taken: set[str]) -> str:
    """`name` if free, else the next free one: "Nome[0]" -> "Nome[1]", "Voce (1)" -> "Voce (2)",
    any other name -> name_2, name_3..."""
    if name not in taken:
        return name
    m = NUMBERED.match(name)
    if m:
        square = m.group(3) is not None
        n = int(m.group(3) if square else m.group(4))
        make = (lambda k: f"{m.group(1)}[{k}]") if square else (lambda k: f"{m.group(1)}({k})")
        while make(n) in taken:
            n += 1
        return make(n)
    out, n = f"{name}_2", 3
    while out in taken:
        out, n = f"{name}_{n}", n + 1
    return out


def place_block(block: list[dict], at: tuple, taken: set[str]) -> list[tuple[dict, tuple]]:
    """(field data, on-screen rect) for a block dropped at `at`; a name already in `taken` gets the next
    free one (see next_name: arrays like "Nome[0]" count up).

    A sum keeps pointing at the renamed fields of the same block. Numbered names all move on by the same
    step, the first one free for all of them: Nome[0..2] -> Nome[3..5] in the same order whatever was
    selected first, and a row Nome[0] + Cognome[0] stays a row (Nome[2] + Cognome[2], not Nome[2] + Cognome[1])."""
    names = list(dict.fromkeys(d["name"] for d in block))  # radio buttons of one group share the name

    def shifted(name, k):
        m = NUMBERED.match(name)
        if not m:
            return None
        return f"{m.group(1)}[{int(m.group(3)) + k}]" if m.group(3) is not None else f"{m.group(1)}({int(m.group(4)) + k})"
    k = 0
    if any(n in taken for n in names):
        k = 1
        while any(shifted(n, k) in taken for n in names):
            k += 1
    renamed = {}
    for n in names:
        name = shifted(n, k) if k and NUMBERED.match(n) else next_name(n, taken)
        taken.add(name)
        renamed[n] = name
    out = []
    for d in block:
        d = {**copy.deepcopy(d), "name": renamed[d["name"]]}
        d["calc"] = [renamed.get(c, c) for c in d.get("calc", [])]
        for key in ("formula", "show_if"):
            if d.get(key):
                d[key] = rename_all(d[key], renamed)
        x0, y0, x1, y1 = d.pop("rect")
        out.append((d, (at[0] + x0, at[1] + y0, at[0] + x1, at[1] + y1)))
    return out


def save_with_fields(src_path: str | Path, fields: list[Field], out_path: str | Path, blank: bool = False,
                     step=None) -> None:
    doc = build_pdf(src_path, fields, blank, step)
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    write_atomic(out_path, data)  # the original is untouched until the new file is complete
