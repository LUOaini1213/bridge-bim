"""桥梁构件生成：每个构件带几何（世界坐标，米）与 BIM 属性。

结构体系：先简支后连续预应力混凝土 T 梁，分联见 config.UNITS。
- 桥台与过渡墩（两联交界）处的梁端是伸缩端：设伸缩缝，梁直接落在永久支座上。
- 连续墩处的梁端是连续端：架梁时落在临时支座上；浇墩顶现浇连续段、张拉负弯矩钢束后
  拆除临时支座，改由墩中心一排永久支座受力——即体系转换。永久支座整个落在连续段下，
  体系转换前不接触预制梁（checks 里量）。

几何只有三种，Rhino 与 IFC 用同一份数据：
- cyl：竖直圆柱（墩柱、桩、支座）；box：水平长方体（系梁）；
- mesh：闭合多面体（顶点 + 面，面按外法向逆时针），其余构件都是它。
  预制 T 梁是竖直截面沿梁轴扫出的棱柱，两端面平行于径向支承线；盖梁顶面随该处桥面横坡；
  铺装、护栏、翼缘现浇段沿路线取样，外边缘跟随曲线（直梁「以直代曲」，曲线由现浇部分吸收）。

梁长：预制梁放在两支承线之间、同一偏距处两点连成的三维弦上，曲线外侧梁比内侧长。
伸缩端到支承线的距离固定，连续端在允许范围内浮动；于是每片梁的可行长度是一个区间，
按梁型（中跨 / 边跨）分别用最少的长度刺穿全部区间——按右端点排序的贪心，
并给出最优性证书：一组两两不相交、个数等于规格数的区间。
"""
import math
from dataclasses import dataclass, field

from . import alignment as AL, config as C


@dataclass
class Element:
    eid: str
    cls: str
    part: str                    # 所属桥梁部分：SUP-L / SUP-R / A00 / P01…P11 / A12
    deck: str
    shape: str
    params: dict
    attrs: dict = field(default_factory=dict)
    volume: float = 0.0


# ====================================================================== 支承线与分联
def support_stations():
    return [C.BRIDGE_START + k * C.SPAN for k in range(C.N_SPANS + 1)]


def support_name(k):
    return ("A%02d" if k in (0, C.N_SPANS) else "P%02d") % k


def unit_bounds():
    """各联的 (起点支承号, 终点支承号)。"""
    out, k = [], 0
    for n in C.UNITS:
        out.append((k, k + n))
        k += n
    if k != C.N_SPANS:
        raise ValueError("分联跨数之和 %d ≠ 总跨数 %d" % (k, C.N_SPANS))
    return out


def support_kind(k):
    """A 桥台 / T 过渡墩（两联交界，设伸缩缝）/ C 连续墩。"""
    if k in (0, C.N_SPANS):
        return "A"
    return "T" if k in {b for _, b in unit_bounds()[:-1]} else "C"


def end_kind(k):
    """梁端在支承 k 处：E 伸缩端 / C 连续端。"""
    return "C" if support_kind(k) == "C" else "E"


def unit_of_span(k):
    for u, (a, b) in enumerate(unit_bounds(), 1):
        if a < k <= b:
            return u
    raise ValueError("跨号 %d 不在任何一联内" % k)


# ====================================================================== 平面几何
def frame(s):
    """桩号处中线点、切线、左法线（支承线方向）。"""
    x, y, th, _ = AL.at_station(s)
    return (x, y), (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))


def project(x, y, guess):
    """平面点到路线的投影：返回 (桩号, 偏距)。牛顿迭代。"""
    s = guess
    for _ in range(30):
        px, py, th, k = AL.at_station(s)
        tx, ty = math.cos(th), math.sin(th)
        dx, dy = x - px, y - py
        off = -dx * ty + dy * tx
        step = (dx * tx + dy * ty) / (1.0 - k * off)
        s += step
        if abs(step) < 1e-11:
            break
    px, py, th, _ = AL.at_station(s)
    return s, -(x - px) * math.sin(th) + (y - py) * math.cos(th)


def station_on_plane(o, q, t, h, guess):
    """偏距 o 的平行线与竖直平面 {p : (p - q)·t = h} 的交点桩号（牛顿迭代）。"""
    s = guess
    for _ in range(50):
        x, y = AL.offset_xy(s, o)
        f = (x - q[0]) * t[0] + (y - q[1]) * t[1] - h
        _, _, th, k = AL.at_station(s)
        step = f / ((math.cos(th) * t[0] + math.sin(th) * t[1]) * (1.0 - k * o))
        s -= step
        if abs(step) < 1e-12:
            break
    return s


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(len(a)))


def _dist(p, q):
    return math.sqrt(sum((p[i] - q[i]) ** 2 for i in range(3)))


def section_area(pts=None):
    pts = pts or C.T_SECTION
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2.0


_TOP_Y = max(y for _, y in C.T_SECTION)
IDX_R = min((i for i, p in enumerate(C.T_SECTION) if p[1] == _TOP_Y), key=lambda i: C.T_SECTION[i][0])
IDX_L = max((i for i, p in enumerate(C.T_SECTION) if p[1] == _TOP_Y), key=lambda i: C.T_SECTION[i][0])
HALF_FLANGE = C.T_SECTION[IDX_L][0]


