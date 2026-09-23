"""构件生成与检查。只用标准库。

- 数量、几何（闭合、体积、截面积 × 长度）、盖梁顶面随横坡、耳切剖分；
- 梁长归并：区间刺穿与暴力枚举对照、最优性证书、每片梁落在自己的可行区间；
- 每条检查都先做反例：把模型故意弄坏一处，那条检查必须变红——
  「全过」本身不是证据，一条从来不会失败的检查什么也没验证。
"""
import math
import os
import random
import sys
import unittest
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import alignment as AL, checks as K, config as C, model as M, pipeline as P, yard as Y  # noqa: E402

EXPECTED_COUNTS = {
    "girder": 120, "diaphragm": 480, "wet_joint": 96, "cantilever": 48, "continuity": 18, "pavement": 24, "barrier": 48,
    "expansion_joint": 8, "seat": 150, "bearing": 150, "temp_support": 180, "cap": 22, "abut_cap": 4,
    "backwall": 4, "column": 44, "tie": 22, "pile": 52,
}


def fresh():
    els, sup = M.build()
    return els, {e.eid: e for e in els}


def verdict(check, els):
    return check(els)[1]


def shift(points, v):
    for i, p in enumerate(points):
        points[i] = (p[0] + v[0], p[1] + v[1], p[2] + (v[2] if len(v) > 2 else 0.0))


class Counts(unittest.TestCase):
    def test_element_counts_by_class(self):
        els, _ = fresh()
        got = {}
        for e in els:
            got[e.cls] = got.get(e.cls, 0) + 1
        self.assertEqual(got, EXPECTED_COUNTS)

    def test_ids_are_unique(self):
        els, _ = fresh()
        self.assertEqual(len({e.eid for e in els}), len(els))

    def test_support_kinds_follow_the_units(self):
        kinds = "".join(M.support_kind(k) for k in range(C.N_SPANS + 1))
        self.assertEqual(kinds, "ACCCTCCCTCCCA")


class Geometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.els, cls.by = fresh()

    def test_every_solid_is_closed_and_outward(self):
        n = 0
        for e in self.els:
            if e.shape == "mesh":
                for s in e.params["solids"]:
                    n += 1
                    self.assertTrue(M.solid_closed(s), e.eid)
                    self.assertGreater(M.solid_volume(s), 0, e.eid)
        self.assertGreater(n, 800)

    def test_girder_volume_is_section_area_times_plan_length(self):
        """竖直截面沿斜轴扫出、两端斜切：体积 = 截面积 × 梁轴平面长度（截面左右对称，斜切多出的与少掉的抵消）。"""
        A = M.section_area()
        worst = max(abs(g.volume - A * g.attrs["plan_length"]) / g.volume for g in self.els if g.cls == "girder")
        self.assertLess(worst, 1e-9)

    def test_girder_length_equals_its_spec(self):
        for g in (e for e in self.els if e.cls == "girder"):
            p0, p1 = g.params["p0"], g.params["p1"]
            self.assertAlmostEqual(math.dist(p0, p1) if hasattr(math, "dist") else
                                   math.sqrt(sum((p1[i] - p0[i]) ** 2 for i in range(3))), g.attrs["length"], places=9)

    def test_cap_top_follows_the_cross_slope(self):
        for cap in (e for e in self.els if e.cls in ("cap", "abut_cap")):
            f = cap.params["frame"]
            top = cap.params["solids"][0]["v"][4:]
            for p in top:
                a = (p[0] - f["o"][0]) * f["n"][0] + (p[1] - f["o"][1]) * f["n"][1]
                self.assertAlmostEqual(p[2], f["z_c"] - f["slope"] * a, places=9)
            self.assertAlmostEqual(f["slope"], AL.slope_left(M.support_stations()[cap.attrs["support"]], cap.deck))

    def test_continuity_bearing_sits_on_the_pier_line(self):
        for b in (e for e in self.els if e.cls == "bearing" and e.attrs["kind"] == "cont"):
            (x, y), t, _ = M.frame(M.support_stations()[b.attrs["support"]])
            c = b.params["c"]
            self.assertAlmostEqual((c[0] - x) * t[0] + (c[1] - y) * t[1], 0.0, places=9)

    def test_triangulation_keeps_the_volume_and_leaves_only_tris_and_quads(self):
        for e in self.els:
            if e.cls not in ("girder", "continuity", "pavement"):
                continue
            for s in e.params["solids"]:
                t = M.triangulate(s)
                self.assertTrue(all(len(f) in (3, 4) for f in t["f"]))
                self.assertAlmostEqual(M.solid_volume(t), M.solid_volume(s), places=9)
                self.assertTrue(M.solid_closed(t))

    def test_deck_edge_follows_the_curve(self):
        """翼缘现浇段外缘沿路线取样，每个样点都在桥面边缘偏距上（直梁以直代曲，曲线由现浇段吸收）。"""
        e = self.by["CT-R07-1"]
        v = e.params["solids"][0]["v"]
        c = dict(C.DECKS)["R"]
        for i in range(0, len(v), 4):
            s, o = M.project(v[i][0], v[i][1], C.BRIDGE_START + 6.5 * C.SPAN)
            self.assertAlmostEqual(o, c - C.DECK_WIDTH / 2, places=6)


