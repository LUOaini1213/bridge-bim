"""Independently read a Rhino replay .3dm and compare its real saved state.

    python scripts/check_replay.py model/replay/bridge_2026-12-21.3dm

Requires rhino3dm, but never launches Rhino. The untouched complete source
model supplies expected object identities, geometry and original BIM fields.
"""
import argparse
import json
import os
import struct
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rhino3dm                      # noqa: E402
from bridge import replay as R      # noqa: E402
from bridge.pipeline import compute  # noqa: E402
from bridge import stage_results as SR  # noqa: E402
from bridge.numcmp import compare_stage_json  # noqa: E402


def elements(model):
    result = {}
    for obj in model.Objects:
        eid = obj.Attributes.GetUserString("eid")
        if eid:
            if eid in result:
                raise AssertionError("duplicate BIM id: " + eid)
            result[eid] = obj
    return result


def verify_units(model, source):
    if source.Settings.ModelUnitSystem != rhino3dm.UnitSystem.Meters:
        raise AssertionError("complete source .3dm must use metres")
    if model.Settings.ModelUnitSystem != rhino3dm.UnitSystem.Meters:
        raise AssertionError("replay .3dm units changed; source coordinates are metres")


def verify(path):
    stem = os.path.splitext(os.path.abspath(path))[0]
    with open(stem + ".json", encoding="utf-8") as handle:
        info = json.load(handle)
    if not info.get("ok"):
        raise AssertionError("native replay failed: " + info.get("error", "unknown error"))
    model = rhino3dm.File3dm.Read(stem + ".3dm")
    source = rhino3dm.File3dm.Read(os.path.join(ROOT, "model", "bridge_bim.3dm"))
    if model is None or source is None:
        raise AssertionError("cannot read replay or complete source .3dm")
    verify_units(model, source)
    if "construction_config" not in info:
        raise AssertionError("replay must preserve its validated construction input")
    result = compute(configuration=info["construction_config"])
    expected = R.snapshot(result["els"], result["rows"], info["datetime"] if info["time_semantics"] == "exact_time" else info["date"], result["construction"])
    if info.get("configuration_sha256") != expected["configuration_sha256"] or info.get("mode") != expected["mode"]:
        raise AssertionError("saved input provenance/mode mismatch")
    actual, original = elements(model), elements(source)
    if set(actual) != set(expected["elements"]) or set(actual) != set(original):
        raise AssertionError("replay does not preserve the complete source BIM id set")
    for eid, state in expected["elements"].items():
        obj, before = actual[eid], original[eid]
        attrs = obj.Attributes
        if attrs.GetUserString("replay_date") != expected["date"]:
            raise AssertionError(eid + ": missing/wrong saved replay date")
        if attrs.GetUserString("replay_datetime") != expected["datetime"]:
            raise AssertionError(eid + ": wrong hour-level state")
        if attrs.GetUserString("replay_state") != state["phase"]:
            raise AssertionError(eid + ": wrong saved phase")
        if state["task"]:
            task = result["construction"]["by_id"][state["task"]]
            required = {"construction_task":task["id"], "construction_start":task["start"].isoformat(),
                        "construction_finish":task["finish"].isoformat(), "construction_mode":expected["mode"],
                        "construction_config_sha256":expected["configuration_sha256"], "construction_crew":task["crew_id"],
                        "construction_effective_crew":task["effective_crew_id"],
                        "construction_crew_strategy":task["crew_strategy"],
                        "construction_wait_reasons":json.dumps(task["wait_reasons"],ensure_ascii=False),
                        "construction_effective_wait_reasons":json.dumps(task["effective_wait_reasons"],ensure_ascii=False),
                        "construction_effective_start":task["effective_start"].isoformat() if task["effective_start"] else "HELD",
                        "construction_release_at":task["release_at"].isoformat() if task["release_at"] else "HELD",
                        "construction_hold_reasons":json.dumps(task["hold_reasons"],ensure_ascii=False),
                        "construction_release_gates":json.dumps(task["gates"],ensure_ascii=False,sort_keys=True)}
            for key, value in required.items():
                if attrs.GetUserString(key) != value:
                    raise AssertionError(eid + ": wrong saved configuration/gate field " + key)
        if (attrs.Mode != rhino3dm.ObjectMode.Hidden) != state["visible"]:
            raise AssertionError(eid + ": actual .3dm hidden mode disagrees with schedule")
        if state["visible"]:
            layer = model.Layers.FindIndex(attrs.LayerIndex)
            if not layer.Visible:
                raise AssertionError(eid + ": visible element is on a hidden layer")
        if attrs.Id != before.Attributes.Id:
            raise AssertionError(eid + ": original Rhino object id changed")
        for key, value in before.Attributes.GetUserStrings():
            if attrs.GetUserString(key) != value:
                raise AssertionError(eid + ": original BIM field changed: " + key)
        a, b = obj.Geometry.GetBoundingBox(), before.Geometry.GetBoundingBox()
        for point_a, point_b in ((a.Min, b.Min), (a.Max, b.Max)):
            if max(abs(getattr(point_a, axis) - getattr(point_b, axis)) for axis in ("X", "Y", "Z")) > 1e-7:
                raise AssertionError(eid + ": original geometry bounds changed")
    if info["counts"] != expected["counts"] or info["elements"] != expected["elements"] or info.get("tasks") != expected["tasks"] or info.get("effective_plan_finish") != expected["effective_plan_finish"]:
        raise AssertionError("JSON counters/states disagree with the current schedule")
    if dict(model.Strings).get("施工回放日期") != expected["date"]:
        raise AssertionError("document user text is missing the saved replay date")
    strings = dict(model.Strings)
    if strings.get("施工模式") != expected["mode"] or strings.get("施工配置") != json.dumps(expected["construction_config"],ensure_ascii=False,sort_keys=True) or strings.get("门禁待放行任务") != str(expected["counts"]["tasks_held"]):
        raise AssertionError("saved native document input/mode/gates mismatch")
    if info.get("stage_result"):
        data = SR.build(result)
        stage = info["stage_result"]
        selected = SR.select(data, stage["stage"], stage["line"])
        same, _, difference = compare_stage_json(json.dumps(stage), json.dumps(selected), selected=True)
        if not same:
            raise AssertionError("saved structural values differ from actual existing calculations")
        curves = [obj for obj in model.Objects if obj.Attributes.GetUserString("result_kind") == "moment_curve"]
        if len(curves) != len(selected["segments"]):
            raise AssertionError("wrong number of saved native moment curves")
        supports = {obj.Attributes.GetUserString("result_element"): obj for obj in model.Objects
                    if obj.Attributes.GetUserString("result_kind") in ("support_active", "support_inactive")}
        if set(supports) != {item["eid"] for item in selected["supports"]}:
            raise AssertionError("saved support set differs from actual structural line")
        for item in selected["supports"]:
            attrs = supports[item["eid"]].Attributes
            if abs(float(attrs.GetUserString("result_value")) - item["reaction"]) > 1e-9:
                raise AssertionError("wrong native saved stage reaction")
            if (attrs.GetUserString("result_kind") == "support_active") != item["active"]:
                raise AssertionError("wrong native stage support state")
        for obj, (ia, ib) in zip(curves, selected["segments"]):
            scale = 12.0 / max(1.0, max(abs(value) for value in selected["moments"]))
            if abs(float(obj.Attributes.GetUserString("moment_scale")) - scale) > 1e-12:
                raise AssertionError("wrong diagram display scale")
            points = list(obj.Geometry.TryGetPolyline())
            if len(points) != ib - ia + 1:
                raise AssertionError("native diagram node count differs")
            for index, point in enumerate(points, ia):
                expected_z = selected["points"][index][2] + 25 - selected["moments"][index] * scale
                if abs(point.Z - expected_z) > 1e-7:
                    raise AssertionError("native diagram ordinate differs from real moment")
    with open(stem + ".png", "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("native replay screenshot is not a PNG")
    width, height = struct.unpack(">II", header[16:24])
    if width != 1800 or height <= 160 or os.path.getsize(stem + ".png") < 10000:
        raise AssertionError("native replay screenshot is missing or incomplete")
    return expected["counts"]


def expected_stem(job):
    moment = R.parse_moment(job["date"])
    stem = "bridge_" + moment.date().isoformat()
    if len(job["date"]) > 10:
        stem += "_" + moment.strftime("%H%M")
    if job.get("stage"):
        stem += "_" + job["stage"] + "_" + job.get("line", "L-U2-G3")
    return stem


def batch_paths(folder, jobs, batch):
    """Require the complete configured eight-job evidence set, before readback."""
    folder = Path(folder)
    exports = batch.get("batch_exports", [])
    if batch.get("ok") is not True or len(jobs) != 8 or len(exports) != len(jobs):
        raise AssertionError("native batch must contain all eight configured formal snapshots")
    stems = [expected_stem(job) for job in jobs]
    if len(set(stems)) != len(stems):
        raise AssertionError("verification jobs have duplicate output names")
    paths = []
    for job, stem, entry in zip(jobs, stems, exports):
        names = {ext: stem + "." + ext for ext in ("3dm", "json", "png")}
        if entry.get("files") != names or entry.get("datetime") != R.parse_moment(job["date"]).isoformat():
            raise AssertionError("native batch job identity/time differs: " + stem)
        for name in names.values():
            if not (folder / name).is_file():
                raise AssertionError("missing expected formal snapshot artifact: " + name)
        info = json.loads((folder / names["json"]).read_text(encoding="utf-8"))
        if info.get("datetime") != entry["datetime"] or info.get("counts") != entry.get("counts") or info.get("files") != names:
            raise AssertionError("batch entry differs from actual saved snapshot JSON: " + stem)
        stage = info.get("stage_result")
        if job.get("stage"):
            if not stage or stage.get("stage") != job["stage"] or stage.get("line") != job.get("line", "L-U2-G3"):
                raise AssertionError("missing/wrong expected structural case: " + stem)
        elif stage:
            raise AssertionError("unexpected structural case in calendar-only snapshot: " + stem)
        paths.append(str(folder / names["3dm"]))
    panel = batch.get("panel_test", {})
    for flag in ("nonmodal", "timer_advanced", "pause_stable", "date_controls", "slider_controls",
                 "selection_query", "reentrant_consistent", "busy_save_blocked", "export_guard_consistent"):
        if panel.get(flag) is not True:
            raise AssertionError("missing actual native panel evidence: " + flag)
    qa_time = panel.get("timer_after", "")
    qa_stem = expected_stem({"date": qa_time})
    qa_names = {ext: qa_stem + "." + ext for ext in ("3dm", "json", "png")}
    if panel.get("export_qa_files") != qa_names:
        raise AssertionError("native export-reentry QA snapshot identity differs")
    for name in qa_names.values():
        if not (folder / "qa" / name).is_file():
            raise AssertionError("missing native export-reentry QA artifact: " + name)
    info = json.loads((folder / "qa" / qa_names["json"]).read_text(encoding="utf-8"))
    if info.get("datetime") != qa_time or info.get("files") != qa_names:
        raise AssertionError("native export-reentry snapshot differs from actual paused time")
    return paths + [str(folder / "qa" / qa_names["3dm"])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="saved native replay .3dm paths")
    parser.add_argument("--all", action="store_true", help="require and independently verify all eight configured jobs, panel QA and their batch inventory")
    args = parser.parse_args()
    paths = args.paths
    if args.all:
        folder = Path(ROOT) / "model/replay"
        jobs = json.loads((Path(ROOT) / "rhino/verification_jobs.json").read_text(encoding="utf-8"))
        batch = json.loads((folder / "bridge_batch.json").read_text(encoding="utf-8"))
        paths = batch_paths(folder, jobs, batch)
    if not paths:
        parser.error("supply saved paths or --all")
    for path in paths:
        counts = verify(path)
        print("PASS %s: %d/%d girders, %d/%d units converted, %d active temporary supports" % (
            os.path.basename(path), counts["girders_erected"], counts["girders_total"],
            counts["units_converted"], counts["units_total"], counts["temporary_supports_active"]))


if __name__ == "__main__":
    main()
