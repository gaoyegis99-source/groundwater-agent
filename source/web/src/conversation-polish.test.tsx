import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { ApiError } from './api';
import { identityStorageScope, scopedStorageKey } from './browser-scope';
import { MessageComposer } from './MessageComposer';
import { productUi } from './product-ui';
import { SessionRenameDialog } from './SessionRenameDialog';
import { capabilitiesFixture, dataDetail, dataRef, historyFixture, sessionResponseFixture, sessionResultsFixture, sessionSummaryFixture, taskFixture } from './test-fixtures';
import { useProjectSessions } from './use-project-sessions';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(accept => { resolve = accept; }); return { promise, resolve }; }

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, '', '/#/');
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
});

it('offers an explicit template undo and retires it after further editing', async () => {
  render(<App />);
  await screen.findByTitle('项目：demo');
  const input = screen.getByRole('textbox', { name: '消息输入框' });
  const example = productUi().examples[0];
  const template = screen.getByRole('button', { name: `${example.title} ${example.prompt}` });
  expect(template).toHaveTextContent(example.title);
  fireEvent.change(input, { target: { value: '我自己的未发送问题' } });
  fireEvent.click(template);
  expect(input).toHaveValue(example.prompt);
  expect(input).toHaveFocus();
  expect(screen.getByText(`已填入“${example.title}”，可修改后发送。`)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '撤销填入' }));
  expect(input).toHaveValue('我自己的未发送问题');
  fireEvent.click(template);
  fireEvent.change(input, { target: { value: `${example.prompt} 新增条件` } });
  expect(screen.queryByRole('button', { name: '撤销填入' })).not.toBeInTheDocument();
  expect(input).toHaveValue(`${example.prompt} 新增条件`);
  expect(vi.mocked(fetch).mock.calls).toHaveLength(2);
});

it('keeps the newest search result when an older session request finishes late', async () => {
  const first = deferred<Response>();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.includes('query=old')) return first.promise;
    if (url.includes('query=new')) return json({ items: [sessionSummaryFixture({ title: '新的匹配' })], nextCursor: null });
    return json({ items: [], nextCursor: null });
  }));
  const log = vi.fn();
  const { result } = renderHook(() => useProjectSessions(log));
  await waitFor(() => expect(result.current.capabilities).not.toBeNull());
  act(() => result.current.setSessionQuery('old'));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('query=old'))).toBe(true));
  act(() => result.current.setSessionQuery('new'));
  await waitFor(() => expect(result.current.sessions[0]?.title).toBe('新的匹配'));
  await act(async () => first.resolve(json({ items: [sessionSummaryFixture({ title: '过期的匹配' })], nextCursor: 20 })));
  expect(result.current.sessions[0]?.title).toBe('新的匹配');
  expect(result.current.sessionCursor).toBeNull();
});

it('keeps rename errors in the dialog and blocks duplicate saves while pending', async () => {
  const pending = deferred<void>();
  const rename = vi.fn().mockRejectedValueOnce(new ApiError(409, 'SESSION_CONFLICT')).mockImplementationOnce(() => pending.promise);
  render(<SessionRenameDialog title="原名称" onRename={rename} />);
  fireEvent.click(screen.getByRole('button', { name: '重命名' }));
  fireEvent.change(screen.getByRole('textbox', { name: '对话名称' }), { target: { value: '修改后的名称' } });
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  const dialog = screen.getByRole('dialog', { name: '重命名对话' });
  await within(dialog).findByRole('alert');
  expect(within(dialog).getByRole('textbox')).toHaveValue('修改后的名称');
  fireEvent.click(within(dialog).getByRole('button', { name: '保存' }));
  expect(within(dialog).getByRole('button', { name: '正在保存…' })).toBeDisabled();
  fireEvent.submit(within(dialog).getByRole('textbox').closest('form')!);
  expect(rename).toHaveBeenCalledTimes(2);
  await act(async () => pending.resolve());
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('shows archived session state, current selection and a complete long title', async () => {
  const title = '很长的监测井分析任务名称，包含必须完整核对的区域、年份、数据来源及处理目标';
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [sessionSummaryFixture({ title, archived: true })], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  const current = await screen.findByRole('link', { name: title });
  expect(current).toHaveAttribute('title', title);
  expect(current).toHaveAttribute('aria-current', 'page');
  expect(within(current).getByText('已归档')).toBeVisible();
});

it('closes a rename dialog when navigation changes its owning session', async () => {
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [sessionSummaryFixture({ title: '第一份分析' }), sessionSummaryFixture({ sessionId: 'session-2', title: '第二份分析' })], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '重命名' }));
  expect(screen.getByRole('textbox', { name: '对话名称' })).toHaveValue('第一份分析');
  act(() => { window.history.replaceState({}, '', '/#/sessions/session-2'); window.dispatchEvent(new HashChangeEvent('hashchange')); });
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '重命名对话' })).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '重命名' }));
  expect(screen.getByRole('textbox', { name: '对话名称' })).toHaveValue('第二份分析');
});

