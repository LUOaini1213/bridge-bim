"""把桥梁模型导出成 IFC 4.3（IFC4X3_ADD2）。

数据来源是 bridge/ 的构件表——Rhino 建模脚本读的也是这一份；Rhino 存下的 .3dm 由测试用
rhino3dm 读回、逐件对照。IFC 不从 .3dm 反读：路线的语义定义（线元类型、半径、回旋线参数、
竖曲线）和双精度坐标在 Rhino 的网格里已经不存在了。

IFC 结构：
    IfcProject
     ├─ IfcAlignment「主线」：水平（直线 / 回旋线 / 圆曲线）与竖向（坡段 / 抛物线）两层语义定义，
     │   配 IfcCompositeCurve / IfcGradientCurve 几何；13 条支承线各一个 IfcReferent（Pset_Stationing）
     └─ IfcSite → IfcBridge（GIRDER）
          ├─ IfcBridgePart SUPERSTRUCTURE → DECK 左幅 / 右幅 → DECK_SEGMENT 第 1–3 联
          └─ IfcBridgePart SUBSTRUCTURE → ABUTMENT A00 / A12、PIER P01–P11（IfcLinearPlacement 按桩号定位）
    预制 T 梁：IfcBeam T_BEAM，T 形截面沿梁轴斜向拉伸，两端用 IfcBooleanClippingResult
      按径向支承线切齐；其余构件是 IfcPolygonalFaceSet（与 Rhino 网格同一组顶点）或拉伸体。
    4D：IfcWorkSchedule 下 120 个预制任务、120 个架梁任务、每幅每联的连续段浇筑与体系转换任务；
      架梁任务产出对应的梁（IfcRelAssignsToProduct），体系转换任务消耗临时支座
      （IfcRelAssignsToProcess，拆除），任务间 IfcRelSequence。
    结构分析（bridge/structure.py 的计算，写成 IFC 结构分析领域的两个 IfcStructuralAnalysisModel）：
      阶段一「预制梁简支」：每片梁三段 IfcStructuralCurveMember（两端外伸 + 支座间），支座与临时支座是带
        IfcBoundaryNodeCondition 的 IfcStructuralPointConnection；一期恒载作为荷载工况（梁上线荷载、
        横隔板集中力），结果组是支座反力。
      阶段二「体系转换后的连续梁」：每条梁位线 12 段，永久支座为边界条件；荷载工况「拆除临时支座」
        （ActionSource = PROPPING，反力反向施加）与「二期恒载」；结果组是支座反力标准值组合 Rck。
      分析杆件与物理构件用 IfcRelAssignsToProduct 关联（预制梁 ↔ 它的分析段、支座 ↔ 它的支承点），
      梁与支座另挂 BridgeBIM_Structural 属性集（内力设计值、挠度、压应力与利用率）。
      力的单位是 N、N/m（IfcUnitAssignment 里声明）；属性集里的数值是 kN、kN·m，单位写在属性名里。

GlobalId 由名称经 uuid5 推出、文件头时间戳固定，同一份模型每次导出的 IFC 逐字节相同。

    python scripts/export_ifc.py            # 写 model/bridge_bim.ifc
    python scripts/export_ifc.py --check    # 重导一遍，与已提交的 IFC 逐字节比对
"""
import datetime
import math
import os
import sys
import tempfile
import uuid

import numpy

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.alignment
import ifcopenshell.api.context
import ifcopenshell.api.material
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.sequence
import ifcopenshell.api.spatial
import ifcopenshell.api.group
import ifcopenshell.api.structural
import ifcopenshell.api.unit
import ifcopenshell.geom
import ifcopenshell.guid
from ifcopenshell import ifcopenshell_wrapper

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import alignment as AL, config as C, schedule as S                    # noqa: E402
from bridge.model import section_area, support_kind, support_name, support_stations, unit_bounds   # noqa: E402
from bridge.numcmp import compare                                                  # noqa: E402
from bridge.pipeline import (CLASS_NAMES, COUNT_ITEMS, bearing_force_rows, compute, girder_force_rows,  # noqa: E402
                             structure)

MODEL_IFC = os.path.join(ROOT, "model", "bridge_bim.ifc")
NS = uuid.UUID("0c7d2b8e-8e1f-4f5b-a3c4-1b2e9a6d7f31")   # 固定命名空间，GlobalId 可复现
TIMESTAMP = "2026-09-23T00:00:00"
DECK_NAMES = {"L": "左幅", "R": "右幅"}
NAMES = dict(CLASS_NAMES, **COUNT_ITEMS)

# 构件类别 → (IFC 实体, PredefinedType, ObjectType)
IFC_CLASS = {
    "girder": ("IfcBeam", "T_BEAM", None),
    "diaphragm": ("IfcBeam", "DIAPHRAGM", None),
    "wet_joint": ("IfcSlab", "USERDEFINED", "湿接缝"),
    "cantilever": ("IfcSlab", "USERDEFINED", "翼缘现浇段"),
    "continuity": ("IfcBeam", "USERDEFINED", "墩顶现浇连续段"),
    "pavement": ("IfcCourse", "PAVEMENT", None),
    "barrier": ("IfcRailing", "GUARDRAIL", None),
    "expansion_joint": ("IfcDiscreteAccessory", "EXPANSION_JOINT_DEVICE", None),
    "seat": ("IfcBeam", "HATSTONE", None),
    "bearing": ("IfcBearing", "ELASTOMERIC", None),
    "temp_support": ("IfcBearing", "USERDEFINED", "临时支座"),
    "cap": ("IfcBeam", "PIERCAP", None),
    "abut_cap": ("IfcBeam", "PIERCAP", None),
    "backwall": ("IfcWall", "RETAININGWALL", None),
    "column": ("IfcColumn", "PIERSTEM", None),
    "tie": ("IfcBeam", "USERDEFINED", "系梁"),
    "pile": ("IfcPile", "BORED", None),
}
QTO = {"IfcBeam": "Qto_BeamBaseQuantities", "IfcSlab": "Qto_SlabBaseQuantities", "IfcColumn": "Qto_ColumnBaseQuantities",
       "IfcPile": "Qto_PileBaseQuantities", "IfcWall": "Qto_WallBaseQuantities", "IfcCourse": "Qto_CourseBaseQuantities",
       "IfcRailing": "Qto_RailingBaseQuantities"}
CONCRETE_PSET = ("IfcBeam", "IfcSlab", "IfcColumn", "IfcPile", "IfcWall", "IfcRailing")
MATERIAL = {"bearing": "氯丁橡胶（板式橡胶支座）", "temp_support": "钢砂筒", "expansion_joint": "型钢伸缩装置",
            "pavement": "C40 调平层 + 沥青混凝土"}


