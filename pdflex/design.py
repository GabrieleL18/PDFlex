"""The Editor's engine: a page's objects (lines, boxes, shapes, pictures, text) and changes made in place.

Only what is touched changes: the object is taken off the page with a redaction limited to its own kind
(lines leave text alone, text leaves lines alone), whatever went with it is drawn back as it was, and the
object is drawn again where it goes. Fields and buttons are annotations: redactions never touch them."""
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import content
from pdflex.i18n import tr

THIN = 2.0  # pt: a filled box thinner than this is a line
GLYPH = 18.0  # pt: filled shapes up to this size, side by side, are letters drawn as shapes
GLYPH_GAP = 6.0  # pt between two letters of the same word / line
ROTATED = tr("Pagina ruotata: per modificarla, raddrizzala prima nella sezione Pagine.")


def _image_info(page) -> list[dict]:
    # ponytail: PyMuPDF keeps the first answer on the page object (page._image_info): after a change it would
    # still list the pictures as they were. Drop the copy each time; remove when PyMuPDF refreshes it itself.
    page._image_info = None
    # an image clipped away entirely shows nothing (a moved line of text may carry one, out of sight)
    return [i for i in page.get_image_info(xrefs=True) if not pymupdf.Rect(i["bbox"]).is_empty]


def _images(page) -> list[dict]:
    """The pictures drawn on the page, as get_image_info gives them (bbox, xref, transform): with those it leaves
    out, drawn under 50 % opacity, found in the page's own stream."""
    info = _image_info(page)
    shown = [pymupdf.Rect(b) for t, b in page.get_bboxlog() if "image" in t]
    missing = [r for r in shown if not any(content._same(r, i["bbox"]) for i in info)]
    if missing:
        names = {i[7]: i[0] for i in page.get_images(full=True)}
        for name, t in content.image_draws(page):
            r = pymupdf.Rect(0, 0, 1, 1) * t
            hit = next((k for k, x in enumerate(missing) if content._same(r, x)), None)
            if names.get(name) and hit is not None:
                missing.pop(hit)
                info.append({"bbox": tuple(r), "xref": names[name], "transform": tuple(t)})
    return info


class EditError(Exception):
    """A change that could not be made whole: the caller puts the page back as it was."""


@dataclass
class Obj:
    kind: str  # "line" | "rect" | "shape" | "image" | "text"
    rect: tuple  # page coordinates, as get_drawings / get_text give them
    state: str = "edit"  # "edit" | "move" (moved, resized, deleted; not changed inside) | "locked"
    reason: str = ""  # why it is "move" or "locked": tooltip and banner
    data: dict = field(default_factory=dict)


def _box(item) -> pymupdf.Rect | None:
    """The rectangle a path item draws, if it is one: 're', an axis-aligned 'qu'."""
    if item[0] == "re":
        return pymupdf.Rect(item[1])
    if item[0] == "qu" and pymupdf.Quad(item[1]).is_rectangular:
        return pymupdf.Quad(item[1]).rect
    return None


def _closed_box(items) -> pymupdf.Rect | None:
    """Four straight sides at right angles, closed: a rectangle drawn line by line."""
    if len(items) != 4 or any(i[0] != "l" for i in items):
        return None
    pts = [i[1] for i in items] + [items[-1][2]]
    if abs(pts[0] - pts[-1]) > 0.5:
        return None
    if not all(abs(a.x - b.x) < 0.5 or abs(a.y - b.y) < 0.5 for a, b in zip(pts, pts[1:])):
        return None
    return pymupdf.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))


_STYLE = ("fill", "color", "width", "dashes", "lineCap", "lineJoin", "closePath", "even_odd", "fill_opacity",
          "stroke_opacity")


def _full(d: dict) -> dict:
    """A path of get_cdrawings (plain tuples, 5 times faster to read) as get_drawings gives it: points and
    rectangles, every style key. Only for the paths that become objects of their own: not for every letter."""
    P = pymupdf.Point
    conv = {"l": lambda i: (i[0], P(i[1]), P(i[2])), "re": lambda i: (i[0], pymupdf.Rect(i[1]), *i[2:]),
            "qu": lambda i: (i[0], pymupdf.Quad(i[1])), "c": lambda i: (i[0], *map(P, i[1:5]))}
    return {**{k: None for k in _STYLE}, **d, "rect": pymupdf.Rect(d["rect"]),
            "items": [conv.get(i[0], tuple)(i) for i in d["items"]]}


def _near(a, b) -> bool:
    return abs(a[0] - b[0]) < 0.05 and abs(a[1] - b[1]) < 0.05


def _arrows(items) -> tuple[bool, bool] | None:
    """A line with arrow heads, as set_arrows draws it: two short strokes meeting at an end, for each head."""
    if len(items) not in (3, 5) or any(i[0] != "l" for i in items):
        return None
    a, b, ends = items[0][1], items[0][2], [False, False]
    for k in range(1, len(items), 2):
        tip = items[k][2]
        if not _near(tip, items[k + 1][1]) or not (_near(tip, a) or _near(tip, b)):
            return None
        ends[0 if _near(tip, a) else 1] = True
    return tuple(ends)


def _round_box(items) -> tuple[pymupdf.Rect, float] | None:
    """A box with round corners, as set_round draws it: four straight sides and four quarter curves."""
    if len(items) != 8 or "".join(i[0][0] for i in items) not in ("lclclclc", "clclclcl"):
        return None
    pts = [p for i in items for p in i[1:]]
    if not all(abs(i[1].x - i[2].x) < 0.05 or abs(i[1].y - i[2].y) < 0.05 for i in items if i[0] == "l"):
        return None
    c = next(i for i in items if i[0] == "c")
    return pymupdf.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts)), \
        abs(c[4].x - c[1].x)


def _shape(d) -> Obj:
    items, r = d["items"], pymupdf.Rect(d["rect"])
    data = {"drawing": d}
    if len(items) == 1 and items[0][0] == "l":
        return Obj("line", tuple(r), data={**data, "p1": tuple(items[0][1]), "p2": tuple(items[0][2])})
    heads = _arrows(items)
    if heads:  # its box from its points: MuPDF's leaves out part of a head
        pts = [p for i in items for p in i[1:]]
        r = pymupdf.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))
        return Obj("line", tuple(r), data={**data, "p1": tuple(items[0][1]), "p2": tuple(items[0][2]), "arrows": heads})
    rounded = _round_box(items)
    if rounded:
        return Obj("rect", tuple(rounded[0]), data={**data, "round": rounded[1]})
    box = _box(items[0]) if len(items) == 1 else _closed_box(items)
    if box is not None:
        if d.get("fill") is not None and min(box.width, box.height) < THIN:  # a line drawn as a thin box
            mid = (box.y0 + box.y1) / 2 if box.height < box.width else (box.x0 + box.x1) / 2
            p1, p2 = (((box.x0, mid), (box.x1, mid)) if box.height < box.width else ((mid, box.y0), (mid, box.y1)))
            return Obj("line", tuple(r), data={**data, "p1": p1, "p2": p2, "thick": min(box.width, box.height)})
        return Obj("rect", tuple(r), data=data)
    return Obj("shape", tuple(r), data=data)


