import html
import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from pdflex import open_pdf

RED = (1, 0, 0)
GREEN = (0, 0.7, 0)
BLUE = (0.18, 0.44, 0.86)  # moved text
PURPLE = (0.48, 0.31, 0.82)  # same words, different formatting
OPACITY = 0.3
SIDE_COLORS = {"delete": (RED, None), "insert": (None, GREEN), "replace": (RED, GREEN), "move": (BLUE, BLUE),
               "format": (PURPLE, PURPLE), "image": (RED, GREEN)}  # (original, new version)
KIND_TITLES = {"delete": "Rimosso", "insert": "Aggiunto", "replace": "Modificato", "move": "Spostato",
               "format": "Formattazione", "image": "Immagine"}
IMAGE = "[immagine]"  # the "text" of an image in lists and exports
FONT = "C:/Windows/Fonts/arial.ttf"


@dataclass
class Word:
    page: int
    rect: tuple[float, float, float, float]
    text: str
    style: tuple = field(default=(), compare=False)  # (bold, italic, size) when known


@dataclass
class Change:
    kind: str  # "delete" | "insert" | "replace" | "move" | "format"
    old: list[Word]
    new: list[Word]
    old_at: Word | None
    new_at: Word | None
    whole: list[int] = field(default_factory=list)  # pages added / removed entirely (insert / delete)

    def pages_text(self) -> str:
        """ "Pagina 5 aggiunta", "Pagine 5, 6 rimosse": a whole page in or out, said as such."""
        n = [str(p + 1) for p in self.whole]
        verb = "aggiunt" if self.kind == "insert" else "rimoss"
        return f"Pagina {n[0]} {verb}a" if len(n) == 1 else f"Pagine {', '.join(n)} {verb}e"

    def image_text(self) -> str:
        return "Immagine cambiata" if self.old and self.new else "Immagine aggiunta" if self.new else "Immagine rimossa"

    def label(self) -> str:
        def short(words):
            s = " ".join(w.text for w in words)
            return s if len(s) <= 60 else s[:59] + "…"
        if self.kind == "image":
            return f"{self.image_text()} (p. {(self.new or self.old)[0].page + 1})"
        if self.whole:
            return f"{self.pages_text()} per intero: {short(self.old or self.new)}"
        if self.kind == "delete":
            return f"Rimosso (p. {self.old[0].page + 1}): {short(self.old)}"
        if self.kind == "insert":
            return f"Aggiunto (p. {self.new[0].page + 1}): {short(self.new)}"
        if self.kind == "move":
            return f"Spostato (p. {self.old[0].page + 1} → p. {self.new[0].page + 1}): {short(self.old)}"
        if self.kind == "format":
            return f"Formattazione (p. {self.new[0].page + 1}): {short(self.new)} — {_style_change(self)}"
        return f"Modificato (p. {self.old[0].page + 1}): {short(self.old)} → {short(self.new)}"


def _style_change(c: Change) -> str:
    a, b = c.old[0].style, c.new[0].style
    parts = []
    if a[0] != b[0]:
        parts.append("grassetto" if b[0] else "tolto il grassetto")
    if a[1] != b[1]:
        parts.append("corsivo" if b[1] else "tolto il corsivo")
    if a[2] != b[2]:
        parts.append(f"dimensione {a[2]:g} → {b[2]:g}")
    return ", ".join(parts)


def _styles(page, words) -> list[tuple]:
    """(bold, italic, size) of each word, from the text span under its centre."""
    spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
    rows = {}
    for s in spans:
        rows.setdefault(round((s["bbox"][1] + s["bbox"][3]) / 8), []).append(s)  # bucket by line height
    out = []
    for w in words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        near = rows.get(round(cy / 4), []) + rows.get(round(cy / 4) - 1, []) + rows.get(round(cy / 4) + 1, [])
        s = next((s for s in near if s["bbox"][0] - 1 <= cx <= s["bbox"][2] + 1 and s["bbox"][1] - 1 <= cy <= s["bbox"][3] + 1),
                 None)
        if s is None:
            out.append(())
            continue
        font = s["font"].lower()
        out.append((bool(s["flags"] & 16) or "bold" in font or "-bd" in font,
                    bool(s["flags"] & 2) or "italic" in font or "oblique" in font, round(s["size"], 1)))
    return out


def ocr_available() -> bool:
    """OCR needs Tesseract installed on the PC (with Italian language data)."""
    try:
        return bool(pymupdf.get_tessdata())
    except Exception:
        return False


