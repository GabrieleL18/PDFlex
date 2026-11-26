"""Formulas and conditions written by the user: "ARROTONDA([Quantità] * [Prezzo]; 2)", "[Altro] = SÌ O [Importo] > 1000".

The text is parsed into a tree, never run: the tree is evaluated here (Compila, Anteprima, Serie) and translated
into JavaScript for PDF readers (Acrobat, Edge, Chrome). Both follow the same rules, so they give the same result."""
import json
import re
from decimal import ROUND_HALF_UP, Decimal

from pdflex.i18n import tr


class FormulaError(ValueError):
    def __init__(self, msg: str, pos: int = 0):
        super().__init__(msg)
        self.pos = pos


FUNCS = {"SOMMA": "SUM", "SUM": "SUM", "MEDIA": "AVERAGE", "AVERAGE": "AVERAGE", "MIN": "MIN", "MAX": "MAX",
         "ARROTONDA": "ROUND", "ROUND": "ROUND", "ASS": "ABS", "ABS": "ABS", "SE": "IF", "IF": "IF"}
_ARITY = {"SUM": (1, 99), "AVERAGE": (1, 99), "MIN": (1, 99), "MAX": (1, 99), "ROUND": (1, 2), "ABS": (1, 1),
          "IF": (2, 3)}
_WORDS = {"E": "and", "AND": "and", "O": "or", "OR": "or", "NON": "not", "NOT": "not",
          "SÌ": True, "SI": True, "YES": True, "VERO": True, "TRUE": True, "NO": False, "FALSO": False, "FALSE": False}
_CMP = ("=", "<>", "<=", ">=", "<", ">")
_FALSE_MARKS = {"", "off", "0", "no", "false", "falso", "n"}  # a check box's value when it is not ticked

_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<num>\d+(?:[.,]\d+)?|[.,]\d+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>\[(?:\\.|\[[^\[\]\\]*\]|[^\[\]\\])*\])
  | (?P<op><>|<=|>=|[-+*/×÷()=<>;])
  | (?P<word>[^\W\d_]\w*)
