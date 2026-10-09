"""Complete example construction plan shared by IFC and Rhino.

The original precast/erection baseline is preserved in schedule.py. Added
production rates and curing holds are explicit visualization assumptions,
not measured site data or concrete strength acceptance criteria. Changing
these values changes this plan, never the existing structural load cases.
"""
from datetime import date, datetime, time, timedelta
import math
from . import config as C
from . import construction_input as CI
from .crew_schedule import CrewPool
from .model import unit_bounds, unit_of_span

NAMES = {"diaphragm": "横隔板", "wet_joint": "湿接缝", "cantilever": "翼缘现浇段",
         "pavement": "桥面铺装", "barrier": "护栏", "expansion_joint": "伸缩装置"}
FOLLOW_ON = frozenset(NAMES)
NOTICE = ("施工工时、养护、班组与工作日历来自同源 JSON 配置；默认为演示假设，非现场实测；"
          "simulation 使用 synthetic release，养护时长不代表强度验收；project 须有放行记录并满足前置门禁。"
          "计划及满足所选班组策略与门禁的可行预测均非实测进度，原整联力学案例不代表日期部分架设重算。")


def at(day, hours=0):
    return CI.Calendar(CI.load()["calendar"]).at(day, hours)


def work_start(value):
    return CI.Calendar(CI.load()["calendar"]).start(value)


def work_end(start, hours):
    """Working hours use the existing daily shift; cure uses elapsed hours."""
    if not math.isfinite(hours) or hours < 0:
        raise ValueError("work hours must be finite and nonnegative")
    return CI.Calendar(CI.load()["calendar"]).end(start, hours)


