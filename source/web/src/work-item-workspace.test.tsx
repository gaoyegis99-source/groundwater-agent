import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import sourceProfile from '../../products/groundwater/ui/product-ui.json';
import App from './App';
import { decodeProductUi, setProductUi } from './product-ui';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, instant, sessionSummaryFixture } from './test-fixtures';
import type { components } from './generated/public-api-v12';

const profile = decodeProductUi(sourceProfile);
const summary: components['schemas']['kernel__WorkItemSummary'] = {
  workItemId: 'work-1', title: '枯水期监测', description: '核对浅层井资料',
  projectId: 'demo', ownerSubject: 'test-user', status: 'ACTIVE',
  revision: 2, createdAt: instant, updatedAt: instant, archivedAt: null,
};
const sessions = [
  { sessionId: 'session-1', title: '水位资料核对', linkedAt: instant, archivedAt: null },
  { sessionId: 'session-2', title: '季节趋势讨论', linkedAt: instant, archivedAt: null },
];
const snapshot: components['schemas']['kernel__WorkItemLifecycleSnapshot'] = {
  workItemId: 'work-1', revision: 2, state: 'WAITING_INPUT', reason: 'TASK_INPUT_REQUIRED', eventCursor: 5,
  tracks: sessions.map(({ sessionId }) => ({
    sessionId, taskId: null, taskRevision: null, state: 'WAITING_INPUT', reason: 'TASK_INPUT_REQUIRED',
    confirmationId: null, stopPending: false, publications: [], review: null, acceptedAt: null,
  })),
};

const detail: components['schemas']['kernel__WorkItemDetail'] = {
  workItem: summary, sessions, tasks: [], taskRuns: [], agentRuns: [], workflowRuns: [],
  assets: [{ linkId: 'hidden-asset-id', linkedAt: instant, reference: null, state: 'UNAVAILABLE' }],
  objects: [{ linkId: 'hidden-object-id', linkedAt: instant, reference: null, state: 'UNAVAILABLE' }],
};

