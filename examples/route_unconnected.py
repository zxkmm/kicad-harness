"""Local maze router for the leftovers of a mostly-routed 2-layer board.

usage: python3 route_unconnected.py board.kicad_pcb drc.json [--only NET,..] [--skip NET,..]
         [--first SUBSTR,..] [--block x,y,w,h;..] [--margin MM] [--dry] [--gnd]

drc.json must come from `kicad-cli pcb drc --refill-zones --format json` (current fills).
Each unconnected pair is routed with A* on a 0.05 mm raster of KiCad's own copper
shapes (pad/track/zone polygons dilated by clearance + half width), F/B + vias.
GND pad->zone pairs target the main GND fill. --gnd links GND fill islands instead.
Fallbacks: narrower track, then via-in-pad (printed as VIA-IN-PAD -- review those).
Writes with pcbnew.SaveBoard (native, not the lossy IPC model). Refill + DRC after.
Measured on a 148-part RP2350 board: 48 -> 3 unconnected (the 3 pre-existing), 0 new
clearance errors. Watch for routes that sever GND pour; bisect by stripping nets.
"""
import sys, json, math, heapq, argparse
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
import pcbnew

if not hasattr(pcbnew.SwigPyIterator, "next"):
    pcbnew.SwigPyIterator.next = pcbnew.SwigPyIterator.__next__

ap = argparse.ArgumentParser()
ap.add_argument("board"); ap.add_argument("drc")
ap.add_argument("--only", default=""); ap.add_argument("--skip", default="")
ap.add_argument("--res", type=float, default=0.05)
ap.add_argument("--margin", type=float, default=5.0)
ap.add_argument("--dry", action="store_true")
ap.add_argument("--max", type=int, default=999)
ap.add_argument("--first", default="")
ap.add_argument("--gnd", action="store_true", help="link disconnected GND fill components instead")
ap.add_argument("--block", default="", help="x,y,w,h;x,y,w,h keepout rects for all routes")
A = ap.parse_args()

RES = A.res
CLR = 0.15
EDGE = 0.30
HOLE_CLR = 0.25
VIA_D, VIA_DRILL = 0.6, 0.35
LAYERS = [pcbnew.F_Cu, pcbnew.B_Cu]

# Track widths per net: edit to match the project's net classes (.kicad_pro net_settings).
def width_for(net):
    if "VBUS" in net: return 0.4
    if "3V3" in net or "3.3" in net or "3v3" in net: return 0.3
    if net in ("VTARGET", "GND"): return 0.3
    return 0.2

b = pcbnew.LoadBoard(A.board)
TO = pcbnew.ToMM; FROM = pcbnew.FromMM

# index items by uuid
byid = {}
for t in b.GetTracks(): byid[t.m_Uuid.AsString()] = t
for f in b.GetFootprints():
    for p in f.Pads(): byid[p.m_Uuid.AsString()] = p
for z in b.Zones(): byid[z.m_Uuid.AsString()] = z

def polys_of(item, layer, inflate=0.0):
    """list of (outline, [holes]) in mm for item copper on layer"""
    if isinstance(item, dict): return item.get(layer, [])
    ps = pcbnew.SHAPE_POLY_SET()
    if isinstance(item, pcbnew.ZONE):
        if not item.IsOnLayer(layer): return []
        ps = item.GetFilledPolysList(layer)
    else:
        if not item.IsOnLayer(layer): return []
        item.TransformShapeToPolygon(ps, layer, FROM(inflate), FROM(0.005), pcbnew.ERROR_OUTSIDE)
    out = []
    for i in range(ps.OutlineCount()):
        def chain(c):
            return [(TO(c.CPoint(j).x), TO(c.CPoint(j).y)) for j in range(c.PointCount())]
        holes = [chain(ps.Hole(i, h)) for h in range(ps.HoleCount(i))]
        out.append((chain(ps.Outline(i)), holes))
    return out

def item_bbox(item):
    if isinstance(item, dict):
        pts = [p for L in item for o, h in item[L] for p in o]
        return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)
    bb = item.GetBoundingBox()
    return TO(bb.GetLeft()), TO(bb.GetTop()), TO(bb.GetRight()), TO(bb.GetBottom())

