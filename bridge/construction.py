"""Complete example construction plan shared by IFC and Rhino.

The original precast/erection baseline is preserved in schedule.py. Added
production rates and curing holds are explicit visualization assumptions,
not measured site data or concrete strength acceptance criteria. Changing
these values changes this plan, never the existing structural load cases.
"""
from datetime import date, datetime, time, timedelta
import math
from . import config as C
from .model import unit_bounds, unit_of_span

ASSUMPTIONS = {
    "diaphragm": {"work_hours": 4.0, "cure_hours": 48.0},
    "wet_joint": {"work_hours": 4.0, "cure_hours": 48.0},
    "cantilever": {"work_hours": 4.0, "cure_hours": 48.0},
    "pavement": {"work_hours": 20.0, "cure_hours": 72.0},
    "barrier": {"work_hours": 20.0, "cure_hours": 72.0},
    "expansion_joint": {"work_hours": 4.0, "cure_hours": 0.0},
}
# Added conversion work duration is also a configurable demonstration input.
# Continuity placement/cure use the existing DAY_HOURS / CONT_CURE_DAYS.
CONVERSION_WORK_HOURS = 2.0
NAMES = {"diaphragm": "横隔板", "wet_joint": "湿接缝", "cantilever": "翼缘现浇段",
         "pavement": "桥面铺装", "barrier": "护栏", "expansion_joint": "伸缩装置"}
FOLLOW_ON = frozenset(NAMES)
NOTICE = ("新增工序工效、养护停留及每幅每工种一班组均为可配置演示假设，非现场实测；"
          "养护时长不代表强度验收。完整示例计划顺延连续段与转换；原架梁基线及力学结果保留作对照。")


def at(day, hours=0):
    return datetime.combine(day, time()) + timedelta(hours=C.DAY_START_HOUR + hours)


def work_start(value):
    start, end = at(value.date()), at(value.date(), C.DAY_HOURS)
    if value < start:
        return start
    return at(value.date() + timedelta(days=1)) if value >= end else value


def work_end(start, hours):
    """Working hours use the existing daily shift; cure uses elapsed hours."""
    if not math.isfinite(hours) or hours < 0:
        raise ValueError("work hours must be finite and nonnegative")
    current, remaining = work_start(start), float(hours)
    while remaining > 1e-9:
        available = (at(current.date(), C.DAY_HOURS) - current).total_seconds() / 3600
        used = min(available, remaining)
        current += timedelta(hours=used)
        remaining -= used
        if remaining > 1e-9:
            current = at(current.date() + timedelta(days=1))
    return current


