"""篡改演练：把仓库复制到临时目录，每次故意改坏一处产物或参数，跑相关检查，要求预期的每一道都变红。

测试「全过」不是证据——一道从来不会失败的检查什么也没验证。这里证明的是：已提交的
.3dm、IFC、CSV、README 与代码之间，任何一处被单独改动都会被抓到；产物之间互有冗余的地方
（表 ↔ IFC ↔ README、IFC ↔ 几何引擎）要求两到三道独立的检查同时变红。

    python scripts/tamper_drill.py          # 约 2 分钟；不改动工作区里的任何文件
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SKIP = {".git", ".venv", "__pycache__", ".pytest_cache"}


def copy_repo():
    dst = tempfile.mkdtemp(prefix="bridge_bim_drill_")
    for name in os.listdir(ROOT):
        if name in SKIP:
            continue
        src = os.path.join(ROOT, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst, name), ignore=shutil.ignore_patterns(*SKIP))
        else:
            shutil.copy2(src, dst)
    return dst


def edit(path, old, new):
    s = open(path, encoding="utf-8", newline="").read()
    if s.count(old) < 1:
        raise RuntimeError("篡改点没找到：%s 里的 %r" % (path, old[:60]))
    open(path, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))


def edit_3dm(path, eid, key=None, value=None, delete=False):
    import rhino3dm
    m = rhino3dm.File3dm.Read(path)
    o = next(o for o in m.Objects if o.Attributes.Name == eid)
    if delete:
        m.Objects.Delete(o.Attributes.Id)
    else:
        o.Attributes.SetUserString(key, value)
    if not m.Write(path, 8):
        raise RuntimeError("写回 .3dm 失败")


def first_girder_line_in_ifc(path, eid):
    """IFC 里 G-xxx 那根梁定位点坐标所在的一行（IFCCARTESIANPOINT），用来挪位置。"""
    s = open(path, encoding="utf-8").read()
    ent = re.search(r"#(\d+)=IFCBEAM\('[^']+',\$,'%s'.*?,#(\d+),#(\d+)," % re.escape(eid), s).group(2)
    plc = re.search(r"#%s=IFCLOCALPLACEMENT\(\$,#(\d+)\);" % ent, s).group(1)
    axis = re.search(r"#%s=IFCAXIS2PLACEMENT3D\(#(\d+)," % plc, s).group(1)
    return re.search(r"#%s=IFCCARTESIANPOINT\(\(([^)]*)\)\);" % axis, s).group(0)


CHECKS = {
    "data": [PY, "scripts/build_data.py", "--check"],
    "ifc": [PY, "scripts/export_ifc.py", "--check"],
    "readme": [PY, "scripts/check_readme.py"],
    "model": [PY, "-m", "unittest", "tests.test_model"],
    "ifc_data": [PY, "-m", "pytest", "-q", "tests/test_artifacts.py", "-k", "IfcData or Ifc4D"],
    "ifc_geom": [PY, "-m", "pytest", "-q", "tests/test_artifacts.py", "-k", "IfcGeometry"],
    "rhino": [PY, "-m", "pytest", "-q", "tests/test_artifacts.py", "-k", "Rhino3dm"],
}


def drills():
    def bearings(d):
        edit(os.path.join(d, "data", "bearings.csv"), "131.3037,0.1507", "131.3037,0.1607")

    def girders(d):
        edit(os.path.join(d, "data", "girders.csv"), "G-L06-3,L,6,3,2,中跨,C,C,7.000,28.78",
             "G-L06-3,L,6,3,2,中跨,C,C,7.000,28.79")

    def ifc_move(d):
        p = os.path.join(d, "model", "bridge_bim.ifc")
        line = first_girder_line_in_ifc(p, "G-R07-3")
        x = line.split("((")[1].split(",")[0]
        edit(p, line, line.replace("((" + x + ",", "((" + repr(float(x) + 0.05) + ",", 1))

    def ifc_date(d):
        p = os.path.join(d, "model", "bridge_bim.ifc")
        s = open(p, encoding="utf-8").read()
        m = re.search(r"IFCTASK\('[^']+',\$,'\\X2\\[0-9A-F]+\\X0\\ G-L03-2'.*?,#(\d+),\.INSTALLATION\.\)", s)
        tt = m.group(1)
        line = re.search(r"#%s=IFCTASKTIME\([^;]*;" % tt, s).group(0)
        edit(p, line, line.replace("2026-12-", "2026-11-", 1))

    def m3_attr(d):
        edit_3dm(os.path.join(d, "model", "bridge_bim.3dm"), "B-P05-R3", "seat_height", "0.2500")

    def m3_delete(d):
        edit_3dm(os.path.join(d, "model", "bridge_bim.3dm"), "TS-L10-2a", delete=True)

    def config(d):
        edit(os.path.join(d, "bridge", "config.py"), "CONT_HALF_MIN, CONT_HALF_MAX = 0.35, 0.50",
             "CONT_HALF_MIN, CONT_HALF_MAX = 0.35, 0.55")

    def readme(d):
        edit(os.path.join(d, "README.md"), "| 16 | 21 | 50 | 0 | 34 | 15 | 是 |", "| 16 | 21 | 50 | 0 | 33 | 15 | 是 |")

    return [
        ("垫石标高表改一个垫石高 1 cm", bearings, ["data", "ifc_data", "readme"]),
        ("梁长表改一片梁的长度 1 cm", girders, ["data", "ifc_data"]),
        ("IFC 里一片梁平移 5 cm", ifc_move, ["ifc", "ifc_geom"]),
        ("IFC 里一个架设任务提前一个月", ifc_date, ["ifc", "ifc_data"]),
        (".3dm 里一个支座的垫石高属性", m3_attr, ["rhino"]),
        (".3dm 里删掉一个临时支座", m3_delete, ["rhino"]),
        ("改参数（连续段允许更宽）却不重算产物", config, ["data", "model"]),
        ("README 改一个数", readme, ["readme"]),
    ]


def main():
    base = copy_repo()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        # 先确认副本本身是绿的
        for key, cmd in CHECKS.items():
            r = subprocess.run(cmd, cwd=base, capture_output=True, text=True, encoding="utf-8", env=env)
            if r.returncode != 0:
                print("副本本身没通过 %s，演练无意义：\n%s" % (key, (r.stdout + r.stderr)[-2000:]))
                sys.exit(1)
        results = []
        for name, fn, keys in drills():
            d = copy_repo()
            try:
                fn(d)
                red = []
                for key in keys:
                    r = subprocess.run(CHECKS[key], cwd=d, capture_output=True, text=True, encoding="utf-8", env=env)
                    if r.returncode != 0:
                        red.append(key)
                results.append((name, keys, red))
                print("%-28s 预期变红 %-28s 实际变红 %s" % (name, ",".join(keys), ",".join(red) or "（无）"))
            finally:
                shutil.rmtree(d, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    missed = [(n, sorted(set(k) - set(r))) for n, k, r in results if set(r) != set(k)]
    if missed:
        for n, m in missed:
            print("MISSED %s：%s 没有变红" % (n, ",".join(m)))
        sys.exit(1)
    print("PASS %d 处篡改，每处都被预期的全部检查抓到（共 %d 次变红）" % (len(results), sum(len(r) for _, _, r in results)))


if __name__ == "__main__":
    main()