def _glyph(d) -> bool:
    """A letter, or a whole word, drawn as a filled outline: low, and curved unless it is tiny."""
    r = pymupdf.Rect(d["rect"])
    if d["type"] != "f" or r.height > GLYPH or (len(d["items"]) == 1 and _box(d["items"][0]) is not None):
        return False
    return r.width <= GLYPH or any(i[0] == "c" for i in d["items"])


def _group_glyphs(glyphs: list[dict]) -> list[Obj]:
    """Letters drawn as shapes, joined into words / lines: one object each, moved as a whole."""
    out: list[list[dict]] = []
    for d in sorted(glyphs, key=lambda d: (round(d["rect"][3]), d["rect"][0])):
        x0, y0, _, y1 = d["rect"]
        for group in out:
            lx0, ly0, lx1, ly1 = group[-1]["rect"]  # plain numbers: hundreds of letters times their groups
            if abs((ly0 + ly1) / 2 - (y0 + y1) / 2) <= 3 and -1 <= x0 - lx1 <= GLYPH_GAP:
                group.append(d)
                break
        else:
            out.append([d])
    objs = []
    for group in out:
        r = pymupdf.Rect(group[0]["rect"])
        for d in group[1:]:
            r |= d["rect"]
        objs.append(Obj("shape", tuple(r), "move", tr("Testo disegnato come forme: si sposta e si ridimensiona, "
                        "le parole non si cambiano"), {"drawings": group}))
    return objs


def _inside(r: pymupdf.Rect, clips: list[pymupdf.Rect]) -> bool:
    return all(c.contains(r) or (r.is_empty and c.contains(r.tl) and c.contains(r.br)) for c in clips)


def _clip_map(drawings: list[dict]) -> dict[int, list[pymupdf.Rect]]:
    """The clips each path is drawn under, by sequence number (drawings: page.get_cdrawings(extended=True))."""
    out, clips = {}, {}  # level -> scissor of the clip open at that level
    for d in drawings:
        level = d.get("level", 0)
        for k in [k for k in clips if k >= level]:
            del clips[k]
        if d["type"] == "clip":
            clips[level] = pymupdf.Rect(d["scissor"])
        elif d["type"] != "group":
            out[d["seqno"]] = list(clips.values())
    return out


def _annot_rects(page) -> list[pymupdf.Rect]:
    """Where the fields, buttons and notes are. Read from their /Rect: loading 490 fields as widgets takes 0.5 s."""
    doc, m, out = page.parent, page.transformation_matrix, []
    for xref, kind, _ in page.annot_xrefs():
        if kind in (pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_POPUP):
            continue
        typ, val = doc.xref_get_key(xref, "Rect")
        if typ == "array":
            out.append((pymupdf.Rect([float(v) for v in val.strip("[]").split()]) * m).normalize())
    return out


# Only page_objects reads the paths in full (get_drawings): everywhere else the raw form (get_cdrawings, plain
# tuples) is enough and 5 times faster, which on a page of hundreds of paths is the difference between a move
# that keeps up with the arrows and one that takes seconds.
def _key(d: dict) -> tuple:
    return (d["type"], *(round(v, 1) for v in d["rect"]), d.get("fill"), d.get("color"))


def _stuck(page) -> set[tuple]:
    """Paths the redaction cannot take off this page (some PDFs draw them so): they can only stay as they are.
    Found by trying it on a copy of the page."""
    src = pymupdf.open()
    src.insert_pdf(page.parent, from_page=page.number, to_page=page.number, annots=False)
    sp = src[0]
    sp.add_redact_annot(sp.rect)
    sp.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                        text=pymupdf.PDF_REDACT_TEXT_NONE)
    return {_key(d) for d in sp.get_cdrawings()}


def page_objects(page: pymupdf.Page) -> tuple[list[Obj], list[str]]:
    """The page's objects in drawing order, and the lines for the warning banner."""
    objs, glyphs, locked = [], [], 0
    clips = _clip_map(page.get_cdrawings(extended=True))  # extended mode for the clips only: it splits a filled
    plain = page.get_cdrawings()  # and stroked path in two, where plain mode gives one ("fs": one object)
    own = content.paths(page, plain)[1]  # changed where they are drawn: the redaction is not needed for them
    stuck = _stuck(page) if any(d["seqno"] not in own for d in plain) else set()
    for d in plain:
        if not _inside(pymupdf.Rect(d["rect"]), clips.get(d["seqno"], [])):
            o = _shape(_full(d))
            o.state, o.reason = "locked", tr("Ritagliato: resta com'è")
            objs.append(o)
            locked += 1
        elif _key(d) in stuck and d["seqno"] not in own:
            o = _shape(_full(d))
            o.state, o.reason = "locked", tr("PDFlex non riesce a staccarlo dalla pagina: resta com'è")
            objs.append(o)
            locked += 1
        elif _glyph(d):
            glyphs.append(d)  # as they are: a letter is only moved, its points stay plain numbers
        else:
            objs.append(_shape(_full(d)))
    objs += _group_glyphs(glyphs)
    for info in _images(page):
        o = Obj("image", tuple(pymupdf.Rect(info["bbox"])), data={"xref": info["xref"], "transform": info["transform"]})
        if not info["xref"]:
            o.state, o.reason = "locked", tr("Immagine incorporata nel testo della pagina: resta com'è")
            locked += 1
        objs.append(o)
    fonts = {f[3].split("+")[-1]: f[0] for f in page.get_fonts()}
    widgets = _annot_rects(page)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            r = pymupdf.Rect(line["bbox"])
            if not spans or any(w.contains(r) or w.intersects(r) and (w & r).get_area() > r.get_area() / 2
                                for w in widgets):
                continue  # empty, or the text of a field / button
            objs.append(Obj("text", tuple(r), data={"spans": spans, "font_xref": fonts.get(spans[0]["font"]),
                                                    "dir": tuple(line["dir"])}))
    warnings = []
    if glyphs:
        warnings.append(tr("Testo disegnato come forme: si sposta, ma le parole non si possono cambiare ({0} forme).").format(len(glyphs)))
    if locked:
        warnings.append(tr("{0} oggetti restano come sono (sfumature, ritagli o forme che PDFlex non riesce a staccare dalla pagina).").format(locked))
    if page.rotation:
        for o in objs:
            o.state, o.reason = "locked", ROTATED
        warnings = [ROTATED]
    return objs, warnings


# ---------------------------------------------------------------- changes made in place

_BASE14 = {"": "helv", "TiRo": "tiro", "Cour": "cour"}  # fields.FONTS keys -> PyMuPDF's built-in fonts

# fonts for text: family -> (regular, bold, italic, bold italic). The first three every PDF reader has; the
# others are Windows's own, put in the PDF with only the letters used (a missing file: the regular one instead)
_BUILT_IN = {"Helvetica": ("helv", "hebo", "heit", "hebi"), "Times": ("tiro", "tibo", "tiit", "tibi"),
             "Courier": ("cour", "cobo", "coit", "cobi")}
