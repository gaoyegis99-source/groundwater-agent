import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DatabaseWorkbench } from './DatabaseWorkbench';
import { WorkspacePanel } from './WorkspacePanel';
import { dataDetail, historyFixture } from './test-fixtures';
import type { ResourceDetail } from './types';

vi.mock('./ArtifactViewer', () => ({ ArtifactViewer: ({ resource }: { resource: ResourceDetail }) => <p>预览：{resource.title}</p> }));
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const versionList = { items: [1, 2].map(n => ({ versionId: `version-${n}`, semanticId: `semantics-${n}`, createdAt: '2026-10-01T00:00:00Z' })), nextCursor: null };
const catalog = { items: [1, 2].map(n => ({ datasetId: `dataset-${n}`, project: 'project', title: `资料 ${n}`, tags: [], currentVersion: `version-${n}`, createdAt: '2026-10-01T00:00:00Z', formats: ['json'], semantics: {} })), nextCursor: null, matchBasis: 'catalog' };

it('removes the prior preview and attach action as soon as a different immutable version is selected', async () => {
  const next = deferred<Response>(); const attach = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/versions/version-1')) return json(dataDetail({ title: '原版本' }));
    if (url.includes('/data/versions/version-2')) return next.promise;
    if (url.includes('/versions?')) return json(versionList);
    return json(catalog);
  }));
  render(<DatabaseWorkbench project="project" initialId="version-1" onClose={vi.fn()} onAttach={attach} />);
  await screen.findByText('预览：原版本');
  await screen.findByRole('option', { name: 'version-2' });
  fireEvent.change(screen.getByLabelText('不可变版本'), { target: { value: 'version-2' } });
  expect(screen.queryByText('预览：原版本')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '附加精确版本' })).not.toBeInTheDocument();
  expect(screen.getByText('正在读取所选版本…')).toHaveAttribute('role', 'status');
  await act(async () => next.resolve(json(dataDetail({ versionId: 'version-2', semanticId: 'semantics-2', title: '新版本' }))));
  fireEvent.click(await screen.findByRole('button', { name: '附加精确版本' }));
  expect(attach).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ versionId: 'version-2', semanticId: 'semantics-2' }));
});

it('ignores a late version response and retries an explicit read failure without restoring old data', async () => {
  const old = deferred<Response>(); let fail = true;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/versions/version-1')) return old.promise;
    if (url.includes('/data/versions/version-2')) return fail ? json({ error: { code: 'READ_FAILED' } }, 503) : json(dataDetail({ versionId: 'version-2', datasetId: 'dataset-2', title: '新资料' }));
    if (url.includes('/versions?')) return json(versionList);
    return json(catalog);
  }));
  render(<DatabaseWorkbench project="project" onClose={vi.fn()} onAttach={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /资料 1/ }));
  fireEvent.click(screen.getByRole('button', { name: /资料 2/ }));
  await screen.findByText('所选版本读取失败');
  await act(async () => old.resolve(json(dataDetail({ title: '迟到的旧资料' }))));
  expect(screen.queryByText('预览：迟到的旧资料')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '附加精确版本' })).not.toBeInTheDocument();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: '重新读取所选版本' }));
  expect(await screen.findByText('预览：新资料')).toBeVisible();
});

it('preserves the exact version when version history fails and offers an independent retry', async () => {
  let fail = true;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/versions/')) return json(dataDetail());
    if (url.includes('/versions?')) return fail ? json({ error: { code: 'READ_FAILED' } }, 503) : json(versionList);
    return json(catalog);
  }));
  render(<DatabaseWorkbench project="project" initialId="version-1" onClose={vi.fn()} onAttach={vi.fn()} />);
  await screen.findByText('版本列表读取失败');
  expect(screen.getByLabelText('不可变版本')).toHaveValue('version-1');
  expect(screen.getByRole('button', { name: '附加精确版本' })).toBeEnabled();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: '重新读取版本列表' }));
  await screen.findByRole('option', { name: 'version-2' });
  expect(screen.queryByText('版本列表读取失败')).not.toBeInTheDocument();
});

it('does not append a stale catalog page after the filters change', async () => {
  const page = deferred<Response>();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('cursor=')) return page.promise;
    if (url.includes('query=')) return json({ ...catalog, items: [catalog.items[1]] });
    return json({ ...catalog, items: [catalog.items[0]], nextCursor: 'next' });
  }));
  render(<DatabaseWorkbench project="project" onClose={vi.fn()} onAttach={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
  fireEvent.change(screen.getByLabelText('搜索'), { target: { value: '资料 2' } });
  await screen.findByRole('button', { name: /资料 2/ });
  await act(async () => page.resolve(json({ ...catalog, items: [catalog.items[0]] })));
  expect(screen.queryByRole('button', { name: /资料 1/ })).not.toBeInTheDocument();
});

