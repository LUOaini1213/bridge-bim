"""模型检查：每条返回 (名称, 是否通过, 实测说明)。

规则直接在生成出的构件几何上量，不信任「按构造应当成立」——比如梁端间隙量的是
梁端面 12 个顶点到支承线的距离（顺带验证端面与支承线平行），湿接缝量的是两片梁翼缘
边缘的实际距离，垫石是否落在盖梁上是在盖梁局部坐标里逐角量。
"""
import math
from collections import defaultdict

from . import alignment as AL, config as C
from .model import (chords, equalize, family, frame, length_interval, project, solid_closed, solid_volume,
                    support_kind, support_stations)


def _by_cls(els, cls):
    return [e for e in els if e.cls == cls]


def tiny(x, floor=1e-9):
    """浮点残差只报上界：1e-13 量级的数是舍入噪声，换一台机器末位就变，写进产物会让逐字节核对失效。"""
    return "< %.0e" % floor if abs(x) < floor else "%.2e" % x


def check_alignment_continuity(els):
    worst_p = worst_t = worst_k = 0.0
    for a, b in zip(AL.SEGMENTS, AL.SEGMENTS[1:]):
        x, y, th, k = a.at(a.L)
        worst_p = max(worst_p, math.hypot(x - b.x0, y - b.y0))
        worst_t = max(worst_t, abs(th - b.th0))
        worst_k = max(worst_k, abs(k - b.k0))
    ok = worst_p < 1e-6 and worst_t < 1e-9 and worst_k < 1e-12
    return ("平曲线线元衔接：位置、方位角、曲率连续", ok,
            "%d 处衔接，最大位置差 %s m，方位角差 %s rad，曲率差 %s" % (len(AL.SEGMENTS) - 1, tiny(worst_p), tiny(worst_t),
                                                             tiny(worst_k, 1e-12)))


def check_profile_continuity(els):
    """在切点两侧各 ε 处取值：扣掉坡线本身在 2ε 上的高差、竖曲线在 ε 上的变坡，剩下的才是不连续。"""
    worst_z = worst_g = 0.0
    n = 0
    eps = 1e-6
    for i, L in enumerate(C.V_CURVE_LENGTH):
        if L <= 0:
            continue
        for st in (C.V_PVI[i][0] - L / 2.0, C.V_PVI[i][0] + L / 2.0):
            z1, g1 = AL.profile(st - eps)
            z2, g2 = AL.profile(st + eps)
            worst_z = max(worst_z, abs((z2 - z1) - (g1 + g2) * eps))
            worst_g = max(worst_g, abs(g2 - g1))     # 竖曲线一侧在 ε 上本身只变 Δi·ε/L ≈ 1e-10
            n += 1
    return ("竖曲线起终点：高程与纵坡连续", worst_z < 1e-6 and worst_g < 1e-6,
            "%d 处，最大高程差 %s m，坡度差 %s" % (n, tiny(worst_z), tiny(worst_g)))


def check_spans(els):
    st = support_stations()
    worst = max(abs((b - a) - C.SPAN) for a, b in zip(st, st[1:]))
    return ("跨径 = %.0f m（沿中线逐跨量）" % C.SPAN, worst < 1e-9, "%d 跨，最大偏差 %s m" % (len(st) - 1, tiny(worst)))


def _end_distances(g, end):
    """梁端面 12 个顶点到该端支承线（竖直平面）的距离，朝跨内为正。"""
    k = g.attrs["span"] - (1 if end == "a" else 0)
    P, t, _ = frame(support_stations()[k])
    sgn = 1.0 if end == "a" else -1.0
    n = len(C.T_SECTION)
    ring = g.params["solids"][0]["v"][(0 if end == "a" else n):(n if end == "a" else 2 * n)]
    return [sgn * ((p[0] - P[0]) * t[0] + (p[1] - P[1]) * t[1]) for p in ring]