def gid(name):
    return ifcopenshell.guid.compress(uuid.uuid5(NS, name).hex)


def _pt(f, p):
    return f.createIfcCartesianPoint(tuple(float(v) for v in p))


def _dir(f, v):
    return f.createIfcDirection(tuple(float(x) for x in v))


def _axis(f, loc=(0.0, 0.0, 0.0), z=None, x=None):
    return f.createIfcAxis2Placement3D(_pt(f, loc), _dir(f, z) if z else None, _dir(f, x) if x else None)


# ---------------------------------------------------------------------- 几何
class Geometry:
    """按构件形状写 IFC 几何；截面、圆形轮廓按尺寸共用。"""

    def __init__(self, f, body):
        self.f, self.body = f, body
        self._circles, self._tsec = {}, None

    def circle(self, r):
        if r not in self._circles:
            self._circles[r] = self.f.createIfcCircleProfileDef(
                "AREA", "φ%d" % round(2000 * r), self.f.createIfcAxis2Placement2D(self.f.createIfcCartesianPoint((0.0, 0.0)), None),
                float(r))
        return self._circles[r]

    def tsection(self):
        if self._tsec is None:
            pts = [self.f.createIfcCartesianPoint((float(x), float(y))) for x, y in C.T_SECTION]
            self._tsec = self.f.createIfcArbitraryClosedProfileDef(
                "AREA", "T梁跨中截面", self.f.createIfcPolyline(pts + [pts[0]]))
        return self._tsec

    def shape(self, rep_type, items):
        return self.f.createIfcProductDefinitionShape(None, None, [
            self.f.createIfcShapeRepresentation(self.body, "Body", rep_type, items)])

    def element(self, e):
        """返回 (Representation, 定位原点)。"""
        f = self.f
        if e.shape == "cyl":
            solid = f.createIfcExtrudedAreaSolid(self.circle(e.params["r"]), _axis(f), _dir(f, (0, 0, 1)),
                                                 float(e.params["h"]))
            return self.shape("SweptSolid", [solid]), e.params["c"]
        if e.shape == "box":
            p = e.params
            prof = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(
                f.createIfcCartesianPoint((0.0, 0.0)), None), float(p["l"]), float(p["w"]))
            solid = f.createIfcExtrudedAreaSolid(prof, _axis(f, z=(0, 0, 1), x=(p["dir"][0], p["dir"][1], 0.0)),
                                                 _dir(f, (0, 0, 1)), float(p["h"]))
            return self.shape("SweptSolid", [solid]), p["c"]
        if e.cls == "girder":
            return self.girder(e), e.params["p0"]
        origin = e.params["solids"][0]["v"][0]
        items = []
        for sol in e.params["solids"]:
            coords = [tuple(float(p[i] - origin[i]) for i in range(3)) for p in sol["v"]]
            faces = [f.createIfcIndexedPolygonalFace(tuple(i + 1 for i in fc)) for fc in sol["f"]]
            items.append(f.createIfcPolygonalFaceSet(f.createIfcCartesianPointList3D(coords), True, faces, None))
        return self.shape("Tessellation", items), origin

    def girder(self, e):
        """T 形截面在「横向 N、竖向 z」平面内，沿梁轴斜向拉伸；两端按径向支承线切。
        定位原点取梁轴起点 p0，拉伸体前后各多伸 EXT，再用两个半空间切掉。"""
        f = self.f
        p0, p1, u = e.params["p0"], e.params["p1"], e.params["u"]
        S = e.attrs["length"]
        D = tuple((p1[i] - p0[i]) / S for i in range(3))
        dh = math.hypot(D[0], D[1])
        N = (-u[1], u[0], 0.0)
        EXT = 0.1
        pos = _axis(f, loc=tuple(-EXT * D[i] for i in range(3)), z=(u[0], u[1], 0.0), x=N)
        solid = f.createIfcExtrudedAreaSolid(self.tsection(), pos, _dir(f, (0.0, D[2], dh)), float(S + 2 * EXT))
        ta, tb = e.params["ta"], e.params["tb"]
        for loc, normal in (((0.0, 0.0, 0.0), (-ta[0], -ta[1], 0.0)),
                            (tuple(p1[i] - p0[i] for i in range(3)), (tb[0], tb[1], 0.0))):
            # 法向朝梁外；AgreementFlag = .F.：半空间的材料在法向所指一侧，即被切掉的那一侧
            half = f.createIfcHalfSpaceSolid(f.createIfcPlane(_axis(f, loc=loc, z=normal)), False)
            solid = f.createIfcBooleanClippingResult("DIFFERENCE", solid, half)
        return self.shape("Clipping", [solid])


# ---------------------------------------------------------------------- 路线
def build_alignment(f):
    al = ifcopenshell.api.alignment.create(f, "主线", include_vertical=True, start_station=C.START_STATION)
    al.Description = "直线 %.0f m + 回旋线 %.0f m + 圆曲线 R=%.0f m + 回旋线 %.0f m + 直线；竖曲线 %.0f m" % (
        C.H_SEGMENTS[0][1], C.LS, C.R_CURVE, C.LS, max(C.V_CURVE_LENGTH))
    h = ifcopenshell.api.alignment.get_horizontal_layout(al)
    for seg in AL.SEGMENTS:
        r0 = 0.0 if seg.k0 == 0 else 1.0 / seg.k0
        r1 = 0.0 if seg.k1 == 0 else 1.0 / seg.k1
        ifcopenshell.api.alignment.create_layout_segment(f, h, f.createIfcAlignmentHorizontalSegment(
            None, None, _pt(f, (seg.x0, seg.y0)), float(seg.th0), float(r0), float(r1), float(seg.L), None, seg.kind))
    v = ifcopenshell.api.alignment.get_vertical_layout(al)
    pv, lens = C.V_PVI, C.V_CURVE_LENGTH
    grades = [(pv[i + 1][1] - pv[i][1]) / (pv[i + 1][0] - pv[i][0]) for i in range(len(pv) - 1)]
    x0 = pv[0][0]
    for i in range(len(pv) - 1):
        L_in = lens[i] / 2.0 if i > 0 else 0.0
        L_out = lens[i + 1] / 2.0 if i + 1 < len(pv) - 1 else 0.0
        s0, s1 = pv[i][0] + L_in, pv[i + 1][0] - L_out
        ifcopenshell.api.alignment.create_layout_segment(f, v, f.createIfcAlignmentVerticalSegment(
            None, None, float(s0 - x0), float(s1 - s0), float(AL.profile(s0)[0]), float(grades[i]), float(grades[i]),
            None, "CONSTANTGRADIENT"))
        if L_out > 0:
            g1, g2 = grades[i], grades[i + 1]
            ifcopenshell.api.alignment.create_layout_segment(f, v, f.createIfcAlignmentVerticalSegment(
                None, None, float(s1 - x0), float(2 * L_out), float(AL.profile(s1)[0]), float(g1), float(g2),
                float(2 * L_out / (g2 - g1)), "PARABOLICARC"))
    return al


