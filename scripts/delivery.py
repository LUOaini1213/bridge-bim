"""Check or rebuild the complete Rhino delivery with a portable integrity index.

Use the Python executable belonging to the environment where dependencies were
installed. Native rebuild requires licensed Rhino 8 on Windows. Offline check
never starts Rhino and rejects stale inputs, code or output files.

Revalidation covers commits that touch only documentation, tests, offline
checkers, CI or dependency-list comments: it reruns every offline check without
Rhino, requires every delivered artifact to keep its native-build SHA256, and
appends the new input fingerprints to the index. Any change to an input that
feeds the native build (NATIVE_INPUTS) still requires --rebuild.
"""
import argparse
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INDEX = "model/delivery_index.json"
HASH_POLICY = "SHA256; text CRLF normalized to LF"
# Inputs that feed the native Rhino build or a committed artifact. A change to any of them needs --rebuild.
# scripts/build_data.py and scripts/export_ifc.py are deliberately absent: Rhino never runs them, and the
# offline checks regenerate data/ and the IFC with them and compare against the committed files.
NATIVE_INPUTS = ("bridge/*", "facade/*", "build/*", "rhino/*", "construction/*", "quality/*", "web/*", "textures/*",
                 "model/*", "viewer.js", "index.html", "scripts/run_*.py", "scripts/check_ids.py", "scripts/check_source.py")
TEXT_SUFFIXES = {".py", ".md", ".json", ".csv", ".ifc", ".txt", ".yml", ".yaml", ".html", ".js", ".css", ".xml", ".ids", ".bat"}


def profile(root):
    if (root / "facade/config.py").is_file():
        return "facade"
    if (root / "bridge/config.py").is_file():
        return "bridge"
    if (root / "build/rhino_mesh.py").is_file():
        return "yard"
    raise ValueError("Cannot identify this repository's public Rhino workflow")


def digest(path):
    if path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitattributes":
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def collect(root, patterns):
    paths = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Delivery file resolves outside repository: " + str(path))
                paths.add(path)
    return {path.relative_to(root).as_posix(): digest(path) for path in sorted(paths)}


def inputs(root, project, config=None):
    patterns = [".gitattributes", "README.md", "docs/*.md", "scripts/*.py", "tests/*.py", "requirements*.txt", "quality/profile.json",
                "rhino/*.py", "rhino/*.json", ".github/workflows/*.yml", ".github/workflows/*.yaml"]
    if project in ("facade", "bridge"):
        patterns += [project + "/*.py", "scripts/*.py", "tests/*.py"]
    else:
        patterns += ["build/*.py", "web/*.js", "web/*.html", "viewer.js", "index.html",
                     "model/*.glb", "model/materials.json", "textures/**/*", "model/textures/**/*"]
    if project == "bridge":
        patterns.append(config or "construction/input.json")
    return collect(root, patterns)


def artifacts(root, project):
    if project == "facade":
        patterns = ["model/facade_bim.3dm", "model/facade_bim.ifc", "model/source_config.json",
                    "data/*.csv", "data/*.json", "model/quality/native_all.*", "model/quality/ids_report.*",
                    "model/quality/coping_joint_repair.json", "model/quality/roof_coping_repair.json",
                    "model/replay/facade_*.3dm", "model/replay/facade_*.json", "model/replay/facade_*.png",
                    "model/replay/run_log.json", "quality/delivery.ids"]
    elif project == "bridge":
        patterns = ["model/bridge_bim.3dm", "model/bridge_bim.ifc", "model/source_config.json", "data/*.csv", "data/*.json",
                    "model/quality/native_spatial.*", "model/quality/native_lifecycle.json",
                    "model/quality/ids_report.*", "model/replay/bridge_*.3dm", "model/replay/bridge_*.json",
                    "model/replay/bridge_*.png", "model/replay/qa/bridge_*.*", "model/project_replay/*.*", "quality/delivery.ids"]
    else:
        patterns = ["model/vmu_site_future*.3dm", "model/vmu_site_future*_inventory.csv",
                    "model/vmu_site_future_qa.json", "model/vmu_site_future_native_source_qa.json",
                    "model/vmu_site_future_rhino_delivery.json", "model/vmu_site_future_delivery_qa.json",
                    "model/vmu_site_future_geometry.*", "model/rhino_assets.json", "model/rhino_views.json",
                    "model/rhino_assets/*.png", "renders/rhino/*.png", "renders/rhino/contact_sheet.jpg"]
    return collect(root, patterns)


