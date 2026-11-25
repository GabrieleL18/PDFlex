"""Safety copies of unsaved work, so a crash loses nothing: written every few seconds while something is
unsaved (and right before the risky steps), deleted when PDFlex closes normally. Found at the next start:
PDFlex offers to pick up from there."""
import dataclasses
import json
import os
from pathlib import Path

from pdflex import ui, write_atomic
from pdflex.fields import Field


def folder() -> Path:
    return ui.DATA_DIR / "ripristino"  # through ui: the tests move DATA_DIR to a temp folder


def _stamp(path: str) -> list:
    st = os.stat(path)
    return [st.st_mtime_ns, st.st_size]


def save_editor(path: str, pdf: bytes) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    write_atomic(folder() / "editor.pdf", pdf)
    (folder() / "editor.json").write_text(json.dumps({"path": path}), encoding="utf-8")


def save_forms(path: str, fields: list[Field]) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    data = {"path": path, "stamp": _stamp(path),
            "fields": [{k.name: getattr(f, k.name) for k in dataclasses.fields(Field) if k.name != "orig"}
                       for f in fields]}
    write_atomic(folder() / "moduli.json", json.dumps(data).encode("utf-8"))


def clear(name: str | None = None) -> None:
    """One tab's copy ("editor" / "moduli"), or all of them."""
    for p in folder().glob(f"{name or '*'}.*"):
        p.unlink(missing_ok=True)


def pending() -> dict:
    """What a crash left: {"editor": (original path, copy of the PDF), "moduli": (path, fields data)}.
    The fields only while their PDF is as it was: they point at its widgets."""
    found = {}
    try:
        ed = json.loads((folder() / "editor.json").read_text(encoding="utf-8"))
        if (folder() / "editor.pdf").exists():
            found["editor"] = (ed["path"], folder() / "editor.pdf")
    except (OSError, ValueError, KeyError):
        pass
    try:
        mo = json.loads((folder() / "moduli.json").read_text(encoding="utf-8"))
        if _stamp(mo["path"]) == mo["stamp"]:
            found["moduli"] = (mo["path"], mo["fields"])
    except (OSError, ValueError, KeyError):
        pass
    return found


def fields_from(data: list[dict], loaded: list[Field]) -> list[Field]:
    """The saved fields back as Field objects; the ones from the PDF get their original again,
    so saving changes only what was changed."""
    origs = {f.xref: f.orig for f in loaded if f.xref}
    out = []
    for d in data:
        f = Field(**{**d, "rect": tuple(d["rect"])})
        f.orig = origs.get(f.xref)
        if f.orig is None:
            f.xref = f.parent = 0  # its widget is not there any more: a new field
        out.append(f)
    return out
