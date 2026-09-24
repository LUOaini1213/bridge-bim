"""上部结构计算（教学级）：截面特性、荷载横向分布、施工阶段内力、汽车荷载影响线加载、
作用组合、基频与冲击系数、挠度与支座验算。只用标准库（Rhino 8 内置的 3.9 也要能跑）。

这不是设计计算书：没有预应力钢束设计，不验算截面应力与承载力，钢筋量仍是指标估算；
不计收缩徐变次内力、温度梯度、预应力次内力与支座沉降，内力组合里只有结构重力与汽车荷载。
收缩、徐变、温度与制动力只用来算支座处的位移（支座剪切变形、滑板行程、梁端缝）。
它回答的是「按现在的构件尺寸，梁里的内力、支座反力、活载挠度大概是多少，
支座规格与挠度限值过不过」。每个规范系数的出处写在 config.py。

几何与荷载都取自 BIM 构件（model.build 生成的同一份数据）：
- 每幅、每联、每个梁位是一条梁位线：各跨预制梁与墩顶连续段沿梁轴（平面长度）展开成一维坐标，
  支座、临时支座就在模型里它们所在的位置。
- 一期恒载 = 预制梁 + 横隔板 + 湿接缝 + 翼缘现浇段，构件体积 × 26 kN/m³：梁自重、两侧湿接缝各一半、
  边梁的翼缘现浇段沿梁长均布；每道横隔板一半给两侧的梁，作为集中力放在它与梁轴的交点。
  墩顶连续段的混凝土直接浇在永久支座上【假设】，只进该支座的反力，不在梁里产生弯矩。
- 二期恒载 = 铺装（沥青 24、调平层 25 kN/m³）+ 护栏（26 kN/m³），每跨五片梁均分。

方法：
- 截面：多边形积分，精确对应模型里的截面。预制截面承担一期恒载；湿接缝浇好后的组合截面
  （边梁翼缘延到桥面边缘）承担体系转换、二期恒载与汽车荷载；墩顶连续段按实心块取截面。
  抗扭惯性矩按矩形分块 Σ c·b·t³。
- 横向分布：跨中用修正偏心压力法（刚性横梁 + 主梁抗扭修正 β），支点用杠杆原理法；
  车辆横向按轮距 1.8 m、车距 1.3 m、距路缘 0.5 m 布置，1–3 列逐一枚举并乘横向车道布载系数。
  每片梁取两个极端：分到最多（mc、m0），分到最少（车在另一侧时偏心压力法给出负值）。
  弯矩、挠度全跨用 mc；剪力与支座反力的 m 在支点取 m0、到 1/4 跨（第一道中横隔板）线性过渡为 mc。
  连续梁阶段按教材《桥梁工程》第六章的「等代简支梁」：按跨中挠度相等求抗弯刚度修正系数
  C_w = w简支 / w连续（这里直接用本仓的梁单元算：两跨等跨 1.391，三跨 1.429 / 1.818，与教材表 2-6-4
  一致），以 C_w·I 代入 β，抗扭惯性矩不修正；全桥偏安全地取最大的 C_w（中跨）统一算 mc。
  同一节还给了更合适的窄桥判别 θ' = (B/2l)·⁴√(C_w·Jx/Jy) ≤ 0.3，不满足时宜用比拟正交异性板法（G-M 法）。
  本桥 θ' ≈ 0.41，所以两法都算、mc 取大值：G-M 的横向挠曲方程用传递矩阵直接解（不查表），K0、K1 按
  Kα = K0 + (K1 − K0)√α 内插；对教材例 2-4-6 三片梁的 mc 差 ≤ 1%。
- 纵向：平面欧拉梁单元直接刚度法（Hermite 形函数、一致质量矩阵），半带宽 3 的带状分解。
  施工阶段：① 一期恒载由每片预制梁两端支承（伸缩端永久支座、连续端临时支座）的简支梁承担；
  ② 体系转换：拆临时支座等于把它们的反力反向加到以永久支座为支承的整联连续梁上；
  ③ 二期恒载与汽车荷载作用于连续梁。
- 汽车荷载（公路-I级车道荷载）：逐节点求影响线，均布荷载布满同号区段，集中荷载放在最大竖标处；
  计算剪力与支座反力时集中荷载乘 1.2。Pk 按该联最大计算跨径取【假设：规范没写多跨连续梁取哪一跨】。
- 冲击系数 μ 由结构基频 f 定。对每条梁位线的整联连续梁（组合截面，一、二期恒载质量）求竖弯频率：
  正弯矩、剪力、支座反力用基频 f1；负弯矩用该联第一频带的最高一阶（n 跨一联取第 n 阶，四跨约为 f1 的 2 倍）——
  一阶振型在中间支点处弯矩为零，高阶振型的最大弯矩就在支点上【假设：按王荣霞等（公路交通科技 2018
  年第 5 期，引袁向荣 2013）对三跨连续梁取第三阶竖弯频率的做法推广到 n 跨】。条文说明的连续梁基频估算式
  （13.616 / 23.651，据专利 CN105404718A 转引）只作对照，见 code_frequency_estimates。
- 组合：基本组合 γ0(γG·G + 1.4·(1+μ)·Q)，结构重力有利时 γG 取 1.0；频遇组合 G + 0.7·Q（不计冲击）。
- 挠度：汽车荷载频遇值（不计冲击）乘挠度长期增长系数 ηθ，刚度 B0 = 0.95·Ec·I，限值 L/600。
- 支座：Rck 由结构重力与汽车荷载（计入冲击）标准值组合，Ae（加劲钢板面积）≥ Rck/σc（JTG 3362-2018
  第 8.7.3 条，σc 按 JT/T 4 取）；基本组合下（结构重力有利取 1.0）单向受压支座始终受压（第 4.1.8 条）。
  位移与剪切变形（bearing_design）：每幅每联的不动点 = 普通板式支座抗推刚度的平面形心；温度（D60 有效温度）、
  收缩与徐变（附录 C，龄期取自梁场与架梁计划）、制动力（按刚度分配）；再验算 te ≥ 2Δl / 1.43Δl、受压稳定、
  压缩与转角、加劲钢板、抗滑、四氟滑板摩擦、滑板行程与梁端缝。
- 曲线梁的弯扭耦合不计：每跨圆心角不超过 2.5°，梁位线展开成直梁。
"""
import bisect
import math

from . import config as C, model as MD, schedule as SC

G_ACC = 9.80665                     # m/s²，重力加速度（kN → t 质量）
N_G = len(C.GIRDER_OFFSETS)
PAVEMENT_UNIT = (C.ASPHALT_T * C.UNIT_ASPHALT + (C.PAVEMENT_T - C.ASPHALT_T) * C.UNIT_LEVELLING) / C.PAVEMENT_T
N_DIAPHRAGMS = 2 + len(C.DIAPHRAGM_FRACTIONS)


# ====================================================================== 截面
def polygon_props(pts):
    """逆时针多边形的面积、形心 y、对形心水平轴的惯性矩。"""
    A = Sy = Iy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cr = x0 * y1 - x1 * y0
        A += cr
        Sy += (y0 + y1) * cr
        Iy += (y0 * y0 + y0 * y1 + y1 * y1) * cr
    A /= 2.0
    yc = Sy / (6.0 * A)
    return A, yc, Iy / 12.0 - A * yc * yc


def composite_polygon(left, right):
    """预制 T 形截面两侧翼缘延长到 left / right（相对梁轴，左正），延长段厚 WET_JOINT_T。"""
    sec = list(C.T_SECTION)
    t = C.WET_JOINT_T
    return [(-right, 0.0), (-right, -t)] + sec[1:-1] + [(left, -t), (left, 0.0)]


def joint_polygon(left, right, edge=None):
    """墩顶连续段在一个梁位上的截面：梁位块与两侧梁间横梁都是全梁高；边梁外侧（翼缘边以外）
    只有桥面板厚。edge 为 "right"（1 号梁，外侧在 −x）或 "left"（外侧在 +x）。"""
    h, t, f = C.H_GIRDER, C.WET_JOINT_T, MD.HALF_FLANGE
    if edge == "right":
        return [(-right, 0.0), (-right, -t), (-f, -t), (-f, -h), (left, -h), (left, 0.0)]
    if edge == "left":
        return [(-right, 0.0), (-right, -h), (f, -h), (f, -t), (left, -t), (left, 0.0)]
    return [(-right, 0.0), (-right, -h), (left, -h), (left, 0.0)]


def _clip_below(pts, y0):
    """多边形在 y ≥ y0 那部分（Sutherland–Hodgman，单个半平面）。"""
    out = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ina, inb = a[1] >= y0, b[1] >= y0
        if ina:
            out.append(a)
        if ina != inb:
            f = (y0 - a[1]) / (b[1] - a[1])
            out.append((a[0] + f * (b[0] - a[0]), y0))
    return out


def _c_torsion(b, t):
    """矩形抗扭惯性矩系数（t ≤ b）：c = (1 − 0.63 r + 0.052 r⁵)/3，r = t/b。"""
    r = min(b, t) / max(b, t)
    return (1.0 - 0.63 * r + 0.052 * r ** 5) / 3.0


def torsion_parts(pts):
    """T 形截面分成三块矩形 [(长边, 短边)]：翼缘（腹板顶以上，平均厚）、腹板、马蹄。"""
    web = [p for p in C.T_SECTION if abs(abs(p[0]) - C.WEB_HALF) < 1e-9]
    y_top, y_bot = max(p[1] for p in web), min(p[1] for p in web)
    width = max(p[0] for p in pts) - min(p[0] for p in pts)
    a_top = polygon_props(_clip_below(pts, y_top))[0]
    t_f = a_top / width
    below = [(x, -y) for x, y in _clip_below([(x, -y) for x, y in pts], -y_bot)]   # y ≤ y_bot 那部分
    a_bulb = abs(polygon_props(below[::-1])[0]) if len(below) >= 3 else 0.0
    h_bulb = y_bot - min(p[1] for p in pts)
    b_bulb = a_bulb / h_bulb
    return [(width, t_f), (y_top - y_bot, 2 * C.WEB_HALF), (max(b_bulb, h_bulb), min(b_bulb, h_bulb))]


def _rect_torsion(parts):
    return sum(_c_torsion(b, t) * b * t ** 3 for b, t in parts)


def torsion_constant(pts):
    """T 形截面按三块矩形求抗扭惯性矩 Σ c·b·t³。"""
    return _rect_torsion(torsion_parts(pts))


def girder_sections():
    """每个梁位的截面特性：预制截面（A0、I0）、组合截面（A、I、IT）、墩顶连续段实心截面（Aj、Ij）。
    边梁外侧组合到桥面边缘。两幅桥横断面相同。"""
    offs = C.GIRDER_OFFSETS
    pre = polygon_props(C.T_SECTION)
    out = {}
    for i, g in enumerate(offs, 1):
        left = (offs[i] - g) / 2 if i < N_G else C.DECK_WIDTH / 2 - g
        right = (g - offs[i - 2]) / 2 if i > 1 else C.DECK_WIDTH / 2 + g
        poly = composite_polygon(left, right)
        comp = polygon_props(poly)
        joint = polygon_props(joint_polygon(left, right, "right" if i == 1 else "left" if i == N_G else None))
        out[i] = {"a": g, "A0": pre[0], "I0": pre[2], "y0": pre[1], "A": comp[0], "I": comp[2], "yc": comp[1],
                  "IT": torsion_constant(poly), "left": left, "right": right, "Aj": joint[0], "Ij": joint[2]}
    return out


# ====================================================================== 荷载横向分布
def lanes_for_width(w):
    """JTG D60-2015 表 4.3.1-4，车辆单向行驶。"""
    for lo, n in ((24.5, 7), (21.0, 6), (17.5, 5), (14.0, 4), (10.5, 3), (7.0, 2)):
        if w >= lo:
            return n
    return 1


def eccentric_eta(secs, beta, k, e):
    """修正偏心压力法：单位荷载作用在横向位置 e（相对幅中心，左正）时 k 号梁分担的比例。"""
    sum_i = sum(s["I"] for s in secs.values())
    sum_ai2 = sum(s["a"] ** 2 * s["I"] for s in secs.values())
    s = secs[k]
    return s["I"] / sum_i + beta * e * s["a"] * s["I"] / sum_ai2


