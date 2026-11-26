"""Guess form fields from what a printed form usually looks like.

Recognised: ____ runs, fill-in lines, empty boxes, small squares (checkboxes), white boxes on a tinted
band (Agenzia delle Entrate style, split along the row lines) and rows of ticks (one letter per cell)."""
import re

import pymupdf

from pdflex.fields import Field, clean_value

_KIND_WORDS = [  # (kind, fmt, label pattern)
    ("date", "", r"\bdata\b|\bdate\b|\bnat[oa] il\b|\bgiorno\b"),
    ("signature", "", r"\bfirma\b|\bsignature\b"),
    ("text", "email", r"\be-?mail\b|\bpec\b"),
    ("text", "phone", r"\btelefono\b|\bcellulare\b"),
    ("text", "amount", r"\bimporto\b|\btotale\b|\bprezzo\b|\beuro\b|€"),
]
CODE = re.compile(r"^(?:[A-Z]{1,2}\d{1,3}|\d{1,2})$")  # row codes: A01, RE12, 1, 2...
DECIMALS = re.compile(r"^[,.%]\d*$")  # a printed ",00" / "," / "%" inside an amount box
COLUMN_NO = 7.0  # pt: a number this short in a box's corner numbers the column, it is not a label


def _column_no(w) -> bool:
    return w[4].isdigit() and len(w[4]) <= 2 and w[3] - w[1] <= COLUMN_NO


def _overlap(a, b) -> float:
    r = pymupdf.Rect(a) & pymupdf.Rect(b)
    if r.is_empty:
        return 0.0
    small = min(pymupdf.Rect(a).get_area(), pymupdf.Rect(b).get_area()) or 1
    return r.get_area() / small


def _text_in(words, rect) -> list:
    """Printed words inside rect. Not counted: decimals like ",00", tiny print (under 5.5 pt), column numbers."""
    r = pymupdf.Rect(rect)
    return [w for w in words if r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
            and not DECIMALS.match(w[4]) and "___" not in w[4] and w[3] - w[1] > 5.5 and not _column_no(w)]


def _mid(w) -> pymupdf.Point:
    return pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)


def _tick_box(r) -> bool:
    return r.width <= 22 and r.height <= 22 and abs(r.width - r.height) <= 4


def _has_text(words, rect) -> bool:
    """Printed text inside: a label cell, not a box to fill."""
    return bool(_text_in(words, rect))


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


def _brackets(hlines, vlines, combs):
    """A line with short ticks rising from it at both ends (and maybe between): "|COGNOME |NOME |".

    Ticks evenly spaced and close together make one box per letter (Mod. N.), else each gap is a field.
    Returns (rect, cells) like _combs, and the lines used."""
    out, used = [], []
    for x0, x1, y in hlines:
        if any(abs(y - c.y1) < 2 and c.x0 - 2 <= x0 and x1 <= c.x1 + 2 for c, _ in combs):
            continue
        ticks = sorted({round(x, 1): y1 - y0 for x, y0, y1 in vlines
                        if abs(y1 - y) < 1.5 and 4 <= y1 - y0 <= 30 and x0 - 1.5 <= x <= x1 + 1.5}.items())
        if len(ticks) < 2 or ticks[0][0] - x0 > 2 or x1 - ticks[-1][0] > 2:
            continue
        xs = [x for x, _ in ticks]
        top = y - max(max(h for _, h in ticks), 12)
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        if max(gaps) <= 30 and max(gaps) - min(gaps) <= max(gaps) * 0.15:
            out.append((pymupdf.Rect(xs[0], top, xs[-1], y), len(gaps)))
        else:
            out += [(pymupdf.Rect(a, top, b, y), 0) for a, b in zip(xs, xs[1:]) if b - a >= 14]
        used.append((x0, x1, y))
    return out, used


def _widen_comb(rect, cells, drawings):
    """Ticks only between the cells: the first and last cell end at the edges of the white box around them."""
    step = rect.width / cells
    for d in drawings:
        box = pymupdf.Rect(d["rect"])
        if not (d.get("fill") and min(d["fill"]) >= 0.95 and box.y0 <= rect.y0 + 8 and abs(box.y1 - rect.y1) < 2):
            continue
        if abs(rect.x0 - box.x0 - step) <= step * 0.15:
            rect, cells = pymupdf.Rect(box.x0, rect.y0, rect.x1, rect.y1), cells + 1
        if abs(box.x1 - rect.x1 - step) <= step * 0.15:
            rect, cells = pymupdf.Rect(rect.x0, rect.y0, box.x1, rect.y1), cells + 1
    return rect, cells


