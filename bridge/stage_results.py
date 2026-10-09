"""Geometry-neutral views of the existing, verified structural load cases.

These are whole-line load cases, not a solver for partially erected dates.
Mc and M2 are increments. Their reactions must not be labelled cumulative.
"""
from .pipeline import structure

STAGES = {
    "M1": {"name": "一期恒载 M1", "reaction": "R_G1", "meaning": "全联各跨简支一期恒载；临时支座有效"},
    "Mc": {"name": "体系转换增量 Mc", "reaction": "R_conv", "meaning": "拆除临时支座产生的弯矩和永久支座反力增量"},
    "M2": {"name": "二期恒载增量 M2", "reaction": "R_G2", "meaning": "连续体系上铺装及护栏荷载增量"},
    "MG": {"name": "恒载合计 MG", "reaction": "R_G", "meaning": "M1+Mc+M2；永久支座合计另含连续段自重 R_cs"},
}
NOTICE = "整联既有计算载荷工况；日期只控制施工可见性，不重新求解部分架设体系。Mc/M2 显示增量，非累计值。"


def build(result):
    computed = structure(result)
    lines = []
    for solved in computed["lines"]:
        model = solved["L"]
        points = []
        for x in model["xs"]:
            girder = min(model["girders"], key=lambda g: max(g["xa"] - x, 0, x - g["xb"]))
            geometry = result["by_id"][girder["eid"]].params
            fraction = (x - girder["xa"]) / (girder["xb"] - girder["xa"])
            points.append([geometry["p0"][j] + fraction * (geometry["p1"][j] - geometry["p0"][j]) for j in range(3)])
        supports = []
        for item in solved["bearings"] + solved["temps"]:
            source = result["by_id"][item["id"]]
            temporary = source.cls == "temp_support"
            vertices = [p for solid in source.params.get("solids", []) for p in solid["v"]]
            center = source.params.get("c") or [sum(p[j] for p in vertices) / len(vertices) for j in range(3)]
            supports.append({"eid": item["id"], "temporary": temporary, "kind": item.get("kind", "temp"),
                             "point": list(center), "x": item["x"],
                             "M1": item["R_G1"], "Mc": 0.0 if temporary else item["R_conv"],
                             "M2": 0.0 if temporary else item["R_G2"],
                             "MG": 0.0 if temporary else item["R_G"],
                             "removal_action": -item["R_G1"] if temporary else 0.0})
        lines.append({"id": "%s-U%d-G%d" % (model["deck"], model["unit"], model["line"]),
                      "deck": model["deck"], "unit": model["unit"], "girder_line": model["line"],
                      "xs": list(model["xs"]), "points": points,
                      "segments": [[g["ia"], g["ib"]] for g in model["girders"]],
                      "girders": [g["eid"] for g in model["girders"]],
                      "moments": {key: list(solved[key]) for key in STAGES}, "supports": supports,
                      "loads": solved["loads"], "reaction_sums": solved["reactions"]})
    return {"schema": 1, "source": "bridge.structure.analyse (unchanged M1/Mc/M2/MG)",
            "moment_unit": "kN.m", "reaction_unit": "kN", "notice": NOTICE, "stages": STAGES, "lines": lines}


def select(data, stage="M1", line_id="L-U2-G3"):
    if stage not in STAGES:
        raise ValueError("unknown structural stage: " + stage)
    line = next((item for item in data["lines"] if item["id"] == line_id), None)
    if line is None:
        raise ValueError("unknown girder line: " + line_id)
    support_rows = []
    counts = {True: 0, False: 0}
    for item in line["supports"]:
        counts[item["temporary"]] += 1
        tag = ("T" if item["temporary"] else "P") + "%02d" % counts[item["temporary"]]
        active = item["temporary"] if stage == "M1" and item["kind"] == "temp" else not item["temporary"]
        if stage == "M1" and item["kind"] == "cont":
            active = False
        support_rows.append(dict(item, reaction=item[stage], active=active, tag=tag))
    return {"stage": stage, "line": line_id, "label": STAGES[stage]["name"], "notice": NOTICE,
            "meaning": STAGES[stage]["meaning"], "moments": line["moments"][stage], "supports": support_rows,
            "points": line["points"], "segments": line["segments"] if stage == "M1" else [[0, len(line["xs"]) - 1]],
            "maximum": max(line["moments"][stage]), "minimum": min(line["moments"][stage])}
