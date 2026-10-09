#! python3
"""原生读回实际预制 T 梁网格，检查梁间三维实体穿透与净距。"""
import hashlib
import json
import os
import sys
import time
import traceback

ROOT = os.environ.get("BRIDGE_BIM_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rhino"))
import Rhino
import Rhino.Geometry as RG
import spatial_quality as spatial


def main():
    path = os.path.join(ROOT, "model", "bridge_bim.3dm")
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if not Rhino.RhinoDoc.OpenFile(path):
        raise RuntimeError("打不开 bridge_bim.3dm")
    doc = Rhino.RhinoDoc.ActiveDoc
    scale = Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    transform = RG.Transform.Scale(RG.Point3d.Origin, scale)
    units, errors = [], []
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.NormalObjects = settings.HiddenObjects = settings.LockedObjects = True
    settings.IdefObjects = False
    for obj in doc.Objects.GetObjectList(settings):
        eid = obj.Attributes.GetUserString("eid")
        if not eid or obj.Attributes.GetUserString("class") != "girder":
            continue
        geo = obj.Geometry.Duplicate()
        geo.Transform(transform)
        try:
            if isinstance(geo, RG.Mesh):
                if not geo.IsValid or not geo.IsClosed:
                    raise ValueError("预制梁网格不是有效闭合体")
                # Every planar mesh face is preserved as a trimmed planar Brep face.
                brep = RG.Brep.CreateFromMesh(geo, True)
            elif isinstance(geo, RG.Brep):
                brep = geo
            else:
                raise ValueError("不支持的预制梁几何")
            if brep.SolidOrientation == RG.BrepSolidOrientation.Inward:
                brep.Flip()
            units.append(spatial.unit(eid, [spatial.part(brep, "actual Rhino girder mesh")], str(obj.Id), "girder"))
        except Exception as exc:
            errors.append({"eid": eid, "error": str(exc)})
    if errors:
        raise RuntimeError(json.dumps(errors, ensure_ascii=False))
    if len(units) != 120:
        raise ValueError("源模型应包含120片真实预制梁，实际 %d" % len(units))
    out = {"rhino": str(Rhino.RhinoApp.Version), "source_sha256": digest,
           "scope": "all 120 actual precast girders, between girders; supports and designed cast-in-place connections are outside this report",
           "source_unit": str(doc.ModelUnitSystem), "analysis_unit": "mm", "source_to_mm": scale,
           "fixtures": spatial.run_fixtures(), "spatial": spatial.analyse(units, float(os.environ.get("BRIDGE_CLEARANCE_MM", "25")), 0.01)}
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == digest
    out["source_preserved"] = True
    out["ok"] = out["fixtures"]["ok"] and out["spatial"]["ok"]
    doc.Modified = False
    return out


if __name__ == "__main__":
    started = time.monotonic()
    try:
        result = main()
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    result["seconds"] = round(time.monotonic() - started, 2)
    out = os.path.join(ROOT, "model", "quality")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "native_spatial.json"), "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    if Rhino.RhinoDoc.ActiveDoc:
        Rhino.RhinoDoc.ActiveDoc.Modified = False
