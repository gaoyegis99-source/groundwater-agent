"""HTTP/queue contract tests; native algorithms are covered by V6 live acceptance."""
import asyncio
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from jsonschema import Draft202012Validator

from porous_remote import MEDIA_TYPES, PORTS, VERSION
from porous_remote.api import app
from porous_remote.queue import OperationQueue, _OUTPUTS
from scripts.export_request_contract import request_schema


def digest(value):
    return 'sha256:'+hashlib.sha256(value).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('api-key', 'package-key'):
        (tmp_path/name).write_text('test-only-secret-'*4)
    vendor=tmp_path/'vendor';vendor.mkdir()
    monkeypatch.setenv('POROUS_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('POROUS_VENDOR_DIR',str(vendor))
    monkeypatch.setenv('POROUS_API_KEY_FILE',str(tmp_path/'api-key'))
    monkeypatch.setenv('POROUS_PACKAGE_KEY_FILE',str(tmp_path/'package-key'))
    async def paused_worker(_self):
        await asyncio.Future()
    # Keep the real durable queue but control scheduling for replay/cancel tests.
    monkeypatch.setattr(OperationQueue,'_drain',paused_worker)
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer '+(tmp_path/'api-key').read_text()
        yield client


def upload(client,content=b'contract-test-input',format_='zip'):
    response=client.post('/api/v1/uploads',json=dict(
        name='input.'+format_,sizeBytes=len(content),sha256=digest(content),
        mediaType=MEDIA_TYPES[format_],format=format_))
    assert response.status_code==201,response.text
    session=response.json();path=f"/api/v1/uploads/{session['uploadId']}"
    headers={'X-Content-SHA256':digest(content)}
    assert client.put(path+'/parts/0',content=content,headers=headers).status_code==200
    assert client.put(path+'/parts/0',content=content,headers=headers).json()['replayed']
    response=client.post(path+'/complete');assert response.status_code==200
    return {k:response.json()[k] for k in ('artifactId','mediaType','format','sizeBytes','sha256')}


def test_health_version_and_authentication(client):
    for path in ('/healthz','/readyz','/version'):
        assert client.get(path,headers={'Authorization':''}).status_code==200
    assert client.get('/version').json()==VERSION
    assert set(client.get('/api/v1/capabilities').json()['operations'])==set(PORTS)
    response=client.get('/api/v1/capabilities',headers={'Authorization':'Bearer wrong'})
    assert response.status_code==401 and response.headers['www-authenticate']=='Bearer'
    assert response.json()['error']['request_id']==response.headers['x-request-id']


def test_request_field_error_does_not_echo_input_or_enqueue(client):
    response=client.post('/api/v1/operations',json={
        'operationType':'model.compile','clientRequestId':'missing-fields',
        'inputs':{'project_package':{'secret_input':'must-not-be-echoed'}},'parameters':{}})
    assert response.status_code==422
    error=response.json()['error']
    assert error['code']=='REQUEST_VALIDATION_ERROR'
    assert {'location':['body','inputs','project_package','artifactId'],'type':'missing'} in error['details']
    assert error['request_id']==response.headers['x-request-id']
    assert 'must-not-be-echoed' not in response.text
    assert client.get('/api/v1/operations/by-client-request/missing-fields').status_code==404


@pytest.mark.parametrize('operation',list(PORTS))
def test_all_fixed_operations_upload_reconcile_replay_conflict_and_cancel(client,operation):
    request=dict(operationType=operation,clientRequestId='contract:'+operation,
                 inputs={port:upload(client,format_=fmt) for port,fmt in PORTS[operation].items()},
                 parameters={'includeCalibratedModel':True} if operation=='pest.calibrate' else {})
    first=client.post('/api/v1/operations',json=request);assert first.status_code==202,first.text
    identifier=first.json()['operationId'];path='/api/v1/operations/'+identifier
    assert client.get('/api/v1/operations/by-client-request/'+request['clientRequestId']).json()['operationId']==identifier
    # Artifact IDs are transient upload identities. Identical bytes still replay.
    port=next(iter(request['inputs']))
    request['inputs'][port]=upload(client,format_=PORTS[operation][port])
    replay=client.post('/api/v1/operations',json=request)
    assert replay.status_code==202 and replay.json()['operationId']==identifier and replay.json()['idempotentReplay']
    request['inputs'][port]=upload(client,b'different-input',PORTS[operation][port])
    assert client.post('/api/v1/operations',json=request).status_code==409
    cancelled=client.post(path+'/cancel').json()
    assert cancelled['status']=='cancelled' and cancelled['remoteTerminationConfirmed']
    assert client.get(path+'/results').status_code==409
    assert client.get(path+'/receipt').json()['status']=='cancelled'


def test_upload_hash_and_input_identity_are_verified(client):
    content=b'12345'
    metadata=dict(name='input.zip',sizeBytes=5,sha256=digest(content),mediaType='application/zip',format='zip')
    session=client.post('/api/v1/uploads',json=metadata).json()
    path=f"/api/v1/uploads/{session['uploadId']}"
    assert client.put(path+'/parts/0',content=content,headers={'X-Content-SHA256':digest(b'wrong')}).status_code==422
    assert client.post(path+'/complete').status_code==409
    artifact=upload(client);artifact['sha256']=digest(b'wrong')
    response=client.post('/api/v1/operations',json=dict(operationType='model.compile',clientRequestId='bad-identity',inputs={'project_package':artifact},parameters={}))
    assert response.status_code==422 and response.json()['error']['code']=='OPERATION_INPUT_INVALID'


def test_service_ports_match_every_v6_operator_version():
    source=Path(__file__).resolve().parents[3]/'source'
    product=json.loads((source/'products/groundwater/product-manifest.json').read_bytes())
    binding=next(item for item in product['remoteServices'] if item['serviceId']=='porous-groundwater')
    assert binding['apiVersion']==VERSION['apiVersion']
    assert binding['allowedServiceVersions']==[VERSION['serviceVersion']]
    assert binding['allowedModelVersions']==[VERSION['modelVersion']]
    assert set(binding['allowedOperations'])==set(PORTS)
    catalog=source/'packages/operator-pack/catalog/operators'
    seen=set()
    for path in catalog.glob('groundwater.porous.*/*/manifest.json'):
        spec=json.loads(path.read_bytes());operation=spec['remoteBinding']['operationType'];seen.add(operation)
        assert spec['runtime']=='remote_http' and spec['remoteBinding']['serviceId']=='porous-groundwater'
        assert {p['name']:p['formats'][0] for p in spec['inputs']}==PORTS[operation]
        expected=dict(_OUTPUTS[operation])
        if operation=='pest.calibrate' and spec['version']=='1.0.0':expected.pop('calibrated_model_package')
        assert {p['name']:p['formats'][0] for p in spec['outputs'] if p['kind']=='artifact' and p['name']!='run_receipt'}==expected
        assert any(p['name']=='run_receipt' and p['formats']==['json'] for p in spec['outputs'])
    assert seen==set(PORTS)


def test_exported_request_contract_matches_adapter_and_rejects_wrong_ports():
    schema=request_schema();Draft202012Validator.check_schema(schema)
    stored=Path(__file__).resolve().parents[1]/'contracts/remote-operation.v1.json'
    assert json.loads(stored.read_bytes())==schema
    validator=Draft202012Validator(schema)
    for operation,ports in PORTS.items():
        request=dict(operationType=operation,clientRequestId='contract',parameters={},inputs={
            port:dict(artifactId='artifact-'+'a'*32,sizeBytes=1,sha256=digest(b'x'),
                      mediaType=MEDIA_TYPES[fmt],format=fmt) for port,fmt in ports.items()})
        validator.validate(request)
        request['inputs']['unexpected']=next(iter(request['inputs'].values()))
        assert list(validator.iter_errors(request))


@pytest.mark.parametrize('operation',list(PORTS))
def test_result_manifest_pins_artifact_bytes_formats_and_download_paths(operation,tmp_path):
    parameters={'includeCalibratedModel':True}
    for port,fmt in _OUTPUTS[operation].items():
        (tmp_path/f'{port}.{fmt}').write_bytes(b'contract-output-'+port.encode())
    (tmp_path/'values.json').write_text('{}')
    result=OperationQueue._materialize(tmp_path,operation,parameters)
    assert {k:result[k] for k in VERSION}==VERSION
    for artifact in result['artifacts']:
        raw=(tmp_path/f"{artifact['port']}.{artifact['format']}").read_bytes()
        assert artifact['sizeBytes']==len(raw) and artifact['sha256']==digest(raw)
        assert artifact['mediaType']==MEDIA_TYPES[artifact['format']]
        assert artifact['downloadUrl']==f"/api/v1/operations/{tmp_path.name}/artifacts/{artifact['port']}"