_WINDOWS = {
    "Arial": ("arial", "arialbd", "ariali", "arialbi"),
    "Book Antiqua": ("bkant", "antquab", "antquai", "antquabi"),
    "Bookman Old Style": ("bookos", "bookosb", "bookosi", "bookosbi"),
    "Calibri": ("calibri", "calibrib", "calibrii", "calibriz"),
    "Cambria": ("cambria", "cambriab", "cambriai", "cambriaz"),
    "Candara": ("candara", "candarab", "candarai", "candaraz"),
    "Century": ("century",) * 4,
    "Century Gothic": ("gothic", "gothicb", "gothici", "gothicbi"),
    "Comic Sans MS": ("comic", "comicbd", "comici", "comicz"),
    "Consolas": ("consola", "consolab", "consolai", "consolaz"),
    "Constantia": ("constan", "constanb", "constani", "constanz"),
    "Corbel": ("corbel", "corbelb", "corbeli", "corbelz"),
    "Franklin Gothic": ("framd", "framd", "framdit", "framdit"),
    "Garamond": ("gara", "garabd", "garait", "gara"),
    "Georgia": ("georgia", "georgiab", "georgiai", "georgiaz"),
    "Impact": ("impact",) * 4,
    "Ink Free": ("inkfree",) * 4,
    "Lucida Console": ("lucon",) * 4,
    "Palatino Linotype": ("pala", "palab", "palai", "palabi"),
    "Segoe Print": ("segoepr", "segoeprb", "segoepr", "segoeprb"),
    "Segoe Script": ("segoesc", "segoescb", "segoesc", "segoescb"),
    "Segoe UI": ("segoeui", "segoeuib", "segoeuii", "segoeuiz"),
    "Tahoma": ("tahoma", "tahomabd", "tahoma", "tahomabd"),
    "Times New Roman": ("times", "timesbd", "timesi", "timesbi"),
    "Trebuchet MS": ("trebuc", "trebucbd", "trebucit", "trebucbi"),
    "Verdana": ("verdana", "verdanab", "verdanai", "verdanaz"),
}


def _font_file(name: str) -> str | None:
    folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    return next((str(f) for f in (folder / f"{name}.ttf", folder / f"{name.upper()}.TTF") if f.exists()), None)


FONTS = {**_BUILT_IN, **{fam: tuple(_font_file(f) or _font_file(v[0]) for f in v)
                         for fam, v in _WINDOWS.items() if _font_file(v[0])}}


def font_spec(family: str, bold: bool = False, italic: bool = False) -> str:
    """What restyle(fontname=...) takes for this family and style."""
    return FONTS[family][bold + 2 * italic]


def font_style(page, obj: Obj) -> tuple[str | None, bool, bool]:
    """A text's family among FONTS (None: one of its own), and whether it is bold, italic."""
    span = obj.data["spans"][0]
    name = span["font"].split("+")[-1].lower().replace(" ", "").replace("-", "")
    bold = bool(span["flags"] & 16) or "bold" in name
    italic = bool(span["flags"] & 2) or "italic" in name or "oblique" in name
    for alias, fam in (("nimbussans", "helvetica"), ("nimbusroman", "times"), ("nimbusmono", "courier")):
        name = name.replace(alias, fam)  # the built-in three, as PyMuPDF writes them (URW's look-alikes)
    family = next((f for f in sorted(FONTS, key=len, reverse=True) if f.lower().replace(" ", "") in name), None)
    return family, bold, italic


def _drawings(o: Obj) -> list[dict]:
    return o.data.get("drawings") or ([o.data["drawing"]] if "drawing" in o.data else [])


def _area(o: Obj) -> pymupdf.Rect:
    r = pymupdf.Rect(o.rect)
    if o.kind == "text":  # the middle of the line: its characters, not the neighbours above and below
        dx, dy = o.data.get("dir", (1, 0))
        if abs(dy) < 0.01:
            h = r.height
            return pymupdf.Rect(r.x0 + 0.5, r.y0 + h * 0.25, r.x1 - 0.5, r.y1 - h * 0.25)
        if abs(dx) < 0.01:  # reading up or down the page: its neighbours are left and right
            w = r.width
            return pymupdf.Rect(r.x0 + w * 0.25, r.y0 + 0.5, r.x1 - w * 0.25, r.y1 - 0.5)
        return r + (0.5, 0.5, -0.5, -0.5)  # slanted: its box, neighbours are checked afterwards
    if o.kind == "image":
        return r + (0.5, 0.5, -0.5, -0.5)
    for d in _drawings(o):  # the redaction takes a path only if it covers its stroke too
        r |= _inked(d)
    return r


def _inked(d: dict) -> pymupdf.Rect:
    """Where a path leaves ink: its outline grown by half the stroke width."""
    hw = (d.get("width") or 0) / 2 if d.get("color") is not None else 0
    return pymupdf.Rect(d["rect"]) + (-hw, -hw, hw, hw)


MARGINS = (0.5, 1.5, 3, 6, 12, 25, 50)  # pt around a shape, tried in turn until the redaction takes it
# (MuPDF wants up to 25 pt for a path inside nested forms)


def _redact_shapes(page, areas: list[pymupdf.Rect]) -> None:
    page_rect = page.rect
    for r in areas:
        page.add_redact_annot(r & page_rect if r.intersects(page_rect) else r)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=pymupdf.PDF_REDACT_TEXT_NONE)


def _removed(before: list[dict], after: list[dict]) -> Counter:
    return Counter(map(_key, before)) - Counter(map(_key, after))


def _shape_margin(page, areas: list[pymupdf.Rect], targets: Counter, before: list[dict]) -> float:
    """How much room the redaction needs around these shapes to take them all: MuPDF measures a path its own
    way (curve handles, sharp corners), so it is tried on a copy of the page rather than guessed. A margin
    that cuts a neighbouring path only in part (leaving pieces of it) is no good either."""
    taken = cut = False
    for m in MARGINS:
        src = pymupdf.open()
        src.insert_pdf(page.parent, from_page=page.number, to_page=page.number, annots=False)
        _redact_shapes(src[0], [a + (-m, -m, m, m) for a in areas])
        after = src[0].get_cdrawings()
        if not targets - _removed(before, after):
            taken = True
            if not Counter(map(_key, after)) - Counter(map(_key, before)):  # no piece of a path left behind
                return m
            cut = True
    if taken and cut:
        raise EditError(tr("Questa forma è intrecciata con un'altra grande forma della pagina: PDFlex non riesce a "
                        "modificarla da sola, e resta com'è."))
    raise EditError(tr("PDFlex non riesce a staccare questa forma dalla pagina: resta com'è."))


def _mapping(old: pymupdf.Rect, new: pymupdf.Rect) -> pymupdf.Matrix:
    """The matrix that takes rectangle old onto rectangle new (a flat side keeps its size)."""
    sx = new.width / old.width if old.width > 0.01 else 1
    sy = new.height / old.height if old.height > 0.01 else 1
    return pymupdf.Matrix(sx, 0, 0, sy, new.x0 - old.x0 * sx, new.y0 - old.y0 * sy)


def _straight(m: pymupdf.Matrix) -> bool:
    return abs(m.b) < 1e-9 and abs(m.c) < 1e-9


def _turn(m: pymupdf.Matrix) -> float:
    """How far m turns, in degrees, clockwise on the page."""
    return math.degrees(math.atan2(m.b, m.a))