def torsion_beta(secs, span, cw=1.0):
    """抗扭修正系数 β = 1 / (1 + G l² ΣIT / (12 E Σ aᵢ² Iᵢ))，G = 0.4E（教材式 2-4-48）。
    连续梁按等代简支梁：抗弯惯性矩乘刚度修正系数 cw，抗扭惯性矩不修正（教材第六章第四节）。"""
    sum_it = sum(s["IT"] for s in secs.values())
    sum_ai2 = sum(s["a"] ** 2 * s["I"] for s in secs.values())
    return 1.0 / (1.0 + C.G_RATIO * span ** 2 * sum_it / (12.0 * cw * sum_ai2))


def lever_eta(secs, k, e):
    """杠杆原理法：桥面板在相邻主梁之间按简支、边梁外侧按悬臂，k 号梁分得的比例。
    荷载在悬臂上时边梁大于 1、相邻梁为负，任何位置各梁之和都等于 1。"""
    xs = sorted(s["a"] for s in secs.values())
    j = max(0, min(len(xs) - 2, bisect.bisect_right(xs, e) - 1))     # 荷载所在板跨（外侧延到悬臂）
    a0, a1 = xs[j], xs[j + 1]
    a = secs[k]["a"]
    if a == a0:
        return (a1 - e) / (a1 - a0)
    if a == a1:
        return (e - a0) / (a1 - a0)
    return 0.0


def wheel_offsets(n_veh):
    """n 列车的车轮相对第一个车轮的横向位置。"""
    rel = []
    x = 0.0
    for v in range(n_veh):
        rel += [x, x + C.WHEEL_GAUGE]
        x += C.WHEEL_GAUGE + C.WHEEL_BETWEEN
    return rel


def wheel_layouts(n_veh, e_lo, e_hi, anchors):
    """n 列车横向布置的候选位置：车轮组整体平移，影响线分段线性，最优处一定是某个车轮落在
    某个折点（梁位）上，或车队贴住一侧路缘。返回车轮横向位置列表的列表。"""
    rel = wheel_offsets(n_veh)
    lo, hi = e_lo + C.WHEEL_TO_CURB, e_hi - C.WHEEL_TO_CURB
    span = rel[-1]
    if span > hi - lo + 1e-9:
        return []
    starts = {lo, hi - span}
    for a in anchors:
        for r in rel:
            s = a - r
            if lo - 1e-9 <= s <= hi - span + 1e-9:
                starts.add(min(max(s, lo), hi - span))
    return [[s + r for r in rel] for s in sorted(starts)]


# ---------------------------------------------------------------------- 比拟正交异性板法（G-M 法）
# 教材第六章第四节：连续梁按 θ' = (B/2l)·⁴√(C_w·Jx/Jy) ≤ 0.3 判「窄桥」，比 B/l ≤ 0.5 更合适；不满足时宜用 G-M 法。
THETA_NARROW = 0.3
GM_ENVELOPE = True                  # θ' > 0.3 时 mc 取修正偏心压力法与 G-M 法的大值（测试里关掉它，检查要变红）
# 教材表 2-4-5：横隔梁翼板每侧有效宽度 λ，按 c / l'（c 为相邻横隔梁净距之半，l' 为两根边主梁的中心距）查 λ / c
FLANGE_LAMBDA = ((0.05, 0.983), (0.10, 0.936), (0.15, 0.867), (0.20, 0.789), (0.25, 0.710), (0.30, 0.635),
                 (0.35, 0.568), (0.40, 0.509), (0.45, 0.459), (0.50, 0.416))
GM_STEPS = 400                      # 横向传递矩阵的步数（板宽 [−1, 1] 等分）


def _mm4(P, Q):
    return [[sum(P[i][l] * Q[l][j] for l in range(4)) for j in range(4)] for i in range(4)]


def _mv4(P, v):
    return [sum(P[i][j] * v[j] for j in range(4)) for i in range(4)]


def _expm4(A, s):
    """4×4 矩阵指数 exp(A·s)：缩放平方 + 泰勒级数。"""
    M = [[a * s for a in row] for row in A]
    norm = max(sum(abs(a) for a in row) for row in M)
    k = max(0, int(math.ceil(math.log(norm, 2))) + 1) if norm > 0.25 else 0
    M = [[a / 2.0 ** k for a in row] for row in M]
    E = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    term = [row[:] for row in E]
    for m in range(1, 24):
        term = [[a / m for a in row] for row in _mm4(term, M)]
        E = [[E[i][j] + term[i][j] for j in range(4)] for i in range(4)]
    for _ in range(k):
        E = _mm4(E, E)
    return E


def gm_influence(theta, alpha, eta_k, steps=None):
    """比拟板在横向（以承重结构半宽无量纲化，η ∈ [−1, 1]）的挠曲：单位正弦线荷载作用在 η_k。
    w'''' − 2α(πθ)² w'' + (πθ)⁴ w = δ(η − η_k)，两侧自由边 w'' = 0、w''' − 2α(πθ)² w' = 0（不计泊松比）；
    K = 2(πθ)⁴ w，全宽平均为 1。状态 (w, w', w'', w''') 沿 η 用传递矩阵推进，过荷载点 w''' 跳 1。
    返回函数 e ↦ K(η_k, e)——由互等定理，它也是「荷载在 e 时 η_k 处梁位的 K」，即 η_k 那片梁的横向影响线。"""
    n = steps or GM_STEPS
    lam = math.pi * theta
    c, a = lam ** 4, alpha * lam * lam
    A = [[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0], [-c, 0.0, 2 * a, 0.0]]
    h = 2.0 / n
    Eh = _expm4(A, h)
    grid = [-1.0 + h * i for i in range(n + 1)]
    ik = max(0, min(n - 1, int(math.floor((eta_k + 1.0) / h + 1e-9))))    # 荷载点所在的步

    def march(y0, load):
        ys = [y0]
        y = y0
        for i in range(n):
            if i == ik:                                  # 这一步里有荷载点：走到荷载点、w''' 跳、再走完
                y = _mv4(_expm4(A, eta_k - grid[i]), y)
                if load:
                    y = [y[0], y[1], y[2], y[3] + 1.0]
                y = _mv4(_expm4(A, grid[i + 1] - eta_k), y)
            else:
                y = _mv4(Eh, y)
            ys.append(y)
        return ys
    yp = march([0.0, 0.0, 0.0, 0.0], True)
    y1 = march([1.0, 0.0, 0.0, 0.0], False)                # 左边自由边：w'' = 0，w''' = 2a·w'
    y2 = march([0.0, 1.0, 0.0, 2 * a], False)
    g = lambda y: (y[2], y[3] - 2 * a * y[1])              # noqa: E731 右边自由边的两个条件
    r, p, q = g(yp[-1]), g(y1[-1]), g(y2[-1])
    det = p[0] * q[1] - q[0] * p[1]
    w0 = (-r[0] * q[1] + q[0] * r[1]) / det
    w1 = (-p[0] * r[1] + r[0] * p[1]) / det
    ys = [[u + w0 * v + w1 * s for u, v, s in zip(a_, b_, c_)] for a_, b_, c_ in zip(yp, y1, y2)]
    at_k = _mv4(_expm4(A, eta_k - grid[ik]), ys[ik])
    after_k = at_k[:3] + [at_k[3] + 1.0]

    def K(e):
        """从左侧最近的节点按泰勒级数走到 e：步长 ≤ h，高阶导数由方程本身给出（w'''' = 2a·w'' − c·w，
        再求导两次），截断误差 ~ h⁷ / 7!，可以忽略。"""
        if not -1.0 - 1e-9 <= e <= 1.0 + 1e-9:
            raise ValueError("荷载位置 %.4f 在比拟板宽度以外" % e)
        i = max(0, min(n - 1, int(math.floor((e + 1.0) / h))))
        y0, x0 = ys[i], grid[i]
        if i == ik and e > eta_k:                        # 与荷载点同一步、在它右边：从荷载点（跳过之后）起步
            y0, x0 = after_k, eta_k
        s = e - x0
        w, w1_, w2_, w3_ = y0
        w4 = 2 * a * w2_ - c * w
        w5 = 2 * a * w3_ - c * w1_
        w6 = 2 * a * w4 - c * w2_
        return 2.0 * c * (w + s * (w1_ + s / 2 * (w2_ + s / 3 * (w3_ + s / 4 * (w4 + s / 5 * (w5 + s / 6 * w6))))))
    return K


GM_SCAN = 0.05                      # m：G-M 影响线上找最不利车位的粗扫步长


def smooth_extremes(fn, rel, lo, hi):
    """车队起点 s ∈ [lo, hi] 时 Σ fn(s + r) 的 (最大, 最小)。G-M 的影响线是光滑曲线，最不利处不一定有车轮落在
    梁位上（分段线性时才一定），所以先每 GM_SCAN 粗扫、再在最好的点两侧用黄金分割细化到 1e-6 m。"""
    def f(s):
        return sum(fn(s + r) for r in rel)
    m = max(1, int(math.ceil((hi - lo) / GM_SCAN - 1e-9)))
    ss = [lo + (hi - lo) * j / m for j in range(m + 1)]
    vals = [f(s) for s in ss]
    g = (math.sqrt(5.0) - 1.0) / 2.0
    out = []
    for sign in (1.0, -1.0):
        j = max(range(m + 1), key=lambda i: sign * vals[i])
        a, b = ss[max(0, j - 1)], ss[min(m, j + 1)]
        x1, x2 = b - g * (b - a), a + g * (b - a)
        f1, f2 = sign * f(x1), sign * f(x2)
        while b - a > 1e-6:
            if f1 > f2:
                b, x2, f2 = x2, x1, f1
                x1 = b - g * (b - a)
                f1 = sign * f(x1)
            else:
                a, x1, f1 = x1, x2, f2
                x2 = a + g * (b - a)
                f2 = sign * f(x2)
        out.append(sign * max(sign * vals[j], f1, f2))
    return out[0], out[1]


def flange_lambda(c_over_l):
    """教材表 2-4-5 直线内插；表外按端值。"""
    t = FLANGE_LAMBDA
    if c_over_l <= t[0][0]:
        return t[0][1]
    for (x0, y0), (x1, y1) in zip(t, t[1:]):
        if c_over_l <= x1:
            return y0 + (y1 - y0) * (c_over_l - x0) / (x1 - x0)
    return t[-1][1]


def gm_plate(Ix, ITx_rib, b, h1, a, delta, hd, l_cross, B, span, cw=1.0, G=None):
    """G-M 法的参数（教材例 2-4-6 的算法，单位统一即可）：主梁抗弯 Ix、梁肋抗扭 I'Tx、主梁间距 b；
    桥面板平均厚 h1、横隔梁间距 a、横隔梁肋厚 δ、横隔梁全高 hd（桥面顶到梁底）、横隔梁计算长度 l'（两边梁中心距）；
    承重结构半宽 B、跨径 l。连续梁按等代简支梁：Jx 乘 C_w（θ' = ⁴√C_w·θ，α' = α / √C_w，教材式 2-6-8、2-6-9）。"""
    G = C.G_RATIO if G is None else G
    c = (a - delta) / 2
    lam = flange_lambda(c / l_cross) * c
    Af, Aw = 2 * lam * h1, delta * hd
    ay = (Af * h1 / 2 + Aw * hd / 2) / (Af + Aw)
    Iy = 2 * lam * h1 ** 3 / 12 + Af * (ay - h1 / 2) ** 2 + delta * hd ** 3 / 12 + Aw * (hd / 2 - ay) ** 2
    Jx, Jy = Ix / b, Iy / a
    ITy_rib = _c_torsion(hd - h1, delta) * (hd - h1) * delta ** 3
    JT = h1 ** 3 / 3 + ITx_rib / b + ITy_rib / a            # 教材式 2-4-79
    theta = B / span * (cw * Jx / Jy) ** 0.25
    alpha = G * JT / (2 * math.sqrt(Jx * Jy)) / math.sqrt(cw)
    return {"theta": theta, "alpha": alpha, "Jx": Jx, "Jy": Jy, "JT": JT, "Iy": Iy, "lambda": lam, "h1": h1,
            "a": a, "B": B, "span": span, "cw": cw}


