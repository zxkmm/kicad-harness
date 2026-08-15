# Issues found while dogfooding

Kept while building a TP4056 USB-C charger end to end with nothing but this
harness — library lookup, schematic authored as text, netlist, board, placement,
routing, checks. Everything here was hit in practice, not read off the source.

**Fixed** entries are done and verified. **Open** entries say what I would do.

Session: 2026-08-15, KiCad 10.0.5, kipy 0.7.1, Arch Linux.

---

## Fixed

### 1. `kh live` crashed instead of explaining itself
**Severity: high** — it was the first command I ran, and it failed with a
traceback-shaped error that pointed nowhere.

```
ApiError: KiCad returned error: no handler available for request of type
          kiapi.common.commands.GetOpenDocuments
```

`status()` is documented as reporting "without raising", but it only guarded
`connect()`. With KiCad running and **no editor window open**, `ping` succeeds
and `get_open_documents` has no handler at all — the project manager does not
serve document requests. The exception escaped, `cmd_live` never ran, and the
user got an error naming a protobuf message instead of "open a board".

The same hole was in `run_script`: it swallowed the failure and left `board =
None`, so the user's script died later on an `AttributeError` far from the cause.

*Fixed:* `status()` catches it and reports `editor_open: false` plus what to do;
`kh live` exits non-zero unless the layer is actually usable; `run_script`
raises the actionable error instead of handing out a `None` board.

### 2. Netlist → board did not exist
**Severity: high** — the top gap on the list, and the one that blocks the whole
schematic-first workflow.

*Fixed:* `kh board-from-netlist`. Straight `pcbnew`: load each footprint from
the library, set reference/value/FPID, create `NETINFO_ITEM`s, assign pads,
save. No GUI, no clicks, no live API. ~1 s for this board.

Deliberate choices worth knowing:

- It sets each footprint's **sheet path** from the netlist's `tstamps`. Without
  it, DRC's schematic-parity check reports every part as unknown. With it,
  parity is clean and KiCad's own "update PCB from schematic" round-trips.
- It reports **`unmatched_pads`** — a pin the netlist names that the footprint
  does not have. That is the symbol/footprint mismatch, caught at build time
  instead of as a mystery later.
- It refuses to overwrite an existing board without `--overwrite`, and refuses
  outright if a `.lck` file suggests the board is open in KiCad, because this
  writes the file underneath the editor.

### 3. `FindPadByNumber` silently netted only one pad of a group
**Severity: high** — a real electrical bug, and it shipped in my first cut of
`board-from-netlist` before DRC caught it.

A pad number is not unique. The TP4056's EPAD is **8 pads all numbered `9`**
(the exposed pad plus its thermal via array); a USB-C shield is **4 pads all
numbered `SH`**. `FOOTPRINT.FindPadByNumber()` returns the first match, so 7 of
8 EPAD pads and 3 of 4 shield pads were left on no net.

That is invisible in the netlist and invisible in a render. DRC found it, but
only as a confusing symptom — `Items shorting two nets (nets GND and )`, the
same pad number shorting a net to no-net:

```
PTH pad 9 [GND] of U1  <->  PTH pad 9 [<no net>] of U1
```

*Fixed:* assign to every pad carrying that number. Also written into `SKILL.md`,
because anything else that maps pins to pads has the same trap.

### 4. No offline placement path
**Severity: medium.** `kh exec` is the only way to move a part, and it needs a
board open in KiCad. A board being built headlessly is by definition not open,
so there was no way to place what `board-from-netlist` had just created.

*Fixed:* `kh place` (file-level, `--set REF=x,y[,rot][,side]` or `--json`) and
`kh outline` for an Edge.Cuts rectangle. A board with no outline has no DRC.

### 5. A board built from a netlist failed DRC on stock footprints
**Severity: medium.** An empty board comes up with KiCad's defaults: 0.2 mm
clearance, 0.3 mm minimum hole. Plenty of shipped footprints violate both — the
USB-C receptacle's pads are 0.15 mm apart, the SOIC thermal vias drill 0.2 mm.
First DRC run: **28 clearance + 6 drill-out-of-range errors**, none of them
anything I had done.