def build(elements, rows, assumptions=None, configuration=None):
    if not rows:
        raise ValueError("empty erection schedule")
    settings = CI.load(configuration)
    calendar = CI.Calendar(settings["calendar"])
    at = calendar.at
    work_start, work_end = calendar.start, calendar.end
    inputs = {key: dict(value) for key, value in settings["durations"].items()}
    if assumptions:
        for key, value in assumptions.items():
            if key not in inputs:
                raise ValueError("unknown construction class: " + key)
            if set(value) - {"work_hours", "cure_hours"}:
                raise ValueError("unknown duration input for " + key)
            inputs[key].update(value)
    settings["durations"] = inputs
    CI.validate(settings)
    for values in inputs.values():
        if not all(math.isfinite(v) for v in values.values()) or values["work_hours"] <= 0 or values["cure_hours"] < 0:
            raise ValueError("work_hours > 0 and cure_hours >= 0 required")
    tasks, by_id, outputs, removals = [], {}, {}, {}
    releases = {(r["task_id"], r["gate"]): r for r in settings["releases"]}
    strategy = settings["scheduling"]["crew_strategy"]
    origin = at(CI.day(settings["baseline"]["yard_start"]))
    pools = CrewPool(calendar, settings["crews"], origin, strategy)
    effective_pools = CrewPool(calendar, settings["crews"], origin, strategy)

    def task(ident, label, cls, start, hours, eids=(), predecessors=(), cure=0,
             deck="", unit=0, span=0, removes=(), example=False,
             launch_before=0, transfer_before=None, effective_not_before=None):
        if ident in by_id:
            raise ValueError("duplicate task: " + ident)
        requested_start = start
        if cls in CI.CLASSES:
            start, finish_work, crew, allocation_waits = pools.reserve(deck, cls, start, hours, ident)
        else:
            start = work_start(start) if hours else start
            finish_work = work_end(start, hours) if hours else start
            crew, allocation_waits = "", []
            if start > requested_start:
                allocation_waits.append("calendar: next working shift")
        item = {"id": ident, "name": label, "class": cls, "deck": deck, "unit": unit, "span": span,
                "start": start, "work_finish": finish_work, "finish": finish_work + timedelta(hours=cure),
                "work_hours": float(hours), "cure_hours": float(cure), "example_assumption": settings["mode"] == "simulation",
                "progress_basis": "forecast_only_not_measured", "added_process": example,
                "elements": list(eids), "removes": list(removes), "predecessors": list(predecessors),
                "crew_id": crew, "crew_strategy": strategy, "wait_reasons": allocation_waits,
                "mode": settings["mode"], "source": dict(settings["source"])}
        item["machine_launch_before_hours"] = float(launch_before)
        item["machine_transfer_before_days"] = transfer_before
        for predecessor in predecessors:
            if by_id[predecessor]["finish"] > start:
                raise ValueError("dependency finishes after start: " + predecessor + " -> " + ident)
        tasks.append(item)
        by_id[ident] = item
        gates = CI.GATES.get(cls, ())
        entry = cls in ("girder", "conversion")
        item["gates"] = [{"name": gate, "position": "entry" if entry else "completion",
                          "synthetic": settings["mode"] == "simulation",
                          "record": releases.get((ident, gate))} for gate in gates]
        holds = []
        effective_waits = []
        earliest = start
        for predecessor in predecessors:
            ready = by_id[predecessor]["release_at"]
            if ready is None:
                holds.append("predecessor not released: " + predecessor)
            else:
                if ready > earliest:
                    effective_waits.append("predecessor release: " + predecessor)
                earliest = max(earliest, ready)
        if effective_not_before is not None:
            if effective_not_before > earliest:
                effective_waits.append("machine: continuity lag after erection")
            earliest = max(earliest, effective_not_before)
        # A delayed machine chain still requires each unsplit launch and the
        # next-day-plus-transfer gap; old forecast gaps cannot substitute it.
        if cls == "girder" and len(predecessors) > 1:
            machine_ready = by_id[predecessors[-1]]["release_at"]
            if machine_ready is not None:
                if transfer_before is not None:
                    transfer_ready = at(machine_ready.date() + timedelta(days=1 + transfer_before))
                    if transfer_ready > earliest:
                        effective_waits.append("machine: next-day transfer interval")
                    earliest = max(earliest, transfer_ready)
                elif launch_before:
                    launch_start = work_start(machine_ready)
                    if (at(launch_start.date(), settings["calendar"]["day_hours"]) - launch_start).total_seconds() / 3600 < launch_before - 1e-9:
                        launch_start = work_start(at(launch_start.date() + timedelta(days=1)))
                    launch_ready = work_end(launch_start, launch_before)
                    if launch_ready > earliest:
                        effective_waits.append("machine: unsplit launch work")
                    earliest = max(earliest, launch_ready)
        if settings["mode"] == "project":
            for gate in item["gates"]:
                if gate["record"] is None:
                    holds.append("missing approval: " + gate["name"])
                elif entry:
                    if CI.moment(gate["record"]["approved_at"]) > earliest:
                        effective_waits.append("entry approval: " + gate["name"])
                    earliest = max(earliest, CI.moment(gate["record"]["approved_at"]))
        entry_blocked = any(h.startswith("predecessor") for h in holds) or (entry and bool(holds))
        if entry_blocked:
            effective_start = effective_work = effective_finish = ready = None
            effective_crew = ""
        else:
            if cls in CI.CLASSES:
                effective_start, effective_work, effective_crew, resource_waits = effective_pools.reserve(deck, cls, earliest, hours, ident)
                effective_waits.extend(resource_waits)
            else:
                effective_start = work_start(earliest) if hours else earliest
                if cls == "girder" and (at(effective_start.date(), settings["calendar"]["day_hours"]) - effective_start).total_seconds() / 3600 < hours - 1e-9:
                    effective_start = work_start(at(effective_start.date() + timedelta(days=1)))
                    effective_waits.append("machine: erection must fit one shift")
                if effective_start > earliest and not effective_waits:
                    effective_waits.append("calendar: next working shift")
                effective_work = work_end(effective_start, hours) if hours else effective_start
                effective_crew = ""
            effective_finish = effective_work + timedelta(hours=cure)
            ready = None if holds else effective_finish
            if ready is not None and settings["mode"] == "project":
                ready = max([ready] + [CI.moment(g["record"]["approved_at"]) for g in item["gates"]])
        item.update(effective_start=effective_start, effective_work_finish=effective_work,
                    effective_finish=effective_finish, release_at=ready, effective_crew_id=effective_crew,
                    status="HELD" if holds else "PLANNED", hold_reasons=holds, effective_wait_reasons=effective_waits)
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
                  cure=(at(row["to_storage"]) - at(row["cast"])).total_seconds()/3600,
                  deck=row["deck"], span=row["span"])
        preds = [tp["id"]] + ([previous] if previous else [])
        te = task("E%03d" % number, "架设 " + eid, "girder", at(row["erect"], row["erect_hour"]),
                  settings["baseline"]["erect_hours"], [eid], preds, deck=row["deck"], unit=unit_of_span(row["span"]), span=row["span"],
                  launch_before=settings["baseline"]["launch_hours"] if row["pos"] == 1 and row["span"] > 1 else 0,
                  transfer_before=settings["baseline"]["transfer_days"] if row["pos"] == 1 and row["span"] == 1 and previous else None)
        erected[eid], previous = te, te["id"]
    last_span = {}
    # A configurable crew pool per deck and work type. fixed_route preserves
    # span-order dispatch; earliest_gap may insert later tasks into free slots.
    for deck, _ in C.DECKS:
        for span in range(1, C.N_SPANS + 1):
            preds = [erected[r["girder"]]["id"] for r in rows if r["deck"] == deck and r["span"] == span]
            for cls in ("diaphragm", "wet_joint", "cantilever"):
                values = inputs[cls]
                eids = [e.eid for e in elements if e.cls == cls and e.deck == deck and e.attrs["span"] == span]
                start = max(by_id[p]["finish"] for p in preds)
                t = task("F-%s-%s%02d" % (cls, deck, span), NAMES[cls] + " %s%02d" % (deck, span), cls,
                         start, values["work_hours"], eids, preds, values["cure_hours"], deck,
                         unit_of_span(span), span, example=True)
                preds = [t["id"]]
            last_span[(deck, span)] = t
    conversions = []
    for deck, _ in C.DECKS:
        for unit, (a, b) in enumerate(unit_bounds(), 1):
            preds = [last_span[(deck, span)]["id"] for span in range(a + 1, b + 1)]
            earliest = max(r["erect"] for r in rows if r["deck"] == deck and a < r["span"] <= b)
            start = max(at(earliest + timedelta(days=settings["baseline"]["continuity_lag_days"])),
                        max(by_id[p]["finish"] for p in preds))
            eids = [e.eid for e in elements if e.cls == "continuity" and e.deck == deck and e.attrs["unit"] == unit]
            erection_times = [erected[r["girder"]]["release_at"] for r in rows if r["deck"] == deck and a < r["span"] <= b]
            effective_cast_min = at(max(erection_times).date() + timedelta(days=settings["baseline"]["continuity_lag_days"])) if all(v is not None for v in erection_times) else None
            tc = task("C-%s%d-1" % (deck, unit), "浇筑墩顶连续段 %s第%d联" % (deck, unit), "continuity",
                      start, inputs["continuity"]["work_hours"], eids, preds, cure=inputs["continuity"]["cure_hours"], deck=deck, unit=unit, example=True, effective_not_before=effective_cast_min)
            remove = [e.eid for e in elements if e.cls == "temp_support" and e.deck == deck
                      and unit_of_span(next(r["span"] for r in rows if r["girder"] == e.attrs["girder"])) == unit]
            tk = task("C-%s%d-2" % (deck, unit), "体系转换 %s第%d联" % (deck, unit), "conversion",
                      tc["finish"], inputs["conversion"]["work_hours"], predecessors=[tc["id"]], cure=inputs["conversion"]["cure_hours"],
                      deck=deck, unit=unit, removes=remove, example=True)
            conversions.append({"deck": deck, "unit": unit, "cast": tc["start"], "conversion": tk["finish"]})
            preds = [tk["id"]]
            for cls in ("pavement", "barrier"):
                values = inputs[cls]
                eids = [e.eid for e in elements if e.cls == cls and e.deck == deck and e.attrs["unit"] == unit]
                start = by_id[preds[0]]["finish"]
                t = task("F-%s-%s%d" % (cls, deck, unit), NAMES[cls] + " %s第%d联" % (deck, unit), cls,
                         start, values["work_hours"], eids, preds, values["cure_hours"], deck, unit, example=True)
                preds = [t["id"]]
            for span in range(a + 1, b + 1):
                last_span[(deck, span)] = t
    for element in (e for e in elements if e.cls == "expansion_joint"):
        k = element.attrs["support"]
        adjacent = [span for span in (k, k + 1) if 1 <= span <= C.N_SPANS]
        preds = sorted({last_span[(element.deck, span)]["id"] for span in adjacent})
        values = inputs["expansion_joint"]
        start = max(by_id[p]["finish"] for p in preds)
        t = task("F-" + element.eid, "安装伸缩装置 " + element.eid, "expansion_joint", start,
                 values["work_hours"], [element.eid], preds, values["cure_hours"], element.deck, example=True)
    for ident, gate in releases:
        if ident not in by_id or gate not in CI.GATES.get(by_id[ident]["class"], ()):
            raise ValueError("unknown release task/gate: " + ident + "/" + gate)
    return {"tasks": tasks, "by_id": by_id, "outputs": outputs, "removals": removals,
            "conversions": conversions, "start": min(t["start"] for t in tasks),
            "finish": max(t["finish"] for t in tasks), "assumptions": inputs, "notice": NOTICE,
            "configuration": settings, "configuration_sha256": CI.fingerprint(settings),
            "crew_strategy": strategy,
            "mode": settings["mode"], "held_tasks": sum(t["status"] == "HELD" for t in tasks),
            "reference_baseline": {"file":"data/conversions.csv", "lag_days":C.CONT_CAST_LAG_DAYS,
                                   "cure_days":C.CONT_CURE_DAYS,
                                   "notice":"原转换参考算法，日期精度；不含后续工序、配置养护或门禁，summary.conversion_last 非完整计划完成时间"},
            "effective_finish": max(t["release_at"] for t in tasks) if all(t["release_at"] is not None for t in tasks) else None}


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
    import json
    return [{k: (v.isoformat() if isinstance(v, datetime) else " ".join(v) if isinstance(v, list) and all(isinstance(x, str) for x in v)
                else json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else "" if v is None else v)
             for k, v in t.items()} for t in plan["tasks"]]
