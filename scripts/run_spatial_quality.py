"""Run native Rhino 3D girder clearance checks without changing the source model."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clearance-mm", type=float, default=25)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not math.isfinite(args.clearance_mm) or args.clearance_mm < 0:
        parser.error("clearance must be finite and nonnegative")
    rhino = Path(os.environ.get("RHINO_EXE", r"C:\Program Files\Rhino 8\System\Rhino.exe"))
    if not rhino.is_file():
        parser.error("Rhino 8 not found; set RHINO_EXE")
    source = ROOT / "model" / "bridge_bim.3dm"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    log = ROOT / "model" / "quality" / "native_spatial.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()
    with tempfile.TemporaryDirectory(prefix="bridge_spatial_") as temp:
        script = Path(temp) / "check_clearance.py"
        if not str(script).isascii() or " " in str(script):
            parser.error("TMP/TEMP must be an ASCII path without spaces")
        shutil.copyfile(ROOT / "rhino" / "check_clearance.py", script)
        env = dict(os.environ, BRIDGE_BIM_ROOT=str(ROOT), BRIDGE_CLEARANCE_MM=str(args.clearance_mm))
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
        cmd = '"%s" /nosplash /notemplate /runscript="_-RunPythonScript %s _-Exit"' % (rhino, script)
        proc = subprocess.Popen(cmd, env=env, startupinfo=startup)
        try:
            proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            sys.exit("Rhino spatial check timed out; only this process was terminated")
    if hashlib.sha256(source.read_bytes()).hexdigest() != before:
        sys.exit("source model changed unexpectedly")
    if not log.is_file():
        sys.exit("Rhino wrote no result; check its license and Python runtime")
    result = json.loads(log.read_text(encoding="utf-8"))
    if result.get("source_preserved"):
        from check_spatial_quality import main as verify
        verify()
    if not result.get("ok"):
        print("FAIL native Rhino spatial check: model/quality/native_spatial.json")
        if result.get("error"):
            print(result["error"])
        sys.exit(1)
    q = result["spatial"]
    print("PASS native Rhino: %d girders, %d volume clashes, %d clearance/contact events" % (
        q["units"], len(q["clashes"]), len(q["clearance_events"])))


if __name__ == "__main__":
    main()
