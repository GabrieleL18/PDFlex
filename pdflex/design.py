"""The Editor's engine: a page's objects (lines, boxes, shapes, pictures, text) and changes made in place.

Only what is touched changes: the object is taken off the page with a redaction limited to its own kind
(lines leave text alone, text leaves lines alone), whatever went with it is drawn back as it was, and the
object is drawn again where it goes. Fields and buttons are annotations: redactions never touch them."""
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from . import content

THIN = 2.0  # pt: a filled box thinner than this is a line
GLYPH = 18.0  # pt: filled shapes up to this size, side by side, are letters drawn as shapes
GLYPH_GAP = 6.0  # pt between two letters of the same word / line
ROTATED = "Pagina ruotata: per modificarla, raddrizzala prima nella sezione Pagine."


def _image_info(page) -> list[dict]:
    # ponytail: PyMuPDF keeps the first answer on the page object (page._image_info): after a change it would
    # still list the pictures as they were. Drop the copy each time; remove when PyMuPDF refreshes it itself.
    page._image_info = None
    # an image clipped away entirely shows nothing (a moved line of text may carry one, out of sight)
    return [i for i in page.get_image_info(xrefs=True) if not pymupdf.Rect(i["bbox"]).is_empty]


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


def _shape(d) -> Obj:
    items, r = d["items"], pymupdf.Rect(d["rect"])
    data = {"drawing": d}
    if len(items) == 1 and items[0][0] == "l":
        return Obj("line", tuple(r), data={**data, "p1": tuple(items[0][1]), "p2": tuple(items[0][2])})
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
        objs.append(Obj("shape", tuple(r), "move", "Testo disegnato come forme: si sposta, non si modifica",
                        {"drawings": group}))
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
            o.state, o.reason = "locked", "Ritagliato: resta com'è"
            objs.append(o)
            locked += 1
        elif _key(d) in stuck and d["seqno"] not in own:
            o = _shape(_full(d))
            o.state, o.reason = "locked", "PDFlex non riesce a staccarlo dalla pagina: resta com'è"
            objs.append(o)
            locked += 1
        elif _glyph(d):
            glyphs.append(d)  # as they are: a letter is only moved, its points stay plain numbers
        else:
            objs.append(_shape(_full(d)))
    objs += _group_glyphs(glyphs)
    for info in _image_info(page):
        o = Obj("image", tuple(pymupdf.Rect(info["bbox"])), data={"xref": info["xref"], "transform": info["transform"]})
        if not info["xref"]:
            o.state, o.reason = "locked", "Immagine incorporata nel testo della pagina: resta com'è"
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
            o = Obj("text", tuple(r), data={"spans": spans, "font_xref": fonts.get(spans[0]["font"])})
            if abs(line["dir"][1]) > 0.01:
                o.state, o.reason = "locked", "Testo ruotato: resta com'è"
                locked += 1
            objs.append(o)
    warnings = []
    if glyphs:
        warnings.append("Testo disegnato come forme: si sposta, ma le parole non si possono cambiare "
                        f"({len(glyphs)} forme).")
    if locked:
        warnings.append(f"{locked} oggetti restano come sono (sfumature, ritagli, testo ruotato o forme che "
                        "PDFlex non riesce a staccare dalla pagina).")
    if page.rotation:
        for o in objs:
            o.state, o.reason = "locked", ROTATED
        warnings = [ROTATED]
    return objs, warnings


# ---------------------------------------------------------------- changes made in place

_BASE14 = {"": "helv", "TiRo": "tiro", "Cour": "cour"}  # fields.FONTS keys -> PyMuPDF's built-in fonts


def _drawings(o: Obj) -> list[dict]:
    return o.data.get("drawings") or ([o.data["drawing"]] if "drawing" in o.data else [])


def _area(o: Obj) -> pymupdf.Rect:
    r = pymupdf.Rect(o.rect)
    if o.kind == "text":  # the middle of the line: its characters, not the neighbours above and below
        h = r.height
        return pymupdf.Rect(r.x0 + 0.5, r.y0 + h * 0.25, r.x1 - 0.5, r.y1 - h * 0.25)
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
        raise EditError("Questa forma è intrecciata con un'altra grande forma della pagina: PDFlex non riesce a "
                        "modificarla da sola, e resta com'è.")
    raise EditError("PDFlex non riesce a staccare questa forma dalla pagina: resta com'è.")


def _mapping(old: pymupdf.Rect, new: pymupdf.Rect) -> pymupdf.Matrix:
    """The matrix that takes rectangle old onto rectangle new (a flat side keeps its size)."""
    sx = new.width / old.width if old.width > 0.01 else 1
    sy = new.height / old.height if old.height > 0.01 else 1
    return pymupdf.Matrix(sx, 0, 0, sy, new.x0 - old.x0 * sx, new.y0 - old.y0 * sy)


