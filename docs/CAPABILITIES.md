# What KiCad actually exposes to an agent

Findings from probing KiCad **10.0.5** on Arch Linux, `kicad-python` (kipy) **0.7.1**.
Verified by running against a real project, not read off documentation.

## Summary

| Capability | Verdict | Route |
|---|---|---|
| Read board geometry, nets, footprints | **yes** | `pcbnew` module, offline |
| Render layout to an image an agent can read | **yes** | `kicad-cli` SVG → `rsvg-convert` |
| Move/rotate/place components in a running KiCad | **yes** | kipy IPC |
| Create/edit tracks, vias, zones, text, shapes | **yes** | kipy IPC |
| **Edit schematics programmatically** | **no** — see below | — |
| DRC / ERC as machine-readable JSON | **yes** | `kicad-cli` |
| Netlist / BOM export | **yes** | `kicad-cli` |
| Trigger any GUI action | yes, unstable | `kicad.run_action(name)` |
| Autorouting | none built in | but see the DSN/SES bridge below |
| Specctra DSN out / SES in | **yes, headless** | `pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES` |
| Ratsnest/airwires in a rendered image | **no** | SVG export omits them |

## The schematic API: looks present, does not work

This one is a trap, and worth spelling out because the source tree strongly
suggests otherwise.

kipy 0.7.1 ships ~1700 lines of hand-written schematic wrappers —
`kipy/schematic.py` and `kipy/schematic_types.py` — describing a complete
read/write API: `create_items` / `update_items` / `remove_items`, `get_symbols`,
`get_lines` (wires), `get_labels`, `get_hierarchy`, commit/undo support, and 28
item classes from `SchematicSymbolInstance` to `BusEntry`. Reading the source,
you would conclude schematic automation is solved.

**It is not. `import kipy.schematic` fails outright:**

```
ImportError: cannot import name 'BusEntryType'
             from 'kipy.proto.schematic.schematic_types_pb2'
```

The wrappers import protobuf symbols that kipy's own generated modules do not
contain. Measured on this install:

| module | lines | contents |
|---|---|---|
| `proto/schematic/schematic_commands_pb2.py` | 13 | **empty** — no commands |
| `proto/schematic/schematic_types_pb2.py` | 28 | 10 symbols, no `BusEntryType` |
| `proto/board/board_commands_pb2.py` | 110 | the real thing, for comparison |

`schematic_commands_pb2` being empty is the decisive part: there are no
schematic commands **on the wire at all**. This is not a packaging slip that a
reinstall fixes — the `.pyi` stubs agree with the `.py` files. It is unreleased
work vendored ahead of the protos that would make it function.

Verified broken on **KiCad 10.0.5 + kicad-python 0.7.1** (the latest release as
of 2026-08; 0.7.1, 0.7.0, 0.6.0 … 0.0.1 are all that exist on PyPI).

Note that `get_open_documents(DOCTYPE_SCHEMATIC)` **does** work and will happily
return your open `.kicad_sch` — that lives in the common protos. It is easy to
mistake that for schematic support. `kh live` reports `schematic_api: false` so
you do not have to find out the hard way.

So the old advice still holds today, for a new reason: **treat schematics as
files, not objects.**

- `kicad-cli sch export netlist` gives you connectivity with no parsing at all
- `kicad-cli sch erc --format json` gives you what is wrong with it
- `.kicad_sch` is plain, stable s-expression text — read and edit it directly

The direction of travel is clear, though. When a kipy release ships matching
protos, `kicad_harness.live.get_schematic()` should start working unchanged;
`schematic_supported()` is the feature check.

## Two Python bindings, and they are not the same thing

This trips people up constantly:

**`pcbnew`** — the classic in-process SWIG binding. Works headless against files
on disk. Board only, no schematic. This is what footprint wizard scripts use.
Cannot touch a running editor's in-memory state.

**`kipy`** — the IPC binding, KiCad 9+. Talks over a socket to a *running* KiCad.
Board **and schematic**, proper undo/commit semantics, changes appear live in the
GUI. Install with `pip install kicad-python` (the import name is `kipy`).

The harness uses `pcbnew` for offline measurement and `kipy` for live editing.

