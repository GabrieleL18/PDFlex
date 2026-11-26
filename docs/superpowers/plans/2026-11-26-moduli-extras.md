# Moduli extras Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In Moduli: edit many fields at once, paste copied rows into the next table row, choose the checkbox mark, currency amounts, more calculations, tooltip/read-only/visibility/max length/range, tab order by columns with numbers on the page, and a non-blocking "Salvato" toast.

**Architecture:** New `Field` attributes are read and written in `pdflex/fields.py` (load, new widget, update of existing widget, blocks). The properties panel in `pdflex/editor.py` is reworked to apply one property at a time to all selected fields. Table-row search is a pure function in `pdflex/detect.py`. The toast is one helper in `pdflex/ui.py` used by every "saved" message.

**Tech Stack:** Python 3, PyMuPDF 2.1, PySide6, pytest (`.venv/Scripts/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-11-26-moduli-extras-design.md`

## Global Constraints

- Every new user-visible Italian string goes through `tr()` and gets an English entry in `pdflex/i18n_en.py` (`EN` dict), placeholders identical (`tests/test_i18n.py`).
- Forms saved by earlier versions must load and re-save unchanged: `amount|2|1` = euro before the number; a checkbox without `/MK/CA` = check mark; no new key is written for a property the user did not change (`_update_widget` writes only diffs against `f.orig`).
- New `Field` attributes have defaults equal to "nothing set", so `Field(**d)` from old backups and blocks still works.
- Non-ASCII characters inside JavaScript strings are written as `\uXXXX` escapes (viewers misread raw UTF-8 in scripts).
- No new dependencies. Run tests with `QT_QPA_PLATFORM=offscreen` (set by `tests/conftest.py`).
- Work on branch `moduli-extras`, one commit per task.

## Review Focus

1. A multi-selection mixing kinds (text + checkbox): only rows common to all show; changing "Obbligatorio" must not touch any other property of any field. → Task 4 test `test_multi_edit_touches_only_changed_prop`.
2. A form made in another program (Acrobat) with `AFNumber_Format(2, 0, 0, 0, "$", false)` and `AFRange_Validate`: must load as amount/$/after with range, and re-save byte-identical scripts if untouched. → Task 1 test `test_foreign_amount_and_range_read`.
3. Paste in a table whose next rows already hold fields: must skip to the first free row, never overlap. → Task 6 test `test_table_step_skips_taken_rows`.
4. Paste at the last row of a table / bottom of page: no row left → falls back to B / +10, never off the page. → Task 6 test `test_table_step_none_at_last_row`.
5. A checkbox whose mark changed, then moved (widget `update()` regenerates the look with ✓): the chosen mark must survive. → Task 2 test `test_mark_survives_move`.

---

### Task 1: Amount currency, calculation operations, min/max range (scripts)

**Files:**
- Modify: `pdflex/fields.py` (`Field`, `FORMATS`, `fmt_parts`, `fmt_label`, `_fmt_js`, `_fmt_from_js`, `_scripts`, `load_fields`, `_update_widget` "changed" scripts list, `sample_value`, `_BLOCK_KEYS`)
- Modify: `pdflex/i18n_en.py`
- Test: `tests/test_form_extras.py`

**Interfaces:**
- Produces:
  - `Field.calc_op: str = "SUM"`, `Field.vmin: str = ""`, `Field.vmax: str = ""` (decimal point, "" = no bound).
  - `CALC_OPS = {"SUM": tr("Somma"), "AVG": tr("Media"), "MIN": tr("Minimo"), "MAX": tr("Massimo"), "PRD": tr("Prodotto")}`
  - `CURRENCIES = ("€", "$", "£", "CHF", "")` ("" = none)
  - amount format string `amount|<dec>|<thousands 0/1>|<symbol>|<before 0/1>`; `fmt_parts("amount")` → `("amount", ["2", "1", "€", "1"])`; `fmt_parts` of number/percent unchanged (2 args).
  - `FORMATS["amount"] = tr("Importo")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_amount_currency_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 10, 100, 30), "usd", fmt="amount|2|1|$|0"),
              Field("text", 0, (10, 40, 100, 60), "eur", fmt="amount"),
              Field("text", 0, (10, 70, 100, 90), "gbp", fmt="amount|0|0|£|1")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    assert 'AFNumber_Format(2, 2, 0, 0, " $", false)' in _js(doc)
    assert '"\\u00A3 "' in _js(doc)
    got = {f.name: f.fmt for f in load_fields(doc)}
    assert got == {"usd": "amount|2|1|$|0", "eur": "amount|2|1|€|1", "gbp": "amount|0|0|£|1"}

def test_calc_ops_and_range_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("text", 0, (10, 10, 100, 30), "a", fmt="number", vmin="0", vmax="100.5"),
              Field("text", 0, (10, 40, 100, 60), "b", fmt="number", vmin="", vmax="10"),
              Field("text", 0, (10, 70, 100, 90), "m", fmt="number", calc=["a", "b"], calc_op="AVG")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    js = _js(doc)
    assert 'AFSimple_Calculate("AVG"' in js and "AFRange_Validate(true, 0, true, 100.5)" in js
    assert "AFRange_Validate(false, 0, true, 10)" in js
    got = {f.name: f for f in load_fields(doc)}
    assert (got["a"].vmin, got["a"].vmax, got["b"].vmin, got["b"].vmax) == ("0", "100.5", "", "10")
    assert got["m"].calc_op == "AVG" and got["a"].calc_op == "SUM"

def test_foreign_amount_and_range_read(make_pdf, tmp_path):
    # a field written by another program: Acrobat's own scripts
    ...  # build a text widget with pymupdf, set script_format = 'AFNumber_Format(2, 0, 0, 0, "$", false);'
         # and script_change = "AFRange_Validate(true, 1, false, 0);", save, load_fields
    assert f.fmt == "amount|2|1|$|0" and f.vmin == "1" and f.vmax == ""

def test_old_amount_format_still_euro_before():
    assert fmt_parts("amount|2|1") == ("amount", ["2", "1", "€", "1"])
    assert sample_value("amount|2|1|$|0", "x") == "1.234,50 $"
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_form_extras.py -q -k "currency or calc_ops or foreign_amount or old_amount"`
Expected: FAIL (unexpected keyword `vmin` / wrong fmt).

- [ ] **Step 3: Implement**

- `_fmt_js` amount: `AFNumber_Format(dec, sep, 0, 0, "<cur>", <true|false>)` where `cur = sym + " "` when before, `" " + sym` when after, `""` when no symbol; escape non-ASCII as `\uXXXX`. Keystroke unchanged. Drop the `tr('"\\u20AC "')` special case.
- `_fmt_js` for `NUMERIC` bases: validate script = `AFRange_Validate(<bool>, <vmin or 0>, <bool>, <vmax or 0>);` when vmin or vmax is set, else `None`. `_fmt_js` gets the field's vmin/vmax, so change its signature to `_fmt_js(fmt: str, kind: str = "text", vmin: str = "", vmax: str = "") -> tuple`.
- `_fmt_from_js`: cur string non-empty after decoding `\uXXXX` and stripping spaces → `amount|dec|thousands|sym|<1 if prepend true else 0>`; empty → `number|…`. Note: "amount, no currency" reads back as number (accepted).
- `load_fields`: parse `AFRange_Validate\(\s*(true|false)\s*,\s*([-\d.]+)\s*,\s*(true|false)\s*,\s*([-\d.]+)` from `js["V"]`; parse op from `AFSimple_Calculate\(\s*"(SUM|PRD|AVG|MIN|MAX)"`.
- `_scripts`: calc uses `f.calc_op`. `_update_widget`: "V" changes when fmt, vmin or vmax differ; "C" when calc or calc_op differ.
- `sample_value` amount: symbol and position from the format.
- `_BLOCK_KEYS` += `"calc_op", "vmin", "vmax"`. English strings in `i18n_en.py`.

- [ ] **Step 4: Run the whole file**

Run: `.venv/Scripts/python -m pytest tests/test_form_extras.py tests/test_formats.py -q`
Expected: all pass (update the existing assertion that `fmt == "amount|2|1"` to `"amount|2|1|€|1"`).

- [ ] **Step 5: Commit** — `git add pdflex/fields.py pdflex/i18n_en.py tests/ && git commit -m "Moduli: valuta negli importi, media/min/max/prodotto, minimo e massimo"`

---

### Task 2: Checkbox mark, tooltip, read-only, visibility, max length (widget properties)

**Files:**
- Modify: `pdflex/fields.py` (`Field`, `load_fields`, `_new_widget`, `build_pdf` new-widget branch, `_update_widget`, `_BLOCK_KEYS`)
- Modify: `pdflex/i18n_en.py`
- Test: `tests/test_form_extras.py`

**Interfaces:**
- Produces:
  - `Field.mark: str = ""` (ZapfDingbats char; "" = check `3`), `MARKS = {"": tr("Spunta"), "8": tr("Croce"), "l": tr("Punto"), "n": tr("Quadrato"), "u": tr("Rombo"), "H": tr("Stella")}`
  - `Field.tooltip: str = ""`, `Field.readonly: bool = False`, `Field.maxlen: int = 0`
  - `Field.visibility: str = ""`, `VISIBILITY = {"": tr("Visibile"), "hidden": tr("Nascosto"), "print": tr("Solo in stampa"), "screen": tr("Solo a schermo")}`
  - `_apply_mark(doc, xref: int, mark: str) -> None`: sets `/MK/CA (<char>)` and replaces the glyph in every non-`Off` stream of `AP/N`: `re.sub(rb"\((.)\) Tj", b"(" + char + b") Tj", stream)`. Verified on PyMuPDF 2.1: it always draws `(3) Tj`.

- [ ] **Step 1: Write the failing tests**

```python
def test_widget_props_roundtrip(make_pdf, tmp_path):
    src = make_pdf(tmp_path, ["x"])
    fields = [Field("checkbox", 0, (10, 10, 30, 30), "c", default="1", mark="8"),
              Field("text", 0, (10, 40, 100, 60), "t", tooltip="Il tuo nome", readonly=True, maxlen=12,
                    visibility="print"),
              Field("text", 0, (10, 70, 100, 90), "h", visibility="hidden"),
              Field("text", 0, (10, 100, 100, 120), "s", visibility="screen")]
    out = tmp_path / "o.pdf"
    save_with_fields(src, fields, out)
    doc = open_pdf(out)
    got = {f.name: f for f in load_fields(doc)}
    assert got["c"].mark == "8" and b"(8) Tj" in _on_stream(doc, got["c"].xref)
    t = got["t"]
    assert (t.tooltip, t.readonly, t.maxlen, t.visibility) == ("Il tuo nome", True, 12, "print")
    assert got["h"].visibility == "hidden" and got["s"].visibility == "screen"
    assert doc.xref_get_key(t.xref, "F")[1] == "36"   # NoView + Print

def test_mark_survives_move(make_pdf, tmp_path):
    # save with mark "H", load, move rect, save again: stream still "(H) Tj"

def test_calc_field_stays_readonly_without_flag(make_pdf, tmp_path):
    # Field calc=["a"], readonly False -> saved with ReadOnly bit, loads with readonly False

def test_old_checkbox_reads_as_check(make_pdf, tmp_path):
    # checkbox saved with mark "" -> no MK/CA "8"; reloads with mark == ""; resave changes nothing
```

`_on_stream(doc, xref)` is a test helper: the bytes of the first non-`Off` entry of `AP/N`.

- [ ] **Step 2: Run and see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_form_extras.py -q -k "widget_props or mark or readonly or old_checkbox"`
Expected: FAIL.

- [ ] **Step 3: Implement**

- Load: `tooltip = w.field_label or ""`; `readonly = bool(flags & READ_ONLY) and not calc`; `maxlen = w.text_maxlen` when not comb; `mark` from `xref_get_key(w.xref, "MK/CA")` when string and not in `("3", "4")`; visibility from annotation `F` (int): bit 2 → `hidden`; bits 32+4 → `print`; no bit 4 and no bit 2 → `screen`; else `""`.
- New widget: `w.field_label`, read-only bit when `readonly or calc`, `w.text_maxlen = comb or maxlen`, then after `add_widget`: `F` key (`""`→4, `hidden`→2, `print`→36, `screen`→0) and `_apply_mark` for checkboxes.
- Update: `TU` via `_set_inherited` (`"null"` when empty); read-only bit = `readonly or calc`; `MaxLen` = `comb or maxlen`; `F` keeps the other bits and only rewrites bits 2, 4, 32; `_apply_mark` after `_safe_update` for every checkbox whose mark is not "" (update regenerates the look).
- `_BLOCK_KEYS` += `"mark", "tooltip", "readonly", "maxlen", "visibility"`.

- [ ] **Step 4: Run**

Run: `.venv/Scripts/python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Commit** — `"Moduli: segno delle caselle, suggerimento, sola lettura, visibilità, massimo caratteri"`

---

### Task 3: Properties panel rows for one field

**Files:**
- Modify: `pdflex/editor.py` (panel build ~L600-660, `_show_props`, `_update_rows`, `_props_edited`)
- Modify: `pdflex/i18n_en.py`
- Test: `tests/test_editor_ux.py`

**Interfaces:**
- Consumes: Task 1-2 attributes and dicts (`CALC_OPS`, `CURRENCIES`, `MARKS`, `VISIBILITY`).
- Produces widgets: `p_kind` (QComboBox: text/multiline/date, label "Tipo"), `p_w`, `p_h` (QLineEdit, `QDoubleValidator`, "Larghezza (pt)", "Altezza (pt)"), `p_mark`, `p_currency`, `p_before` (QComboBox data 1/0: "Prima del numero"/"Dopo il numero"), `p_calc_op` (on the same row as `p_calc`, label "Calcolo (facoltativo)"), `p_tooltip` ("Suggerimento"), `p_readonly` (Toggle "Sola lettura"), `p_visibility` ("Visibilità"), `p_maxlen` ("Massimo caratteri", int 0-9999, placeholder "Nessuno"), `p_vmin`, `p_vmax` ("Valore minimo"/"Valore massimo", placeholder "Nessuno"; comma accepted, stored with ".").
- Row visibility: `p_kind` for text/multiline/date; `p_w/p_h` all; `p_mark` checkbox; `p_currency/p_before` when base == amount; `p_calc_op` with `p_calc`; `p_tooltip/p_visibility` all but image; `p_readonly` all but image/button/signature; `p_maxlen` text/multiline with comb == 0; `p_vmin/p_vmax` for NUMERIC.
- Kind change rules: → date: fmt `date|dd/mm/yyyy`; date → text: fmt ""; → multiline: fmt kept only if "" or email, comb 0. Width/height change keeps the on-screen top-left (`to_view`/`from_view`).

- [ ] **Step 1: Write the failing tests**

```python
def test_kind_combo_switches_text_multiline_date(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30)); tab.select_field(f)
    tab.p_kind.setCurrentIndex(tab.p_kind.findData("date"))
    assert f.kind == "date" and f.fmt.startswith("date|")
    tab.p_kind.setCurrentIndex(tab.p_kind.findData("multiline"))
    assert f.kind == "multiline" and f.fmt == ""

def test_size_rows_keep_top_left(tab):
    f = tab.add_field("text", 0, (10, 10, 100, 30)); tab.select_field(f)
    tab.p_w.setText("50"); tab.p_w.editingFinished.emit()
    tab.p_h.setText("15"); tab.p_h.editingFinished.emit()
    assert f.rect == (10, 10, 60, 25)

def test_new_rows_write_field(tab):
    c = tab.add_field("checkbox", 0, (10, 40, 30, 60)); tab.select_field(c)
    tab.p_mark.setCurrentIndex(tab.p_mark.findData("H")); assert c.mark == "H"
    t = tab.add_field("text", 0, (10, 80, 100, 100)); tab.select_field(t)
    tab.p_fmt.setCurrentIndex(tab.p_fmt.findData("amount"))
    tab.p_currency.setCurrentIndex(tab.p_currency.findData("$"))
    tab.p_before.setCurrentIndex(tab.p_before.findData(0))
    tab.p_vmin.setText("1,5"); tab.p_vmin.editingFinished.emit()
    assert t.fmt == "amount|2|1|$|0" and t.vmin == "1.5"
```

- [ ] **Step 2: Run and see them fail** — `.venv/Scripts/python -m pytest tests/test_editor_ux.py -q -k "kind_combo or size_rows or new_rows"` → FAIL (no attribute `p_kind`).

- [ ] **Step 3: Implement** the rows, their `_show_props` loading and `_update_rows` visibility, and their writing in `_props_edited` (Task 4 reshapes this; keep the writing for each property in its own small block so it can move).

- [ ] **Step 4: Run** — `.venv/Scripts/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Commit** — `"Moduli: tipo, misure e nuove proprietà nel pannello"`

---

### Task 4: Edit many fields at once

**Files:**
- Modify: `pdflex/editor.py` (`_show_props`, `_update_rows`, `_props_edited`, signal wiring, `multi_box`)
- Test: `tests/test_editor_ux.py`

**Interfaces:**
- Consumes: Task 3 widgets.
- Produces:
  - `EditorTab._targets() -> list[Field]`: `self.selection` when more than one, else `[self.selected]` (or `[]`).
  - `EditorTab._rows_for(f: Field) -> set[str]`: the row keys a field's kind/format shows (the rules now inside `_update_rows`). Shown rows = intersection over targets, minus `"name"` and `"value"` when more than one.
  - `EditorTab._set_prop(key: str) -> None`: reads the widget for `key`, writes it to every target with one undo snapshot (`_push`), skips fields where the value is already equal, keeps the existing side rules (radio rename/exclusive default, dropdown default match, clean radio value, kind/fmt coupling, calc only for number/amount).
  - Each widget's signal calls `_set_prop(<its key>)`; `_props_edited()` stays as the "commit what is being typed" entry used by save/preview/export: it calls `_set_prop` for the focused QLineEdit/QPlainTextEdit only.
  - Mixed values: QLineEdit → empty text, placeholder `tr("(diversi)")`; QComboBox → a temporary first item `tr("(diversi)")` with data `MIXED = object()` (removed once a real item is chosen; choosing it writes nothing); Toggle → `setTristate(True)` + `Qt.PartiallyChecked`, next click sets checked for all.
  - The `multi_box` (align/distribute/block/delete) stays above the properties; with many selected, `self.props` is visible too.

- [ ] **Step 1: Write the failing tests**

```python
def test_multi_edit_touches_only_changed_prop(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30)); a.font_size = 9; a.fmt = "number|2|1"
    b = tab.add_field("checkbox", 0, (10, 40, 30, 60)); b.mark = "8"
    tab.select_many([a, b])
    assert tab.props.isVisible() and not tab.form.isRowVisible(tab.p_name)
    assert not tab.form.isRowVisible(tab.p_fmt)          # not common to text + checkbox
    before_a, before_b = copy.deepcopy(a), copy.deepcopy(b)
    tab.p_required.setChecked(True)
    assert a.required and b.required
    assert dataclasses.replace(a, required=False) == before_a and dataclasses.replace(b, required=False) == before_b
    tab.undo(); assert not a.required and not b.required   # one undo step

