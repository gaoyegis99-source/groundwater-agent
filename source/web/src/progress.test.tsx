import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { activityFixture, capabilitiesFixture, dataRef, historyFixture, messageReceiptFixture, replyFixture, sessionResponseFixture, sessionResultsFixture, taskFixture, type HistoryFixture } from './test-fixtures';
import { identityStorageScope, scopedStorageKey } from './browser-scope';
import type { Confirmation } from './types';
import kernelSessionEvents from './fixtures/kernel-session-events.json';

const storageScope = identityStorageScope('groundwater', 'demo', 'test-user');

class Events {
  static instances: Events[] = [];
  static previews: Events[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  closed = false;
  constructor(public url: string) { (url.endsWith("/preview") ? Events.previews : Events.instances).push(this); }
  addEventListener(type: string, handler: (event: MessageEvent) => void) { this.listeners.set(type, handler); }
  close() { this.closed = true; }
  emit(type: string, id: number) { this.listeners.get(type)?.(new MessageEvent(type, { lastEventId: String(id), data: '{"status":"COMPLETE"}' })); }
}
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
const snapshot = (value: HistoryFixture = {}) => historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '分析这些资料' }], ...value });
function delayed() { let finish!: (value: Response) => void; const promise = new Promise<Response>(resolve => { finish = resolve; }); return { promise, finish }; }
async function navigate(id: string) { await act(async () => { window.location.hash = `#/sessions/${id}`; window.dispatchEvent(new HashChangeEvent('hashchange')); }); }
beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); Events.instances = []; Events.previews = [];
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('EventSource', Events); Element.prototype.scrollIntoView = vi.fn();
});
function basics(url: string) {
  if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
  if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
  if (url.includes('/results')) return json(sessionResultsFixture());
  throw new Error(`unexpected endpoint ${url}`);
}

// Kernel streams persisted event_type verbatim (session_streams.py), including
// underscores; JSON field camel-casing does not rename an SSE event.
it('subscribes to the complete persisted Kernel session event inventory', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/messages') ? json(snapshot()) : basics(url)));
  render(<App />);
  await screen.findByText('分析这些资料');
  expect([...Events.instances[0].listeners.keys()]).toEqual(expect.arrayContaining(kernelSessionEvents.events));
});

it.each([
  'agent.plan_recorded', 'workflow.stage_changed', 'workflow.communication_changed',
  'workflow.retry_queued', 'confirmation.recovery_created',
  'workflow.recovery_completed', 'workflow.recovery_failed',
  'task.preparation_revised', 'session.metadata_updated',
])('refreshes the visible task after the Kernel wire event %s', async type => {
  let cursor = 10;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/messages')
    ? json(snapshot({ eventCursor: cursor, turns: [{ reply: replyFixture(cursor === 10 ? '等待状态更新' : '已读取新的任务状态') }] }))
    : basics(url)));
  render(<App />);
  await screen.findByText('等待状态更新');
  cursor = 11;
  act(() => Events.instances[0].emit(type, cursor));
  await screen.findByText('已读取新的任务状态');
});

it.each(['workflow.timed_out', 'job.timed_out', 'workflow.retry_queued', 'workflow.recovery_completed', 'workflow.recovery_failed'])(
  'reconciles authoritative attempt results after %s', async type => {
    let cursor = 10, resultReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/messages')) return json(snapshot({ eventCursor: cursor }));
      if (url.includes('/results')) { resultReads++; return json(sessionResultsFixture()); }
      return basics(url);
    }));
    render(<App />);
    await waitFor(() => expect(resultReads).toBe(1));
    cursor = 11;
    act(() => Events.instances[0].emit(type, cursor));
    await waitFor(() => expect(resultReads).toBe(2));
  },
);