# ====================================================================== 多面体
def prism_faces(n):
    """两个 n 点环（环 0 的法向指向环 1）围成的棱柱：两端封口 + n 个侧面。"""
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    return faces


def loft_faces(m, n):
    """m 个截面、每个 n 点，沿截面法向依次排列：首尾封口 + 侧面（三角形）。"""
    faces = [tuple(range(n - 1, -1, -1)), tuple(range((m - 1) * n, m * n))]
    for r in range(m - 1):
        for i in range(n):
            j = (i + 1) % n
            a, b, c, d = r * n + i, r * n + j, (r + 1) * n + j, (r + 1) * n + i
            faces += [(a, b, c), (a, c, d)]
    return faces


def _newell(pts):
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    L = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (nx / L, ny / L, nz / L)


def planar(pts, tol=1e-7):
    if len(pts) <= 3:
        return True
    n = _newell(pts)
    o = pts[0]
    return all(abs(sum((p[i] - o[i]) * n[i] for i in range(3))) <= tol for p in pts)


def make_solid(verts, faces):
    """非平面的四边形面拆成两个三角形；平面多边形保留。"""
    out = []
    for f in faces:
        if len(f) > 3 and not planar([verts[i] for i in f]):
            if len(f) != 4:
                raise ValueError("非平面面只允许是四边形")
            out += [(f[0], f[1], f[2]), (f[0], f[2], f[3])]
        else:
            out.append(tuple(f))
    return {"v": [tuple(p) for p in verts], "f": out}


def prism(bottom, top):
    """底环（俯视逆时针）+ 顶环（同序）围成的竖向棱柱。"""
    return make_solid(list(bottom) + list(top), prism_faces(len(bottom)))


def solid_volume(sol):
    """散度定理：各面按扇形剖成三角形求带符号体积（对平面多边形，凸凹都成立）。"""
    v = sol["v"]
    o = v[0]
    vol = 0.0
    for f in sol["f"]:
        a = _sub(v[f[0]], o)
        for i in range(1, len(f) - 1):
            b, c = _sub(v[f[i]], o), _sub(v[f[i + 1]], o)
            vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return vol / 6.0


def solid_closed(sol):
    """二流形且定向一致：每条有向边恰好出现一次，其反向边也恰好出现一次。"""
    seen = {}
    for f in sol["f"]:
        for i in range(len(f)):
            e = (f[i], f[(i + 1) % len(f)])
            seen[e] = seen.get(e, 0) + 1
    return all(c == 1 and seen.get((e[1], e[0]), 0) == 1 for e, c in seen.items())


def triangulate(sol):
    """把多于四边的平面多边形面剖成三角形（耳切法），供只收三 / 四边形的网格使用。"""
    v, out = sol["v"], []
    for f in sol["f"]:
        if len(f) <= 4:
            out.append(tuple(f))
            continue
        n = _newell([v[i] for i in f])
        ax = max(range(3), key=lambda i: abs(n[i]))
        u_, w_ = [(1, 2), (2, 0), (0, 1)][ax]
        sgn = 1.0 if n[ax] > 0 else -1.0
        pts = {i: (v[i][u_], v[i][w_]) for i in f}
        idx = list(f)

        def cross(a, b, c):
            return sgn * ((pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1])
                          - (pts[b][1] - pts[a][1]) * (pts[c][0] - pts[a][0]))

        def inside(p, a, b, c):
            return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

        while len(idx) > 3:
            for j in range(len(idx)):
                a, b, c = idx[j - 1], idx[j], idx[(j + 1) % len(idx)]
                if cross(a, b, c) <= 1e-12:
                    continue
                if any(inside(p, a, b, c) for p in idx if p not in (a, b, c)):
                    continue
                out.append((a, b, c))
                idx.pop(j)
                break
            else:
                raise ValueError("耳切失败：多边形不是简单多边形")
        out.append(tuple(idx))
    return {"v": v, "f": out}


def top_area(sections):
    """取样截面顶边围成的曲面面积（逐格按两个三角形）。sections: [(左顶点, 右顶点)]。"""
    def tri(a, b, c):
        u = _sub(b, a)
        w = _sub(c, a)
        x = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        return math.sqrt(sum(t * t for t in x)) / 2.0
    return sum(tri(p[0], p[1], q[1]) + tri(p[0], q[1], q[0]) for p, q in zip(sections, sections[1:]))


# ====================================================================== 梁长归并
def chords():
    """每片梁的支承弦。PA、PB：两支承线上、该梁偏距处的梁顶设计点；
    mu = 弦长 / 弦在支承线法向上的投影，梁端距支承线 h 时沿梁轴让出 mu·h。"""
    st = support_stations()
    out = {}
    for d, c in C.DECKS:
        for k in range(1, C.N_SPANS + 1):
            ta, tb = frame(st[k - 1])[1], frame(st[k])[1]
            for i, g in enumerate(C.GIRDER_OFFSETS, 1):
                o = c + g
                PA = AL.offset_xy(st[k - 1], o) + (AL.deck_top(st[k - 1], o, d) - C.PAVEMENT_T,)
                PB = AL.offset_xy(st[k], o) + (AL.deck_top(st[k], o, d) - C.PAVEMENT_T,)
                v = _sub(PB, PA)
                L3 = math.sqrt(sum(x * x for x in v))
                out[(d, k, i)] = {"PA": PA, "PB": PB, "L3": L3, "offset": o, "ta": ta, "tb": tb,
                                  "mu_a": L3 / (v[0] * ta[0] + v[1] * ta[1]),
                                  "mu_b": L3 / (v[0] * tb[0] + v[1] * tb[1]),
                                  "ka": end_kind(k - 1), "kb": end_kind(k)}
    return out