def _tinted(drawings):
    """Big coloured backgrounds (table bands)."""
    return [pymupdf.Rect(d["rect"]) for d in drawings
            if d.get("fill") and min(d["fill"]) < 0.95 and d["rect"].width * d["rect"].height > 2000]


DIGITS = {"giorno": 2, "mese": 2, "anno": 4, "gg": 2, "mm": 2, "aaaa": 4}  # columns of a date box


def _columns(piece, vlines, words, label_top):
    """(rect, letters, part) for each column of a box split by vertical lines (giorno | mese | anno).

    Columns all the same and narrow: one box per letter, a single comb field. A column labelled
    giorno / mese / anno gets that many letters; any other column is a plain field of its own."""
    seps = {}
    for x, y0, y1 in vlines:  # dashed separators come in pieces: add them up per x
        if piece.x0 + 3 < x < piece.x1 - 3:
            seps[round(x)] = seps.get(round(x), 0) + max(0, min(y1, piece.y1) - max(y0, piece.y0))
    xs = [piece.x0, *sorted(x for x, h in seps.items() if h >= piece.height * 0.5), piece.x1]
    if len(xs) == 2:
        return [(piece, 0, "")]
    widths = [b - a for a, b in zip(xs, xs[1:])]
    if max(widths) <= 30 and max(widths) - min(widths) <= 3:
        return [(piece, len(widths), "")]
    out = []
    for a, b in zip(xs, xs[1:]):
        col = pymupdf.Rect(a, piece.y0, b, piece.y1)
        names = [w[4].lower() for w in words  # above the free part, or tiny print in the box's upper half
                 if a <= (w[0] + w[2]) / 2 <= b and label_top <= w[1] and w[3] <= piece.y0 + piece.height * 0.6]
        part = next((n for n in names if n in DIGITS), "")
        out.append((col, DIGITS.get(part, 0), part))
    return out


def _boxes(drawings, hlines, vlines, words):
    """White boxes on a tinted background, cut along the row lines that cross them; plus empty outlined boxes.

    Returns (rect, info): info["letters"] > 0 for a box split in columns, one letter each; "inner" is the
    label printed inside the box, "part" giorno / mese / anno, "whole" the box a column belongs to."""
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
            text = _text_in(words, piece)
            if text and _tick_box(piece) and all(len(w[4]) <= 3 and w[4][0].isalpha() for w in text):
                out.append((piece, {"answer": True}))  # a box to tick with its answer inside: "SÌ", "NO"
                continue
            if _tick_box(piece) and not text and any(
                    _column_no(w) and piece.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
                    for w in words):
                out.append((piece, {"answer": True}))  # "6 [ ]": dropped when an empty box follows
                continue
            whole, inner = pymupdf.Rect(piece), " ".join(w[4] for w in text)
            inside = [w for w in words if piece.contains(_mid(w)) and "___" not in w[4]]
            # whatever is printed in the box stays readable: the letters never go over it
            titles = [w for w in inside if not DECIMALS.match(w[4]) and not _column_no(w)]
            if titles:  # "GIORNO MESE ANNO", "Comune (o Stato estero) di nascita": the field goes under it
                below = max(w[3] for w in titles) + 0.5
                if bottom - below < (max(9, 0.35 * piece.height) if text else 6):
                    continue  # no room left under the title
                piece.y0 = below
            cols = _columns(piece, vlines, words, top - 1)
            info = {"whole": whole, "inner": inner}
            if len(cols) == 1 and not cols[0][1]:
                numbers = [w for w in inside if _column_no(w) and w[3] > piece.y0 + 0.5 and w[0] < piece.x0 + 8]
                if numbers:  # column number in the corner: start right of it
                    piece.x0 = max(w[2] for w in numbers) + 0.5
                cents = [w for w in inside if DECIMALS.match(w[4]) and w[0] > piece.x1 - 20]
                if cents:  # ",00" or "%" printed at the end: the amount ends right before it
                    piece.x1 = min(w[0] for w in cents) - 0.5
                    info["align"] = 2
                cols = [(piece, 0, "")]
            out += [(col, {**info, "letters": n, "part": part}) for col, n, part in cols]
    return out


