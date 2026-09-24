"""上部结构计算：闭式解、恒等式、与 OpenSees 互核，以及每条结构检查的反例。

标准库部分在 Python 3.9 上跑（CI core-py39）；OpenSees 互核只在装了 openseespy 时跑。
每条结构检查都先证明它会失败——把模型或参数改坏，检查必须变红——再拿它的「通过」当证据。
"""
import math
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import config as C, model as M, schedule as SC, structure as ST  # noqa: E402

try:
    import openseespy.opensees as ops
except ImportError:                      # core-py39 不装第三方库
    if os.environ.get("REQUIRE_OPENSEES"):
        raise                            # CI 的全量任务装了它：加载失败就该红，不能静默跳过互核
    ops = None

EI = C.E_C50 * 0.45


def rel(a, b):
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


class Close(unittest.TestCase):
    def close(self, a, b, tol=1e-9, scale=None):
        """相对误差 ≤ tol（scale 给定时相对 scale）：带状分解的舍入在 1e-11 量级，闭式解比对留到 1e-9。"""
        s = scale if scale is not None else max(abs(a), abs(b), 1e-300)
        self.assertLessEqual(abs(a - b), tol * s, "%r vs %r" % (a, b))


def grid(L, n):
    return [L * j / n for j in range(n + 1)]


def trapezoid(b1, b2, y1, y2):
    """上底 b1（在 y1）、下底 b2（在 y2）的梯形：(面积, 形心 y, 对自身形心的惯性矩)。"""
    h = abs(y1 - y2)
    a = (b1 + b2) / 2 * h
    d = h * (b1 + 2 * b2) / (3 * (b1 + b2))           # 形心距 b1 边
    i = h ** 3 * (b1 * b1 + 4 * b1 * b2 + b2 * b2) / (36 * (b1 + b2))
    return a, y1 - d if y2 < y1 else y1 + d, i


def rect(b, y1, y2):
    h = abs(y1 - y2)
    return b * h, (y1 + y2) / 2, b * h ** 3 / 12


def combine(parts):
    A = sum(p[0] for p in parts)
    yc = sum(p[0] * p[1] for p in parts) / A
    return A, yc, sum(p[2] + p[0] * (p[1] - yc) ** 2 for p in parts)


# ====================================================================== 截面
class Sections(unittest.TestCase):
    PARTS = [rect(1.85, 0.0, -0.16), trapezoid(1.85, 0.20, -0.16, -0.25), rect(0.20, -0.25, -1.60),
             trapezoid(0.20, 0.50, -1.60, -1.75), rect(0.50, -1.75, -2.00)]

    def test_precast_section_equals_a_hand_decomposition(self):
        """翼缘板、承托、腹板、马蹄过渡段、马蹄五块手算，平行移轴合成。"""
        A, yc, I = combine(self.PARTS)
        a, y, i = ST.polygon_props(C.T_SECTION)
        self.assertAlmostEqual(a, A, places=12)
        self.assertAlmostEqual(y, yc, places=12)
        self.assertAlmostEqual(i, I, places=12)
        self.assertAlmostEqual(A, 0.83575, places=12)

    def test_composite_section_adds_the_wet_joint_strips(self):
        for k, s in ST.girder_sections().items():
            parts = self.PARTS + [rect(s["left"] - 0.925, 0.0, -0.16), rect(s["right"] - 0.925, 0.0, -0.16)]
            A, yc, I = combine(parts)
            self.assertAlmostEqual(s["A"], A, places=12)
            self.assertAlmostEqual(s["yc"], yc, places=12)
            self.assertAlmostEqual(s["I"], I, places=12)

    def test_joint_section_is_the_solid_block(self):
        secs = ST.girder_sections()
        s = secs[3]
        self.assertAlmostEqual(s["Ij"], (s["left"] + s["right"]) * C.H_GIRDER ** 3 / 12, places=12)
        e = secs[1]                                            # 1 号梁外侧只有桥面板厚
        A, yc, I = combine([rect(e["left"] + 0.925, 0.0, -2.0), rect(e["right"] - 0.925, 0.0, -0.16)])
        self.assertAlmostEqual(e["Aj"], A, places=12)
        self.assertAlmostEqual(e["Ij"], I, places=12)

    def test_rectangle_torsion_coefficient_matches_saint_venant(self):
        """c·b·t³ 的系数 c 对照 Saint-Venant 精确解（b/t = 1、2、4、10 时 0.141、0.229、0.281、0.312）。"""
        for bt, exact in ((1, 0.1406), (2, 0.2287), (4, 0.2808), (10, 0.3123)):
            self.assertAlmostEqual(ST._c_torsion(bt, 1.0), exact, delta=0.0015)


# ====================================================================== 横向分布
class Distribution(Close):
    @classmethod
    def setUpClass(cls):
        cls.secs, cls.beta, cls.coef, cls.info = ST.distribution()

    def test_each_unit_load_is_shared_out_completely(self):
        for e in [x / 7.0 for x in range(-44, 45)]:           # 含悬臂段
            for beta in (self.beta, 1.0):
                self.assertAlmostEqual(sum(ST.eccentric_eta(self.secs, beta, k, e) for k in self.secs), 1.0, places=12)
            self.assertAlmostEqual(sum(ST.lever_eta(self.secs, k, e) for k in self.secs), 1.0, places=12)

    def test_mirror_symmetry(self):
        for e in (-5.1, -2.0, 0.3, 4.4):
            for k in self.secs:
                self.assertAlmostEqual(ST.eccentric_eta(self.secs, self.beta, k, e),
                                       ST.eccentric_eta(self.secs, self.beta, 6 - k, -e), places=12)
                self.assertAlmostEqual(ST.lever_eta(self.secs, k, e), ST.lever_eta(self.secs, 6 - k, -e), places=12)
        for key, v in self.coef[1].items():
            if isinstance(v, str):
                self.assertEqual(v, self.coef[5][key])
            else:
                self.close(v, self.coef[5][key], 1e-11, 1.0)

    def test_edge_girder_by_hand(self):
        """1 号梁（右边梁）：两列车贴右路缘，车轮在 −5.375、−3.575、−2.275、−0.475 m；
        η = I₁/ΣI + β·e·a₁·I₁/Σa²I，mc = 1.0 × ½ Σ η。杠杆原理：一列车，1.2 × ½ × (1.194 + 0.459)。"""
        s = self.secs
        sum_i = sum(x["I"] for x in s.values())
        sum_ai2 = sum(x["a"] ** 2 * x["I"] for x in s.values())
        wheels = [-5.375, -3.575, -2.275, -0.475]
        mc = 1.0 * 0.5 * sum(s[1]["I"] / sum_i + self.beta * e * s[1]["a"] * s[1]["I"] / sum_ai2 for e in wheels)
        self.assertAlmostEqual(self.coef[1]["ecc_max"], mc, places=12)
        self.assertEqual(self.coef[1]["mc_max"], self.coef[1]["ecc_max"])      # 边梁由修正偏心压力法控制
        self.assertEqual(self.coef[1]["mc_max_lanes"], 2)
        m0 = 1.2 * 0.5 * ((1 + 0.475 / 2.45) + (1 - 1.325 / 2.45))
        self.assertAlmostEqual(self.coef[1]["m0_max"], m0, places=12)
        beta = 1 / (1 + 0.4 * C.SPAN ** 2 * sum(x["IT"] for x in s.values()) / (12 * sum_ai2))
        self.assertAlmostEqual(self.beta, beta, places=15)

    def test_enumerated_layouts_beat_a_millimetre_scan(self):
        """最优布置只可能在「某个车轮落在梁位上」或「贴路缘」处：逐毫米平移车队扫一遍，找不到更大的。"""
        half = C.DECK_WIDTH / 2 - C.BARRIER_W
        for k in self.secs:
            for key, fn in (("ecc", lambda e, k=k: ST.eccentric_eta(self.secs, self.beta, k, e)),
                            ("m0", lambda e, k=k: ST.lever_eta(self.secs, k, e))):
                best_hi, best_lo = 0.0, 0.0
                for n in range(1, self.info["lanes"] + 1):
                    rel_ = [0.0, 1.8]
                    for v in range(1, n):
                        rel_ += [v * 3.1, v * 3.1 + 1.8]
                    lo, hi = -half + 0.5, half - 0.5 - rel_[-1]
                    for j in range(int(round((hi - lo) * 1000)) + 1):
                        m = C.LANE_FACTORS[n] * 0.5 * sum(fn(lo + j / 1000 + r) for r in rel_)
                        best_hi, best_lo = max(best_hi, m), min(best_lo, m)
                self.assertLessEqual(best_hi, self.coef[k][key + "_max"] + 1e-12)
                self.assertGreater(best_hi, self.coef[k][key + "_max"] - 1e-3)
                self.assertGreaterEqual(best_lo, self.coef[k][key + "_min"] - 1e-12)
                self.assertLess(best_lo, self.coef[k][key + "_min"] + 1e-3)


