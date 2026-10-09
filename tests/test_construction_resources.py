"""Independent STEP readback of calendar exceptions and real crew allocations."""
import unittest
import ifcopenshell
import ifcopenshell.api.root as IR
import ifcopenshell.validate as IV
from bridge import construction_input as CI
from bridge.pipeline import compute
from scripts.export_ifc import build_schedule, _normalize_sets, _stable_ids
from scripts.check_construction_resources import check


class ConstructionResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg=CI.load(); cfg['calendar']['weekdays']=[0,1,2,3,4]
        cfg['calendar']['rest_dates']=['2026-12-25']; cfg['calendar']['work_dates']=['2026-12-26']
        cfg['crews']['L']['wet_joint']=2; cfg['durations']['wet_joint']['work_hours']=20
        cfg['scheduling']['crew_strategy']='earliest_gap'
        cls.cfg=cfg;cls.result=compute(configuration=cfg)
        f=ifcopenshell.file(schema='IFC4X3_ADD2')
        IR.create_entity(f,ifc_class='IfcProject',name='calendar and crew fixture')
        referenced={eid for t in cls.result['construction']['tasks'] for eid in t['elements']+t['removes']}
        products={eid:IR.create_entity(f,ifc_class='IfcBuildingElementProxy',name=eid) for eid in sorted(referenced)}
        build_schedule(f,cls.result,products);_normalize_sets(f);_stable_ids(f)
        cls.step=f.to_string()

    def fresh(self): return ifcopenshell.file.from_string(self.step)

    def test_standard_calendar_and_crew_bindings_survive_step_readback(self):
        f=self.fresh();out=check(f,self.cfg,self.result['construction'])
        self.assertEqual(out,dict(crews=17,calendars=1,assigned_tasks=104,rest_dates=1,work_dates=1,crew_strategy='earliest_gap'))
        logger=IV.json_logger(); IV.validate(f,logger)
        self.assertEqual(logger.statements,[])

    def test_wrong_weekday_or_omitted_exception_is_rejected(self):
        f=self.fresh();f.by_type('IfcWorkCalendar')[0].WorkingTimes[0].RecurrencePattern.WeekdayComponent=[1,2,3,4,7]
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])
        f=self.fresh();f.by_type('IfcWorkCalendar')[0].ExceptionTimes=None
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])

    def test_same_counts_with_wrong_task_crew_relation_is_rejected(self):
        f=self.fresh(); crews=f.by_type('IfcCrewResource'); replacement=next(c for c in crews if c.Identification=='R:wet_joint:1')
        relation=next(rel for rel in f.by_type('IfcRelAssignsToProcess') if rel.RelatingProcess.Identification=='F-wet_joint-L01')
        relation.RelatedObjects=[replacement]
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])

    def test_resource_actual_work_must_remain_empty(self):
        f=self.fresh();f.by_type('IfcCrewResource')[0].Usage.ActualWork='PT4H'
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])

    def test_hidden_recurrence_limits_and_duplicate_resource_binding_are_rejected(self):
        f=self.fresh();f.by_type('IfcWorkCalendar')[0].WorkingTimes[0].RecurrencePattern.Occurrences=1
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])
        f=self.fresh();extra=next(w for w in f.by_type('IfcWorkCalendar')[0].WorkingTimes if w.StartDate)
        extra.RecurrencePattern.MonthComponent=[12]
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])
        f=self.fresh();rel=next(v for v in f.by_type('IfcRelAssignsToProcess') if v.RelatingProcess.Identification=='F-wet_joint-L01')
        IR.create_entity(f,ifc_class='IfcRelAssignsToProcess').RelatingProcess=rel.RelatingProcess
        duplicate=f.by_type('IfcRelAssignsToProcess')[-1];duplicate.RelatedObjects=rel.RelatedObjects
        with self.assertRaises(ValueError): check(f,self.cfg,self.result['construction'])

    def test_midnight_end_is_24_hour_ifc_time_and_next_date_in_plan(self):
        from copy import deepcopy
        from datetime import datetime
        cfg=deepcopy(self.cfg);cfg['calendar']['start_hour']=14;cfg['calendar']['day_hours']=10
        result=compute(configuration=cfg)
        self.assertEqual(CI.Calendar(cfg['calendar']).end(datetime(2026,12,7,14),10),datetime(2026,12,8,0))
        f=ifcopenshell.file(schema='IFC4X3_ADD2');IR.create_entity(f,ifc_class='IfcProject',name='midnight fixture')
        referenced={eid for t in result['construction']['tasks'] for eid in t['elements']+t['removes']}
        products={eid:IR.create_entity(f,ifc_class='IfcBuildingElementProxy',name=eid) for eid in sorted(referenced)}
        build_schedule(f,result,products)
        f=ifcopenshell.file.from_string(f.to_string())
        self.assertEqual(f.by_type('IfcWorkCalendar')[0].WorkingTimes[0].RecurrencePattern.TimePeriods[0].EndTime,'24:00:00')
        self.assertEqual(check(f,cfg,result['construction'])['work_dates'],1)
        logger=IV.json_logger();IV.validate(f,logger);self.assertEqual(logger.statements,[])


if __name__=='__main__': unittest.main()