def family(ch):
    return {"CC": "中跨", "EC": "边跨", "CE": "边跨", "EE": "单跨"}[ch["ka"] + ch["kb"]]


def gap_range(ch, hmin=None, hmax=None):
    """g = 弦长 − 梁长（沿梁轴两端让出之和）的可行范围。中跨梁两端对称让出。"""
    hmin = C.CONT_HALF_MIN if hmin is None else hmin
    hmax = C.CONT_HALF_MAX if hmax is None else hmax
    ma, mb = ch["mu_a"], ch["mu_b"]
    if ch["ka"] == "C" and ch["kb"] == "C":
        return 2 * hmin * max(ma, mb), 2 * hmax * min(ma, mb)
    if ch["ka"] == "E" and ch["kb"] == "E":
        g = (ma + mb) * C.EXP_HALF
        return g, g
    me, mc = (ma, mb) if ch["ka"] == "E" else (mb, ma)
    return me * C.EXP_HALF + mc * hmin, me * C.EXP_HALF + mc * hmax


def length_interval(ch, hmin=None, hmax=None):
    """可行梁长区间，换算到 SPEC_STEP 网格上的整数 [lo, hi]。"""
    g0, g1 = gap_range(ch, hmin, hmax)
    lo = math.ceil((ch["L3"] - g1) / C.SPEC_STEP - 1e-9)
    hi = math.floor((ch["L3"] - g0) / C.SPEC_STEP + 1e-9)
    return lo, hi


def stab(intervals):
    """区间刺穿。intervals：{键: (lo, hi)}（整数网格）。按右端点排序贪心：
    遇到还没被刺穿的区间，就在它的右端点放一个点。
    返回 (点列表, {键: 点}, 证书)。证书里的区间两两不相交、个数等于点数，
    任何刺穿方案都至少要这么多个点——所以这个结果是最优的。"""
    items = sorted(intervals.items(), key=lambda kv: (kv[1][1], kv[1][0], kv[0]))
    points, assign, witness, cur = [], {}, [], None
    for key, (lo, hi) in items:
        if lo > hi:
            raise ValueError("%s 的可行梁长区间在网格上为空" % (key,))
        if cur is None or lo > cur:
            cur = hi
            points.append(cur)
            witness.append(key)
        assign[key] = cur
    return points, assign, witness


def equalize(chs=None, hmin=None, hmax=None):
    """按梁型分别刺穿。返回 ({梁型: (规格列表, 证书)}, {梁: 梁长})。"""
    chs = chs or chords()
    fams = {}
    for key, ch in chs.items():
        fams.setdefault(family(ch), {})[key] = length_interval(ch, hmin, hmax)
    result, assign = {}, {}
    for fam in sorted(fams):
        pts, asg, wit = stab(fams[fam])
        result[fam] = ([round(p * C.SPEC_STEP, 2) for p in pts], wit)
        for key, p in asg.items():
            assign[key] = round(p * C.SPEC_STEP, 2)
    return result, assign


def naive_lengths(chs=None):
    """对照：逐片按名义缝宽下料、取整到 SPEC_STEP，得到的 {(梁型, 梁长)}。"""
    chs = chs or chords()
    out = set()
    for ch in chs.values():
        ha = C.EXP_HALF if ch["ka"] == "E" else C.CONT_HALF_NOM
        hb = C.EXP_HALF if ch["kb"] == "E" else C.CONT_HALF_NOM
        S = ch["L3"] - ch["mu_a"] * ha - ch["mu_b"] * hb
        out.add((family(ch), round(round(S / C.SPEC_STEP) * C.SPEC_STEP, 2)))
    return sorted(out)


def end_halves(ch, S):
    """梁长定为 S 后，两端面到支承线的距离 (h_a, h_b)。"""
    g = ch["L3"] - S
    ma, mb = ch["mu_a"], ch["mu_b"]
    if ch["ka"] == "C" and ch["kb"] == "C":
        return g / (2 * ma), g / (2 * mb)
    if ch["ka"] == "E" and ch["kb"] == "E":
        return C.EXP_HALF, C.EXP_HALF
    if ch["ka"] == "E":
        return C.EXP_HALF, (g - ma * C.EXP_HALF) / mb
    return (g - mb * C.EXP_HALF) / ma, C.EXP_HALF


# ====================================================================== 预制 T 梁
def _section_ring(p, D, N, t):
    """梁端 12 点：竖直截面（横向 N、竖向 z）上的点沿梁轴 D 滑到端面——过 p、法向 t 的竖直平面。"""
    Nt = N[0] * t[0] + N[1] * t[1]
    Dt = D[0] * t[0] + D[1] * t[1]
    ring = []
    for x, y in C.T_SECTION:
        dl = -x * Nt / Dt
        ring.append((p[0] + x * N[0] + dl * D[0], p[1] + x * N[1] + dl * D[1], p[2] + y + dl * D[2]))
    return ring


