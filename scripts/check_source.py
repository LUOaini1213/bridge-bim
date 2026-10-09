"""Read-only source .3dm/profile preflight, before any delivery is rewritten.

The actual saved engineering geometry and metadata are checked against core
inputs; replay-to-source agreement alone cannot certify a corrupted source.
"""
import argparse
from collections import Counter
import math
from pathlib import Path
import sys
import rhino3dm

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bridge import alignment as AL, config as C, yard as Y, source_profile as SP, construction_input as CI
from bridge.model import project, triangulate
from bridge.pipeline import compute, element_rows
from bridge.numcmp import compare

TOLERANCE_M=1e-3  # Existing artifact gate for single-precision saved mesh vertices.
PRIMITIVE_TOLERANCE_M=1e-6  # Analytic Breps retain double precision.


def primitive_matches(brep, element):
    """Necessary analytic/topological and dimensional proof for core primitives."""
    tol=PRIMITIVE_TOLERANCE_M
    if not brep.IsValid or not brep.IsSolid: return False
    def sample(face, fraction=0.5):
        u,v=face.Domain(0),face.Domain(1)
        a,b=u.T0+fraction*(u.T1-u.T0),(v.T0+v.T1)/2
        return face.PointAt(a,b),face.NormalAt(a,b)
    if element.shape=='cyl':
        if len(brep.Faces)!=3: return False
        walls=[face for face in brep.Faces if face.IsCylinder(tol)]
        caps=[face for face in brep.Faces if face.IsPlanar(tol)]
        if len(walls)!=1 or len(caps)!=2: return False
        c,r,h=element.params['c'],element.params['r'],element.params['h']
        for fraction in (0.125,0.375,0.625,0.875):
            p,n=sample(walls[0],fraction)
            if not all(math.isfinite(v) for v in (p.X,p.Y,p.Z,n.X,n.Y,n.Z)): return False
            if abs(n.Z)>tol or abs(math.hypot(p.X-c[0],p.Y-c[1])-r)>tol: return False
        levels=[]
        for face in caps:
            p,n=sample(face)
            if not all(math.isfinite(v) for v in (p.X,p.Y,p.Z,n.X,n.Y,n.Z)): return False
            if abs(n.X)>tol or abs(n.Y)>tol or abs(abs(n.Z)-1)>tol: return False
            levels.append(p.Z)
        return all(abs(a-b)<=tol for a,b in zip(sorted(levels),(c[2],c[2]+h)))
    if len(brep.Faces)!=6 or len(brep.Vertices)!=8 or not all(face.IsPlanar(tol) for face in brep.Faces):
        return False
    p=element.params;c,d=p['c'],p['dir']
    remaining=[(c[0]+a*d[0]*p['l']/2-b*d[1]*p['w']/2,c[1]+a*d[1]*p['l']/2+b*d[0]*p['w']/2,c[2]+z*p['h'])
               for a in (-1,1) for b in (-1,1) for z in (0,1)]
    for vertex in brep.Vertices:
        q=vertex.Location
        if not all(math.isfinite(v) for v in (q.X,q.Y,q.Z)): return False
        index=min(range(len(remaining)),key=lambda i:max(abs(a-b) for a,b in zip((q.X,q.Y,q.Z),remaining[i])))
        if max(abs(a-b) for a,b in zip((q.X,q.Y,q.Z),remaining.pop(index)))>tol: return False
    return True


def model_bbox(e):
    if e.shape=='cyl':
        c,r,h=e.params['c'],e.params['r'],e.params['h']
        return (c[0]-r,c[1]-r,c[2]),(c[0]+r,c[1]+r,c[2]+h)
    if e.shape=='box':
        p=e.params;c,d=p['c'],p['dir']
        vertices=[(c[0]+a*d[0]*p['l']/2-b*d[1]*p['w']/2,
                   c[1]+a*d[1]*p['l']/2+b*d[0]*p['w']/2,c[2]+z*p['h'])
                  for a in (-1,1) for b in (-1,1) for z in (0,1)]
    else:
        vertices=[v for solid in e.params['solids'] for v in solid['v']]
    return tuple(min(v[i] for v in vertices) for i in range(3)),tuple(max(v[i] for v in vertices) for i in range(3))


def bounds_error(geometry, wanted):
    box=geometry.GetBoundingBox()
    actual=((box.Min.X,box.Min.Y,box.Min.Z),(box.Max.X,box.Max.Y,box.Max.Z))
    errors=[abs(actual[j][i]-wanted[j][i]) for j in (0,1) for i in range(3)]
    if not all(math.isfinite(x) for pt in actual for x in pt): return float('inf')
    return max(errors)