""", re.VERBOSE)
_REF = re.compile(r"\[(?:\\.|\[[^\[\]\\]*\]|[^\[\]\\])*\]")  # brackets inside a name are fine when paired: [Nome[0]]


def _ref_name(token: str) -> str:
    return re.sub(r"\\(.)", r"\1", token[1:-1])


def ref_text(name: str) -> str:
    """[name] as written in a formula: as it is when it reads back the same ([Nome[0]]), else with
    brackets and \\ escaped ([a\\]b])."""
    plain = "[" + name.replace("\\", "\\\\") + "]"
    if _REF.fullmatch(plain) and _ref_name(plain) == name:
        return plain
    return "[" + re.sub(r"([\\\[\]])", r"\\\1", name) + "]"


def _tokens(text: str) -> list[tuple[str, str, int]]:
    out, i = [], 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m:
            what = text[i]
            if what == '"':
                raise FormulaError(tr("Testo tra virgolette non chiuso"), i)
            if what == "[":
                raise FormulaError(tr("Nome di campo senza ] di chiusura"), i)
            raise FormulaError(tr("Carattere non valido: {0}").format(what), i)
        kind = m.lastgroup
        if kind != "space":
            out.append((kind, m.group(), i))
        i = m.end()
    return out


class _Parser:
    def __init__(self, text: str):
        self.toks, self.i, self.end = _tokens(text), 0, len(text)

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else ("end", "", self.end)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def word(self, t):
        return _WORDS.get(t[1].upper()) if t[0] == "word" else None

    def expect(self, value: str):
        t = self.take()
        if t[1] != value:
            raise FormulaError(tr("Manca «{0}»").format(value), t[2])

    def parse(self):
        if not self.toks:
            raise FormulaError(tr("La formula è vuota"), 0)
        node = self.or_()
        t = self.peek()
        if t[0] != "end":
            raise FormulaError(tr("Non capisco «{0}»").format(t[1]), t[2])
        return node

    def or_(self):
        node = self.and_()
        while self.word(self.peek()) == "or":
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.word(self.peek()) == "and":
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.word(self.peek()) == "not":
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        node = self.add()
        if self.peek()[1] in _CMP:
            op = self.take()[1]
            node = ("bin", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek()[1] in ("*", "/", "×", "÷"):
            op = {"×": "*", "÷": "/"}.get(self.take()[1], self.toks[self.i - 1][1])
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek()[1] == "-":
            self.take()
            return ("neg", self.unary())
        if self.peek()[1] == "+":
            self.take()
            return self.unary()
        return self.atom()

    def atom(self):
        kind, value, pos = self.take()
        if kind == "num":
            return ("num", float(value.replace(",", ".")))
        if kind == "str":
            return ("str", value[1:-1].replace('""', '"'))
        if kind == "ref":
            return ("ref", _ref_name(value))
        if value == "(":
            node = self.or_()
            self.expect(")")
            return node
        if kind == "word":
            w = _WORDS.get(value.upper())
            if isinstance(w, bool):
                return ("bool", w)
            name = FUNCS.get(value.upper())
            if name is None:
                raise FormulaError(tr("Funzione sconosciuta: {0}").format(value), pos)
            self.expect("(")
            args = [self.or_()]
            while self.peek()[1] == ";":
                self.take()
                args.append(self.or_())
            self.expect(")")
            lo, hi = _ARITY[name]
            if not lo <= len(args) <= hi:
                raise FormulaError(tr("{0}: numero di valori sbagliato").format(value.upper()), pos)
            return ("call", name, args)
        if kind == "end":
            raise FormulaError(tr("La formula finisce troppo presto"), pos)
        raise FormulaError(tr("Non capisco «{0}»").format(value), pos)


def parse(text: str):
    return _Parser(text).parse()


def _walk(node):
    yield node
    for child in node[1:]:
        if isinstance(child, tuple):
            yield from _walk(child)
        elif isinstance(child, list):
            for c in child:
                yield from _walk(c)


def refs(text: str) -> set[str]:
    return {n[1] for n in _walk(parse(text)) if n[0] == "ref"}


def rename(text: str, old: str, new: str) -> str:
    """Every [old] becomes [new]; the rest of the text stays exactly as typed."""
    return rename_all(text, {old: new})


def rename_all(text: str, names: dict[str, str]) -> str:
    """Several names at once: {A: A_2, A_2: A_2_2} does not turn A into A_2_2."""
    try:
        toks = _tokens(text)
    except FormulaError:
        return text
    out, last = [], 0
    for kind, value, pos in toks:
        if kind == "ref" and _ref_name(value) in names and names[_ref_name(value)] != _ref_name(value):
            out += [text[last:pos], ref_text(names[_ref_name(value)])]
            last = pos + len(value)
    return "".join(out) + text[last:]


def _prep(node, kinds):
    """A radio group is stored with its choices cleaned ("Persona fisica" -> "Persona_fisica"): text compared
    with it is cleaned the same way."""
    from pdflex.fields import clean_value
    if node[0] == "bin" and node[1] in _CMP:
        a, b = _prep(node[2], kinds), _prep(node[3], kinds)
        for x, y in ((a, b), (b, a)):
            if x[0] == "ref" and kinds.get(x[1]) == "radio" and y[0] == "str" and y[1]:
                y2 = ("str", clean_value(y[1]))
                a, b = (a, y2) if y is b else (y2, b)
        return ("bin", node[1], a, b)
    return tuple(_prep(c, kinds) if isinstance(c, tuple) else [_prep(x, kinds) for x in c] if isinstance(c, list)
                 else c for c in node)


def check(text: str, kinds: dict[str, str], choices: dict[str, list[str]]) -> str:
    """"" when the formula is fine, else what is wrong."""
    from pdflex.fields import clean_value
    try:
        node = parse(text)
    except FormulaError as e:
        return str(e)
    missing = sorted({n[1] for n in _walk(node) if n[0] == "ref" and n[1] not in kinds})
    if missing:
        return tr("Campi che non esistono: ") + ", ".join(missing)
    for n in _walk(node):
        if n[0] == "bin" and n[1] in _CMP:
            for x, y in ((n[2], n[3]), (n[3], n[2])):
                if (x[0] == "ref" and kinds.get(x[1]) == "radio" and y[0] == "str" and y[1]
                        and clean_value(y[1]) not in choices.get(x[1], [])):
                    return tr("«{0}» non è una delle scelte di {1}").format(y[1], x[1])
    return ""


# ---------------------------------------------------------------- the values

_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)$")


def read_number(s) -> float | None:
    """A number as people write it: "1.234,50", "1234,5", "€ 1.234,50", "12,5%"; or as readers keep it, "1234.5"."""
    if isinstance(s, bool):
        return float(s)
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"[\s €$£]|CHF", "", str(s), flags=re.I)
    percent = s.endswith("%")
    if percent:
        s = s[:-1]
    if "," in s:
        s = s.replace(".", "").replace(",", ".", 1)
    elif re.match(r"^-?\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".", "")
    if not _NUM.match(s):
        return None
    n = float(s)
    return n / 100 if percent else n


def _round(x: float, n=0) -> float:
    """Half away from zero, on the number as it is written (1,005 -> 1,01): what JavaScript's _round does."""
    n = max(0, min(10, int(n)))
    sign = -1 if x < 0 else 1
    r = float(Decimal(repr(abs(x))).quantize(Decimal(1).scaleb(-n), ROUND_HALF_UP)) * sign
    return 0.0 if r == 0 else r


