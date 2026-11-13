import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { promisify } from 'node:util';
import { expect, test, type Page } from '@playwright/test';
import type { ExecutionTask, History, ResourceDetail, ResourceReference, Result } from '../src/types';
import type { components } from '../src/generated/public-api-v12';
import { isolatedInteractionClients, openSummaryProposal, type InteractionSession } from './interaction-isolated-helpers';

test.use({ trace: 'off', extraHTTPHeaders: {} });
test.skip(process.env.PLATFORM_INTERACTION_GATE !== '1', 'Run with the isolated interaction gate.');

const exec = promisify(execFile);

async function isolatedNativeWorker(baseURL: string) {
  const id = process.env.PLATFORM_RECOVERY_NATIVE_WORKER ?? '';
  expect(id).toMatch(/^[a-f0-9]{12,64}$/);
  const docker = process.env.PLATFORM_RECOVERY_DOCKER ?? 'docker';
  const command = async (...args: string[]) => (await exec(docker, args, { timeout: 15_000 })).stdout.trim();
  const inspect = async () => {
    return JSON.parse(await command('inspect', '--type', 'container', '--format',
      '{"labels":{{json .Config.Labels}},"state":{{json .State}}}', id)) as {
      labels: Record<string, string>;
      state: { Running: boolean; Paused: boolean };
    };
  };
  const native = await inspect();
  const project = native.labels['com.docker.compose.project'];
  expect(project).toMatch(/^platform-groundwater-e2e-[a-f0-9]{12}$/);
  expect(native.labels['com.docker.compose.service']).toBe('executor-native-worker');
  expect(native.state).toMatchObject({ Running: true, Paused: false });
  const port = new URL(baseURL).port;
  const ports = await command('ps', '--filter', `label=com.docker.compose.project=${project}`,
    '--filter', 'label=com.docker.compose.service=gateway', '--format', '{{.Ports}}');
  expect(ports).toContain(`127.0.0.1:${port}->8080/tcp`);
  return {
    project,
    async stop() {
      await command('stop', '--time', '3', id);
      expect((await inspect()).state.Running).toBe(false);
    },
    async start() {
      if (!(await inspect()).state.Running) await command('start', id);
      expect((await inspect()).state).toMatchObject({ Running: true, Paused: false });
    },
  };
}

type StreamEvent = {
  id: number;
  type: string;
  data: { workflowRunId?: string; confirmationId?: string; sourceWorkflowRunId?: string; stage?: string };
};
type BrowserStream = { opens: number; events: StreamEvent[] };

async function observeBrowserStream(page: Page) {
  await page.addInitScript(() => {
    const captured: BrowserStream = { opens: 0, events: [] };
    Object.assign(window, { recoveryStream: captured });
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        if (!String(url).includes('/events?')) return;
        this.addEventListener('open', () => { captured.opens += 1; });
        for (const type of ['workflow.queued', 'job.running', 'workflow.timed_out', 'confirmation.recovery_created',
          'workflow.retry_queued', 'workflow.stage_changed', 'workflow.succeeded', 'workflow.recovery_completed']) {
          this.addEventListener(type, (event: MessageEvent<string>) => {
            const id = Number(event.lastEventId);
            if (!captured.events.some(entry => entry.id === id)) {
              captured.events.push({ id, type, data: JSON.parse(event.data).data });
            }
          });
        }
      }
    };
  });
}

function terminalEvents(raw: string): StreamEvent[] {
  return [...raw.matchAll(/^id: (\d+)\nevent: ([\w.]+)\ndata: (.+)$/gm)]
    .map(([, id, type, payload]) => ({ id: Number(id), type, data: JSON.parse(payload).data }));
}

type Run = {
  runId: string;
  status: string;
  error: string | null;
  remoteJobId: string | null;
  durationMs: number | null;
  outputs: ResourceReference[];
};