def compare(expected, actual, label):
    changed = sorted(key for key in set(expected) | set(actual) if expected.get(key) != actual.get(key))
    if changed:
        hint = ("; run --revalidate if only documentation, tests, offline checkers or CI changed, otherwise --rebuild"
                if label == "inputs/code" else "; run --rebuild")
        raise ValueError("Stale delivery " + label + ": " + ", ".join(changed[:12]) +
                         (" ..." if len(changed) > 12 else "") + hint)


def native_input(path, config=None):
    return path == config or any(fnmatchcase(path, pattern) for pattern in NATIVE_INPUTS)


def changed_keys(before, after):
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def effective_inputs(record):
    """Input fingerprints the index currently vouches for: the native build's, then each offline revalidation.

    Every revalidation may change only non-native inputs; an index edited to sneak a native change in is rejected."""
    current = record.get("inputs", {})
    for number, entry in enumerate(record.get("revalidations", []), 1):
        if entry.get("state") != "passed" or entry.get("native_rebuild") is not False:
            raise ValueError("Revalidation %d is not a passed offline revalidation; run --rebuild" % number)
        after = entry.get("inputs", {})
        native = [key for key in changed_keys(current, after) if native_input(key, record.get("construction_config"))]
        if native:
            raise ValueError("Revalidation %d changed native build inputs (%s); run --rebuild" % (number, ", ".join(native[:6])))
        current = after
    return current


def verify_index(root, project, config=None):
    path = root / INDEX
    if not path.is_file():
        raise ValueError("No delivery index; run --rebuild with licensed Rhino 8")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != 1 or record.get("project") != project or record.get("state") != "passed":
        raise ValueError("Delivery is incomplete or failed; run --rebuild")
    if record.get("hash_policy") != HASH_POLICY:
        raise ValueError("Unsupported delivery fingerprint policy; run --rebuild")
    if config is not None and config != record.get("construction_config"):
        raise ValueError("Requested construction profile differs from delivery index")
    saved_config = record.get("construction_config")
    if project == "bridge":
        resolve_config(root, saved_config)
    compare(effective_inputs(record), inputs(root, project, saved_config), "inputs/code")
    compare(record.get("artifacts", {}), artifacts(root, project), "artifacts")
    if not record.get("build_environment", {}).get("rhino_version"):
        raise ValueError("Missing native Rhino version in delivery index")
    return record


def resolve_config(root, value):
    path = Path(value or "construction/input.json")
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Construction profile must be an existing file inside this repository")
    return path.relative_to(root.resolve()).as_posix()


