"""Complete plan constraints independent of Rhino and external packages."""
import json
import unittest
from collections import defaultdict
from datetime import timedelta
from bridge import construction as CP, schedule as S
from bridge.model import build


class CompletePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elements, _ = build()
        cls.rows, _ = S.plan()
        cls.plan = CP.build(cls.elements, cls.rows)

    def test_all_scheduled_products_are_unique_and_removals_complete(self):
        classes = CP.FOLLOW_ON | {"girder", "continuity"}
        expected = {e.eid for e in self.elements if e.cls in classes}
        actual = [eid for t in self.plan["tasks"] for eid in t["elements"]]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(set(actual)))
        self.assertEqual(set(self.plan["removals"]), {e.eid for e in self.elements if e.cls == "temp_support"})

    def test_dependencies_are_acyclic_and_have_nonnegative_finish_start_lag(self):
        seen = set()
        for task in self.plan["tasks"]:
            self.assertNotIn(task["id"], seen)
            for predecessor in task["predecessors"]:
                self.assertIn(predecessor, seen)
                self.assertLessEqual(self.plan["by_id"][predecessor]["finish"], task["start"])
            seen.add(task["id"])

    def test_one_crew_per_deck_and_type_has_no_placement_overlap(self):
        groups = defaultdict(list)
        for task in self.plan["tasks"]:
            if task["class"] in CP.FOLLOW_ON:
                groups[task["deck"], task["class"]].append(task)
        for group in groups.values():
            ordered = sorted(group, key=lambda t: t["start"])
            for a, b in zip(ordered, ordered[1:]):
                self.assertLessEqual(a["work_finish"], b["start"])

    def test_cure_uses_elapsed_time_while_placement_uses_working_hours(self):
        for task in self.plan["tasks"]:
            if task["class"] == "precast":
                continue
            self.assertEqual(task["finish"] - task["work_finish"], timedelta(hours=task["cure_hours"]))
            self.assertEqual(CP.work_end(task["start"], task["work_hours"]), task["work_finish"])
        # Eight evening hours require the following working day.
        self.assertEqual(CP.work_end(CP.at(self.rows[0]["erect"], 8), 8), CP.at(self.rows[0]["erect"] + timedelta(days=1), 6))

    def test_configurable_assumptions_reschedule_downstream_without_changing_baseline(self):
        original = [dict(r) for r in self.rows]
        changed = CP.build(self.elements, self.rows, {"cantilever": {"cure_hours": 240}})
        self.assertGreater(changed["finish"], self.plan["finish"])
        self.assertGreater(changed["conversions"][0]["conversion"], self.plan["conversions"][0]["conversion"])
        self.assertEqual(self.rows, original)
        self.assertEqual(S.conversions(self.rows)[-1]["conversion"].isoformat(), "2027-02-02")

    def test_task_assumptions_are_explicit_and_export_is_json_ready(self):
        for task in self.plan["tasks"]:
            self.assertEqual(task["example_assumption"], task["class"] not in ("precast", "girder"))
        exported = CP.serializable(self.plan)
        self.assertEqual(json.loads(json.dumps(exported)), exported)
        self.assertEqual(exported["finish"], "2027-02-20T16:00:00")
        with self.assertRaises(ValueError):
            CP.build(self.elements, self.rows, {"pavement": {"work_hours": -1}})
        with self.assertRaises(ValueError):
            CP.build(self.elements, self.rows, {"pavement": {"cure_hours": float("nan")}})


if __name__ == "__main__":
    unittest.main()
