"""已提交产物之间互相对得上：.3dm（rhino3dm 读回）、IFC（ifcopenshell 读回 + 几何引擎）、data/ 里的表。

需要 requirements.txt 里的 ifcopenshell 与 rhino3dm；CI 的 artifacts 任务跑这一组。
"""
import csv
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import unittest
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ifcopenshell                            # noqa: E402
import ifcopenshell.api.alignment              # noqa: E402
import ifcopenshell.api.alignment.util as AU   # noqa: E402
import ifcopenshell.geom                       # noqa: E402
import ifcopenshell.util.element as UE         # noqa: E402
import ifcopenshell.util.shape as US           # noqa: E402
import ifcopenshell.validate                   # noqa: E402
import rhino3dm                                # noqa: E402

from bridge import alignment as AL, config as C, schedule as S   # noqa: E402
from bridge.model import support_name, support_stations           # noqa: E402
from bridge.numcmp import compare                                 # noqa: E402
from bridge.pipeline import compute, element_rows                 # noqa: E402

IFC_PATH = os.path.join(ROOT, "model", "bridge_bim.ifc")
M3_PATH = os.path.join(ROOT, "model", "bridge_bim.3dm")


def rows(name):
    return list(csv.DictReader(open(os.path.join(ROOT, "data", name), encoding="utf-8")))


