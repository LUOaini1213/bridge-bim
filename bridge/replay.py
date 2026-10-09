"""Hour-level states of the shared complete example construction plan."""
import re
from datetime import date, datetime, time
from . import construction as CP
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


def event_times(rows, elements=None):
    elements = elements if elements is not None else build()[0]
    plan = CP.build(elements, rows)
    return sorted({t[key] for t in plan["tasks"] for key in ("start", "work_finish", "finish")})


def event_days(rows, elements=None):
    return sorted({value.date() for value in event_times(rows, elements)})


def adjacent_day(rows, day, direction):
    day = parse_day(day)
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    candidates = [d for d in event_days(rows) if (d - day).days * direction > 0]
    return (candidates[0] if direction == 1 else candidates[-1]) if candidates else day


def task_state(task, moment):
    if moment < task["start"]:
        return "pending"
    if moment < task["work_finish"]:
        return "constructing"
    if moment < task["finish"]:
        return "curing"
    return "completed"


def snapshot(elements, rows, value, plan=None):
    moment = parse_moment(value)
    plan = plan or CP.build(elements, rows)
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
            if moment < erection["start"]:
                phase, visible = "not_active", False
            elif moment < removal["finish"]:
                phase, visible = "active", True
            else:
                phase, visible = "removed", False
            task_id = removal["id"]
        elif task_id:
            task = plan["by_id"][task_id]
            phase = task_states[task_id]
            visible = phase != "pending"
            if cls == "girder":
                phase = {"pending": "not_erected", "constructing": "installing", "completed": "erected"}[phase]
            elif cls == "continuity":
                conversion = plan["by_id"]["C-%s%d-2" % (element.deck, element.attrs["unit"])]
                phase = "not_cast" if not visible else "continuous" if moment >= conversion["finish"] else "curing"
        states[eid] = {"class": cls, "phase": phase, "visible": visible,
                       "scheduled": cls in SCHEDULED_CLASSES, "task": task_id}
    counts = {
        "girders_erected": sum(s["class"] == "girder" and s["phase"] == "erected" for s in states.values()),
        "girders_installing": sum(s["phase"] == "installing" for s in states.values()),
        "girders_total": sum(s["class"] == "girder" for s in states.values()),
        "continuity_cast": sum(s["class"] == "continuity" and s["visible"] for s in states.values()),
        "continuity_total": sum(s["class"] == "continuity" for s in states.values()),
        "units_converted": sum(c["conversion"] <= moment for c in plan["conversions"]),
        "units_total": len(plan["conversions"]),
        "temporary_supports_active": sum(s["class"] == "temp_support" and s["visible"] for s in states.values()),
        "follow_on_completed": sum(s["class"] in CP.FOLLOW_ON and s["phase"] == "completed" for s in states.values()),
        "follow_on_total": sum(s["class"] in CP.FOLLOW_ON for s in states.values()),
        "tasks_completed": sum(v == "completed" for v in task_states.values()), "tasks_total": len(task_states),
        "yard_in_production": sum(CP.at(r["cast"]) <= moment < CP.at(r["to_storage"]) for r in rows),
        "yard_in_storage": sum(CP.at(r["to_storage"]) <= moment < CP.at(r["erect"], r["erect_hour"]) for r in rows),
    }
    semantics = "exact_time" if isinstance(value, datetime) or isinstance(value, str) and len(value) > 10 else "end_of_day"
    return {"date": moment.date().isoformat(), "datetime": moment.isoformat(), "time_semantics": semantics,
            "elements": states, "tasks": task_states, "counts": counts, "plan_finish": plan["finish"].isoformat(),
            "limitations": list(LIMITATIONS)}
