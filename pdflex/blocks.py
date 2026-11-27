"""Blocks: a rectangle split into rows and columns, drawn on the page as plain shapes and text, kept editable
through the Editor's note in the PDF (see designer.py). Every operation returns a new Block."""
import dataclasses
from dataclasses import dataclass, field

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