def test_multi_mixed_values_show_diversi(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30)); a.font_size = 9
    b = tab.add_field("text", 0, (10, 40, 100, 60)); b.font_size = 12
    tab.select_many([a, b])
    assert tab.p_size.text() == "" and tab.p_size.placeholderText() == "(diversi)"
    tab.p_size.setText("10"); tab.p_size.editingFinished.emit()
    assert a.font_size == b.font_size == 10

def test_multi_kind_only_between_similar(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30)); b = tab.add_field("date", 0, (10, 40, 100, 60))
    tab.select_many([a, b]); assert tab.form.isRowVisible(tab.p_kind)
    c = tab.add_field("checkbox", 0, (10, 70, 30, 90)); tab.select_many([a, c])
    assert not tab.form.isRowVisible(tab.p_kind)
    tab.select_many([a, b]); tab.p_kind.setCurrentIndex(tab.p_kind.findData("multiline"))
    assert a.kind == b.kind == "multiline"

def test_multi_size_each_keeps_its_corner(tab):
    a = tab.add_field("text", 0, (10, 10, 100, 30)); b = tab.add_field("text", 0, (200, 50, 260, 70))
    tab.select_many([a, b]); tab.p_w.setText("40"); tab.p_w.editingFinished.emit()
    assert a.rect == (10, 10, 50, 30) and b.rect == (200, 50, 240, 70)