def check_expansion_ends(els):
    """伸缩端（桥台、过渡墩）：梁端面到支承线的距离处处等于 EXP_HALF。"""
    worst, n = 0.0, 0
    for g in _by_cls(els, "girder"):
        for end in ("a", "b"):
            if g.attrs["end_" + end] != "E":
                continue
            worst = max(worst, max(abs(x - C.EXP_HALF) for x in _end_distances(g, end)))
            n += 1
    return ("伸缩端梁端距支承线 = %.2f m（端面与支承线平行）" % C.EXP_HALF, worst < 1e-6,
            "%d 个梁端 × 12 个端面顶点，最大偏差 %s m" % (n, tiny(worst)))


def continuity_joints(els):
    """[(连续墩号, 幅, 梁位, 连续段宽, 端面不平行度)]：宽 = 两侧梁端面到墩中心线的距离之和。"""
    g = {e.eid: e for e in _by_cls(els, "girder")}
    out = []
    for k in range(1, C.N_SPANS):
        if support_kind(k) != "C":
            continue
        for d, _ in C.DECKS:
            for i in range(1, len(C.GIRDER_OFFSETS) + 1):
                da = _end_distances(g["G-%s%02d-%d" % (d, k, i)], "b")
                db = _end_distances(g["G-%s%02d-%d" % (d, k + 1, i)], "a")
                w = [x + y for x in da for y in db]
                out.append((k, d, i, min(w), (max(da) - min(da)) + (max(db) - min(db))))
    return out


def check_continuity_joints(els):
    js = continuity_joints(els)
    lo, hi = 2 * C.CONT_HALF_MIN, 2 * C.CONT_HALF_MAX
    ws = [j[3] for j in js]
    skew = max(j[4] for j in js)
    ok = min(ws) >= lo - 1e-6 and max(ws) <= hi + 1e-6 and skew < 1e-6
    return ("连续墩现浇连续段宽 %.2f – %.2f m" % (lo, hi), ok,
            "%d 处，%.3f – %.3f m，梁端面不平行度 %s m" % (len(js), min(ws), max(ws), tiny(skew)))


def check_wet_joints(els):
    ws = []
    for e in _by_cls(els, "wet_joint"):
        ws += [e.attrs["width_a"], e.attrs["width_b"]]
    return ("湿接缝宽 ≥ %.2f m（翼缘边缘实测）" % C.MIN_WET_JOINT, min(ws) >= C.MIN_WET_JOINT,
            "量了 %d 处，%.3f – %.3f m" % (len(ws), min(ws), max(ws)))


def check_seats(els):
    hs = [(e.attrs["height"], e.eid) for e in _by_cls(els, "seat")]
    lo, hi = min(hs), max(hs)
    ok = lo[0] >= C.SEAT_MIN - 1e-9 and hi[0] <= C.SEAT_MAX
    return ("支座垫石高 %.2f – %.2f m" % (C.SEAT_MIN, C.SEAT_MAX), ok,
            "%d 个垫石，%.3f m（%s）– %.3f m（%s）" % (len(hs), lo[0], lo[1], hi[0], hi[1]))


def _caps(els):
    return {(e.attrs["support"], e.deck): e for e in els if e.cls in ("cap", "abut_cap")}


def _local(cap, p):
    f = cap.params["frame"]
    dx, dy = p[0] - f["o"][0], p[1] - f["o"][1]
    return dx * f["t"][0] + dy * f["t"][1], dx * f["n"][0] + dy * f["n"][1]


def check_supports_on_caps(els):
    """垫石与临时支座的平面四角都落在盖梁 / 台帽顶面之内（盖梁局部坐标里量）。"""
    caps = _caps(els)
    worst, where = float("inf"), None
    for e in els:
        if e.cls not in ("seat", "temp_support"):
            continue
        cap = caps[(e.attrs["support"], e.deck)]
        f = cap.params["frame"]
        for p in e.params["solids"][0]["v"][:4]:
            b, a = _local(cap, p)
            m = min(b - f["b"][0], f["b"][1] - b, a - f["a"][0], f["a"][1] - a)
            if m < worst:
                worst, where = m, e.eid
    return ("垫石、临时支座落在盖梁 / 台帽顶面内", worst >= 0, "最小边距 %.3f m（%s）" % (worst, where))


