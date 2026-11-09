import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { productUi } from './product-ui';
import { capabilitiesFixture, dataDetail, dataRef, historyFixture, replyFixture, sessionResultsFixture, sessionSummaryFixture, type HistoryFixture } from './test-fixtures';
import type { Confirmation } from './types';

class TestEvents {
  static latest: TestEvents;
  static cursor = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  constructor() { TestEvents.latest = this; }
  addEventListener(type: string, handler: (event: MessageEvent) => void) { this.listeners.set(type, handler); }
  close() {}
  emit(type: string, id: number) { TestEvents.cursor = id; this.listeners.get(type)?.(new MessageEvent(type, { lastEventId: String(id), data: '{}' })); }
}
const json = (data: unknown) => new Response(JSON.stringify(data && typeof data === 'object' && 'turns' in data ? historyFixture({ eventCursor: TestEvents.cursor, ...data as HistoryFixture }) : data), { status: 200, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  TestEvents.cursor = 0;
  sessionStorage.clear();
  window.history.replaceState({}, '', '/#/');
  vi.stubGlobal('EventSource', TestEvents);
  Element.prototype.scrollIntoView = vi.fn();
});

it('all eight examples only populate the single composer without posting', async () => {
  const examples = productUi().examples.map(item => [item.title, item.prompt] as const);
  const calls = vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    throw new Error(`unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', calls);
  render(<App />);
  await screen.findByTitle('项目：demo');
  for (const [label, message] of examples.slice(0, 4)) {
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue(message);
  }
  const more = screen.getByText('更多场景').closest('details');
  expect(more).not.toHaveAttribute('open');
  fireEvent.click(screen.getByText('更多场景'));
  expect(more).toHaveAttribute('open');
  for (const [label, message] of examples.slice(4)) {
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue(message);
  }
  expect(calls.mock.calls).toHaveLength(2);
});

it('shows the authenticated research identity and disables writes for a viewer', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture({
      currentUser: {
        subject: 'auditor@example.test', displayName: '项目审计员',
        roles: ['viewer'],
      },
    }));
    if (url.endsWith('/sessions')) return new Response(JSON.stringify({
      type: 'urn:test:problem:role-forbidden', title: 'Forbidden', status: 403,
      code: 'ROLE_FORBIDDEN', traceId: 'trace', requestId: 'request',
    }), { status: 403, headers: { 'Content-Type': 'application/problem+json' } });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  expect(await screen.findByText('项目审计员')).toBeVisible();
  expect(screen.getByText('项目环境')).toBeVisible();
  expect(screen.getByRole('button', { name: '新建对话' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: '添加附件' })).toBeDisabled();
});

it('applies session search and archived filters through the owner-scoped API', async () => {
  const requests: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.includes('/sessions')) {
      requests.push(url);
      return json({ items: [], nextCursor: null });
    }
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByTitle('项目：demo');
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索对话' }), {
    target: { value: '监测任务' },
  });
  await waitFor(() => expect(requests.some(url => url.includes('query=%E7%9B%91%E6%B5%8B%E4%BB%BB%E5%8A%A1'))).toBe(true));
  fireEvent.click(screen.getByRole('checkbox', { name: '显示归档' }));
  await waitFor(() => expect(requests.some(url =>
    url.includes('query=%E7%9B%91%E6%B5%8B%E4%BB%BB%E5%8A%A1') && url.includes('archived=true'),
  )).toBe(true));
});

it('renames and archives the active session without deleting its history', async () => {
  window.location.hash = '#/sessions/session-1';
  const updates: unknown[] = [];
  const session = sessionSummaryFixture({ title: '原名称' });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [session], nextCursor: null });
    if (url.endsWith('/sessions/session-1') && init?.method === 'PATCH') {
      updates.push(JSON.parse(String(init.body)));
      return json({ sessionId: 'session-1', title: '新名称', archived: false });
    }
    if (url.endsWith('/sessions/session-1/messages')) return json(historyFixture({ sessionId: 'session-1' }));
    if (url.includes('/sessions/session-1/results')) return json(sessionResultsFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByRole('button', { name: '重命名' });
  fireEvent.click(screen.getByRole('button', { name: '重命名' }));
  fireEvent.change(screen.getByRole('textbox', { name: '对话名称' }), {
    target: { value: '新名称' },
  });
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await waitFor(() => expect(updates).toContainEqual({ title: '新名称' }));
  fireEvent.click(await screen.findByRole('button', { name: '归档' }));
  await waitFor(() => expect(updates).toContainEqual({ archived: true }));
  expect(window.location.hash).toBe('#/');
});

it('fails closed for an HTML-only API response and reconnects', async () => {
  let ready = false;
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return ready
      ? json(capabilitiesFixture())
      : new Response('<html>preview</html>', { headers: { 'Content-Type': 'text/html' } });
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    throw new Error(`unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  expect(await screen.findByText('服务暂时不可用')).toBeVisible();
  expect(screen.getByText(/API_RESPONSE_INVALID/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '发送消息' })).not.toBeInTheDocument();
  ready = true;
  fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
  await screen.findByTitle('项目：demo');
  expect(screen.queryByText('服务暂时不可用')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), {
    target: { value: '连接恢复后的问题' },
  });
  expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled();
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/sessions'))).toHaveLength(1);
});