```

- [ ] **Step 2: Run and see them fail** — `-k multi_` → FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** — `.venv/Scripts/python -m pytest tests -q` → all pass (existing single-field tests are the regression net).
- [ ] **Step 5: Commit** — `"Moduli: proprietà di più campi insieme"`

---

### Task 5: Field drawing: mark, hidden fields, tab order by columns and numbers

**Files:**
- Modify: `pdflex/editor.py` (`FieldItem.paint`/`_paint_info`, list panel buttons near `b_sort`, `sort_by_position`)
- Modify: `pdflex/i18n_en.py`
- Test: `tests/test_editor_ux.py`

**Interfaces:**
- Produces:
  - `EditorTab.sort_by_columns()`: per page, key `(page, round(x0 / 6), y0)` on view rects; button `b_sort_cols` "Ordina per colonne", tip "Ordine Tab dall'alto in basso, colonna per colonna, pagina per pagina". Rename `b_sort` text to "Ordina per righe" (tip unchanged).
  - `EditorTab.c_order`: Toggle "Mostra ordine" in the same row; `EditorTab.tab_number(f: Field) -> int`: 1-based position in `self.fields`, counting a radio group once (buttons of a group share the group's first number).
  - Painting: with `c_order` on, a filled round badge (accent colour, white bold number, ~16 px) at the field's top-left corner. Checkbox items draw the mark glyph (`{"": "✓", "8": "✗", "l": "●", "n": "■", "u": "◆", "H": "★"}`) centred, in the kind colour. A field with `visibility` "hidden" or "screen"/"print" draws a dashed outline and its fill at half alpha.

- [ ] **Step 1: Write the failing tests**

```python
def test_sort_by_columns(tab):
    a = tab.add_field("text", 0, (10, 100, 90, 120)); b = tab.add_field("text", 0, (200, 10, 290, 30))
    c = tab.add_field("text", 0, (10, 10, 90, 30))
    tab.sort_by_columns(); assert tab.fields == [c, a, b]

