---
name: kicad-harness
description: >
  Inspect, measure, render and edit KiCad boards and schematics from an agent.
  Gives you eyes (render any board region to PNG and look at it), hands (live
  IPC into a running KiCad -- no copy-pasting into the built-in console), and a
  verdict (DRC/ERC as compact JSON). Also generates a footprint from a datasheet
  (image, PDF or dimensions) when no library has the part. Use for placement,
  layout review, positioning components, checking a board, scripting schematic
  edits, or making a footprint.
---

# KiCad Harness

## Improve this skill as you use it

**This skill is a git repo, and you are expected to edit it.** Every time you
find something here that is wrong, missing, or out of date — a command that does
not behave as documented, a KiCad behaviour nobody wrote down, a bug in
`kicad_harness/` — **fix it in the repo, then commit and push, in this same
session.** Not a note in your reply; a commit.

```bash
git -C <this repo> add <the files you changed>
git -C <this repo> commit -m "sview: sub-sheets render blank without --all"
git -C <this repo> push
```

You have standing permission from the repo owner to commit and push these
fixes without asking. If you cannot push (a fork, no credentials), commit on a
branch and say a PR is ready. Never commit the user's board, schematic, renders
or netlists — only this repo's own files.

Findings about measured KiCad behaviour are the most valuable thing you can
leave behind, because the next agent cannot guess them from source. Where each
kind of finding belongs, and what not to commit: `docs/SELF_ITERATION.md`.
Something broken you did not fix goes in `ISSUES.md`.

---

You are working on a real KiCad project. This harness gives you these layers.
Reach for the lowest one that answers the question.

| Layer | Needs | Use it for |
|---|---|---|
| **libraries** | nothing | looking up real symbol/footprint ids and pin numbers |
| **offline** | nothing | positions, nets, bboxes, DRC, ERC, netlist, BOM |
| **visual** | nothing | *seeing* the layout — verify placement actually looks right |
| **live** | API server on | editing a board that is open in KiCad, right now |
| **footprints** | nothing | making the part when no library has it — see below |

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
types, each pin's connection point, the symbol's `default_footprint`, and its
`footprint_filters` (which tell you which footprints the symbol is meant to pair
with). Pin numbering is exactly where memory fails — the TP4056 has a 9th EPAD
pin that is easy to forget, and getting a pin number wrong produces a board that
looks fine and does not work.

A pad number is **not unique**, either: that EPAD is 8 pads all numbered `9`,
and a USB-C shield is 4 pads all numbered `SH`. Anything that assigns a net by
pad number has to assign it to all of them.

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

## Schematic to board, with no GUI

There is no schematic API (see below), so a schematic is a **file** you write.
`kh sym --sexpr Lib:Name` hands back the raw symbol body for the file's
`lib_symbols` block, and `kh sym --pins` gives the pin coordinates the wires
have to land on. Library coordinates are y-up, the sheet is y-down, and
connectivity is decided purely by coordinates — `docs/RECIPES.md` has the
transform for all four rotations and the junction rule.

From a schematic, the rest is headless:

```bash
kh erc --sch .                                   # electrical check
kh sview --sch . --all --out /tmp/sch.png        # render every sheet -- then look
kh netlist --sch . --out n.net                   # read it: are these the nets you meant?
kh board-from-netlist --sch . --outline 20,20,38,26.5 \
    --clearance 0.125 --min-drill 0.2            # -> .kicad_pcb, parts + nets
kh place --pcb . --json placement.json           # place, without KiCad running
kh view --pcb . --out /tmp/b.png                 # -> look
kh drc --pcb . --severity all
```

`board-from-netlist` is the step KiCad itself does not expose headlessly:
`kicad-cli pcb import` only reads foreign CAD, and pcbnew's netlist import is a
GUI action. It reports `unmatched_pads` — a pin the netlist names that the
footprint does not have — which is the symbol/footprint mismatch that otherwise
surfaces much later as a mystery.

On a hierarchical design `kh sview` defaults to the **root** sheet, which is
usually just one box per child — the parts are on the children. Name one with
`--sheet`, or get the lot with `--all`. `--region x,y,w,h` zooms in sheet
millimetres, the same numbers that are in the file:

```bash
kh sview --sch . --all --out /tmp/s.png              # -> /tmp/s-<SheetName>.png
kh sview --sch . --sheet PowerSupply --out /tmp/p.png
kh sview --sch . --sheet Power --region 130,112,102,43 --out /tmp/zoom.png
```

**A clean ERC does not mean the circuit is right.** ERC checks pin types, not
intent. Read the netlist and confirm the nets are the ones you meant.

**Changing a schematic a human already drew** is a different job from writing
one — clone existing symbols instead of composing them, keep uuids, watch that
property angles are relative to the symbol, and diff against a baseline netlist
rather than against perfection. `docs/SCHEMATIC_EDITS.md` has the whole
procedure.

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
edits, save first or you will be looking at stale geometry. This is the single
most common mistake — when an image does not match what you just did, this is why.