def linear_placement(f, curve, dist):
    """沿路线的 IfcLinearPlacement，附带算好的笛卡尔位置（CartesianPosition）。"""
    st = ifcopenshell.geom.settings()
    ev = ifcopenshell_wrapper.function_item_evaluator(st, ifcopenshell_wrapper.map_shape(st, curve.wrapped_data))
    m = numpy.array(ev.evaluate(float(dist)))
    lp = f.createIfcLinearPlacement(None, f.createIfcAxis2PlacementLinear(
        f.createIfcPointByDistanceExpression(f.createIfcLengthMeasure(float(dist)), None, None, None, curve), None, None))
    lp.CartesianPosition = _axis(f, loc=(m[0, 3], m[1, 3], m[2, 3]), z=(m[0, 2], m[1, 2], m[2, 2]),
                                 x=(m[0, 0], m[1, 0], m[2, 0]))
    return lp


# ---------------------------------------------------------------------- 属性
def _barrier_length(e):
    v = e.params["solids"][0]["v"]
    mids = [((v[i][0] + v[i + 1][0]) / 2, (v[i][1] + v[i + 1][1]) / 2, (v[i][2] + v[i + 1][2]) / 2)
            for i in range(0, len(v), 4)]
    return sum(math.sqrt(sum((b[j] - a[j]) ** 2 for j in range(3))) for a, b in zip(mids, mids[1:]))


def quantities(e):
    q = {"NetVolume": float(e.volume)} if e.cls not in ("bearing", "temp_support", "expansion_joint") else {}
    a = e.attrs
    if e.cls == "girder":
        q.update({"Length": float(a["length"]), "CrossSectionArea": float(section_area()),
                  "NetWeight": float(e.volume * C.RC_DENSITY_T * 1000.0)})
    elif e.cls in ("column", "pile"):
        q.update({"Length": float(e.params["h"]), "CrossSectionArea": math.pi * e.params["r"] ** 2})
    elif e.cls in ("cap", "abut_cap"):
        q["Length"] = float(C.CAP_LEN)
    elif e.cls in ("wet_joint", "cantilever"):
        q["Depth"] = float(C.WET_JOINT_T)
    elif e.cls == "pavement":
        q = {"Volume": float(e.volume), "Thickness": float(C.PAVEMENT_T)}
    elif e.cls == "barrier":
        q = {"Length": _barrier_length(e)}
    elif e.cls == "backwall":
        q.update({"Length": float(C.CAP_LEN), "Width": float(C.BACKWALL_T), "Height": float(a["height"])})
    return q


def own_props(e, plan, sup):
    """BridgeBIM_Element：每个构件都有的编号、归属；再加各类构件自己的数据。"""
    a = e.attrs
    p = {"ElementID": e.eid, "Category": NAMES[e.cls], "Deck": DECK_NAMES[e.deck]}
    if "span" in a:
        p["Span"] = int(a["span"])
    if "unit" in a:
        p["Unit"] = "第%d联" % a["unit"]
    if "support" in a:
        p["Support"] = support_name(a["support"])
    if e.cls == "girder":
        r = plan[e.eid]
        end = {"E": "伸缩端", "C": "连续端"}
        p.update({"Family": a["family"], "PrecastLength": float(a["length"]), "Position": int(a["girder"]),
                  "EndA": end[a["end_a"]], "EndB": end[a["end_b"]], "EndGapA": float(a["half_a"]),
                  "EndGapB": float(a["half_b"]), "EndSkewA": float(a["skew_a"]), "EndSkewB": float(a["skew_b"]),
                  "Weight_t": round(e.volume * C.RC_DENSITY_T, 3), "Bed": int(r["bed"]),
                  "CastDate": r["cast"].isoformat(), "ToStorageDate": r["to_storage"].isoformat(),
                  "ErectDate": r["erect"].isoformat(), "ErectOrder": int(r["order"]),
                  "StorageDays": int(r["storage_days"]), "AgeAtErection": int(r["age_at_erect"])})
    elif e.cls == "bearing":
        b = sup[e.eid]
        p.update({"Kind": "连续墩永久支座" if b["kind"] == "cont" else "伸缩端永久支座", "Size": b["size"],
                  "Girders": "/".join(b["girders"]), "TopElevation": float(b["top"]),
                  "SeatTopElevation": float(b["seat_top"]), "SeatHeight": float(b["seat_height"])})
    elif e.cls == "temp_support":
        b = sup[e.eid]
        p.update({"Girder": b["girders"][0], "TopElevation": float(b["top"]), "Height": float(b["height"]),
                  "Removal": "体系转换时拆除"})
    elif e.cls == "seat":
        p.update({"Bearing": a["bearing"], "Height": float(a["height"])})
    elif e.cls in ("cap", "abut_cap"):
        fr = e.params["frame"]
        p.update({"TopElevationAtCentre": float(a["top"]), "BottomElevation": float(a["bottom"]),
                  "CrossSlope": float(fr["slope"]), "SupportKind": {"A": "桥台", "T": "过渡墩", "C": "连续墩"}[a["kind"]]})
    elif e.cls == "continuity":
        p.update({"JointWidthMin": float(a["joint_min"]), "JointWidthMax": float(a["joint_max"])})
    elif e.cls == "expansion_joint":
        p.update({"Gap": float(a["gap"]), "Length": float(a["length"])})
    elif e.cls in ("wet_joint",):
        p.update({"WidthStart": float(a["width_a"]), "WidthEnd": float(a["width_b"])})
    return p