def test_tab_number_counts_radio_group_once(tab):
    r1 = tab.add_field("radio", 0, (10, 10, 25, 25)); r2 = tab.add_field("radio", 0, (40, 10, 55, 25))
    r2.name = r1.name
    t = tab.add_field("text", 0, (10, 40, 100, 60))
    assert (tab.tab_number(r1), tab.tab_number(r2), tab.tab_number(t)) == (1, 1, 2)
```

- [ ] **Step 2: Run and see them fail** — `-k "sort_by_columns or tab_number"` → FAIL.
- [ ] **Step 3: Implement**; check by eye with `/run` or a screenshot of the editor (badge, glyphs, dashed hidden field).
- [ ] **Step 4: Run** — `.venv/Scripts/python -m pytest tests -q` → all pass.
- [ ] **Step 5: Commit** — `"Moduli: ordine Tab per colonne, numeri sui campi, segno e campi nascosti nel disegno"`

---

### Task 6: Paste into the next table row (A), repeat the manual move (B)

**Files:**
- Modify: `pdflex/detect.py` (new `row_lines`, `table_step`)
- Modify: `pdflex/editor.py` (`copy_selected`, `paste`)
- Test: `tests/test_detect.py`, `tests/test_editor_ux.py`

**Interfaces:**
- Produces:
  - `row_lines(page: pymupdf.Page) -> list[tuple[float, float, float]]`: `(x0, x1, y)` horizontals in unrotated page coordinates: `_lines(page.get_drawings())[0]` plus the top and bottom edge of every drawn rectangle (`re` items, and `qu` items that are axis-aligned) at least 3 pt wide.
  - `table_step(hlines, box: tuple, taken: list[tuple], page_height: float) -> float | None`: dy that moves `box` into the first free next row, or None. Algorithm:

```python
# lines covering >= 80% of the box width, ys merged within 1 pt, sorted
# a = last y <= box.y0 + 1 ; b = first y >= box.y1 - 1 ; ia, ib their indexes; n = ib - ia (rows the box spans)
# for k = 1, 2, ...: top = ys[ia + k*n], bottom = ys[ib + k*n] (stop when out of range)
#     dy = top - a ; moved = box shifted by dy
#     if moved.y1 <= bottom + 1 and moved.y1 <= page_height and no rect in taken intersects moved (shrunk by 0.5): return dy
# return None
```

  - Editor clip state becomes a dict: `self._clip = {"block", "home": (x, y), "page", "at": (x, y), "made": list[Field], "placed": (x, y), "step": (dx, dy) | None}`.
  - `paste()` on the same page, in order: (A) on an unrotated page, `table_step(row_lines(page), copied box in PDF coords, rects of all fields on the page, page.rect.height)` measured from the copied fields' box (`home`), so each Ctrl+V lands on the next free row; (B) else if the fields made by the previous paste are still in `self.fields` and their top-left differs from `placed`, `step = their top-left - previous at-reference`, at = their top-left + step, and the step is kept for the next pastes; (C) else the current +10 / back-to-home behaviour. Another page: unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_detect.py
H = [(50, 400, y) for y in (100, 120, 140, 160)]

def test_table_step_next_row():
    assert table_step(H, (60, 102, 200, 118), [], 800) == 20

def test_table_step_skips_taken_rows():
    assert table_step(H, (60, 102, 200, 118), [(60, 122, 200, 138)], 800) == 40

def test_table_step_none_at_last_row():
    assert table_step(H, (60, 142, 200, 158), [], 800) is None

def test_table_step_ignores_short_lines():
    assert table_step([(50, 80, y) for y in (100, 120, 140)], (60, 102, 200, 118), [], 800) is None

def test_row_lines_reads_cell_rectangles(tmp_path):
    doc = pymupdf.open(); page = doc.new_page()
    for y in (100, 120): page.draw_rect((50, y, 400, y + 20))
    ys = sorted({round(l[2]) for l in row_lines(page)})
    assert ys == [100, 120, 140]

# tests/test_editor_ux.py
def test_paste_goes_to_next_table_row(qapp, make_pdf, tmp_path): # PDF with rows at 100/120/140/160 (draw_line)
    # field Nome[0] at (60,102,200,118); copy; paste -> Nome[1] at y0 122; paste -> Nome[2] at y0 142

def test_paste_repeats_manual_move(tab):  # no lines on the page
    f = tab.add_field("text", 0, (10, 10, 100, 30)); tab.select_many([f]); tab.copy_selected()
    p1 = tab.paste()[0]; tab._move([p1], -10, 30)       # user puts it 40 below the original
    p2 = tab.paste()[0]
    assert to_view(tab.doc[0], p2.rect)[1] == 90         # 10 -> 50 -> 90
```