def _put_image(page, t: pymupdf.Matrix, overlay: bool = True, opacity: float = 1, **source) -> None:
    """A picture drawn through t (unit square -> page, as get_image_info's "transform"): turned or flipped as t
    says, not only stretched over a box as insert_image draws it. source: xref=, stream= or pixmap=."""
    # the transparency first: set after the picture, it would take the picture's entry out of the resources
    gs = b"/" + page._set_opacity(CA=opacity, ca=opacity).encode() + b" gs " if opacity < 1 else b""
    page.insert_image(pymupdf.Rect(0, 0, 1, 1) * t, keep_proportion=False, overlay=overlay, **source)
    c = page.get_contents()[-1 if overlay else 0]
    cm = pymupdf.Matrix(1, 0, 0, -1, 0, 1) * t * ~page.transformation_matrix  # PDF space, image rows upward
    data = page.parent.xref_stream(c)
    new = re.sub(rb"[-+.\d\s]+cm(\s*)(/[^\s/]+\s+Do)", lambda g: b"\n" + b" ".join(content._num(v) for v in cm)
                 + b" cm" + g.group(1) + gs + g.group(2), data, count=1)
    page.parent.update_stream(c, new)


def opacity(page, obj: Obj) -> float:
    """How opaque obj is drawn, 0 to 1."""
    if obj.kind == "text":
        return obj.data["spans"][0].get("alpha", 255) / 255
    if obj.kind == "image":  # the transparency set just before it is drawn, as _put_image writes it
        name = next((i[7] for i in page.get_images(full=True) if i[0] == obj.data["xref"]), None)
        data = b"\n".join(page.parent.xref_stream(x) or b"" for x in page.get_contents())
        g = re.search(rb"/([^\s/]+)\s+gs\s+/" + re.escape(name.encode()) + rb"\s+Do", data) if name else None
        if g:
            typ, val = page.parent.xref_get_key(page.xref, f"Resources/ExtGState/{g.group(1).decode()}/ca")
            return float(val) if typ == "float" or typ == "int" else 1.0
        return 1.0
    d = obj.data.get("drawing") or {}
    return d.get("fill_opacity") if d.get("fill_opacity") is not None else d.get("stroke_opacity") or 1.0


def dash_style(obj: Obj) -> tuple:
    """A drawing's dashes, as on and off lengths in points (() solid)."""
    d = (obj.data.get("drawing") or {}).get("dashes") or ""
    return tuple(float(v) for v in d[d.find("[") + 1:d.find("]")].split()) if "[" in d else ()


def _draw_items(shape, items, m: pymupdf.Matrix) -> None:
    P = pymupdf.Point  # the raw form gives plain tuples
    for it in items:
        if it[0] == "l":
            shape.draw_line(P(it[1]) * m, P(it[2]) * m)
        elif it[0] == "re" and _straight(m):
            shape.draw_rect(pymupdf.Rect(it[1]).normalize() * m)  # some PDFs draw boxes with a negative width
        elif it[0] == "re":  # turned: its four corners
            shape.draw_quad(pymupdf.Rect(it[1]).normalize().quad * m)
        elif it[0] == "qu":
            shape.draw_quad(pymupdf.Quad(it[1]) * m)
        elif it[0] == "c":
            shape.draw_bezier(P(it[1]) * m, P(it[2]) * m, P(it[3]) * m, P(it[4]) * m)
        else:
            raise EditError(tr("Una forma contiene un tratto che PDFlex non sa ridisegnare."))


def _finish(shape, d: dict, style: dict) -> None:
    cap = d.get("lineCap") or 0
    dashes = ("[" + " ".join(f"{v:g}" for v in style["dashes"]) + "] 0") if "dashes" in style else d.get("dashes")
    shape.finish(color=style.get("color", d.get("color")), fill=style.get("fill", d.get("fill")),
                 width=style.get("width", d.get("width")) or 0, dashes=dashes or None,
                 lineCap=max(cap) if isinstance(cap, (tuple, list)) else cap, lineJoin=int(d.get("lineJoin") or 0),
                 closePath=bool(d.get("closePath")), even_odd=bool(d.get("even_odd")),
                 fill_opacity=style.get("opacity", 1 if d.get("fill_opacity") is None else d["fill_opacity"]),
                 stroke_opacity=style.get("opacity", 1 if d.get("stroke_opacity") is None else d["stroke_opacity"]))


def _own_font(page, o: Obj) -> pymupdf.Font | None:
    xref = o.data.get("font_xref")
    buf = page.parent.extract_font(xref)[3] if xref else b""
    return pymupdf.Font(fontbuffer=buf) if buf else None


def font_for(spec: str) -> pymupdf.Font:
    """The font a font_spec (or a fields.FONTS key) names."""
    if spec in _BASE14:
        return pymupdf.Font(_BASE14[spec])
    if spec.lower().endswith((".ttf", ".otf")):
        font = pymupdf.Font(fontfile=spec)
        font.from_file = True  # _write puts in only the letters it uses
        return font
    return pymupdf.Font(spec)


def _text_font(page, o: Obj, span: dict, override: str | None, text: str) -> pymupdf.Font:
    """The font new words are written with: the text's own one when it can write them, else the closest built-in."""
    if override is not None:
        return font_for(override)
    font = _own_font(page, o)
    if font is not None:
        missing = missing_letters(page, o, text)
        if missing:
            raise EditError(tr("PDFlex non riesce a scrivere con il carattere di questo testo le lettere: {0}").format(missing))
        return font
    name = span["font"].lower()
    base = "tiro" if "times" in name else "cour" if "courier" in name else "helv"
    return pymupdf.Font({"helv": "hebo", "tiro": "tibo", "cour": "cobo"}[base] if "bold" in name else base)


def _write(page, origin, text: str, font: pymupdf.Font, fontsize: float, color, way=(1, 0), alpha=1.0) -> None:
    """way: the direction it reads in, on the page ((1, 0): left to right)."""
    # TextWriter, not insert_text: insert_text writes the built-in fonts in Latin-1 and loses "€"
    tw = pymupdf.TextWriter(page.rect)
    for i, line in enumerate(text.split("\n")):
        tw.append(pymupdf.Point(origin) + (0, i * fontsize * 1.2), line, font=font, fontsize=fontsize)
    dx, dy = way  # TextWriter turns in PDF space, upward: the other way round
    morph = None if abs(dy) < 1e-6 and dx > 0 else (pymupdf.Point(origin), pymupdf.Matrix(dx, -dy, dy, dx, 0, 0))
    if not getattr(font, "from_file", False):
        tw.write_text(page, color=color, morph=morph, opacity=alpha if alpha < 1 else -1)
        return
    # a font file goes in whole (Arial: 1 MB): written on a page of its own, cut down to the letters it uses,
    # then shown on this one. Not subset_fonts on the PDF itself: it would cut the fonts of its fields too
    tmp = pymupdf.open()
    tw.write_text(tmp.new_page(width=page.rect.width, height=page.rect.height), color=color, morph=morph,
                  opacity=alpha if alpha < 1 else -1)
    tmp.subset_fonts()
    page.show_pdf_page(page.rect, tmp, 0)