def _candidates(page, words):
    drawings = page.get_drawings()
    hlines, vlines = _lines(drawings)
    found = []  # (default kind, rect, comb, info: see _boxes)
    for w in words:
        m = re.search(r"_{3,}", w[4])
        if m:
            x0, y0, x1, y1 = w[:4]
            per_char = (x1 - x0) / max(len(w[4]), 1)
            x0 = x0 + per_char * m.start()  # "Nome:____" glued together: start at the underscores
            found.append(("text", pymupdf.Rect(x0, y1 - 14, x1, y1 + 1), 0, {}))
    combs = [_widen_comb(r, n, drawings) for r, n in _combs(vlines)]
    found += [("text", r, n, {}) for r, n in combs]
    brackets, bracket_lines = _brackets(hlines, vlines, combs)
    for r, n in brackets:
        over = [w for w in words if w[3] > r.y0 and w[1] < r.y0 + 4 and w[2] > r.x0 and w[0] < r.x1]
        if over:  # the label reaches into the space above the line: the letters start under it
            r.y0 = max(w[3] for w in over) + 0.5
        if n or (r.height >= 7 and not _has_text(words, r)):
            found.append(("text", r, n, {}))
    for r, info in _boxes(drawings, hlines, vlines, words):
        letters = info.get("letters", 0)
        if _tick_box(r) and not letters:
            found.append(("checkbox", r, 0, info))
        elif letters or (r.width >= 14 and 9 <= r.height <= 160):
            inner = r + (1, 1, -1, -1) if not letters else r  # comb boxes must line up with the printed ones
            found.append(("multiline" if r.height > 40 and not letters else "text", inner, letters, info))
    # "M [ ]": the printed letter is only the label of the empty box right after it
    boxes = [r for k, r, _, i in found if k == "checkbox" and not i.get("answer")]
    found = [c for c in found if not (c[3].get("answer") and any(
        0 <= b.x0 - c[1].x1 <= 6 and min(b.y1, c[1].y1) - max(b.y0, c[1].y0) > 4 for b in boxes))]
    tinted = _tinted(drawings)
    for x0, x1, y in hlines:
        if any(t.y0 - 1.5 <= y <= t.y1 + 1.5 and min(x1, t.x1) - max(x0, t.x0) > (x1 - x0) / 2 for t in tinted):
            continue  # inside or on the edge of a coloured table: there the fields are boxes, lines are rows
        if x1 - x0 < 50 or any(abs(y - c.y1) < 2 and c.x0 - 2 <= x0 and x1 <= c.x1 + 2 for c, _ in combs):
            continue
        if (x0, x1, y) in bracket_lines:
            continue
        band = pymupdf.Rect(x0, y - 16, x1, y - 1)
        if not _has_text(words, band):  # text right above = a row separator under a label
            found.append(("text", band, 0, {}))
    return found


