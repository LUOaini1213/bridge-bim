"""IFC project gates are planned metadata, never fabricated actual progress."""
import json
import unittest
from datetime import datetime
from copy import deepcopy
import ifcopenshell
import ifcopenshell.util.element as UE
from bridge import construction_input as CI
from bridge.pipeline import compute
from scripts.export_ifc import build_ifc


class ConstructionDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = CI.load()
        config['mode'] = 'project'
        config['releases'] = [{'task_id':'E001', 'gate':g, 'approved_at':'2027-07-01T08:00',
                               'reference':'TEST-PROJECT-RELEASE', 'source':'unit test fixture; not field approval'} for g in CI.GATES['girder']]
        config['releases'].append({'task_id':'Y001','gate':'fabrication_acceptance','approved_at':'2027-07-01T08:00','reference':'TEST-YARD-RELEASE','source':'unit test fixture'})
        cls.result = compute(configuration=config)
        # Round-trip the IFC STEP text independently rather than only objects.
        cls.file = ifcopenshell.file.from_string(build_ifc(cls.result).to_string())
        cls.tasks = {t.Identification:t for t in cls.file.by_type('IfcTask')}

    def test_project_release_effective_times_and_holds_survive_ifc_readback(self):
        task = self.tasks['E001']; props = UE.get_pset(task,'BridgeBIM_ConstructionTask')
        self.assertEqual(task.Status,'PLANNED')
        self.assertEqual(props['Mode'],'project')
        self.assertEqual(props['EffectiveStart'],'2027-07-01T08:00:00')
        self.assertEqual(props['ReleaseAt'],'2027-07-01T10:00:00')
        self.assertEqual(json.loads(props['ReleaseGates'])[0]['record']['reference'],'TEST-PROJECT-RELEASE')
        held = self.tasks['C-L1-2']; props = UE.get_pset(held,'BridgeBIM_ConstructionTask')
        self.assertEqual(held.Status,'HELD')
        self.assertEqual(props['ReleaseAt'],'HELD')
        self.assertTrue(any('predecessor' in v for v in json.loads(props['HoldReasons'])))
        self.assertEqual(props['ConfigurationSHA256'], CI.fingerprint(self.result['construction_config']))
        self.assertEqual(self.tasks['Y120'].Status,'HELD')
        self.assertEqual(UE.get_pset(self.tasks['Y120'],'BridgeBIM_ConstructionTask','ReleaseAt'),'HELD')

    def test_project_ifc_uses_schedule_only_without_actuals_or_completion(self):
        for task in self.tasks.values():
            tt = task.TaskTime
            self.assertIsNotNone(tt.ScheduleStart)
            self.assertIsNotNone(tt.ScheduleFinish)
            for field in ('ActualStart','ActualFinish','ActualDuration','ActualWork','Completion'):
                if hasattr(tt,field): self.assertIsNone(getattr(tt,field))
        first = self.tasks['E001']
        self.assertLess(datetime.fromisoformat(first.TaskTime.ScheduleStart), datetime(2027,7,1,8))

    def test_task_identity_is_stable_across_mode_and_configuration(self):
        # GUID derivation is identity based; mode, status and approval dates
        # must not alter the identity of an existing construction task.
        default = build_ifc(compute())
        previous = {t.Identification:t.GlobalId for t in default.by_type('IfcTask')}
        self.assertEqual({k:t.GlobalId for k,t in self.tasks.items()},previous)


if __name__ == '__main__': unittest.main()
