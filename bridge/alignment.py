"""路线：平曲线（直线 / 回旋线 / 圆曲线）、竖曲线（抛物线）、横坡与超高、地面线。

平面坐标由线元逐段积分得到：方位角 θ(t) = θ0 + κ0·t + (κ1-κ0)·t²/(2L)，
位置 = 起点 + ∫(cos θ, sin θ) dt。直线与圆曲线有解析解；回旋线没有，
这里用分段 5 点 Gauss–Legendre 求积。另附测量上常用的回旋线级数公式，
供测试做独立对照（两种算法、再加 ifcopenshell 的线形几何，三方互核）。
"""
import math

from . import config as C

_GL_X = (-0.9061798459386640, -0.5384693101056831, 0.0, 0.5384693101056831, 0.9061798459386640)
_GL_W = (0.2369268850561891, 0.4786286704993665, 0.5688888888888889, 0.4786286704993665, 0.2369268850561891)


class Segment:
    def __init__(self, kind, s0, length, k0, k1, x0, y0, th0):
        self.kind, self.s0, self.L, self.k0, self.k1 = kind, s0, length, k0, k1
        self.x0, self.y0, self.th0 = x0, y0, th0

    def heading(self, t):
        return self.th0 + self.k0 * t + (self.k1 - self.k0) * t * t / (2.0 * self.L)

    def curvature(self, t):
        return self.k0 + (self.k1 - self.k0) * t / self.L

    def at(self, t):
        """线元内距起点 t 处的 (x, y, 方位角, 曲率)。"""
        th = self.heading(t)
        if self.kind == "LINE":
            return (self.x0 + t * math.cos(self.th0), self.y0 + t * math.sin(self.th0), th, 0.0)
        if self.kind == "CIRCULARARC":
            k = self.k0
            return (self.x0 + (math.sin(th) - math.sin(self.th0)) / k,
                    self.y0 - (math.cos(th) - math.cos(self.th0)) / k, th, k)
        dx, dy = integrate(self.heading, t)
        return (self.x0 + dx, self.y0 + dy, th, self.curvature(t))


def integrate(heading, t, step=2.0):
    """∫0^t (cos θ, sin θ) du，分段 5 点 Gauss–Legendre。"""
    if t == 0:
        return 0.0, 0.0
    n = max(1, int(math.ceil(abs(t) / step)))
    h = t / n
    sx = sy = 0.0
    for i in range(n):
        a = i * h
        for xi, wi in zip(_GL_X, _GL_W):
            u = a + (xi + 1.0) * h / 2.0
            th = heading(u)
            sx += wi * math.cos(th)
            sy += wi * math.sin(th)
    return sx * h / 2.0, sy * h / 2.0


def clothoid_series(A, l, terms=6):
    """回旋线（起点曲率 0）在切线坐标系下的级数公式：
    x = l - l^5/(40A^4) + l^9/(3456A^8) - …，y = l^3/(6A^2) - l^7/(336A^6) + …
    即 x = Σ (-1)^n l^(4n+1) / ((4n+1)(2n)! (2A²)^(2n))，y 同理取 4n+3 与 (2n+1)!。"""
    x = y = 0.0
    for n in range(terms):
        x += (-1) ** n * l ** (4 * n + 1) / ((4 * n + 1) * math.factorial(2 * n) * (2 * A * A) ** (2 * n))
        y += (-1) ** n * l ** (4 * n + 3) / ((4 * n + 3) * math.factorial(2 * n + 1) * (2 * A * A) ** (2 * n + 1))
    return x, y


def build_segments():
    segs, s = [], 0.0
    x, y = C.START_XY
    th = math.radians(C.START_AZIMUTH_DEG)
    for kind, L, k0, k1 in C.H_SEGMENTS:
        seg = Segment(kind, s, L, k0, k1, x, y, th)
        x, y, th, _ = seg.at(L)
        segs.append(seg)
        s += L
    return segs


