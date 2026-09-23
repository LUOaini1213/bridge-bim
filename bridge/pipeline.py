"""把路线、构件、检查、梁场与架梁计划算成一组交付表。Rhino 脚本、IFC 导出、测试共用。"""
import math
from collections import OrderedDict, defaultdict
from datetime import timedelta

from . import alignment as AL, config as C, schedule as S, yard as Y
from .checks import continuity_joints, run_all
from .model import build, chords, equalize, naive_lengths, support_kind, support_name, support_stations

CLASS_NAMES = OrderedDict([
    ("girder", "预制 T 梁"), ("wet_joint", "湿接缝"), ("cantilever", "翼缘现浇段"), ("continuity", "墩顶现浇连续段"),
    ("pavement", "桥面铺装"), ("barrier", "混凝土护栏"), ("seat", "支座垫石"),
    ("cap", "盖梁"), ("abut_cap", "桥台台帽"), ("backwall", "桥台背墙"), ("column", "墩柱"),
    ("tie", "系梁"), ("pile", "钻孔灌注桩"),
])
COUNT_ITEMS = OrderedDict([("bearing", "板式橡胶支座"), ("temp_support", "临时支座（体系转换后拆除）"),
                           ("expansion_joint", "伸缩装置")])
KIND_NAMES = {"A": "桥台", "T": "过渡墩", "C": "连续墩"}


def compute(n_beds=None, erect_start=None):
    els, sup = build()
    rows, idle = S.plan(n_beds=n_beds, erect_start=erect_start)
    sm = S.summarize(rows, idle)
    return {"els": els, "sup": sup, "rows": rows, "idle": idle, "summary": sm,
            "by_id": {e.eid: e for e in els}, "plan": {r["girder"]: r for r in rows}}


def girder_rows(r):
    out = []
    for e in sorted((e for e in r["els"] if e.cls == "girder"), key=lambda e: r["plan"][e.eid]["order"]):
        p, a = r["plan"][e.eid], e.attrs
        out.append(OrderedDict([
            ("girder", e.eid), ("deck", e.deck), ("span", a["span"]), ("pos", a["girder"]), ("unit", a["unit"]),
            ("family", a["family"]), ("end_a", a["end_a"]), ("end_b", a["end_b"]),
            ("offset_m", "%.3f" % a["offset"]), ("length_m", "%.2f" % a["length"]),
            ("half_a_m", "%.4f" % a["half_a"]), ("half_b_m", "%.4f" % a["half_b"]),
            ("skew_a_deg", "%.3f" % a["skew_a"]), ("skew_b_deg", "%.3f" % a["skew_b"]),
            ("weight_t", "%.2f" % (e.volume * C.RC_DENSITY_T)), ("order", p["order"]), ("bed", p["bed"]),
            ("cast", p["cast"].isoformat()), ("to_storage", p["to_storage"].isoformat()),
            ("erect", p["erect"].isoformat()), ("storage_days", p["storage_days"]), ("age_at_erect", p["age_at_erect"]),
        ]))
    return out


def bearing_rows(r):
    """支座垫石标高表：全部永久支座。"""
    out = []
    for b in sorted((b for b in r["sup"] if b["kind"] != "temp"), key=lambda b: (b["support"], b["deck"], b["id"])):
        out.append(OrderedDict([
            ("bearing", b["id"]), ("kind", "连续墩" if b["kind"] == "cont" else "伸缩端"), ("size", b["size"]),
            ("support", support_name(b["support"])), ("deck", b["deck"]), ("line", b["line"]),
            ("girders", "/".join(b["girders"])), ("x", "%.4f" % b["x"]), ("y", "%.4f" % b["y"]),
            ("bearing_top", "%.4f" % b["top"]), ("seat_top", "%.4f" % b["seat_top"]),
            ("cap_top", "%.4f" % b["cap_top"]), ("seat_height", "%.4f" % b["seat_height"]),
        ]))
    return out


def temp_rows(r):
    out = []
    for b in sorted((b for b in r["sup"] if b["kind"] == "temp"), key=lambda b: (b["support"], b["deck"], b["id"])):
        out.append(OrderedDict([
            ("support_id", b["id"]), ("support", support_name(b["support"])), ("deck", b["deck"]),
            ("girder", b["girders"][0]), ("x", "%.4f" % b["x"]), ("y", "%.4f" % b["y"]),
            ("top", "%.4f" % b["top"]), ("bottom", "%.4f" % b["bottom"]), ("height", "%.4f" % b["height"]),
        ]))
    return out


def joint_rows(r):
    """梁端缝：连续墩逐梁位的现浇连续段宽，过渡墩与桥台的伸缩缝宽。"""
    out = []
    for k, d, i, w, _ in continuity_joints(r["els"]):
        out.append(OrderedDict([("support", support_name(k)), ("kind", "现浇连续段"), ("deck", d), ("line", i),
                                ("width_m", "%.4f" % w)]))
    for e in (e for e in r["els"] if e.cls == "expansion_joint"):
        out.append(OrderedDict([("support", support_name(e.attrs["support"])), ("kind", "伸缩缝"), ("deck", e.deck),
                                ("line", ""), ("width_m", "%.4f" % e.attrs["gap"])]))
    return out