# ====================================================================== 横向分布：G-M 法
class GMMethod(Close):
    """比拟正交异性板的横向挠曲：闭式解、恒等式、刚性极限，以及教材例 2-4-6。"""

    def test_hetenyi_closed_form(self):
        """α = 0 时方程就是弹性地基上的自由梁 w'''' + k w = δ（EI = 1，梁长 2）：跨中集中力下的跨中挠度
        w = (λ / 2k)·(ch 2λ + cos 2λ + 2) / (sh 2λ + sin 2λ)，λ = (k/4)^¼（Hetényi）。"""
        for theta in (0.2, 0.324, 0.5, 0.9):
            k = (math.pi * theta) ** 4
            lam = (k / 4) ** 0.25
            w = lam / (2 * k) * (math.cosh(2 * lam) + math.cos(2 * lam) + 2) / (math.sinh(2 * lam) + math.sin(2 * lam))
            self.close(ST.gm_influence(theta, 0.0, 0.0)(0.0), 2 * k * w, 1e-10)

    def test_width_average_is_one_and_reciprocity_holds(self):
        for theta, alpha in ((0.3, 0.0), (0.45, 0.3), (0.6, 1.0)):
            for eta_k in (-0.8, 0.13, 1.0):
                K = ST.gm_influence(theta, alpha, eta_k)
                n = 400                                       # Simpson
                s = sum((1 if j in (0, n) else 4 if j % 2 else 2) * K(-1 + 2 * j / n) for j in range(n + 1)) * (2 / n) / 3
                self.close(s / 2, 1.0, 1e-6)
                for e in (-0.95, -0.3, 0.55):
                    self.close(K(e), ST.gm_influence(theta, alpha, e)(eta_k), 1e-10)

    def test_rigid_limit(self):
        """横向无限刚（θ → 0）、不计抗扭：K → 1 + 3ηe——刚性横梁法（偏心压力法）对连续分布主梁的结果。"""
        for eta_k, e in ((0.8, 0.8), (-0.5, 0.9), (0.0, 0.4)):
            self.assertAlmostEqual(ST.gm_influence(0.02, 0.0, eta_k)(e), 1 + 3 * eta_k * e, delta=2e-4)

    def test_textbook_example_2_4_6(self):
        """教材例 2-4-6（五梁式 T 梁，l = 19.5 m，梁距 1.6 m，横隔梁间距 4.85 m）：由原始尺寸算出 θ = 0.324、α = 0.02334；
        三片梁的 mc 为 0.505 / 0.456 / 0.409（教材查 G-M 图表所得），这里直接解方程得到的差 ≤ 1.1%。
        教材表里个别 K 值（查图读数）与方程解差到 0.13，但落到 mc 上相互抵消，只差 1% 左右。"""
        p = ST.gm_plate(6.628e10, 2.089e9, 1600.0, 110.0, 4850.0, 150.0, 1000.0, 6400.0, 4000.0, 19500.0)
        self.assertAlmostEqual(p["theta"], 0.324, delta=0.0005)
        self.assertAlmostEqual(p["alpha"], 0.02334, delta=0.00005)
        sa = 0.1528                                           # 教材取 √α = 0.1528
        th = 0.324
        ils = {y: (ST.gm_influence(th, 0.0, y / 4000.0), ST.gm_influence(th, 1.0, y / 4000.0)) for y in (3200.0, 1600.0, 0.0)}

        def eta(y, e):
            k0, k1 = ils[y][0](e / 4000.0), ils[y][1](e / 4000.0)
            return (k0 + (k1 - k0) * sa) / 5
        wheels = [3000.0, 1200.0, -100.0, -1900.0]           # 教材图 2-4-46：两列车靠 1 号梁一侧
        self.assertAlmostEqual(0.5 * sum(eta(3200.0, e) for e in wheels), 0.505, delta=0.505 * 0.011)
        self.assertAlmostEqual(0.5 * sum(eta(1600.0, e) for e in wheels), 0.456, delta=0.456 * 0.011)
        best = ST.smooth_extremes(lambda e: eta(0.0, e * 1000.0), ST.wheel_offsets(2), -3.0, 3.0 - 4.9)[0]
        self.assertAlmostEqual(best / 2 * 1.0, 0.409, delta=0.409 * 0.011)

    def test_optimiser_beats_a_millimetre_scan(self):
        """G-M 影响线是光滑曲线：逐毫米平移车队扫一遍，找到的极值不比 smooth_extremes 的更好（差 < 1e-9）。"""
        secs, _, coef, info = ST.distribution(cw=1.9)
        gm = info["gm"]
        half = C.DECK_WIDTH / 2 - C.BARRIER_W
        for k in (1, 3):
            il0 = ST.gm_influence(gm["theta"], 0.0, secs[k]["a"] / gm["B"])
            il1 = ST.gm_influence(gm["theta"], 1.0, secs[k]["a"] / gm["B"])
            sa = math.sqrt(gm["alpha"])

            def fn(e):
                a0, a1 = il0(e / gm["B"]), il1(e / gm["B"])
                return (a0 + (a1 - a0) * sa) / 5
            best_hi, best_lo = 0.0, 0.0
            for n in range(1, info["lanes"] + 1):
                rel_ = ST.wheel_offsets(n)
                lo, hi = -half + 0.5, half - 0.5 - rel_[-1]
                for j in range(int(round((hi - lo) * 1000)) + 1):
                    m = C.LANE_FACTORS[n] * 0.5 * sum(fn(lo + j / 1000 + r) for r in rel_)
                    best_hi, best_lo = max(best_hi, m), min(best_lo, m)
            self.assertLessEqual(best_hi, coef[k]["gm_max"] + 1e-9)
            self.assertGreater(best_hi, coef[k]["gm_max"] - 1e-6)
            self.assertGreaterEqual(best_lo, coef[k]["gm_min"] - 1e-9)

    def test_envelope_takes_the_larger_share(self):
        secs, _, coef, info = ST.distribution(cw=1.9)
        self.assertGreater(info["gm"]["theta"], ST.THETA_NARROW)
        for c in coef.values():
            self.assertEqual(c["mc_max"], max(c["ecc_max"], c["gm_max"]))
            self.assertEqual(c["mc_min"], min(c["ecc_min"], c["gm_min"]))
        self.assertGreater(coef[3]["gm_max"], coef[3]["ecc_max"])                  # 中梁：偏心压力法偏小
        self.assertLess(coef[1]["gm_max"], coef[1]["ecc_max"])                     # 边梁：偏心压力法偏大
        with mock.patch.object(ST, "GM_ENVELOPE", False):
            off = ST.distribution(cw=1.9)[2]
        for c in off.values():
            self.assertEqual(c["mc_max"], c["ecc_max"])

    def test_plate_parameters_come_from_the_bim_dimensions(self):
        """θ' 与 α' 按等代简支梁修正：θ' = ⁴√C_w·θ，α' = α / √C_w（教材式 2-6-8、2-6-9）。"""
        secs = ST.girder_sections()
        a, b = ST.gm_parameters(secs, C.SPAN, 1.0), ST.gm_parameters(secs, C.SPAN, 1.909)
        self.close(b["theta"], a["theta"] * 1.909 ** 0.25, 1e-12)
        self.close(b["alpha"], a["alpha"] / math.sqrt(1.909), 1e-12)
        self.assertAlmostEqual(a["B"], len(C.GIRDER_OFFSETS) * 2.45 / 2, places=12)
        self.close(a["Jy"], a["Iy"] / (C.SPAN / 4), 1e-12)                            # 中横隔板间距 = 1/4 跨


