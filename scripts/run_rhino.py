"""调起本机 Rhino 8 建模或导出指定日期的施工回放，等退出后打印日志。

Rhino 命令行对非 ASCII 路径不可靠（仓库路径里可能有中文），所以先把脚本复制到
纯 ASCII 的临时目录，再通过环境变量 BRIDGE_BIM_ROOT 告诉它仓库在哪。

    python scripts/run_rhino.py
    python scripts/run_rhino.py --replay 2026-12-21
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge.replay import parse_moment  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RHINO = os.environ.get("RHINO_EXE", r"C:\Program Files\Rhino 8\System\Rhino.exe")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", metavar="DATE_OR_DATETIME", help="施工日期（日末）或当地时刻 YYYY-MM-DDTHH:MM")
    parser.add_argument("--dates", nargs="+", metavar="DATE_OR_DATETIME", help="在同一个 Rhino 进程中顺序导出多时刻")
    parser.add_argument("--jobs", metavar="JSON", help="批量任务文件：[{date, stage?, line?}]，可混合日期及不同工况")
    parser.add_argument("--stage", choices=("M1", "Mc", "M2", "MG"), help="附加既有整联载荷工况弯矩/反力/支承图")
    parser.add_argument("--line", default="L-U2-G3", help="载荷工况梁位线，如 L-U2-G3")
    parser.add_argument("--verify-panel", action="store_true", help="在原生 Rhino 内创建非模态 Eto 面板并验证播放、查询及阶段切换")
    parser.add_argument("--spatial-quality", action="store_true", help="批量结束后在同进程调用原生三维预制梁质量检查")
    parser.add_argument("--output-dir", help="回放产物目录；默认为 model/replay")
    args = parser.parse_args()
    if sum(bool(value) for value in (args.replay, args.dates, args.jobs)) > 1:
        parser.error("--replay、--dates 与 --jobs 任选一个")
    jobs = None
    if args.jobs:
        if args.stage:
            parser.error("--jobs 的工况请写在 JSON 的 stage 中")
        try:
            with open(args.jobs, encoding="utf-8") as handle:
                jobs = json.load(handle)
            if not isinstance(jobs, list) or not jobs:
                raise ValueError("任务文件应为非空列表")
            import re
            for job in jobs:
                if set(job) - {"date", "stage", "line"} or "date" not in job:
                    raise ValueError("任务字段应为 date、可选 stage/line")
                parse_moment(job["date"])
                if job.get("stage") not in (None, "M1", "Mc", "M2", "MG"):
                    raise ValueError("未知载荷工况")
                if not re.fullmatch(r"[LR]-U[1-3]-G[1-5]", job.get("line", "L-U2-G3")):
                    raise ValueError("任务中的 line 无效")
        except (OSError, ValueError, TypeError) as error:
            parser.error(str(error))
        args.replay = jobs[0]["date"]
    if args.dates:
        args.replay = args.dates[0]
    if args.output_dir and not args.replay:
        parser.error("--output-dir 需要同时指定 --replay")
    if (args.stage or args.verify_panel or args.spatial_quality) and not args.replay:
        parser.error("--stage/--verify-panel/--spatial-quality 需要指定回放或批量任务")
    if args.spatial_quality and not os.path.isfile(os.path.join(ROOT, "rhino", "spatial_quality.py")):
        parser.error("缺少已验证的 rhino/spatial_quality.py")
    if args.replay:
        try:
            moment = parse_moment(args.replay)
            for value in args.dates or []:
                parse_moment(value)
        except ValueError as error:
            parser.error(str(error))
        if args.stage:
            import re
            if not re.fullmatch(r"[LR]-U[1-3]-G[1-5]", args.line):
                parser.error("--line 应为 L/R-U1..3-G1..5")
    tmp = os.path.join(tempfile.gettempdir(), "bridge_bim_run")
    os.makedirs(tmp, exist_ok=True)
    script_name = "replay_construction.py" if args.replay else "build_model.py"
    script = os.path.join(tmp, script_name)
    shutil.copyfile(os.path.join(ROOT, "rhino", script_name), script)
    if not script.isascii():
        sys.exit("临时路径含非 ASCII 字符：%s，请把 TMP 设到纯英文目录" % script)
    if " " in script:
        sys.exit("临时脚本路径含空格：%s" % script)
    env = dict(os.environ, BRIDGE_BIM_ROOT=ROOT)
    # Do not inherit a previous replay mode into a normal model build.
    for key in ("BRIDGE_REPLAY_DATE", "BRIDGE_REPLAY_DATES", "BRIDGE_REPLAY_JOBS", "BRIDGE_REPLAY_OUT", "BRIDGE_REPLAY_LOG", "BRIDGE_REPLAY_STAGE", "BRIDGE_REPLAY_LINE", "BRIDGE_PANEL_TEST", "BRIDGE_SPATIAL_QA"):
        env.pop(key, None)
    if args.replay:
        output = os.path.abspath(args.output_dir or os.path.join(ROOT, "model", "replay"))
        os.makedirs(output, exist_ok=True)
        basename = "bridge_" + moment.date().isoformat()
        if len(args.replay) > 10:
            basename += "_" + moment.strftime("%H%M")
        if args.stage:
            basename += "_" + args.stage + "_" + args.line
            env.update(BRIDGE_REPLAY_STAGE=args.stage, BRIDGE_REPLAY_LINE=args.line)
        if args.verify_panel:
            env["BRIDGE_PANEL_TEST"] = "1"
        if args.spatial_quality:
            env["BRIDGE_SPATIAL_QA"] = "1"
        log = os.path.join(output, "bridge_batch.json" if jobs or args.dates and len(args.dates) > 1 else basename + ".json")
        if jobs:
            env["BRIDGE_REPLAY_JOBS"] = json.dumps(jobs)
        if args.dates:
            env["BRIDGE_REPLAY_DATES"] = json.dumps(args.dates)
        env.update(BRIDGE_REPLAY_DATE=args.replay, BRIDGE_REPLAY_OUT=output, BRIDGE_REPLAY_LOG=log)
    else:
        log = os.path.join(ROOT, "model", "build_log.json")
    if os.path.exists(log):
        os.remove(log)
    # 手工拼命令行：整段 /runscript 外包一层引号、脚本路径不再加引号。交给 subprocess
    # 按列表拼的话，内层引号会被转义成 \"，Rhino 收到畸形路径后会停在命令行等输入。
    cmdline = '"%s" /nosplash /notemplate /runscript="_-RunPythonScript %s _-Exit"' % (RHINO, script)
    t0 = time.time()
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.Popen(cmdline, env=env, startupinfo=startup)
    try:
        proc.wait(timeout=1500)
    except subprocess.TimeoutExpired:
        proc.kill()
        sys.exit("Rhino 1500 秒未退出，已终止")
    if not os.path.exists(log):
        sys.exit("Rhino 退出了但没写日志（%.0f 秒）——脚本多半没被执行" % (time.time() - t0))
    data = json.load(open(log, encoding="utf-8"))
    for s in data.get("steps", []):
        print(s)
    if not data.get("ok"):
        print(data.get("error", "（无错误信息）"))
        sys.exit(1)
    print("完成：%s 秒，%s 个对象，Rhino %s" % (data["seconds"], data["objects"], data["rhino"]))
    if args.replay:
        print("回放产物：" + output)


if __name__ == "__main__":
    main()
