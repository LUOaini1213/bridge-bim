"""Stage viewer regression, increment semantics and load/reaction balance."""
import json
import unittest
import copy
from bridge import stage_results as SR
from bridge.pipeline import compute
from bridge.numcmp import compare_json, compare_stage_json, stage_roundoff_summary


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
        # Exact first failures from Ubuntu Python 3.11 and 3.9 CI. These are
        # pointwise failures but tiny relative to the actual Mc vector norm.
        native = copy.deepcopy(self.data)
        linux = copy.deepcopy(native)
        for index, windows_value, linux_value in (
                (128, 2.008547859960828, 2.008547853753413),
                (86, 2.1927654116367705, 2.1927654092191915)):
            native["lines"][0]["moments"]["Mc"][index] = windows_value
            linux["lines"][0]["moments"]["Mc"][index] = linux_value
        self.assertFalse(compare_json(json.dumps(native), json.dumps(linux), float_atol=2e-9)[0])
        diagnostics = []
        self.assertTrue(compare_stage_json(json.dumps(native), json.dumps(linux), diagnostics=diagnostics)[0])
        self.assertEqual(len(diagnostics), 2)
        summary = stage_roundoff_summary(diagnostics)
        self.assertAlmostEqual(summary["groups"]["Mc"]["max_absolute"], 6.207415026437957e-9, delta=1e-15)
        self.assertFalse(summary["violations"])
        # The same known cancellation at an exact zero is accepted only in
        # that curve, never in coordinates or reactions with a zero baseline.
        native["lines"][0]["moments"]["Mc"][0] = 0.0
        linux = copy.deepcopy(native)
        linux["lines"][0]["moments"]["Mc"][0] = 6.207415026437957e-9
        self.assertTrue(compare_stage_json(json.dumps(native), json.dumps(linux))[0])
        for changed_field in ("coordinate", "reaction", "moment_tamper", "nonfinite", "integer", "bool", "id", "order", "key"):
            tampered = copy.deepcopy(native)
            line = tampered["lines"][0]
            if changed_field == "coordinate":
                native_zero = copy.deepcopy(native)
                native_zero["lines"][0]["points"][0][0] = 0.0
                tampered = copy.deepcopy(native_zero)
                tampered["lines"][0]["points"][0][0] = 6.207415026437957e-9
                self.assertFalse(compare_stage_json(json.dumps(native_zero), json.dumps(tampered))[0])
                continue
            if changed_field == "reaction":
                original = line["supports"][0]["Mc"]
                line["supports"][0]["Mc"] += 2 * max(2e-9, 1e-9 * max(1.0, abs(original)))
            elif changed_field == "moment_tamper":
                line["moments"]["Mc"][0] += 1e-4
                line["moments"]["M1"][0] += 1e-4
            elif changed_field == "nonfinite":
                line["moments"]["Mc"][0] = float("nan")
            elif changed_field == "integer":
                line["moments"]["Mc"][0] = 0
            elif changed_field == "bool":
                line["supports"][0]["temporary"] = 1
            elif changed_field == "id":
                line["id"] = "L-U9-G3"
            elif changed_field == "order":
                line["moments"]["Mc"].reverse()
            elif changed_field == "key":
                line["moments"]["unexpected"] = [0.0]
            self.assertFalse(compare_stage_json(json.dumps(native), json.dumps(tampered))[0], changed_field)
        # Diagnostics must traverse the entire vector, reporting both late
        # failures instead of hiding everything after the first mismatch.
        tampered = copy.deepcopy(native)
        for index in (0, -1):
            tampered["lines"][0]["moments"]["Mc"][index] += 1e-4
        diagnostics = []
        self.assertFalse(compare_stage_json(json.dumps(native), json.dumps(tampered), diagnostics=diagnostics)[0])
        self.assertEqual(len(stage_roundoff_summary(diagnostics)["violations"]), 2)
        for original, changed in ((native, dict(native, schema=2)), (dict(native, schema=2), dict(native, schema=2))):
            self.assertFalse(compare_stage_json(json.dumps(original), json.dumps(changed))[0])
        selected = SR.select(native, "Mc", native["lines"][0]["id"])
        changed = copy.deepcopy(selected)
        changed["moments"][0] += 6.207415026437957e-9
        self.assertTrue(compare_stage_json(json.dumps(selected), json.dumps(changed), selected=True)[0])
        changed["moments"][0] += 1e-4
        self.assertFalse(compare_stage_json(json.dumps(selected), json.dumps(changed), selected=True)[0])
        # Extrema are independently stored scalar fields, outside the only
        # permitted exception (the moment array itself).
        extrema_case = next(SR.select(self.data, "Mc", line["id"]) for line in self.data["lines"]
                            if 20.0 < max(line["moments"]["Mc"]) < 30.0)
        changed = copy.deepcopy(extrema_case)
        changed["maximum"] += 1e-7
        self.assertFalse(compare_stage_json(json.dumps(extrema_case), json.dumps(changed), selected=True)[0])


if __name__ == "__main__":
    unittest.main()
