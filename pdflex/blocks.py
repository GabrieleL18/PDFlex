"""Blocks: a rectangle split into rows and columns, drawn on the page as plain shapes and text, kept editable
through the Editor's note in the PDF (see designer.py). Every operation returns a new Block."""
import dataclasses
from dataclasses import dataclass, field

import pymupdf

from pdflex.i18n import tr

MIN_CELL = 4.0  # pt: no row or column gets thinner than this
HEAD_ROW, HEAD_COL = 18.0, 70.0  # pt: a ready-made block's header row and header column


@dataclass
class Block:
    rect: tuple  # x0, y0, x1, y1 (PDF points)
    cols: list  # widths as fractions of the width, sum 1
    rows: list  # heights as fractions of the height, sum 1
    header_row: bool = False
    header_col: bool = False
    merges: list = field(default_factory=list)  # (r0, c0, r1, c1), inclusive
    texts: dict = field(default_factory=dict)  # "r,c" (top-left cell of a merge) -> text
    border: str = "#3B6FC4"  # "" for border / fill / head_fill / lines: none
    border_width: float = 1
    radius: float = 6
    fill: str = "#F5F8FD"
    head_fill: str = "#E9EFF9"
    lines: str = "#D5DCE6"
    line_width: float = 0.5
    text_size: float = 8
    text_color: str = "#1F2A44"
    font: str = "Helvetica"  # a family of design.FONTS; headers in its bold


def _edges(start: float, length: float, fr: list) -> list[float]:
    out, at = [start], start
    for f in fr:
        at += f * length
        out.append(at)
    out[-1] = start + length  # no rounding left over at the far side
    return out


def col_edges(b: Block) -> list[float]:
    return _edges(b.rect[0], b.rect[2] - b.rect[0], b.cols)


def row_edges(b: Block) -> list[float]:
    return _edges(b.rect[1], b.rect[3] - b.rect[1], b.rows)


def _merge_at(b: Block, r: int, c: int):
    return next((m for m in b.merges if m[0] <= r <= m[2] and m[1] <= c <= m[3]), None)


def cells(b: Block) -> list[tuple]:
    """(r0, c0, r1, c1, rect) of every cell, a merge as one, row by row."""
    xs, ys = col_edges(b), row_edges(b)
    out = []
    for r in range(len(b.rows)):
        for c in range(len(b.cols)):
            m = _merge_at(b, r, c) or (r, c, r, c)
            if (m[0], m[1]) == (r, c):
                out.append((*m, (xs[m[1]], ys[m[0]], xs[m[3] + 1], ys[m[2] + 1])))
    return out


def cell_at(b: Block, x: float, y: float) -> tuple[int, int] | None:
    for r0, c0, _, _, (x0, y0, x1, y1) in cells(b):
        if x0 <= x <= x1 and y0 <= y <= y1:
            return r0, c0
    return None


def _clip(b: Block) -> Block:
    """Merges and texts kept to the rows and columns there are."""
    nr, nc = len(b.rows), len(b.cols)
    merges = []
    for r0, c0, r1, c1 in b.merges:
        m = (r0, c0, min(r1, nr - 1), min(c1, nc - 1))
        if r0 < nr and c0 < nc and (m[0], m[1]) != (m[2], m[3]):
            merges.append(m)
    texts = {k: t for k, t in b.texts.items() if int(k.split(",")[0]) < nr and int(k.split(",")[1]) < nc}
    return dataclasses.replace(b, merges=merges, texts=texts)


def set_count(b: Block, axis: str, n: int) -> Block:
    """n rows or columns: a new one halves the last, one taken away joins the last two."""
    fr = list(getattr(b, axis))
    n = max(1, int(n))
    while len(fr) < n:
        fr[-1:] = [fr[-1] / 2, fr[-1] / 2]
    while len(fr) > n:
        fr[-2:] = [fr[-2] + fr[-1]]
    return _clip(dataclasses.replace(b, **{axis: fr}))


def move_divider(b: Block, axis: str, i: int, pos: float) -> Block:
    """The line after row / column i moved to pos (page coordinate): only the two beside it change."""
    edges = col_edges(b) if axis == "cols" else row_edges(b)
    length = edges[-1] - edges[0]
    a, z = edges[i], edges[i + 2]
    pos = min(max(pos, a + MIN_CELL), z - MIN_CELL)
    fr = list(getattr(b, axis))
    fr[i], fr[i + 1] = (pos - a) / length, (z - pos) / length
    return dataclasses.replace(b, **{axis: fr})


def _area(b: Block, r: int, c: int) -> set:
    m = _merge_at(b, r, c) or (r, c, r, c)
    return {(rr, cc) for rr in range(m[0], m[2] + 1) for cc in range(m[1], m[3] + 1)}


