# Demo prompt — TP4056 charger from scratch

Paste the block below into a fresh Claude Code session started in the
`kicad-harness` repo. It builds a small, genuinely useful board while
stress-testing the harness and improving it.

---

```text
You have a KiCad harness in this repo. Read SKILL.md and docs/CAPABILITIES.md
first — CAPABILITIES.md records what KiCad actually exposes, measured on this
machine, including several things that look like they work and do not.

Install it if needed with ./setup.sh, then use ./.venv/bin/kh for every command.

## The board

Build a TP4056 single-cell Li-ion charger — the small DIY module everyone has a
drawer full of. Make it in a NEW directory outside this repo, e.g.
~/kicad_demo/tp4056_charger/. Do not touch any of my existing projects.

Scope, deliberately small:

- USB-C receptacle input (5 V), with the two 5.1k CC pull-downs
- TP4056 charger IC
- PROG resistor setting ~500 mA charge current — derive the value from the
  datasheet relation, state the arithmetic, do not guess
- CHRG and STDBY indicator LEDs with series resistors
- Input and battery decoupling capacitors
- 2-pin JST or screw-terminal battery output, and a 2-pin load output
- Single sided, hand-solderable parts (0805 passives), roughly 25 x 20 mm

Follow the datasheet's application circuit. Where you make an engineering
choice, say why in one line.

## Rules that matter

1. NEVER invent a library identifier. Look up every symbol and footprint with
   `kh sym` / `kh fp`, get real pin numbers with `kh sym --pins`, and confirm the
   whole set with `kh validate` before writing any netlist. Ids like
   `Device:LED_Generic` are exactly the kind of thing that looks right and does
   not exist.

2. Close the loop visually. After any placement, render with `kh view` and
   actually read the PNG. Do not report a placement as done on the strength of a
   script exiting 0.

3. Check your work. `kh erc` on the schematic, `kh drc` on the board. Report the
   real counts, including the ones you did not fix.

4. If a step needs me to click something in the GUI, stop and tell me exactly
   what to click. Do not pretend it happened.

## The second job: improve the harness

While building the board, you are also dogfooding the harness. Keep
`ISSUES.md` in the kicad-harness repo and append to it as you go:

- anything that was awkward, missing, or needed a workaround
- anything whose output was too verbose, too terse, or the wrong shape
- anything documented that turned out not to be true

Fix what is cheap and clearly right, and note what you would do about the rest.
Do not refactor broadly — the harness is deliberately a plain deterministic CLI
with no API keys and no model calls, driven by a skill. Keep it that way.

Known gaps, in priority order — solving the first one is the most valuable thing
you could do this session:

- **Netlist to board.** There is no headless path from a netlist or schematic to
  a .kicad_pcb. `kicad-cli pcb import` only handles foreign CAD formats, and
  pcbnew exposes no netlist import. Two candidate routes: build the board
  directly with pcbnew (`FootprintLoad` + `Add` + net assignment), or trigger
  the GUI action `pcbnew.EditorControl.importNetlist` over the live API and
  accept one click. Work out which is actually viable and implement it as
  `kh board-from-netlist` or similar.
- **No schematic API.** kipy ships schematic wrappers whose protobufs are
  missing, so `import kipy.schematic` fails outright. Author `.kicad_sch` as
  s-expression text instead, and validate with `kh erc`.
- **No ratsnest in renders.** Use the `unconnected` section of `kh drc`.

Start by telling me your plan and which parts you have verified exist versus
which you are assuming. Then build.
```

---

## What this exercises

| Harness capability | Where it gets used |
|---|---|
| `kh sym` / `kh fp` / `kh sym --pins` | resolving TP4056, USB-C, LEDs, passives |
| `kh validate` | gate before the netlist is written |
| `kh erc` | schematic correctness |
| `kh view` + reading the PNG | placement review |
| `kh drc` | courtyard overlap, clearance, unconnected |
| `kh exec` / live IPC | positioning parts in the running editor |

The TP4056 is a good demo because it is small enough to finish, common enough
that a model has strong (and therefore dangerous) priors about it, and has a
real pin-numbering trap: the SOIC-8 part has a **9th EPAD pin** that is easy to
forget and impossible to notice from memory.
