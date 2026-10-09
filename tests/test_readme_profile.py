"""Custom construction prose and profile checks must follow the selected JSON."""
from copy import deepcopy
import unittest
from bridge import construction_input as CI, construction as CP
from bridge.pipeline import compute, girder_rows
from scripts.check_readme import construction_claim_values, validate_profile


class ReadmeProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=CI.load()
        cls.cfg['baseline']['n_beds']=3
        cls.cfg['baseline']['erect_hours']=2.5
        cls.cfg['calendar']['day_hours']=9
        cls.cfg['scheduling']['crew_strategy']='earliest_gap'
        cls.result=compute(configuration=cls.cfg)
        cls.plan=CP.serializable(cls.result['construction'])
        cls.tasks=[{k:str(v) for k,v in row.items()} for row in CP.table(cls.result['construction'])]
        cls.girders=[{k:str(v) for k,v in row.items()} for row in girder_rows(cls.result)]
        cls.summary={k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in cls.result['summary'].items()}
        cls.summary['beds']=3

    def verify(self, plan=None, summary=None, girders=None, tasks=None):
        return validate_profile(self.cfg, plan if plan is not None else self.plan,
                                summary if summary is not None else self.summary,
                                girders if girders is not None else self.girders,
                                tasks if tasks is not None else self.tasks)

    def test_prose_uses_three_beds_and_fractional_productivity_instead_of_c(self):
        values=construction_claim_values(self.cfg)
        self.assertEqual(values['beds'],3)
        self.assertEqual(values['productivity'],[7,14,'9','2.5','6',4])
        self.assertEqual(construction_claim_values(CI.load())['beds'],16)

    def test_custom_profile_is_independently_verified_without_default_readme_prose(self):
        report=self.verify()
        self.assertEqual(report['beds'],3)
        self.assertEqual(report['tasks'],344)
        self.assertEqual(report['crew_strategy'],'earliest_gap')
        self.assertEqual(report['configuration_sha256'],CI.fingerprint(self.cfg))

    def test_held_state_or_false_approval_tampering_is_rejected(self):
        bad=deepcopy(self.plan); bad['tasks'][0]['release_at']='2000-01-01T08:00:00'
        with self.assertRaises(ValueError): self.verify(plan=bad)
        bad=deepcopy(self.plan); bad['tasks'][0]['work_hours']=False
        with self.assertRaises(ValueError): self.verify(plan=bad)

    def test_stale_csv_or_default_bed_summary_cannot_pass_custom_profile(self):
        bad=deepcopy(self.tasks);bad[1]['crew_strategy']='fixed_route'
        with self.assertRaises(ValueError): self.verify(tasks=bad)
        bad=deepcopy(self.girders);bad[0]['bed']='16'
        with self.assertRaises(ValueError): self.verify(girders=bad)
        bad=deepcopy(self.summary);bad['beds']=16
        with self.assertRaises(ValueError): self.verify(summary=bad)


if __name__=='__main__': unittest.main()