class EventStream extends EventTarget {
  static CLOSED = 2;
  static connections: EventStream[] = [];
  readyState = 1;
  constructor(readonly url: string) { super(); EventStream.connections.push(this); }
  close() { this.readyState = EventStream.CLOSED; }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function fixture({
  onCreate, onArchive, onReview, readSnapshot, onCreateSession, onLinkSession, readContext, customResponse, readDetail, candidates = [], role = 'researcher',
}: {
  onCreate?: (command: Record<string, unknown>) => Promise<Response>;
  onArchive?: (command: Record<string, unknown>) => Promise<Response>;
  onReview?: (command: Record<string, unknown>) => Promise<Response>;
  readSnapshot?: () => typeof snapshot;
  onCreateSession?: (command: Record<string, unknown>) => Promise<Response>;
  onLinkSession?: (command: Record<string, unknown>) => Promise<Response>;
  readContext?: (sessionId: string) => Promise<Response>;
  customResponse?: (url: URL, init?: RequestInit) => Promise<Response | undefined>;
  readDetail?: () => typeof detail;
  candidates?: ReturnType<typeof sessionSummaryFixture>[];
  role?: 'researcher' | 'viewer';
} = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const parsed = new URL(url, window.location.origin);
    const { pathname, searchParams } = parsed;
    const response = await customResponse?.(parsed, init);
    if (response) return response;
    if (pathname === '/api/v12/capabilities') {
      return json(capabilitiesForProfile(profile, {
        currentUser: { subject: 'test-user', displayName: '测试用户', roles: [role] },
        services: ['data', 'knowledge-next'],
      }));
    }
    if (pathname === '/api/v12/sessions') return json({ items: candidates, nextCursor: null });
    if (pathname.endsWith('/messages') && pathname.startsWith('/api/v12/sessions/')) {
      return json(historyFixture({ sessionId: pathname.split('/')[4], turns: [] }));
    }
    if (pathname.endsWith('/results') && pathname.startsWith('/api/v12/sessions/')) {
      return json({ sessionId: pathname.split('/')[4], items: [], nextCursor: null });
    }
    if (pathname.endsWith('/work-item') && pathname.startsWith('/api/v12/sessions/')) {
      return readContext?.(pathname.split('/')[4]) ?? json({ context: null });
    }
    if (pathname === '/api/v12/work-items' && init?.method === 'POST') return onCreate!(JSON.parse(String(init.body)));
    if (pathname === '/api/v12/work-items/work-1/archive') return onArchive!(JSON.parse(String(init?.body)));
    if (pathname === '/api/v12/work-items/work-1/review') return onReview!(JSON.parse(String(init?.body)));
    if (pathname === '/api/v12/work-items/work-1/sessions/create') return onCreateSession!(JSON.parse(String(init?.body)));
    if (pathname === '/api/v12/work-items/work-1/sessions') return onLinkSession!(JSON.parse(String(init?.body)));
    expect(searchParams.get('projectId')).toBe('demo');
    if (pathname === '/api/v12/work-items') {
      return searchParams.has('after')
        ? json({ items: [{ ...summary, workItemId: 'work-2', title: '区域补给核查' }], nextAfter: null })
        : json({ items: [summary], nextAfter: 'work-1' });
    }
    if (pathname.endsWith('/snapshot')) return json({ ...(readSnapshot?.() ?? snapshot), workItemId: pathname.split('/')[4] });
    if (pathname.endsWith('/activity')) {
      return json({ entries: [{ kind: 'SESSION', sequence: 5, createdAt: instant, sessionId: 'session-1', eventType: 'task.revised' }], nextAfter: null });
    }
    if (pathname === '/api/v12/work-items/work-1') return json(readDetail?.() ?? detail);
    throw new Error(`Unhandled API ${init?.method} ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function open(hash: string) {
  act(() => {
    window.history.replaceState({}, '', `/${hash}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

async function selectSection(name: string) {
  const navigation = await screen.findByRole('navigation', { name: '工作分区' });
  await userEvent.setup().click(within(navigation).getByRole('link', { name }));
}

beforeEach(() => {
  window.history.replaceState({}, '', '/#/');
  sessionStorage.clear();
  setProductUi(profile);
  EventStream.connections = [];
  vi.stubGlobal('EventSource', EventStream);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('switches work sections through links and history while preserving the work read and event stream', async () => {
  const fetchMock = fixture({ readDetail: () => ({ ...detail, workflowRuns: [{
    runId: 'run-1', agentRunId: 'agent-1', sessionId: 'session-1',
    workflowRef: { id: 'monitoring', version: '1.0.0', checksum: `sha256:${'1'.repeat(64)}` },
  }] }) });
  const user = userEvent.setup();
  open('#/work-items/work-1');
  render(<App />);
  const navigation = await screen.findByRole('navigation', { name: '工作分区' });
  expect(within(navigation).getByRole('link', { name: '概览' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: '继续对话' })).toHaveAttribute('href', '#/sessions/session-2?workItem=work-1');
  expect(screen.queryByRole('region', { name: '关联会话' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.includes('/activity?'))).toBe(false);
  const readCount = () => fetchMock.mock.calls.filter(([url]) => new URL(url, window.location.origin).pathname === '/api/v12/work-items/work-1').length;
  const material = within(navigation).getByRole('link', { name: '资料' });
  expect(material).toHaveAttribute('href', '#/work-items/work-1/materials');
  await waitFor(() => expect(EventStream.connections).toHaveLength(1));
  const readsBeforeNavigation = readCount();
  material.focus();
  await user.keyboard('{Enter}');
  expect(await screen.findByRole('region', { name: '资料与业务对象' })).toBeVisible();
  expect(screen.getByRole('group', { name: '资料与业务对象' })).toHaveFocus();
  expect(material).toHaveAttribute('aria-current', 'page');
  expect(screen.queryByRole('button', { name: '归档工作' })).not.toBeInTheDocument();
  await user.click(within(navigation).getByRole('link', { name: '活动' }));
  expect(await screen.findByText('任务已更新')).toBeVisible();
  expect(screen.queryByRole('region', { name: '资料与业务对象' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '枯水期监测' })).toBeVisible();
  act(() => window.history.back());
  expect(await screen.findByRole('region', { name: '资料与业务对象' })).toBeVisible();
  expect(material).toHaveAttribute('aria-current', 'page');
  expect(EventStream.connections).toHaveLength(1);
  expect(EventStream.connections[0].readyState).not.toBe(EventStream.CLOSED);
  expect(readCount()).toBe(readsBeforeNavigation);
  expect(fetchMock.mock.calls.some(([url]) => url.includes('/workflow-runs/'))).toBe(false);
});

it('loads the task section directly and routes tracks to the existing session workbench', async () => {
  fixture({ readSnapshot: () => ({ ...snapshot, tracks: [
    { ...snapshot.tracks[0], taskId: 'task-1', taskRevision: 2 },
  ] }) });
  open('#/work-items/work-1/tasks');
  render(<App />);
  const progress = await screen.findByRole('region', { name: '任务与进度' });
  expect(await within(progress).findByRole('link', { name: '查看任务与运行 · 水位资料核对' })).toHaveAttribute('href', '#/sessions/session-1/workbench/tasks?workItem=work-1');
  expect(within(progress).getByRole('link', { name: '配置参数 · 水位资料核对' })).toHaveAttribute('href', '#/sessions/session-1/workbench/parameters?workItem=work-1');
  expect(screen.queryByRole('region', { name: '成果中心' })).not.toBeInTheDocument();
});

it('removes the detail and all section links when the server revokes access on a refresh', async () => {
  let denied = false;
  fixture({ customResponse: async ({ pathname }) => {
    if (denied && pathname === '/api/v12/work-items/work-1/snapshot') return json({ code: 'FORBIDDEN' }, 403);
  } });
  open('#/work-items/work-1/conversations');
  render(<App />);
  expect(await screen.findByRole('link', { name: '水位资料核对' })).toBeVisible();
  await waitFor(() => expect(EventStream.connections).toHaveLength(1));
  denied = true;
  act(() => EventStream.connections[0].dispatchEvent(new Event('work_item.refresh')));
  expect(await screen.findByRole('alert')).toHaveTextContent('当前身份无权访问');
  expect(screen.queryByRole('heading', { name: '枯水期监测' })).not.toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: '工作分区' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '水位资料核对' })).not.toBeInTheDocument();
});

it('keeps an unknown work section invalid without reading a work item', async () => {
  const fetchMock = fixture();
  open('#/work-items/work-1/unknown');
  render(<App />);
  expect(await screen.findByRole('alert')).toHaveTextContent('工作链接无效');
  expect(screen.getByRole('link', { name: '工作列表' })).toHaveAttribute('href', '#/work-items');
  expect(fetchMock.mock.calls.some(([url]) => url.includes('/work-items/work-1'))).toBe(false);
});

it('replays a submitted edit across sections and a page reload after the authority has advanced', async () => {
  const title = '已核对的枯水期监测';
  const commands: unknown[] = [];
  let finishFirst: ((value: Response) => void) | undefined;
  let firstSignal: AbortSignal | null | undefined;
  const receipt = () => json({ workItemId: 'work-1', revision: 3, changed: true, linkId: null });
  fixture({
    readDetail: () => commands.length ? { ...detail, workItem: { ...summary, title, revision: 3 } } : detail,
    readSnapshot: () => commands.length ? { ...snapshot, revision: 3, eventCursor: 6 } : snapshot,
    customResponse: async ({ pathname }, init) => {
      if (pathname !== '/api/v12/work-items/work-1/update') return;
      commands.push(JSON.parse(String(init?.body)));
      if (commands.length === 1) {
        firstSignal = init?.signal;
        return new Promise<Response>(resolve => { finishFirst = resolve; });
      }
      return receipt();
    },
  });
  open('#/work-items/work-1');
  const page = render(<App />);
  const edit = await screen.findByRole('button', { name: '编辑标题和说明' });
  await waitFor(() => expect(edit).toBeEnabled());
  fireEvent.click(edit);
  fireEvent.change(screen.getByRole('textbox', { name: '标题' }), { target: { value: title } });
  fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
  await waitFor(() => expect(finishFirst).toBeTypeOf('function'));
  await selectSection('活动');
  await screen.findByRole('region', { name: '活动' });
  expect(firstSignal?.aborted).toBe(true);
  await act(async () => finishFirst?.(receipt()));
  await selectSection('概览');
  expect(await screen.findByRole('button', { name: '重试保存' })).toBeInTheDocument();
  page.unmount();
  render(<App />);
  expect(await screen.findByRole('heading', { name: title })).toBeVisible();
  expect(screen.getByRole('textbox', { name: '标题' })).toHaveValue(title);
  expect(screen.getByRole('textbox', { name: '标题' })).toBeDisabled();
  const replay = screen.getByRole('button', { name: '重试保存' });
  await waitFor(() => expect(replay).toBeEnabled());
  expect(commands).toHaveLength(1);
  fireEvent.click(replay);
  expect(await screen.findByRole('button', { name: '编辑标题和说明' })).toBeVisible();
  expect(commands).toHaveLength(2);
  expect(commands[0]).toEqual({ projectId: 'demo', requestId: expect.any(String), expectedRevision: 2,
    title, description: '核对浅层井资料' });
  expect(commands[1]).toEqual(commands[0]);
});

it('skips directly to the work main region without changing the section URL', async () => {
  fixture();
  open('#/work-items/work-1/tasks');
  render(<App />);
  const skip = await screen.findByRole('link', { name: '跳到主要内容' });
  skip.focus();
  await userEvent.setup().keyboard('{Enter}');
  expect(screen.getByRole('main')).toHaveFocus();
  expect(window.location.hash).toBe('#/work-items/work-1/tasks');
});

it('associates one exact historical semantic version and replays a lost attach receipt', async () => {
  const pinned = dataRef({ semanticId: 'semantics-historical' });
  const version = dataDetail({
    project: 'demo', title: '水位观测资料',
    semantics: { description: '当前定义', crs: 'EPSG:4326' },
  });
  const submitted: Record<string, unknown>[] = [];
  fixture({ customResponse: async ({ pathname }, init) => {
    if (pathname === '/api/v12/data/datasets') return json({
      items: [{ datasetId: version.datasetId, title: version.title, currentVersion: version.versionId,
        project: 'demo', tags: [], createdAt: instant, formats: ['json'], semantics: {} }],
      nextCursor: null, matchBasis: 'catalog',
    });
    if (pathname.endsWith('/datasets/dataset-1/versions')) return json({ items: [], nextCursor: null });
    if (pathname.endsWith('/datasets/dataset-1/quality-runs')) return json({ items: [], nextCursor: null });
    if (pathname.endsWith('/versions/version-1')) return json(version);
    if (pathname.endsWith('/versions/version-1/semantics')) return json({
      items: [{ semanticId: pinned.semanticId, createdAt: instant, actor: {} }], nextCursor: null,
    });
    if (pathname.endsWith('/versions/version-1/semantics/semantics-historical')) return json({
      versionId: 'version-1', semanticId: pinned.semanticId, createdAt: instant, actor: {},
      body: { description: '冻结水位语义', source: 'research', verticalDatum: '1985 国家高程基准', timeRange: '2020—2021' },
    });
    if (pathname.endsWith('/work-items/work-1/assets')) {
      submitted.push(JSON.parse(String(init?.body)));
      if (submitted.length === 1) throw new TypeError('receipt was lost');
      return json({ workItemId: 'work-1', revision: 3, linkId: 'exact-asset', changed: true });
    }
  } });
  open('#/work-items/work-1');
  render(<App />);
  await selectSection('资料');
  fireEvent.click(await screen.findByRole('button', { name: '选择数据资料' }));
  fireEvent.click(await screen.findByRole('button', { name: /水位观测资料/ }));
  await screen.findByRole('option', { name: /semantics-historical/ });
  fireEvent.change(screen.getByLabelText('语义修订'), { target: { value: pinned.semanticId } });
  expect(await screen.findByText('1985 国家高程基准')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '选择此资料版本' }));
  fireEvent.click(screen.getByRole('button', { name: '确认关联资料' }));
  expect(await screen.findByRole('button', { name: '重试关联' })).toBeVisible();
  await selectSection('活动');
  await screen.findByRole('region', { name: '活动' });
  await selectSection('资料');
  expect(submitted).toHaveLength(1);
  fireEvent.click(await screen.findByRole('button', { name: '重试关联' }));
  await waitFor(() => expect(submitted).toHaveLength(2));
  expect(submitted[0]).toEqual({
    projectId: 'demo', requestId: expect.any(String), expectedRevision: 2, reference: { kind: 'DATA', dataRef: pinned },
  });
  expect(submitted[1]).toEqual(submitted[0]);
});

it('chooses a field from the exact source before saving a locally scoped business object', async () => {
  const source = dataDetail({
    project: 'demo', title: '井台账', descriptor: { fields: [
      { fieldId: 'key-well', name: '井号', type: 'string' },
      { fieldId: 'no-semantics', name: '未定义字段', type: 'string' },
    ] },
    semantics: { fields: { 'key-well': { meaning: '项目井号' } } },
  });
  const post = vi.fn();
  let linked = false;
  const expected = { objectKind: 'AQUIFER', fieldId: 'key-well', externalId: 'AQ-02', source: dataRef() } as const;
  fixture({
    readDetail: () => linked ? { ...detail, workItem: { ...summary, revision: 3 }, objects: [
      { linkId: 'object-1', linkedAt: instant, state: 'AVAILABLE', reference: expected },
    ] } : detail,
    readSnapshot: () => linked ? { ...snapshot, revision: 3, eventCursor: 6 } : snapshot,
    customResponse: async ({ pathname }, init) => {
      if (pathname === '/api/v12/data/datasets') return json({
        items: [{ datasetId: source.datasetId, title: source.title, currentVersion: source.versionId,
          project: 'demo', tags: [], createdAt: instant, formats: ['json'], semantics: {} }],
        nextCursor: null, matchBasis: 'catalog',
      });
      if (pathname.endsWith('/versions/version-1')) return json(source);
      if (pathname.endsWith('/versions') || pathname.endsWith('/semantics') || pathname.endsWith('/quality-runs')) {
        return json({ items: [], nextCursor: null });
      }
      if (pathname.endsWith('/work-items/work-1/objects')) {
        post(JSON.parse(String(init?.body)));
        linked = true;
        return json({ workItemId: 'work-1', revision: 3, linkId: 'object-1', changed: true });
      }
    },
  });
  open('#/work-items/work-1');
  render(<App />);
  await selectSection('资料');
  fireEvent.click(await screen.findByRole('button', { name: '关联监测井、区域或含水层' }));
  fireEvent.click(await screen.findByRole('button', { name: /井台账/ }));
  fireEvent.click(await screen.findByRole('button', { name: '选择此对象来源' }));
  const form = screen.getByRole('form', { name: '填写关联业务对象' });
  expect(form).toHaveFocus();
  expect(within(form).queryByRole('option', { name: /未定义字段/ })).not.toBeInTheDocument();
  expect(within(form).getByRole('button', { name: '确认关联对象' })).toBeDisabled();
  fireEvent.change(within(form).getByLabelText('对象类型'), { target: { value: 'AQUIFER' } });
  fireEvent.change(within(form).getByLabelText('来源字段'), { target: { value: 'key-well' } });
  fireEvent.change(within(form).getByLabelText('业务键'), { target: { value: ' AQ-02 ' } });
  fireEvent.click(within(form).getByRole('button', { name: '确认关联对象' }));
  expect(await screen.findByText('含水层 · AQ-02')).toBeVisible();
  expect(screen.getByText('业务键来源字段 key-well')).toBeVisible();
  expect(post).toHaveBeenCalledExactlyOnceWith({
    projectId: 'demo', requestId: expect.any(String), expectedRevision: 2, reference: expected,
  });
});

it('closes a WorkItem data inspector when navigation changes the session', async () => {
  const reference = dataRef();
  fixture({
    candidates: sessions.map(session => sessionSummaryFixture({ sessionId: session.sessionId, title: session.title })),
    readContext: async sessionId => json({ context: sessionId === 'session-1' ? {
      sessionId, linkedAt: instant, workItem: summary, objects: [], openTasks: [], approvedActions: [],
      assets: [{ linkId: 'data-1', linkedAt: instant, state: 'AVAILABLE', reference: { kind: 'DATA', dataRef: reference } }],
    } : null }),
    customResponse: async ({ pathname }) => {
      if (pathname.endsWith('/data/versions/version-1')) return json(dataDetail({ title: '甲会话待选资料' }));
      if (pathname.endsWith('/data/datasets')) return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
      if (pathname.endsWith('/versions') || pathname.endsWith('/semantics') || pathname.endsWith('/quality-runs')) {
        return json({ items: [], nextCursor: null });
      }
    },
  });
  open('#/sessions/session-1?workItem=work-1');
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '打开 JSON 资料' }));
  expect(await screen.findByRole('heading', { name: '甲会话待选资料' })).toBeVisible();
  open('#/sessions/session-2');
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '数据库' })).not.toBeInTheDocument());
  expect(screen.queryByRole('button', { name: '附加精确版本' })).not.toBeInTheDocument();
  expect(screen.queryByText('甲会话待选资料')).not.toBeInTheDocument();
});

