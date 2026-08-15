"""Lay out a ladder filter left to right.

Run with:  kh exec examples/place_lc_ladder.py

Edit SERIES / SHUNT / geometry below to match your filter, then verify with:
  kh view --refs L1,L2,L3,L4,C1,C2,C3 --margin 3 --no-square --out /tmp/lc.png
"""

from kicad_harness.live import Commit, footprints_by_ref
from kipy.geometry import Vector2, Angle

SERIES = ["L1", "L2", "L3", "L4"]   # series elements, in signal order
SHUNT = ["C1", "C2", "C3"]          # shunt elements, sitting between them

X0, Y0 = 50.0, 60.0                 # mm, left end of the signal line
PITCH = 6.0                         # mm between series parts
DROP = 3.5                          # mm the shunt parts sit below the line

fps = footprints_by_ref(board)  # noqa: F821 -- injected by `kh exec`

missing = [r for r in SERIES + SHUNT if r not in fps]
if missing:
    raise SystemExit(f"not on this board: {missing}")

placed = {}

with Commit(board, "place ladder filter"):  # noqa: F821
    for i, ref in enumerate(SERIES):
        fp = fps[ref]
        fp.position = Vector2.from_xy_mm(X0 + i * PITCH, Y0)
        fp.orientation = Angle.from_degrees(0)
        board.update_items(fp)  # noqa: F821
        placed[ref] = [X0 + i * PITCH, Y0]

    for i, ref in enumerate(SHUNT):
        x = X0 + i * PITCH + PITCH / 2      # midway between series parts
        fp = fps[ref]
        fp.position = Vector2.from_xy_mm(x, Y0 + DROP)
        fp.orientation = Angle.from_degrees(90)   # vertical, feeding ground
        board.update_items(fp)  # noqa: F821
        placed[ref] = [x, Y0 + DROP]

board.save()  # noqa: F821 -- so the offline renderer sees the new positions

result = placed