def check_temp_heights(els):
    hs = [(e.attrs["height"], e.eid) for e in _by_cls(els, "temp_support")]
    lo, hi = min(hs), max(hs)
    return ("临时支座高 ≥ %.2f m" % C.TEMP_MIN, lo[0] >= C.TEMP_MIN,
            "%d 个，%.3f m（%s）– %.3f m（%s）" % (len(hs), lo[0], lo[1], hi[0], hi[1]))


def _rect_gap(cap, e1, e2):
    """两块平面矩形（边与盖梁坐标轴平行）之间的净距；重叠时为负。"""
    def box(e):
        pts = [_local(cap, p) for p in e.params["solids"][0]["v"][:4]]
        return min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)
    a, b = box(e1), box(e2)
    gb = max(a[0] - b[1], b[0] - a[1])
    ga = max(a[2] - b[3], b[2] - a[3])
    return max(gb, ga)


def check_temp_clear_of_seats(els, need=0.05):
    """连续墩上临时支座与永久支座垫石互不重叠，平面净距 ≥ need。"""
    caps = _caps(els)
    seats = defaultdict(list)
    for e in _by_cls(els, "seat"):
        seats[(e.attrs["support"], e.deck)].append(e)
    worst, where = float("inf"), None
    for t in _by_cls(els, "temp_support"):
        key = (t.attrs["support"], t.deck)
        for s in seats[key]:
            gap = _rect_gap(caps[key], t, s)
            if gap < worst:
                worst, where = gap, t.eid
    return ("临时支座与永久支座垫石平面净距 ≥ %.2f m" % need, worst >= need, "最小 %.3f m（%s）" % (worst, where))


def check_perm_bearing_under_joint(els, need=0.05):
    """连续墩永久支座整个落在现浇连续段下：支座边缘到两侧预制梁端面 ≥ need，体系转换前不受力。"""
    g = {e.eid: e for e in _by_cls(els, "girder")}
    worst, where = float("inf"), None
    for b in _by_cls(els, "bearing"):
        if b.attrs["kind"] != "cont":
            continue
        k = b.attrs["support"]
        P, t, _ = frame(support_stations()[k])
        c = b.params["c"]
        s0 = (c[0] - P[0]) * t[0] + (c[1] - P[1]) * t[1]
        ga, gb = (g[x] for x in b.attrs["girders"])
        before = min(_end_distances(ga, "b"))           # 前一跨梁端面在墩中心线之前多远
        after = min(_end_distances(gb, "a"))
        half = b.params["r"] if b.shape == "cyl" else b.params["w"] / 2     # 顺桥向半尺寸
        m = min(before + s0, after - s0) - half
        if m < worst:
            worst, where = m, b.eid
    return ("连续墩永久支座全在现浇连续段下（距预制梁端 ≥ %.2f m）" % need, worst >= need,
            "最小 %.3f m（%s）" % (worst, where))


def check_pier_caps_above_ground(els):
    worst, where = float("inf"), None
    for e in _by_cls(els, "cap"):
        k = e.attrs["support"]
        for p in e.params["solids"][0]["v"][:4]:
            s, o = project(p[0], p[1], support_stations()[k])
            clr = e.attrs["bottom"] - AL.ground(s, o)
            if clr < worst:
                worst, where = clr, e.eid
    return ("墩盖梁底高出地面（桥台埋入路堤，不在此列）", worst > 0, "最小 %.2f m（%s）" % (worst, where))


def _road_frame():
    x, y, th, _ = AL.at_station(C.ROAD_STATION)
    return (x, y), (math.cos(th), math.sin(th))


def check_clearance(els):
    """被交道路两侧边线之间，梁底最低点到路面的竖向净空。"""
    (rx, ry), t = _road_frame()
    half = C.ROAD_WIDTH / 2
    worst, where = float("inf"), None
    for e in _by_cls(els, "girder"):
        p0, p1 = e.params["p0"], e.params["p1"]
        for f in [i / 200.0 for i in range(201)]:
            x = p0[0] + (p1[0] - p0[0]) * f
            y = p0[1] + (p1[1] - p0[1]) * f
            along = (x - rx) * t[0] + (y - ry) * t[1]
            if abs(along) > half:
                continue
            soffit = p0[2] + (p1[2] - p0[2]) * f - C.H_GIRDER
            _, o = project(x, y, C.ROAD_STATION)   # 梁底这一点到路线的偏距，即它在被交道路上的位置
            clr = soffit - AL.ground(C.ROAD_STATION, o)
            if clr < worst:
                worst, where = clr, e.eid
    return ("被交道路净空 ≥ %.1f m" % C.ROAD_CLEARANCE, worst >= C.ROAD_CLEARANCE,
            "最小 %.2f m（%s 梁底）" % (worst, where))


