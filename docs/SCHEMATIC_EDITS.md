# Editing a schematic that already exists

`RECIPES.md` covers *authoring* a `.kicad_sch` from nothing. This covers the
harder case: changing one that a human drew, without wrecking the rest of it.

There is no schematic API (`CAPABILITIES.md` explains why), so this is text
surgery on s-expressions. Everything below was learned the hard way on a real
three-sheet hierarchical board.

## Before you touch the file: is an editor holding it?

Offline edits and a running Eeschema will silently fight, and Eeschema wins —
its next Ctrl+S writes the whole file from memory and your edits are gone.

```bash
kh live                       # look at "editor_open"
ls ~*.lck                     # in the project dir
```

`~<project>.kicad_pro.lck` **on its own** means only the project manager is
open — no document is loaded, and offline editing is safe. A
`~<sheet>.kicad_sch.lck` means Eeschema has that sheet; stop and ask the user
to close it. `kh live` reporting `editor_open: false` while KiCad is clearly
running is the same signal: project manager only.

## Chunk the file, don't reformat it

A `.kicad_sch` is a flat list of top-level items, each one starting at column 0
plus one tab: `\t(symbol`, `\t(wire`, `\t(junction`, `\t(global_label`, ... Split
on those, edit whole chunks, and reassemble. Untouched chunks come back
byte-identical, so `git diff` shows only what you meant to change.

```python
import re
_TOP = re.compile(r'^\t\(', re.M)

def chunks(t):
    """(start, end) of every top-level '\\t(...)' block. Quote-aware."""
    out, i, n = [], 0, len(t)
    while (m := _TOP.search(t, i)):
        s = m.start()
        d, j, instr = 1, s + 2, False
        while j < n:
            c = t[j]
            if instr:
                if c == '\\': j += 2; continue
                if c == '"': instr = False
            elif c == '"': instr = True
            elif c == '(': d += 1
            elif c == ')':
                d -= 1
                if d == 0: break
            j += 1
        out.append((s, j + 1)); i = j + 1
    return out
```

The quote handling is not optional: `Description` properties contain escaped
quotes (`"Power symbol creates a global label with name \"GND\" , ground"`), and
a naive paren counter walks straight past the end of the block.

Do **not** round-trip through a generic s-expression writer. KiCad's formatter
has per-node quirks, and reformatting the whole file buries your three-line
change in a 4000-line diff.

## Clone an existing symbol; never compose one by hand

To add a part, copy a placed symbol that has **the same `lib_id` and the same
rotation**, translate every `(at x y ...)` in the block, then swap the
Reference, the Value and the uuids.

```python
AT = re.compile(r'\(at (-?[\d.]+) (-?[\d.]+)')
def shift(block, dx, dy):
    return AT.sub(lambda m: '(at %s %s' % (fmt(float(m[1]) + dx),
                                           fmt(float(m[2]) + dy)), block)
```

Why cloning and not a template: a symbol's field layout is *not* uniform. A
`Device:C` at rotation 0 puts its Reference at `(-3.81, +1.2701)` with
`(justify right)`; at 180 it is `(+3.81, -1.2701)` with `(justify left)`; at 270
it is `(0, -7.62)` with a property angle of 90 and no justify at all. Get one
wrong and the text lands on top of a neighbouring part. Cloning inherits all of
it for free — and the `(at ...)` translation is the only thing you have to
reason about.

Match the rotation, though. Cloning a rot-0 part and then editing `(at x y 0)`
to `(at x y 90)` rotates the body but leaves the fields where they were.

## Property angles are relative to the symbol

This is the one that bites hardest, because the file looks correct.

A property's rendered angle is **symbol angle + property angle, mod 360**. A
field written as `(at x y 0)` on a symbol placed at `(at ... 270)` renders
*vertically*, not horizontally. Two such fields 2.54 mm apart in x — the normal
autoplaced spacing for a horizontal part — then overlap each other, and the
netlist is perfectly fine while the sheet is unreadable.

To force horizontal text on a rotated symbol, set the property angle to
`(360 - symbol_angle) % 360`:

| symbol `(at)` angle | property angle for horizontal text |
|---|---|
| 0   | 0   |
| 90  | 270 |
| 180 | 180 |
| 270 | 90  |

## Keep uuids when you move or rebuild a symbol

The board links each footprint to its symbol by uuid. Hand a moved symbol a
fresh uuid and the next *Update PCB from Schematic* treats it as a delete plus
an add: the footprint loses its placement and its routing.

Translating a block in place keeps the uuids automatically. If you have to
*rebuild* one — a rotation change, say, where you clone a differently-rotated
model — graft the old identity back on:

```python
PINUU = re.compile(r'\(pin "([^"]+)"\n(\s*)\(uuid "([0-9a-f-]{36})"\)')

def graft_uuids(new, old):
    """same symbol uuid, same per-pin uuids -> the PCB still recognises it"""
    new = new.replace('(uuid "%s")' % sym_uuid(new),
                      '(uuid "%s")' % sym_uuid(old), 1)
    pu = {m[1]: m[3] for m in PINUU.finditer(old)}
    return PINUU.sub(lambda m: '(pin "%s"\n%s(uuid "%s")'
                     % (m[1], m[2], pu[m[1]]), new)
```