it('ignores a late stop failure after another session has its own stop in flight', async () => {
  const firstStop = delayed(), secondStop = delayed();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url.endsWith('/stop')) return url.includes('/session-1/') ? firstStop.promise : secondStop.promise;
    if (url.endsWith('/messages')) return json(snapshot({ sessionId: url.includes('/session-1/') ? 'session-1' : 'session-2', turns: [{ status: 'RUNNING' }] }));
    return basics(url);
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '停止当前处理' }));
  await navigate('session-2');
  fireEvent.click(await screen.findByRole('button', { name: '停止当前处理' }));
  expect(screen.getByRole('button', { name: '正在请求停止…' })).toBeDisabled();
  await act(async () => firstStop.finish(new Response(JSON.stringify({ code: 'STOP_OLD_SESSION_FAILED' }), { status: 503 })));
  expect(screen.getByRole('button', { name: '正在请求停止…' })).toBeDisabled();
  expect(screen.queryByText('STOP_OLD_SESSION_FAILED')).not.toBeInTheDocument();
  await act(async () => secondStop.finish(json({ sessionId: 'session-2', agentRunId: 'agent-1', requestId: 'stop-2', status: 'REQUESTED' })));
});

const editableConfirmation = (id: string): Confirmation => ({
  confirmationId: id, goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
  reason: '', decision: null, decisionState: null,
  actions: [{ toolCallId: 'summary', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
    workflowId: 'summary', workflowVersion: '1.0.0', arguments: { axis: 'columns' } }],
});

it('does not close another session parameter editor when a previous revision finishes', async () => {
  const revision = delayed();
  let posts = 0;
  const capabilities = capabilitiesFixture();
  capabilities.models.find(item => item.modelId === 'groundwater.summary')!.inputSchema = { type: 'object', properties: { axis: { type: 'string' } } };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url.endsWith('/revisions')) { posts++; return revision.promise; }
    if (url.endsWith('/capabilities')) return json(capabilities);
    if (url.endsWith('/messages')) return json(snapshot({ sessionId: url.includes('/session-1/') ? 'session-1' : 'session-2', turns: [{ status: 'WAITING_CONFIRMATION', confirmations: [editableConfirmation(url.includes('/session-1/') ? 'first' : 'second')] }] }));
    return basics(url);
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '编辑参数' }));
  fireEvent.change(await screen.findByDisplayValue('columns'), { target: { value: 'rows' } });
  fireEvent.click(screen.getByRole('button', { name: '保存为新修订' }));
  await waitFor(() => expect(posts).toBe(1));
  await navigate('session-2');
  fireEvent.click(await screen.findByRole('button', { name: '编辑参数' }));
  fireEvent.change(await screen.findByDisplayValue('columns'), { target: { value: '第二个会话的未保存修改' } });
  await act(async () => { revision.finish(json(sessionResponseFixture())); await vi.dynamicImportSettled(); });
  expect(screen.getByTestId('confirmation-editor')).toBeInTheDocument();
  expect(screen.getByDisplayValue('第二个会话的未保存修改')).toBeVisible();
});

it('keeps stop disabled while the server is processing the durable control request', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/messages')
    ? json(snapshot({ turns: [{ status: 'RUNNING' }], controlRequests: [{ requestId: 'stop-1', agentRunId: 'agent-1', status: 'PROCESSING' }] }))
    : basics(url)));
  render(<App />);
  expect(await screen.findByRole('button', { name: '正在请求停止…' })).toBeDisabled();
});