- [ ] **Step 2: Run and see them fail** — `-k "table_step or row_lines or paste_goes or paste_repeats"` → FAIL.
- [ ] **Step 3: Implement** as in Interfaces; keep `_fit` so nothing lands off the page.
- [ ] **Step 4: Run** — `.venv/Scripts/python -m pytest tests -q` → all pass (existing paste tests keep the +10 fallback).
- [ ] **Step 5: Commit** — `"Moduli: incolla nella riga successiva della tabella o ripete lo spostamento"`

---

### Task 7: "Salvato" toast

**Files:**
- Modify: `pdflex/ui.py` (new `toast`)
- Modify: `pdflex/editor.py` (`ask_save` success, `ask_export`), `pdflex/designer.py` (`ask_save` success), `pdflex/fill.py:181`, `pdflex/pages.py:524,531`, `pdflex/batch.py:277,279`, `pdflex/compare.py:1183,1194`
- Modify: `pdflex/i18n_en.py`; tests that monkeypatch `info` for these messages (`tests/test_new_features.py:104-111`, `tests/test_designer.py:367` if it is a save)
- Test: `tests/test_ui_smoke.py`

**Interfaces:**
- Produces: `toast(parent: QWidget, title: str, detail: str = "", ms: int = 4000) -> QWidget` in `pdflex/ui.py`. A frameless child of `parent.window()`, bottom-centre 24 px above the edge, rounded 10 px, drop shadow (`QGraphicsDropShadowEffect`), theme colours from `color()`, green check icon (`icon("check", GREEN)` or the closest existing glyph), bold title, detail below in muted text (word-wrapped, max ~420 px). Hover stops the timer, leave restarts it; click closes; a new toast replaces the one showing (one at a time, stored on the window). Fades out over 200 ms.
- Callers: title `tr("Salvato")` + detail = file name (`Path(path).name`) for single saves; `tr("Creati {0} PDF")` + folder for batches; export keeps its explanation as detail; compare: `tr("Esportato")` + file name. Errors stay `warn`.

