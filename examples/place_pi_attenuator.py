"""Lay out a pi-shape resistor attenuator ladder (offline or live).

Run with:
    python3 examples/place_pi_attenuator.py --pcb <path-to-board>
or via live API:
    kh exec examples/place_pi_attenuator.py

Features:
- Handles standard single-resistor shunt arms:
      -|-
- Handles multi-resistor stacked vertical shunt arms:
       |
      -|-
       |
- Handles arbitrary length series chains (touching pad-to-pad, 0 mm gap)
- Exact pad-edge-to-pad-edge neck spacing (e.g. 0.8 mm) between series and shunt pads
- Alternates 0°/180° and 90°/-90° rotations to match pin connections and prevent track crossings.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


# ── Geometry helpers (0805 metric default) ──────────────────────────
def get_geometry(pad_w: float = 1.025, pad_h: float = 1.4, pad_center: float = 0.9125,
                 neck_gap: float = 0.8, series_gap: float = 0.0) -> dict:
    """Calculate pitch and offsets for SMD chip resistors.

    neck_gap: pad-edge to pad-edge distance between horizontal series pad
              and vertical shunt pad.
    series_gap: pad-edge to pad-edge distance between adjacent series pads.
    """
    pad_x_half = pad_w / 2
    pad_y_half = pad_h / 2
    series_pad_outer = pad_center + pad_x_half
    shunt_pad_x = pad_y_half

    neck_cc = neck_gap + shunt_pad_x + series_pad_outer
    series_cc = series_gap + 2 * series_pad_outer
    shunt_y = pad_center + pad_x_half
    shunt_stack_pitch = 2 * (pad_center + pad_x_half)

    return {
        "neck_cc": neck_cc,
        "series_cc": series_cc,
        "shunt_y": shunt_y,
        "shunt_stack_pitch": shunt_stack_pitch,
    }


def layout_ladder(stages: list[dict], x0: float, y0: float, geom: dict) -> dict[str, list[float]]:
    """Place a chain of pi stages starting at (x0, y0).

    stages: list of dicts, each representing a junction node:
      {
        "shunt_up":   [ref, ...],    # list of refs extending UP from signal line
        "shunt_down": [ref, ...],    # list of refs extending DOWN from signal line
        "series":     [ref, ...],    # series chain connecting to the next junction
      }
    """
    placement = {}
    jx = x0

    for node in stages:
        # 1. Place vertical shunt arm UP (-Y direction)
        arm_up = node.get("shunt_up", [])
        for idx, r in enumerate(arm_up):
            y = y0 - geom["shunt_y"] - idx * geom["shunt_stack_pitch"]
            rot = -90 if (idx % 2 == 0) else 90
            placement[r] = [round(jx, 4), round(y, 4), rot]

        # 2. Place vertical shunt arm DOWN (+Y direction)
        arm_down = node.get("shunt_down", [])
        for idx, r in enumerate(arm_down):
            y = y0 + geom["shunt_y"] + idx * geom["shunt_stack_pitch"]
            rot = 90 if (idx % 2 == 0) else -90
            placement[r] = [round(jx, 4), round(y, 4), rot]

        # 3. Place horizontal series chain to next junction
        srs = node.get("series", [])
        if srs:
            first_s_x = jx + geom["neck_cc"]
            for idx, r in enumerate(srs):
                sx = first_s_x + idx * geom["series_cc"]
                rot = 0 if (idx % 2 == 0) else 180
                placement[r] = [round(sx, 4), round(y0, 4), rot]

            last_s_x = first_s_x + (len(srs) - 1) * geom["series_cc"]
            jx = last_s_x + geom["neck_cc"]

    return placement


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pcb", default=".", help="Board file or project dir (default: .)")
    parser.add_argument("--dry-run", action="store_true", help="Print placement JSON without applying")
    parser.add_argument("--x0", type=float, default=30.0, help="Starting X (mm)")
    parser.add_argument("--y0", type=float, default=20.0, help="Starting Y (mm)")
    parser.add_argument("--neck", type=float, default=0.8, help="Neck pad-edge gap (mm, default: 0.8)")
    args = parser.parse_args()

    geom = get_geometry(neck_gap=args.neck)

    # Example 3-stage attenuator with both 1-element and 2-element vertical shunt arms:
    example_stages = [
        {"shunt_up": ["R1"],        "shunt_down": ["R2"],        "series": ["R5", "R6", "R7"]},
        {"shunt_up": ["R13"],       "shunt_down": ["R14"],       "series": ["R18", "R19", "R20"]},
        {"shunt_up": ["R62", "R64"],"shunt_down": ["R73", "R74"],"series": ["R78", "R79", "R80", "R81"]},
        {"shunt_up": ["R135", "R136"],"shunt_down": ["R139", "R140"], "series": []},
    ]

    placement = layout_ladder(example_stages, args.x0, args.y0, geom)

    if args.dry_run:
        print(json.dumps(placement, indent=2))
        return

    json_path = "/tmp/_pi_att_placement.json"
    with open(json_path, "w") as f:
        json.dump(placement, f, indent=2)

    cmd = ["kh", "place", "--pcb", args.pcb, "--json", json_path]
    subprocess.run(cmd, check=True)
    if os.path.exists(json_path):
        os.remove(json_path)


if __name__ == "__main__":
    main()
