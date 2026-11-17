"""Guess form fields from what a printed form usually looks like.

Recognised: ____ runs, fill-in lines, empty boxes, small squares (checkboxes), white boxes on a tinted
band (Agenzia delle Entrate style, split along the row lines) and rows of ticks (one letter per cell)."""
import re

import pymupdf

from pdflex.editor import Field, clean_value

_KIND_WORDS = [  # (kind, fmt, label pattern)
    ("date", "", r"\bdata\b|\bdate\b|\bnat[oa] il\b"),
    ("signature", "", r"\bfirma\b|\bsignature\b"),
    ("text", "email", r"\be-?mail\b|\bpec\b"),
    ("text", "amount", r"\bimporto\b|\btotale\b|\bprezzo\b|\beuro\b|€"),
]
CODE = re.compile(r"^(?:[A-Z]{1,2}\d{1,3}|\d{1,2})$")  # row codes: A01, RE12, 1, 2...
DECIMALS = re.compile(r"^[,.%]\d*$")  # a printed ",00" / "," / "%" inside an amount box


def _overlap(a, b) -> float:
    r = pymupdf.Rect(a) & pymupdf.Rect(b)
    if r.is_empty:
        return 0.0
    small = min(pymupdf.Rect(a).get_area(), pymupdf.Rect(b).get_area()) or 1
    return r.get_area() / small


def _has_text(words, rect) -> bool:
    """Printed text inside: a label cell, not a box to fill.

    Not counted: decimals like ",00" and tiny column numbers in a corner (under 5.5 pt)."""
    r = pymupdf.Rect(rect)
    return any(r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)) and not DECIMALS.match(w[4])
               and "___" not in w[4] and w[3] - w[1] > 5.5 for w in words)


def _kind_for(label: str, default: str):
    low = label.lower()
    if default == "checkbox":
        return default, ""
    for kind, fmt, pattern in _KIND_WORDS:
        if re.search(pattern, low):
            return kind, fmt
    return default, ""


def _name(label: str) -> str:
    n = re.sub(r"_+", "_", clean_value(label.lower())).strip("_")[:40]
    return n if n and n != "opzione" else ""


