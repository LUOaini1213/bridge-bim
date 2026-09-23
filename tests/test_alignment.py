"""路线几何：平曲线两种算法互核、圆曲线解析解对数值积分、投影往返、竖曲线要素、超高过渡。
只用标准库（Rhino 8 内置的 CPython 3.9 要能跑同一份 bridge/）。"""
import math
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import alignment as AL, config as C          # noqa: E402
from bridge.model import frame, project, station_on_plane  # noqa: E402


def _grades():
    pv = C.V_PVI
    return [(pv[i + 1][1] - pv[i][1]) / (pv[i + 1][0] - pv[i][0]) for i in range(len(pv) - 1)]


class Horizontal(unittest.TestCase):
    def test_segment_sequence_is_line_clothoid_arc_clothoid_line(self):
        self.assertEqual([s.kind for s in AL.SEGMENTS], ["LINE", "CLOTHOID", "CIRCULARARC", "CLOTHOID", "LINE"])
        self.assertAlmostEqual(AL.LENGTH, sum(L for _, L, _, _ in C.H_SEGMENTS), places=9)

    def test_clothoid_gauss_legendre_matches_series_formula(self):
        """第一段回旋线在自己的切线坐标系里：数值积分与测量上的级数公式一致到 1e-9 m。"""
        seg = AL.SEGMENTS[1]
        A = math.sqrt(C.R_CURVE * C.LS)
        c, s = math.cos(seg.th0), math.sin(seg.th0)
        worst = 0.0
        for l in (5.0, 27.5, 55.0, 82.5, C.LS):
            x, y, _, _ = seg.at(l)
            u, v = (x - seg.x0) * c + (y - seg.y0) * s, -(x - seg.x0) * s + (y - seg.y0) * c
            xs, ys = AL.clothoid_series(A, l)
            worst = max(worst, math.hypot(u - xs, v - ys))
        self.assertLess(worst, 1e-9)

    def test_clothoid_end_offset_is_the_textbook_value(self):
        """回旋线终点的切线支距 y ≈ Ls²/(6R)（级数首项），差值应小于第二项的量级。"""
        seg = AL.SEGMENTS[1]
        x, y, _, _ = seg.at(C.LS)
        c, s = math.cos(seg.th0), math.sin(seg.th0)
        v = -(x - seg.x0) * s + (y - seg.y0) * c
        first = C.LS ** 2 / (6 * C.R_CURVE)
        second = C.LS ** 7 / (336 * (C.R_CURVE * C.LS) ** 3)         # l⁷/(336A⁶)，A² = R·Ls
        self.assertLess(abs(v - first), 1.01 * second)

    def test_circular_arc_closed_form_matches_numerical_integration(self):
        seg = AL.SEGMENTS[2]
        for l in (40.0, 95.0, seg.L):
            x, y, _, _ = seg.at(l)
            dx, dy = AL.integrate(seg.heading, l)
            self.assertLess(math.hypot(x - seg.x0 - dx, y - seg.y0 - dy), 1e-9)

    def test_total_deflection(self):
        """两段回旋线各转 Ls/2R，圆曲线转 Lc/R，最后一段直线方位角 = 起点 + 三者之和。"""
        lc = C.H_SEGMENTS[2][1]
        want = 2 * C.LS / (2 * C.R_CURVE) + lc / C.R_CURVE
        self.assertAlmostEqual(AL.SEGMENTS[4].th0 - AL.SEGMENTS[0].th0, want, places=12)

    def test_offset_point_is_on_the_normal(self):
        for st in (10100.0, 10375.0, 10500.0, 10680.0, 10900.0):
            (x, y), t, n = frame(st)
            for o in (-13.4, 7.0):
                px, py = AL.offset_xy(st, o)
                self.assertAlmostEqual((px - x) * t[0] + (py - y) * t[1], 0.0, places=9)
                self.assertAlmostEqual((px - x) * n[0] + (py - y) * n[1], o, places=9)

    def test_projection_round_trip(self):
        rnd = random.Random(20260923)
        worst = 0.0
        for _ in range(300):
            s, o = rnd.uniform(10250, 10800), rnd.uniform(-25, 25)
            x, y = AL.offset_xy(s, o)
            s2, o2 = project(x, y, s + rnd.uniform(-8, 8))
            worst = max(worst, abs(s2 - s), abs(o2 - o))
        self.assertLess(worst, 1e-8)

    def test_station_on_plane_lands_on_the_plane(self):
        (x, y), t, _ = frame(10470.0)
        for o in (-13.375, 0.0, 13.375):
            for h in (-0.04, 0.04):
                s = station_on_plane(o, (x, y), t, h, 10470.0)
                px, py = AL.offset_xy(s, o)
                self.assertAlmostEqual((px - x) * t[0] + (py - y) * t[1], h, places=10)


class Vertical(unittest.TestCase):
    def test_pvi_tangent_points_and_external(self):
        s_pvi, z_pvi = C.V_PVI[1]
        L = C.V_CURVE_LENGTH[1]
        g1, g2 = _grades()
        z_mid, g_mid = AL.profile(s_pvi)
        self.assertAlmostEqual(z_pvi - z_mid, (g1 - g2) * L / 8, places=9)          # 外距 E = ΔiL/8
        self.assertAlmostEqual(g_mid, (g1 + g2) / 2, places=12)
        for sgn, g in ((-1, g1), (1, g2)):
            z, gr = AL.profile(s_pvi + sgn * L / 2)
            self.assertAlmostEqual(z, z_pvi + sgn * g * L / 2, places=9)          # 切点在两条坡线上
            self.assertAlmostEqual(gr, g, places=12)

    def test_grade_changes_linearly_inside_the_curve(self):
        s_pvi, _ = C.V_PVI[1]
        L = C.V_CURVE_LENGTH[1]
        g1, g2 = _grades()
        for f in (0.1, 0.35, 0.8):
            _, g = AL.profile(s_pvi - L / 2 + f * L)
            self.assertAlmostEqual(g, g1 + (g2 - g1) * f, places=12)


class CrossSlope(unittest.TestCase):
    def test_tangent_crown_and_full_superelevation(self):
        self.assertAlmostEqual(AL.slope_left(10100.0, "L"), C.CROSSFALL)          # 直线段：各幅向外侧排水
        self.assertAlmostEqual(AL.slope_left(10100.0, "R"), -C.CROSSFALL)
        for d in ("L", "R"):
            self.assertAlmostEqual(AL.slope_left(10500.0, d), C.SUPERELEVATION)   # 圆曲线：全超高向内侧

    def test_runoff_is_linear_over_the_clothoid(self):
        s0 = C.START_STATION + C.H_SEGMENTS[0][1]
        for f in (0.25, 0.5, 0.75):
            want = -C.CROSSFALL + (C.SUPERELEVATION + C.CROSSFALL) * f
            self.assertAlmostEqual(AL.slope_left(s0 + f * C.LS, "R"), want, places=12)

    def test_deck_rotates_about_its_own_centre(self):
        for st in (10380.0, 10500.0):
            for d, c in C.DECKS:
                self.assertAlmostEqual(AL.deck_top(st, c, d), AL.profile(st)[0], places=12)


if __name__ == "__main__":
    unittest.main()
