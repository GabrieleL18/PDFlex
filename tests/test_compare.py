import pymupdf
import time

from pdflex import open_pdf
from pdflex.compare import Word, diff_words, export_report, extract_words


def W(*texts):
    return [Word(0, (0, 0, 1, 1), t) for t in texts]


def test_identical_no_changes():
    assert diff_words(W("a", "b"), W("a", "b")) == []


def test_one_word_changed_is_single_replace():
    ch = diff_words(W("Il", "prezzo", "è", "100", "euro"), W("Il", "prezzo", "è", "120", "euro"))
    assert len(ch) == 1 and ch[0].kind == "replace"
    assert [w.text for w in ch[0].old] == ["100"] and [w.text for w in ch[0].new] == ["120"]
    assert ch[0].label() == "Modificato (p. 1): 100 → 120"


def test_insert_and_delete():
    ins = diff_words(W("a", "c"), W("a", "b", "c"))
    assert [c.kind for c in ins] == ["insert"]
    assert ins[0].old_at.text == "c" and ins[0].new_at.text == "b"
    assert ins[0].label() == "Aggiunto (p. 1): b"
    end = diff_words(W("a"), W("a", "z"))
    assert end[0].old_at.text == "a"
    dele = diff_words(W("a", "b", "c"), W("a", "c"))
    assert [c.kind for c in dele] == ["delete"] and dele[0].new_at.text == "c"
    assert dele[0].label() == "Rimosso (p. 1): b"


def test_label_truncated():
    ch = diff_words(W("a"), W("a", *["parola"] * 30))
    text = ch[0].label().split(": ", 1)[1]
    assert len(text) == 60 and text.endswith("…")


def test_extract_words_pages(make_pdf, tmp_path):
    doc = open_pdf(make_pdf(tmp_path, ["uno due", "tre"]))
    assert [(w.page, w.text) for w in extract_words(doc)] == [(0, "uno"), (0, "due"), (1, "tre")]


def test_no_text_pdf_gives_no_words(make_pdf, tmp_path):
    assert extract_words(open_pdf(make_pdf(tmp_path, [""]))) == []


def test_export_report(make_pdf, tmp_path):
    old = make_pdf(tmp_path, ["prezzo 100 euro", "fine"])
    new = make_pdf(tmp_path, ["prezzo 120 € → euro", "fine", "allegato"])
    changes = diff_words(extract_words(open_pdf(old)), extract_words(open_pdf(new)))
    out = tmp_path / "r.pdf"
    export_report(old, new, changes, out)
    doc = open_pdf(out)
    assert doc.page_count == 1 + 3
    first = doc[0].get_text().replace(chr(0xa0), " ")
    assert "Confronto PDF" in first and f"Modifiche: {len(changes)}" in first and "→" in first
    w_old = open_pdf(old)[0].rect.width
    w_new = open_pdf(new)[0].rect.width
    assert abs(doc[-1].rect.width - (w_old + w_new)) < 0.5


def test_export_many_changes_paginates(make_pdf, tmp_path):
    old = make_pdf(tmp_path, ["x"])
    new = make_pdf(tmp_path, ["x"])
    changes = diff_words(W(*[t for i in range(200) for t in (f"o{i}", f"k{i}")]),
                         W(*[t for i in range(200) for t in (f"n{i}", f"k{i}")]))
    assert len(changes) == 200
    out = tmp_path / "r.pdf"
    export_report(old, new, changes, out)
    assert open_pdf(out).page_count >= 3


def test_long_document_is_fast():
    old = W(*[f"w{i % 500}" for i in range(20000)])
    new = list(old)
    new[5000] = Word(0, (0, 0, 1, 1), "X")
    t = time.perf_counter()
    assert len(diff_words(old, new)) == 1
    assert time.perf_counter() - t < 5


def test_ignore_case_and_punctuation():
    old, new = W("Il", "Prezzo", "è", "100,", "euro"), W("il", "prezzo", "è", "100", "euro.")
    assert len(diff_words(old, new)) == 2
    assert len(diff_words(old, new, ignore_case=True)) == 1  # "100, euro" vs "100 euro." is one change
    assert diff_words(old, new, ignore_case=True, ignore_punct=True) == []
    assert diff_words(W("a", "-", "b"), W("a", "b"), ignore_punct=True) == []


