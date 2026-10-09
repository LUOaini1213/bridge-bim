"""Pure saved-report counterexamples; native geometry fixtures run in Rhino."""
import copy
import unittest
from scripts.check_spatial_quality import KINDS, validate_clearance_summary, validate_boundary_checks


def event(threshold=5.0):
    return {"kind": "at_clearance" if threshold == 5 else "below_clearance",
            "required_clearance_mm": threshold, "actual_gap_mm": 5.0,
            "point_mm": [12.5, 5.0, 5.0], "mesh_witness_available": False,
            "point_source": "proved_box_closest_midpoint",
            "mesh_contact_search": {"tolerance_mm": 0.01, "hit": False},
            "distance_evidence": {"method": "proved_axis_aligned_box_gap", "axis_box_proof": [True, True],
                                  "a_bounds_mm": [[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]],
                                  "b_bounds_mm": [[15.0, 0.0, 0.0], [25.0, 10.0, 10.0]],
                                  "comparison_epsilon_mm": 1e-7}}


def report(item):
    counts = {kind: int(item["kind"] == kind) for kind in KINDS}
    blocking = int(item["kind"] != "at_clearance")
    return {"ok": True, "clearance_ok": not blocking, "clearance_events": [item],
            "clearance_mm": item["required_clearance_mm"], "clearance_comparison_epsilon_mm": 1e-7,
            "clearance_counts": counts, "blocking_clearance_events": blocking}


def candidate(threshold=3.0, witness=True):
    item = {"kind": "threshold_candidate", "required_clearance_mm": threshold,
            "point_mm": [12.5, 5.0, 5.0], "mesh_witness_available": witness,
            "mesh_contact_search": {"tolerance_mm": 0.01, "hit": False}, "review_required": True,
            "distance_status": "mesh threshold candidate; exact model distance unverified",
            "clearance_mesh_proof": {"proofs": [{"ok": True}, {"ok": witness}],
                                     "search_distance_mm": threshold + (2e-6 if witness else 0.0),
                                     "boundary_error_budget_mm": 2e-6}}
    if witness:
        item["witness_radius_mm"] = threshold / 2
    else:
        item["point_source"] = "unverified_bbox_candidate_midpoint"
    return item


def fixtures():
    diagnostics = []
    for threshold, expected in ((6, "below_clearance"), (5, "at_clearance"), (4, None)):
        item = event(threshold)
        proof = item["distance_evidence"]
        diagnostics.append({"clearance_mm": threshold, "expected_kind": expected, "aggregate_clearance_ok": threshold <= 5,
                            "bbox_gap_mm": 5.0, "clashes": [], "unresolved": [],
                            "clearance_events": [] if expected is None else [item],
                            "parts": [{"axis_box_proof": True, "bbox_min_mm": bounds[0], "bbox_max_mm": bounds[1]}
                                      for bounds in (proof["a_bounds_mm"], proof["b_bounds_mm"])]})
    narrow = event(10)
    narrow["actual_gap_mm"] = 9.999
    narrow["distance_evidence"]["b_bounds_mm"] = [[19.999, 0.0, 0.0], [29.999, 10.0, 10.0]]
    extra = lambda threshold, item: {"clearance_mm": threshold, "aggregate_clearance_ok": False,
                                     "clashes": [], "unresolved": [], "clearance_events": [item]}
    return {"ok": True, "checks": list(range(6)), "boundary_checks": {
        "ok": True, "model_gap_mm": 5.0, "thresholds_mm": [6, 5, 4], "diagnostics": diagnostics,
        "subthreshold_check": extra(10, narrow), "nonbox_candidate_check": extra(3, candidate()),
        "uncertified_nohit_check": extra(1, candidate(1, False))}}


class SpatialReport(unittest.TestCase):
    def test_exact_boundary_is_not_deficient_but_actual_below_is(self):
        boundary = report(event())
        validate_clearance_summary(boundary)
        self.assertTrue(boundary["clearance_ok"])
        below = report(event(6))
        validate_clearance_summary(below)
        self.assertFalse(below["clearance_ok"])
        wrong = copy.deepcopy(boundary)
        wrong["clearance_ok"] = False
        with self.assertRaises(AssertionError):
            validate_clearance_summary(wrong)

    def test_forged_distance_proof_or_classification_is_rejected(self):
        for mutation in ("gap", "proof", "bounds", "epsilon", "kind", "nonfinite", "false_witness"):
            q = report(event())
            item = q["clearance_events"][0]
            if mutation == "gap":
                item["actual_gap_mm"] = 4.9
            elif mutation == "proof":
                item["distance_evidence"]["axis_box_proof"] = [True, 1]
            elif mutation == "bounds":
                item["distance_evidence"]["b_bounds_mm"][0][0] = 14.9
            elif mutation == "epsilon":
                item["distance_evidence"]["comparison_epsilon_mm"] = 0.01
            elif mutation == "kind":
                item["kind"] = "below_clearance"
            elif mutation == "nonfinite":
                item["point_mm"][0] = float("nan")
            elif mutation == "false_witness":
                item["witness_radius_mm"] = 0.0
            with self.subTest(mutation=mutation):
                with self.assertRaises(AssertionError):
                    validate_clearance_summary(q)

    def test_mesh_threshold_candidate_cannot_claim_an_exact_distance(self):
        item = candidate(5.0)
        q = report(item)
        validate_clearance_summary(q)
        self.assertFalse(q["clearance_ok"])
        for field, value in (("actual_gap_mm", 5.0), ("distance_evidence", event()["distance_evidence"]),
                             ("review_required", False)):
            wrong = copy.deepcopy(q)
            wrong["clearance_events"][0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(AssertionError):
                    validate_clearance_summary(wrong)
        nohit = report(candidate(1.0, False))
        validate_clearance_summary(nohit)
        self.assertFalse(nohit["clearance_ok"])
        nohit["clearance_events"][0]["clearance_mesh_proof"]["proofs"][1]["ok"] = True
        with self.assertRaises(AssertionError):
            validate_clearance_summary(nohit)

    def test_counts_and_all_three_boundary_fixture_results_are_required(self):
        valid = fixtures()
        validate_boundary_checks(valid)
        for mutation in ("missing", "model_gap", "aggregate", "classification", "dropped_case", "missing_subthreshold", "missing_nohit"):
            wrong = copy.deepcopy(valid)
            boundary = wrong["boundary_checks"]
            if mutation == "missing":
                del wrong["boundary_checks"]
            elif mutation == "model_gap":
                boundary["model_gap_mm"] = 0
            elif mutation == "aggregate":
                boundary["diagnostics"][1]["aggregate_clearance_ok"] = False
            elif mutation == "classification":
                boundary["diagnostics"][1]["clearance_events"][0]["kind"] = "below_clearance"
            elif mutation == "dropped_case":
                boundary["diagnostics"].pop()
            elif mutation == "missing_subthreshold":
                del boundary["subthreshold_check"]
            elif mutation == "missing_nohit":
                del boundary["uncertified_nohit_check"]
            with self.subTest(mutation=mutation):
                with self.assertRaises((AssertionError, KeyError)):
                    validate_boundary_checks(wrong)
        for mutation in ("counts", "blocking"):
            wrong = report(event())
            if mutation == "counts":
                wrong["clearance_counts"]["at_clearance"] = 0
            else:
                wrong["blocking_clearance_events"] = 1
            with self.assertRaises(AssertionError):
                validate_clearance_summary(wrong)