def _girder(key, ch, S):
    d, k, i = key
    ha, hb = end_halves(ch, S)
    PA, PB = ch["PA"], ch["PB"]
    v = _sub(PB, PA)
    la, lb = ha * ch["mu_a"] / ch["L3"], 1.0 - hb * ch["mu_b"] / ch["L3"]
    p0 = tuple(PA[j] + v[j] * la for j in range(3))
    p1 = tuple(PA[j] + v[j] * lb for j in range(3))
    D = tuple((p1[j] - p0[j]) / S for j in range(3))
    lp = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    u = ((p1[0] - p0[0]) / lp, (p1[1] - p0[1]) / lp)
    N = (-u[1], u[0])
    ring_a = _section_ring(p0, D, N, ch["ta"])
    ring_b = _section_ring(p1, D, N, ch["tb"])
    sol = make_solid(ring_a + ring_b, prism_faces(len(ring_a)))

    def skew(t):
        return math.degrees(math.asin(u[0] * t[1] - u[1] * t[0]))

    attrs = {"span": k, "girder": i, "unit": unit_of_span(k), "offset": round(ch["offset"], 3),
             "length": S, "plan_length": lp, "family": family(ch), "end_a": ch["ka"], "end_b": ch["kb"],
             "half_a": ha, "half_b": hb, "skew_a": skew(ch["ta"]), "skew_b": skew(ch["tb"])}
    params = {"solids": [sol], "p0": p0, "p1": p1, "u": u, "ta": ch["ta"], "tb": ch["tb"]}
    return Element("G-%s%02d-%d" % key, "girder", "SUP-" + d, d, "mesh", params, attrs, solid_volume(sol))


def corner(g, end, idx):
    """梁端截面第 idx 点（IDX_L / IDX_R 为左 / 右翼缘顶角）。"""
    return g.params["solids"][0]["v"][idx + (0 if end == "a" else len(C.T_SECTION))]


def soffit_z(g, q):
    """梁底面（竖直截面沿梁轴扫出，梁底是含梁轴方向的平面）在平面点 q 处的高程。"""
    p0, p1, u = g.params["p0"], g.params["p1"], g.params["u"]
    lp = g.attrs["plan_length"]
    return p0[2] - C.H_GIRDER + ((q[0] - p0[0]) * u[0] + (q[1] - p0[1]) * u[1]) * (p1[2] - p0[2]) / lp


# ====================================================================== 桥面现浇部分
def _loft(sections):
    """sections：[(lo 点, hi 点)]，每点 (x, y, 底高程, 顶高程)，lo 在右、hi 在左，沿路线前进方向排列。"""
    ring = []
    for lo, hi in sections:
        ring += [(lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]), (hi[0], hi[1], hi[3]), (lo[0], lo[1], lo[3])]
    return make_solid(ring, loft_faces(len(sections), 4))


def _stations(s0, s1):
    n = max(2, int(math.ceil((s1 - s0) / C.LOFT_STEP - 1e-9)))
    return [s0 + (s1 - s0) * j / n for j in range(n + 1)]


def _span_band(d, k, lo, hi, dz_bottom, dz_top):
    """跨 k 上、偏距 lo..hi 之间、随路线曲线的带：连续墩处到墩中心线为止（相邻跨在此相接），
    伸缩端让出 EXP_HALF。返回 (实体, 顶边截面)。"""
    st = support_stations()
    ends = []
    for sup, sgn in ((k - 1, 1.0), (k, -1.0)):
        h = 0.0 if end_kind(sup) == "C" else sgn * C.EXP_HALF
        P, t, _ = frame(st[sup])
        ends.append([station_on_plane(o, P, t, h, st[sup]) for o in (lo, hi)])
    inner = _stations(st[k - 1] + (C.EXP_HALF if end_kind(k - 1) == "E" else 0.0),
                      st[k] - (C.EXP_HALF if end_kind(k) == "E" else 0.0))[1:-1]
    rows = [ends[0]] + [[s, s] for s in inner] + [ends[1]]
    secs, tops = [], []
    for s_lo, s_hi in rows:
        pts = []
        for s, o in ((s_lo, lo), (s_hi, hi)):
            x, y = AL.offset_xy(s, o)
            z = AL.deck_top(s, o, d)
            pts.append((x, y, z + dz_bottom, z + dz_top))
        secs.append(tuple(pts))
        tops.append(tuple((p[0], p[1], p[3]) for p in pts))
    return _loft(secs), tops


def _flange_edge_at(ca, cb, s):
    """翼缘边线（ca→cb 直线）与桩号 s 处径向竖直面的交点。"""
    P, t, _ = frame(s)
    lam = ((P[0] - ca[0]) * t[0] + (P[1] - ca[1]) * t[1]) / ((cb[0] - ca[0]) * t[0] + (cb[1] - ca[1]) * t[1])
    return tuple(ca[j] + (cb[j] - ca[j]) * lam for j in range(3))


