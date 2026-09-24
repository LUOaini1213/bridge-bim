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
        for name in ("hero", "pier_detail", "erection_4d", "yard", "profile", "sections", "length_specs", "forces",
                     "structure_3d"):
            self.assertTrue(os.path.getsize(os.path.join(ROOT, "docs", "img", name + ".png")) > 50000, name)


def read_png(data):
    """8 位、不交错的 RGB / RGBA PNG（bytes）→ (宽, 高, 每像素字节数, 逐行 bytes)。只用标准库。"""
    import struct
    import zlib
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos, idat = 8, []
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError("只支持 8 位 RGB / RGBA、不交错")
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
        pos += 12 + n
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    rows_, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            up, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + up) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + up) >> 1)) & 255
            elif f == 4:
                p = a + up - c
                pa, pb, pc = abs(p - a), abs(p - up), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else up if pb <= pc else c)) & 255
        rows_.append(bytes(line))
        prev = line
    return w, h, bpp, rows_


def enclosed_white_blobs(data, min_area):
    """纯白（三个通道都 ≥ 250）像素的 4 连通块里，不碰图边、面积 ≥ min_area 的：被别的颜色整圈包住的白块。"""
    w, h, bpp, rows_ = read_png(data)
    white = [bytearray(1 if min(r[x * bpp:x * bpp + 3]) >= 250 else 0 for x in range(w)) for r in rows_]
    seen = [bytearray(w) for _ in range(h)]
    out = []
    for y0 in range(h):
        for x0 in range(w):
            if not white[y0][x0] or seen[y0][x0]:
                continue
            stack, area, edge, box = [(x0, y0)], 0, False, [x0, y0, x0, y0]
            seen[y0][x0] = 1
            while stack:
                x, y = stack.pop()
                area += 1
                edge = edge or x in (0, w - 1) or y in (0, h - 1)
                box = [min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y)]
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and white[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = 1
                        stack.append((nx, ny))
            if not edge and area >= min_area:
                out.append((area, tuple(box)))
    return out


def tiny_png(w, h, pixel):
    """合成一张 RGB PNG（逐行轮流用 0 / 1 / 2 / 4 号滤波，顺带检验解码器），pixel(x, y) → (r, g, b)。"""
    import struct
    import zlib
    raw = bytearray()
    prev = bytes(w * 3)
    for y in range(h):
        line = bytes(v for x in range(w) for v in pixel(x, y))
        f = (0, 1, 2, 4)[y % 4]
        enc = bytearray()
        for i, v in enumerate(line):
            a = line[i - 3] if i >= 3 else 0
            up, c = prev[i], (prev[i - 3] if i >= 3 else 0)
            if f == 4:
                p = a + up - c
                pa, pb, pc = abs(p - a), abs(p - up), abs(p - c)
                pred = a if pa <= pb and pa <= pc else up if pb <= pc else c
            else:
                pred = (0, a, up)[f] if f < 3 else 0
            enc.append((v - pred) & 255)
        raw += bytes([f]) + enc
        prev = line

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


class Drawings(unittest.TestCase):
    """出图的版面问题：文字遮罩取的是视口背景色（白），开在色块上的字会连同色块一起被盖掉，只剩一个白块。"""

    def test_white_box_detector_catches_a_masked_label(self):
        """合成图：蓝底上一个 20×10 的白块（被蓝色包住）和一条贴边的白带——只有前者算数。"""
        def px(x, y):
            if 10 <= x < 30 and 8 <= y < 18 or y >= 27:
                return (255, 255, 255)
            return (20, 80, 160)
        blobs = enclosed_white_blobs(tiny_png(48, 30, px), 100)
        self.assertEqual(blobs, [(200, (10, 8, 29, 17))])

    def test_numbers_in_the_length_spec_cells_are_not_masked(self):
        """梁长布置图：每格的梁长数字写在色块上，不能开遮罩。开着遮罩时每格都有一个 200 像素以上的白块
        （深色格的白字连同色块一起看不见）；修好后格里只剩字形笔画，最大的也不到 100 像素。"""
        data = open(os.path.join(ROOT, "docs", "img", "length_specs.png"), "rb").read()
        self.assertEqual(enclosed_white_blobs(data, 200), [])


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


class IfcStructural(unittest.TestCase):
    """IFC 里的两个结构分析模型：拓扑自洽、边界条件只在支承点、荷载合计与计算一致、反力等于支座表、
    分析杆件落在物理梁的轴线上并挂到它名下。"""
    STAGE1, STAGE2 = "施工阶段一：预制梁简支", "施工阶段二：体系转换后的连续梁"

    @classmethod
    def setUpClass(cls):
        cls.models = {m.Name: m for m in IFC.by_type("IfcStructuralAnalysisModel")}
        cls.items = {}
        for name, m in cls.models.items():
            objs = [o for rel in m.IsGroupedBy for o in rel.RelatedObjects]
            cls.items[name] = {"members": [o for o in objs if o.is_a("IfcStructuralCurveMember")],
                               "points": [o for o in objs if o.is_a("IfcStructuralPointConnection")]}
        cls.groups = {g.Name: g for g in IFC.by_type("IfcStructuralLoadGroup")}
        cls.bearing_rows = {r["bearing"]: r for r in rows("bearing_reactions.csv")}

    @staticmethod
    def vertex(obj):
        return obj.Representation.Representations[0].Items[0]

    @staticmethod
    def edge(m):
        return m.Representation.Representations[0].Items[0]

    def activities(self, group):
        return [o for rel in self.groups[group].IsGroupedBy for o in rel.RelatedObjects]

    def test_two_staged_models_serve_the_bridge(self):
        self.assertEqual(set(self.models), {self.STAGE1, self.STAGE2})
        for m in self.models.values():
            self.assertEqual(m.PredefinedType, "LOADING_3D")
            served = [b for rel in m.ServicesBuildings for b in rel.RelatedBuildings]
            self.assertEqual([b.is_a() for b in served], ["IfcBridge"])
        units = {u.UnitType: u for u in IFC.by_type("IfcUnitAssignment")[0].Units}
        self.assertEqual(units["FORCEUNIT"].Name, "NEWTON")
        self.assertIn("LINEARFORCEUNIT", units)

    def test_counts(self):
        n_g = sum(1 for e in R["els"] if e.cls == "girder")
        lines = len(rows("girder_lines.csv"))
        self.assertEqual(len(self.items[self.STAGE1]["members"]), 3 * n_g)
        self.assertEqual(len(self.items[self.STAGE1]["points"]), 4 * n_g)
        self.assertEqual(len(self.items[self.STAGE2]["members"]), 12 * lines)
        self.assertEqual(len(self.items[self.STAGE2]["points"]), 13 * lines)

    def test_members_and_connections_share_their_vertices(self):
        for name, it in self.items.items():
            for m in it["members"]:
                rels = m.ConnectedBy
                self.assertEqual(len(rels), 2, m.Name)
                e = self.edge(m)
                self.assertEqual({self.vertex(r.RelatedStructuralConnection).id() for r in rels},
                                 {e.EdgeStart.id(), e.EdgeEnd.id()}, m.Name)

    def test_boundary_conditions_only_at_the_supports(self):
        temps = {e.eid for e in R["els"] if e.cls == "temp_support"}
        perm = {e.eid for e in R["els"] if e.cls == "bearing"}
        for name, want in ((self.STAGE1, temps | {b for b in perm if not b.startswith("B-P")}), (self.STAGE2, perm)):
            held = {p.Description: p for p in self.items[name]["points"] if p.AppliedCondition is not None}
            self.assertEqual(set(held), want)
            free = [p for p in self.items[name]["points"] if p.AppliedCondition is None]
            self.assertTrue(all(p.Description is None for p in free))
            for p in held.values():
                c = p.AppliedCondition
                self.assertTrue(c.TranslationalStiffnessZ.wrappedValue and c.TranslationalStiffnessY.wrappedValue)
        fixed_x = Counter(p.Name.rsplit("-", 1)[0] for p in self.items[self.STAGE2]["points"]
                          if p.AppliedCondition is not None and p.AppliedCondition.TranslationalStiffnessX.wrappedValue)
        self.assertEqual(len(fixed_x), len(rows("girder_lines.csv")))           # 每条梁位线恰好一个纵向固定点
        self.assertTrue(all(v == 1 for v in fixed_x.values()))

    def projected_total(self, group):
        tot = 0.0
        for a in self.activities(group):
            if a.is_a("IfcStructuralCurveAction"):
                self.assertEqual(a.ProjectedOrTrue, "PROJECTED_LENGTH")
                e = a.Representation.Representations[0].Items[0]
                p, q = e.EdgeStart.VertexGeometry.Coordinates, e.EdgeEnd.VertexGeometry.Coordinates
                tot += -a.AppliedLoad.LinearForceZ * math.hypot(q[0] - p[0], q[1] - p[1])
            else:
                tot += -a.AppliedLoad.ForceZ
        return tot / 1000.0

    def test_loads_add_up_to_the_computed_totals(self):
        lines = rows("girder_lines.csv")
        temps = [r for r in self.bearing_rows.values() if r["kind"] == "临时支座"]
        for group, want, n in (("一期恒载", sum(float(r["G1_kN"]) for r in lines), len(lines)),
                               ("二期恒载", sum(float(r["G2_kN"]) for r in lines), len(lines)),
                               ("体系转换：拆除临时支座", sum(float(r["R_G1"]) for r in temps), len(temps))):
            self.assertLess(abs(self.projected_total(group) - want), 0.05 * n + 1e-6, group)   # 表里保留 1 位小数
        self.assertEqual(self.groups["体系转换：拆除临时支座"].ActionSource, "PROPPING")
        self.assertEqual(self.groups["二期恒载"].ActionSource, "COMPLETION_G1")

    def test_reactions_equal_the_bearing_table(self):
        got = {}
        for res in IFC.by_type("IfcStructuralResultGroup"):
            for rel in res.IsGroupedBy:
                for r in rel.RelatedObjects:
                    got[(res.Name, r.Name)] = r.AppliedLoad.ForceZ / 1000.0
        for bid, row in self.bearing_rows.items():
            if row["kind"] == "临时支座" or not bid.startswith("B-P"):
                self.assertLess(abs(got[("阶段一支座反力", "R1-" + bid)] - float(row["R_G1"])), 0.05 + 1e-9, bid)
            if row["kind"] != "临时支座":
                self.assertLess(abs(got[("支座反力 Rck", "RCK-" + bid)] - float(row["Rck"])), 0.05 + 1e-9, bid)
        stage1 = sum(v for (g, _), v in got.items() if g == "阶段一支座反力")
        self.assertLess(abs(stage1 - self.projected_total("一期恒载")), 1e-6 * stage1)    # IFC 里自己也平衡

    def test_stage_one_members_lie_on_their_girder_axis(self):
        from bridge.structure import polygon_props
        dz = polygon_props(C.T_SECTION)[1]                   # 预制截面形心在梁顶以下
        n_checked = 0
        for rel in IFC.by_type("IfcRelAssignsToProduct"):
            g = rel.RelatingProduct
            if not g.is_a("IfcBeam") or g.PredefinedType != "T_BEAM":
                continue
            e = BY[g.Name]
            p0, p1 = e.params["p0"], e.params["p1"]
            d = [p1[i] - p0[i] for i in range(3)]
            n = math.sqrt(sum(x * x for x in d))
            for m in rel.RelatedObjects:
                if not m.Name.startswith("S1-"):
                    continue
                for v in (self.edge(m).EdgeStart, self.edge(m).EdgeEnd):
                    q = v.VertexGeometry.Coordinates
                    w = [q[0] - p0[0], q[1] - p0[1], q[2] - dz - p0[2]]
                    t = sum(w[i] * d[i] for i in range(3)) / n
                    self.assertLess(math.sqrt(max(0.0, sum(x * x for x in w) - t * t)), 1e-6, m.Name)
                    self.assertTrue(-1e-6 <= t <= n + 1e-6, m.Name)
                n_checked += 1
        self.assertEqual(n_checked, len(self.items[self.STAGE1]["members"]))

    def test_every_analytical_object_belongs_to_one_product(self):
        owner = Counter()
        for rel in IFC.by_type("IfcRelAssignsToProduct"):
            if rel.RelatingProduct.is_a("IfcElement"):
                for o in rel.RelatedObjects:
                    if o.is_a("IfcStructuralItem"):
                        owner[o.id()] += 1
        members = [m for it in self.items.values() for m in it["members"]]
        held = [p for it in self.items.values() for p in it["points"] if p.AppliedCondition is not None]
        self.assertTrue(all(owner[o.id()] == 1 for o in members + held))

    def test_structural_psets_equal_the_tables(self):
        g_rows = {r["girder"]: r for r in rows("girder_forces.csv")}
        n = 0
        for p in IFC.by_type("IfcElement"):
            ps = UE.get_pset(p, "BridgeBIM_Structural")
            if p.Name in g_rows:
                r = g_rows[p.Name]
                self.assertEqual(ps["DesignMomentSaggingMax_kNm"], float(r["M_ud_pos"]))
                self.assertEqual(ps["DesignMomentHoggingMin_kNm"], float(r["M_ud_neg"]))
                self.assertEqual(ps["DeflectionRatio"], float(r["deflection_ratio"]))
                n += 1
            elif p.Name in self.bearing_rows:
                r = self.bearing_rows[p.Name]
                key, col = ("ReactionFirstStage_kN", "R_G1") if r["kind"] == "临时支座" else ("Rck_kN", "Rck")
                self.assertEqual(ps[key], float(r[col]))
                if r["kind"] != "临时支座":
                    self.assertEqual(ps["Utilisation"], float(r["utilisation"]))
                n += 1
            else:
                self.assertIsNone(ps, p.Name)
        self.assertEqual(n, len(g_rows) + len(self.bearing_rows))

    def test_bearing_movements_equal_the_design_table(self):
        """永久支座的位移与验算结果：IFC 属性集逐个等于 data/bearing_design.csv；伸缩端是四氟滑板（有行程、没有剪切
        利用率），连续墩是普通板式（有剪切与抗滑利用率）。"""
        d_rows = {r["bearing"]: r for r in rows("bearing_design.csv")}
        seen = 0
        for p in IFC.by_type("IfcBearing"):
            if p.Name not in d_rows:
                continue
            r, ps = d_rows[p.Name], UE.get_pset(p, "BridgeBIM_Structural")
            self.assertEqual(ps["BearingType"], r["type"])
            self.assertEqual(ps["Contraction_mm"], float(r["contract_mm"]))
            self.assertEqual(ps["DistanceToFixedPoint_m"], float(r["to_fixed_point_m"]))
            self.assertEqual(ps["RotationUtilisation"], float(r["util_rotation"]))
            if r["type"] == "四氟滑板":
                self.assertEqual(ps["SlideTravel_m"], float(r["slide_travel_m"]))
                self.assertNotIn("ShearUtilisation", ps)
            else:
                self.assertEqual(ps["ShearUtilisation"], float(r["util_shear"]))
                self.assertEqual(ps["SlipUtilisation"], float(r["util_slip"]))
                self.assertNotIn("SlideTravel_m", ps)
            seen += 1
        self.assertEqual(seen, len(d_rows))


if __name__ == "__main__":
    unittest.main()
