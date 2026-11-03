"""Bearing ages: the structural results use the original baseline, the IFC/Rhino 4D uses the full example plan.

bridge.structure.bearing_ages() takes the girder ages from the original yard/erection baseline
(bridge/schedule.py). The IFC IfcWorkSchedule and the Rhino timeline use the full example plan
(bridge/construction.py), which casts every continuity joint later because wet joints and flange
pours now come first. These tests pin that, for the default input, the baseline is the conservative
choice: the full plan never makes a girder younger when a bearing starts to carry it, and redoing
the bearing design with the plan's ages never increases a shortening or a shear utilisation.
Standard library only.
"""
import unittest

from bridge import model as MD, structure as ST
from bridge.pipeline import compute, structure


def plan_bearing_ages(r):
    """Same definition as structure.bearing_ages(), read from the full example plan instead of the baseline.

    Expansion end = youngest girder of the unit at its erection; continuous pier = youngest girder of
    the unit on the day its continuity joint is cast. Ages in whole days, as in the baseline."""
    tasks = r["construction"]["tasks"]
    by_id = {t["id"]: t for t in tasks}
    cast, erect = {}, {}
    for t in tasks:
        if t["class"] == "girder":
            (girder,) = t["elements"]
            (pre,) = [p for p in t["predecessors"] if by_id[p]["class"] == "precast"]
            cast[girder], erect[girder] = by_id[pre]["start"].date(), t["start"].date()
    cont = {(t["deck"], t["unit"]): t["start"].date() for t in tasks if t["class"] == "continuity"}
    out = {}
    for row in r["rows"]:
        for u, (a, b) in enumerate(MD.unit_bounds(), 1):
            if a < row["span"] <= b:
                g, key = row["girder"], (row["deck"], u)
                end, con = (erect[g] - cast[g]).days, (cont[key] - cast[g]).days
                out[key + ("end",)] = min(out.get(key + ("end",), end), end)
                out[key + ("cont",)] = min(out.get(key + ("cont",), con), con)
    return out


class PlanAges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = compute()
        cls.base = ST.bearing_ages(cls.r["rows"])
        cls.plan = plan_bearing_ages(cls.r)

    def test_same_keys_as_the_baseline(self):
        self.assertEqual(sorted(self.plan), sorted(self.base))

    def test_plan_keeps_erection_ages_and_casts_continuity_later(self):
        for key, age in self.base.items():
            with self.subTest(key=key):
                if key[2] == "end":
                    self.assertEqual(self.plan[key], age)
                else:
                    self.assertGreater(self.plan[key], age)

    def test_baseline_bearing_movements_are_not_smaller_than_with_plan_ages(self):
        s = structure(self.r)
        base = {x["id"]: x for x in s["bearing_design"]["rows"]}
        alt = {x["id"]: x for x in ST.bearing_design(s, self.r["els"], {"ages": self.plan})["rows"]}
        self.assertEqual(sorted(alt), sorted(base))
        for bid, x in alt.items():
            b = base[bid]
            with self.subTest(bearing=bid):
                self.assertLessEqual(x["d_contract"], b["d_contract"] + 1e-12)
                if not x["sliding"]:
                    self.assertLessEqual(x["util_shear"], b["util_shear"] + 1e-12)
        # The continuous piers really do gain: the comparison is not vacuous.
        self.assertTrue(any(alt[i]["d_contract"] < base[i]["d_contract"] - 1e-6
                            for i in alt if alt[i]["kind"] == "cont"))

    def test_counterexample_younger_plan_would_be_caught(self):
        """If the plan cast a continuity joint earlier than the baseline, the design would get worse."""
        s = structure(self.r)
        younger = {k: (max(1, v - 10) if k[2] == "cont" else v) for k, v in self.base.items()}
        base = {x["id"]: x for x in s["bearing_design"]["rows"]}
        alt = {x["id"]: x for x in ST.bearing_design(s, self.r["els"], {"ages": younger})["rows"]}
        self.assertTrue(any(alt[i]["d_contract"] > base[i]["d_contract"] + 1e-6 for i in alt))


if __name__ == "__main__":
    unittest.main()
