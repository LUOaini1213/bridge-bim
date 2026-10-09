"""Date boundaries and replay scope, independent of Rhino and third parties."""
import json
import unittest
from datetime import date, datetime, timedelta

from bridge import replay as R, schedule as S, construction as CP
from bridge.model import build, unit_of_span


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elements, _ = build()
        cls.rows, _ = S.plan()
        cls.complete = CP.build(cls.elements, cls.rows)
        cls.conversions = [{**c, "cast": c["cast"].date(), "conversion": c["conversion"].date()} for c in cls.complete["conversions"]]
        cls.first = min(r["erect"] for r in cls.rows)
        cls.last = cls.complete["finish"].date()
        cls.plan = {r["girder"]: r for r in cls.rows}

    def at(self, day):
        return R.snapshot(self.elements, self.rows, day)

    def test_before_erection_is_empty_but_static_context_is_explicit(self):
        result = self.at(self.first - timedelta(days=1))
        self.assertEqual(result["counts"]["girders_erected"], 0)
        self.assertEqual(result["counts"]["continuity_cast"], 0)
        self.assertEqual(result["counts"]["temporary_supports_active"], 0)
        self.assertEqual(result["elements"]["C-P01-L1"]["phase"], "static_reference")
        self.assertTrue(result["elements"]["C-P01-L1"]["visible"])

    def test_exact_erection_day_includes_all_of_that_days_girders(self):
        result = self.at(self.first)
        installed = {eid for eid, state in result["elements"].items()
                     if state["class"] == "girder" and state["visible"]}
        expected = {r["girder"] for r in self.rows if r["erect"] == self.first}
        self.assertEqual(installed, expected)
        self.assertGreater(len(installed), 0)
        # A support changes on the same date as its own girder, not when
        # the entire span or unit has finished.
        for element in self.elements:
            if element.cls == "temp_support":
                self.assertEqual(result["elements"][element.eid]["visible"],
                                 element.attrs["girder"] in expected)

    def test_cast_day_precedes_conversion_and_temporary_support_removal(self):
        conversion = self.conversions[0]
        joints = [e for e in self.elements if e.cls == "continuity"
                  and (e.deck, e.attrs["unit"]) == (conversion["deck"], conversion["unit"])]
        before = self.at(conversion["cast"] - timedelta(days=1))
        cast = self.at(conversion["cast"])
        prior_conversion = self.at(conversion["conversion"] - timedelta(days=1))
        converted = self.at(conversion["conversion"])
        for element in joints:
            self.assertFalse(before["elements"][element.eid]["visible"])
            self.assertEqual(cast["elements"][element.eid]["phase"], "curing")
            self.assertEqual(prior_conversion["elements"][element.eid]["phase"], "curing")
            self.assertEqual(converted["elements"][element.eid]["phase"], "continuous")
        supports = [e for e in self.elements if e.cls == "temp_support" and e.deck == conversion["deck"]
                    and unit_of_span(self.plan[e.attrs["girder"]]["span"]) == conversion["unit"]]
        self.assertTrue(supports)
        for element in supports:
            self.assertTrue(cast["elements"][element.eid]["visible"])
            self.assertTrue(prior_conversion["elements"][element.eid]["visible"])
            self.assertEqual(converted["elements"][element.eid]["phase"], "removed")
            self.assertFalse(converted["elements"][element.eid]["visible"])

    def test_complete_example_plan_includes_all_follow_on_work(self):
        result = self.at(self.last)
        self.assertEqual(result["counts"]["girders_erected"], len(self.rows))
        self.assertEqual(result["counts"]["continuity_cast"], result["counts"]["continuity_total"])
        self.assertEqual(result["counts"]["units_converted"], len(self.conversions))
        self.assertEqual(result["counts"]["temporary_supports_active"], 0)
        self.assertEqual(result["counts"]["yard_in_production"], 0)
        self.assertEqual(result["counts"]["yard_in_storage"], 0)
        for state in result["elements"].values():
            if state["class"] in CP.FOLLOW_ON:
                self.assertTrue(state["visible"])
                self.assertTrue(state["scheduled"])
                self.assertEqual(state["phase"], "completed")
        self.assertEqual(result["counts"], self.at(self.last + timedelta(days=1))["counts"])

    def test_replaying_backward_is_history_independent_and_json_ready(self):
        before = self.at(self.first)
        self.at(self.last)
        self.assertEqual(self.at(self.first), before)
        self.assertEqual(json.loads(json.dumps(before)), before)
        self.assertEqual(len(before["elements"]), len(self.elements))

    def test_next_previous_skip_to_actual_events_and_clamp_at_plan_ends(self):
        events = R.event_days(self.rows)
        self.assertEqual(events, sorted(set(events)))
        self.assertEqual(R.adjacent_day(self.rows, events[0], 1), events[1])
        self.assertEqual(R.adjacent_day(self.rows, events[1], -1), events[0])
        self.assertEqual(R.adjacent_day(self.rows, events[0], -1), events[0])
        self.assertEqual(R.adjacent_day(self.rows, events[-1], 1), events[-1])
        self.assertIn(self.last, events)

    def test_dates_are_unambiguous_and_invalid_inputs_fail(self):
        self.assertEqual(R.parse_day("2027-02-02"), date(2027, 2, 2))
        self.assertEqual(R.parse_day(self.first), self.first)
        for value in ("20270202", "2027-02-30", "02/02/2027", "2027-2-2", datetime(2027, 2, 2), None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                R.parse_day(value)
        with self.assertRaises(ValueError):
            R.snapshot(self.elements, [], self.first)

    def test_hour_boundaries_differ_from_end_of_day(self):
        row = self.rows[0]
        start = CP.at(row["erect"], row["erect_hour"])
        before = self.at(start - timedelta(seconds=1))
        during = self.at(start)
        after = self.at(start + timedelta(hours=2))
        self.assertEqual(before["counts"]["girders_erected"], 0)
        self.assertEqual(during["elements"][row["girder"]]["phase"], "installing")
        self.assertEqual(during["counts"]["girders_installing"], 1)
        self.assertEqual(after["elements"][row["girder"]]["phase"], "erected")
        self.assertEqual(after["time_semantics"], "exact_time")

    def test_new_conversion_task_controls_removal_exactly(self):
        conversion = self.complete["conversions"][0]
        task = self.complete["by_id"]["C-L1-2"]
        before, after = self.at(task["finish"] - timedelta(seconds=1)), self.at(task["finish"])
        for eid in task["removes"]:
            self.assertTrue(before["elements"][eid]["visible"])
            self.assertFalse(after["elements"][eid]["visible"])
        # Original baseline conversion is still available for comparison,
        # but must never remove supports early in the expanded plan.
        original = S.conversions(self.rows)[0]["conversion"]
        self.assertLess(original, conversion["conversion"].date())
        self.assertTrue(self.at(original)["elements"][task["removes"][0]]["visible"])

    def test_casting_curing_and_completion_are_separate_events(self):
        task = next(t for t in self.complete["tasks"] if t["class"] == "wet_joint")
        eid = task["elements"][0]
        self.assertFalse(self.at(task["start"] - timedelta(seconds=1))["elements"][eid]["visible"])
        self.assertEqual(self.at(task["start"])["elements"][eid]["phase"], "constructing")
        self.assertEqual(self.at(task["work_finish"])["elements"][eid]["phase"], "curing")
        self.assertEqual(self.at(task["finish"])["elements"][eid]["phase"], "completed")


if __name__ == "__main__":
    unittest.main()