test('recovers an actual timed-out workflow across Web, TLI and SDK', async ({ page, baseURL }, info) => {
  test.setTimeout(225_000);
  const clients = await isolatedInteractionClients(page, baseURL);
  const native = await isolatedNativeWorker(baseURL ?? process.env.PLATFORM_BASE_URL!);
  await observeBrowserStream(page);
  const screenshot = (name: string) => page.screenshot({ path: info.outputPath(name + '.png') });
  const record = (name: string, value: unknown) => writeFileSync(info.outputPath(name + '.json'), JSON.stringify(value, null, 2) + '\n');
  const responseFor = (path: string) => page.waitForResponse(response =>
    new URL(response.url()).pathname === path && response.request().method() === 'POST');
  const initial = await openSummaryProposal(page, clients, `interaction-recovery-${randomUUID()}`);
  const { sessionId, projectId, versionId, confirmationId, goalRevision, cursor } = initial;
  const path = `/api/v12/sessions/${sessionId}`;
  const history = () => clients.get<History>(path + '/messages');
  const session = () => clients.get<InteractionSession>(path);
  const tasks = async () => (await history()).turns.find(turn => turn.goalRevision === goalRevision)?.executionTasks ?? [];
  const workflow = (id: string) => clients.get<Run>(`/api/v12/workflow-runs/${id}?projectId=${projectId}`);
  const nodeDiagnostics = (id: string) => clients.get<components['schemas']['kernel__WorkflowNodeDiagnosticsResponse']>(
    `/api/v12/workflow-runs/${id}/nodes?projectId=${projectId}`,
  );
  const card = (id: string) => page.locator(`[data-workflow-run-id="${id}"]`);
  await screenshot('01-initial-confirmation');

  let failedTask!: ExecutionTask;
  let failedRun!: Run;
  let follow!: Promise<{ output?: string; failure?: Error; seconds: number }>;
  try {
    await native.stop();
    const confirmed = responseFor(path + '/confirm');
    await page.getByTestId('confirm-execution').click();
    const decision = await confirmed;
    expect(decision.status()).toBe(202);
    expect(decision.request().postDataJSON()).toEqual({ confirmationId });
    // The real session SSE endpoint closes each connection after 25 seconds.
    // Holding the Worker through the real 60-second deadline forces TLI to reconnect.
    const followingStarted = Date.now();
    follow = clients.cli('--session', sessionId, 'events', '--cursor', String(cursor), '--follow')
      .then(output => ({ output, seconds: (Date.now() - followingStarted) / 1000 }),
        (failure: Error) => ({ failure, seconds: (Date.now() - followingStarted) / 1000 }));
    await expect.poll(async () => (await tasks())[0]?.status, {
      timeout: 110_000, intervals: [500, 1000],
    }).toBe('TIMED_OUT');
    [failedTask] = await tasks();
    expect(failedTask).toMatchObject({ status: 'TIMED_OUT', attempt: 1, outputCount: 0, isLatestAttempt: true,
      confirmationId, errorCode: 'JOB_DEADLINE_EXCEEDED' });
    failedRun = await workflow(failedTask.workflowRunId);
    expect(failedRun).toMatchObject({ status: 'TIMED_OUT', error: 'JOB_DEADLINE_EXCEEDED', outputs: [] });
    expect(failedRun.remoteJobId).toBeTruthy();
    expect(failedRun.durationMs).toBeGreaterThan(45_000);
    expect((await session()).remoteOutputs).toEqual([]);
    await expect(card(failedTask.workflowRunId)).toHaveAttribute('data-status', 'TIMED_OUT');
  } finally {
    await native.start();
  }

  const firstFollow = await follow;
  if (firstFollow.failure) throw firstFollow.failure;
  const firstEvents = terminalEvents(firstFollow.output!);
  expect(firstFollow.seconds).toBeGreaterThan(45);
  expect(firstEvents.length).toBeGreaterThan(1);
  expect(firstEvents.every((event, index) => event.id > (index ? firstEvents[index - 1].id : cursor))).toBe(true);
  expect(firstEvents).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: 'job.running', data: expect.objectContaining({ workflowRunId: failedTask.workflowRunId }) }),
    expect.objectContaining({ type: 'workflow.timed_out', data: expect.objectContaining({ workflowRunId: failedTask.workflowRunId }) }),
  ]));
  expect(['agent.complete', 'agent.failed', 'workflow.timed_out']).toContain(firstEvents.at(-1)?.type);
  writeFileSync(info.outputPath('tli-follow.sse'), firstFollow.output!);
  record('first-attempt', { nativeProject: native.project, task: failedTask, run: failedRun, cursor, followSeconds: firstFollow.seconds, firstEvents });

  const workspace = page.getByTestId('workspace-panel');
  await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '运行' }).click();
  const tasksTab = workspace.getByRole('tab', { name: '任务', exact: true });
  await tasksTab.click();
  const failedNodes = workspace.locator(`[data-execution-run-id="${failedTask.workflowRunId}"]`);
  await failedNodes.getByText('节点详情', { exact: true }).click();
  await expect(failedNodes).toContainText('JOB_DEADLINE_EXCEEDED', { timeout: 15_000 });
  const originalDiagnostics = await nodeDiagnostics(failedTask.workflowRunId);
  expect(originalDiagnostics).toMatchObject({ workflowRunId: failedTask.workflowRunId, availability: 'AVAILABLE' });
  expect(originalDiagnostics.nodes).toEqual(expect.arrayContaining([
    expect.objectContaining({ status: 'CANCELLED', errorCode: 'JOB_DEADLINE_EXCEEDED', attemptCount: 0 }),
  ]));
  const recover = workspace.getByRole('button', { name: '准备恢复未完成步骤', exact: true });
  await expect(recover).toBeEnabled();
  await expect(card(failedTask.workflowRunId).getByRole('link', { name: '下载', exact: true })).toHaveCount(0);
  await screenshot('02-timeout-visible');
  const recovering = responseFor(path + `/workflow-runs/${failedTask.workflowRunId}/recoveries`);
  await recover.click();
  const recoveryResponse = await recovering;
  expect(recoveryResponse.status()).toBe(201);
  const { requestId, expectedGoalRevision } = recoveryResponse.request().postDataJSON();
  expect(expectedGoalRevision).toBe(goalRevision);
  expect(requestId).toBeTruthy();
  const recovery = (await recoveryResponse.json() as InteractionSession).confirmation!;
  expect(recovery).toMatchObject({ kind: 'RECOVERY', revision: 2, status: 'PENDING', sourceWorkflowRunId: failedTask.workflowRunId,
    actions: initial.confirmation.actions });
  const recoveryCard = page.locator(`[data-confirmation-id="${recovery.confirmationId}"]`);
  await expect(recoveryCard).toContainText('失败恢复批次');
  await expect(recoveryCard.getByTestId('confirm-execution')).toBeEnabled();
  const seenByTli = JSON.parse(await clients.cli('--session', sessionId, 'status')) as InteractionSession;
  expect(seenByTli.confirmation).toEqual(recovery);
  expect(seenByTli.workflowRunIds).toEqual([failedTask.workflowRunId]);
  expect(seenByTli.remoteOutputs).toEqual([]);
  expect(await tasks()).toHaveLength(1);
  record('recovery-pending', { requestId, confirmation: recovery, taskIds: seenByTli.workflowRunIds });
  await screenshot('03-recovery-confirmation');

  const recoveryCursor = (await history()).eventCursor;
  expect(await clients.cli('--session', sessionId, 'events', '--cursor', String(recoveryCursor), '--follow')).toBe('');
  const sdkApproval = await clients.python<{ replayed: InteractionSession; confirmed: InteractionSession }>(`
import json
import os
import sys
from groundwater_api_client import GroundwaterClient, models, operations

origin, session, failed_run, request, revision, confirmation = sys.argv[1:]
with GroundwaterClient(origin, api_token=os.environ["GROUNDWATER_API_TOKEN"]) as client:
    replayed = client.call(
        operations.KERNEL_RECOVER_WORKFLOW,
        path={"sessionId": session, "workflowRunId": failed_run},
        body=models.KernelWorkflowRecoveryRequest(requestId=request, expectedGoalRevision=int(revision)),
    )
    assert replayed.confirmation["confirmationId"] == confirmation
    assert replayed.workflow_run_ids == [failed_run]
    confirmed = client.confirm(session, confirmation)
    print(json.dumps({
        "replayed": replayed.model_dump(mode="json", by_alias=True),
        "confirmed": confirmed.model_dump(mode="json", by_alias=True),
    }))
`, sessionId, failedTask.workflowRunId, requestId, String(goalRevision), recovery.confirmationId);
  expect(sdkApproval.replayed.confirmation?.confirmationId).toBe(recovery.confirmationId);
  expect(sdkApproval.confirmed.sessionId).toBe(sessionId);
  const followingRecovery = clients.cli('--session', sessionId, 'events', '--cursor', String(recoveryCursor), '--follow')
    .then(output => ({ output, failure: undefined }), (failure: Error) => ({ output: undefined, failure }));

  await expect.poll(async () => (await tasks()).map(task => task.status), { timeout: 80_000 }).toEqual(['TIMED_OUT', 'SUCCEEDED']);
  const [retained, restored] = await tasks();
  expect(retained).toMatchObject({ workflowRunId: failedTask.workflowRunId, attempt: 1, status: 'TIMED_OUT',
    isLatestAttempt: false, outputCount: 0 });
  expect(restored).toMatchObject({ attempt: 2, status: 'SUCCEEDED', isLatestAttempt: true, outputCount: 2,
    retryOfWorkflowRunId: failedTask.workflowRunId, confirmationId: recovery.confirmationId });
  expect(restored.workflowRunId).not.toBe(retained.workflowRunId);
  const finalRun = await workflow(restored.workflowRunId);
  expect(finalRun).toMatchObject({ status: 'SUCCEEDED', error: null });
  expect(finalRun.remoteJobId).toBeTruthy();
  expect(finalRun.remoteJobId).not.toBe(failedRun.remoteJobId);
  expect(await workflow(retained.workflowRunId)).toEqual(failedRun);
  const recoveryFollow = await followingRecovery;
  if (recoveryFollow.failure) throw recoveryFollow.failure;
  const resumedEvents = terminalEvents(recoveryFollow.output!);
  expect(resumedEvents.every((event, index) => event.id > (index ? resumedEvents[index - 1].id : recoveryCursor))).toBe(true);
  expect(resumedEvents).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: 'workflow.succeeded', data: expect.objectContaining({ workflowRunId: restored.workflowRunId }) }),
  ]));
  expect(resumedEvents.some(event => event.type === 'workflow.timed_out')).toBe(false);
  const terminalCursor = (await history()).eventCursor;
  expect(await clients.cli('--session', sessionId, 'events', '--cursor', String(terminalCursor), '--follow')).toBe('');
  writeFileSync(info.outputPath('tli-recovery-follow.sse'), recoveryFollow.output!);

  const finalCard = card(restored.workflowRunId);
  await expect(finalCard).toHaveAttribute('data-status', 'SUCCEEDED');
  await expect(finalCard).toContainText('已通过第 2 次执行恢复');
  await expect(finalCard.getByRole('link', { name: '下载', exact: true })).toHaveCount(2);
  await expect(card(retained.workflowRunId)).toHaveAttribute('data-status', 'TIMED_OUT');
  await expect(card(retained.workflowRunId).getByRole('link', { name: '下载', exact: true })).toHaveCount(0);
  const browserStream = await page.evaluate(() => (window as unknown as { recoveryStream: BrowserStream }).recoveryStream);
  expect(browserStream.opens).toBeGreaterThanOrEqual(2);
  expect(browserStream.events).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: 'workflow.timed_out', data: expect.objectContaining({ workflowRunId: retained.workflowRunId }) }),
    expect.objectContaining({ type: 'confirmation.recovery_created', data: expect.objectContaining({ confirmationId: recovery.confirmationId }) }),
    expect.objectContaining({ type: 'workflow.succeeded', data: expect.objectContaining({ workflowRunId: restored.workflowRunId }) }),
  ]));
  await screenshot('04-recovery-live-success');

  await page.reload();
  await expect(finalCard).toHaveAttribute('data-status', 'SUCCEEDED');
  await expect(card(retained.workflowRunId)).toHaveAttribute('data-status', 'TIMED_OUT');
  await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
  await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '运行' }).click();
  await expect(recover).toHaveCount(0);
  const restoredNodes = workspace.locator(`[data-execution-run-id="${restored.workflowRunId}"]`);
  await restoredNodes.getByText('节点详情', { exact: true }).click();
  await expect(restoredNodes).toContainText('节点已完成');
  const restoredDiagnostics = await nodeDiagnostics(restored.workflowRunId);
  expect(restoredDiagnostics).toMatchObject({ workflowRunId: restored.workflowRunId, availability: 'AVAILABLE' });
  expect(restoredDiagnostics.nodes).toEqual(expect.arrayContaining([
    expect.objectContaining({ status: 'SUCCEEDED', errorCode: null, attemptCount: 1 }),
  ]));
  expect(await nodeDiagnostics(retained.workflowRunId)).toEqual(originalDiagnostics);
  const graph = await clients.get<{ workflowRunId: string; availability: string; nodes: { nodeId: string; dependsOn: string[] }[] }>(
    `/api/v12/workflow-runs/${restored.workflowRunId}/graph?projectId=${encodeURIComponent(projectId)}`,
  );
  expect(graph).toMatchObject({ workflowRunId: restored.workflowRunId, availability: 'AVAILABLE' });
  expect(graph.nodes.map(node => node.nodeId).sort()).toEqual(restoredDiagnostics.nodes.map(node => node.nodeId).sort());
  record('workflow-graph', graph);
  record('node-diagnostics', { original: originalDiagnostics, recovered: restoredDiagnostics });
  await screenshot('06-workflow-node-diagnostics');
  await workspace.getByRole('button', { name: '返回对话' }).click();
  const resultPage = JSON.parse(await clients.cli('--session', sessionId, 'results')) as { items: Result[] };
  expect(resultPage.items).toHaveLength(1);
  const [outcome] = resultPage.items;
  expect(outcome).toMatchObject({ status: 'COMPLETE', attempt: 2, workflowRunId: restored.workflowRunId });
  expect(outcome.remoteOutputs.map(item => item.outputName).sort()).toEqual(['summary', 'summary-details']);
  expect(outcome.remoteOutputs).toEqual(finalRun.outputs);
  const downloadEvidence: { output: ResourceReference; publicationId: string; path: string }[] = [];
  for (const output of outcome.remoteOutputs) {
    const version = await clients.get<ResourceDetail>(`/api/v12/data/versions/${output.versionId}`);
    expect(version.inputVersions).toContain(versionId);
    expect(version.publicationId).toBeTruthy();
    const downloading = page.waitForEvent('download');
    await finalCard.locator(`[data-resource-id="${output.versionId}"]`).getByRole('link', { name: '下载', exact: true }).click();
    const destination = info.outputPath(`${output.outputName}.json`);
    await (await downloading).saveAs(destination);
    const data = readFileSync(destination);
    expect(data.length).toBe(output.sizeBytes);
    expect(`sha256:${createHash('sha256').update(data).digest('hex')}`).toBe(output.checksum);
    downloadEvidence.push({ output, publicationId: version.publicationId, path: destination });
  }
  expect(new Set(downloadEvidence.map(item => item.publicationId)).size).toBe(1);
  const summary = JSON.parse(readFileSync(info.outputPath('summary.json'), 'utf8')).hydraulic_head_m;
  expect(summary).toMatchObject({ count: 2, min: 81.2, max: 82.4 });
  expect(summary.mean).toBeCloseTo(81.8, 10);
  await screenshot('05-recovery-restored');
  record('cross-interface-recovery', {
    status: 'PASS', sessionId, inputVersionId: versionId, initialConfirmationId: confirmationId,
    recoveryConfirmationId: recovery.confirmationId, original: retained, recovered: restored,
    firstFollowSeconds: firstFollow.seconds, browserStream, outcome, downloads: downloadEvidence,
  });
});
