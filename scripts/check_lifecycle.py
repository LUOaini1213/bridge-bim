"""Validate saved native lifecycle QA evidence without launching Rhino."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLAGS = ("ok", "old_document_closed", "timer_stopped", "sticky_removed",
         "old_save_query_tick_blocked", "old_accessor_rejected", "new_document_panel_available", "source_preserved")


def verify():
    report = json.loads((ROOT / "model/quality/native_lifecycle.json").read_text(encoding="utf-8"))
    for flag in FLAGS:
        if report.get(flag) is not True:
            raise AssertionError("Missing native lifecycle evidence: " + flag)
    for key in ("old_document_serial", "new_document_serial"):
        if type(report.get(key)) is not int or report[key] < 1:
            raise AssertionError("Invalid actual document runtime serial: " + key)
    if report["old_document_serial"] == report["new_document_serial"]:
        raise AssertionError("Lifecycle fixture did not replace the actual document")
    source_hash = hashlib.sha256((ROOT / "model/bridge_bim.3dm").read_bytes()).hexdigest()
    if report.get("source_sha256") != source_hash:
        raise AssertionError("Native lifecycle evidence is stale for the source model")
    return report


if __name__ == "__main__":
    verify()
    print("PASS actual document close, old actions blocked, fresh nonmodal panel, source preserved")