class Equalization(unittest.TestCase):
    def test_greedy_stabbing_matches_brute_force(self):
        """随机小算例：贪心的点数等于枚举出的最少点数（候选点取各区间右端点就够）。"""
        rnd = random.Random(1213)
        for _ in range(300):
            n = rnd.randint(1, 7)
            iv = {}
            for i in range(n):
                lo = rnd.randint(0, 30)
                iv[i] = (lo, lo + rnd.randint(0, 8))
            pts, assign, wit = M.stab(iv)
            cands = sorted({hi for _, hi in iv.values()})
            best = next(k for k in range(1, n + 1)
                        for combo in combinations(cands, k)
                        if all(any(lo <= p <= hi for p in combo) for lo, hi in iv.values()))
            self.assertEqual(len(pts), best)
            self.assertTrue(all(iv[k][0] <= assign[k] <= iv[k][1] for k in iv))
            wiv = sorted(iv[w] for w in wit)
            self.assertTrue(all(a[1] < b[0] for a, b in zip(wiv, wiv[1:])))
            self.assertEqual(len(wit), len(pts))

    def test_spec_counts(self):
        fams, _ = M.equalize()
        self.assertEqual({f: len(v[0]) for f, v in fams.items()}, {"中跨": 4, "边跨": 6})
        self.assertEqual(len(M.naive_lengths()), 66)

    def test_every_girder_sits_in_its_own_interval(self):
        chs = M.chords()
        _, assign = M.equalize(chs)
        for key, ch in chs.items():
            lo, hi = M.length_interval(ch)
            self.assertLessEqual(lo * C.SPEC_STEP - 1e-9, assign[key])
            self.assertLessEqual(assign[key], hi * C.SPEC_STEP + 1e-9)

    def test_half_gaps_stay_in_range(self):
        els, _ = fresh()
        for g in (e for e in els if e.cls == "girder"):
            for end in ("a", "b"):
                h = g.attrs["half_" + end]
                if g.attrs["end_" + end] == "E":
                    self.assertAlmostEqual(h, C.EXP_HALF, places=12)
                else:
                    self.assertGreaterEqual(h, C.CONT_HALF_MIN - 1e-9)
                    self.assertLessEqual(h, C.CONT_HALF_MAX + 1e-9)

    def test_more_tolerance_never_needs_more_specs(self):
        rows = P.spec_sensitivity_rows()
        totals = [r["total"] for r in rows]
        self.assertEqual(totals, sorted(totals, reverse=True))
        chosen = next(r for r in rows if r["tolerance_m"] == "%.3f" % ((C.CONT_HALF_MAX - C.CONT_HALF_MIN) / 2))
        self.assertEqual(chosen["total"], 10)