def run(*args):
    return subprocess.run([sys.executable] + list(args), capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"), cwd=ROOT)


R = compute()
BY = R["by_id"]
PLAN = R["plan"]
IFC = ifcopenshell.open(IFC_PATH)
M3 = rhino3dm.File3dm.Read(M3_PATH)


def model_bbox(e):
    if e.shape == "cyl":
        c, r, h = e.params["c"], e.params["r"], e.params["h"]
        return (c[0] - r, c[1] - r, c[2]), (c[0] + r, c[1] + r, c[2] + h)
    if e.shape == "box":
        p = e.params
        c, d = p["c"], p["dir"]
        pts = [(c[0] + a * d[0] * p["l"] / 2 - b * d[1] * p["w"] / 2, c[1] + a * d[1] * p["l"] / 2 + b * d[0] * p["w"] / 2)
               for a in (-1, 1) for b in (-1, 1)]
        return ((min(x for x, _ in pts), min(y for _, y in pts), c[2]),
                (max(x for x, _ in pts), max(y for _, y in pts), c[2] + p["h"]))
    vs = [v for s in e.params["solids"] for v in s["v"]]
    return tuple(min(v[i] for v in vs) for i in range(3)), tuple(max(v[i] for v in vs) for i in range(3))


class Reproducible(unittest.TestCase):
    def test_data_is_what_the_model_computes(self):
        r = run(os.path.join("scripts", "build_data.py"), "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_ifc_is_what_the_exporter_writes(self):
        r = run(os.path.join("scripts", "export_ifc.py"), "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Rhino3dm(unittest.TestCase):
    """.3dm 由 Rhino 8 写出；这里不用 Rhino，拿 rhino3dm 读回来逐件核。"""

    @classmethod
    def setUpClass(cls):
        cls.objs = {}
        for o in M3.Objects:
            lay = M3.Layers.FindIndex(o.Attributes.LayerIndex).FullPath
            if lay.startswith("上部结构") or lay.startswith("下部结构"):
                cls.objs.setdefault(o.Attributes.Name, []).append((o, lay))

    def test_one_object_per_element(self):
        self.assertEqual(sorted(self.objs), sorted(BY))
        self.assertTrue(all(len(v) == 1 for v in self.objs.values()))

    def test_every_attribute_equals_the_model(self):
        want = element_rows(R)
        for eid, row in want.items():
            o = self.objs[eid][0][0]
            got = {k: o.Attributes.GetUserString(k) for k in row}
            for k, v in row.items():
                ok, _, why = compare(got[k] or "", v, "decimal")
                self.assertTrue(ok, "%s.%s：%s（%s）" % (eid, k, why, got[k]))

    def test_girders_sit_on_their_length_spec_layer(self):
        for g in (e for e in R["els"] if e.cls == "girder"):
            lay = self.objs[g.eid][0][1]
            self.assertEqual(lay, "上部结构::预制T梁::%s %.2f m" % (g.attrs["family"], g.attrs["length"]))

    def test_geometry_bounding_boxes_match_the_model(self):
        worst = 0.0
        for eid, [(o, _)] in self.objs.items():
            bb = o.Geometry.GetBoundingBox()
            lo, hi = model_bbox(BY[eid])
            got = (bb.Min.X, bb.Min.Y, bb.Min.Z), (bb.Max.X, bb.Max.Y, bb.Max.Z)
            worst = max(worst, max(abs(got[k][i] - (lo, hi)[k][i]) for k in (0, 1) for i in range(3)))
        self.assertLess(worst, 1e-3)          # 网格顶点按单精度存盘时误差在 1e-4 m 量级

    def test_meshes_are_closed(self):
        meshes = [o.Geometry for v in self.objs.values() for o, _ in v if isinstance(o.Geometry, rhino3dm.Mesh)]
        self.assertEqual(len(meshes), sum(1 for e in R["els"] if e.shape == "mesh"))
        self.assertTrue(all(m.IsClosed for m in meshes))

    def test_no_local_paths_in_the_3dm(self):
        """.3dm 默认会写进本机存盘路径和渲染环境的贴图路径；公开仓库里不该有这些。
        允许的只有 Windows 的公共目录（C:/Users/Public），构建脚本故意先存到那里再复制进仓库。"""
        b = open(M3_PATH, "rb").read()
        for text in (b.decode("utf-8", "ignore"), b.decode("utf-16-le", "ignore"), b[1:].decode("utf-16-le", "ignore")):
            self.assertNotIn("AppData", text)
            self.assertNotIn("Desktop", text)
            users = set(re.findall(r"[A-Za-z]:[\\/]Users[\\/]([^\\/\x00]+)", text))
            self.assertLessEqual(users, {"Public"}, users)

    def test_build_log_and_images(self):
        log = json.load(open(os.path.join(ROOT, "model", "build_log.json"), encoding="utf-8"))
        self.assertTrue(log["ok"])
        for name in ("hero", "pier_detail", "erection_4d", "yard", "profile", "sections", "length_specs"):
            self.assertTrue(os.path.getsize(os.path.join(ROOT, "docs", "img", name + ".png")) > 50000, name)


class IfcSchema(unittest.TestCase):
    def test_schema_validation_reports_nothing(self):
        issues = []

        class H(logging.Handler):
            def emit(self, rec):
                issues.append(rec.getMessage())
        lg = logging.getLogger("ifc-validate-test")
        lg.addHandler(H())
        lg.setLevel(logging.DEBUG)
        ifcopenshell.validate.validate(IFC, lg, express_rules=False)
        self.assertEqual(issues, [])
        self.assertEqual(IFC.schema_identifier, "IFC4X3_ADD2")

    def test_classes_and_predefined_types(self):
        from importlib import util as iu
        spec = iu.spec_from_file_location("ex", os.path.join(ROOT, "scripts", "export_ifc.py"))
        ex = iu.module_from_spec(spec)
        spec.loader.exec_module(ex)
        want = Counter(ex.IFC_CLASS[e.cls][:2] for e in R["els"])
        got = Counter((p.is_a(), p.PredefinedType) for p in IFC.by_type("IfcElement"))
        self.assertEqual(got, want)
        names = {p.Name for p in IFC.by_type("IfcElement")}
        self.assertEqual(names, set(BY))

    def test_spatial_tree(self):
        bridge = IFC.by_type("IfcBridge")[0]
        self.assertEqual(bridge.PredefinedType, "GIRDER")
        parts = {p.Name: p for p in IFC.by_type("IfcBridgePart")}
        self.assertEqual(len([p for p in parts.values() if p.PredefinedType == "DECK_SEGMENT"]), 6)
        self.assertEqual(sorted(p.Name for p in parts.values() if p.PredefinedType in ("PIER", "ABUTMENT")),
                         sorted(support_name(k) for k in range(C.N_SPANS + 1)))
        for p in IFC.by_type("IfcElement"):
            container = UE.get_container(p)
            e = BY[p.Name]
            if e.part.startswith("SUP-"):
                self.assertIn(container.PredefinedType, ("DECK", "DECK_SEGMENT"), p.Name)
            else:
                self.assertEqual(container.Name, e.part, p.Name)


class IfcGeometry(unittest.TestCase):
    """几何引擎（OpenCascade）把 IFC 里的拉伸、布尔切割、多边形面集算成实体，与模型逐件比体积和包围盒。"""

    @classmethod
    def setUpClass(cls):
        st = ifcopenshell.geom.settings()
        st.set("use-world-coords", True)
        cls.res = {}
        for p in IFC.by_type("IfcElement"):
            sh = ifcopenshell.geom.create_shape(st, p)          # 留住 sh：链式取 verts 会读到已释放的内存
            v = list(sh.geometry.verts)
            pts = [v[i:i + 3] for i in range(0, len(v), 3)]
            cls.res[p.Name] = (US.get_volume(sh.geometry), pts)

    def test_every_element_has_a_solid(self):
        self.assertEqual(set(self.res), set(BY))

    def test_volumes(self):
        worst = {}
        for eid, (vol, _) in self.res.items():
            e = BY[eid]
            key = "cyl" if e.shape == "cyl" else ("girder" if e.cls == "girder" else "other")
            worst[key] = max(worst.get(key, 0.0), abs(vol - e.volume) / e.volume)
        self.assertLess(worst["girder"], 1e-6)          # 斜向拉伸 + 两个半空间切割，算出的体积 = 截面积 × 平面长度
        self.assertLess(worst["other"], 1e-6)
        self.assertLess(worst["cyl"], 0.005)            # 引擎把圆离散成多边形，体积略小

    def test_positions(self):
        worst = {}
        for eid, (_, pts) in self.res.items():
            e = BY[eid]
            lo, hi = model_bbox(e)
            if e.shape == "cyl":
                d = max(abs(min(p[2] for p in pts) - lo[2]), abs(max(p[2] for p in pts) - hi[2]),
                        abs((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2 - (lo[0] + hi[0]) / 2),
                        abs((min(p[1] for p in pts) + max(p[1] for p in pts)) / 2 - (lo[1] + hi[1]) / 2))
            else:
                d = max(max(abs(min(p[i] for p in pts) - lo[i]), abs(max(p[i] for p in pts) - hi[i])) for i in range(3))
            key = "cyl" if e.shape == "cyl" else "other"
            worst[key] = max(worst.get(key, 0.0), d)
        self.assertLess(worst["other"], 1e-6)
        self.assertLess(worst["cyl"], 1e-3)


class IfcAlignment(unittest.TestCase):
    def test_design_parameters_are_the_config(self):
        al = IFC.by_type("IfcAlignment")[0]
        h = ifcopenshell.api.alignment.get_horizontal_layout(al)
        segs = [s.DesignParameters for s in ifcopenshell.api.alignment.get_layout_segments(h)
                if s.DesignParameters.SegmentLength > 0]
        self.assertEqual([s.PredefinedType for s in segs], [k for k, _, _, _ in C.H_SEGMENTS])
        for s, (_, L, k0, k1) in zip(segs, C.H_SEGMENTS):
            self.assertAlmostEqual(s.SegmentLength, L, places=9)
            self.assertAlmostEqual(s.StartRadiusOfCurvature, 1 / k0 if k0 else 0.0, places=6)
            self.assertAlmostEqual(s.EndRadiusOfCurvature, 1 / k1 if k1 else 0.0, places=6)
        v = ifcopenshell.api.alignment.get_vertical_layout(al)
        vsegs = [s.DesignParameters for s in ifcopenshell.api.alignment.get_layout_segments(v)
                 if s.DesignParameters.HorizontalLength > 0]
        self.assertEqual([s.PredefinedType for s in vsegs], ["CONSTANTGRADIENT", "PARABOLICARC", "CONSTANTGRADIENT"])
        self.assertAlmostEqual(vsegs[1].HorizontalLength, C.V_CURVE_LENGTH[1], places=9)

    def test_geometry_kernel_agrees_with_our_own_alignment(self):
        """第三种算法：ifcopenshell 的几何内核求值 IfcGradientCurve，与自己的积分对照。"""
        curve = ifcopenshell.api.alignment.get_curve(IFC.by_type("IfcAlignment")[0])
        self.assertEqual(curve.is_a(), "IfcGradientCurve")
        wp = wz = 0.0
        s = C.START_STATION
        while s <= C.START_STATION + AL.LENGTH:
            m = AU.evaluate_representation(curve, s - C.START_STATION)
            x, y, _, _ = AL.at_station(s)
            wp = max(wp, math.hypot(m[3, 0] - x, m[3, 1] - y))
            wz = max(wz, abs(m[3, 2] - AL.profile(s)[0]))
            s += 2.5
        self.assertLess(wp, 1e-5)
        self.assertLess(wz, 1e-6)

    def test_supports_are_placed_by_station(self):
        refs = {r.Name: r for r in IFC.by_type("IfcReferent")}
        parts = {p.Name: p for p in IFC.by_type("IfcBridgePart")}
        for k, s in enumerate(support_stations()):
            ref = refs[AL.station_label(s)]
            self.assertAlmostEqual(UE.get_pset(ref, "Pset_Stationing", "Station"), s, places=9)
            lp = parts[support_name(k)].ObjectPlacement
            self.assertEqual(lp.is_a(), "IfcLinearPlacement")
            self.assertAlmostEqual(lp.RelativePlacement.Location.DistanceAlong.wrappedValue, s - C.START_STATION, places=9)
            loc = lp.CartesianPosition.Location.Coordinates
            x, y, _, _ = AL.at_station(s)
            self.assertLess(math.hypot(loc[0] - x, loc[1] - y), 1e-5)


class IfcData(unittest.TestCase):
    def test_girder_properties_equal_the_girder_table(self):
        g_rows = {r["girder"]: r for r in rows("girders.csv")}
        for b in IFC.by_type("IfcBeam"):
            if b.PredefinedType != "T_BEAM":
                continue
            p = UE.get_pset(b, "BridgeBIM_Element")
            r = g_rows[b.Name]
            self.assertEqual("%.2f" % p["PrecastLength"], r["length_m"])
            self.assertEqual(p["ErectDate"], r["erect"])
            self.assertEqual(p["CastDate"], r["cast"])
            self.assertEqual(p["Bed"], int(r["bed"]))
            q = UE.get_pset(b, "Qto_BeamBaseQuantities")
            self.assertAlmostEqual(q["Length"], float(r["length_m"]), places=9)

    def test_bearing_seat_heights_equal_the_bearing_table(self):
        b_rows = {r["bearing"]: r for r in rows("bearings.csv")}
        n = 0
        for b in IFC.by_type("IfcBearing"):
            if b.PredefinedType != "ELASTOMERIC":
                continue
            p = UE.get_pset(b, "BridgeBIM_Element")
            self.assertLess(abs(p["SeatHeight"] - float(b_rows[b.Name]["seat_height"])), 6e-5)
            self.assertLess(abs(p["TopElevation"] - float(b_rows[b.Name]["bearing_top"])), 6e-5)
            n += 1
        self.assertEqual(n, len(b_rows))

    def test_volumes_add_up_to_the_takeoff(self):
        from importlib import util as iu
        spec = iu.spec_from_file_location("ex", os.path.join(ROOT, "scripts", "export_ifc.py"))
        ex = iu.module_from_spec(spec)
        spec.loader.exec_module(ex)
        tot = Counter()
        for p in IFC.by_type("IfcElement"):
            e = BY[p.Name]
            q = dict(UE.get_pset(p, ex.QTO.get(p.is_a(), "")) or {})
            q.update(UE.get_pset(p, "Qto_BodyGeometryValidation") or {})
            vol = q.get("NetVolume", q.get("Volume"))
            self.assertIsNotNone(vol, "%s 在 IFC 里没有体积" % p.Name)
            self.assertAlmostEqual(vol, e.volume, places=9)
            tot[e.cls] += vol
        for r in rows("takeoff.csv"):
            if r["concrete_m3"] and r["class"] != "total":
                self.assertAlmostEqual(tot[r["class"]], float(r["concrete_m3"]), delta=0.006, msg=r["class"])


class Ifc4D(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = {t.Name: t for t in IFC.by_type("IfcTask")}

    def test_every_girder_is_erected_by_exactly_one_task_on_its_date(self):
        g_rows = {r["girder"]: r for r in rows("girders.csv")}
        seen = Counter()
        for rel in IFC.by_type("IfcRelAssignsToProduct"):
            prod = rel.RelatingProduct
            for t in rel.RelatedObjects:
                if t.is_a("IfcTask") and t.Name.startswith("架设 "):
                    seen[prod.Name] += 1
                    self.assertEqual(t.Name, "架设 " + prod.Name)
                    self.assertEqual(t.TaskTime.ScheduleStart[:10], g_rows[prod.Name]["erect"])
                    h = PLAN[prod.Name]["erect_hour"]
                    self.assertEqual(t.TaskTime.ScheduleStart[11:16], "%02d:%02d" % (C.DAY_START_HOUR + int(h), round(h % 1 * 60)))
        self.assertEqual(set(seen), set(g_rows))
        self.assertTrue(all(v == 1 for v in seen.values()))

    def test_sequence_lags_equal_the_gaps_between_tasks(self):
        """每条完成—开始关系的时差 = 后续开始 − 前置结束，且不为负（排程里没有倒着来的）。"""
        import datetime as dt
        import re

        def t(s):
            return dt.datetime.fromisoformat(s)

        def dur(s):
            d, h, m = (int(x) for x in re.fullmatch(r"P(\d+)DT(\d+)H(\d+)M", s).groups())
            return dt.timedelta(days=d, hours=h, minutes=m)
        n = 0
        for rel in IFC.by_type("IfcRelSequence"):
            self.assertEqual(rel.SequenceType, "FINISH_START")
            gap = t(rel.RelatedProcess.TaskTime.ScheduleStart) - t(rel.RelatingProcess.TaskTime.ScheduleFinish)
            self.assertEqual(dur(rel.TimeLag.LagValue.wrappedValue), gap)
            self.assertGreaterEqual(gap, dt.timedelta(0))
            n += 1
        self.assertEqual(n, 120 + 119 + 2 * len(S.conversions(R["rows"])))

    def test_precast_tasks_precede_erection(self):
        preds = {}
        for rel in IFC.by_type("IfcRelSequence"):
            preds.setdefault(rel.RelatedProcess.Name, []).append(rel.RelatingProcess.Name)
        for r in rows("girders.csv"):
            self.assertIn("预制 " + r["girder"], preds["架设 " + r["girder"]])
            t = self.tasks["预制 " + r["girder"]]
            self.assertEqual(t.TaskTime.ScheduleStart[:10], r["cast"])

    def test_conversion_tasks_remove_the_temporary_supports(self):
        c_rows = rows("conversions.csv")
        removed = Counter()
        for rel in IFC.by_type("IfcRelAssignsToProcess"):
            t = rel.RelatingProcess
            self.assertTrue(t.Name.startswith("体系转换 "))
            for o in rel.RelatedObjects:
                self.assertEqual(BY[o.Name].cls, "temp_support")
                removed[o.Name] += 1
        self.assertEqual(set(removed), {e.eid for e in R["els"] if e.cls == "temp_support"})
        self.assertTrue(all(v == 1 for v in removed.values()))
        convs = [t for n, t in self.tasks.items() if n.startswith("体系转换 ")]
        self.assertEqual(sorted(t.TaskTime.ScheduleStart[:10] for t in convs), sorted(r["conversion"] for r in c_rows))


if __name__ == "__main__":
    unittest.main()
