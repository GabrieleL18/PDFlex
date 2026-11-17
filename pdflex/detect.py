"""Guess form fields from what a printed form usually looks like: ____ runs, lines, boxes, squares."""
import re

import pymupdf

from pdflex.editor import Field, clean_value

_KIND_WORDS = [  # (kind, fmt, words in the label)
    ("date", "", ("data", "date", "nato il", "nata il")),
    ("signature", "", ("firma", "signature")),
    ("text", "email", ("email", "e-mail", "mail")),
    ("text", "amount", ("importo", "totale", "prezzo", "euro", "€")),
]


def _overlap(a, b) -> float:
    r = pymupdf.Rect(a) & pymupdf.Rect(b)
    if r.is_empty:
        return 0.0
    small = min(pymupdf.Rect(a).get_area(), pymupdf.Rect(b).get_area()) or 1
    return r.get_area() / small


def _label(words, rect) -> str:
    """Text just left of the field on the same line, else the line just above."""
    r = pymupdf.Rect(rect)
    mid = (r.y0 + r.y1) / 2
    left = [w for w in words if w[2] <= r.x0 + 2 and w[1] - 4 <= mid <= w[3] + 4 and r.x0 - w[2] < 250]
    if not left:
        left = [w for w in words if 0 < r.y0 - w[3] < 20 and w[0] < r.x1 and w[2] > r.x0 - 50]
    left = [w for w in left if "___" not in w[4]]
    left.sort(key=lambda w: (w[5], w[6], w[7]))
    text = " ".join(w[4] for w in left[-4:]).strip(" :.")
    return text


def _kind_for(label: str, default: str):
    low = label.lower()
    for kind, fmt, keys in _KIND_WORDS:
        if any(k in low for k in keys):
            if default == "checkbox":
                break
            return kind, fmt
    return default, ""


def _name(label: str) -> str:
    n = clean_value(label.lower()).strip("_")
    n = re.sub(r"_+", "_", n)[:40]
    return n if n and n != "opzione" else ""


def detect_fields(page: pymupdf.Page, existing: list[Field]) -> list[Field]:
    """Fields suggested for this page (PDF coordinates), skipping spots that already have one."""
    words = page.get_text("words")
    candidates = []  # (default kind, rect)
    for w in words:
        m = re.search(r"_{3,}", w[4])
        if m:
            x0, y0, x1, y1 = w[:4]
            per_char = (x1 - x0) / max(len(w[4]), 1)
            x0 = x0 + per_char * m.start()  # "Nome:____" glued together: start at the underscores
            candidates.append(("text", (x0, y1 - 14, x1, y1 + 1)))
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 1 and abs(p2.x - p1.x) >= 50:
                    x0, x1 = sorted((p1.x, p2.x))
                    candidates.append(("text", (x0, p1.y - 16, x1, p1.y - 1)))
            elif item[0] == "re":
                r = pymupdf.Rect(item[1])
                if 6 <= r.width <= 20 and abs(r.width - r.height) <= 2:
                    candidates.append(("checkbox", tuple(r)))
                elif r.width >= 50 and r.height < 2:  # a "line" drawn as a thin rectangle
                    candidates.append(("text", (r.x0, r.y0 - 16, r.x1, r.y0 - 1)))
                elif r.width >= 50 and 12 <= r.height <= 120 and not d.get("fill"):
                    kind = "multiline" if r.height > 40 else "text"
                    candidates.append((kind, tuple(r + (1, 1, -1, -1))))
    taken = [f.rect for f in existing if f.page == page.number]
    used = {f.name for f in existing}
    out = []
    for default, rect in candidates:
        if any(_overlap(rect, t) > 0.3 for t in taken):
            continue
        label = _label(words, rect)
        kind, fmt = _kind_for(label, default)
        name = _name(label) or default
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        taken.append(rect)
        out.append(Field(kind, page.number, tuple(round(v, 1) for v in rect), name, fmt=fmt))
    return out
