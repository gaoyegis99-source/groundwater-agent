import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  fireEvent.click(await screen.findByRole('button', { name: '选择数据资料' }));
  fireEvent.click(await screen.findByRole('button', { name: /水位观测资料/ }));
  await screen.findByRole('option', { name: /semantics-historical/ });
  fireEvent.change(screen.getByLabelText('语义修订'), { target: { value: pinned.semanticId } });
  expect(await screen.findByText('1985 国家高程基准')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '选择此资料版本' }));
  fireEvent.click(screen.getByRole('button', { name: '确认关联资料' }));
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
  expect(screen.getByRole('link', { name: '水位资料核对' })).toHaveAttribute('href', '#/sessions/session-1?workItem=work-1');
  expect(screen.getByRole('link', { name: '季节趋势讨论' })).toBeVisible();
  const materials = screen.getByRole('region', { name: '资料与业务对象' });
  expect(within(materials).getAllByText(/暂不可用/)).toHaveLength(2);
  expect(materials).not.toHaveTextContent('hidden-object-id');
  expect(materials).not.toHaveTextContent('hidden-asset-id');
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
  open('#/work-items/work-1');
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
  fireEvent.click(await screen.findByRole('button', { name: '验收 · 水位资料核对' }));
  expect(screen.getByRole('button', { name: '确认验收' })).toBeEnabled();
  reviewed = reviewing(laterToken, 6);
  act(() => EventStream.connections[0].dispatchEvent(new Event('work_item.refresh')));
  expect(await screen.findByText('交付已变化，请重新核对并发起验收。')).toBeVisible();
  expect(screen.queryByRole('button', { name: '确认验收' })).not.toBeInTheDocument();
  expect(review).not.toHaveBeenCalled();
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
  const picker = await screen.findByRole('button', { name: '加入已有会话' });
  await waitFor(() => expect(picker).toBeEnabled());
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole('radio', { name: /已有勘察会话/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认关联' }));
  expect(await screen.findByText(/该会话已归属其他业务工作/)).toBeVisible();
  expect(link).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'candidate', expectedRevision: 2 }));
  expect(await screen.findByRole('button', { name: '在此工作中新建会话' })).toBeVisible();
  expect(window.location.hash).toBe('#/work-items/work-1');
});