it('does not navigate away from a newly selected session when an earlier archive finishes', async () => {
  const archive = deferred<Response>();
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PATCH') return archive.promise;
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [sessionSummaryFixture({ title: '第一份分析' }), sessionSummaryFixture({ sessionId: 'session-2', title: '第二份分析' })], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '归档' }));
  expect(screen.getByRole('button', { name: '正在保存…' })).toBeDisabled();
  act(() => { window.history.replaceState({}, '', '/#/sessions/session-2'); window.dispatchEvent(new HashChangeEvent('hashchange')); });
  await act(async () => archive.resolve(json({ sessionId: 'session-1', title: '第一份分析', archived: true })));
  await screen.findByText('对话已归档，可在“显示归档”中找回。');
  expect(window.location.hash).toBe('#/sessions/session-2');
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
});

it('summarizes a pending attachment while retaining its full immutable identity in details', async () => {
  const versionId = '01234567-89ab-cdef-0123-456789abcdef';
  const scope = identityStorageScope('groundwater', 'demo', 'test-user');
  sessionStorage.setItem(scopedStorageKey(scope, 'draft-attachments', ''), JSON.stringify([{ ...dataRef({ versionId }), familyId: 'dataset-1', name: '监测井水位记录.csv' }]));
  render(<App />);
  await screen.findByTitle('项目：demo');
  const pending = await screen.findByTestId('pending-attachments');
  expect(pending).toHaveTextContent('待发送');
  expect(within(pending).getByRole('button', { name: /^监测井水位记录.csv/ })).toHaveTextContent('01234567…cdef');
  expect(within(pending).getByText(versionId)).not.toBeVisible();
  fireEvent.click(within(pending).getByText('附件版本详情'));
  expect(within(pending).getByText(versionId)).toBeVisible();
  expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled();
});

it('uses the shared short version for sent structured attachments and exposes the complete version', async () => {
  const versionId = '01234567-89ab-cdef-0123-456789abcdef';
  const reference = dataRef({ versionId, outputName: 'summary' });
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture({ items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '分析附加资料', inputReferences: [reference] }] }));
    if (url.includes('/data/versions/')) return json(dataDetail({ versionId }));
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  const attachment = await screen.findByRole('button', { name: /查看附件 summary/ });
  expect(attachment).toHaveTextContent('01234567…cdef');
  expect(attachment).toHaveAttribute('title', `数据版本 ${versionId}`);
});