def gm_parameters(secs, span, cw=1.0):
    """本桥的 G-M 参数，全部取自截面与 BIM 的构造尺寸：Ix 取各梁组合截面的平均，梁肋 = 腹板 + 马蹄，
    桥面板平均厚 = 中梁组合截面腹板顶以上的面积 / 梁距；中横隔板厚 DIAPHRAGM_T、底面在 DIAPHRAGM_BOTTOM，
    间距按 1/4、1/2、3/4 跨布置取最小净距；承重结构半宽 = 梁数 × 梁距 / 2。"""
    offs = sorted(C.GIRDER_OFFSETS)
    b = (offs[-1] - offs[0]) / (len(offs) - 1)
    mid = secs[(N_G + 1) // 2]
    poly = composite_polygon(mid["left"], mid["right"])
    y_top = max(p[1] for p in C.T_SECTION if abs(abs(p[0]) - C.WEB_HALF) < 1e-9)
    h1 = polygon_props(_clip_below(poly, y_top))[0] / (mid["left"] + mid["right"])
    fr = sorted(C.DIAPHRAGM_FRACTIONS)
    a = span * min(y - x for x, y in zip([0.0] + fr, fr + [1.0]))
    Ix = sum(s["I"] for s in secs.values()) / N_G
    rib = _rect_torsion(torsion_parts(C.T_SECTION)[1:])
    return gm_plate(Ix, rib, b, h1, a, C.DIAPHRAGM_T, -C.DIAPHRAGM_BOTTOM, offs[-1] - offs[0], N_G * b / 2, span, cw)


def distribution(span=None, cw=1.0):
    """一幅桥的横向分布。返回 (截面, β, {梁位: 系数}, 概况)。系数：
    ecc_max / ecc_min（修正偏心压力法，分到最多 / 最少，后者 ≤ 0）、gm_max / gm_min（G-M 法）、
    mc_max / mc_min（采用值：θ' ≤ 0.3 取修正偏心压力法；θ' > 0.3 取两法的大值 / 小值）、
    m0_max / m0_min（杠杆原理法），rigid_max（β = 1，即不计主梁抗扭，作对照），以及取到各极值的车列数（0 = 不布载）。
    cw：等代简支梁的抗弯刚度修正系数（简支梁取 1）。"""
    span = span or C.SPAN
    secs = girder_sections()
    beta = torsion_beta(secs, span, cw)
    gm = gm_parameters(secs, span, cw)
    half = C.DECK_WIDTH / 2 - C.BARRIER_W
    lanes = lanes_for_width(2 * half)
    anchors = [s["a"] for s in secs.values()]
    envelope = GM_ENVELOPE and gm["theta"] > THETA_NARROW
    out = {}
    for k in secs:
        il0 = gm_influence(gm["theta"], 0.0, secs[k]["a"] / gm["B"])
        il1 = gm_influence(gm["theta"], 1.0, secs[k]["a"] / gm["B"])
        sa = math.sqrt(gm["alpha"])

        def gm_eta(e):                                   # 教材：Kα = K0 + (K1 − K0)·√α，η = Kα / n
            k0, k1 = il0(e / gm["B"]), il1(e / gm["B"])
            return (k0 + (k1 - k0) * sa) / N_G
        vals = {"ecc": [(0.0, 0)], "gm": [(0.0, 0)], "m0": [(0.0, 0)], "rigid": [(0.0, 0)]}   # 不布载也是一种布置
        for n in range(1, lanes + 1):
            f = C.LANE_FACTORS[n] * 0.5
            for wheels in wheel_layouts(n, -half, half, anchors):
                vals["ecc"].append((f * sum(eccentric_eta(secs, beta, k, e) for e in wheels), n))
                vals["m0"].append((f * sum(lever_eta(secs, k, e) for e in wheels), n))
                vals["rigid"].append((f * sum(eccentric_eta(secs, 1.0, k, e) for e in wheels), n))
            rel = wheel_offsets(n)
            lo, hi = -half + C.WHEEL_TO_CURB, half - C.WHEEL_TO_CURB - rel[-1]
            if hi >= lo - 1e-9:
                g_hi, g_lo = smooth_extremes(gm_eta, rel, lo, max(lo, hi))
                vals["gm"] += [(f * g_hi, n), (f * g_lo, n)]
        row = {}
        for key, v in vals.items():
            (row[key + "_max"], row[key + "_max_lanes"]), (row[key + "_min"], row[key + "_min_lanes"]) = max(v), min(v)
        use_gm_max = envelope and row["gm_max"] > row["ecc_max"]
        use_gm_min = envelope and row["gm_min"] < row["ecc_min"]
        row["mc_max"], row["mc_max_lanes"] = (row["gm_max"], row["gm_max_lanes"]) if use_gm_max else (row["ecc_max"], row["ecc_max_lanes"])
        row["mc_min"], row["mc_min_lanes"] = (row["gm_min"], row["gm_min_lanes"]) if use_gm_min else (row["ecc_min"], row["ecc_min_lanes"])
        row["mc_max_method"] = "G-M" if use_gm_max else "偏心压力"
        out[k] = row
    return secs, beta, out, {"lanes": lanes, "roadway": 2 * half, "gm": gm, "envelope": envelope}


# ====================================================================== 平面梁：直接刚度法
class Beam:
    """平面欧拉梁：节点坐标 xs（升序），每单元抗弯刚度 EI、线质量 mass（t/m），节点集中质量 point_mass
    （{节点: t}）；supports 为竖向固定的节点号。每节点两个自由度（竖向位移 w 向上为正、转角 θ）。
    约束自由度直接删去，剩下的刚度矩阵半带宽 3。"""

    def __init__(self, xs, EI, supports, mass=None, point_mass=None, inner_mass=None):
        self.xs = list(xs)
        n = len(xs)
        self.EI = list(EI) if isinstance(EI, (list, tuple)) else [EI] * (n - 1)
        self.mass = None if mass is None else (list(mass) if isinstance(mass, (list, tuple)) else [mass] * (n - 1))
        self.supports = sorted(set(supports))
        fixed = {2 * s for s in self.supports}
        self.free = [d for d in range(2 * n) if d not in fixed]
        self.pos = {d: i for i, d in enumerate(self.free)}
        self.nf = len(self.free)
        self.K = self._assemble(self._k)
        self.L = _band_cholesky(self.K, self.nf, 3)
        self.M = None
        if self.mass is not None:
            self.M = self._assemble(self._m)
            for node, m in (point_mass or {}).items():
                i = self.pos.get(2 * node)
                if i is not None:
                    self.M[i][0] += m
            for x, m in (inner_mass or []):                  # 单元内的集中质量：一致质量 m·NᵀN
                e, N = self.shape(x)
                dofs = [self.pos.get(2 * e + a) for a in range(4)]
                for a in range(4):
                    for b in range(4):
                        if dofs[a] is not None and dofs[b] is not None and dofs[b] <= dofs[a]:
                            self.M[dofs[a]][dofs[a] - dofs[b]] += m * N[a] * N[b]

    def _k(self, e):
        l = self.xs[e + 1] - self.xs[e]
        a = self.EI[e] / l ** 3
        return [[12 * a, 6 * l * a, -12 * a, 6 * l * a], [6 * l * a, 4 * l * l * a, -6 * l * a, 2 * l * l * a],
                [-12 * a, -6 * l * a, 12 * a, -6 * l * a], [6 * l * a, 2 * l * l * a, -6 * l * a, 4 * l * l * a]]

    def _m(self, e):
        l = self.xs[e + 1] - self.xs[e]
        c = self.mass[e] * l / 420.0
        return [[156 * c, 22 * l * c, 54 * c, -13 * l * c], [22 * l * c, 4 * l * l * c, 13 * l * c, -3 * l * l * c],
                [54 * c, 13 * l * c, 156 * c, -22 * l * c], [-13 * l * c, -3 * l * l * c, -22 * l * c, 4 * l * l * c]]

    def _assemble(self, fn):
        band = [[0.0] * 4 for _ in range(self.nf)]          # band[i][j] = A[i][i - j]，j = 0..3
        for e in range(len(self.xs) - 1):
            ke = fn(e)
            dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
            for a in range(4):
                ia = self.pos.get(dofs[a])
                if ia is None:
                    continue
                for b in range(4):
                    ib = self.pos.get(dofs[b])
                    if ib is None or ib > ia:
                        continue
                    band[ia][ia - ib] += ke[a][b]
        return band

    # ------------------------------------------------------------------ 荷载
    def _element_at(self, x):
        return max(0, min(len(self.xs) - 2, bisect.bisect_right(self.xs, x) - 1))

    def shape(self, x):
        """x 所在单元号与该处的 Hermite 形函数值（对应 w1、θ1、w2、θ2）。"""
        e = self._element_at(x)
        l = self.xs[e + 1] - self.xs[e]
        t = (x - self.xs[e]) / l
        return e, (1 - 3 * t * t + 2 * t ** 3, l * t * (1 - t) ** 2, 3 * t * t - 2 * t ** 3, l * t * t * (t - 1))

    def _fixed_end(self, x, P):
        """单元内距左端 a 处向下集中力 P 的固端反力（向上、逆时针为正）：(单元号, [R1, M1, R2, M2])。"""
        e = self._element_at(x)
        l = self.xs[e + 1] - self.xs[e]
        a = x - self.xs[e]
        b = l - a
        return e, [P * b * b * (3 * a + b) / l ** 3, P * a * b * b / l ** 2, P * a * a * (a + 3 * b) / l ** 3,
                   -P * a * a * b / l ** 2]

    def load_vector(self, udl=None, points=None, inner=None):
        """udl：每单元均布荷载（kN/m，向下为正）；points：[(节点号, 向下的力 kN)]；
        inner：[(坐标, 向下的力 kN)]，力在单元内任意位置，按一致荷载分到两端节点。返回全自由度荷载向量。"""
        F = [0.0] * (2 * len(self.xs))
        for x, P in (inner or []):
            e, r = self._fixed_end(x, P)
            for a in range(4):
                F[2 * e + a] -= r[a]
        if udl is not None:
            for e, q in enumerate(udl):
                if q == 0.0:
                    continue
                l = self.xs[e + 1] - self.xs[e]
                F[2 * e] -= q * l / 2
                F[2 * e + 1] -= q * l * l / 12
                F[2 * e + 2] -= q * l / 2
                F[2 * e + 3] += q * l * l / 12
        for node, p in (points or []):
            F[2 * node] -= p
        return F

    def solve(self, F):
        """返回全自由度位移向量。"""
        b = [F[d] for d in self.free]
        x = _band_solve(self.L, b, 3)
        u = [0.0] * (2 * len(self.xs))
        for i, d in enumerate(self.free):
            u[d] = x[i]
        return u

    def results(self, u, udl=None, points=None, inner=None):
        """节点弯矩（下缘受拉为正）、各单元左右端剪力、支座反力（向上为正）。"""
        n = len(self.xs)
        M = [0.0] * n
        V = []
        R = {s: 0.0 for s in self.supports}
        fe = {}
        for x, P in (inner or []):
            e, r = self._fixed_end(x, P)
            fe[e] = [a + b for a, b in zip(fe.get(e, [0.0] * 4), r)]
        for e in range(n - 1):
            l = self.xs[e + 1] - self.xs[e]
            ke = self._k(e)
            ue = u[2 * e:2 * e + 4]
            f = [sum(ke[a][b] * ue[b] for b in range(4)) for a in range(4)]
            q = udl[e] if udl is not None else 0.0
            f[0] += q * l / 2
            f[1] += q * l * l / 12
            f[2] += q * l / 2
            f[3] -= q * l * l / 12
            for a, r in enumerate(fe.get(e, ())):
                f[a] += r
            M[e] = -f[1] if e == 0 else M[e]
            M[e + 1] = f[3]
            V.append((f[0], -f[2]))
            if e in R:
                R[e] += f[0]
            if e + 1 in R:
                R[e + 1] += f[2]
        for node, p in (points or []):
            if node in R:
                R[node] += p
        return M, V, R

    def influence(self):
        """单位竖向力（向下）逐个放在每个节点上。返回 (IM, IV, IR, IW)：
        IM[j][p] 节点 j 弯矩、IV[e][p] 单元 e 剪力（单元内为常数）、IR[s][p] 支座 s 反力、
        IW[j][p] 节点 j 挠度（向下为正）——都是力在节点 p 时的值，即以 p 为自变量的影响线。"""
        n = len(self.xs)
        ne = n - 1
        ls = [self.xs[e + 1] - self.xs[e] for e in range(ne)]
        As = [self.EI[e] / ls[e] ** 3 for e in range(ne)]
        IM = [[0.0] * n for _ in range(n)]
        IV = [[0.0] * n for _ in range(ne)]
        IW = [[0.0] * n for _ in range(n)]
        IR = {s: [0.0] * n for s in self.supports}
        sup = set(self.supports)
        for p in range(n):
            if p in sup:
                IR[p][p] = 1.0
                continue
            F = [0.0] * (2 * n)
            F[2 * p] = -1.0
            u = self.solve(F)
            V = [0.0] * ne
            for e in range(ne):
                l, a = ls[e], As[e]
                w1, t1, w2, t2 = u[2 * e], u[2 * e + 1], u[2 * e + 2], u[2 * e + 3]
                V[e] = a * (12 * w1 + 6 * l * t1 - 12 * w2 + 6 * l * t2)
                IV[e][p] = V[e]
                IM[e + 1][p] = a * l * (6 * w1 + 2 * l * t1 - 6 * w2 + 4 * l * t2)
            IM[0][p] = -As[0] * ls[0] * (6 * u[0] + 4 * ls[0] * u[1] - 6 * u[2] + 2 * ls[0] * u[3])
            for j in range(n):
                IW[j][p] = -u[2 * j]
            for s in self.supports:                     # 节点平衡：R = −V左 + V右（力不在支座上）
                IR[s][p] = (V[s] if s < ne else 0.0) - (V[s - 1] if s > 0 else 0.0)
        return IM, IV, IR, IW

    # ------------------------------------------------------------------ 自振
    def frequencies(self, count=2, iters=200):
        """子空间迭代求前 count 阶竖向自振频率（Hz）。"""
        p = min(self.nf, max(2 * count, count + 4))
        X = []
        for j in range(p):
            X.append([math.sin((i + 1) * (j + 1) * math.pi / (self.nf + 1)) + (0.001 * (i % (j + 2))) for i in range(self.nf)])
        lam = None
        for _ in range(iters):
            MX = [_band_mul(self.M, x, 3) for x in X]
            Y = [_band_solve(self.L, mx, 3) for mx in MX]           # K Y = M X，所以 Yᵀ K Y = Yᵀ M X
            MY = [_band_mul(self.M, y, 3) for y in Y]
            Kr = [[_dot(Y[a], MX[b]) for b in range(p)] for a in range(p)]
            Mr = [[_dot(Y[a], MY[b]) for b in range(p)] for a in range(p)]
            for a in range(p):                                      # 消掉舍入造成的不对称
                for b in range(a):
                    Kr[a][b] = Kr[b][a] = (Kr[a][b] + Kr[b][a]) / 2
                    Mr[a][b] = Mr[b][a] = (Mr[a][b] + Mr[b][a]) / 2
            vals, vecs = _gen_eig(Kr, Mr)
            X = []
            for c in range(p):
                xc = [0.0] * self.nf
                for b in range(p):
                    vb = vecs[c][b]
                    xc = [s + vb * y for s, y in zip(xc, Y[b])]
                X.append(xc)
            if lam is not None and all(abs(vals[i] - lam[i]) <= 1e-13 * abs(vals[i]) for i in range(count)):
                lam = vals
                break
            lam = vals
        return [math.sqrt(v) / (2 * math.pi) for v in lam[:count]]


def _band_cholesky(A, n, bw):
    """对称正定带状矩阵的 Cholesky 分解 A = L Lᵀ；A[i][j] 存 A(i, i−j)。"""
    L = [[0.0] * (bw + 1) for _ in range(n)]
    for i in range(n):
        for j in range(max(0, i - bw), i + 1):
            s = A[i][i - j] if i - j <= bw else 0.0
            for k in range(max(0, i - bw, j - bw), j):
                s -= L[i][i - k] * L[j][j - k]
            if i == j:
                if s <= 0:
                    raise ValueError("刚度矩阵非正定：结构是机构（支座不够）")
                L[i][0] = math.sqrt(s)
            else:
                L[i][i - j] = s / L[j][0]
    return L


def _band_solve(L, b, bw):
    n = len(b)
    y = [0.0] * n
    for i in range(n):
        s = b[i]
        for k in range(max(0, i - bw), i):
            s -= L[i][i - k] * y[k]
        y[i] = s / L[i][0]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = y[i]
        for k in range(i + 1, min(n, i + bw + 1)):
            s -= L[k][k - i] * x[k]
        x[i] = s / L[i][0]
    return x


def _band_mul(A, x, bw):
    n = len(x)
    y = [0.0] * n
    for i in range(n):
        for j in range(max(0, i - bw), i + 1):
            a = A[i][i - j]
            y[i] += a * x[j]
            if j != i:
                y[j] += a * x[i]
    return y


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _gen_eig(K, M):
    """小规模广义特征值问题 K v = λ M v（Cholesky 化成标准问题后用 Jacobi 旋转），按 λ 升序。"""
    n = len(K)
    Lm = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = M[i][j] - sum(Lm[i][k] * Lm[j][k] for k in range(j))
            Lm[i][j] = math.sqrt(s) if i == j else s / Lm[j][j]
    inv = [[0.0] * n for _ in range(n)]
    for c in range(n):
        for i in range(n):
            s = (1.0 if i == c else 0.0) - sum(Lm[i][k] * inv[k][c] for k in range(i))
            inv[i][c] = s / Lm[i][i]
    A = [[sum(inv[i][a] * K[a][b] * inv[j][b] for a in range(n) for b in range(n)) for j in range(n)] for i in range(n)]
    V = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(100):
        off = sum(A[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-30 * max(1.0, sum(A[i][i] ** 2 for i in range(n))):
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(A[p][q]) < 1e-300:
                    continue
                th = 0.5 * math.atan2(2 * A[p][q], A[q][q] - A[p][p])
                c, s = math.cos(th), math.sin(th)
                for k in range(n):
                    akp, akq = A[k][p], A[k][q]
                    A[k][p], A[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = A[p][k], A[q][k]
                    A[p][k], A[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = V[k][p], V[k][q]
                    V[k][p], V[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    order = sorted(range(n), key=lambda i: A[i][i])
    vals = [A[i][i] for i in order]
    vecs = [[sum(inv[b][a] * V[b][i] for b in range(n)) for a in range(n)] for i in order]   # 回到原坐标：v = L⁻ᵀ y
    return vals, vecs


# ====================================================================== 规范函数
def pk(span):
    """公路-I级车道荷载集中力（kN）：L0 ≤ 5 m 取 270，≥ 50 m 取 360，中间直线内插（= 2(L0 + 130)）。"""
    if span <= 5.0:
        return C.PK_SHORT
    if span >= 50.0:
        return C.PK_LONG
    return C.PK_SHORT + (C.PK_LONG - C.PK_SHORT) * (span - 5.0) / 45.0


def negative_moment_mode(n_spans):
    """负弯矩冲击系数用的竖弯振型阶数：n 跨一联的第一频带有 n 个振型，取最高那阶（三跨取第三阶，同文献）。"""
    return n_spans


def impact(f):
    """冲击系数 μ（JTG D60-2015 式 4.3.2）。"""
    if f < 1.5:
        return 0.05
    if f > 14.0:
        return 0.45
    return 0.1767 * math.log(f) - 0.0157


def lane_effects(il, xs, q, p):
    """影响线加载，返回 (正效应, 负效应)：均布 q 布满同号区段（梯形积分，变号处按线性插值切开），
    集中力 p 放在该号最大竖标处。xs 可以有重复点（剪力影响线在截面处的跳跃）。"""
    pos = neg = 0.0
    a = il[0]
    for i in range(1, len(il)):
        b = il[i]
        l = xs[i] - xs[i - 1]
        if a >= 0.0 and b >= 0.0:
            pos += (a + b) * l
        elif a <= 0.0 and b <= 0.0:
            neg += (a + b) * l
        else:
            z = l * a / (a - b)
            if a > 0.0:
                pos += a * z
                neg += b * (l - z)
            else:
                neg += a * z
                pos += b * (l - z)
        a = b
    return q * pos / 2 + p * max(max(il), 0.0), q * neg / 2 + p * min(min(il), 0.0)


# ====================================================================== 梁位线：从 BIM 构件取几何与荷载
def _plan(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def continuity_shares(cs):
    """墩顶连续段按梁位分体积：本梁位那块 + 两侧梁间横梁各一半 + 边梁外侧的桥面板。
    分块顺序见 model.build：先 N_G 块梁位，再 N_G − 1 块梁间横梁，最后 1 号梁外侧、N_G 号梁外侧。"""
    vols = [MD.solid_volume(s) for s in cs.params["solids"]]
    if len(vols) != 2 * N_G + 1:
        raise ValueError("%s 有 %d 块实体，应为 %d（model.build 里连续段的分块变了）" % (cs.eid, len(vols), 2 * N_G + 1))
    own, between, edge = vols[:N_G], vols[N_G:2 * N_G - 1], vols[2 * N_G - 1:]
    out = []
    for i in range(N_G):
        v = own[i] + (between[i - 1] / 2 if i > 0 else 0.0) + (between[i] / 2 if i < N_G - 1 else 0.0)
        out.append(v + (edge[0] if i == 0 else edge[1] if i == N_G - 1 else 0.0))
    return out


MIN_KEY_GAP = 0.20   # m：关键点（梁端、支座、连续段端）之间的最小距离


def _mesh(keys, hmax):
    """关键点之间等分，单元长不超过 hmax。横隔板集中力不占节点，按一致荷载加在单元内——
    若把它也当节点，会和几毫米外的支座挤出毫米级的短单元，刚度矩阵病态（实测过：平衡误差到 1e-4）。"""
    ks = sorted(set(keys))
    if any(b - a < MIN_KEY_GAP for a, b in zip(ks, ks[1:])):
        raise ValueError("两个关键点相距不到 %.2f m" % MIN_KEY_GAP)
    out = [ks[0]]
    for x in ks[1:]:
        x0 = out[-1]
        n = max(1, int(math.ceil((x - x0) / hmax - 1e-9)))
        out += [x0 + (x - x0) * j / n for j in range(1, n)] + [x]
    return out


def girder_line(by, cs_vol, d, unit, i):
    """一条梁位线（幅 d、第 unit 联、梁位 i）的一维模型。坐标是沿梁轴的平面长度，自该联起端梁端起算。
    返回 dict：节点 xs、分段、预制梁、永久支座、临时支座、各单元刚度 / 荷载 / 质量。"""
    s0, s1 = MD.unit_bounds()[unit - 1]
    x = 0.0
    segs, girders, perm, temps = [], [], [], []
    prev = None
    for k in range(s0 + 1, s1 + 1):
        g = by["G-%s%02d-%d" % (d, k, i)]
        p0, p1 = g.params["p0"], g.params["p1"]
        if prev is not None:                           # 墩顶连续段：上一片梁 b 端到这片梁 a 端
            jl = _plan(prev.params["p1"], p0)
            hb, ha = prev.attrs["half_b"], g.attrs["half_a"]
            bid = "B-%s-%s%d" % (MD.support_name(k - 1), d, i)
            perm.append({"id": bid, "x": x + jl * hb / (hb + ha), "support": k - 1, "kind": "cont",
                         "cs": cs_vol[(k - 1, d)][i - 1] * C.UNIT_RC, "plan": bearing_plan(by[bid])})
            segs.append({"kind": "joint", "x0": x, "x1": x + jl, "support": k - 1, "w": cs_vol[(k - 1, d)][i - 1]
                         * C.UNIT_RC / jl})
            x += jl
        lp = g.attrs["plan_length"]
        xa, xb = x, x + lp
        vol = g.volume
        if i > 1:
            vol += by["WJ-%s%02d-%d" % (d, k, i - 1)].volume / 2
        if i < N_G:
            vol += by["WJ-%s%02d-%d" % (d, k, i)].volume / 2
        if i == 1:
            vol += by["CT-%s%02d-1" % (d, k)].volume
        if i == N_G:
            vol += by["CT-%s%02d-2" % (d, k)].volume
        dia = []
        for pair in (i - 1, i):
            if 1 <= pair < N_G:
                for j in range(1, N_DIAPHRAGMS + 1):
                    e = by["D-%s%02d-%d%d" % (d, k, pair, j)]
                    c = MD._flange_edge_at(p0, p1, e.attrs["station"])
                    dia.append({"x": xa + _plan(p0, c), "P": e.volume * C.UNIT_RC / 2, "eid": e.eid})
        sup1 = []
        for end, xe, sgn in (("a", xa, 1.0), ("b", xb, -1.0)):
            s_k = k - 1 if end == "a" else k
            if g.attrs["end_" + end] == "E":
                sid = "B-%s%02d-%d%s" % (d, k, i, end)
                perm.append({"id": sid, "x": xe + sgn * C.BEARING_INSET, "support": s_k, "kind": "end", "cs": 0.0,
                             "plan": bearing_plan(by[sid])})
                sup1.append((sid, xe + sgn * C.BEARING_INSET))
            else:
                sid = "TS-%s%02d-%d%s" % (d, k, i, end)
                temps.append({"id": sid, "x": xe + sgn * C.TEMP_INSET, "support": s_k, "girder": g.eid})
                sup1.append((sid, xe + sgn * C.TEMP_INSET))
        g2 = (by["PV-%s%02d" % (d, k)].volume * PAVEMENT_UNIT
              + (by["BR-%s%02d-1" % (d, k)].volume + by["BR-%s%02d-2" % (d, k)].volume) * C.UNIT_RC) / N_G
        girders.append({"eid": g.eid, "span": k, "xa": xa, "xb": xb, "w1": vol * C.UNIT_RC / lp, "g1": vol * C.UNIT_RC,
                        "dia": dia, "sup": sup1, "g2": g2})
        segs.append({"kind": "girder", "x0": xa, "x1": xb, "eid": g.eid})
        x = xb
        prev = g
    perm.sort(key=lambda b: b["x"])
    x_end = x
    # 连续梁的各跨（永久支座之间）与二期恒载的分布区段（伸缩端到梁端，连续墩处以支座为界）
    spans = [(perm[m]["x"], perm[m + 1]["x"]) for m in range(len(perm) - 1)]
    bounds = [0.0] + [b["x"] for b in perm[1:-1]] + [x_end]
    regions = [(bounds[m], bounds[m + 1], girders[m]["g2"] / (bounds[m + 1] - bounds[m])) for m in range(len(girders))]
    keys = [0.0, x_end] + [s[k_] for s in segs for k_ in ("x0", "x1")] + [b["x"] for b in perm + temps]
    xs = _mesh(keys, C.BEAM_ELEMENT)

    def node(xv, tol=1e-9):
        j = min(range(len(xs)), key=lambda i: abs(xs[i] - xv))
        if abs(xs[j] - xv) > tol:
            raise ValueError("%.6f 离最近的节点 %.6f 太远" % (xv, xs[j]))
        return j

    sec = girder_sections()[i]
    ne = len(xs) - 1
    kind, w1, w2, wj = [], [], [], []
    for e in range(ne):
        xm = (xs[e] + xs[e + 1]) / 2
        s = next(s for s in segs if s["x0"] <= xm <= s["x1"])
        kind.append(s["kind"])
        g = next((g for g in girders if g["eid"] == s.get("eid")), None)
        w1.append(g["w1"] if g else 0.0)
        wj.append(s["w"] if s["kind"] == "joint" else 0.0)
        w2.append(next(r[2] for r in regions if r[0] <= xm <= r[1]))
    for g in girders:
        g["ia"], g["ib"] = node(g["xa"]), node(g["xb"])
        g["sup_nodes"] = [(sid, node(xv)) for sid, xv in g["sup"]]
    for b in perm + temps:
        b["node"] = node(b["x"])
    return {"deck": d, "unit": unit, "line": i, "spans_k": (s0 + 1, s1), "xs": xs, "segs": segs, "girders": girders,
            "perm": perm, "temps": temps, "spans": spans, "span_nodes": [(node(a), node(b)) for a, b in spans],
            "kind": kind, "w1": w1, "w2": w2, "wj": wj, "sec": sec,
            "EI2": [C.E_C50 * (sec["I"] if k_ == "girder" else sec["Ij"]) for k_ in kind]}


def bearing_plan(e):
    """支座平面：("circle", 直径) 或 ("rect", 顺桥向, 横桥向)，直接取 BIM 构件的几何。"""
    return ("circle", 2 * e.params["r"]) if e.shape == "cyl" else ("rect", e.params["w"], e.params["l"])


def effective_area(plan):
    """有效承压面积 = 加劲钢板面积：每边扣掉 BEARING_COVER 的橡胶保护层。"""
    c = 2 * C.BEARING_COVER
    if plan[0] == "circle":
        return math.pi * (plan[1] - c) ** 2 / 4
    return (plan[1] - c) * (plan[2] - c)


def required_size(plan, rck):
    """满足 Rck / Ae ≤ σc 的最小尺寸：圆形给直径；矩形保持横桥向尺寸，给顺桥向尺寸。"""
    c = 2 * C.BEARING_COVER
    a_need = rck / (C.SIGMA_C * 1000.0)
    if plan[0] == "circle":
        return 2 * math.sqrt(a_need / math.pi) + c
    return a_need / (plan[2] - c) + c


def girder_lines(els, only=None):
    """全部梁位线；only 为 [(幅, 联, 梁位)] 时只取这几条（测试里用）。"""
    by = {e.eid: e for e in els}
    cs_vol = {(e.attrs["support"], e.deck): continuity_shares(e) for e in els if e.cls == "continuity"}
    keys = [(d, u, i) for d, _ in C.DECKS for u in range(1, len(C.UNITS) + 1) for i in range(1, N_G + 1)]
    return [girder_line(by, cs_vol, *k) for k in keys if only is None or k in only]


def stage_one_beam(L, g):
    """阶段一：一片预制梁，两端支承（伸缩端永久支座 / 连续端临时支座）、预制截面。返回 (梁, 均布, 单元内集中力)。"""
    ia, ib = g["ia"], g["ib"]
    beam = Beam(L["xs"][ia:ib + 1], C.E_C50 * L["sec"]["I0"], [nd - ia for _, nd in g["sup_nodes"]])
    return beam, [g["w1"]] * (ib - ia), [(q["x"], q["P"]) for q in g["dia"]]


def continuous_beam(L, with_mass=True):
    """体系转换后的整联连续梁：永久支座、组合截面（连续段为实心截面），质量取一、二期恒载与连续段。"""
    supports = [b["node"] for b in L["perm"]]
    if not with_mass:
        return Beam(L["xs"], L["EI2"], supports)
    ne = len(L["xs"]) - 1
    return Beam(L["xs"], L["EI2"], supports, mass=[(L["w1"][e] + L["w2"][e] + L["wj"][e]) / G_ACC for e in range(ne)],
                inner_mass=[(q["x"], q["P"] / G_ACC) for g in L["girders"] for q in g["dia"]])


# ====================================================================== 分析
def _jump(xs, il, j, side):
    """剪力影响线在截面 j 处的跳跃：力从截面左侧移到右侧，截面右侧剪力 +1。
    side = "R"：截面在节点右侧，节点处的力算在截面左边；"L"：截面在节点左侧，节点处的力算在截面右边。"""
    v = il[j]
    pair = [v, v + 1.0] if side == "R" else [v - 1.0, v]
    return xs[:j] + [xs[j], xs[j]] + xs[j + 1:], il[:j] + pair + il[j + 1:]


def _weights(L, m0, mc):
    """剪力、反力用的横向分布系数沿梁长的分布：支座处 m0，到 1/4 跨线性过渡为 mc；伸缩端外伸段取 m0。"""
    out = []
    for x in L["xs"]:
        m = m0
        for a, b in L["spans"]:
            if a <= x <= b:
                q = (b - a) / 4
                t = min(x - a, b - x) / q
                m = mc if t >= 1.0 else m0 + (mc - m0) * t
                break
        out.append(m)
    return out


def _combine_uls(g, q_pos, q_neg, mu_pos, mu_neg):
    """基本组合（含 γ0）：返回 (最大值, 最小值)。结构重力对该方向有利时分项系数取 1.0。"""
    hi = C.GAMMA_0 * ((C.GAMMA_G if g > 0 else C.GAMMA_G_FAV) * g + C.GAMMA_Q1 * (1 + mu_pos) * q_pos)
    lo = C.GAMMA_0 * ((C.GAMMA_G if g < 0 else C.GAMMA_G_FAV) * g + C.GAMMA_Q1 * (1 + mu_neg) * q_neg)
    return hi, lo


def analyse_line(L, coef):
    """一条梁位线：三个施工阶段的恒载内力、汽车荷载包络、组合、挠度、频率与冲击系数、支座反力。"""
    xs = L["xs"]
    n, ne = len(xs), len(xs) - 1
    # ---------------------------------------------------------------- ① 一期恒载：每片预制梁简支
    M1, VL1, VR1, R1, TH1 = [0.0] * n, [0.0] * n, [0.0] * n, {}, {}
    for g in L["girders"]:
        ia = g["ia"]
        sup = [nd - ia for _, nd in g["sup_nodes"]]
        beam, udl, inner = stage_one_beam(L, g)
        u1 = beam.solve(beam.load_vector(udl, None, inner))
        M, V, R = beam.results(u1, udl, None, inner)
        for j, m in enumerate(M):
            M1[ia + j] = m
        for e, (vl, vr) in enumerate(V):
            VR1[ia + e], VL1[ia + e + 1] = vl, vr
        for (sid, nd), s in zip(g["sup_nodes"], sup):
            R1[sid] = R[s]
            TH1[sid] = u1[2 * s + 1]                     # 支座处转角（逆时针为正）
    # ---------------------------------------------------------------- 连续梁（永久支座）
    B2 = continuous_beam(L)
    # ② 体系转换：临时支座反力反向加到连续梁上
    pts = [(t["node"], R1[t["id"]]) for t in L["temps"]]
    uc = B2.solve(B2.load_vector(None, pts))
    Mc, Vc, Rc = B2.results(uc, None, pts)
    # ③ 二期恒载
    u2 = B2.solve(B2.load_vector(L["w2"]))
    M2, V2, R2 = B2.results(u2, L["w2"])
    MG = [M1[j] + Mc[j] + M2[j] for j in range(n)]
    VRG = [VR1[j] + (Vc[j][0] + V2[j][0] if j < ne else 0.0) for j in range(n)]
    VLG = [VL1[j] + (Vc[j - 1][1] + V2[j - 1][1] if j > 0 else 0.0) for j in range(n)]
    # ---------------------------------------------------------------- 频率与冲击系数
    n_sp = negative_moment_mode(len(L["spans"]))
    fs = B2.frequencies(max(n_sp, 2))
    f1, f_neg = fs[0], fs[n_sp - 1]                  # 负弯矩：第一频带最高一阶（n 跨一联的第 n 阶）
    mu_pos, mu_neg = impact(f1), impact(f_neg)
    # ---------------------------------------------------------------- 汽车荷载影响线加载
    IM, IV, IR, IW = B2.influence()
    L0 = max(b - a for a, b in L["spans"])
    P_m, P_v, q = pk(L0), pk(L0) * C.PK_SHEAR_FACTOR, C.Q_K
    c = coef[L["line"]]
    mc_hi, mc_lo = c["mc_max"], c["mc_min"]
    w_hi, w_lo = _weights(L, c["m0_max"], mc_hi), _weights(L, c["m0_min"], mc_lo)
    MQp, MQn, WQ = [0.0] * n, [0.0] * n, [0.0] * n
    for j in range(n):
        ep, en = lane_effects(IM[j], xs, q, P_m)
        MQp[j] = max(mc_hi * ep, mc_lo * en)
        MQn[j] = min(mc_hi * en, mc_lo * ep)
        WQ[j] = mc_hi * lane_effects(IW[j], xs, q, P_m)[0]

    def shear(e, j, side):
        xj, il = _jump(xs, IV[e], j, side)
        hi = lo = 0.0
        for w in (w_hi, w_lo):
            wj = w[:j] + [w[j], w[j]] + w[j + 1:]         # 权重在节点处不跳，复制一份与影响线对齐
            ep, en = lane_effects([a * b for a, b in zip(wj, il)], xj, q, P_v)
            hi, lo = max(hi, ep), min(lo, en)
        return hi, lo

    VRQ = [shear(j, j, "R") if j < ne else (0.0, 0.0) for j in range(n)]
    VLQ = [shear(j - 1, j, "L") if j > 0 else (0.0, 0.0) for j in range(n)]
    # ---------------------------------------------------------------- 组合
    Mud_p, Mud_n = [], []
    for j in range(n):
        hi, lo = _combine_uls(MG[j], MQp[j], MQn[j], mu_pos, mu_neg)
        Mud_p.append(hi)
        Mud_n.append(lo)
    VudR = [max(abs(v) for v in _combine_uls(VRG[j], VRQ[j][0], VRQ[j][1], mu_pos, mu_pos)) for j in range(n)]
    VudL = [max(abs(v) for v in _combine_uls(VLG[j], VLQ[j][0], VLQ[j][1], mu_pos, mu_pos)) for j in range(n)]
    Vud = [max(a, b) for a, b in zip(VudR, VudL)]
    Mfd_p = [MG[j] + C.PSI_F * MQp[j] for j in range(n)]
    Mfd_n = [MG[j] + C.PSI_F * MQn[j] for j in range(n)]
    # 挠度：频遇值（不计冲击）× ηθ，刚度 0.95 EcI —— 影响线按 EcI 算的，除以 0.95
    Wlong = [C.ETA_THETA * C.PSI_F * w / C.STIFF_FACTOR for w in WQ]
    # ---------------------------------------------------------------- 支座反力
    bearings = []
    for b in L["perm"]:
        s = b["node"]
        rg1, rc, rg2 = R1.get(b["id"], 0.0), Rc[s], R2[s]
        hi = lo = 0.0
        for w in (w_hi, w_lo):
            ep, en = lane_effects([a * v for a, v in zip(w, IR[s])], xs, q, P_v)
            hi, lo = max(hi, ep), min(lo, en)
        rg = rg1 + rc + rg2 + b["cs"]
        rck = rg + (1 + mu_pos) * hi
        ae = effective_area(b["plan"])
        # 支座处转角的影响线：由互等定理，等于在该节点加单位逆时针力矩时各节点的挠度（向下为正）
        F = [0.0] * (2 * n)
        F[2 * s + 1] = 1.0
        um = B2.solve(F)
        ep, en = lane_effects([-um[2 * p] for p in range(n)], xs, q, P_m)
        tq_p, tq_n = max(mc_hi * ep, mc_lo * en), min(mc_hi * en, mc_lo * ep)
        bearings.append({"id": b["id"], "kind": b["kind"], "support": b["support"], "x": b["x"], "plan": b["plan"],
                         "R_G1": rg1, "R_conv": rc, "R_G2": rg2, "R_cs": b["cs"], "R_G": rg, "R_Qmax": hi, "R_Qmin": lo,
                         "Rck": rck, "R_ud_min": C.GAMMA_G_FAV * rg + C.GAMMA_Q1 * (1 + mu_pos) * lo, "Ae": ae,
                         "sigma": rck / ae / 1000.0, "size_req": required_size(b["plan"], rck),
                         "th_G1": TH1.get(b["id"], 0.0), "th_D": uc[2 * s + 1] + u2[2 * s + 1], "th_Qp": tq_p, "th_Qn": tq_n})
    temps = [{"id": t["id"], "support": t["support"], "girder": t["girder"], "x": t["x"], "R_G1": R1[t["id"]]}
             for t in L["temps"]]
    # ---------------------------------------------------------------- 各跨挠度与控制截面
    spans = []
    for m, ((a, b), (ja, jb)) in enumerate(zip(L["spans"], L["span_nodes"])):
        jw = max(range(ja, jb + 1), key=lambda j: Wlong[j])
        jm = max(range(ja, jb + 1), key=lambda j: Mud_p[j])
        spans.append({"span": L["spans_k"][0] + m, "L0": b - a, "w": Wlong[jw], "x_w": xs[jw],
                      "limit": (b - a) * C.DEFLECTION_LIMIT, "node_m": jm})
    loads = {"G1": sum(g["g1"] + sum(q_["P"] for q_ in g["dia"]) for g in L["girders"]),
             "G2": sum(g["g2"] for g in L["girders"]), "CS": sum(b["cs"] for b in L["perm"])}
    return {"L": L, "f1": f1, "f_neg": f_neg, "neg_mode": n_sp, "freqs": fs, "mu_pos": mu_pos, "mu_neg": mu_neg,
            "L0": L0, "Pk": P_m,
            "M1": M1, "Mc": Mc, "M2": M2, "MG": MG, "MQp": MQp, "MQn": MQn, "Mud_p": Mud_p, "Mud_n": Mud_n,
            "Mfd_p": Mfd_p, "Mfd_n": Mfd_n, "VRG": VRG, "VLG": VLG, "VRQ": VRQ, "VLQ": VLQ, "Vud": Vud, "VudR": VudR, "VudL": VudL,
            "Wlong": Wlong, "bearings": bearings, "temps": temps, "spans": spans, "loads": loads,
            "reactions": {"G1": sum(R1.values()), "conv": sum(Rc.values()), "G2": sum(R2.values()),
                          "temp": sum(R1[t["id"]] for t in L["temps"])}}


def code_frequency_estimates(x):
    """JTG D60-2004 条文说明给连续梁的基频估算式（据专利 CN105404718A 转引）：正弯矩、剪力用
    f1 = 13.616/(2πl²)·√(EIc/mc)，负弯矩用 f2 = 23.651/(2πl²)·√(EIc/mc)；条文说明同时说基频宜用有限元算，
    没有更精确的方法时才用它。这里只作对照：l 取该联最大计算跨径，Ic 取组合截面，mc 取各跨跨中所在单元的
    一、二期恒载每延米质量的平均（不计横隔板与连续段）。f_simple = π²/(2πl²)·√(EIc/mc) 是一跨简支梁的基频——
    等跨连续梁的一阶振型就是各跨交替的简支振型。"""
    L = x["L"]
    ms = []
    for (a, b), (ja, jb) in zip(L["spans"], L["span_nodes"]):
        jm = min(range(ja, jb), key=lambda j: abs((L["xs"][j] + L["xs"][j + 1]) / 2 - (a + b) / 2))
        ms.append((L["w1"][jm] + L["w2"][jm]) / G_ACC)
    m = sum(ms) / len(ms)
    l = x["L0"]
    base = math.sqrt(C.E_C50 * L["sec"]["I"] / m) / (2 * math.pi * l * l)
    return {"l": l, "m": m, "f1": 13.616 * base, "f2": 23.651 * base, "f_simple": math.pi ** 2 * base}


def span_stiffness_ratios(L):
    """等代简支梁的抗弯刚度修正系数：每跨跨中附近的节点上加单位力，C_w = 同跨简支梁在该点的挠度 /
    连续梁在该点的挠度（教材式 2-6-6：按非简支体系梁与简支梁挠度相等求等代简支梁的抗弯刚度）。"""
    B = continuous_beam(L, with_mass=False)
    EI = C.E_C50 * L["sec"]["I"]
    out = []
    for (a, b), (ja, jb) in zip(L["spans"], L["span_nodes"]):
        jm = min(range(ja, jb + 1), key=lambda j: abs(L["xs"][j] - (a + b) / 2))
        u = B.solve(B.load_vector(None, [(jm, 1.0)]))
        s1, s2 = L["xs"][jm] - a, b - L["xs"][jm]
        out.append((s1 * s1 * s2 * s2 / (3.0 * EI * (b - a))) / -u[2 * jm])
    return out


def analyse(els, only=None):
    """全桥：两幅 × 三联 × 五个梁位 = 30 条梁位线（only 见 girder_lines）。
    连续梁阶段的横向分布用全桥最大的 C_w（偏安全，教材例 2-6-1 的做法）。"""
    lines_L = girder_lines(els, only)
    cws = [span_stiffness_ratios(L) for L in lines_L]
    cw = max(max(c) for c in cws)
    secs, beta, coef, info = distribution(cw=cw)
    beta_simple = distribution()[1]
    lines = []
    for L, c in zip(lines_L, cws):
        x = analyse_line(L, coef)
        x["cw"] = c
        lines.append(x)
    return {"secs": secs, "beta": beta, "beta_simple": beta_simple, "cw": cw, "cw_min": min(min(c) for c in cws),
            "coef": coef, "info": info, "lines": lines}


# ====================================================================== 收缩与徐变（JTG 3362-2018 附录 C）
FCM_TABLE = 40.0      # MPa：表 C.1.2、C.2.2 的数值按 C40（fcm = 0.8 × 40 + 8）算得，C25–C50 直接用；≥ C50 乘 √(32.4/fck)


def notional_size():
    """构件理论厚度 h = 2A/u（mm）：预制截面，周边全部与大气接触。"""
    pts = C.T_SECTION
    u = sum(math.hypot(pts[(i + 1) % len(pts)][0] - pts[i][0], pts[(i + 1) % len(pts)][1] - pts[i][1])
            for i in range(len(pts)))
    return 2.0 * polygon_props(pts)[0] / u * 1000.0


def nominal_shrinkage(rh):
    """名义收缩系数 εcs0 = εs(fcm)·βRH（式 C.1.1-2～4，βsc = 5.0）。"""
    eps_s = (160.0 + 10.0 * 5.0 * (9.0 - FCM_TABLE / 10.0)) * 1e-6
    return eps_s * 1.55 * (1.0 - rh ** 3) * math.sqrt(32.4 / C.FCK_C50)


def shrinkage_development(dt, h_mm):
    """βs(t − ts)（式 C.1.1-5）。"""
    return math.sqrt(dt / (350.0 * (h_mm / 100.0) ** 2 + dt)) if dt > 0 else 0.0


def shrinkage_strain(t0, h_mm, rh, t=None, ts=None):
    """龄期 t0 到 t 的收缩应变 εcs0·[βs(t − ts) − βs(t0 − ts)]（式 C.1.3）；t = None 取终极值。"""
    ts = C.T_SHRINK_START if ts is None else ts
    b_t = 1.0 if t is None else shrinkage_development(t - ts, h_mm)
    return nominal_shrinkage(rh) * (b_t - shrinkage_development(t0 - ts, h_mm))


def nominal_creep(t0, h_mm, rh):
    """名义徐变系数 φ0 = φRH·β(fcm)·β(t0)（式 C.2.1-2～5）。"""
    phi_rh = 1.0 + (1.0 - rh) / (0.46 * (h_mm / 100.0) ** (1.0 / 3.0))
    return phi_rh * 5.3 / math.sqrt(FCM_TABLE / 10.0) / (0.1 + t0 ** 0.2) * math.sqrt(32.4 / C.FCK_C50)


def creep_coefficient(t0, h_mm, rh, t=None):
    """加载龄期 t0、计算龄期 t 的徐变系数 φ0·βc(t − t0)（式 C.2.1-1、6、7）；t = None 取终极值。"""
    if t is None:
        return nominal_creep(t0, h_mm, rh)
    bh = min(1500.0, 150.0 * (1.0 + (1.2 * rh) ** 18) * h_mm / 100.0 + 250.0)
    return nominal_creep(t0, h_mm, rh) * ((t - t0) / (bh + t - t0)) ** 0.3


def prestress_estimate(res):
    """每条梁位线、每跨：按全预应力构件正截面抗裂条件（第 6.3.1 条，预制构件 σst − 0.85σpc ≤ 0）反推跨中要的
    有效预加力 Np。σst = M_G1 / W0（预制截面）+ (M_转换 + M_G2 + 0.7·M_Q) / W（组合截面），都取下缘；
    预加力作用在预制截面上：σpc,下缘 = Np/A0 + Np·ep/W0，ep 为预制截面形心到钢束重心（距梁底 TENDON_COVER）。
    这不是钢束设计——只为给徐变缩短一个量级。返回每跨的 σst、Np 与截面重心处的 σpc = Np/A0（MPa）。"""
    out = []
    for x in res["lines"]:
        L = x["L"]
        sec = L["sec"]
        yb0, yb = sec["y0"] + C.H_GIRDER, sec["yc"] + C.H_GIRDER
        W0, W = sec["I0"] / yb0, sec["I"] / yb
        ep = yb0 - C.TENDON_COVER
        for s in x["spans"]:
            j = s["node_m"]
            sig = x["M1"][j] / W0 + (x["Mc"][j] + x["M2"][j] + C.PSI_F * x["MQp"][j]) / W
            Np = sig / (0.85 * (1.0 / sec["A0"] + ep / W0))
            out.append({"deck": L["deck"], "unit": L["unit"], "line": L["line"], "span": s["span"],
                        "sigma_st": sig / 1000.0, "Np": Np, "sigma_pc": Np / sec["A0"] / 1000.0})
    return out


def bearing_ages(rows=None):
    """支座开始受力时梁的龄期（d），取自梁场与架梁计划：伸缩端 = 该幅该联各片梁架设时龄期的最小值；
    连续墩 = 浇墩顶连续段那天该联各片梁龄期的最小值（全联都按最年轻的一片算，偏安全）。"""
    rows = rows if rows is not None else SC.plan()[0]
    convs = {(c["deck"], c["unit"]): c for c in SC.conversions(rows)}
    out = {}
    for d, _ in C.DECKS:
        for u, (a, b) in enumerate(MD.unit_bounds(), 1):
            gs = [r for r in rows if r["deck"] == d and a < r["span"] <= b]
            out[(d, u, "end")] = min(r["age_at_erect"] for r in gs)
            out[(d, u, "cont")] = min((convs[(d, u)]["cast"] - r["cast"]).days for r in gs)
    return out


# ====================================================================== 支座：位移、剪切变形、转角与抗滑
def bearing_spec(e):
    """BIM 里一个永久支座的规格：平面（取构件几何）、橡胶层厚（按厚度查 BEARING_SERIES）、是否滑板。
    形状系数 S 由加劲钢板尺寸与中间层厚 t1 算：圆形 d0 / 4t1，矩形 l0a·l0b / 2t1(l0a + l0b)。"""
    plan = bearing_plan(e)
    key = ("circle", round(plan[1], 4)) if plan[0] == "circle" else ("rect", round(plan[1], 4), round(plan[2], 4))
    ser = C.BEARING_SERIES[key]
    sliding = bool(e.attrs.get("sliding"))
    t = e.params["h"] - (C.PTFE_T if sliding else 0.0)
    te = next((v for tt, v in ser["te"].items() if abs(tt - t) < 1e-6), None)
    if te is None:
        raise ValueError("%s：橡胶支座厚 %.3f m 不在 %s 的规格系列里" % (e.eid, t, ser["name"]))
    c = 2 * C.BEARING_COVER
    if plan[0] == "circle":
        la, ag, S = plan[1], math.pi * plan[1] ** 2 / 4, (plan[1] - c) / (4 * ser["t1"])
    else:
        l0a, l0b = plan[1] - c, plan[2] - c
        la, ag, S = min(plan[1], plan[2]), plan[1] * plan[2], l0a * l0b / (2 * ser["t1"] * (l0a + l0b))
    return {"name": ser["name"], "series": ser, "t": t, "te": te, "te_eff": te - 2 * C.BEARING_COVER_TB, "la": la,
            "Ag": ag, "S": S, "t1": ser["t1"], "t0": ser["t0"], "sliding": sliding}


def plain_thickness_needed(spec, dl, db):
    """同一平面尺寸的规格系列里，满足剪切变形（te − 5 mm ≥ 2Δl、≥ 1.43(Δl + Δb)）与受压稳定（la/10 ≤ te ≤ la/5）
    的最薄一档，返回橡胶支座总厚；一档都不行返回 None。"""
    for t, te in sorted(spec["series"]["te"].items()):
        eff = te - 2 * C.BEARING_COVER_TB
        if eff >= 2 * dl and eff >= 1.43 * (dl + db) and spec["la"] / 10 - 1e-9 <= te <= spec["la"] / 5 + 1e-9:
            return t
    return None


def bearing_design(res, els, p=None):
    """永久支座的位移与第 8.7.3–8.7.5 条验算。p 覆盖默认输入（敏感性分析用）：climate、t_set、rh、t_ultimate、
    sigma_pc（MPa）、ages。
    - 不动点：每幅每联按普通板式支座的抗推刚度 Ge·Ag/(te − 5 mm) 求平面上的刚度形心（滑板支座不计，墩台按刚性）。
      支座都是各向同性的，所以温度与收缩徐变让整联绕这一点等比例伸缩，位移 = 平面距离 × 应变。
    - 应变：缩短 α(Tset,u − Tmin) + εcs(∞, t0) + σpc/Ec·φ(∞, t0)，伸长 α(Tmax − Tset,l)；t0 为支座开始受力时的龄期。
    - 制动力按刚度分给普通板式支座：整联平移 F / ΣK。
    返回 {"rows": 逐个支座, "units": 每幅每联, "gaps": 伸缩缝, "inputs": 本次用的输入}。"""
    p = dict(p or {})
    climate = p.get("climate", C.CLIMATE)
    t_max, t_min = C.T_EFFECTIVE[climate]
    t_lo, t_hi = p.get("t_set", C.T_SET)
    rh = p.get("rh", C.RH)
    tu = p.get("t_ultimate", C.T_ULTIMATE)
    ge = C.GE_BY_CLIMATE[climate]
    h_mm = notional_size()
    pre = prestress_estimate(res)
    spc = p.get("sigma_pc", max(q["sigma_pc"] for q in pre))
    ages = p.get("ages") or bearing_ages()
    ec = C.E_C50 / 1000.0
    e_up = C.ALPHA_C * (t_max - t_lo)

    def strains(t0):
        return (C.ALPHA_C * (t_hi - t_min), shrinkage_strain(t0, h_mm, rh, tu),
                spc / ec * creep_coefficient(t0, h_mm, rh, tu))
    by = {e.eid: e for e in els}
    lanes = lanes_for_width(2 * (C.DECK_WIDTH / 2 - C.BARRIER_W))
    groups = {}
    for x in res["lines"]:
        L = x["L"]
        g = groups.setdefault((L["deck"], L["unit"]), {"items": [], "length": 0.0, "Pk": 0.0})
        g["length"], g["Pk"] = max(g["length"], L["xs"][-1]), max(g["Pk"], x["Pk"])
        inset = {sid: min(abs(xv - gd["xa"]), abs(xv - gd["xb"])) for gd in L["girders"] for sid, xv in gd["sup"]}
        for b in x["bearings"]:
            g["items"].append((x, b, bearing_spec(by[b["id"]]), inset.get(b["id"])))
    rows, units = [], {}
    for (d, u), g in sorted(groups.items()):
        plain = [(b, sp) for _, b, sp, _ in g["items"] if not sp["sliding"]]
        if not plain:
            raise ValueError("%s 幅第 %d 联没有普通板式支座：平面位置不定" % (d, u))
        ks = {b["id"]: ge * 1000.0 * sp["Ag"] / sp["te_eff"] for b, sp in plain}
        K = sum(ks.values())
        x0 = sum(k * by[i].params["c"][0] for i, k in ks.items()) / K
        y0 = sum(k * by[i].params["c"][1] for i, k in ks.items()) / K
        f_lane = max(C.BRAKE_MIN_LANE, 0.1 * (C.Q_K * g["length"] + g["Pk"]))
        F = C.BRAKE_LANE_FACTOR[lanes] * f_lane
        units[(d, u)] = {"x0": x0, "y0": y0, "K": K, "F_lane": f_lane, "F": F, "d_brake": F / K, "lanes": lanes,
                         "t0_end": ages[(d, u, "end")], "t0_cont": ages[(d, u, "cont")]}
        for x, b, sp, inset in g["items"]:
            c = by[b["id"]].params["c"]
            r = math.hypot(c[0] - x0, c[1] - y0)
            t0 = ages[(d, u, b["kind"])]
            e_t, e_s, e_c = strains(t0)
            dn, dp = r * (e_t + e_s + e_c), r * e_up
            dl = max(dn, dp)
            db = F / K if not sp["sliding"] else 0.0
            mu = x["mu_pos"]
            # 竖向压缩与转角（第 8.7.3 条第 3 款）：Ee = 5.4·Ge·S²；转角含一期恒载（预制梁简支）、体系转换与二期恒载、
            # 汽车荷载（计冲击），不计预应力反拱
            ee = 5.4 * ge * sp["S"] ** 2
            sigma = b["Rck"] / b["Ae"] / 1000.0
            dcm = sigma * sp["te_eff"] * (1.0 / ee + 1.0 / C.EB_RUBBER)
            th0 = b["th_G1"] + b["th_D"]
            theta = max(abs(th0 + (1 + mu) * b["th_Qp"]), abs(th0 + (1 + mu) * b["th_Qn"]))
            ts_need = C.KP_STEEL * sigma * 2 * sp["t1"] / (0.65 * C.STEEL_FY)
            row = {"id": b["id"], "kind": b["kind"], "deck": d, "unit": u, "support": b["support"], "line": x["L"]["line"],
                   "sliding": sp["sliding"], "spec": sp, "r": r, "t0": t0, "eps_t": e_t, "eps_s": e_s, "eps_c": e_c,
                   "d_contract": dn, "d_expand": dp, "d_brake": db, "theta": theta, "dcm": dcm, "Ee": ee, "sigma": sigma,
                   "ts_need": ts_need, "stable": sp["la"] / 10 - 1e-9 <= sp["te"] <= sp["la"] / 5 + 1e-9,
                   "util_rot": theta * sp["la"] / 2 / dcm, "util_comp": dcm / (0.07 * sp["te_eff"]),
                   "util_ts": ts_need / sp["t0"], "R_G": b["R_G"], "Rck": b["Rck"], "inset": inset}
            if sp["sliding"]:
                cap0 = ge * 1000.0 * sp["Ag"] * C.TAN_ALPHA[0]
                cap1 = ge * 1000.0 * sp["Ag"] * C.TAN_ALPHA[1]
                row["util_friction"] = max(C.MU_PTFE * b["R_G"] / cap0, C.MU_PTFE * b["Rck"] / cap1)
                row["travel"] = sp["la"] / 2 + dn + F / K
                row["util_travel"] = row["travel"] / inset
            else:
                row["util_shear"] = max(2.0 * dl / sp["te_eff"], 1.43 * (dl + db) / sp["te_eff"])
                push = 1.4 * ge * 1000.0 * sp["Ag"] * dl / sp["te_eff"]
                fb = F * ks[b["id"]] / K
                r_min = b["R_G"] + 0.5 * (1 + mu) * min(b["R_Qmin"], 0.0)
                row.update(F_brake=fb, push=push, R_slip=r_min,
                           util_slip=max(push / (C.MU_RUBBER_CONCRETE * b["R_G"]), (push + fb) / (C.MU_RUBBER_CONCRETE * r_min)))
            rows.append(row)
    gaps = []
    for e in els:
        if e.cls != "expansion_joint":
            continue
        k = e.attrs["support"]
        near = [r for r in rows if r["kind"] == "end" and r["support"] == k and r["deck"] == e.deck]
        per_unit = {}
        for r_ in near:
            per_unit[r_["unit"]] = max(per_unit.get(r_["unit"], 0.0), r_["d_expand"])
        if not per_unit:                                 # 只算了部分梁位线（测试里）：这道缝两侧都没算
            continue
        close = sum(per_unit.values()) + max(units[(e.deck, u_)]["d_brake"] for u_ in per_unit)
        gaps.append({"eid": e.eid, "support": k, "deck": e.deck, "gap": e.attrs["gap"], "close": close,
                     "units": sorted(per_unit)})
    return {"rows": rows, "units": units, "gaps": gaps,
            "inputs": {"climate": climate, "T": (t_max, t_min), "t_set": (t_lo, t_hi), "rh": rh, "t_ultimate": tu,
                       "Ge": ge, "h_mm": h_mm, "sigma_pc": spc, "prestress": pre, "ages": ages, "e_up": e_up}}


# ====================================================================== 检查（与 checks.py 同格式：名称、是否通过、实测说明）
def _tiny(x, floor=1e-9):
    return "< %.0e" % floor if abs(x) < floor else "%.2e" % x


def check_distribution_premise(res, els):
    """修正偏心压力法的前提：宽跨比 B/l ≤ 0.5（窄桥），且有可靠的横向联结——每跨每对相邻梁在
    1/4、1/2、3/4 跨都有中横隔板。宽跨比用最短的简支计算跨径（最不利）；横隔板位置在模型里量：
    实体顶点的形心投影到路线上得到桩号，再换算成占该跨的比例。"""
    l_min = min(abs(g["sup"][1][1] - g["sup"][0][1]) for r in res["lines"] for g in r["L"]["girders"])
    ratio = C.DECK_WIDTH / l_min
    st = MD.support_stations()
    found = {}
    for e in els:
        if e.cls != "diaphragm":
            continue
        v = e.params["solids"][0]["v"]
        s = MD.project(sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v), e.attrs["station"])[0]
        k = e.attrs["span"]
        found.setdefault((e.deck, k, e.attrs["pair"]), []).append((s - st[k - 1]) / (st[k] - st[k - 1]))
    worst = 0.0
    for fs in found.values():
        for target in C.DIAPHRAGM_FRACTIONS:
            worst = max(worst, min(abs(f - target) for f in fs))
    pairs = len(C.DECKS) * C.N_SPANS * (N_G - 1)
    ok = ratio <= 0.5 and len(found) == pairs and worst <= 0.01
    return ("横向分布方法的前提：宽跨比 ≤ 0.5，每跨每对相邻梁在 1/4、1/2、3/4 跨有横隔板", ok,
            "B/l = %.2f/%.2f = %.3f；%d/%d 对梁间都有，位置最大偏差 %s 跨" % (C.DECK_WIDTH, l_min, ratio, len(found),
                                                                       pairs, _tiny(worst, 1e-6)))


def check_equilibrium(res, els):
    """每条梁位线、每个施工阶段支座反力之和等于荷载之和；再把全桥荷载与构件体积 × 容重对账，
    确认没有哪块混凝土漏算或算了两遍。"""
    worst = 0.0
    for r in res["lines"]:
        ld, rc = r["loads"], r["reactions"]
        for a, b in ((ld["G1"], rc["G1"]), (rc["temp"], rc["conv"]), (ld["G2"], rc["G2"])):
            worst = max(worst, abs(a - b) / a)
    used = {k: sum(r["loads"][k] for r in res["lines"]) for k in ("G1", "G2", "CS")}
    vol = {}
    for e in els:
        vol[e.cls] = vol.get(e.cls, 0.0) + e.volume
    ref = {"G1": (vol["girder"] + vol["diaphragm"] + vol["wet_joint"] + vol["cantilever"]) * C.UNIT_RC,
           "G2": vol["pavement"] * PAVEMENT_UNIT + vol["barrier"] * C.UNIT_RC, "CS": vol["continuity"] * C.UNIT_RC}
    acc = max(abs(used[k] - ref[k]) / ref[k] for k in ref)
    return ("荷载与反力平衡、荷载与构件体积对账", worst < 1e-9 and acc < 1e-9,
            "%d 条梁位线 × 3 个阶段，反力与荷载最大相对差 %s；一期 %.0f kN、二期 %.0f kN、连续段 %.0f kN，"
            "与构件体积 × 容重相差 %s" % (len(res["lines"]), _tiny(worst), used["G1"], used["G2"], used["CS"], _tiny(acc)))


def check_deflection(res, limit=None):
    """汽车荷载频遇值（不计冲击）× ηθ 的长期挠度 ≤ L/600，逐跨。"""
    limit = limit or C.DEFLECTION_LIMIT
    worst = max(((s["w"] / (s["L0"] * limit), s, r["L"]) for r in res["lines"] for s in r["spans"]), key=lambda t: t[0])
    q, s, L = worst
    return ("汽车荷载长期挠度 ≤ L/%d" % round(1 / limit), q <= 1.0,
            "最大 %.1f mm / 限值 %.1f mm = %.2f（%s 幅第 %d 跨 %d 号梁）" % (s["w"] * 1000, s["L0"] * limit * 1000, q,
                                                                 L["deck"], s["span"], L["line"]))


def check_bearing_stress(res, sigma_c=None):
    """板式橡胶支座：Rck / Ae ≤ σc（Rck 含汽车冲击，Ae 为加劲钢板面积）。"""
    sigma_c = sigma_c or C.SIGMA_C
    bs = [b for r in res["lines"] for b in r["bearings"]]
    w = max(bs, key=lambda b: b["sigma"])
    return ("支座平均压应力 ≤ %.0f MPa" % sigma_c, w["sigma"] <= sigma_c,
            "%d 个永久支座，最大 %.2f MPa = %.2f σc（%s，Rck %.0f kN）" % (len(bs), w["sigma"], w["sigma"] / sigma_c,
                                                                  w["id"], w["Rck"]))


def check_uplift(res):
    """作用基本组合下单向受压支座始终受压：结构重力（有利，1.0）+ 1.4 ×（1 + μ）× 汽车荷载最小反力 > 0。"""
    bs = [b for r in res["lines"] for b in r["bearings"]]
    w = min(bs, key=lambda b: b["R_ud_min"])
    return ("基本组合下支座不脱空", w["R_ud_min"] > 0.0,
            "最小 %.0f kN（%s：恒载 %.0f kN，汽车最小 %.0f kN）" % (w["R_ud_min"], w["id"], w["R_G"], w["R_Qmin"]))


def check_narrow_bridge(res):
    """教材第六章第四节：连续梁按 θ' = (B/2l)·⁴√(C_w·Jx/Jy) ≤ 0.3 判窄桥；不满足时宜用 G-M 法。
    这里两法都算，θ' > 0.3 时每片梁的 mc 取两者的大值（最小值取两者的小值），检查这件事真的做了。"""
    gm = res["info"]["gm"]
    cs = res["coef"]
    covered = all(c["mc_max"] >= max(c["ecc_max"], c["gm_max"]) - 1e-12 and c["mc_min"] <= min(c["ecc_min"], c["gm_min"]) + 1e-12
                  for c in cs.values())
    ok = gm["theta"] <= THETA_NARROW or covered
    mid = (N_G + 1) // 2
    parts = ["%s %.4f / %.4f" % (lab, cs[k]["ecc_max"], cs[k]["gm_max"]) for k, lab in ((1, "边梁"), (2, "2 号"), (mid, "中梁"))]
    return ("连续梁窄桥判别 θ' ≤ %.1f，否则 mc 取 G-M 法包络" % THETA_NARROW, ok,
            "θ' = %.3f %s %.1f；修正偏心压力法 / G-M 法：%s，采用%s" % (
                gm["theta"], ">" if gm["theta"] > THETA_NARROW else "≤", THETA_NARROW, "、".join(parts),
                "两者的大值" if res["info"]["envelope"] else "修正偏心压力法"))


def check_bearing_shear(bd):
    """普通板式支座剪切变形：te − 5 mm ≥ 2Δl、计制动力 ≥ 1.43(Δl + Δb)（第 8.7.3 条，容许值同厂家表）；
    全部永久支座受压稳定 la/10 ≤ te ≤ la/5。"""
    rows = bd["rows"]
    plain = [r for r in rows if not r["sliding"]]
    w = max(plain, key=lambda r: r["util_shear"])
    bad = [r["id"] for r in rows if not r["stable"]]
    sp = w["spec"]
    dl = max(w["d_contract"], w["d_expand"])
    return ("支座剪切变形 te ≥ 2Δl、计制动力 1.43Δl；受压稳定 la/10 ≤ te ≤ la/5", w["util_shear"] <= 1.0 and not bad,
            "%d 个普通板式支座，最大 Δl %.1f mm（%s，距不动点 %.1f m）≤ %.1f mm、计制动力 %.1f ≤ %.1f mm，利用率 %.2f；"
            "%d 个永久支座 te 都在 la/10–la/5%s" % (
                len(plain), dl * 1000, w["id"], w["r"], sp["te_eff"] / 2 * 1000, (dl + w["d_brake"]) * 1000,
                sp["te_eff"] / 1.43 * 1000, w["util_shear"], len(rows), "" if not bad else "（不满足：%s）" % bad[0]))


def check_bearing_compression(bd):
    """竖向平均压缩变形 θ·la/2 ≤ δc,m ≤ 0.07te（第 8.7.3 条第 3 款），加劲钢板厚 ≥ Kp·Rck·(tu + tl)/(Ae·σs) 且 ≥ 2 mm。"""
    rows = bd["rows"]
    w = max(rows, key=lambda r: r["util_rot"])
    c = max(rows, key=lambda r: r["util_comp"])
    s = max(rows, key=lambda r: r["util_ts"])
    thin = min(r["spec"]["t0"] for r in rows)
    ok = w["util_rot"] <= 1.0 and c["util_comp"] <= 1.0 and s["util_ts"] <= 1.0 and thin >= 0.002
    return ("支座转角 θ·la/2 ≤ δc,m ≤ 0.07te，加劲钢板厚度", ok,
            "转角最大利用率 %.2f（%s：θ = %.5f rad，含一期恒载、不计反拱；平均压缩 %.2f mm）；平均压缩最大为 0.07te 的 %.2f；"
            "加劲钢板按式 8.7.3-10 最多要 %.2f mm（%s，有 %.0f mm），最薄 %.0f mm ≥ 2 mm" % (
                w["util_rot"], w["id"], w["theta"], w["dcm"] * 1000, c["util_comp"], s["ts_need"] * 1000, s["id"],
                s["spec"]["t0"] * 1000, thin * 1000))


def check_bearing_slip(bd):
    """普通板式支座抗滑（第 8.7.4 条：μR ≥ 1.4GeAgΔl/te，计制动力再加 Fbk）；四氟滑板支座先滑动、橡胶不先剪坏
    （第 8.7.5 条：μf·R ≤ Ge·Ag·tanα）。"""
    rows = bd["rows"]
    plain = [r for r in rows if not r["sliding"]]
    slide = [r for r in rows if r["sliding"]]
    parts, ok = [], True
    if plain:
        w = max(plain, key=lambda r: r["util_slip"])
        ok = ok and w["util_slip"] <= 1.0
        parts.append("普通板式支座抗滑利用率最大 %.2f（%s）" % (w["util_slip"], w["id"]))
    if slide:
        f = max(slide, key=lambda r: r["util_friction"])
        ok = ok and f["util_friction"] <= 1.0
        parts.append("%d 个滑板支座 μf·R ≤ Ge·Ag·tanα，利用率最大 %.2f（%s）" % (len(slide), f["util_friction"], f["id"]))
    return ("支座抗滑与四氟滑板摩擦", ok, "；".join(parts))


def check_bearing_travel(bd):
    """伸缩端：滑板支座的不锈钢板在梁底要伸出 d/2 + 缩短量 + 制动平移，放得进支座中心到梁端的距离；
    升温与制动下两侧梁端（或梁端与背墙）的缝不闭合——缝宽取 BIM 里的伸缩装置。"""
    slide = [r for r in bd["rows"] if r["sliding"]]
    gaps = bd["gaps"]
    worst_g = max(gaps, key=lambda g: g["close"] / g["gap"])
    ok = all(g["close"] < g["gap"] for g in gaps)
    txt = "缝闭合最大 %.1f mm / 缝宽 %.0f mm = %.2f（%s）" % (worst_g["close"] * 1000, worst_g["gap"] * 1000,
                                                     worst_g["close"] / worst_g["gap"], worst_g["eid"])
    if slide:
        t = max(slide, key=lambda r: r["util_travel"])
        ok = ok and t["util_travel"] <= 1.0
        txt = "滑板要伸出 %.3f m ≤ 支座中心到梁端 %.2f m（%s）；" % (t["travel"], t["inset"], t["id"]) + txt
    return ("伸缩端：滑板行程放得进梁底，升温与制动下梁端缝不闭合", ok, txt)


def run_checks(res, els):
    bd = res.get("bearing_design") or bearing_design(res, els)
    return [check_distribution_premise(res, els), check_narrow_bridge(res), check_equilibrium(res, els),
            check_deflection(res), check_bearing_stress(res), check_uplift(res), check_bearing_shear(bd),
            check_bearing_compression(bd), check_bearing_slip(bd), check_bearing_travel(bd)]
