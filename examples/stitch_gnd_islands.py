"""Add GND stitching vias to GND fill islands that have no via / PTH into the other layer.
usage: python3 stitch.py board.kicad_pcb   (zones must be filled)"""
import sys, math
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
import pcbnew
if not hasattr(pcbnew.SwigPyIterator, "next"):
    pcbnew.SwigPyIterator.next = pcbnew.SwigPyIterator.__next__
TO, FROM = pcbnew.ToMM, pcbnew.FromMM
RES = 0.05; VIA_D, VIA_DRILL = 0.6, 0.35; HOLE_CLR = 0.25
b = pcbnew.LoadBoard(sys.argv[1])
L2 = [pcbnew.F_Cu, pcbnew.B_Cu]

def chain(c): return [(TO(c.CPoint(j).x), TO(c.CPoint(j).y)) for j in range(c.PointCount())]
islands = {L: [] for L in L2}
for z in b.Zones():
    if z.GetNetname() != "GND" or z.GetIsRuleArea(): continue
    for L in L2:
        if not z.IsOnLayer(L): continue
        ps = z.GetFilledPolysList(L)
        for i in range(ps.OutlineCount()):
            islands[L].append((abs(ps.Outline(i).Area()) / 1e12, chain(ps.Outline(i)),
                               [chain(ps.Hole(i, h)) for h in range(ps.HoleCount(i))]))
holes = []  # (x,y,r, is_gnd_bridge)
for t in b.GetTracks():
    if t.Type() == pcbnew.PCB_VIA_T:
        p = t.GetPosition(); holes.append((TO(p.x), TO(p.y), TO(t.GetDrillValue()) / 2, t.GetNetname() == "GND", TO(t.GetWidth()) / 2))
for f in b.GetFootprints():
    for p in f.Pads():
        if p.HasHole():
            q = p.GetPosition(); ds = p.GetDrillSize()
            holes.append((TO(q.x), TO(q.y), TO(max(ds.x, ds.y)) / 2, p.GetNetname() == "GND", TO(max(p.GetSize(pcbnew.F_Cu).x, p.GetSize(pcbnew.F_Cu).y)) / 2))

def inside(poly, x, y):
    n = len(poly); c = False
    for i in range(n):
        x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
    return c
def in_island(isl, x, y):
    return inside(isl[1], x, y) and not any(inside(h, x, y) for h in isl[2])

def raster(isl, x0, y0, nx, ny):
    im = Image.new("1", (nx, ny), 0); d = ImageDraw.Draw(im)
    d.polygon([((x - x0) / RES, (y - y0) / RES) for x, y in isl[1]], fill=1)
    for h in isl[2]: d.polygon([((x - x0) / RES, (y - y0) / RES) for x, y in h], fill=0)
    return np.array(im, dtype=bool)
def disk(r):
    n = int(math.ceil(r / RES)); yy, xx = np.mgrid[-n:n + 1, -n:n + 1]
    return (xx * xx + yy * yy) * RES * RES <= r * r

ni = b.FindNet("GND"); added = 0
gvias = [(x, y) for x, y, r, g, cr in holes if g]
def bbox(isl):
    xs = [p[0] for p in isl[1]]; ys = [p[1] for p in isl[1]]
    return min(xs), min(ys), max(xs), max(ys)
FB = [(a, bbox(a)) for a in islands[pcbnew.F_Cu] if a[0] >= 0.5]
BB = [(a, bbox(a)) for a in islands[pcbnew.B_Cu] if a[0] >= 0.5]
for fa, fb in FB:
    for ba, bb in BB:
        x0, y0 = max(fb[0], bb[0]), max(fb[1], bb[1]); x1, y1 = min(fb[2], bb[2]), min(fb[3], bb[3])
        if x1 - x0 < VIA_D or y1 - y0 < VIA_D: continue
        if any(x0 <= x <= x1 and y0 <= y <= y1 and in_island(fa, x, y) and in_island(ba, x, y) for x, y in gvias):
            continue
        x0 -= 0.5; y0 -= 0.5
        nx, ny = int((x1 - x0 + 0.5) / RES) + 2, int((y1 - y0 + 0.5) / RES) + 2
        ok = ndimage.binary_erosion(raster(fa, x0, y0, nx, ny), disk(VIA_D / 2 + 0.02)) & \
             ndimage.binary_erosion(raster(ba, x0, y0, nx, ny), disk(VIA_D / 2 + 0.02))
        if not ok.any(): continue
        hm = Image.new("1", (nx, ny), 0); d = ImageDraw.Draw(hm)
        for x, y, r, g, cr in holes:
            rr = max(r + HOLE_CLR + VIA_DRILL / 2, (0 if g else cr + 0.15 + VIA_D / 2) + RES) / RES; cx, cy = (x - x0) / RES, (y - y0) / RES
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=1)
        ok &= ~np.array(hm, dtype=bool)
        if not ok.any(): continue
        dist = ndimage.distance_transform_edt(ok)
        j, i = np.unravel_index(np.argmax(dist), dist.shape)
        x, y = float(x0 + i * RES), float(y0 + j * RES)
        v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(FROM(x), FROM(y)))
        v.SetWidth(FROM(VIA_D)); v.SetDrill(FROM(VIA_DRILL)); v.SetNet(ni); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        b.Add(v); added += 1; holes.append((x, y, VIA_DRILL / 2, True, VIA_D / 2)); gvias.append((x, y))
        print("via", round(x, 2), round(y, 2), "F area", round(fa[0], 1), "B area", round(ba[0], 1))
print("added", added)
pcbnew.SaveBoard(sys.argv[1], b)
