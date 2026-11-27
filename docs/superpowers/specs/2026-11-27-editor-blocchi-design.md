# Editor: blocchi, righe e colonne, icone, allineamento magnetico

## Why

The user wants to build the graphics of a form like a payslip (`BustaPaga.pdf`) inside PDFlex, in detail, "the way Figma works", but as simple as possible. The payslip is made of a few repeated pieces:

- boxes with rounded corners, a pale fill, a blue border and a title ("Dettagli rapporto", "Presenze");
- a grid with numbered columns 1–31 and named rows (Ordinarie, Ferie, Permessi…);
- a table with a header row, one header spanning two columns ("TOTALE" over "COMPETENZE / TRATTENUTE"), thin row lines;
- sections with a pale header band ("DETRAZIONI": ALTRE, CONIUGE, N. FIGLI…);
- total boxes ("Totale competenze", "Netto da pagare").

Today the Editor draws only a plain rectangle, snaps only while moving objects, and about a dozen of its buttons are text only.

The user decided:

- the pieces are **graphics only**; fields are added afterwards in Moduli (*Rileva campi* already finds empty boxes);
- a block split into rows and columns **stays an editable grid** (add a row later, drag a dividing line), not loose shapes;
- **one kind of thing, the block**, covers every ready-made piece and every split.

## Goals

1. **The block.** A rectangle split into rows and columns, drawn on the page as ordinary PDF graphics (rectangles, lines, text) so every reader shows it. Its settings:
   - outer box: border colour and thickness, corner radius, fill (or none);
   - column widths and row heights, each its own size;
   - an optional header row and an optional header column, with their own pale fill and bold text;
   - inner lines: colour and thickness, or none (a block with no inner lines is a "box");
   - merged cells (a cell spanning several columns and/or rows), so "TOTALE" spans two columns;
   - the text of each cell, with a size; header cells bold; a title in a box with one cell sits at its top left.
2. **It stays editable.** The block's settings are kept in the Editor's note in the PDF (`PDFlexEditor`, the same note that already keeps groups and locks). Selecting any part of it selects the whole block, and its properties appear on the right. Each change is one step of Annulla and redraws the block from its settings.
3. **Ready-made pieces.** After drawing a rectangle with the Rettangolo tool, a small gallery opens next to it with a live preview of each piece at the size just drawn:

   ```
   ┌──────────────────────────────────────────────┐
   │ [▭ Rettangolo] [▢ Riquadro con titolo]        │
   │ [▦ Tabella]    [▦ Griglia]                    │
   │ [▤ Sezione]    [▭ Totali]                     │
   └──────────────────────────────────────────────┘
   ```

   - **Rettangolo:** a plain rectangle, as today (not a block). Escape or a click elsewhere also means this.
   - **Riquadro con titolo:** one cell, rounded corners, pale fill, blue border, "Titolo" at the top left.
   - **Tabella:** header row + 4 columns × 5 rows, thin row lines.
   - **Griglia:** header row and header column, 8 columns × 4 rows (like Presenze).
   - **Sezione:** a pale header band over 3 columns × 3 rows, no outer border (like Detrazioni).
   - **Totali:** 2 columns × 3 rows, pale cells, white lines between them.

   The default colours are the payslip's: blue border, very pale blue fill, light grey inner lines.
4. **Split any rectangle.** A plain rectangle (one drawn in PDFlex or found in the PDF) shows *Righe* and *Colonne* in its properties: changing them turns it into a block.
5. **Editing a block in detail.**
   - *Righe* and *Colonne* numbers: adding one splits the last row or column in two; removing one merges the last two.
   - **Dividing lines:** when the block is selected, dragging an inner line changes the two columns or rows beside it; the outer size does not change. Dragging the block's outer handles scales all rows and columns proportionally.
   - **Cells:** double-click a cell to enter the block and write its text. Shift+click adds cells; *Unisci celle* merges them (only if they form a rectangle); *Dividi cella* splits a merged cell back. See "Picking cells" below.
   - *Riga di intestazione* and *Colonna di intestazione* switches; colours, thicknesses, radius as for any shape.
   - *Separa blocco* turns it into loose shapes (no longer a block).
6. **Allineamento magnetico everywhere.** The guides that today appear only while moving also appear while:
   - drawing a line, rectangle, image or text spot;
   - resizing with a handle;
   - dragging a block's dividing line.

   While drawing or resizing, a small tag next to the mouse shows the size in points ("120 × 40 pt"). Alt held: no snapping, as today.