def substructure_rows(r):
    out = []
    for k, st in enumerate(support_stations()):
        for d, c in C.DECKS:
            cap = r["by_id"][("ABC-%s-%s" if k in (0, C.N_SPANS) else "CAP-%s-%s") % (support_name(k), d)]
            cols = [e for e in r["els"] if e.cls == "column" and e.attrs["support"] == k and e.deck == d]
            piles = [e for e in r["els"] if e.cls == "pile" and e.attrs["support"] == k and e.deck == d]
            out.append(OrderedDict([
                ("support", support_name(k)), ("kind", KIND_NAMES[support_kind(k)]), ("station", AL.station_label(st)),
                ("deck", d), ("ground", "%.3f" % AL.ground(st, c)), ("cap_top", "%.3f" % cap.attrs["top"]),
                ("cap_slope", "%.4f" % cap.params["frame"]["slope"]), ("cap_bottom", "%.3f" % cap.attrs["bottom"]),
                ("column_height_max", "%.3f" % max([e.attrs["height"] for e in cols] or [0.0])),
                ("pile_tip", "%.3f" % min(e.attrs["tip"] for e in piles)), ("columns", len(cols)), ("piles", len(piles)),
            ]))
    return out


def takeoff_rows(r):
    agg = defaultdict(lambda: {"count": 0, "vol": 0.0})
    for e in r["els"]:
        a = agg[e.cls]
        a["count"] += 1
        a["vol"] += e.volume
    out = []
    tot_c = tot_r = 0.0
    n_struct = 0
    for cls, name in CLASS_NAMES.items():
        a = agg[cls]
        rebar = a["vol"] * C.REBAR_KG_M3.get(cls, 0.0) / 1000.0
        if cls != "pavement":
            tot_c += a["vol"]
            n_struct += a["count"]
        tot_r += rebar
        out.append(OrderedDict([("class", cls), ("name", name), ("count", a["count"]), ("unit", "件"),
                                ("concrete_m3", "%.2f" % a["vol"]), ("grade", C.CONCRETE_GRADE.get(cls, "")),
                                ("rebar_t", "%.2f" % rebar)]))
    out.append(OrderedDict([("class", "total"), ("name", "结构混凝土合计（不含铺装）"), ("count", n_struct),
                            ("unit", "件"), ("concrete_m3", "%.2f" % tot_c), ("grade", ""), ("rebar_t", "%.2f" % tot_r)]))
    sizes = defaultdict(int)
    for b in r["sup"]:
        if b["kind"] != "temp":
            sizes[b["size"]] += 1
    for cls, name in COUNT_ITEMS.items():
        spec = {"bearing": "、".join("%s ×%d" % kv for kv in sorted(sizes.items())),
                "temp_support": "%.0f×%.0f mm 砂筒" % (C.TEMP_W * 1000, C.TEMP_W * 1000),
                "expansion_joint": "每道 %.2f m" % C.DECK_WIDTH}[cls]
        out.append(OrderedDict([("class", cls), ("name", name), ("count", agg[cls]["count"]),
                                ("unit", "道" if cls == "expansion_joint" else "个"), ("concrete_m3", ""),
                                ("grade", spec), ("rebar_t", "")]))
    return out


def element_rows(r):
    """每个构件挂到 Rhino 对象上的属性（全是字符串）。.3dm 里读回来要与这里逐项相等，由测试核。"""
    names = dict(CLASS_NAMES, **COUNT_ITEMS)
    extra = {}
    for row in girder_rows(r):
        extra[row["girder"]] = row
    for row in bearing_rows(r):
        extra[row["bearing"]] = row
    for row in temp_rows(r):
        extra[row["support_id"]] = row
    out = OrderedDict()
    for e in r["els"]:
        row = OrderedDict([("eid", e.eid), ("class", e.cls), ("name", names[e.cls]), ("part", e.part),
                           ("deck", e.deck), ("volume_m3", "%.4f" % e.volume)])
        for k in ("span", "unit", "support"):
            if k in e.attrs:
                row[k] = support_name(e.attrs[k]) if k == "support" else str(e.attrs[k])
        for k, fmt in (("height", "%.4f"), ("joint_min", "%.4f"), ("joint_max", "%.4f"), ("gap", "%.4f"),
                       ("bearing", "%s")):
            if k in e.attrs and e.cls not in ("bearing", "temp_support"):
                row[k] = fmt % e.attrs[k]
        for k, v in extra.get(e.eid, {}).items():
            row.setdefault(k, str(v))
        out[e.eid] = row
    return out


def length_spec_rows(r):
    cnt = defaultdict(int)
    for g in girder_rows(r):
        cnt[(g["family"], g["length_m"])] += 1
    return [OrderedDict([("family", f), ("length_m", L), ("count", cnt[(f, L)])]) for f, L in sorted(cnt)]