SEGMENTS = build_segments()
LENGTH = sum(s.L for s in SEGMENTS)


def at_station(station):
    """桩号处中线的 (x, y, 方位角, 曲率)。"""
    d = station - C.START_STATION
    if d < -1e-9 or d > LENGTH + 1e-9:
        raise ValueError("桩号 %.3f 超出路线范围" % station)
    for seg in SEGMENTS:
        if d <= seg.s0 + seg.L + 1e-12:
            return seg.at(max(0.0, d - seg.s0))
    return SEGMENTS[-1].at(SEGMENTS[-1].L)


def offset_xy(station, offset):
    """桩号处、中线左侧 offset 米（右侧为负）的平面坐标。"""
    x, y, th, _ = at_station(station)
    return (x - offset * math.sin(th), y + offset * math.cos(th))


def profile(station):
    """设计高程与纵坡（中线）。抛物线竖曲线：z = z_BVC + g1·x + (g2-g1)·x²/(2L)。"""
    pvis, lens = C.V_PVI, C.V_CURVE_LENGTH
    grades = [(pvis[i + 1][1] - pvis[i][1]) / (pvis[i + 1][0] - pvis[i][0]) for i in range(len(pvis) - 1)]
    for i in range(1, len(pvis) - 1):
        L = lens[i]
        if L <= 0:
            continue
        bvc = pvis[i][0] - L / 2.0
        if bvc <= station <= bvc + L:
            g1, g2 = grades[i - 1], grades[i]
            x = station - bvc
            z_bvc = pvis[i][1] - g1 * L / 2.0
            return z_bvc + g1 * x + (g2 - g1) * x * x / (2.0 * L), g1 + (g2 - g1) * x / L
    for i in range(len(pvis) - 1):
        if pvis[i][0] - 1e-9 <= station <= pvis[i + 1][0] + 1e-9:
            return pvis[i][1] + grades[i] * (station - pvis[i][0]), grades[i]
    raise ValueError("桩号 %.3f 超出纵断面范围" % station)


def _segment_bounds():
    b, s = [], C.START_STATION
    for kind, L, _, _ in C.H_SEGMENTS:
        b.append((kind, s, s + L))
        s += L
    return b


def slope_left(station, deck):
    """该幅路面「向左侧下降」的坡率：直线段按路拱向外侧，圆曲线段全超高向内侧，回旋线上线性过渡。"""
    normal = C.CROSSFALL if deck == "L" else -C.CROSSFALL
    full = C.SUPERELEVATION
    bounds = _segment_bounds()
    kinds = [k for k, _, _ in bounds]
    arc = kinds.index("CIRCULARARC")
    (_, c1a, c1b), (_, _, _), (_, c2a, c2b) = bounds[arc - 1], bounds[arc], bounds[arc + 1]
    if station <= c1a or station >= c2b:
        return normal
    if station < c1b:
        return normal + (full - normal) * (station - c1a) / (c1b - c1a)
    if station <= c2a:
        return full
    return full + (normal - full) * (station - c2a) / (c2b - c2a)


def deck_center(deck):
    return dict(C.DECKS)[deck]


def deck_top(station, offset, deck):
    """设计路面高程：中线设计高程，绕该幅中心线按横坡 / 超高旋转。"""
    z, _ = profile(station)
    return z - slope_left(station, deck) * (offset - deck_center(deck))


def ground(station, offset=0.0):
    """地面线：以 VALLEY_STATION 为谷底的高斯型山谷，横向带坡。"""
    return (C.GROUND_BASE - C.VALLEY_DEPTH * math.exp(-((station - C.VALLEY_STATION) / C.VALLEY_WIDTH) ** 2)
            + C.GROUND_SIDE_SLOPE * offset)


def station_label(station):
    km = int(station // 1000)
    return "K%d+%07.3f" % (km, station - 1000 * km)
