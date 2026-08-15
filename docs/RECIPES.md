# Recipes

## Place an LC ladder filter, then verify it

The signal path in a ladder filter should be physically monotonic — series parts
in a row, shunt parts hanging off the line. Getting this right matters at RF and
is exactly the kind of thing that is easy to script and easy to get subtly wrong.

`examples/place_lc_ladder.py`:

```python
"""Lay out a ladder filter left to right. Run with: kh exec place_lc_ladder.py"""
from kicad_harness.live import Commit, footprints_by_ref
from kipy.geometry import Vector2, Angle

SERIES = ["L1", "L2", "L3", "L4"]      # in signal order
SHUNT  = ["C1", "C2", "C3"]            # between successive series parts

X0, Y0 = 50.0, 60.0                    # mm, left end of the line
PITCH  = 6.0                           # mm between series parts
DROP   = 3.5                           # mm the shunt parts sit below the line

fps = footprints_by_ref(board)
missing = [r for r in SERIES + SHUNT if r not in fps]
if missing:
    raise SystemExit(f"not on this board: {missing}")

placed = {}
with Commit(board, "place ladder filter"):
    for i, ref in enumerate(SERIES):
        fp = fps[ref]
        fp.position = Vector2.from_xy_mm(X0 + i * PITCH, Y0)
        fp.orientation = Angle.from_degrees(0)
        board.update_items(fp)
        placed[ref] = (X0 + i * PITCH, Y0)

    for i, ref in enumerate(SHUNT):
        x = X0 + i * PITCH + PITCH / 2      # between series parts i and i+1
        fp = fps[ref]
        fp.position = Vector2.from_xy_mm(x, Y0 + DROP)
        fp.orientation = Angle.from_degrees(90)   # vertical, feeding ground
        board.update_items(fp)
        placed[ref] = (x, Y0 + DROP)

board.save()          # so the renderer sees it
result = placed
```

Then **look at it** — this is not optional:

```bash
kh exec examples/place_lc_ladder.py
kh view --refs L1,L2,L3,L4,C1,C2,C3 --margin 3 --no-square --out /tmp/lc.png
# read /tmp/lc.png
kh drc --limit 3
```

Read the PNG. Check that series parts are collinear, shunts alternate below,
nothing overlaps, and rotations are what you intended. Then check DRC for new
courtyard violations.

## Find where a subcircuit currently lives

```bash
kh ls --filter Inductor          # refs, positions, bboxes, rotations
kh nets                          # all net names
kh nets --net GND                # every pad on GND, with coordinates
```

`kh ls` bboxes are what `kh view --refs` frames on, so you can predict the window.

## Check courtyard overlap around a part

```bash
kh view --refs U1 --margin 6 --layers courtyard --out /tmp/cy.png
```

Overlapping courtyard outlines are immediately visible. `kh drc` reports them
formally as `courtyards_overlap`.

## Compare before and after

```bash
kh view --refs L1,L2,L3,L4 --out /tmp/before.png
kh exec my_change.py
kh view --refs L1,L2,L3,L4 --out /tmp/after.png
```

Render both with the same `--region` (not `--refs`) if you want a fixed window —
`--refs` reframes as parts move, which hides the movement you are trying to see.

## Align a row of parts to a grid

```python
from kicad_harness.live import Commit, footprints_by_ref
from kipy.geometry import Vector2

GRID = 0.5   # mm
fps = footprints_by_ref(board)
with Commit(board, "snap to grid"):
    for ref in ["R1", "R2", "R3"]:
        fp = fps[ref]
        x = round(fp.position.x / 1e6 / GRID) * GRID
        y = round(fp.position.y / 1e6 / GRID) * GRID
        fp.position = Vector2.from_xy_mm(x, y)
        board.update_items(fp)
```

## Read the schematic without an API

```bash
kh netlist --out /tmp/n.net       # connectivity, s-expression
kh erc --limit 10                 # what is wrong with it
```

For structure, `.kicad_sch` is plain s-expression text — readable directly. For
*editing* a schematic, use the live layer (`sch.create_items` / `update_items`);
see `LIVE_API.md`.

## Whole-board review pass

```bash
kh info                                   # size, part count, layer count
kh view --layers front --width 1600 --out /tmp/f.png
kh view --layers back  --width 1600 --out /tmp/b.png
kh drc --severity all --limit 5
kh erc --severity all --limit 5
```

Read both images, then the two reports. The images catch placement and spacing
problems; the reports catch everything geometric and electrical.