def plain_number(x: float) -> str:
    """At most 2 decimals, decimal comma, no thousands separator: 2,5 / 3 / -0,13."""
    return re.sub(r"\.?0+$", "", f"{_round(x, 2):.2f}").replace(".", ",")


def as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return tr("Sì") if v else tr("No")
    if isinstance(v, float):
        return plain_number(v)
    return str(v)


def _value(name: str, values: dict, kinds: dict):
    v = values.get(name, "")
    v = "" if v is None else str(v)
    kind = kinds.get(name, "text")
    if kind == "checkbox":
        return v.strip().lower() not in _FALSE_MARKS
    if kind == "number":
        return read_number(v) if v.strip() else None
    if kind == "radio" and v == "Off":
        return ""
    return v


def _num(x) -> float:
    """For arithmetic: empty is 0, text that is not a number is NaN (the result is then empty)."""
    if x is None or x == "":
        return 0.0
    n = read_number(x)
    return float("nan") if n is None else n


def _truthy(x) -> bool:
    if isinstance(x, str):
        return x != ""
    return bool(x) and x == x  # NaN is false


def _compare(op: str, a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        a, b = float(_truthy(a)), float(_truthy(b))
    else:
        na = None if a is None or a == "" else read_number(a)
        nb = None if b is None or b == "" else read_number(b)
        a_ok = na is not None or a is None or a == ""
        b_ok = nb is not None or b is None or b == ""
        if a_ok and b_ok:
            a, b = na or 0.0, nb or 0.0
        else:
            a = (as_text(a) if not isinstance(a, str) else a).lower()
            b = (as_text(b) if not isinstance(b, str) else b).lower()
    return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


def _done(x):
    return None if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))) else x


def _call(name: str, args: list):
    if name in ("SUM", "AVERAGE", "MIN", "MAX"):
        nums = [_num(a) for a in args if not (a is None or a == "")]
        if any(n != n for n in nums):
            return None
        if name == "SUM":
            return sum(nums)
        if not nums:
            return None
        return {"AVERAGE": sum(nums) / len(nums), "MIN": min(nums), "MAX": max(nums)}[name]
    if name == "ROUND":
        x, n = _num(args[0]), _num(args[1]) if len(args) > 1 else 0.0
        return None if x != x or n != n else _round(x, n)
    x = _num(args[0])  # ABS
    return None if x != x else abs(x)