def _text_args(page, o: Obj, m: pymupdf.Matrix, style: dict, text: str | None) -> tuple:
    """What _write needs to write the line again (moved by m) with new words or a new colour or font: its first
    span gives place, size and look. Worked out before the page is touched, so a refusal leaves it as it was."""
    s = o.data["spans"][0]
    words = "".join(sp["text"] for sp in o.data["spans"]) if text is None else text
    try:
        font = _text_font(page, o, s, style.get("fontname"), words)
    except EditError:
        if text is None and "fontname" not in style:  # only moving it
            raise EditError(tr("Questo testo è intrecciato con un disegno e il suo carattere non si può riscrivere: "
                            "PDFlex non riesce a spostarlo.")) from None
        raise
    way, still = pymupdf.Point(o.data.get("dir", (1, 0))), pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)
    up = pymupdf.Point(way.y, -way.x) * still  # its letters' height through m: the size it gets
    way = way * still
    way = way / (abs(way) or 1)
    size = style.get("fontsize") or s["size"] * abs(up)
    origin = pymupdf.Point(s["origin"]) * m
    k = style.get("anchor", 0)
    if k and (text is not None or "fontname" in style):  # the other edge, or the middle, stays where it was
        last = pymupdf.Rect(o.data["spans"][-1]["bbox"]) * m
        old = max((c - origin).x * way.x + (c - origin).y * way.y for c in last.quad)
        origin += way * ((old - font.text_length(words, fontsize=size)) * k)
    return (origin, words, font, size, style.get("color") or pymupdf.sRGB_to_pdf(s["color"]), tuple(way),
            style.get("opacity", s.get("alpha", 255) / 255))


def _clip(o: Obj) -> pymupdf.Rect:
    return pymupdf.Rect(o.rect) + (-1, -0.5, 1, 0.5)


def _text_source(page, keep: list[Obj]) -> tuple[pymupdf.Document, list[Obj]]:
    """A copy of the page holding only the text lines in keep, to be shown again elsewhere with their own glyphs:
    no font is rewritten, so it works also with the many fonts a PDF carries without a map of their letters.
    Also returns the lines the copy shows cleanly: alone, with nothing else inside their box."""
    src = pymupdf.open()
    src.insert_pdf(page.parent, from_page=page.number, to_page=page.number, annots=False)
    sp = src[0]
    sp.add_redact_annot(sp.rect)
    sp.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE, graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                        text=pymupdf.PDF_REDACT_TEXT_NONE)
    mids = [_mid(_clip(o)) for o in keep]  # a line's box may come out a little different in the copy
    others = [o for o in page_objects(sp)[0]
              if o.kind == "text" and not any(pymupdf.Rect(o.rect).contains(m) for m in mids)]
    for o in others:
        sp.add_redact_annot(_area(o))
    if others:
        sp.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                            text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    # what the copy could not shed (images in nested forms, bits of other lines) would show twice
    left = [pymupdf.Rect(i["bbox"]) for i in _image_info(sp)] + [pymupdf.Rect(d["rect"]) for d in sp.get_cdrawings()]
    copy_words, page_words = _words(sp), _words(page)
    clean = [o for o in keep if not any(r.intersects(_clip(o)) for r in left)
             and sorted(w for w, r in copy_words if r.intersects(_clip(o)))
             == sorted(w for w, r in page_words if pymupdf.Rect(o.rect).contains(_mid(r)))]
    return src, clean


def _mid(r: pymupdf.Rect) -> pymupdf.Point:
    return (r.tl + r.br) / 2


def _words(page) -> list[tuple]:
    return [(w[4], pymupdf.Rect(w[:4])) for w in page.get_text("words")]


def _edit_in_place(page, shapes: list[tuple], before: list[dict]) -> bool:
    """Shapes changed right where the page draws them (see content.py): their place among the other drawings
    stays. Not when one would leave its clip: there it would be cut, drawn again it is whole."""
    clips = _clip_map(page.get_cdrawings(extended=True))
    for o, m, _ in shapes:
        for d in _drawings(o):
            if m is not None and not _inside(_inked(d) * m, clips.get(d["seqno"], [])):
                return False
    styles = []
    for _, _, style in shapes:
        if "opacity" in style:
            style = {**style, "gs": page._set_opacity(CA=style["opacity"], ca=style["opacity"])}
        styles.append(style)
    return content.edit(page, {d["seqno"]: (m, style) for (o, m, _), style in zip(shapes, styles)
                               for d in _drawings(o)}, before)


def _replace(page, objs: list[Obj], new: list[tuple], layer: tuple | None = None) -> None:
    """Take objs off the page and draw each again through its matrix (None: deleted), with its style overrides
    and, for text, new words: new[i] = (matrix, style, text). What the redactions took with them is drawn back
    as it was. Afterwards the page is checked: EditError if anything else went missing.
    layer: (where, k) to draw them again elsewhere among the rest, as content.place reads it (see restack)."""
    for o in objs:
        if o.state == "locked":
            raise EditError(o.reason or tr("Questo oggetto non si può modificare."))
    before = page.get_cdrawings()
    targets = Counter(_key(d) for o in objs for d in _drawings(o))
    shapes = [(o, m, style) for o, (m, style, _) in zip(objs, new) if o.kind not in ("text", "image")]
    drawn_before = len(page.get_bboxlog()) if layer else 0
    if layer and shapes:  # taken off where they are, drawn again where they go: nothing else is touched
        in_place = content.edit(page, {d["seqno"]: (None, {}) for o, _, _ in shapes for d in _drawings(o)}, before)
        if not in_place:
            raise EditError(tr("PDFlex non riesce a staccare questa forma dal suo posto per portarla sopra o sotto."))
    else:
        in_place = bool(shapes) and _edit_in_place(page, shapes, before)  # else taken off and drawn again
    areas = {"shape": [], "text": [], "image": []}
    for o in objs:
        if in_place and o.kind not in ("text", "image"):
            continue
        areas["text" if o.kind == "text" else "image" if o.kind == "image" else "shape"].append(_area(o))
    if areas["shape"]:
        m = _shape_margin(page, areas["shape"], targets, before)
        areas["shape"] = [a + (-m, -m, m, m) for a in areas["shape"]]
    copied = [o for o, (m, style, text) in zip(objs, new)
              if o.kind == "text" and m is not None and text is None and not {"color", "fontname", "opacity"} & style.keys()]
    src, copied = _text_source(page, copied) if copied else (None, [])  # the others are written again
    rewritten = {id(o): _text_args(page, o, m, style, text) for o, (m, style, text) in zip(objs, new)
                 if o.kind == "text" and m is not None and not any(o is c for c in copied)}
    images_before = len(_images(page))
    alpha = {id(o): opacity(page, o) for o in objs if o.kind == "image"}  # read before it is taken off
    words_kept = [w[0] for w in _words(page) if not any(a.intersects(w[1]) for a in areas["text"])]
    collateral = []
    if areas["shape"]:  # what went with the shapes is drawn back as it was: seen, not guessed
        _redact_shapes(page, areas["shape"])
        extra = _removed(before, page.get_cdrawings()) - targets
        own = {t["seqno"] for o in objs for t in _drawings(o)}
        for d in before:
            if extra[_key(d)] > 0 and d["seqno"] not in own:
                extra[_key(d)] -= 1
                collateral.append(d)
    for kind in ("text", "image"):
        if not areas[kind]:
            continue
        for r in areas[kind]:
            page.add_redact_annot(r)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_REMOVE if kind == "image" else pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE if kind == "text" else pymupdf.PDF_REDACT_TEXT_NONE)
    # shapes not changed in place go back underneath everything, in their original order: a moved background
    # never covers the text (but a sheet drawn before them covers them: in place is tried first)
    redraw = [(d, pymupdf.Identity, {}) for d in collateral]
    for o, (m, style, _) in zip(objs, new):
        if m is not None and (layer or not (in_place and o.kind not in ("text", "image"))):
            redraw += [(d, m, style) for d in _drawings(o)]
    redraw.sort(key=lambda t: t[0]["seqno"])
    if layer:  # drawn on top first, then moved where they go
        where, k = layer
        data, pos, ctm = content.place(page, where, k - (drawn_before - len(page.get_bboxlog())) * (where == "after"))
        mark = content.isolate(page, data)
    if redraw:
        shape = page.new_shape()
        for d, m, style in redraw:
            _draw_items(shape, d["items"], m)
            _finish(shape, d, style)
        shape.commit(overlay=bool(layer))
    for o, (m, style, text) in zip(objs, new):
        if any(o is c for c in copied):
            page.show_pdf_page(_clip(o) * m, src, 0, clip=_clip(o), keep_proportion=False, rotate=-_turn(m))
        elif id(o) in rewritten:
            _write(page, *rewritten[id(o)])
        elif m is not None and o.kind == "image":
            _put_image(page, pymupdf.Matrix(o.data["transform"]) * m, opacity=style.get("opacity", alpha[id(o)]),
                       xref=o.data["xref"])
    if layer:
        try:
            content.splice(page, data, pos, ctm, mark)
        except ValueError:
            raise EditError(tr("PDFlex non riesce a portare questo oggetto sopra o sotto gli altri.")) from None
    gone = sum(len(_drawings(o)) for o, (m, _, _) in zip(objs, new) if m is None)
    if len(page.get_cdrawings()) != len(before) - gone:
        raise EditError(tr("Con questa modifica sarebbe cambiata anche un'altra forma della pagina."))
    gone = sum(1 for o, (m, _, _) in zip(objs, new) if o.kind == "image" and m is None)
    if len(_images(page)) != images_before - gone:
        raise EditError(tr("Con questa modifica sarebbe sparita un'altra immagine che tocca questa."))
    after = [w[0] for w in _words(page)]
    for word in words_kept:
        if word not in after:
            raise EditError(tr("Con questa modifica sarebbe sparita anche la parola \"{0}\" lì vicino.").format(word))
        after.remove(word)


