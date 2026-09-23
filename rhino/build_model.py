#! python3
"""在 Rhino 8 里建桥梁 BIM 模型，存 .3dm，并出图。

由 scripts/run_rhino.py 用 `Rhino.exe /runscript` 调起；仓库根目录经环境变量 BRIDGE_BIM_ROOT
传入（Rhino 命令行对非 ASCII 路径不可靠，脚本本身会被复制到纯 ASCII 的临时路径再跑）。

模型组织：
- 每个构件一个 Rhino 对象，对象名 = 构件编号，属性里挂 BIM 数据（UserText，与
  bridge.pipeline.element_rows 逐项相同）；多面体用网格（双精度顶点），圆柱、系梁用 Brep；
- 图层：路线 / 地形 / 上部结构（预制 T 梁按梁长规格分子层）/ 下部结构 / 梁场 / 分析 / 标注 / 图纸；
  材质挂在图层上（Rhino 8 给每个对象单独指定材质会逐个复制一份，文件大一截）；
- 图纸层里的两个横断面不是另画的：是用竖直平面剖切模型里的网格与 Brep 得到的截线，
  再平移到图纸位置、按构件类别填色。
"""
import datetime
import json
import math
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.environ.get("BRIDGE_BIM_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import System                                          # noqa: E402
import System.Drawing as SD                            # noqa: E402
import System.Drawing.Imaging                          # noqa: E402,F401  PNG 编码器所在命名空间
import System.Drawing.Text                             # noqa: E402,F401
import Rhino                                           # noqa: E402
import Rhino.Geometry as RG                            # noqa: E402

from bridge import alignment as AL, config as C, schedule as S, yard as Y       # noqa: E402
from bridge.model import frame, project, support_kind, support_name, support_stations, triangulate, unit_bounds  # noqa: E402
from bridge.pipeline import compute, element_rows, naive_spec_count             # noqa: E402

LOG = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}
DOC = Rhino.RhinoDoc.ActiveDoc
GEOM, OBJ, WEEK_OBJ = {}, {}, {}                        # 构件编号 -> Rhino 几何 / 对象 Id / 按周着色副本 Id
FONT = "Microsoft YaHei"


def step(msg):
    LOG["steps"].append("%s  %s" % (time.strftime("%H:%M:%S"), msg))


# ------------------------------------------------------------------ 图层（带材质）、属性、文字
_layers = {}


def layer(path, rgb=(0, 0, 0), mat=True):
    if path in _layers:
        return _layers[path]
    parent_id = System.Guid.Empty
    if "::" in path:
        parent_id = DOC.Layers[layer(path.rsplit("::", 1)[0], rgb, False)].Id
    lay = Rhino.DocObjects.Layer()
    lay.Name = path.rsplit("::", 1)[-1]
    lay.Color = SD.Color.FromArgb(*rgb)
    if parent_id != System.Guid.Empty:
        lay.ParentLayerId = parent_id
    idx = DOC.Layers.Add(lay)
    if idx < 0:
        raise RuntimeError("图层创建失败：" + path)
    if mat:
        m = Rhino.DocObjects.Material()
        m.Name = path.replace("::", "-")
        m.DiffuseColor = SD.Color.FromArgb(*rgb)
        lay = DOC.Layers[idx]
        try:
            rm = Rhino.Render.RenderMaterial.CreateBasicMaterial(m, DOC)
            DOC.RenderMaterials.Add(rm)
            lay.RenderMaterial = rm
        except Exception:
            lay.RenderMaterialIndex = DOC.Materials.Add(m)
        lay.CommitChanges()
    _layers[path] = idx
    return idx


def set_visible(path, on):
    lay = DOC.Layers[_layers[path]]
    lay.IsVisible = on
    try:
        lay.SetPersistentVisibility(on)
    except Exception:
        pass


def attrs(layer_path, rgb=None, name=None, mat=True):
    a = Rhino.DocObjects.ObjectAttributes()
    a.LayerIndex = layer(layer_path, rgb or (0, 0, 0), mat)
    a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromLayer
    a.MaterialSource = Rhino.DocObjects.ObjectMaterialSource.MaterialFromLayer
    if name:
        a.Name = name
    return a


def dot(text, p, layer_path, size=16, rgb=(0, 0, 0)):
    td = RG.TextDot(text, RG.Point3d(*p))
    td.FontHeight = size
    a = attrs(layer_path, rgb, mat=False)
    a.ObjectColor = SD.Color.FromArgb(*rgb)
    a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
    DOC.Objects.AddTextDot(td, a)


_font = None


def text(s, p, h, layer_path, rgb=(0, 0, 0), ha="c", va="m", plane_axes=((1, 0, 0), (0, 0, 1))):
    """图纸上的注记：竖直图纸平面（XZ）里的文字，前视图可读。"""
    global _font
    xa, ya = plane_axes
    pl = RG.Plane(RG.Point3d(*p), RG.Vector3d(*xa), RG.Vector3d(*ya))
    te = RG.TextEntity.Create(s, pl, DOC.DimStyles.Current, False, 0.0, 0.0)
    te.TextHeight = h
    te.TextHorizontalAlignment = {"l": Rhino.DocObjects.TextHorizontalAlignment.Left, "c": Rhino.DocObjects.TextHorizontalAlignment.Center,
                                  "r": Rhino.DocObjects.TextHorizontalAlignment.Right}[ha]
    te.TextVerticalAlignment = {"t": Rhino.DocObjects.TextVerticalAlignment.Top, "m": Rhino.DocObjects.TextVerticalAlignment.Middle,
                                "b": Rhino.DocObjects.TextVerticalAlignment.Bottom}[va]
    if _font is None:
        try:
            _font = Rhino.DocObjects.Font.FromQuartetProperties(FONT, False, False)
        except Exception:
            _font = False
    if _font:
        te.Font = _font
    try:                                   # 文字压在线上时用背景色遮住线，CAD 里的做法
        te.MaskEnabled = True
        te.MaskUsesViewportColor = True
        te.MaskOffset = h * 0.15
    except Exception:
        pass
    a = attrs(layer_path, rgb, mat=False)
    a.ObjectColor = SD.Color.FromArgb(*rgb)
    a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
    DOC.Objects.AddText(te, a)


def polyline(pts, layer_path, rgb=(0, 0, 0), width=None):
    pl = RG.Polyline()
    last = None
    for p in pts:
        if last is not None and max(abs(p[i] - last[i]) for i in range(3)) < 1e-9:
            continue                        # 相邻重复点会让 Rhino 判折线无效、AddPolyline 静默失败
        pl.Add(RG.Point3d(*p))
        last = p
    a = attrs(layer_path, rgb, mat=False)
    a.ObjectColor = SD.Color.FromArgb(*rgb)
    a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
    if width:
        a.PlotWeightSource = Rhino.DocObjects.ObjectPlotWeightSource.PlotWeightFromObject
        a.PlotWeight = width
    return DOC.Objects.AddPolyline(pl, a)


_hatch = None


def fill(curve, layer_path, rgb):
    global _hatch
    if _hatch is None:
        hp = DOC.HatchPatterns.FindName("Solid")
        _hatch = hp.Index if hp is not None else DOC.HatchPatterns.Add(Rhino.DocObjects.HatchPattern.Defaults.Solid)
    a = attrs(layer_path, rgb, mat=False)
    a.ObjectColor = SD.Color.FromArgb(*rgb)
    a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
    for h in (RG.Hatch.Create(curve, _hatch, 0.0, 1.0, DOC.ModelAbsoluteTolerance) or []):
        DOC.Objects.AddHatch(h, a)


def rect_curve(x0, z0, x1, z1, y):
    return RG.PolylineCurve([RG.Point3d(x0, y, z0), RG.Point3d(x1, y, z0), RG.Point3d(x1, y, z1),
                             RG.Point3d(x0, y, z1), RG.Point3d(x0, y, z0)])


