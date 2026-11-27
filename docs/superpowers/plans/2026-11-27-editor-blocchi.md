# Editor: blocchi, righe e colonne, icone, allineamento magnetico — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the Editor, rectangles split into editable rows and columns ("blocks"), a gallery of ready-made blocks after drawing a rectangle, snapping while drawing and resizing, and an icon on every button.

**Architecture:**
- A new pure module, `pdflex/blocks.py`, holds:
  - the `Block` model and its operations (split, merge, dividers, presets);
  - `draw(page, block)`, which draws a block as ordinary PDF shapes and text underneath the page's content.
- `designer.py` keeps every block in the Editor's existing PDF note (`PDFlexEditor` → `blocks`), next to groups and locks.
  - It finds blocks again when a page loads, and treats a block's parts as one group.
  - Every block change is one `_edit` step: delete the parts, draw the block again, rewrite the note.
- Snapping reuses `ui.snap_box` with a zero-size box.

**Tech Stack:** Python 3.14, PySide6, PyMuPDF, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-editor-blocchi-design.md`

## Global Constraints

- Nothing new to install or download.
- Every new user-visible string goes through `tr()` and has an English entry in `pdflex/i18n_en.py`. `tests/test_i18n.py::test_every_tr_string_has_english` checks it.
- Rotated pages stay locked, as today: no block is made or redrawn on them.
- One user change is exactly one Annulla step.
- Default colours are the payslip's:
  - border `#3B6FC4`;
  - box fill `#F5F8FD`;
  - header fill `#E9EFF9`;
  - inner lines `#D5DCE6` at 0.5 pt;
  - text `#1F2A44` at 8 pt;
  - corner radius 6 pt.