def _lines(drawings):
    """Axis-aligned strokes: (x0, x1, y) horizontals and (x, y0, y1) verticals."""
    h, v = [], []
    for d in drawings:
        for item in d["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 0.8:
                    h.append((min(p1.x, p2.x), max(p1.x, p2.x), p1.y))
                elif abs(p1.x - p2.x) < 0.8:
                    v.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif item[0] == "re":
                r = pymupdf.Rect(item[1])
                if r.height < 2 and r.width >= 3:
                    h.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width < 2 and r.height >= 2:
                    v.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
    return h, v


def _combs(vlines):
    """Rows of evenly spaced ticks of the same height: (rect, cells)."""
    rows = []  # [y0, y1, xs]: ticks spanning the same heights, within 1.5 pt (65.4999 and 65.5 are one row)
    for x, y0, y1 in sorted(vlines, key=lambda t: (t[2], t[1])):
        row = next((r for r in rows if abs(r[0] - y0) < 1.5 and abs(r[1] - y1) < 1.5), None)
        if row is None:
            rows.append([y0, y1, [x]])
        else:
            row[2].append(x)
    out = []
    for y0, y1, xs in rows:
        xs = sorted(set(round(x, 1) for x in xs))
        run = [xs[0]] if xs else []
        for x in xs[1:] + [None]:
            gap = run[1] - run[0] if len(run) > 1 else None
            if x is not None and 5 <= x - run[-1] <= 30 and (gap is None or abs((x - run[-1]) - gap) <= gap * 0.15):
                run.append(x)
                continue
            if len(run) >= 5:  # 4 cells or more
                top = min(y0, y1 - 13)  # short ticks under the cells: give the letters room
                out.append((pymupdf.Rect(run[0], top, run[-1], y1), len(run) - 1))
            run = [x] if x is not None else []
    return out


def _tinted(drawings):
    """Big coloured backgrounds (table bands)."""
    return [pymupdf.Rect(d["rect"]) for d in drawings
            if d.get("fill") and min(d["fill"]) < 0.95 and d["rect"].width * d["rect"].height > 2000]


def _boxes(drawings, hlines, words):
    """White boxes on a tinted background, cut along the row lines that cross them; plus empty outlined boxes."""
    tinted = _tinted(drawings)
    out = []
    for d in drawings:
        fill, r = d.get("fill"), pymupdf.Rect(d["rect"])
        if r.width < 8 or r.height < 8 or len(d["items"]) > 5:
            continue
        if fill and min(fill) >= 0.95:
            if not any(t.contains(r.tl + (r.br - r.tl) / 2) and t.get_area() > r.get_area() for t in tinted):
                continue  # white on white: invisible, not a box
        elif fill or d.get("color") is None:
            continue
        cuts = sorted({round(y, 1) for x0, x1, y in hlines
                       if r.y0 + 4 < y < r.y1 - 4 and x0 <= r.x0 + 2 and x1 >= r.x1 - 2})
        edges = [r.y0, *cuts, r.y1]
        for top, bottom in zip(edges, edges[1:]):
            piece = pymupdf.Rect(r.x0, top, r.x1, bottom)
            if not _has_text(words, piece):
                out.append(piece)
    return out


def _candidates(page, words):
    drawings = page.get_drawings()
    hlines, vlines = _lines(drawings)
    found = []  # (default kind, rect, comb)
    for w in words:
        m = re.search(r"_{3,}", w[4])
        if m:
            x0, y0, x1, y1 = w[:4]
            per_char = (x1 - x0) / max(len(w[4]), 1)
            x0 = x0 + per_char * m.start()  # "Nome:____" glued together: start at the underscores
            found.append(("text", pymupdf.Rect(x0, y1 - 14, x1, y1 + 1), 0))
    combs = _combs(vlines)
    found += [("text", r, n) for r, n in combs]
    for r in _boxes(drawings, hlines, words):
        if r.width <= 20 and r.height <= 20 and abs(r.width - r.height) <= 4:
            found.append(("checkbox", r, 0))
        elif r.width >= 14 and 9 <= r.height <= 160:
            inner = r + (1, 1, -1, -1)
            found.append(("multiline" if r.height > 40 else "text", inner, 0))
    tinted = _tinted(drawings)
    for x0, x1, y in hlines:
        if any(t.y0 - 1.5 <= y <= t.y1 + 1.5 and min(x1, t.x1) - max(x0, t.x0) > (x1 - x0) / 2 for t in tinted):
            continue  # inside or on the edge of a coloured table: there the fields are boxes, lines are rows
        if x1 - x0 < 50 or any(abs(y - c.y1) < 2 and c.x0 - 2 <= x0 and x1 <= c.x1 + 2 for c, _ in combs):
            continue
        band = pymupdf.Rect(x0, y - 16, x1, y - 1)
        if not _has_text(words, band):  # text right above = a row separator under a label
            found.append(("text", band, 0))
    return found


def _label(words, rect, left_edge, kind, hlines) -> tuple[str, str]:
    """(row code, label) for a field.

    Label: the group of words closest on its left (stopping at the previous field), else the words just
    above it; a checkbox takes the words right after it when there are some ("[ ] Barrare la casella").
    Row code: a word like A01 / B00 / 1 that starts its line left of the field."""
    mid = (rect.y0 + rect.y1) / 2
    # no ",00" and no tiny column numbers; small words like "Ricavi" are real labels and stay
    words = [w for w in words if not DECIMALS.match(w[4]) and not (w[4].isdigit() and w[3] - w[1] <= 5.5)]
    on_row = [w for w in words if w[1] - 3 <= mid <= w[3] + 3 and "___" not in w[4]]
    codes = [w for w in on_row if w[2] <= rect.x0 + 2 and w[7] == 0 and CODE.match(w[4])]
    if not codes:  # a row of several lines (F13): its letter code sits beside one of them
        def region(w):  # between the table lines above and below the code that pass under it
            cx = (w[0] + w[2]) / 2
            ups = [y for x0, x1, y in hlines if x0 <= cx <= x1 and y <= w[1] + 1]
            downs = [y for x0, x1, y in hlines if x0 <= cx <= x1 and y >= w[3] - 1]
            return max(ups, default=w[1] - 45), min(downs, default=w[3] + 45)
        codes = [w for w in words if w[2] <= rect.x0 + 2 and w[7] == 0 and CODE.match(w[4])
                 and not w[4].isdigit() and abs((w[1] + w[3]) / 2 - mid) <= 100]
        codes = [w for w in codes if region(w)[0] <= mid <= region(w)[1]][:1]
    code = max(codes, key=lambda w: w[0])[4] if codes else ""
    if kind == "checkbox":
        after = sorted((w for w in on_row if 0 <= w[0] - rect.x1 < 12), key=lambda w: w[0])
        if after:
            group = [after[0]]
            for w in sorted((w for w in on_row if w[0] > after[0][0]), key=lambda w: w[0]):
                if w[0] - group[-1][2] < 8:
                    group.append(w)
            return code, " ".join(w[4] for w in group[:5])
    left = sorted((w for w in on_row if w[2] <= rect.x0 + 2 and w[0] >= left_edge and rect.x0 - w[2] < 400
                   and w[4] != code), key=lambda w: w[0])
    group = left[-1:]
    for w in reversed(left[:-1]):  # walk left while the words stay close together
        if group[0][0] - w[2] > 15:
            break
        group.insert(0, w)
    above = sorted((w for w in words if 0 < rect.y0 - w[3] < 12 and w[0] < rect.x1 and w[2] > rect.x0 - 10
                    and "___" not in w[4]), key=lambda w: (w[5], w[6], w[7]))
    if above and (not group or rect.x0 - group[-1][2] > 60):
        group = above
    return code, " ".join(w[4] for w in group[:4]).strip(" :.")


def detect_fields(page: pymupdf.Page, existing: list[Field]) -> list[Field]:
    """Fields suggested for this page (PDF coordinates), skipping spots that already have one."""
    words = page.get_text("words")
    hlines = _lines(page.get_drawings())[0]
    taken = [pymupdf.Rect(f.rect) for f in existing if f.page == page.number]
    kept = []
    for kind, rect, comb in _candidates(page, words):
        if not any(_overlap(rect, t) > 0.3 for t in taken):
            taken.append(rect)
            kept.append((kind, rect, comb))
    used = {f.name for f in existing}
    out = []
    for kind, rect, comb in sorted(kept, key=lambda c: (round(c[1].y0 / 4), c[1].x0)):
        # the label ends where the previous field on the same row ends ("ISA [ ] Ricavi [ ]")
        left = max((r.x1 for _, r, _ in kept if r.x1 <= rect.x0 + 1 and min(r.y1, rect.y1) - max(r.y0, rect.y0) > 3),
                   default=0)
        code, label = _label(words, rect, left, kind, hlines)
        kind, fmt = _kind_for(label, kind)
        if code and not code.isdigit():
            name = code.lower()
        else:
            name = "_".join(p for p in (_name(label), code) if p) or kind
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        out.append(Field(kind, page.number, tuple(round(v, 1) for v in rect), name, fmt=fmt, comb=comb))
    return out
