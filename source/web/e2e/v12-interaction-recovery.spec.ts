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

async function isolatedWorker(baseURL: string, service = 'executor-native-worker') {
  const id = (service === 'data-worker' ? process.env.PLATFORM_PUBLICATION_DATA_WORKER : process.env.PLATFORM_RECOVERY_NATIVE_WORKER) ?? '';
  expect(id).toMatch(/^[a-f0-9]{12,64}$/);
  const docker = process.env.PLATFORM_RECOVERY_DOCKER ?? 'docker';
  const command = async (...args: string[]) => (await exec(docker, args, { timeout: 40_000 })).stdout.trim();
  const inspect = async () => {
    return JSON.parse(await command('inspect', '--type', 'container', '--format',
      '{"labels":{{json .Config.Labels}},"state":{{json .State}}}', id)) as {
      labels: Record<string, string>;
      state: { Running: boolean; Paused: boolean };
    };
  };
  const worker = await inspect();
  const project = worker.labels['com.docker.compose.project'];
  expect(project).toMatch(/^platform-groundwater-e2e-[a-f0-9]{12}$/);
  expect(worker.labels['com.docker.compose.service']).toBe(service);
  expect(worker.state).toMatchObject({ Running: true, Paused: false });
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

test('observes real Data publication before commit and retains its Run association', async ({ page, baseURL }, info) => {
  test.setTimeout(120_000);
  const clients = await isolatedInteractionClients(page, baseURL);
  const data = await isolatedWorker(baseURL ?? process.env.PLATFORM_BASE_URL!, 'data-worker');
  const { sessionId, projectId } = await openSummaryProposal(page, clients, `publication-${randomUUID()}`);
  const sessionPath = `/api/v12/sessions/${sessionId}`;
  const workflow = (id: string) => clients.get<Run>(`/api/v12/workflow-runs/${id}?projectId=${projectId}`);
  type Publication = components['schemas']['kernel__WorkflowPublicationResponse'];
  let runId = '';
  let pending: Publication | undefined;
  const publication = () => clients.get<Publication>(`/api/v12/workflow-runs/${runId}/publication?projectId=${projectId}`);
  try {
    await data.stop();
    const confirmation = page.waitForResponse(response =>
      new URL(response.url()).pathname === sessionPath + '/confirm' && response.request().method() === 'POST');
    await page.getByTestId('confirm-execution').click();
    const receipt = await confirmation;
    expect(receipt.status(), await receipt.text()).toBe(202);
    await expect.poll(async () => {
      [runId = ''] = (await clients.get<InteractionSession>(sessionPath)).workflowRunIds;
      if (!runId) return null;
      pending = await publication();
      return pending.state;
    }, { timeout: 30_000 }).toBe('PREPARING');
    expect(pending).toMatchObject({ workflowRunId: runId, publicationId: expect.any(String) });
    expect(await workflow(runId)).toMatchObject({ status: 'RUNNING', outputs: [] });
    await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '运行' }).click();
    const row = page.locator(`[data-execution-run-id="${runId}"]`);
    await row.getByText('成果发布详情', { exact: true }).click();
    await expect(row).toContainText('准备发布');
    await expect(row).toContainText(pending!.publicationId!);
    await expect(row.getByRole('link', { name: '下载' })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('publication-preparing.png') });
  } finally {
    await data.start();
  }
  await expect.poll(async () => (await workflow(runId)).status, { timeout: 60_000 }).toBe('SUCCEEDED');
  const published = await publication();
  expect(published).toEqual({ ...pending, state: 'PUBLISHED' });
  const direct = await page.request.get(`/api/v12/data/publications/${published.publicationId}`);
  expect(direct.status()).toBe(404);
  const deniedProject = await page.request.get(`/api/v12/workflow-runs/${runId}/publication?projectId=other`);
  expect(deniedProject.status()).toBe(404);
  const anonymous = await page.context().browser()!.newContext();
  try {
    expect((await anonymous.request.get(`${baseURL}/api/v12/workflow-runs/${runId}/publication?projectId=${projectId}`)).status()).toBe(401);
  } finally {
    await anonymous.close();
  }
  writeFileSync(info.outputPath('publication-evidence.json'), JSON.stringify({
    pending, published, run: await workflow(runId), directReadStatus: direct.status(), crossProjectStatus: deniedProject.status(),
  }, null, 2));
});

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