7. **Icons on every Editor button.** Every button in the Editor gets a glyph from `ui.py`'s set, with its text kept where it fits and a tooltip always. New glyphs: bold, italic, arrow at start, arrow at end, bring to front, forward, backward, send to back, group, lock, rotate by, merge cells, split cell, split block.

## Non-goals

- Fields inside blocks: Moduli does fields.
- Grids inside cells (nesting) and Figma's auto layout (padding, hug, fill): merged cells cover the payslip.
- A library of the user's own saved pieces.
- Icons outside the Editor (Moduli, Confronta, Serie): the request was about the Editor.
- Measuring distances between objects (Figma's red distance lines).

## Anti-goals

- A PDF that looks different in another reader: blocks are ordinary graphics.
- A panel full of options: the block shows only what the selection needs (block, or cells).
- Losing work silently: if the drawn shapes of a block no longer match its note (the PDF was changed in another program, or a piece was deleted), the block becomes loose shapes and nothing is redrawn over the user's changes.

## Constraints

- PyMuPDF and PySide6 as now; nothing new to install or download.
- Every new text through `tr()` with an English entry (`tests/test_i18n.py` checks it).
- Rotated pages: the Editor already refuses to edit them; blocks follow the same rule.
- Undo keeps working as today: one change, one step.

## Approaches

- **A. One kind, the block (chosen).** Every ready-made piece is a block with different settings; the gallery shows the same block with each preset. One thing to learn and to build, and every piece stays editable.
- **B. Fixed ready-made pieces plus a separate grid.** Faster for the pieces, but a "Presenze" table could not get a new row later: against the choice of editable grids.
- **C. Frames inside frames, like Figma's auto layout.** Most powerful, but many new ideas and options, against "as simple as possible".

## Decided details

- **Where a block lives:** `PDFlexEditor` note → `blocks` → page number → list of blocks. Each block keeps its rectangle, column widths and row heights (points), header switches, merges, cell texts, style, and the keys of the shapes it drew (as groups already do: rectangle rounded to 0.1 pt plus kind).
- **Redrawing (my call):** delete the block's shapes and draw them again from its settings, then store the new keys. The block goes underneath the page's other content, the way a moved shape does today in `design._replace` ("a moved background never covers the text"). Within the block, its texts sit above its own fills. This way, text the PDF or the user put on a block stays visible after every edit. A newly drawn block also goes underneath.
- **Text already inside a split rectangle (my call):** it stays loose: it is not absorbed into the cells. Thanks to the rule above it stays visible. Dragging a divider does not move it.
- **Matching (my call):** on loading a page, a block whose rectangles and lines are not all found is dropped from the note and becomes loose shapes (see "Anti-goals: losing work"). Texts are not part of this check, because MuPDF often joins nearby words into one line. On redraw, a text is treated as the block's own when it sits inside the block and its words are made only of the block's cell texts. Any other text inside the block is left alone.
- **Existing commands on a block (my call):**
  - **Stays a block:** move, arrows, align and distribute (the block counts as one object), Porta sopra/sotto, Raggruppa (the block is one member), Blocca, colour, thickness and size controls (they change the block's style).
  - **Duplicates and copies:** *Duplica* and *Copia*/*Incolla*, on the same or another page, give a new block with its own entry in the note.
  - **Trova e sostituisci:** changes the cell text in the note and redraws the block.
  - **Rotation** (knob or degrees): first asks "Il blocco diventa forme separate. Continuare?", then turns the block into loose shapes.
- **Picking cells (my call, as in Figma):**
  - **Clicks:** a click selects the whole block, and Ctrl+click keeps its meaning (add or remove an object from the selection). A double-click on a cell of a block enters the block: that cell is selected, the cell panel shows with its *Testo* box focused, and the other cells show faintly.
  - **Picking cells:** inside the block, a click picks a cell and Shift+click adds cells, for *Unisci celle*.
  - **Leaving:** Esc, or a click outside the block, leaves the block.
  - This replaces "Ctrl+click picks several cells" in goal 5.

## Left to the builder

- The exact layout of the gallery and the block / cell panels, within the sketch above.
- How cell text is placed (padding, vertical centring) and its default size (the payslip uses about 7–9 pt).
- The size tag's look.
- The glyph drawings.

## Verification

- Tests for the block model: split, merge, add/remove rows and columns, drag a divider, the note round-trip, a block whose shapes changed becomes loose.
- Tests that each preset draws the expected shapes, and that the PDF opens in PyMuPDF with the same shapes.
- Tests that snapping happens while drawing and resizing.
- A test that every Editor button has an icon.
- Rebuild the payslip's "Presenze" and "Codice / Descrizione / TOTALE" tables with blocks, and look at the result rendered to an image.
