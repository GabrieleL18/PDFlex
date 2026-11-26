# Moduli: aspetto, formule, condizioni, colonne, incolla laterale — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** add the following to PDFlex's Moduli:
- field look (border and fill);
- formulas with functions, which may return text;
- "Mostra solo se" conditions;
- a field → column table in Serie;
- sideways paste for tables whose records run in columns.

Formulas and conditions work in PDF readers and inside PDFlex.

**Architecture:**
- **`pdflex/formula.py`** (new, pure Python): parses the user's text into a tree and never `eval`s anything. The tree is evaluated in Python and translated to JavaScript.
- **`pdflex/fields.py`**: stores the new properties.
  - Formulas go in the field's calculate script, with the original text as a marker comment.
  - Conditions go in a per-field key `/PDFlexIf`, plus one hidden helper field whose calculate script runs every condition.
- **`pdflex/logic.py`** (new): reads those rules back from any saved PDF and applies them for Compila, Anteprima and Serie.

**Tech Stack:** Python 3, PyMuPDF 2.1, PySide6, pytest. node.js is on this machine (v24) and is used by the tests to run the generated JavaScript; those tests skip themselves when node is missing.

**Spec:** `docs/superpowers/specs/2026-11-26-moduli-formule-design.md`

## Global Constraints