# ---------------------------------------------------------------------- 主流程
def build_ifc(r):
    f = ifcopenshell.file(schema="IFC4X3_ADD2")
    f.header.file_name.name = "bridge_bim.ifc"
    f.header.file_name.time_stamp = TIMESTAMP
    f.header.file_name.preprocessor_version = "IfcOpenShell %s" % ifcopenshell.version
    f.header.file_name.originating_system = "bridge-bim/scripts/export_ifc.py"

    api = ifcopenshell.api
    project = api.root.create_entity(f, ifc_class="IfcProject", name="高速公路 T 梁桥 BIM（虚构示例）")
    project.Description = "%d×%.0f m 预应力混凝土 T 梁，先简支后连续，分 %d 联；左右幅分离" % (
        C.N_SPANS, C.SPAN, len(C.UNITS))
    api.unit.assign_unit(f, units=[api.unit.add_si_unit(f, t) for t in
                                   ("LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT", "PLANEANGLEUNIT", "MASSUNIT",
                                    "FORCEUNIT")])
    units = f.by_type("IfcUnitAssignment")[0]
    si = {u.UnitType: u for u in units.Units}
    units.Units = list(units.Units) + [api.unit.add_derived_unit(f, "LINEARFORCEUNIT", None,
                                                                 {si["FORCEUNIT"]: 1, si["LENGTHUNIT"]: -1})]
    model_ctx = api.context.add_context(f, context_type="Model")
    graph = api.context.add_context(f, context_type="Model", context_identifier="Reference", target_view="GRAPH_VIEW",
                                    parent=model_ctx)
    body = api.context.add_context(f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW",
                                   parent=model_ctx)
    al = build_alignment(f)
    curve = ifcopenshell.api.alignment.get_curve(al)          # IfcGradientCurve：平面 + 竖向，墩台按它定位

    site = api.root.create_entity(f, ifc_class="IfcSite", name="项目场地")
    st = support_stations()
    bridge = api.root.create_entity(f, ifc_class="IfcBridge", name="%s – %s 大桥" % (
        AL.station_label(st[0]), AL.station_label(st[-1])), predefined_type="GIRDER")
    bridge.Description = "%s，桥长 %.0f m" % (" + ".join("%d×%.0f m" % (n, C.SPAN) for n in C.UNITS), st[-1] - st[0])
    api.aggregate.assign_object(f, relating_object=project, products=[site])
    api.aggregate.assign_object(f, relating_object=site, products=[bridge])

    def part(name, ptype, usage, parent, desc=None):
        """UsageType：上下部结构是竖向划分，左右幅是横向划分，分联与墩台沿路线纵向划分。"""
        p = api.root.create_entity(f, ifc_class="IfcBridgePart", name=name, predefined_type=ptype)
        p.Description, p.UsageType = desc, usage
        api.aggregate.assign_object(f, relating_object=parent, products=[p])
        return p

    sup_part = part("上部结构", "SUPERSTRUCTURE", "VERTICAL", bridge)
    sub_part = part("下部结构", "SUBSTRUCTURE", "VERTICAL", bridge)
    decks, segs = {}, {}
    for d, _ in C.DECKS:
        decks[d] = part(DECK_NAMES[d], "DECK", "LATERAL", sup_part)
        for u, (a, b) in enumerate(unit_bounds(), 1):
            segs[(d, u)] = part("%s第%d联" % (DECK_NAMES[d], u), "DECK_SEGMENT", "LONGITUDINAL", decks[d],
                                "第 %d–%d 跨，%d×%.0f m" % (a + 1, b, b - a, C.SPAN))
    supports, lin = {}, []
    for k, s in enumerate(st):
        kind = support_kind(k)
        supports[k] = part(support_name(k), "ABUTMENT" if kind == "A" else "PIER", "LONGITUDINAL", sub_part,
                           "%s %s" % ({"A": "桥台", "T": "过渡墩（伸缩缝）", "C": "连续墩"}[kind], AL.station_label(s)))
        lin.append((supports[k], s - C.START_STATION))
        ifcopenshell.api.alignment.add_stationing_referent(f, al, s - C.START_STATION, s, AL.station_label(s),
                                                           supports[k])

    # ------------------------------------------------------------------ 构件
    geo = Geometry(f, body)
    plan = r["plan"]
    sup = {b["id"]: b for b in r["sup"]}
    mats = {}
    by_container, by_material, placements, products = {}, {}, [], {}
    for e in r["els"]:
        ifc_cls, ptype, otype = IFC_CLASS[e.cls]
        ent = api.root.create_entity(f, ifc_class=ifc_cls, name=e.eid, predefined_type=ptype)
        if otype:
            ent.ObjectType = otype
        a = e.attrs
        ent.Description = NAMES[e.cls] + (" %s %.2f m" % (a["family"], a["length"]) if e.cls == "girder" else "")
        if ifc_cls == "IfcPile":
            ent.ConstructionType = "CAST_IN_PLACE"
        rep, origin = geo.element(e)
        ent.Representation = rep
        placements.append((ent, origin))
        products[e.eid] = ent

        own = api.pset.add_pset(f, product=ent, name="BridgeBIM_Element")
        api.pset.edit_pset(f, pset=own, properties=own_props(e, plan, sup))
        if ifc_cls in CONCRETE_PSET:
            ps = api.pset.add_pset(f, product=ent, name="Pset_ConcreteElementGeneral")
            api.pset.edit_pset(f, pset=ps, properties={"StrengthClass": C.CONCRETE_GRADE[e.cls],
                                                       "CastingMethod": "PRECAST" if e.cls == "girder" else "INSITU"})
        if e.cls == "girder":
            ps = api.pset.add_pset(f, product=ent, name="Pset_BeamCommon")
            p0, p1 = e.params["p0"], e.params["p1"]
            api.pset.edit_pset(f, pset=ps, properties={
                "Reference": "%s-%.2f" % (a["family"], a["length"]), "Status": "NEW", "Span": float(a["length"]),
                "Slope": math.atan2(p1[2] - p0[2], a["plan_length"]), "LoadBearing": True, "IsExternal": True})
            ps = api.pset.add_pset(f, product=ent, name="Pset_PrecastConcreteElementGeneral")
            api.pset.edit_pset(f, pset=ps, properties={"TypeDesignation": "%s %.2f m" % (a["family"], a["length"]),
                                                       "PieceMark": e.eid})
        q = quantities(e)
        if q and ifc_cls in QTO:
            qto = api.pset.add_qto(f, product=ent, name=QTO[ifc_cls])
            api.pset.edit_qto(f, qto=qto, properties=q)
        if "NetVolume" not in q and "Volume" not in q:
            # 这几类的标准工程量集里没有体积（护栏只有长度，支座等没有标准集）：体积放进
            # Qto_BodyGeometryValidation——它本来就是给接收方拿自己算的几何体积来核对用的
            qto = api.pset.add_qto(f, product=ent, name="Qto_BodyGeometryValidation")
            api.pset.edit_qto(f, qto=qto, properties={"NetVolume": float(e.volume)})

        if e.part.startswith("SUP-"):
            container = decks[e.deck] if e.cls == "expansion_joint" else segs[(e.deck, a["unit"])]
        else:
            container = supports[a["support"]]
        by_container.setdefault(container.id(), (container, []))[1].append(ent)
        mname = MATERIAL.get(e.cls) or "%s 混凝土" % C.CONCRETE_GRADE[e.cls]
        by_material.setdefault(mname, []).append(ent)

    for _, (container, ents) in sorted(by_container.items()):
        api.spatial.assign_container(f, relating_structure=container, products=ents)
    for mname in sorted(by_material):
        m = mats.get(mname) or api.material.add_material(f, name=mname)
        api.material.assign_material(f, products=by_material[mname], type="IfcMaterial", material=m)

    build_schedule(f, r, products)
    build_structural(f, r, products, bridge, graph)

    # 定位最后写：assign_container 会把已有定位改写成相对容器的定位并新建实体，且按集合顺序处理。
    for ent, origin in placements:
        ent.ObjectPlacement = f.createIfcLocalPlacement(None, _axis(f, loc=origin))
    for p, dist in lin:
        p.ObjectPlacement = linear_placement(f, curve, dist)

    _normalize_sets(f)
    _stable_ids(f)
    return f