def extract_words(doc: pymupdf.Document, ocr: bool = False, on_page=None) -> list[Word]:
    out = []
    for page in doc:
        if on_page:
            on_page(page.number)
        words = page.get_text("words")
        styles = None
        if not words and ocr and ocr_available():  # a scanned page: read the image (no formatting to read)
            tp = page.get_textpage_ocr(language="ita+eng", dpi=300, full=True)
            words = page.get_text("words", textpage=tp)
            styles = [()] * len(words)
        styles = styles or _styles(page, words)
        # get_text gives unrotated coordinates; turn them into what the reader sees
        out += [Word(page.number, tuple(pymupdf.Rect(w[:4]) * page.rotation_matrix), w[4], st)
                for w, st in zip(words, styles)]
    return out


def extract_images(doc: pymupdf.Document, on_page=None) -> list[tuple[Word, bytes]]:
    """(where, fingerprint) of each picture, in reading order. Two pictures with the same fingerprint are
    the same picture. A picture covering most of the page is a scan: its text is compared, not its pixels."""
    out = []
    for page in doc:
        if on_page:
            on_page(page.number)
        area = page.rect.width * page.rect.height
        found = []
        for info in page.get_image_info(hashes=True):
            r = pymupdf.Rect(info["bbox"]) * page.rotation_matrix
            if r.width < 8 or r.height < 8 or r.width * r.height > 0.8 * area:
                continue  # specks and lines, or a scanned page
            found.append((Word(page.number, tuple(r), IMAGE), info["digest"]))
        out += sorted(found, key=lambda wd: (round(wd[0].rect[1]), wd[0].rect[0]))
    return out


