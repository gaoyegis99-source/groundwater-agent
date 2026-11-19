"""Pure-function probe using a synthetic SDK transcript; no storage, HTTP or model calls."""
import json,pathlib,sys
from types import SimpleNamespace as NS
ROOT=pathlib.Path(__file__).resolve().parents[3]
for sub in ['source/kernel/src','source/packages/protocol-contracts/src','source/packages/workflow-compiler/src','source/services/data/client/src','source/services/knowledge-next/client/src','source/packages/embedding-client/src']:
 sys.path.insert(0,str(ROOT/sub))
from platform_kernel.agent_runtime.result_processing import prepare_interruption
p=ROOT/'source/domains/groundwater/6.2.0/models/flow-field.model.json'; d=json.loads(p.read_text())
model=NS(model_id=d['modelId'],model_version=d['version'],model_checksum='sha256:'+'a'*64,workflow_id=d['workflowRef']['workflowId'],workflow_version=d['workflowRef']['version'],workflow_checksum=d['workflowRef']['checksum'],input_schema_checksum='sha256:'+'b'*64,tool_name=d['tool']['name'],input_schema=d['tool']['inputSchema'],default_parameters=d['defaultParameters'],data_input_parameters=tuple(d['inputBindings'].values()),field_requirements=tuple(NS(parameter=x['parameter']) for x in d['fieldRequirements']),semantic_requirements=d['semanticRequirements'])
args=dict(versionId='version-probe',wellField='well',timeField='time',xField='x',yField='y',referenceElevationField='elevation',depthToWaterField='depth',targetTime='2026-01-01',sourceCrs='EPSG:32650',targetCrs='EPSG:32650',pixelSize=10,hydraulicConductivityMetersPerDay=1,effectivePorosity=.2,referenceElevationUnit='m',depthToWaterUnit='m')
observation={'data':{'status':'OK','datasetId':'dataset-probe','versionId':'version-probe','currentVersion':'version-newer','semanticId':'semantic-probe','semantics':{'fields':{'f1':{'meaning':'reference_elevation','unit':'ft'},'f2':{'meaning':'depth_to_water','unit':'m'}},'verticalDatum':'datum-probe'},'descriptor':{'fields':[{'fieldId':'f1','name':'elevation','type':'double'},{'fieldId':'f2','name':'depth','type':'double'}]},'objects':[{'role':'raw','objectId':'object-probe','mediaType':'text/csv','format':'csv','size':12,'checksum':'sha256:'+'c'*64}]}}
transcript=[{'type':'function_call','call_id':'read-probe','name':'inspect_data_version'},{'type':'function_call_output','call_id':'read-probe','output':json.dumps(observation)}]
run=NS(agent_run_id='probe-run',actions=[],plans=[NS(revision=1,steps=[NS(tool=model.tool_name)])])
result=NS(interruptions=[NS(name=model.tool_name,call_id='model-probe',arguments=json.dumps(args))],to_input_list=lambda:transcript,to_state=lambda:NS(to_json=lambda **kw:{}))
prepared=prepare_interruption(run,result,{model.tool_name:model})
snapshot=prepared.preparations[0]
output={'probe':'synthetic pure-function boundary, not live exploit','explicit_prepare_call':False,'declared_source_elevation_unit':'ft','proposed_elevation_unit':'m','declared_current_version':'version-newer','selected_version':'version-probe','accepted_actions':len(prepared.actions),'preparation_status':snapshot['status'],'selection_policy':snapshot['selection_policy'],'checks':snapshot['checks'],'blocking_reasons':snapshot['blocking_reasons'],'finding':'Without explicit preparation, proposal boundary validates schema and exact read identity but does not run field/semantic requirements; defaults policy to PINNED.'}
print(json.dumps(output,ensure_ascii=False,indent=2))
assert snapshot['status']=='READY' and len(prepared.actions)==1