it('opens business work from home and loads subsequent pages with authoritative status', async () => {
  fixture();
  render(<App />);
  const entry = await screen.findByRole('link', { name: '业务工作' });
  expect(entry).toHaveAttribute('href', '#/work-items');
  open('#/work-items');
  expect(await screen.findByRole('link', { name: '枯水期监测' })).toHaveAttribute('href', '#/work-items/work-1');
  expect(await screen.findByText('待补充资料')).toBeInTheDocument();
  expect(screen.getByText('2 个会话')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '加载更多工作' }));
  expect(await screen.findByRole('link', { name: '区域补给核查' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '枯水期监测' })).toBeInTheDocument();
});

it('replays an uncertain creation with the original request ID after reload and opens the receipt', async () => {
  const create = vi.fn()
    .mockRejectedValueOnce(new TypeError('Network disconnected'))
    .mockResolvedValueOnce(json({ workItemId: 'work-1', revision: 1, changed: true, linkId: null }, 201));
  fixture({ onCreate: create });
  open('#/work-items/new');
  const rendered = render(<App />);
  const title = await screen.findByRole('textbox', { name: '标题' });
  fireEvent.change(title, { target: { value: '枯水期监测' } });
  fireEvent.change(screen.getByRole('textbox', { name: '说明（可选）' }), { target: { value: '核对浅层井资料' } });
  const submit = screen.getByRole('button', { name: '创建工作' });
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(await screen.findByRole('button', { name: '重试提交' })).toBeInTheDocument();
  expect(create).toHaveBeenCalledTimes(1);

  rendered.unmount();
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '重试提交' }));
  await waitFor(() => expect(window.location.hash).toBe('#/work-items/work-1'));
  expect(create).toHaveBeenCalledTimes(2);
  expect(create.mock.calls[0][0]).toEqual(create.mock.calls[1][0]);
  expect(await screen.findByRole('heading', { name: '枯水期监测' })).toBeInTheDocument();
});