def diff_images(old: list[tuple[Word, bytes]], new: list[tuple[Word, bytes]]) -> list[Change]:
    """Pictures added, removed or replaced by another one; the same picture elsewhere is no change."""
    sm = SequenceMatcher(None, [d for _, d in old], [d for _, d in new], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            a = [old[i1 + k][0]] if i1 + k < i2 else []
            b = [new[j1 + k][0]] if j1 + k < j2 else []
            out.append(Change("image", a, b, a[0] if a else None, b[0] if b else None))
    return out


def merge_changes(text: list[Change], images: list[Change]) -> list[Change]:
    """Picture changes slotted among the text ones by page and height, text order untouched."""
    def at(c):
        w = (c.new or c.old or [c.new_at or c.old_at])[0]
        return (w.page, w.rect[1]) if w else (0, 0)
    out, pending = [], sorted(images, key=at)
    for c in text:
        while pending and at(pending[0]) < at(c):
            out.append(pending.pop(0))
        out.append(c)
    return out + pending


def diff_words(old: list[Word], new: list[Word], ignore_case: bool = False,
               ignore_punct: bool = False, formatting: bool = False) -> list[Change]:
    """Changes in reading order. A piece removed in one place and added unchanged elsewhere is one "move";
    with formatting=True, same words in bold/italic/another size are a "format" change."""
    def key(w):
        t = w.text.lower() if ignore_case else w.text
        return re.sub(r"[^\w]", "", t) if ignore_punct else t
    if ignore_punct:  # a lone "-" or "," is not a word worth reporting
        old = [w for w in old if key(w)]
        new = [w for w in new if key(w)]
    a = [key(w) for w in old]
    b = [key(w) for w in new]
    # Trim the common head/tail first: typical revisions touch a few spots, and
    # SequenceMatcher is roughly quadratic on what remains.
    # A 100+ page contract rewritten everywhere can still take seconds: the tab runs this in a thread (ui.run_busy).
    lo = 0
    while lo < min(len(a), len(b)) and a[lo] == b[lo]:
        lo += 1
    hi = 0
    while hi < min(len(a), len(b)) - lo and a[-1 - hi] == b[-1 - hi]:
        hi += 1
    sm = SequenceMatcher(None, a[lo:len(a) - hi], b[lo:len(b) - hi], autojunk=False)
    keyed = []  # (position in the original, change)
    same = [(k, k) for k in range(lo)]  # pairs of identical words, to look at their formatting
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        i1, i2, j1, j2 = i1 + lo, i2 + lo, j1 + lo, j2 + lo
        if tag == "equal":
            same += list(zip(range(i1, i2), range(j1, j2)))
            continue
        old_at = old[min(i1, len(old) - 1)] if old else None
        new_at = new[min(j1, len(new) - 1)] if new else None
        keyed.append((i1, Change(tag, old[i1:i2], new[j1:j2], old_at, new_at)))
    same += [(len(a) - hi + k, len(b) - hi + k) for k in range(hi)]
    keyed = _find_moves(keyed, key)
    if formatting:
        run = []
        for i, j in same + [(None, None)]:
            differs = i is not None and old[i].style and new[j].style and old[i].style != new[j].style
            if differs and (not run or (i == run[-1][0] + 1 and old[i].style == old[run[0][0]].style
                                        and new[j].style == new[run[0][1]].style)):
                run.append((i, j))
                continue
            if run:
                keyed.append((run[0][0], Change("format", [old[i] for i, _ in run], [new[j] for _, j in run],
                                                old[run[0][0]], new[run[0][1]])))
            run = [(i, j)] if differs else []
    changes = [c for _, c in sorted(keyed, key=lambda kc: kc[0])]
    pages = ({}, {})  # side -> page -> its words' keys
    for side, words in enumerate((old, new)):
        for w in words:
            pages[side].setdefault(w.page, []).append(key(w))
    for c in changes:
        if c.kind in ("insert", "delete"):
            words, side = (c.new, 1) if c.kind == "insert" else (c.old, 0)
            run = [key(w) for w in words]
            c.whole = [pg for pg in sorted({w.page for w in words}) if _covers(run, pages[side][pg])]
    return changes


def _covers(run: list[str], page: list[str]) -> bool:
    """Every word of the page is in the run. The diff may have slid the run a few words over a repeated
    one: a page starting with "Articolo" removed shows up as "3. ... mesi. Articolo" (the next page's)."""
    text = " " + " ".join(run) + " "
    for k in range(min(len(page), 20)):
        for seq in (page[k:] + page[:k], page[len(page) - k:] + page[:len(page) - k]):
            if " " + " ".join(seq) + " " in text:
                return True
    return False


def _find_moves(keyed, key):
    """Pair a removal with an addition of the very same text (2+ words, 8+ letters) into one move."""
    inserts = {}
    for n, (_, c) in enumerate(keyed):
        if c.kind == "insert":
            inserts.setdefault(" ".join(key(w) for w in c.new), []).append(n)
    pairs = {}  # removal index -> addition index
    for n, (_, c) in enumerate(keyed):
        text = " ".join(key(w) for w in c.old) if c.kind == "delete" else ""
        if len(c.old) >= 2 and len(text) >= 8:
            match = next((m for m in inserts.get(text, []) if m not in pairs.values()), None)
            if match is not None:
                pairs[n] = match
    out = []
    for n, (pos, c) in enumerate(keyed):
        if n in pairs:
            ins = keyed[pairs[n]][1]
            out.append((pos, Change("move", c.old, ins.new, c.old_at, ins.new_at)))
        elif n not in pairs.values():
            out.append((pos, c))
    return out


TEXT_RED, TEXT_GREEN = "#B42318", "#1E7B45"


def change_html(c: Change, color: str, red: str = TEXT_RED, green: str = TEXT_GREEN, muted: str = "#5B6675") -> str:
    """One row of the list: what kind of change and where, then the words themselves, coloured."""
    muted = f"color:{muted};"
    if c.kind == "image":
        w = (c.new or c.old)[0]
        size = f"{w.rect[2] - w.rect[0]:.0f} × {w.rect[3] - w.rect[1]:.0f} pt"
        return (f'<span style="color:{color}; font-weight:700;">●&nbsp;{c.image_text()}</span>'
                f'<span style="{muted}"> &nbsp;pagina {w.page + 1} · {size}</span>')

    def words(ws, style):
        t = " ".join(w.text for w in ws)
        t = t if len(t) <= 160 else t[:159] + "…"
        return f'<span style="{style}">{html.escape(t)}</span>'
    old = words(c.old, f"color:{red}; text-decoration:line-through;")
    new = words(c.new, f"color:{green}; font-weight:600;")
    page = (c.old or c.new)[0].page + 1
    if c.kind == "replace":
        body = f"{old} &nbsp;→&nbsp; {new}"
    elif c.kind == "delete":
        body = old
    elif c.kind == "insert":
        body = new
    elif c.kind == "move":
        page = c.new[0].page + 1
        body = (words(c.new, f"color:{color}; font-weight:600;") +
                f' <span style="{muted}">da pagina {c.old[0].page + 1}</span>')
    else:
        body = words(c.new, "font-weight:600;") + f' <span style="{muted}">→ {html.escape(_style_change(c))}</span>'
    if c.whole:  # a page in or out as a whole: say it first, the words are just a reminder of which
        return (f'<span style="color:{color}; font-weight:700;">●&nbsp;{c.pages_text()} per intero</span><br>'
                f'<span style="{muted}">{html.escape(" ".join(w.text for w in (c.new or c.old))[:90])}…</span>')
    return (f'<span style="color:{color}; font-weight:700;">●&nbsp;{KIND_TITLES[c.kind]}</span>'
            f'<span style="{muted}"> &nbsp;pagina {page}</span><br>{body}')


def _summary_pages(out, old_path, new_path, changes):
    font = {"fontname": "arial", "fontfile": FONT} if os.path.exists(FONT) else {"fontname": "helv"}
    lines = [
        ("Confronto PDF", 16),
        (f"Originale: {Path(old_path).name}", 10),
        (f"Nuova versione: {Path(new_path).name}", 10),
        (f"Data: {datetime.now():%d/%m/%Y %H:%M}", 10),
        (f"Modifiche: {len(changes)}", 10),
        ("", 10),
    ]
    for c in changes:
        lines += [(t, 10) for t in textwrap.wrap(c.label(), 95)]
    page, y = None, 0
    for text, size in lines:
        if page is None or y > 842 - 50:
            page, y = out.new_page(width=595, height=842), 60
        if text:
            page.insert_text((50, y), text, fontsize=size, **font)
        y += size + 6


def export_report(old_path, new_path, changes: list[Change], out_path, summary: bool = True) -> None:
    old, new = open_pdf(old_path), open_pdf(new_path)
    # show_pdf_page draws rotated pages wrong: derotate the source and rotate while placing
    ro, rn = [(p.rect, p.rotation) for p in old], [(p.rect, p.rotation) for p in new]
    for d in (old, new):
        for p in d:
            p.set_rotation(0)
    out = pymupdf.open()
    if summary:
        _summary_pages(out, old_path, new_path, changes)
    for i in range(max(old.page_count, new.page_count)):
        r_old = ro[min(i, len(ro) - 1)][0]  # a missing page leaves a blank of the last page's size
        r_new = rn[min(i, len(rn) - 1)][0]
        page = out.new_page(width=r_old.width + r_new.width, height=max(r_old.height, r_new.height))
        if i < old.page_count:
            page.show_pdf_page(pymupdf.Rect(0, 0, r_old.width, r_old.height), old, i, rotate=-ro[i][1])
        if i < new.page_count:
            page.show_pdf_page(pymupdf.Rect(r_old.width, 0, r_old.width + r_new.width, r_new.height), new, i,
                               rotate=-rn[i][1])
        for c in changes:
            left, right = SIDE_COLORS[c.kind]
            for w in c.old:
                if w.page == i:
                    page.draw_rect(pymupdf.Rect(w.rect), color=None, fill=left, fill_opacity=OPACITY)
            for w in c.new:
                if w.page == i:
                    page.draw_rect(pymupdf.Rect(w.rect) + (r_old.width, 0, r_old.width, 0),
                                   color=None, fill=right, fill_opacity=OPACITY)
    out.save(out_path, garbage=3, deflate=True)


def export_changes_xlsx(changes: list[Change], path, old_name: str, new_name: str) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Modifiche"
    ws.append(["N.", "Tipo", "Pagina originale", "Pagina nuova", f"Testo in {old_name}", f"Testo in {new_name}"])
    names = KIND_TITLES
    fills = {"delete": "FBE3E3", "insert": "E2F4E8", "replace": "FFF3D6", "move": "E1EAFB", "format": "ECE5F8",
             "image": "FBE1EE"}
    for i, c in enumerate(changes, 1):
        ws.append([i, names[c.kind],
                   c.old[0].page + 1 if c.old else (c.old_at.page + 1 if c.old_at else None),
                   c.new[0].page + 1 if c.new else (c.new_at.page + 1 if c.new_at else None),
                   " ".join(w.text for w in c.old), " ".join(w.text for w in c.new)])
        ws.cell(ws.max_row, 2).fill = PatternFill("solid", fgColor=fills[c.kind])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEF", (6, 12, 16, 14, 60, 60)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)


# ---------------------------------------------------------------- UI

from PySide6.QtCore import QRectF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
    QSizePolicy, QSplitter, QStackedWidget, QToolTip, QVBoxLayout, QWidget,
)

