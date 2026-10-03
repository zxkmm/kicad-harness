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

### Measured: freerouting on a dense, mostly hand-routed board

Freerouting 2.3.0 headless (`java -jar freerouting-executable.jar --gui.enabled=false
-de b.dsn -do b.ses -mp N`) on a 148-part, 2-layer RP2350 board with ~1500 existing
wires and 48 remaining connections: **unusable.** It reported 655 violations on the
untouched board under its own rule model, and after 5 passes still had 51 of 99
items unrouted (10+ minutes). What was learned on the way:

- `ExportSpecctraDSN` already writes every existing track as `(type fix)`; there is
  no need to lock tracks first.
- By default freerouting runs a **fanout stage on every SMD pin** (519 of 619 here),
  adding stubs and vias all over a finished layout. Pass
  `--router.fanout.enabled=false --router.optimizer.enabled=false`.
- To route only some nets, empty the other nets' `(pins ...)` lists in the
  `(network ...)` section. The nets (and their fixed wires) stay as obstacles.
- Freerouting sees zones as `plane` outlines, not fills, so its unrouted count
  differs from KiCad's (99 vs 48 here).

What worked instead is `examples/route_unconnected.py`: an A* maze router on a raster
of KiCad's own copper polygons, fed by the DRC `unconnected_items` pairs (whose
`uuid`s map straight to board items). 48 -> 3 in ~2 minutes with no new clearance
errors, and `examples/stitch_gnd_islands.py` for pour islands. The traps were all
about **ground pour**: new tracks quietly isolate GND pads and pour regions. Find
the culprit by stripping one net's new tracks at a time, refilling and re-running
DRC (about 20 s per try); then fence that spot off with `--block`.

### pcbnew connectivity from Python is not enough to find islands

`BOARD.GetConnectivity().GetConnectedItems(item)` works but does **not** follow
zone-only connections reliably: pads that DRC calls connected came back as
singleton clusters. `GetRatsnestForNet()` returns an unwrapped `RN_NET`
(no `GetEdges`). For "which pour island is cut off", trust `kicad-cli pcb drc
--refill-zones` and bisect, not the Python connectivity API.

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
  the editor, renders and DRC show stale geometry. Save first.
- **`board.save()` silently drops items the API model does not carry.** On
  KiCad 10.0.5, saving a real board over IPC wrote a file missing 17 `gr_line`,
  1 `gr_poly` and 2 `gr_text` — every one of them a **locked** graphic or
  User-layer construction line — while the footprint moves that had been
  requested were not written at all, and KiCad became unreachable immediately
  after. No error is raised and the resulting file is well-formed, so nothing
  flags it. Have the user press Ctrl+S; use offline `kh place` for moves. If it
  has already happened, `git checkout --` the board or restore from the zips in
  `<project>-backups/`.
- **Two unit systems.** `pcbnew` and kipy use nanometres internally; the harness
  CLI reports millimetres. Use `Vector2.from_xy_mm` rather than converting by hand.
- **`run_action()` is explicitly unstable.** KiCad does not guarantee action
  names across versions. Fine for a nudge like refreshing the view, not
  something to build on.
- **Python 3.14 drops `SwigPyIterator.next()` fallback.** KiCad's bundled SWIG wrapper
  implements container `__iter__` using `it.next()`. Python 3.14 enforces `__next__()`
  strictly, raising `AttributeError: 'SwigPyIterator' object has no attribute 'next'`
  when you iterate a container -- `for t in board.GetTracks():`, `for fp in board.Tracks():`,
  same failure on any `BOARD` container. This bites `GetFootprints()`-style calls too
  wherever they build the list via `for x in Container():` under the hood; `GetFootprints()`
  itself is safe (it's a direct list typemap, not an iterator).
  `kicad_harness.geom` patches `pcbnew.SwigPyIterator.next = __next__` on import, but that
  patch only takes effect if your script actually imports through the harness
  (`from kicad_harness.geom import pcbnew`) -- and `kicad_harness` is only on `sys.path`
  inside the `kh` venv, so a **standalone** script (like the `import pcbnew` example just
  above) will still hit this even though the harness "handles" it. Either import through
  the harness, or carry the two-line patch yourself:
  ```python
  import pcbnew
  if hasattr(pcbnew, "SwigPyIterator") and not hasattr(pcbnew.SwigPyIterator, "next"):
      pcbnew.SwigPyIterator.next = pcbnew.SwigPyIterator.__next__
  ```
  Or sidestep it entirely: index the container instead of iterating —
  `tracks = board.Tracks(); [tracks[i] for i in range(len(tracks))]` needs no patch.
  Indexing hands back a `PCB_TRACK` proxy for **every** item, vias and arcs
  included: `GetClass()` says `PCB_VIA`, but `GetWidth(pcbnew.F_Cu)` then fails
  with "takes 1 positional argument". Call `tracks[i].Cast()` first.
- **SWIG `LoadBoard` → `Save` is lossless, unlike the IPC save.** Measured on KiCad
  10.0.6, 171-footprint 4-layer board: load-and-save with no edits produced a
  byte-identical file (`diff` empty), and after adding ~100 tracks, ~40 vias and
  2 zones the counts of `gr_*`, footprints and `(units` blocks were unchanged.
  So for bulk routing on a board that is *not* open in the editor, a script that
  loads a pristine copy, adds `PCB_TRACK`/`PCB_VIA`/`ZONE` and `Save()`s is the
  safe path. Rebuild from the pristine copy each iteration, so the script stays
  idempotent.
- **Zones fill headlessly:** `kicad-cli pcb drc --refill-zones --save-board` fills
  and writes them back. Keep the `.kicad_pro` next to the board copy (same stem),
  or the netclasses are missing and DRC reports false `track_width` errors on
  every netclass track. A scratch copy gives stale fills unless you refill, and
  then new parts show up as bogus `solder_mask_bridge` against the old pour.
  Diff the violations against a baseline run by (type, item descriptions). The
  absolute count on a real board is mostly pre-existing silk noise.
- **`lib_footprint_mismatch` from rotation alone.** `Diode_SMD:D_SOD-123F` that
  matches the library at 90°/270° is flagged as mismatched at 0° and 180°, with no
  other change (10.0.6). It is a rounding artefact, not a modified footprint.
