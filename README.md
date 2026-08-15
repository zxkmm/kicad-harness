# kicad-harness

Gives an AI coding agent **eyes and hands** on a KiCad project.

Agents are already good at generating KiCad artifacts — footprints, netlists,
placement scripts. What they lack is the other half of the loop: seeing whether
the result is right, and applying it without a human copy-pasting into KiCad's
built-in console. This harness supplies both.

Companion to [kicad-footprint-generate](https://github.com/zxkmm/kicad-footprint-generate),
which handles datasheet → footprint. This handles everything after that.

## Three layers

| Layer | Needs | Gives you |
|---|---|---|
| **libraries** | nothing | real symbol/footprint ids and pin numbers, from the user's own libs |
| **offline** | nothing | component positions, bboxes, nets, DRC, ERC, netlist, BOM |
| **visual** | nothing | any board region rendered to PNG — the agent looks at the layout |
| **live** | API server enabled | edit a board open in KiCad, with proper undo |

## Install

```bash
./setup.sh
```

Needs KiCad 9 or 10 (tested on 10.0.5) and `rsvg-convert`. The venv is created
with `--system-site-packages` because `pcbnew` is installed by KiCad into the
system interpreter and cannot be pip-installed.

For the live layer, enable the API server in KiCad:
**Preferences → Plugins → "Enable KiCad API"**. It ships off.

## Use

```bash
kh sym TP4056                                  # find a real symbol id
kh sym --pins Battery_Management:TP4056-42-ESOP8   # real pin numbers and types
kh fp "USB_C receptacle"                       # find a real footprint id
kh validate --symbols A,B --footprints C       # check ids before using them

kh info                                        # board summary
kh ls --filter Inductor                        # parts, positions, bounding boxes
kh nets --net GND                              # every pad on a net

kh view --refs L1,L2,C3 --margin 3 --out v.png # frame those parts and render
kh view --region 45,55,20,20 --out v.png       # explicit mm window
kh view --layers courtyard --refs U1           # check for overlap

kh drc                                         # violations grouped by type
kh erc
kh netlist --out n.net

kh live                                        # is the IPC connection up?
kh exec place_filter.py                        # run a script against running KiCad
```

Pass `--pcb` / `--sch` to point at a project; otherwise the current directory is
searched. All output is JSON.

## The loop

```
kh ls        →  find the parts
kh exec      →  move them (live, one undo step)
kh view      →  render the result
   look      →  read the PNG
kh drc       →  confirm nothing broke
```

Step 4 is the whole point. A placement script that runs cleanly can still be
visibly wrong — parts rotated 90° off, a filter in the wrong order, courtyards
overlapping. Only the image catches that.

## Agent skill

`SKILL.md` is an [Agent Skills](https://agentskills.io) file. Point Claude Code,
Antigravity, Cursor or similar at this repo and the agent picks up when to reach
for which layer. The Python package does the deterministic work; the skill is
thin on purpose.

## Notes

- Offline tools read the **last-saved file**. Save before rendering, or call
  `board.save()` over the live layer.
- The live API speaks **nanometres**; the CLI speaks millimetres. Use
  `Vector2.from_xy_mm(...)`, never a hand conversion.
- **No ratsnest in renders** — SVG export omits airwires. Use the `unconnected`
  section of `kh drc` instead.
- **No schematic editing** — kipy's schematic module is present but
  non-functional. Explained in `docs/CAPABILITIES.md`.
- **No built-in autorouter**, but Specctra DSN export / SES import both work
  headless from Python, so an external router can be driven with no clicks.

## Documentation

- [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) — what KiCad exposes, measured
  rather than assumed, including why the schematic API that appears to exist in
  kipy's source does not actually work
- [`docs/LIVE_API.md`](docs/LIVE_API.md) — the kipy object model
- [`docs/RECIPES.md`](docs/RECIPES.md) — worked examples

## License

See [LICENSE](LICENSE).