def _line_matrix(o: Obj, p1, p2) -> pymupdf.Matrix:
    """A line's new ends as a matrix on its drawing: the thickness of a line drawn as a thin box stays."""
    a, b = pymupdf.Point(o.data["p1"]), pymupdf.Point(o.data["p2"])
    c, d = pymupdf.Point(p1), pymupdf.Point(p2)
    if abs(a.y - b.y) < 0.5 and abs(c.y - d.y) < 0.5:  # horizontal stays horizontal
        sx = (d.x - c.x) / (b.x - a.x) if abs(b.x - a.x) > 0.01 else 1
        return pymupdf.Matrix(sx, 0, 0, 1, c.x - a.x * sx, c.y - a.y)
    if abs(a.x - b.x) < 0.5 and abs(c.x - d.x) < 0.5:  # vertical stays vertical
        sy = (d.y - c.y) / (b.y - a.y) if abs(b.y - a.y) > 0.01 else 1
        return pymupdf.Matrix(1, 0, 0, sy, c.x - a.x, c.y - a.y * sy)
    if "thick" in o.data:
        raise EditError(tr("Questa linea si può allungare solo nella sua direzione."))
    v, w = b - a, d - c  # a slanted line: rotate and scale it onto the new ends
    den = v.x * v.x + v.y * v.y
    if den < 1e-6:
        return pymupdf.Matrix(1, 0, 0, 1, c.x - a.x, c.y - a.y)
    cos, sin = (v.x * w.x + v.y * w.y) / den, (v.x * w.y - v.y * w.x) / den
    m = pymupdf.Matrix(cos, sin, -sin, cos, 0, 0)
    start = a * m
    return pymupdf.Matrix(cos, sin, -sin, cos, c.x - start.x, c.y - start.y)


def move(page, objs: list[Obj], dx: float, dy: float) -> None:
    _replace(page, objs, [(pymupdf.Matrix(1, 0, 0, 1, dx, dy), {}, None) for _ in objs])


def set_rect(page, obj: Obj, rect: tuple) -> None:
    """New place and size; a line takes its two ends instead: rect = (x1, y1, x2, y2)."""
    if obj.kind == "line" and "arrows" in obj.data:
        return _reshape(page, obj, _arrow_items(rect[:2], rect[2:], obj.data["arrows"], obj.data["drawing"].get("width")))
    if "round" in obj.data:
        return _reshape(page, obj, _round_items(pymupdf.Rect(rect), obj.data["round"]))
    if obj.kind == "line":
        m = _line_matrix(obj, rect[:2], rect[2:])
    else:
        m = _mapping(pymupdf.Rect(obj.rect), pymupdf.Rect(rect))
    _replace(page, [obj], [(m, {}, None)])


def turning(objs: list[Obj], degrees: float) -> pymupdf.Matrix:
    """The matrix that turns objs together around the middle of the box around them, clockwise on the page."""
    box = pymupdf.Rect(objs[0].rect)
    for o in objs[1:]:
        box |= o.rect
    c = (box.tl + box.br) / 2
    return pymupdf.Matrix(1, 0, 0, 1, -c.x, -c.y) * pymupdf.Matrix(degrees) * pymupdf.Matrix(1, 0, 0, 1, c.x, c.y)


def rotate(page, objs: list[Obj], degrees: float) -> None:
    m = turning(objs, degrees)
    _replace(page, objs, [(m, {}, None) for _ in objs])


def _stack(page, objs: list[Obj]) -> dict[int, tuple[int, int] | None]:
    """Where each object comes in what MuPDF draws, by id: the sequence numbers of its first and last piece."""
    log, spans, used, out = page.get_bboxlog(), page.get_texttrace(), set(), {}
    for o in objs:
        if o.kind == "text":
            r = _clip(o)
            seq = [sp["seqno"] for sp in spans if r.contains(_mid(pymupdf.Rect(sp["bbox"])))]
        elif o.kind == "image":
            seq = [next((i for i, (t, b) in enumerate(log) if "image" in t and i not in used
                         and content._same(pymupdf.Rect(b), o.rect)), None)]
            used.update(seq)
        else:
            seq = [d["seqno"] for d in _drawings(o)]
        seq = [q for q in seq if q is not None]
        out[id(o)] = (min(seq), max(seq)) if seq else None
    return out


def _touch(a: Obj, b: Obj) -> bool:
    return (pymupdf.Rect(a.rect) + (-0.5, -0.5, 0.5, 0.5)).intersects(pymupdf.Rect(b.rect) + (-0.5, -0.5, 0.5, 0.5))


