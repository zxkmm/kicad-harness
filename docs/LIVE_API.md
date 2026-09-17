# The live layer (kipy IPC)

Everything here needs **Preferences → Plugins → Enable KiCad API** and a project
open in KiCad. Check with `kh live`.

## Running code

Write a normal Python file and run `kh exec script.py`. It runs in-process:
`print()` goes to your terminal, exceptions give you a real traceback. This
replaces pasting into KiCad's built-in console.

Pre-bound globals: `kicad` (`kipy.KiCad`), `board` (`Board` or `None`), `sch`
(`Schematic` or `None`). A global named `result` is returned to the caller as JSON.

## Units

Nanometres everywhere in kipy. `1 mm == 1_000_000 nm`.

```python
from kipy.geometry import Vector2, Angle
from kipy.util.units import from_mm, to_mm

Vector2.from_xy_mm(50.0, 60.0)     # preferred
Vector2.from_xy(from_mm(50), from_mm(60))
Angle.from_degrees(90)
to_mm(fp.position.x)               # back to mm for reporting
```

## Commits

Group edits so they land as one undo step, and roll back on failure:

```python
from kicad_harness.live import Commit

with Commit(board, "place decoupling"):
    ...          # exception here drops the commit, leaving the board untouched
```

Raw form, if you need it: `board.begin_commit()` / `push_commit(c, "msg")` /
`drop_commit(c)`.

## Board

```python
board.get_footprints()      # FootprintInstance
board.get_pads()            # Pad
board.get_tracks()          # Track | ArcTrack
board.get_vias()            # Via
board.get_zones()           # Zone
board.get_shapes()          # BoardShape
board.get_text()            # BoardText | BoardTextBox
board.get_nets()
board.get_items_by_net(net)
board.get_connected_items(item)
board.get_selection()       # what the user has selected in the GUI
board.get_stackup()
board.get_copper_layer_count()

board.create_items(items)
board.update_items(items)   # after mutating -- changes are not automatic
board.remove_items(items)
board.save()                # lossy -- see below
```

`update_items` is the step people forget. Mutating `fp.position` changes your
local copy only; the board does not move until you push it back.

`board.save()` is the one to avoid. It round-trips the board through the API
model, and anything the model does not represent is gone from the file that
lands — measured on KiCad 10.0.5 as every locked graphic and every User-layer
construction line, with no error and a well-formed result. Let the user press
Ctrl+S, and use offline `kh place` when all you need is to move footprints.

### FootprintInstance

`position` (Vector2), `orientation` (Angle), `layer`, `locked`, `definition`,
`reference_field`, `value_field`, `datasheet_field`, `description_field`,
`attributes`, `id`.

Reference designators sit in a nested text field whose exact path has moved
between kipy versions — use `kicad_harness.live.ref_of(fp)` or
`footprints_by_ref(board)` rather than reaching in directly.

### Moving parts

```python
from kicad_harness.live import Commit, footprints_by_ref
from kipy.geometry import Vector2, Angle

fps = footprints_by_ref(board)
with Commit(board, "reposition"):
    fp = fps["C12"]
    fp.position = Vector2.from_xy_mm(48.0, 61.5)
    fp.orientation = Angle.from_degrees(90)
    board.update_items(fp)
```

### Editing footprint fields

`reference_field`, `value_field`, `description_field` … are `Field` objects, not
strings. The text lives at `.text.value`, and the field also carries `field_id`,
`name`, `layer` and `visible`.

```python
with Commit(board, "annotate placement priority"):
    batch = []
    for ref, text in notes.items():
        f = fps[ref]
        f.description_field.text.value = text
        batch.append(f)
    board.update_items(batch)       # takes a list -- no need to loop
```

`description_field` is `field_id` 5 and lands in the footprint's per-instance
`(property "Description" ...)` in the `.kicad_pcb`. That is **not** the
`(descr ...)` line a few lines above it: `descr` is the library footprint's own
blurb and is restored by "Update Footprints from Library", while the property is
per-instance and is what KiCad shows in the Properties panel. Do not confuse them.