it('collapses each sidebar in place and keeps the resource links on the left', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByTitle('项目：demo');
  const left = screen.getByRole('complementary', { name: '会话侧栏' });
  const right = screen.getByRole('complementary', { name: '任务工作台' });
  expect(screen.getByRole('button', { name: '数据库' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '知识库' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '任务' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '折叠会话侧栏' }));
  expect(left).toHaveClass('is-collapsed');
  fireEvent.click(screen.getByRole('button', { name: '展开会话侧栏' }));
  expect(left).not.toHaveClass('is-collapsed');
  fireEvent.click(screen.getByRole('button', { name: '打开任务工作台' }));
  expect(right).not.toHaveClass('is-collapsed');
  fireEvent.click(screen.getByRole('button', { name: '折叠任务工作台' }));
  expect(right).toHaveClass('is-collapsed');
  expect(screen.queryByRole('button', { name: '关闭工作区面板' })).not.toBeInTheDocument();
});

it('hides management entry points for services not selected by the product', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture({ services: ['data', 'executor'] }));
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByTitle('项目：demo');
  expect(screen.getByRole('button', { name: '数据库' })).toBeVisible();
  expect(screen.queryByRole('button', { name: '知识库' })).not.toBeInTheDocument();
});

it('does not expose Data or Knowledge actions for an empty assembled service topology', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture({ services: [] }));
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByTitle('项目：demo');
  expect(screen.queryByRole('button', { name: '数据库' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '知识库' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '添加附件' })).not.toBeInTheDocument();
});

it('attaches the current immutable version when no confirmation batch exists', async () => {
  window.location.hash = '#/sessions/preparation';
  const stale = dataRef({ versionId: 'stale-version' });
  const history = historyFixture({
    sessionId: 'preparation',
    preparation: {
      schemaVersion: 'task-preparation.v1', preparationId: 'preparation-1', revision: 1,
      sessionId: 'preparation', agentRunId: 'agent-1', goalRevision: 1,
      taskId: null, baseTaskRevision: null, status: 'BLOCKED', freshForCurrentTurn: true, disposition: 'ACTIVE',
      entries: [{ targetKind: 'EXECUTABLE_MODEL', targetId: 'groundwater.summary', targetVersion: '3.0.0',
        selectionPolicy: 'CURRENT_REQUIRED', status: 'BLOCKED', inputs: { versionId: stale }, knowledgeRefs: [], parameters: {}, parameterSources: {},
        checks: [{ kind: 'DATA_VERSION_NOT_CURRENT', input: 'versionId', status: 'FAIL', currentVersion: 'current-version' }], blockingReasons: [] }],
    },
  });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(history);
    if (url.endsWith('/data/versions/stale-version')) return json(dataDetail({ versionId: 'stale-version' }));
    if (url.endsWith('/data/versions/current-version')) return json(dataDetail({ versionId: 'current-version' }));
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByTestId('recovered-active-session');
  fireEvent.click(screen.getByRole('button', { name: '打开任务工作台' }));
  await screen.findByText(/DATA_VERSION_NOT_CURRENT/);
  fireEvent.click(screen.getByRole('button', { name: '使用当前版本' }));
  expect(await screen.findByText(/测试数据 · current-version/)).toBeVisible();
  expect(screen.queryByRole('dialog', { name: '数据库' })).not.toBeInTheDocument();
});