- Test command: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`. Known issue: an access violation at interpreter exit after "N passed" (exit 127/139). It is pre-existing and is not a failure.
- Work on branch `editor-blocchi`, cut from `main`.

## Review Focus

1. **A page with a full-page white background rectangle** (many generated PDFs have one): a block drawn "underneath" must still be visible. Expected: the block is visible. Pinned by `test_block_visible_over_page_background` (Task 2).
2. **Annulla after a block change** must bring back both the old shapes and the old note entry, so the block is editable again. Pinned by `test_block_edit_undo_restores_note` (Task 3).
3. **A block moved (arrows), then resized by its outer handles, then edited** (Righe +1) must redraw at its new place and size, not at the old one. Pinned by `test_block_follows_move_and_resize` (Task 3).
4. **Copy a block, paste it on another page**: the copy is its own editable block, and the original is unchanged. Pinned by `test_block_copy_paste_other_page` (Task 3).
5. **A block one of whose lines was deleted or changed elsewhere** becomes loose shapes. Nothing is redrawn over the user's change. Pinned by `test_broken_block_is_loose` (Task 3).

---

### Task 1: Block model

**Files:**
- Create: `pdflex/blocks.py`
- Test: `tests/test_blocks.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class Block:
      rect: tuple                      # x0, y0, x1, y1 (PDF points)
      cols: list[float]                # widths as fractions of the width, sum 1
      rows: list[float]                # heights as fractions of the height, sum 1
      header_row: bool = False
      header_col: bool = False
      merges: list[tuple] = field(default_factory=list)   # (r0, c0, r1, c1), inclusive
      texts: dict[str, str] = field(default_factory=dict) # "r,c" (top-left cell of a merge) -> text
      border: str = "#3B6FC4"; border_width: float = 1; radius: float = 6
      fill: str = "#F5F8FD"; head_fill: str = "#E9EFF9"
      lines: str = "#D5DCE6"; line_width: float = 0.5
      text_size: float = 8; text_color: str = "#1F2A44"
  # "" for border / fill / head_fill / lines means none
  def cells(b: Block) -> list[tuple[int, int, int, int, tuple]]   # (r0, c0, r1, c1, rect), merges as one cell, row by row
  def cell_at(b: Block, x: float, y: float) -> tuple[int, int] | None  # top-left (r, c) of the cell under the point
  def set_count(b: Block, axis: str, n: int) -> Block    # axis "rows" | "cols"; n >= 1
  def move_divider(b: Block, axis: str, i: int, pos: float) -> Block  # divider after index i to page coordinate pos
  def merge(b: Block, picked: list[tuple[int, int]]) -> Block | None   # None: they do not form a rectangle
  def split(b: Block, r: int, c: int) -> Block           # the merge whose top-left is (r, c) undone
  def scaled(b: Block, rect: tuple) -> Block              # same fractions, new rect
  def to_json(b: Block) -> dict
  def from_json(d: dict) -> Block
  PRESETS: list[tuple[str, str]]   # (id, tr(label)): rettangolo, riquadro, tabella, griglia, sezione, totali
  def preset(pid: str, rect: tuple) -> Block | None      # None for "rettangolo" (a plain rectangle)
  ```
  Operations return a new `Block` (`dataclasses.replace`), never mutate.

- [ ] **Step 1: Write the failing tests** in `tests/test_blocks.py`:
  ```python
  from pdflex import blocks as bl

  def B(**kw):
      return bl.Block((100, 100, 400, 220), [0.25] * 4, [0.25] * 4, **kw)

  def test_cells_and_merge():
      b = bl.merge(B(), [(0, 1), (0, 2)])
      assert b.merges == [(0, 1, 0, 2)]
      cs = bl.cells(b)
      assert len(cs) == 15 and cs[1][:4] == (0, 1, 0, 2) and cs[1][4] == (175, 100, 325, 130)
      assert bl.merge(B(), [(0, 0), (1, 1)]) is None          # not a rectangle
      assert bl.split(b, 0, 1).merges == []

  def test_set_count_splits_last_and_trims():
      b = bl.set_count(B(texts={"3,3": "x", "0,0": "a"}), "cols", 5)
      assert b.cols == [0.25, 0.25, 0.25, 0.125, 0.125]
      b = bl.set_count(b, "cols", 2)
      assert b.cols == [0.25, 0.75] and b.texts == {"0,0": "a"}   # text of a removed column goes

  def test_move_divider_and_min_size():
      b = bl.move_divider(B(), "cols", 0, 130)                # 100 + 30
      assert b.cols[0] == pytest.approx(0.1) and b.cols[1] == pytest.approx(0.4)
      b = bl.move_divider(B(), "cols", 0, 90)                 # past the edge: cell kept >= 4 pt
      assert b.cols[0] * 300 == pytest.approx(4)

  def test_cell_at_merged_and_json_roundtrip():
      b = bl.merge(B(texts={"0,1": "TOTALE"}), [(0, 1), (0, 2)])
      assert bl.cell_at(b, 300, 110) == (0, 1) and bl.cell_at(b, 50, 50) is None
      assert bl.from_json(bl.to_json(b)) == b

  def test_presets():
      r = (0, 0, 300, 150)
      assert bl.preset("rettangolo", r) is None
      assert [p for p, _ in bl.PRESETS] == ["rettangolo", "riquadro", "tabella", "griglia", "sezione", "totali"]
      t = bl.preset("tabella", r)
      assert t.header_row and len(t.cols) == 4 and len(t.rows) == 6      # header + 5
      g = bl.preset("griglia", r)
      assert g.header_row and g.header_col and len(g.cols) == 9 and len(g.rows) == 5
      assert bl.preset("riquadro", r).texts == {"0,0": "Titolo"} and bl.preset("sezione", r).border == ""
  ```
  (`import pytest` at the top.)
- [ ] **Step 2: Run it.** Run `.venv/Scripts/python -m pytest tests/test_blocks.py -q`. Expected: FAIL, `ModuleNotFoundError: pdflex.blocks`.
- [ ] **Step 3: Implement `pdflex/blocks.py`.**
  - **Shrinking:** `set_count` adds by halving the last row or column. It removes by joining the last two. Merges and texts that fall outside are dropped, and merges are clipped.
  - **Dividers:** `move_divider` keeps every cell at least 4 pt and leaves the total unchanged.
  - **Presets:**
    - riquadro: 1×1, text "Titolo";
    - tabella: header row + 4×5;
    - griglia: header row + header column, 8×4 body, so 9 columns × 5 rows;
    - sezione: header row + 3×3, `border=""`, `fill=""`;
    - totali: 2×3, `fill="#F5F8FD"`, `lines="#FFFFFF"`, `line_width=2`.
  - **Header sizes:** a header row is 18 pt high and a header column 70 pt wide, both capped at half the block. The rest is shared out evenly.
- [ ] **Step 4: Run it.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** Run `git add pdflex/blocks.py tests/test_blocks.py`, then `git commit -m "Editor: modello dei blocchi (righe, colonne, celle unite, modelli)"`.

### Task 2: Drawing a block on the page

**Files:**
- Modify: `pdflex/blocks.py`
- Test: `tests/test_blocks.py`

**Interfaces:**
- Consumes: Task 1's `Block`, `cells`. From `pdflex/design.py`: `_round_items(rect, k)`, `_draw_items(shape, items, m)`, `_write(page, origin, text, font, fontsize, color)`, `page_objects(page)`.
- Produces:
  - `draw(page, b: Block) -> None`;
  - `own_texts(objs: list[design.Obj], b: Block) -> list[design.Obj]`: the text objects inside `b.rect` whose words, spaces removed, are made only of `b`'s cell texts;
  - `preview(b: Block, w: int, h: int) -> QPixmap`, drawn by rendering `draw` on a scratch PDF page the block's size.

- [ ] **Step 1: Write the failing tests:**
  ```python
  import pymupdf
  from pdflex import design

  def _page():
      doc = pymupdf.open(); return doc, doc.new_page()

  def test_draw_table_shapes_and_text():
      doc, page = _page()
      b = bl.preset("tabella", (100, 100, 400, 250))
      b = dataclasses.replace(b, texts={"0,0": "CODICE"})
      bl.draw(page, b)
      objs = design.page_objects(page)[0]
      assert "CODICE" in page.get_text()
      assert any(o.kind == "rect" and pymupdf.Rect(o.rect).contains(pymupdf.Rect(100, 100, 400, 250)) for o in objs)
      lines = [o for o in objs if o.kind == "line"]
      assert len(lines) == 3 + 5                                 # 3 inner column lines, 5 inner row lines
      assert bl.own_texts(objs, b) and all(o.kind == "text" for o in bl.own_texts(objs, b))

  def test_merged_cell_has_no_line_inside():
      doc, page = _page()
      b = bl.merge(bl.preset("tabella", (100, 100, 400, 250)), [(0, 1), (0, 2)])
      bl.draw(page, b)
      x = 100 + 300 * 0.5                                         # the line between columns 1 and 2
      assert not any(o.kind == "line" and abs(o.rect[0] - x) < 1 and o.rect[1] < 118
                     for o in design.page_objects(page)[0])

  def test_block_is_drawn_under_existing_text():
      doc, page = _page()
      page.insert_text((110, 120), "sopra")
      bl.draw(page, bl.preset("riquadro", (100, 100, 300, 200)))
      assert page.get_text("words")[0][4] == "sopra" and page.get_cdrawings()   # the text is still readable

  def test_block_visible_over_page_background():
      doc, page = _page()
      page.draw_rect(page.rect, color=None, fill=(1, 1, 1))     # a white sheet under everything
      bl.draw(page, bl.preset("riquadro", (100, 100, 300, 200)))
      pix = page.get_pixmap(clip=(150, 150, 151, 151))
      assert pix.pixel(0, 0) != (255, 255, 255)                  # the pale fill shows
  ```
  (Import `dataclasses` at the top.)
- [ ] **Step 2: Run it.** Expected: FAIL, `AttributeError: module 'pdflex.blocks' has no attribute 'draw'`.
- [ ] **Step 3: Implement `draw`.**
  - **Order, bottom to top:** outer fill, header bands, inner lines, outer border, texts.
  - **Drawing underneath:** use `shape.commit(overlay=False)` and `TextWriter.write_text(..., overlay=False)`. Each underlay goes below the previous one, so issue them top to bottom: texts first, fills last.
  - **Inner lines:** skip the lines inside merged cells. A header band takes the outer radius only on its outer corners. Simplest: clip it with the same rounded path, or draw square bands when `radius == 0`.
  - **Text placement:** header cells are bold (`hebo`) and centred. Body cells are top-left with 4 pt padding.
  - **Page background:** a filled rectangle covering at least 90% of the page and drawn before everything else is a page background. Draw the block just above it. Put it in the right place with the existing layer mechanism: `content.place(page, "after", k)`, `content.isolate`, `content.splice`, as `design._replace` does for `layer`. That makes `test_block_visible_over_page_background` pass.
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit.** Commit with the message "Editor: disegno dei blocchi sotto il contenuto della pagina".

### Task 3: Blocks in the Editor: note, selection, commands

**Files:**
- Modify: `pdflex/designer.py`
- Test: `tests/test_designer_blocks.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces on `DesignTab`:
  - `self.blocks: list[tuple[int, Block, list[Obj]]]`: (index in the note's list for this page, block at its current place, all its parts on the page, own texts included). Filled by `_apply_marks`. Each block's parts also join `self.groups`, so a click selects the whole block.
  - `block_of(obj) -> tuple | None`: the entry of `self.blocks` that `obj` belongs to.
  - `apply_block(entry: tuple | None, new: Block, replace: list[Obj] = ()) -> bool`, one `_edit` step. It deletes the old block's parts (or `replace`), calls `blocks.draw(page, new)`, and finds the new parts by comparing `page_objects` before and after (texts excluded). It then writes the note entry `{"block": to_json(new), "keys": [_key(o) for the new non-text parts], "drawn": union of their rects}`, replacing `entry`'s if given, and selects the new block.
  - `use_preset(pid: str) -> None`: turns the last drawn rectangle (`self.last_drawn: Obj | None`) into `preset(pid, its rect)`.
- **Note format:** `marks["blocks"][str(page)] = [entry, ...]`.
  - **Finding a block on load:** in `_apply_marks`, an entry is a block when every key is found with `_is`. Its current rect is the stored `rect` mapped from `drawn` to the union of the found parts (scale and translate), then `scaled`.
  - **Broken entries:** an entry whose parts are not all found is skipped. It is removed the next time the note is written.
- **Commands** (see the spec's "Existing commands on a block"):
  - `_follow` updates block keys like group keys.
  - `delete_selected` removes the entries of the blocks it deletes.
  - `duplicate_selected` and `paste` add an entry for each copied block. Keys are shifted by the offset. `copy_selected` puts the entries of the copied blocks in `self.clip`.
  - `rotate_selected` with a block in the selection first calls `ui.message(self, tr("Il blocco diventa forme separate. Continuare?"), "ask", [tr("Continua"), tr("Annulla")])`. It drops the entry, then rotates.
  - `replace_all` changes the cell texts of matching blocks and redraws them. Their own texts are left out of the plain text replacement.

- [ ] **Step 1: Write the failing tests** in `tests/test_designer_blocks.py`. Use a `_tab(tmp_path)` helper that opens a blank A4 page, as `tests/test_designer.py::_pdf` does but without fields.
  ```python
  def test_draw_rect_then_preset_makes_a_block(qapp, tmp_path):
      tab = _tab(tmp_path); tab.set_mode("rect"); tab.drawn((100, 100, 400, 250))
      tab.use_preset("tabella")
      assert len(tab.blocks) == 1 and tab.selection and all(tab.block_of(o) for o in tab.selection)
      assert len(tab._undo) == 2                                  # rectangle, then block: two steps

  def test_block_edit_undo_restores_note(qapp, tmp_path):
      tab = _block(tmp_path, "tabella")
      entry = tab.blocks[0]
      tab.apply_block(entry, bl.set_count(entry[1], "rows", 8))
      assert len(tab.blocks[0][1].rows) == 8
      tab.undo()
      assert len(tab.blocks) == 1 and len(tab.blocks[0][1].rows) == 6

  def test_block_follows_move_and_resize(qapp, tmp_path):
      tab = _block(tmp_path, "tabella")
      tab.select(tab.blocks[0][2]); tab.move_selected(20, 30)
      assert tab.blocks[0][1].rect == pytest.approx((120, 130, 420, 280), abs=1)
      tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "rows", 7))
      assert tab.blocks[0][1].rect == pytest.approx((120, 130, 420, 280), abs=1)

  def test_block_copy_paste_other_page(qapp, tmp_path):
      tab = _block(tmp_path, "griglia", pages=2)
      tab.select(tab.blocks[0][2]); tab.copy_selected(); tab.go_page(1); tab.paste()
      assert len(tab.blocks) == 1
      tab.apply_block(tab.blocks[0], bl.set_count(tab.blocks[0][1], "cols", 3))
      tab.go_page(0)
      assert len(tab.blocks[0][1].cols) == 9

  def test_broken_block_is_loose(qapp, tmp_path):
      tab = _block(tmp_path, "tabella")
      line = next(o for o in tab.blocks[0][2] if o.kind == "line")
      tab._edit(design.delete, [line])                           # as if changed by another program
      assert tab.blocks == []

  def test_delete_block_and_rotate_asks(qapp, tmp_path, monkeypatch):
      tab = _block(tmp_path, "riquadro")
      monkeypatch.setattr(ui, "message", lambda *a, **k: 1)     # Annulla
      tab.select(tab.blocks[0][2]); tab.rotate_selected(90)
      assert len(tab.blocks) == 1
      tab.delete_selected()
      assert tab.blocks == [] and _marks(tab.doc).get("blocks", {}).get("0", []) == []   # from pdflex.designer import _marks

  def test_find_replace_changes_cell_text(qapp, tmp_path):
      tab = _block(tmp_path, "riquadro")
      tab.replace_all("Titolo", "Presenze")
      assert tab.blocks[0][1].texts["0,0"] == "Presenze" and "Presenze" in tab.doc[0].get_text()
  ```
  `_block(tmp_path, pid, pages=1)` opens the page or pages, draws the rectangle (100, 100, 400, 250), calls `use_preset(pid)` and returns the tab.
- [ ] **Step 2: Run it.** Expected: FAIL (`use_preset` / `blocks` missing).
- [ ] **Step 3: Implement** the interfaces above in `designer.py`. `drawn()` for "rect" keeps drawing the plain rectangle and sets `self.last_drawn`. The gallery that calls `use_preset` comes in Task 4.
- [ ] **Step 4: Run it**, then the whole of `tests/test_designer.py`. Expected: all PASS. `test_draw_components` is unchanged and must still pass.
- [ ] **Step 5: Commit.** Commit with the message "Editor: i blocchi restano modificabili (nota nel PDF, copia, sposta, annulla)".

### Task 4: Gallery of ready-made blocks

**Files:**
- Modify: `pdflex/designer.py`
- Test: `tests/test_designer_blocks.py`

**Interfaces:**
- Consumes: `blocks.PRESETS`, `blocks.preview`, `DesignTab.use_preset`, `last_drawn`.
- Produces: `class BlockGallery(QFrame)` with `buttons: dict[str, QToolButton]`. It opens with `Qt.Popup` next to the drawn rectangle (`tab.gallery.show_at(view_point)`). Each button shows `preview(preset(pid, rect), 96, 64)` as its icon and the label below. Clicking calls `use_preset(pid)` and closes it. Rettangolo, Esc or a click elsewhere just closes it.

- [ ] **Step 1: Write the failing test:**
  ```python
  def test_gallery_after_drawing_a_rectangle(qapp, tmp_path):
      tab = _tab(tmp_path); tab.show(); tab.set_mode("rect"); tab.drawn((100, 100, 400, 250))
      assert tab.gallery.isVisible() and list(tab.gallery.buttons) == [p for p, _ in bl.PRESETS]
      assert not tab.gallery.buttons["tabella"].icon().isNull()
      tab.gallery.buttons["griglia"].click()
      assert not tab.gallery.isVisible() and tab.blocks[0][1].header_col
  ```
- [ ] **Step 2: Run it.** Expected: FAIL (no `gallery`).
- [ ] **Step 3: Implement it.** `drawn()` opens the gallery after a rectangle of at least 20 × 12 pt. Smaller rectangles stay plain, with no gallery.
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit.** Commit with the message "Editor: modelli pronti dopo aver disegnato un rettangolo".

### Task 5: Block and cell panels, dividers

**Files:**
- Modify: `pdflex/designer.py`
- Test: `tests/test_designer_blocks.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces on `DesignTab`:
  - **Block panel** (shown when the selection is exactly one block): `p_rows`, `p_cols` (QSpinBox 1–60), `p_head_row`, `p_head_col` (`ui.Toggle`), `p_lines` (colour button like `p_color`, with "Nessuno"), `p_line_width` (QLineEdit), `p_text_size` (QLineEdit), `b_unblock` ("Separa blocco"). The existing colour, fill, thickness and corner rows change the block's `border`, `fill`, `border_width` and `radius`.
  - **A plain rectangle** shows `p_rows` and `p_cols` at 1. Changing either turns it into a block that keeps its colours: `border` and `lines` take its stroke colour, `fill` its fill.
  - **Cell mode:**
    - `enter_cells(entry, r, c)`, `pick_cell(r, c, add: bool)`, `leave_cells()`;
    - state in `self.cell_mode: tuple | None` (the block entry) and `self.picked: list[tuple[int, int]]`;
    - the cell panel has `p_cell_text` (QLineEdit), `b_merge` ("Unisci celle") and `b_split` ("Dividi cella");
    - a double-click on a block part enters, a click inside picks, Shift+click adds, and Esc or a click outside leaves.
  - **Dividers:** `class DividerItem(QGraphicsRectItem)` with `axis` ("cols" | "rows"), `index` and `drop_at(pos: float)` (PDF points) sits on each inner divider of the selected block. It is dragged along its axis, and on release calls `apply_block(entry, move_divider(...))`.
  - **Outer sides:** the block's four outer sides get a `DividerItem` too, with `index` -1 (left / top) or `len - 1` (right / bottom). Their `drop_at` moves that side, and the block is `scaled`: all rows and columns in proportion.

- [ ] **Step 1: Write the failing tests:**
  ```python
  def test_block_panel_rows_cols_and_header(qapp, tmp_path):
      tab = _block(tmp_path, "tabella"); tab.select(tab.blocks[0][2])
      assert tab.form.isRowVisible(tab.p_rows) and tab.p_cols.value() == 4
      tab.p_cols.setValue(6)
      assert len(tab.blocks[0][1].cols) == 6
      tab.p_head_col.setChecked(True)
      assert tab.blocks[0][1].header_col

  def test_split_a_plain_rectangle(qapp, tmp_path):
      tab = _tab(tmp_path); tab.set_mode("rect"); tab.drawn((100, 100, 300, 200)); tab.gallery.close()
      tab.select([tab.last_drawn]); tab.p_rows.setValue(3)
      assert len(tab.blocks) == 1 and len(tab.blocks[0][1].rows) == 3

  def test_cells_text_merge_split(qapp, tmp_path):
      tab = _block(tmp_path, "tabella"); e = tab.blocks[0]
      tab.enter_cells(e, 0, 1); tab.pick_cell(0, 2, add=True)
      tab.b_merge.click()
      assert tab.blocks[0][1].merges == [(0, 1, 0, 2)]
      tab.enter_cells(tab.blocks[0], 0, 1); tab.p_cell_text.setText("TOTALE"); tab.p_cell_text.editingFinished.emit()
      assert tab.blocks[0][1].texts["0,1"] == "TOTALE"
      tab.b_split.click()
      assert tab.blocks[0][1].merges == []

  def test_drag_divider(qapp, tmp_path):
      tab = _block(tmp_path, "tabella"); tab.show(); tab.select(tab.blocks[0][2])
      d = next(i for i in tab.scene.items() if isinstance(i, DividerItem) and i.axis == "cols" and i.index == 0)
      d.drop_at(130)                                              # what the mouse release calls, in PDF points
      assert tab.blocks[0][1].cols[0] * 300 == pytest.approx(30, abs=0.5)

  def test_drag_outer_side_scales(qapp, tmp_path):
      tab = _block(tmp_path, "tabella"); tab.show(); tab.select(tab.blocks[0][2])
      d = next(i for i in tab.scene.items() if isinstance(i, DividerItem) and i.axis == "cols" and i.index == 3)
      d.drop_at(700)                                              # right side from 400 to 700: twice as wide
      b = tab.blocks[0][1]
      assert b.rect[2] == pytest.approx(700, abs=0.5) and b.cols == pytest.approx([0.25] * 4)

  def test_unblock(qapp, tmp_path):
      tab = _block(tmp_path, "tabella"); tab.select(tab.blocks[0][2]); tab.b_unblock.click()
      assert tab.blocks == [] and len(tab.objects) > 5
  ```
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Implement it.**
  - The faint cell outlines and the picked highlight are scene items above the page (z 900), removed on `leave_cells`.
  - `_show_props` chooses the panel:
    - one block, not in cell mode: block panel;
    - cell mode: cell panel;
    - otherwise: as today.
- [ ] **Step 4: Run it.** Expected: PASS, and `tests/test_designer.py` stays green.
- [ ] **Step 5: Commit.** Commit with the message "Editor: righe, colonne, celle e divisori dei blocchi".

### Task 6: Snapping while drawing and resizing, size tag

**Files:**
- Modify: `pdflex/designer.py`
- Test: `tests/test_designer_blocks.py`

**Interfaces:**
- Consumes: `ui.snap_box(box, others, page, reach)` and `ui.draw_guides`. Snap a point by passing `QRectF(p, p)`, a zero-size box.
- **Produces:**
  - `PageView.mouseMoveEvent` snaps the band's moving corner while drawing. `ObjItem.mouseMoveEvent` snaps the moving sides while resizing. `DividerItem` snaps while dragged. Each shows the guides; Alt held means no snapping.
  - `self.size_tag` (QGraphicsSimpleTextItem, z 1001) shows `"{w:.0f} × {h:.0f} pt"` next to the mouse while drawing or resizing, and is hidden on release.
  - `start_drag()` also runs on the press that starts a drawing.

- [ ] **Step 1: Write the failing test:**
  ```python
  def test_drawing_snaps_to_an_edge(qapp, tmp_path):
      tab = _tab(tmp_path); tab.resize(1200, 900); tab.show(); qapp.processEvents()
      tab.set_mode("rect"); tab.drawn((300, 100, 400, 200)); tab.gallery.close()   # a box to catch
      tab.set_mode("rect")
      z, v = tab.zoom, tab.view
      to_view = lambda x, y: v.mapFromScene(x * z, y * z)
      start, end = to_view(100, 300), to_view(297.5, 350)        # 2.5 pt from the box's left edge
      _drag(v, start, end)
      new = max(tab.objects, key=lambda o: o.rect[1])
      assert new.rect[2] == pytest.approx(300, abs=0.3)

  def test_size_tag_while_drawing(qapp, tmp_path):
      ...  # press and move without releasing; assert tab.size_tag.isVisible() and "pt" in tab.size_tag.text()
  ```
  Write the second test in full in the same style: `QTest.mousePress`, then `QTest.mouseMove`, check, then release.
- [ ] **Step 2: Run it.** Expected: FAIL. The rectangle ends at 297.5, and there is no `size_tag`.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run it**, then `tests/test_designer.py`. Expected: PASS.
- [ ] **Step 5: Commit.** Commit with the message "Editor: allineamento magnetico anche mentre disegni e ridimensioni".

### Task 7: An icon on every Editor button

**Files:**
- Modify: `pdflex/ui.py` (new glyphs), `pdflex/designer.py`
- Test: `tests/test_designer_blocks.py`

**Interfaces:**
- **New glyph names in `ui.py`'s set:** `bold`, `italic`, `arrow-start`, `arrow-end`, `front`, `forward`, `backward`, `back`, `group`, `lock`, `turn`, `merge`, `split`, `unblock`, `palette`. The style matches the existing ones: 24×24, strokes only.
- **The buttons:**
  - every text-only button in `DesignTab` gets its glyph and keeps a short text where it fits;
  - Primo piano, Avanti, Indietro and In fondo become icon-only, with their tooltips;
  - the colour swatch buttons (`p_color`, `p_fill`, `p_lines`) show the `palette` glyph when the colour is "Nessuno".

- [ ] **Step 1: Write the failing test:**
  ```python
  def test_every_editor_button_has_an_icon(qapp, tmp_path):
      from PySide6.QtWidgets import QAbstractButton
      tab = _block(tmp_path, "tabella"); tab.select(tab.blocks[0][2])
      bare = [b.text() or b.toolTip() for b in tab.findChildren(QAbstractButton)
              if b.icon().isNull() and not isinstance(b, ui.Toggle)]
      assert bare == []
  ```
- [ ] **Step 2: Run it.** Expected: FAIL, listing the text-only buttons (Grassetto, Corsivo, All'inizio…).
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run it.** Expected: PASS.
- [ ] **Step 5: Commit.** Commit with the message "Editor: icone su tutti i pulsanti".

### Task 8: English texts, README, visual check

**Files:**
- Modify: `pdflex/i18n_en.py`, `README.md`
- Test: `tests/test_i18n.py` (the existing coverage test)

- [ ] **Step 1: Run the i18n test.** Run `.venv/Scripts/python -m pytest tests/test_i18n.py -q`. Expected: FAIL, listing the new strings.
- [ ] **Step 2: Add the English entries.**
  - **README:** in the "Editor" line, add blocks with rows and columns, ready-made pieces after drawing a rectangle, and guides while drawing and resizing.
- [ ] **Step 3: Run the whole suite.** Run `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`. Expected: all pass.
- [ ] **Step 4: Visual check.**
  - **Build the sample.** A scratch script (in the scratchpad, not in the repo) rebuilds on an A4 page:
    - the payslip's "Presenze": a griglia, 32 columns × 7 rows, with the 1–31 header and the row names;
    - the "CODICE / DESCRIZIONE / … / TOTALE" table: a tabella, with TOTALE merged over two columns;
    - it uses `blocks.draw` with the note written as `apply_block` writes it.
  - **Look at it.** Save it, render it to PNG with PyMuPDF, then look at the image with the Read tool. Also open the saved PDF in a `DesignTab`, and check both blocks are found and that changing the rows of one of them works.
  - **Record it.** Note what looks wrong in the ledger. A problem found here is a finding for the final review.
- [ ] **Step 5: Commit.** Commit with the message "Editor blocchi: testi in inglese e README".