**`update_items` on a footprint is lossy too — not just `board.save()`.**
Measured on KiCad 10.0.6: pushing 132 footprints through `update_items` to set
one field silently **deleted the `(units ...)` block from every one of them**.
The file went from 157 blocks to 25; the 132 touched footprints each lost

```
(units
    (unit
        (name "A")
        (pins "1" "2")
    )
)
```

which sits between `(sheetfile ...)` and `(attr ...)` and carries the
symbol-unit-to-pad mapping synced from the schematic. Nothing else changed —
pads, `fp_line`, `fp_poly`, `fp_text`, `property` and the `model` reference all
survived, and the diff was exactly the 131×6 removed lines. No error, no
warning, and the resulting file is well-formed and loads fine.

So the `board.save()` warning generalises: **anything the API model does not
represent is dropped from whatever you push through it**, at whatever
granularity you push. Before a bulk `update_items`, snapshot the footprints you
are about to touch, and diff after the save:

```bash
unzip -p <project>-backups/<newest>.zip <project>.kicad_pcb > /tmp/base.kicad_pcb
diff <(grep -v '(property "Description"' /tmp/base.kicad_pcb) \
     <(grep -v '(property "Description"' <project>.kicad_pcb)
```

Counting element types is **not** enough to prove no damage — a profile of
`pad`/`fp_line`/`property`/`model` counts looked identical across touched and
untouched footprints while `units` was quietly missing from all of them. Diff the
text.

Repair, if it already happened: KiCad writes a zip into `<project>-backups/` on
every save, so the pre-damage file is usually still there. Re-insert each
footprint's block verbatim before its `(attr ` line, then have the user
**File → Revert** in the PCB editor — the editor still holds the damaged board in
memory and will re-save the damage otherwise.

**A field's live value can be stale relative to the file.** Observed on a real
board: the `.kicad_pcb` held the user's custom Description strings, but the API
returned the stock library text (`"Unpolarized capacitor"`, `"Resistor"`) for
those same footprints — while `position` matched the file exactly, so the board
was otherwise current. "Update PCB from Schematic" resets footprint fields from
the schematic symbol, and until the next save the file and the editor disagree.

Two consequences, both of which cost real work if missed:

- Read the `.kicad_pcb` as well as the API before concluding a field is unset.
- Saying "press Ctrl+S" propagates the *editor's* version and silently discards
  whatever exists only in the file. Capture the file's text first, then rewrite it.

## Schematic — not available

`sch` is `None`, and `get_schematic()` raises. kipy 0.7.1 contains schematic
wrapper classes but not the protobuf definitions they need, and its schematic
command proto is empty, so no schematic operation exists on the wire. See
[CAPABILITIES.md](CAPABILITIES.md#the-schematic-api-looks-present-does-not-work)
for the measurements.

Do not be misled by `get_open_documents(DOCTYPE_SCHEMATIC)` returning your open
schematic — that call lives in the common protos and works regardless.

Feature-check before assuming:

```python
from kicad_harness.live import schematic_supported
if not schematic_supported():
    ...   # fall back to files
```

Work with schematics as text instead:

```bash
kh netlist --out /tmp/n.net    # connectivity, no parsing needed
kh erc --limit 10              # what is wrong with it
```

and edit `.kicad_sch` s-expressions directly. When a kipy release ships matching
protos, `get_schematic()` should begin working with no change to your code.

## GUI actions

```python
kicad.run_action("pcbnew.Control.zoomFitScreen")
```

Officially unstable: KiCad does not guarantee action names across releases.
Useful for nudging the view; do not build logic on it.

## Other

```python
kicad.get_version()
kicad.ping()
kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
kicad.get_kicad_binary_path("kicad-cli")
kicad.get_text_extents(text)      # measure before placing
board.expand_text_variables("${REVISION}")
```
