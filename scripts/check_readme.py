"""README 里的每个数字，对着已提交的产物回算。

每条核对 = 一条在 README 里必须恰好命中一次的正则 + 一个从产物算出期望值的函数。
写成阈值的句子（「差 < 1e-6」）核对的是：README 写的阈值等于测试里真正断言的那个阈值。
最后断言核对条数等于 EXPECTED——改 README 时某句不再匹配，不会被静默跳过。

    python scripts/check_readme.py
"""
import ast
import csv
import glob
import json
import logging
import math
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ifcopenshell            # noqa: E402
import ifcopenshell.validate   # noqa: E402
import rhino3dm                # noqa: E402

from bridge import alignment as AL, config as C, model as M   # noqa: E402

EXPECTED = 185
README = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()


def rows(name):
    return list(csv.DictReader(open(os.path.join(ROOT, "data", name), encoding="utf-8")))


def src(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


SUMMARY = json.load(open(os.path.join(ROOT, "data", "summary.json"), encoding="utf-8"))
CHECKS = json.load(open(os.path.join(ROOT, "data", "checks.json"), encoding="utf-8"))
GIRDERS = rows("girders.csv")
BEARINGS = {r["bearing"]: r for r in rows("bearings.csv")}
TAKEOFF = {r["class"]: r for r in rows("takeoff.csv")}
SPECS = rows("length_specs.csv")
SPEC_SENS = rows("length_spec_sensitivity.csv")
SENS = {(int(r["beds"]), int(r["lead_days"])): r for r in rows("sensitivity.csv")}
CONV = rows("conversions.csv")
ELEMENTS = rows("elements.csv")
IFC = ifcopenshell.open(os.path.join(ROOT, "model", "bridge_bim.ifc"))
T_MODEL, T_ALIGN, T_ART = src("tests/test_model.py"), src("tests/test_alignment.py"), src("tests/test_artifacts.py")
T_STRUCT, S_STRUCT = src("tests/test_structure.py"), src("bridge/structure.py")
LATERAL = {int(r["pos"]): r for r in rows("lateral_distribution.csv")}
LINES = rows("girder_lines.csv")
SECTIONS = rows("sections.csv")
REACTIONS = rows("bearing_reactions.csv")

failures, checked = [], [0]


def n(s):
    """去掉千分位逗号，便于和产物里的原始数比。"""
    return s.replace(",", "")


def claim(label, pattern, expected):
    """README 里 pattern 必须恰好命中一次，捕获组逐个等于 expected。"""
    checked[0] += 1
    hits = list(re.finditer(pattern, README))
    if len(hits) != 1:
        failures.append("%s：正则命中 %d 次（应为 1）：%s" % (label, len(hits), pattern))
        return
    got = tuple(n(g) for g in hits[0].groups())
    want = tuple(str(x) for x in expected)
    if got != want:
        failures.append("%s：README 写 %s，产物算出 %s" % (label, got, want))


def test_const(text, pattern):
    """测试源码里断言用的常数（取第一处）。"""
    m = re.search(pattern, text)
    if not m:
        failures.append("测试源码里找不到：%s" % pattern)
        return "?"
    return m.group(1)


def validate_issues():
    issues = []

    class H(logging.Handler):
        def emit(self, rec):
            issues.append(rec)
    lg = logging.getLogger("readme-ifc-validate")
    lg.addHandler(H())
    lg.setLevel(logging.DEBUG)
    ifcopenshell.validate.validate(IFC, lg, express_rules=False)
    return len(issues)


def n_tests():
    k = 0
    for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
        tree = ast.parse(open(p, encoding="utf-8").read())
        k += sum(1 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    return k


def mutants():
    """scripts/mutation_drill.py 里 MUTANTS 列表的长度（不 import：它会 import 测试）。"""
    tree = ast.parse(src("scripts/mutation_drill.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "MUTANTS":
            return len(node.value.elts)
    failures.append("scripts/mutation_drill.py 里找不到 MUTANTS")
    return "?"


def ifc_structure():
    """两个结构分析模型各自的杆件、节点、带边界条件的节点数；以及荷载、反力的实体数。"""
    out = {}
    for m in IFC.by_type("IfcStructuralAnalysisModel"):
        objs = [o for rel in m.IsGroupedBy for o in rel.RelatedObjects]
        pts = [o for o in objs if o.is_a("IfcStructuralPointConnection")]
        out[m.Name] = (sum(1 for o in objs if o.is_a("IfcStructuralCurveMember")), len(pts),
                       sum(1 for o in pts if o.AppliedCondition is not None))
    return out


def main():
    cls = Counter(r["class"] for r in ELEMENTS)
    kinds = Counter(M.support_kind(k) for k in range(C.N_SPANS + 1))
    st = M.support_stations()
    groups = Counter(c["group"] for c in CHECKS)
    passed = sum(c["pass"] for c in CHECKS)
    fams = Counter(r["family"] for r in SPECS)
    pv = C.V_PVI
    g1 = (pv[1][1] - pv[0][1]) / (pv[1][0] - pv[0][0])
    g2 = (pv[2][1] - pv[1][1]) / (pv[2][0] - pv[1][0])
    Lv = C.V_CURVE_LENGTH[1]
    R_v = Lv / abs(g2 - g1)
    T_v = Lv / 2
    sizes = Counter(b["size"] for b in BEARINGS.values())
    seat_h = [float(b["seat_height"]) for b in BEARINGS.values()]
    skew = max(max(abs(float(g["skew_a_deg"])), abs(float(g["skew_b_deg"]))) for g in GIRDERS)
    joints = [float(r["width_m"]) for r in rows("joints.csv") if r["kind"] == "现浇连续段"]
    ifc_types = Counter((p.is_a(), p.PredefinedType) for p in IFC.by_type("IfcElement"))
    detail = {c["name"]: c["detail"] for c in CHECKS}
    solids = re.search(r"(\d+) 个实体", detail["多面体闭合、法向朝外"]).group(1)

    # ---- 英文摘要
    claim("摘要：路线长", r"A (\d+) m route", ["%d" % AL.LENGTH])
    claim("摘要：跨数与联数", r"carries a (\d+)-span, (\d+)-unit", [C.N_SPANS, len(C.UNITS)])
    claim("摘要：构件与梁", r"(\d+) elements, (\d+) of them precast", [len(ELEMENTS), cls["girder"]])
    claim("摘要：梁长", r"(\d+) lengths instead of (\d+)", [SUMMARY["length_specs"], SUMMARY["length_specs_naive"]])
    claim("摘要：检查", r"(\d+) design, construction and structural checks", [len(CHECKS)])
    # ---- 一眼看懂
    claim("总览：路线", r"路线 \*\*(\d+)\*\* m：直线 – 回旋线 – 圆曲线 R=\*\*(\d+)\*\* m – 回旋线 – 直线，竖曲线 R=\*\*(\d+)\*\* m，圆曲线段超高 \*\*(\d+)%\*\*",
          ["%d" % AL.LENGTH, "%d" % C.R_CURVE, "%d" % round(R_v), "%d" % round(C.SUPERELEVATION * 100)])
    claim("总览：桥位", r"桥梁 \*\*(K[\d+.]+) – (K[\d+.]+)\*\*，\*\*(\d+)\*\*×30 m 分 \*\*(\d+)\*\* 联",
          [AL.station_label(st[0]), AL.station_label(st[-1]), C.N_SPANS, len(C.UNITS)])
    claim("总览：构件", r"共 \*\*(\d+)\*\* 个构件，其中预制 T 梁 \*\*(\d+)\*\* 片", [len(ELEMENTS), cls["girder"]])
    claim("总览：梁长", r"要 \*\*(\d+)\*\* 种长度，归并后 \*\*(\d+)\*\* 种（中跨 \*\*(\d+)\*\* \+ 边跨 \*\*(\d+)\*\*）",
          [SUMMARY["length_specs_naive"], SUMMARY["length_specs"], fams["中跨"], fams["边跨"]])
    claim("总览：检查", r"模型 (\d+) 条 \+ 梁场与架梁 (\d+) 条 \+ 上部结构 (\d+) 条，\*\*(\d+)/(\d+)\*\* 通过",
          [groups["模型"], groups["梁场与架梁"], groups["上部结构"], passed, len(CHECKS)])
    claim("总览：结构计算", r"(\d+) 条梁位线里基本组合正弯矩最大 \*\*([\d.]+)\*\* kN·m、墩顶负弯矩最大 \*\*(-[\d.]+)\*\* kN·m，"
          r"活载长期挠度最大为限值的 \*\*([\d.]+)\*\*；\s*\n  支座按压应力选型（伸缩端 (φ\d+)、连续墩 (\d+×\d+)），"
          r"利用率最大 \*\*([\d.]+)\*\*；自写求解器与 OpenSees 互核到 (1e-\d+)",
          [SUMMARY["structure_lines"], "%.1f" % SUMMARY["Mud_pos_max"], "%.1f" % SUMMARY["Mud_neg_min"],
           "%.3f" % SUMMARY["deflection_ratio_max"], "φ%d" % round(C.BEARING_D * 1000),
           "%d×%d" % (round(C.BEARING_CONT_A * 1000), round(C.BEARING_CONT_B * 1000)),
           "%.3f" % max(SUMMARY["bearing_util_end_max"], SUMMARY["bearing_util_cont_max"]),
           test_const(T_STRUCT, r"self\.assertLess\(rel\(a, b\), (1e-\d+)\)")])
    claim("总览：4D", r"梁场 \*\*(\d+)\*\* 个台座提前 \*\*(\d+)\*\* 天开工，架桥机 \*\*(\d+)\*\* 天架完 (\d+) 片、\*\*(\d+)\*\* 天等梁；存梁峰值 \*\*(\d+)\*\* 片（容量 \*\*(\d+)\*\*）；\*\*([\d-]+)\*\* 完成全部体系转换",
          [SUMMARY["beds"], SUMMARY["lead_days"], SUMMARY["erect_days"], SUMMARY["girders"], SUMMARY["wait_days"],
           SUMMARY["storage_peak"], SUMMARY["storage_capacity"], SUMMARY["conversion_last"]])
    claim("总览：IFC", r"IFC 4\.3：\*\*([\d,]+)\*\* 个实体，schema 校验 \*\*(\d+)\*\* 个问题；几何引擎逐件算出实体，体积与位置和模型一致到 (1e-\d)",
          [len(list(IFC)), validate_issues(), test_const(T_ART, r'self\.assertLess\(worst\["other"\], (1e-\d)\)')])
    # ---- 路线
    b = C.START_STATION
    names = {"LINE": "直线", "CLOTHOID": "回旋线", "CIRCULARARC": "圆曲线"}
    for i, (kind, L, k0, k1) in enumerate(C.H_SEGMENTS):
        def kstr(k):
            return "0" if k == 0 else "1/%d" % round(1 / k)
        curv = kstr(k0) if k0 == k1 else "%s → %s" % (kstr(k0), kstr(k1))
        claim("线元表 %d" % i, r"\| %s \| %s \| (\d+) \| ([^|]+) \|" % (names[kind], re.escape(AL.station_label(b))),
              ["%d" % L, curv])
        b += L
    claim("回旋线两算法", r"独立算一遍，两者差 < (1e-\d+) m", [test_const(T_ALIGN, r"self\.assertLess\(worst, (1e-\d+)\)")])
    claim("几何内核第三算法", r"与自己的积分在平面上差 < (1e-\d+) m、高程上差 < (1e-\d+) m",
          [test_const(T_ART, r"self\.assertLess\(wp, (1e-\d+)\)"), test_const(T_ART, r"self\.assertLess\(wz, (1e-\d+)\)")])
    claim("纵断面要素", r"变坡点 (K[\d+.]+)，高程 ([\d.]+)，前坡 ([+-][\d.]+)%、后坡 ([+-][\d.]+)%，抛物线竖曲线 L=(\d+) m\s*\n（R=(\d+)，T=(\d+)，E=([\d.]+)）",
          [AL.station_label(pv[1][0]), "%.3f" % pv[1][1], "%+.3f" % (g1 * 100), "%+.3f" % (g2 * 100), "%d" % Lv,
           "%d" % round(R_v), "%d" % T_v, "%.3f" % (T_v ** 2 / (2 * R_v))])
    claim("横坡", r"直线段各幅 (\d+)% 向外侧排水，圆曲线段两幅都 (\d+)% 超高",
          ["%d" % round(C.CROSSFALL * 100), "%d" % round(C.SUPERELEVATION * 100)])
    # ---- 结构体系
    claim("分联", r"12 跨分 (\d) 联（([^）]+)）", [len(C.UNITS), " + ".join("%d×%.0f" % (u, C.SPAN) for u in C.UNITS)])
    claim("支承线分类", r"(\d+) 条支承线分三种：(\d+) 个桥台、(\d+) 个过渡墩（两联交界，设伸缩缝）、\s*\n(\d+) 个连续墩",
          [len(st), kinds["A"], kinds["T"], kinds["C"]])
    claim("伸缩端", r"梁端面距支承线 ([\d.]+) m，联与联之间留 ([\d.]+) m 伸缩缝；梁直接落在 (φ\d+×\d+) 永久支座上，共 (\d+) 个",
          ["%.2f" % C.EXP_HALF, "%.2f" % (2 * C.EXP_HALF), "φ%d×%d" % (C.BEARING_D * 1000, round(C.BEARING_T * 1000)),
           sizes["φ%d×%d" % (C.BEARING_D * 1000, round(C.BEARING_T * 1000))]])
    cont_size = "%d×%d×%d" % (round(C.BEARING_CONT_A * 1000), round(C.BEARING_CONT_B * 1000), round(C.BEARING_CONT_T * 1000))
    claim("连续端", r"各落在一个临时支座上，共 (\d+) 个；墩中心一排 (\d+×\d+×\d+) 矩形永久支座（(\d+) 个，\s*\n  顺桥向 (\d+) mm）",
          [cls["temp_support"], cont_size, sizes[cont_size], "%d" % round(C.BEARING_CONT_A * 1000)])
    claim("横隔板", r"每跨相邻两片梁之间 (\d+) 道横隔板（两道端横隔板 \+ ([^）]+) 跨）",
          [2 + len(C.DIAPHRAGM_FRACTIONS), "、".join({0.25: "1/4", 0.5: "1/2", 0.75: "3/4"}[f] for f in C.DIAPHRAGM_FRACTIONS)])
    claim("连续段宽", r"连续段宽 ([\d.]+)–([\d.]+) m，由梁长归并决定", ["%.3f" % min(joints), "%.3f" % max(joints)])
    claim("斜角与垫石", r"曲线上最大斜角 ([\d.]+)°[\s\S]*?垫石高 ([\d.]+)–([\d.]+) m", ["%.3f" % skew, "%.3f" % min(seat_h), "%.3f" % max(seat_h)])
    # ---- 梁长归并
    claim("名义缝宽", r"逐片按名义缝宽（伸缩端 ([\d.]+) m、连续端 ([\d.]+) m）下料取整到 (\d+) mm，\s*\n要 \*\*(\d+)\*\* 种梁长",
          ["%.2f" % C.EXP_HALF, "%.3f" % C.CONT_HALF_NOM, "%d" % round(C.SPEC_STEP * 1000), SUMMARY["length_specs_naive"]])
    claim("浮动范围", r"连续端的缝宽可以在 ([\d.]+)–([\d.]+) m 之间浮动（连续段宽 ([\d.]+)–([\d.]+) m）",
          ["%.2f" % C.CONT_HALF_MIN, "%.2f" % C.CONT_HALF_MAX, "%.2f" % (2 * C.CONT_HALF_MIN), "%.2f" % (2 * C.CONT_HALF_MAX)])
    for r in SPECS:
        claim("梁长表 %s %s" % (r["family"], r["length_m"]), r"\| %s \| %s \| (\d+) \|" % (r["family"], re.escape(r["length_m"])),
              [r["count"]])
    for r in SPEC_SENS:
        claim("浮动敏感性 %s" % r["tolerance_m"], r"\| ±%s \| ([\d.–]+) \| (\d+) \| (\d+) \| (\d+) \|" % re.escape(r["tolerance_m"]),
              [r["joint_range_m"], r["middle"], r["end"], r["total"]])
    # ---- 构件与检查
    zh = {"girder": "预制 T 梁", "diaphragm": "横隔板", "wet_joint": "湿接缝", "cantilever": "翼缘现浇段", "continuity": "墩顶现浇连续段",
          "pavement": "桥面铺装", "barrier": "混凝土护栏", "expansion_joint": "伸缩装置", "seat": "支座垫石",
          "bearing": "板式橡胶支座", "temp_support": "临时支座", "cap": "盖梁", "abut_cap": "桥台台帽",
          "backwall": "桥台背墙", "column": "墩柱", "tie": "系梁", "pile": "钻孔灌注桩"}
    for k, name in zh.items():
        claim("构件表 %s" % k, r"\n\| %s \| (\d+) \|\n" % re.escape(name), [cls[k]])
    claim("取样与实体数", r"沿路线每 ([\d.]+) m 取样[\s\S]*?共 (\d+) 个实体。检查", ["%g" % C.LOFT_STEP, solids])
    for c in CHECKS:
        claim("检查表：%s" % c["name"], r"\| %s \| (✅|❌) \| ([^\n|]+) \|" % re.escape(c["name"]),
              ["✅" if c["pass"] else "❌", c["detail"]])
    claim("反例", r"伸缩端挪 (\d+) cm、把连续端往墩中心线伸 ([\d.]+) m、把永久支座往梁端下推 ([\d.]+) m、翻转一个面、把一片梁多切 (\d+) cm",
          [int(round(100 * float(test_const(T_MODEL, r'shift\(ring, \((0\.\d+) \* g\.params\["ta"\]')))),
           test_const(T_MODEL, r'shift\(ring, \((0\.\d+) \* g\.params\["tb"\]'),
           test_const(T_MODEL, r'b\.params\["c"\] = \(c\[0\] \+ (0\.\d+) \* t\[0\]'),
           int(round(100 * float(test_const(T_MODEL, r"/ L \* (0\.\d+) for i")))),])
    # ---- 垫石标高表
    claim("垫石表行数", r"全表 (\d+) 行见\s*\n\[`data/bearings\.csv`\][\s\S]*?临时支座 (\d+) 行", [len(BEARINGS), cls["temp_support"]])
    for bid in ("B-L01-1a", "B-L01-2a", "B-P01-L1"):
        r = BEARINGS[bid]
        claim("垫石表 %s" % bid, r"\| %s \| (\S+) \| (\S+) \| (\S+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|" % bid,
              [r["kind"], r["size"], r["support"], r["x"], r["y"], r["bearing_top"], r["seat_top"], r["cap_top"], r["seat_height"]])
    # ---- 工程量
    for k, r in TAKEOFF.items():
        if not r["concrete_m3"]:
            continue
        claim("工程量 %s" % k, r"\| %s \| (\d+) \| ([\d.]+) \| ([^|]*) \| ([\d.]+) \|" % re.escape(r["name"]),
              [r["count"], r["concrete_m3"], r["grade"], r["rebar_t"]])
    # ---- 上部结构计算
    eq = re.search(r"一期 (\d+) kN、二期 (\d+) kN、连续段 (\d+) kN", detail["荷载与反力平衡、荷载与构件体积对账"]).groups()
    claim("结构：荷载合计", r"全桥一期恒载 (\d+) kN、二期 (\d+) kN、连续段 (\d+) kN；[\s\S]*?差都 < (1e-\d+)。",
          list(eq) + [test_const(S_STRUCT, r"acc < (1e-\d+)")])
    pks = [float(r["Pk_kN"]) for r in LINES]
    claim("结构：车道荷载", r"qk = ([\d.]+) kN/m，Pk = 2\(L0 \+ 130\) = ([\d.]+)–([\d.]+) kN",
          ["%g" % C.Q_K, "%.2f" % min(pks), "%.2f" % max(pks)])
    claim("结构：β", r"抗扭修正 β = ([\d.]+)", [LATERAL[1]["beta"]])
    claim("结构：频率与冲击", r"f1 = ([\d.]+)–([\d.]+) Hz，μ = ([\d.]+)–([\d.]+)；",
          ["%.3f" % SUMMARY["f1_min"], "%.3f" % SUMMARY["f1_max"], "%.4f" % SUMMARY["mu_pos_min"], "%.4f" % SUMMARY["mu_pos_max"]])
    for label, pos in (("1 / 5 号（边梁）", 1), ("2 / 4 号", 2), ("3 号", 3)):
        r = LATERAL[pos]
        claim("横向分布 %d" % pos, r"\| %s \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|" % re.escape(label),
              [r["I_composite_m4"], r["mc_max"], r["mc_no_torsion"], r["m0_max"]])
    claim("结构：γ0", r"基本组合 γ0\(γG·G \+ 1\.4\(1 \+ μ\)Q\)，γ0 = ([\d.]+)", ["%.1f" % C.GAMMA_0])
    claim("结构：截面表行数", r"全表 (\d+) 行见 \[`data/sections\.csv`\]", [len(SECTIONS)])
    l111 = [r for r in SECTIONS if (r["deck"], r["unit"], r["line"]) == ("L", "1", "1")]
    for label, at in (("第 1 跨正弯矩最大处", "第 1 跨"), ("P01 墩顶", "P01"), ("第 2 跨正弯矩最大处", "第 2 跨"), ("P02 墩顶", "P02")):
        r = next(x for x in l111 if x["at"] == at)
        claim("施工阶段弯矩 %s" % at, r"\| %s \| (-?[\d.]+) \| (-?[\d.]+) \| (-?[\d.]+) \| (-?[\d.]+) \| (-?[\d.]+) \| ([\d.]+) \| (-?[\d.]+) \|"
              % re.escape(label), [r["M_G1"], r["M_conversion"], r["M_G2"], r["M_G"], r["M_Q"], r["mu"], r["M_ud"]])
    ends = [r for r in REACTIONS if r["kind"] == "伸缩端"]
    conts = [r for r in REACTIONS if r["kind"] == "连续墩"]
    rck_c = max(float(r["Rck"]) for r in conts)
    circle = 2 * math.sqrt(rck_c / (C.SIGMA_C * 1000.0) / math.pi) + 2 * C.BEARING_COVER
    step_d = 50                                   # 圆形支座直径按 50 mm 进级
    next_d = int(math.ceil(circle * 1000 / step_d)) * step_d
    clear_need = test_const(src("bridge/checks.py"), r"def check_perm_bearing_under_joint\(els, need=([\d.]+)\)")
    perm_detail = detail["连续墩永久支座全在现浇连续段下（距预制梁端 ≥ %s m）" % clear_need]
    claim("支座选型", r"伸缩端 Rck 最大 ([\d.]+) kN、\s*\n需要直径 ([\d.]+) m，取 (φ\d+)；连续墩 Rck 最大 ([\d.]+) kN，圆形要 φ(\d+)，"
          r"按 (\d+) mm 进级是 φ(\d+)——可连续端梁端离墩中心线\s*\n最近只有 ([\d.]+) m，φ\d+ 的边缘离梁端只剩 ([\d.]+) m，"
          r"正好卡在检查下限上。所以用矩形 (\d+) × (\d+)（顺桥向 × 横桥向），\s*\n顺桥向离梁端 ([\d.]+) m。σc = (\d+) MPa 是【假设】",
          ["%.1f" % max(float(r["Rck"]) for r in ends), "%.3f" % max(float(r["size_required_m"]) for r in ends),
           "φ%d" % round(C.BEARING_D * 1000), "%.1f" % rck_c, "%d" % round(circle * 1000), step_d, next_d,
           "%.2f" % C.CONT_HALF_MIN, "%.2f" % (C.CONT_HALF_MIN - next_d / 2000.0),
           "%d" % round(C.BEARING_CONT_A * 1000), "%d" % round(C.BEARING_CONT_B * 1000),
           re.search(r"最小 ([\d.]+) m", perm_detail).group(1), "%d" % C.SIGMA_C])
    if abs(C.CONT_HALF_MIN - next_d / 2000.0 - float(clear_need)) > 1e-9:
        failures.append("支座选型：README 说 φ%d 正好卡在下限 %s m 上，参数变了这句话就不成立" % (next_d, clear_need))
    claim("结构反例", r"把一道横隔板挪 (\d+) m、\s*\n漏算每跨一道横隔板、连续段混凝土算两遍、把挠度刚度 ([\d.]+)EcI 改成 ([\d.]+)EcI、"
          r"在 BIM 里把一个支座顺桥向改成 (\d+) mm、\s*\n把容重改成 ([\d.]+) kN/m³",
          [int(float(test_const(T_STRUCT, r"\(p\[0\] \+ ([\d.]+) \* t\[0\]"))), "%.2f" % C.STIFF_FACTOR,
           test_const(T_STRUCT, r'"STIFF_FACTOR", ([\d.]+)\)'),
           "%d" % round(1000 * float(test_const(T_STRUCT, r'params\["w"\] = ([\d.]+)'))),
           test_const(T_STRUCT, r'"UNIT_RC", ([\d.]+)\)')])
    claim("OpenSees 互核", r"前两阶频率逐项差 < (1e-\d+)（相对）", [test_const(T_STRUCT, r"\), (1e-\d+) \* scale_m\)")])
    claim("变异演练", r"对求解器做 (\d+) 种变异", [mutants()])
    n_temp = sum(1 for r in REACTIONS if r["kind"] == "临时支座")
    claim("结果回写", r"(\d+) 片预制梁和 (\d+) 个支座（永久 (\d+) \+ 临时 (\d+)）",
          [cls["girder"], len(REACTIONS), len(REACTIONS) - n_temp, n_temp])
    # ---- 梁场与 4D
    claim("梁场布置", r"(\d+) 个制梁台座、(\d+) 个存梁位 × (\d+) 层、两台 (\d+) t 龙门吊抬吊、钢筋加工区，\s*\n运梁便道 (\d+) m",
          [C.N_BEDS, C.STORAGE_POSITIONS, C.STORAGE_LAYERS, "%d" % C.GANTRY_SWL_T, "%d" % round(SUMMARY["haul_route_m"])])
    claim("工效", r"每个台座 (\d+) 天一个周期，开浇满 (\d+) 天才能架设；架桥机每天 (\d+) 个有效工时，\s*\n每片梁 (\d+) 小时、每跨架完过孔 (\d+) 小时，左幅架完转场 (\d+) 天",
          [C.BED_CYCLE_DAYS, C.MIN_AGE_DAYS, "%d" % C.DAY_HOURS, "%d" % C.ERECT_HOURS, "%d" % C.LAUNCH_HOURS, C.TRANSFER_DAYS])
    claim("排程", r"梁场 \*\*([\d-]+)\*\* 开浇，架桥机 \*\*([\d-]+)\*\* 开架、\*\*([\d-]+)\*\* 架完，历时 \*\*(\d+)\*\* 天，\s*\n等于「梁全部备齐时」架桥机本身的极限工期 (\d+) 天——\*\*(\d+)\*\* 天等梁。存梁峰值 \*\*(\d+)\*\* 片（\*\*([\d-]+)\*\*），\s*\n最长存梁 (\d+) 天，架设时最短龄期 (\d+) 天",
          [SUMMARY["cast_first"], SUMMARY["erect_first"], SUMMARY["erect_last"], SUMMARY["erect_days"],
           SUMMARY["machine_bound_days"], SUMMARY["wait_days"], SUMMARY["storage_peak"], SUMMARY["storage_peak_day"],
           SUMMARY["storage_max_days"], SUMMARY["age_min"]])
    claim("可行定义", r"「可行」= 存梁峰值不超过 (\d+) 片、存梁期不超过 (\d+) 天", [SUMMARY["storage_capacity"], C.MAX_STORAGE_DAYS])
    for (nb, lead), r in sorted(SENS.items()):
        if nb in (12, 14, 16, 18) and lead in (14, 21, 28):
            claim("台座敏感性 %d/%d" % (nb, lead), r"\| %d \| %d \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (是|否) \|" % (nb, lead),
                  [r["erect_days"], r["wait_days"], r["storage_peak"], r["storage_max_days"], "是" if r["feasible"] == "yes" else "否"])
    fourteen_never = not any(r["wait_days"] == "0" and r["feasible"] == "yes" for (nb, _), r in SENS.items() if nb == 14)
    claim("最少台座", r"台座最少的是 \*\*(\d+)\*\* 个、提前 \*\*(\d+)\*\* 天——就是采用的方案；(\d+) 个台座怎么调提前量都(做不到)",
          [SUMMARY["min_beds_zero_wait"], SUMMARY["min_beds_zero_wait_lead"], 14, "做不到" if fourteen_never else "做得到"])
    claim("连续段与体系转换", r"每幅每联最后一片梁架完的第(二)天浇墩顶连续段，(\d+) 天后张拉",
          ["二" if C.CONT_CAST_LAG_DAYS == 1 else str(C.CONT_CAST_LAG_DAYS + 1), C.CONT_CURE_DAYS])
    for r in CONV:
        claim("体系转换 %s%s" % (r["deck"], r["unit"]),
              r"\| %s \| 第%s联 \| %s \| %s \| ([\d-]+) \| ([\d-]+) \| ([\d-]+) \|" % ({"L": "左幅", "R": "右幅"}[r["deck"]], r["unit"],
                                                                                re.escape(r["spans"]), r["piers"]),
              [r["erected"], r["continuity_cast"], r["conversion"]])
    claim("梁场图说明", r"存梁峰值日的梁场：台座上在制 (\d+) 片、存梁区 (\d+) 片",
          [min(C.N_BEDS, sum(1 for g in GIRDERS if g["cast"] <= SUMMARY["storage_peak_day"] < g["to_storage"])),
           SUMMARY["storage_peak"]])
    # ---- IFC
    claim("IFC 桥梁分部", r"共 (\d+) 个 IfcBridgePart", [len(IFC.by_type("IfcBridgePart"))])
    claim("IFC 切割与面集", r"共 (\d+) 个切割；其余多面体是 IfcPolygonalFaceSet（(\d+) 个",
          [len(IFC.by_type("IfcBooleanClippingResult")), len(IFC.by_type("IfcPolygonalFaceSet"))])
    tasks = IFC.by_type("IfcTask")
    claim("IFC 任务", r"下 (\d+) 个 IfcTask（预制 (\d+)、架设 (\d+)、连续段浇筑与体系转换各 (\d+)",
          [len(tasks), sum(1 for t in tasks if t.Name.startswith("预制 ")), sum(1 for t in tasks if t.Name.startswith("架设 G-")),
           sum(1 for t in tasks if t.Name.startswith("体系转换 "))])
    claim("IFC 顺序关系", r"(\d+) 条 IfcRelSequence", [len(IFC.by_type("IfcRelSequence"))])
    sm = ifc_structure()
    s1, s2 = sm.get("施工阶段一：预制梁简支", (0, 0, 0)), sm.get("施工阶段二：体系转换后的连续梁", (0, 0, 0))
    claim("IFC 结构分析模型", r"(两)个 IfcStructuralAnalysisModel，按施工阶段分开。阶段一「预制梁简支」：(\d+) 根 IfcStructuralCurveMember"
          r"[\s\S]*?(\d+) 个 IfcStructuralPointConnection，其中 (\d+) 个带 IfcBoundaryNodeCondition[\s\S]*?"
          r"阶段二「体系转换后的连续梁」：(\d+) 根杆件（每条梁位线 (\d+) 段）、(\d+) 个节点，\s*\n  (\d+) 个永久支座为边界条件",
          ["两" if len(sm) == 2 else len(sm), s1[0], s1[1], s1[2], s2[0], s2[0] // max(1, len(LINES)), s2[1], s2[2]])
    claim("IFC 结构荷载与结果", r"共 (\d+) 条线荷载（按水平投影长度）和 (\d+) 个集中力；[\s\S]*?共 (\d+) 个 IfcStructuralPointReaction",
          [len(IFC.by_type("IfcStructuralCurveAction")), len(IFC.by_type("IfcStructuralPointAction")),
           len(IFC.by_type("IfcStructuralPointReaction"))])
    ifc_rows = [("预制 T 梁", "IfcBeam", "T_BEAM"), ("横隔板", "IfcBeam", "DIAPHRAGM"), ("盖梁、台帽", "IfcBeam", "PIERCAP"), ("支座垫石", "IfcBeam", "HATSTONE"),
                ("墩顶连续段、系梁", "IfcBeam", "USERDEFINED"), ("永久支座", "IfcBearing", "ELASTOMERIC"),
                ("临时支座", "IfcBearing", "USERDEFINED"), ("湿接缝、翼缘现浇段", "IfcSlab", "USERDEFINED"),
                ("墩柱", "IfcColumn", "PIERSTEM"), ("钻孔灌注桩", "IfcPile", "BORED"), ("桥面铺装", "IfcCourse", "PAVEMENT"),
                ("混凝土护栏", "IfcRailing", "GUARDRAIL"), ("伸缩装置", "IfcDiscreteAccessory", "EXPANSION_JOINT_DEVICE"),
                ("桥台背墙", "IfcWall", "RETAININGWALL")]
    for label, ent, pt in ifc_rows:
        claim("IFC 类表 %s" % label, r"\| %s \| %s \| %s \| (\d+) \|" % (re.escape(label), ent, pt), [ifc_types[(ent, pt)]])
    claim("IFC 实体与校验", r"文件共 \*\*([\d,]+)\*\* 个实体，ifcopenshell 的 schema 校验 \*\*(\d+)\*\* 个问题",
          [len(list(IFC)), validate_issues()])
    claim("IFC 跨平台容差", r"只允许落在浮点数的 (1e-\d+)（相对）以内", [test_const(src("bridge/numcmp.py"), r"(1e-\d+) \* max")])
    claim("IFC 几何引擎", r"体积和包围盒都与模型差 < (1e-\d+)；圆柱被引擎离散成多边形，体积差 < ([\d.]+)%",
          [test_const(T_ART, r'self\.assertLess\(worst\["girder"\], (1e-\d)\)'),
           "%g" % (100 * float(test_const(T_ART, r'self\.assertLess\(worst\["cyl"\], (0\.\d+)\)')))])
    # ---- 复现与结构
    claim("测试总数", r"跑全部 (\d+) 个测试", [n_tests()])
    claim("仓库：检查条数", r"\| `bridge/checks\.py` \| (\d+) 条模型检查 \|", [groups["模型"]])
    claim("仓库：结构检查", r"频率与冲击系数、组合、挠度与支座验算；(\d+) 条结构检查", [groups["上部结构"]])
    claim("仓库：梁场检查", r"梁场布置与 (\d+) 条检查", [groups["梁场与架梁"]])
    claim("仓库：表数", r"\| `data/` \| (\d+) 个表", [len([p for p in os.listdir(os.path.join(ROOT, "data"))
                                                        if p.endswith((".csv", ".json"))])])

    if checked[0] != EXPECTED:
        failures.append("核对条数 %d ≠ EXPECTED %d——README 增删了带数字的句子，请同步本脚本" % (checked[0], EXPECTED))
    if failures:
        for f in failures:
            print("MISMATCH", f)
        sys.exit(1)
    print("PASS README 的 %d 处数字全部与产物一致" % checked[0])


if __name__ == "__main__":
    main()