it('restores detail, exposes both sessions, redacts unavailable references and tears down its event stream', async () => {
  fixture();
  open('#/work-items/work-1');
  render(<App />);
  expect(await screen.findByRole('heading', { name: '枯水期监测' })).toBeInTheDocument();
  await selectSection('对话');
  expect(await screen.findByRole('link', { name: '水位资料核对' })).toHaveAttribute('href', '#/sessions/session-1?workItem=work-1');
  expect(screen.getByRole('link', { name: '季节趋势讨论' })).toBeVisible();
  await selectSection('资料');
  const materials = await screen.findByRole('region', { name: '资料与业务对象' });
  expect(within(materials).getAllByText(/暂不可用/)).toHaveLength(2);
  expect(materials).not.toHaveTextContent('hidden-object-id');
  expect(materials).not.toHaveTextContent('hidden-asset-id');
  await selectSection('活动');
  expect(await screen.findByText('任务已更新')).toBeInTheDocument();
  await waitFor(() => expect(EventStream.connections[0]?.url).toContain('afterEventId=5'));
  open('#/work-items');
  await waitFor(() => expect(EventStream.connections[0].readyState).toBe(EventStream.CLOSED));
});

it('keeps archived state server-owned when an archive conflicts with another revision', async () => {
  const archive = vi.fn(async () => json({
    type: 'about:blank', title: 'Conflict', status: 409, detail: 'conflict',
    code: 'WORK_ITEM_REVISION_CONFLICT', requestId: 'request', traceId: 'trace', errors: [],
  }, 409));
  fixture({ onArchive: archive });
  open('#/work-items/work-1');
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '归档工作' }));
  fireEvent.click(screen.getByRole('button', { name: '确认归档' }));
  expect(await screen.findByText(/工作已更新，请核对最新状态/)).toBeInTheDocument();
  expect(archive).toHaveBeenCalledWith(expect.objectContaining({ projectId: 'demo', expectedRevision: 2 }));
  expect(screen.queryByText('已归档')).not.toBeInTheDocument();
});