it('creates one continuation session for duplicate clicks and permits an explicit retry after failure', async () => {
  const firstCreation = deferred<Response>();
  const task = taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const output = dataRef({ versionId: 'published-result-version', outputName: 'summary' });
  let creations = 0;
  let messagesSent = 0;
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/sessions') && init?.method === 'POST') {
      creations += 1;
      return creations === 1 ? firstCreation.promise : json(sessionResponseFixture({ sessionId: 'continued-session' }));
    }
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture([{ goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'COMPLETE', workflowRunId: task.workflowRunId, remoteOutputs: [output] }]));
    if (url.endsWith('/messages')) {
      if (init?.method === 'POST') messagesSent += 1;
      return json(historyFixture({ sessionId: url.includes('/continued-session/') ? 'continued-session' : 'session-1', items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '上一轮分析' }], turns: [{ executionTasks: [task] }] }));
    }
    if (url.endsWith(`/data/versions/${output.versionId}/semantics/${output.semanticId}`)) return json({
      versionId: output.versionId, semanticId: output.semanticId, body: { description: '成果发布时语义' }, actor: {}, createdAt: '2026-10-09T01:00:00Z',
    });
    if (url.includes('/data/versions/')) return json(dataDetail({ versionId: output.versionId, semanticId: 'later-semantics', semantics: { description: '后来修改的语义' } }));
    if (url.includes('/data/datasets/')) return json({ items: [], nextCursor: null });
    if (url.includes('/data/datasets?')) return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  const continuation = await screen.findByRole('button', { name: '带成果打开新会话' });
  const input = screen.getByRole('textbox', { name: '消息输入框' });
  fireEvent.change(input, { target: { value: '保留当前会话草稿' } });
  act(() => { fireEvent.click(continuation); fireEvent.click(continuation); });
  await waitFor(() => expect(creations).toBe(1));
  expect(screen.getByRole('button', { name: '正在创建对话…' })).toBeDisabled();
  await act(async () => firstCreation.resolve(new Response(JSON.stringify({ type: 'urn:test:problem:unavailable', title: 'Unavailable', status: 503, code: 'SERVICE_UNAVAILABLE', traceId: 'trace', requestId: 'create-session-request' }), { status: 503, headers: { 'Content-Type': 'application/problem+json' } })));
  await screen.findByRole('alert');
  expect(window.location.hash).toBe('#/sessions/session-1');
  expect(input).toHaveValue('保留当前会话草稿');
  const retry = screen.getByRole('button', { name: '带成果打开新会话' });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  await waitFor(() => expect(window.location.hash).toBe('#/sessions/continued-session'));
  expect(creations).toBe(2);
  const scope = identityStorageScope('groundwater', 'demo', 'test-user');
  expect(JSON.parse(sessionStorage.getItem(scopedStorageKey(scope, 'draft-attachments', 'continued-session'))!)).toEqual([expect.objectContaining(output)]);
  expect(messagesSent).toBe(0);
  fireEvent.click(await screen.findByTitle(/查看资料内容与版本/));
  expect(await screen.findByText('成果发布时语义')).toBeVisible();
  expect(screen.queryByText('后来修改的语义')).not.toBeInTheDocument();
});

it('keeps enabled control messages visually sendable while ordinary work is busy', () => {
  const send = vi.fn();
  render(<MessageComposer inputRef={{ current: null }} value="查看状态" selection={false} busy canSend tools={null} onChange={vi.fn()} onSend={send} />);
  const button = screen.getByRole('button', { name: '发送消息' });
  expect(button).toHaveTextContent('发送');
  fireEvent.click(button);
  expect(send).toHaveBeenCalledOnce();
});

it('includes the visible waiting state in the disabled send control name', () => {
  render(<MessageComposer inputRef={{ current: null }} value="继续分析" selection={false} busy canSend={false} tools={null} onChange={vi.fn()} onSend={vi.fn()} />);
  expect(screen.getByRole('button')).toHaveAccessibleName(/等待/);
  expect(screen.getByRole('button')).toBeDisabled();
});

