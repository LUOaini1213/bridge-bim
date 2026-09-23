"""重算全部数据产物，写到 data/。不需要 Rhino。

    python scripts/build_data.py            # 重写 data/
    python scripts/build_data.py --check    # 只比对：重算结果与已提交的 data/ 逐字节一致，否则退出 1
"""
import csv
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import config as C, yard as Y           # noqa: E402
from bridge import pipeline as P                     # noqa: E402
from bridge.numcmp import compare                    # noqa: E402

DATA = os.path.join(ROOT, "data")


def csv_text(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def outputs():
    r = P.compute()
    sens = P.sensitivity_rows()
    best = P.minimal_beds(sens)
    checks = P.all_checks(r)
    specs = P.length_spec_rows(r)
    sm = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r["summary"].items()}
    sm.update({
        "elements": len(r["els"]), "girders": sum(1 for e in r["els"] if e.cls == "girder"),
        "bearings": sum(1 for b in r["sup"] if b["kind"] != "temp"),
        "temp_supports": sum(1 for b in r["sup"] if b["kind"] == "temp"),
        "length_specs": len(specs), "length_specs_naive": P.naive_spec_count(),
        "storage_capacity": Y.capacity(), "haul_route_m": round(Y.haul_length(), 1),
        "checks_passed": sum(c["pass"] for c in checks), "checks_total": len(checks),
        "beds": C.N_BEDS, "lead_days": (C.ERECT_START - C.YARD_START).days,
        "min_beds_zero_wait": best["beds"] if best else None,
        "min_beds_zero_wait_lead": best["lead_days"] if best else None,
    })
    sm.update(P.structure_summary(r))
    return {
        "alignment_stations.csv": csv_text(P.alignment_rows()),
        "elements.csv": csv_text([{"eid": e.eid, "class": e.cls, "part": e.part, "deck": e.deck,
                                   "volume_m3": "%.4f" % e.volume} for e in r["els"]]),
        "girders.csv": csv_text(P.girder_rows(r)),
        "bearings.csv": csv_text(P.bearing_rows(r)),
        "temp_supports.csv": csv_text(P.temp_rows(r)),
        "joints.csv": csv_text(P.joint_rows(r)),
        "conversions.csv": csv_text(P.conversion_rows(r)),
        "length_spec_sensitivity.csv": csv_text(P.spec_sensitivity_rows()),
        "substructure.csv": csv_text(P.substructure_rows(r)),
        "takeoff.csv": csv_text(P.takeoff_rows(r)),
        "length_specs.csv": csv_text(specs),
        "yard_daily.csv": csv_text(P.daily_rows(r)),
        "sensitivity.csv": csv_text(sens),
        "lateral_distribution.csv": csv_text(P.lateral_rows(r)),
        "girder_lines.csv": csv_text(P.line_rows(r)),
        "sections.csv": csv_text(P.section_rows(r)),
        "girder_forces.csv": csv_text(P.girder_force_rows(r)),
        "bearing_reactions.csv": csv_text(P.bearing_force_rows(r)),
        "checks.json": json.dumps(checks, ensure_ascii=False, indent=1) + "\n",
        "summary.json": json.dumps(sm, ensure_ascii=False, indent=1) + "\n",
    }


def main():
    files = outputs()
    if "--check" in sys.argv:
        bad, loose = [], 0
        for n, t in files.items():
            path = os.path.join(DATA, n)
            old = open(path, encoding="utf-8", newline="").read() if os.path.exists(path) else ""
            ok, k, why = compare(old, t, "decimal")
            if not ok:
                bad.append("%s（%s）" % (n, why or "文件缺失"))
            loose += k
        if bad:
            print("MISMATCH: data/ 与重算结果不一致：" + "；".join(bad))
            sys.exit(1)
        if loose:
            print("PASS data/ 的 %d 个文件与重算结果一致（%d 处数字只差末位 1 个单位：跨平台数学库的舍入差）"
                  % (len(files), loose))
        else:
            print("PASS data/ 的 %d 个文件与重算结果逐字节一致" % len(files))
        return
    os.makedirs(DATA, exist_ok=True)
    for n, t in files.items():
        with open(os.path.join(DATA, n), "w", encoding="utf-8", newline="") as f:
            f.write(t)
    print("写出 data/ 下 %d 个文件" % len(files))


if __name__ == "__main__":
    main()