it('keeps WorkItem actions and reads unavailable to a viewer even on a direct link', async () => {
  const fetchMock = fixture({ role: 'viewer' });
  open('#/work-items/work-1/results');
  render(<App />);
  expect(await screen.findByText('当前身份无法访问业务工作')).toBeVisible();
  expect(screen.queryByRole('button', { name: '归档工作' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.includes('/work-items'))).toBe(false);
});

it('discards a late list response after switching to another subject', async () => {
  const { WorkItemWorkspace } = await import('./WorkItemWorkspace');
  let resolvePrevious: ((value: Response) => void) | undefined;
  let signal: AbortSignal | null | undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('projectId=first')) {
      signal = init?.signal;
      return new Promise<Response>(resolve => { resolvePrevious = resolve; });
    }
    if (url.includes('/snapshot')) return json({ ...snapshot, workItemId: 'work-2', state: 'DRAFT', tracks: [] });
    return json({
      items: [{ ...summary, title: '当前身份的工作', workItemId: 'work-2', projectId: 'second', ownerSubject: 'second-user' }],
      nextAfter: null,
    });
  }));
  const route = { kind: 'list' } as const;
  const props = { route, brand: 'Groundwater', mark: '/icon', signOut: null, permitted: true, identityError: false,
    dataEnabled: true, knowledgeEnabled: true, retryIdentity: () => {}, onSessionsChange: () => {} };
  const initial = render(<WorkItemWorkspace {...props} project="first" subject="first-user" scope="groundwater:first:first-user" />);
  await waitFor(() => expect(resolvePrevious).toBeTypeOf('function'));
  initial.rerender(<WorkItemWorkspace {...props} project="second" subject="second-user" scope="groundwater:second:second-user" />);
  expect(await screen.findByRole('link', { name: '当前身份的工作' })).toBeVisible();
  expect(signal?.aborted).toBe(true);
  await act(async () => {
    resolvePrevious?.(json({ items: [{ ...summary, title: '前一身份的工作', projectId: 'first', ownerSubject: 'first-user' }], nextAfter: null }));
  });
  expect(screen.queryByText('前一身份的工作')).not.toBeInTheDocument();
});

it('invalidates an open review confirmation when the verified result set changes', async () => {
  const review = vi.fn(async () => json({ workItemId: 'work-1', revision: 3, changed: true, linkId: null }));
  function reviewing(token: string, cursor: number): typeof snapshot {
    return {
      ...snapshot, eventCursor: cursor, state: 'WAITING_REVIEW', reason: 'DELIVERY_REVIEW_REQUIRED',
      tracks: [{ ...snapshot.tracks[0], state: 'WAITING_REVIEW', review: { token, resultLinkIds: [] } }],
    };
  }
  const activeToken = `sha256:${'a'.repeat(64)}`;
  const laterToken = `sha256:${'b'.repeat(64)}`;
  let reviewed = reviewing(activeToken, 5);
  fixture({ onReview: review, readSnapshot: () => reviewed });
  open('#/work-items/work-1');
  render(<App />);
  await selectSection('成果');
  const candidate = await screen.findByRole('button', { name: '验收 · 水位资料核对' });
  await waitFor(() => expect(candidate).toBeEnabled());
  fireEvent.click(candidate);
  expect(screen.getByRole('button', { name: '确认验收' })).toBeEnabled();
  reviewed = reviewing(laterToken, 6);
  act(() => EventStream.connections[0].dispatchEvent(new Event('work_item.refresh')));
  expect(await screen.findByText('交付已变化，请重新核对并发起验收。')).toBeVisible();
  expect(screen.queryByRole('button', { name: '确认验收' })).not.toBeInTheDocument();
  expect(review).not.toHaveBeenCalled();
});