def _label(words, rect, left_edge, kind, hlines) -> tuple[str, str]:
    """(row code, label) for a field.

    Label: the group of words closest on its left (stopping at the previous field), else the words just
    above it; a checkbox takes the words right after it when there are some ("[ ] Barrare la casella").
    Row code: a word like A01 / B00 / 1 that starts its line left of the field."""
    mid = (rect.y0 + rect.y1) / 2
    # no ",00" and no tiny column numbers; small words like "Ricavi" are real labels and stay
    words = [w for w in words if not DECIMALS.match(w[4]) and not _column_no(w)]
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
        inside = [w for w in words if rect.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
        if inside:  # "SÌ" / "NO" printed in the box
            return code, " ".join(w[4] for w in inside)
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
    above = sorted((w for w in words if -1 < rect.y0 - w[3] < 12 and w[0] < rect.x1 and w[2] > rect.x0 - 10
                    and "___" not in w[4]), key=lambda w: (w[5], w[6], w[7]))
    if above and (not group or rect.x0 - group[-1][2] > 60):
        group = above
    return code, " ".join(w[4] for w in group[:4]).strip(" :.")


def _code_length(label: str, rect, fmt: str) -> tuple[int, str]:
    """(letters, format) of a codice fiscale / partita IVA box without printed cells: 16 or 11 characters."""
    low = label.lower()
    if "partita iva" in low or "p. iva" in low or ("fiscale" in low and ("societ" in low or " ente" in low)):
        n = 11
    elif "codice" in low and "fiscale" in low:
        n = 16
    else:
        return 0, fmt
    return (n, "cf" if n == 16 else fmt) if rect.width >= n * 6 else (0, fmt)


def row_lines(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """(x0, x1, y) of every horizontal edge on the page: drawn lines, and the top and bottom of boxes
    (tables are often drawn as one rectangle per cell)."""
    drawings = page.get_drawings()
    out = list(_lines(drawings)[0])
    for d in drawings:
        for item in d["items"]:
            if item[0] == "re":
                r = pymupdf.Rect(item[1])
            elif item[0] == "qu" and item[1].is_rectangular:
                r = item[1].rect
            else:
                continue
            if r.height >= 2 and r.width >= 3:  # thinner ones are lines, already in
                out += [(r.x0, r.x1, r.y0), (r.x0, r.x1, r.y1)]
    return out


def table_step(hlines, box: tuple, taken: list[tuple], page_height: float) -> float | None:
    """How far down `box` moves to sit in the next free row of the table it is in, at the same place in
    the row; None when it is in no table, or no row below is free. Rows come from the lines spanning at
    least 80% of the box; a box spanning n rows moves n rows at a time."""
    x0, y0, x1, y1 = box
    spans = []  # a row's line drawn cell by cell: the pieces joined first (same height, touching)
    for a, b, y in sorted(hlines, key=lambda h: (round(h[2]), h[0])):
        if spans and abs(spans[-1][2] - y) <= 1 and a <= spans[-1][1] + 2:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b, y])
    ys = []
    for y in sorted(y for a, b, y in spans if min(b, x1) - max(a, x0) >= 0.8 * (x1 - x0)):
        if not ys or y - ys[-1] > 1:  # the same line drawn twice, or a cell's edge and its neighbour's
            ys.append(y)
    above = [i for i, y in enumerate(ys) if y <= y0 + 1]
    below = [i for i, y in enumerate(ys) if y >= y1 - 1]
    if not above or not below:
        return None
    ia, ib = above[-1], below[0]
    n = ib - ia
    k = 1
    while n > 0 and ib + k * n < len(ys):
        dy = ys[ia + k * n] - ys[ia]
        top, bottom = y0 + dy, y1 + dy
        free = not any(t[0] < x1 - 0.5 and t[2] > x0 + 0.5 and t[1] < bottom - 0.5 and t[3] > top + 0.5 for t in taken)
        if bottom <= ys[ib + k * n] + 1 and bottom <= page_height and free:
            return dy
        k += 1
    return None


def detect_fields(page: pymupdf.Page, existing: list[Field]) -> list[Field]:
    """Fields suggested for this page (PDF coordinates), skipping spots that already have one."""
    words = page.get_text("words")
    hlines = _lines(page.get_drawings())[0]
    taken = [pymupdf.Rect(f.rect) for f in existing if f.page == page.number]
    kept = []
    for kind, rect, comb, info in _candidates(page, words):
        if not any(_overlap(rect, t) > 0.3 for t in taken):
            taken.append(rect)
            kept.append((kind, rect, comb, info))
    used = {f.name for f in existing}
    out = []
    for kind, rect, comb, info in sorted(kept, key=lambda c: (round(c[1].y0 / 4), c[1].x0)):
        part, box = info.get("part", ""), info.get("whole") or rect  # a column is named after its whole box
        # the label ends where the previous field on the same row ends ("ISA [ ] Ricavi [ ]")
        left = max((r.x1 for _, r, _, _ in kept if r.x1 <= box.x0 + 1 and min(r.y1, box.y1) - max(r.y0, box.y0) > 3),
                   default=0)
        code, label = _label(words, box, left, kind, hlines)
        if info.get("inner") and not part:  # printed in the box itself: "[Telefono] prefisso numero"
            label = f"{label} {info['inner']}" if len(label.split()) == 1 else info["inner"]
        label = " ".join(t for t in label.split() if t.lower() not in DIGITS)
        kind, fmt = _kind_for(label, kind) if not comb else (kind, "")  # one letter per box: no date script
        if kind == "text" and not comb:
            comb, fmt = _code_length(label, rect, fmt)
        if code and not code.isdigit():
            answer = _name(label) if kind == "checkbox" and len(label) <= 3 else ""
            name = "_".join(p for p in (code.lower(), answer) if p)
        else:
            name = "_".join(p for p in (_name(label), code) if p) or kind
        if part:
            name = f"{name}_{part}" if name != kind else part
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        out.append(Field(kind, page.number, tuple(round(v, 1) for v in rect), name, fmt=fmt, comb=comb,
                         align=info.get("align", 0)))
    return out
