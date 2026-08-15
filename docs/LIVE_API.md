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
board.save()
```

`update_items` is the step people forget. Mutating `fp.position` changes your
local copy only; the board does not move until you push it back.

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
