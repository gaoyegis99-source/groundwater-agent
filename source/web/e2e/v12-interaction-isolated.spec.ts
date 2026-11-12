import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { ResourceReference } from '../src/types';
import { isolatedInteractionClients, openSummaryProposal } from './interaction-isolated-helpers';

test.use({ trace: 'off', extraHTTPHeaders: {}, actionTimeout: 20_000 });

const sdkWorkflow = String.raw`
import csv
import hashlib
import io
import json
import os
import sys
import sysconfig
import time
from contextlib import closing
from math import isclose
from pathlib import Path
from statistics import mean, pstdev

import groundwater_api_client as sdk
import httpx
from platform_protocol_contracts import ApiProblem, TransportError

origin, session, project, confirmation, version, cursor, lose_response, out_dir = sys.argv[1:]
cursor = int(cursor)
lose_response = lose_response == '1'
out_dir = Path(out_dir)
out_dir.mkdir(parents=True, exist_ok=True)
token = os.environ['GROUNDWATER_API_TOKEN']
assert Path(sdk.__file__).resolve().is_relative_to(sysconfig.get_path('purelib')), sdk.__file__


class DiscardConfirmationResponse(httpx.BaseTransport):
    """Discard one accepted response after the real Gateway has handled it."""

    def __init__(self):
        self.transport = httpx.HTTPTransport()
        self.accepted_status = None

    def handle_request(self, request):
        response = self.transport.handle_request(request)
        if request.method != 'POST' or request.url.path != f'/api/v12/sessions/{session}/confirm':
            return response
        try:
            assert self.accepted_status is None, 'SDK unexpectedly retried the POST itself'
            assert json.loads(request.content) == {'confirmationId': confirmation}
            assert response.status_code == 202, response.status_code
            response.read()
            assert response.json()['sessionId'] == session
            self.accepted_status = response.status_code
        finally:
            response.close()
        raise httpx.ReadError('test discards the real Gateway 202', request=request)

    def close(self):
        self.transport.close()


def wait_session(client, predicate, description):
    for _ in range(400):
        state = client.session(session)
        if predicate(state):
            return state
        if state.agent_steps[-1:] in (['FAILED'], ['TIMED_OUT'], ['CANCELLED']):
            raise AssertionError((description, state.agent_steps, client.history(session).turns[-1]))
        time.sleep(0.15)
    raise AssertionError((description, state.agent_steps))


def events_until(client, after, terminal):
    buffer = b''
    events = []
    with closing(client.events(session, cursor=after)) as stream:
        for chunk in stream:
            buffer += chunk
            while b'\n\n' in buffer:
                frame, buffer = buffer.split(b'\n\n', 1)
                fields = dict(line.split(b':', 1) for line in frame.splitlines() if b':' in line)
                if b'id' not in fields:
                    continue
                event_id = int(fields[b'id'])
                assert event_id > (events[-1]['id'] if events else after)
                event_type = fields[b'event'].strip().decode()
                payload = json.loads(fields[b'data'])
                assert payload['schemaVersion'] == 'session-event.v20' and payload['type'] == event_type
                events.append({'id': event_id, 'type': event_type, 'workflowRunId': payload['data'].get('workflowRunId')})
                if event_type == terminal:
                    return events
    raise AssertionError((terminal, events))


with sdk.GroundwaterClient(origin, api_token=token, timeout=15) as client:
    before = client.session(session)
    assert before.project_id == project and before.agent_steps[-1] == 'WAITING_CONFIRMATION'
    assert before.confirmation['confirmationId'] == confirmation and before.confirmation['status'] == 'PENDING'
    assert before.workflow_run_ids == [] and before.remote_outputs == []
    assert not any(item.get('workflowRunId') for item in client.results(session).items)
    action, = before.confirmation['actions']
    assert action['tool'] == 'run_summary' and action['modelId'] == 'groundwater.summary'
    assert action['modelVersion'] == '3.1.0' and action['workflowVersion'] == '1.1.0'
    assert action['arguments'] == {'versionId': version, 'axis': 'columns', 'column': 'hydraulic_head_m', 'unit': ''}
    reference = client.input_reference(version).model_dump(mode='json', by_alias=True, exclude_none=True)
    assert list(action['inputReferences']) == ['versionId']
    assert all(action['inputReferences']['versionId'][key] == value for key, value in reference.items())
    history = client.history(session)
    assert history.event_cursor == cursor and history.turns[-1]['executionTasks'] == []
    assert any(item['role'] == 'user' and item['inputReferences'][0]['versionId'] == version for item in history.items if item.get('inputReferences'))
    raw_input = b''.join(client.download_object(version, reference['objectId']))
    assert len(raw_input) == reference['sizeBytes'] and 'sha256:' + hashlib.sha256(raw_input).hexdigest() == reference['checksum']
    observations = [float(row['hydraulic_head_m']) for row in csv.DictReader(io.StringIO(raw_input.decode()))]
    assert observations == [81.2, 82.4]

    receipt = {'mode': 'normal', 'confirmationId': confirmation}
    if lose_response:
        fault = DiscardConfirmationResponse()
        with httpx.Client(transport=fault, trust_env=False, timeout=15) as http, sdk.GroundwaterClient(origin, api_token=token, client=http) as lossy:
            try:
                lossy.confirm(session, confirmation)
            except TransportError as error:
                assert str(error) == 'HTTP_TRANSPORT_FAILED' and isinstance(error.__cause__, httpx.ReadError)
            else:
                raise AssertionError('SDK did not observe the discarded response')
        assert fault.accepted_status == 202
        reconciled = wait_session(client, lambda state: state.confirmation['status'] == 'CONFIRMED' and len(state.workflow_run_ids) == 1, 'reconcile after lost 202')
        assert reconciled.confirmation['confirmationId'] == confirmation
        original_id, = reconciled.workflow_run_ids
        try:
            replayed = client.confirm(session, confirmation)
        except ApiProblem as error:
            assert error.problem.status == 409 and error.problem.code == 'CONFIRMATION_NOT_CURRENT', error.problem
            retry = {'status': 409, 'code': error.problem.code}
        else:
            assert replayed.session_id == session and replayed.confirmation['confirmationId'] == confirmation
            assert replayed.workflow_run_ids == [original_id]
            retry = {'status': 202, 'workflowRunIds': replayed.workflow_run_ids}
        receipt = {'mode': 'lost-202', 'confirmationId': confirmation, 'gatewayAcceptedStatus': fault.accepted_status,
                   'sdkError': 'HTTP_TRANSPORT_FAILED', 'workflowRunIdBeforeRetry': original_id, 'retry': retry}
    else:
        accepted = client.confirm(session, confirmation)
        assert accepted.session_id == session and accepted.confirmation['confirmationId'] == confirmation

    completed = wait_session(client, lambda state: state.agent_steps[-1:] == ['COMPLETE'], 'await terminal session')
    workflow_id, = completed.workflow_run_ids
    if lose_response:
        assert workflow_id == original_id
    run = client.workflow_run(workflow_id, project_id=project)
    assert run.status == 'SUCCEEDED' and run.run_id == workflow_id and run.project_id == project
    assert run.model_id == action['modelId'] and run.arguments == action['arguments']
    assert run.model_checksum == action['modelChecksum'] and run.workflow_checksum == action['workflowChecksum']
    task, = client.history(session).turns[-1]['executionTasks']
    assert task['workflowRunId'] == workflow_id and task['confirmationId'] == confirmation and task['attempt'] == 1
    output_set, = client.results(session).items
    assert output_set['workflowRunId'] == workflow_id and output_set['remoteOutputs'] == run.outputs == completed.remote_outputs

    queued = events_until(client, cursor, 'workflow.queued')
    events = queued + events_until(client, queued[-1]['id'], 'agent.complete')
    assert [item['workflowRunId'] for item in events if item['type'] == 'workflow.queued'] == [workflow_id], events
    assert [item['workflowRunId'] for item in events if item['type'] == 'workflow.succeeded'] == [workflow_id], events
    assert [item['id'] for item in events] == sorted({item['id'] for item in events}), events
    assert {item['outputName'] for item in run.outputs} == {'summary', 'summary-details'} and len(run.outputs) == 2

    for output in run.outputs:
        data = b''.join(client.download_object(output['versionId'], output['objectId']))
        assert len(data) == output['sizeBytes'] and 'sha256:' + hashlib.sha256(data).hexdigest() == output['checksum']
        assert client.version(output['versionId']).input_versions == [version]
        (out_dir / (output['outputName'] + '.json')).write_bytes(data)
    stats = json.loads((out_dir / 'summary.json').read_bytes())['hydraulic_head_m']
    assert stats['rowCount'] == stats['count'] == 2 and stats['nullCount'] == stats['nonFiniteCount'] == 0
    assert isclose(stats['mean'], mean(observations)) and isclose(stats['stddev'], pstdev(observations))
    print(json.dumps({'sessionId': session, 'workflowRunId': workflow_id, 'outputs': run.outputs,
                      'cursor': cursor, 'reconnectCursor': queued[-1]['id'], 'events': events,
                      'statistics': stats, 'confirmation': receipt}))
`;