- **Downloads:** do not download or install anything (the user's rule: "NON SCARICARE ASSOLUTAMENTE NULLA SE NON TE LO SPECIFICO").
- **Never run what the user typed:** no `eval`/`exec` of user text in Python, and no user text pasted raw into JavaScript. Field names go through `json.dumps` and then `_js_escape`.
- **Same answer everywhere:** Python and the generated JavaScript give the same result for the same input. The node tests prove it.
- **Bilingual UI:** every new user-facing string goes through `tr()` and gets its English entry in `pdflex/i18n_en.py`.
- **Function and constant names:** case-insensitive; Italian and English both work.
  - SOMMA/SUM, MEDIA/AVERAGE, MIN, MAX, ARROTONDA/ROUND, ASS/ABS, SE/IF;
  - E/AND, O/OR, NON/NOT;
  - SÌ/SI/YES/VERO/TRUE, NO/FALSO/FALSE.
- **New fields look as before:** grey 0.6 border, 1 pt, solid, no fill. *Esporta per il gestionale* still strips border and fill.
- **Other programs' scripts:** a calculate script that is neither PDFlex's nor Acrobat's simplified notation is kept byte for byte and never evaluated.
- **Tests:** `.venv/Scripts/python -m pytest tests -q` (offscreen, `PDFLEX_LANG=it` via conftest).

## Review Focus

1. **Numbers written the Italian way** ("1.234,50", "1234,5", "€ 1.234,50", "12,5%") and raw reader values ("1234.5", 0.125). Each must read as the same number in Python and in JavaScript. A formula over a typed amount must not be off by a factor of 1000.
2. **Field names** with spaces, accents, quotes, brackets or `]` must survive four things: parse, rename, `to_js`, and save → reload. A name containing `]` is written `[a\]b]`.
3. **A form from another program** with its own calculate script must keep that script after it is moved and saved in PDFlex. Neither Compila nor Serie may evaluate it.
4. **Conditions.** A hidden conditional check box or radio group must end up unticked, and a hidden text field empty, so formulas over them see empty or NO. Reopening the saved PDF in PDFlex must show the condition text unchanged.
5. **The hidden helper field `PDFlex_logica`** must never appear in:
   - the editor's field list;
   - Compila's inputs;
   - Serie's columns;
   - the tab order numbers;
   - the duplicate-name check.

   It must be rebuilt, not duplicated, on every save.

---

### Task 1: The expression language (`pdflex/formula.py`)

**Files:**
- Create: `pdflex/formula.py`
- Test: `tests/test_formula.py`

**Interfaces:**
- Produces:
  - `class FormulaError(ValueError)`, with `.pos: int`.
  - `parse(text: str) -> Node`. `Node` is a tuple tree:
    - `("num", float)`, `("str", str)`, `("bool", bool)`, `("ref", name)`;
    - `("neg", n)`, `("bin", op, a, b)` with op in `+ - * / = <> < > <= >=`;
    - `("and", a, b)`, `("or", a, b)`, `("not", a)`;
    - `("call", FNAME, [args])`, where FNAME is canonical English: SUM AVERAGE MIN MAX ROUND ABS IF.
  - `refs(text: str) -> set[str]`. Raises FormulaError.
  - `rename(text: str, old: str, new: str) -> str`. Rewrites only `[old]` references and leaves the rest of the text exactly as typed.
  - `check(text: str, kinds: dict[str, str], choices: dict[str, list[str]]) -> str`:
    - returns "" when the text is fine, otherwise an Italian message (through `tr`);
    - `kinds` maps name → "number" | "text" | "checkbox" | "radio" | "dropdown";
    - `choices` maps a radio group to its stored values.
  - `evaluate(node, values: dict[str, str], kinds: dict[str, str]) -> float | str | bool | None`. `None` means empty.
  - `to_js(node, kinds) -> str`: a JavaScript expression that uses the prelude helpers.
  - `JS_PRELUDE: str`: the helper functions `_n`, `_s`, `_b`, `_cmp`, `_round`, `_fmt`.
  - `read_number(s: str) -> float | None` and `plain_number(x: float) -> str`.
  - `as_text(v) -> str`. Shows a result: "" for None; numbers via `plain_number`; bool as "Sì"/"No".

**Rules the tests pin:**
- **Grammar,** lowest precedence first: `O` < `E` < `NON` < comparison < `+ -` < `* / × ÷` < unary `-` < atom.
  - An atom is a number, `"text"` (a doubled `""` inside), `[name]` (`\]` and `\\` escape), a constant, a call `NAME(arg; arg)` or `( expr )`.
  - Numbers: `1,5` and `1.5` are both 1.5, and so is `,5`. A `,` is a decimal comma only between digits or right before a digit. Arguments are separated by `;` only.
- **`read_number`:**
  - First it strips spaces, NBSP, € $ £ CHF and a trailing `%`. A trailing `%` divides by 100.
  - Then the separators:
    - if `,` is present, every `.` is removed and `,` becomes `.`;
    - else, if it matches `^-?\d{1,3}(\.\d{3})+$`, the dots are removed (Italian thousands);
    - else it is a plain float.
  - "" or anything else gives None.
- **Value of a reference:**
  - checkbox: True when the value is in batch's TRUE_WORDS, or a non-"Off" export value;
  - radio and dropdown: the text;
  - number: `read_number`, with empty giving None;
  - text: the string.
- **Arithmetic** treats None as 0. Division by 0 gives None. Any operand that is None or non-numeric text (after `read_number`) makes the result None. Exception: `+` with empty operands treats them as 0.
- **Comparison:**
  - when both sides read as numbers, numeric;
  - when one side is bool, both become bool, with "" and None as False;
  - otherwise text with `casefold()`.

  A radio reference compared with a text literal compares against `clean_value(literal)`.
- **Functions:**
  - ROUND(x; n) is round-half-away-from-zero, the same as JS `_round`;
  - AVERAGE ignores None;
  - IF(c; a; b) evaluates c as bool. A number is truthy when ≠ 0, a string when not empty.
- **`plain_number`:** rounds to 2, strips trailing zeros, uses a decimal comma and no thousands separator: 2.5 → "2,5", 3.0 → "3", -0.125 → "-0,13".

- [ ] **Step 1: Write the failing tests** in `tests/test_formula.py`.
  - `test_parse_precedence`:
    - `parse("1 + 2 * 3")` evaluates to 7;
    - `parse("NON 1 > 2 E 3 = 3 O 0")` evaluates to True;
    - `parse("2 × 3 ÷ 4")` evaluates to 1.5.
  - `test_decimal_comma_and_semicolons`:
    - `evaluate(parse("ARROTONDA(1,005; 2)"), {}, {})` is 1.01;
    - `parse("MAX(1,5; 2.5)")` evaluates to 2.5;
    - `parse("SOMMA(1;2;3)")` evaluates to 6.
  - `test_english_names`: `parse("IF(TRUE; ROUND(2.345; 1); 0)")` evaluates to 2.3 (2.345 → 2.3 at 1 decimal, half away from zero on the binary value; assert `== round(2.345, 1)` computed by the same `_round` rule: 2.3).
  - `test_refs_with_odd_names`: `refs('[Prezzo unitario] * [Q.tà] + [a\\]b]') == {"Prezzo unitario", "Q.tà", "a]b"}`.
  - `test_rename_keeps_text`: `rename("[A]*2 + [AB]", "A", "Quantità") == "[Quantità]*2 + [AB]"`.
  - `test_read_number`: "1.234,50" → 1234.5; "1234,5" → 1234.5; "1234.5" → 1234.5; "1.234" → 1234.0; "€ 1.234,50" → 1234.5; "12,5%" → 0.125; "" → None; "abc" → None.
  - `test_text_result_and_plain_number`:
    - `evaluate(parse('SE([Tipo] = "ditta"; "Partita IVA"; "Codice fiscale")'), {"Tipo": "Ditta"}, {"Tipo": "dropdown"})` is "Partita IVA";
    - `plain_number(2.5) == "2,5"`, `plain_number(3.0) == "3"`.
  - `test_checkbox_and_radio_values`:
    - `[Altro] = SÌ` is True with `{"Altro": "1"}` and with `{"Altro": "Yes"}`, and False with `{"Altro": ""}`;
    - `[Tipo] = "Persona fisica"` with radio value "Persona_fisica" is True.
  - `test_division_by_zero_is_empty`: `evaluate(parse("1/0"), {}, {}) is None`.
  - `test_errors`:
    - `check("[Nessuno] + 1", {"A": "number"}, {})` mentions "Nessuno";
    - `check("1 +", {}, {})` is non-empty;
    - `check('[Tipo] = "Altro"', {"Tipo": "radio"}, {"Tipo": ["Ditta"]})` is non-empty;
    - `parse("PIPPO(1)")` raises FormulaError.
  - `test_js_agrees_with_python`, parametrized over 12 formulas × value sets covering: Italian amounts, a percent value, an empty value, a checkbox, a radio literal, IF returning text, ROUND, AVERAGE with empty, division by zero, and comparisons of text and numbers.
    - For each, build `JS_PRELUDE + "var r = " + to_js(parse(t), kinds) + ";"`, with a stub `this.getField(n)` returning `{value: values[n]}`, where checkbox "" becomes "Off".
    - Print `_fmt(r)` with `console.log` and run it with `node -e`.
    - Assert it equals `as_text(evaluate(...))`, after mapping JS true/false to "Sì"/"No".
    - Skip when `shutil.which("node")` is None.
- [ ] **Step 2: Run them to see them fail.** Run `.venv/Scripts/python -m pytest tests/test_formula.py -q`. Expected: FAIL with `ModuleNotFoundError: pdflex.formula`.
- [ ] **Step 3: Implement `pdflex/formula.py`.**
  - A hand-written tokenizer plus a recursive-descent parser following the grammar above.
  - `evaluate` walks the tree.
  - `to_js` emits calls into `JS_PRELUDE` helpers, never raw user text. Names go through `json.dumps` and `_js_escape` (import it from fields lazily to avoid a cycle, or move `_js_escape` into formula.py and re-export it from fields).
  - In the prelude, `_n(name)` reads a field by its kind with the same rules as `read_number`, and `_fmt` mirrors `as_text`.
- [ ] **Step 4: Run the tests.** Run `.venv/Scripts/python -m pytest tests/test_formula.py -q`. Expected: all pass. The node test runs and is not skipped.
- [ ] **Step 5: Commit** with `git add pdflex/formula.py tests/test_formula.py && git commit -m "Formule: linguaggio, calcolo in Python e in JavaScript"`.

### Task 2: Field look (`fields.py`)

**Files:**
- Modify: `pdflex/fields.py` (`Field`, `_new_widget`, `_update_widget`, `load_fields`, `_BLOCK_KEYS`)
- Test: `tests/test_form_look.py`

**Interfaces:**
- Produces new `Field` attributes:
  - `border_color: str = ""`: "" is the default grey, "none" is no border, otherwise "#rrggbb" (black included);
  - `fill_color: str = ""`: "" is no fill;
  - `border_width: int = 1`: 1, 2 or 3;
  - `border_style: str = ""`: "" solid, or one of "dashed", "beveled", "inset", "underline".
- Produces constants `BORDER_STYLES = {"": tr("Pieno"), "dashed": tr("Tratteggiato"), "beveled": tr("In rilievo"), "inset": tr("Incassato"), "underline": tr("Sottolineato")}` and `BORDER_WIDTHS = {1: tr("Sottile"), 2: tr("Medio"), 3: tr("Spesso")}`.
- PyMuPDF mapping: `w.border_style` takes "S", "D", "B", "I", "U", and dashed gets `w.border_dashes = [3, 3]`. With "none", set `border_width = 0` and `border_color = None`.
- Load:
  - grey 0.6 (all three channels within 0.01) gives "";
  - no border colour, or width 0, gives "none";
  - any other colour is written with a hex helper that keeps black ("#000000"). The existing `_hex` turns black into "", so it cannot be used here.

- [ ] **Step 1: Write the failing tests.**
  - `test_new_field_keeps_old_look`: a default text field saves with border colour (0.6, 0.6, 0.6), width 1, no fill.
  - `test_look_round_trip`: a text field with `border_color="#000000"`, `fill_color="#ffeecc"`, `border_width=2`, `border_style="dashed"`, and a checkbox with `border_color="none"`, are saved, then read back with `load_fields`. Both must come back equal on those four attributes.
  - `test_look_change_on_saved_field`: load a saved field, set `border_style="underline"` and `fill_color="#ddeeff"`, save again, and reload. It must show the change, and its format script must still be there (use an amount field).
  - `test_export_strips_look`: with `save_with_fields(..., blank=True)`, a field with a fill colour has no fill and no border.
  - `test_block_keeps_look`: `make_block` then `place_block` keep `fill_color` and `border_style`.
- [ ] **Step 2: Run them.** Run `.venv/Scripts/python -m pytest tests/test_form_look.py -q`. Expected: FAIL, because `Field` has no attribute `border_color`.
- [ ] **Step 3: Implement.**
  - Add the attributes and constants.
  - `_new_widget` writes them.
  - `_update_widget` writes them on `w` before `_safe_update` when any of the four changed.
  - `load_fields` reads them.
  - Add the four keys to `_BLOCK_KEYS`.
- [ ] **Step 4: Run the new tests and the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit** as "Moduli: bordo e sfondo dei campi".

### Task 3: Formulas and conditions in the PDF (`fields.py`)

**Files:**
- Modify: `pdflex/fields.py`
- Test: `tests/test_form_formulas.py`

**Interfaces:**
- Consumes: Task 1 (`parse`, `refs`, `rename`, `check`, `to_js`, `JS_PRELUDE`, `_js_escape`).
- Produces:
  - **New `Field` attributes:** `formula: str = ""`, `show_if: str = ""`, and `foreign_calc: bool` (`dc_field(default=False, compare=False, repr=False)`). `foreign_calc` is True when the field has a calculate script that is neither PDFlex's nor simplified notation.
  - **The helper field:** `HELPER = "PDFlex_logica"`.
  - **The formula's script:** `/** PDFlex: <_js_escape(text) with "*/" written "* /"> **/` + `(function(){` + `JS_PRELUDE` + `var r = <to_js>; event.value = <a number when the field has a numeric format, otherwise _fmt(r)>;` + `})();`.
  - **The condition's key:** `/PDFlexIf (text)` on the field holder (parent or widget), written with `pymupdf.get_pdf_str`.
  - **The helper:**
    - when any field has `show_if`, `build_pdf` adds one text widget named `HELPER` on page 0;
    - rect (0, 0, 1, 1), annotation flag Hidden, read-only;
    - calculate script: the prelude, then for each conditional field, in the fields' order, `var on = _b(<to_js>); var f = this.getField(<name>); f.display = on ? <display.visible | display.noView | display.noPrint by the field's visibility> : display.hidden; if (!on) <empty it: f.value = ""; for checkbox/radio f.value = "Off">;`.
    - `build_pdf` deletes any old helper first, like a removed field, so it is rebuilt and never duplicated.
  - **Calculation order:** `/CO` lists the formula fields and quick totals sorted so that each comes after the fields it reads, with the helper last.
  - **Loading:**
    - `load_fields` skips the `HELPER` widget;
    - a marker comment gives `formula` back (unescape `\uXXXX`);
    - `BVCALC … EVCALC` gives a formula, with bare names becoming `[name]`;
    - `/PDFlexIf` gives `show_if`;
    - any other C script sets `foreign_calc`.
  - **The read-only flag** covers `readonly or calc or formula`, here and in `_update_widget`'s `locked`.
  - **`_check_names`** raises `ValueError` naming the field for:
    - `check()` errors in `formula` / `show_if`;
    - a dependency cycle across formula, calc and show_if references, with the message `tr("Calcolo circolare: ") + " → ".join(path)`.
  - **Blocks:**
    - `_BLOCK_KEYS` gains `formula` and `show_if`;
    - `place_block` rewrites them through `rename` with its `renamed` map, as it already does for `calc`.
  - **`_scripts(f)["C"]`** is the formula script when `formula` is set, otherwise the AFSimple one.
  - **`_update_widget`'s "C" change test** includes `formula`. A changed `show_if` writes `/PDFlexIf`, or `null` when empty.

- [ ] **Step 1: Write the failing tests.**
  - `test_formula_round_trip`: `ARROTONDA([Quantità] * [Prezzo]; 2)` on an amount field saves and reloads with the same text. The widget is read-only, and its C script contains `PDFlex:`.
  - `test_condition_round_trip_and_helper`:
    - a checkbox `Altro` and a text field `Dettaglio` with `show_if='[Altro] = SÌ'` save and reload with that `show_if`;
    - `HELPER` exists exactly once after saving twice;
    - `load_fields` does not return it.
  - `test_calc_order_follows_dependencies`: B = `[A]*2` and C = `[B]+1` are created with C first in the list. In `/CO`, B comes before C and the helper is last.
  - `test_cycle_rejected`: A = `[B]+1` and B = `[A]+1` make `build_pdf` raise `ValueError` mentioning "circolare".
  - `test_unknown_name_rejected`: a formula with `[Nessuno]` makes `build_pdf` raise `ValueError` mentioning "Nessuno".
  - `test_acrobat_simplified_notation_read`: a widget whose C script is `/** BVCALC Quantita * Prezzo EVCALC **/ event.value = ...` loads with `formula == "[Quantita] * [Prezzo]"`.
  - `test_foreign_calc_kept`:
    - set up a widget whose C script is `event.value = 42;` (written with `xref_set_key`);
    - load it, move it, and save;
    - the script must be byte-identical and `foreign_calc` True.
  - `test_block_rewrites_formula`: a block with `Prezzo[0]` and `Totale[0] = [Prezzo[0]]*2` placed with taken `{"Prezzo[0]", "Totale[0]"}` gives `Totale[1] = [Prezzo[1]]*2`.
- [ ] **Step 2: Run them.** Run `.venv/Scripts/python -m pytest tests/test_form_formulas.py -q`. Expected: FAIL, because `Field` has no `formula`.
- [ ] **Step 3: Implement** the Interfaces above. The cycle search is a DFS over name → refs, with refs from `formula`, `calc` and `show_if`.
- [ ] **Step 4: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit** as "Moduli: formule e campi a condizione nel PDF".

### Task 4: PDFlex computes them (`logic.py`, `batch.fill_form`, `fill_sample`, Compila)

**Files:**
- Create: `pdflex/logic.py`
- Modify: `pdflex/batch.py` (`fill_form`), `pdflex/fields.py` (`fill_sample`), `pdflex/fill.py` (`form_inputs`, `FillDialog`)
- Test: `tests/test_logic.py`

**Interfaces:**
- Consumes: Task 1 and Task 3 (the marker comment, `/PDFlexIf`, `HELPER`, AFSimple totals).
- Produces:
  - `class Logic`, with:
    - `.kinds: dict[str, str]`;
    - `.formats: dict[str, str]` (name → fmt);
    - `.computed: dict[str, Node]`, in `/CO` order: formulas, plus quick totals turned into a SUM / AVERAGE / MIN / MAX node or a product chain;
    - `.conditions: dict[str, Node]`.
  - `form_logic(doc) -> Logic`. Foreign scripts are ignored.
  - `Logic.run(values: dict[str, str]) -> tuple[dict[str, str], set[str]]`. It returns the new values (computed ones written as the field's format shows them; hidden ones emptied) and the hidden names.
    - Each pass applies the conditions and then the computed fields in order.
    - It repeats until nothing changes, at most `len(fields) + 1` passes.
    - Add a `# ponytail:` comment on the fixed point: about n² for big forms; a dependency-ordered single pass if that ever matters.
  - `format_value(x, fmt) -> str`:
    - a number with a numeric fmt is written like `sample_value` does; factor that number formatting out of `sample_value` into `fmt_number(n, fmt)` in fields.py and use it in both;
    - otherwise `as_text(x)`.
  - `Logic.apply(doc) -> None`. It reads the current widget values, runs, writes the values with `_safe_update`, and sets the Hidden bit (`_set_visibility(..., "hidden")`) on hidden widgets.
  - **Callers:**
    - `batch.fill_form` ends with `form_logic(doc).apply(doc)`, which covers Compila's `fill_values` and Serie;
    - `fill_sample` ends with the same call, which covers Anteprima.
  - **`fill.form_inputs`:**
    - skips `HELPER`;
    - adds computed fields as `Input(kind="computed")`;
    - sets `Input.show_if = True` on conditional fields.
  - **`FillDialog`:**
    - a computed input is a read-only QLineEdit;
    - on every change, `Logic.run(self.values())` fills the computed boxes, hides the rows of hidden names (`form.setRowVisible`) and clears their widgets.

- [ ] **Step 1: Write the failing tests.**
  - `test_run_computes_and_hides`: a saved form with Quantità and Prezzo, `Totale = [Quantità]*[Prezzo]` (amount, 2 decimals, €), checkbox `Altro`, and `Dettaglio` with `show_if [Altro] = SÌ`. Running it with `{"Quantità": "2", "Prezzo": "1.234,50", "Altro": "", "Dettaglio": "x"}` gives Totale "€ 2.469,00", and Dettaglio is "" and in hidden.
  - `test_quick_total_computed`: an AFSimple SUM of two fields computes "3" from "1" and "2", with no format.
  - `test_fill_form_applies_logic`: after `fill_form(doc, row, mapping)`:
    - the Totale widget's value is the computed one, even when the row has a Totale column with another number;
    - Dettaglio's widget has annotation flag Hidden.
  - `test_fill_sample_computes`: after `fill_sample`, Totale is non-empty.
  - `test_foreign_script_not_evaluated`: a widget with `event.value = 42;` keeps the value it was filled with.
  - `test_fill_dialog_live` (qapp):
    - `FillDialog` on the saved form, then set Quantità = "2" and Prezzo = "3" and call the timer slot;
    - the Totale box shows "€ 6,00";
    - Dettaglio's row is hidden until Altro is ticked.
- [ ] **Step 2: Run them.** Run `.venv/Scripts/python -m pytest tests/test_logic.py -q`. Expected: FAIL with `ModuleNotFoundError: pdflex.logic`.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass, including the existing batch, fill and preview tests.
- [ ] **Step 5: Commit** as "Formule e condizioni anche in Compila, Anteprima e Serie".

### Task 5: Editor panel and canvas (`editor.py`)

**Files:**
- Modify: `pdflex/editor.py`
- Test: `tests/test_editor_formulas.py`

**Interfaces:**
- Consumes: Tasks 1–3 (`BORDER_STYLES`, `BORDER_WIDTHS`, the new `Field` attributes, `check`, `rename`).
- Produces:
  - **New panel boxes,** each in `_prop_widgets`, `_rows_for` and `_get`/`_apply`, so multi-selection and "(diversi)" work for free:
    - `p_border` (colour button, with "Nessuno" in its menu) and `p_fill` (colour button, with "Nessuno");
    - `p_bwidth` and `p_bstyle` (combos);
    - `p_formula` (QLineEdit) and `p_show_if` (QLineEdit), each with an error QLabel `objectName="Error"` under it, filled by `check()` on `editingFinished`.
  - **Rows:**
    - look rows for every kind except image and button;
    - `formula` for kind "text" with base in ("", "number", "amount", "percent");
    - `show_if` for every kind except image and button;
    - the quick-total row is hidden while `formula` is set.
  - **Renaming:** `_apply("name")` rewrites `[old]` → `[new]` (via `rename`) and the `calc` lists in every other field.
  - **Click-to-insert:** `self._insert_into`, set from `QApplication.focusChanged` when focus moves from `p_formula` or `p_show_if` to the page view, and cleared otherwise. While it is set, `FieldItem.mousePressEvent` inserts `[name]` (with `]` escaped) at the box's cursor, gives the focus back to the box, and does not change the selection.
  - **Canvas:**
    - `FieldItem.paint` draws the fill colour inside the box and the border in its colour and style (dashed is a DashLine; underline is the bottom line only; beveled and inset are solid), under the kind outline;
    - `details()` adds "formula", "a condizione".
  - **Other scripts:** `foreign_calc` fields show the formula box disabled with placeholder `tr("Script da un altro programma")`.

- [ ] **Step 1: Write the failing tests** (in the style of `tests/test_editor_ux.py`, with the qapp fixture and an editor tab on a temp PDF).
  - `test_formula_row_and_validation`: the formula row shows for a text field and not for a checkbox. Typing `[Nessuno]+1` and finishing the edit shows a non-empty error label and still stores the text.
  - `test_rename_rewrites_formulas_and_totals`: rename A to Quantità. The formula of B (`[A]*2`) becomes `[Quantità]*2`, and C's `calc` `["A"]` becomes `["Quantità"]`.
  - `test_multi_select_look`: with two fields selected, setting the fill to "#ffeecc" sets it on both; mixed border styles show "(diversi)".
  - `test_click_inserts_name`: with focus in `p_formula` (simulate `_insert_into = p_formula`), pressing a FieldItem named "Prezzo" inserts "[Prezzo]" and the selection is unchanged.
  - `test_show_if_on_radio_group_copies`: setting `show_if` on one radio button sets it on every button of the group.
  - `test_foreign_calc_disabled`: a loaded field with `foreign_calc` shows a disabled formula box.
- [ ] **Step 2: Run them.** Run `.venv/Scripts/python -m pytest tests/test_editor_formulas.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit** as "Moduli: pannello per aspetto, formule e condizioni".

### Task 6: Serie, field → column table (`batch.py`)

**Files:**
- Modify: `pdflex/batch.py` (`BatchTab`)
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: Task 4 (`form_logic(doc).computed`).
- Produces:
  - **The fields listed:** `BatchTab.set_template` leaves out `HELPER` and the computed fields.
  - **The table:** a "Campi e colonne" panel section, a QScrollArea holding a QFormLayout with one row per field: a label and a QComboBox of `"(nessuna)"` (data None) plus the headers.
    - It is rebuilt when the template or the table changes, preselected from `match_columns`.
    - `self.mapping` is read from the combos.
    - `_update` keeps the summary text and the run button state.
  - **Accessors** for tests:
    - `BatchTab.column_box(field) -> QComboBox`;
    - `BatchTab.current_mapping() -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_mapping_table_prefilled_and_editable` (qapp):
    - a form with `Nome` and `Codice`, and a CSV with `nome;cod_cliente`;
    - Nome's box shows "nome" and Codice's shows "(nessuna)";
    - choosing "cod_cliente" for Codice makes `current_mapping()` give `{"Nome": "nome", "Codice": "cod_cliente"}` and the summary say 2 of 2.
  - `test_computed_fields_not_listed`: a form with a formula field `Totale` has no row for it and no row for `HELPER`.
- [ ] **Step 2: Run them.** Run `.venv/Scripts/python -m pytest tests/test_batch.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit** as "Serie: scegli la colonna di ogni campo".

### Task 7: Sideways paste (`detect.py`, `editor.paste`)

**Files:**
- Modify: `pdflex/detect.py` (add `col_lines`), `pdflex/editor.py` (`paste`)
- Test: `tests/test_detect.py`, `tests/test_editor_ux.py`

**Interfaces:**
- Consumes: the existing `table_step(hlines, box, taken, page_height)`, unchanged.
- Produces:
  - `col_lines(page) -> list[tuple[float, float, float]]`: `(y0, y1, x)` of every vertical edge (drawn verticals from `_lines(...)[1]`, plus the left and right of boxes). This is `row_lines` transposed.
  - **`paste`:**
    - it computes `down = table_step(row_lines(page), box, taken, page.rect.height)` and `side = table_step(col_lines(page), (y0, x0, y1, x1), [(t[1], t[0], t[3], t[2]) for t in taken], page.rect.width)`;
    - a block taller than wide tries side first, then down; any other block tries down first, then side;
    - the first that is not None sets `at`: `(home[0] + side, home[1])` or `(home[0], home[1] + down)`;
    - the fallbacks after that are unchanged.

- [ ] **Step 1: Write the failing tests.**
  - `test_col_lines_from_cells`: a page with 3 cell rectangles side by side gives vertical edges at their x positions.
  - `test_table_step_sideways`: with verticals at x = 100, 150, 200, 250 spanning y 100–300, the transposed box (100, 100, 300, 150) with nothing taken gives 50.
  - `test_paste_goes_right_in_column_table` (editor):
    - a page with a 4-column × 3-row grid of cell rectangles, and 3 fields stacked in column 1;
    - copying all three and pasting puts them in column 2 at the same y positions;
    - pasting again puts them in column 3.
  - `test_paste_row_table_still_goes_down`: the existing row-table test still passes. Also, one wide field in a grid goes down, not right.
- [ ] **Step 2: Run them.** Run `.venv/Scripts/python -m pytest tests/test_detect.py tests/test_editor_ux.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit** as "Moduli: incolla nella colonna successiva quando la tabella va di lato".

### Task 8: Words, README, reader check

**Files:**
- Modify: `pdflex/i18n_en.py`, `README.md`
- Test: `tests/test_i18n.py` (existing: every `tr()` string has an English entry)

- [ ] **Step 1: Run the i18n test.** Run `.venv/Scripts/python -m pytest tests/test_i18n.py -q`. Expected: FAIL, listing the new strings.
- [ ] **Step 2: Add the English entries.** Update the README's Forms list (look, formulas, conditions, sideways paste) and its Batch line (choose the column for each field).
- [ ] **Step 3: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 4: Check in a PDF reader.**
  - Save a sample form to the scratchpad with a formula, a condition, a quick total and a coloured dashed field.
  - Open it as `file:///…` in the built-in browser (Chromium's PDF viewer) and type values.
  - Check that the total updates, that `Dettaglio` appears only with `Altro` ticked, and that the look matches.
  - Record what works and what does not in the ledger. Anything that fails is a finding for the final review, not a silent pass.
- [ ] **Step 5: Commit** as "Formule e condizioni: testi in inglese e README".