def merge(b: Block, picked: list[tuple[int, int]]) -> Block | None:
    """The picked cells as one; None when together they are not a rectangle."""
    area = set().union(*(_area(b, r, c) for r, c in picked)) if picked else set()
    if len(area) < 2:
        return None
    r0, c0 = min(r for r, _ in area), min(c for _, c in area)
    r1, c1 = max(r for r, _ in area), max(c for _, c in area)
    if len(area) != (r1 - r0 + 1) * (c1 - c0 + 1):
        return None
    merges = [m for m in b.merges if not (r0 <= m[0] and m[2] <= r1 and c0 <= m[1] and m[3] <= c1)]
    texts = {k: t for k, t in b.texts.items()
             if k == f"{r0},{c0}" or tuple(map(int, k.split(","))) not in area}
    return dataclasses.replace(b, merges=merges + [(r0, c0, r1, c1)], texts=texts)


def split(b: Block, r: int, c: int) -> Block:
    return dataclasses.replace(b, merges=[m for m in b.merges if (m[0], m[1]) != (r, c)])


def scaled(b: Block, rect: tuple) -> Block:
    return dataclasses.replace(b, rect=tuple(rect))


def to_json(b: Block) -> dict:
    d = dataclasses.asdict(b)
    d["rect"], d["merges"] = list(b.rect), [list(m) for m in b.merges]
    return d


def from_json(d: dict) -> Block:
    known = {f.name for f in dataclasses.fields(Block)}
    d = {k: v for k, v in d.items() if k in known}
    return Block(**{**d, "rect": tuple(d["rect"]), "merges": [tuple(m) for m in d.get("merges", [])]})


PRESETS = [("rettangolo", tr("Rettangolo")), ("riquadro", tr("Riquadro con titolo")), ("tabella", tr("Tabella")),
           ("griglia", tr("Griglia")), ("sezione", tr("Sezione")), ("totali", tr("Totali"))]


def _sizes(length: float, n: int, head: float | None = None) -> list[float]:
    """n even fractions; with head, a first one head points long (at most half) and n even ones after it."""
    if head is None:
        return [1 / n] * n
    h = min(head, length / 2) / length
    return [h] + [(1 - h) / n] * n


def preset(pid: str, rect: tuple) -> Block | None:
    """A ready-made block filling rect; None for a plain rectangle."""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if pid == "riquadro":
        return Block(tuple(rect), [1.0], [1.0], texts={"0,0": tr("Titolo")})
    if pid == "tabella":
        return Block(tuple(rect), _sizes(w, 4), _sizes(h, 5, HEAD_ROW), header_row=True)
    if pid == "griglia":
        return Block(tuple(rect), _sizes(w, 8, HEAD_COL), _sizes(h, 4, HEAD_ROW), header_row=True, header_col=True)
    if pid == "sezione":
        return Block(tuple(rect), _sizes(w, 3), _sizes(h, 3, HEAD_ROW), header_row=True, border="", fill="")
    if pid == "totali":
        return Block(tuple(rect), _sizes(w, 2), _sizes(h, 3), fill="#F5F8FD", lines="#FFFFFF", line_width=2, radius=0)
    return None


# ---------------------------------------------------------------- on the page

def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) if len(h) == 6 else None


def _path(shape, r: tuple, k: float, corners=(1, 1, 1, 1)) -> None:
    """A box as one closed path, the corners flagged in corners (top left, top right, bottom right, bottom left)
    rounded by k."""
    P = pymupdf.Point
    x0, y0, x1, y1 = r
    k = max(0.0, min(k, (x1 - x0) / 2, (y1 - y0) / 2))
    tl, tr_, br, bl_ = (k * c for c in corners)
    c = 0.5523  # the handles of a quarter circle

    def turn(a, corner, b, kk):
        if kk >= 0.05:
            shape.draw_bezier(a, a + (corner - a) * c, b + (corner - b) * c, b)
        else:
            shape.draw_line(a, b)
    pts = [P(x0 + tl, y0), P(x1 - tr_, y0)]
    shape.draw_line(*pts)
    turn(P(x1 - tr_, y0), P(x1, y0), P(x1, y0 + tr_), tr_)
    shape.draw_line(P(x1, y0 + tr_), P(x1, y1 - br))
    turn(P(x1, y1 - br), P(x1, y1), P(x1 - br, y1), br)
    shape.draw_line(P(x1 - br, y1), P(x0 + bl_, y1))
    turn(P(x0 + bl_, y1), P(x0, y1), P(x0, y1 - bl_), bl_)
    shape.draw_line(P(x0, y1 - bl_), P(x0, y0 + tl))
    turn(P(x0, y0 + tl), P(x0, y0), P(x0 + tl, y0), tl)


def _is_head(b: Block, r: int, c: int) -> bool:
    return (b.header_row and r == 0) or (b.header_col and c == 0)