def check(model, configuration=None, result=None, expected_attributes=None, source_configuration=None):
    def fail(message):
        raise ValueError(message+'; source/profile mismatch: use delivery.py --rebuild --regenerate-source')
    if model is None: fail('source .3dm could not be read')
    if model.Settings.ModelUnitSystem!=rhino3dm.UnitSystem.Meters: fail('source must use metres')
    result=result or compute(configuration=configuration)
    reference=source_configuration if source_configuration is not None else SP.load()['construction_config']
    # Original cast/erect/age fields describe the source baseline. A new
    # calendar or gate profile is carried by construction_* replay metadata.
    wanted=expected_attributes if expected_attributes is not None else element_rows(compute(configuration=reference))
    by_id={}; yard_beds={}; worst=0.0
    for obj in model.Objects:
        attrs=obj.Attributes
        layer=model.Layers.FindIndex(attrs.LayerIndex)
        if layer is None: fail('invalid layer index')
        path=layer.FullPath
        if path.startswith(('上部结构','下部结构')):
            eid=attrs.GetUserString('eid')
            if not eid or eid in by_id: fail('missing/duplicate engineering BIM ID '+str(eid))
            if attrs.Name!=eid: fail('object name/BIM ID differ: '+eid)
            by_id[eid]=(obj,path)
        if path=='梁场::制梁台座':
            if attrs.Name in yard_beds: fail('duplicate yard bed name')
            yard_beds[attrs.Name]=obj
    if set(by_id)!=set(result['by_id']): fail('engineering object IDs/count differ from core model')
    for eid,(obj,path) in by_id.items():
        e=result['by_id'][eid]
        for key,value in wanted[eid].items():
            okay,_,reason=compare(obj.Attributes.GetUserString(key) or '',value,'decimal')
            if not okay: fail(eid+'.'+key+': '+str(reason))
        if e.cls=='girder' and path!='上部结构::预制T梁::%s %.2f m'%(e.attrs['family'],e.attrs['length']):
            fail(eid+': girder length-spec layer differs')
        error=bounds_error(obj.Geometry,model_bbox(e));worst=max(worst,error)
        if error>=TOLERANCE_M: fail(eid+': geometry placement/dimensions differ by %.6g m'%error)
        if e.shape=='mesh':
            mesh=obj.Geometry
            if not isinstance(mesh,rhino3dm.Mesh) or not mesh.IsValid or not mesh.IsClosed: fail(eid+': expected valid closed mesh')
            vertices=[v for solid in e.params['solids'] for v in triangulate(solid)['v']]
            if len(mesh.Vertices)!=len(vertices): fail(eid+': mesh vertex count differs')
            for actual,expected in zip(mesh.Vertices,vertices):
                if not all(math.isfinite(v) for v in (actual.X,actual.Y,actual.Z)):
                    fail(eid+': nonfinite mesh vertex')
                if max(abs(v-w) for v,w in zip((actual.X,actual.Y,actual.Z),expected))>=TOLERANCE_M:
                    fail(eid+': mesh vertex/transform differs')
            def oriented(face):
                # Accept face ordering/cyclic rotations, preserve winding.
                face=tuple(face)
                return min(face[i:]+face[:i] for i in range(len(face)))
            expected_faces=[];base=0
            for solid in e.params['solids']:
                tri=triangulate(solid)
                expected_faces.extend(oriented(tuple(base+i for i in face)) for face in tri['f'])
                base+=len(tri['v'])
            actual_faces=[oriented(face[:3] if face[2]==face[3] else face) for face in mesh.Faces]
            if Counter(actual_faces)!=Counter(expected_faces):
                fail(eid+': mesh face topology/winding differs')
        elif not isinstance(obj.Geometry,rhino3dm.Brep):
            fail(eid+': expected Brep, instance/other geometry is unsupported')
        elif not primitive_matches(obj.Geometry,e):
            fail(eid+': analytic primitive shape/solid/radius/axis/caps differ')
    cfg=result['construction_config']; beds=Y.beds(configuration=cfg)
    if set(yard_beds)!={'台座 %d'%i for i in range(1,len(beds)+1)}:
        fail('configured yard bed count differs')
    boundary=Y.boundary(cfg)
    grounds=[]
    for u in (boundary[0],(boundary[0]+boundary[2])/2,boundary[2]):
        for v in (boundary[1],(boundary[1]+boundary[3])/2,boundary[3]):
            x,y=Y.to_world(u,v);station,offset=project(x,y,C.YARD_STATION+u)
            grounds.append(AL.ground(station,offset))
    pad_z=max(grounds)+0.3
    for i,bed in enumerate(beds,1):
        u0,v0,u1,v1=bed
        points=[Y.to_world(u,v)+(z,) for u in (u0,u1) for v in (v0,v1) for z in (pad_z,pad_z+0.4)]
        expected=tuple(min(p[k] for p in points) for k in range(3)),tuple(max(p[k] for p in points) for k in range(3))
        if bounds_error(yard_beds['台座 %d'%i].Geometry,expected)>=TOLERANCE_M:
            fail('yard bed placement/profile differs: '+str(i))
    return {'elements':len(by_id),'beds':len(beds),'units':'metres','max_bbox_error_m':worst,
            'configuration_sha256':result['construction']['configuration_sha256'],
            'source_reference_configuration_sha256':CI.fingerprint(reference)}


def check_file(path, configuration=None, source_config=SP.DEFAULT_PATH):
    import hashlib
    reference=SP.load(source_config)
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=reference['source_sha256']:
        raise ValueError('source SHA differs from verified source/profile record; use --regenerate-source')
    return check(rhino3dm.File3dm.Read(str(path)),configuration,source_configuration=reference['construction_config'])


def main():
    import json
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=str(ROOT/'model'/'bridge_bim.3dm'))
    parser.add_argument('--construction-config')
    parser.add_argument('--source-config',default=str(SP.DEFAULT_PATH),help='portable source baseline provenance companion')
    args=parser.parse_args()
    print('PASS source/profile '+json.dumps(check_file(args.source,args.construction_config,args.source_config),sort_keys=True))


if __name__=='__main__': main()
