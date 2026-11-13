import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { capabilitiesFixture, historyFixture, messageReceiptFixture, replyFixture, sessionResponseFixture, sessionResultsFixture, turnFixture } from './test-fixtures';
import { messageBody, newRequestId, routingLabel } from './message-routing';
import type { History, MessageRequest } from './types';
import { identityStorageScope, scopedStorageKey } from './browser-scope';

const storageScope = identityStorageScope('groundwater', 'demo', 'test-user');
const cacheKey = (kind: string, id = '') => scopedStorageKey(storageScope, kind, id);

class Events {
  static latest: Events;
  listeners = new Map<string, (event: MessageEvent) => void>();
  constructor() { Events.latest = this; }
  addEventListener(type: string, handler: (event: MessageEvent) => void) { this.listeners.set(type, handler); }
  close() {}
  emit(type: string) { this.listeners.get(type)?.(new MessageEvent(type, { lastEventId: '2' })); }
}
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
let posts: { path: string; body: MessageRequest }[];
let history: History;
let post: (body: MessageRequest) => Promise<Response>;
beforeEach(() => {
  sessionStorage.clear(); window.history.replaceState({}, '', '/#/sessions/routing');
  posts = []; history = historyFixture({ sessionId: 'routing', items: [], turns: [] });
  post = async body => json(messageReceiptFixture({ requestId: body.requestId, sessionId: 'routing' }));
  vi.stubGlobal('EventSource', Events);
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      if (path.endsWith('/sessions')) return json(sessionResponseFixture({ sessionId: 'created-routing', goalRevision: 0 }));
      const body = JSON.parse(String(init.body)); posts.push({ path, body }); return post(body);
    }
    if (path.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (path.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (path.includes('/results')) return json(sessionResultsFixture([], { sessionId: 'routing' }));
    if (path.endsWith('/messages')) return json({ ...history, sessionId: path.split('/')[4] });
    if (path.includes('/trace')) return json({});
    throw new Error(`Unexpected URL ${path}`);
  }));
});
async function ready() {
  await screen.findByTitle('项目：demo');
  const input = screen.getByRole('textbox', { name: '消息输入框' });
  // Capabilities arrive before the session snapshot. Wait for the same
  // snapshot-recovery boundary that the UI shows to users, so Enter tests do
  // not race the initial asynchronous load (some scenarios intentionally keep
  // the send button disabled because a turn is still running).
  await waitFor(() => expect(screen.queryByText('正在恢复对话…')).not.toBeInTheDocument());
  return input;
}
it('creates RFC 4122 version 4 request IDs without secure-context randomUUID', () => {
  const secureContextMethod = vi.spyOn(crypto, 'randomUUID').mockImplementation(() => { throw new Error('secure context only'); });
  try {
    const id = newRequestId();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(secureContextMethod).not.toHaveBeenCalled();
  } finally {
    secureContextMethod.mockRestore();
  }
});
it('sends default AUTO to the unified endpoint and preserves business identifiers', async () => {
  render(<App />); const input = await ready();
  expect(screen.queryByRole('combobox', { name: '提问方式' })).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: '井号 W01，UID-123 是业务编号' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(posts).toEqual([{ path: '/api/v12/sessions/routing/messages', body: {
    requestId: expect.any(String), requestedMode: 'AUTO', message: '井号 W01，UID-123 是业务编号',
  } }]));
  await waitFor(() => expect(input).toHaveValue(''));
});
it('recovers independently from corrupted attachment, pending-request and routing caches', async () => {
  sessionStorage.setItem(cacheKey('draft', 'routing'), '保留的草稿');
  sessionStorage.setItem(cacheKey('draft-attachments', 'routing'), '{');
  sessionStorage.setItem(cacheKey('message-request', 'routing'), JSON.stringify({ body: { requestedMode: 'AUTO' } }));
  sessionStorage.setItem(cacheKey('routing-draft', 'routing'), JSON.stringify({ body: null, attachments: [] }));
  sessionStorage.setItem('unrelated-cache', 'keep');

  render(<App />);
  const input = await ready();
  await waitFor(() => expect(input).toHaveValue('保留的草稿'));
  expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled();
  await waitFor(() => expect(sessionStorage.getItem(cacheKey('routing-draft', 'routing'))).toBeNull());
  expect(sessionStorage.getItem(cacheKey('draft-attachments', 'routing'))).not.toBe('{');
  expect(sessionStorage.getItem(cacheKey('message-request', 'routing'))).toBeNull();
  expect(sessionStorage.getItem('unrelated-cache')).toBe('keep');
});
it('does not expose mode overrides and treats former slash commands as ordinary AUTO text', async () => {
  render(<App />); const input = await ready();
  expect(screen.queryByRole('button', { name: '高级发送选项' })).not.toBeInTheDocument();
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: '/draft 排序后统计' } });
  expect(input).toHaveValue('/draft 排序后统计');
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(input).toHaveValue(''));
  expect(posts[0].body).toMatchObject({ requestedMode: 'AUTO', message: '/draft 排序后统计' });
});
it('restores and retries exactly the same request after a network failure, preserving edits for later', async () => {
  post = async () => { throw new TypeError('offline'); };
  const view = render(<App />); const input = await ready();
  fireEvent.change(input, { target: { value: '原始井号 W01' } }); fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByRole('button', { name: '重试发送' });
  const original = posts[0].body;
  expect(JSON.parse(sessionStorage.getItem(cacheKey('message-request', 'routing'))!).body).toEqual(original);
  fireEvent.change(input, { target: { value: '另一个问题 W02' } });
  view.unmount(); render(<App />);
  await screen.findByRole('button', { name: '重试发送' });
  await waitFor(() => expect(screen.getByRole('button', { name: '重试发送' })).toBeEnabled());
  post = async body => json(messageReceiptFixture({ requestId: body.requestId, sessionId: 'routing' }));
  fireEvent.click(screen.getByRole('button', { name: '重试发送' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '重试发送' })).not.toBeInTheDocument());
  expect(posts[1].body).toEqual(original);
  expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue('另一个问题 W02');
  expect(sessionStorage.getItem(cacheKey('message-request', 'routing'))).toBeNull();
});
it('recovers an accepted request from history after a lost response without posting again', async () => {
  const body = messageBody('已接收问题');
  sessionStorage.setItem(cacheKey('draft', 'routing'), body.message);
  sessionStorage.setItem(cacheKey('message-request', 'routing'), JSON.stringify({ body, draftText: body.message, attachments: [] }));
  history = historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content: body.message }], turns: [{ requestId: body.requestId, requestedMode: 'AUTO', routingStatus: 'ROUTING_RUNNING', status: 'ROUTING' }] });
  render(<App />);
  await screen.findByText('已接收问题');
  await waitFor(() => {
    expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue('');
    expect(screen.queryByRole('button', { name: '重试发送' })).not.toBeInTheDocument();
  });
  expect(posts).toHaveLength(0);
  expect(await screen.findByRole('status')).toHaveTextContent('正在判断需求');
});
it('renders persisted intent choices and sends the exact option through AUTO', async () => {
  history = historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '我需要一个可复用的工作流模板' }, { messageId: 2, goalRevision: 1, role: 'assistant', content: '你希望我如何继续？' }], turns: [{ requestId: 'original-request', requestedMode: 'AUTO', routingStatus: 'START_REQUESTED', intentDecision: 'NEEDS_CLARIFICATION', resolvedMode: 'DIALOGUE', clarificationRequired: true, reply: replyFixture('你希望我如何继续？'), interactionOptions: [{ label: '整理处理流程', description: '形成可复用流程', message: '请整理当前任务的可复用工作流。' }, { label: '普通说明', description: '只解释方法', message: '请只说明可行方法。' }] }] });
  render(<App />); const input = await ready();
  fireEvent.change(input, { target: { value: '另一个尚未发送的问题' } });
  const options = screen.getByRole('region', { name: '下一步选项' });
  expect(options.querySelector('button')).toHaveTextContent('整理处理流程形成可复用流程');
  expect(screen.queryByText('处理方式不对')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /整理处理流程/ }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ requestId: expect.any(String), requestedMode: 'AUTO', message: '请整理当前任务的可复用工作流。' });
  await waitFor(() => expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled());
  expect(input).toHaveValue('另一个尚未发送的问题'); expect(screen.getByText('你希望我如何继续？')).toBeVisible();
});
it('refreshes routing via durable events and exposes routing metadata only in debug details', async () => {
  window.history.replaceState({}, '', '/?debug=1#/sessions/routing');
  const turn = turnFixture({ requestId: 'routing-uid', requestedMode: 'AUTO', routingStatus: 'ROUTING_REQUESTED', status: 'ROUTING' });
  history = historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '我的需求' }], turns: [turn] });
  render(<App />); await screen.findByText('我的需求');
  expect(screen.getByRole('status')).toHaveTextContent('正在判断需求');
  history = historyFixture({ ...history, eventCursor: 2, turns: [{ ...turn, status: 'COMPLETE', routingStatus: 'START_REQUESTED', intentDecision: 'NEEDS_CLARIFICATION', resolvedMode: 'DIALOGUE', clarificationRequired: true }] });
  act(() => Events.latest.emit('intent_routing.resolved'));
  await waitFor(() => expect(screen.getByRole('button', { name: /^已完成/ })).toBeVisible());
  fireEvent.click(screen.getByRole('button', { name: /^已完成/ }));
  expect(screen.queryByText('需要补充说明')).not.toBeInTheDocument();
  expect(screen.getByText(/routing-uid/)).not.toBeVisible();
  fireEvent.click(screen.getByText('调试信息')); fireEvent.click(screen.getByText('需求判断与请求记录'));
  expect(screen.getByText(/routing-uid/)).toBeVisible();
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('/trace'))).toBe(true));
});
it('always sends natural-language messages through AUTO', () => {
  expect(messageBody('继续')).toMatchObject({ requestedMode: 'AUTO', message: '继续' });
  const automatic = turnFixture({ requestId: 'request', requestedMode: 'AUTO', routingStatus: 'DECIDED', intentDecision: 'DIALOGUE', resolvedMode: 'DIALOGUE' });
  expect(routingLabel({ ...automatic, routingStatus: 'FAILED' })).toBe('需求理解未完成');
});
it('does not expose explicit mode recovery for a failed AUTO router turn', async () => {
  history = historyFixture({
    items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '路由失败的原问题' }],
    turns: [{ requestId: 'failed-request', requestedMode: 'AUTO', routingStatus: 'FAILED',
      status: 'FAILED', errorCode: 'AGENT_INTENT_ROUTING_INVALID',
      diagnostics: { reason: 'AGENT_INTENT_ROUTING_INVALID' } }],
  });
  render(<App />); await ready();
  expect(screen.getByRole('button', { name: /^处理未完成/ })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.queryByText('处理方式不对')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '下一步选项' })).not.toBeInTheDocument();
  expect(posts).toHaveLength(0);
});
it('moves a homepage submission into its new session and retains the same requestId', async () => {
  window.location.hash = '#/';
  render(<App />); const input = await ready();
  fireEvent.change(input, { target: { value: '排序后统计' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({ path: '/api/v12/sessions/created-routing/messages', body: { requestedMode: 'AUTO' } });
  await waitFor(() => expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue(''));
  expect(sessionStorage.getItem(cacheKey('message-request'))).toBeNull();
  expect(sessionStorage.getItem(cacheKey('message-request', 'created-routing'))).toBeNull();
});

it('retains separate unsent text and attachment identities across sessions and reloads', async () => {
  const resource = { kind: 'data_version', service: 'data-primary', datasetId: 'dataset-1', versionId: 'version-3', semanticId: 'semantics-3', objectId: 'object-3', checksum: `sha256:${'a'.repeat(64)}`, name: '井号W01.csv', familyId: 'dataset-1', format: 'csv', mediaType: 'text/csv', sizeBytes: 12 };
  sessionStorage.setItem(cacheKey('draft-attachments', 'routing'), JSON.stringify([resource]));
  const view = render(<App />); const input = await ready();
  fireEvent.change(input, { target: { value: '第一份草稿' } });
  await act(async () => { window.location.hash = '#/sessions/second'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  const second = await ready(); expect(second).toHaveValue('');
  fireEvent.change(second, { target: { value: '第二个问题' } });
  await act(async () => { window.location.hash = '#/sessions/routing'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  const restored = await ready(); expect(restored).toHaveValue('第一份草稿');
  expect(screen.getByRole('button', { name: '井号W01.csv · version-3' })).toBeVisible();
  view.unmount(); render(<App />); const reloaded = await ready();
  await waitFor(() => expect(reloaded).toHaveValue('第一份草稿'));
  fireEvent.keyDown(reloaded, { key: 'Enter' });
  await waitFor(() => expect(reloaded).toHaveValue(''));
  expect(posts[0].body).toMatchObject({ requestedMode: 'AUTO', message: '第一份草稿', inputReferences: [{ datasetId: 'dataset-1', versionId: 'version-3', semanticId: 'semantics-3', objectId: 'object-3' }] });
  expect(sessionStorage.getItem(cacheKey('draft', 'second'))).toBe('第二个问题');
});

it('does not invent intent options when the original user message is not loaded', async () => {
  history = historyFixture({ nextCursor: 2, items: [{ messageId: 2, goalRevision: 1, role: 'assistant', content: '分页边界上的回复' }], turns: [{ requestId: 'old-request', requestedMode: 'AUTO', routingStatus: 'DECIDED', reply: replyFixture('分页边界上的回复') }] });
  render(<App />); await screen.findByText('分页边界上的回复');
  expect(screen.queryByText('处理方式不对')).not.toBeInTheDocument();
});

it('restores a failed AUTO draft and submits a fresh AUTO request without a correction identity', async () => {
  let original: MessageRequest;
  post = async body => {
    original = body;
    history = historyFixture({ eventCursor: 1, items: [{ messageId: 1, goalRevision: 1, role: 'user', content: body.message }], turns: [{ requestId: body.requestId, requestedMode: 'AUTO', routingStatus: 'ROUTING_RUNNING', status: 'ROUTING' }] });
    return json(messageReceiptFixture({ requestId: body.requestId, sessionId: 'routing' }));
  };
  render(<App />); const input = await ready();
  fireEvent.change(input, { target: { value: '请分析井号 W01' } }); fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(input).toHaveValue(''));
  history = { ...history, eventCursor: 2, turns: [turnFixture({ ...history.turns[0], status: 'FAILED', routingStatus: 'FAILED', errorCode: 'AGENT_INTENT_ROUTING_INVALID' })] };
  act(() => Events.latest.emit('intent_routing.failed'));
  await waitFor(() => expect(input).toHaveValue('请分析井号 W01'));
  expect(screen.queryByText('处理方式不对')).not.toBeInTheDocument();
  post = async body => json(messageReceiptFixture({ requestId: body.requestId, sessionId: 'routing' }));
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(input).toHaveValue(''));
  expect(posts[1].body).toEqual({ requestId: expect.any(String), requestedMode: 'AUTO', message: original!.message });
  expect(posts[1].body.requestId).not.toBe(original!.requestId);
  expect(JSON.parse(sessionStorage.getItem(cacheKey('routing-draft', 'routing'))!).body).toEqual(posts[1].body);
});