def _paint(page, b: Block) -> None:
    """The block drawn on top of the page: fill, header bands, inner lines, border, texts."""
    from pdflex import design
    xs, ys = col_edges(b), row_edges(b)
    x0, y0, x1, y1 = b.rect
    k = b.radius
    shape = page.new_shape()
    if _rgb(b.fill):
        _path(shape, b.rect, k)
        shape.finish(color=None, fill=_rgb(b.fill), closePath=True)
    if _rgb(b.head_fill):
        if b.header_row:
            _path(shape, (x0, y0, x1, ys[1]), k, (1, 1, 0, 0))
            shape.finish(color=None, fill=_rgb(b.head_fill), closePath=True)
        if b.header_col:
            top = ys[1] if b.header_row else y0
            _path(shape, (x0, top, xs[1], y1), k, (0 if b.header_row else 1, 0, 0, 1))
            shape.finish(color=None, fill=_rgb(b.head_fill), closePath=True)
    if _rgb(b.lines) and b.line_width > 0:
        for i in range(1, len(b.cols)):  # each column line, in the pieces no merged cell crosses
            for a, z in _runs(len(b.rows), lambda r: (m := _merge_at(b, r, i)) is None or m[1] == i):
                shape.draw_line((xs[i], ys[a]), (xs[i], ys[z]))
                shape.finish(color=_rgb(b.lines), width=b.line_width, closePath=False)
        for j in range(1, len(b.rows)):
            for a, z in _runs(len(b.cols), lambda c: (m := _merge_at(b, j, c)) is None or m[0] == j):
                shape.draw_line((xs[a], ys[j]), (xs[z], ys[j]))
                shape.finish(color=_rgb(b.lines), width=b.line_width, closePath=False)
    if _rgb(b.border) and b.border_width > 0:
        _path(shape, b.rect, k)
        shape.finish(color=_rgb(b.border), fill=None, width=b.border_width, closePath=True)
    shape.commit()
    size, color = b.text_size, _rgb(b.text_color) or (0, 0, 0)
    for r0, c0, _, _, (cx0, cy0, cx1, cy1) in cells(b):
        text = b.texts.get(f"{r0},{c0}", "").strip()
        if not text:
            continue
        head = _is_head(b, r0, c0)
        font = design.font_for(design.font_spec(b.font if b.font in design.FONTS else "Helvetica", bold=head))
        if head:
            w = font.text_length(text, size)
            origin = ((cx0 + cx1 - w) / 2, (cy0 + cy1) / 2 + size * 0.35)
        else:
            origin = (cx0 + 4, cy0 + 4 + size * 0.8)
        design._write(page, origin, text, font, size, color)


def _runs(n: int, keep) -> list[tuple[int, int]]:
    """The stretches [a, z) of 0..n-1 where keep(i) holds."""
    out, start = [], None
    for i in range(n + 1):
        if i < n and keep(i):
            start = i if start is None else start
        elif start is not None:
            out.append((start, i))
            start = None
    return out


def draw(page, b: Block) -> None:
    """The block as plain shapes and text, underneath the page's content (just above a page-size background
    sheet, when there is one): what the PDF or the user put on it stays visible."""
    from pdflex import content
    area = page.rect.get_area()
    sheets = [d["seqno"] for d in page.get_cdrawings()
              if d.get("fill") is not None and pymupdf.Rect(d["rect"]).get_area() >= 0.9 * area]
    data, pos, ctm = content.place(page, "after", max(sheets)) if sheets else content.place(page, "back")
    mark = content.isolate(page, data)
    _paint(page, b)
    content.splice(page, data, pos, ctm, mark)


def own_texts(objs: list, b: Block) -> list:
    """The text objects that are the block's own: inside it, every word one of its cells' words."""
    allowed = {w for t in b.texts.values() for w in t.split()}
    box = pymupdf.Rect(b.rect)
    out = []
    for o in objs:
        if o.kind != "text" or not box.contains(pymupdf.Rect(o.rect).tl + (pymupdf.Rect(o.rect).br - pymupdf.Rect(o.rect).tl) * 0.5):
            continue
        words = "".join(s["text"] for s in o.data["spans"]).split()
        if words and all(w in allowed for w in words):
            out.append(o)
    return out


def preview(b: Block, w: int, h: int):
    """A picture of the block, at most w x h pixels (for the gallery)."""
    from PySide6.QtGui import QImage, QPixmap
    bw, bh = b.rect[2] - b.rect[0], b.rect[3] - b.rect[1]
    doc = pymupdf.open()
    page = doc.new_page(width=bw + 2, height=bh + 2)
    _paint(page, scaled(b, (1, 1, bw + 1, bh + 1)))
    zoom = min(w / (bw + 2), h / (bh + 2))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())