def commands(project, rebuild=False, config=None, regenerate_source=False):
    if project == "bridge":
        config = config or "construction/input.json"
    option = ["--construction-config", config] if project == "bridge" else []
    if rebuild:
        if project == "facade":
            steps = [["scripts/run_roof_repair.py", "--end-joints"], ["scripts/build_data.py"],
                     ["scripts/export_ifc.py"], ["scripts/check_ids.py", "--write-rules"],
                     ["scripts/run_quality.py"], ["scripts/run_replay.py"]]
        elif project == "bridge":
            steps = ([["scripts/run_rhino.py"] + option] if regenerate_source else [])
            steps += [["scripts/check_source.py"] + option,
                      ["scripts/build_data.py"] + option, ["scripts/export_ifc.py"] + option,
                      ["scripts/check_ids.py", "--write-rules"],
                      ["scripts/run_rhino.py", "--jobs", "rhino/verification_jobs.json",
                       "--verify-panel", "--spatial-quality"] + option]
        else:
            steps = [["build/prepare_rhino_assets.py"], ["build/capture_views.py"],
                     ["build/run_rhino_qa.py", "--enrich", "--timeout", "900"],
                     ["build/check_3dm.py", "--report", "model/vmu_site_future_qa.json"],
                     ["build/check_3dm.py", "--model", "model/vmu_site_future_native.3dm", "--inventory",
                      "model/vmu_site_future_native_inventory.csv", "--report", "model/vmu_site_future_native_source_qa.json"],
                     ["build/check_delivery.py"]]
        return steps
    if project == "facade":
        return [["scripts/build_data.py", "--check"], ["scripts/export_ifc.py", "--check"],
                ["-m", "pytest", "tests", "-q"], ["scripts/check_ids.py", "--no-write-reports"], ["scripts/check_ifc.py"],
                ["scripts/check_quality.py", "model/quality/native_all.json", "--no-write-reports"],
                ["scripts/check_replay.py"], ["scripts/check_readme.py"]]
    if project == "bridge":
        return [["scripts/check_source.py"] + option, ["scripts/build_data.py", "--check"] + option,
                ["scripts/export_ifc.py", "--check"] + option] + ([["-m", "pytest", "tests", "-q"]] if config == "construction/input.json" else []) + [
                ["scripts/check_ids.py", "--no-write-reports"], ["scripts/check_spatial_quality.py"],
                ["scripts/check_replay.py", "--all"], ["scripts/check_lifecycle.py"],
                ["scripts/check_project_gates.py"],
                ["scripts/check_construction_resources.py"] + option,
                ["scripts/check_readme.py"] + ([] if config == "construction/input.json" else ["--construction-config", config, "--profile-summary"])]
    return [["-m", "unittest", "discover", "-s", "build", "-p", "test_*.py"],
            ["build/prepare_rhino_assets.py", "--check"],
            ["build/check_3dm.py"],
            ["build/check_3dm.py", "--model", "model/vmu_site_future_native.3dm", "--inventory",
             "model/vmu_site_future_native_inventory.csv"],
            ["build/check_delivery.py", "--no-write-reports"]]


def preflight_native():
    if os.name != "nt":
        raise ValueError("Native rebuild requires licensed Rhino 8 on Windows; use --check offline")
    exe = Path(os.environ.get("RHINO_EXE", r"C:\Program Files\Rhino 8\System\Rhino.exe"))
    if not exe.is_file():
        raise ValueError("Rhino 8 executable is missing; set RHINO_EXE. Existing delivery is preserved")