*Fixed:* `--clearance` / `--track-width` / `--min-drill` on
`board-from-netlist`, applied to the Default netclass and the board constraints.
With rules matching the parts, all 34 disappeared.

### 6. `kh sym --pins` gave pin numbers but not pin *positions*
**Severity: medium.** The docs say to treat schematics as files and edit the
s-expressions — but you cannot place a wire without knowing where the pin is,
and that was the one thing the library index did not expose.

*Fixed:* `--pins` now returns each pin's `x`, `y`, `rotation`, `length`. Added
`kh sym --sexpr Lib:Name`, which returns the raw symbol body with its name
namespaced, ready to drop into a schematic's `lib_symbols` block — extracted as
text rather than re-serialised from a parse tree, so formatting and quoting
survive. It flattens `(extends ...)` too, since a derived symbol that keeps the
reference will not load standalone.

### 7. No way to look at a schematic
**Severity: medium.** `kh view` is board-only, and the loop the harness is built
around — do it, render it, *look* — was unavailable for the half of the project
the harness now helps you author.

It mattered immediately. My first generated schematic was ERC-clean and its
netlist was correct, and it was still visibly wrong: the CHRG and STDBY pins
leave the TP4056 2.54 mm apart, so the two LED branches were drawn on top of
each other. No check catches that. The picture did.

*Fixed:* `kh sview`.

### 8. RECIPES.md told you to use the schematic API that does not work
**Severity: low, but corrosive.** `CAPABILITIES.md` explains at length that
`import kipy.schematic` fails outright; `RECIPES.md` ended with "For *editing* a
schematic, use the live layer (`sch.create_items` / `update_items`)". An agent
reading the recipes first burns a cycle discovering otherwise.

*Fixed:* corrected, and replaced with what actually works — authoring `.kicad_sch`
as text, including the coordinate transform and the junction rule.

### 9. SWIG leak noise buried command output
**Severity: low.** Any command that adds objects to a `BOARD` printed ~10 lines
of `swig/python detected a memory leak of type 'PCB_SHAPE *', no destructor
found` to stderr, after the JSON. The objects are not leaked — the board owns
them — but it reads like a failure, and I misread it as one.

Marking `thisown = 0` is the correct ownership annotation but does **not**
silence it; the messages come from interpreter finalization. *Fixed* by exiting
via `os._exit()` after flushing, which skips finalization. Nothing here needs
it — temporary directories are released by their context managers.

---

## Open

### 10. Nothing verifies that a netlist means what you intended
**Severity: high — the biggest remaining gap, and it is not a tooling gap.**

`kh erc` returned clean on my schematic before I had checked a single net. ERC
validates pin *types* — a power input with no driver, a pin with nothing on it.
It cannot tell a charger from a short, because "wired wrongly but legally" is
not an electrical error.

I caught the one real bug this way: my VBUS rail had no `power:VBUS` symbol on
it, so the rail was an anonymous net and the `PWR_FLAG` I had placed was sitting
on a two-symbol island. ERC did flag *that* one, but only indirectly, and I
initially misdiagnosed it as a symbol-rotation problem — I had to write a
four-rotation probe to prove rotation was innocent before looking at the net.

What would have caught it instantly: reading back the netlist and comparing it
to intent. That is what I ended up doing by hand.

*Proposal:* `kh netcheck --expect expected_nets.yaml`, asserting a stated net
list against the exported netlist and diffing both ways — nets you named that do
not exist, nodes you did not expect on a net. Small, deterministic, and it turns
"ERC is clean" into an actual claim about the circuit. The strong-priors problem
this harness exists to solve applies to topology just as much as to library ids.

