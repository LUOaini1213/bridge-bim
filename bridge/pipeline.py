"""把路线、构件、检查、梁场与架梁计划算成一组交付表。Rhino 脚本、IFC 导出、测试共用。"""
import math
from collections import OrderedDict, defaultdict
from datetime import timedelta

from . import alignment as AL, config as C, schedule as S, structure as ST, yard as Y
from .checks import continuity_joints, run_all
from .model import build, chords, equalize, naive_lengths, support_kind, support_name, support_stations

CLASS_NAMES = OrderedDict([
    ("girder", "预制 T 梁"), ("diaphragm", "横隔板"), ("wet_joint", "湿接缝"), ("cantilever", "翼缘现浇段"), ("continuity", "墩顶现浇连续段"),
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


def structure(r):
    """上部结构计算（全桥约 7 s），算一次缓存在 r 里；支座位移按本次的梁场与架梁计划取龄期。"""
    if "struct" not in r:
        s = ST.analyse(r["els"])
        s["bearing_design"] = ST.bearing_design(s, r["els"], {"ages": ST.bearing_ages(r["rows"])})
        r["struct"] = s
    return r["struct"]


def lateral_rows(r):
    """各梁位的截面特性与荷载横向分布系数（两幅相同）。"""
    s = structure(r)
    gm = s["info"]["gm"]
    out = []
    for k, sec in s["secs"].items():
        c = s["coef"][k]
        out.append(OrderedDict([
            ("pos", k), ("offset_m", "%.2f" % sec["a"]), ("A_precast_m2", "%.4f" % sec["A0"]),
            ("I_precast_m4", "%.5f" % sec["I0"]), ("A_composite_m2", "%.4f" % sec["A"]), ("I_composite_m4", "%.5f" % sec["I"]),
            ("IT_m4", "%.5f" % sec["IT"]), ("I_joint_m4", "%.5f" % sec["Ij"]),
            ("beta_simple", "%.4f" % s["beta_simple"]), ("Cw", "%.3f" % s["cw"]), ("beta", "%.4f" % s["beta"]),
            ("mc_max", "%.4f" % c["mc_max"]), ("mc_max_lanes", c["mc_max_lanes"]),
            ("mc_min", "%.4f" % c["mc_min"]), ("mc_min_lanes", c["mc_min_lanes"]),
            ("m0_max", "%.4f" % c["m0_max"]), ("m0_max_lanes", c["m0_max_lanes"]), ("m0_min", "%.4f" % c["m0_min"]),
            ("mc_no_torsion", "%.4f" % c["rigid_max"]), ("mc_eccentric", "%.4f" % c["ecc_max"]),
            ("mc_gm", "%.4f" % c["gm_max"]), ("mc_from", c["mc_max_method"]), ("theta_gm", "%.4f" % gm["theta"]),
            ("alpha_gm", "%.5f" % gm["alpha"]),
        ]))
    return out


def bearing_design_rows(r):
    """永久支座的位移与验算：到不动点的距离、开始受力时的龄期、各项应变与位移（mm）、各条验算的利用率。"""
    bd = structure(r)["bearing_design"]
    out = []
    for x in sorted(bd["rows"], key=lambda x: x["id"]):
        sp = x["spec"]
        plain = not x["sliding"]
        out.append(OrderedDict([
            ("bearing", x["id"]), ("kind", "连续墩" if x["kind"] == "cont" else "伸缩端"),
            ("type", "四氟滑板" if x["sliding"] else "普通板式"), ("support", support_name(x["support"])),
            ("deck", x["deck"]), ("unit", x["unit"]), ("line", x["line"]), ("te_mm", "%.0f" % (sp["te"] * 1000)),
            ("S", "%.2f" % sp["S"]), ("to_fixed_point_m", "%.3f" % x["r"]), ("age_d", x["t0"]),
            ("strain_temp_1e6", "%.1f" % (x["eps_t"] * 1e6)), ("strain_shrink_1e6", "%.1f" % (x["eps_s"] * 1e6)),
            ("strain_creep_1e6", "%.1f" % (x["eps_c"] * 1e6)), ("contract_mm", "%.1f" % (x["d_contract"] * 1000)),
            ("expand_mm", "%.1f" % (x["d_expand"] * 1000)), ("brake_mm", "%.1f" % (x["d_brake"] * 1000)),
            ("shear_allow_mm", "%.1f" % ((sp["te_eff"] / 2) * 1000) if plain else ""),
            ("util_shear", "%.3f" % x["util_shear"] if plain else ""), ("theta_rad", "%.5f" % x["theta"]),
            ("compression_mm", "%.3f" % (x["dcm"] * 1000)), ("util_rotation", "%.3f" % x["util_rot"]),
            ("util_compression", "%.3f" % x["util_comp"]), ("plate_need_mm", "%.2f" % (x["ts_need"] * 1000)),
            ("util_slip", "%.3f" % x["util_slip"] if plain else ""),
            ("util_friction", "" if plain else "%.3f" % x["util_friction"]),
            ("slide_travel_m", "" if plain else "%.3f" % x["travel"]),
            ("util_travel", "" if plain else "%.3f" % x["util_travel"]),
        ]))
    return out


BEARING_VARIANTS = [
    ("本桥：温热地区、RH 80%、σpc 取反推值、收缩徐变取终极值", {}),
    ("寒冷地区（Tmin −10 ℃，Ge 1.2 MPa）", {"climate": "寒冷"}),
    ("严寒地区（Tmin −23 ℃，Ge 1.5 MPa）", {"climate": "严寒"}),
    ("RH 40%–70%（按 55%）", {"rh": 0.55}),
    ("不计徐变（σpc = 0）", {"sigma_pc": 0.0}),
    ("收缩徐变只算到 10 年", {"t_ultimate": 3650}),
    ("安装温度放宽到 5–25 ℃", {"t_set": (5.0, 25.0)}),
]


def bearing_sensitivity_rows(r):
    """支座位移对假设输入的敏感性：每次只改一项，看伸缩端与连续墩的最大缩短量、普通板式支座要多厚。"""
    s = structure(r)
    ages = s["bearing_design"]["inputs"]["ages"]
    out = []
    for label, p in BEARING_VARIANTS:
        bd = ST.bearing_design(s, r["els"], dict(p, ages=ages))
        e = max((x for x in bd["rows"] if x["kind"] == "end"), key=lambda x: x["d_contract"])
        c = max((x for x in bd["rows"] if x["kind"] == "cont"), key=lambda x: x["util_shear"])
        need_e = ST.plain_thickness_needed(e["spec"], max(e["d_contract"], e["d_expand"]), 0.0)
        need_c = ST.plain_thickness_needed(c["spec"], max(c["d_contract"], c["d_expand"]), c["d_brake"])
        out.append(OrderedDict([
            ("variant", label), ("end_contract_mm", "%.1f" % (e["d_contract"] * 1000)),
            ("end_plain_needed_mm", "%d" % round(need_e * 1000) if need_e else "不够"),
            ("cont_contract_mm", "%.1f" % (c["d_contract"] * 1000)), ("cont_util", "%.2f" % c["util_shear"]),
            ("cont_needed_mm", "%d" % round(need_c * 1000) if need_c else "不够"),
        ]))
    return out


def line_rows(r):
    """30 条梁位线：频率、冲击系数、荷载合计、内力与挠度的极值。"""
    out = []
    for x in structure(r)["lines"]:
        L = x["L"]
        out.append(OrderedDict([
            ("deck", L["deck"]), ("unit", L["unit"]), ("line", L["line"]), ("spans", "%d–%d" % L["spans_k"]),
            ("length_m", "%.3f" % L["xs"][-1]), ("elements", len(L["xs"]) - 1), ("L0_max_m", "%.3f" % x["L0"]),
            ("Pk_kN", "%.2f" % x["Pk"]), ("Cw_end", "%.3f" % min(x["cw"][0], x["cw"][-1])),
            ("Cw_mid", "%.3f" % max(x["cw"][1:-1] or x["cw"])), ("f1_Hz", "%.4f" % x["f1"]),
            ("f_neg_Hz", "%.4f" % x["f_neg"]), ("neg_mode", x["neg_mode"]),
            ("f1_code_Hz", "%.4f" % ST.code_frequency_estimates(x)["f1"]),
            ("f2_code_Hz", "%.4f" % ST.code_frequency_estimates(x)["f2"]),
            ("f_simple_Hz", "%.4f" % ST.code_frequency_estimates(x)["f_simple"]),
            ("mu_pos", "%.4f" % x["mu_pos"]), ("mu_neg", "%.4f" % x["mu_neg"]),
            ("G1_kN", "%.1f" % x["loads"]["G1"]), ("G2_kN", "%.1f" % x["loads"]["G2"]),
            ("continuity_kN", "%.1f" % x["loads"]["CS"]), ("Mud_pos_max_kNm", "%.1f" % max(x["Mud_p"])),
            ("Mud_neg_min_kNm", "%.1f" % min(x["Mud_n"])), ("Vud_max_kN", "%.1f" % max(x["Vud"])),
            ("deflection_ratio_max", "%.3f" % max(s["w"] / s["limit"] for s in x["spans"])),
        ]))
    return out


def section_rows(r):
    """控制截面：每跨正弯矩最大处与每个连续墩墩顶，按施工阶段拆开的恒载弯矩、汽车荷载与组合（kN·m）。"""
    out = []
    for x in structure(r)["lines"]:
        L = x["L"]
        items = [(L["xs"][s["node_m"]], "跨内正弯矩最大", "第 %d 跨" % s["span"], s["node_m"], True) for s in x["spans"]]
        items += [(b["x"], "墩顶", support_name(b["support"]), b["node"], False) for b in L["perm"] if b["kind"] == "cont"]
        for xv, kind, where, j, pos in sorted(items):
            mq, mu, mud, mfd = ((x["MQp"][j], x["mu_pos"], x["Mud_p"][j], x["Mfd_p"][j]) if pos else
                                (x["MQn"][j], x["mu_neg"], x["Mud_n"][j], x["Mfd_n"][j]))
            out.append(OrderedDict([
                ("deck", L["deck"]), ("unit", L["unit"]), ("line", L["line"]), ("section", kind), ("at", where),
                ("x_m", "%.3f" % xv), ("M_G1", "%.1f" % x["M1"][j]), ("M_conversion", "%.1f" % x["Mc"][j]),
                ("M_G2", "%.1f" % x["M2"][j]), ("M_G", "%.1f" % x["MG"][j]), ("M_Q", "%.1f" % mq), ("mu", "%.4f" % mu),
                ("M_ud", "%.1f" % mud), ("M_fd", "%.1f" % mfd),
            ]))
    return out


def girder_force_rows(r):
    """每片预制梁在它自己的长度范围内的内力设计值与所在跨的活载挠度。"""
    out = []
    for x in structure(r)["lines"]:
        L = x["L"]
        for m, g in enumerate(L["girders"]):
            ia, ib = g["ia"], g["ib"]
            rng = range(ia, ib + 1)
            jp = max(rng, key=lambda j: x["Mud_p"][j])
            jn = min(rng, key=lambda j: x["Mud_n"][j])
            v = max([x["VudR"][j] for j in range(ia, ib)] + [x["VudL"][j] for j in range(ia + 1, ib + 1)])
            s = x["spans"][m]
            out.append(OrderedDict([
                ("girder", g["eid"]), ("deck", L["deck"]), ("span", g["span"]), ("pos", L["line"]),
                ("g1_kN_m", "%.3f" % g["w1"]), ("diaphragm_kN", "%.2f" % sum(q["P"] for q in g["dia"])),
                ("M_G1_max", "%.1f" % max(x["M1"][j] for j in rng)), ("M_G_max", "%.1f" % max(x["MG"][j] for j in rng)),
                ("M_ud_pos", "%.1f" % x["Mud_p"][jp]), ("x_pos_m", "%.3f" % (L["xs"][jp] - g["xa"])),
                ("M_ud_neg", "%.1f" % x["Mud_n"][jn]), ("x_neg_m", "%.3f" % (L["xs"][jn] - g["xa"])),
                ("V_ud_max", "%.1f" % v), ("deflection_mm", "%.2f" % (s["w"] * 1000)),
                ("deflection_limit_mm", "%.2f" % (s["limit"] * 1000)), ("deflection_ratio", "%.3f" % (s["w"] / s["limit"])),
            ]))
    return sorted(out, key=lambda o: o["girder"])


def bearing_force_rows(r):
    """永久支座反力与压应力验算，临时支座（体系转换前）的反力。单位 kN、m²、MPa。"""
    size = {e.eid: e.attrs["size"] for e in r["els"] if e.cls == "bearing"}
    out = []
    for x in structure(r)["lines"]:
        L = x["L"]
        for b in x["bearings"]:
            out.append(OrderedDict([
                ("bearing", b["id"]), ("kind", "连续墩" if b["kind"] == "cont" else "伸缩端"),
                ("support", support_name(b["support"])), ("deck", L["deck"]), ("line", L["line"]), ("size", size[b["id"]]),
                ("R_G1", "%.1f" % b["R_G1"]), ("R_conversion", "%.1f" % b["R_conv"]), ("R_G2", "%.1f" % b["R_G2"]),
                ("R_continuity", "%.1f" % b["R_cs"]), ("R_G", "%.1f" % b["R_G"]), ("R_Q_max", "%.1f" % b["R_Qmax"]),
                ("R_Q_min", "%.1f" % b["R_Qmin"]), ("mu", "%.4f" % x["mu_pos"]), ("Rck", "%.1f" % b["Rck"]),
                ("R_ud_min", "%.1f" % b["R_ud_min"]), ("Ae_m2", "%.4f" % b["Ae"]), ("sigma_MPa", "%.2f" % b["sigma"]),
                ("utilisation", "%.3f" % (b["sigma"] / C.SIGMA_C)), ("size_required_m", "%.3f" % b["size_req"]),
            ]))
        for t in x["temps"]:
            out.append(OrderedDict([
                ("bearing", t["id"]), ("kind", "临时支座"), ("support", support_name(t["support"])), ("deck", L["deck"]),
                ("line", L["line"]), ("size", ""), ("R_G1", "%.1f" % t["R_G1"]),
            ] + [(k, "") for k in ("R_conversion", "R_G2", "R_continuity", "R_G", "R_Q_max", "R_Q_min", "mu", "Rck",
                                   "R_ud_min", "Ae_m2", "sigma_MPa", "utilisation", "size_required_m")]))
    return sorted(out, key=lambda o: o["bearing"])


def structure_summary(r):
    s = structure(r)
    ls = s["lines"]
    bs = [b for x in ls for b in x["bearings"]]
    return OrderedDict([
        ("structure_lines", len(ls)), ("f1_min", round(min(x["f1"] for x in ls), 3)),
        ("f1_max", round(max(x["f1"] for x in ls), 3)), ("mu_pos_min", round(min(x["mu_pos"] for x in ls), 4)),
        ("mu_pos_max", round(max(x["mu_pos"] for x in ls), 4)), ("mu_neg_min", round(min(x["mu_neg"] for x in ls), 4)),
        ("mu_neg_max", round(max(x["mu_neg"] for x in ls), 4)), ("cw", round(s["cw"], 3)), ("beta", round(s["beta"], 4)), ("Mud_pos_max", round(max(max(x["Mud_p"]) for x in ls), 1)),
        ("Mud_neg_min", round(min(min(x["Mud_n"]) for x in ls), 1)), ("Vud_max", round(max(max(x["Vud"]) for x in ls), 1)),
        ("deflection_ratio_max", round(max(sp["w"] / sp["limit"] for x in ls for sp in x["spans"]), 3)),
        ("bearing_util_end_max", round(max(b["sigma"] for b in bs if b["kind"] == "end") / C.SIGMA_C, 3)),
        ("bearing_util_cont_max", round(max(b["sigma"] for b in bs if b["kind"] == "cont") / C.SIGMA_C, 3)),
        ("bearing_R_ud_min", round(min(b["R_ud_min"] for b in bs), 1)),
    ] + code_frequency_summary(ls) + bearing_summary(s))


def code_frequency_summary(ls):
    """条文说明估算式与有限元频率的对照。"""
    est = [(ST.code_frequency_estimates(x), x) for x in ls]
    return [
        ("f1_code_min", round(min(e["f1"] for e, _ in est), 3)), ("f1_code_max", round(max(e["f1"] for e, _ in est), 3)),
        ("f2_code_min", round(min(e["f2"] for e, _ in est), 3)), ("f2_code_max", round(max(e["f2"] for e, _ in est), 3)),
        ("f1_code_over_fe_min", round(min(e["f1"] / x["f1"] for e, x in est), 3)),
        ("f1_code_over_fe_max", round(max(e["f1"] / x["f1"] for e, x in est), 3)),
        ("f2_code_over_fe_min", round(min(e["f2"] / x["f_neg"] for e, x in est), 3)),
        ("f2_code_over_fe_max", round(max(e["f2"] / x["f_neg"] for e, x in est), 3)),
        ("mu_pos_code_min", round(min(ST.impact(e["f1"]) for e, _ in est), 4)),
        ("mu_pos_code_max", round(max(ST.impact(e["f1"]) for e, _ in est), 4)),
        ("mu_neg_code_min", round(min(ST.impact(e["f2"]) for e, _ in est), 4)),
        ("mu_neg_code_max", round(max(ST.impact(e["f2"]) for e, _ in est), 4)),
        ("f1_fe_over_simple_max_dev", round(max(abs(x["f1"] / e["f_simple"] - 1) for e, x in est), 4)),
    ]


def bearing_summary(s):
    """横向分布的 G-M 包络与支座位移验算的要点。"""
    gm, cs, bd = s["info"]["gm"], s["coef"], s["bearing_design"]
    rows, units, inp = bd["rows"], bd["units"], bd["inputs"]
    ends = [x for x in rows if x["kind"] == "end"]
    conts = [x for x in rows if x["kind"] == "cont"]
    pre = inp["prestress"]
    top = max(pre, key=lambda q: q["sigma_pc"])
    mid = (len(cs) + 1) // 2
    u0 = next(iter(units.values()))
    return [
        ("gm_theta", round(gm["theta"], 4)), ("gm_alpha", round(gm["alpha"], 5)), ("gm_envelope", bool(s["info"]["envelope"])),
        ("gm_h1", round(gm["h1"], 4)), ("gm_a", round(gm["a"], 3)),
        ("mc_mid_eccentric", round(cs[mid]["ecc_max"], 4)), ("mc_mid_gm", round(cs[mid]["gm_max"], 4)),
        ("notional_size_mm", round(inp["h_mm"], 1)), ("sigma_pc_max", round(top["sigma_pc"], 2)),
        ("sigma_pc_min", round(min(q["sigma_pc"] for q in pre), 2)), ("prestress_Np_max", round(top["Np"], 0)),
        ("age_end_min", min(x["t0"] for x in ends)), ("age_cont_min", min(x["t0"] for x in conts)),
        ("brake_lane_kN", round(u0["F_lane"], 1)), ("brake_kN", round(u0["F"], 1)),
        ("brake_shift_mm", round(max(u["d_brake"] for u in units.values()) * 1000, 1)),
        ("end_contract_max_mm", round(max(x["d_contract"] for x in ends) * 1000, 1)),
        ("end_expand_max_mm", round(max(x["d_expand"] for x in ends) * 1000, 1)),
        ("end_to_fixed_max_m", round(max(x["r"] for x in ends), 2)),
        ("cont_contract_max_mm", round(max(x["d_contract"] for x in conts) * 1000, 1)),
        ("cont_shear_util_max", round(max(x["util_shear"] for x in conts), 3)),
        ("bearing_rotation_util_max", round(max(x["util_rot"] for x in rows), 3)),
        ("bearing_slip_util_max", round(max(x["util_slip"] for x in conts), 3)),
        ("slide_friction_util_max", round(max(x["util_friction"] for x in ends), 3) if ends and ends[0]["sliding"] else None),
        ("slide_travel_max_m", round(max(x["travel"] for x in ends), 3) if ends and ends[0]["sliding"] else None),
        ("joint_close_util_max", round(max(g["close"] / g["gap"] for g in bd["gaps"]), 3)),
    ]


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
    for row in girder_force_rows(r):
        extra[row["girder"]].update((k, v) for k, v in row.items() if k not in extra[row["girder"]])
    for row in bearing_force_rows(r):
        tgt = extra[row["bearing"]]
        tgt.update((k, v) for k, v in row.items() if k not in tgt and v != "")
    for row in bearing_design_rows(r):
        tgt = extra[row["bearing"]]
        tgt.update((k, v) for k, v in row.items() if k not in tgt and v != "")
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
    out += [{"group": "上部结构", "name": n, "pass": bool(ok), "detail": m}
            for n, ok, m in ST.run_checks(structure(r), r["els"])]
    return out