class Checks(unittest.TestCase):
    def test_all_checks_pass_on_the_model(self):
        els, _ = fresh()
        failed = [n for n, ok, _ in K.run_all(els) if not ok]
        self.assertEqual(failed, [])
        self.assertEqual(len(K.ALL), 17)


class CheckCounterexamples(unittest.TestCase):
    """每条检查一个反例：弄坏一处，断言这一条变红。"""

    def assertTurnsRed(self, check, els):
        self.assertTrue(verdict(check, fresh()[0]), "反例前提：原模型这条应当通过")
        self.assertFalse(verdict(check, els), "弄坏之后 %s 仍然通过——这条检查没有牙齿" % check.__name__)

    def test_alignment_joint_break(self):
        seg = AL.SEGMENTS[2]
        x0 = seg.x0
        try:
            seg.x0 += 0.01
            self.assertFalse(verdict(K.check_alignment_continuity, []))
        finally:
            seg.x0 = x0
        self.assertTrue(verdict(K.check_alignment_continuity, []))

    def test_profile_jump(self):
        orig = AL.profile
        s_pvi, _ = C.V_PVI[1]
        L = C.V_CURVE_LENGTH[1]
        try:
            AL.profile = lambda st: (orig(st)[0] + (0.01 if abs(st - s_pvi) < L / 2 else 0.0), orig(st)[1])
            self.assertFalse(verdict(K.check_profile_continuity, []))
        finally:
            AL.profile = orig

    def test_span_off_by_5_cm(self):
        orig = K.support_stations
        try:
            K.support_stations = lambda: [s + (0.05 if i == 6 else 0.0) for i, s in enumerate(orig())]
            self.assertFalse(verdict(K.check_spans, []))
        finally:
            K.support_stations = orig

    def test_expansion_end_moved_1_cm(self):
        els, by = fresh()
        g = by["G-L01-1"]
        n = len(C.T_SECTION)
        v = g.params["solids"][0]["v"]
        ring = v[:n]
        shift(ring, (0.01 * g.params["ta"][0], 0.01 * g.params["ta"][1]))
        v[:n] = ring
        self.assertTurnsRed(K.check_expansion_ends, els)

    def test_continuity_joint_squeezed(self):
        els, by = fresh()
        g = by["G-L01-1"]
        n = len(C.T_SECTION)
        v = g.params["solids"][0]["v"]
        ring = v[n:]
        shift(ring, (0.2 * g.params["tb"][0], 0.2 * g.params["tb"][1]))   # 梁端朝墩中心线伸 0.2 m
        v[n:] = ring
        self.assertTurnsRed(K.check_continuity_joints, els)

    def test_wet_joint_too_narrow(self):
        els, by = fresh()
        by["WJ-R05-2"].attrs["width_a"] = 0.40
        self.assertTurnsRed(K.check_wet_joints, els)

    def test_seat_too_high(self):
        els, by = fresh()
        by["S-P03-L2"].attrs["height"] = 0.45
        self.assertTurnsRed(K.check_seats, els)

    def test_temp_support_off_the_cap(self):
        els, by = fresh()
        ts = by["TS-R02-1b"]                      # 在 P02 墩中心线前约 0.8 m；再往跨内挪 1 m 就出了 2.4 m 宽的盖梁
        _, t, n = M.frame(M.support_stations()[2])
        ring = ts.params["solids"][0]["v"]
        shift(ring, (-1.0 * t[0], -1.0 * t[1]))
        self.assertTurnsRed(K.check_supports_on_caps, els)

    def test_temp_support_too_short(self):
        els, by = fresh()
        by["TS-L06-3a"].attrs["height"] = 0.05     # 第 6 跨起点在连续墩 P05 上
        self.assertTurnsRed(K.check_temp_heights, els)

    def test_temp_support_pushed_onto_the_seat(self):
        els, by = fresh()
        ts = by["TS-L01-3b"]                      # P01 前一跨梁端下，推向墩中心线 0.12 m
        _, t, _ = M.frame(M.support_stations()[1])
        shift(ts.params["solids"][0]["v"], (0.12 * t[0], 0.12 * t[1]))
        self.assertTurnsRed(K.check_temp_clear_of_seats, els)

    def test_permanent_bearing_slid_under_a_girder(self):
        els, by = fresh()
        b = by["B-P09-R1"]
        _, t, _ = M.frame(M.support_stations()[9])
        c = b.params["c"]
        b.params["c"] = (c[0] + 0.1 * t[0], c[1] + 0.1 * t[1], c[2])
        self.assertTurnsRed(K.check_perm_bearing_under_joint, els)

    def test_cap_buried(self):
        els, by = fresh()
        by["CAP-P06-R"].attrs["bottom"] -= 20.0
        self.assertTurnsRed(K.check_pier_caps_above_ground, els)

    def test_clearance_requirement_raised(self):
        els, _ = fresh()
        old = C.ROAD_CLEARANCE
        try:
            C.ROAD_CLEARANCE = 14.0
            self.assertFalse(verdict(K.check_clearance, els))
        finally:
            C.ROAD_CLEARANCE = old

    def test_column_moved_onto_the_road(self):
        els, by = fresh()
        col = by["C-P06-L1"]
        (x, y), t, _ = M.frame(C.ROAD_STATION)
        col.params["c"] = (x, y, col.params["c"][2])
        self.assertTurnsRed(K.check_columns_clear_of_road, els)

    def test_erector_too_small(self):
        els, _ = fresh()
        old = C.ERECTOR_SWL_T
        try:
            C.ERECTOR_SWL_T = 60.0
            self.assertFalse(verdict(K.check_erector, els))
        finally:
            C.ERECTOR_SWL_T = old

    def test_flipped_face(self):
        els, by = fresh()
        f = by["G-R11-3"].params["solids"][0]["f"]
        f[0] = tuple(reversed(f[0]))
        self.assertTurnsRed(K.check_solids, els)

    def test_girder_cut_5_cm_long(self):
        els, by = fresh()
        g = by["G-L06-3"]
        p0, p1 = g.params["p0"], g.params["p1"]
        L = math.sqrt(sum((p1[i] - p0[i]) ** 2 for i in range(3)))
        g.params["p1"] = tuple(p1[i] + (p1[i] - p0[i]) / L * 0.05 for i in range(3))
        self.assertTurnsRed(K.check_length_specs, els)