def _cantilever(g, side, d, c):
    """翼缘现浇段：边梁翼缘边线（直线）到桥面边缘（随曲线）。side 1 在右（1 号梁外）、2 在左。
    两端截面落在梁端面上。返回 (实体, 起端桥面边缘点, 止端桥面边缘点)。"""
    idx = IDX_R if side == "1" else IDX_L
    ca, cb = corner(g, "a", idx), corner(g, "b", idx)
    o_edge = c - C.DECK_WIDTH / 2 if side == "1" else c + C.DECK_WIDTH / 2
    sa = project(ca[0], ca[1], C.BRIDGE_START + (g.attrs["span"] - 1) * C.SPAN)[0]
    sb = project(cb[0], cb[1], C.BRIDGE_START + g.attrs["span"] * C.SPAN)[0]
    ea = station_on_plane(o_edge, ca, g.params["ta"], 0.0, sa)
    eb = station_on_plane(o_edge, cb, g.params["tb"], 0.0, sb)

    def edge_pt(s):
        x, y = AL.offset_xy(s, o_edge)
        return (x, y, AL.deck_top(s, o_edge, d) - C.PAVEMENT_T)

    rows = [(ca, edge_pt(ea))] + [(_flange_edge_at(ca, cb, s), edge_pt(s)) for s in _stations(sa, sb)[1:-1]] \
        + [(cb, edge_pt(eb))]
    secs = []
    for inner, outer in rows:
        a = (inner[0], inner[1], inner[2] - C.WET_JOINT_T, inner[2])
        b = (outer[0], outer[1], outer[2] - C.WET_JOINT_T, outer[2])
        secs.append((b, a) if side == "1" else (a, b))
    return _loft(secs), rows[0][1], rows[-1][1]


def _drop(top, h):
    return [(p[0], p[1], p[2] - h) for p in top]


def diaphragm_stations(g1, g2):
    """一跨里两片相邻梁之间 5 道横隔板的桩号：两道端横隔板在两片梁中较远的梁端再往里
    BEARING_INSET 处（落在两片梁的范围内、与支座对齐），三道在 1/4、1/2、3/4 跨。"""
    k = g1.attrs["span"]
    st = support_stations()
    sa, sb = st[k - 1], st[k]
    ha = max(g1.attrs["half_a"], g2.attrs["half_a"]) + C.BEARING_INSET
    hb = max(g1.attrs["half_b"], g2.attrs["half_b"]) + C.BEARING_INSET
    return ([(sa + ha, C.END_DIAPHRAGM_T)] + [(sa + f * (sb - sa), C.DIAPHRAGM_T) for f in C.DIAPHRAGM_FRACTIONS]
            + [(sb - hb, C.END_DIAPHRAGM_T)])


def _diaphragm(g1, g2, s, t):
    """桩号 s 处沿径向的横隔板：右梁 g1 的左腹板面到左梁 g2 的右腹板面，沿梁轴厚 t。"""
    ends = []
    for g, lat in ((g1, C.WEB_HALF), (g2, -C.WEB_HALF)):
        p0, p1, u = g.params["p0"], g.params["p1"], g.params["u"]
        grade = (p1[2] - p0[2]) / g.attrs["plan_length"]
        c = _flange_edge_at(p0, p1, s)                 # 梁轴（梁顶中心线）与该径向竖直面的交点
        nx, ny = -u[1], u[0]
        ends.append([(c[0] + sg * t / 2 * u[0] + lat * nx, c[1] + sg * t / 2 * u[1] + lat * ny, c[2] + sg * t / 2 * grade)
                     for sg in (-1.0, 1.0)])
    (a0, a1), (b0, b1) = ends
    ring = (a0, a1, b1, b0)                            # 俯视逆时针：沿 g1 前进、折向 g2、沿 g2 后退
    return prism([(p[0], p[1], p[2] + C.DIAPHRAGM_BOTTOM) for p in ring],
                 [(p[0], p[1], p[2] + C.DIAPHRAGM_TOP) for p in ring])