def build(elements, rows, assumptions=None):
    if not rows:
        raise ValueError("empty erection schedule")
    if not 0 < C.DAY_HOURS <= 24 - C.DAY_START_HOUR:
        raise ValueError("work shift must fit within one calendar day")
    inputs = {key: dict(value) for key, value in ASSUMPTIONS.items()}
    if assumptions:
        for key, value in assumptions.items():
            if key not in inputs:
                raise ValueError("unknown construction class: " + key)
            if set(value) - {"work_hours", "cure_hours"}:
                raise ValueError("unknown duration input for " + key)
            inputs[key].update(value)
    for values in inputs.values():
        if not all(math.isfinite(v) for v in values.values()) or values["work_hours"] <= 0 or values["cure_hours"] < 0:
            raise ValueError("work_hours > 0 and cure_hours >= 0 required")
    tasks, by_id, outputs, removals = [], {}, {}, {}

    def task(ident, label, cls, start, hours, eids=(), predecessors=(), cure=0,
             deck="", unit=0, span=0, removes=(), example=False):
        if ident in by_id:
            raise ValueError("duplicate task: " + ident)
        start = work_start(start) if hours else start
        finish_work = work_end(start, hours) if hours else start
        item = {"id": ident, "name": label, "class": cls, "deck": deck, "unit": unit, "span": span,
                "start": start, "work_finish": finish_work, "finish": finish_work + timedelta(hours=cure),
                "work_hours": float(hours), "cure_hours": float(cure), "example_assumption": example,
                "elements": list(eids), "removes": list(removes), "predecessors": list(predecessors)}
        for predecessor in predecessors:
            if by_id[predecessor]["finish"] > start:
                raise ValueError("dependency finishes after start: " + predecessor + " -> " + ident)
        tasks.append(item)
        by_id[ident] = item
        for eid in eids:
            if eid in outputs:
                raise ValueError("duplicate construction output: " + eid)
            outputs[eid] = ident
        for eid in removes:
            removals[eid] = ident
        return item

    erected = {}
    previous = None
    for row in rows:
        eid, number = row["girder"], row["order"]
        tp = task("Y%03d" % number, "预制 " + eid, "precast", at(row["cast"]), 0,
                  deck=row["deck"], span=row["span"])
        tp["finish"] = tp["work_finish"] = at(row["to_storage"])
        preds = [tp["id"]] + ([previous] if previous else [])
        te = task("E%03d" % number, "架设 " + eid, "girder", at(row["erect"], row["erect_hour"]),
                  C.ERECT_HOURS, [eid], preds, deck=row["deck"], unit=unit_of_span(row["span"]), span=row["span"])
        erected[eid], previous = te, te["id"]
    crew = {}
    last_span = {}
    # One work crew per deck and work type. Crew advances after placement;
    # the separate curing hold can overlap its next span's placement.
    for deck, _ in C.DECKS:
        for span in range(1, C.N_SPANS + 1):
            preds = [erected[r["girder"]]["id"] for r in rows if r["deck"] == deck and r["span"] == span]
            for cls in ("diaphragm", "wet_joint", "cantilever"):
                values = inputs[cls]
                eids = [e.eid for e in elements if e.cls == cls and e.deck == deck and e.attrs["span"] == span]
                start = max([by_id[p]["finish"] for p in preds] + [crew.get((deck, cls), at(C.YARD_START))])
                t = task("F-%s-%s%02d" % (cls, deck, span), NAMES[cls] + " %s%02d" % (deck, span), cls,
                         start, values["work_hours"], eids, preds, values["cure_hours"], deck,
                         unit_of_span(span), span, example=True)
                crew[(deck, cls)] = t["work_finish"]
                preds = [t["id"]]
            last_span[(deck, span)] = t
    conversions = []
    for deck, _ in C.DECKS:
        for unit, (a, b) in enumerate(unit_bounds(), 1):
            preds = [last_span[(deck, span)]["id"] for span in range(a + 1, b + 1)]
            earliest = max(r["erect"] for r in rows if r["deck"] == deck and a < r["span"] <= b)
            start = max(at(earliest + timedelta(days=C.CONT_CAST_LAG_DAYS)),
                        max(by_id[p]["finish"] for p in preds))
            eids = [e.eid for e in elements if e.cls == "continuity" and e.deck == deck and e.attrs["unit"] == unit]
            tc = task("C-%s%d-1" % (deck, unit), "浇筑墩顶连续段 %s第%d联" % (deck, unit), "continuity",
                      start, C.DAY_HOURS, eids, preds, cure=24 * C.CONT_CURE_DAYS, deck=deck, unit=unit, example=True)
            remove = [e.eid for e in elements if e.cls == "temp_support" and e.deck == deck
                      and unit_of_span(next(r["span"] for r in rows if r["girder"] == e.attrs["girder"])) == unit]
            tk = task("C-%s%d-2" % (deck, unit), "体系转换 %s第%d联" % (deck, unit), "conversion",
                      tc["finish"], CONVERSION_WORK_HOURS, predecessors=[tc["id"]],
                      deck=deck, unit=unit, removes=remove, example=True)
            conversions.append({"deck": deck, "unit": unit, "cast": tc["start"], "conversion": tk["finish"]})
            preds = [tk["id"]]
            for cls in ("pavement", "barrier"):
                values = inputs[cls]
                eids = [e.eid for e in elements if e.cls == cls and e.deck == deck and e.attrs["unit"] == unit]
                start = max(by_id[preds[0]]["finish"], crew.get((deck, cls), at(C.YARD_START)))
                t = task("F-%s-%s%d" % (cls, deck, unit), NAMES[cls] + " %s第%d联" % (deck, unit), cls,
                         start, values["work_hours"], eids, preds, values["cure_hours"], deck, unit, example=True)
                crew[(deck, cls)] = t["work_finish"]
                preds = [t["id"]]
            for span in range(a + 1, b + 1):
                last_span[(deck, span)] = t
    for element in (e for e in elements if e.cls == "expansion_joint"):
        k = element.attrs["support"]
        adjacent = [span for span in (k, k + 1) if 1 <= span <= C.N_SPANS]
        preds = sorted({last_span[(element.deck, span)]["id"] for span in adjacent})
        values = inputs["expansion_joint"]
        start = max([by_id[p]["finish"] for p in preds] + [crew.get((element.deck, "expansion_joint"), at(C.YARD_START))])
        t = task("F-" + element.eid, "安装伸缩装置 " + element.eid, "expansion_joint", start,
                 values["work_hours"], [element.eid], preds, values["cure_hours"], element.deck, example=True)
        crew[(element.deck, "expansion_joint")] = t["work_finish"]
    return {"tasks": tasks, "by_id": by_id, "outputs": outputs, "removals": removals,
            "conversions": conversions, "start": min(t["start"] for t in tasks),
            "finish": max(t["finish"] for t in tasks), "assumptions": inputs, "notice": NOTICE}


def serializable(plan):
    def clean(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value
    return clean({k: v for k, v in plan.items() if k != "by_id"})


def table(plan):
    return [{k: (v.isoformat() if isinstance(v, datetime) else " ".join(v) if isinstance(v, list) else v)
             for k, v in t.items()} for t in plan["tasks"]]