# ------------------------------------------------------------------ 几何
def mesh_of(solids):
    m = RG.Mesh()
    m.Vertices.UseDoublePrecisionVertices = True
    for sol in solids:
        t = triangulate(sol)
        base = m.Vertices.Count
        for p in t["v"]:
            m.Vertices.Add(p[0], p[1], p[2])
        for f in t["f"]:
            if len(f) == 3:
                m.Faces.AddFace(base + f[0], base + f[1], base + f[2])
            else:
                m.Faces.AddFace(base + f[0], base + f[1], base + f[2], base + f[3])
    m.Normals.ComputeNormals()
    m.Compact()
    return m


def geometry(e):
    if e.shape == "cyl":
        c = e.params["c"]
        circ = RG.Circle(RG.Plane(RG.Point3d(c[0], c[1], c[2]), RG.Vector3d.ZAxis), e.params["r"])
        return RG.Cylinder(circ, e.params["h"]).ToBrep(True, True)
    if e.shape == "box":
        p = e.params
        c, d = p["c"], p["dir"]
        pl = RG.Plane(RG.Point3d(c[0], c[1], c[2]), RG.Vector3d(d[0], d[1], 0), RG.Vector3d(-d[1], d[0], 0))
        return RG.Box(pl, RG.Interval(-p["l"] / 2, p["l"] / 2), RG.Interval(-p["w"] / 2, p["w"] / 2),
                      RG.Interval(0, p["h"])).ToBrep()
    return mesh_of(e.params["solids"])


def add_geom(g, a):
    if isinstance(g, RG.Mesh):
        return DOC.Objects.AddMesh(g, a)
    return DOC.Objects.AddBrep(g, a)


STYLE = {
    "girder": ("上部结构::预制T梁", (214, 210, 200)),
    "wet_joint": ("上部结构::湿接缝", (196, 192, 182)),
    "cantilever": ("上部结构::翼缘现浇段", (196, 192, 182)),
    "continuity": ("上部结构::墩顶现浇连续段", (170, 176, 196)),
    "pavement": ("上部结构::桥面铺装", (70, 72, 76)),
    "barrier": ("上部结构::护栏", (236, 234, 228)),
    "expansion_joint": ("上部结构::伸缩装置", (40, 40, 40)),
    "cap": ("下部结构::盖梁", (204, 200, 192)),
    "abut_cap": ("下部结构::台帽", (204, 200, 192)),
    "backwall": ("下部结构::背墙", (190, 186, 178)),
    "column": ("下部结构::墩柱", (210, 206, 198)),
    "tie": ("下部结构::系梁", (192, 188, 180)),
    "pile": ("下部结构::桩", (168, 150, 128)),
    "seat": ("下部结构::支座垫石", (140, 140, 140)),
    "bearing": ("下部结构::永久支座", (25, 25, 25)),
    "temp_support": ("下部结构::临时支座", (240, 130, 20)),
}
WEEK_COLORS = [(254, 224, 144), (253, 174, 97), (244, 109, 67), (215, 48, 39), (165, 0, 38),
               (116, 173, 209), (69, 117, 180), (49, 54, 149)]
MID_COLORS = [(8, 81, 156), (49, 130, 189), (107, 174, 214), (158, 202, 225), (198, 219, 239)]
END_COLORS = [(166, 54, 3), (217, 72, 1), (241, 105, 19), (253, 141, 60), (253, 174, 107), (253, 208, 162),
              (254, 230, 206)]


def spec_key(e):
    return "%s %.2f m" % (e.attrs["family"], e.attrs["length"])


def spec_colors(r):
    specs = sorted({(e.attrs["family"], e.attrs["length"]) for e in r["els"] if e.cls == "girder"})
    mids = [s for s in specs if s[0] == "中跨"]
    ends = [s for s in specs if s[0] != "中跨"]
    out = {}
    for pal, group in ((MID_COLORS, mids), (END_COLORS, ends)):
        for i, s in enumerate(group):
            out["%s %.2f m" % s] = pal[min(i, len(pal) - 1)]
    return out