def _dt(d, hour):
    return "%sT%02d:00:00" % (d.isoformat(), hour)


def _at(d, hours):
    """日期 d 的开工时刻再过 hours 小时。"""
    return datetime.datetime.combine(d, datetime.time(C.DAY_START_HOUR)) + datetime.timedelta(hours=hours)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _duration(td):
    """timedelta → ISO 8601 时长（P…DT…H…M）。"""
    secs = int(round(td.total_seconds()))
    if secs < 0:
        raise ValueError("时差为负：%s" % td)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return "P%dDT%dH%dM" % (d, h, m)


# ---------------------------------------------------------------------- 结构分析
def _line_axis(r, L):
    """梁位线的一维坐标 x → 梁轴（梁顶中心线）上的三维点，再竖向偏移 dz 到截面形心。
    预制梁段在梁顶弦线上线性插值，连续段在前后两片梁的梁端点之间插值。"""
    by = r["by_id"]
    segs = L["segs"]
    ends = []
    for i, sg in enumerate(segs):
        if sg["kind"] == "girder":
            g = by[sg["eid"]]
            ends.append((sg["x0"], sg["x1"], g.params["p0"], g.params["p1"]))
        else:
            ends.append((sg["x0"], sg["x1"], by[segs[i - 1]["eid"]].params["p1"], by[segs[i + 1]["eid"]].params["p0"]))

    def at(x, dz):
        for x0, x1, a, b in ends:
            if x0 - 1e-9 <= x <= x1 + 1e-9:
                t = (x - x0) / (x1 - x0)
                return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2]) + dz)
        raise ValueError("x = %.6f 不在梁位线上" % x)
    return at


