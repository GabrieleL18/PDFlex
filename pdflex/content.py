"""A page's own content stream, read as PDF operators: the paths it draws, changed right where they are drawn.

Taking a path off with a redaction and drawing it again elsewhere loses its place among the other drawings: put
under everything, it vanished under a white sheet drawn first; put on top, it would cover the text. Changed in
place it keeps its place, its clip, its colour space and its transparency. Only paths drawn by the page's own
stream qualify (not those inside a form XObject); every change is checked against what MuPDF then draws."""
import re
from dataclasses import dataclass, replace

import pymupdf

_OPERAND = (rb"%[^\r\n]*|\((?:\.|[^\()]|\((?:\.|[^\()])*\))*\)|<<|>>|<[0-9A-Fa-f\s]*>|[\[\]{}]"
            rb"|/[^\s()<>\[\]{}/%]*|[-+.0-9]+")
# one operator with the operands before it: the regex walks the numbers, Python only sees the operators
_OP = re.compile(rb"\s*((?:(?:" + _OPERAND + rb")\s*)*)([^\s()<>\[\]{}/%0-9+.\-][^\s()<>\[\]{}/%]*)", re.S)
_PAINT = {b"S": "s", b"s": "s", b"f": "f", b"F": "f", b"f*": "f", b"B": "fs", b"B*": "fs", b"b": "fs", b"b*": "fs"}
_BUILD = {b"m": 2, b"l": 2, b"c": 6, b"v": 4, b"y": 4, b"re": 4, b"h": 0}


@dataclass
class Path:
    start: int  # where its first operator (with its numbers) begins in the stream
    end: int  # just after its painting operator
    ops: list  # [(operator, [numbers])]: how it is built, in its own coordinates
    clip: bytes  # b"W" / b"W*" when the path is also a clip, else b""
    paint: bytes
    look: bytes  # operators between its points and its painting, kept as they are (PyMuPDF puts colours there)
    ctm: pymupdf.Matrix  # its own coordinates -> page coordinates
    rect: pymupdf.Rect  # page coordinates, from its points (as MuPDF measures it: curve handles included)


