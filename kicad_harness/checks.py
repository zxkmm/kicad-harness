"""DRC / ERC / netlist, reduced to something small enough to reason about.

The raw kicad-cli JSON reports are far too verbose to read in full when a board
has hundreds of violations, so everything here groups by rule type first and
only then lists individual instances.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections import Counter
from typing import Optional


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _load(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def _digest(items: list[dict], limit: int) -> dict:
    """Group violations by type, keep a bounded sample of each."""
    by_type: dict[str, list[dict]] = {}
    for v in items:
        by_type.setdefault(v.get("type", "unknown"), []).append(v)

    groups = []
    for vtype, vs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        samples = []
        for v in vs[:limit]:
            item = {"description": v.get("description", "")}
            sev = v.get("severity")
            if sev:
                item["severity"] = sev
            locs = []
            for it in v.get("items", []):
                loc = {"desc": it.get("description", "")}
                pos = it.get("pos")
                if isinstance(pos, dict):
                    loc["x"] = pos.get("x")
                    loc["y"] = pos.get("y")
                locs.append(loc)
            if locs:
                item["at"] = locs
            samples.append(item)
        groups.append(
            {
                "type": vtype,
                "count": len(vs),
                "truncated": len(vs) > limit,
                "samples": samples,
            }
        )
    return {"total": len(items), "by_type": groups}


def drc(
    pcb: str,
    severity: str = "error",
    limit: int = 5,
    all_track_errors: bool = False,
) -> dict:
    """Run DRC. `severity` is one of: all, error, warning."""
    with tempfile.TemporaryDirectory() as tmp:
        rpt = os.path.join(tmp, "drc.json")
        cmd = ["kicad-cli", "pcb", "drc", "--format", "json", "--output", rpt]
        if severity == "error":
            cmd.append("--severity-error")
        elif severity == "warning":
            cmd.append("--severity-warning")
        else:
            cmd.append("--severity-all")
        if all_track_errors:
            cmd.append("--all-track-errors")
        cmd.append(pcb)

        r = _run(cmd)
        if not os.path.exists(rpt):
            raise RuntimeError(f"DRC failed:\n{r.stdout}\n{r.stderr}")
        data = _load(rpt)

    violations = data.get("violations", [])
    unconnected = data.get("unconnected_items", [])
    footprint = data.get("schematic_parity", []) or data.get("footprint_parity", [])

    return {
        "source": os.path.abspath(pcb),
        "clean": not (violations or unconnected or footprint),
        "violations": _digest(violations, limit),
        "unconnected": _digest(unconnected, limit),
        "schematic_parity": _digest(footprint, limit),
    }


def erc(sch: str, severity: str = "all", limit: int = 5) -> dict:
    """Run ERC on a schematic (pass the root sheet)."""
    with tempfile.TemporaryDirectory() as tmp:
        rpt = os.path.join(tmp, "erc.json")
        cmd = ["kicad-cli", "sch", "erc", "--format", "json", "--output", rpt]
        if severity == "error":
            cmd.append("--severity-error")
        elif severity == "warning":
            cmd.append("--severity-warning")
        else:
            cmd.append("--severity-all")
        cmd.append(sch)

        r = _run(cmd)
        if not os.path.exists(rpt):
            raise RuntimeError(f"ERC failed:\n{r.stdout}\n{r.stderr}")
        data = _load(rpt)

    sheets = data.get("sheets", [])
    flat = []
    for sheet in sheets:
        for v in sheet.get("violations", []):
            v = dict(v)
            v.setdefault("sheet", sheet.get("path"))
            flat.append(v)

    return {
        "source": os.path.abspath(sch),
        "clean": not flat,
        "violations": _digest(flat, limit),
    }


def netlist(sch: str, out: str, fmt: str = "kicadsexpr") -> dict:
    """Export a netlist. Formats: kicadsexpr, kicadxml, cadstar, orcadpcb2, spice."""
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    r = _run(["kicad-cli", "sch", "export", "netlist", "--format", fmt, "--output", out, sch])
    if not os.path.exists(out):
        raise RuntimeError(f"netlist export failed:\n{r.stdout}\n{r.stderr}")
    return {"netlist": out, "format": fmt, "bytes": os.path.getsize(out)}


def bom(sch: str, out: str, fields: Optional[str] = None) -> dict:
    """Export a CSV BOM."""
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cmd = ["kicad-cli", "sch", "export", "bom", "--output", out]
    if fields:
        cmd += ["--fields", fields]
    cmd.append(sch)
    r = _run(cmd)
    if not os.path.exists(out):
        raise RuntimeError(f"BOM export failed:\n{r.stdout}\n{r.stderr}")
    return {"bom": out, "bytes": os.path.getsize(out)}
