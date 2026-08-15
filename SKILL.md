---
name: kicad-harness
description: >
  Inspect, measure, render and edit KiCad boards and schematics from an agent.
  Gives you eyes (render any board region to PNG and look at it), hands (live
  IPC into a running KiCad -- no copy-pasting into the built-in console), and a
  verdict (DRC/ERC as compact JSON). Use for placement, layout review,
  positioning components, checking a board, or scripting schematic edits.
---

# KiCad Harness

You are working on a real KiCad project. This harness gives you three layers.
Reach for the lowest one that answers the question.

| Layer | Needs | Use it for |
|---|---|---|
| **libraries** | nothing | looking up real symbol/footprint ids and pin numbers |
| **offline** | nothing | positions, nets, bboxes, DRC, ERC, netlist, BOM |
| **visual** | nothing | *seeing* the layout — verify placement actually looks right |
| **live** | API server on | editing a board that is open in KiCad, right now |

## Never invent a library identifier

`Device:LED_Generic`, `LED_SMD:LED_0805_Generic`, `Capacitor_SMD:C_0805` are all
things a confident model writes and none of them exist. A netlist built from
remembered ids fails later, inside KiCad, with an error that does not point at
the cause.

**Look every part up. Every time.**

```bash
kh sym TP4056                        # -> Battery_Management:TP4056-42-ESOP8
kh fp "USB_C receptacle"             # search footprints
kh sym --pins Battery_Management:TP4056-42-ESOP8   # real pin numbers and types
```

`--pins` is what a netlist actually needs: pin *numbers*, names, electrical
types, the symbol's `default_footprint`, and its `footprint_filters` (which tell
you which footprints the symbol is meant to pair with). Pin numbering is exactly
where memory fails — the TP4056 has a 9th EPAD pin that is easy to forget, and
getting a pin number wrong produces a board that looks fine and does not work.

Before writing any netlist, confirm the whole set at once:

```bash
kh validate --symbols "Device:R,Device:C,Battery_Management:TP4056-42-ESOP8" \
            --footprints "Resistor_SMD:R_0805_2012Metric"
```

Exit code is non-zero if anything is wrong, and each bad id comes back with
suggestions. Pass `--project <dir>` so project-local library tables are included.

This reads the user's **actual** libraries — global tables, nested tables, and
any custom libraries they have installed — not a built-in list.

Run everything through `kh` (or `python -m kicad_harness`). Every command emits
JSON on stdout.

## The loop that matters

When you place or move anything, **always close the loop by looking at it.**
Do not report a placement as done until you have rendered it and read the image.

```bash
kh ls --pcb <proj> --filter Inductor        # 1. find the parts and their bboxes
kh exec place_filter.py                     # 2. move them (live, undoable)
kh view --pcb <proj> --refs L1,C1,C2 \
        --margin 3 --out /tmp/check.png     # 3. render the result
# 4. Read /tmp/check.png  <-- actually look at it
kh drc --pcb <proj>                         # 5. confirm nothing broke
```

Step 4 is the point of this harness. A placement script that runs without error
can still be visibly wrong — overlapping courtyards, parts rotated 90° off,
a filter laid out in the wrong order. Only the image catches that.

## Seeing the board

```bash
kh view --refs L1,L2,C3 --margin 3 --out v.png   # auto-frame those parts
kh view --region 45,55,20,20 --out v.png         # explicit mm window: x,y,w,h
kh view --layers courtyard --refs U1 --out v.png # check for overlap
kh view --layers back --out back.png             # whole board, bottom
```

Layer presets: `front`, `front-clean`, `back`, `copper`, `both`, `outline`,
`assembly`, `courtyard`. Or pass a raw KiCad layer list.

Coordinates are millimetres in KiCad page space, **y grows downward**. The
`region_mm` and `mm_per_px` in the output tell you the scale you are looking at.

Renders read the **last-saved file**. If the board is open in KiCad with unsaved
edits, save first (or call `board.save()` over the live layer) or you will be
looking at stale geometry. This is the single most common mistake — when an
image does not match what you just did, this is why.

## Editing a running KiCad (live layer)

Check the connection first:

```bash
kh live      # exit 0 and "connected": true means you are good
```

If it fails it prints exactly what the user must do (Preferences → Plugins →
Enable KiCad API). Ask them to do it; you cannot toggle it yourself.

Then write a normal Python file and run it. It executes in-process, so `print()`
and tracebacks come straight back to you:

```bash
kh exec my_edit.py
```

Inside the script these are already bound: `kicad`, `board`, and `sch` (which is
`None` — see below). Wrap edits in a commit so they land as one undo step and
roll back on error:

```python
from kicad_harness.live import Commit, footprints_by_ref
from kipy.geometry import Vector2

fps = footprints_by_ref(board)
with Commit(board, "place LC filter"):
    for i, ref in enumerate(["L1", "C1", "L2", "C2"]):
        f = fps[ref]
        f.position = Vector2.from_xy_mm(50.0 + i * 3.0, 60.0)
        board.update_items(f)
result = "placed 4 parts"               # `result` is returned to the caller
```

**Units in the live API are nanometres** (`1 mm == 1_000_000`), while the offline
layer (`kh ls`, `kh view`) speaks millimetres. Never hand-convert: use
`Vector2.from_xy_mm(...)`, and `kipy.util.units.from_mm` / `to_mm` for scalars.
Unit confusion is the most common way a placement script silently flings parts
a metre off the page.

See `docs/LIVE_API.md` for the full object model and `docs/RECIPES.md` for
worked examples.

## Checking

```bash
kh drc  --pcb <proj>                # errors, grouped by rule type
kh drc  --severity all --limit 10   # everything, more samples each
kh erc  --sch <proj>
kh netlist --sch <proj> --out n.net
```

Output is grouped by violation type with a bounded sample per type, because a
board with 400 unconnected pads produces a report you cannot read otherwise.
`total` is the true count; `truncated` says whether you are seeing all of them.

## Generating footprints

Footprint generation from datasheets is a separate concern — use the
`kicad-footprint-generate` skill for that. This harness is about working with a
project that already exists.

## What is not available

- **No schematic editing.** kipy ships schematic wrapper classes, but the
  protobufs behind them are missing and its schematic command proto is empty, so
  `import kipy.schematic` fails and `sch` is always `None`. Do not spend time
  trying to make it work. Treat schematics as **files**: `kh netlist` for
  connectivity, `kh erc` for problems, and edit `.kicad_sch` s-expressions as
  text. `kh live` reports `schematic_api: false`.
- **No ratsnest in renders.** SVG export omits airwires, so you cannot see
  unrouted connections in an image. Use `kh drc` — the `unconnected` section
  lists them with coordinates.
- **No autorouter in KiCad**, and no route-this-net call. You can place, and you
  can draw tracks explicitly over the live API. For actual autorouting, the
  Specctra bridge (`pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES`) drives an
  external router headlessly — see `docs/CAPABILITIES.md`.
- **No GUI screenshots.** `kh view` renders from the file instead, which is
  better: exact, headless, and you choose the layers and window.
