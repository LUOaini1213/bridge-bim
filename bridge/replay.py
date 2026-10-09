"""Hour-level states of the shared complete example construction plan."""
import re
from datetime import date, datetime, time
from . import construction as CP
from . import construction_input as CI
from .model import build

SCHEDULED_CLASSES = frozenset(("girder", "continuity", "temp_support")) | CP.FOLLOW_ON
EXCLUDED_CLASSES = frozenset()
LIMITATIONS = (
    CP.NOTICE,
    "日期输入表示日末；日期+时刻按任务开始/施工结束/养护结束/转换结束精确判断。不模拟吊装运动或混凝土渐增体积。",
    "下部结构、垫石与永久支座作为静态参照，不代表已按该日期施工完成。",
    "梁场仅统计在制与存梁数量；原模型的峰值日梁场快照在回放中隐藏。",
    "结构面板显示既有整联 M1/Mc/M2 载荷工况，非该日期的部分施工重新求解；原支座龄期仍取原基线。",
)


def parse_day(value):
    if isinstance(value, datetime):
        raise ValueError("日期应为 YYYY-MM-DD，不含时刻")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    return date.fromisoformat(value)


def parse_moment(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ValueError("请使用项目当地无时区时刻")
        return value
    if isinstance(value, date) or isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(parse_day(value), time(23, 59, 59))
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", value):
        return datetime.fromisoformat(value)
    raise ValueError("时刻格式应为 YYYY-MM-DD HH:MM，或 YYYY-MM-DD（日末）")


def event_times(rows, elements=None, plan=None):
    elements = elements if elements is not None else build()[0]
    plan = plan or CP.build(elements, rows)
    return sorted({t[key] for t in plan["tasks"] for key in ("start", "work_finish", "finish", "effective_start", "effective_work_finish", "effective_finish", "release_at") if t[key] is not None})


def event_days(rows, elements=None):
    return sorted({value.date() for value in event_times(rows, elements)})


def timeline_bounds(plan):
    """Partial project approvals may extend beyond an otherwise held forecast."""
    values = [t[k] for t in plan["tasks"] for k in ("start", "work_finish", "finish", "effective_start", "effective_work_finish", "effective_finish", "release_at") if t[k] is not None]
    return min(values), max(values)


def adjacent_day(rows, day, direction):
    day = parse_day(day)
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    candidates = [d for d in event_days(rows) if (d - day).days * direction > 0]
    return (candidates[0] if direction == 1 else candidates[-1]) if candidates else day


def task_state(task, moment):
    start = task["effective_start"]
    if start is None:
        return "pending" if moment < task["start"] else "held"
    if moment < start:
        return "pending"
    if moment < task["effective_work_finish"]:
        return "constructing"
    if moment < task["effective_finish"]:
        return "curing"
    if task["release_at"] is None or moment < task["release_at"]:
        return "held"
    return "completed"


def snapshot(elements, rows, value, plan=None):
    moment = parse_moment(value)
    plan = plan or CP.build(elements, rows)
    calendar = CI.Calendar(plan["configuration"]["calendar"])
    task_states = {t["id"]: task_state(t, moment) for t in plan["tasks"]}
    if len({r["girder"] for r in rows}) != len(rows):
        raise ValueError("施工排程包含重复梁号")
    states = {}
    for element in elements:
        if element.eid in states:
            raise ValueError("模型包含重复构件编号：" + element.eid)
        eid, cls = element.eid, element.cls
        task_id = plan["outputs"].get(eid)
        phase, visible = "static_reference", True
        if cls == "temp_support":
            erection = plan["by_id"][plan["outputs"][element.attrs["girder"]]]
            removal = plan["by_id"][plan["removals"][eid]]
            if erection["effective_start"] is None or moment < erection["effective_start"]:
                phase, visible = "not_active", False
            elif removal["release_at"] is None or moment < removal["release_at"]:
                phase, visible = "active", True
            else:
                phase, visible = "removed", False
            task_id = removal["id"]
        elif task_id:
            task = plan["by_id"][task_id]
            phase = task_states[task_id]
            visible = phase != "pending" and task["effective_start"] is not None
            if cls == "girder":
                phase = {"pending": "not_erected", "constructing": "installing", "completed": "erected", "held": "held", "curing": "curing"}[phase]
            elif cls == "continuity":
                conversion = plan["by_id"]["C-%s%d-2" % (element.deck, element.attrs["unit"])]
                phase = ("held" if phase == "held" else "not_cast") if not visible else "held" if phase == "held" else "continuous" if conversion["release_at"] is not None and moment >= conversion["release_at"] else "curing"
        states[eid] = {"class": cls, "phase": phase, "visible": visible,
                       "scheduled": cls in SCHEDULED_CLASSES, "task": task_id,
                       "hold_reasons": plan["by_id"][task_id]["hold_reasons"] if task_id else []}
    counts = {
        "girders_erected": sum(s["class"] == "girder" and s["phase"] == "erected" for s in states.values()),
        "girders_installing": sum(s["phase"] == "installing" for s in states.values()),
        "girders_total": sum(s["class"] == "girder" for s in states.values()),
        "continuity_cast": sum(s["class"] == "continuity" and s["visible"] for s in states.values()),
        "continuity_total": sum(s["class"] == "continuity" for s in states.values()),
        "units_converted": sum(t["class"] == "conversion" and task_states[t["id"]] == "completed" for t in plan["tasks"]),
        "units_total": len(plan["conversions"]),
        "temporary_supports_active": sum(s["class"] == "temp_support" and s["visible"] for s in states.values()),
        "follow_on_completed": sum(s["class"] in CP.FOLLOW_ON and s["phase"] == "completed" for s in states.values()),
        "follow_on_total": sum(s["class"] in CP.FOLLOW_ON for s in states.values()),
        "tasks_completed": sum(v == "completed" for v in task_states.values()), "tasks_total": len(task_states),
        "tasks_held": sum(v == "held" for v in task_states.values()),
        "yard_in_production": sum(calendar.at(r["cast"]) <= moment < calendar.at(r["to_storage"]) for r in rows),
        "yard_in_storage": sum(calendar.at(r["to_storage"]) <= moment and (plan["by_id"][plan["outputs"][r["girder"]]]["effective_start"] is None or moment < plan["by_id"][plan["outputs"][r["girder"]]]["effective_start"]) for r in rows),
    }
    semantics = "exact_time" if isinstance(value, datetime) or isinstance(value, str) and len(value) > 10 else "end_of_day"
    return {"date": moment.date().isoformat(), "datetime": moment.isoformat(), "time_semantics": semantics,
            "elements": states, "tasks": task_states, "counts": counts, "plan_finish": plan["finish"].isoformat(),
            "limitations": list(LIMITATIONS), "mode": plan["mode"],
            "construction_config": plan["configuration"], "configuration_sha256": plan["configuration_sha256"],
            "effective_plan_finish": plan["effective_finish"].isoformat() if plan["effective_finish"] else None}