def restack(page, obj: Obj, where: str) -> None:
    """obj drawn again over everything ("front"), under everything ("back"), just over the next thing above it
    that it touches ("forward") or just under the next one below ("backward")."""
    if obj.state == "locked":
        raise EditError(obj.reason or tr("Questo oggetto non si può modificare."))
    objs = [o for o in page_objects(page)[0] if not (o.kind == obj.kind and content._same(pymupdf.Rect(o.rect), obj.rect))]
    z = _stack(page, objs + [obj])
    if z[id(obj)] is None:
        raise EditError(tr("PDFlex non trova dove è disegnato questo oggetto."))
    near = [o for o in objs if z[id(o)] is not None and _touch(o, obj)]
    if where == "forward":
        above = [o for o in near if z[id(o)][0] > z[id(obj)][1]]
        if not above:
            raise EditError(tr("È già sopra a tutto quello che tocca."))
        layer = ("after", z[id(min(above, key=lambda o: z[id(o)][0]))][1])
    elif where == "backward":
        below = [o for o in near if z[id(o)][1] < z[id(obj)][0]]
        if not below:
            raise EditError(tr("È già sotto a tutto quello che tocca."))
        layer = ("before", z[id(max(below, key=lambda o: z[id(o)][1]))][0])
    else:
        layer = (where, 0)
    _replace(page, [obj], [(pymupdf.Identity, {}, None)], layer)


def delete(page, objs: list[Obj]) -> None:
    _replace(page, objs, [(None, {}, None) for _ in objs])


def paste(page, src_page, objs: list[Obj], dx: float, dy: float) -> None:
    """Objects of src_page (another page, of this PDF or another) drawn on page, moved by dx, dy, on top."""
    duplicate(page, objs, dx, dy, src_page)


def duplicate(page, objs: list[Obj], dx: float, dy: float, src_page=None) -> None:
    """A copy of each object, moved by dx, dy: a shape's copy goes just above it (see content.copy), else on top;
    text is copied with its own glyphs when it can be, written again otherwise. Nothing on the page is taken off.
    src_page: the objects are of that page instead (see paste)."""
    for o in objs:
        if o.state == "locked":
            raise EditError(o.reason or tr("Questo oggetto non si può modificare."))
    src_page = src_page or page
    m = pymupdf.Matrix(1, 0, 0, 1, dx, dy)
    before, images_before, words_before = page.get_cdrawings(), len(_images(page)), _words(page)
    paths = [d for o in objs if o.kind not in ("text", "image") for d in _drawings(o)]
    texts = [o for o in objs if o.kind == "text"]
    src, clean = _text_source(src_page, texts) if texts else (None, [])
    written = {id(o): _text_args(src_page, o, m, {}, None) for o in texts if not any(o is c for c in clean)}
    alpha = {id(o): opacity(src_page, o) for o in objs if o.kind == "image"}
    clips = _clip_map(page.get_cdrawings(extended=True)) if paths and src_page is page else {}
    if paths and not (src_page is page and all(_inside(_inked(d) * m, clips.get(d["seqno"], [])) for d in paths)
                      and content.copy(page, [d["seqno"] for d in paths], m, before)):
        shape = page.new_shape()
        for d in sorted(paths, key=lambda d: d["seqno"]):
            _draw_items(shape, d["items"], m)
            _finish(shape, d, {})
        shape.commit()
    for o in objs:
        if id(o) in written:
            _write(page, *written[id(o)])
        elif o.kind == "text":
            page.show_pdf_page(_clip(o) * m, src, 0, clip=_clip(o), keep_proportion=False)
        elif o.kind == "image" and src_page is page:
            _put_image(page, pymupdf.Matrix(o.data["transform"]) * m, opacity=alpha[id(o)], xref=o.data["xref"])
        elif o.kind == "image":  # from another PDF: the picture itself, with its transparency mask
            sdoc, info = src_page.parent, src_page.parent.extract_image(o.data["xref"])
            pix = pymupdf.Pixmap(sdoc, o.data["xref"])
            if info.get("smask"):
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(sdoc, info["smask"]))
            _put_image(page, pymupdf.Matrix(o.data["transform"]) * m, opacity=alpha[id(o)], pixmap=pix)
    if len(page.get_cdrawings()) != len(before) + len(paths):
        raise EditError(tr("PDFlex non è riuscito a copiare queste forme così come sono."))
    if len(_images(page)) != images_before + sum(o.kind == "image" for o in objs):
        raise EditError(tr("PDFlex non è riuscito a copiare questa immagine."))
    after = [w for w, _ in _words(page)]
    for w, _ in words_before:
        if w not in after:
            raise EditError(tr("Con questa copia sarebbe sparita la parola \"{0}\".").format(w))
        after.remove(w)


def restyle(page, objs: Obj | list[Obj], *, color=None, fill=None, width=None, fontsize=None, fontname=None,
            opacity=None, dashes=None, anchor: float = 0) -> None:
    """A new look for one object or several at once (each takes what fits it: a text no fill, a box no font).
    anchor, for text: what stays in place when it gets wider or narrower (0 left edge, 0.5 middle, 1 right).
    opacity 0 to 1; dashes: on and off lengths in points, () solid."""
    objs = [objs] if isinstance(objs, Obj) else objs
    given = {k: v for k, v in (("color", color), ("fill", fill), ("width", width), ("fontsize", fontsize),
                               ("fontname", fontname), ("opacity", opacity), ("dashes", dashes)) if v is not None}
    new = []
    for obj in objs:
        text = obj.kind == "text"
        style = {k: v for k, v in given.items()
                 if k in (("color", "fontsize", "fontname", "opacity") if text else
                          ("opacity",) if obj.kind == "image" else ("color", "fill", "width", "opacity", "dashes"))}
        m = pymupdf.Identity
        if text and "fontsize" in style:
            k = style.pop("fontsize") / obj.data["spans"][0]["size"]
            r = pymupdf.Rect(obj.rect)
            x = r.x0 + r.width * anchor
            m = pymupdf.Matrix(k, 0, 0, k, x - x * k, r.y0 - r.y0 * k)
        if text and anchor:
            style["anchor"] = anchor
        new.append((m, style, None))
    _replace(page, objs, new)


def _arrow_items(a, b, heads: tuple[bool, bool], width: float) -> list:
    """A line from a to b with an arrow head at the start and / or the end: two short strokes meeting at the tip."""
    a, b = pymupdf.Point(a), pymupdf.Point(b)
    ops, size = [("m", [a]), ("l", [b])], max(6.0, 4 * (width or 1))
    for tip, other, on in ((a, b, heads[0]), (b, a, heads[1])):
        if on and abs(tip - other) > 0.01:
            u = (tip - other) / abs(tip - other)
            back, side = tip - u * size, pymupdf.Point(-u.y, u.x) * size * 0.45
            ops += [("m", [back + side]), ("l", [tip]), ("l", [back - side])]
    return ops