test('keeps a real stop receipt separate from the final cancellation and published outcomes', async ({ page, baseURL }, info) => {
  const clients = await isolatedInteractionClients(page, baseURL);
  const native = await isolatedWorker(baseURL ?? process.env.PLATFORM_BASE_URL!);
  for (const outcome of ['CANCELLED', 'SUCCEEDED'] as const) {
    const initial = await openSummaryProposal(page, clients, `interaction-stop-${outcome}-${randomUUID()}`);
    const path = `/api/v12/sessions/${initial.sessionId}`;
    const history = () => clients.get<History>(path + '/messages');
    const readTurn = (snapshot: History) => snapshot.turns.find(item => item.goalRevision === initial.goalRevision)!;
    const stopPath = path + '/stop';
    let release!: () => void;
    const allowStop = new Promise<void>(resolve => { release = resolve; });
    let intercepted!: () => void;
    const stopStarted = new Promise<void>(resolve => { intercepted = resolve; });
    const delayedStop = (url: URL) => url.pathname === stopPath;
    if (outcome === 'SUCCEEDED') {
      await page.route(delayedStop, async route => {
        intercepted();
        await allowStop;
        await route.continue();
      }, { times: 1 });
    }

    let run!: ExecutionTask;
    let receipt!: { requestId: string; agentRunId: string; status: string };
    try {
      await native.stop();
      const approved = page.waitForResponse(response => response.request().method() === 'POST'
        && new URL(response.url()).pathname === path + '/confirm');
      await page.getByTestId('confirm-execution').click();
      expect((await approved).status()).toBe(202);
      await expect.poll(async () => {
        const turn = readTurn(await history());
        return [turn.status, turn.executionTasks.length];
      }, { timeout: 20_000 }).toEqual(['WAITING_EXECUTION', 1]);
      [run] = readTurn(await history()).executionTasks;
      const runPath = `/api/v12/workflow-runs/${run.workflowRunId}?projectId=${initial.projectId}`;
      await expect.poll(async () => Boolean((await clients.get<Run>(runPath)).remoteJobId), { timeout: 15_000 }).toBe(true);
      const received = page.waitForResponse(response => response.request().method() === 'POST'
        && new URL(response.url()).pathname === stopPath);
      await page.getByRole('button', { name: '停止当前处理', exact: true }).click();
      if (outcome === 'SUCCEEDED') {
        await stopStarted;
        await expect(page.getByRole('button', { name: '正在请求停止…', exact: true })).toBeDisabled();
        expect(readTurn(await history()).executionTasks[0].status).not.toBe('CANCELLED');
        await native.start();
        await expect.poll(async () => readTurn(await history()).executionTasks[0].status, { timeout: 40_000 })
          .toBe('SUCCEEDED');
        release();
      }
      const response = await received;
      expect(response.status()).toBe(202);
      receipt = await response.json();
      expect(receipt).toMatchObject({ status: 'REQUESTED', agentRunId: run.agentRunId });
      expect(receipt.requestId).toBeTruthy();
      await expect.poll(async () => (await history()).controlRequests?.find(item => item.requestId === receipt.requestId)?.status,
        { timeout: 30_000 }).toBe('COMPLETE');
    } finally {
      release();
      await native.start();
    }

    const terminal = readTurn(await history()).executionTasks[0];
    expect(terminal).toMatchObject({ workflowRunId: run.workflowRunId, status: outcome });
    const runDetails = await clients.get<Run>(`/api/v12/workflow-runs/${run.workflowRunId}?projectId=${initial.projectId}`);
    expect(runDetails.status).toBe(outcome);
    expect(runDetails.outputs).toHaveLength(outcome === 'CANCELLED' ? 0 : 2);
    const turn = page.locator(`#turn-${initial.goalRevision}`);
    await expect(turn.getByText('停止请求处理结束', { exact: true })).toBeVisible();
    const card = turn.locator(`[data-workflow-run-id="${run.workflowRunId}"]`);
    await expect(card).toHaveAttribute('data-status', outcome);
    await expect(card.getByRole('link', { name: '下载', exact: true })).toHaveCount(runDetails.outputs.length);
    const events = await clients.python<StreamEvent[]>(`
import json
import os
import sys
from contextlib import closing
from groundwater_api_client import GroundwaterClient

origin, session_id, cursor, request_id = sys.argv[1:]
events = []
buffer = b""
with GroundwaterClient(origin, api_token=os.environ["GROUNDWATER_API_TOKEN"]) as client:
    with closing(client.events(session_id, cursor=int(cursor))) as stream:
        for chunk in stream:
            buffer += chunk
            while b"\\n\\n" in buffer:
                frame, buffer = buffer.split(b"\\n\\n", 1)
                fields = dict(line.split(b":", 1) for line in frame.splitlines() if b":" in line)
                if b"id" not in fields:
                    continue
                payload = json.loads(fields[b"data"])
                if payload["type"].startswith("control.") and payload["data"].get("requestId") == request_id:
                    events.append({"id": int(fields[b"id"]), "type": payload["type"], "data": payload["data"]})
            if events and events[-1]["type"] == "control.complete":
                break
print(json.dumps(events))
`, initial.sessionId, String(initial.cursor), receipt.requestId);
    expect(events.map(event => event.type)).toEqual(['control.requested', 'control.complete']);
    expect(events[0].id).toBeLessThan(events[1].id);
    await page.screenshot({ path: info.outputPath(`stop-${outcome.toLowerCase()}.png`) });
    writeFileSync(info.outputPath(`stop-${outcome.toLowerCase()}.json`),
      JSON.stringify({ receipt, terminal, events }, null, 2) + '\n');
  }
});

