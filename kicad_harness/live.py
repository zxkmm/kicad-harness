"""Live IPC into a *running* KiCad, via kipy.

This is the layer that replaces pasting code into KiCad's built-in console.
Scripts run in this process, so stdout and tracebacks come straight back to
whoever called them.

Requires the API server: KiCad -> Preferences -> Plugins -> "Enable KiCad API".
"""

from __future__ import annotations

import os
import runpy
from typing import Any, Optional

ENABLE_HINT = (
    "KiCad's IPC API server is not reachable.\n"
    "  1. In KiCad: Preferences -> Plugins -> tick 'Enable KiCad API'\n"
    "  2. Keep a project open (the API serves whatever documents are open)\n"
    "  3. Re-run. If it still fails, restart KiCad so the server binds its socket."
)

NO_EDITOR_HINT = (
    "The API server is up and answering ping, but no editor window is open, so "
    "there is nothing to talk to: document requests come back 'no handler "
    "available'. The project manager alone does not serve them.\n"
    "Open the board in the PCB editor (or the schematic in Eeschema) and re-run. "
    "Everything that does not need a running editor -- kh sym / fp / validate, "
    "view, drc, erc, netlist, board-from-netlist, place -- works regardless."
)


def _import_kipy():
    try:
        import kipy  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "kipy is not installed. Install with: pip install kicad-python"
        ) from e
    return kipy


def connect(timeout_ms: int = 5000):
    """Return a connected kipy.KiCad, or raise with an actionable message."""
    kipy = _import_kipy()
    try:
        kicad = kipy.KiCad(timeout_ms=timeout_ms)
        kicad.ping()
    except Exception as e:
        raise ConnectionError(f"{ENABLE_HINT}\n\nunderlying error: {e}") from None
    return kicad


def status() -> dict:
    """Report whether the live layer is usable, without raising."""
    info: dict[str, Any] = {
        "socket_env": os.environ.get("KICAD_API_SOCKET"),
        "connected": False,
    }
    try:
        _import_kipy()
        info["kipy"] = True
    except RuntimeError as e:
        info["kipy"] = False
        info["error"] = str(e)
        return info

    try:
        kicad = connect()
    except ConnectionError as e:
        info["error"] = str(e)
        return info

    from kipy.proto.common.types import DocumentType

    info["connected"] = True
    info["kicad_version"] = str(kicad.get_version())
    info["api_version"] = str(kicad.get_api_version())

    # Listing documents fails outright when KiCad is running with no editor
    # window -- the request has no handler at all, which surfaces as an
    # ApiError, not an empty list. Report that as the actionable state it is
    # rather than letting it escape from a function documented not to raise.
    def _docs(kind):
        return [d.board_filename for d in kicad.get_open_documents(kind)]

    try:
        info["open_boards"] = _docs(DocumentType.DOCTYPE_PCB)
        info["open_schematics"] = _docs(DocumentType.DOCTYPE_SCHEMATIC)
        info["editor_open"] = True
    except Exception as e:
        info["editor_open"] = False
        info["open_boards"] = []
        info["open_schematics"] = []
        info["error"] = f"{NO_EDITOR_HINT}\n\nunderlying error: {e}"
    # Documents are listed even when the schematic API itself is unusable.
    info["schematic_api"] = schematic_supported()
    return info


def get_board(kicad=None):
    """The PCB currently open in KiCad."""
    kicad = kicad or connect()
    return kicad.get_board()


SCHEMATIC_UNAVAILABLE = (
    "The schematic API is not usable on this KiCad/kipy combination.\n"
    "kipy ships hand-written schematic wrappers (kipy/schematic.py) that import "
    "protobuf symbols its own generated modules do not contain -- "
    "schematic_commands_pb2 is empty, so there are no schematic commands on the "
    "wire at all. It is unreleased work staged ahead of the protos.\n"
    "Verified broken on KiCad 10.0.5 + kicad-python 0.7.1 (latest as of 2026-08).\n"
    "Until a release fixes it: read schematics with `kh netlist` / `kh erc` or by "
    "parsing .kicad_sch directly, and edit them as text."
)


def schematic_supported() -> bool:
    """True if kipy's schematic module actually imports on this install."""
    try:
        import kipy.schematic  # noqa: F401
    except Exception:
        return False
    return True


def get_schematic(kicad=None, index: int = 0):
    """The schematic currently open in KiCad, if this kipy build supports it.

    kipy has no get_schematic() helper, so build one from the open-document list.
    """
    kicad = kicad or connect()
    from kipy.proto.common.types import DocumentType

    try:
        from kipy.schematic import Schematic
    except ImportError as e:
        raise RuntimeError(f"{SCHEMATIC_UNAVAILABLE}\n\nimport error: {e}") from None

    docs = kicad.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
    if not docs:
        raise RuntimeError("no schematic is open in KiCad")
    return Schematic(kicad._client, docs[index])


def run_script(path: str, argv: Optional[list[str]] = None) -> dict:
    """Execute a Python file with a live session pre-bound.

    The script sees these globals already set up:
        kicad  -- kipy.KiCad
        board  -- the open Board, or None if none is open
        sch    -- the open Schematic, or None if none is open

    Anything the script leaves in a global named `result` is returned.
    """
    import sys

    kicad = connect()

    board = None
    sch = None
    try:
        board = kicad.get_board()
    except Exception as e:
        # Distinguish "no board open" from "no editor at all". The second used
        # to surface as `board is None` and then an AttributeError deep inside
        # the user's script, which points nowhere near the cause.
        if "no handler available" in str(e):
            raise ConnectionError(f"{NO_EDITOR_HINT}\n\nunderlying error: {e}") from None
    try:
        sch = get_schematic(kicad)
    except Exception:
        pass

    old_argv = sys.argv
    sys.argv = [path] + list(argv or [])
    try:
        glb = runpy.run_path(
            path, init_globals={"kicad": kicad, "board": board, "sch": sch}
        )
    finally:
        sys.argv = old_argv

    return {"result": glb.get("result")}


def ref_of(footprint) -> str:
    """Reference designator of a live FootprintInstance.

    The reference lives in a nested text field; kipy has shuffled the exact
    path between versions, so probe rather than assume.
    """
    field = footprint.reference_field
    for path in ("text.value", "text.text", "value"):
        obj = field
        try:
            for part in path.split("."):
                obj = getattr(obj, part)
        except AttributeError:
            continue
        if isinstance(obj, str):
            return obj
    return str(field)


def footprints_by_ref(board) -> dict:
    """{'R1': FootprintInstance, ...} for the live board."""
    return {ref_of(f): f for f in board.get_footprints()}


class Commit:
    """Context manager for an undoable edit.

    Everything inside becomes a single entry in KiCad's undo stack; an exception
    rolls the whole thing back instead of leaving the document half-edited.

        with Commit(board, "place decoupling caps"):
            ...
    """

    def __init__(self, doc, message: str = "kicad-harness edit"):
        self.doc = doc
        self.message = message
        self.commit = None

    def __enter__(self):
        self.commit = self.doc.begin_commit()
        return self.commit

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.doc.push_commit(self.commit, self.message)
        else:
            self.doc.drop_commit(self.commit)
        return False
