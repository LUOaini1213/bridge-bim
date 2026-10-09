#! python3
"""Standalone native document-close QA; import main() after other batch jobs.

Uses a distinct temporary .3dm to force Rhino to replace the actual active
document. Lifecycle callbacks use the real schedule snapshot, without changing
the canonical model. Source is reopened before releasing its temporary copy.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import timedelta

ROOT = os.environ.get("BRIDGE_BIM_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import Rhino
import scriptcontext as sc
from bridge import construction as CP, replay as R
from bridge.pipeline import compute


def main():
    # Use a distinct module name when another repository's rhino package is
    # already loaded in the same Rhino CPython process.
    spec = importlib.util.spec_from_file_location("bridge_native_lifecycle_timeline", os.path.join(ROOT, "rhino", "timeline_panel.py"))
    timeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(timeline)
    source = os.path.join(ROOT, "model", "bridge_bim.3dm")
    with open(source, "rb") as handle:
        before_hash = hashlib.sha256(handle.read()).hexdigest()
    result = compute()
    with open(os.path.join(ROOT, "data", "stage_results.json"), encoding="utf-8") as handle:
        result["stage_data"] = json.load(handle)
    initial = result["construction"]["finish"] - timedelta(days=2)
    calls = {"apply": 0, "query": 0, "save": 0, "stage": 0}

    def apply(value):
        calls["apply"] += 1
        return R.snapshot(result["els"], result["rows"], value, result["construction"])

    def query(_eid, _state):
        calls["query"] += 1
        return "native lifecycle query"

    def save(_state):
        calls["save"] += 1
        raise AssertionError("Closed document must never reach an export callback")

    def stage(_stage, _line):
        calls["stage"] += 1

    active = Rhino.RhinoDoc.ActiveDoc
    if active:
        active.Modified = False
    assert Rhino.RhinoDoc.OpenFile(source), "Open canonical source for lifecycle fixture"
    doc = Rhino.RhinoDoc.ActiveDoc
    sc.doc = doc
    form = timeline.show(doc, result, initial, apply, stage, save, query)
    form.toggle(None, None)
    assert form.play.Text == "暂停"
    serial = doc.RuntimeSerialNumber
    report = {"ok": False, "source_sha256": before_hash, "old_document_serial": int(serial)}
    try:
        with tempfile.TemporaryDirectory(prefix="bridge_lifecycle_") as folder:
            copied = os.path.join(folder, "different_bridge_document.3dm")
            shutil.copyfile(source, copied)
            doc.Modified = False
            try:
                assert Rhino.RhinoDoc.OpenFile(copied), "Open different file must close old document"
                deadline = time.monotonic() + 2
                while not form.is_closed and time.monotonic() < deadline:
                    Rhino.RhinoApp.Wait()
                    time.sleep(0.02)
                active = Rhino.RhinoDoc.ActiveDoc
                assert active is not None and active.RuntimeSerialNumber != serial
                assert Rhino.RhinoDoc.FromRuntimeSerialNumber(serial) is None
                assert form.is_closed and form.play.Text == "播放" and timeline.KEY not in sc.sticky
                paused, before_calls = form.current, dict(calls)
                form.save(None, None)
                form.lookup(None, None)
                form.selected(None, None)
                form.tick(None, None)
                form.update(initial + timedelta(hours=1))
                form.toggle(None, None)
                assert form.current == paused and calls == before_calls, (calls, before_calls)
                try:
                    form.active_document()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Closed document accessor must reject old serial")
                sc.doc = active
                fresh = timeline.show(active, result, initial, apply, stage, save, query)
                try:
                    assert fresh is not form and fresh.active_document().RuntimeSerialNumber == active.RuntimeSerialNumber
                    assert fresh.Visible and not fresh.is_closed
                finally:
                    fresh.Close()
                report.update(ok=True, old_document_closed=True, timer_stopped=True,
                              sticky_removed=True, old_save_query_tick_blocked=True,
                              old_accessor_rejected=True, new_document_panel_available=True,
                              new_document_serial=int(active.RuntimeSerialNumber))
            finally:
                active = Rhino.RhinoDoc.ActiveDoc
                if active:
                    active.Modified = False
                assert Rhino.RhinoDoc.OpenFile(source), "Release temporary .3dm Windows handle"
                sc.doc = Rhino.RhinoDoc.ActiveDoc
    finally:
        if not form.is_closed:
            form.Close()
        with open(source, "rb") as handle:
            report["source_preserved"] = hashlib.sha256(handle.read()).hexdigest() == before_hash
        report["ok"] = report["ok"] and report["source_preserved"]
        output = os.path.join(ROOT, "model", "quality", "native_lifecycle.json")
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    assert report["ok"], report
    return report


if __name__ == "__main__":
    main()
