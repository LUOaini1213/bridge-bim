#! python3
"""Replay the existing bridge .3dm in Rhino 8; keep geometry and BIM IDs.

Run directly in Rhino for a nonmodal Eto timeline and load-case controls, or
use scripts/run_rhino.py --replay DATE/TIME [--stage M1/Mc/M2/MG].
"""
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import timedelta

ROOT = os.environ.get("BRIDGE_BIM_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import Rhino                                          # noqa: E402
import Rhino.Geometry as RG                           # noqa: E402
import System.Drawing as SD                           # noqa: E402
import System.Drawing.Imaging                         # noqa: E402,F401
import rhinoscriptsyntax as rs                        # noqa: E402
import scriptcontext as sc                            # noqa: E402

from bridge import alignment as AL, config as C, replay as R, construction as CP, construction_input as CI, stage_results as SR  # noqa: E402
from bridge.model import frame                        # noqa: E402
from bridge.pipeline import compute                   # noqa: E402
from rhino import stage_viewer, timeline_panel          # noqa: E402

PALETTE = {"erected": (70, 135, 205), "curing": (236, 165, 55),
           "continuous": (55, 155, 105), "active": (224, 116, 50),
           "static_reference": (160, 166, 170), "installing": (230, 120, 35),
           "constructing": (230, 120, 35), "completed": (55, 155, 105), "held": (210, 55, 70)}
OUT_DIR = os.environ.get("BRIDGE_REPLAY_OUT") or os.path.join(ROOT, "model", "replay")
LOG = {"ok": False, "steps": []}


def objects(doc):
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.HiddenObjects = True
    settings.LockedObjects = True
    settings.NormalObjects = True
    return list(doc.Objects.GetObjectList(settings))


def model_objects(doc):
    result = {}
    for obj in objects(doc):
        eid = obj.Attributes.GetUserString("eid")
        if eid:
            if eid in result:
                raise RuntimeError("模型含重复 BIM 编号：" + eid)
            result[eid] = obj.Id
    return result


def load_document(expected_ids):
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc and model_objects(doc):
        return doc
    # Opening a file can close the active document on Windows. Only do it
    # automatically in the empty document created by the batch launcher.
    if doc and objects(doc):
        raise RuntimeError("当前文档不是桥梁 BIM。请先保存，再打开 model/bridge_bim.3dm 后运行回放。")
    path = os.path.join(ROOT, "model", "bridge_bim.3dm")
    if not os.path.isfile(path):
        raise RuntimeError("缺少 model/bridge_bim.3dm，请先运行 scripts/run_rhino.py 建模。")
    opened = Rhino.RhinoDoc.Open(path)
    doc = opened[0] if isinstance(opened, tuple) else opened
    if doc is None or set(model_objects(doc)) != expected_ids:
        raise RuntimeError("无法打开完整桥梁 BIM 文档")
    return doc


def validate_document(doc, result):
    ids = model_objects(doc)
    expected = set(result["by_id"])
    if set(ids) != expected:
        raise RuntimeError("文档构件与当前配置不一致；请先重新生成完整模型。")
    # Canonical cast/erect UserText is the preserved source baseline. A custom
    # calendar changes construction_* attributes, never geometry or identity.
    for eid, element in result["by_id"].items():
        if doc.Objects.FindId(ids[eid]).Attributes.GetUserString("class") != element.cls:
            raise RuntimeError("源模型类别与当前几何模型不一致：" + eid)
    return ids


def prepare_layers(doc):
    terrain = next((lay for lay in doc.Layers if not lay.IsDeleted and lay.FullPath == "地形"), None)
    if terrain is not None:
        doc.Layers.SetCurrentLayerIndex(terrain.Index, True)
    for lay in doc.Layers:
        if lay.IsDeleted:
            continue
        visible = lay.FullPath.split("::")[0] in ("地形", "上部结构", "下部结构")
        lay.IsVisible = visible
        lay.SetPersistentVisibility(visible)
        lay.CommitChanges()


def apply_snapshot(doc, ids, result, day):
    state = R.snapshot(result["els"], result["rows"], day, result["construction"])
    for eid, item in state["elements"].items():
        object_id = ids[eid]
        obj = doc.Objects.FindId(object_id)
        attrs = obj.Attributes.Duplicate()
        attrs.SetUserString("replay_date", state["date"])
        attrs.SetUserString("replay_datetime", state["datetime"])
        attrs.SetUserString("replay_state", item["phase"])
        attrs.SetUserString("replay_scope", "scheduled" if item["scheduled"] else item["phase"])
        attrs.SetUserString("construction_task", item["task"] or "")
        if item["task"]:
            task = result["construction"]["by_id"][item["task"]]
            attrs.SetUserString("construction_start", task["start"].isoformat())
            attrs.SetUserString("construction_finish", task["finish"].isoformat())
            attrs.SetUserString("construction_example_assumption", str(task["example_assumption"]).lower())
            attrs.SetUserString("construction_mode", state["mode"])
            attrs.SetUserString("construction_config_sha256", state["configuration_sha256"])
            attrs.SetUserString("construction_crew", task["crew_id"])
            attrs.SetUserString("construction_effective_crew", task["effective_crew_id"])
            attrs.SetUserString("construction_crew_strategy", task["crew_strategy"])
            attrs.SetUserString("construction_wait_reasons", json.dumps(task["wait_reasons"], ensure_ascii=False))
            attrs.SetUserString("construction_effective_wait_reasons", json.dumps(task["effective_wait_reasons"], ensure_ascii=False))
            attrs.SetUserString("construction_effective_start", task["effective_start"].isoformat() if task["effective_start"] else "HELD")
            attrs.SetUserString("construction_release_at", task["release_at"].isoformat() if task["release_at"] else "HELD")
            attrs.SetUserString("construction_hold_reasons", json.dumps(task["hold_reasons"], ensure_ascii=False))
            attrs.SetUserString("construction_release_gates", json.dumps(task["gates"], ensure_ascii=False, sort_keys=True))
        if item["visible"]:
            attrs.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
            attrs.ObjectColor = SD.Color.FromArgb(*PALETTE[item["phase"]])
        if not doc.Objects.ModifyAttributes(object_id, attrs, True):
            raise RuntimeError("无法更新回放属性：" + eid)
        (doc.Objects.Show if item["visible"] else doc.Objects.Hide)(object_id, True)
    doc.Strings.SetString("施工回放日期", state["date"])
    doc.Strings.SetString("施工回放时刻", state["datetime"])
    doc.Strings.SetString("施工回放时间语义", state["time_semantics"])
    doc.Strings.SetString("施工回放范围", "完整示例计划：梁架设、横隔板、湿缝、翼缘、连续段、转换、铺装、护栏、伸缩装置")
    doc.Strings.SetString("施工回放限制", "\n".join(R.LIMITATIONS))
    doc.Strings.SetString("施工配置", json.dumps(state["construction_config"], ensure_ascii=False, sort_keys=True))
    doc.Strings.SetString("施工模式", state["mode"])
    doc.Strings.SetString("门禁待放行任务", str(state["counts"]["tasks_held"]))
    doc.Strings.SetString("源排程说明", "原 cast/erect 属性与梁场峰值几何为源基线；配置计划见 construction_* 和施工配置，非实测进度")
    doc.Views.Redraw()
    counts = state["counts"]
    Rhino.RhinoApp.WriteLine("%s：已架 %d/%d，转换 %d/%d 联，临时支座 %d；在制/存梁 %d/%d" % (
        state["date"], counts["girders_erected"], counts["girders_total"], counts["units_converted"],
        counts["units_total"], counts["temporary_supports_active"], counts["yard_in_production"], counts["yard_in_storage"]))
    return state


def point(station, lateral, z):
    (x, y), _, normal = frame(station)
    return RG.Point3d(x + lateral * normal[0], y + lateral * normal[1], z)


def prepare_view(doc):
    view = doc.Views.Find("Perspective", False) or doc.Views.ActiveView
    if view is None:
        raise RuntimeError("文档没有可用于截图的视口")
    doc.Views.ActiveView = view
    view.Maximized = True
    viewport = view.ActiveViewport
    modes = Rhino.Display.DisplayModeDescription.GetDisplayModes()
    viewport.DisplayMode = next(mode for mode in modes if mode.EnglishName == "Shaded")
    mid = C.BRIDGE_START + C.N_SPANS * C.SPAN / 2
    z = AL.profile(mid)[0]
    # Keep the whole bridge in a consistent, lower three-quarter view. The
    # former high angle hid the piers against a large field of ground mesh.
    viewport.ChangeToPerspectiveProjection(True, 30)
    viewport.SetCameraLocations(point(mid, 0, z - 8), point(mid + 80, -280, z + 90))
    LOG["display_mode"] = viewport.DisplayMode.EnglishName
    return view


def prepare_render_meshes(doc, ids, state):
    """The complete source is saved small, so create Brep render caches now."""
    count = 0
    for eid, item in state["elements"].items():
        if not item["visible"]:
            continue
        obj = doc.Objects.FindId(ids[eid])
        if isinstance(obj.Geometry, (RG.Brep, RG.Extrusion)):
            count += obj.CreateMeshes(RG.MeshType.Render, RG.MeshingParameters.Default, False)
    LOG["render_meshes_created"] = count
    doc.Views.Redraw()
    Rhino.RhinoApp.Wait()


def capture(view, path, state):
    view.Redraw()
    Rhino.RhinoApp.Wait()
    size = view.ActiveViewport.Size
    width = 1800
    capture_size = SD.Size(width, round(width * size.Height / max(size.Width, 1)))
    # GetDisplayMode returns a local description. Passing its attributes only
    # to capture avoids changing or persisting the user's built-in Shaded mode.
    mode = Rhino.Display.DisplayModeDescription.GetDisplayMode(view.ActiveViewport.DisplayMode.Id)
    attributes = mode.DisplayAttributes
    attributes.MeshSpecificAttributes.ShowMeshWires = False
    attributes.ShowSurfaceEdges = False
    attributes.ViewSpecificAttributes.DrawGrid = False
    attributes.ViewSpecificAttributes.DrawGridAxes = False
    attributes.ViewSpecificAttributes.DrawWorldAxes = False
    try:
        # Warm up the display pipeline after opening a save-small .3dm in a
        # hidden window. Publish only the subsequent, explicitly shaded frame.
        warmup = view.CaptureToBitmap(capture_size, attributes)
        if warmup is not None:
            warmup.Dispose()
        view.Redraw()
        Rhino.RhinoApp.Wait()
        bitmap = view.CaptureToBitmap(capture_size, attributes)
    finally:
        mode.Dispose()
    LOG["capture_mesh_wires"] = False
    if bitmap is None:
        raise RuntimeError("Rhino 视口截图失败")
    # Add a separate caption strip; do not cover the native viewport image.
    stage = state.get("stage_result")
    caption_height = 390 if stage else 160
    output = SD.Bitmap(width, bitmap.Height + caption_height)
    graphics = SD.Graphics.FromImage(output)
    title_font = SD.Font("Microsoft YaHei", 25)
    body_font = SD.Font("Microsoft YaHei", 15)
    table_font = SD.Font("Microsoft YaHei", 12)
    brush = SD.SolidBrush(SD.Color.FromArgb(30, 42, 55))
    try:
        graphics.Clear(SD.Color.White)
        graphics.DrawImageUnscaled(bitmap, 0, caption_height)
        title = "曲线 T 梁桥 · 施工时间轴  " + state["datetime"].replace("T", " ")
        if stage:
            title += "  / " + stage["stage"] + " " + stage["line"]
        graphics.DrawString(title, title_font, brush, 24.0, 12.0)
        counts = state["counts"]
        graphics.DrawString("已架 %d/%d 片（安装中 %d） |  完成体系转换 %d/%d 联  |  临时支座 %d 个  |  在制/存梁 %d/%d 片" % (
            counts["girders_erected"], counts["girders_total"], counts["girders_installing"], counts["units_converted"], counts["units_total"],
            counts["temporary_supports_active"], counts["yard_in_production"], counts["yard_in_storage"]),
            body_font, brush, 24.0, 60.0)
        graphics.DrawString("后续完成 %d/%d；橙：施工/养护  绿：已完成  灰：静态下部结构" % (
            counts["follow_on_completed"], counts["follow_on_total"]), body_font, brush, 24.0, 92.0)
        graphics.DrawString("%s / 门禁待放行 %d；非实测进度；" % (state["mode"], counts["tasks_held"]) + (stage["meaning"] if stage else "原整联力学结果不随日期重算。"), body_font, brush, 24.0, 122.0)
        if stage:
            graphics.DrawString("支承及有符号反力（kN） · P=永久，T=临时；三维短标号对应下表，箭头长度仅示意，精确值见表", table_font, brush, 24.0, 162.0)
            for index, item in enumerate(stage["supports"]):
                column, row = divmod(index, 6)
                x, y = 24.0 + column * 875, 214.0 + row * 25
                if row == 0:
                    for label, dx in (("标号", 0), ("构件编号", 65), ("工况支承", 285), ("反力 / 增量", 400), ("拆除施加作用", 610)):
                        graphics.DrawString(label, table_font, brush, x + dx, 188.0)
                removal = "%.3f" % item["removal_action"] if stage["stage"] == "Mc" and item["temporary"] else "—"
                values = ((item["tag"], 0), (item["eid"], 65), ("有效" if item["active"] else "无效", 285),
                          ("%.3f" % item["reaction"], 400), (removal, 610))
                for value, dx in values:
                    graphics.DrawString(value, table_font, brush, x + dx, y)
            graphics.DrawString("弯矩范围：%.3f .. %.3f kN.m；图形采用自适应比例，数值来自原计算。" % (stage["minimum"], stage["maximum"]), table_font, brush, 24.0, 367.0)
        output.Save(path, SD.Imaging.ImageFormat.Png)
    finally:
        brush.Dispose()
        title_font.Dispose()
        body_font.Dispose()
        table_font.Dispose()
        graphics.Dispose()
        output.Dispose()
        bitmap.Dispose()


def export_snapshot(doc, view, state, output_dir=None):
    output_dir = output_dir or OUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    basename = "bridge_" + state["date"]
    if state["time_semantics"] == "exact_time":
        basename += "_" + state["datetime"][11:16].replace(":", "")
    if state.get("stage_result"):
        basename += "_" + state["stage_result"]["stage"] + "_" + state["stage_result"]["line"]
    paths = {ext: os.path.join(output_dir, basename + "." + ext) for ext in ("3dm", "png", "json")}
    # As in build_model.py, write via Public so .3dm headers do not expose a
    # user profile path. The complete source model is never overwritten.
    public = os.path.join(os.environ.get("PUBLIC") or tempfile.gettempdir(), "Documents", "bridge-bim", "replay")
    os.makedirs(public, exist_ok=True)
    neutral = os.path.join(public, basename + ".3dm")
    options = Rhino.FileIO.FileWriteOptions()
    options.SuppressDialogBoxes = True
    options.IncludeRenderMeshes = False
    options.IncludeHistory = False
    options.IncludePreviewImage = True
    options.UpdateDocumentPath = False
    modified = doc.Modified
    try:
        capture(view, paths["png"], state)
        if not doc.WriteFile(neutral, options):
            raise RuntimeError("回放 .3dm 保存失败")
    finally:
        doc.Modified = modified
    if os.path.normcase(os.path.abspath(neutral)) != os.path.normcase(os.path.abspath(paths["3dm"])):
        shutil.copyfile(neutral, paths["3dm"])
    info = dict(state, ok=True, objects=len(objects(doc)), rhino=str(Rhino.RhinoApp.Version),
                files={ext: os.path.basename(path) for ext, path in paths.items()})
    with open(paths["json"], "w", encoding="utf-8", newline="\n") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    Rhino.RhinoApp.WriteLine("已导出回放 .3dm / PNG / JSON：" + basename)
    return info


def verify_project_gates(doc, ids, view, result):
    """Native evidence from deliberately synthetic project acceptance fixtures."""
    from copy import deepcopy
    config = deepcopy(result["construction_config"])
    config["mode"], config["releases"] = "project", []
    config["source"] = {"id":"native-project-gate-fixture", "reference":"native QA fixture, not project/site evidence", "author":"bridge-bim QA"}
    blocked = compute(configuration=config)
    missing = apply_snapshot(doc, ids, blocked, "2028-07-01T10:00")
    assert missing["counts"]["units_converted"] == 0 and missing["counts"]["girders_erected"] == 0
    assert missing["counts"]["tasks_held"] > 0
    obj = doc.Objects.FindId(ids["G-L01-1"])
    assert obj.Attributes.GetUserString("construction_release_at") == "HELD"
    assert "missing approval" in obj.Attributes.GetUserString("construction_hold_reasons")
    folder = os.path.join(ROOT, "model", "project_replay")
    prepare_render_meshes(doc, ids, missing)
    missing_info = export_snapshot(doc, view, missing, folder)
    config["releases"] = [{"task_id":"E001", "gate":gate, "approved_at":"2027-07-01T08:00",
                           "reference":"NATIVE-QA-FIXTURE-001 (not site acceptance)", "source":"native test fixture"} for gate in CI.GATES["girder"]]
    config["releases"].append({"task_id":"Y001","gate":"fabrication_acceptance","approved_at":"2027-07-01T08:00","reference":"NATIVE-YARD-QA-FIXTURE-001 (not site acceptance)","source":"native test fixture"})
    approved = compute(configuration=config)
    before = apply_snapshot(doc, ids, approved, "2027-07-01T07:59")
    assert before["counts"]["girders_erected"] == 0 and not before["elements"]["G-L01-1"]["visible"]
    during = apply_snapshot(doc, ids, approved, "2027-07-01T08:00")
    assert during["counts"]["girders_installing"] == 1
    assert not doc.Objects.FindId(ids["G-L01-1"]).IsHidden
    after = apply_snapshot(doc, ids, approved, "2027-07-01T10:00")
    assert after["counts"]["girders_erected"] == 1 and after["counts"]["units_converted"] == 0
    assert after["counts"]["temporary_supports_active"] > 0
    assert doc.Objects.FindId(ids["G-L01-1"]).Attributes.GetUserString("construction_release_at") == "2027-07-01T10:00:00"
    prepare_render_meshes(doc, ids, after)
    approved_info = export_snapshot(doc, view, after, folder)
    report = {"ok":True, "fixture_notice":"Synthetic QA approvals only; never field/project acceptance",
              "rhino":str(Rhino.RhinoApp.Version), "missing_approval_held":True,
              "before_approval_hidden":True, "at_approval_installing":True,
              "after_work_erected":True, "downstream_conversion_blocked":True,
              "temporary_supports_retained":True,
              "missing":missing_info["files"], "approved":approved_info["files"]}
    with open(os.path.join(folder,"native_gates.json"),"w",encoding="utf-8",newline="\n") as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2); handle.write("\n")
    return report