def _round_items(r: pymupdf.Rect, k: float) -> list:
    """A box with corners rounded by k (0: square), as four sides and four quarter curves."""
    P, x0, y0, x1, y1 = pymupdf.Point, r.x0, r.y0, r.x1, r.y1
    k = max(0.0, min(k, r.width / 2, r.height / 2))
    if k < 0.05:
        return [("m", [P(x0, y0)]), ("l", [P(x1, y0)]), ("l", [P(x1, y1)]), ("l", [P(x0, y1)]), ("h", [])]
    c = k * 0.5523  # the handles of a quarter circle
    return [("m", [P(x0 + k, y0)]), ("l", [P(x1 - k, y0)]), ("c", [P(x1 - k + c, y0), P(x1, y0 + k - c), P(x1, y0 + k)]),
            ("l", [P(x1, y1 - k)]), ("c", [P(x1, y1 - k + c), P(x1 - k + c, y1), P(x1 - k, y1)]),
            ("l", [P(x0 + k, y1)]), ("c", [P(x0 + k - c, y1), P(x0, y1 - k + c), P(x0, y1 - k)]),
            ("l", [P(x0, y0 + k)]), ("c", [P(x0, y0 + k - c), P(x0 + k - c, y0), P(x0 + k, y0)]), ("h", [])]


def _reshape(page, obj: Obj, items: list) -> None:
    if obj.state == "locked":
        raise EditError(obj.reason or tr("Questo oggetto non si può modificare."))
    if "drawing" not in obj.data or not content.reshape(page, {obj.data["drawing"]["seqno"]: items}):
        raise EditError(tr("PDFlex non riesce a cambiare la forma di questo oggetto: si può solo spostare."))


def set_arrows(page, obj: Obj, start: bool, end: bool) -> None:
    """Arrow heads on a line's ends."""
    width = obj.data["drawing"].get("width") or 1
    _reshape(page, obj, _arrow_items(obj.data["p1"], obj.data["p2"], (start, end), width))


def set_round(page, obj: Obj, radius: float) -> None:
    """Round corners for a box (0: square again)."""
    _reshape(page, obj, _round_items(pymupdf.Rect(obj.rect), radius))


_DISTRIBUTE = {"h": (0, 2), "v": (1, 3)}


def distribution(objs: list[Obj], axis: str) -> list[tuple[Obj, float, float]]:
    """How far each of objs moves to leave the same gap between them, across ("h") or down ("v"): the first and
    the last stay. Only those that move."""
    a, b = _DISTRIBUTE[axis]
    order = sorted(objs, key=lambda o: o.rect[a])
    if len(order) < 3:
        return []
    gap = (order[-1].rect[b] - order[0].rect[a] - sum(o.rect[b] - o.rect[a] for o in order)) / (len(order) - 1)
    out, at = [], order[0].rect[b] + gap
    for o in order[1:-1]:
        d = at - o.rect[a]
        if abs(d) > 0.05:
            out.append((o, d, 0) if axis == "h" else (o, 0, d))
        at += o.rect[b] - o.rect[a] + gap
    return out


def distribute(page, objs: list[Obj], axis: str) -> None:
    moves = distribution(objs, axis)
    if moves:
        _replace(page, [o for o, _, _ in moves], [(pymupdf.Matrix(1, 0, 0, 1, dx, dy), {}, None) for _, dx, dy in moves])


def replace_image(page, obj: Obj, data: bytes) -> None:
    """Another picture in an image's place: in its box (in its own proportions, in the middle), turned as it was."""
    if obj.state == "locked":
        raise EditError(obj.reason or tr("Questo oggetto non si può modificare."))
    try:
        pix = pymupdf.Pixmap(data)
    except (RuntimeError, ValueError):
        raise EditError(tr("Questa immagine non si apre.")) from None
    t, alpha = pymupdf.Matrix(obj.data["transform"]), opacity(page, obj)
    w, h = abs(pymupdf.Point(t.a, t.b)), abs(pymupdf.Point(t.c, t.d))  # the box's sides, along the picture's own
    aspect = pix.width / pix.height
    sx, sy = (aspect * h / w, 1) if w / h > aspect else (1, w / (aspect * h))
    _replace(page, [obj], [(None, {}, None)])
    _put_image(page, pymupdf.Matrix(sx, 0, 0, sy, (1 - sx) / 2, (1 - sy) / 2) * t, opacity=alpha, stream=data)


# ---------------------------------------------------------------- new words, new components

def missing_letters(page, obj: Obj, text: str) -> str:
    """The letters of text that PDFlex cannot write with the font of obj ("" when it can write them all or the
    text has no font of its own). Many embedded fonts carry no map of their letters: then it is all of them."""
    font = _own_font(page, obj)
    if font is None:
        return ""
    return "".join(dict.fromkeys(c for c in text if not c.isspace() and not font.has_glyph(ord(c))))


def set_text(page, obj: Obj, text: str, fallback: bool = False, anchor: float = 0) -> None:
    """New words for a text line, in its own font; with fallback, in Helvetica when letters are missing.
    anchor: as for restyle."""
    missing = missing_letters(page, obj, text)
    if missing and not fallback:
        raise EditError(tr("Il carattere di questo testo non ha le lettere: {0}").format(missing))
    style = {"fontname": ""} if missing else {}
    _replace(page, [obj], [(pymupdf.Identity, {**style, "anchor": anchor} if anchor else style, text)])


_ALIGN = {"left": (0, None), "hcenter": (0.5, None), "right": (1, None),
          "top": (None, 0), "vcenter": (None, 0.5), "bottom": (None, 1)}


def alignment(page, objs: list[Obj], how: str) -> list[tuple[Obj, float, float]]:
    """How far each of objs moves to line up on one side or on the middle of the box around them (how: a key of
    _ALIGN); one alone, on the page's. Only those that move."""
    box = pymupdf.Rect(page.rect) if len(objs) == 1 else pymupdf.Rect(objs[0].rect)
    for o in objs[1:]:
        box |= o.rect
    kx, ky = _ALIGN[how]
    out = []
    for o in objs:
        r = pymupdf.Rect(o.rect)
        dx = 0 if kx is None else (box.x0 + box.width * kx) - (r.x0 + r.width * kx)
        dy = 0 if ky is None else (box.y0 + box.height * ky) - (r.y0 + r.height * ky)
        if abs(dx) > 0.05 or abs(dy) > 0.05:
            out.append((o, dx, dy))
    return out


def align(page, objs: list[Obj], how: str) -> None:
    moves = alignment(page, objs, how)
    if moves:
        _replace(page, [o for o, _, _ in moves], [(pymupdf.Matrix(1, 0, 0, 1, dx, dy), {}, None) for _, dx, dy in moves])


# new components go on top of everything (PyMuPDF's default overlay=True)
def add_text(page, point: tuple, text: str, fontsize: float = 11, fontname: str = "", color=(0, 0, 0)) -> None:
    _write(page, point, text, pymupdf.Font(_BASE14.get(fontname, "helv")), fontsize, color)


def add_line(page, p1: tuple, p2: tuple, color=(0, 0, 0), width: float = 1) -> None:
    page.draw_line(p1, p2, color=color, width=width)


def add_rect(page, rect: tuple, color=(0, 0, 0), fill=None, width: float = 1) -> None:
    page.draw_rect(rect, color=color, fill=fill, width=width)


def add_image(page, rect: tuple, data: bytes) -> None:
    page.insert_image(rect, stream=data)
