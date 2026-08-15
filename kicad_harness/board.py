"""Build a `.kicad_pcb` from a netlist, headless.

This is the step KiCad does not expose. `kicad-cli pcb import` only reads
foreign CAD formats, and pcbnew's own netlist import lives behind a GUI action,
so the usual advice is "open the board and press the button". There is no need:
`pcbnew` can load footprints from the library, create nets, and assign pads
directly, which is all that importing a netlist actually does.

What this does *not* do is place anything sensibly -- parts land on a scratch
grid, exactly as they do when KiCad imports a netlist into an empty board. Use
the live layer or a placement script for that, then look at the result.
"""

from __future__ import annotations

import os
from typing import Optional

from .geom import pcbnew
from .libs import LibraryIndex, _parse


def _kids(node, head):
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _val(node, head, default=None):
    for c in _kids(node, head):
        if len(c) > 1:
            return c[1]
    return default


def parse_netlist(path: str) -> dict:
    """Components and nets out of a `kicadsexpr` netlist."""
    tree = _parse(open(path, encoding="utf-8", errors="replace").read())
    export = None
    for node in tree:
        if isinstance(node, list) and node and node[0] == "export":
            export = node
            break
    if export is None:
        raise ValueError(
            f"{path} is not a KiCad s-expression netlist "
            "(export it with `kh netlist --format kicadsexpr`)"
        )

    comps = []
    for block in _kids(export, "components"):
        for c in _kids(block, "comp"):
            comps.append({
                "ref": _val(c, "ref"),
                "value": _val(c, "value", ""),
                "footprint": _val(c, "footprint", ""),
                "tstamps": _val(c, "tstamps", ""),
            })

    nets = []
    for block in _kids(export, "nets"):
        for n in _kids(block, "net"):
            nodes = [(_val(x, "ref"), _val(x, "pin")) for x in _kids(n, "node")]
            nets.append({"name": _val(n, "name", ""), "nodes": nodes})

    return {"components": comps, "nets": nets}


def _bbox_mm(fp) -> tuple[float, float]:
    try:
        b = fp.GetBoundingBox(False, False)
    except TypeError:
        b = fp.GetBoundingBox()
    return (pcbnew.ToMM(b.GetWidth()), pcbnew.ToMM(b.GetHeight()))


