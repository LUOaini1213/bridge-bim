"""Native Rhino result geometry for existing whole-line structural cases."""
import Rhino
import Rhino.Geometry as RG
import System.Drawing as SD
from bridge import stage_results as SR

TAG = "bridge_stage_result"


def clear(doc):
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.HiddenObjects = True
    for obj in list(doc.Objects.GetObjectList(settings)):
        if obj.Attributes.GetUserString(TAG) == "true":
            doc.Objects.Delete(obj.Id, True)
    doc.Strings.SetString("施工阶段结果", "")
    doc.Strings.SetString("施工阶段结果说明", "")


def draw(doc, data, stage, line_id):
    clear(doc)
    selected = SR.select(data, stage, line_id)
    layer_index = doc.Layers.FindByFullPath("施工阶段结果", -1)
    if layer_index < 0:
        layer = Rhino.DocObjects.Layer()
        layer.Name = "施工阶段结果"
        layer_index = doc.Layers.Add(layer)
    layer = doc.Layers[layer_index]
    layer.IsVisible = True
    layer.CommitChanges()
    created = []
    moment_scale = 12.0 / max(1.0, max(abs(value) for value in selected["moments"]))
    moment_offset = 25.0

    def attrs(kind, value=None, eid=None, color=(165, 40, 160)):
        a = Rhino.DocObjects.ObjectAttributes()
        a.LayerIndex = layer_index
        a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        a.ObjectColor = SD.Color.FromArgb(*color)
        a.SetUserString(TAG, "true")
        a.SetUserString("result_stage", stage)
        a.SetUserString("result_line", line_id)
        a.SetUserString("result_kind", kind)
        a.SetUserString("result_semantics", selected["meaning"])
        a.SetUserString("moment_scale", repr(moment_scale))
        a.SetUserString("moment_offset", repr(moment_offset))
        if value is not None:
            a.SetUserString("result_value", repr(value))
        if eid:
            a.SetUserString("result_element", eid)
        return a

    # Diagram is drawn above its true beam axis. Positive moment is drawn
    # below the datum. Scale affects only drawing, never the saved results.
    base = [RG.Point3d(p[0], p[1], p[2] + moment_offset) for p in selected["points"]]
    curve = [RG.Point3d(p.X, p.Y, p.Z - value * moment_scale) for p, value in zip(base, selected["moments"])]
    for ia, ib in selected["segments"]:
        created.append(doc.Objects.AddPolyline(base[ia:ib + 1], attrs("datum", color=(80, 80, 80))))
        created.append(doc.Objects.AddPolyline(curve[ia:ib + 1], attrs("moment_curve")))
    for index in {min(range(len(curve)), key=lambda i: selected["moments"][i]),
                  max(range(len(curve)), key=lambda i: selected["moments"][i])}:
        value = selected["moments"][index]
        created.append(doc.Objects.AddTextDot(RG.TextDot("M = %.2f kN.m" % value, curve[index]), attrs("moment_extreme", value)))
    for item in selected["supports"]:
        p = item["point"]
        origin = RG.Point3d(p[0], p[1], p[2] + 13)
        value, active = item["reaction"], item["active"]
        color = (35, 145, 90) if active else (170, 170, 170)
        created.append(doc.Objects.AddSphere(RG.Sphere(origin, 0.35), attrs("support_active" if active else "support_inactive", value, item["eid"], color)))
        if active:
            height = max(0.4, min(7.0, abs(value) * 0.004)) * (1 if value >= 0 else -1)
            end = RG.Point3d(origin.X, origin.Y, origin.Z + height)
            a = attrs("reaction", value, item["eid"], (35, 145, 90) if value >= 0 else (210, 65, 45))
            a.ObjectDecoration = Rhino.DocObjects.ObjectDecoration.EndArrowhead
            created.append(doc.Objects.AddLine(origin, end, a))
            label = RG.Point3d(end.X, end.Y, end.Z + (1.5 if item["temporary"] and item["eid"].endswith("a") else -1.5 if item["temporary"] else 0))
            created.append(doc.Objects.AddTextDot(RG.TextDot(item["tag"], label), attrs("reaction_label", value, item["eid"], color)))
        elif stage == "Mc" and item["temporary"]:
            # Removed support's former reaction is an applied action, not
            # a remaining support reaction. Keep the distinction in metadata.
            value = item["removal_action"]
            position = RG.Point3d(origin.X, origin.Y + (4 if item["eid"].endswith("a") else -4), origin.Z + (1 if item["eid"].endswith("a") else -3))
            created.append(doc.Objects.AddTextDot(RG.TextDot(item["tag"] + " 拆", position),
                                                  attrs("removal_action", value, item["eid"], (210, 110, 35))))
        else:
            created.append(doc.Objects.AddTextDot(RG.TextDot(item["tag"], origin), attrs("inactive_label", value, item["eid"], color)))
    heading = RG.Point3d(base[0].X, base[0].Y, base[0].Z + 14)
    created.append(doc.Objects.AddTextDot(RG.TextDot("%s / %s\n%s\n整联既有载荷工况；与日期状态分开\nM: kN.m  R: kN  绿:有效支承 灰:无效支承" %
                                                  (selected["label"], line_id, selected["meaning"]), heading), attrs("legend")))
    if any(value == System.Guid.Empty for value in created):
        raise RuntimeError("阶段结果几何创建失败")
    doc.Strings.SetString("施工阶段结果", stage + " / " + line_id)
    doc.Strings.SetString("施工阶段结果说明", SR.NOTICE)
    doc.Views.Redraw()
    return dict(selected, object_count=len(created), moment_scale=moment_scale, reaction_scale=0.004)


import System  # noqa: E402
