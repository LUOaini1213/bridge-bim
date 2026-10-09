"""Portable provenance of the complete source model's reference baseline."""
import hashlib
import json
from pathlib import Path
from . import construction_input as CI

DEFAULT_PATH=Path(__file__).resolve().parents[1]/'model'/'source_config.json'


def record(source, configuration, basis, rhino_version):
    cfg=CI.load(configuration)
    return {'schema':1,'source_filename':Path(source).name,
            'source_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            'construction_config':cfg,'configuration_sha256':CI.fingerprint(cfg),
            'basis':basis,'rhino_version':rhino_version}


def load(path=DEFAULT_PATH):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if set(value)!={'schema','source_filename','source_sha256','construction_config','configuration_sha256','basis','rhino_version'} or type(value['schema']) is not int or value['schema']!=1:
        raise ValueError('invalid source profile record')
    cfg=CI.load(value['construction_config'])
    if value['configuration_sha256']!=CI.fingerprint(cfg):
        raise ValueError('source reference configuration fingerprint differs')
    for field in ('source_filename','source_sha256','basis','rhino_version'):
        if not isinstance(value[field],str) or not value[field].strip(): raise ValueError('empty source provenance '+field)
    if len(value['source_sha256'])!=64 or any(c not in '0123456789abcdef' for c in value['source_sha256']):
        raise ValueError('invalid source SHA256')
    value['construction_config']=cfg
    return value


def write(path, source, configuration, basis, rhino_version):
    value=record(source,configuration,basis,rhino_version)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=1,allow_nan=False)+'\n',encoding='utf-8')
    return value
