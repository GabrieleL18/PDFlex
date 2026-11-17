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