it('retains a correction after stop failure and sends it once only after the stop is acknowledged', async () => {
  const stopIds: string[] = [];
  const messages: Record<string, unknown>[] = [];
  let stopped = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      if (url.endsWith('/stop')) {
        stopIds.push(body.requestId);
        if (stopIds.length === 1) return new Response(JSON.stringify({ code: 'SERVICE_UNAVAILABLE' }), { status: 503 });
        stopped = true;
        return json({ sessionId: 'session-1', agentRunId: 'agent-1', requestId: body.requestId, status: 'REQUESTED' });
      }
      if (url.endsWith('/messages')) { messages.push(body); return json(messageReceiptFixture({ requestId: body.requestId })); }
    }
    if (url.endsWith('/messages')) return json(snapshot({ turns: [{ status: stopped ? 'CANCELLED' : 'RUNNING' }],
      controlRequests: stopped ? [{ agentRunId: 'agent-1', requestId: stopIds[0], status: 'COMPLETE' }] : [] }));
    return basics(url);
  }));
  render(<App />);
  await screen.findByText('分析这些资料');
  act(() => Events.instances[0].onopen?.());
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), { target: { value: '调整为最近一年的资料' } });
  fireEvent.click(screen.getByRole('button', { name: '停止后按输入内容纠正' }));
  await screen.findByRole('alert');
  expect(messages).toHaveLength(0);
  expect(screen.getByRole('textbox', { name: '排队消息 1' })).toHaveValue('调整为最近一年的资料');
  fireEvent.click(await screen.findByRole('button', { name: '停止当前处理' }));
  await waitFor(() => expect(messages).toHaveLength(1));
  expect(stopIds).toHaveLength(2);
  expect(stopIds[1]).toBe(stopIds[0]);
  expect(messages[0]).toMatchObject({ message: '调整为最近一年的资料', correctsAgentRunId: 'agent-1', requestedMode: 'AUTO' });
  await waitFor(() => expect(screen.queryByRole('textbox', { name: '排队消息 1' })).not.toBeInTheDocument());
});

it('submits a confirmation once during repeated activation and permits an explicit retry after failure', async () => {
  const pending = delayed();
  let posts = 0, confirmed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url.endsWith('/confirm')) {
      posts++;
      if (posts === 1) return pending.promise;
      confirmed = true; return json(sessionResponseFixture());
    }
    if (url.endsWith('/messages')) return json(snapshot({ turns: [{ status: confirmed ? 'WAITING_EXECUTION' : 'WAITING_CONFIRMATION',
      confirmations: [{ ...editableConfirmation('pending'), status: confirmed ? 'CONFIRMED' : 'PENDING' }] }] }));
    return basics(url);
  }));
  render(<App />);
  const confirm = await screen.findByRole('button', { name: '确认执行' });
  fireEvent.click(confirm); fireEvent.click(confirm);
  await waitFor(() => expect(posts).toBe(1));
  await act(async () => pending.finish(new Response(JSON.stringify({ code: 'SERVICE_UNAVAILABLE' }), { status: 503 })));
  fireEvent.click(await screen.findByRole('button', { name: '确认执行' }));
  await waitFor(() => expect(posts).toBe(2));
  await waitFor(() => expect(screen.queryByRole('button', { name: '确认执行' })).not.toBeInTheDocument());
});

it('installs history before SSE and slow details, and coalesces covered metadata events without extra GETs', async () => {
  const first = delayed(), next = delayed();
  let reads = 0;
  const calls = vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) { reads++; return reads === 1 ? first.promise : reads === 2 ? next.promise : json(snapshot({ eventCursor: 13, turns: [{ status: 'RUNNING' }] })); }
    return basics(url);
  });
  vi.stubGlobal('fetch', calls); render(<App />);
  await screen.findByTitle('项目：demo'); expect(Events.instances).toHaveLength(0);
  await act(async () => first.finish(json(snapshot({ eventCursor: 10, turns: [{ status: 'RUNNING', requestMode: 'DIALOGUE' }] }))));
  await screen.findByText('分析这些资料');
  expect(Events.instances[0].url).toBe('/api/v12/sessions/session-1/events?afterEventId=10');
  expect(screen.getByRole('status')).toHaveTextContent('处理请求');
  act(() => Events.instances[0].emit('agent.activity', 11));
  await waitFor(() => expect(reads).toBe(2));
  act(() => { Events.instances[0].emit('agent.activity', 12); Events.instances[0].emit('agent.activity', 12); });
  await act(async () => next.finish(json(snapshot({ eventCursor: 12, turns: [{ status: 'RUNNING', activities: [activityFixture({ status: 'COMPLETE', finishedAt: '2026-10-09T01:00:03Z' })] }] }))));
  fireEvent.click(screen.getByRole('button', { name: /^正在处理/ }));
  await screen.findByText('获取所需信息');
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 160)); });
  expect(reads).toBe(2);
  expect(calls.mock.calls.filter(([url]) => url.includes('/results'))).toHaveLength(1);
  expect(calls.mock.calls.filter(([url]) => url.includes('/recommendations/'))).toHaveLength(0);
  expect(screen.getByRole('status')).toHaveTextContent('处理请求');
  act(() => Events.instances[0].emit('agent.activity', 13));
  await waitFor(() => expect(reads).toBe(3));
});

