"""Configuration, resource pools and approval gates; no Rhino dependency."""
import json
import os
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge import construction as CP, construction_input as CI, replay as R, schedule as S
from bridge.pipeline import compute
from bridge.model import build


class ConstructionInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = CI.load(CI.DEFAULT_PATH)
        cls.elements, _ = build()
        cls.rows, _ = S.plan(configuration=cls.settings)

    def config(self, mode="simulation"):
        value = deepcopy(self.settings)
        value["mode"] = mode
        return value

    def plan(self, settings):
        rows, _ = S.plan(configuration=settings)
        return CP.build(self.elements, rows, configuration=settings), rows

    def record(self, ident, gate, value):
        return {"task_id": ident, "gate": gate, "approved_at": value,
                "reference": "TEST-APPROVAL-001 (fixture, not field data)", "source": "unit test fixture"}

    def approve_yard(self, config, value):
        config['releases'] += [self.record('Y%03d' % i,'fabrication_acceptance',value) for i in range(1,121)]

    def test_default_compatibility_and_no_configuration_leak(self):
        original, rows = self.plan(self.config())
        self.assertEqual(original["finish"], datetime(2027, 2, 20, 16))
        self.assertEqual(original["effective_finish"], original["finish"])
        self.assertEqual(len(original["tasks"]), 344)
        self.assertTrue(all(t["start"] == t["effective_start"] for t in original["tasks"]))
        changed = self.config(); changed["calendar"]["start_hour"] = 7
        self.plan(changed)
        restored, restored_rows = self.plan(self.config())
        self.assertEqual(restored_rows, rows)
        self.assertEqual(CP.serializable(restored), CP.serializable(original))

    def test_two_real_crews_work_in_parallel_without_any_crew_overlap(self):
        one = self.config(); one["durations"]["diaphragm"]["work_hours"] = 100
        single, _ = self.plan(one)
        two = deepcopy(one); two["crews"]["L"]["diaphragm"] = 2
        two["crews"]["R"]["diaphragm"] = 2
        parallel, _ = self.plan(two)
        tasks = [t for t in parallel["tasks"] if t["deck"] == "L" and t["class"] == "diaphragm"]
        self.assertEqual({t["crew_id"] for t in tasks}, {"L:diaphragm:1", "L:diaphragm:2"})
        self.assertTrue(any(a["start"] < b["work_finish"] and b["start"] < a["work_finish"] for i, a in enumerate(tasks) for b in tasks[i+1:]))
        self.assertLess(parallel["finish"], single["finish"])
        for field, start, finish in (("crew_id", "start", "work_finish"), ("effective_crew_id", "effective_start", "effective_work_finish")):
            groups = defaultdict(list)
            for t in parallel["tasks"]:
                if t[field]: groups[t[field]].append(t)
            for group in groups.values():
                group.sort(key=lambda t: t[start])
                for a, b in zip(group, group[1:]): self.assertLessEqual(a[finish], b[start])

    def test_calendar_weekends_holidays_exceptions_and_elapsed_cure(self):
        config = self.config()
        config["calendar"].update(weekdays=[0,1,2,3,4], rest_dates=["2026-12-07"], work_dates=["2026-12-06"])
        cal = CI.Calendar(config["calendar"])
        self.assertEqual(cal.end(datetime(2026,12,4,16), 12), datetime(2026,12,6,18))
        self.assertEqual(cal.start(datetime(2026,12,7,8)), datetime(2026,12,8,8))
        plan, rows = self.plan(config)
        self.assertTrue(all(cal.working(r["cast"]) and cal.working(r["erect"]) for r in rows))
        for t in plan["tasks"]:
            self.assertTrue(cal.working(t["start"].date()))
            if t["class"] != "precast":
                self.assertEqual(t["finish"] - t["work_finish"], timedelta(hours=t["cure_hours"]))
        by_bed = defaultdict(list)
        for r in rows: by_bed[r["bed"]].append(r)
        for bed in by_bed.values():
            for a,b in zip(bed,bed[1:]): self.assertLessEqual(a["to_storage"], b["cast"])

    def test_seven_am_calendar_is_used_by_yard_counts_and_erection(self):
        config = self.config(); config["calendar"]["start_hour"] = 7
        plan, rows = self.plan(config)
        state = R.snapshot(self.elements, rows, "2026-11-16T07:00", plan)
        self.assertGreater(state["counts"]["yard_in_production"], 0)
        state = R.snapshot(self.elements, rows, "2026-12-07T07:00", plan)
        self.assertEqual(state["counts"]["girders_installing"], 1)

    def test_missing_project_approvals_never_auto_release_even_a_year_later(self):
        plan, rows = self.plan(self.config("project"))
        state = R.snapshot(self.elements, rows, plan["finish"] + timedelta(days=365), plan)
        self.assertGreater(state["counts"]["tasks_held"], 0)
        self.assertEqual(state["counts"]["tasks_completed"], 0)
        self.assertEqual(plan['by_id']['Y120']['status'],'HELD')
        self.assertEqual(state["counts"]["girders_erected"], 0)
        self.assertEqual(state["counts"]["units_converted"], 0)
        self.assertEqual(state["counts"]["follow_on_completed"], 0)
        self.assertIsNone(plan["effective_finish"])
        self.assertEqual(plan["by_id"]["C-L1-2"]["status"], "HELD")

    def test_conversion_only_approval_cannot_bypass_missing_predecessors(self):
        config = self.config("project")
        config["releases"] = [self.record("C-L1-2", g, "2027-07-01T08:00") for g in CI.GATES["conversion"]]
        plan, rows = self.plan(config)
        state = R.snapshot(self.elements, rows, "2028-07-01", plan)
        self.assertEqual(state["counts"]["units_converted"], 0)
        self.assertIsNone(plan["by_id"]["C-L1-2"]["effective_start"])
        self.assertTrue(any("predecessor" in v for v in plan["by_id"]["C-L1-2"]["hold_reasons"]))

    def test_first_approved_erection_unlocks_only_at_approval_time(self):
        config = self.config("project")
        config["releases"] = [self.record("E001", g, "2027-07-01T08:00") for g in CI.GATES["girder"]]
        config['releases'].append(self.record('Y001','fabrication_acceptance','2027-07-01T08:00'))
        plan, rows = self.plan(config)
        before = R.snapshot(self.elements, rows, "2027-07-01T07:59", plan)
        after = R.snapshot(self.elements, rows, "2027-07-01T08:00", plan)
        done = R.snapshot(self.elements, rows, "2027-07-01T10:00", plan)
        self.assertFalse(before["elements"]["G-L01-1"]["visible"])
        self.assertEqual(after["counts"]["girders_installing"], 1)
        self.assertEqual(done["counts"]["girders_erected"], 1)
        self.assertEqual(done["counts"]["units_converted"], 0)
        self.assertGreater(done["counts"]["temporary_supports_active"], 0)
        self.assertIsNone(plan["effective_finish"])
        self.assertGreaterEqual(R.timeline_bounds(plan)[1], datetime(2027,7,1,10))

    def test_late_approval_preserves_launch_and_deck_transfer_machine_time(self):
        config = self.config("project")
        config["releases"] = [self.record("E%03d" % i, g, "2027-07-01T08:00") for i in range(1,121) for g in CI.GATES["girder"]]
        self.approve_yard(config,'2027-07-01T08:00')
        plan, rows = self.plan(config)
        t, cal = plan["by_id"], CI.Calendar(config["calendar"])
        self.assertEqual(t["E006"]["effective_start"], datetime(2027,7,2,14))
        self.assertGreaterEqual(t["E061"]["effective_start"], cal.at(t["E060"]["release_at"].date() + timedelta(days=5)))
        self.assertEqual(t["E006"]["machine_launch_before_hours"], 6)
        self.assertEqual(t["E061"]["machine_transfer_before_days"], 4)
        state = R.snapshot(self.elements, rows, "2028-07-01", plan)
        self.assertEqual(state["counts"]["girders_erected"], 120)
        self.assertEqual(state["counts"]["units_converted"], 0)
        self.assertEqual(state["counts"]["temporary_supports_active"], sum(e.cls == "temp_support" for e in self.elements))

    def test_erection_cannot_bypass_yard_acceptance_and_late_yard_shifts_work(self):
        config=self.config('project')
        config['releases']=[self.record('E001',g,'2027-07-01T08:00') for g in CI.GATES['girder']]
        plan, rows=self.plan(config)
        self.assertIsNone(plan['by_id']['E001']['effective_start'])
        config['releases'].append(self.record('Y001','fabrication_acceptance','2027-07-05T08:00'))
        plan, rows=self.plan(config)
        self.assertEqual(plan['by_id']['E001']['effective_start'],datetime(2027,7,5,8))
        state=R.snapshot(self.elements,rows,'2027-07-01T10:00',plan)
        self.assertEqual(state['counts']['girders_erected'],0)

    def test_late_erection_approval_near_shift_end_stays_unsplit(self):
        for hour, expected in [(16,datetime(2027,7,2,16)),(17,datetime(2027,7,6,8))]:
            config=self.config('project')
            config['calendar'].update(weekdays=[0,1,2,3,4],rest_dates=['2027-07-05'])
            config['releases']=[self.record('E001',g,'2027-07-02T%d:00' % hour) for g in CI.GATES['girder']]
            config['releases'].append(self.record('Y001','fabrication_acceptance','2027-07-01T08:00'))
            plan,_=self.plan(config); t=plan['by_id']['E001']
            self.assertEqual(t['effective_start'],expected)
            self.assertEqual(t['effective_work_finish']-t['effective_start'],timedelta(hours=2))

    def test_all_approvals_propagate_effective_work_and_conversion_support_removal(self):
        config = self.config("project")
        template, _ = self.plan(self.config())
        config["releases"] = [self.record(t["id"], g, "2027-07-01T08:00") for t in template["tasks"] for g in CI.GATES.get(t["class"], ())]
        plan, rows = self.plan(config)
        self.assertGreater(plan["effective_finish"], plan["finish"])
        for t in plan["tasks"]:
            for p in t["predecessors"]: self.assertGreaterEqual(t["effective_start"], plan["by_id"][p]["release_at"])
        conversion = plan["by_id"]["C-L1-2"]
        before = R.snapshot(self.elements, rows, conversion["release_at"] - timedelta(seconds=1), plan)
        after = R.snapshot(self.elements, rows, conversion["release_at"], plan)
        self.assertEqual(after["counts"]["units_converted"], before["counts"]["units_converted"] + 1)
        self.assertLess(after["counts"]["temporary_supports_active"], before["counts"]["temporary_supports_active"])
        state = R.snapshot(self.elements, rows, "2027-07-01T08:00", plan)
        self.assertEqual(state["counts"]["follow_on_completed"], 0)

    def test_invalid_inputs_fail_instead_of_falling_back(self):
        mutants = []
        for path, value in [(('unexpected',),1), (('schema',),True), (('calendar','weekdays'),[]),
                            (('calendar','weekdays'),[True]), (('calendar','day_hours'),float('nan')),
                            (('durations','pavement','work_hours'),True), (('crews','L','pavement'),True),
                            (('crews','L','pavement'),0), (('baseline','n_beds'),0), (('baseline','min_age_days'),1)]:
            c = self.config(); node=c
            for key in path[:-1]: node=node[key]
            node[path[-1]]=value; mutants.append(c)
        c=self.config(); c['calendar']['rest_dates']=['2026-12-07']; c['calendar']['work_dates']=['2026-12-07']; mutants.append(c)
        for config in mutants:
            with self.subTest(config=config), self.assertRaises(ValueError): CI.validate(config)
        with self.assertRaises(ValueError): S.plan(n_beds=0)
        with self.assertRaises(FileNotFoundError): CI.load("missing-explicit-config.json")

    def test_invalid_release_records_unknown_task_gate_timezone_and_empty_reference(self):
        base=self.config('project')
        for key,value in [('reference',' '),('source',''),('approved_at','2027-01-01T08:00+08:00'),('task_id','E999'),('gate','made_up')]:
            config=deepcopy(base); rec=self.record('E001','erection_strength','2027-07-01T08:00'); rec[key]=value; config['releases']=[rec]
            with self.subTest(key=key), self.assertRaises(ValueError): self.plan(config)
        config=deepcopy(base); rec=self.record('E001','erection_strength','2027-07-01T08:00'); config['releases']=[rec,dict(rec)]
        with self.assertRaises(ValueError): self.plan(config)

    def test_environment_entry_and_saved_input_are_same_source_without_silent_default(self):
        config=self.config('project'); config['calendar']['start_hour']=7
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'project.json'; path.write_text(json.dumps(config),encoding='utf-8')
            with patch.dict(os.environ, BRIDGE_CONSTRUCTION_CONFIG=str(path)):
                result=compute()
                state=R.snapshot(result['els'],result['rows'],'2028-01-01',result['construction'])
                self.assertEqual(state['mode'],'project')
                self.assertEqual(state['counts']['units_converted'],0)
                self.assertEqual(CI.fingerprint(config),state['configuration_sha256'])
                self.assertEqual(result['construction_config'],config)
            restored=compute(configuration=state['construction_config'])
            again=R.snapshot(restored['els'],restored['rows'],'2028-01-01',restored['construction'])
            self.assertEqual(again,state)


if __name__ == '__main__': unittest.main()