### 11. Renders still cannot show connectivity
**Severity: medium.** Documented, and it bit exactly as advertised: after
placement I had a picture that told me nothing about whether parts that belong
to a net were anywhere near each other. `kh drc`'s `unconnected` section is the
suggested substitute, but it is a flat list of pad pairs — for this board, 37
entries — with no sense of *length*, which is the thing placement is judged on.

*Proposal:* `kh ratsnest` — per net, the minimum spanning tree over its pad
positions and the total length, sorted worst-first. Pure geometry over data
already in `BoardView`, no KiCad involvement. That gives a placement a number
that can be compared before and after, which the image cannot.

Rendering actual airwires is also possible — pcbnew can compute the ratsnest and
the SVG could be overlaid — but the MST metric is a fraction of the work and
answers the question that actually comes up.

### 12. Rotation semantics are undocumented in both directions
**Severity: medium.** Schematic symbol rotation and board footprint rotation are
different conventions, neither is written down, and both silently produce a
loadable file when wrong.

I resolved the schematic side by measurement — `gen/probe_rotation.py` in the
demo project places a symbol at all four rotations with a uniquely named label
on each pin and reads the netlist back. All four matched the transform now
documented in `RECIPES.md`. The board side I sidestepped by placing everything
at rotation 0 and confirming by eye.

*Proposal:* fold that probe into the repo as a test. It runs in about a second
and it is the only thing standing between a future change and a silently wrong
transform.

### 13. `board-from-netlist` cannot update an existing board
**Severity: medium.** It builds from scratch or refuses. Real projects change a
value, add a part, rename a net — and then you want the placement and routing
you already have. Today the answer is "rebuild and re-place", which throws away
the routing.

*Proposal:* `--update`, matching footprints by the sheet path already being
written, then adding/removing/re-netting only what changed. The path bookkeeping
is done; this is the diff on top.

### 14. Placement is hand-written coordinates
**Severity: low.** `gen/placement.json` is fourteen hand-tuned pairs, and I
iterated it three times against DRC — twice for silkscreen overlap, once for
courtyard overlap. Each round is: guess, place, render, read, adjust.

Not obviously the harness's job, and I would not add an autoplacer. But two
cheap things would have removed most of the iterations: a `kh place --check`
that reports courtyard overlap *before* saving, and letting `--set` take a
reference-relative position (`--set C1=U1+0.5,5.5`), since almost every part
here is positioned with respect to another one.

### 15. `kh view` frames on the outline, not the content
**Severity: low.** Rendering the scratch-grid board showed a mostly empty
rectangle: parts land outside the Edge.Cuts outline, and "whole board" means the
outline. Nothing is wrong, but the first render of a freshly built board is
uninformative exactly when you most want to look at it.

*Proposal:* frame on the union of the outline and all footprint bounding boxes.

### 16. No round-trip test
**Severity: medium.** The harness has no tests at all. Everything above was
found by using it. The obvious one writes itself now: generate the demo
schematic, ERC, netlist, board, place, DRC, assert clean — the whole chain in
about ten seconds, against real libraries.

---

## Notes, not issues

- **Specctra bridge works exactly as documented.** `ExportSpecctraDSN` →
  freerouting → `ImportSpecctraSES` routed this board headlessly with no clicks.
  Worth knowing: freerouting reported "0 unrouted and 12 violations" against its
  own rules, and KiCad's DRC then found **zero** violations on the imported
  result. Its violation count is not KiCad's, so check with `kh drc` rather than
  trusting the router's summary. Its own warning that multi-threaded
  optimization is broken is worth heeding (`-mt 1`), though on this board both
  produced the same score.
- **`kicad-cli sch export netlist` does not list `PWR_FLAG` or power symbols**
  as components — they are not board parts. Anything trying to verify a flag is
  connected has to go through ERC, not the netlist. Cost me one wrong probe.
- **`_parse` in `libs.py` is doing real work outside its stated scope.** Its
  docstring says "only used for single-symbol detail"; `board.py` now parses
  netlists with it. It is a fine little parser — the comment is just stale.
