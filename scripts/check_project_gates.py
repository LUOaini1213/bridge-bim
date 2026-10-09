"""Read both native project fixture snapshots and require real gate evidence."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.check_replay import verify

FLAGS = ("missing_approval_held","before_approval_hidden","at_approval_installing",
         "after_work_erected","downstream_conversion_blocked","temporary_supports_retained")


def validate_report(report):
    if report.get("ok") is not True or any(report.get(k) is not True for k in FLAGS):
        raise AssertionError("incomplete/failed native project gate evidence")
    if "Synthetic QA" not in report.get("fixture_notice",""):
        raise AssertionError("project fixture must not pretend to be field approval")
    for key, stem in (("missing","bridge_2028-07-01_1000"),("approved","bridge_2027-07-01_1000")):
        if report.get(key) != {ext:stem+"."+ext for ext in ("3dm","png","json")}:
            raise AssertionError("project gate fixture inventory mismatch")


def main():
    folder=ROOT/"model/project_replay"
    report=json.loads((folder/"native_gates.json").read_text(encoding="utf-8"))
    validate_report(report)
    for key in ("missing","approved"):
        info=json.loads((folder/report[key]["json"]).read_text(encoding="utf-8"))
        if info.get("mode") != "project": raise AssertionError("gate fixture mode changed")
        counts=verify(folder/report[key]["3dm"])
        if counts["units_converted"] != 0 or counts["tasks_held"] <= 0:
            raise AssertionError("missing acceptance bypassed a downstream gate")
        if counts["girders_erected"] != (0 if key=="missing" else 1):
            raise AssertionError("wrong approved erection state")
        if key=="approved" and counts["temporary_supports_active"] <= 0:
            raise AssertionError("temporary supports removed before conversion release")
    print("PASS native project gates: missing/early approval held; first approved girder only; supports retained")


if __name__=="__main__": main()
