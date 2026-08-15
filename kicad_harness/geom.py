"""Geometry queries against a .kicad_pcb on disk, via the pcbnew module.

Read-only. Safe to run while the board is open in the editor -- it reads the
last-saved file, which is the same thing kicad-cli sees.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import dataclass, asdict
from typing import Iterable, Sequence


@contextlib.contextmanager
def _quiet():
    """pcbnew spews wxWidgets property asserts to stderr on import. Swallow them."""
    fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(fd, 2)
        os.close(devnull)
        os.close(fd)


with _quiet():
    import pcbnew  # noqa: E402


def _mm(v) -> float:
    return round(pcbnew.ToMM(v), 4)


@dataclass
class Box:
    """Axis-aligned box in millimetres, KiCad page coordinates (y grows downward)."""

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def grown(self, margin: float) -> "Box":
        return Box(self.x - margin, self.y - margin, self.w + 2 * margin, self.h + 2 * margin)

    def union(self, other: "Box") -> "Box":
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        x1 = max(self.x + self.w, other.x + other.w)
        y1 = max(self.y + self.h, other.y + other.h)
        return Box(x0, y0, x1 - x0, y1 - y0)

    def squared(self) -> "Box":
        """Expand the short side so the box is square -- keeps rendered aspect honest."""
        side = max(self.w, self.h)
        return Box(self.cx - side / 2, self.cy - side / 2, side, side)

    def as_dict(self) -> dict:
        return asdict(self)


def _box(bb) -> Box:
    return Box(_mm(bb.GetLeft()), _mm(bb.GetTop()), _mm(bb.GetWidth()), _mm(bb.GetHeight()))


@dataclass
class Component:
    ref: str
    value: str
    footprint: str
    layer: str
    x: float
    y: float
    rotation: float
    bbox: Box
    locked: bool

    def as_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = self.bbox.as_dict()
        return d


class BoardView:
    """A loaded board. Construct once, query many times."""

    def __init__(self, pcb_path: str):
        self.path = os.path.abspath(pcb_path)
        if not os.path.exists(self.path):
            raise FileNotFoundError(self.path)
        with _quiet():
            self._board = pcbnew.LoadBoard(self.path)

    # -- components -------------------------------------------------------

    def components(self) -> list[Component]:
        out = []
        with _quiet():
            for f in self._board.GetFootprints():
                pos = f.GetPosition()
                out.append(
                    Component(
                        ref=f.GetReference(),
                        value=f.GetValue(),
                        footprint=f.GetFPIDAsString(),
                        layer=self._board.GetLayerName(f.GetLayer()),
                        x=_mm(pos.x),
                        y=_mm(pos.y),
                        rotation=f.GetOrientationDegrees(),
                        bbox=_box(f.GetBoundingBox(False, False)),
                        locked=f.IsLocked(),
                    )
                )
        out.sort(key=lambda c: c.ref)
        return out

    def find(self, refs: Iterable[str]) -> list[Component]:
        """Look up components by reference. Raises if any ref is unknown."""
        wanted = list(refs)
        by_ref = {c.ref: c for c in self.components()}
        missing = [r for r in wanted if r not in by_ref]
        if missing:
            raise KeyError(f"no such component(s): {', '.join(missing)}")
        return [by_ref[r] for r in wanted]

    # -- extents ----------------------------------------------------------

    def board_box(self) -> Box:
        """Bounding box of Edge.Cuts. Falls back to everything if there is no outline."""
        with _quiet():
            bb = self._board.GetBoardEdgesBoundingBox()
            if bb.GetWidth() <= 0 or bb.GetHeight() <= 0:
                bb = self._board.GetBoundingBox()
        return _box(bb)

    def box_of(self, refs: Sequence[str], margin: float = 2.0) -> Box:
        comps = self.find(refs)
        box = comps[0].bbox
        for c in comps[1:]:
            box = box.union(c.bbox)
        return box.grown(margin)

    # -- nets -------------------------------------------------------------

    def nets(self) -> list[str]:
        with _quiet():
            # keys come back as wxString, which has no ordering against itself
            names = [str(n) for n in self._board.GetNetsByName().keys()]
        return sorted(n for n in names if n)

    def pads_on_net(self, net: str) -> list[dict]:
        out = []
        with _quiet():
            for p in self._board.GetPads():
                if p.GetNetname() != net:
                    continue
                pos = p.GetPosition()
                fp = p.GetParentFootprint()
                out.append(
                    {
                        "ref": fp.GetReference() if fp else None,
                        "pad": p.GetNumber(),
                        "x": _mm(pos.x),
                        "y": _mm(pos.y),
                    }
                )
        return out

    def summary(self) -> dict:
        comps = self.components()
        box = self.board_box()
        with _quiet():
            layers = self._board.GetCopperLayerCount()
            tracks = len(list(self._board.GetTracks()))
        return {
            "path": self.path,
            "components": len(comps),
            "nets": len(self.nets()),
            "copper_layers": layers,
            "track_segments": tracks,
            "board_box_mm": box.as_dict(),
        }