class Grid:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0 = x0, y0
        self.nx = int(math.ceil((x1 - x0) / RES)) + 1
        self.ny = int(math.ceil((y1 - y0) / RES)) + 1
    def img(self): return Image.new("1", (self.nx, self.ny), 0)
    def px(self, x, y): return ((x - self.x0) / RES, (y - self.y0) / RES)
    def draw(self, im, polys, val=1):
        d = ImageDraw.Draw(im)
        for outl, holes in polys:
            if len(outl) >= 3: d.polygon([self.px(*p) for p in outl], fill=val, outline=val)
            for h in holes:
                if len(h) >= 3: d.polygon([self.px(*p) for p in h], fill=1 - val)
    def disk(self, im, x, y, r, val=1):
        d = ImageDraw.Draw(im); cx, cy = self.px(x, y); rr = r / RES
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=val, outline=val)
    def mm(self, i, j): return (self.x0 + i * RES, self.y0 + j * RES)
    def cell(self, x, y): return (int(round((x - self.x0) / RES)), int(round((y - self.y0) / RES)))

def kernel(r):
    n = int(math.ceil(r / RES))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1]
    return (xx * xx + yy * yy) * RES * RES <= r * r

# board outline polygon (Edge.Cuts) as a mask builder
def outline_polys():
    ps = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(ps, False)
    out = []
    for i in range(ps.OutlineCount()):
        c = ps.Outline(i)
        out.append(([(TO(c.CPoint(j).x), TO(c.CPoint(j).y)) for j in range(c.PointCount())],
                    [[(TO(ps.Hole(i, h).CPoint(j).x), TO(ps.Hole(i, h).CPoint(j).y)) for j in range(ps.Hole(i, h).PointCount())] for h in range(ps.HoleCount(i))]))
    return out
OUTLINE = outline_polys()

def main_gnd_fill(layer):
    best, area = None, 0
    for z in b.Zones():
        if z.GetNetname() != "GND" or z.GetIsRuleArea() or not z.IsOnLayer(layer): continue
        ps = z.GetFilledPolysList(layer)
        for i in range(ps.OutlineCount()):
            a = abs(ps.Outline(i).Area())
            if a > area:
                c = ps.Outline(i)
                best = ([(TO(c.CPoint(j).x), TO(c.CPoint(j).y)) for j in range(c.PointCount())],
                        [[(TO(ps.Hole(i, h).CPoint(j).x), TO(ps.Hole(i, h).CPoint(j).y)) for j in range(ps.Hole(i, h).PointCount())] for h in range(ps.HoleCount(i))])
                area = a
    return best