it.each([
  ['PREPARING', '待发布'],
  ['FAILED', '发布失败'],
] as const)('separates execution from publication in the work results center (%s)', async (state, label) => {
  const workflowRef = { id: 'monitoring', version: '1.0.0', checksum: `sha256:${'1'.repeat(64)}` };
  const run = {
    runId: 'run-1', agentRunId: 'agent-1', projectId: 'demo', toolCallId: 'tool-1', position: 0,
    modelId: 'monitoring', modelVersion: '1.0.0', modelChecksum: workflowRef.checksum,
    workflowId: workflowRef.id, workflowVersion: workflowRef.version, workflowChecksum: workflowRef.checksum,
    executorId: 'executor', executionService: 'executor', remoteJobId: 'job-1', status: 'SUCCEEDED',
    outputs: [], arguments: {}, error: null, createdAt: instant, startedAt: instant, finishedAt: instant, durationMs: 1,
  };
  fixture({
    readDetail: () => ({ ...detail, workflowRuns: [{ runId: 'run-1', agentRunId: 'agent-1', sessionId: 'session-1', workflowRef }] }),
    customResponse: async ({ pathname }) => {
      if (pathname === '/api/v12/workflow-runs/run-1') return json(run);
      if (pathname === '/api/v12/workflow-runs/run-1/publication') {
        return json({ workflowRunId: 'run-1', publicationId: 'publication-1', state });
      }
      return undefined;
    },
  });
  open('#/work-items/work-1');
  render(<App />);
  await selectSection('成果');
  const center = await screen.findByRole('region', { name: '成果中心' });
  expect(await within(center).findByText('执行成功')).toBeVisible();
  expect(await within(center).findByText(label)).toBeVisible();
  expect(within(center).queryByRole('button', { name: /复用|确认验收/ })).not.toBeInTheDocument();
});

function publishedResultFixture({ isLinked = () => true, onAssociate }: {
  isLinked?: () => boolean;
  onAssociate?: (command: unknown) => Promise<Response>;
} = {}) {
  const output = { ...dataRef(), outputName: 'result-summary' };
  const reference = { kind: 'RESULT', runId: 'run-1', publicationId: 'publication-1', outputName: output.outputName, dataRef: output } as const;
  const workflowRef = { id: 'monitoring', version: '1.0.0', checksum: `sha256:${'1'.repeat(64)}` };
  let readable = true;
  let workAccessible = true;
  let linkAvailable = true;
  let outputAvailable = true;
  let workDetailDelay: { wait: Promise<void>; started: () => void } | undefined;
  const run = {
    runId: 'run-1', agentRunId: 'agent-1', projectId: 'demo', toolCallId: 'tool-1', position: 0,
    modelId: 'monitoring', modelVersion: '1.0.0', modelChecksum: workflowRef.checksum,
    workflowId: workflowRef.id, workflowVersion: workflowRef.version, workflowChecksum: workflowRef.checksum,
    executorId: 'executor', executionService: 'executor', remoteJobId: 'job-1', status: 'SUCCEEDED',
    outputs: [output], arguments: {}, error: null, createdAt: instant, startedAt: instant, finishedAt: instant, durationMs: 1,
  };
  fixture({
    readSnapshot: () => onAssociate && isLinked() ? { ...snapshot, revision: 3, eventCursor: 6 } : snapshot,
    readDetail: () => ({ ...detail, workItem: onAssociate && isLinked() ? { ...summary, revision: 3 } : summary,
      agentRuns: [{ sessionId: 'session-1', agentRunId: 'agent-1', goalRevision: 1 }],
      workflowRuns: [{ runId: 'run-1', agentRunId: 'agent-1', sessionId: 'session-1', workflowRef }],
      assets: isLinked() ? [{ linkId: 'result-link', linkedAt: instant, reference: readable && linkAvailable ? reference : null,
        state: readable && linkAvailable ? 'AVAILABLE' : 'UNAVAILABLE' }] : [] }),
    customResponse: async ({ pathname }, init) => {
      if (pathname === '/api/v12/work-items/work-1' && workDetailDelay) {
        workDetailDelay.started();
        await workDetailDelay.wait;
      }
      if (!workAccessible && pathname === '/api/v12/work-items/work-1/snapshot') return json({ code: 'FORBIDDEN' }, 403);
      if (onAssociate && pathname === '/api/v12/work-items/work-1/assets') return onAssociate(JSON.parse(String(init?.body)));
      if (pathname === '/api/v12/workflow-runs/run-1') return json(outputAvailable ? run : { ...run, outputs: [] });
      if (pathname === '/api/v12/workflow-runs/run-1/publication') {
        return json({ workflowRunId: 'run-1', publicationId: 'publication-1', state: 'PUBLISHED' });
      }
      if (pathname === '/api/v12/agent-runs/agent-1/trace') return json({ agentRunId: 'agent-1', traceId: 'trace-1', trace: null, spans: [] });
      if (pathname.startsWith('/api/v12/data/versions/version-1') && !readable) return json({ code: 'DATA_VERSION_NOT_FOUND' }, 404);
      if (pathname === '/api/v12/data/versions/version-1') return json(dataDetail({ semanticId: 'semantics-current' }));
      if (pathname === '/api/v12/data/versions/version-1/semantics/semantics-1') return json({
        versionId: 'version-1', semanticId: 'semantics-1', createdAt: instant, actor: {}, body: { description: '锁定成果语义' },
      });
      if (pathname === '/api/v12/data/versions/version-1/objects/object-1/content') return json({ monitoringHighlights: 4 });
      if (pathname === '/api/v12/data/datasets') return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
      if (pathname.endsWith('/versions') || pathname.endsWith('/semantics') || pathname.endsWith('/quality-runs')) {
        return json({ items: [], nextCursor: null });
      }
      return undefined;
    },
  });
  return {
    revokeDataAccess: () => { readable = false; },
    revokeWorkAccess: () => { workAccessible = false; },
    revokeResultLink: () => { linkAvailable = false; },
    revokeResultOutput: () => { outputAvailable = false; },
    pauseWorkDetail: () => {
      let resume!: () => void;
      const started = vi.fn();
      workDetailDelay = { started, wait: new Promise(resolve => { resume = resolve; }) };
      return { started, resume };
    },
  };
}