def _eval(node, values, kinds):
    t = node[0]
    if t in ("num", "str", "bool"):
        return node[1]
    if t == "ref":
        return _value(node[1], values, kinds)
    if t == "neg":
        return _done(-_num(_eval(node[1], values, kinds)))
    if t == "not":
        return not _truthy(_eval(node[1], values, kinds))
    if t == "and":
        return _truthy(_eval(node[1], values, kinds)) and _truthy(_eval(node[2], values, kinds))
    if t == "or":
        return _truthy(_eval(node[1], values, kinds)) or _truthy(_eval(node[2], values, kinds))
    if t == "call" and node[1] == "IF":
        args = node[2]
        if _truthy(_eval(args[0], values, kinds)):
            return _eval(args[1], values, kinds)
        return _eval(args[2], values, kinds) if len(args) > 2 else None
    if t == "call":
        return _done(_call(node[1], [_eval(a, values, kinds) for a in node[2]]))
    op, a, b = node[1], _eval(node[2], values, kinds), _eval(node[3], values, kinds)
    if op in _CMP:
        return _compare(op, a, b)
    x, y = _num(a), _num(b)
    if op == "/":
        return None if y == 0 else _done(x / y)
    return _done({"+": x + y, "-": x - y, "*": x * y}[op])


def evaluate(node, values: dict[str, str], kinds: dict[str, str]):
    """float, str, bool, or None (empty)."""
    return _eval(_prep(node, kinds), values, kinds)


# ---------------------------------------------------------------- JavaScript