def route_pair(net, src_item, dst_item, dst_main_gnd=False, w=None, via_in_pad=False):
    w = w or width_for(net)
    # window
    bbs = [item_bbox(src_item)]
    if not isinstance(dst_item, (pcbnew.ZONE, dict)): bbs.append(item_bbox(dst_item))
    x0 = min(bb[0] for bb in bbs) - A.margin; y0 = min(bb[1] for bb in bbs) - A.margin
    x1 = max(bb[2] for bb in bbs) + A.margin; y1 = max(bb[3] for bb in bbs) + A.margin
    if isinstance(dst_item, pcbnew.ZONE):  # zone target: search near the source only
        pass
    g = Grid(x0, y0, x1, y1)
    win = pcbnew.BOX2I(pcbnew.VECTOR2I(FROM(x0 - 1), FROM(y0 - 1)), pcbnew.VECTOR2L(FROM(x1 - x0 + 2), FROM(y1 - y0 + 2)))

    obst = {L: g.img() for L in LAYERS}
    holes = g.img()
    padm = g.img()
    srcm = {L: g.img() for L in LAYERS}
    dstm = {L: g.img() for L in LAYERS}

    def add_item(it):
        bb = it.GetBoundingBox()
        if not bb.Intersects(win): return
        same = it.GetNetname() == net and it.GetNetCode() > 0
        for L in LAYERS:
            if not it.IsOnLayer(L): continue
            if same: continue
            g.draw(obst[L], polys_of(it, L))
        if isinstance(it, pcbnew.PAD):
            for L in LAYERS:
                if it.IsOnLayer(L): g.draw(padm, polys_of(it, L))
        # holes
        if isinstance(it, pcbnew.PAD) and it.HasHole():
            p = it.GetPosition(); ds = it.GetDrillSize()
            g.disk(holes, TO(p.x), TO(p.y), TO(max(ds.x, ds.y)) / 2)
        if isinstance(it, pcbnew.PCB_VIA):
            p = it.GetPosition(); g.disk(holes, TO(p.x), TO(p.y), TO(it.GetDrillValue()) / 2)

    for t in b.GetTracks(): add_item(t)
    for f in b.GetFootprints():
        for p in f.Pads(): add_item(p)
    for z in b.Zones():
        if z.GetIsRuleArea(): continue
        if z.GetNetname() == net: continue
        if z.GetNetname() == "GND": continue   # the pour re-flows around new copper
        for L in LAYERS:
            if z.IsOnLayer(L) and z.GetBoundingBox().Intersects(win):
                g.draw(obst[L], polys_of(z, L))

    for L in LAYERS:
        g.draw(srcm[L], polys_of(src_item, L))
        if dst_main_gnd:
            mg = main_gnd_fill(L)
            if mg: g.draw(dstm[L], [mg])
        else:
            g.draw(dstm[L], polys_of(dst_item, L))

    for r in filter(None, A.block.split(";")):
        bx, by, bw, bh = map(float, r.split(","))
        rect = [([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)], [])]
        for L in LAYERS: g.draw(obst[L], rect)
    inside = g.img(); g.draw(inside, OUTLINE)
    inside = np.array(inside, dtype=bool)
    ob = {L: np.array(obst[L], dtype=bool) for L in LAYERS}
    hl = np.array(holes, dtype=bool)
    edge_t = ~ndimage.binary_erosion(inside, kernel(EDGE + w / 2 + RES), border_value=0)
    edge_v = ~ndimage.binary_erosion(inside, kernel(EDGE + VIA_D / 2 + RES), border_value=0)
    free = {L: ~ndimage.binary_dilation(ob[L], kernel(CLR + w / 2 + RES)) & ~edge_t for L in LAYERS}
    viaok = ~edge_v & ~ndimage.binary_dilation(hl, kernel(HOLE_CLR + VIA_DRILL / 2 + RES))
    for L in LAYERS:
        viaok &= ~ndimage.binary_dilation(ob[L], kernel(CLR + VIA_D / 2 + RES))
    if not via_in_pad:
        viaok &= ~ndimage.binary_dilation(np.array(padm, dtype=bool), kernel(VIA_D / 2 + 0.05))
    # holes also block tracks on both layers (pads already cover copper; NPTH holes)
    S = {L: np.array(srcm[L], dtype=bool) for L in LAYERS}
    D = {L: np.array(dstm[L], dtype=bool) for L in LAYERS}
    if dst_main_gnd:
        # land the via fully inside the main fill
        D[pcbnew.B_Cu] = ndimage.binary_erosion(D[pcbnew.B_Cu], kernel(VIA_D / 2 + 0.1))
        D[pcbnew.F_Cu] = ndimage.binary_erosion(D[pcbnew.F_Cu], kernel(0.3))
    for L in LAYERS:
        free[L] |= S[L] & ~ndimage.binary_dilation(ob[L], kernel(CLR + w / 2 + RES))  # start inside pad
        free[L] |= D[L] & ~ndimage.binary_dilation(ob[L], kernel(CLR + w / 2 + RES))

    # A* over (layer, y, x, dir)
    li = {L: k for k, L in enumerate(LAYERS)}
    F = np.stack([free[L] for L in LAYERS])
    Sm = np.stack([S[L] for L in LAYERS]) & F
    Dm = np.stack([D[L] for L in LAYERS]) & F
    if not Sm.any() or not Dm.any():
        return None, "no free source/target cells"
    dys, dxs = np.nonzero(Dm.any(0))
    tc = np.stack([dxs, dys], 1)
    # heuristic: distance to target bbox
    tx0, tx1, ty0, ty1 = dxs.min(), dxs.max(), dys.min(), dys.max()
    def h(x, y):
        dx = max(tx0 - x, 0, x - tx1); dy = max(ty0 - y, 0, y - ty1)
        return (max(dx, dy) + 0.414 * min(dx, dy))
    DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    VIA_COST = 1.2 / RES
    TURN = 0.5 / RES * 0.2
    best = {}
    pq = []
    for l, y, x in zip(*np.nonzero(Sm)):
        for d in range(8):
            st = (int(l), int(y), int(x), d)
            best[st] = 0.0
        heapq.heappush(pq, (h(x, y), 0.0, (int(l), int(y), int(x), -1), None))
    prev = {}
    goal = None
    nl, ny, nx = F.shape
    seen = set()
    while pq:
        f, gc, st, pr = heapq.heappop(pq)
        if st in seen: continue
        seen.add(st)
        prev[st] = pr
        l, y, x, d = st
        if Dm[l, y, x]:
            goal = st; break
        for nd, (dx, dy) in enumerate(DIRS):
            x2, y2 = x + dx, y + dy
            if not (0 <= x2 < nx and 0 <= y2 < ny) or not F[l, y2, x2]: continue
            if dx and dy and not (F[l, y, x2] and F[l, y2, x]): continue
            c = gc + (1.414 if dx and dy else 1.0)
            if d >= 0 and nd != d:
                turn = min((nd - d) % 8, (d - nd) % 8)
                if turn > 2: continue
                c += TURN * turn
            s2 = (l, y2, x2, nd)
            if s2 in seen: continue
            heapq.heappush(pq, (c + h(x2, y2), c, s2, st))
        if viaok[y, x]:
            l2 = 1 - l
            if F[l2, y, x]:
                s2 = (l2, y, x, -1)
                if s2 not in seen:
                    c = gc + VIA_COST
                    heapq.heappush(pq, (c + h(x, y), c, s2, st))
        if len(seen) > 6_000_000: return None, "search too large"
    if goal is None: return None, "no path"
    path = []
    st = goal
    while st is not None:
        path.append(st); st = prev[st]
    path.reverse()
    return (g, path, F, viaok, w), "ok"