def check_columns_clear_of_road(els):
    (rx, ry), t = _road_frame()
    worst, where = float("inf"), None
    for e in _by_cls(els, "column"):
        px, py, _ = e.params["c"]
        along = abs((px - rx) * t[0] + (py - ry) * t[1])
        clr = along - e.params["r"] - C.ROAD_WIDTH / 2
        if clr < worst:
            worst, where = clr, e.eid
    return ("墩柱外缘距被交道路边线 ≥ %.1f m" % C.ROAD_SIDE_CLEAR, worst >= C.ROAD_SIDE_CLEAR,
            "最小 %.2f m（%s）" % (worst, where))


def check_erector(els):
    heaviest = max(_by_cls(els, "girder"), key=lambda e: e.volume)
    t = heaviest.volume * C.RC_DENSITY_T
    return ("最重预制梁 ≤ 架桥机额定 %.0f t" % C.ERECTOR_SWL_T, t <= C.ERECTOR_SWL_T,
            "%s %.1f t，利用率 %.0f%%" % (heaviest.eid, t, t / C.ERECTOR_SWL_T * 100))


def check_solids(els):
    """全部多面体闭合（每条边恰被两个面共用、方向相反）且外法向（体积为正）。"""
    n = bad = 0
    for e in els:
        if e.shape != "mesh":
            continue
        for s in e.params["solids"]:
            n += 1
            if not solid_closed(s) or solid_volume(s) <= 0:
                bad += 1
    return ("多面体闭合、法向朝外", bad == 0, "%d 个实体，不合格 %d 个" % (n, bad))


def check_length_specs(els):
    """梁长归并：每片梁的实际长度落在自己的可行区间内、等于所分配的规格；
    且每种梁型的规格数达到下界——证书里的区间两两不相交、个数等于规格数。"""
    chs = chords()
    fams, assign = equalize(chs)
    worst_len, outside = 0.0, 0
    for e in _by_cls(els, "girder"):
        key = (e.deck, e.attrs["span"], e.attrs["girder"])
        p0, p1 = e.params["p0"], e.params["p1"]
        L = math.sqrt(sum((p1[j] - p0[j]) ** 2 for j in range(3)))
        worst_len = max(worst_len, abs(L - assign[key]))
        lo, hi = length_interval(chs[key])
        if not (lo * C.SPEC_STEP - 1e-9 <= L <= hi * C.SPEC_STEP + 1e-9):
            outside += 1
    cert_ok, parts = True, []
    for fam, (specs, wit) in sorted(fams.items()):
        iv = sorted(length_interval(chs[w]) for w in wit)
        disjoint = all(a[1] < b[0] for a, b in zip(iv, iv[1:]))
        same_family = all(family(chs[w]) == fam for w in wit)
        cert_ok = cert_ok and disjoint and same_family and len(wit) == len(specs)
        parts.append("%s %d 种" % (fam, len(specs)))
    ok = cert_ok and outside == 0 and worst_len < 1e-9
    return ("预制梁长规格数达到下界（区间刺穿最优性证书）", ok,
            "%s；实际梁长与规格最大差 %s m，越出可行区间 %d 片" % ("、".join(parts), tiny(worst_len), outside))


ALL = [check_alignment_continuity, check_profile_continuity, check_spans, check_expansion_ends,
       check_continuity_joints, check_wet_joints, check_seats, check_supports_on_caps, check_temp_heights,
       check_temp_clear_of_seats, check_perm_bearing_under_joint, check_pier_caps_above_ground, check_clearance,
       check_columns_clear_of_road, check_erector, check_solids, check_length_specs]


def run_all(els):
    return [f(els) for f in ALL]