def _draw_items(shape, items, m: pymupdf.Matrix) -> None:
    P = pymupdf.Point  # the raw form gives plain tuples
    for it in items:
        if it[0] == "l":
            shape.draw_line(P(it[1]) * m, P(it[2]) * m)
        elif it[0] == "re":
            shape.draw_rect(pymupdf.Rect(it[1]) * m)
        elif it[0] == "qu":
            shape.draw_quad(pymupdf.Quad(it[1]) * m)
        elif it[0] == "c":
            shape.draw_bezier(P(it[1]) * m, P(it[2]) * m, P(it[3]) * m, P(it[4]) * m)
        else:
            raise EditError("Una forma contiene un tratto che PDFlex non sa ridisegnare.")


def _finish(shape, d: dict, style: dict) -> None:
    cap = d.get("lineCap") or 0
    shape.finish(color=style.get("color", d.get("color")), fill=style.get("fill", d.get("fill")),
                 width=style.get("width", d.get("width")) or 0, dashes=d.get("dashes") or None,
                 lineCap=max(cap) if isinstance(cap, (tuple, list)) else cap, lineJoin=int(d.get("lineJoin") or 0),
                 closePath=bool(d.get("closePath")), even_odd=bool(d.get("even_odd")),
                 fill_opacity=1 if d.get("fill_opacity") is None else d["fill_opacity"],
                 stroke_opacity=1 if d.get("stroke_opacity") is None else d["stroke_opacity"])


def _own_font(page, o: Obj) -> pymupdf.Font | None:
    xref = o.data.get("font_xref")
    buf = page.parent.extract_font(xref)[3] if xref else b""
    return pymupdf.Font(fontbuffer=buf) if buf else None


def _text_font(page, o: Obj, span: dict, override: str | None, text: str) -> pymupdf.Font:
    """The font new words are written with: the text's own one when it can write them, else the closest built-in."""
    if override is not None:
        return pymupdf.Font(_BASE14.get(override, "helv"))
    font = _own_font(page, o)
    if font is not None:
        missing = missing_letters(page, o, text)
        if missing:
            raise EditError(f"PDFlex non riesce a scrivere con il carattere di questo testo le lettere: {missing}")
        return font
    name = span["font"].lower()
    base = "tiro" if "times" in name else "cour" if "courier" in name else "helv"
    return pymupdf.Font({"helv": "hebo", "tiro": "tibo", "cour": "cobo"}[base] if "bold" in name else base)


def _write(page, origin, text: str, font: pymupdf.Font, fontsize: float, color) -> None:
    # TextWriter, not insert_text: insert_text writes the built-in fonts in Latin-1 and loses "€"
    tw = pymupdf.TextWriter(page.rect)
    for i, line in enumerate(text.split("\n")):
        tw.append(pymupdf.Point(origin) + (0, i * fontsize * 1.2), line, font=font, fontsize=fontsize)
    tw.write_text(page, color=color)


def _text_args(page, o: Obj, m: pymupdf.Matrix, style: dict, text: str | None) -> tuple:
    """What _write needs to write the line again (moved by m) with new words or a new colour or font: its first
    span gives place, size and look. Worked out before the page is touched, so a refusal leaves it as it was."""
    s = o.data["spans"][0]
    words = "".join(sp["text"] for sp in o.data["spans"]) if text is None else text
    try:
        font = _text_font(page, o, s, style.get("fontname"), words)
    except EditError:
        if text is None and "fontname" not in style:  # only moving it
            raise EditError("Questo testo è intrecciato con un disegno e il suo carattere non si può riscrivere: "
                            "PDFlex non riesce a spostarlo.") from None
        raise
    return (pymupdf.Point(s["origin"]) * m, words, font, style.get("fontsize") or s["size"] * abs(m.d),
            style.get("color") or pymupdf.sRGB_to_pdf(s["color"]))


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
    return content.edit(page, {d["seqno"]: (m, style) for o, m, style in shapes for d in _drawings(o)}, before)