it.each(['pending', 'open'])('retires a %s upload when its owning session changes', async phase => {
  const uploadCapabilities = deferred<Response>();
  window.history.replaceState({}, '', '/#/sessions/session-1');
  const scope = identityStorageScope('groundwater', 'demo', 'test-user');
  sessionStorage.setItem(scopedStorageKey(scope, 'draft', 'session-2'), '第二个会话的草稿');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/data/capabilities')) return uploadCapabilities.promise;
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [sessionSummaryFixture(), sessionSummaryFixture({ sessionId: 'session-2' })], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
  const view = render(<App />);
  await screen.findByTitle('项目：demo');
  fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['well,head\nW01,5'], 'session-1.csv', { type: 'text/csv' })] } });
  await screen.findByText('正在读取上传要求…');
  const resolveCapabilities = () => uploadCapabilities.resolve(json({
    version: '1', productId: 'groundwater', project: 'demo', currentUser: capabilitiesFixture().currentUser,
    permissions: { catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true, userUploadCreate: true, managedPublicationWrite: false, semanticWrite: false, accessWrite: true, governanceRead: false, qualityWrite: false, mlopsWrite: false },
    identityMode: 'gateway', writeEnabled: true, formats: ['csv'], formatMediaTypes: { csv: 'text/csv' }, query: {}, preview: {}, limits: { uploadBytes: 1073741824 }, contracts: {}, openapi: '/openapi.json',
  }));
  if (phase === 'open') {
    await act(async () => resolveCapabilities());
    await screen.findByRole('dialog', { name: '添加资料' });
  }
  act(() => { window.history.replaceState({}, '', '/#/sessions/session-2'); window.dispatchEvent(new HashChangeEvent('hashchange')); });
  if (phase === 'pending') await act(async () => resolveCapabilities());
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '添加资料' })).not.toBeInTheDocument());
  expect(screen.queryByText('正在读取上传要求…')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '消息输入框' })).toHaveValue('第二个会话的草稿');
  expect(sessionStorage.getItem(scopedStorageKey(scope, 'draft-attachments', 'session-2'))).toBe('[]');
});

it('keeps control message sending disabled while another local mutation is pending', async () => {
  const creation = deferred<Response>();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return creation.promise;
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(historyFixture());
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<App />);
  await screen.findByTitle('项目：demo');
  fireEvent.change(screen.getByRole('textbox', { name: '消息输入框' }), { target: { value: '查看状态' } });
  fireEvent.click(screen.getByRole('button', { name: '新建对话' }));
  expect(screen.getByRole('button', { name: /发送消息/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /发送消息/ })).toHaveAttribute('title', '正在创建对话 · 草稿会保留');
  await act(async () => creation.resolve(json(sessionResponseFixture())));
});

it('deduplicates overlaps within and across pages and discards a page from the previous filter', async () => {
  const older = deferred<Response>();
  const first = sessionSummaryFixture({ sessionId: 'session-1', title: '首页会话' });
  const second = sessionSummaryFixture({ sessionId: 'session-2', title: '第二页会话' });
  const calls = vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.includes('cursor=8')) return json({ items: [first, second, second], nextCursor: 4 });
    if (url.includes('cursor=4')) return older.promise;
    if (url.includes('query=')) return json({ items: [], nextCursor: null });
    return json({ items: [first, first], nextCursor: 8 });
  });
  vi.stubGlobal('fetch', calls);
  render(<App />);
  await screen.findByRole('link', { name: '首页会话' });
  const directory = within(screen.getByTestId('session-list'));
  expect(directory.getAllByRole('link', { name: '首页会话' })).toHaveLength(1);
  fireEvent.click(await directory.findByRole('button', { name: '更早的对话' }));
  expect(await directory.findAllByRole('link', { name: '第二页会话' })).toHaveLength(1);
  expect(directory.getAllByRole('link')).toHaveLength(2);
  fireEvent.click(directory.getByRole('button', { name: '更早的对话' }));
  await waitFor(() => expect(calls.mock.calls.some(([url]) => url.includes('cursor=4'))).toBe(true));
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '不匹配的关键词' } });
  expect(directory.queryByRole('button', { name: '更早的对话' })).not.toBeInTheDocument();
  expect(await directory.findByText(/没有匹配/)).toBeVisible();
  await act(async () => older.resolve(json({ items: [second], nextCursor: 2 })));
  expect(directory.queryAllByRole('link')).toHaveLength(0);
  expect(directory.queryByRole('button', { name: '更早的对话' })).not.toBeInTheDocument();
});

