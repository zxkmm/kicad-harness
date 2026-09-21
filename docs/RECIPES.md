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

result = placed
```

Note there is no `board.save()` here on purpose — the live save drops locked and
User-layer graphics (see `SKILL.md`). Save from KiCad, then render.

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

## Place a pi-attenuator resistor ladder (exact pad-edge neck gap)

Pi-attenuators in RF signal paths require precise spacing and orientation:
- **Neck clearance is pad-edge to pad-edge**, not component center-to-center.
  When an RF layout specifies e.g. a "0.8 mm neck", this is the gap between the
  transverse shunt pad edge and the inline series pad edge.
  For 0805 (pads $1.025 \times 1.4\text{ mm}$ centered at $\pm 0.9125\text{ mm}$):
  - A horizontal series resistor (0°) extends $\pm 1.425\text{ mm}$ along X ($0.9125 + 0.5125$).
  - A vertical shunt resistor ($\pm 90^\circ$) has its $1.4\text{ mm}$ width along X, extending $\pm 0.7\text{ mm}$.
  - Shunt-to-series center-to-center pitch for a $0.8\text{ mm}$ neck is:
    $$0.8 + 0.700 + 1.425 = \mathbf{2.925\text{ mm}}$$
- **Stacked vertical shunt arms** (` | -|- | `): High-attenuation stages often place
  two (or more) resistors in series to GND in each shunt arm to meet voltage/power
  ratings. The inner resistor touches the signal line at $Y_{\text{signal}} \pm 1.425\text{ mm}$,
  and subsequent resistors stack vertically touching pad-to-pad (pitch = $2.85\text{ mm}$ for 0805).
- **Zero-gap inline series chains**: Adjacent series resistors butt pad-to-pad
  ($\text{pitch} = 2.85\text{ mm}$ for 0805).
- **Alternating rotations prevent crossed traces**:
  - Series resistors alternate $0^\circ / 180^\circ$ so that pin 2 meets pin 2 and pin 1 meets pin 1.
  - Shunt arms orientation in KiCad (+Y downwards):
    - In KiCad screen space, a $+90^\circ$ rotation is clockwise: Pad 1 (initially at $-X$) rotates to $+Y$ (downwards).
    - For an arm extending UP ($Y < Y_{\text{signal}}$), Pad 1 faces the signal line ($+Y$) at $\mathbf{rot = 90^\circ}$.
    - For an arm extending DOWN ($Y > Y_{\text{signal}}$), Pad 1 faces the signal line ($-Y$) at $\mathbf{rot = -90^\circ}$ (or $270^\circ$).
    - Stacked multi-element arms alternate rotations so adjacent pads match pin numbers and touch without crossing traces.

See `examples/place_pi_attenuator.py` for the full script:

```python
# Shunt-to-series pitch with exact 0.8mm pad-edge neck:
NECK_CC = 0.8 + 0.7 + 1.425   # 2.925 mm (0805)
SERIES_CC = 2 * 1.425          # 2.850 mm (0805 touching pad-to-pad)
```

Verify with:
```bash
kh view --refs R1,R2,R5,R6,R7,R13,R14 --margin 2 --out /tmp/pi_check.png
```

## Bridge touching pads with copper shapes (eliminate V-gaps and satisfy DRC)

When surface-mount pads on the same net are placed touching pad-to-pad (e.g. in
zero-gap series chains or vertically stacked shunt arms):
1. The footprint rounded corners create a tiny notch or "V-gap" at the contact line.
2. KiCad DRC will flag the pads as unconnected unless a copper track or shape
   physically joins them.

You can bridge all touching pad pairs automatically by generating filled
`pcbnew.PCB_SHAPE` copper rectangles assigned to the common net.

See `examples/bridge_touching_pads.py`:

```python
# Detect touching pads and add copper rectangular bridges:
TOL = 1000  # 1 µm touch tolerance (nm)
pads = [p for p in b.GetPads() if p.IsOnLayer(pcbnew.F_Cu) and p.GetNetCode() > 0]
for i, a in enumerate(pads):
    for c in pads[i+1:]:
        if a.GetNetCode() != c.GetNetCode() or a.GetParent() == c.GetParent():
            continue
        ba, bc = a.GetBoundingBox(), c.GetBoundingBox()
        ba2 = pcbnew.BOX2I(ba.GetPosition(), ba.GetSize()); ba2.Inflate(TOL)
        if not ba2.Intersects(bc):
            continue
        pa, pc = a.GetPosition(), c.GetPosition()
        if abs(pa.x - pc.x) < abs(pa.y - pc.y):   # stacked vertically
            x1, x2 = max(ba.GetLeft(), bc.GetLeft()), min(ba.GetRight(), bc.GetRight())
            y1, y2 = pa.y, pc.y
        else:                                      # side by side
            y1, y2 = max(ba.GetTop(), bc.GetTop()), min(ba.GetBottom(), bc.GetBottom())
            x1, x2 = pa.x, pc.x
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_RECT)
        s.SetStart(pcbnew.VECTOR2I(x1, y1))
        s.SetEnd(pcbnew.VECTOR2I(x2, y2))
        s.SetFilled(True)
        s.SetWidth(0)
        s.SetLayer(pcbnew.F_Cu)
        s.SetNet(a.GetNet())
        b.Add(s)
