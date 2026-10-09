"""Independently verify native girder quality report scope and source identities."""
import hashlib
import html
import json
import math
from pathlib import Path
import sys
import rhino3dm

ROOT = Path(__file__).resolve().parents[1]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    source = ROOT / "model" / "bridge_bim.3dm"
    result = json.loads((ROOT / "model" / "quality" / "native_spatial.json").read_text(encoding="utf-8"))
    assert result["source_preserved"], result.get("error", "native run did not finish")
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["analysis_unit"] == "mm" and result["source_to_mm"] == 1000
    model = rhino3dm.File3dm.Read(str(source))
    girders = {o.Attributes.GetUserString("eid"): str(o.Attributes.Id) for o in model.Objects
               if o.Attributes.GetUserString("class") == "girder" and o.Attributes.GetUserString("eid")}
    q = result["spatial"]
    assert q["units"] == q["solid_parts"] == len(girders) == 120
    assert result["fixtures"]["ok"] and len(result["fixtures"]["checks"]) == 6
    assert q["ok"] == (not q["clashes"] and not q["unresolved"])
    assert result["ok"] == q["ok"]
    assert q["clearance_ok"] == (q["ok"] and not q["clearance_events"])
    for event in q["clashes"] + q["clearance_events"] + q["unresolved"]:
        assert event["a"] != event["b"]
        assert girders[event["a"]] == event["a_guid"] and girders[event["b"]] == event["b_guid"]
        assert event["a_part"] and event["b_part"]
    for event in q["clashes"] + q["clearance_events"]:
        assert len(event["point_mm"]) == 3 and all(math.isfinite(v) for v in event["point_mm"])
        if "intersection_volume_mm3" in event:
            assert event["intersection_volume_mm3"] > 0
    rows = []
    for label, events in (("VOLUME CLASH", q["clashes"]), ("CLEARANCE / CONTACT", q["clearance_events"]),
                          ("UNRESOLVED", q["unresolved"])):
        for event in events:
            cells = [label, event["a"], event["b"], str(event.get("point_mm", "unavailable")),
                     event.get("reason", event.get("kind", "volume overlap"))]
            rows.append("<tr>" + "".join("<td>%s</td>" % html.escape(cell) for cell in cells) + "</tr>")
    page = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>Bridge 3D quality</title>"
            "<style>body{font:15px system-ui;margin:30px}td,th{border:1px solid #ccc;padding:7px}"
            "table{border-collapse:collapse}</style><h1>预制梁三维质量报告</h1>"
            "<p>120 片实际预制梁；净距阈值 %.2f mm；体积碰撞 %d；接触/净距事件 %d；未决 %d。</p>"
            "<p>范围为梁与梁之间；支座及设计现浇连接不在此报告内。网格见证点不表示精确最短距离。</p>"
            "<table><tr><th>类别</th><th>梁 A</th><th>梁 B</th><th>位置 mm</th><th>状态</th></tr>%s</table></html>") % (
                q["clearance_mm"], len(q["clashes"]), len(q["clearance_events"]), len(q["unresolved"]), "".join(rows))
    (ROOT / "model/quality/native_spatial.html").write_text(page, encoding="utf-8")
    if not result["ok"]:
        sys.exit("FAIL native girder 3D report; see model/quality/native_spatial.html")
    print("PASS native girder 3D report: all 120 source IDs, metre-to-mm conversion, actual geometry fixtures")


if __name__ == "__main__":
    main()
