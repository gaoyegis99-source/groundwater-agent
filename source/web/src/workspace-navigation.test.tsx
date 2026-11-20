import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import sourceProfile from '../../products/groundwater/ui/product-ui.json';
import App from './App';
import { decodeProductUi, setProductUi } from './product-ui';
import { capabilitiesForProfile, historyFixture, instant, sessionResponseFixture } from './test-fixtures';

const profile = decodeProductUi(sourceProfile);

class EventStream extends EventTarget {
  static CLOSED = 2;
  readyState = 1;
  close() { this.readyState = EventStream.CLOSED; }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function go(path: string) {
  act(() => {
    window.history.replaceState({}, '', `/${path}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

function context(workItemId: string, title: string) {
  return { context: {
    workItem: {
      workItemId, title, projectId: 'demo', ownerSubject: 'test-user', description: '',
      status: 'ACTIVE', revision: 1, createdAt: instant, updatedAt: instant, archivedAt: null,
    },
    sessionId: workItemId === 'north' ? 'session-1' : 'session-2', linkedAt: instant,
    assets: [], objects: [], openTasks: [], approvedActions: [],
  } };
}

function mockWorkspace(services = ['data', 'knowledge-next'], readContext: (id: string) => Response | Promise<Response> = id =>
  json(id === 'session-1' ? context('north', '北区监测工作') : { context: null })) {
  const requests = vi.fn(async (url: string) => {
    const { pathname } = new URL(url, window.location.origin);
    if (pathname === '/api/v12/capabilities') return json(capabilitiesForProfile(profile, {
      currentUser: { subject: 'test-user', displayName: '测试用户', roles: ['researcher'] }, services,
    }));
    if (pathname === '/api/v12/sessions') return json({ items: [], nextCursor: null });
    if (pathname === '/api/v12/data/datasets') return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    if (pathname.startsWith('/api/v12/knowledge/')) return json({ items: [], nextCursor: null });
    if (pathname.startsWith('/api/v12/sessions/')) {
      const [, , , , id, path] = pathname.split('/');
      if (path === 'work-item') return readContext(id);
      if (path === 'messages') return json(historyFixture({ sessionId: id, turns: [] }));
      if (path === 'results') return json({ sessionId: id, items: [], nextCursor: null });
    }
    throw new Error(`Unhandled request ${url}`);
  });
  vi.stubGlobal('fetch', requests);
  return requests;
}

beforeEach(() => {
  setProductUi(profile);
  window.history.replaceState({}, '', '/#/');
  sessionStorage.clear();
  vi.stubGlobal('EventSource', EventStream);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('opens a direct global directory as a page with stable navigation and heading focus', async () => {
  mockWorkspace();
  go('#/data');
  render(<App />);

  const heading = await screen.findByRole('heading', { level: 1, name: '数据库' });
  await waitFor(() => expect(heading).toHaveFocus());
  const nav = screen.getByRole('navigation', { name: '工作台导航' });
  expect(await within(nav).findByRole('link', { name: '业务工作' })).toHaveAttribute('href', '#/work-items');
  expect(within(nav).getByRole('link', { name: '数据库' })).toHaveAttribute('aria-current', 'page');
  expect(within(nav).getByRole('link', { name: '知识与方法' })).toHaveAttribute('href', '#/knowledge');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  go('#/capabilities');
  await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: '能力目录' })).toHaveFocus());
  expect(within(nav).getByRole('link', { name: '能力目录' })).toHaveAttribute('aria-current', 'page');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('recovers only the server-confirmed work and isolates drafts when switching sessions', async () => {
  mockWorkspace();
  go('#/sessions/session-1?workItem=stale-hint');
  render(<App />);

  const work = await screen.findByRole('link', { name: '返回业务工作' });
  expect(work).toHaveAttribute('href', '#/work-items/north');
  expect(screen.getByRole('button', { name: '新建独立对话' })).toBeVisible();
  expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveAccessibleDescription(/北区监测工作/);
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), { target: { value: '北区未发送草稿' } });

  go('#/sessions/session-1/workbench/tasks');
  const tasks = await screen.findByRole('main', { name: '任务工作台' });
  expect(within(tasks).getByRole('link', { name: '返回业务工作' })).toHaveAttribute('href', '#/work-items/north');

  go('#/sessions/session-2?workItem=north');
  await waitFor(() => expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveAccessibleDescription(/普通咨询/));
  expect(screen.queryByRole('link', { name: '返回业务工作' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue('');

  go('#/sessions/session-1');
  await screen.findByRole('link', { name: '返回业务工作' });
  expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue('北区未发送草稿');
});

it('omits an unconfigured directory and makes no underlying service request', async () => {
  const requests = mockWorkspace([]);
  go('#/knowledge');
  render(<App />);

  await screen.findByText('当前项目未配置此目录。');
  expect(screen.getByRole('heading', { level: 1, name: '知识与方法' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '知识与方法' })).not.toBeInTheDocument();
  expect(requests.mock.calls.some(([url]) => url.includes('/knowledge/'))).toBe(false);
});

it('ignores an earlier work-context response and explains the active permission failure', async () => {
  let finishNorth!: (response: Response) => void;
  let markNorthRequested!: () => void;
  const north = new Promise<Response>(resolve => { finishNorth = resolve; });
  const northRequested = new Promise<void>(resolve => { markNorthRequested = resolve; });
  mockWorkspace(undefined, id => {
    if (id !== 'session-1') return json({ code: 'FORBIDDEN' }, 403);
    markNorthRequested();
    return north;
  });
  go('#/sessions/session-1');
  render(<App />);
  await northRequested;

  go('#/sessions/session-2/workbench/tasks?workItem=north');
  const tasks = await screen.findByRole('main', { name: '任务工作台' });
  await within(tasks).findByText('当前身份无法查看工作归属');
  await act(async () => { finishNorth(json(context('north', '北区监测工作'))); });

  expect(within(tasks).getByRole('button', { name: '重新读取工作归属' })).toBeEnabled();
  expect(within(tasks).queryByRole('link', { name: '返回业务工作' })).not.toBeInTheDocument();
  expect(within(tasks).queryByText(/北区监测工作/)).not.toBeInTheDocument();
});

it.each([
  ['new conversation', false], ['first message', false], ['first message after returning home', true],
] as const)('preserves current navigation when a pending home %s returns', async (action, returnHome) => {
  const read = mockWorkspace();
  let finish!: (response: Response) => void;
  let markRequested!: () => void;
  const creation = new Promise<Response>(resolve => { finish = resolve; });
  const requested = new Promise<void>(resolve => { markRequested = resolve; });
  const requests = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v12/sessions' && init?.method === 'POST') {
      markRequested();
      return creation;
    }
    return read(url);
  });
  vi.stubGlobal('fetch', requests);
  render(<App />);

  const composer = await screen.findByRole('textbox', { name: '消息输入框' });
  await screen.findByRole('link', { name: '数据库' });
  fireEvent.change(composer, { target: { value: '保留首页草稿' } });
  fireEvent.click(screen.getByRole('button', { name: action === 'new conversation' ? '新建对话' : '发送消息' }));
  await requested;
  go('#/data');
  if (returnHome) {
    go('#/');
    fireEvent.change(await screen.findByRole('textbox', { name: '消息输入框' }), { target: { value: '返回后更新的草稿' } });
  }
  const listReads = read.mock.calls.filter(([url]) => url === '/api/v12/sessions').length;
  await act(async () => { finish(json(sessionResponseFixture({ sessionId: 'new-session' }))); });
  await waitFor(() => expect(read.mock.calls.filter(([url]) => url === '/api/v12/sessions').length).toBeGreaterThan(listReads));
  expect(window.location.hash).toBe(returnHome ? '#/' : '#/data');
  expect(requests.mock.calls.some(([url, init]) => url.endsWith('/messages') && init?.method === 'POST')).toBe(false);

  go('#/');
  expect(await screen.findByRole('textbox', { name: '消息输入框' })).toHaveValue(returnHome ? '返回后更新的草稿' : '保留首页草稿');
  expect(screen.queryByRole('button', { name: '重试发送' })).not.toBeInTheDocument();
});

it('rechecks workspace authorization on same-session navigation', async () => {
  let permission = true;
  mockWorkspace(undefined, () => permission ? json(context('north', '北区监测工作')) : json({ code: 'FORBIDDEN' }, 403));
  go('#/sessions/session-1');
  render(<App />);
  await screen.findByRole('link', { name: '返回业务工作' });

  permission = false;
  go('#/sessions/session-1/workbench/tasks');
  const tasks = await screen.findByRole('main', { name: '任务工作台' });
  await within(tasks).findByText('当前身份无法查看工作归属');
  expect(within(tasks).queryByRole('link', { name: '返回业务工作' })).not.toBeInTheDocument();
});