def los(F, l, a, b_):
    (x0, y0), (x1, y1) = a, b_
    n = max(abs(x1 - x0), abs(y1 - y0))
    for k in range(n + 1):
        x = round(x0 + (x1 - x0) * k / max(n, 1)); y = round(y0 + (y1 - y0) * k / max(n, 1))
        if not F[l, y, x]: return False
    return True

def to_segments(res):
    g, path, F, viaok, w = res
    # split into layer runs; via where layer changes
    runs, vias = [], []
    cur = [path[0]]
    for st in path[1:]:
        if st[0] != cur[-1][0]:
            runs.append(cur); vias.append((st[2], st[1])); cur = [st]
        else: cur.append(st)
    runs.append(cur)
    segs = []
    for run in runs:
        l = run[0][0]
        pts = [(s[2], s[1]) for s in run]
        # drop consecutive duplicates
        dd = [pts[0]]
        for p in pts[1:]:
            if p != dd[-1]: dd.append(p)
        pts = dd
        # collapse collinear
        simp = [pts[0]]
        for i in range(1, len(pts) - 1):
            a, c = simp[-1], pts[i + 1]; p = pts[i]
            v1 = (p[0] - a[0], p[1] - a[1]); v2 = (c[0] - p[0], c[1] - p[1])
            if v1[0] * v2[1] - v1[1] * v2[0] != 0: simp.append(p)
        if len(pts) > 1: simp.append(pts[-1])
        # greedy 45-degree line-of-sight shortcut
        out = [simp[0]]; i = 0
        while i < len(simp) - 1:
            j = len(simp) - 1
            while j > i + 1:
                dx, dy = simp[j][0] - simp[i][0], simp[j][1] - simp[i][1]
                if (dx == 0 or dy == 0 or abs(dx) == abs(dy)) and los(F, l, simp[i], simp[j]): break
                j -= 1
            out.append(simp[j]); i = j
        for a, c in zip(out, out[1:]):
            segs.append((l, g.mm(*a), g.mm(*c)))
    return segs, [g.mm(*v) for v in vias], w

uns = [] if A.gnd else json.load(open(A.drc))["unconnected_items"]
first = A.first.split(",")
uns.sort(key=lambda u: 0 if any(f and f in u["items"][0]["description"] for f in first) else 1)
only = set(A.only.split(",")) - {""}; skip = set(A.skip.split(",")) - {""}
done_nets = 0
added_t = added_v = 0
fails = []
for u in uns[:A.max]:
    it = [byid.get(i["uuid"]) for i in u["items"]]
    if None in it:
        fails.append(("missing", u["items"][0]["description"])); continue
    net = it[0].GetNetname()
    if only and net not in only: continue
    if net in skip: continue
    a, c = it
    gnd_zone = False
    if isinstance(a, pcbnew.ZONE): a, c = c, a
    if isinstance(a, pcbnew.ZONE):
        fails.append(("zone-zone", net)); continue
    if isinstance(c, pcbnew.ZONE) and net == "GND":
        gnd_zone = True
    res, msg = route_pair(net, a, c, dst_main_gnd=gnd_zone)
    if res is None and width_for(net) > 0.2:
        res, msg = route_pair(net, a, c, dst_main_gnd=gnd_zone, w=0.2); msg += " (narrow)"
    if res is None:
        res, msg = route_pair(net, a, c, dst_main_gnd=gnd_zone, w=0.2, via_in_pad=True)
        if res is not None: print("VIA-IN-PAD", net)
    desc = " | ".join(i["description"] for i in u["items"])
    if res is None:
        fails.append((msg, desc)); print("FAIL", msg, desc); continue
    segs, vias, w = to_segments(res)
    print("OK  ", desc, f"{len(segs)} segs {len(vias)} vias")
    if A.dry: continue
    ni = b.FindNet(net)
    for l, p, q in segs:
        if p == q: continue
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(FROM(p[0]), FROM(p[1]))); t.SetEnd(pcbnew.VECTOR2I(FROM(q[0]), FROM(q[1])))
        t.SetWidth(FROM(w)); t.SetLayer(LAYERS[l]); t.SetNet(ni)
        b.Add(t); added_t += 1
    for p in vias:
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(FROM(p[0]), FROM(p[1])))
        v.SetWidth(FROM(VIA_D)); v.SetDrill(FROM(VIA_DRILL)); v.SetNet(ni)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        b.Add(v); added_v += 1