from pdflex import ui  # noqa: E402
from pdflex.ui import DocView, button, info, warn  # noqa: E402

KIND_NAMES = KIND_TITLES
KIND_COLORS = {"delete": ui.RED, "insert": ui.GREEN, "replace": ui.AMBER, "move": "#2F6FDB", "format": "#7A4FD1",
               "image": "#C2357A"}
HINT = "Trascina qui il PDF o clicca per sceglierlo"
NO_OCR = ("{} non contiene testo: è una scansione. Per leggerla serve l'OCR: installa Tesseract "
          "(github.com/UB-Mannheim/tesseract/wiki) spuntando la lingua italiana, poi riapri PDFlex.")


class ChangeBar(ui.MarkBar):
    """The one scrollbar between the two documents: a tick for every change, a click on a tick selects it."""
    picked = Signal(int)  # index of the change

    def __init__(self):
        super().__init__()
        self.ticks: list[tuple[float, str, int, str]] = []  # (0..1 down the document, colour, change, label)
        self.current = -1
        self.setObjectName("Changes")
        self.setMouseTracking(True)
        self.setFixedWidth(22)

    def _y(self, frac: float) -> float:
        return 3 + frac * (self.height() - 6)

    def tick_at(self, y: float):
        """The change whose tick is under y (4 px either way), or None."""
        near = min(self.ticks, key=lambda t: abs(self._y(t[0]) - y), default=None)
        return near if near and abs(self._y(near[0]) - y) <= 4 else None

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        for frac, color, i, _ in self.ticks:
            h = 5 if i == self.current else 3
            p.fillRect(QRectF(2, self._y(frac) - h / 2, self.width() - 4, h), QColor(color))
        cur = next((t for t in self.ticks if t[2] == self.current), None)
        if cur:  # the change shown now: ringed
            p.setPen(QPen(QColor(ui.color("accent")), 1.5))
            p.drawRect(QRectF(0.75, self._y(cur[0]) - 4, self.width() - 1.5, 8))

    def mousePressEvent(self, e):
        tick = self.tick_at(e.position().y()) if e.button() == Qt.LeftButton else None
        if tick:
            self.picked.emit(tick[2])
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        tick = None if e.buttons() else self.tick_at(e.position().y())
        if tick:
            QToolTip.showText(e.globalPosition().toPoint(), tick[3], self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(e)


class CompareTab(QWidget):
    def __init__(self):
        super().__init__()
        self.old_path = self.new_path = None
        self.changes: list[Change] = []
        self.current = -1
        self._syncing = False
        self.setAcceptDrops(True)

        bar = ui.Bar()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_new = button("Nuovo confronto", "refresh")
        self.b_new.clicked.connect(self.reset)
        self.summary = QLabel("Scegli i due PDF da confrontare", objectName="Muted")
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # long counts must not widen the window
        self.sync = ui.Toggle("Scorri insieme")
        self.sync.setChecked(True)
        self.sync.setToolTip("Scorrendo un documento, l'altro segue alla stessa pagina")
        self.b_zout = button("", "zoom-out", "Ghost", "Rimpicciolisci (Ctrl+rotellina)")
        self.b_zin = button("", "zoom-in", "Ghost", "Ingrandisci (Ctrl+rotellina)")
        self.zoom_label = button("100%", None, "Ghost", "Adatta alla larghezza")
        self.b_page = button("", "page", "Ghost", "Pagina intera: una pagina alla volta sullo schermo")
        self.b_full = button("", "expand", "Ghost", "Schermo intero (F11, Esc per uscire)")
        self.b_zout.clicked.connect(lambda: self.zoom_by(1 / 1.25))
        self.b_zin.clicked.connect(lambda: self.zoom_by(1.25))
        self.zoom_label.clicked.connect(self.zoom_fit)
        self.b_page.clicked.connect(self.zoom_page)
        self.b_full.clicked.connect(lambda: ui.toggle_full_screen(self))
        self.b_prev = button("Precedente", "prev", tip="Modifica precedente")
        self.b_next = button("Successiva", "next", tip="Modifica successiva")
        self.b_export = button("Esporta PDF", "export", "Primary")
        self.b_xlsx = button("Esporta Excel", "table", tip="Elenco delle modifiche in un foglio Excel")
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next.clicked.connect(lambda: self.step(1))
        self.b_export.clicked.connect(self.ask_export)
        self.b_xlsx.clicked.connect(self.ask_export_xlsx)
        bl.addWidget(self.b_new)
        bl.addSpacing(10)
        bl.addWidget(self.summary, 1)
        for w in (self.b_zout, self.zoom_label, self.b_zin, self.b_page, self.b_full):
            bl.addWidget(w)
        bl.addSpacing(10)
        bl.addWidget(self.sync)
        bl.addSpacing(10)
        for w in (self.b_prev, self.b_next, self.b_xlsx, self.b_export):
            bl.addWidget(w)

        # empty state: two drop zones
        self.drop_old = ui.DropZone("Originale", glyph="form")
        self.drop_new = ui.DropZone("Nuova versione", glyph="form")
        self.drop_old.picked.connect(lambda p: self._pick("old", p))
        self.drop_new.picked.connect(lambda p: self._pick("new", p))
        empty = QWidget()
        empty.setObjectName("Desk")
        el = QVBoxLayout(empty)
        el.setContentsMargins(60, 40, 60, 40)
        head = QLabel("Confronta due versioni di un documento", objectName="H1", alignment=Qt.AlignCenter)
        sub = QLabel("Le parole tolte appaiono in rosso sull'originale, quelle aggiunte in verde sulla nuova versione.",
                     objectName="Muted", alignment=Qt.AlignCenter)
        sub.setWordWrap(True)
        zones = QHBoxLayout()
        zones.setSpacing(24)
        zones.addWidget(self.drop_old)
        zones.addWidget(self.drop_new)
        el.addStretch()
        el.addWidget(head)
        el.addWidget(sub)
        el.addSpacing(20)
        el.addLayout(zones)
        el.addStretch(2)

        # result
        self.old_view, self.new_view = DocView(), DocView()
        self.old_view.verticalScrollBar().valueChanged.connect(lambda: self._follow(self.old_view, self.new_view))
        self.new_view.verticalScrollBar().valueChanged.connect(lambda: self._follow(self.new_view, self.old_view))
        self.old_view.horizontalScrollBar().valueChanged.connect(lambda: self._follow_x(self.old_view, self.new_view))
        self.new_view.horizontalScrollBar().valueChanged.connect(lambda: self._follow_x(self.new_view, self.old_view))
        self.old_view.scaled.connect(self._zoom_to)
        self.new_view.scaled.connect(self._zoom_to)
        # the changes' bar in the middle (a click on a tick selects it); each document keeps its own scrollbar too
        self.bar = ChangeBar()
        own = self.old_view.verticalScrollBar()
        own.rangeChanged.connect(self._bar_range)
        own.valueChanged.connect(self._bar_follow)
        self.bar.valueChanged.connect(own.setValue)
        self.bar.picked.connect(self.go_to)
        self.old_label, self.new_label = QLabel(), QLabel()
        self.list = QListWidget(objectName="Changes")
        self.list.setItemDelegate(ui.HtmlDelegate(self.list))
        self.list.setResizeMode(QListView.Adjust)  # rows re-wrap when the panel is resized
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._row_changed)

        def column(label, view, color):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            label.setObjectName("Column")
            label.setStyleSheet(f"border-bottom: 3px solid {color};")
            v.addWidget(label)
            v.addWidget(view, 1)  # the view takes the height, not the label (the bar's one is empty)
            return w

        side = QFrame(objectName="Panel")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 12, 0, 0)
        title = QLabel("Modifiche", objectName="H2")
        title.setContentsMargins(14, 0, 14, 4)
        sl.addWidget(title)
        opts = QVBoxLayout()
        opts.setContentsMargins(14, 0, 14, 8)
        self.kind_filter = QComboBox()
        for label, kind in (("Tutte le modifiche", ""), ("Solo aggiunte", "insert"), ("Solo rimozioni", "delete"),
                            ("Solo sostituzioni", "replace"), ("Solo testo spostato", "move"),
                            ("Solo formattazione", "format"), ("Solo immagini", "image")):
            self.kind_filter.addItem(label, kind)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        self.ignore_case = ui.Toggle("Ignora maiuscole e minuscole")
        self.ignore_punct = ui.Toggle("Ignora la punteggiatura")
        self.ignore_case.setChecked(True)
        self.ignore_punct.setChecked(True)
        self.formatting = ui.Toggle("Segnala grassetto, corsivo e dimensione")
        self.formatting.toggled.connect(self._rediff)
        self.include_list = ui.Toggle("Elenco modifiche nel PDF esportato")
        self.include_list.setChecked(True)
        self.include_list.setToolTip("Spento: il PDF esportato contiene solo le pagine affiancate")
        for c in (self.ignore_case, self.ignore_punct):
            c.toggled.connect(self._rediff)
            opts.addWidget(c)
        opts.addWidget(self.formatting)
        opts.addWidget(self.include_list)
        opts.addWidget(self.kind_filter)
        sl.addLayout(opts)
        sl.addWidget(self.list)
        split = QSplitter()
        split.addWidget(column(self.old_label, self.old_view, ui.RED))
        split.addWidget(column(QLabel(), self.bar, ui.color("line")))
        split.addWidget(column(self.new_label, self.new_view, ui.GREEN))
        split.addWidget(side)
        split.setSizes([520, 22, 520, 300])
        split.setChildrenCollapsible(False)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(split)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        layout.addWidget(self.stack, 1)
        self.reset()

    # ---- choosing files
    def dragEnterEvent(self, e):
        if ui.pdf_from_drop(e):
            e.acceptProposedAction()

    def dropEvent(self, e):  # dropped outside the zones: fill the first empty slot
        path = ui.pdf_from_drop(e)
        if path:
            self._pick("new" if self.old_path else "old", path)

    def _check(self, path) -> bool:
        """Open the file and make sure it has text; on failure say which file and why."""
        name = Path(path).name
        try:
            doc = open_pdf(path)
        except ValueError as e:
            warn(self, f"{name}: {e}")
            return False
        if not any(page.get_text("words") for page in doc) and not ocr_available():
            info(self, NO_OCR.format(name))
            return False
        return True

    def _pick(self, side, path):
        if not self._check(path):
            return  # the other side keeps its file
        if side == "old":
            self.old_path = path
            self.drop_old.set_file(path)
        else:
            self.new_path = path
            self.drop_new.set_file(path)
        missing = "la nuova versione" if not self.new_path else "l'originale" if not self.old_path else ""
        if missing:
            self.summary.setText(f"{Path(path).name} caricato. Ora scegli {missing}.")
        if self.old_path and self.new_path:
            self.load(self.old_path, self.new_path)

    def reset(self):
        self.old_path = self.new_path = None
        self.changes, self.current = [], -1
        self.list.clear()
        self.drop_old.set_file(None)
        self.drop_new.set_file(None)
        self.summary.setText("Scegli i due PDF da confrontare")
        self.stack.setCurrentIndex(0)
        self._buttons()

    def _buttons(self):
        loaded = self.stack.currentIndex() == 1
        for w in (self.b_new, self.sync, self.b_zout, self.b_zin, self.zoom_label, self.b_page, self.b_full):
            w.setVisible(loaded)
        self.b_export.setEnabled(loaded)
        self.b_xlsx.setEnabled(bool(self.changes))
        self.b_prev.setEnabled(self._next_visible(-1) is not None)
        self.b_next.setEnabled(self._next_visible(1) is not None)

    # ---- compare
    def load(self, old_path: str, new_path: str) -> None:
        try:
            old, new = open_pdf(old_path), open_pdf(new_path)
        except ValueError as e:
            warn(self, str(e))
            return
        # OCR of a scan can take a while: a bar with Annulla, refreshed after every page
        # (no thread: PyMuPDF keeps Python's lock while it works, the window would freeze all the same)
        try:
            n_old, n = old.page_count, old.page_count + new.page_count
            with ui.progress(self, "Leggo testo e immagini delle pagine...", 2 * n, cancel=True) as step:
                old_words = extract_words(old, ocr=True, on_page=step)
                new_words = extract_words(new, ocr=True, on_page=lambda i: step(n_old + i))
                images = diff_images(extract_images(old, lambda i: step(n + i)),
                                     extract_images(new, lambda i: step(n + n_old + i)))
        except ui.Cancelled:
            self.reset()
            self.summary.setText("Confronto annullato")
            return
        empty = [Path(p).name for p, w in ((old_path, old_words), (new_path, new_words)) if not w]
        if empty:
            info(self, NO_OCR.format(", ".join(empty)))
            return
        self.old_path, self.new_path = old_path, new_path
        self.docs, self.words, self.images = (old, new), (old_words, new_words), images
        self.old_label.setText(f"Originale  {Path(old_path).name}")
        self.new_label.setText(f"Nuova versione  {Path(new_path).name}")
        self.stack.setCurrentIndex(1)
        self._rediff()

    def _rediff(self):
        """Diff again with the current options and redraw the highlights."""
        if self.stack.currentIndex() != 1:
            return
        self.changes, self.current = [], -1
        self.list.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.old_view.show_doc(self.docs[0])
            self.new_view.show_doc(self.docs[1])
            options = dict(ignore_case=self.ignore_case.isChecked(), ignore_punct=self.ignore_punct.isChecked(),
                           formatting=self.formatting.isChecked())
            text = ui.run_busy(self, "Confronto i testi...", lambda: diff_words(*self.words, **options))
            self.changes = merge_changes(text, self.images)
        finally:
            QApplication.restoreOverrideCursor()
        counts = {k: sum(c.kind == k for c in self.changes) for k in KIND_NAMES}
        for c in self.changes:
            left, right = SIDE_COLORS[c.kind]
            self.old_view.highlight(c.old, left or RED, OPACITY)
            self.new_view.highlight(c.new, right or GREEN, OPACITY)
            if c.whole:  # the whole sheet tinted, not only its words
                view, doc, rgb = ((self.new_view, self.docs[1], GREEN) if c.kind == "insert"
                                  else (self.old_view, self.docs[0], RED))
                view.highlight([Word(pg, tuple(doc[pg].rect), "") for pg in c.whole], rgb, 0.08)
            item = QListWidgetItem(c.label())  # plain text: tooltips and screen readers
            item.setData(Qt.UserRole + 1, change_html(c, KIND_COLORS[c.kind], ui.color("text_red"),
                                                      ui.color("text_green"), ui.color("muted")))
            self.list.addItem(item)
        if self.changes:
            parts = [f"{n} {word}" for n, word in ((counts["insert"], "aggiunte"), (counts["delete"], "rimozioni"),
                                                    (counts["replace"], "sostituzioni"), (counts["move"], "spostamenti"),
                                                    (counts["format"], "di formattazione"),
                                                    (counts["image"], "immagini")) if n]
            self.summary.setText(f"{len(self.changes)} modifiche: " + ", ".join(parts))
        else:
            self.summary.setText("Nessuna differenza nel testo")
        self._apply_filter()

    def _hidden(self, i) -> bool:
        kind = self.kind_filter.currentData()
        return bool(kind) and self.changes[i].kind != kind

    def _apply_filter(self):
        for i in range(len(self.changes)):
            self.list.setRowHidden(i, self._hidden(i))
        self._update_ticks()
        if self.current < 0 or self._hidden(self.current):
            self.current = -1
            self.step(1)
        self._buttons()

    def _update_ticks(self):
        """Ticks on the middle bar, like an overview of the whole document: where each change is in the original
        (or in the new version, for what the original lacks)."""
        if self.old_view.scene() is None or self.new_view.scene() is None:
            return  # the tab is being destroyed: its widgets go before the signals stop
        ticks = []
        for i, c in enumerate(self.changes):
            if self._hidden(i):
                continue
            for view, words in ((self.old_view, c.old or [c.old_at]), (self.new_view, c.new or [c.new_at])):
                total = view.scene().sceneRect().height() if view.offsets else 0
                if words[0] is not None and total:
                    ticks.append((view.word_rect(words[0])[1] / total, KIND_COLORS[c.kind], i, c.label()))
                    break
        self.bar.ticks = ticks
        self.bar.update()

    def _bar_range(self, lo, hi):
        own = self.old_view.verticalScrollBar()
        self.bar.blockSignals(True)
        self.bar.setRange(lo, hi)
        self.bar.setPageStep(own.pageStep())
        self.bar.setSingleStep(own.singleStep())
        self.bar.setValue(own.value())
        self.bar.blockSignals(False)
        self._update_ticks()  # zoom or resize: the document got taller or shorter

    def _bar_follow(self, value):
        self.bar.blockSignals(True)
        self.bar.setValue(value)
        self.bar.blockSignals(False)

    def _next_visible(self, d):
        i = self.current + d
        while 0 <= i < len(self.changes):
            if not self._hidden(i):
                return i
            i += d
        return None

    def step(self, d):
        i = self._next_visible(d)
        if i is not None:
            self.go_to(i)

    def _zoom_to(self, scale: float):
        """Both documents always share the same zoom, keeping the reader on the same spot."""
        pos = self.old_view.position() if self.old_view.offsets else None
        self._syncing = True
        try:
            self.old_view.set_scale(scale)
            self.new_view.set_scale(scale)
            if pos:
                self.old_view.scroll_to(*pos)
                self.new_view.scroll_to(*pos)
        finally:
            self._syncing = False
        self.zoom_label.setText(f"{round(self.old_view.scale * 100)}%")

    def zoom_by(self, factor: float):
        self._zoom_to(self.old_view.scale * factor)

    def zoom_fit(self):
        self._zoom_to(1.0)

    def zoom_page(self):
        """One whole page on screen, starting at its top."""
        page, _ = self.old_view.position()
        self._zoom_to(min(self.old_view.page_fit_scale(), self.new_view.page_fit_scale()))
        self.old_view.scroll_to(page, 0)
        if not self.sync.isChecked():
            self.new_view.scroll_to(self.new_view.position()[0], 0)

    def _follow_x(self, src: DocView, dst: DocView):
        """Zoomed in, moving sideways on one document moves the other the same way."""
        if self._syncing or not self.sync.isChecked():
            return
        a, b = src.horizontalScrollBar(), dst.horizontalScrollBar()
        self._syncing = True
        try:
            b.setValue(round(a.value() / a.maximum() * b.maximum()) if a.maximum() else 0)
        finally:
            self._syncing = False

    def _follow(self, src: DocView, dst: DocView):
        if self._syncing or not self.sync.isChecked() or not src.offsets or not dst.offsets:
            return
        self._syncing = True
        try:
            dst.scroll_to(*src.position())
        finally:
            self._syncing = False

    def go_to(self, index: int) -> None:
        if 0 <= index < len(self.changes):
            self.list.setCurrentRow(index)  # → _row_changed

    def _row_changed(self, index):
        if not 0 <= index < len(self.changes):
            return
        self.current = index
        self.bar.current = index
        self.bar.update()
        c = self.changes[index]
        self._syncing = True  # each side centres on its own copy of the change
        try:
            self.old_view.mark(c.old, c.old_at)
            self.new_view.mark(c.new, c.new_at)
        finally:
            self._syncing = False
        self._buttons()

    def ask_export(self):
        default = str(Path(self.new_path).with_name(Path(self.new_path).stem + "_confronto.pdf"))
        path, _ = QFileDialog.getSaveFileName(self, "Esporta confronto", default, "PDF (*.pdf)")
        if path:
            try:
                self.export(path)
            except (ValueError, OSError, RuntimeError) as e:
                warn(self, f"Esportazione non riuscita: {e}")
                return
            info(self, f"Confronto esportato in {path}")

    def ask_export_xlsx(self):
        default = str(Path(self.new_path).with_name(Path(self.new_path).stem + "_modifiche.xlsx"))
        path, _ = QFileDialog.getSaveFileName(self, "Esporta elenco modifiche", default, "Excel (*.xlsx)")
        if path:
            try:
                self.export_xlsx(path)
            except OSError as e:
                warn(self, f"Esportazione non riuscita (il file è aperto in Excel?): {e}")
                return
            info(self, f"Elenco esportato in {path}")

    def export_xlsx(self, path: str) -> None:
        export_changes_xlsx(self.changes, path, Path(self.old_path).name, Path(self.new_path).name)

    def export(self, path: str) -> None:
        export_report(self.old_path, self.new_path, self.changes, path, summary=self.include_list.isChecked())

