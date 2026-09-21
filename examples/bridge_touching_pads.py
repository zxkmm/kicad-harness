"""Bridge touching pads on the same net with copper rectangles to eliminate V-gaps.

When two surface-mount pads on the same net touch (e.g. series resistors
butted pad-to-pad, or vertically stacked shunt resistors), the rounded corners
leave a tiny notch ("V-gap"). Furthermore, KiCad DRC may report them as unconnected
if there is no copper item joining their centers.

This script detects touching pad pairs on the same net and adds filled copper
rectangles (PCB_SHAPE) bridging the pad centers across their shared span.

Usage:
    1. Inside KiCad PCB Editor Scripting Console:
           exec(open("examples/bridge_touching_pads.py").read())
       Then press Ctrl+S to save.

    2. From the command line:
           python3 examples/bridge_touching_pads.py --pcb path/to/project
           python3 examples/bridge_touching_pads.py --pcb path/to/project --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import pcbnew
    if hasattr(pcbnew, "SwigPyIterator") and not hasattr(pcbnew.SwigPyIterator, "next"):
        pcbnew.SwigPyIterator.next = pcbnew.SwigPyIterator.__next__
except ImportError:
    pcbnew = None


def bridge_touching_pads(board, layer=None, tolerance_nm=1000, dry_run=False):
    """Find touching pad pairs on the same net and add copper rectangular bridges.

    board: pcbnew.BOARD instance
    layer: pcbnew copper layer (default: pcbnew.F_Cu)
    tolerance_nm: touch tolerance in nanometres (default: 1000 nm = 1 µm)
    dry_run: if True, do not add shapes to board

    Returns: list of dicts describing each bridge added
    """
    if layer is None:
        layer = pcbnew.F_Cu

    pads = [p for p in board.GetPads() if p.IsOnLayer(layer) and p.GetNetCode() > 0]
    bridges = []

    for i, a in enumerate(pads):
        for c in pads[i + 1:]:
            # Must share the same net, but belong to different footprints
            if a.GetNetCode() != c.GetNetCode() or a.GetParent() == c.GetParent():
                continue

            ba, bc = a.GetBoundingBox(), c.GetBoundingBox()
            ba2 = pcbnew.BOX2I(ba.GetPosition(), ba.GetSize())
            ba2.Inflate(tolerance_nm)
            if not ba2.Intersects(bc):
                continue

            pa, pc = a.GetPosition(), c.GetPosition()
            if abs(pa.x - pc.x) < abs(pa.y - pc.y):
                # Stacked vertically: span X across shared width, span Y between pad centers
                x1 = max(ba.GetLeft(), bc.GetLeft())
                x2 = min(ba.GetRight(), bc.GetRight())
                y1 = pa.y
                y2 = pc.y
                orientation = "vertical"
            else:
                # Side-by-side: span Y across shared height, span X between pad centers
                y1 = max(ba.GetTop(), bc.GetTop())
                y2 = min(ba.GetBottom(), bc.GetBottom())
                x1 = pa.x
                x2 = pc.x
                orientation = "horizontal"

            fp_a = a.GetParentFootprint().GetReference() if a.GetParentFootprint() else "?"
            fp_c = c.GetParentFootprint().GetReference() if c.GetParentFootprint() else "?"
            net_name = a.GetNetname()

            info = {
                "footprints": (fp_a, fp_c),
                "pads": (a.GetNumber(), c.GetNumber()),
                "net": net_name,
                "orientation": orientation,
                "box_mm": (x1 / 1e6, y1 / 1e6, x2 / 1e6, y2 / 1e6),
            }
            bridges.append(info)

            if not dry_run:
                s = pcbnew.PCB_SHAPE(board)
                s.SetShape(pcbnew.SHAPE_T_RECT)
                s.SetStart(pcbnew.VECTOR2I(x1, y1))
                s.SetEnd(pcbnew.VECTOR2I(x2, y2))
                s.SetFilled(True)
                s.SetWidth(0)
                s.SetLayer(layer)
                s.SetNet(a.GetNet())
                board.Add(s)

    return bridges


def main():
    if pcbnew is None:
        sys.exit("Error: pcbnew module not found. Run under KiCad python or with --system-site-packages venv.")

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pcb", default=".", help="Board file or project directory (default: current dir)")
    parser.add_argument("--tol", type=int, default=1000, help="Touch tolerance in nm (default: 1000 = 1 um)")
    parser.add_argument("--layer", default="F.Cu", choices=["F.Cu", "B.Cu"], help="Copper layer (default: F.Cu)")
    parser.add_argument("--dry-run", action="store_true", help="Report touching pads without modifying board")
    args = parser.parse_args()

    # Resolve PCB path
    pcb_path = args.pcb
    if os.path.isdir(pcb_path):
        candidates = [os.path.join(pcb_path, f) for f in os.listdir(pcb_path) if f.endswith(".kicad_pcb")]
        if not candidates:
            sys.exit(f"No .kicad_pcb found in {pcb_path}")
        pcb_path = candidates[0]

    board = pcbnew.LoadBoard(pcb_path)
    layer = pcbnew.F_Cu if args.layer == "F.Cu" else pcbnew.B_Cu

    bridges = bridge_touching_pads(board, layer=layer, tolerance_nm=args.tol, dry_run=args.dry_run)

    print(f"{'Preview:' if args.dry_run else 'Applied:'} {len(bridges)} pad bridges on {args.layer}")
    for b in bridges[:10]:
        fp = f"{b['footprints'][0]}.{b['pads'][0]} <-> {b['footprints'][1]}.{b['pads'][1]}"
        print(f"  {fp:20s}  net={b['net']:20s}  ({b['orientation']})")
    if len(bridges) > 10:
        print(f"  ... and {len(bridges) - 10} more")

    if not args.dry_run and bridges:
        board.Save(pcb_path)
        print(f"Saved {pcb_path}")


# When executed inside KiCad's GUI Python console:
if __name__ == "__main__" and "pcbnew" in sys.modules and len(sys.argv) == 1:
    try:
        b = pcbnew.GetBoard()
        if b and b.GetFileName():
            bridges = bridge_touching_pads(b)
            pcbnew.Refresh()
            print(f"{len(bridges)} bridges added to open board. Press Ctrl+S to save.")
        else:
            main()
    except Exception:
        main()
elif __name__ == "__main__":
    main()