```

Run in KiCad GUI Python Scripting Console:
```python
exec(open("examples/bridge_touching_pads.py").read())
```
Or run headless from the terminal:
```bash
python3 examples/bridge_touching_pads.py --pcb .
```

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
kh sview --all --out /tmp/sch.png # render every sheet and look at them
```

On a hierarchical design, plain `kh sview` gives you the root sheet — a box per
child and nothing else. `--all` writes `/tmp/sch-<SheetName>.png` per sheet;
`--sheet <name>` picks one; `--region x,y,w,h` zooms in sheet millimetres.

For structure, `.kicad_sch` is plain s-expression text — readable directly.

There is **no schematic editing API** — kipy ships the wrappers but not the
protobufs behind them, so `import kipy.schematic` fails outright and `sch` is
always `None`. See `CAPABILITIES.md`. Edit `.kicad_sch` as text instead.

## Change a schematic someone else drew

Different job from the section below, which writes one from scratch. Cloning
existing symbols, keeping uuids, property angles being relative to the symbol,
and diffing against a baseline netlist: `docs/SCHEMATIC_EDITS.md`.

## Author a schematic as text

`kh sym --pins` gives each pin's connection point in symbol coordinates, and
`kh sym --sexpr` gives the raw symbol body to embed in the file's `lib_symbols`
block. That is everything needed to write a `.kicad_sch` from scratch.

```bash
kh sym --pins Device:R                 # -> pin 1 at (0, 3.81), pin 2 at (0, -3.81)
kh sym --sexpr Device:R                # -> the (symbol "Device:R" ...) body
```

Two things to know, because neither is written down anywhere and both produce
files that load fine and net up wrong:

- **Library coordinates are y-up; the sheet is y-down.** A pin at symbol
  `(x, y)` on an instance placed at `(X, Y)` with rotation 0 lands at
  `(X + x, Y - y)`. Rotations fold that flip in, so 90 and 270 are not sign
  swaps of one another: 90 -> `(-y, -x)`, 180 -> `(-x, y)`, 270 -> `(y, x)`.
- **Connectivity is purely coordinates.** A wire end that misses a pin is a
  separate net and nothing in the file says so. A wire ending on the *middle*
  of another wire needs an explicit `(junction ...)`, or the two are merely
  crossing.
- **But two pins at the same coordinate are connected with no wire at all** —
  every `power:GND` symbol butted onto a capacitor pin works this way. A checker
  that requires each pin to land on a wire end will call a third of a healthy
  sheet broken.

Both transforms above are confirmed against a real board: the pin coordinates
they produce land exactly on the wire endpoints, junctions and coincident pins
that KiCad's own netlister agrees on.

Verify with `kh erc`, then read the netlist to confirm the nets are the ones
you meant — ERC passes happily on a circuit that is wired wrongly but legally.
Then `kh sview` and look at it, because overlapping symbols are not an
electrical error and only the picture shows them.

## Netlist to board, headless

```bash
kh board-from-netlist --sch . --outline 20,20,38,26.5 \
    --clearance 0.125 --min-drill 0.2      # parts + nets, on a scratch grid
kh place --pcb . --json placement.json     # move them (no KiCad needed)
kh view --pcb . --layers front --out /tmp/b.png
kh drc --pcb . --severity all
```