def board_from_netlist(
    netlist: str,
    out_pcb: str,
    project_dir: Optional[str] = None,
    copper_layers: int = 2,
    origin=(30.0, 30.0),
    row_width: float = 90.0,
    gap: float = 2.0,
    outline=None,
    rules: Optional[dict] = None,
    overwrite: bool = False,
) -> dict:
    """Create `out_pcb` holding every component and net in `netlist`."""
    out_pcb = os.path.abspath(out_pcb)
    if os.path.exists(out_pcb) and not overwrite:
        raise FileExistsError(
            f"{out_pcb} already exists; pass overwrite to replace it. "
            "Rebuilding a board from the netlist discards all placement and routing."
        )
    if os.path.exists(out_pcb + ".lck") or os.path.exists(
            os.path.join(os.path.dirname(out_pcb), "~" + os.path.basename(out_pcb) + ".lck")):
        raise RuntimeError(
            f"{out_pcb} looks open in KiCad (lock file present). "
            "Close it first -- this writes the file directly and KiCad would "
            "overwrite the result on its next save."
        )

    data = parse_netlist(netlist)
    index = LibraryIndex(project_dir)
    fp_libs = index.footprint_libs

    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(copper_layers)
    applied_rules = apply_rules(board, rules or {})

    placed, missing_lib, missing_fp = {}, [], []
    x, y, row_h = origin[0], origin[1], 0.0

    for comp in sorted(data["components"], key=lambda c: c["ref"] or ""):
        ident = comp["footprint"] or ""
        lib, _, name = ident.partition(":")
        if not name:
            missing_fp.append({"ref": comp["ref"], "footprint": ident,
                               "why": "no Footprint field on the symbol"})
            continue
        libdir = fp_libs.get(lib)
        if libdir is None:
            missing_lib.append({"ref": comp["ref"], "library": lib,
                                "known": sorted(fp_libs)[:8]})
            continue

        fp = pcbnew.FootprintLoad(libdir, name)
        if fp is None:
            missing_fp.append({"ref": comp["ref"], "footprint": ident,
                               "why": f"not found in {libdir}"})
            continue

        fp.SetReference(comp["ref"])
        fp.SetValue(comp["value"])
        try:
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
        except Exception:
            pass
        # The sheet path is what ties this footprint back to its symbol; without
        # it DRC's schematic-parity check reports every part as unknown.
        if comp["tstamps"]:
            try:
                fp.SetPath(pcbnew.KIID_PATH(comp["tstamps"]))
            except Exception:
                pass

        w, h = _bbox_mm(fp)
        if x > origin[0] and x + w > origin[0] + row_width:
            x = origin[0]
            y += row_h + gap
            row_h = 0.0
        fp.SetPosition(pcbnew.VECTOR2I_MM(x + w / 2.0, y + h / 2.0))
        fp.thisown = 0
        board.Add(fp)
        placed[comp["ref"]] = ident
        x += w + gap
        row_h = max(row_h, h)

    # Nets, then pads. A pad named in the netlist that the footprint does not
    # have is the classic symbol/footprint mismatch -- surface it, do not
    # silently leave the pad on no net.
    unmatched = []
    for net in data["nets"]:
        if not net["name"]:
            continue
        info = pcbnew.NETINFO_ITEM(board, net["name"])
        info.thisown = 0
        board.Add(info)
        for ref, pin in net["nodes"]:
            fp = board.FindFootprintByReference(ref)
            if fp is None:
                unmatched.append({"net": net["name"], "ref": ref, "pin": pin,
                                  "why": "component not on the board"})
                continue
            # Not FindPadByNumber: it returns the *first* match, and a pad
            # number is not unique. An exposed pad with thermal vias repeats
            # its number for every via (the TP4056's EPAD is 8 pads numbered
            # "9"), and a USB-C shield is four pads all numbered "SH". Netting
            # only the first leaves the rest floating, which DRC then reports
            # as the same pad shorting a net to no-net.
            pads = [p for p in fp.Pads() if p.GetNumber() == pin]
            if not pads:
                unmatched.append({
                    "net": net["name"], "ref": ref, "pin": pin,
                    "why": "footprint has no such pad",
                    "footprint_has": sorted({p.GetNumber() for p in fp.Pads() if p.GetNumber()}),
                })
                continue
            for pad in pads:
                pad.SetNet(info)

    if outline:
        _add_outline(board, *outline)

    board.BuildListOfNets()
    if not pcbnew.SaveBoard(out_pcb, board):
        raise RuntimeError(f"SaveBoard failed for {out_pcb}")

    return {
        "board": out_pcb,
        "components": len(placed),
        "nets": sum(1 for n in data["nets"] if n["name"]),
        "rules_mm": applied_rules,
        "missing_libraries": missing_lib,
        "missing_footprints": missing_fp,
        "unmatched_pads": unmatched,
        "ok": not (missing_lib or missing_fp or unmatched),
    }