def build_structural(f, r, products, bridge, ctx):
    """两个施工阶段的结构分析模型，见模块说明。返回 {名称: 实体}，测试里用。"""
    api = ifcopenshell.api
    res = structure(r)
    origin = f.createIfcLocalPlacement(None, _axis(f))
    up = _dir(f, (0.0, 0.0, 1.0))
    out = {}

    def cond(name, x, y, z):
        """平面梁位线在三维里的约束：竖向 z，横向 y，纵向 x 只在每根梁（每条梁位线）的第一个支承点固定；
        绕梁轴转动固定（抗扭），两个弯曲转动放开。"""
        b = [f.createIfcBoolean(v) for v in (x, y, z, True, False, False)]
        return f.createIfcBoundaryNodeCondition(name, *b)

    bc_fix = cond("固定支承：纵、横、竖向固定，绕梁轴转动固定", True, True, True)
    bc_slide = cond("活动支承：横、竖向固定，绕梁轴转动固定，纵向放开", False, True, True)

    def topo(kind, items):
        return f.createIfcProductDefinitionShape(None, None, [f.createIfcTopologyRepresentation(ctx, "Reference", kind, items)])

    def point(name, xyz, condition=None, desc=None):
        v = f.createIfcVertexPoint(_pt(f, xyz))
        c = api.root.create_entity(f, ifc_class="IfcStructuralPointConnection", name=name)
        c.ObjectPlacement, c.Representation, c.AppliedCondition, c.Description = origin, topo("Vertex", [v]), condition, desc
        out[name] = c
        return c, v

    def member(name, v0, v1, desc, sec_a, sec_i):
        m = api.root.create_entity(f, ifc_class="IfcStructuralCurveMember", name=name, predefined_type="RIGID_JOINED_MEMBER")
        m.ObjectPlacement, m.Representation, m.Axis, m.Description = origin, topo("Edge", [f.createIfcEdge(v0, v1)]), up, desc
        ps = api.pset.add_pset(f, product=m, name="BridgeBIM_AnalyticalMember")
        api.pset.edit_pset(f, pset=ps, properties={"SectionArea_m2": float(sec_a), "MomentOfInertia_m4": float(sec_i),
                                                   "ElasticModulus_kN_m2": float(C.E_C50)})
        out[name] = m
        return m

    def connect(m, c):
        api.structural.add_structural_member_connection(f, relating_structural_member=m, related_structural_connection=c)

    def activity(cls, name, element, rep_items, kind, load, **attrs):
        a = api.root.create_entity(f, ifc_class=cls, name=name)
        a.ObjectPlacement, a.Representation, a.AppliedLoad, a.GlobalOrLocal = origin, topo(kind, rep_items), load, "GLOBAL_COORDS"
        for k_, v_ in attrs.items():
            setattr(a, k_, v_)
        rel = api.root.create_entity(f, ifc_class="IfcRelConnectsStructuralActivity")
        rel.RelatingElement, rel.RelatedStructuralActivity = element, a
        out[name] = a
        return a

    def line_load(name, m, w):
        load = f.createIfcStructuralLoadLinearForce(name, 0.0, 0.0, -w * 1000.0, 0.0, 0.0, 0.0)
        return activity("IfcStructuralCurveAction", name, m, list(m.Representation.Representations[0].Items), "Edge",
                        load, PredefinedType="CONST", ProjectedOrTrue="PROJECTED_LENGTH")   # 线荷载按水平投影长度计（计算用的是平面坐标）

    def point_load(name, m, xyz, P):
        load = f.createIfcStructuralLoadSingleForce(name, 0.0, 0.0, -P * 1000.0, 0.0, 0.0, 0.0)
        return activity("IfcStructuralPointAction", name, m, [f.createIfcVertexPoint(_pt(f, xyz))], "Vertex", load)

    def reaction(name, c, v, R):
        load = f.createIfcStructuralLoadSingleForce(name, 0.0, 0.0, R * 1000.0, 0.0, 0.0, 0.0)
        return activity("IfcStructuralPointReaction", name, c, [v], "Vertex", load)

    def group(name, desc, ptype, action_type, source, purpose=None):
        g = api.root.create_entity(f, ifc_class="IfcStructuralLoadGroup", name=name, predefined_type=ptype)
        g.Description, g.ActionType, g.ActionSource, g.Purpose = desc, action_type, source, purpose
        out[name] = g
        return g

    def results(name, desc, for_group):
        g = api.root.create_entity(f, ifc_class="IfcStructuralResultGroup", name=name)
        g.Description, g.TheoryType, g.ResultForLoadGroup, g.IsLinear = desc, "FIRST_ORDER_THEORY", for_group, True
        out[name] = g
        return g

    def model(name, desc, groups, result_groups):
        m = api.structural.add_structural_analysis_model(f)
        m.Name, m.Description, m.PredefinedType, m.SharedPlacement = name, desc, "LOADING_3D", origin
        m.LoadedBy, m.HasResults = groups, result_groups
        api.structural.assign_to_building(f, structural_analysis_model=m, building=bridge)
        out[name] = m
        return m

    links = {}                          # 物理构件 → 分析对象

    def link(eid, obj):
        links.setdefault(eid, []).append(obj)

    # ------------------------------------------------------------------ 阶段一：预制梁简支
    g1 = group("一期恒载", "预制梁 + 横隔板 + 湿接缝 + 翼缘现浇段，构件体积 × 26 kN/m³；由简支的预制梁承担",
               "LOAD_CASE", "PERMANENT_G", "DEAD_LOAD_G")
    r1 = results("阶段一支座反力", "一期恒载下永久支座（伸缩端）与临时支座（连续端）的反力", g1)
    items1, acts1, reac1 = [], [], []
    for x in res["lines"]:
        L = x["L"]
        at = _line_axis(r, L)
        y0 = L["sec"]["y0"]
        R1 = {b["id"]: b["R_G1"] for b in x["bearings"]}
        R1.update({t["id"]: t["R_G1"] for t in x["temps"]})
        for g in L["girders"]:
            (sa, xa_s), (sb, xb_s) = g["sup"]
            xs_ = [g["xa"], xa_s, xb_s, g["xb"]]
            pts = []
            for j, xv in enumerate(xs_):
                sid = sa if j == 1 else sb if j == 2 else None
                c, v = point("S1-%s-P%d" % (g["eid"], j), at(xv, y0), (bc_fix if j == 1 else bc_slide) if sid else None,
                             sid)
                pts.append((c, v))
                items1.append(c)
                if sid:
                    link(sid, c)
                    reac1.append(reaction("R1-%s" % sid, c, v, R1[sid]))
            for j in range(3):
                m = member("S1-%s-%d" % (g["eid"], j + 1), pts[j][1], pts[j + 1][1],
                           ("外伸段" if j != 1 else "支座间") + "，预制截面", L["sec"]["A0"], L["sec"]["I0"])
                connect(m, pts[j][0])
                connect(m, pts[j + 1][0])
                items1.append(m)
                link(g["eid"], m)
                acts1.append(line_load("G1-" + m.Name, m, g["w1"]))
                for q in g["dia"]:
                    if xs_[j] <= q["x"] < xs_[j + 1] or (j == 2 and q["x"] == xs_[3]):
                        acts1.append(point_load("D-%s-%s" % (q["eid"], g["eid"]), m, at(q["x"], y0), q["P"]))
    api.group.assign_group(f, products=acts1, group=g1)
    api.group.assign_group(f, products=reac1, group=r1)
    m1 = model("施工阶段一：预制梁简支", "每片预制梁两端支承（伸缩端永久支座、连续端临时支座），承担一期恒载", [g1], [r1])
    api.structural.assign_structural_analysis_model(f, products=items1, structural_analysis_model=m1)

    # ------------------------------------------------------------------ 阶段二：体系转换后的连续梁
    cv = group("体系转换：拆除临时支座", "临时支座的阶段一反力反向加到连续梁上", "LOAD_CASE", "PERMANENT_G", "PROPPING")
    g2 = group("二期恒载", "铺装（沥青 24、调平层 25 kN/m³）+ 护栏（26 kN/m³），每跨五片梁均分", "LOAD_CASE",
               "PERMANENT_G", "COMPLETION_G1")
    ck = group("支座反力标准值组合", "Rck = 结构重力（一期 + 体系转换 + 二期 + 连续段自重）+ 汽车荷载（公路-I级，计冲击）；"
               "汽车荷载按影响线包络，不在这里列作荷载", "LOAD_COMBINATION", "NOTDEFINED", "NOTDEFINED", "支座压应力验算")
    r2 = results("支座反力 Rck", "永久支座反力标准值组合（含冲击），用于板式橡胶支座压应力验算", ck)
    items2, acts_cv, acts_g2, reac2 = [], [], [], []
    for x in res["lines"]:
        L = x["L"]
        at = _line_axis(r, L)
        yc = L["sec"]["yc"]
        tag = "S2-%s%d-%d" % (L["deck"], L["unit"], L["line"])
        perm = {round(b["x"], 9): b for b in L["perm"]}
        keys = sorted({0.0, L["xs"][-1]} | {g["xa"] for g in L["girders"]} | {g["xb"] for g in L["girders"]}
                      | {b["x"] for b in L["perm"]})
        pts = []
        first = min(b["x"] for b in L["perm"])
        rck = {b["id"]: b["Rck"] for b in x["bearings"]}
        for j, xv in enumerate(keys):
            b = perm.get(round(xv, 9))
            c, v = point("%s-P%02d" % (tag, j), at(xv, yc), (bc_fix if xv == first else bc_slide) if b else None,
                         b["id"] if b else None)
            pts.append((c, v))
            items2.append(c)
            if b:
                link(b["id"], c)
                reac2.append(reaction("RCK-%s" % b["id"], c, v, rck[b["id"]]))
        mems = []
        for j in range(len(keys) - 1):
            xm = (keys[j] + keys[j + 1]) / 2
            sg = next(sg for sg in L["segs"] if sg["x0"] <= xm <= sg["x1"])
            joint = sg["kind"] == "joint"
            m = member("%s-%02d" % (tag, j + 1), pts[j][1], pts[j + 1][1],
                       ("墩顶现浇连续段，实心截面" if joint else "预制梁 %s，组合截面" % sg["eid"]),
                       L["sec"]["Aj"] if joint else L["sec"]["A"], L["sec"]["Ij"] if joint else L["sec"]["I"])
            connect(m, pts[j][0])
            connect(m, pts[j + 1][0])
            items2.append(m)
            mems.append((keys[j], keys[j + 1], m))
            link(("CS-%s-%s" % (support_name(sg["support"]), L["deck"])) if joint else sg["eid"], m)
            e = next(e_ for e_ in range(len(L["xs"]) - 1) if L["xs"][e_] <= xm <= L["xs"][e_ + 1])
            acts_g2.append(line_load("G2-" + m.Name, m, L["w2"][e]))
        R1 = {t["id"]: t["R_G1"] for t in x["temps"]}
        for t in L["temps"]:
            m = next(m_ for a, b, m_ in mems if a <= t["x"] <= b)
            acts_cv.append(point_load("CV-%s" % t["id"], m, at(t["x"], yc), R1[t["id"]]))
    api.group.assign_group(f, products=acts_cv, group=cv)
    api.group.assign_group(f, products=acts_g2, group=g2)
    api.group.assign_group(f, products=reac2, group=r2)
    m2 = model("施工阶段二：体系转换后的连续梁", "每条梁位线一联四跨连续梁，永久支座承力；承担体系转换、二期恒载与汽车荷载",
               [cv, g2, ck], [r2])
    api.structural.assign_structural_analysis_model(f, products=items2, structural_analysis_model=m2)

    # ------------------------------------------------------------------ 分析对象 ↔ 物理构件、构件上的计算结果
    for eid in sorted(links):
        rel = api.root.create_entity(f, ifc_class="IfcRelAssignsToProduct")
        rel.RelatingProduct, rel.RelatedObjects = products[eid], links[eid]
    for row in girder_force_rows(r):
        ps = api.pset.add_pset(f, product=products[row["girder"]], name="BridgeBIM_Structural")
        api.pset.edit_pset(f, pset=ps, properties={
            "FirstStageLoad_kN_m": float(row["g1_kN_m"]), "DiaphragmLoad_kN": float(row["diaphragm_kN"]),
            "MomentFirstStageMax_kNm": float(row["M_G1_max"]), "MomentDeadMax_kNm": float(row["M_G_max"]),
            "DesignMomentSaggingMax_kNm": float(row["M_ud_pos"]), "DesignMomentHoggingMin_kNm": float(row["M_ud_neg"]),
            "DesignShearMax_kN": float(row["V_ud_max"]), "LiveDeflection_mm": float(row["deflection_mm"]),
            "LiveDeflectionLimit_mm": float(row["deflection_limit_mm"]), "DeflectionRatio": float(row["deflection_ratio"])})
    for row in bearing_force_rows(r):
        if row["kind"] == "临时支座":
            props = {"ReactionFirstStage_kN": float(row["R_G1"])}
        else:
            props = {"ReactionDead_kN": float(row["R_G"]), "ReactionLiveMax_kN": float(row["R_Q_max"]),
                     "ReactionLiveMin_kN": float(row["R_Q_min"]), "ImpactFactor": float(row["mu"]),
                     "Rck_kN": float(row["Rck"]), "ReactionUltimateMin_kN": float(row["R_ud_min"]),
                     "EffectiveArea_m2": float(row["Ae_m2"]), "MeanPressure_MPa": float(row["sigma_MPa"]),
                     "PressureLimit_MPa": float(C.SIGMA_C), "Utilisation": float(row["utilisation"]),
                     "RequiredSize_m": float(row["size_required_m"])}
        ps = api.pset.add_pset(f, product=products[row["bearing"]], name="BridgeBIM_Structural")
        api.pset.edit_pset(f, pset=ps, properties=props)
    return out


