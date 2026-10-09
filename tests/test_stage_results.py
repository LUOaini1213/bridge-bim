"""Stage viewer regression, increment semantics and load/reaction balance."""
import json
import unittest
from bridge import stage_results as SR
from bridge.pipeline import compute
from bridge.numcmp import compare_json


class StageResults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = SR.build(compute())

    def test_thirty_lines_have_actual_unchanged_case_values(self):
        self.assertEqual(len(self.data["lines"]), 30)
        selected = SR.select(self.data, "M1", "L-U2-G3")
        # Regression baseline predates the extended example plan.
        self.assertAlmostEqual(selected["maximum"], 2671.92291557495, places=7)
        for line in self.data["lines"]:
            for a, b, c, total in zip(*(line["moments"][k] for k in ("M1", "Mc", "M2", "MG"))):
                self.assertAlmostEqual(a + b + c, total, places=9)

    def test_reaction_balance_and_temporary_removal_are_not_cumulative(self):
        for line in self.data["lines"]:
            self.assertAlmostEqual(sum(s["M1"] for s in line["supports"]), line["loads"]["G1"], places=6)
            self.assertAlmostEqual(sum(s["M2"] for s in line["supports"]), line["loads"]["G2"], places=6)
            released = -sum(s["removal_action"] for s in line["supports"] if s["temporary"])
            self.assertAlmostEqual(sum(s["Mc"] for s in line["supports"]), released, places=6)
            self.assertAlmostEqual(sum(s["MG"] for s in line["supports"]), sum(line["loads"].values()), places=6)

    def test_stage_supports_and_segment_discontinuities_match_the_structural_system(self):
        first = SR.select(self.data, "M1")
        converted = SR.select(self.data, "Mc")
        second = SR.select(self.data, "M2")
        self.assertEqual(len(first["segments"]), 4)
        self.assertEqual(len(converted["segments"]), 1)
        self.assertTrue(all(s["active"] for s in first["supports"] if s["temporary"]))
        self.assertTrue(all(not s["active"] for s in first["supports"] if s["kind"] == "cont"))
        self.assertTrue(all(not s["active"] for s in converted["supports"] if s["temporary"]))
        self.assertTrue(all(s["active"] for s in second["supports"] if not s["temporary"]))
        self.assertIn("增量", converted["meaning"])
        self.assertIn("增量", second["meaning"])
        self.assertNotEqual(converted["moments"], SR.select(self.data, "MG")["moments"])

    def test_stage_data_is_json_ready_and_invalid_selection_fails(self):
        self.assertEqual(json.loads(json.dumps(self.data)), self.data)
        with self.assertRaises(ValueError):
            SR.select(self.data, "today")
        with self.assertRaises(ValueError):
            SR.select(self.data, "M1", "L-U9-G3")

    def test_unrounded_json_accepts_roundoff_but_rejects_value_or_schema_tampering(self):
        reference = {"moment": 269.30016721596263, "node": 3, "eid": "G-L05-3", "active": True}
        other = dict(reference, moment=269.3001672159772)
        self.assertTrue(compare_json(json.dumps(reference), json.dumps(other))[0])
        for changes in ({"moment": reference["moment"] + 1e-4}, {"node": 4}, {"node": 3.0},
                        {"active": 1}, {"eid": "G-L05-4"}, {"moment": float("nan")}):
            self.assertFalse(compare_json(json.dumps(reference), json.dumps(dict(reference, **changes)))[0])
        self.assertFalse(compare_json(json.dumps(reference), json.dumps({**reference, "extra": 0}))[0])
        self.assertFalse(compare_json("[1, 2]", "[2, 1]")[0])
        self.assertTrue(compare_json('{"Mc": 0.0}', '{"Mc": 1.99e-9}', float_atol=2e-9)[0])
        self.assertFalse(compare_json('{"Mc": 0.0}', '{"Mc": 2.01e-9}', float_atol=2e-9)[0])


if __name__ == "__main__":
    unittest.main()