test('keeps a delayed real confirmation snapshot and SSE out of a different Session', async ({ page, baseURL }, info) => {
  const clients = await isolatedInteractionClients(page, baseURL);
  const initial = await openSummaryProposal(page, clients, `interaction-switch-${randomUUID()}`);
  const other = JSON.parse(await clients.cli('new')) as InteractionSession;
  const historyPath = `/api/v12/sessions/${initial.sessionId}/messages`;
  const matches = (url: URL) => url.pathname === historyPath;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let received!: (snapshot: History) => void;
  const captured = new Promise<History>(resolve => { received = resolve; });
  let delivered!: () => void;
  const finished = new Promise<void>(resolve => { delivered = resolve; });
  await page.route(matches, async route => {
    const response = await route.fetch();
    const snapshot = await response.json() as History;
    received(snapshot);
    await held;
    try {
      await route.fulfill({ response });
    } finally {
      delivered();
    }
  }, { times: 1 });

  await page.reload();
  try {
    const before = await captured;
    expect(before.sessionId).toBe(initial.sessionId);
    expect(before.turns.flatMap(turn => turn.confirmations)).toContainEqual(expect.objectContaining({
      confirmationId: initial.confirmationId, status: 'PENDING',
    }));
    await page.locator(`a.session-link[href="#/sessions/${other.sessionId}"]`).click();
    await expect(page).toHaveURL(new RegExp(`#/sessions/${other.sessionId}$`));
    const newHistory = await clients.get<History>(`/api/v12/sessions/${other.sessionId}/messages`);
    expect(newHistory).toMatchObject({ sessionId: other.sessionId, items: [], turns: [], task: null });
  } finally {
    release();
  }
  await finished;
  await expect(page.getByRole('heading', { name: '开始地下水分析' })).toBeVisible();
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  await expect(page.getByTestId('result-card')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('different-session-after-delayed-snapshot.png') });

  await page.addInitScript(sessionId => {
    const gate = { eventId: null as string | null, release: null as (() => void) | null };
    Object.assign(window, { oldSessionEvent: gate });
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      override addEventListener(type: string, listener: ((this: EventSource, event: MessageEvent) => void) | EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) {
        if (type !== 'confirmation.rejected' || !new URL(this.url).pathname.endsWith(`/${sessionId}/events`)) {
          if (typeof listener === 'function') super.addEventListener(type, listener, options);
          else super.addEventListener(type, listener, options);
          return;
        }
        super.addEventListener(type, (event: MessageEvent) => {
          gate.eventId = event.lastEventId;
          gate.release = () => {
            if (typeof listener === 'function') listener.call(this, event);
            else listener.handleEvent(event);
          };
        }, options);
      }
    };
  }, initial.sessionId);
  await page.goto(`/?debug=1#/sessions/${initial.sessionId}`);
  await expect(page.getByTestId('confirm-execution')).toBeEnabled();
  await expect(page.getByTestId('connection-status')).toHaveText(/实时更新已连接/);
  await clients.cli('--session', initial.sessionId, 'reject', initial.confirmationId, '检验会话切换后的事件隔离');
  const eventId = () => page.evaluate(() => (window as unknown as { oldSessionEvent: { eventId: string | null } }).oldSessionEvent.eventId);
  await expect.poll(eventId).toMatch(/^\d+$/);
  await page.locator(`a.session-link[href="#/sessions/${other.sessionId}"]`).click();
  await expect(page.getByRole('heading', { name: '开始地下水分析' })).toBeVisible();
  await page.evaluate(() => (window as unknown as { oldSessionEvent: { release: () => void } }).oldSessionEvent.release());
  const releasedEventId = await eventId();
  await expect(page.locator('.debug-panel pre').first()).not.toContainText(`SSE confirmation.rejected ${releasedEventId}`);
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  await expect(page.getByTestId('result-card')).toHaveCount(0);
  writeFileSync(info.outputPath('delayed-session-event.json'),
    JSON.stringify({ initialSession: initial.sessionId, activeSession: other.sessionId, eventId: releasedEventId }, null, 2) + '\n');
});