`--clearance` / `--min-drill` matter more than they look: an empty board comes
up with a 0.2 mm clearance and a 0.3 mm minimum hole, and stock KiCad
footprints violate both — a USB-C receptacle's pads sit 0.15 mm apart and a
thermal-via pattern drills 0.2 mm. Left at the defaults the board fails DRC on
geometry the library itself shipped.

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

## "My netclass isn't applying" — resolve it the way KiCad does

When a user reports that routing uses the wrong width, do **not** reason about
whether the `netclass_patterns` in `.kicad_pro` *should* match. Ask the running
KiCad what it actually resolved:

```python
nets = {n.name: n for n in board.get_nets()}
targets = [nets[k] for k in ("USB_D+", "USB_D-")]
for net, nc in board.get_netclass_for_nets(targets).items():
    print(net.name, nc.name, nc.track_width, nc.diff_pair_track_width, nc.diff_pair_gap)
```

`board.get_netclass_for_nets(list_of_Net)` returns `{Net: NetClass}` and is the
same resolver the router uses, so it settles pattern-matching questions
outright. Widths come back in **nanometres**.

Observed on KiCad 10.0.6: patterns containing `+` and `-` (`USB_D+`) match fine;
priority 0 correctly beats `Default` at 2147483647.

If the resolver returns the class you wanted but routing still uses the old
width, **check the DRC floor first — it is the most common cause and the
easiest to miss**:

```bash
python3 -c "import json;print(json.load(open('x.kicad_pro'))['board']['design_settings']['rules'])"
```

`rules.min_track_width` (Board Setup -> Design Rules -> Constraints) is a hard
floor: KiCad resolves the netclass width and then **clamps it up** to this
value. A netclass asking for 0.165 on a board with `min_track_width: 0.2` lays
0.2, silently, forever — re-routing never helps. Observed on 10.0.6.

The tell is an **asymmetry between width and gap**: there is no minimum-gap
constraint, so on a differential pair the netclass `diff_pair_gap` comes out
exactly right while the width is wrong. If gap is correct and width is not,
stop looking at the UI and read `rules.min_track_width`. Confirm by measuring
what was actually laid down:

```python
pitch = centre_to_centre_of_the_two_nets   # from Track start/end
gap   = pitch - width                      # == netclass diff_pair_gap?
```

The provenance labels KiCad itself uses are greppable, and enumerate every
source a width can come from:

```bash
strings -n 4 /usr/bin/_pcbnew.kiface | grep -A3 "board minimum track width"
# -> board minimum clearance / board minimum track width / existing track / netclass '%s'
```

Only once the DRC floor is ruled out is the override in the **editor session**:

- `auto_track_width: true` in `<proj>.kicad_prl` makes the router inherit the
  width of the object it starts from, ignoring the netclass entirely.
- the toolbar track-width dropdown may be pinned to an explicit value; with
  `board.design_settings.track_widths == []` the only correct setting is
  "use netclass width".

Neither is reachable over the API — the user has to toggle them in the GUI.
Locations, confirmed against `~/.config/kicad/10.0/toolbars/pcbnew-toolbars.json`
on 10.0.6: both live in the **TOP_AUX** toolbar, `control.PCBTrackWidth`
(the dropdown) followed immediately by `pcbnew.EditorControl.autoTrackWidth`
(the toggle). The toggle ships with **no default hotkey**.

Read that JSON rather than describing toolbars from memory — KiCad 9+ toolbars
are user-customisable, so positions are per-install. The action ids and their
labels are greppable out of the binary:

```bash
strings -n 4 /usr/bin/_pcbnew.kiface | grep -i -A2 "autoTrackWidth"
```

Note the exact scope of autoTrackWidth: *"When routing from an existing track
use its width instead of the current width setting."* On a net with **no
existing segments** it does not apply, so it cannot explain a wrong width on a
never-routed net — check the dropdown before blaming it.

Also worth saying out loud: `diff_pair_gap` is applied *only* by the
differential pair router (Route → Differential Pair). Drawing the two nets with
the single-track router picks up `track_width` but leaves the gap — and so the
differential impedance — to whatever the user happens to draw.
