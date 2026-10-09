"""Validated, shared construction input; no measured progress is inferred."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "construction" / "input.json"
CLASSES = ("diaphragm", "wet_joint", "cantilever", "continuity", "conversion",
           "pavement", "barrier", "expansion_joint")
GATES = {"precast": ("fabrication_acceptance",), "girder": ("erection_strength", "prestress_grout"),
         "diaphragm": ("concrete_strength",), "wet_joint": ("concrete_strength",),
         "cantilever": ("concrete_strength",), "continuity": ("concrete_strength",),
         "conversion": ("continuity_strength", "negative_moment_prestress_grout", "conversion_authorization"),
         "pavement": ("concrete_strength",), "barrier": ("concrete_strength",),
         "expansion_joint": ("installation_acceptance",)}


def _keys(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(label + " keys must be exactly " + ", ".join(required))


def _number(value, label, minimum=0, strict=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number")
    if (value <= minimum if strict else value < minimum) or (integer and not isinstance(value, int)):
        raise ValueError(label + " is outside the allowed range/type")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be nonempty text")


def day(value):
    if not isinstance(value, str):
        raise ValueError("date must be YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must be YYYY-MM-DD")
    return parsed


def moment(value):
    if not isinstance(value, str):
        raise ValueError("approved_at must be an ISO local datetime")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None or "T" not in value:
        raise ValueError("approved_at must be an ISO local datetime without timezone")
    return parsed


def validate(value):
    data = deepcopy(value)
    _keys(data, ("schema", "mode", "source", "calendar", "baseline", "durations", "crews", "releases"), "input")
    if type(data["schema"]) is not int or data["schema"] != 1 or data["mode"] not in ("simulation", "project"):
        raise ValueError("schema=1 and mode=simulation/project required")
    _keys(data["source"], ("id", "reference", "author"), "source")
    for k, v in data["source"].items():
        _text(v, "source." + k)
    cal = data["calendar"]
    _keys(cal, ("weekdays", "rest_dates", "work_dates", "start_hour", "day_hours"), "calendar")
    if not isinstance(cal["weekdays"], list) or not cal["weekdays"] or any(type(x) is not int or x not in range(7) for x in cal["weekdays"]) or len(set(cal["weekdays"])) != len(cal["weekdays"]):
        raise ValueError("weekdays must contain unique integers 0..6 (Monday=0)")
    for key in ("rest_dates", "work_dates"):
        if not isinstance(cal[key], list):
            raise ValueError(key + " must be a date list")
        for v in cal[key]:
            day(v)
        if len(set(cal[key])) != len(cal[key]):
            raise ValueError("duplicate " + key)
    if set(cal["rest_dates"]) & set(cal["work_dates"]):
        raise ValueError("rest_dates and work_dates must not intersect")
    _number(cal["start_hour"], "start_hour")
    _number(cal["day_hours"], "day_hours", strict=True)
    if cal["start_hour"] + cal["day_hours"] > 24:
        raise ValueError("shift must fit one day")
    baseline = data["baseline"]
    _keys(baseline, ("yard_start", "erect_start", "n_beds", "bed_cycle_days", "min_age_days", "erect_hours", "launch_hours", "transfer_days", "continuity_lag_days"), "baseline")
    day(baseline["yard_start"]); day(baseline["erect_start"])
    for k in ("n_beds", "bed_cycle_days", "min_age_days"):
        _number(baseline[k], k, strict=True, integer=True)
    if baseline["min_age_days"] < baseline["bed_cycle_days"]:
        raise ValueError("min_age_days must not precede bed release")
    for k in ("transfer_days", "continuity_lag_days"):
        _number(baseline[k], k, integer=True)
    for k in ("erect_hours", "launch_hours"):
        _number(baseline[k], k, strict=True)
        if baseline[k] > cal["day_hours"]:
            raise ValueError(k + " cannot exceed the daily shift")
    _keys(data["durations"], CLASSES, "durations")
    for cls, values in data["durations"].items():
        _keys(values, ("work_hours", "cure_hours"), cls)
        _number(values["work_hours"], cls + ".work_hours", strict=True)
        _number(values["cure_hours"], cls + ".cure_hours")
    _keys(data["crews"], ("L", "R"), "crews")
    for deck, counts in data["crews"].items():
        _keys(counts, CLASSES, "crews." + deck)
        for cls, count in counts.items():
            _number(count, deck + "." + cls, strict=True, integer=True)
    if not isinstance(data["releases"], list):
        raise ValueError("releases must be a list")
    seen = set()
    for rec in data["releases"]:
        _keys(rec, ("task_id", "gate", "approved_at", "reference", "source"), "release")
        for k in ("task_id", "gate", "reference", "source"):
            _text(rec[k], "release." + k)
        moment(rec["approved_at"])
        key = (rec["task_id"], rec["gate"])
        if key in seen:
            raise ValueError("duplicate release: " + str(key))
        seen.add(key)
    if data["mode"] == "simulation" and data["releases"]:
        raise ValueError("simulation uses synthetic releases; project records require project mode")
    return data


def load(configuration=None):
    if isinstance(configuration, dict):
        return validate(configuration)
    path = Path(configuration or os.environ.get("BRIDGE_CONSTRUCTION_CONFIG", DEFAULT_PATH))
    # Missing/invalid explicit input always fails; there is no default fallback.
    return validate(json.loads(path.read_text(encoding="utf-8")))


def fingerprint(configuration):
    return hashlib.sha256(json.dumps(configuration, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


class Calendar:
    def __init__(self, settings):
        self.settings = settings
        self.weekdays = set(settings["weekdays"])
        self.rest = {day(v) for v in settings["rest_dates"]}
        self.extra = {day(v) for v in settings["work_dates"]}

    def at(self, value, hours=0):
        return datetime.combine(value, time()) + timedelta(hours=self.settings["start_hour"] + hours)

    def working(self, value):
        return value in self.extra or (value not in self.rest and value.weekday() in self.weekdays)

    def start(self, value):
        while not self.working(value.date()) or value >= self.at(value.date(), self.settings["day_hours"]):
            value = self.at(value.date() + timedelta(days=1))
        return max(value, self.at(value.date()))

    def end(self, value, hours):
        remaining, value = float(hours), self.start(value)
        while remaining > 1e-9:
            available = (self.at(value.date(), self.settings["day_hours"]) - value).total_seconds() / 3600
            used = min(available, remaining)
            value += timedelta(hours=used)
            remaining -= used
            if remaining > 1e-9:
                value = self.start(self.at(value.date() + timedelta(days=1)))
        return value