interface SdkEvidence {
  sessionId: string;
  workflowRunId: string;
  outputs: (ResourceReference & { outputName: string })[];
  cursor: number;
  reconnectCursor: number;
  events: { id: number; type: string; workflowRunId?: string }[];
  statistics: { count: number; mean: number; stddev: number };
  confirmation: { mode: string; confirmationId: string; gatewayAcceptedStatus?: number; sdkError?: string;
    workflowRunIdBeforeRetry?: string; retry?: { status: number } };
}

for (const scenario of [
  { marker: 'isolated-interaction-summary', title: 'Web, installed SDK and TLI share a successful workflow', loseResponse: false },
  { marker: 'isolated-interaction-confirmation-loss', title: 'losing an accepted confirmation response does not execute twice', loseResponse: true },
]) {
  test(scenario.title, async ({ page, baseURL }, info) => {
    test.skip(process.env.PLATFORM_INTERACTION_GATE !== '1', 'Requires the isolated interaction Compose gate.');
    test.setTimeout(240_000);
    const clients = await isolatedInteractionClients(page, baseURL);
    const proposal = await openSummaryProposal(page, clients, scenario.marker);
    await page.screenshot({ path: info.outputPath('shared-confirmation.png') });
    writeFileSync(info.outputPath('frozen-proposal.json'), JSON.stringify(proposal, null, 2));
    const { sessionId, projectId, confirmationId, versionId, cursor } = proposal;
    const sdkOutput = info.outputPath('sdk-downloads');
    const result = await clients.python<SdkEvidence>(sdkWorkflow, sessionId, projectId, confirmationId,
      versionId, String(cursor), scenario.loseResponse ? '1' : '0', sdkOutput);
    expect(result.sessionId).toBe(sessionId);
    expect(result.confirmation.confirmationId).toBe(confirmationId);
    expect(result.statistics.count).toBe(2);
    expect(result.statistics.mean).toBeCloseTo(81.8, 10);
    expect(result.statistics.stddev).toBeCloseTo(0.6, 10);
    if (scenario.loseResponse) {
      expect(result.confirmation).toMatchObject({
        mode: 'lost-202', gatewayAcceptedStatus: 202, sdkError: 'HTTP_TRANSPORT_FAILED',
        workflowRunIdBeforeRetry: result.workflowRunId,
      });
      expect([202, 409]).toContain(result.confirmation.retry?.status);
    }
    writeFileSync(info.outputPath('sdk-verification.json'), JSON.stringify(result, null, 2));

    const followed = await clients.cli('--session', sessionId, 'events', '--cursor', String(cursor), '--follow');
    writeFileSync(info.outputPath('tli-events.sse'), followed);
    expect(followed.match(/^event: workflow\.queued$/gm)).toHaveLength(1);
    expect(followed.match(/^event: workflow\.succeeded$/gm)).toHaveLength(1);
    expect(followed).toContain('event: agent.complete');
    expect([...followed.matchAll(/^id: (\d+)$/gm)].map(match => Number(match[1])))
      .toEqual(result.events.map(event => event.id));

    const listed = JSON.parse(await clients.cli('--session', sessionId, 'results'));
    expect(listed.items).toMatchObject([{ workflowRunId: result.workflowRunId, remoteOutputs: result.outputs }]);
    const cards = page.getByTestId('result-card');
    await expect(cards).toHaveCount(1);
    await expect(cards).toHaveAttribute('data-workflow-run-id', result.workflowRunId);
    await expect(cards).toHaveAttribute('data-status', 'SUCCEEDED');
    await expect(cards.getByRole('link', { name: '下载', exact: true })).toHaveCount(2);
    for (const output of result.outputs) {
      const webPath = info.outputPath(`web-${output.outputName}.json`);
      const tliPath = info.outputPath(`tli-${output.outputName}.json`);
      const link = cards.locator(`[data-resource-id="${output.versionId}"]`).getByRole('link', { name: '下载', exact: true });
      await expect(link).toHaveAttribute('href', `/api/v12/data/versions/${output.versionId}/objects/${output.objectId}/content`);
      await expect(link).toHaveAttribute('download', `${output.outputName}.json`);
      const downloading = page.waitForEvent('download');
      await link.click();
      const download = await downloading;
      expect(download.suggestedFilename()).toMatch(/\.json$/);
      await download.saveAs(webPath);
      await clients.cli('download', output.versionId, output.objectId, tliPath);
      for (const path of [webPath, tliPath]) {
        const bytes = readFileSync(path);
        expect(bytes.length, path).toBe(output.sizeBytes);
        expect('sha256:' + createHash('sha256').update(bytes).digest('hex'), path).toBe(output.checksum);
      }
    }
    await page.reload();
    await expect(cards).toHaveCount(1);
    await expect(cards).toHaveAttribute('data-workflow-run-id', result.workflowRunId);
    await expect(cards).toHaveAttribute('data-status', 'SUCCEEDED');
    await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('shared-result.png') });
    writeFileSync(info.outputPath('interaction-verification.json'), JSON.stringify({
      status: 'PASS', ...result, projectId, versionId, browserDownloads: 2, tliDownloads: 2, sdkDownloads: 2,
    }, null, 2));
  });
}