# ====================================================================== 杆系求解器：闭式解
class BeamClosedForm(Close):
    def test_simple_span_udl(self):
        L, q = 30.0, 10.0
        b = ST.Beam(grid(L, 40), EI, [0, 40])
        udl = [q] * 40
        u = b.solve(b.load_vector(udl))
        M, V, R = b.results(u, udl)
        self.close(M[20], q * L * L / 8)
        self.close(-u[40], 5 * q * L ** 4 / (384 * EI))
        self.close(R[0], q * L / 2)
        self.close(V[0][0], q * L / 2)

    def test_point_load_inside_an_element(self):
        """集中力不在节点上（一致荷载）：节点弯矩、反力与节点挠度都等于闭式解。"""
        L, P, a = 20.0, 100.0, 7.3                      # 7.3 落在第 14 个单元内
        xs = grid(L, 40)
        b = ST.Beam(xs, EI, [0, 40])
        inner = [(a, P)]
        u = b.solve(b.load_vector(None, None, inner))
        M, V, R = b.results(u, None, None, inner)
        bb = L - a
        self.close(R[0], P * bb / L)
        self.close(R[40], P * a / L)
        w_max = max(abs(v) for v in u[0::2])
        for j, x in enumerate(xs):
            m = P * bb * x / L if x <= a else P * a * (L - x) / L
            self.close(M[j], m, scale=P * a * bb / L)
            w = (P * bb * x * (L * L - bb * bb - x * x) / (6 * L * EI) if x <= a else
                 P * a * (L - x) * (L * L - a * a - (L - x) ** 2) / (6 * L * EI))
            self.close(-u[2 * j], w, scale=w_max)

    def test_overhang(self):
        """两端外伸 a 的简支梁：支点弯矩 −q a²/2，跨中 q l²/8 − q a²/2。"""
        a, l, q = 0.35, 28.0, 25.0
        xs = [0.0, a / 2, a] + [a + l * j / 40 for j in range(1, 41)] + [a + l + a / 2, a + l + a]
        b = ST.Beam(xs, EI, [2, 42])
        udl = [q] * (len(xs) - 1)
        M, V, R = b.results(b.solve(b.load_vector(udl)), udl)
        self.close(M[2], -q * a * a / 2, scale=q * l * l / 8)
        self.close(M[22], q * l * l / 8 - q * a * a / 2)
        self.close(R[2] + R[42], q * (l + 2 * a))

    def test_continuous_beams(self):
        q, L = 10.0, 30.0
        b = ST.Beam(grid(2 * L, 60), EI, [0, 30, 60])
        udl = [q] * 60
        M, V, R = b.results(b.solve(b.load_vector(udl)), udl)
        self.close(M[30], -q * L * L / 8)
        self.close(R[30], 10 / 8 * q * L)
        b = ST.Beam(grid(4 * L, 80), EI, [0, 20, 40, 60, 80])
        udl = [q] * 80
        M, V, R = b.results(b.solve(b.load_vector(udl)), udl)
        self.close(M[20], -3 / 28 * q * L * L)
        self.close(M[40], -2 / 28 * q * L * L)
        for s, c in zip((0, 20, 40, 60, 80), (11, 32, 26, 32, 11)):
            self.close(R[s], c / 28 * q * L)

    def test_frequencies(self):
        L, m = 30.0, 4.0
        b = ST.Beam(grid(L, 60), EI, [0, 60], mass=m)
        f = b.frequencies(2)
        for n, fn in enumerate(f, 1):
            self.assertLess(rel(fn, n * n * math.pi / (2 * L * L) * math.sqrt(EI / m)), 1e-6)
        b = ST.Beam(grid(2 * L, 120), EI, [0, 60, 120], mass=m)
        f1, f2 = b.frequencies(2)
        lam = 3.9266023120479                            # tan λ = tanh λ 的第一个根（固结-铰支）
        self.assertLess(rel(f2 / f1, (lam / math.pi) ** 2), 1e-6)

    @staticmethod
    def synthetic_line(n_spans, per=60, L=30.0):
        xs = grid(L * n_spans, per * n_spans)
        sup = [per * k for k in range(n_spans + 1)]
        return {"xs": xs, "EI2": [EI] * (len(xs) - 1), "perm": [{"node": s} for s in sup],
                "spans": [(xs[a], xs[b]) for a, b in zip(sup, sup[1:])], "span_nodes": list(zip(sup, sup[1:])),
                "sec": {"I": EI / C.E_C50}}

    def test_equivalent_simple_beam_matches_the_textbook_table(self):
        """等代简支梁刚度修正系数 C_w（教材表 2-6-4）：两跨等跨 1.391；三跨等跨边跨 1.429、中跨 1.818；
        教材例 2-6-1（4×30 m 先简支后连续）取边跨 1.432、中跨 1.860。"""
        c2 = ST.span_stiffness_ratios(self.synthetic_line(2))
        c3 = ST.span_stiffness_ratios(self.synthetic_line(3))
        c4 = ST.span_stiffness_ratios(self.synthetic_line(4))
        for got, want in ((c2[0], 1.391), (c3[0], 1.429), (c3[1], 1.818), (c4[0], 1.432), (c4[1], 1.860)):
            self.assertAlmostEqual(got, want, delta=0.0015)
        for c in (c2, c3, c4):
            self.close(c[0], c[-1], 1e-9)                   # 对称

    def test_equivalent_simple_beam_beta(self):
        secs = ST.girder_sections()
        sum_ai2 = sum(x["a"] ** 2 * x["I"] for x in secs.values())
        cw = 1.9
        beta = 1 / (1 + 0.4 * C.SPAN ** 2 * sum(x["IT"] for x in secs.values()) / (12 * cw * sum_ai2))
        self.close(ST.distribution(cw=cw)[1], beta, 1e-15)
        self.assertGreater(ST.distribution(cw=cw)[2][1]["mc_max"], ST.distribution()[2][1]["mc_max"])

    def test_three_span_frequencies_match_the_literature(self):
        """三跨等跨连续梁前三阶竖弯频率比 π² : 3.55² : 4.3²（王荣霞等 2018 引的理论值，文中取两位有效数字）。"""
        b = ST.Beam(grid(90.0, 180), EI, [0, 60, 120, 180], mass=4.0)
        f = b.frequencies(3)
        self.assertAlmostEqual(f[1] / f[0], 3.55 ** 2 / math.pi ** 2, delta=0.01)
        self.assertAlmostEqual(f[2] / f[0], 4.3 ** 2 / math.pi ** 2, delta=0.01)
        self.assertEqual(ST.negative_moment_mode(3), 3)

    def test_inner_mass_on_a_node_equals_a_nodal_mass(self):
        xs = grid(30.0, 30)
        a = ST.Beam(xs, EI, [0, 30], mass=3.0, point_mass={12: 5.0})
        b = ST.Beam(xs, EI, [0, 30], mass=3.0, inner_mass=[(12.0, 5.0)])
        for x, y in zip(a.frequencies(2), b.frequencies(2)):
            self.assertLess(rel(x, y), 1e-11)

    def test_influence_lines(self):
        L = 30.0
        xs = grid(L, 30)
        b = ST.Beam(xs, EI, [0, 30])
        IM, IV, IR, IW = b.influence()
        c = 10
        w_max = max(abs(v) for row in IW for v in row)
        for p, x in enumerate(xs):
            self.close(IM[c][p], (x * (L - xs[c]) if x <= xs[c] else xs[c] * (L - x)) / L, scale=L / 4)
            self.close(IR[0][p] + IR[30][p], 1.0)
            self.close(IR[0][p], (L - x) / L, scale=1.0)
        for i in range(31):
            for j in range(31):
                self.close(IW[i][j], IW[j][i], scale=w_max)             # 位移互等
        # 剪力影响线在截面处跳 1：截面 c 右侧，力从左到右越过截面
        x2, il = ST._jump(xs, IV[c], c, "R")
        self.close(il[c + 1] - il[c], 1.0)
        self.close(il[c], -xs[c] / L)