it('allows one in-flight history read and one merged follow-up for events beyond its snapshot', async () => {
  const pending = delayed(); let reads = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) { reads++; return reads === 2 ? pending.promise : json(snapshot({ eventCursor: reads === 1 ? 10 : 14, turns: [{ status: 'RUNNING' }] })); }
    return basics(url);
  }));
  render(<App />); await screen.findByText('分析这些资料');
  act(() => Events.instances[0].emit('agent.activity', 11));
  await waitFor(() => expect(reads).toBe(2));
  act(() => { Events.instances[0].emit('agent.activity', 12); Events.instances[0].emit('agent.activity', 13); Events.instances[0].emit('agent.activity', 14); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 160)); });
  expect(reads).toBe(2);
  await act(async () => pending.finish(json(snapshot({ eventCursor: 11, turns: [{ status: 'RUNNING' }] }))));
  expect(reads).toBe(2);
  await waitFor(() => expect(reads).toBe(3));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 160)); });
  expect(reads).toBe(3);
});

it('a business event already covered by history still refreshes its details and does not stall later activity reads', async () => {
  let reads = 0, resultReads = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) { reads++; return json(snapshot({ eventCursor: reads === 1 ? 10 : reads === 2 ? 20 : 21, turns: [{ status: 'RUNNING' }] })); }
    if (url.includes('/results')) { resultReads++; return json(sessionResultsFixture()); }
    return basics(url);
  }));
  render(<App />); await screen.findByText('分析这些资料');
  act(() => Events.instances[0].emit('agent.activity', 11));
  await waitFor(() => expect(reads).toBe(2));
  act(() => Events.instances[0].emit('workflow.succeeded', 20));
  await waitFor(() => expect(resultReads).toBe(2));
  expect(reads).toBe(2);
  act(() => Events.instances[0].emit('agent.activity', 21));
  await waitFor(() => expect(reads).toBe(3));
});

it('replaces results with each authoritative response, including retry removal and output correction', async () => {
  let messageReads = 0;
  let resultReads = 0;
  const resultPages = [
    [{ goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary', status: 'COMPLETE', workflowRunId: 'workflow-old', attempt: 1,
      remoteOutputs: [dataRef({ versionId: 'old-output', outputName: '旧成果' })] }],
    [{ goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary', status: 'COMPLETE', workflowRunId: 'workflow-new', attempt: 2,
      remoteOutputs: [dataRef({ versionId: 'new-output', outputName: '新成果' })] }],
    [{ goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary', status: 'COMPLETE', workflowRunId: 'workflow-new', attempt: 2,
      remoteOutputs: [dataRef({ versionId: 'corrected-output', outputName: '更正成果' })] }],
  ];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) {
      const cursors = [10, 20, 21];
      return json(snapshot({ eventCursor: cursors[messageReads++] ?? 21, turns: [{ status: 'RUNNING' }] }));
    }
    if (url.includes('/results')) return json(sessionResultsFixture(resultPages[resultReads++] ?? resultPages.at(-1)!));
    return basics(url);
  }));

  render(<App />);
  await screen.findByText('旧成果');

  act(() => Events.instances[0].emit('workflow.succeeded', 20));
  await screen.findByText('新成果');
  expect(screen.queryByText('旧成果')).not.toBeInTheDocument();

  act(() => Events.instances[0].emit('workflow.succeeded', 21));
  await screen.findByText('更正成果');
  expect(screen.queryByText('新成果')).not.toBeInTheDocument();
  expect(resultReads).toBe(3);
});

