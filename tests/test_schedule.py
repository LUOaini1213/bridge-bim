"""梁场预制、存梁、架梁与体系转换计划的不变量。只用标准库。

计划由 bridge.schedule 按规则排出；这里不信排出来的汇总数，逐片、逐日重数一遍。
"""
import os
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import config as C, pipeline as P, schedule as S, yard as Y   # noqa: E402
from bridge.model import support_kind, unit_bounds                         # noqa: E402


class Plan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.idle = S.plan()
        cls.sm = S.summarize(cls.rows, cls.idle)

    def test_order_left_deck_then_right_span_by_span(self):
        seq = [(r["deck"], r["span"], r["pos"]) for r in self.rows]
        self.assertEqual(seq, sorted(seq, key=lambda t: ("LR".index(t[0]), t[1], t[2])))
        self.assertEqual(len(seq), 120)

    def test_erection_never_goes_backwards_and_respects_age(self):
        for a, b in zip(self.rows, self.rows[1:]):
            self.assertLessEqual(a["erect"], b["erect"])
        for r in self.rows:
            self.assertGreaterEqual((r["erect"] - r["cast"]).days, C.MIN_AGE_DAYS)
            self.assertGreaterEqual(r["erect"], r["to_storage"])

    def test_daily_erector_hours(self):
        """每天架梁 + 过孔的工时不超过 DAY_HOURS（按规则重算，不看排程器内部状态）。"""
        per_day = {}
        for r in self.rows:
            h = C.ERECT_HOURS
            per_day[r["erect"]] = per_day.get(r["erect"], 0.0) + h
        self.assertLessEqual(max(per_day.values()), C.DAY_HOURS)
        self.assertLessEqual(max(sum(1 for r in self.rows if r["erect"] == d) for d in per_day),
                             int(C.DAY_HOURS // C.ERECT_HOURS))

    def test_transfer_between_decks(self):
        last_l = max(r["erect"] for r in self.rows if r["deck"] == "L")
        first_r = min(r["erect"] for r in self.rows if r["deck"] == "R")
        self.assertGreaterEqual((first_r - last_l).days, 1 + C.TRANSFER_DAYS)

    def test_casting_uses_each_bed_once_per_cycle(self):
        by_bed = {}
        for r in self.rows:
            by_bed.setdefault(r["bed"], []).append(r["cast"])
        self.assertEqual(len(by_bed), C.N_BEDS)
        for casts in by_bed.values():
            for a, b in zip(casts, casts[1:]):
                self.assertGreaterEqual((b - a).days, C.BED_CYCLE_DAYS)

    def test_machine_bound_and_zero_wait(self):
        self.assertEqual(S.machine_bound_days(), 50)
        self.assertEqual(self.sm["erect_days"], 50)
        self.assertEqual(self.sm["wait_days"], 0)

    def test_storage_curve_recounted(self):
        curve = dict(S.storage_curve(self.rows))
        d = min(curve)
        while d <= max(curve):
            n = sum(1 for r in self.rows if r["to_storage"] <= d < r["erect"])
            self.assertEqual(curve[d], n)
            d += timedelta(days=1)
        self.assertLessEqual(max(curve.values()), Y.capacity())
        self.assertEqual(max(curve.values()), self.sm["storage_peak"])

    def test_yard_snapshot_on_peak_day(self):
        beds, stored = S.yard_state(self.rows, self.sm["storage_peak_day"])
        self.assertLessEqual(len(beds), C.N_BEDS)
        self.assertEqual(len({b for b, _ in beds}), len(beds))                   # 一个台座上只有一片梁
        self.assertEqual(len(stored), self.sm["storage_peak"])
        self.assertLess(max(slot for slot, _, _ in stored), C.STORAGE_POSITIONS)
        self.assertLessEqual(max(layer for _, layer, _ in stored), C.STORAGE_LAYERS - 1)


class Conversion(unittest.TestCase):
    def test_one_conversion_per_deck_and_unit(self):
        rows, _ = S.plan()
        convs = S.conversions(rows)
        self.assertEqual(len(convs), len(C.DECKS) * len(C.UNITS))
        for m in convs:
            a, b = unit_bounds()[m["unit"] - 1]
            last = max(r["erect"] for r in rows if r["deck"] == m["deck"] and a < r["span"] <= b)
            self.assertEqual(m["erected"], last)
            self.assertEqual(m["cast"], last + timedelta(days=C.CONT_CAST_LAG_DAYS))
            self.assertEqual(m["conversion"], m["cast"] + timedelta(days=C.CONT_CURE_DAYS))
            self.assertEqual([int(p[1:]) for p in m["piers"]], [k for k in range(a + 1, b) if support_kind(k) == "C"])

    def test_last_conversion_date(self):
        rows, idle = S.plan()
        self.assertEqual(S.summarize(rows, idle)["conversion_last"].isoformat(), "2027-02-02")


class Sensitivity(unittest.TestCase):
    def test_configured_plan_is_the_minimal_zero_wait_feasible_one(self):
        rows = P.sensitivity_rows()
        best = P.minimal_beds(rows)
        self.assertEqual((best["beds"], best["lead_days"]), (C.N_BEDS, (C.ERECT_START - C.YARD_START).days))

    def test_fewer_beds_at_the_same_lead_make_the_erector_wait(self):
        lead = (C.ERECT_START - C.YARD_START).days
        rows = {(r["beds"], r["lead_days"]): r for r in P.sensitivity_rows()}
        for nb in P.BEDS_SWEEP:
            if nb < C.N_BEDS:
                self.assertGreater(rows[(nb, lead)]["wait_days"], 0, nb)

    def test_earlier_start_overflows_storage(self):
        """同样 16 个台座，梁场提前 28 天开工：架梁不等梁了，但存梁峰值超出 40 片容量。"""
        rows = {(r["beds"], r["lead_days"]): r for r in P.sensitivity_rows()}
        r = rows[(C.N_BEDS, 28)]
        self.assertEqual(r["wait_days"], 0)
        self.assertGreater(r["storage_peak"], Y.capacity())
        self.assertEqual(r["feasible"], "no")


if __name__ == "__main__":
    unittest.main()