# ====================================================================== 规范函数与影响线加载
class CodeFunctions(unittest.TestCase):
    def test_pk_and_impact(self):
        self.assertEqual(ST.pk(5.0), 270.0)
        self.assertEqual(ST.pk(50.0), 360.0)
        self.assertAlmostEqual(ST.pk(30.0), 2 * (30.0 + 130.0), places=12)    # 表 4.3.1-2：2(L0 + 130)
        self.assertEqual(ST.impact(1.4), 0.05)
        self.assertEqual(ST.impact(14.1), 0.45)
        self.assertAlmostEqual(ST.impact(3.0), 0.1767 * math.log(3.0) - 0.0157, places=15)

    def test_lane_loading_of_a_triangle(self):
        """简支梁跨中弯矩影响线（峰值 L/4）：q 布满全跨、P 放在峰值处 = qL²/8 + PL/4。"""
        L = 30.0
        xs = grid(L, 30)
        il = [min(x, L - x) / 2 for x in xs]
        pos, neg = ST.lane_effects(il, xs, 10.5, 320.0)
        self.assertAlmostEqual(pos, 10.5 * L * L / 8 + 320.0 * L / 4, places=9)
        self.assertEqual(neg, 0.0)

    def test_sign_change_is_split_exactly(self):
        pos, neg = ST.lane_effects([1.0, -1.0], [0.0, 2.0], 10.0, 100.0)
        self.assertAlmostEqual(pos, 10.0 * 0.5 + 100.0, places=12)
        self.assertAlmostEqual(neg, -10.0 * 0.5 - 100.0, places=12)