def test_export_changes_excel(tmp_path):
    import openpyxl
    from pdflex.compare import export_changes_xlsx
    ch = diff_words(W("a", "b", "c"), W("a", "x", "c", "d"))
    out = tmp_path / "m.xlsx"
    export_changes_xlsx(ch, out, "vecchio.pdf", "nuovo.pdf")
    rows = list(openpyxl.load_workbook(out).active.values)
    assert rows[0][:3] == ("N.", "Tipo", "Pagina originale")
    assert rows[1][1] == "Modificato" and rows[1][4] == "b" and rows[1][5] == "x"
    assert rows[2][1] == "Aggiunto" and rows[2][5] == "d"


def test_ocr_unavailable_is_graceful(make_pdf, tmp_path):
    from pdflex.compare import ocr_available
    doc = open_pdf(make_pdf(tmp_path, [""]))
    assert isinstance(ocr_available(), bool)
    if not ocr_available():
        assert extract_words(doc, ocr=True) == []


def test_export_without_list_of_changes(make_pdf, tmp_path):
    old, new = make_pdf(tmp_path, ["prezzo 100 euro"]), make_pdf(tmp_path, ["prezzo 120 euro"])
    changes = diff_words(extract_words(open_pdf(old)), extract_words(open_pdf(new)))
    out = tmp_path / "solo_pagine.pdf"
    export_report(old, new, changes, out, summary=False)
    doc = open_pdf(out)
    assert doc.page_count == 1 and "Confronto PDF" not in doc[0].get_text()


def _w(text, style=(False, False, 11), page=0, i=[0]):  # noqa: B006 (counter on purpose)
    i[0] += 1
    return Word(page, (i[0] * 10, 0, i[0] * 10 + 8, 10), text, style)


def test_moved_paragraph_is_one_move_not_delete_plus_insert():
    para = "il pagamento avviene entro trenta giorni".split()
    old = [_w(t) for t in ["Premessa", "uno"] + para + ["Articolo", "due", "fine"]]
    new = [_w(t) for t in ["Premessa", "uno", "Articolo", "due"] + para + ["fine"]]
    changes = diff_words(old, new)
    assert [c.kind for c in changes] == ["move"]
    # either reading is right: the paragraph went down, or "Articolo due" came up
    assert " ".join(w.text for w in changes[0].old) in (" ".join(para), "Articolo due")
    assert "Spostato" in changes[0].label()


def test_formatting_change_reported_only_when_asked():
    bold = (True, False, 11)
    old = [_w(t) for t in "il canone è dovuto".split()]
    new = [_w("il"), _w("canone", bold), _w("è"), _w("dovuto")]
    assert diff_words(old, new) == []
    changes = diff_words(old, new, formatting=True)
    assert [c.kind for c in changes] == ["format"] and [w.text for w in changes[0].new] == ["canone"]
    assert "Formattazione" in changes[0].label() and "grassetto" in changes[0].label()


def test_extract_words_reads_bold(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 72), "normale", fontname="helv")
    p.insert_text((72, 100), "grassetto", fontname="hebo")
    words = {w.text: w for w in extract_words(doc)}
    assert words["grassetto"].style[0] is True and words["normale"].style[0] is False


def test_whole_page_added_or_removed_is_said_so():
    def doc(pages):
        return [Word(p, (0, 0, 1, 1), t) for p, text in enumerate(pages) for t in text.split()]
    a = ["uno due tre", "quattro cinque sei", "sette otto"]
    b = ["uno due tre", "pagina nuova tutta inserita", "quattro cinque sei", "sette otto"]
    [c] = diff_words(doc(a), doc(b))
    assert c.kind == "insert" and c.whole == [1]
    assert c.label().startswith("Pagina 2 aggiunta per intero")
    [c] = diff_words(doc(b), doc(a))
    assert c.kind == "delete" and c.whole == [1] and c.pages_text() == "Pagina 2 rimossa"
    [c] = diff_words(doc(["uno due tre"]), doc(["uno due tre quattro"]))  # a word, not a page
    assert c.whole == []