it('ignores an older result response that finishes after a newer authoritative read', async () => {
  const staleRead = delayed();
  let messageReads = 0;
  let resultReads = 0;
  const result = (workflowRunId: string, versionId: string, outputName: string) => ({
    goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary',
    status: 'COMPLETE', workflowRunId, remoteOutputs: [dataRef({ versionId, outputName })],
  });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) {
      const cursors = [10, 20, 21];
      return json(snapshot({ eventCursor: cursors[messageReads++] ?? 21, turns: [{ status: 'RUNNING' }] }));
    }
    if (url.includes('/results')) {
      resultReads += 1;
      if (resultReads === 1) return json(sessionResultsFixture([result('workflow-initial', 'initial-output', '初始成果')]));
      if (resultReads === 2) return staleRead.promise;
      return json(sessionResultsFixture([result('workflow-current', 'current-output', '当前成果')]));
    }
    return basics(url);
  }));

  render(<App />);
  await screen.findByText('初始成果');
  act(() => Events.instances[0].emit('workflow.succeeded', 20));
  await waitFor(() => expect(resultReads).toBe(2));
  act(() => Events.instances[0].emit('workflow.succeeded', 21));
  await screen.findByText('当前成果');

  await act(async () => staleRead.finish(json(sessionResultsFixture([
    result('workflow-stale', 'stale-output', '过时成果'),
  ]))));

  expect(screen.getByText('当前成果')).toBeInTheDocument();
  expect(screen.queryByText('初始成果')).not.toBeInTheDocument();
  expect(screen.queryByText('过时成果')).not.toBeInTheDocument();
});

it('publishes result files without a recommendation detail request', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) return json(snapshot({ eventCursor: 10, turns: [{ requestMode: 'DIALOGUE', executionTasks: [taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: 1 })] }] }));
    if (url.includes('/results')) return json(sessionResultsFixture([{ goalRevision: 1, agentRunId: 'agent-1', modelId: 'groundwater.summary', status: 'SUCCEEDED', workflowRunId: 'workflow-1', toolCallId: 'tool-1', remoteOutputs: [{ kind: 'data_version', service: 'data-primary', datasetId: 'dataset-output', versionId: 'output', semanticId: 'semantic-output', objectId: 'object-output', checksum: 'sha256:output', outputName: 'summary', mediaType: 'application/json', format: 'json', sizeBytes: 128 }] }]));
    return basics(url);
  }));
  render(<App />);
  await screen.findByRole('link', { name: '下载' });
  expect(screen.queryByText('正在同步结果文件…')).not.toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/recommendations/'))).toBe(false);
});

it('retains a draft when an old POST finishes after navigating A to B to A', async () => {
  const post = delayed(); const calls = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return post.promise;
    if (url.endsWith('/messages')) return json(snapshot({ sessionId: url.includes('/second/') ? 'second' : 'session-1' }));
    return basics(url);
  });
  vi.stubGlobal('fetch', calls); render(<App />); await screen.findByText('分析这些资料');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '仍要保留的草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(screen.getByText('正在提交消息 · 草稿会保留')).toBeVisible();
  await navigate('second'); await waitFor(() => expect(Events.instances).toHaveLength(2));
  await navigate('session-1'); await waitFor(() => expect(Events.instances).toHaveLength(3));
  const historyReads = calls.mock.calls.filter(([url]) => url.endsWith('/messages')).length;
  await act(async () => post.finish(json(messageReceiptFixture())));
  expect(screen.getByRole('textbox')).toHaveValue('仍要保留的草稿');
  expect(calls.mock.calls.filter(([url]) => url.endsWith('/messages'))).toHaveLength(historyReads);
  expect(Events.instances[0].closed).toBe(true);
});