# ====================================================================== 全桥
class Bridge(Close):
    @classmethod
    def setUpClass(cls):
        cls.els, _ = M.build()
        cls.by = {e.eid: e for e in cls.els}
        cls.res = ST.analyse(cls.els)

    def test_all_structural_checks_pass(self):
        for name, ok, detail in ST.run_checks(self.res, self.els):
            self.assertTrue(ok, "%s：%s" % (name, detail))

    def test_stage_one_moments_are_statics(self):
        """阶段一每片梁都是静定的：节点弯矩按静力平衡直接算（支座反力 → 截面左侧力矩和）。"""
        for x in self.res["lines"][:5]:
            L = x["L"]
            for g in L["girders"]:
                (_, xa_s), (_, xb_s) = g["sup"]
                pts = [(q["x"], q["P"]) for q in g["dia"]]
                tot = g["w1"] * (g["xb"] - g["xa"]) + sum(P for _, P in pts)
                mom_b = g["w1"] * ((g["xb"] - xa_s) ** 2 - (g["xa"] - xa_s) ** 2) / 2 + sum(P * (xp - xa_s) for xp, P in pts)
                rb = mom_b / (xb_s - xa_s)
                ra = tot - rb
                for j in range(g["ia"], g["ib"] + 1):
                    xj = L["xs"][j]
                    m = ra * max(0.0, xj - xa_s) + rb * max(0.0, xj - xb_s) - g["w1"] * (xj - g["xa"]) ** 2 / 2
                    m -= sum(P * (xj - xp) for xp, P in pts if xp < xj)
                    self.assertLess(abs(x["M1"][j] - m), 1e-6 * 3000, (g["eid"], j))

    def test_conversion_releases_every_temporary_reaction(self):
        for x in self.res["lines"]:
            self.assertLess(rel(x["reactions"]["conv"], x["reactions"]["temp"]), 1e-9)
            self.assertEqual(len(x["temps"]), 2 * (len(x["L"]["girders"]) - 1))

    def test_continuity_concrete_goes_straight_to_its_bearing(self):
        shares = {}
        for e in self.els:
            if e.cls == "continuity":
                for i, v in enumerate(ST.continuity_shares(e), 1):
                    shares["B-%s-%s%d" % (M.support_name(e.attrs["support"]), e.deck, i)] = v
                self.assertAlmostEqual(sum(ST.continuity_shares(e)), e.volume, places=9)
        for x in self.res["lines"]:
            for b in x["bearings"]:
                self.assertAlmostEqual(b["R_cs"], shares.get(b["id"], 0.0) * C.UNIT_RC, places=9)

    def test_distribution_weights_along_the_span(self):
        L = self.res["lines"][0]["L"]
        w = ST._weights(L, 0.9, 0.5)
        for (a, b), (ja, jb) in zip(L["spans"], L["span_nodes"]):
            self.assertAlmostEqual(w[ja], 0.9, places=12)
            self.assertAlmostEqual(w[jb], 0.9, places=12)
            for j in range(ja, jb + 1):
                t = min(L["xs"][j] - a, b - L["xs"][j]) / ((b - a) / 4)
                self.assertAlmostEqual(w[j], 0.5 if t >= 1 else 0.9 - 0.4 * t, places=12)

    def test_bearing_area_is_read_from_the_bim_geometry(self):
        e = self.by["B-P05-R1"]
        self.assertEqual(ST.bearing_plan(e), ("rect", C.BEARING_CONT_A, C.BEARING_CONT_B))
        self.assertAlmostEqual(ST.effective_area(ST.bearing_plan(e)), (0.49) * (0.59), places=12)
        e = self.by["B-L01-1a"]
        self.assertAlmostEqual(ST.effective_area(ST.bearing_plan(e)), math.pi * 0.44 ** 2 / 4, places=12)

    def test_required_size_is_the_break_even_size(self):
        for x in self.res["lines"][:3]:
            for b in x["bearings"]:
                plan = b["plan"]
                need = (("circle", b["size_req"]) if plan[0] == "circle" else ("rect", b["size_req"], plan[2]))
                self.assertAlmostEqual(b["Rck"] / ST.effective_area(need) / 1000.0, C.SIGMA_C, places=9)

    def test_negative_moment_uses_the_top_of_the_first_band(self):
        for x in self.res["lines"]:
            n = len(x["L"]["spans"])
            self.assertEqual(x["neg_mode"], n)
            self.assertEqual(x["f_neg"], x["freqs"][n - 1])
            self.assertGreater(x["f_neg"] / x["f1"], 1.9)          # 四跨一联：约 2.0 f1
            self.close(x["mu_neg"], ST.impact(x["f_neg"]), 1e-15)

    def test_code_frequency_estimates(self):
        """条文说明的估算式按原式手算一条梁位线；等跨连续梁的一阶振型是各跨交替的简支振型，所以有限元的 f1
        与一跨简支梁的 π²/(2πl²)·√(EIc/mc) 差不到 1.5%；估算式的 f1、f2 都高于有限元的基频与第四阶。"""
        x = self.res["lines"][0]
        L = x["L"]
        e = ST.code_frequency_estimates(x)
        root = math.sqrt(C.E_C50 * L["sec"]["I"] / e["m"])
        self.close(e["f1"], 13.616 / (2 * math.pi * x["L0"] ** 2) * root, 1e-12)
        self.close(e["f2"], 23.651 / (2 * math.pi * x["L0"] ** 2) * root, 1e-12)
        g = L["girders"][0]
        self.assertGreater(e["m"], g["w1"] / ST.G_ACC)
        for y in self.res["lines"]:
            f = ST.code_frequency_estimates(y)
            self.assertLess(abs(y["f1"] / f["f_simple"] - 1), 0.015)
            self.assertGreater(f["f1"], y["f1"])
            self.assertGreater(f["f2"], y["f_neg"])

    def test_continuous_stage_distribution_uses_the_largest_cw(self):
        self.assertEqual(self.res["cw"], max(max(x["cw"]) for x in self.res["lines"]))
        self.close(self.res["beta"], ST.torsion_beta(self.res["secs"], C.SPAN, self.res["cw"]), 1e-15)
        self.assertGreater(self.res["beta"], self.res["beta_simple"])

    def test_decks_are_not_mirror_images(self):
        """曲线内外侧梁长不同：左右幅对应梁位线的内力不应完全相等（防止两幅误用同一份几何）。"""
        a = self.res["lines"][0]
        b = next(x for x in self.res["lines"] if x["L"]["deck"] == "R" and x["L"]["unit"] == 1 and x["L"]["line"] == 1)
        self.assertNotAlmostEqual(max(a["Mud_p"]), max(b["Mud_p"]), places=3)


# ====================================================================== 收缩徐变（附录 C）
class AppendixC(unittest.TestCase):
    # 表 C.2.2 名义徐变系数：加载龄期 → (40%–70%：h = 100/200/300/600, 70%–99%：同)。14 d 那行在线转录版抄错了
    # （后半行与 7 d 重复），不比。
    PHI0 = {3: (3.90, 3.50, 3.31, 3.03, 2.83, 2.65, 2.56, 2.44), 7: (3.33, 3.00, 2.82, 2.59, 2.41, 2.26, 2.19, 2.08),
            28: (2.56, 2.30, 2.17, 1.99, 1.86, 1.74, 1.69, 1.60), 60: (2.21, 1.99, 1.88, 1.72, 1.61, 1.51, 1.46, 1.39),
            90: (2.05, 1.84, 1.74, 1.59, 1.49, 1.39, 1.35, 1.28)}

    def test_nominal_shrinkage_matches_table_c12(self):
        """公式 0.5298 / 0.3101（×10⁻³），表列 0.529 / 0.310：表是截到三位小数的。"""
        for rh, table in ((0.55, 0.529), (0.80, 0.310)):
            v = ST.nominal_shrinkage(rh) * 1e3
            self.assertEqual(math.floor(v * 1000) / 1000, table)

    def test_nominal_creep_matches_table_c22(self):
        """40 格里 39 格与公式差在两位小数的舍入以内（≤ 0.0055）；7 d、h = 200 mm、RH 55% 那格表列 3.00，
        公式是 2.987——同一行、同一列的其余格都对得上，按表格的笔误处理，单独放宽。"""
        for t0, row in self.PHI0.items():
            for j, h in enumerate((100, 200, 300, 600) * 2):
                rh = 0.55 if j < 4 else 0.80
                tol = 0.014 if (t0, h, rh) == (7, 200, 0.55) else 0.0055
                self.assertAlmostEqual(ST.nominal_creep(t0, h, rh), row[j], delta=tol, msg=(t0, h, rh))

    def test_development_functions(self):
        """βs、βc 从 0 单调增到 1；ts 之前不收缩；终极值（t = None）等于名义值。"""
        h = 210.0
        prev = -1.0
        for t in (7, 8, 30, 365, 3650, 36500):
            v = ST.shrinkage_strain(7, h, 0.8, t=t)
            self.assertGreaterEqual(v, prev)
            prev = v
        self.assertEqual(ST.shrinkage_strain(7, h, 0.8, t=7), 0.0)
        self.assertAlmostEqual(ST.shrinkage_strain(3, h, 0.8, ts=3), ST.nominal_shrinkage(0.8), places=15)
        self.assertLess(ST.creep_coefficient(20, h, 0.8, t=3650), ST.creep_coefficient(20, h, 0.8))
        self.assertAlmostEqual(ST.creep_coefficient(20, h, 0.8, t=10 ** 9), ST.creep_coefficient(20, h, 0.8), places=6)

    def test_development_by_hand(self):
        """βs(100 d, h = 200 mm) = √(100 / (350·2² + 100))；βH(h = 200, RH 80%) = 150(1 + 0.96¹⁸)·2 + 250，
        βc(1000 d) = (1000 / (βH + 1000))^0.3。"""
        self.assertAlmostEqual(ST.shrinkage_development(100, 200), math.sqrt(100 / 1500), places=15)
        bh = 150 * (1 + 0.96 ** 18) * 2 + 250
        self.assertAlmostEqual(ST.creep_coefficient(28, 200, 0.8, t=1028) / ST.nominal_creep(28, 200, 0.8),
                               (1000 / (bh + 1000)) ** 0.3, places=12)

    def test_notional_size_by_hand(self):
        """h = 2A/u：预制 T 梁截面周长逐段手算（翼缘顶 1.85、翼缘边 2 × 0.16、承托斜边、腹板、马蹄斜边与侧边、底 0.5）。"""
        u = (1.85 + 2 * 0.16 + 2 * math.hypot(0.825, 0.09) + 2 * 1.35 + 2 * math.hypot(0.15, 0.15) + 2 * 0.25 + 0.5)
        self.assertAlmostEqual(ST.notional_size(), 2 * 0.83575 / u * 1000, places=9)


