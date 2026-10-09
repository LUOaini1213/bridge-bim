"""Route priority versus inserting work into an earlier available crew slot."""
from collections import defaultdict
from datetime import datetime
import unittest
from bridge import construction_input as CI, construction as CP, model as M, schedule as S
from bridge.crew_schedule import CrewPool


class CrewStrategy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elements, _ = M.build()

    def delayed(self, strategy, calendar=None, crews=1):
        cfg = CI.load()
        cfg['scheduling']['crew_strategy'] = strategy
        if calendar:
            cfg['calendar'].update(calendar)
        cfg['crews']['L']['wet_joint'] = crews
        rows, _ = S.plan(configuration=cfg)
        base = CP.build(self.elements, rows, configuration=cfg)
        cfg['mode'] = 'project'
        cfg['releases'] = [dict(task_id=t['id'], gate=g, approved_at='2026-11-10T08:00',
                               reference='UNIT-TEST', source='synthetic unit-test approval')
                           for t in base['tasks'] for g in CI.GATES.get(t['class'], ())]
        for rec in cfg['releases']:
            if rec['task_id'] == 'F-diaphragm-L01':
                rec['approved_at'] = '2027-07-01T08:00'
        return CP.build(self.elements, rows, configuration=cfg)

    def check_no_overlap_or_bypass(self, plan):
        reservations = defaultdict(list)
        cal = CI.Calendar(plan['configuration']['calendar'])
        for t in plan['tasks']:
            if t['effective_start'] is None:
                continue
            self.assertTrue(cal.working(t['effective_start'].date()))
            for p in t['predecessors']:
                self.assertIsNotNone(plan['by_id'][p]['release_at'])
                self.assertGreaterEqual(t['effective_start'], plan['by_id'][p]['release_at'])
            if t['effective_crew_id']:
                reservations[t['effective_crew_id']].append((t['effective_start'],t['effective_work_finish']))
        for intervals in reservations.values():
            for a,b in zip(sorted(intervals),sorted(intervals)[1:]):
                self.assertLessEqual(a[1],b[0])

    def test_fixed_route_keeps_late_span_priority_and_explains_wait(self):
        plan = self.delayed('fixed_route')
        a,b = [plan['by_id']['F-wet_joint-L%02d'%s] for s in (1,2)]
        self.assertEqual(a['effective_start'],datetime(2027,7,1,8))
        self.assertEqual(b['effective_start'],datetime(2027,7,1,12))
        self.assertIn('fixed_route: wait for F-wet_joint-L01',b['effective_wait_reasons'])
        self.check_no_overlap_or_bypass(plan)

    def test_gap_policy_inserts_ready_second_span_before_late_first(self):
        plan = self.delayed('earliest_gap')
        a,b = [plan['by_id']['F-wet_joint-L%02d'%s] for s in (1,2)]
        self.assertEqual(a['effective_start'],datetime(2027,7,1,8))
        self.assertEqual(b['effective_start'],datetime(2026,12,12,8))
        self.assertEqual(b['effective_work_finish'],datetime(2026,12,12,12))
        self.assertLess(b['effective_work_finish'],a['effective_start'])
        self.check_no_overlap_or_bypass(plan)

    def test_gap_allocation_respects_rest_days_and_multiple_crews(self):
        plan = self.delayed('earliest_gap',dict(weekdays=[0,1,2,3,4],rest_dates=['2026-12-25']),2)
        self.check_no_overlap_or_bypass(plan)
        for t in plan['tasks']:
            if t['effective_start'] is not None:
                self.assertNotEqual(t['effective_start'].date().isoformat(),'2026-12-25')
        # A real collision uses the second crew rather than duplicating crew 1.
        cfg=plan['configuration']; cal=CI.Calendar(cfg['calendar'])
        start=datetime(2026,12,24,8)
        pool=CrewPool(cal,cfg['crews'],start,'earliest_gap')
        a=pool.reserve('L','wet_joint',start,12,'parallel-A')
        b=pool.reserve('L','wet_joint',start,12,'parallel-B')
        self.assertEqual(a[:2],b[:2])
        self.assertNotEqual(a[2],b[2])
        self.assertEqual(a[1],datetime(2026,12,28,10))

    def test_missing_gate_remains_held_for_both_policies(self):
        for policy in ('fixed_route','earliest_gap'):
            cfg = CI.load(); cfg['mode']='project'; cfg['scheduling']['crew_strategy']=policy
            rows,_=S.plan(configuration=cfg)
            plan=CP.build(self.elements,rows,configuration=cfg)
            self.assertIsNone(plan['by_id']['C-L1-2']['release_at'])
            self.assertIsNone(plan['by_id']['F-wet_joint-L02']['effective_start'])

    def test_unknown_or_boolean_strategy_is_rejected(self):
        for bad in ('optimal',True,0,{'fixed_route':True}):
            cfg=CI.load();cfg['scheduling']['crew_strategy']=bad
            with self.assertRaises(ValueError): CI.validate(cfg)

    def test_previous_complete_schema_one_profile_normalizes_to_original_route(self):
        old=CI.load();del old['scheduling']
        migrated=CI.load(old)
        self.assertEqual(migrated['scheduling'],dict(crew_strategy='fixed_route'))
        self.assertEqual(CI.fingerprint(old),CI.fingerprint(migrated))
        rows,_=S.plan(configuration=old)
        plan=CP.build(self.elements,rows,configuration=old)
        self.assertEqual(plan['finish'],datetime(2027,2,20,16))
        self.assertEqual(plan['configuration_sha256'],CI.fingerprint(old))
        for key in ('crews','baseline'):
            incomplete=dict(old);del incomplete[key]
            with self.assertRaises(ValueError): CI.load(incomplete)
        unknown=dict(old,unexpected=True)
        with self.assertRaises(ValueError): CI.load(unknown)


if __name__=='__main__': unittest.main()
