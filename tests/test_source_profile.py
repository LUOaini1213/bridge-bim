"""The actual saved source must match core geometry, not just its own replays."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import rhino3dm as R
from bridge import construction_input as CI, source_profile as SP
from bridge.pipeline import compute, element_rows
from scripts.check_source import check, check_file, model_bbox

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'model'/'bridge_bim.3dm'


class SourceProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference=SP.load()
        cls.cfg=cls.reference['construction_config']
        cls.result=compute(configuration=cls.cfg)
        cls.attrs=element_rows(cls.result)

    def model(self): return R.File3dm.Read(str(SOURCE))
    def verify(self,model,result=None):
        return check(model,result=result or self.result,expected_attributes=self.attrs,source_configuration=self.cfg)
    def engineering(self,model,eid):
        return next(o for o in model.Objects if o.Attributes.GetUserString('eid')==eid)

    def test_actual_source_has_core_shape_metadata_and_reference_provenance(self):
        out=self.verify(self.model())
        self.assertEqual(out['elements'],1470);self.assertEqual(out['beds'],self.cfg['baseline']['n_beds'])
        self.assertLess(out['max_bbox_error_m'],1e-3)
        self.assertEqual(out['source_reference_configuration_sha256'],CI.fingerprint(self.cfg))

    def test_new_calendar_hours_and_gates_keep_original_source_dates_as_reference(self):
        cfg=deepcopy(self.cfg);cfg['calendar']['weekdays']=[0,1,2,3,4];cfg['calendar']['start_hour']=7
        cfg['baseline']['erect_hours']=2.5;cfg['mode']='project';cfg['scheduling']['crew_strategy']='earliest_gap'
        result=compute(configuration=cfg)
        self.assertNotEqual([row['erect'] for row in result['rows']],[row['erect'] for row in self.result['rows']])
        out=self.verify(self.model(),result)
        self.assertEqual(out['configuration_sha256'],CI.fingerprint(cfg))
        self.assertNotEqual(out['configuration_sha256'],out['source_reference_configuration_sha256'])

    def test_translation_missing_id_bad_metadata_and_units_are_rejected(self):
        for mutation in ('translate','missing_id','metadata','units'):
            with self.subTest(mutation=mutation):
                m=self.model();o=self.engineering(m,'G-L01-1')
                if mutation=='translate': o.Geometry.Transform(R.Transform.Translation(1,0,0))
                elif mutation=='missing_id': o.Attributes.SetUserString('eid','')
                elif mutation=='metadata': o.Attributes.SetUserString('class','bearing')
                else: m.Settings.ModelUnitSystem=R.UnitSystem.Millimeters
                with self.assertRaisesRegex(ValueError,'regenerate-source'): self.verify(m)

    def test_changed_bed_count_needs_explicit_source_rebuild(self):
        cfg=deepcopy(self.cfg);cfg['baseline']['n_beds']=3
        with self.assertRaisesRegex(ValueError,'bed count'):
            self.verify(self.model(),compute(configuration=cfg))

    def test_same_bbox_cylinder_replaced_by_box_is_rejected(self):
        m=self.model();o=self.engineering(m,'B-L01-1a');lo,hi=model_bbox(self.result['by_id']['B-L01-1a'])
        replacement=R.Brep.CreateFromBoundingBox(R.BoundingBox(R.Point3d(*lo),R.Point3d(*hi)))
        attrs=R.ObjectAttributes();attrs.Id=o.Attributes.Id;attrs.Name=o.Attributes.Name;attrs.LayerIndex=o.Attributes.LayerIndex
        for key,value in o.Attributes.GetUserStrings(): attrs.SetUserString(key,value)
        m.Objects.Delete(attrs.Id);m.Objects.AddBrep(replacement,attrs)
        with self.assertRaisesRegex(ValueError,'analytic primitive'): self.verify(m)

    def test_closed_mesh_with_reversed_face_or_nonfinite_vertex_is_rejected(self):
        m=self.model();mesh=self.engineering(m,'G-L01-1').Geometry
        a,b,c,d=mesh.Faces[0]
        self.assertTrue(mesh.Faces.SetFace(0,c,b,a))
        self.assertTrue(mesh.IsClosed)
        with self.assertRaisesRegex(ValueError,'topology/winding'): self.verify(m)
        m=self.model();mesh=self.engineering(m,'G-L01-1').Geometry
        mesh.Vertices[0]=R.Point3f(float('nan'),0,0)
        with self.assertRaises(ValueError): self.verify(m)

    def test_source_hash_and_reference_config_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source_config.json'
            bad=deepcopy(self.reference);bad['source_sha256']='0'*64
            path.write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'SHA'): check_file(SOURCE,source_config=path)
            bad=deepcopy(self.reference);bad['construction_config']['baseline']['n_beds']=3
            path.write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'fingerprint'): SP.load(path)


if __name__=='__main__': unittest.main()