it('keeps database keyboard focus out of panes hidden by the responsive layout', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/versions/')) return json(dataDetail());
    if (url.includes('/versions?')) return json(versionList);
    return json(catalog);
  }));
  render(<DatabaseWorkbench project="project" initialId="version-1" onClose={vi.fn()} onAttach={vi.fn()} />);
  await screen.findByRole('button', { name: '附加精确版本' });
  screen.getByRole('region', { name: '数据目录' }).style.display = 'none';
  screen.getByRole('region', { name: '数据详情' }).style.display = 'none';
  const close = screen.getByRole('button', { name: '关闭数据库' });
  close.focus();
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
  expect(screen.getByRole('button', { name: '预览' })).toHaveFocus();
  const dialog = screen.getByRole('dialog', { name: '数据库' });
  dialog.focus();
  fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
  expect(screen.getByRole('button', { name: '预览' })).toHaveFocus();
});

it('makes the task tabs a single keyboard stop with arrow, Home and End navigation', () => {
  render(<WorkspacePanel history={historyFixture()} results={[]} activeId="keyboard-tabs" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  const task = screen.getByRole('tab', { name: '任务' });
  task.focus();
  expect(task).toHaveAttribute('tabindex', '0');
  expect(screen.getByRole('tab', { name: '数据' })).toHaveAttribute('tabindex', '-1');
  fireEvent.keyDown(task, { key: 'ArrowRight' });
  expect(screen.getByRole('tab', { name: '成果' })).toHaveFocus();
  expect(screen.getByRole('tabpanel', { name: '成果' })).toBeVisible();
  fireEvent.keyDown(screen.getByRole('tab', { name: '成果' }), { key: 'Home' });
  expect(screen.getByRole('tab', { name: '数据' })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole('tab', { name: '数据' }), { key: 'End' });
  expect(screen.getByRole('tab', { name: '成果' })).toHaveAttribute('aria-selected', 'true');
});

it('opens the task panel as a keyboard-dismissable overlay at tablet widths', () => {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query === '(max-width: 1050px)', addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  const close = vi.fn();
  render(<WorkspacePanel history={historyFixture()} results={[]} activeId="tablet" collapsed={false} onToggle={close} onJump={vi.fn()} />);
  const dialog = screen.getByRole('dialog', { name: '任务工作台' });
  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(dialog).toHaveClass('is-overlay');
  expect(screen.getByRole('button', { name: '折叠任务工作台' })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(close).toHaveBeenCalledOnce();
});

it('dismisses the compact workspace when following a conversation or database action', () => {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query === '(max-width: 1050px)', addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  const close = vi.fn(), jump = vi.fn(), openDatabase = vi.fn();
  render(<WorkspacePanel history={historyFixture()} results={[]} activeId="tablet-actions" collapsed={false} onToggle={close} onJump={jump} onOpenDatabase={openDatabase} />);
  fireEvent.click(screen.getByRole('button', { name: '查看本轮回答' }));
  expect(close).toHaveBeenCalledOnce();
  expect(jump).toHaveBeenCalledExactlyOnceWith(1);
  fireEvent.click(screen.getByRole('tab', { name: '数据' }));
  fireEvent.click(screen.getByRole('button', { name: '打开数据库选择数据' }));
  expect(close).toHaveBeenCalledTimes(2);
  expect(openDatabase).toHaveBeenCalledOnce();
});

it('clears fullscreen positioning when the workspace is collapsed', () => {
  const props = { history: historyFixture(), results: [], activeId: 'fullscreen-close', onToggle: vi.fn(), onJump: vi.fn() };
  const { rerender } = render(<WorkspacePanel {...props} collapsed={false} />);
  fireEvent.click(screen.getByRole('button', { name: '全屏任务工作台' }));
  expect(screen.getByRole('dialog', { name: '任务工作台' })).toHaveClass('is-fullscreen');
  rerender(<WorkspacePanel {...props} collapsed />);
  expect(screen.getByTestId('workspace-panel')).not.toHaveClass('is-fullscreen');
  rerender(<WorkspacePanel {...props} collapsed={false} />);
  expect(screen.queryByRole('dialog', { name: '任务工作台' })).not.toBeInTheDocument();
});

it('retries failed result loading and never keeps the previous result while switching', async () => {
  let fail = true;
  const second = deferred<Response>();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).includes('version-2') ? second.promise : fail ? json({ error: { code: 'READ_FAILED' } }, 503) : json(dataDetail({ title: '第一份成果' }))));
  const props = { history: historyFixture(), results: [], activeId: 'result-retry', collapsed: false, onToggle: vi.fn(), onJump: vi.fn() };
  const { rerender } = render(<WorkspacePanel {...props} selectedResourceId="version-1" />);
  await screen.findByRole('button', { name: '重新读取成果' });
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: '重新读取成果' }));
  await screen.findByText('预览：第一份成果');
  rerender(<WorkspacePanel {...props} selectedResourceId="version-2" />);
  expect(screen.queryByText('预览：第一份成果')).not.toBeInTheDocument();
  await act(async () => second.resolve(json(dataDetail({ versionId: 'version-2', title: '第二份成果' }))));
  await waitFor(() => expect(screen.getByText('预览：第二份成果')).toBeVisible());
});