# ====================================================================== 支座：位移、剪切变形、转角与抗滑
class BearingMovements(Close):
    @classmethod
    def setUpClass(cls):
        cls.els, _ = M.build()
        cls.by = {e.eid: e for e in cls.els}
        cls.res = ST.analyse(cls.els)
        cls.bd = ST.bearing_design(cls.res, cls.els)

    def test_series_table_is_self_consistent(self):
        """规格表：te = n·t1 + 2 × 2.5 mm，总厚 t = te + (n + 1)·t0；容许位移 (te − 5)/2、0.7(te − 5) 与表里的
        Δl1、Δl2 一致（d450：22 / 27.5 / 33 / 38.5，30.8 / 38.5 / 46.2 / 53.9；500×600：22.5 / 30 / 37.5 / 45）。"""
        dl1 = {"d450": (22.0, 27.5, 33.0, 38.5), "500×600": (22.5, 30.0, 37.5, 45.0)}
        dl2 = {"d450": (30.8, 38.5, 46.2, 53.9), "500×600": (31.5, 42.0, 52.5, 63.0)}
        for ser in C.BEARING_SERIES.values():
            for j, (t, te) in enumerate(sorted(ser["te"].items())):
                n = (te - 2 * C.BEARING_COVER_TB) / ser["t1"]
                self.assertAlmostEqual(n, round(n), places=9)
                self.assertAlmostEqual(t, te + (round(n) + 1) * ser["t0"], places=9)
                self.assertAlmostEqual((te - 0.005) / 2 * 1000, dl1[ser["name"]][j], places=9)
                self.assertAlmostEqual(0.7 * (te - 0.005) * 1000, dl2[ser["name"]][j], places=9)

    def test_shape_factor_matches_the_table(self):
        for eid in ("B-L01-1a", "B-P05-R1"):
            sp = ST.bearing_spec(self.by[eid])
            self.assertAlmostEqual(sp["S"], sp["series"]["S"], delta=0.005)

    def test_bearing_type_is_read_from_the_bim(self):
        self.assertTrue(ST.bearing_spec(self.by["B-L01-1a"])["sliding"])
        self.assertFalse(ST.bearing_spec(self.by["B-P05-R1"])["sliding"])
        self.assertAlmostEqual(ST.bearing_spec(self.by["B-L01-1a"])["t"], 0.084, places=12)   # 87 mm 减 3 mm 四氟板

    def test_ages_come_from_the_schedule(self):
        rows = SC.plan()[0]
        convs = {(c["deck"], c["unit"]): c for c in SC.conversions(rows)}
        ages = self.bd["inputs"]["ages"]
        for (d, u, kind), t in ages.items():
            a, b = M.unit_bounds()[u - 1]
            gs = [r for r in rows if r["deck"] == d and a < r["span"] <= b]
            want = (min((r["erect"] - r["cast"]).days for r in gs) if kind == "end"
                    else min((convs[(d, u)]["cast"] - r["cast"]).days for r in gs))
            self.assertEqual(t, want)
        self.assertGreaterEqual(min(ages.values()), C.MIN_AGE_DAYS)

    def test_fixed_point_is_the_stiffness_centroid(self):
        """Σ k·(P − P0) = 0；滑板支座不计刚度。对称布置：不动点离中间那个连续墩的支座连线不到 1 m。"""
        for (d, u), g in self.bd["units"].items():
            sx = sy = 0.0
            for r in self.bd["rows"]:
                if r["deck"] == d and r["unit"] == u and not r["sliding"]:
                    c = self.by[r["id"]].params["c"]
                    k = C.GE_BY_CLIMATE[C.CLIMATE] * 1000 * r["spec"]["Ag"] / r["spec"]["te_eff"]
                    sx, sy = sx + k * (c[0] - g["x0"]), sy + k * (c[1] - g["y0"])
            self.assertLess(math.hypot(sx, sy), 1e-6 * g["K"])
            mid = M.unit_bounds()[u - 1][0] + 2
            ps = [self.by[r["id"]].params["c"] for r in self.bd["rows"] if r["deck"] == d and r["support"] == mid]
            dist = min(math.hypot(p[0] - g["x0"], p[1] - g["y0"]) for p in ps)
            self.assertLess(dist, 3.0)

    def test_displacement_is_distance_times_strain(self):
        inp = self.bd["inputs"]
        t_max, t_min = inp["T"]
        for r in self.bd["rows"][::17]:
            e_t = C.ALPHA_C * (inp["t_set"][1] - t_min)
            e_s = ST.shrinkage_strain(r["t0"], inp["h_mm"], inp["rh"])
            e_c = inp["sigma_pc"] / (C.E_C50 / 1000) * ST.creep_coefficient(r["t0"], inp["h_mm"], inp["rh"])
            self.close(r["d_contract"], r["r"] * (e_t + e_s + e_c), 1e-12)
            self.close(r["d_expand"], r["r"] * C.ALPHA_C * (t_max - inp["t_set"][0]), 1e-12)

    def test_braking_force_by_hand(self):
        """一联 120 m：10% × (10.5 × 120 + Pk) ≈ 158 kN < 165 kN，取 165；三车道同向 × 2.34 = 386.1 kN，全部由普通板式支座分担。"""
        for (d, u), g in self.bd["units"].items():
            self.assertEqual(g["lanes"], 3)
            self.assertEqual(g["F_lane"], 165.0)
            self.close(g["F"], 2.34 * 165.0, 1e-12)
            shares = sum(r["F_brake"] for r in self.bd["rows"] if not r["sliding"] and (r["deck"], r["unit"]) == (d, u))
            self.close(shares, g["F"], 1e-9)

    def test_prestress_estimate_meets_the_crack_criterion_exactly(self):
        """反推出的有效预加力正好让跨中下缘 σst − 0.85σpc = 0。"""
        for q in self.bd["inputs"]["prestress"][::7]:
            sec = ST.girder_sections()[q["line"]]
            yb0 = sec["y0"] + C.H_GIRDER
            W0 = sec["I0"] / yb0
            pc = q["Np"] / sec["A0"] + q["Np"] * (yb0 - C.TENDON_COVER) / W0
            self.close(q["sigma_st"] * 1000 - 0.85 * pc, 0.0, 1e-9, q["sigma_st"] * 1000)

    def test_rotation_influence_line_is_reciprocal(self):
        """支座转角影响线用了互等定理：在节点 p 放单位向下力时支座 s 的转角 = 在 s 加单位力矩时 p 的挠度（向下为正）。"""
        b = ST.Beam(grid(90.0, 90), EI, [0, 30, 60, 90])
        for s in (0, 30):
            F = [0.0] * 182
            F[2 * s + 1] = 1.0
            um = b.solve(F)
            for p in (7, 45, 71):
                G = [0.0] * 182
                G[2 * p] = -1.0
                self.close(b.solve(G)[2 * s + 1], -um[2 * p], 1e-9)

    def test_end_rotation_of_a_simple_span(self):
        """简支梁均布荷载：端转角 qL³/24EI（向下荷载左端顺时针为负）。"""
        L, q = 30.0, 10.0
        b = ST.Beam(grid(L, 40), EI, [0, 40])
        u = b.solve(b.load_vector([q] * 40))
        self.close(u[1], -q * L ** 3 / (24 * EI), 1e-9)
        self.close(u[81], q * L ** 3 / (24 * EI), 1e-9)

    def test_compression_by_hand(self):
        """δc,m = σ·te'·(1/Ee + 1/Eb)，Ee = 5.4·Ge·S²：d450（S = 10、te' = 55 mm）在 10 MPa 下是 1.29 mm——
        厂家表按这个值给出允许转角 2δ/d = 0.0057（温热地区）。"""
        sp = ST.bearing_spec(self.by["B-L01-1a"])
        d = 10.0 * sp["te_eff"] * (1 / (5.4 * 1.0 * 100.0) + 1 / C.EB_RUBBER)
        self.assertAlmostEqual(2 * d / 0.45, 0.0057, delta=0.00005)
        r = next(x for x in self.bd["rows"] if x["id"] == "B-L01-1a")
        self.close(r["dcm"], r["sigma"] * sp["te_eff"] * (1 / (5.4 * sp["S"] ** 2) + 1 / C.EB_RUBBER), 1e-12)

    def test_plain_end_bearings_would_not_do(self):
        """伸缩端要是用普通板式支座：同一平面尺寸最厚一档（114 mm）也不够 2Δl——这就是换滑板支座的理由。"""
        e = max((r for r in self.bd["rows"] if r["kind"] == "end"), key=lambda r: r["d_contract"])
        self.assertIsNone(ST.plain_thickness_needed(e["spec"], e["d_contract"], 0.0))
        self.assertGreater(e["d_contract"], (0.082 - 0.005) / 2)
        c = max((r for r in self.bd["rows"] if r["kind"] == "cont"), key=lambda r: r["util_shear"])
        self.assertAlmostEqual(ST.plain_thickness_needed(c["spec"], c["d_contract"], c["d_brake"]), C.BEARING_CONT_T, places=12)


