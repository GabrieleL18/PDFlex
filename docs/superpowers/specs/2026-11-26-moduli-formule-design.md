# Moduli: aspetto, formule, campi a condizione, colonne della serie

## Why

PDFlex already does what a simple form needs. These four extras are what PDFcraft and Acrobat offer and PDFlex does not:

- forms that look like the paper they replace (aspetto);
- totals that are more than a sum (formule);
- questions that appear only when they apply (condizioni);
- an Excel file whose column names do not match the field names (serie).

The user chose the full version of each:

- functions in formulas;
- several conditions with E/O;
- a hidden field is emptied;
- formulas and conditions also work inside PDFlex (Compila, Anteprima, Serie), not only in PDF readers.

## Goals

1. **Aspetto.** For every field, alone or in a multi-selection:
   - border colour, or no border;
   - fill colour, or no fill;
   - border thickness: thin 1, medium 2, thick 3 pt;
   - border style: solid, dashed, beveled, inset, underline.

   New fields keep today's look: grey (0.6) 1 pt solid border, no fill. The canvas shows the chosen look. *Esporta per il gestionale* still strips borders and fill. Saved blocks keep the look.
2. **Formule.** A text field can show the result of a formula instead of a typed value, for example `ARROTONDA([Quantità] * [Prezzo] - [Sconto]; 2)`.
   - **Field names:** written in square brackets. Clicking a field on the page while the formula box has the focus inserts `[its name]`.
   - **Operators:** `+ - * /` (`×` and `÷` are accepted too) and parentheses.
   - **Numbers:** `1,5` and `1.5` both mean one and a half. There are no thousands separators, and `;` separates the arguments.
   - **Functions:** SOMMA, MEDIA, MIN, MAX, ARROTONDA(x; decimals), ASS, SE(condition; if true; if false).
   - **Comparisons and logic:** `= <> > < >= <=`, E, O, NON, SÌ/NO, and text in `"…"`.
   - Names are case-insensitive. English names (SUM, AVERAGE, ROUND, ABS, IF, AND, OR, NOT, YES, NO) work too: the app is bilingual. SI without an accent also works.
   - The result can be a number or a text (the user's choice), e.g. `SE([Tipo] = "Ditta"; "Partita IVA"; "Codice fiscale")`, so a formula works on plain text fields too.
     - A numeric result shows in the field's format (number, amount, percentage).
     - Without a numeric format, a number is written with at most 2 decimals, a decimal comma and no thousands separator, trailing zeros dropped; Python and the reader script must write it identically.
     - An empty result is empty.
   - The field is read-only, like a total.
   - The existing quick totals (Somma, Media, Minimo, Massimo, Prodotto of a list) stay. The calculation menu gains "Formula", which shows the formula box.
3. **Condizioni.** Any field (every kind) can have "Mostra solo se", written in the same language. Example: `[Altro] = SÌ O ([Importo] > 1000 E [Tipo] = "Ditta")`. While the condition is false the field is hidden and its value is emptied, so it does not count in totals and data.
4. **Validation while editing.** A *Valida* check next to each box, and again on save, rejects:
   - wrong syntax;
   - unknown field names;
   - a formula or condition that, through other fields, ends up depending on its own field (a cycle).

   The message names what is wrong, like the existing "missing fields in a total" check.
5. **Renaming** a field in the panel rewrites it in every total list, formula and condition that uses it. Today a rename rewrites nothing, and total lists are rewritten only when fields are pasted or placed as a block; that rewrite now also covers formulas and conditions inside the copied set. **Deleting** a referenced field is reported on save.
6. **In PDF readers** (Acrobat, Edge, Chrome): formulas recompute and conditional fields show or hide while someone fills in the form.
7. **In PDFlex:**
   - *Compila* shows formula fields and totals as read-only values that update while typing. It hides conditional inputs live and empties them. The saved copy holds the computed values and the hidden flags.
   - *Anteprima* applies the same rules to its sample data.
   - *Serie* computes them for every row: computed values are written, and fields whose condition is false are hidden and empty. This covers the existing quick totals too, which was the user's choice ("nelle copie della serie scrive i totali già calcolati"): a computed field is never taken from a column, and it does not appear in the column table.
8. **Serie, colonne:** a table with one row per form field, "campo → colonna". Each row has a menu with "(nessuna)" plus the file's columns, already filled in with today's automatic same-name matches. The "N campi su M trovati" summary follows the table.

9. **Incolla laterale** (asked by the user while planning): today pasting copied fields that sit in a table goes to the next free row below. When the table's records run in columns instead (cells arranged in a column, the next record to the right), the paste goes to the next free column on the right, at the same place in the column.
   - A copied block taller than it is wide tries the column to the right first, then the row below.
   - Any other block tries the row below first, then the column to the right.
   - Columns come from the page's vertical lines and box edges, exactly as rows come from the horizontal ones.
   - When neither direction is free, the existing fallbacks apply unchanged: the repeated manual move, then the small offset.

## Non-goals

- Text functions (joining, upper case), date arithmetic, conditions on whole pages.
- Running JavaScript written by hand or by another program. Such scripts are kept untouched, as today, and PDFlex does not evaluate them.
- Saving the column mapping between sessions.
- List boxes and push buttons as formula or condition targets. They stay untouched, as today.

## Anti-goals

- PDFlex must never `eval` or run what the user typed, in Python or in JavaScript built by gluing strings together. Formulas are parsed into a tree, and only that tree is evaluated or translated.
- A form made by another program must not lose its own scripts when opened and saved.
- What PDFlex computes and what Acrobat or Chrome compute must agree on the same input.

## Approaches

- **Chosen: one small expression language.** A Python parser turns the text into a tree. The tree is evaluated in Python for Compila, Anteprima and Serie, and translated to JavaScript for the PDF. Conditions are written as formulas too: this was the user's pick over guided rows ("campo / operatore / valore"), for the freedom to mix E/O with parentheses.
- **Rejected: guided rows for conditions.** Easier for beginners, but only "all true" or "at least one", and a second front end for the same engine.
- **Rejected: raw JavaScript.** PDFlex could not compute it itself, and the user asked for formulas and conditions inside PDFlex too.

## Decided details

- New `Field` attributes:
  - `formula: str` ("" none; when set, `calc` is ignored);
  - `show_if: str` ("" always shown);
  - `border_color: str` ("#rrggbb", "none" or "" for the default grey);
  - `fill_color: str` ("" none);
  - `border_width: int` (1–3);
  - `border_style: str` ("" solid, plus the four others).

  All of them join `_BLOCK_KEYS`.
- **How formulas and conditions are written into the PDF:**
  - The original text is saved inside the script as a marker comment, `/** PDFlex: <text> **/`, so the field reopens with exactly what was typed.
  - An Acrobat "simplified field notation" calculation (`/** BVCALC … EVCALC **/`) is read as a formula: bare names become `[names]`.
  - Any other calculation script is shown as "script da un altro programma", read-only, and kept.
- **Calculation order:** the form's calculation order (AcroForm `/CO`) is written in dependency order, so that a total of formulas is computed after the formulas it uses.
- **Values inside formulas and conditions:**
  - a text field holding a number is read with the field's format; empty counts as 0;
  - a check box is SÌ or NO;
  - a menu or choice is its text;
  - a comparison is numeric when both sides read as numbers, otherwise it compares text, ignoring case;
  - dividing by zero leaves the result empty.
- **Radio groups and repeated fields** (my call): "Mostra solo se" belongs to the whole field. The panel copies it to every button of a radio group and to every copy of a field shown on several pages, as it already does for the name. There is no hiding of single buttons.
- **Text compared with a radio group** (my call): a radio button's stored value is its label cleaned by `clean_value`, so "Persona fisica" is stored as "Persona_fisica". When a condition compares a radio group with text, the text is cleaned the same way when the formula is checked, so Python and the PDF reader compare the same thing. *Valida* rejects text that is not one of the group's choices. Menus compare their text as it is.
- **Fill code:** the Python evaluator is one module, shared by Compila, Anteprima and Serie (`batch.fill_form` already serves Compila).

## Left to the builder

- **How conditions run in PDF readers.** A check box or radio has no calculation event in the PDF standard, so the visibility logic must live somewhere every field change reaches. One option is a hidden helper text field whose calculation script holds every condition; it must not show up as a field in Compila, Serie or the field list. Any approach is fine if it passes the reader check below.
- The exact panel layout of the four look controls, and of the formula and condition boxes, following the existing panel style.
- The module name for the parser and evaluator.

## Verification beyond unit tests

Save a sample form with a formula, a condition, a quick total and a coloured field. Open it from disk in the built-in browser (Chromium's PDF viewer), fill it in, and check:

- the formula updates;
- the conditional field appears and disappears;
- the look matches.

Acrobat is not on this machine. That check is the user's.