def test_whole_page_found_when_pages_start_alike():
    """Every page starts with "Articolo": the diff slides the removed run by one word, still a whole page."""
    pages = [f"Articolo {i} il prezzo resta uguale per tutti" for i in range(6)]
    def doc(ps):
        return [Word(p, (0, 0, 1, 1), t) for p, text in enumerate(ps) for t in text.split()]
    [c] = diff_words(doc(pages), doc(pages[:3] + pages[4:]))
    assert c.kind == "delete" and c.whole == [3], " ".join(w.text for w in c.old)


def _pic(page, rect, rgb):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), 0)
    pix.set_rect(pix.irect, rgb)
    page.insert_image(pymupdf.Rect(rect), pixmap=pix, keep_proportion=False)


def test_images_added_removed_changed(tmp_path):
    from pdflex.compare import diff_images, extract_images
    def make(name, pics, scan=False):
        d = pymupdf.open()
        page = d.new_page()
        page.insert_text((72, 60), "testo uguale")
        for rect, rgb in pics:
            _pic(page, rect, rgb)
        if scan:  # a whole-page picture is a scan: not compared as a picture
            _pic(page, page.rect, (250, 250, 250))
        d.save(tmp_path / name)
        return pymupdf.open(tmp_path / name)
    red, blue, green = (220, 30, 30), (30, 30, 220), (30, 160, 30)
    old = make("a.pdf", [((72, 100, 172, 200), red), ((72, 300, 172, 400), blue)], scan=True)
    new = make("b.pdf", [((300, 100, 400, 200), red), ((72, 300, 172, 400), green), ((72, 500, 172, 600), blue)])
    changes = diff_images(extract_images(old), extract_images(new))
    # red only moved: no change. Blue is still there, further down: green is the one added
    assert [c.image_text() for c in changes] == ["Immagine aggiunta"] and changes[0].new[0].rect[1] == 300
    gone = diff_images(extract_images(new), extract_images(old))
    assert [c.image_text() for c in gone] == ["Immagine rimossa"]


def test_compare_tab_lists_image_changes(qapp, tmp_path):
    from pdflex.compare import CompareTab
    paths = []
    for name, rgb in (("x.pdf", (220, 30, 30)), ("y.pdf", (30, 30, 220))):
        d = pymupdf.open()
        page = d.new_page()
        page.insert_text((72, 60), "logo")
        _pic(page, (72, 100, 172, 200), rgb)
        d.save(tmp_path / name)
        paths.append(str(tmp_path / name))
    tab = CompareTab()
    tab.load(*paths)
    assert [c.kind for c in tab.changes] == ["image"]
    assert "Immagine cambiata" in tab.list.item(0).data(256 + 1)
    tab.kind_filter.setCurrentIndex(tab.kind_filter.findData("image"))
    assert not tab.list.isRowHidden(0)
    tab.export(str(tmp_path / "r.pdf"))
    tab.export_xlsx(str(tmp_path / "r.xlsx"))


def test_lines_first_gives_the_same_changes():
    """Diffing lines, then words inside the lines that differ, finds what a word-by-word diff finds."""
    import random
    rnd = random.Random(3)
    lines = [[rnd.choice("di la il contratto parte mesi euro".split()) for _ in range(8)] for _ in range(60)]
    edited = [l[:] for l in lines]
    edited[10][3], edited[40][0] = "nuovo", "anni"
    del edited[25]

    def words(ls, with_lines):
        return [Word(0, (0, 0, 1, 1), t, line=(0, 0, n) if with_lines else ()) for n, l in enumerate(ls) for t in l]
    plain = diff_words(words(lines, False), words(edited, False))
    by_line = diff_words(words(lines, True), words(edited, True))
    assert [(c.kind, c.old, c.new) for c in by_line] == [(c.kind, c.old, c.new) for c in plain]