def write_index(root, value):
    path = root / INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def environment(root, project):
    report = {"facade": "model/quality/native_all.json", "bridge": "model/replay/bridge_batch.json",
              "yard": "model/vmu_site_future_rhino_delivery.json"}[project]
    native = json.loads((root / report).read_text(encoding="utf-8"))
    version = native.get("rhino") or native.get("rhino_version")
    if not version:
        raise ValueError("Native workflow did not record the Rhino version")
    packages = {}
    for name in ("rhino3dm", "pytest", "ifcopenshell", "ifctester", "numpy", "Pillow", "playwright", "openseespy"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": packages, "rhino_version": version}


def offline_environment():
    packages = {}
    for name in ("rhino3dm", "pytest", "ifcopenshell", "ifctester", "numpy", "openseespy"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": packages}


def revalidate(root):
    """Offline-only refresh of the input fingerprints after documentation/test/checker/CI changes."""
    project = profile(root)
    path = root / INDEX
    if not path.is_file():
        raise ValueError("No delivery index; run --rebuild with licensed Rhino 8")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != 1 or record.get("project") != project or record.get("state") != "passed":
        raise ValueError("Delivery is incomplete or failed; run --rebuild")
    if record.get("hash_policy") != HASH_POLICY or not record.get("build_environment", {}).get("rhino_version"):
        raise ValueError("Unsupported or incomplete delivery index; run --rebuild")
    config = record.get("construction_config")
    if project == "bridge":
        resolve_config(root, config)
    compare(record.get("artifacts", {}), artifacts(root, project), "artifacts")
    previous = effective_inputs(record)
    before = inputs(root, project, config)
    changed = changed_keys(previous, before)
    if not changed:
        print("Nothing to revalidate; inputs match " + INDEX, flush=True)
        return record
    native = [key for key in changed if native_input(key, config)]
    if native:
        raise ValueError("Native build inputs changed (" + ", ".join(native[:6]) + "); run --rebuild with licensed Rhino 8")
    steps = commands(project, config=config)
    run(root, steps, config)
    compare(before, inputs(root, project, config), "inputs changed during revalidation")
    compare(record.get("artifacts", {}), artifacts(root, project), "artifacts")
    record.setdefault("revalidations", []).append({
        "state": "passed", "native_rebuild": False, "finished_at": datetime.now(timezone.utc).isoformat(),
        "changed": changed, "checks": [" ".join(step) for step in steps], "environment": offline_environment(),
        "inputs": before})
    write_index(root, record)
    verify_index(root, project, config)
    print("PASS offline revalidation of %d changed inputs; %s" % (len(changed), INDEX), flush=True)
    return record


def run(root, steps, config=None):
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("BRIDGE_CONSTRUCTION_CONFIG", None)
    if config:
        env["BRIDGE_CONSTRUCTION_CONFIG"] = str(root / config)
    for step in steps:
        print("RUN " + " ".join(step), flush=True)
        # Use this exact environment's interpreter; do not fall back to global Python.
        subprocess.run([sys.executable] + step, cwd=root, env=env, check=True)


def execute(root, rebuild=False, manifest_only=False, config=None, regenerate_source=False):
    project = profile(root)
    if config is not None and project != "bridge":
        raise ValueError("--construction-config applies only to bridge-bim")
    if regenerate_source and (project != "bridge" or not rebuild):
        raise ValueError("--regenerate-source requires bridge --rebuild")
    if rebuild:
        preflight_native()  # Fail before touching any existing output or index.
        config = resolve_config(root, config) if project == "bridge" else None
        if project == "bridge" and not regenerate_source:
            # A stale/altered source must fail before any formal output or the
            # previous successful index is overwritten. Geometry changes need
            # the explicit source regeneration option.
            run(root, [["scripts/check_source.py", "--construction-config", config]], config)
        before = inputs(root, project, config)
        record = {"schema": 1, "hash_policy": HASH_POLICY, "project": project, "state": "building", "construction_config": config,
                  "started_at": datetime.now(timezone.utc).isoformat()}
        write_index(root, record)
        try:
            run(root, commands(project, True, config, regenerate_source), config)
            run(root, commands(project, config=config), config)
            compare(before, inputs(root, project, config), "inputs changed during rebuild")
            record.update(state="passed", inputs=before, artifacts=artifacts(root, project),
                          build_environment=environment(root, project),
                          finished_at=datetime.now(timezone.utc).isoformat())
            write_index(root, record)
        except (Exception, KeyboardInterrupt) as error:
            record.update(state="failed", error=str(error))
            write_index(root, record)
            raise
    else:
        record = verify_index(root, project, config)
        if not manifest_only:
            run(root, commands(project, config=record.get("construction_config")), record.get("construction_config"))
            verify_index(root, project, config)
    print("PASS complete " + project + " delivery; " + INDEX, flush=True)
    return record


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Offline full check (default)")
    mode.add_argument("--rebuild", action="store_true", help="Refresh native delivery and all derived outputs")
    mode.add_argument("--revalidate", action="store_true",
                      help="After documentation/test/checker/CI-only changes: rerun every offline check, "
                           "keep artifacts, record new input fingerprints")
    parser.add_argument("--manifest-only", action="store_true", help="Check fingerprints after CI's independent checks")
    parser.add_argument("--construction-config", help="Bridge profile inside this repository")
    parser.add_argument("--regenerate-source", action="store_true", help="Explicitly rebuild bridge source geometry (e.g. changed beds)")
    args = parser.parse_args()
    if args.manifest_only and (args.rebuild or args.revalidate):
        parser.error("--manifest-only cannot accompany --rebuild or --revalidate")
    if args.revalidate and (args.construction_config or args.regenerate_source):
        parser.error("--revalidate uses the construction profile recorded in the index")
    try:
        if args.revalidate:
            revalidate(ROOT)
            return
        config = resolve_config(ROOT, args.construction_config) if args.construction_config else None
        execute(ROOT, args.rebuild, args.manifest_only, config, args.regenerate_source)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, "FAIL " + str(error) + "\n")


if __name__ == "__main__":
    main()