def add_path(net, res):
    global added_t, added_v
    segs, vias, w = to_segments(res)
    ni = b.FindNet(net)
    for l, p, q in segs:
        if p == q: continue
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(FROM(p[0]), FROM(p[1]))); t.SetEnd(pcbnew.VECTOR2I(FROM(q[0]), FROM(q[1])))
        t.SetWidth(FROM(w)); t.SetLayer(LAYERS[l]); t.SetNet(ni); b.Add(t); added_t += 1
    for p in vias:
        v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(FROM(p[0]), FROM(p[1])))
        v.SetWidth(FROM(VIA_D)); v.SetDrill(FROM(VIA_DRILL)); v.SetNet(ni); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        b.Add(v); added_v += 1
    return len(segs), len(vias)

if A.gnd:
    def chainp(c): return [(TO(c.CPoint(j).x), TO(c.CPoint(j).y)) for j in range(c.PointCount())]
    isl = []  # (layer, area, outline, holes)
    for z in b.Zones():
        if z.GetNetname() != "GND" or z.GetIsRuleArea(): continue
        for L in LAYERS:
            if not z.IsOnLayer(L): continue
            ps = z.GetFilledPolysList(L)
            for i in range(ps.OutlineCount()):
                isl.append((L, abs(ps.Outline(i).Area()) / 1e12, chainp(ps.Outline(i)), [chainp(ps.Hole(i, h)) for h in range(ps.HoleCount(i))]))
    def pin(poly, x, y):
        c = False; n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
        return c
    def contains(k, x, y):
        L, a, o, hs = isl[k]
        return pin(o, x, y) and not any(pin(h, x, y) for h in hs)
    par = list(range(len(isl)))
    def find(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    bridges = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == "GND":
            p = t.GetPosition(); bridges.append((TO(p.x), TO(p.y)))
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.HasHole() and p.GetNetname() == "GND":
                q = p.GetPosition(); bridges.append((TO(q.x), TO(q.y)))
    for x, y in bridges:
        ks = [k for k in range(len(isl)) if contains(k, x, y)]
        for k in ks[1:]: par[find(k)] = find(ks[0])
    main = find(max(range(len(isl)), key=lambda k: isl[k][1]))
    comps = {}
    for k in range(len(isl)): comps.setdefault(find(k), []).append(k)
    mainpolys = {L: [(isl[k][2], isl[k][3]) for k in comps[main] if isl[k][0] == L] for L in LAYERS}
    for r, ks in comps.items():
        if r == main: continue
        area = sum(isl[k][1] for k in ks)
        if area < 1.0: continue
        src = {L: [(isl[k][2], isl[k][3]) for k in ks if isl[k][0] == L] for L in LAYERS}
        x0, y0, x1, y1 = item_bbox(src)
        # restrict target to a window around the island so the raster stays small
        res, msg = route_pair("GND", src, mainpolys, w=0.3)
        if res is None: res, msg = route_pair("GND", src, mainpolys, w=0.2)
        where = f"island {round(x0,1)},{round(y0,1)} area {round(area,2)}"
        if res is None: print("FAIL", msg, where); fails.append((msg, where)); continue
        print("OK  ", where, add_path("GND", res))

print(f"added {added_t} tracks {added_v} vias; {len(fails)} failures")
for f in fails: print("  ", f)
if not A.dry: pcbnew.SaveBoard(A.board, b)