it('an older page cannot advance the stream cursor or current revision, even when its snapshot is newer', async () => {
  let reads = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/messages?cursor=')) return json(snapshot({ goalRevision: 9, eventCursor: 100, nextCursor: null, items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '旧问题' }], turns: [{ goalRevision: 1, status: 'COMPLETE' }] }));
    if (url.endsWith('/messages')) { reads++; return json(snapshot({ goalRevision: 2, eventCursor: reads === 1 ? 10 : 11, nextCursor: 2, items: [{ messageId: 2, goalRevision: 2, role: 'user', content: '当前问题' }], turns: [{ goalRevision: 2, status: 'RUNNING' }] })); }
    return basics(url);
  }));
  render(<App />); await screen.findByText('当前问题');
  fireEvent.click(screen.getByRole('button', { name: '更早消息' }));
  await screen.findByText('旧问题');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '尚未发送' } });
  expect(screen.getByRole('button', { name: /发送消息/ })).toBeDisabled();
  expect(Events.instances).toHaveLength(1);
  act(() => Events.instances[0].emit('agent.activity', 11));
  await waitFor(() => expect(reads).toBe(2));
});

it('a connection failure retains the task state and never posts or starts an HTTP retry loop', async () => {
  const calls = vi.fn(async (url: string) => url.endsWith('/messages') ? json(snapshot({ eventCursor: 10, turns: [{ status: 'RUNNING', activities: [activityFixture()] }] })) : basics(url));
  vi.stubGlobal('fetch', calls); render(<App />); await screen.findByText('分析这些资料');
  fireEvent.click(screen.getByRole('button', { name: /^正在处理/ }));
  await screen.findAllByText('获取所需信息');
  act(() => { Events.instances[0].onopen?.(); Events.instances[0].onerror?.(); });
  const count = calls.mock.calls.length;
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 160)); });
  expect(calls.mock.calls).toHaveLength(count);
  expect(screen.getByRole('status')).toHaveTextContent('获取所需信息');
  expect(screen.getByText('实时更新尚未连接，当前显示最后收到的状态。')).toBeVisible();
});

it('scopes submission blocking to its view and cannot clear another view’s newer pending operation', async () => {
  const firstPost = delayed(), secondPost = delayed();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return url.includes('/second/') ? secondPost.promise : firstPost.promise;
    if (url.endsWith('/messages')) return json(snapshot({ sessionId: url.includes('/second/') ? 'second' : 'session-1' }));
    return basics(url);
  }));
  render(<App />); await screen.findByText('分析这些资料');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '会话A的问题' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await navigate('second'); await waitFor(() => expect(Events.instances).toHaveLength(2));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '会话B的问题' } });
  expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await act(async () => firstPost.finish(json(messageReceiptFixture())));
  expect(screen.getByRole('button', { name: /发送消息/ })).toBeDisabled();
  expect(screen.getByRole('textbox')).toHaveValue('会话B的问题');
  await act(async () => secondPost.finish(json(messageReceiptFixture({ sessionId: 'second' }))));
  await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''));
});

it('keeps submission blocking through first-session creation and exposes creation failure without losing the draft', async () => {
  window.location.hash = '#/';
  let failed = true;
  const post = delayed();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url.endsWith('/sessions')) return failed ? new Response(JSON.stringify({ code: 'CREATE_FAILED' }), { status: 503, headers: { 'Content-Type': 'application/json' } }) : json(sessionResponseFixture({ sessionId: 'created', goalRevision: 0 }));
    if (init?.method === 'POST') return post.promise;
    if (url.endsWith('/messages')) return json(historyFixture({ sessionId: 'created', goalRevision: 0, turns: [] }));
    return basics(url);
  }));
  render(<App />); await screen.findByTitle('项目：demo');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '首次发送草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await expect(screen.findByRole('alert')).resolves.toHaveTextContent('CREATE_FAILED');
  expect(screen.getByRole('textbox')).toHaveValue('首次发送草稿');
  failed = false;
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(Events.instances).toHaveLength(1));
  expect(Events.instances[0].url).toContain('/sessions/created/events');
  expect(screen.getByRole('button', { name: /发送消息/ })).toBeDisabled();
  expect(screen.getByText('正在提交消息 · 草稿会保留')).toBeVisible();
  await act(async () => post.finish(json(messageReceiptFixture({ sessionId: 'created' }))));
  await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''));
});

