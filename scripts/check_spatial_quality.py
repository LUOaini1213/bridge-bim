"""Independently verify native girder quality report scope and source identities."""
import hashlib
import html
import json
import math
from pathlib import Path
import sys
import rhino3dm

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("contact", "below_clearance", "at_clearance", "threshold_candidate")
EPSILON_MM = 1e-7


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def bounds_gap(a, b):
    for bounds in (a, b):
        assert len(bounds) == 2 and all(len(point) == 3 for point in bounds)
        assert all(finite(value) for point in bounds for value in point)
        assert all(bounds[0][i] <= bounds[1][i] for i in range(3))
    return math.sqrt(sum(max(0.0, a[0][i] - b[1][i], b[0][i] - a[1][i]) ** 2 for i in range(3)))


def validate_clearance_event(event, clearance, epsilon=EPSILON_MM):
    assert event["kind"] in KINDS
    assert finite(event["required_clearance_mm"]) and event["required_clearance_mm"] == clearance
    assert len(event["point_mm"]) == 3 and all(finite(value) for value in event["point_mm"])
    assert type(event["mesh_witness_available"]) is bool
    search = event["mesh_contact_search"]
    assert finite(search["tolerance_mm"]) and search["tolerance_mm"] > 0 and type(search["hit"]) is bool
    if event["mesh_witness_available"]:
        assert finite(event["witness_radius_mm"]) and event["witness_radius_mm"] >= 0
    else:
        assert "witness_radius_mm" not in event
    if event["kind"] == "threshold_candidate":
        assert event.get("review_required") is True
        assert event.get("distance_status") == "mesh threshold candidate; exact model distance unverified"
        assert "actual_gap_mm" not in event and "distance_evidence" not in event
        mesh = event["clearance_mesh_proof"]
        assert len(mesh["proofs"]) == 2 and all(type(proof["ok"]) is bool for proof in mesh["proofs"])
        certified = all(proof["ok"] for proof in mesh["proofs"])
        assert mesh["boundary_error_budget_mm"] == 2e-6
        expected_search = max(clearance, search["tolerance_mm"]) + (2e-6 if certified else 0.0)
        assert math.isclose(mesh["search_distance_mm"], expected_search, rel_tol=0, abs_tol=1e-12)
        if not event["mesh_witness_available"]:
            assert not certified and event.get("point_source") == "unverified_bbox_candidate_midpoint"
        return
    evidence = event["distance_evidence"]
    assert evidence["method"] == "proved_axis_aligned_box_gap"
    assert len(evidence["axis_box_proof"]) == 2 and all(value is True for value in evidence["axis_box_proof"])
    assert evidence["comparison_epsilon_mm"] == epsilon == EPSILON_MM
    actual = event["actual_gap_mm"]
    assert finite(actual) and actual >= 0
    independent = bounds_gap(evidence["a_bounds_mm"], evidence["b_bounds_mm"])
    assert math.isclose(actual, independent, rel_tol=1e-12, abs_tol=1e-8), "proved-box distance differs from bounds"
    expected = ("contact" if actual <= epsilon else "at_clearance" if abs(actual - clearance) <= epsilon
                else "below_clearance" if actual < clearance else None)
    assert event["kind"] == expected, "boundary distance must not be labelled below clearance"
    if not event["mesh_witness_available"]:
        assert event.get("point_source") == "proved_box_closest_midpoint"


def validate_clearance_summary(q):
    assert q["clearance_comparison_epsilon_mm"] == EPSILON_MM
    assert finite(q["clearance_mm"]) and q["clearance_mm"] >= 0
    for event in q["clearance_events"]:
        validate_clearance_event(event, q["clearance_mm"])
    counts = {kind: sum(event["kind"] == kind for event in q["clearance_events"]) for kind in KINDS}
    assert set(q["clearance_counts"]) == set(KINDS)
    assert all(type(q["clearance_counts"][kind]) is int and q["clearance_counts"][kind] == value for kind, value in counts.items())
    blocking = sum(counts[kind] for kind in KINDS if kind != "at_clearance")
    assert type(q["blocking_clearance_events"]) is int and q["blocking_clearance_events"] == blocking
    assert type(q["clearance_ok"]) is bool and q["clearance_ok"] == (q["ok"] and blocking == 0)