## Enabling the live API

It ships **disabled**. In `~/.config/kicad/10.0/kicad_common.json`:

```json
"api": { "enable_server": false, "interpreter_path": "/usr/bin/python3" }
```

Turn it on in the GUI — **Preferences → Plugins → "Enable KiCad API"**. Do not
hand-edit that JSON while KiCad is running; it rewrites the file on exit and
your change is lost.

Once enabled, KiCad binds a socket and `kipy.KiCad()` finds it via the
`KICAD_API_SOCKET` environment variable or the platform default path.

## Autorouting: none built in, but the bridge is fully scriptable

KiCad has no autorouter. It has had, for twenty years, the **Specctra interchange
bridge** that external routers plug into:

- **DSN out** — board outline, layers, netlist, keepouts, design rules
- **SES in** — the tracks and vias the router decided on

Both are exposed to Python and work headless, with no GUI and no dialogs:

```python
import pcbnew
b = pcbnew.LoadBoard("board.kicad_pcb")
pcbnew.ExportSpecctraDSN(b, "board.dsn")     # -> True
# ... run an external router on board.dsn, producing board.ses ...
pcbnew.ImportSpecctraSES(b, "board.ses")
pcbnew.SaveBoard("board.kicad_pcb", b)
```

Overloads: both take either `(filename)` against the GUI's current board, or
`(BOARD, filename)` against one you loaded yourself. Use the second.

Measured: 42-component board exported to a 28 KB DSN in about a second.

Two things to be careful about:

- **These are file-level operations.** They do not go through the IPC API, so
  they act on the last-saved file, not on what the editor holds in memory. If
  the board is open in KiCad, a `SaveBoard` underneath it will be silently
  clobbered the next time the user saves. Check for the `~*.lck` lock file, or
  require the board to be closed.
- `kicad-cli` has **no** DSN export — the full export list is `3dpdf brep drill
  dxf gencad gerbers glb hpgl ipc2581 ipcd356 odb pdf ply pos ps stats step stl
  stpz svg u3d vrml xao`. Python bindings only.

The equivalent GUI actions, if you want them via `run_action`, are
`pcbnew.EditorControl.exportSpecctraDSN` and
`pcbnew.EditorControl.importSpecctraSession` — but those open file dialogs, so
prefer the Python functions.

## Why rendering beats screenshotting

The instinct is to screenshot the KiCad window. Rendering from the file is
strictly better:

- **Exact.** SVG export uses a viewBox in millimetres over the page, so board
  coordinates map 1:1 onto image coordinates. Zooming to a region is a viewBox
  rewrite — no scraping, no window-manager dependency, no guessing at scale.
- **Selective.** You choose the layers. Courtyards only, to check overlap.
  Copper only, to check routing. Silkscreen, to check refs.
- **Headless and fast.** ~0.5 s for an SVG export of a 42-part board; DRC on the
  same board takes ~3 s.

The one thing it will not show you is the ratsnest. For unrouted connections,
read the `unconnected` section of `kh drc` instead — it gives you the same
information with coordinates, in text.

## Timings on a 42-component, 35-net, 2-layer board

| Operation | Time |
|---|---|
| SVG export | 0.5 s |
| SVG → PNG | < 0.1 s |
| DRC (JSON) | 3 s |
| ERC (JSON) | < 1 s |
| `pcbnew.LoadBoard` | < 1 s |

Fast enough to sit inside an edit → render → look → fix loop.

## Gotchas

- **`pcbnew` prints wxWidgets assertion noise to stderr on import.** Harmless.
  `kicad_harness.geom` suppresses it.
- **`GetNetsByName()` keys are `wxString`,** which cannot be sorted against each
  other. Convert with `str()` first.
- **Offline tools read the last-saved file.** With the board open and dirty in
  the editor, renders and DRC show stale geometry. Save first, or call
  `board.save()` over IPC.
- **Two unit systems.** `pcbnew` and kipy use nanometres internally; the harness
  CLI reports millimetres. Use `Vector2.from_xy_mm` rather than converting by hand.
- **`run_action()` is explicitly unstable.** KiCad does not guarantee action
  names across versions. Fine for a nudge like refreshing the view, not
  something to build on.