- [ ] **Step 1: Write the failing tests**

```python
def test_toast_shows_and_replaces(qapp):
    w = QWidget(); w.resize(800, 600); w.show()
    t1 = ui.toast(w, "Salvato", "a.pdf", ms=50)
    assert t1.isVisible() and t1.parent() is w.window()
    t2 = ui.toast(w, "Salvato", "b.pdf")
    assert not t1.isVisible() and t2.isVisible()
    QTest.qWait(400); assert t2.isVisible()    # 4 s default
    QTest.mouseClick(t2, Qt.LeftButton); QTest.qWait(300); assert not t2.isVisible()

def test_editor_save_shows_toast(...):  # monkeypatch editor.toast, ask_save with a stubbed file dialog -> called with "Salvato", "<name>.pdf"
```

- [ ] **Step 2: Run and see them fail** — `-k toast` → FAIL.
- [ ] **Step 3: Implement** `toast`, swap the callers, update the tests that patched `info`.
- [ ] **Step 4: Run** — `.venv/Scripts/python -m pytest tests -q` → all pass; look at it once in the running app in light and dark theme.
- [ ] **Step 5: Commit** — `"Avviso di salvataggio che sparisce da solo"`

---

### Task 8: Final check

- [ ] Run `.venv/Scripts/python -m pytest tests -q` → all pass.
- [ ] Launch the app, open a PDF with a table in Moduli: multi-select edit, paste rows, checkbox marks, amount in $, tab numbers, save → toast; open the saved PDF in a browser viewer to check marks and the currency.
- [ ] Update `README.md` feature list if it lists Moduli properties.