it('does not merge a delayed older page into a newly selected conversation', async () => {
  window.location.hash = '#/sessions/first';
  let finishOld!: (value: Response) => void;
  const history = (id: string, old = false) => ({ sessionId: id, goalRevision: 2,
    items: [{ messageId: old ? 1 : 2, goalRevision: 2, role: 'user', content: old ? '旧会话的延迟消息' : `${id}当前消息` }],
    turns: [{ goalRevision: 2, agentRunId: id, status: 'COMPLETE', errorCode: null, requestMode: 'DIALOGUE', confirmations: [] }], nextCursor: old ? null : 2 });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/messages?cursor=')) return new Promise<Response>(resolve => { finishOld = resolve; });
    if (url.includes('/results')) return json(sessionResultsFixture());
    const id = url.includes('/first') ? 'first' : 'second';
    if (url.endsWith('/messages')) return json(history(id));
    return json({ sessionId: id, projectId: 'demo', goalRevision: 2, agentRunId: id, confirmation: null });
  }));
  render(<App />);
  await screen.findByText('first当前消息');
  fireEvent.click(screen.getByRole('button', { name: '更早消息' }));
  await act(async () => { window.location.hash = '#/sessions/second'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  await screen.findByText('second当前消息');
  await act(async () => { finishOld(json(history('first', true))); });
  expect(screen.queryByText('旧会话的延迟消息')).not.toBeInTheDocument();
  expect(screen.getByText('second当前消息')).toBeVisible();
});

it('a previous conversation POST finishing cannot invalidate the current conversation load', async () => {
  window.location.hash = '#/sessions/first';
  let finishPost!: (value: Response) => void;
  let finishSecondHistory!: (value: Response) => void;
  const history = (id: string) => ({ sessionId: id, goalRevision: 1,
    items: [{ messageId: 1, goalRevision: 1, role: 'user', content: `${id}当前消息` }],
    turns: [{ goalRevision: 1, agentRunId: id, status: 'COMPLETE', errorCode: null, requestMode: 'DIALOGUE', confirmations: [] }], nextCursor: null });
  const calls = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return new Promise<Response>(resolve => { finishPost = resolve; });
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/second/messages')) return new Promise<Response>(resolve => { finishSecondHistory = resolve; });
    const id = url.includes('/first') ? 'first' : 'second';
    if (url.endsWith('/messages')) return json(history(id));
    return json({ sessionId: id, projectId: 'demo', goalRevision: 1, agentRunId: id, confirmation: null });
  });
  vi.stubGlobal('fetch', calls);
  render(<App />);
  await screen.findByText('first当前消息');
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), { target: { value: '在第一个会话发送' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await act(async () => { window.location.hash = '#/sessions/second'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  await waitFor(() => {
    expect(finishPost).toBeDefined();
    expect(finishSecondHistory).toBeDefined();
  });
  const firstReads = calls.mock.calls.filter(([url, init]) => url.includes('/first') && init?.method === 'GET').length;
  await act(async () => { finishPost(json({
    schemaVersion: 'message-receipt.v1', request: {}, requestId: 'request-1',
    sessionId: 'first', goalRevision: 1, agentRunId: 'first', requestedMode: 'AUTO',
    routingSource: 'router', routingStatus: 'RESOLVED', intentDecision: null,
    intent: null, semanticIntent: null, taskAction: null, controlIntent: null,
    clarificationReason: null, dialoguePolicy: null, resolvedMode: 'DIALOGUE',
    clarificationRequired: false, workflowDraftId: null, correctsRequestId: null,
    correctsAgentRunId: null, errorCode: null,
  })); });
  expect(calls.mock.calls.filter(([url, init]) => url.includes('/first') && init?.method === 'GET')).toHaveLength(firstReads);
  await act(async () => { finishSecondHistory(json(history('second'))); });
  await screen.findByText('second当前消息');
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), { target: { value: '第二个会话的草稿' } });
  await waitFor(() => expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled());
  expect(screen.queryByText('正在恢复对话…')).not.toBeInTheDocument();
});