def apply_rules(board, rules: dict) -> dict:
    """Set the constraints DRC checks against.

    An empty board comes up with KiCad's stock rules: 0.2 mm clearance and a
    0.3 mm minimum hole. Plenty of perfectly manufacturable footprints violate
    those -- a USB-C receptacle's pads are 0.15 mm apart, and a thermal-via
    pattern drills 0.2 mm -- so a board built from a netlist fails DRC on
    geometry the library itself shipped. Set the rules to match the parts.
    """
    ds = board.GetDesignSettings()
    out = {}

    def mm(key, attr):
        if rules.get(key) is not None:
            setattr(ds, attr, pcbnew.FromMM(float(rules[key])))
        out[key] = pcbnew.ToMM(getattr(ds, attr))

    mm("min_clearance", "m_MinClearance")
    mm("min_through_drill", "m_MinThroughDrill")
    mm("hole_to_hole", "m_HoleToHoleMin")
    mm("min_track_width", "m_TrackMinWidth")

    if rules.get("clearance") is not None or rules.get("track_width") is not None:
        classes = board.GetAllNetClasses()
        default = classes["Default"]
        if rules.get("clearance") is not None:
            default.SetClearance(pcbnew.FromMM(float(rules["clearance"])))
        if rules.get("track_width") is not None:
            default.SetTrackWidth(pcbnew.FromMM(float(rules["track_width"])))
    try:
        default = board.GetAllNetClasses()["Default"]
        out["clearance"] = pcbnew.ToMM(default.GetClearance())
        out["track_width"] = pcbnew.ToMM(default.GetTrackWidth())
    except Exception:
        pass
    return out


def place(pcb: str, moves: dict, save: bool = True) -> dict:
    """Move footprints on a board *file*, with no KiCad running.

    `kh exec` only reaches a board that is open in the editor. A board being
    built headlessly is not open, so placement needs a file-level path too.

    `moves` maps ref -> (x_mm, y_mm[, rotation_deg[, side]]), where side is
    "front" or "back".
    """
    pcb = os.path.abspath(pcb)
    board = pcbnew.LoadBoard(pcb)
    by_ref = {f.GetReference(): f for f in board.GetFootprints()}

    unknown = [r for r in moves if r not in by_ref]
    if unknown:
        raise KeyError(
            f"not on this board: {sorted(unknown)}; it has {sorted(by_ref)}"
        )

    done = {}
    for ref, spec in moves.items():
        fp = by_ref[ref]
        x, y = float(spec[0]), float(spec[1])
        rot = float(spec[2]) if len(spec) > 2 and spec[2] not in ("", None) else None
        side = spec[3] if len(spec) > 3 and spec[3] else None

        if side:
            on_back = fp.GetLayer() == pcbnew.B_Cu
            if (side == "back") != on_back:
                fp.Flip(fp.GetPosition(), False)
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        if rot is not None:
            fp.SetOrientationDegrees(rot)
        done[ref] = {"x": x, "y": y, "rotation": rot if rot is not None
                     else fp.GetOrientationDegrees(),
                     "side": "back" if fp.GetLayer() == pcbnew.B_Cu else "front"}

    if save and not pcbnew.SaveBoard(pcb, board):
        raise RuntimeError(f"SaveBoard failed for {pcb}")
    return {"board": pcb, "moved": done}


def set_outline(pcb: str, x: float, y: float, w: float, h: float,
                replace: bool = True) -> dict:
    """Set a rectangular Edge.Cuts outline on an existing board file."""
    pcb = os.path.abspath(pcb)
    board = pcbnew.LoadBoard(pcb)
    if replace:
        for d in list(board.GetDrawings()):
            if d.GetLayer() == pcbnew.Edge_Cuts:
                board.Remove(d)
    _add_outline(board, x, y, w, h)
    if not pcbnew.SaveBoard(pcb, board):
        raise RuntimeError(f"SaveBoard failed for {pcb}")
    return {"board": pcb, "outline_mm": {"x": x, "y": y, "w": w, "h": h}}


def _add_outline(board, x, y, w, h, width_mm: float = 0.1):
    """A rectangular Edge.Cuts outline. Without one, DRC has no board."""
    edge = pcbnew.Layer_to_LSET(pcbnew.Edge_Cuts) if hasattr(pcbnew, "Layer_to_LSET") else None
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I_MM(*a))
        seg.SetEnd(pcbnew.VECTOR2I_MM(*b))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(width_mm))
        seg.thisown = 0          # the board owns it now, not Python
        board.Add(seg)
    return edge