it('withdraws temporary reply text on disconnect and final publication without persisting it', async () => {
  let current = snapshot({ turns: [{ status: 'RUNNING', reply: null }] });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/messages') ? json(current) : basics(url)));
  render(<App />); await screen.findByText('分析这些资料');
  const emit = (sequence: number, text: string | null) => act(() => Events.previews[0].listeners.get('preview.update')?.(new MessageEvent('preview.update', { data: JSON.stringify({ sequence, text, agentRunId: current.turns[0].agentRunId }) })));
  act(() => Events.previews[0].listeners.get('preview.update')?.(new MessageEvent('preview.update', { data: 'null' })));
  act(() => Events.previews[0].listeners.get('preview.update')?.(new MessageEvent('preview.update', { data: '{invalid' })));
  expect(screen.queryByText('临时结论')).toBeNull();
  emit(2, '临时结论'); expect(screen.getByText('临时结论')).toBeVisible();
  emit(1, '过期结论'); expect(screen.queryByText('过期结论')).toBeNull();
  expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain('临时结论');
  act(() => Events.previews[0].onerror?.()); expect(screen.queryByText('临时结论')).toBeNull();
  emit(3, '另一段临时内容');
  current = snapshot({ eventCursor: 20, turns: [{ status: 'COMPLETE', reply: null }] });
  act(() => Events.instances[0].emit('agent.complete', 20));
  await waitFor(() => expect(screen.queryByText('另一段临时内容')).toBeNull());
});

it('restores an uncertain queued request for explicit same-ID retry without automatic POST', async () => {
  const queueKey = scopedStorageKey(storageScope, 'message-queue', 'session-1');
  localStorage.setItem(queueKey, JSON.stringify([{ id: 'queued', requestId: 'original-request', text: '后续任务', attachments: [], createdAt: 'now' }]));
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { const body = JSON.parse(String(init.body)); posts.push(body); return json(messageReceiptFixture({ requestId: body.requestId })); }
    return url.endsWith('/messages') ? json(snapshot()) : basics(url);
  }));
  render(<App />); await screen.findByText('分析这些资料');
  act(() => Events.instances[0].onopen?.());
  await screen.findByRole('button', { name: '重试发送' });
  expect(posts).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '重试发送' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({ requestId: 'original-request', message: '后续任务' });
  await waitFor(() => expect(localStorage.getItem(queueKey)).toBeNull());
});

it('refreshes a recovered confirmation after the SSE connection reopens', async () => {
  let recovered = false;
  let reads = 0;
  const confirmation = (id: string, reason: string, status: 'PENDING' | 'SUPERSEDED') => ({
    confirmationId: id,
    goalRevision: 1,
    status,
    agentRunId: 'agent-1',
    reason,
    decision: null,
    decisionState: null,
    kind: id === 'recovered' ? 'RECOVERY' as const : 'INITIAL' as const,
    actions: [{
      toolCallId: `tool-${id}`,
      tool: 'run_summary',
      modelId: 'summary',
      modelVersion: '1',
      workflowId: 'flow',
      workflowVersion: '1',
      arguments: {},
    }],
  });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) {
      reads += 1;
      return json(snapshot({
        eventCursor: recovered ? 12 : 10,
        turns: [{
          status: 'WAITING_CONFIRMATION',
          confirmations: recovered
            ? [confirmation('original', '原确认已失效', 'SUPERSEDED'), confirmation('recovered', '恢复后的确认', 'PENDING')]
            : [confirmation('original', '原始确认', 'PENDING')],
        }],
      }));
    }
    return basics(url);
  }));

  render(<App />);
  await waitFor(() => expect(screen.getByTestId('confirmation-card')).toHaveAttribute('data-confirmation-id', 'original'));
  act(() => Events.instances[0].onopen?.());
  act(() => Events.instances[0].onerror?.());
  recovered = true;
  act(() => Events.instances[0].onopen?.());

  await screen.findByText('失败恢复批次');
  expect(document.querySelector('[data-confirmation-id="recovered"]')).not.toBeNull();
  expect(reads).toBe(2);
  expect(screen.getAllByRole('button', { name: '确认执行' })).toHaveLength(1);
});