def build_schedule(f, r, products):
    """任务的开始 / 结束直接取自 bridge.schedule 的排程。任务间关系另写 IfcRelSequence（完成—开始），
    时差按两端实际时刻算出——不用 API 的 assign_sequence：它会按前置任务「连锁」改写后续任务的日期，
    把架梁提前到预制完成的当天。"""
    sq = ifcopenshell.api.sequence
    wp = sq.add_work_plan(f, name="施工计划（工效为假设值）", predefined_type="PLANNED")
    ws = sq.add_work_schedule(f, name="梁场预制与架梁 4D 计划", predefined_type="PLANNED", work_plan=wp)
    convs = S.conversions(r["rows"])
    first = _at(min(row["cast"] for row in r["rows"]), 0)
    last = _at(max(m["conversion"] for m in convs), C.DAY_HOURS)
    for w in (wp, ws):                     # API 默认填当前时刻，固定下来才能逐字节复现
        w.CreationDate, w.StartTime, w.FinishTime = TIMESTAMP, _iso(first), _iso(last)
    span = {}

    def task(name, ident, parent=None, ptype="NOTDEFINED", start=None, finish=None):
        t = sq.add_task(f, work_schedule=None if parent else ws, parent_task=parent, name=name, predefined_type=ptype)
        t.Identification = ident             # add_task 挂在父任务下时会自动编号，这里用自己的编号
        if start is not None:
            tt = sq.add_task_time(f, task=t)
            tt.DurationType = "ELAPSEDTIME"
            tt.ScheduleStart, tt.ScheduleFinish = _iso(start), _iso(finish)
            tt.ScheduleDuration = _duration(finish - start)
            span[t.id()] = (start, finish)
        return t

    def after(pred, succ):
        lag = f.createIfcLagTime(None, None, None, f.createIfcDuration(_duration(span[succ.id()][0] - span[pred.id()][1])),
                                 "ELAPSEDTIME")
        f.createIfcRelSequence(ifcopenshell.guid.new(), None, None, None, pred, succ, lag, "FINISH_START", None)

    root_yard = task("梁场预制", "Y")
    root_erect = task("架梁", "E")
    root_conv = task("墩顶连续与体系转换", "C")
    groups = {}
    for d, _ in C.DECKS:
        for u in range(1, len(C.UNITS) + 1):
            groups[(d, u)] = task("架设 %s第%d联" % (DECK_NAMES[d], u), "E-%s%d" % (d, u), root_erect)
    prev = None
    unit_last = {}
    for row in r["rows"]:
        g = row["girder"]
        e_unit = r["by_id"][g].attrs["unit"]
        tp = task("预制 " + g, "Y%03d" % row["order"], root_yard, "CONSTRUCTION", _at(row["cast"], 0),
                  _at(row["to_storage"], 0))
        t0 = _at(row["erect"], row["erect_hour"])
        te = task("架设 " + g, "E%03d" % row["order"], groups[(row["deck"], e_unit)], "INSTALLATION", t0,
                  t0 + datetime.timedelta(hours=C.ERECT_HOURS))
        sq.assign_product(f, relating_product=products[g], related_object=te)
        after(tp, te)
        if prev is not None:
            after(prev, te)
        prev = te
        unit_last[(row["deck"], e_unit)] = te
    for m in convs:
        key = (m["deck"], m["unit"])
        label = "%s第%d联" % (DECK_NAMES[m["deck"]], m["unit"])
        tc = task("浇筑墩顶连续段 " + label, "C-%s%d-1" % key, root_conv, "CONSTRUCTION", _at(m["cast"], 0),
                  _at(m["cast"], C.DAY_HOURS))
        tk = task("体系转换 " + label, "C-%s%d-2" % key, root_conv, "USERDEFINED", _at(m["conversion"], 0),
                  _at(m["conversion"], C.DAY_HOURS))
        tk.ObjectType = "张拉负弯矩钢束、拆除临时支座"
        after(unit_last[key], tc)
        after(tc, tk)
        piers = [int(p[1:]) for p in m["piers"]]
        for e in r["els"]:
            if e.deck != m["deck"] or e.attrs.get("support") not in piers:
                continue
            if e.cls == "continuity":
                sq.assign_product(f, relating_product=products[e.eid], related_object=tc)
            elif e.cls == "temp_support":
                sq.assign_process(f, relating_process=tk, related_object=products[e.eid])
    # 汇总任务的起止 = 子任务的最早开始、最晚结束
    for parent in [root_yard, root_erect, root_conv] + list(groups.values()):
        kids = []
        stack = [parent]
        while stack:
            t = stack.pop()
            for rel in t.IsNestedBy:
                for k in rel.RelatedObjects:
                    stack.append(k)
                    if k.id() in span:
                        kids.append(span[k.id()])
        tt = sq.add_task_time(f, task=parent)
        tt.DurationType = "ELAPSEDTIME"
        s0, s1 = min(k[0] for k in kids), max(k[1] for k in kids)
        tt.ScheduleStart, tt.ScheduleFinish, tt.ScheduleDuration = _iso(s0), _iso(s1), _duration(s1 - s0)