it('drops directory and mutation responses from an earlier project even if the same Session ID is reused', async () => {
  const stalePage = deferred<Response>();
  const stalePatch = deferred<Response>();
  let projectId = 'demo';
  const item = () => sessionSummaryFixture({ projectId, title: projectId === 'demo' ? '旧项目' : '新项目' });
  const calls = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture({ projectId }));
    if (init?.method === 'PATCH') return stalePatch.promise;
    if (url.includes('query=')) return stalePage.promise;
    return json({ items: [item()], nextCursor: null });
  });
  vi.stubGlobal('fetch', calls);
  const log = vi.fn();
  const { result } = renderHook(() => useProjectSessions(log));
  await waitFor(() => expect(result.current.sessions[0]?.title).toBe('旧项目'));
  let mutation: Promise<boolean> | undefined;
  act(() => { mutation = result.current.updateSession('session-1', { archived: true }); });
  act(() => result.current.setSessionQuery('旧项目'));
  await waitFor(() => expect(calls.mock.calls.some(([url]) => url.includes('query='))).toBe(true));
  projectId = 'other-project';
  await act(async () => result.current.connectProject());
  expect(result.current.sessionQuery).toBe('');
  expect(result.current.sessions[0]?.title).toBe('新项目');
  await act(async () => {
    stalePage.resolve(json({ items: [sessionSummaryFixture({ title: '旧查询' })], nextCursor: 2 }));
    stalePatch.resolve(json({ sessionId: 'session-1', title: '旧修改', archived: true }));
    expect(await mutation).toBe(false);
  });
  expect(result.current.sessions[0]?.title).toBe('新项目');
  expect(result.current.sessionCursor).toBeNull();
  expect(result.current.updatingSessionId).toBeNull();
});

it('keeps the selected Session and its snapshot when earlier history and creation finish late', async () => {
  const oldMessages = deferred<Response>();
  const create = deferred<Response>();
  const calls = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions') && init?.method === 'POST') return create.promise;
    if (url.endsWith('/sessions')) return json({ items: [
      sessionSummaryFixture({ title: '旧会话' }),
      sessionSummaryFixture({ sessionId: 'session-2', title: '当前会话' }),
    ], nextCursor: null });
    if (url.endsWith('/session-1/messages')) return oldMessages.promise;
    if (url.endsWith('/session-2/messages')) return json(historyFixture({
      sessionId: 'session-2', items: [{ messageId: 2, goalRevision: 1, role: 'user', content: '新会话用户内容' }],
    }));
    if (url.includes('/results')) return json(sessionResultsFixture([], { sessionId: 'session-2' }));
    throw new Error(`unexpected endpoint ${url}`);
  });
  window.history.replaceState({}, '', '/#/sessions/session-1');
  vi.stubGlobal('fetch', calls);
  render(<App />);
  const current = await screen.findByRole('link', { name: '当前会话' });
  await waitFor(() => expect(calls.mock.calls.some(([url]) => url.endsWith('/session-1/messages'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: '新建对话' }));
  await waitFor(() => expect(calls.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  act(() => {
    window.history.replaceState({}, '', current.getAttribute('href')!);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(await screen.findByText('新会话用户内容')).toBeVisible();
  await act(async () => {
    oldMessages.resolve(json(historyFixture({
      items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '迟到的旧会话内容' }],
    })));
    create.resolve(json(sessionResponseFixture({ sessionId: 'new-session' })));
  });
  expect(screen.queryByText('迟到的旧会话内容')).not.toBeInTheDocument();
  expect(current).toHaveAttribute('aria-current', 'page');
  expect(window.location.hash).toBe('#/sessions/session-2');
});
