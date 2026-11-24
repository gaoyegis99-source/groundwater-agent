"""Publish two curated cases from exact local Data versions using the public API."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
import io
import json
from math import isfinite
from time import monotonic, sleep

import httpx
import pyarrow as pa
import pyarrow.parquet as parquet


@dataclass(frozen=True)
class Case:
    name: str
    title: str
    id_field: str
    numeric_fields: tuple[str, ...]
    nature: str
    source_checksum: str
    description: str


MONITORING = Case(
    'monitoring-trend', '监测趋势案例 · 合成月度井水位', 'well',
    ('hydraulic_head_m',), 'SYNTHETIC_TEST_ONLY',
    'sha256:6df800a8e81744dff184449103934852570409233a4669e028eccd3104225c6d',
    '从库内逐项模型验收监测表提取井号、观测日期和水位高程。供工程演示。',
)
FLOW = Case(
    'flow-field', '地下水流场案例 · USGS MRVA 2024', 'well_id',
    ('x', 'y', 'reference_elevation_m', 'depth_to_water_m'),
    'REAL_DERIVED_TEST_SAMPLE',
    'sha256:57e9ba790f34bb9f1a19c6c6ec3f05b02f5dcaa357a80ce5baf269b248c0675c',
    '库内 USGS 实测衍生井点样本。分批采集日期保留。流场是目标时点估算，流速依赖情景参数。',
)


def request(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    response = client.request(method, '/api/v12/data' + path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f'{method} {path}: {response.status_code} {response.text}')
    return response.json()


def timestamp(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time.min, timezone.utc)


def normalize(case: Case, content: bytes, row_count: int) -> tuple[bytes, str]:
    rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
    if not rows or len(rows) != row_count:
        raise ValueError(f'{case.name}: unexpected observation count')
    times = [timestamp(row['time']) for row in rows]
    identities = [row[case.id_field] for row in rows]
    if not all(identities) or len(set(zip(identities, times))) != row_count:
        raise ValueError(f'{case.name}: incomplete or duplicate observations')
    columns = {
        case.id_field: pa.array(identities, type=pa.string()),
        'time': pa.array(times, type=pa.timestamp('ms', tz='UTC')),
    }
    for name in case.numeric_fields:
        values = [float(row[name]) for row in rows]
        if not all(map(isfinite, values)):
            raise ValueError(f'{case.name}: non-finite values in {name}')
        columns[name] = pa.array(values, type=pa.float64())
    buffer = io.BytesIO()
    parquet.write_table(pa.table(columns), buffer)
    return buffer.getvalue(), f'{min(times).isoformat()}/{max(times).isoformat()}'


def source_case(client: httpx.Client, case: Case, version_id: str) -> tuple[dict, bytes, dict]:
    detail = request(client, 'GET', f'/versions/{version_id}')
    raw, = [item for item in detail['objects'] if item['role'] == 'raw']
    semantics = detail['semantics']
    if raw['format'] != 'csv' or raw['checksum'] != case.source_checksum or (case == FLOW and (
        semantics.get('crs') != 'EPSG:5070' or semantics.get('verticalDatum') != 'NAVD88'
    )):
        raise ValueError(f'{case.name}: source format or spatial reference does not match the curated case')
    response = client.get(f'/api/v12/data/versions/{version_id}/objects/{raw["objectId"]}/content')
    response.raise_for_status()
    if 'sha256:' + sha256(response.content).hexdigest() != raw['checksum']:
        raise ValueError(f'{case.name}: source checksum mismatch')
    data, coverage = normalize(case, response.content, detail['descriptor']['rowCount'])
    source = {
        'datasetId': detail['datasetId'], 'versionId': version_id,
        'semanticId': detail['semanticId'], 'objectId': raw['objectId'], 'checksum': raw['checksum'],
    }
    fields = {item['name']: item['fieldId'] for item in detail['descriptor']['fields']}
    meanings = semantics.get('fields') or {}
    selected = (case.id_field, 'time', *case.numeric_fields)
    semantic_fields = {
        f'f{index}': meanings[fields[name]]
        for index, name in enumerate(selected) if fields[name] in meanings
    }
    batch = sha256(data).hexdigest()
    output = {
        'name': case.name, 'title': case.title, 'format': 'parquet', 'policy': 'FILE_QUERYABLE',
        'tags': [f'case:groundwater-{case.name}', f'case-batch:{batch}', 'input:versionId', case.nature],
        'semantics': {
            'description': case.description, 'timezone': 'UTC', 'timeRange': coverage, 'fields': semantic_fields,
            'crs': semantics.get('crs'), 'verticalDatum': semantics.get('verticalDatum'),
            'limitations': [*(semantics.get('limitations') or []), '原始日期按 UTC 零时编码，精度仍为日。'],
            'source': 'workspace-case-from-existing-data',
        },
    }
    return output, data, source


def publish(client: httpx.Client, cases: list[tuple[dict, bytes, dict]], same_project_store: bool) -> dict:
    provenance = {'purpose': 'curated-groundwater-workspace-cases'}
    for output, _, origin in cases:
        name = output['name']
        provenance.update({f'{name}:{field}': value for field, value in origin.items()})
    manifest = {
        'outputs': [output for output, _, _ in cases],
        'inputVersions': list(dict.fromkeys(origin['versionId'] for _, _, origin in cases)) if same_project_store else [],
        'provenance': provenance,
    }
    idem = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    publication = request(client, 'POST', '/publications', json={**manifest, 'idempotencyKey': f'workspace-cases-{idem}'})
    path = '/publications/' + publication['publicationId']
    if publication['state'] == 'OPEN':
        for output, content, _ in cases:
            request(client, 'PUT', f'{path}/items/{output["name"]}', content=content,
                    headers={'X-Content-SHA256': 'sha256:' + sha256(content).hexdigest()})
        request(client, 'POST', path + '/prepare')
    deadline = monotonic() + 120
    while monotonic() < deadline:
        publication = request(client, 'GET', path)
        if publication['state'] == 'PUBLISHED':
            return publication['receipt']
        if publication['state'] == 'READY':
            return request(client, 'POST', path + '/commit')
        if publication['state'] in ('FAILED', 'ABORTED'):
            raise RuntimeError(f'{path}: {publication["state"]}')
        sleep(0.25)
    raise TimeoutError(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitoring-version', required=True)
    parser.add_argument('--flow-version', required=True)
    parser.add_argument('--source-port', type=int, default=28186)
    parser.add_argument('--target-port', type=int, default=28186)
    args = parser.parse_args()
    common = {'timeout': 30, 'trust_env': False, 'headers': {'X-Client-Type': 'cli'}}
    with (httpx.Client(base_url=f'http://127.0.0.1:{args.source_port}', **common) as source,
          httpx.Client(base_url=f'http://127.0.0.1:{args.target_port}', **common) as target):
        cases = [source_case(source, MONITORING, args.monitoring_version), source_case(source, FLOW, args.flow_version)]
        receipt = publish(target, cases, args.source_port == args.target_port)
        for item in receipt['outputs']:
            detail = request(target, 'GET', '/versions/' + item['versionId'])
            source_record = next((origin for output, _, origin in cases if output['name'] == item['name']))
            item.update(title=detail['title'], rowCount=detail['descriptor']['rowCount'], source=source_record)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