# ====================================================================== 反例：每条检查都必须会失败
class Counterexamples(unittest.TestCase):
    ONE = [("R", 2, 1)]

    def assertRed(self, result):
        self.assertFalse(result[1], "检查本该失败却通过了：%s" % (result,))

    def test_distribution_premise_catches_a_moved_diaphragm(self):
        els, _ = M.build()
        by = {e.eid: e for e in els}
        d = by["D-L03-23"]                                    # 第 3 跨、2–3 号梁之间、跨中那道
        t = M.frame(d.attrs["station"])[1]
        d.params["solids"][0]["v"] = [(p[0] + 3.0 * t[0], p[1] + 3.0 * t[1], p[2]) for p in d.params["solids"][0]["v"]]
        res = ST.analyse(els, self.ONE)
        self.assertRed(ST.check_distribution_premise(res, els))

    def test_distribution_premise_catches_a_wide_bridge(self):
        els, _ = M.build()
        res = ST.analyse(els, self.ONE)
        self.assertTrue(ST.check_distribution_premise(res, els)[1])
        with mock.patch.object(C, "DECK_WIDTH", 16.0):
            self.assertRed(ST.check_distribution_premise(res, els))

    def test_equilibrium_catches_a_forgotten_diaphragm(self):
        els, _ = M.build()
        with mock.patch.object(ST, "N_DIAPHRAGMS", ST.N_DIAPHRAGMS - 1):
            res = ST.analyse(els)
        self.assertRed(ST.check_equilibrium(res, els))

    def test_equilibrium_catches_double_counted_continuity_concrete(self):
        els, _ = M.build()
        orig = ST.continuity_shares
        with mock.patch.object(ST, "continuity_shares", lambda cs: [2 * v for v in orig(cs)]):
            res = ST.analyse(els)
        self.assertRed(ST.check_equilibrium(res, els))

    def test_deflection_catches_a_soft_girder(self):
        els, _ = M.build()
        with mock.patch.object(C, "STIFF_FACTOR", 0.05):
            res = ST.analyse(els, self.ONE)
        self.assertRed(ST.check_deflection(res))

    def test_bearing_stress_catches_a_small_bearing_in_the_bim(self):
        els, _ = M.build()
        by = {e.eid: e for e in els}
        by["B-P05-R1"].params["w"] = 0.30                     # 在 BIM 里把支座顺桥向改成 300 mm
        res = ST.analyse(els, self.ONE)
        self.assertRed(ST.check_bearing_stress(res))

    def test_uplift_catches_a_weightless_deck(self):
        els, _ = M.build()
        with mock.patch.object(C, "UNIT_RC", 0.3), mock.patch.object(ST, "PAVEMENT_UNIT", 0.3):
            res = ST.analyse(els, self.ONE)
        self.assertRed(ST.check_uplift(res))

    def test_narrow_bridge_catches_a_missing_envelope(self):
        els, _ = M.build()
        with mock.patch.object(ST, "GM_ENVELOPE", False):
            res = ST.analyse(els, self.ONE)
        self.assertRed(ST.check_narrow_bridge(res))

    def test_bearing_shear_catches_plain_bearings_at_the_expansion_ends(self):
        """伸缩端换回普通板式支座 GYZ d450×84：离不动点 60 m，位移远超 (te − 5)/2 = 27.5 mm。"""
        with mock.patch.object(C, "END_BEARING_SLIDING", False), mock.patch.object(C, "BEARING_T", 0.084):
            els, _ = M.build()
            res = ST.analyse(els, self.ONE)
            self.assertRed(ST.check_bearing_shear(ST.bearing_design(res, els)))

    def test_bearing_shear_catches_a_thin_pier_bearing(self):
        """连续墩支座换成 500×600 最薄的 70 mm 一档：容许 22.5 mm，离不动点 30 m 处要 24.7 mm。"""
        with mock.patch.object(C, "BEARING_CONT_T", 0.070):
            els, _ = M.build()
            res = ST.analyse(els, self.ONE)
            self.assertRed(ST.check_bearing_shear(ST.bearing_design(res, els)))

    def test_rotation_catches_the_thinnest_end_bearing(self):
        """伸缩端橡胶部分换成最薄的 69 mm 一档（te' = 44 mm）：压缩量变小，梁端转角就会让支座边缘脱空。"""
        with mock.patch.object(C, "BEARING_T", 0.069 + C.PTFE_T):
            els, _ = M.build()
            res = ST.analyse(els, [("R", 3, 1)])
            self.assertRed(ST.check_bearing_compression(ST.bearing_design(res, els)))

    def test_slip_catches_a_slippery_seat_and_a_dry_slide(self):
        els, _ = M.build()
        res = ST.analyse(els, self.ONE)
        with mock.patch.object(C, "MU_RUBBER_CONCRETE", 0.1):
            self.assertRed(ST.check_bearing_slip(ST.bearing_design(res, els)))
        with mock.patch.object(C, "MU_PTFE", 0.12):                             # 不加硅脂（D60 表 4.3.13）
            self.assertRed(ST.check_bearing_slip(ST.bearing_design(res, els)))

    def test_travel_catches_a_tight_joint_in_the_bim(self):
        els, _ = M.build()
        res = ST.analyse(els, self.ONE)
        self.assertTrue(ST.check_bearing_travel(ST.bearing_design(res, els))[1])
        by = {e.eid: e for e in els}
        by["EJ-P08-R"].attrs["gap"] = 0.015                                     # 在 BIM 里把过渡墩处的缝改成 15 mm
        self.assertRed(ST.check_bearing_travel(ST.bearing_design(res, els)))

    def test_travel_catches_a_bearing_too_close_to_the_girder_end(self):
        with mock.patch.object(C, "BEARING_INSET", 0.26):
            els, _ = M.build()
            res = ST.analyse(els, self.ONE)
            self.assertRed(ST.check_bearing_travel(ST.bearing_design(res, els)))