test('queues a follow-up during real execution without changing the approved batch', async ({ page, baseURL }, info) => {
  const clients = await isolatedInteractionClients(page, baseURL);
  const native = await isolatedWorker(baseURL ?? process.env.PLATFORM_BASE_URL!);
  const initial = await openSummaryProposal(page, clients, `interaction-queue-${randomUUID()}`);
  const path = `/api/v12/sessions/${initial.sessionId}`;
  const history = () => clients.get<History>(path + '/messages');
  const originalTurn = (snapshot: History) => snapshot.turns.find(turn => turn.goalRevision === initial.goalRevision);
  const followUp = '请比较当前资料可用的趋势分析方法，不要执行计算。';
  const postedRevisions: number[] = [];
  page.on('response', async response => {
    const request = response.request();
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== path + '/messages'
      || request.postDataJSON().message !== followUp || response.status() !== 202) return;
    const { goalRevision } = await response.json();
    postedRevisions.push(goalRevision);
  });

  let runId = '';
  try {
    await native.stop();
    const decision = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname === path + '/confirm');
    await page.getByTestId('confirm-execution').click();
    expect((await decision).status()).toBe(202);
    await expect.poll(async () => {
      const turn = originalTurn(await history());
      return [turn?.status, turn?.executionTasks.length];
    }, { timeout: 20_000 }).toEqual(['WAITING_EXECUTION', 1]);
    const composer = page.getByRole('textbox', { name: '消息输入框' });
    await composer.fill(followUp);
    await page.getByRole('button', { name: '稍后发送', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '排队消息 1' })).toHaveValue(followUp);
    const during = await history();
    expect(during.goalRevision).toBe(initial.goalRevision);
    expect(during.items.some(message => message.content === followUp)).toBe(false);
    const turn = originalTurn(during)!;
    expect(turn.status).toBe('WAITING_EXECUTION');
    const [run] = turn.executionTasks;
    expect(run.confirmationId).toBe(initial.confirmationId);
    runId = run.workflowRunId;
    expect(turn.confirmations.find(item => item.confirmationId === initial.confirmationId)?.actions)
      .toEqual(initial.confirmation.actions);
    await page.screenshot({ path: info.outputPath('follow-up-queued.png') });
  } finally {
    await native.start();
  }

  await expect.poll(async () => {
    const snapshot = await history();
    return snapshot.items.filter(message => message.role === 'user' && message.content === followUp)
      .map(message => message.goalRevision);
  }, { timeout: 65_000 }).toEqual([initial.goalRevision + 1]);
  await expect.poll(async () => (await history()).turns.find(turn => turn.goalRevision === initial.goalRevision + 1)?.status)
    .toBe('COMPLETE');
  const final = await history();
  const turn = originalTurn(final)!;
  expect(turn.executionTasks.map(run => [run.workflowRunId, run.status])).toEqual([[runId, 'SUCCEEDED']]);
  expect(turn.confirmations.find(item => item.confirmationId === initial.confirmationId)?.actions)
    .toEqual(initial.confirmation.actions);
  expect(final.turns.find(item => item.goalRevision === initial.goalRevision + 1)?.executionTasks).toEqual([]);
  await expect(page.getByRole('textbox', { name: '排队消息 1' })).toHaveCount(0);
  expect(postedRevisions).toEqual([initial.goalRevision + 1]);
  writeFileSync(info.outputPath('queue-and-frozen-batch.json'), JSON.stringify({
    status: 'PASS', sessionId: initial.sessionId, runId, approvedConfirmationId: initial.confirmationId,
    approvedGoalRevision: initial.goalRevision, postedRevisions,
  }, null, 2) + '\n');
});

test('recovers an actual timed-out workflow across Web, TLI and SDK', async ({ page, baseURL }, info) => {
  test.setTimeout(225_000);
  const clients = await isolatedInteractionClients(page, baseURL);
  const native = await isolatedWorker(baseURL ?? process.env.PLATFORM_BASE_URL!);
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
  const retainedNodes = workspace.locator(`[data-execution-run-id="${retained.workflowRunId}"]`);
  await retainedNodes.getByText('节点详情', { exact: true }).click();
  await expect(retainedNodes).toContainText('JOB_DEADLINE_EXCEEDED');
  await expect(restoredNodes).not.toContainText('JOB_DEADLINE_EXCEEDED');
  await restoredNodes.getByText('运行标识与时间', { exact: true }).click();
  await expect(restoredNodes.getByText(restored.workflowRunId, { exact: true })).toBeVisible();
  await expect(restoredNodes.getByText(recovery.confirmationId, { exact: true })).toBeVisible();
  await expect(restoredNodes.locator('time').first()).toHaveAttribute('datetime', restored.createdAt);
  record('workflow-graph', graph);
  record('node-diagnostics', { original: originalDiagnostics, recovered: restoredDiagnostics });
  await screenshot('06-workflow-node-diagnostics');
  await page.setViewportSize({ width: 390, height: 844 });
  await restoredNodes.getByText('运行标识与时间', { exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await screenshot('07-workflow-node-diagnostics-mobile');
  await page.setViewportSize({ width: 1440, height: 1000 });
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