def js_prelude() -> str:
    """The helpers every generated script starts with; the same rules as above, line by line."""
    return ("""var _doc = this;
function _rn(s) { if (typeof s == "number") return s; if (typeof s == "boolean") return s ? 1 : 0;
  s = String(s).replace(/[\\s\\u00a0\\u20ac$\\u00a3]|CHF/gi, ""); var pc = /%$/.test(s); if (pc) s = s.slice(0, -1);
  if (s.indexOf(",") >= 0) s = s.replace(/\\./g, "").replace(",", ".");
  else if (/^-?\\d{1,3}(\\.\\d{3})+$/.test(s)) s = s.replace(/\\./g, "");
  if (!/^[-+]?(\\d+\\.?\\d*|\\.\\d+)$/.test(s)) return null; var n = parseFloat(s); return pc ? n / 100 : n; }
function _v(name, kind) { var f = _doc.getField(name); if (!f) return kind == "checkbox" ? false : (kind == "number" ? null : "");
  var v = (typeof f.valueAsString == "string") ? f.valueAsString : f.value; v = (v === null || v === undefined) ? "" : String(v);
  if (kind == "checkbox") return ["", "off", "0", "no", "false", "falso", "n"].indexOf(v.replace(/^\\s+|\\s+$/g, "").toLowerCase()) < 0;
  if (kind == "number") return /\\S/.test(v) ? _rn(v) : null;
  if (kind == "radio" && v == "Off") return ""; return v; }
function _num(x) { if (x === null || x === "") return 0; var n = _rn(x); return n === null ? NaN : n; }
function _t(x) { if (typeof x == "string") return x !== ""; return !!x && x === x; }
function _done(x) { return (typeof x == "number" && !isFinite(x)) ? null : x; }
function _round(x, n) { n = Math.max(0, Math.min(10, Math.floor(n))); var s = x < 0 ? -1 : 1, a = Math.abs(x);
  var r = Number(Math.round(Number(String(a) + "e" + n)) + "e-" + n);
  if (isNaN(r)) r = Math.round(a * Math.pow(10, n)) / Math.pow(10, n); r = r * s; return r == 0 ? 0 : r; }
function _pl(x) { return _round(x, 2).toFixed(2).replace(/\\.?0+$/, "").replace(".", ","); }
function _fmt(v) { if (v === null || v === undefined) return ""; if (typeof v == "boolean") return v ? YES_WORD : NO_WORD;
  if (typeof v == "number") return _pl(v); return String(v); }
function _txt(x) { return typeof x == "string" ? x : _fmt(x); }
function _cmp(op, a, b) {
  if (typeof a == "boolean" || typeof b == "boolean") { a = _t(a) ? 1 : 0; b = _t(b) ? 1 : 0; }
  else { var na = (a === null || a === "") ? null : _rn(a), nb = (b === null || b === "") ? null : _rn(b);
    var aok = na !== null || a === null || a === "", bok = nb !== null || b === null || b === "";
    if (aok && bok) { a = na || 0; b = nb || 0; } else { a = _txt(a).toLowerCase(); b = _txt(b).toLowerCase(); } }
  if (op == "=") return a == b; if (op == "<>") return a != b; if (op == "<") return a < b;
  if (op == ">") return a > b; if (op == "<=") return a <= b; return a >= b; }
function _ar(op, a, b) { var x = _num(a), y = _num(b); if (op == "/") return y == 0 ? null : _done(x / y);
  return _done(op == "+" ? x + y : op == "-" ? x - y : x * y); }
function _f(name, args) {
  if (name == "SUM" || name == "AVERAGE" || name == "MIN" || name == "MAX") { var ns = [];
    for (var i = 0; i < args.length; i++) { if (args[i] === null || args[i] === "") continue; var n = _num(args[i]);
      if (n !== n) return null; ns.push(n); }
    if (name == "SUM") { var s = 0; for (var j = 0; j < ns.length; j++) s += ns[j]; return _done(s); }
    if (!ns.length) return null;
    if (name == "AVERAGE") { var t = 0; for (var k = 0; k < ns.length; k++) t += ns[k]; return _done(t / ns.length); }
    return _done(name == "MIN" ? Math.min.apply(null, ns) : Math.max.apply(null, ns)); }
  if (name == "ROUND") { var x = _num(args[0]), d = args.length > 1 ? _num(args[1]) : 0;
    return (x !== x || d !== d) ? null : _round(x, d); }
  var y = _num(args[0]); return y !== y ? null : Math.abs(y); }
""".replace("YES_WORD", json.dumps(tr("Sì"))).replace("NO_WORD", json.dumps(tr("No"))))


def _js(node, kinds) -> str:
    t = node[0]
    if t == "num":
        return repr(node[1])
    if t == "str":
        return json.dumps(node[1])  # ensure_ascii: accents as \\u00e0, quotes escaped
    if t == "bool":
        return "true" if node[1] else "false"
    if t == "ref":
        return f"_v({json.dumps(node[1])}, {json.dumps(kinds.get(node[1], 'text'))})"
    if t == "neg":
        return f"_done(-_num({_js(node[1], kinds)}))"
    if t == "not":
        return f"(!_t({_js(node[1], kinds)}))"
    if t in ("and", "or"):
        return f"(_t({_js(node[1], kinds)}) {'&&' if t == 'and' else '||'} _t({_js(node[2], kinds)}))"
    if t == "call" and node[1] == "IF":
        a = node[2]
        return f"(_t({_js(a[0], kinds)}) ? {_js(a[1], kinds)} : {_js(a[2], kinds) if len(a) > 2 else 'null'})"
    if t == "call":
        return f"_f({json.dumps(node[1])}, [{', '.join(_js(a, kinds) for a in node[2])}])"
    fn = "_cmp" if node[1] in _CMP else "_ar"
    return f"{fn}({json.dumps(node[1])}, {_js(node[2], kinds)}, {_js(node[3], kinds)})"


def to_js(node, kinds: dict[str, str]) -> str:
    return _js(_prep(node, kinds), kinds)