def main():
    started = time.time()
    result = compute()
    with open(os.path.join(ROOT, "data", "stage_results.json"), encoding="utf-8") as handle:
        result["stage_data"] = json.load(handle)
    doc = load_document(set(result["by_id"]))
    sc.doc = doc
    ids = validate_document(doc, result)
    prepare_layers(doc)
    view = prepare_view(doc)
    stage_viewer.clear(doc)
    current_stage = {"value": None, "line": "L-U2-G3"}

    def apply(value):
        state = apply_snapshot(doc, ids, result, value)
        if current_stage["value"]:
            state["stage_result"] = SR.select(result["stage_data"], current_stage["value"], current_stage["line"])
        return state

    def show_stage(stage, line):
        current_stage.update(value=stage, line=line)
        if stage:
            drawn = stage_viewer.draw(doc, result["stage_data"], stage, line)
            points = drawn["points"]
            target = RG.Point3d(*[sum(p[j] for p in points) / len(points) for j in range(3)])
            target.Z += 8
            view.ActiveViewport.SetCameraLocations(target, target + RG.Vector3d(65, -170, 85))
            LOG["stage_result_objects"] = drawn["object_count"]
        else:
            stage_viewer.clear(doc)
            prepare_view(doc)
        doc.Views.Redraw()

    def save(state, output_dir=None):
        if current_stage["value"]:
            state["stage_result"] = SR.select(result["stage_data"], current_stage["value"], current_stage["line"])
        else:
            state.pop("stage_result", None)
        prepare_render_meshes(doc, ids, state)
        return export_snapshot(doc, view, state, output_dir)

    def query(eid, state):
        if eid not in result["by_id"]:
            return "未找到构件编号：" + eid
        element = result["by_id"][eid]
        item = state["elements"][eid]
        lines = [eid + " / " + element.cls + " / " + item["phase"], "日历可见：" + str(item["visible"])]
        if item["task"]:
            task = result["construction"]["by_id"][item["task"]]
            lines.extend([task["name"], "开始 " + task["start"].isoformat(" "),
                          "施工结束 " + task["work_finish"].isoformat(" "), "养护/任务结束 " + task["finish"].isoformat(" "),
                          "演示假设：" + str(task["example_assumption"])])
            lines.extend(["模式 " + state["mode"] + " / 计划班组 " + task["crew_id"] + " / 有效班组 " + task["effective_crew_id"],
                          "班组策略 " + task["crew_strategy"],
                          "可行预测开始 " + (str(task["effective_start"]) if task["effective_start"] else "HELD"),
                          "可行预测放行 " + (str(task["release_at"]) if task["release_at"] else "HELD"),
                          "排程等待原因 " + "; ".join(task["effective_wait_reasons"]),
                          "待放行原因 " + "; ".join(task["hold_reasons"]),
                          "放行依据 " + json.dumps(task["gates"], ensure_ascii=False, sort_keys=True),
                          "输入来源 " + json.dumps(task["source"], ensure_ascii=False, sort_keys=True)])
        for solved in result["stage_data"]["lines"]:
            support = next((s for s in solved["supports"] if s["eid"] == eid), None)
            if support:
                lines.append("既有工况反力 kN：" + ", ".join("%s=%.3f" % (key, support[key]) for key in SR.STAGES))
            if eid in solved["girders"]:
                index = solved["girders"].index(eid)
                ia, ib = solved["segments"][index]
                lines.append("既有整联工况 " + solved["id"] + " / 该梁弯矩范围 kN.m")
                lines.extend("%s: %.3f .. %.3f" % (key, min(solved["moments"][key][ia:ib + 1]), max(solved["moments"][key][ia:ib + 1])) for key in SR.STAGES)
        return "\n".join(lines)
    supplied = os.environ.get("BRIDGE_REPLAY_DATE")
    if supplied:
        stage = os.environ.get("BRIDGE_REPLAY_STAGE")
        if stage:
            show_stage(stage, os.environ.get("BRIDGE_REPLAY_LINE", "L-U2-G3"))
        state = apply(supplied)
        if os.environ.get("BRIDGE_PANEL_TEST"):
            panel = timeline_panel.show(doc, result, supplied, apply, show_stage, save, query)
            try:
                # Test the actual Eto UI timer with the native message pump;
                # do not substitute a direct call to its event handler.
                initial = min(max(R.parse_moment(supplied), result["construction"]["by_id"]["E120"]["start"]),
                              result["construction"]["finish"] - timedelta(days=1))
                panel.update(initial)
                panel.toggle(None, None)
                deadline = time.time() + 4.0
                while panel.current == initial and time.time() < deadline:
                    Rhino.RhinoApp.Wait()
                    time.sleep(0.04)
                panel.toggle(None, None)
                advanced = panel.current > initial
                paused = panel.current
                deadline = time.time() + 0.8
                while time.time() < deadline:
                    Rhino.RhinoApp.Wait()
                    time.sleep(0.04)
                pause_stable = panel.current == paused
                panel.date.Text = initial.date().isoformat()
                panel.hour.Value, panel.minute.Value = initial.hour, initial.minute
                panel.enter(None, None)
                date_controls = panel.current == initial
                panel.slider.Value = 1
                slider_controls = panel.current == panel.start + timedelta(hours=1)
                panel.update(paused)
                original_apply, original_export = panel.apply_callback, panel.export_callback
                export_calls, export_consistent, export_info = [0], [False], [{}]
                def reentrant_export(export_state):
                    export_calls[0] += 1
                    expected_moment = export_state["datetime"]
                    panel.slider.Value = 3
                    panel.save(None, None)
                    export_info[0] = original_export(export_state, os.path.join(OUT_DIR, "qa"))
                    export_consistent[0] = (export_info[0]["datetime"] == expected_moment
                                            and doc.Strings.GetValue("施工回放时刻") == expected_moment)
                try:
                    panel.export_callback = reentrant_export
                    panel.save(None, None)
                    export_guard_consistent = (export_calls[0] == 1 and export_consistent[0]
                                               and panel.current == panel.start + timedelta(hours=3)
                                               and R.parse_moment(panel.state["datetime"]) == panel.current
                                               and panel.slider.Value == 3 and not panel.updating)
                finally:
                    panel.export_callback = original_export
                panel.update(paused)
                original_apply, original_export = panel.apply_callback, panel.export_callback
                injected, save_calls = [False], [0]
                def mixed_save(_state):
                    save_calls[0] += 1
                    raise RuntimeError("不应在apply期间保存")
                def reentrant_apply(value):
                    result_state = original_apply(value)
                    if not injected[0]:
                        injected[0] = True
                        panel.slider.Value = 2
                        panel.save(None, None)
                        panel.tick(None, None)  # busy timer must be skipped
                    return result_state
                try:
                    panel.apply_callback, panel.export_callback = reentrant_apply, mixed_save
                    panel.update(initial)
                    reentrant_consistent = (panel.current == panel.start + timedelta(hours=2)
                                            and R.parse_moment(panel.state["datetime"]) == panel.current
                                            and panel.slider.Value == 2 and not panel.updating)
                    busy_save_blocked = save_calls[0] == 0 and panel.busy_rejections > 0
                finally:
                    panel.apply_callback, panel.export_callback = original_apply, original_export
                panel.update(paused)
                panel.stage.SelectedIndex = 2
                panel.line.SelectedIndex = 8
                panel.lookup(None, None)
                doc.Objects.Select(ids["G-L05-3"])
                panel.selected(None, None)
                selection_query = "G-L05-3" in panel.details.Text
                doc.Objects.UnselectAll()
                LOG["panel_test"] = {"nonmodal": panel.Visible, "timer_advanced": advanced, "pause_stable": pause_stable,
                                     "date_controls": date_controls, "slider_controls": slider_controls, "selection_query": selection_query,
                                     "reentrant_consistent": reentrant_consistent, "busy_save_blocked": busy_save_blocked,
                                     "export_guard_consistent": export_guard_consistent, "export_qa_files": export_info[0].get("files", {}),
                                     "timer_initial": initial.isoformat(), "timer_after": paused.isoformat(),
                                     "stage": current_stage["value"], "line": current_stage["line"], "query": panel.details.Text}
                if not all((panel.Visible, advanced, pause_stable, date_controls, slider_controls, selection_query,
                            reentrant_consistent, busy_save_blocked, export_guard_consistent, current_stage["value"] == "Mc")):
                    raise RuntimeError("原生 Eto 面板、UI timer、阶段切换或构件查询验证失败")
            finally:
                panel.Close()
            current_stage.update(value=stage, line=os.environ.get("BRIDGE_REPLAY_LINE", "L-U2-G3"))
            show_stage(stage, current_stage["line"])
            state = apply(supplied)
        exports = []
        values = json.loads(os.environ["BRIDGE_REPLAY_DATES"]) if os.environ.get("BRIDGE_REPLAY_DATES") else [supplied]
        jobs = (json.loads(os.environ["BRIDGE_REPLAY_JOBS"]) if os.environ.get("BRIDGE_REPLAY_JOBS") else
                [{"date": value, "stage": stage, "line": current_stage["line"]} for value in values])
        for job in jobs:
            show_stage(job.get("stage"), job.get("line", "L-U2-G3"))
            state = apply(job["date"])
            info = save(state)
            LOG.update(info)
            exports.append({"datetime": info["datetime"], "counts": info["counts"], "files": info["files"]})
        if len(exports) > 1:
            LOG["batch_exports"] = exports
        if os.environ.get("BRIDGE_PANEL_TEST"):
            show_stage(None, "L-U2-G3")
            LOG["project_gates"] = verify_project_gates(doc, ids, view, result)
            # Restore the final formal simulation/project state after fixture QA.
            show_stage(jobs[-1].get("stage"),jobs[-1].get("line","L-U2-G3"))
            apply(jobs[-1]["date"])
        if os.environ.get("BRIDGE_SPATIAL_QA"):
            doc.Modified = False
            from rhino import check_clearance
            quality_started = time.time()
            quality = check_clearance.main()
            quality["seconds"] = round(time.time() - quality_started, 2)
            folder = os.path.join(ROOT, "model", "quality")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "native_spatial.json"), "w", encoding="utf-8", newline="\n") as handle:
                json.dump(quality, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            LOG["spatial_quality"] = {"ok": quality["ok"], "seconds": quality["seconds"], "report": "model/quality/native_spatial.json"}
            if not quality["ok"]:
                raise RuntimeError("原生三维质量检查未通过，请查看 model/quality/native_spatial.json")
        if os.environ.get("BRIDGE_PANEL_TEST"):
            from rhino import verify_lifecycle
            LOG["document_lifecycle"] = verify_lifecycle.main()
        LOG["seconds"] = round(time.time() - started, 1)
        if Rhino.RhinoDoc.ActiveDoc:
            Rhino.RhinoDoc.ActiveDoc.Modified = False
        return
    day = doc.Strings.GetValue("施工回放时刻") or result["construction_config"]["baseline"]["erect_start"]
    for message in R.LIMITATIONS:
        Rhino.RhinoApp.WriteLine(message)
    timeline_panel.show(doc, result, day, apply, show_stage, save, query)


try:
    main()
except Exception:
    LOG["ok"] = False
    LOG["error"] = traceback.format_exc()
    Rhino.RhinoApp.WriteLine(LOG["error"])
finally:
    if os.environ.get("BRIDGE_REPLAY_DATE") and Rhino.RhinoDoc.ActiveDoc:
        Rhino.RhinoDoc.ActiveDoc.Modified = False
    log_path = os.environ.get("BRIDGE_REPLAY_LOG")
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(LOG, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