**Do not reach for `board.save()` to do that saving.** The live save
round-trips the board through the API model, and item types the model does not
carry are dropped on the way out — observed on KiCad 10.0.5 to delete every
**locked** graphic and every User-layer construction line on a real board
(17 `gr_line`, 1 `gr_poly`, 2 `gr_text`), while the footprint moves that were
requested went unwritten and KiCad became unreachable straight afterwards. The
loss is silent: no error, and the file is well-formed when it lands.

**`update_items` is lossy in the same way**, so Ctrl+S does not make you safe —
it only moves the question to *what you pushed*. Setting one field on 132
footprints deleted the `(units ...)` block from all 132 (KiCad 10.0.6). Push as
few items as the job needs, and prove no damage by **diffing the text** against
the newest `<project>-backups/` zip — counting pads and graphics per footprint
looks clean while whole blocks are missing. Details and the repair procedure:
[docs/LIVE_API.md](docs/LIVE_API.md#editing-footprint-fields).

Ask the user to press Ctrl+S instead. If you did call it, check immediately:

```bash
git diff --stat
grep -c 'gr_line\|gr_poly\|gr_text' board.kicad_pcb    # against the old count
```

`git checkout --` the board, or fall back to the zips in `<project>-backups/`.
For moving parts, prefer the offline `kh place --pcb <dir> --set REF=x,y,rot`,
which rewrites only the footprint's `(at ...)` line and never loads the rest of
the file into a lossy model.

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

## Making a footprint the libraries do not have

`kh fp` came back with nothing usable, or the user handed you a datasheet: build
the part. The output is a **hardcoded** KiCad Footprint Wizard script — every
dimension baked in, nothing for the user to configure, runnable as-is — plus the
`.kicad_mod` it produces, exported into a `.pretty` library so they are not
forced to run the wizard themselves.

The full procedure is `docs/footprints/GUIDE.md`. What decides the outcome:

**1. Measure the drawing; do not eyeball it.** Render the datasheet at 600 dpi,
scan for stroke centres, derive mm-per-pixel from one printed dimension, then
cross-validate that scale against every other printed dimension. Prefer the
"RECOMMENDED P.C.B. LAYOUT" page over mechanical views — they dimension from
different datums and will not reconcile. `docs/footprints/MEASUREMENT.md`.

**2. Pick a blueprint, or admit there isn't one.** `templates/` holds the
official KiCad wizards — `qfp_wizard.py` for peripheral pins, `bga_wizard.py`
for grid arrays, `PadArray.py` for the coordinate math. Irregular land patterns
(USB-C, card edges, most connectors) fit **no** template: non-uniform pitch,
mixed pad widths, mechanical anchors. Write an explicit pad table instead of
bending `PadArray` around them. `docs/footprints/API_REFERENCE.md`.

**3. Verify by looking, not by exiting 0.** A syntax check and a
`BuildFootprint()` lifecycle run only prove the script *runs*. Dump every pad in
mm, then **overlay the generated pads back onto the datasheet image** using the
pixel mapping from step 1 and read the picture — the same rule as the placement
loop above. `docs/footprints/VERIFICATION.md`.

**4. Deliver honestly.** State the orientation and origin, list every dimension
you **inferred** rather than read, and flag whatever the fab should check.
`docs/footprints/ENVIRONMENT.md` covers where the script and the library go.

Then carry on with the rest of this skill: `kh validate --footprints <lib:name>`
confirms KiCad resolves the new id, and `kh view --refs <ref>` renders it in
place next to its neighbours.

## What is not available

- **No schematic editing API.** kipy ships schematic wrapper classes, but the
  protobufs behind them are missing and its schematic command proto is empty, so
  `import kipy.schematic` fails and `sch` is always `None`. Do not spend time
  trying to make it work. Treat schematics as **files**: `kh netlist` for
  connectivity, `kh erc` for problems, `kh sview` to look, and write/edit
  `.kicad_sch` s-expressions as text. `kh live` reports `schematic_api: false`.
- **The live layer needs an editor window, not just a running KiCad.** With only
  the project manager open, the socket answers `ping` but every document request
  comes back "no handler available". `kh live` reports `editor_open: false` and
  exits non-zero; everything offline still works.
- **No ratsnest in renders.** SVG export omits airwires, so you cannot see
  unrouted connections in an image. Use `kh drc` — the `unconnected` section
  lists them with coordinates.
- **No autorouter in KiCad**, and no route-this-net call. You can place, and you
  can draw tracks explicitly over the live API. For actual autorouting, the
  Specctra bridge (`pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES`) drives an
  external router headlessly — see `docs/CAPABILITIES.md`.
- **No GUI screenshots.** `kh view` renders from the file instead, which is
  better: exact, headless, and you choose the layers and window.