# ------------------------------------------------------------------ 构件
def build(r):
    rows = element_rows(r)
    n_closed = n_mesh = 0
    for e in r["els"]:
        lay, rgb = STYLE[e.cls]
        if e.cls == "girder":
            lay = "%s::%s" % (lay, spec_key(e))
        a = attrs(lay, rgb, name=e.eid)
        for k, v in rows[e.eid].items():
            a.SetUserString(k, v)
        g = geometry(e)
        if isinstance(g, RG.Mesh):
            n_mesh += 1
            n_closed += 1 if g.IsClosed else 0
        gid = add_geom(g, a)
        if gid == System.Guid.Empty:
            raise RuntimeError("对象创建失败：" + e.eid)
        GEOM[e.eid], OBJ[e.eid] = g, gid
    step("构件 %d 个（网格 %d 个，其中闭合 %d 个）" % (len(r["els"]), n_mesh, n_closed))
    wk0 = r["summary"]["erect_first"]
    for e in r["els"]:
        if e.cls == "girder":
            wk = min((r["plan"][e.eid]["erect"] - wk0).days // 7, len(WEEK_COLORS) - 1)
            WEEK_OBJ[e.eid] = DOC.Objects.AddMesh(GEOM[e.eid], attrs("分析::按架设周::第%d周" % (wk + 1),
                                                                     WEEK_COLORS[wk], name=e.eid))
    step("分析：预制梁按架设周着色副本 %d 个" % len(WEEK_OBJ))


# ------------------------------------------------------------------ 路线、地形
S_LO = max(C.START_STATION, C.BRIDGE_START - 450.0)                 # 地面网格不超出路线起终点
S_HI = min(C.START_STATION + AL.LENGTH, C.BRIDGE_START + C.N_SPANS * C.SPAN + 300.0)


def draw_context(r):
    pts = []
    s = C.START_STATION
    while s <= C.START_STATION + AL.LENGTH + 1e-9:
        x, y = AL.offset_xy(s, 0.0)
        pts.append((x, y, AL.profile(s)[0]))
        s += 2.0
    polyline(pts, "路线::中线（设计高程）", (200, 30, 30))
    m = RG.Mesh()
    m.Vertices.UseDoublePrecisionVertices = True
    ss = [S_LO + 10.0 * i for i in range(int((S_HI - S_LO) / 10.0) + 1)]
    oo = [-300.0 + 10.0 * j for j in range(61)]
    for s in ss:
        for o in oo:
            x, y = AL.offset_xy(s, o)
            m.Vertices.Add(x, y, AL.ground(s, o))
    n = len(oo)
    for i in range(len(ss) - 1):
        for j in range(n - 1):
            m.Faces.AddFace(i * n + j, (i + 1) * n + j, (i + 1) * n + j + 1, i * n + j + 1)
    m.Normals.ComputeNormals()
    DOC.Objects.AddMesh(m, attrs("地形::地面", (150, 168, 118)))
    (px, py), t, nn = frame(C.ROAD_STATION)
    rm = RG.Mesh()
    k = 0
    for o in [-300.0 + 2.5 * j for j in range(241)]:
        for sgn in (-1, 1):
            q = (px + nn[0] * o + sgn * t[0] * C.ROAD_WIDTH / 2, py + nn[1] * o + sgn * t[1] * C.ROAD_WIDTH / 2)
            rm.Vertices.Add(q[0], q[1], AL.ground(C.ROAD_STATION, o) + 0.05)
        if k:
            rm.Faces.AddFace(2 * k - 2, 2 * k - 1, 2 * k + 1, 2 * k)
        k += 1
    rm.Normals.ComputeNormals()
    DOC.Objects.AddMesh(rm, attrs("地形::被交道路", (62, 62, 66)))
    for k, s in enumerate(support_stations()):
        x, y = AL.offset_xy(s, 0.0)
        kind = {"A": "桥台", "T": "过渡墩", "C": "连续墩"}[support_kind(k)]
        dot("%s %s" % (support_name(k), kind), (x, y, AL.profile(s)[0] + 7.0), "标注::墩台", 15)
    step("路线中线、地面（%d×%d 网格）、被交道路、墩台标注" % (len(ss), len(oo)))


# ------------------------------------------------------------------ 梁场
def _ybox(r4, z0, z1):
    """梁场局部矩形 (u0, v0, u1, v1) 拉成 z0..z1 的盒子。局部 v 指向路线右侧，u × v 朝下，
    所以用 (v, u) 做平面轴，法向才朝上。"""
    u0, v0, u1, v1 = r4
    (ox, oy), u, v = Y.frame()
    pl = RG.Plane(RG.Point3d(ox, oy, 0), RG.Vector3d(v[0], v[1], 0), RG.Vector3d(u[0], u[1], 0))
    return RG.Box(pl, RG.Interval(v0, v1), RG.Interval(u0, u1), RG.Interval(z0, z1)).ToBrep()


def _yard_girder(u_mid, v_mid, z, length):
    """台座、存梁位的长边沿梁场局部 v 向，梁也沿 v 向放：T 截面在 (u, z) 平面内沿 v 拉伸。"""
    (ox, oy), u, v = Y.frame()
    pts = [RG.Point3d(x, yy + C.H_GIRDER, 0) for x, yy in C.T_SECTION + [C.T_SECTION[0]]]
    brep = RG.Extrusion.Create(RG.PolylineCurve(pts), length, True).ToBrep()
    bb = brep.GetBoundingBox(True)
    brep.Translate(RG.Vector3d(0, 0, -(bb.Min.Z + bb.Max.Z) / 2))
    src = RG.Plane(RG.Point3d(0, 0, 0), RG.Vector3d(1, 0, 0), RG.Vector3d(0, 1, 0))
    c = Y.to_world(u_mid, v_mid)
    dst = RG.Plane(RG.Point3d(c[0], c[1], z), RG.Vector3d(u[0], u[1], 0), RG.Vector3d(0, 0, 1))
    brep.Transform(RG.Transform.PlaneToPlane(src, dst))
    return brep


def yard_pad_z():
    b = Y.boundary()
    zs = []
    for uu in (b[0], (b[0] + b[2]) / 2, b[2]):
        for vv in (b[1], (b[1] + b[3]) / 2, b[3]):
            x, y = Y.to_world(uu, vv)
            s, o = project(x, y, C.YARD_STATION + uu)
            zs.append(AL.ground(s, o))
    return max(zs) + 0.3, min(zs) - 1.0


def draw_yard(r):
    L = "梁场::"
    z, z_low = yard_pad_z()
    b = Y.boundary()
    DOC.Objects.AddBrep(_ybox(b, z_low, z), attrs(L + "场坪", (216, 212, 200)))
    for i, bed in enumerate(Y.beds(), 1):
        DOC.Objects.AddBrep(_ybox(bed, z, z + 0.4), attrs(L + "制梁台座", (96, 132, 182), name="台座 %d" % i))
    for i, slot in enumerate(Y.storage(), 1):
        u0, v0, u1, v1 = slot
        for vv in (v0 + 1.0, v1 - 2.0):
            DOC.Objects.AddBrep(_ybox((u0, vv, u1, vv + 1.0), z, z + 0.5), attrs(L + "存梁台座", (140, 110, 80),
                                                                                 name="存梁位 %d" % i))
    DOC.Objects.AddBrep(_ybox(Y.rebar_area(), z, z + 0.05), attrs(L + "钢筋加工区", (230, 196, 110)))
    for (p0, p1) in Y.rails():
        DOC.Objects.AddBrep(_ybox((p0[0], p0[1] - 0.3, p1[0], p1[1] + 0.3), z, z + 0.2), attrs(L + "龙门吊轨道", (70, 70, 70)))
    for uu in (Y.beds()[5][0] - 1.0, Y.storage()[8][0] - 1.0):
        for vv in (-3.0, C.BED_L + 3.0):
            DOC.Objects.AddBrep(_ybox((uu, vv - 0.4, uu + 0.8, vv + 0.4), z, z + 11.0), attrs(L + "龙门吊", (245, 190, 20)))
        DOC.Objects.AddBrep(_ybox((uu, -3.5, uu + 0.8, C.BED_L + 3.5), z + 10.2, z + 11.2), attrs(L + "龙门吊", (245, 190, 20)))
    route = [(x, y, z + 0.3) for x, y in Y.haul_route()]
    route[-1] = (route[-1][0], route[-1][1], AL.deck_top(C.BRIDGE_START, dict(C.DECKS)["R"], "R"))
    polyline(route, L + "运梁便道", (210, 40, 30), 0.5)
    day = r["summary"]["storage_peak_day"]
    beds, stored = S.yard_state(r["rows"], day)
    lengths = {e.eid: e.attrs["length"] for e in r["els"] if e.cls == "girder"}
    for bed_no, g in beds:
        u0, v0, u1, v1 = Y.beds()[bed_no - 1]
        DOC.Objects.AddBrep(_yard_girder((u0 + u1) / 2, (v0 + v1) / 2, z + 0.4, lengths[g]),
                            attrs(L + "在制梁", (150, 190, 230), name=g))
    for slot, lyr, g in stored:
        u0, v0, u1, v1 = Y.storage()[slot]
        DOC.Objects.AddBrep(_yard_girder((u0 + u1) / 2, (v0 + v1) / 2, z + 0.5 + lyr * (C.H_GIRDER + 0.3), lengths[g]),
                            attrs(L + "存梁", (214, 210, 200), name=g))
    ux = (b[0] + b[2]) / 2
    dot("制梁台座 %d 个（蓝）" % C.N_BEDS, (Y.to_world((Y.beds()[0][0] + Y.beds()[-1][2]) / 2, -8.0) + (z + 1,)),
        L + "文字", 16)
    dot("存梁 %d 位 × %d 层" % (C.STORAGE_POSITIONS, C.STORAGE_LAYERS),
        (Y.to_world((Y.storage()[0][0] + Y.storage()[-1][2]) / 2, -8.0) + (z + 1,)), L + "文字", 16)
    dot("钢筋加工区", (Y.to_world((Y.rebar_area()[0] + Y.rebar_area()[2]) / 2, 16.0) + (z + 1,)), L + "文字", 15)
    dot("运梁便道 %.0f m → 0 号桥台" % Y.haul_length(), route[1], L + "文字", 15)
    dot("%s（存梁峰值日）：台座上 %d 片、存梁区 %d 片" % (day.isoformat(), len(beds), len(stored)),
        (Y.to_world(ux, C.BED_L + 16.0) + (z + 1,)), L + "文字", 17)
    step("梁场：%d 台座、%d 存梁位；%s 快照 台座 %d 片 / 存梁 %d 片" % (len(Y.beds()), len(Y.storage()),
                                                                day.isoformat(), len(beds), len(stored)))
    return day, len(beds), len(stored)


# ------------------------------------------------------------------ 图纸：纵断面
PROFILE_Y, VEX, ZREF, XREF = -420.0, 4.0, 85.0, C.BRIDGE_START - 50.0
LP = "图纸::纵断面"


def pz(elev):
    return (elev - ZREF) * VEX


def px(station):
    return station - XREF


def draw_profile(r):
    y = PROFILE_Y
    st = support_stations()
    s0, s1 = XREF, C.BRIDGE_START + C.N_SPANS * C.SPAN + 50.0
    c = dict(C.DECKS)["L"]

    def line(pts, rgb=(0, 0, 0), w=None):
        polyline([(px(s), y, pz(z)) for s, z in pts], LP, rgb, w)

    ss = [s0 + i for i in range(int(s1 - s0) + 1)]
    line([(s, AL.ground(s, 0.0)) for s in ss], (130, 90, 40), 0.35)
    line([(s, AL.profile(s)[0]) for s in ss], (210, 30, 30), 0.5)
    inside = [s for s in ss if st[0] <= s <= st[-1]]
    line([(s, AL.deck_top(s, c, "L")) for s in inside], (70, 70, 70))
    line([(s, AL.deck_top(s, c, "L") - C.PAVEMENT_T - C.H_GIRDER) for s in inside], (70, 70, 70))
    by_id = r["by_id"]
    tip_min = min(e.attrs["tip"] for e in r["els"] if e.cls == "pile")
    for k, s in enumerate(st):
        name = support_name(k)
        cap = by_id[("ABC-%s-L" if k in (0, C.N_SPANS) else "CAP-%s-L") % name]
        b0, b1 = cap.params["frame"]["b"]
        top, bot = cap.attrs["top"], cap.attrs["bottom"]
        line([(s + b0, bot), (s + b1, bot), (s + b1, top), (s + b0, top), (s + b0, bot)], (40, 40, 40))
        for e in (by_id.get("C-%s-L1" % name), by_id.get("PL-%s-L1" % name)):
            if e is None:
                continue
            z0, z1 = e.params["c"][2], e.params["c"][2] + e.params["h"]
            rr = e.params["r"]
            line([(s - rr, z1), (s - rr, z0), (s + rr, z0), (s + rr, z1)], (40, 40, 40))
        text(name, (px(s), y, pz(tip_min) - 6), 3.6, LP)
    z_unit = pz(max(AL.deck_top(s, c, "L") for s in inside)) + 16
    for k, s in enumerate(st):
        if support_kind(k) != "C":
            text("伸缩缝", (px(s), y, z_unit + 3), 2.8, LP, (200, 60, 30))
    for u, (a, b) in enumerate(unit_bounds(), 1):
        polyline([(px(st[a]) + 0.5, y, z_unit - 2), (px(st[a]) + 0.5, y, z_unit), (px(st[b]) - 0.5, y, z_unit),
                  (px(st[b]) - 0.5, y, z_unit - 2)], LP, (60, 60, 60))
        text("第%d联  %d×%.0f m" % (u, b - a, C.SPAN), (px((st[a] + st[b]) / 2), y, z_unit + 3), 3.4, LP)
    text("全长 %.0f m，%s，先简支后连续" % (st[-1] - st[0], " + ".join("%d×%.0f" % (n, C.SPAN) for n in C.UNITS)),
         (px((st[0] + st[-1]) / 2), y, z_unit + 11), 3.8, LP)
    # 竖曲线要素
    pvi_s, pvi_z = C.V_PVI[1]
    Lv = C.V_CURVE_LENGTH[1]
    g1 = (C.V_PVI[1][1] - C.V_PVI[0][1]) / (C.V_PVI[1][0] - C.V_PVI[0][0])
    g2 = (C.V_PVI[2][1] - C.V_PVI[1][1]) / (C.V_PVI[2][0] - C.V_PVI[1][0])
    R = abs(Lv / (g2 - g1))
    T = Lv / 2
    E = T * T / (2 * R)
    line([(pvi_s - T, AL.profile(pvi_s - T)[0]), (pvi_s, pvi_z), (pvi_s + T, AL.profile(pvi_s + T)[0])], (230, 150, 150))
    zc = pz(pvi_z) + 7
    polyline([(px(pvi_s - T), y, zc - 1.5), (px(pvi_s - T), y, zc), (px(pvi_s + T), y, zc), (px(pvi_s + T), y, zc - 1.5)],
             LP, (180, 20, 20))
    text("变坡点 %s  高程 %.3f   R=%.0f  T=%.0f  E=%.3f" % (AL.station_label(pvi_s), pvi_z, R, T, E),
         (px(pvi_s), y, zc + 2.5), 3.0, LP, (180, 20, 20))
    # 被交道路与净空
    g_road = AL.ground(C.ROAD_STATION, 0.0)
    line([(C.ROAD_STATION - C.ROAD_WIDTH / 2, g_road), (C.ROAD_STATION + C.ROAD_WIDTH / 2, g_road)], (40, 40, 200), 0.6)
    soffit = AL.profile(C.ROAD_STATION)[0] - C.PAVEMENT_T - C.H_GIRDER
    line([(C.ROAD_STATION, g_road), (C.ROAD_STATION, soffit)], (40, 40, 200))
    chk = next(ck for ck in r["checks"] if ck["name"].startswith("被交道路净空"))
    zm = pz((g_road + soffit) / 2)
    text("被交道路 %s" % AL.station_label(C.ROAD_STATION), (px(C.ROAD_STATION) + 2, y, zm + 2.2), 2.8, LP,
         (40, 40, 200), ha="l")
    text("净空要求 ≥ %.1f m，实测%s" % (C.ROAD_CLEARANCE, chk["detail"].split("（")[0].replace("最小", "最小 ")),
         (px(C.ROAD_STATION) + 2, y, zm - 2.2), 2.8, LP, (40, 40, 200), ha="l")
    text("纵断面图（沿路线中线；桥面、梁底、墩台取左幅；竖向比例放大 %d 倍）" % VEX, (px((s0 + s1) / 2), y, z_unit + 21), 4.6, LP)
    draw_profile_table(r, pz(tip_min) - 14)
    step("纵断面：地面线、设计线、桥面与梁底、13 处墩台、竖曲线要素、被交道路净空、资料表")


def draw_profile_table(r, z_top):
    """纵断面下方的资料表：设计高程 / 地面高程 / 坡度坡长 / 里程桩号 / 直线及平曲线。"""
    y = PROFILE_Y
    s0, s1 = XREF, C.BRIDGE_START + C.N_SPANS * C.SPAN + 50.0
    x0, x1 = -62.0, px(s1)
    rows = ["设计高程", "地面高程", "坡度 / 坡长", "里程桩号", "直线及平曲线"]
    H = 10.0
    for i in range(len(rows) + 1):
        polyline([(x0, y, z_top - i * H), (x1, y, z_top - i * H)], LP, (80, 80, 80))
    for xx in (x0, 0.0, x1):
        polyline([(xx, y, z_top), (xx, y, z_top - len(rows) * H)], LP, (80, 80, 80))
    for i, name in enumerate(rows):
        text(name, (x0 + 31, y, z_top - (i + 0.5) * H), 3.2, LP)
    st = support_stations()
    for s in st:
        text("%.3f" % AL.profile(s)[0], (px(s), y, z_top - 0.5 * H), 2.5, LP)
        text("%.3f" % AL.ground(s, 0.0), (px(s), y, z_top - 1.5 * H), 2.5, LP)
        text(AL.station_label(s)[:-4], (px(s), y, z_top - 3.5 * H), 2.5, LP)
        polyline([(px(s), y, z_top - 3 * H), (px(s), y, z_top - 3 * H + 1.5)], LP, (80, 80, 80))
    # 坡度 / 坡长：变坡点之间的斜线
    pv = C.V_PVI
    zr = z_top - 2 * H
    for (sa, za), (sb, zb) in zip(pv, pv[1:]):
        a, b = max(sa, s0), min(sb, s1)
        g = (zb - za) / (sb - sa)
        up = g > 0
        polyline([(px(a), y, zr - (H - 1.5 if up else 1.5)), (px(b), y, zr - (1.5 if up else H - 1.5))], LP, (80, 80, 80))
        text("%+.3f%% / %.0f m" % (g * 100, sb - sa), (px((a + b) / 2), y, zr - 0.5 * H + 2.4), 2.6, LP)
    for s, _ in pv[1:-1]:
        polyline([(px(s), y, zr), (px(s), y, zr - H)], LP, (80, 80, 80))
    # 直线及平曲线：直线画在中线上，左转曲线向上折（缓和曲线为斜线，圆曲线为平台）
    zb = z_top - 5 * H
    base, up = zb + 3.0, zb + 7.0
    bounds, s = [], C.START_STATION
    for kind, L, k0, k1 in C.H_SEGMENTS:
        bounds.append((kind, s, s + L, k0, k1))
        s += L
    pts = []
    for kind, a, b, k0, k1 in bounds:
        a2, b2 = max(a, s0), min(b, s1)
        if a2 >= b2:
            continue
        for ss_ in (a2, b2):
            k = k0 + (k1 - k0) * (ss_ - a) / (b - a) if b > a else k0
            pts.append((px(ss_), y, base + (up - base) * k * C.R_CURVE))
    polyline(pts, LP, (40, 40, 40), 0.4)
    names = ["ZH", "HY", "YH", "HZ"]
    for i, (kind, a, b, k0, k1) in enumerate(bounds[1:5]):
        if s0 <= a <= s1:
            text("%s %s" % (names[i], AL.station_label(a)[:-4]), (px(a), y, zb + 8.6 if i in (1, 2) else zb + 1.3),
                 2.2, LP)
    arc = bounds[2]
    text("R=%.0f  Ls=%.0f" % (C.R_CURVE, C.LS), (px((arc[1] + arc[2]) / 2), y, up - 1.6), 2.6, LP)


def profile_bbox(r):
    tip = min(e.attrs["tip"] for e in r["els"] if e.cls == "pile")
    top = pz(max(AL.deck_top(s, dict(C.DECKS)["L"], "L") for s in support_stations())) + 16 + 25
    return (-64.0, PROFILE_Y, pz(tip) - 14 - 5 * 10.0 - 3), (px(C.BRIDGE_START + C.N_SPANS * C.SPAN + 50) + 2,
                                                            PROFILE_Y, top)


# ------------------------------------------------------------------ 图纸：由模型剖切得到的横断面
SECTION_Y = -720.0
LS = "图纸::横断面"
SECTIONS = [("P06 墩顶横断面（%s）" % AL.station_label(C.BRIDGE_START + 6 * C.SPAN), C.BRIDGE_START + 6 * C.SPAN, 0.0),
            ("跨中横断面（%s，被交道路处）" % AL.station_label(C.ROAD_STATION), C.ROAD_STATION, 62.0)]
SECTION_FILL = {"girder": (214, 208, 196), "wet_joint": (176, 170, 160), "cantilever": (176, 170, 160),
                "continuity": (150, 158, 184), "pavement": (80, 80, 84), "barrier": (196, 194, 188),
                "cap": (204, 200, 192), "column": (204, 200, 192), "tie": (188, 184, 176), "pile": (174, 156, 132),
                "seat": (110, 110, 110), "bearing": (20, 20, 20), "temp_support": (240, 130, 20),
                "expansion_joint": (40, 40, 40), "abut_cap": (204, 200, 192), "backwall": (190, 186, 178)}


def draw_sections(r):
    tol = DOC.ModelAbsoluteTolerance
    n_cut = {}
    for title, s, xoff in SECTIONS:
        (qx, qy), t, n = frame(s)
        src = RG.Plane(RG.Point3d(qx, qy, 0), RG.Vector3d(n[0], n[1], 0), RG.Vector3d.ZAxis)
        dst = RG.Plane(RG.Point3d(xoff, SECTION_Y, 0), RG.Vector3d(-1, 0, 0), RG.Vector3d.ZAxis)
        xf = RG.Transform.PlaneToPlane(src, dst)
        cut = 0
        for e in r["els"]:
            g = GEOM[e.eid]
            bb = g.GetBoundingBox(True)
            ds = [(cx - qx) * t[0] + (cy - qy) * t[1] for cx in (bb.Min.X, bb.Max.X) for cy in (bb.Min.Y, bb.Max.Y)]
            if min(ds) > 0.01 or max(ds) < -0.01:
                continue
            curves = []
            if isinstance(g, RG.Mesh):
                for pl in (RG.Intersect.Intersection.MeshPlane(g, src) or []):
                    curves.append(pl.ToPolylineCurve())
            else:
                ok, crvs, _ = RG.Intersect.Intersection.BrepPlane(g, src, tol)
                if ok and crvs:
                    curves += list(crvs)
            if not curves:
                continue
            for crv in RG.Curve.JoinCurves(curves, tol * 10):
                crv.Transform(xf)
                a = attrs(LS, (30, 30, 30), name=e.eid, mat=False)
                DOC.Objects.AddCurve(crv, a)
                if crv.IsClosed:
                    fill(crv, LS + "::填充", SECTION_FILL.get(e.cls, (200, 200, 200)))
            cut += 1
        n_cut[title] = cut
        # 地面线（与被交道路）直接按地面函数取 ±22 m，不去切整块地形网格
        g_pts = [(xoff - a, SECTION_Y, AL.ground(s, a)) for a in [-22.0 + 0.5 * i for i in range(89)]]
        polyline(g_pts, LS, (130, 90, 40), 0.35)
        if abs(s - C.ROAD_STATION) < 1e-6:
            polyline([(xoff - a, SECTION_Y, AL.ground(s, a) + 0.05) for a in (-22.0, 22.0)], LS, (40, 40, 200), 0.8)
        top = max(AL.deck_top(s, c, d) for d, c in C.DECKS) + C.BARRIER_H
        text(title, (xoff, SECTION_Y, top + 5.0), 1.5, LS)
        for d, c in C.DECKS:
            sl = AL.slope_left(s, d)
            text("%s  i=%.1f%%" % ({"L": "左幅", "R": "右幅"}[d], abs(sl) * 100),
                 (xoff - c, SECTION_Y, AL.deck_top(s, c, d) + 2.4), 0.9, LS)
        both = [AL.slope_left(s, d) for d, _ in C.DECKS]
        if min(both) > 0:
            text("位于圆曲线段：两幅均按 %.0f%% 超高向曲线内侧（左）倾斜" % (C.SUPERELEVATION * 100),
                 (xoff, SECTION_Y, top + 3.2), 0.85, LS)
    # P06 注记
    x0 = SECTIONS[0][2]
    cap = r["by_id"]["CAP-P06-R"]
    col = r["by_id"]["C-P06-R1"]
    pile = r["by_id"]["PL-P06-R1"]
    cR = dict(C.DECKS)["R"]
    notes = [("墩顶现浇连续段（含横梁），宽 %.2f–%.2f m" % (r["by_id"]["CS-P06-R"].attrs["joint_min"],
                                                     r["by_id"]["CS-P06-R"].attrs["joint_max"]), cap.attrs["top"] + 3.2),
             ("永久支座 φ550 + 垫石", cap.attrs["top"] + 0.9),
             ("盖梁 %.1f×%.1f m，顶面随横坡" % (C.CAP_W, C.CAP_H), cap.attrs["bottom"] + 0.8),
             ("墩柱 φ%.1f m，高 %.2f m" % (C.COLUMN_D, col.attrs["height"]), col.params["c"][2] + col.params["h"] / 2),
             ("系梁 %.1f×%.1f m" % (C.TIE_W, C.TIE_H), r["by_id"]["TB-P06-R"].params["c"][2] + 0.6),
             ("钻孔桩 φ%.1f m，长 %.0f m，桩底 %.2f" % (C.PILE_D, C.PILE_LEN_PIER, pile.attrs["tip"]), pile.attrs["tip"] + 8.0)]
    for label, z in notes:
        text(label, (x0 - cR + 8.2, SECTION_Y, z), 0.85, LS, ha="l")
    # 跨中：净空标注（左幅 5 号梁，与全宽最小值所在的梁同一片）
    x1 = SECTIONS[1][2]
    s = C.ROAD_STATION
    g = r["by_id"]["G-L07-5"]
    p0, p1 = g.params["p0"], g.params["p1"]
    (qx, qy), t, n = frame(s)
    f = ((qx - p0[0]) * t[0] + (qy - p0[1]) * t[1]) / ((p1[0] - p0[0]) * t[0] + (p1[1] - p0[1]) * t[1])
    q = [p0[j] + (p1[j] - p0[j]) * f for j in range(3)]
    a = (q[0] - qx) * n[0] + (q[1] - qy) * n[1]
    soffit = q[2] - C.H_GIRDER
    road = AL.ground(s, a)
    polyline([(x1 - a, SECTION_Y, road), (x1 - a, SECTION_Y, soffit)], LS, (40, 40, 200), 0.35)
    for zz in (road, soffit):
        polyline([(x1 - a - 0.6, SECTION_Y, zz), (x1 - a + 0.6, SECTION_Y, zz)], LS, (40, 40, 200), 0.35)
    chk = next(ck for ck in r["checks"] if ck["name"].startswith("被交道路净空"))
    text("净空 %.2f m（此断面）；全宽%s ≥ %.1f m" % (soffit - road, chk["detail"].split("（")[0], C.ROAD_CLEARANCE),
         (x1 - a + 1.0, SECTION_Y, (road + soffit) / 2), 0.9, LS, (40, 40, 200), ha="l")
    text("被交道路（宽 %.0f m，与路线正交）" % C.ROAD_WIDTH, (x1 + 8.0, SECTION_Y, AL.ground(s, -8.0) + 1.0), 0.9, LS,
         (40, 40, 200))
    text("预制 T 梁 ×5 / 幅，梁高 %.1f m；湿接缝 %.2f m" % (C.H_GIRDER, 0.60), (x1, SECTION_Y, soffit - 1.6), 0.9, LS)
    step("横断面（模型剖切）：%s" % "，".join("%s 切到 %d 个构件" % kv for kv in n_cut.items()))


def sections_bbox(r):
    lo_z = min(e.attrs["tip"] for e in r["els"] if e.cls == "pile" and e.attrs["support"] == 6) - 1.5
    hi_z = max(AL.deck_top(C.ROAD_STATION, c, d) for d, c in C.DECKS) + 8
    return (SECTIONS[0][2] - 20, SECTION_Y, lo_z), (SECTIONS[1][2] + 20, SECTION_Y, hi_z)


# ------------------------------------------------------------------ 图纸：预制梁长布置
SPEC_Y = -1100.0
LG = "图纸::梁长布置"
CELL_W, CELL_H = 30.0, 6.0


def spec_rows():
    """行序（自上而下）：左幅 5→1 号梁，右幅 5→1 号梁——平面上左侧在上。"""
    out = []
    n = len(C.GIRDER_OFFSETS)
    for d, _ in C.DECKS:
        for i in range(n, 0, -1):
            out.append((d, i))
    return out


def row_z(idx):
    return -idx * CELL_H - (4.0 if idx >= len(C.GIRDER_OFFSETS) else 0.0)


def draw_length_specs(r):
    y = SPEC_Y
    colors = spec_colors(r)
    by_id = r["by_id"]
    counts = {}
    for e in r["els"]:
        if e.cls == "girder":
            counts[spec_key(e)] = counts.get(spec_key(e), 0) + 1
    for idx, (d, i) in enumerate(spec_rows()):
        z = row_z(idx)
        text("%s %d 号梁" % ({"L": "左幅", "R": "右幅"}[d], i), (-3.0, y, z - CELL_H / 2), 2.4, LG, ha="r")
        for k in range(1, C.N_SPANS + 1):
            e = by_id["G-%s%02d-%d" % (d, k, i)]
            x0 = (k - 1) * CELL_W
            crv = rect_curve(x0 + 0.4, z - CELL_H + 0.4, x0 + CELL_W - 0.4, z - 0.4, y)
            fill(crv, LG + "::填充", colors[spec_key(e)])
            rgb = (255, 255, 255) if colors[spec_key(e)] in MID_COLORS[:2] + END_COLORS[:2] else (0, 0, 0)
            text("%.2f" % e.attrs["length"], (x0 + CELL_W / 2, y + -0.1, z - CELL_H / 2), 2.6, LG, rgb)
    st = support_stations()
    z_head = CELL_H * 0 + 2.5
    for k in range(1, C.N_SPANS + 1):
        text("第%d跨" % k, ((k - 0.5) * CELL_W, y, z_head), 2.2, LG)
    for k in range(C.N_SPANS + 1):
        kind = support_kind(k)
        text(support_name(k), (k * CELL_W, y, z_head + 4.5), 2.2, LG, (200, 60, 30) if kind != "C" else (0, 0, 0))
    for u, (a, b) in enumerate(unit_bounds(), 1):
        polyline([(a * CELL_W + 0.5, y, z_head + 8.0), (a * CELL_W + 0.5, y, z_head + 9.5), (b * CELL_W - 0.5, y, z_head + 9.5),
                  (b * CELL_W - 0.5, y, z_head + 8.0)], LG, (60, 60, 60))
        text("第%d联：边跨梁（一端伸缩、一端连续）+ 中跨梁（两端连续）" % u, ((a + b) / 2 * CELL_W, y, z_head + 12.0), 2.2, LG)
    z_leg = row_z(len(spec_rows()) - 1) - CELL_H - 6.0
    keys = sorted(colors, key=lambda k: (k.split()[0] != "中跨", k))
    for j, key in enumerate(keys):
        col, rw = j % 5, j // 5
        x0 = col * 72.0
        z0 = z_leg - rw * 7.0
        fill(rect_curve(x0, z0 - 4.0, x0 + 8.0, z0, y), LG + "::填充", colors[key])
        text("%s ×%d" % (key, counts[key]), (x0 + 10.0, y, z0 - 2.0), 2.6, LG, ha="l")
    n_specs = len(colors)
    text("预制 T 梁长度布置：%d 种长度（逐片按名义缝宽下料为 %d 种）；连续端缝宽在 %.2f–%.2f m 内浮动吸收曲线造成的梁长差" % (
        n_specs, naive_spec_count(), 2 * C.CONT_HALF_MIN, 2 * C.CONT_HALF_MAX), (C.N_SPANS * CELL_W / 2, y, z_head + 20.0),
        3.2, LG)
    step("梁长布置图：%d 行 × %d 跨，%d 种长度" % (len(spec_rows()), C.N_SPANS, n_specs))


def length_specs_bbox():
    z_leg = row_z(len(spec_rows()) - 1) - CELL_H - 6.0
    return (-26.0, SPEC_Y, z_leg - 12.0), (C.N_SPANS * CELL_W + 2.0, SPEC_Y, 2.5 + 24.0)


# ------------------------------------------------------------------ 出图
def display_mode(*names):
    modes = Rhino.Display.DisplayModeDescription.GetDisplayModes()
    for nm in names:
        for m in modes:
            if m.EnglishName == nm:
                return m
    return None


def capture(view, path, w=2000, content_aspect=None):
    """截图比例跟视口一致；给了 content_aspect 就再按内容比例居中裁一刀。"""
    view.Redraw()
    Rhino.RhinoApp.Wait()
    vs = view.ActiveViewport.Size
    aspect = float(vs.Width) / max(1, vs.Height)
    h = int(round(w / aspect))
    vc = Rhino.Display.ViewCapture()
    vc.Width = w
    vc.Height = h
    vc.ScaleScreenItems = False
    vc.DrawAxes = False
    vc.DrawGrid = False
    vc.DrawGridAxes = False
    vc.TransparentBackground = False
    bmp = vc.CaptureToBitmap(view)
    if bmp is None:
        raise RuntimeError("截图失败：" + path)
    if content_aspect:
        cw, ch = w, h
        if content_aspect < aspect:
            cw = min(w, int(h * content_aspect * 1.02))
        else:
            ch = min(h, int(w / content_aspect * 1.02))
        rect = SD.Rectangle((w - cw) // 2, (h - ch) // 2, cw, ch)
        bmp = bmp.Clone(rect, bmp.PixelFormat)
    bmp.Save(path, SD.Imaging.ImageFormat.Png)
    step("出图 %s（%d×%d）" % (os.path.basename(path), bmp.Width, bmp.Height))


def stitch(paths, captions, out, cols, legend=None):
    """把几张截图拼成一张，每张上方一行说明；legend 给了就在底部加一条色块图例。"""
    bmps = [SD.Bitmap(p) for p in paths]
    w, h = bmps[0].Width, bmps[0].Height
    rows = (len(bmps) + cols - 1) // cols
    cap_h = 64
    leg_h = 70 if legend else 0
    canvas = SD.Bitmap(w * cols, (h + cap_h) * rows + leg_h)
    g = SD.Graphics.FromImage(canvas)
    g.Clear(SD.Color.White)
    g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit
    font = SD.Font(FONT, 26.0, SD.FontStyle.Regular, SD.GraphicsUnit.Pixel)
    brush = SD.SolidBrush(SD.Color.FromArgb(20, 20, 20))
    for i, b in enumerate(bmps):
        x, y = (i % cols) * w, (i // cols) * (h + cap_h)
        g.DrawImage(b, x, y + cap_h, w, h)
        g.DrawString(captions[i], font, brush, SD.PointF(x + 18, y + 16))
    if legend:
        y0 = (h + cap_h) * rows + 18
        small = SD.Font(FONT, 22.0, SD.FontStyle.Regular, SD.GraphicsUnit.Pixel)
        step_x = (w * cols - 40) // len(legend)
        for j, (rgb, label) in enumerate(legend):
            x0 = 20 + j * step_x
            g.FillRectangle(SD.SolidBrush(SD.Color.FromArgb(*rgb)), x0, y0, 34, 34)
            g.DrawString(label, small, brush, SD.PointF(x0 + 42, y0 + 3))
    g.Dispose()
    canvas.Save(out, SD.Imaging.ImageFormat.Png)
    for b in bmps:
        b.Dispose()
    step("拼图 %s（%d 张，%d×%d）" % (os.path.basename(out), len(paths), canvas.Width, canvas.Height))


TOPS = ("路线", "地形", "上部结构", "下部结构", "梁场", "分析", "标注", "图纸")


def only(*visible):
    for path in _layers:
        on = any(path == v or path.startswith(v + "::") or v.startswith(path + "::") for v in visible)
        if path.split("::")[0] in TOPS:
            set_visible(path, on)
    DOC.Views.Redraw()


def _pt(s, a, z, b=0.0):
    """桩号 s、偏距 a（左正）、沿切线再走 b 处的点。"""
    (x, y), t, n = frame(s)
    return (x + b * t[0] + a * n[0], y + b * t[1] + a * n[1], z)


def sun_on():
    try:
        sun = DOC.Lights.Sun
        try:
            sun.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
        except Exception:
            pass
        sun.Enabled = True
        sun.ManualControl = True
        sun.Altitude = 42.0
        sun.Azimuth = 200.0
        try:
            sun.EndChange()
        except Exception:
            pass
        step("太阳光：开，高度角 42°")
    except Exception as ex:
        step("太阳光设置失败：%s" % ex)


def shoot(img_dir, r, yard_info):
    view = DOC.Views.Find("Perspective", False) or DOC.Views.ActiveView
    DOC.Views.ActiveView = view
    view.Maximized = True
    vp = view.ActiveViewport
    rendered = display_mode("Rendered", "Shaded")
    wire = display_mode("Wireframe", "Shaded")
    tmp = tempfile.mkdtemp()

    def persp(target, cam, lens=35):
        vp.ChangeToPerspectiveProjection(True, lens)
        vp.SetCameraLocations(RG.Point3d(*target), RG.Point3d(*cam))

    def ortho_front(lo, hi, pad=1.03):
        vp.SetProjection(Rhino.Display.DefinedViewportProjection.Front, None, False)
        dx, dz = hi[0] - lo[0], hi[2] - lo[2]
        bb = RG.BoundingBox(RG.Point3d(lo[0] - dx * (pad - 1) / 2, lo[1], lo[2] - dz * (pad - 1) / 2),
                            RG.Point3d(hi[0] + dx * (pad - 1) / 2, hi[1], hi[2] + dz * (pad - 1) / 2))
        vp.ZoomBoundingBox(bb)
        return dx / dz

    st = support_stations()
    mid = C.BRIDGE_START + C.N_SPANS * C.SPAN / 2
    z_mid = AL.profile(mid)[0]

    # 1 全景：从曲线外侧、桥尾方向回看，谷底墩最高
    only("地形", "上部结构", "下部结构")
    vp.DisplayMode = rendered
    persp(_pt(C.BRIDGE_START + 170, -4, 121), _pt(C.BRIDGE_START + 400, -150, 168), 32)
    capture(view, os.path.join(img_dir, "hero.png"), 2000)

    # 2 连续墩近景：① 架梁阶段（梁端落在临时支座上）② 体系转换后（浇连续段、拆临时支座）
    k = 2
    s = st[k]
    cR = dict(C.DECKS)["R"]
    name = support_name(k)
    b = r["by_id"]["B-%s-R1" % name]
    bz = b.params["c"][2]
    tgt = (b.params["c"][0], b.params["c"][1], bz + 0.35)
    (qx, qy), t, n = frame(s)
    cam = (tgt[0] - 13.0 * n[0] + 7.0 * t[0], tgt[1] - 13.0 * n[1] + 7.0 * t[1], tgt[2] + 3.2)
    cs = [e for e in r["els"] if e.cls == "continuity" and e.attrs["support"] == k and e.deck == "R"]
    ts = [e for e in r["els"] if e.cls == "temp_support" and e.attrs["support"] == k and e.deck == "R"]
    only("下部结构", "上部结构::预制T梁", "上部结构::墩顶现浇连续段", "地形::地面")
    vp.DisplayMode = rendered
    for e in cs:
        DOC.Objects.Hide(OBJ[e.eid], True)
    persp(tgt, cam, 45)
    a_path, b_path = os.path.join(tmp, "pier_a.png"), os.path.join(tmp, "pier_b.png")
    capture(view, a_path, 1300)
    for e in cs:
        DOC.Objects.Show(OBJ[e.eid], True)
    for e in ts:
        DOC.Objects.Hide(OBJ[e.eid], True)
    capture(view, b_path, 1300)
    for e in ts:
        DOC.Objects.Show(OBJ[e.eid], True)
    ga, gb = r["by_id"]["G-R%02d-1" % k], r["by_id"]["G-R%02d-1" % (k + 1)]
    gap = ga.attrs["half_b"] + gb.attrs["half_a"]
    stitch([a_path, b_path],
           ["① 架梁：%s 两跨梁端落在临时支座（橙）上，梁端缝 %.2f m，墩中心永久支座（黑）不受力" % (name, gap),
            "② 体系转换：浇墩顶连续段（蓝灰）、张拉负弯矩钢束、拆临时支座，由永久支座受力"],
           os.path.join(img_dir, "pier_detail.png"), 2)

    # 3 4D：四个日期的桥上状态（按架设周着色；连续段按浇筑日期出现，临时支座在体系转换后消失）
    convs = S.conversions(r["rows"])
    first = next(m for m in convs if m["deck"] == "L" and m["unit"] == 1)
    dates = [first["erected"], next(m for m in convs if m["deck"] == "L" and m["unit"] == 3)["erected"],
             next(m for m in convs if m["deck"] == "R" and m["unit"] == 1)["conversion"], max(m["conversion"] for m in convs)]
    only("地形", "下部结构", "分析::按架设周", "上部结构::墩顶现浇连续段")
    vp.DisplayMode = rendered
    persp(_pt(mid, 8, z_mid - 12), _pt(mid - 30, -250, z_mid + 175), 40)
    conv_of = {(m["deck"], m["unit"]): m for m in convs}

    def unit_at(k_sup):
        return next(u for u, (a0, b0) in enumerate(unit_bounds(), 1) if a0 < k_sup < b0)

    tiles, caps = [], []
    for i, d in enumerate(dates):
        n_up = 0
        for e in r["els"]:
            if e.cls == "girder":
                on = r["plan"][e.eid]["erect"] <= d
                n_up += on
                (DOC.Objects.Show if on else DOC.Objects.Hide)(WEEK_OBJ[e.eid], True)
                continue
            if e.cls == "continuity":
                on = conv_of[(e.deck, e.attrs["unit"])]["cast"] <= d
            elif e.cls == "temp_support":           # 梁架上去之后才受力，体系转换那天拆除
                girder = "G-" + e.eid[3:-1]
                on = r["plan"][girder]["erect"] <= d < conv_of[(e.deck, unit_at(e.attrs["support"]))]["conversion"]
            else:
                continue
            (DOC.Objects.Show if on else DOC.Objects.Hide)(OBJ[e.eid], True)
        done = [m for m in convs if m["conversion"] <= d]
        p = os.path.join(tmp, "d%d.png" % i)
        capture(view, p, 1200)
        tiles.append(p)
        caps.append("%s  已架 %d / %d 片；完成体系转换的联 %d / %d（左右幅各 %d 联）" % (
            d.isoformat(), n_up, len(r["rows"]), len(done), len(convs), len(C.UNITS)))
    for e in r["els"]:
        if e.cls in ("continuity", "temp_support"):
            DOC.Objects.Show(OBJ[e.eid], True)
        if e.cls == "girder":
            DOC.Objects.Show(WEEK_OBJ[e.eid], True)
    wk0 = r["summary"]["erect_first"]
    legend = []
    for i in range((r["summary"]["erect_last"] - wk0).days // 7 + 1):
        d0 = wk0 + datetime.timedelta(days=7 * i)
        d1 = min(d0 + datetime.timedelta(days=6), r["summary"]["erect_last"])
        legend.append((WEEK_COLORS[min(i, len(WEEK_COLORS) - 1)], "第%d周 %s–%s" % (i + 1, d0.strftime("%m-%d"),
                                                                                    d1.strftime("%m-%d"))))
    stitch(tiles, caps, os.path.join(img_dir, "erection_4d.png"), 2, legend)

    # 4 梁场（存梁峰值日）
    only("梁场", "地形", "路线::中线（设计高程）", "上部结构", "下部结构")
    vp.DisplayMode = rendered
    bd = Y.boundary()
    (ox, oy), u, v = Y.frame()
    zc = yard_pad_z()[0]
    ctr = Y.to_world((bd[0] + bd[2]) / 2 + 25, (bd[1] + bd[3]) / 2 - 5)
    cam = Y.to_world(bd[0] - 55, bd[3] + 95)
    persp((ctr[0], ctr[1], zc), (cam[0], cam[1], zc + 95), 35)
    capture(view, os.path.join(img_dir, "yard.png"), 2000)

    # 5–7 图纸：白底线框
    app = Rhino.ApplicationSettings.AppearanceSettings
    bg = app.ViewportBackgroundColor
    app.ViewportBackgroundColor = SD.Color.White
    try:
        vp.DisplayMode = wire
        only("图纸::纵断面")
        lo, hi = profile_bbox(r)
        capture(view, os.path.join(img_dir, "profile.png"), 2400, ortho_front(lo, hi))
        only("图纸::横断面")
        lo, hi = sections_bbox(r)
        capture(view, os.path.join(img_dir, "sections.png"), 2200, ortho_front(lo, hi))
        only("图纸::梁长布置")
        lo, hi = length_specs_bbox()
        capture(view, os.path.join(img_dir, "length_specs.png"), 2400, ortho_front(lo, hi))
    finally:
        app.ViewportBackgroundColor = bg
    only(*TOPS)
    vp.DisplayMode = rendered


def scrub_local_paths():
    """出完图再清掉渲染环境：Rhino 默认的 Studio 环境会把本机 AppData 下的贴图路径写进 .3dm。"""
    n = 0
    try:
        for env in list(DOC.RenderEnvironments):
            if DOC.RenderEnvironments.Remove(env):
                n += 1
    except Exception as ex:
        step("清理渲染环境失败：%s" % ex)
    try:
        DOC.RenderSettings.BackgroundStyle = Rhino.Display.BackgroundStyle.SolidColor
    except Exception as ex:
        step("背景改纯色失败：%s" % ex)
    step("清理渲染环境 %d 个（避免把本机路径写进 .3dm）" % n)


def main():
    t0 = time.time()
    DOC.ModelUnitSystem = Rhino.UnitSystem.Meters
    DOC.ModelAbsoluteTolerance = 0.001
    r = compute()
    from bridge.pipeline import all_checks
    r["checks"] = all_checks(r)
    step("算完：%d 个构件，检查 %d/%d 通过" % (len(r["els"]), sum(c["pass"] for c in r["checks"]), len(r["checks"])))
    DOC.Strings.SetString("项目", "高速公路 T 梁桥 BIM（虚构示例）")
    DOC.Strings.SetString("桥跨", " + ".join("%d×%.0f m" % (n, C.SPAN) for n in C.UNITS))
    DOC.Strings.SetString("构件数", str(len(r["els"])))
    build(r)
    draw_context(r)
    yard_info = draw_yard(r)
    draw_profile(r)
    draw_sections(r)
    draw_length_specs(r)
    sun_on()
    img_dir = os.path.join(ROOT, "docs", "img")
    if not os.path.isdir(img_dir):
        os.makedirs(img_dir)
    shoot(img_dir, r, yard_info)
    scrub_local_paths()
    out = os.path.join(ROOT, "model", "bridge_bim.3dm")
    opt = Rhino.FileIO.FileWriteOptions()
    for k, v in (("IncludeRenderMeshes", False), ("IncludeHistory", False), ("IncludePreviewImage", True)):
        try:
            setattr(opt, k, v)
        except Exception:
            pass
    # .3dm 会把自己的存盘路径写进文件头：先存到不含用户名的公共目录，再复制进仓库
    neutral = os.path.join(os.environ.get("PUBLIC") or tempfile.gettempdir(), "Documents", "bridge-bim")
    os.makedirs(neutral, exist_ok=True)
    tmp_out = os.path.join(neutral, "bridge_bim.3dm")
    ok = DOC.WriteFile(tmp_out, opt)
    if ok:
        shutil.copyfile(tmp_out, out)
    step("存盘 bridge_bim.3dm：%s（%d 个材质）" % (ok, DOC.Materials.Count))
    LOG.update({"ok": bool(ok), "seconds": round(time.time() - t0, 1), "objects": DOC.Objects.Count,
                "rhino": str(Rhino.RhinoApp.Version)})


try:
    main()
except Exception:
    LOG["ok"] = False
    LOG["error"] = traceback.format_exc()
finally:
    DOC.Modified = False
    os.makedirs(os.path.join(ROOT, "model"), exist_ok=True)
    with open(os.path.join(ROOT, "model", "build_log.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(LOG, f, ensure_ascii=False, indent=1)
