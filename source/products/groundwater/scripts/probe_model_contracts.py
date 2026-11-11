"""Probe both deployed model adapters from the remote_http Worker network."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from platform_executor_service.remote_http import RemoteOperationError, RemoteOperationRunner


async def probe(config_path: Path) -> dict:
    config=json.loads(config_path.read_bytes())
    results={}
    async def checkpoint(_state):
        pass
    for service,operation in (
        ('groundwater-sustainability','sustainability.evaluate'),
        ('porous-groundwater','model.compile'),
    ):
        runner=RemoteOperationRunner(config,{
            'remoteBinding':{'serviceId':service,'operationType':operation},
        },checkpoint)
        async with runner._client() as client:
            for path in ('/healthz','/readyz'):
                response=await client.get(path,headers={'Authorization':''})
                assert response.status_code==200,(service,path,response.status_code)
            version=(await client.get('/version',headers={'Authorization':''})).json()
            runner._validate_version(version)
            response=await client.get('/api/v1/capabilities')
            runner._check_response(response,{200})
            assert set(config['remoteServices'][service]['allowedOperations'])<=set(response.json()['operations'])
            response=await client.get('/api/v1/capabilities',headers={'Authorization':'Bearer invalid-test-token'})
            assert response.status_code==401 and response.headers['www-authenticate']=='Bearer'
            key='contract-probe-'+uuid4().hex
            response=await client.post('/api/v1/operations',json={
                'operationType':operation,'clientRequestId':key,'parameters':{},
            })
            assert response.status_code==422
            error=response.json()['error']
            assert error['code']=='REQUEST_VALIDATION_ERROR'
            assert error['request_id']==response.headers['x-request-id']
            try:
                runner._check_response(response,{202})
            except RemoteOperationError as exc:
                assert exc.code=='REMOTE_INPUT_VALIDATION_FAILED'
                assert 'body.inputs (missing)' in str(exc)
            else:
                raise AssertionError('FIELD_ERROR_NOT_RECOGNIZED_BY_WORKER')
            assert (await client.get('/api/v1/operations/by-client-request/'+key)).status_code==404
            content=b'{}'
            created=await client.post('/api/v1/uploads',json={
                'name':'contract-probe.json','sizeBytes':len(content),
                'sha256':'sha256:'+hashlib.sha256(content).hexdigest(),
                'mediaType':'application/json','format':'json',
            })
            assert created.status_code==201
            path='/api/v1/uploads/'+created.json()['uploadId']
            response=await client.put(path+'/parts/0',content=content,
                                      headers={'X-Content-SHA256':'sha256:'+'0'*64})
            assert response.status_code==422
            assert (await client.post(path+'/complete')).status_code==409
            results[service]={
                'status':'PASS',**version,'health':'PASS','authentication':'PASS',
                'fieldError':'PASS','rejectedInputNotEnqueued':'PASS','uploadIntegrity':'PASS',
            }
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executor-config',type=Path,default=Path('/config/executor-config.json'))
    args=parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.executor_config)),ensure_ascii=False,indent=2))
