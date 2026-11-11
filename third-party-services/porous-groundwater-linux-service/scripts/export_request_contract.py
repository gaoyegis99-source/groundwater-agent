"""Export/check the service request schema from the actual V6 adapter models."""
import argparse
import json
from pathlib import Path
import sys

SERVICE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVICE))
import porous_remote  # noqa: E402

# These two modules are copied unchanged by stage_service.py at image build time.
porous_remote.__path__.append(str(SERVICE.parent/'groundwater-sustainability-service/app'))
from porous_remote import MEDIA_TYPES, PORTS  # noqa: E402
from porous_remote.api import Operation  # noqa: E402


def request_schema():
    schema=Operation.model_json_schema()
    schema.update({'$schema':'https://json-schema.org/draft/2020-12/schema',
                   '$id':'urn:groundwater:porous:remote-operation.v1',
                   'title':'Porous groundwater fixed remote operations'})
    schema['properties']['operationType']['enum']=list(PORTS)
    schema['allOf']=[]
    for operation,ports in PORTS.items():
        parameters={'type':'object','additionalProperties':False,'properties':{}}
        if operation=='pest.calibrate':
            # Empty parameters retain the v1.0 operator response shape. v1.1
            # explicitly requests the additional calibrated model output.
            parameters['properties']['includeCalibratedModel']={'const':True,'type':'boolean'}
        schema['allOf'].append({
            'if':{'properties':{'operationType':{'const':operation}}},
            'then':{'properties':{
                'inputs':{'type':'object','additionalProperties':False,'required':list(ports),
                          'properties':{port:{'allOf':[
                              {'$ref':'#/$defs/Input'},
                              {'properties':{'format':{'const':fmt},'mediaType':{'const':MEDIA_TYPES[fmt]}}}
                          ]} for port,fmt in ports.items()}},
                'parameters':parameters,
            }},
        })
    return schema


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    path=SERVICE/'contracts/remote-operation.v1.json'
    content=json.dumps(request_schema(),ensure_ascii=False,indent=2)+'\n'
    if args.check:
        if path.read_text()!=content:raise SystemExit('REMOTE_REQUEST_CONTRACT_DRIFT')
    else:
        path.write_text(content)