# IFC 里这些属性是 SET（无序），ifcopenshell.api 用 Python 集合存，每次运行顺序不同。
# 落盘前按实体序号排好——实体序号由创建顺序决定，是确定的。LIST 属性（点序、面序、嵌套序）不动。
SET_ATTRS = {
    "IfcRelAggregates": ["RelatedObjects"],
    "IfcRelContainedInSpatialStructure": ["RelatedElements"],
    "IfcRelDefinesByProperties": ["RelatedObjects"],
    "IfcRelDeclares": ["RelatedDefinitions"],
    "IfcRelAssociatesMaterial": ["RelatedObjects"],
    "IfcRelAssignsToProduct": ["RelatedObjects"],
    "IfcRelAssignsToProcess": ["RelatedObjects"],
    "IfcRelAssignsToControl": ["RelatedObjects"],
    "IfcRelPositions": ["RelatedProducts"],
    "IfcUnitAssignment": ["Units"],
    "IfcPropertySet": ["HasProperties"],
    "IfcElementQuantity": ["Quantities"],
    "IfcShapeRepresentation": ["Items"],
    "IfcRelAssignsToGroup": ["RelatedObjects"],
    "IfcRelServicesBuildings": ["RelatedBuildings"],
    "IfcStructuralAnalysisModel": ["LoadedBy", "HasResults"],
}


def _normalize_sets(f):
    for cls, attrs in SET_ATTRS.items():
        for ent in f.by_type(cls, include_subtypes=False):
            for a in attrs:
                v = getattr(ent, a, None)
                if v and len(v) > 1:
                    setattr(ent, a, sorted(v, key=lambda x: x.id()))


def _stable_ids(f):
    """GlobalId 由内容推出：对象按「类别 | 名称 | 编号」，属性集按所属对象，关系按两端对象。"""
    seen = {}

    def put(ent, base):
        n = seen.get(base, 0)
        seen[base] = n + 1
        ent.GlobalId = gid(base + ("#%d" % n if n else ""))

    roots = f.by_type("IfcRoot")
    for e in roots:
        if not e.is_a("IfcRelationship") and not e.is_a("IfcPropertyDefinition"):
            put(e, "%s|%s|%s" % (e.is_a(), e.Name or "", getattr(e, "Identification", None) or ""))
    for e in roots:
        if e.is_a("IfcPropertyDefinition"):
            owner = e.DefinesOccurrence[0].RelatedObjects[0] if getattr(e, "DefinesOccurrence", None) else None
            put(e, "%s|%s|%s" % (e.is_a(), e.Name or "", owner.GlobalId if owner is not None else ""))
    for e in roots:
        if e.is_a("IfcRelationship"):
            ends = []
            for i in range(len(e)):
                v = e[i]
                if isinstance(v, ifcopenshell.entity_instance) and v.is_a("IfcRoot"):
                    ends.append(v.GlobalId)
                elif isinstance(v, tuple) and v and isinstance(v[0], ifcopenshell.entity_instance) and v[0].is_a("IfcRoot"):
                    ends.append(v[0].GlobalId)
            put(e, "%s|%s" % (e.is_a(), "|".join(ends)))


def export(path=MODEL_IFC, r=None):
    r = r or compute()
    f = build_ifc(r)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 自己写文件、固定 LF：ifcopenshell 的 f.write() 在 Windows 上写 CRLF。
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f.to_string())
    return f


def main():
    if "--check" in sys.argv:
        tmp = os.path.join(tempfile.mkdtemp(), "bridge_bim.ifc")
        export(tmp)
        a = open(tmp, "rb").read()
        b = open(MODEL_IFC, "rb").read() if os.path.exists(MODEL_IFC) else b""
        if a == b:
            print("PASS 重导的 IFC 与已提交文件逐字节一致（%d 字节）" % len(a))
            return
        ok, k, why = compare(b.decode("utf-8"), a.decode("utf-8"), "relative")
        if not ok:
            print("MISMATCH: 重导的 IFC 与已提交的 model/bridge_bim.ifc 不一致：%s" % why)
            sys.exit(1)
        print("PASS 重导的 IFC 与已提交文件一致：结构、编号、文字逐字相同，%d 个浮点数只在 1e-9（相对）以内不同"
              "（跨平台数学库的末位差）" % k)
        return
    f = export()
    print("写出 %s：%d 个实体，%d 字节" % (os.path.relpath(MODEL_IFC, ROOT), len(list(f)), os.path.getsize(MODEL_IFC)))


if __name__ == "__main__":
    main()