SPEC_TOLERANCES = (0.005, 0.02, 0.04, 0.06, 0.075, 0.10, 0.125, 0.15)


def spec_sensitivity_rows():
    """连续端允许浮动 ±w（绕名义值）时，两类梁各需几种梁长。w = 0.005 相当于逐片按名义缝宽取整。"""
    chs = chords()
    out = []
    for w in SPEC_TOLERANCES:
        fams, _ = equalize(chs, C.CONT_HALF_NOM - w, C.CONT_HALF_NOM + w)
        cnt = {f: len(v[0]) for f, v in fams.items()}
        out.append(OrderedDict([("tolerance_m", "%.3f" % w),
                                ("joint_range_m", "%.2f–%.2f" % (2 * (C.CONT_HALF_NOM - w), 2 * (C.CONT_HALF_NOM + w))),
                                ("middle", cnt.get("中跨", 0)), ("end", cnt.get("边跨", 0)), ("total", sum(cnt.values()))]))
    return out


def naive_spec_count():
    return len(naive_lengths())


def alignment_rows():
    """逐桩坐标表：桥位范围每 10 m 一桩，另加线元起终点与支承线。"""
    pts = set()
    lo, hi = C.BRIDGE_START - 50.0, C.BRIDGE_START + C.N_SPANS * C.SPAN + 50.0
    s = lo
    while s <= hi + 1e-9:
        pts.add(round(s, 3))
        s += 10.0
    b = C.START_STATION
    for _, L, _, _ in C.H_SEGMENTS:
        if lo <= b <= hi:
            pts.add(round(b, 3))
        b += L
    for st in support_stations():
        pts.add(round(st, 3))
    out = []
    for st in sorted(pts):
        x, y, th, k = AL.at_station(st)
        z, g = AL.profile(st)
        az = (90.0 - math.degrees(th)) % 360.0        # 测量方位角：自北顺时针
        out.append(OrderedDict([
            ("station", AL.station_label(st)), ("x", "%.4f" % x), ("y", "%.4f" % y), ("azimuth_deg", "%.6f" % az),
            ("curvature", "%.8f" % k), ("design_z", "%.4f" % z), ("grade", "%.5f" % g),
            ("slope_left_L", "%.4f" % AL.slope_left(st, "L")), ("slope_left_R", "%.4f" % AL.slope_left(st, "R")),
            ("ground", "%.3f" % AL.ground(st)),
        ]))
    return out


def daily_rows(r):
    curve = dict(S.storage_curve(r["rows"]))
    erect = defaultdict(int)
    cast = defaultdict(int)
    for p in r["rows"]:
        erect[p["erect"]] += 1
        cast[p["cast"]] += 1
    d, end = min(curve), max(curve)
    out = []
    while d <= end:
        out.append(OrderedDict([("date", d.isoformat()), ("cast", cast.get(d, 0)), ("erected", erect.get(d, 0)),
                                ("in_storage", curve.get(d, 0))]))
        d += timedelta(days=1)
    return out


def conversion_rows(r):
    return [OrderedDict([("deck", m["deck"]), ("unit", m["unit"]), ("spans", "%d–%d" % m["spans"]),
                         ("piers", " ".join(m["piers"])), ("erected", m["erected"].isoformat()),
                         ("continuity_cast", m["cast"].isoformat()), ("conversion", m["conversion"].isoformat())])
            for m in S.conversions(r["rows"])]


BEDS_SWEEP = (8, 10, 12, 14, 16, 18)
LEAD_SWEEP = (14, 21, 28, 35, 42)


def sensitivity_rows():
    out = []
    for nb in BEDS_SWEEP:
        for lead in LEAD_SWEEP:
            rows, idle = S.plan(n_beds=nb, erect_start=C.YARD_START + timedelta(days=lead))
            m = S.summarize(rows, idle)
            fits = m["storage_peak"] <= Y.capacity() and m["storage_max_days"] <= C.MAX_STORAGE_DAYS
            out.append(OrderedDict([("beds", nb), ("lead_days", lead), ("erect_days", m["erect_days"]),
                                    ("wait_days", m["wait_days"]), ("storage_peak", m["storage_peak"]),
                                    ("storage_max_days", m["storage_max_days"]), ("feasible", "yes" if fits else "no")]))
    return out


def minimal_beds(rows):
    """零等梁且不超存梁容量、存梁期的方案里，台座最少的一个（同台座数取提前量最小）。"""
    ok = [r for r in rows if r["feasible"] == "yes" and r["wait_days"] == 0]
    return min(ok, key=lambda r: (r["beds"], r["lead_days"])) if ok else None


def all_checks(r):
    out = [{"group": "模型", "name": n, "pass": bool(ok), "detail": m} for n, ok, m in run_all(r["els"])]
    heaviest = max(e.volume for e in r["els"] if e.cls == "girder") * C.RC_DENSITY_T
    out += [{"group": "梁场与架梁", "name": n, "pass": bool(ok), "detail": m} for n, ok, m in Y.run_checks(r["summary"], heaviest)]
    return out