def _points(op: bytes, nums: list) -> list[pymupdf.Point]:
    if op == b"re":
        x, y, w, h = nums
        return [pymupdf.Point(x, y), pymupdf.Point(x + w, y), pymupdf.Point(x + w, y + h), pymupdf.Point(x, y + h)]
    return [pymupdf.Point(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]


def _bbox(build: list) -> pymupdf.Rect:
    xs, ys = [], []
    for op, nums in build:
        if op == b"re":
            x, y, w, h = nums
            xs += (x, x + w)
            ys += (y, y + h)
        else:
            xs += nums[0::2]
            ys += nums[1::2]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


_last: tuple = (None, None, [])  # the stream read last and its paths: page_objects reads it, the edit after it too


def _read(page) -> tuple[bytes, list[Path]]:
    """The stream and the paths it paints, in order; no paths when it holds something PDFlex cannot follow."""
    global _last
    data = b"\n".join(page.parent.xref_stream(x) or b"" for x in page.get_contents())
    key = tuple(page.transformation_matrix)
    if _last[0] != key or _last[1] != data:
        _last = key, data, _parse(page, data)
    return data, _last[2]


def _parse(page, data: bytes) -> list[Path]:
    out, build = [], []
    start, clip, ctm, stack, look, odd = None, b"", page.transformation_matrix, [], [], False
    try:
        for m in _OP.finditer(data):
            tok = m.group(2)
            if tok in _BUILD:
                n = _BUILD[tok]
                if start is None:
                    start = m.start(1)
                build.append((tok, [float(a) for a in m.group(1).split()[-n:]] if n else []))
            elif tok in (b"W", b"W*"):
                clip = tok
            elif tok in _PAINT or tok == b"n":
                if build and tok != b"n" and not odd:
                    out.append(Path(start, m.end(), build, clip, tok, b"\n".join(look), ctm, _bbox(build) * ctm))
                build, start, clip, look, odd = [], None, b"", [], False
            elif tok in (b"BI", b"ID"):
                return []  # an inline image: its data could pass for operators. Left to the old way
            elif start is not None and tok not in (b"q", b"Q", b"cm", b"Do", b"BT"):
                look.append(data[m.start(1):m.end()])
            elif start is not None:
                odd = True  # the state changes in the middle of a path: left as it is
            if tok == b"q":
                stack.append(ctm)
            elif tok == b"Q":
                ctm = stack.pop() if stack else ctm
            elif tok == b"cm":
                ctm = pymupdf.Matrix(*[float(a) for a in m.group(1).split()[-6:]]) * ctm
    except (ValueError, TypeError, IndexError):
        return []  # numbers PDFlex does not understand: leave this page to the old way
    return out


def _same(a: pymupdf.Rect, b) -> bool:
    return all(abs(x - y) < 0.6 for x, y in zip(a, b))


def paths(page, drawings: list[dict] | None = None) -> tuple[bytes, dict[int, Path]]:
    """The stream, and the paths it draws itself by MuPDF's sequence number (drawings: page.get_cdrawings()).
    The paths of forms and patterns come in between in MuPDF's list: they are skipped, they are not here."""
    data, found = _read(page)
    drawings = page.get_cdrawings() if drawings is None else drawings
    out, j = {}, 0
    for d in drawings:
        for k in (j, j + 1):  # j + 1: a path of the stream that MuPDF does not draw (nothing to paint)
            if k < len(found) and _PAINT[found[k].paint] == d["type"] and _same(found[k].rect, d["rect"]):
                out[d["seqno"]] = found[k]
                j = k + 1
                break
    return data, out


def _num(v: float) -> bytes:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return (s if s not in ("-0", "") else "0").encode()


def _rgb(c) -> bytes:
    return b" ".join(_num(v) for v in c)


def _rewrite(p: Path, m: pymupdf.Matrix | None, style: dict) -> bytes:
    """The path again: its points through m (page coordinates), its look changed by style; deleted when m is None
    (a path that is also a clip stays, as a clip only). What the stream set between its points and its painting
    goes first: it also holds for what comes after."""
    kept = [p.look] if p.look else []
    if m is None:
        m, paint, style = pymupdf.Identity, b"n", {}
        if not p.clip:
            return b"\n".join(kept)
    else:
        paint = p.paint
    x = p.ctm * m * ~p.ctm  # the change, in the path's own coordinates
    parts = []
    for op, nums in p.ops:
        if op == b"re" and abs(x.b) < 1e-9 and abs(x.c) < 1e-9:
            a, b = pymupdf.Point(nums[0], nums[1]) * x, pymupdf.Point(nums[0] + nums[2], nums[1] + nums[3]) * x
            parts.append(b" ".join(map(_num, (a.x, a.y, b.x - a.x, b.y - a.y))) + b" re")
        elif op == b"re":  # turned: four sides
            x0, y0, w, h = nums
            q = [pymupdf.Point(*pt) * x for pt in ((x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h))]
            parts += [_num(q[0].x) + b" " + _num(q[0].y) + b" m"] + [_num(t.x) + b" " + _num(t.y) + b" l" for t in q[1:]]
            parts.append(b"h")
        else:
            pts = [pt * x for pt in _points(op, nums)] if nums else []
            parts.append(b" ".join(_num(v) for pt in pts for v in pt) + (b" " if pts else b"") + op)
    look = []
    if "color" in style:
        look.append(_rgb(style["color"]) + b" RG")
        paint = {b"f": b"B", b"F": b"B", b"f*": b"B*"}.get(paint, paint)
    if "fill" in style:
        look.append(_rgb(style["fill"]) + b" rg")
        paint = {b"S": b"B", b"s": b"b"}.get(paint, paint)
    if "width" in style:
        look.append(_num(style["width"] / (abs(p.ctm.a * p.ctm.d - p.ctm.b * p.ctm.c) ** 0.5 or 1)) + b" w")
    body = parts + ([p.clip] if p.clip else []) + [paint]
    return b"\n".join(kept + ([b"q", *look, *body, b"Q"] if look else body))


def _write(page, data: bytes) -> None:
    doc, xrefs = page.parent, page.get_contents()
    shared = any(x in q.get_contents() for q in doc if q.number != page.number for x in xrefs)
    if shared or not xrefs:
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
    else:
        xref = xrefs[0]
    doc.update_stream(xref, data, compress=False)  # deflated when the PDF is saved
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")


def _key(d: dict) -> tuple:
    return (d["type"], *(round(v, 1) for v in d["rect"]), d.get("fill"), d.get("color"))


def edit(page, edits: dict[int, tuple], before: list[dict] | None = None) -> bool:
    """Change paths where they are drawn: edits = {seqno: (matrix or None to delete, style)}. False, with the
    page as it was, when one of them is not the page's own or MuPDF does not then draw what was meant."""
    before = page.get_cdrawings() if before is None else before
    data, own = paths(page, before)
    if not edits or any(s not in own for s in edits):
        return False
    pieces, pos = [], 0
    for s in sorted(edits, key=lambda s: own[s].start):
        p = own[s]
        pieces += [data[pos:p.start], _rewrite(p, *edits[s])]
        pos = p.end
    _write(page, b"".join(pieces) + data[pos:])
    after = page.get_cdrawings()
    want = [_key(d) for d in before if d["seqno"] not in edits]
    kept = [d for d in before if d["seqno"] not in edits or edits[d["seqno"]][0] is not None]
    ok = len(after) == len(kept) and [_key(d) for d, k in zip(after, kept) if k["seqno"] not in edits] == want
    if ok:  # the changed ones are where they should be
        for d, k in zip(after, kept):
            if k["seqno"] in edits:
                p, (m, _) = own[k["seqno"]], edits[k["seqno"]]
                pts = [pt * p.ctm * m for op, nums in p.ops for pt in _points(op, nums)]  # turned: not its box
                ok = ok and _same(_bbox([(b"l", [v for pt in pts for v in pt])]), d["rect"])
    if not ok:
        _write(page, data)
    return ok


def copy(page, seqnos: list[int], m: pymupdf.Matrix, before: list[dict] | None = None) -> bool:
    """A copy of each path through m, drawn right after it: just above it, under what the page draws next.
    False, with the page as it was, as for edit."""
    before = page.get_cdrawings() if before is None else before
    data, own = paths(page, before)
    if not seqnos or any(s not in own for s in seqnos):
        return False
    pieces, pos = [], 0
    for s in sorted(seqnos, key=lambda s: own[s].start):
        p = own[s]  # the copy is no clip: it would cut what comes after
        pieces += [data[pos:p.end], b"\n", _rewrite(replace(p, clip=b""), m, {})]
        pos = p.end
    _write(page, b"".join(pieces) + data[pos:])
    after, want = page.get_cdrawings(), []
    for d in before:
        want.append((d["type"], pymupdf.Rect(d["rect"])))
        if d["seqno"] in seqnos:
            want.append((d["type"], pymupdf.Rect(d["rect"]) * m))
    ok = len(after) == len(want) and all(d["type"] == t and _same(r, d["rect"]) for d, (t, r) in zip(after, want))
    if not ok:
        _write(page, data)
    return ok
