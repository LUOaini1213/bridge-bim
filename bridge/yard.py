"""预制梁场布置：几何（供 Rhino 画图）与检查。

梁场局部坐标：u 沿路线切线方向（桩号增大方向），v 指向路线右侧（离开路线）。
一排制梁台座、一排存梁台座（双层）并列，两台龙门吊沿 u 向轨道抬吊；
运梁便道从梁场出口接到 0 号桥台。
"""
import math

from . import alignment as AL, config as C


def frame():
    """梁场局部原点与两个单位向量（世界坐标）。"""
    ox, oy = AL.offset_xy(C.YARD_STATION, C.YARD_OFFSET)
    _, _, th, _ = AL.at_station(C.YARD_STATION)
    u = (math.cos(th), math.sin(th))
    v = (u[1], -u[0])                     # 右手侧
    return (ox, oy), u, v


def to_world(pu, pv):
    (ox, oy), u, v = frame()
    return (ox + pu * u[0] + pv * v[0], oy + pu * u[1] + pv * v[1])


def beds(n=None):
    n = n or C.N_BEDS
    pitch = C.BED_W + C.BED_GAP
    return [(i * pitch, 0.0, i * pitch + C.BED_W, C.BED_L) for i in range(n)]


def storage():
    start = C.N_BEDS * (C.BED_W + C.BED_GAP) + 8.0
    pitch = C.SLOT_W + C.SLOT_GAP
    return [(start + i * pitch, 0.0, start + i * pitch + C.SLOT_W, C.BED_L) for i in range(C.STORAGE_POSITIONS)]


def rebar_area():
    w, l = C.REBAR_AREA
    return (-w - 6.0, 0.0, -6.0, l)


def rails():
    """龙门吊轨道：两条沿 u 向的直线，跨越台座全长两侧。"""
    s = storage()
    u0, u1 = rebar_area()[0], s[-1][2] + 4.0
    return [((u0, -3.0), (u1, -3.0)), ((u0, C.BED_L + 3.0), (u1, C.BED_L + 3.0))]


def boundary():
    s = storage()
    return (rebar_area()[0] - 6.0, -10.0, s[-1][2] + 10.0, C.BED_L + 10.0)


def haul_route():
    """运梁便道：梁场出口 → 路基 → 0 号桥台（右幅中心线）。返回世界坐标折线。"""
    b = boundary()
    right = dict(C.DECKS)["R"]
    join = min(C.YARD_STATION + b[2] + 15.0, C.BRIDGE_START - 10.0)   # 上路基的位置，不越过桥台
    return [to_world(b[2], -4.0), AL.offset_xy(join, right), AL.offset_xy(C.BRIDGE_START, right)]


def _length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _min_dist_to_road(rects):
    """矩形四角到路线中线的最小距离（按投影偏距取绝对值）。"""
    from .model import project
    worst = float("inf")
    for r in rects:
        for pu, pv in ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])):
            x, y = to_world(pu, pv)
            _, o = project(x, y, C.YARD_STATION + pu)
            worst = min(worst, abs(o))
    return worst


def capacity():
    return C.STORAGE_POSITIONS * C.STORAGE_LAYERS


def run_checks(summary, heaviest_t):
    lift = C.GANTRY_COUNT * C.GANTRY_SWL_T * C.DUAL_LIFT_FACTOR
    dist = _min_dist_to_road([boundary()])
    return [
        ("存梁峰值 ≤ 存梁容量（%d 个台座 × %d 层）" % (C.STORAGE_POSITIONS, C.STORAGE_LAYERS),
         summary["storage_peak"] <= capacity(), "容量 %d 片，峰值 %d 片（%s）" % (
             capacity(), summary["storage_peak"], summary["storage_peak_day"].isoformat())),
        ("存梁期 ≤ %d 天" % C.MAX_STORAGE_DAYS, summary["storage_max_days"] <= C.MAX_STORAGE_DAYS,
         "最长 %d 天" % summary["storage_max_days"]),
        ("架设时龄期 ≥ %d 天" % C.MIN_AGE_DAYS, summary["age_min"] >= C.MIN_AGE_DAYS, "最短 %d 天" % summary["age_min"]),
        ("龙门吊抬吊能力 ≥ 最重预制梁", lift >= heaviest_t,
         "%d 台 × %.0f t × %.1f = %.0f t，最重梁 %.1f t" % (C.GANTRY_COUNT, C.GANTRY_SWL_T, C.DUAL_LIFT_FACTOR, lift, heaviest_t)),
        ("梁场不占路基（距路线中线 ≥ %.0f m）" % C.MIN_YARD_TO_ROAD, dist >= C.MIN_YARD_TO_ROAD,
         "梁场边缘距中线 %.1f m" % dist),
    ]


def haul_length():
    return _length(haul_route())