it('a delayed older page cannot overwrite a new running turn or unlock the composer', async () => {
  window.location.hash = '#/sessions/session-1';
  let finishOld!: (value: Response) => void;
  let revision = 25;
  const turn = (goal: number, status: string) => ({ goalRevision: goal, agentRunId: `agent-${goal}`, status, errorCode: null, requestMode: 'DIALOGUE', confirmations: [] });
  const oldPage = { sessionId: 'session-1', goalRevision: 25,
    items: [{ messageId: 24, goalRevision: 25, role: 'user', content: '更早的问题' }], turns: [turn(25, 'RUNNING')], nextCursor: null };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/messages?cursor=')) return new Promise<Response>(resolve => { finishOld = resolve; });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json({ sessionId: 'session-1', goalRevision: revision,
      items: [{ messageId: 25, goalRevision: 25, role: 'user', content: '上一轮问题' }, ...(revision === 26 ? [{ messageId: 26, goalRevision: 26, role: 'user', content: '新一轮问题' }] : [])],
      turns: [turn(25, 'COMPLETE'), ...(revision === 26 ? [turn(26, 'RUNNING')] : [])], nextCursor: 25 });
    return json({ sessionId: 'session-1', projectId: 'demo', goalRevision: revision, agentRunId: `agent-${revision}`, confirmation: null });
  }));
  render(<App />);
  await screen.findByText('上一轮问题');
  fireEvent.click(screen.getByRole('button', { name: '更早消息' }));
  revision = 26;
  await act(async () => { TestEvents.latest.emit('agent.running', 26); await new Promise(resolve => setTimeout(resolve, 150)); });
  expect(screen.queryByText('新一轮问题')).not.toBeInTheDocument();
  await act(async () => { finishOld(json(oldPage)); });
  await screen.findByText('新一轮问题');
  await screen.findByText('更早的问题');
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), { target: { value: '暂存草稿' } });
  expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
  expect(screen.getAllByRole('status').filter(item => item.textContent === '处理请求')).toHaveLength(1);
});

it('preserves ordinary message text and separates connection status', async () => {
  window.location.hash = '#/sessions/selection';
  const content = '普通业务说明和井号 W01';
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content }], turns: [{ goalRevision: 1, requestMode: 'DIALOGUE', status: 'COMPLETE' }] }));
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByText(content);
  expect(screen.getByTestId('connection-status')).toHaveTextContent('正在连接实时更新');
  act(() => TestEvents.latest.onopen?.());
  expect(screen.getByTestId('connection-status')).toHaveTextContent('实时更新已连接');
  act(() => TestEvents.latest.onerror?.());
  expect(screen.getByTestId('connection-status')).toHaveTextContent('实时更新连接中断');
  expect(screen.getByText(content)).toBeVisible();
  expect(screen.getByRole('button', { name: /^已完成/ })).toBeVisible();
});

it('keeps a deep-linked older verification session visible and marks its confirmation card', async () => {
  window.location.hash = '#/sessions/older-verification';
  const confirmation: Confirmation = {
    confirmationId: 'confirmed-test', goalRevision: 1, status: 'CONFIRMED', agentRunId: 'agent-test',
    reason: '', decision: 'confirm', decisionState: null,
    actions: [{ toolCallId: 'summary', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'summary', workflowVersion: '1.0.0', arguments: { resourceId: 'version-1' } }],
  };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [sessionSummaryFixture({ sessionId: 'recent', title: '最近的业务会话' })], nextCursor: 10 });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture({ sessionId: 'older-verification',
      items: [{ messageId: 1, goalRevision: 1, role: 'user', content: 'SYNTHETIC_TEST_ONLY-P1：执行概览测试' }],
      turns: [{ status: 'COMPLETE', confirmations: [confirmation] }],
    }));
    throw new Error(`unexpected endpoint ${url}`);
  }));

  render(<App />);

  expect(await screen.findByTestId('recovered-active-session')).toHaveTextContent('当前会话 · 【验证会话】SYNTHETIC_TEST_ONLY-P1：执行概览测试');
  expect(screen.getByTestId('recovered-active-session')).toHaveClass('active');
  expect(screen.getByText('最近的业务会话')).toBeVisible();
  expect(screen.queryByText('暂无对话')).not.toBeInTheDocument();
  expect(screen.getByTestId('confirmation-card')).toHaveTextContent('验证会话 · 不代表业务执行');
});