# ====================================================================== 生成
def build():
    """生成全部构件。返回 (构件列表, 支承表)。支承表含永久支座（end 伸缩端 / cont 连续墩）
    与临时支座（temp），每项带所在支承、幅、位置、顶底高程。"""
    chs = chords()
    _, assign = equalize(chs)
    st = support_stations()
    els, sup = [], []
    G, ct_ends = {}, {}
    on_cap = {}                  # (支承号, 幅) -> [支承项]
    n_g = len(C.GIRDER_OFFSETS)

    for d, c in C.DECKS:
        for k in range(1, C.N_SPANS + 1):
            # ---------------------------------------------------------------- 预制 T 梁
            for i in range(1, n_g + 1):
                g = _girder((d, k, i), chs[(d, k, i)], assign[(d, k, i)])
                G[(d, k, i)] = g
                els.append(g)
                u = g.params["u"]
                for end, s_idx, sgn in (("a", k - 1, 1.0), ("b", k, -1.0)):
                    p = g.params["p0" if end == "a" else "p1"]
                    kind = g.attrs["end_" + end]
                    inset = C.BEARING_INSET if kind == "E" else C.TEMP_INSET
                    q = (p[0] + sgn * u[0] * inset, p[1] + sgn * u[1] * inset)
                    item = {"id": ("B-" if kind == "E" else "TS-") + "%s%02d-%d%s" % (d, k, i, end),
                            "kind": "end" if kind == "E" else "temp", "support": s_idx, "deck": d, "line": i,
                            "girders": [g.eid], "xy": q, "top": soffit_z(g, q), "girder": g}
                    on_cap.setdefault((s_idx, d), []).append(item)
            # ---------------------------------------------------------------- 横隔板（沿径向，每跨 5 道）
            u_ = unit_of_span(k)
            for i in range(1, n_g):
                g1, g2 = G[(d, k, i)], G[(d, k, i + 1)]
                for j, (s_d, t_d) in enumerate(diaphragm_stations(g1, g2), 1):
                    sol = _diaphragm(g1, g2, s_d, t_d)
                    els.append(Element("D-%s%02d-%d%d" % (d, k, i, j), "diaphragm", "SUP-" + d, d, "mesh",
                                       {"solids": [sol]}, {"span": k, "unit": u_, "pair": i, "pos": j, "station": s_d,
                                                           "thickness": t_d}, solid_volume(sol)))
            # ---------------------------------------------------------------- 湿接缝与翼缘现浇段
            for i in range(1, n_g):
                g1, g2 = G[(d, k, i)], G[(d, k, i + 1)]
                top = [corner(g1, "a", IDX_L), corner(g1, "b", IDX_L), corner(g2, "b", IDX_R), corner(g2, "a", IDX_R)]
                sol = prism(_drop(top, C.WET_JOINT_T), top)
                els.append(Element("WJ-%s%02d-%d" % (d, k, i), "wet_joint", "SUP-" + d, d, "mesh", {"solids": [sol]},
                                   {"span": k, "unit": u_, "width_a": _dist(top[0], top[3]),
                                    "width_b": _dist(top[1], top[2])}, solid_volume(sol)))
            for side, gi in (("1", 1), ("2", n_g)):
                sol, ea, eb = _cantilever(G[(d, k, gi)], side, d, c)
                ct_ends[(d, k, side)] = (ea, eb)
                els.append(Element("CT-%s%02d-%s" % (d, k, side), "cantilever", "SUP-" + d, d, "mesh",
                                   {"solids": [sol]}, {"span": k, "unit": u_}, solid_volume(sol)))
            # ---------------------------------------------------------------- 铺装与护栏
            inner = C.DECK_WIDTH / 2 - C.BARRIER_W
            sol, tops = _span_band(d, k, c - inner, c + inner, -C.PAVEMENT_T, 0.0)
            els.append(Element("PV-%s%02d" % (d, k), "pavement", "SUP-" + d, d, "mesh", {"solids": [sol]},
                               {"span": k, "unit": u_, "area": top_area(tops)}, solid_volume(sol)))
            for side, lo, hi in (("1", c - C.DECK_WIDTH / 2, c - inner), ("2", c + inner, c + C.DECK_WIDTH / 2)):
                sol, _ = _span_band(d, k, lo, hi, -C.PAVEMENT_T, C.BARRIER_H)
                els.append(Element("BR-%s%02d-%s" % (d, k, side), "barrier", "SUP-" + d, d, "mesh",
                                   {"solids": [sol]}, {"span": k, "unit": u_}, solid_volume(sol)))

    # -------------------------------------------------------------------- 墩顶现浇连续段与连续墩永久支座
    for k in range(1, C.N_SPANS):
        if support_kind(k) != "C":
            continue
        name = support_name(k)
        for d, c in C.DECKS:
            pieces, joints = [], []
            for i in range(1, n_g + 1):
                ga, gb = G[(d, k, i)], G[(d, k + 1, i)]
                top = [corner(ga, "b", IDX_R), corner(gb, "a", IDX_R), corner(gb, "a", IDX_L), corner(ga, "b", IDX_L)]
                pieces.append(prism(_drop(top, C.H_GIRDER), top))
                hb, ha = ga.attrs["half_b"], gb.attrs["half_a"]
                joints.append(hb + ha)
                tau = hb / (hb + ha)
                p1, p0 = ga.params["p1"], gb.params["p0"]
                q = tuple(p1[j] + (p0[j] - p1[j]) * tau for j in range(3))
                on_cap.setdefault((k, d), []).append(
                    {"id": "B-%s-%s%d" % (name, d, i), "kind": "cont", "support": k, "deck": d, "line": i,
                     "girders": [ga.eid, gb.eid], "xy": (q[0], q[1]), "top": q[2] - C.H_GIRDER, "joint": (ga, gb)})
            for i in range(1, n_g):                       # 梁位之间：墩顶横梁，全梁高
                top = [corner(G[(d, k, i)], "b", IDX_L), corner(G[(d, k + 1, i)], "a", IDX_L),
                       corner(G[(d, k + 1, i + 1)], "a", IDX_R), corner(G[(d, k, i + 1)], "b", IDX_R)]
                pieces.append(prism(_drop(top, C.H_GIRDER), top))
            e1b, e1f = ct_ends[(d, k, "1")][1], ct_ends[(d, k + 1, "1")][0]
            e2b, e2f = ct_ends[(d, k, "2")][1], ct_ends[(d, k + 1, "2")][0]
            for top in ([e1b, e1f, corner(G[(d, k + 1, 1)], "a", IDX_R), corner(G[(d, k, 1)], "b", IDX_R)],
                        [corner(G[(d, k, n_g)], "b", IDX_L), corner(G[(d, k + 1, n_g)], "a", IDX_L), e2f, e2b]):
                pieces.append(prism(_drop(top, C.WET_JOINT_T), top))   # 边梁外侧：只有桥面板厚
            els.append(Element("CS-%s-%s" % (name, d), "continuity", "SUP-" + d, d, "mesh", {"solids": pieces},
                               {"support": k, "unit": unit_of_span(k), "joint_min": min(joints),
                                "joint_max": max(joints)}, sum(solid_volume(p) for p in pieces)))

    # -------------------------------------------------------------------- 伸缩装置
    for k in range(C.N_SPANS + 1):
        if end_kind(k) != "E":
            continue
        P, t, _ = frame(st[k])
        h0 = -C.EXP_HALF if k > 0 else 0.0
        h1 = C.EXP_HALF if k < C.N_SPANS else 0.0
        for d, c in C.DECKS:
            secs = []
            for h in (h0, h1):
                pts = []
                for o in (c - C.DECK_WIDTH / 2, c + C.DECK_WIDTH / 2):
                    s = station_on_plane(o, P, t, h, st[k])
                    x, y = AL.offset_xy(s, o)
                    z = AL.deck_top(s, o, d)
                    pts.append((x, y, z - C.PAVEMENT_T - C.WET_JOINT_T, z))
                secs.append(tuple(pts))
            sol = _loft(secs)
            els.append(Element("EJ-%s-%s" % (support_name(k), d), "expansion_joint", "SUP-" + d, d, "mesh",
                               {"solids": [sol]}, {"support": k, "length": C.DECK_WIDTH, "gap": h1 - h0},
                               solid_volume(sol)))

    # -------------------------------------------------------------------- 下部结构
    for k, s in enumerate(st):
        name = support_name(k)
        kind = support_kind(k)
        abut = kind == "A"
        toward = 1.0 if k == 0 else -1.0
        (ox, oy), t, n = frame(s)
        for d, c in C.DECKS:
            oc = (ox + c * n[0], oy + c * n[1])
            sigma = AL.slope_left(s, d)

            def a_of(q, oc=oc, n=n):
                return (q[0] - oc[0]) * n[0] + (q[1] - oc[1]) * n[1]

            def at(b, a, oc=oc, t=t, n=n):
                return (oc[0] + b * t[0] + a * n[0], oc[1] + b * t[1] + a * n[1])

            items = on_cap[(k, d)]
            for it in items:
                if it["kind"] == "end":
                    it["seat_top"] = it["top"] - C.BEARING_T
                elif it["kind"] == "cont":
                    it["seat_top"] = it["top"] - C.BEARING_CONT_T
            z_c = min(it["seat_top"] + sigma * a_of(it["xy"]) for it in items if "seat_top" in it) - C.SEAT_MIN

            def cap_top(q, z_c=z_c, sigma=sigma, a_of=a_of):
                return z_c - sigma * a_of(q)

            # 盖梁 / 台帽：顶面随横坡，底面水平
            if abut:
                b0, b1 = sorted((-toward * C.BACKWALL_T, toward * (C.ABUT_CAP_W - C.BACKWALL_T)))
                depth, cls, eid = C.ABUT_CAP_H, "abut_cap", "ABC-%s-%s" % (name, d)
            else:
                b0, b1 = -C.CAP_W / 2, C.CAP_W / 2
                depth, cls, eid = C.CAP_H, "cap", "CAP-%s-%s" % (name, d)
            a0, a1 = -C.CAP_LEN / 2, C.CAP_LEN / 2
            foot = [at(b0, a0), at(b1, a0), at(b1, a1), at(b0, a1)]
            bottom_z = z_c - depth
            sol = prism([(x, y, bottom_z) for x, y in foot], [(x, y, cap_top((x, y))) for x, y in foot])
            cap_frame = {"o": oc, "t": t, "n": n, "b": (b0, b1), "a": (a0, a1), "z_c": z_c, "slope": sigma}
            els.append(Element(eid, cls, name, d, "mesh", {"solids": [sol], "frame": cap_frame},
                               {"support": k, "kind": kind, "top": z_c, "bottom": bottom_z}, solid_volume(sol)))
            if abut:
                bw0, bw1 = sorted((-toward * C.BACKWALL_T, 0.0))
                foot = [at(bw0, a0), at(bw1, a0), at(bw1, a1), at(bw0, a1)]
                deck = [AL.deck_top(s, c + a, d) for a in (a0, a0, a1, a1)]
                sol = prism([(x, y, cap_top((x, y))) for x, y in foot],
                            [(x, y, z) for (x, y), z in zip(foot, deck)])
                els.append(Element("BW-%s-%s" % (name, d), "backwall", name, d, "mesh", {"solids": [sol]},
                                   {"support": k, "height": AL.deck_top(s, c, d) - z_c}, solid_volume(sol)))

            # 垫石、永久支座、临时支座
            for it in items:
                q = it["xy"]
                if it["kind"] == "temp":
                    w = C.TEMP_W / 2
                    foot = [(q[0] + bb * t[0] + aa * n[0], q[1] + bb * t[1] + aa * n[1])
                            for bb, aa in ((-w, -w), (w, -w), (w, w), (-w, w))]
                    g = it["girder"]
                    sol = prism([(x, y, cap_top((x, y))) for x, y in foot],
                                [(x, y, soffit_z(g, (x, y))) for x, y in foot])
                    it["bottom"] = cap_top(q)
                    it["height"] = it["top"] - it["bottom"]
                    els.append(Element(it["id"], "temp_support", name, d, "mesh", {"solids": [sol]},
                                       {"support": k, "girder": g.eid, "height": it["height"]}, solid_volume(sol)))
                    continue
                cont = it["kind"] == "cont"
                w = (C.SEAT_W_CONT if cont else C.SEAT_W) / 2
                foot = [(q[0] + bb * t[0] + aa * n[0], q[1] + bb * t[1] + aa * n[1])
                        for bb, aa in ((-w, -w), (w, -w), (w, w), (-w, w))]
                sol = prism([(x, y, cap_top((x, y))) for x, y in foot], [(x, y, it["seat_top"]) for x, y in foot])
                it["seat"] = "S-" + it["id"][2:]
                it["seat_height"] = it["seat_top"] - cap_top(q)
                it["cap_top"] = cap_top(q)
                els.append(Element(it["seat"], "seat", name, d, "mesh", {"solids": [sol], "w": 2 * w},
                                   {"support": k, "height": it["seat_height"], "bearing": it["id"]},
                                   solid_volume(sol)))
                attrs = {"support": k, "girders": it["girders"], "kind": it["kind"]}
                if cont:                                    # 矩形：l 沿支承线（横桥向）、w 顺桥向
                    ba, bb_, bt = C.BEARING_CONT_A, C.BEARING_CONT_B, C.BEARING_CONT_T
                    it["bottom"] = it["top"] - bt
                    attrs.update(type="GJZ", sliding=False)
                    it["size"] = attrs["size"] = "GJZ %d×%d×%d" % (round(ba * 1000), round(bb_ * 1000), round(bt * 1000))
                    els.append(Element(it["id"], "bearing", name, d, "box",
                                       {"c": (q[0], q[1], it["bottom"]), "dir": n, "l": bb_, "w": ba, "h": bt},
                                       attrs, ba * bb_ * bt))
                else:                                       # 滑板支座：橡胶支座 + 顶面四氟板，厚度含四氟板
                    bd, bt = C.BEARING_D, C.BEARING_T
                    it["bottom"] = it["top"] - bt
                    slide = C.END_BEARING_SLIDING
                    attrs.update(type="GYZF4" if slide else "GYZ", sliding=slide)
                    it["size"] = attrs["size"] = "%s φ%d×%d" % (attrs["type"], round(bd * 1000), round(bt * 1000))
                    els.append(Element(it["id"], "bearing", name, d, "cyl",
                                       {"c": (q[0], q[1], it["bottom"]), "r": bd / 2, "h": bt}, attrs,
                                       math.pi * (bd / 2) ** 2 * bt))

            # 墩柱、桩、系梁（桥台不设墩柱，桩直接接台帽）
            b_mid = (b0 + b1) / 2 if abut else 0.0
            for j, co in enumerate(C.COLUMN_OFFSETS, 1):
                px, py = at(b_mid, co)
                if abut:
                    pile_top, pd, plen = bottom_z, C.ABUT_PILE_D, C.PILE_LEN_ABUT
                else:
                    gz = AL.ground(s, c + co)
                    pile_top, pd, plen = gz - C.COLUMN_EMBED, C.PILE_D, C.PILE_LEN_PIER
                    hcol = bottom_z - pile_top
                    els.append(Element("C-%s-%s%d" % (name, d, j), "column", name, d, "cyl",
                                       {"c": (px, py, pile_top), "r": C.COLUMN_D / 2, "h": hcol},
                                       {"support": k, "height": hcol, "ground": gz},
                                       math.pi * (C.COLUMN_D / 2) ** 2 * hcol))
                els.append(Element("PL-%s-%s%d" % (name, d, j), "pile", name, d, "cyl",
                                   {"c": (px, py, pile_top - plen), "r": pd / 2, "h": plen},
                                   {"support": k, "tip": pile_top - plen}, math.pi * (pd / 2) ** 2 * plen))
            if not abut:
                span_between = C.COLUMN_OFFSETS[1] - C.COLUMN_OFFSETS[0] - C.COLUMN_D
                tz = AL.ground(s, c) - 0.2 - C.TIE_H
                els.append(Element("TB-%s-%s" % (name, d), "tie", name, d, "box",
                                   {"c": (oc[0], oc[1], tz), "dir": n, "l": span_between, "w": C.TIE_W, "h": C.TIE_H},
                                   {"support": k}, span_between * C.TIE_W * C.TIE_H))
            for it in items:
                row = {kk: vv for kk, vv in it.items() if kk not in ("girder", "joint")}
                row["x"], row["y"] = it["xy"]
                del row["xy"]
                sup.append(row)
    return els, sup