it.each(['available', 'denied'] as const)('recovers a lost result association receipt after the exact output is already linked (Data %s)', async data => {
  const commands: unknown[] = [];
  const { revokeDataAccess } = publishedResultFixture({
    isLinked: () => commands.length > 0,
    onAssociate: async command => {
      commands.push(command);
      if (commands.length === 1) throw new TypeError('receipt was lost');
      return json({ workItemId: 'work-1', revision: 3, linkId: 'result-link', changed: true });
    },
  });
  open('#/work-items/work-1/results');
  const page = render(<App />);
  const openOutput = () => screen.findByRole('button', { name: '查看成果 · result summary' });
  fireEvent.click(await openOutput());
  const associate = await screen.findByRole('button', { name: '关联到此工作' });
  await waitFor(() => expect(associate).toBeEnabled());
  fireEvent.click(associate);
  await screen.findByRole('button', { name: '重试关联成果' });
  page.unmount();
  if (data === 'denied') revokeDataAccess();
  render(<App />);
  expect(await screen.findByText('成果关联回执待核对')).toBeVisible();
  expect(commands).toHaveLength(1);
  fireEvent.click(await openOutput());
  if (data === 'available') expect(await screen.findByText('已关联到工作')).toBeVisible();
  else {
    expect(await screen.findByText('成果不可用或当前身份无权读取。')).toBeVisible();
    expect(screen.queryByRole('button', { name: '关联到此工作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '在数据工作台查看' })).not.toBeInTheDocument();
  }
  const replay = await screen.findByRole('button', { name: '重试关联成果' });
  await waitFor(() => expect(replay).toBeEnabled());
  fireEvent.click(replay);
  await waitFor(() => expect(commands).toHaveLength(2));
  expect(commands[0]).toEqual({ projectId: 'demo', requestId: expect.any(String), expectedRevision: 2,
    reference: { kind: 'RESULT', runId: 'run-1', outputName: 'result-summary' } });
  expect(commands[1]).toEqual(commands[0]);
});

it('opens only the checked output, then removes its viewer and reuse action when Data access is revoked', async () => {
  const { revokeDataAccess } = publishedResultFixture();
  open('#/work-items/work-1');
  render(<App />);
  await selectSection('成果');
  const center = await screen.findByRole('region', { name: '成果中心' });
  expect(within(center).queryByText('预览来源与范围')).not.toBeInTheDocument();
  expect(within(center).queryByRole('button', { name: '在数据工作台查看' })).not.toBeInTheDocument();
  fireEvent.click(await within(center).findByRole('button', { name: '查看成果 · result summary' }));
  expect(await within(center).findByRole('region', { name: '预览来源与范围' })).toBeVisible();
  expect(within(center).getByLabelText('复用目标会话')).toHaveValue('');
  const opener = await within(center).findByRole('button', { name: '在数据工作台查看' });
  await userEvent.setup().click(opener);
  const database = await screen.findByRole('dialog', { name: '数据库' });
  expect(await within(database).findByText('锁定成果语义')).toBeVisible();
  expect(within(database).getByRole('link', { name: '下载对象' })).toHaveAttribute('href', '/api/v12/data/versions/version-1/objects/object-1/content');
  fireEvent.click(within(database).getByRole('button', { name: '关闭数据库' }));
  await waitFor(() => expect(opener).toHaveFocus());
  revokeDataAccess();
  act(() => window.dispatchEvent(new Event('focus')));
  expect(await within(center).findByText('成果不可用或当前身份无权读取。')).toBeVisible();
  expect(within(center).queryByRole('region', { name: '预览来源与范围' })).not.toBeInTheDocument();
  expect(within(center).queryByLabelText('复用目标会话')).not.toBeInTheDocument();
  expect(within(center).queryByRole('button', { name: '在数据工作台查看' })).not.toBeInTheDocument();
});

it('closes an already open result inspector when WorkItem access is revoked', async () => {
  const { revokeWorkAccess } = publishedResultFixture();
  open('#/work-items/work-1/results');
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '查看成果 · result summary' }));
  await userEvent.setup().click(await screen.findByRole('button', { name: '在数据工作台查看' }));
  const database = await screen.findByRole('dialog', { name: '数据库' });
  expect(await within(database).findByText('锁定成果语义')).toBeVisible();
  revokeWorkAccess();
  act(() => EventStream.connections[0].dispatchEvent(new Event('work_item.refresh')));
  await waitFor(() => expect(database).not.toBeInTheDocument());
  expect(await screen.findByRole('alert')).toHaveTextContent('当前身份无权访问');
  await waitFor(() => expect(screen.getByRole('heading', { name: '业务工作详情' })).toHaveFocus());
});