def _replace(page, objs: list[Obj], new: list[tuple]) -> None:
    """Take objs off the page and draw each again through its matrix (None: deleted), with its style overrides
    and, for text, new words: new[i] = (matrix, style, text). What the redactions took with them is drawn back
    as it was. Afterwards the page is checked: EditError if anything else went missing."""
    for o in objs:
        if o.state == "locked":
            raise EditError(o.reason or "Questo oggetto non si può modificare.")
    before = page.get_cdrawings()
    targets = Counter(_key(d) for o in objs for d in _drawings(o))
    shapes = [(o, m, style) for o, (m, style, _) in zip(objs, new) if o.kind not in ("text", "image")]
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
              if o.kind == "text" and m is not None and text is None and not {"color", "fontname"} & style.keys()]
    src, copied = _text_source(page, copied) if copied else (None, [])  # the others are written again
    rewritten = {id(o): _text_args(page, o, m, style, text) for o, (m, style, text) in zip(objs, new)
                 if o.kind == "text" and m is not None and not any(o is c for c in copied)}
    images_before = len(_image_info(page))
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
        if m is not None and not (in_place and o.kind not in ("text", "image")):
            redraw += [(d, m, style) for d in _drawings(o)]
    redraw.sort(key=lambda t: t[0]["seqno"])
    if redraw:
        shape = page.new_shape()
        for d, m, style in redraw:
            _draw_items(shape, d["items"], m)
            _finish(shape, d, style)
        shape.commit(overlay=False)
    for o, (m, style, text) in zip(objs, new):
        if any(o is c for c in copied):
            page.show_pdf_page(_clip(o) * m, src, 0, clip=_clip(o), keep_proportion=False)
        elif id(o) in rewritten:
            _write(page, *rewritten[id(o)])
        elif m is not None and o.kind == "image":
            page.insert_image(pymupdf.Rect(o.rect) * m, xref=o.data["xref"], keep_proportion=False)
    gone = sum(len(_drawings(o)) for o, (m, _, _) in zip(objs, new) if m is None)
    if len(page.get_cdrawings()) != len(before) - gone:
        raise EditError("Con questa modifica sarebbe cambiata anche un'altra forma della pagina.")
    gone = sum(1 for o, (m, _, _) in zip(objs, new) if o.kind == "image" and m is None)
    if len(_image_info(page)) != images_before - gone:
        raise EditError("Con questa modifica sarebbe sparita un'altra immagine che tocca questa.")
    after = [w[0] for w in _words(page)]
    for word in words_kept:
        if word not in after:
            raise EditError(f'Con questa modifica sarebbe sparita anche la parola "{word}" lì vicino.')
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
        raise EditError("Questa linea si può allungare solo nella sua direzione.")
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
    if obj.kind == "line":
        m = _line_matrix(obj, rect[:2], rect[2:])
    else:
        m = _mapping(pymupdf.Rect(obj.rect), pymupdf.Rect(rect))
    _replace(page, [obj], [(m, {}, None)])


def delete(page, objs: list[Obj]) -> None:
    _replace(page, objs, [(None, {}, None) for _ in objs])


def restyle(page, obj: Obj, *, color=None, fill=None, width=None, fontsize=None, fontname=None) -> None:
    style = {k: v for k, v in (("color", color), ("fill", fill), ("width", width), ("fontsize", fontsize),
                               ("fontname", fontname)) if v is not None}
    m = pymupdf.Identity
    if obj.kind == "text" and "fontsize" in style:
        k = style.pop("fontsize") / obj.data["spans"][0]["size"]
        r = pymupdf.Rect(obj.rect)
        m = pymupdf.Matrix(k, 0, 0, k, r.x0 - r.x0 * k, r.y0 - r.y0 * k)
    _replace(page, [obj], [(m, style, None)])


# ---------------------------------------------------------------- new words, new components

def missing_letters(page, obj: Obj, text: str) -> str:
    """The letters of text that PDFlex cannot write with the font of obj ("" when it can write them all or the
    text has no font of its own). Many embedded fonts carry no map of their letters: then it is all of them."""
    font = _own_font(page, obj)
    if font is None:
        return ""
    return "".join(dict.fromkeys(c for c in text if not c.isspace() and not font.has_glyph(ord(c))))


def set_text(page, obj: Obj, text: str, fallback: bool = False) -> None:
    """New words for a text line, in its own font; with fallback, in Helvetica when letters are missing."""
    missing = missing_letters(page, obj, text)
    if missing and not fallback:
        raise EditError(f"Il carattere di questo testo non ha le lettere: {missing}")
    _replace(page, [obj], [(pymupdf.Identity, {"fontname": ""} if missing else {}, text)])


# new components go on top of everything (PyMuPDF's default overlay=True)
def add_text(page, point: tuple, text: str, fontsize: float = 11, fontname: str = "", color=(0, 0, 0)) -> None:
    _write(page, point, text, pymupdf.Font(_BASE14.get(fontname, "helv")), fontsize, color)


def add_line(page, p1: tuple, p2: tuple, color=(0, 0, 0), width: float = 1) -> None:
    page.draw_line(p1, p2, color=color, width=width)


def add_rect(page, rect: tuple, color=(0, 0, 0), fill=None, width: float = 1) -> None:
    page.draw_rect(rect, color=color, fill=fill, width=width)


def add_image(page, rect: tuple, data: bytes) -> None:
    page.insert_image(rect, stream=data)