The symbol's own uuid is the *first* one in the block, before the properties.

New parts need genuinely new uuids everywhere — `uuid.uuid4()` on the symbol and
on each `(pin "N" (uuid ...))`.

## Pin-to-pin connections have no wire

`RECIPES.md` says connectivity is decided purely by coordinates, which is true,
but it is easy to read that as "every pin sits on a wire endpoint". It does not.

Two pins at the same coordinate are connected with nothing between them, and
human-drawn sheets are full of it — every `power:GND` symbol butted directly
onto a capacitor pin, for example. If you write a checker that flags pins not
landing on a wire end or a junction, it will report a third of a healthy sheet
as broken. Include pin coordinates in the connectivity graph, and treat a
coincident pin pair as an edge.

This also means a *stale* pin-to-pin pair is invisible: move a part 2.54 mm and
a connection that was never drawn just disappears, with no dangling wire to
show for it. Re-read the netlist after any move.

## Label kind decides the net name

- `global_label "FOO"` → the net is named `FOO`.
- `label "FOO"` (local) on a sub-sheet → the net is named `/SheetName/FOO`.

If a spec calls for an exact net name, or the net has to span sheets, it must be
a global label. Renaming a net means editing the label text — auto-generated
`Net-(U6-V_{OUT})` style names vanish the moment any label lands on that net.

To *name* a net that currently has none, the tidiest edit is a stub off the
existing wire: a junction on the wire, two short wires forming an L, and the
label on the free end. That keeps the label out of the way of the parts. Use
rotation 0 (`justify left`, text runs right) or 180 (`justify right`, text runs
left) — those are the two forms you can copy verbatim from an existing label.

## Netclass patterns match auto-generated net names

Projects often put RF or diff-pair nets into a netclass by listing their names
under `net_settings.netclass_patterns` in the `.kicad_pro`, **including
auto-generated ones** like `Net-(J5-In)`. Insert a part into such a net and its
two halves get *new* auto names. The old pattern then matches only the stub
that kept the original driver, and the rest falls back to `Default`: wrong
track width on the board, and no error anywhere.

After any edit that splits a net, compare the `(class ...)` of every net
against the baseline netlist. Add patterns for the new names by inserting text
into the `.kicad_pro`; don't `json.dump` it, which reformats the whole file.
The schematic render also shows it: netclass-coloured wires turn back to
default green on exactly the new segments.

## The netlist does not carry DNP

`kh netlist` (kicadsexpr, via `kicad-cli`) has no `dnp` field. Asserting "R6 and
R8 are still DNP" against the netlist silently passes on an empty set. Read it
from the source instead:

```bash
grep -c '(dnp yes)' *.kicad_sch
```

Same for anything else that lives on the symbol rather than the connection:
custom fields, `in_bom`, `exclude_from_sim`.

## The loop: baseline, edit, diff, look

Never check an edited schematic against perfection — check it against **what it
was**. A design that had six ERC violations before should have the same six
after, and any assertion that a net is unchanged needs the old netlist to
compare with.

```bash
cp *.kicad_sch /tmp/orig/                     # 1. back up
kh netlist --sch . --out /tmp/baseline.net    # 2. baseline BEFORE editing
kh erc --sch .                                #    and its violation count

python3 my_edit.py                            # 3. edit

kh netlist --sch . --out /tmp/after.net       # 4. diff net membership,
kh erc --sch .                                #    values, part count
kh sview --sch . --all --out /tmp/s.png       # 5. render every sheet
kh sview --sch . --sheet Power --region 130,112,102,43 --out /tmp/z.png
```

Then **read the images**. A clean ERC and a correct netlist still allow text
sitting on top of a symbol, a label overlapping a wire, or a part parked on
another part — none of those are electrical errors, and only the picture shows
them. `--region` takes `x,y,w,h` in sheet millimetres with y growing downward,
the same coordinates that are in the file, so you can zoom straight to the part
you just moved.

Make the edit script re-runnable from the backup rather than incremental. You
will want to adjust a coordinate four times, and replaying from a known-good
original beats trying to undo.

## What to assert

Net membership as exact sets, both directions:

```python
assert nets['XO_3V3'] == sorted('U6.5 C19.2 C26.2 C27.2 FB1.1 R9.1 X1.4'.split())
```

Then the things that catch collateral damage, which is what actually goes wrong:

- every net you did *not* touch is byte-identical to baseline
- `GND`'s delta is exactly the pins you added and removed
- component count = old − deleted + added
- values of parts you never mentioned are unchanged
- the set of `unconnected-(...)` nets is unchanged
- `(dnp yes)` count is unchanged (from the `.kicad_sch`, not the netlist)

The "every other net is identical" check is the one that earns its keep. It
catches the wire you shortened by 2.54 mm too much, which is the failure mode
this kind of editing actually has.