it.each(['linked reference', 'run output'] as const)('closes an open inspector after a refresh confirms loss of its %s', async missing => {
  const { pauseWorkDetail, revokeResultLink, revokeResultOutput } = publishedResultFixture({ isLinked: () => missing === 'linked reference' });
  open('#/work-items/work-1/results');
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '查看成果 · result summary' }));
  await userEvent.setup().click(await screen.findByRole('button', { name: '在数据工作台查看' }));
  const dialog = await screen.findByRole('dialog', { name: '数据库' });
  expect(await within(dialog).findByText('锁定成果语义')).toBeVisible();
  const { started, resume } = pauseWorkDetail();
  if (missing === 'linked reference') revokeResultLink();
  else revokeResultOutput();
  act(() => EventStream.connections[0].dispatchEvent(new Event('work_item.refresh')));
  await waitFor(() => expect(started).toHaveBeenCalled());
  expect(dialog).toBeVisible();
  await act(async () => resume());
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
  expect(screen.getByRole('heading', { name: '枯水期监测' })).toBeVisible();
});

it('replays the same WorkItem acceptance after an uncertain receipt and a page reload', async () => {
  const token = `sha256:${'c'.repeat(64)}`;
  const reviewSnapshot: typeof snapshot = { ...snapshot, state: 'WAITING_REVIEW', reason: 'DELIVERY_REVIEW_REQUIRED',
    tracks: [{ ...snapshot.tracks[0], state: 'WAITING_REVIEW', review: { token, resultLinkIds: [] } }] };
  let accepted = false;
  const sendReview = vi.fn().mockRejectedValueOnce(new TypeError('Lost receipt')).mockImplementationOnce(async () => {
    accepted = true;
    return json({ workItemId: 'work-1', revision: 3, changed: true, linkId: null });
  });
  fixture({ onReview: sendReview, readSnapshot: () => accepted ? { ...reviewSnapshot, revision: 3, state: 'COMPLETED',
    eventCursor: 6, tracks: [{ ...reviewSnapshot.tracks[0], acceptedAt: instant, state: 'COMPLETED', review: null }] } : reviewSnapshot,
  readDetail: () => ({ ...detail, workItem: accepted ? { ...summary, revision: 3 } : summary }) });
  open('#/work-items/work-1');
  const first = render(<App />);
  await selectSection('成果');
  const candidate = await screen.findByRole('button', { name: '验收 · 水位资料核对' });
  await waitFor(() => expect(candidate).toBeEnabled());
  fireEvent.click(candidate);
  fireEvent.click(screen.getByRole('button', { name: '确认验收' }));
  await screen.findByRole('button', { name: '重试验收' });
  first.unmount();
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '重试验收' }));
  expect(await screen.findByText('已验收', { exact: true })).toBeVisible();
  expect(sendReview).toHaveBeenCalledTimes(2);
  expect(sendReview.mock.calls[0][0]).toMatchObject({ sessionId: 'session-1', expectedRevision: 2, reviewToken: token });
  expect(sendReview.mock.calls[1][0]).toEqual(sendReview.mock.calls[0][0]);
});

it('replays a lost linked-session receipt and opens the verified work background without importing old messages', async () => {
  const create = vi.fn()
    .mockRejectedValueOnce(new TypeError('Network disconnected'))
    .mockResolvedValueOnce(json({ workItemId: 'work-1', revision: 3, changed: true, linkId: 'new-session' }));
  const getContext = vi.fn(async (sessionId: string) => json({
    context: {
      sessionId, linkedAt: instant, workItem: summary, assets: [], objects: [],
      openTasks: [{ sessionId: 'session-1', sessionTitle: '水位资料核对', taskId: 'prior-task', revision: 2, updatedAt: instant }],
      approvedActions: [],
    },
  }));
  fixture({ onCreateSession: create, readContext: getContext });
  open('#/work-items/work-1');
  const page = render(<App />);
  await selectSection('对话');
  const createButton = await screen.findByRole('button', { name: '在此工作中新建会话' });
  await waitFor(() => expect(createButton).toBeEnabled());
  fireEvent.click(createButton);
  expect(await screen.findByRole('button', { name: '重试会话操作' })).toBeVisible();
  page.unmount();
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '重试会话操作' }));
  await waitFor(() => expect(window.location.hash).toBe('#/sessions/new-session?workItem=work-1'));
  expect(create.mock.calls[0][0]).toEqual(create.mock.calls[1][0]);
  const context = await screen.findByRole('region', { name: '工作背景' });
  expect(await within(context).findByText('枯水期监测')).toBeVisible();
  expect(within(context).getByText('核对浅层井资料')).toBeVisible();
  expect(within(context).getByText(/任务修订 2/)).toBeVisible();
  expect(screen.getByRole('link', { name: '返回业务工作' })).toHaveAttribute('href', '#/work-items/work-1');
  expect(getContext).toHaveBeenCalledWith('new-session');
});

it('offers a safe alternate path when a selected session already belongs to another work item', async () => {
  const link = vi.fn(async () => json({
    type: 'about:blank', title: 'Conflict', status: 409, detail: 'already linked',
    code: 'SESSION_ALREADY_LINKED', requestId: 'request', traceId: 'trace', errors: [],
  }, 409));
  fixture({ candidates: [sessionSummaryFixture({ sessionId: 'candidate', title: '已有勘察会话' })], onLinkSession: link });
  open('#/work-items/work-1');
  render(<App />);
  await selectSection('对话');
  const picker = await screen.findByRole('button', { name: '加入已有会话' });
  await waitFor(() => expect(picker).toBeEnabled());
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole('radio', { name: /已有勘察会话/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认关联' }));
  expect(await screen.findByText(/该会话已归属其他业务工作/)).toBeVisible();
  expect(link).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'candidate', expectedRevision: 2 }));
  expect(await screen.findByRole('button', { name: '在此工作中新建会话' })).toBeVisible();
  expect(window.location.hash).toBe('#/work-items/work-1/conversations');
});