it('blocks ordinary messages while a control proposal awaits confirmation', async () => {
  window.location.hash = '#/sessions/control';
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture({
      controlMessages: [{ requestId: 'control-request', message: '停止分析', responseKind: 'CONTROL', semanticIntent: 'UNSPECIFIED', controlIntent: 'STOP_ANALYSIS', clarificationReason: null, statusSnapshot: 'ANALYZING', waitingReason: 'ANALYSIS_IN_PROGRESS', results: [], status: 'CONTROL_PENDING', createdAt: 'now' }],
      controlProposals: [{ proposalId: 'proposal', sessionId: 'control', sourceRequestId: 'control-request', intent: 'STOP_ANALYSIS', targetType: 'AGENT_RUN', targetId: 'run', taskRevision: null, status: 'PENDING', decisionRequestId: null, createdAt: 'now', decidedAt: null }],
    }));
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByText('停止本次分析');
  expect(screen.getByText(/等待控制确认/)).toBeVisible();
  const input = screen.getByRole('textbox', { name: '消息输入框' });
  fireEvent.change(input, { target: { value: '继续普通分析' } });
  expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
  fireEvent.change(input, { target: { value: '查看状态' } });
  expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled();
});

it('renders completed turns from Turn.reply and keeps folded activity before the answer', async () => {
  window.location.hash = '#/sessions/structured';
  const reply = { ...replyFixture('结构化结论'),
    details: [{ markdown: '折叠的补充依据', sourceIndexes: [] }] };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture({
      items: [
        { messageId: 1, goalRevision: 1, role: 'user', content: '问题' },
        { messageId: 2, goalRevision: 1, role: 'assistant', content: '不应作为展示输入的线性全文' },
      ],
      turns: [{ status: 'COMPLETE', reply }],
    }));
    throw new Error(`unexpected endpoint ${url}`);
  }));

  render(<App />);
  await screen.findByText('结构化结论');
  expect(screen.queryByText('不应作为展示输入的线性全文')).not.toBeInTheDocument();
  expect(screen.getByText('折叠的补充依据')).not.toBeVisible();
  const answer = screen.getByText('结构化结论').closest('.reply-text')!;
  const activity = screen.getByTestId('activity-panel');
  expect(activity.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('button', { name: /^已完成/ })).toHaveAttribute('aria-expanded', 'false');
});

it('does not scroll away from history when a new answer arrives', async () => {
  window.location.hash = '#/sessions/scroll';
  let answered = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '之前的问题' }, ...(answered ? [{ messageId: 2, goalRevision: 1, role: 'assistant', content: '新的回答' }] : [])], turns: [{ status: answered ? 'COMPLETE' : 'RUNNING', reply: answered ? replyFixture('新的回答') : null }] }));
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByText('之前的问题');
  const list = screen.getByTestId('message-list');
  Object.defineProperties(list, { scrollHeight: { value: 1400 }, clientHeight: { value: 400 }, scrollTop: { value: 0, writable: true } });
  fireEvent.scroll(list);
  vi.mocked(Element.prototype.scrollIntoView).mockClear();
  answered = true;
  act(() => TestEvents.latest.emit('agent.complete', 2));
  await screen.findByText('新的回答');
  const latest = await screen.findByRole('button', { name: '查看最新消息' });
  expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  fireEvent.click(latest);
  expect(Element.prototype.scrollIntoView).toHaveBeenCalledOnce();
});