# ====================================================================== OpenSees 互核
@unittest.skipIf(ops is None, "没装 openseespy")
class OpenSeesCrossCheck(unittest.TestCase):
    """同样的节点、单元、支座与荷载在 OpenSees 里建一遍（elasticBeamColumn、beamUniform / beamPoint 荷载、
    一致质量），节点位移、弯矩、反力与频率逐项比对。两边都是精确的欧拉梁单元，差别只有舍入。"""

    @classmethod
    def setUpClass(cls):
        els, _ = M.build()
        cls.L = ST.girder_lines(els, [("L", 2, 1)])[0]

    @staticmethod
    def build(xs, EIs, supports, mass=None):
        ops.wipe()
        ops.model("basic", "-ndm", 2, "-ndf", 3)
        for i, x in enumerate(xs):
            ops.node(i + 1, float(x), 0.0)
            ops.fix(i + 1, 1, 1 if i in supports else 0, 0)       # 轴向全约束：只留竖弯
        ops.geomTransf("Linear", 1)
        for e in range(len(xs) - 1):
            args = ["elasticBeamColumn", e + 1, e + 1, e + 2, 1.0, C.E_C50, EIs[e] / C.E_C50, 1]
            if mass is not None:
                args += ["-mass", mass[e], "-cMass"]
            ops.element(*args)

    @staticmethod
    def solve():
        ops.system("BandGeneral")
        ops.numberer("Plain")
        ops.constraints("Plain")
        ops.integrator("LoadControl", 1.0)
        ops.algorithm("Linear")
        ops.analysis("Static")
        ops.analyze(1)
        ops.reactions()

    def compare(self, beam, xs, M_, R_, u):
        n = len(xs)
        scale_m = max(abs(v) for v in M_)
        for e in range(n - 1):
            f = ops.eleResponse(e + 1, "localForce")
            self.assertLess(abs(f[5] - M_[e + 1]), 1e-9 * scale_m)
        scale_r = max(abs(v) for v in R_.values())
        for s, r in R_.items():
            self.assertLess(abs(ops.nodeReaction(s + 1, 2) - r), 1e-9 * scale_r)
        scale_u = max(abs(v) for v in u[0::2])
        for j in range(n):
            self.assertLess(abs(ops.nodeDisp(j + 1, 2) - u[2 * j]), 1e-9 * scale_u)

    def test_stage_one_girder(self):
        L = self.L
        g = L["girders"][1]
        beam, udl, inner = ST.stage_one_beam(L, g)
        xs = beam.xs
        u = beam.solve(beam.load_vector(udl, None, inner))
        M_, V_, R_ = beam.results(u, udl, None, inner)
        self.build(xs, beam.EI, beam.supports)
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        for e, q in enumerate(udl):
            ops.eleLoad("-ele", e + 1, "-type", "-beamUniform", -q)
        for x, P in inner:
            e = beam._element_at(x)
            ops.eleLoad("-ele", e + 1, "-type", "-beamPoint", -P, (x - xs[e]) / (xs[e + 1] - xs[e]))
        self.solve()
        self.compare(beam, xs, M_, R_, u)

    def test_continuous_beam_conversion_and_second_stage(self):
        L = self.L
        beam = ST.continuous_beam(L, with_mass=False)
        pts = [(t["node"], 400.0 + 10.0 * i) for i, t in enumerate(L["temps"])]
        u = beam.solve(beam.load_vector(L["w2"], pts))
        M_, V_, R_ = beam.results(u, L["w2"], pts)
        self.build(L["xs"], L["EI2"], beam.supports)
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        for e, q in enumerate(L["w2"]):
            ops.eleLoad("-ele", e + 1, "-type", "-beamUniform", -q)
        for nd, P in pts:
            ops.load(nd + 1, 0.0, -P, 0.0)
        self.solve()
        self.compare(beam, L["xs"], M_, R_, u)

    def test_influence_line_ordinates(self):
        L = self.L
        beam = ST.continuous_beam(L, with_mass=False)
        IM, IV, IR, IW = beam.influence()
        for p in (7, 40, 83, 120):
            self.build(L["xs"], L["EI2"], beam.supports)
            ops.timeSeries("Linear", 1)
            ops.pattern("Plain", 1, 1)
            ops.load(p + 1, 0.0, -1.0, 0.0)
            self.solve()
            for j in range(len(L["xs"])):
                self.assertLess(abs(-ops.nodeDisp(j + 1, 2) - IW[j][p]), 1e-9 * max(abs(v[p]) for v in IW))
            for s in beam.supports:
                self.assertLess(abs(ops.nodeReaction(s + 1, 2) - IR[s][p]), 1e-9)
            for e in range(len(L["xs"]) - 1):
                self.assertLess(abs(ops.eleResponse(e + 1, "localForce")[5] - IM[e + 1][p]), 1e-9 * 30)

    def test_frequencies(self):
        L = self.L
        ne = len(L["xs"]) - 1
        mass = [(L["w1"][e] + L["w2"][e] + L["wj"][e]) / ST.G_ACC for e in range(ne)]
        supports = [b["node"] for b in L["perm"]]
        ours = ST.Beam(L["xs"], L["EI2"], supports, mass=mass).frequencies(4)
        self.build(L["xs"], L["EI2"], supports, mass)
        lam = ops.eigen("-fullGenLapack", 4)
        theirs = [math.sqrt(v) / (2 * math.pi) for v in lam]
        for a, b in zip(ours, theirs):
            self.assertLess(rel(a, b), 1e-9)


if __name__ == "__main__":
    unittest.main()