def validate_boundary_checks(fixtures):
    assert fixtures["ok"] is True and len(fixtures["checks"]) == 6
    boundary = fixtures["boundary_checks"]
    assert boundary["ok"] is True and boundary["model_gap_mm"] == 5.0
    assert boundary["thresholds_mm"] == [6, 5, 4] and len(boundary["diagnostics"]) == 3
    for row, threshold, kind in zip(boundary["diagnostics"], (6, 5, 4), ("below_clearance", "at_clearance", None)):
        assert row["clearance_mm"] == threshold and row["expected_kind"] == kind
        assert not row["clashes"] and not row["unresolved"]
        assert row["aggregate_clearance_ok"] is (threshold <= 5)
        assert len(row["parts"]) == 2 and all(part["axis_box_proof"] is True for part in row["parts"])
        parts = [[[value for value in point] for point in (part["bbox_min_mm"], part["bbox_max_mm"])] for part in row["parts"]]
        assert bounds_gap(*parts) == row["bbox_gap_mm"] == 5.0
        if kind is None:
            assert not row["clearance_events"]
        else:
            assert len(row["clearance_events"]) == 1
            event = row["clearance_events"][0]
            validate_clearance_event(event, threshold)
            assert event["kind"] == kind and event["actual_gap_mm"] == 5.0
            assert event["distance_evidence"]["a_bounds_mm"] == parts[0] and event["distance_evidence"]["b_bounds_mm"] == parts[1]
    narrow = boundary["subthreshold_check"]
    assert narrow["clearance_mm"] == 10 and narrow["aggregate_clearance_ok"] is False
    assert not narrow["clashes"] and not narrow["unresolved"] and len(narrow["clearance_events"]) == 1
    event = narrow["clearance_events"][0]
    validate_clearance_event(event, 10)
    assert event["kind"] == "below_clearance" and abs(event["actual_gap_mm"] - 9.999) <= EPSILON_MM
    for name, threshold in (("nonbox_candidate_check", 3), ("uncertified_nohit_check", 1)):
        row = boundary[name]
        assert row["clearance_mm"] == threshold and row["aggregate_clearance_ok"] is False
        assert not row["clashes"] and not row["unresolved"] and row["clearance_events"]
        for event in row["clearance_events"]:
            validate_clearance_event(event, threshold)
            assert event["kind"] == "threshold_candidate"
        if name == "uncertified_nohit_check":
            assert len(row["clearance_events"]) == 1 and row["clearance_events"][0]["mesh_witness_available"] is False


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
    validate_boundary_checks(result["fixtures"])
    assert q["ok"] == (not q["clashes"] and not q["unresolved"])
    assert result["ok"] == q["ok"]
    validate_clearance_summary(q)
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
                     event.get("reason", event.get("kind", "volume overlap")),
                     str(event.get("actual_gap_mm", "unverified"))]
            rows.append("<tr>" + "".join("<td>%s</td>" % html.escape(cell) for cell in cells) + "</tr>")
    page = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>Bridge 3D quality</title>"
            "<style>body{font:15px system-ui;margin:30px}td,th{border:1px solid #ccc;padding:7px}"
            "table{border-collapse:collapse}</style><h1>预制梁三维质量报告</h1>"
            "<p>120 片实际预制梁；净距阈值 %.2f mm；体积碰撞 %d；接触 %d；不足 %d；达到阈值 %d；待复核候选 %d；未决 %d。</p>"
            "<p>范围为梁与梁之间；支座及设计现浇连接不在此报告内。认证轴对齐盒记录精确模型距离；"
            "达到阈值不计不足，接触和未核实候选仍单列。普通网格见证点不表示精确最短距离。</p>"
            "<table><tr><th>类别</th><th>梁 A</th><th>梁 B</th><th>位置 mm</th><th>状态</th><th>认证模型距离 mm</th></tr>%s</table></html>") % (
                q["clearance_mm"], len(q["clashes"]), *(q["clearance_counts"][kind] for kind in KINDS), len(q["unresolved"]), "".join(rows))
    (ROOT / "model/quality/native_spatial.html").write_text(page, encoding="utf-8")
    if not result["ok"]:
        sys.exit("FAIL native girder 3D report; see model/quality/native_spatial.html")
    print("PASS native girder 3D report: all 120 source IDs, units, six geometry fixtures and three exact-boundary checks")


if __name__ == "__main__":
    main()