class YardCheckCounterexamples(unittest.TestCase):
    def _run(self):
        r = P.compute()
        heaviest = max(e.volume for e in r["els"] if e.cls == "girder") * C.RC_DENSITY_T
        return r, [ok for _, ok, _ in Y.run_checks(r["summary"], heaviest)]

    def test_all_pass(self):
        self.assertEqual(self._run()[1], [True] * 5)

    def _flip(self, attr, value, idx):
        old = getattr(C, attr)
        try:
            setattr(C, attr, value)
            self.assertFalse(self._run()[1][idx], "%s=%s 之后第 %d 条梁场检查仍然通过" % (attr, value, idx + 1))
        finally:
            setattr(C, attr, old)

    def test_storage_too_small(self):
        self._flip("STORAGE_POSITIONS", 15, 0)

    def test_storage_limit_too_short(self):
        self._flip("MAX_STORAGE_DAYS", 10, 1)

    def test_age_rule(self):
        r = P.compute()
        heaviest = max(e.volume for e in r["els"] if e.cls == "girder") * C.RC_DENSITY_T
        sm = dict(r["summary"], age_min=C.MIN_AGE_DAYS - 1)
        self.assertFalse(Y.run_checks(sm, heaviest)[2][1])

    def test_gantry_too_weak(self):
        self._flip("GANTRY_SWL_T", 30.0, 3)

    def test_yard_on_the_road(self):
        self._flip("MIN_YARD_TO_ROAD", 60.0, 4)


if __name__ == "__main__":
    unittest.main()
