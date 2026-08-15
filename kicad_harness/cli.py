"""kh -- command line front end.

Every command prints JSON on stdout so the caller can parse it, and nothing
else. Errors go to stderr with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from . import checks, render
from .geom import Box, BoardView


# --------------------------------------------------------------------------
# project resolution


def _find(pattern: str, where: str) -> list[str]:
    """Project files in `where`, ignoring KiCad's own scratch files.

    A dirty editor drops `_autosave-foo.kicad_pcb` next to `foo.kicad_pcb`, and
    saving leaves `foo.kicad_pcb-bak`. Neither is the project.
    """
    hits = sorted(glob.glob(os.path.join(where, pattern)))
    return [
        h
        for h in hits
        if not os.path.basename(h).startswith(("_autosave-", "~"))
        and not h.endswith("-bak")
    ]


def resolve_pcb(arg: str | None) -> str:
    if arg and os.path.isfile(arg):
        return arg
    where = arg if arg and os.path.isdir(arg) else os.getcwd()
    hits = _find("*.kicad_pcb", where)
    if not hits:
        raise SystemExit(f"no .kicad_pcb found in {where} (pass one explicitly)")
    if len(hits) > 1:
        raise SystemExit(
            "multiple boards found, pass one explicitly:\n  " + "\n  ".join(hits)
        )
    return hits[0]


def resolve_sch(arg: str | None) -> str:
    """Resolve the *root* schematic -- the one named after the project."""
    if arg and os.path.isfile(arg):
        return arg
    where = arg if arg and os.path.isdir(arg) else os.getcwd()
    pro = _find("*.kicad_pro", where)
    if pro:
        root = os.path.splitext(pro[0])[0] + ".kicad_sch"
        if os.path.isfile(root):
            return root
    hits = _find("*.kicad_sch", where)
    if not hits:
        raise SystemExit(f"no .kicad_sch found in {where} (pass one explicitly)")
    if len(hits) > 1:
        raise SystemExit(
            "multiple schematics and no project file; pass the root sheet explicitly:\n  "
            + "\n  ".join(hits)
        )
    return hits[0]


def emit(obj):
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


# --------------------------------------------------------------------------
# commands


def cmd_info(a):
    emit(BoardView(resolve_pcb(a.pcb)).summary())


def cmd_ls(a):
    bv = BoardView(resolve_pcb(a.pcb))
    comps = bv.components()
    if a.filter:
        needle = a.filter.lower()
        comps = [
            c for c in comps
            if needle in c.ref.lower()
            or needle in c.value.lower()
            or needle in c.footprint.lower()
        ]
    emit([c.as_dict() for c in comps])


def cmd_view(a):
    region = None
    if a.region:
        try:
            x, y, w, h = (float(v) for v in a.region.split(","))
        except ValueError:
            raise SystemExit("--region wants four numbers: x,y,w,h (mm)")
        region = Box(x, y, w, h)
    emit(
        render.render(
            pcb=resolve_pcb(a.pcb),
            out_png=a.out,
            layers=a.layers,
            refs=a.refs.split(",") if a.refs else None,
            region=region,
            margin_mm=a.margin,
            width_px=a.width,
            background=a.background,
            theme=a.theme,
            square=not a.no_square,
        )
    )


def cmd_drc(a):
    emit(checks.drc(resolve_pcb(a.pcb), severity=a.severity, limit=a.limit,
                    all_track_errors=a.all_track_errors))


def cmd_erc(a):
    emit(checks.erc(resolve_sch(a.sch), severity=a.severity, limit=a.limit))


def cmd_netlist(a):
    emit(checks.netlist(resolve_sch(a.sch), a.out, fmt=a.format))


def cmd_bom(a):
    emit(checks.bom(resolve_sch(a.sch), a.out, fields=a.fields))


def cmd_live(a):
    from . import live

    info = live.status()
    emit(info)
    if not info.get("connected"):
        sys.exit(1)


def cmd_exec(a):
    from . import live

    emit(live.run_script(a.script, a.args))


def _index(a):
    from .libs import LibraryIndex

    proj = getattr(a, "project", None)
    if proj and os.path.isfile(proj):
        proj = os.path.dirname(proj)
    return LibraryIndex(proj)


def cmd_sym(a):
    idx = _index(a)
    if a.pins:
        emit(idx.symbol_detail(a.pins))
    elif a.query:
        emit(idx.search_symbols(a.query, a.limit))
    else:
        emit(idx.summary())


def cmd_fp(a):
    idx = _index(a)
    if a.query:
        emit(idx.search_footprints(a.query, a.limit))
    else:
        emit(idx.summary())


def cmd_validate(a):
    idx = _index(a)
    res = idx.validate(
        symbols=a.symbols.split(",") if a.symbols else [],
        footprints=a.footprints.split(",") if a.footprints else [],
    )
    emit(res)
    if not res["ok"]:
        sys.exit(1)


def cmd_nets(a):
    bv = BoardView(resolve_pcb(a.pcb))
    if a.net:
        emit({"net": a.net, "pads": bv.pads_on_net(a.net)})
    else:
        emit(bv.nets())


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kh", description="KiCad agent harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    def board_arg(sp):
        sp.add_argument("--pcb", help="board file or project dir (default: cwd)")

    def sch_arg(sp):
        sp.add_argument("--sch", help="root schematic or project dir (default: cwd)")

    s = sub.add_parser("info", help="board summary")
    board_arg(s)
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("ls", help="list components with positions and bboxes")
    board_arg(s)
    s.add_argument("--filter", help="substring match on ref/value/footprint")
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("nets", help="list nets, or the pads on one net")
    board_arg(s)
    s.add_argument("--net", help="show pads on this net")
    s.set_defaults(func=cmd_nets)

    s = sub.add_parser("view", help="render board or region to PNG")
    board_arg(s)
    s.add_argument("--out", default="view.png", help="output PNG path")
    s.add_argument("--refs", help="comma-separated refs to frame, e.g. L1,C1,C2")
    s.add_argument("--region", help="explicit region x,y,w,h in mm")
    s.add_argument("--layers", default="front",
                   help="preset (%s) or raw KiCad layer list" % ", ".join(render.PRESETS))
    s.add_argument("--margin", type=float, default=2.0, help="mm around --refs")
    s.add_argument("--width", type=int, default=1200, help="output width in px")
    s.add_argument("--background", default="white")
    s.add_argument("--theme", help="KiCad color theme name")
    s.add_argument("--no-square", action="store_true",
                   help="do not pad the region to a square")
    s.set_defaults(func=cmd_view)

    s = sub.add_parser("drc", help="design rules check")
    board_arg(s)
    s.add_argument("--severity", default="error", choices=["all", "error", "warning"])
    s.add_argument("--limit", type=int, default=5, help="samples per violation type")
    s.add_argument("--all-track-errors", action="store_true")
    s.set_defaults(func=cmd_drc)

    s = sub.add_parser("erc", help="electrical rules check")
    sch_arg(s)
    s.add_argument("--severity", default="all", choices=["all", "error", "warning"])
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_erc)

    s = sub.add_parser("netlist", help="export netlist")
    sch_arg(s)
    s.add_argument("--out", default="netlist.net")
    s.add_argument("--format", default="kicadsexpr",
                   choices=["kicadsexpr", "kicadxml", "cadstar", "orcadpcb2", "spice",
                            "spicemodel", "allegro", "pads"])
    s.set_defaults(func=cmd_netlist)

    s = sub.add_parser("bom", help="export CSV BOM")
    sch_arg(s)
    s.add_argument("--out", default="bom.csv")
    s.add_argument("--fields")
    s.set_defaults(func=cmd_bom)

    def proj_arg(sp):
        sp.add_argument("--project", help="project dir, to include its local lib tables")

    s = sub.add_parser("sym", help="search the user's symbol libraries")
    s.add_argument("query", nargs="?", help="omit to show library counts")
    s.add_argument("--pins", metavar="LIB:NAME", help="full pin list for one symbol")
    s.add_argument("--limit", type=int, default=40)
    proj_arg(s)
    s.set_defaults(func=cmd_sym)

    s = sub.add_parser("fp", help="search the user's footprint libraries")
    s.add_argument("query", nargs="?", help="omit to show library counts")
    s.add_argument("--limit", type=int, default=40)
    proj_arg(s)
    s.set_defaults(func=cmd_fp)

    s = sub.add_parser("validate", help="check Lib:Name ids exist before using them")
    s.add_argument("--symbols", help="comma-separated, e.g. Device:R,Device:C")
    s.add_argument("--footprints", help="comma-separated")
    proj_arg(s)
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("live", help="check the live IPC connection to running KiCad")
    s.set_defaults(func=cmd_live)

    s = sub.add_parser("exec", help="run a Python script against running KiCad")
    s.add_argument("script")
    s.add_argument("args", nargs="*")
    s.set_defaults(func=cmd_exec)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
