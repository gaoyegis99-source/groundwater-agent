import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DatabaseWorkbench } from './DatabaseWorkbench';
import { KnowledgeDrawer, ResourcePreview } from './Drawers';
import { dataDetail } from './test-fixtures';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const checksum = `sha256:${'a'.repeat(64)}`;
const evidenceBundle = (items: Record<string, unknown>[]) => ({
  schemaVersion: 'evidence-bundle.v1', status: 'OK', receiptId: 'receipt-1',
  knowledgeRevision: 1, indexGeneration: 'generation-1', observedAt: '2026-10-01T00:00:00Z',
  items, coverage: {}, budget: {}, timingsMs: {},
});

it('reads an exact Knowledge Next evidence identity and exposes its binding status', async () => {
  const contextChecksum = `sha256:${'b'.repeat(64)}`;
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(evidenceBundle([
    {
      status: 'OK', versionId: 'knowledge-version-1', unitId: 'unit-context', checksum: contextChecksum,
      documentTitle: '地下水监测技术手册', content: '所有水位值均应注明统一的高程基准。',
      locator: { section: '2.1' }, scope: 'DATA_BOUND', bindingMatch: 'EXACT',
    },
    {
      status: 'OK', versionId: 'knowledge-version-1', unitId: 'unit-3', checksum,
      documentTitle: '地下水监测技术手册', content: '以观测井口作为测量基准。',
      locator: { section: '3.2', page: 18 }, scope: 'DATA_BOUND', bindingMatch: 'EXACT',
      bindingsVerifiedAt: 'not-a-date',
      bindings: [{ serviceBinding: 'data-primary', projectId: 'project', datasetId: 'dataset-1', versionId: 'data-version-7', semanticId: 'semantic-3', fieldId: 'f2' }],
      sources: [{
        sourceId: 'manual', kind: 'OFFICIAL_GUIDANCE', title: '监测手册', publisher: '监测部门',
        version: '2025', publishedAt: '2025-01-10', accessedAt: '2026-09-30',
        reviewAfter: '2027-10-01', lifecycleStatus: 'ACTIVE', url: 'https://example.org/manual',
      }],
    },
  ])));
  render(<KnowledgeDrawer project="project" reference={{ versionId: 'knowledge-version-1', unitId: 'unit-3', checksum }} onClose={vi.fn()} />);
  expect(await screen.findByText('以观测井口作为测量基准。')).toBeVisible();
  expect(screen.getAllByText('地下水监测技术手册')[0]).toBeVisible();
  expect(screen.getByText('引用片段原文')).toBeVisible();
  expect(screen.getByText('必需上下文')).toBeVisible();
  expect(screen.getByText('所有水位值均应注明统一的高程基准。')).toBeVisible();
  expect(screen.getByText(/章节 3.2/)).toBeVisible();
  expect(screen.getByText(/页码 18/)).toBeVisible();
  expect(screen.getAllByText('数据绑定知识 · 精确匹配')).toHaveLength(2);
  for (const value of ['project', 'dataset-1', 'data-version-7', 'semantic-3', 'f2']) {
    expect(screen.getByText(value)).toBeVisible();
  }
  expect(screen.getByRole('link', { name: '监测手册' })).toHaveAttribute('href', 'https://example.org/manual');
  expect(screen.getByText(/监测部门/)).toBeVisible();
  expect(screen.getByText(/版本 2025/)).toBeVisible();
  expect(screen.getByText(/发布 2025-01-10/)).toBeVisible();
  expect(screen.getByText(/访问 2026-09-30/)).toBeVisible();
  expect(screen.queryByText(/绑定核对时间|Invalid Date/)).not.toBeInTheDocument();
  expect(screen.getAllByText('unit-3')[0]).not.toBeVisible();
  expect(screen.getByText(checksum)).not.toBeVisible();
  fireEvent.click(screen.getAllByText('技术身份')[0]);
  expect(screen.getAllByText('unit-3')[0]).toBeVisible();
  expect(screen.getByText(checksum)).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith('/api/v12/knowledge/evidence/read', expect.objectContaining({ method: 'POST' }));
  const request = fetchMock.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(request.body))).toEqual({
    refs: [{ versionId: 'knowledge-version-1', unitId: 'unit-3', checksum }],
  });
});

it('does not present non-OK evidence content as a valid basis', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(evidenceBundle([{
    status: 'STALE', versionId: 'knowledge-version-1', unitId: 'unit-3', checksum,
    documentTitle: '已更新的监测手册', content: '这段旧内容不得继续显示。',
    sources: [{
      sourceId: 'old', kind: 'OFFICIAL_GUIDANCE', title: '旧来源', publisher: '旧发布方',
      reviewAfter: '2025-01-01', lifecycleStatus: 'WITHDRAWN', url: 'https://example.org/old',
    }],
  }])));

  const { container } = render(<KnowledgeDrawer project="project" reference={{ versionId: 'knowledge-version-1', unitId: 'unit-3', checksum }} onClose={vi.fn()} />);

  expect(await screen.findByText('该引用已失效，不能作为当前回答依据。')).toBeVisible();
  expect(screen.getByText('已失效')).toBeVisible();
  expect(screen.queryByText('这段旧内容不得继续显示。')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '旧来源' })).not.toBeInTheDocument();
  expect(container.querySelector('.status-badge.status-active')).toBeNull();
});

it('lists Knowledge Next documents and looks up the selected identity', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input);
    if (url.endsWith('/knowledge/documents?limit=100')) return json({ items: [{ documentId: 'method', currentVersion: 'kv-1', title: '监测方法', createdAt: '2026-10-01T00:00:00Z' }], nextCursor: null });
    return json(evidenceBundle([{ status: 'OK', versionId: 'kv-1', unitId: 'u-1', checksum, content: '完整方法。' }]));
  });
  render(<KnowledgeDrawer project="project" onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /监测方法/ }));
  expect(await screen.findByText('完整方法。')).toBeVisible();
});

it('does not substitute evidence with a different checksum for the requested citation', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(json(evidenceBundle([{ status: 'OK', versionId: 'kv-1', unitId: 'u-1', checksum: `sha256:${'b'.repeat(64)}`, content: '内容不同的片段。' }])))
    .mockResolvedValueOnce(json(evidenceBundle([{ status: 'OK', versionId: 'kv-1', unitId: 'u-1', checksum, content: '所引用的精确片段。' }])));
  render(<KnowledgeDrawer project="project" reference={{ versionId: 'kv-1', unitId: 'u-1', checksum }} onClose={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('知识读取未完成');
  expect(screen.queryByText('内容不同的片段。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重新读取证据' }));
  expect(await screen.findByText('所引用的精确片段。')).toBeVisible();
});

it('returns from a cited unit to a searchable directory and preserves its position after reading', async () => {
  const requests: string[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input);
    requests.push(url);
    if (url.includes('/knowledge/documents')) return json({ items: [{ documentId: 'method', currentVersion: 'kv-1', title: '监测方法', createdAt: '2026-10-01T00:00:00Z' }], nextCursor: null });
    return json(evidenceBundle([{ status: 'OK', versionId: 'kv-1', unitId: 'u-1', checksum, content: '完整方法。' }]));
  });
  render(<KnowledgeDrawer project="project" reference={{ versionId: 'kv-1', unitId: 'u-1', checksum }} onClose={vi.fn()} />);
  expect(await screen.findByText('完整方法。')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '返回知识目录' }));
  const search = await screen.findByRole('searchbox', { name: '搜索知识目录' });
  fireEvent.change(search, { target: { value: '监测' } });
  fireEvent.submit(screen.getByRole('search', { name: '知识目录搜索' }));
  const selected = await screen.findByRole('button', { name: /监测方法/ });
  const directory = screen.getByRole('region', { name: '知识目录结果' });
  directory.scrollTop = 230;
  fireEvent.click(selected);
  expect(await screen.findByText('完整方法。')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '返回知识目录' }));
  expect(search).toHaveValue('监测');
  expect(directory.scrollTop).toBe(230);
  await waitFor(() => expect(selected).toHaveFocus());
  expect(requests.some(url => new URL(url, 'http://localhost').searchParams.get('query') === '监测')).toBe(true);
});

it('distinguishes directory loading and failure from an empty result and retries the same search', async () => {
  let finish: (value: Response) => void = () => {};
  vi.spyOn(globalThis, 'fetch')
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(json({ items: [], nextCursor: null }));
  render(<KnowledgeDrawer project="project" onClose={vi.fn()} />);
  expect(await screen.findByText('正在读取知识目录…')).toBeVisible();
  expect(screen.queryByText('知识目录还没有内容。')).not.toBeInTheDocument();
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledOnce());
  await act(async () => finish(json({ error: { code: 'KNOWLEDGE_UNAVAILABLE' } }, 503)));
  expect(await screen.findByRole('alert')).toHaveTextContent('知识目录读取未完成');
  expect(screen.queryByText('知识目录还没有内容。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试读取目录' }));
  expect(await screen.findByText('知识目录还没有内容。')).toBeVisible();
});

it('loads the next directory page and retries a failed document lookup without losing the directory', async () => {
  let lookups = 0;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input);
    if (url.includes('/knowledge/documents')) return json(url.includes('cursor=next')
      ? { items: [{ documentId: 'second', currentVersion: 'kv-2', title: '第二份方法', createdAt: '2026-10-01T00:00:00Z' }], nextCursor: null }
      : { items: [{ documentId: 'first', currentVersion: 'kv-1', title: '第一份方法', createdAt: '2026-10-01T00:00:00Z' }], nextCursor: 'next' });
    lookups += 1;
    return lookups === 1 ? json({ error: { code: 'KNOWLEDGE_UNAVAILABLE' } }, 503)
      : json(evidenceBundle([{ status: 'OK', versionId: 'kv-2', unitId: 'u-2', checksum, content: '第二份完整方法。' }]));
  });
  render(<KnowledgeDrawer project="project" onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: '加载更多知识' }));
  fireEvent.click(await screen.findByRole('button', { name: /第二份方法/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent('知识读取未完成');
  fireEvent.click(screen.getByRole('button', { name: '重新读取证据' }));
  expect(await screen.findByText('第二份完整方法。')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '返回知识目录' }));
  expect(screen.getByRole('button', { name: /第一份方法/ })).toBeVisible();
  expect(screen.getByRole('button', { name: /第二份方法/ })).toBeVisible();
});

it('aborts a previous citation read and ignores its late response when the requested identity changes', async () => {
  let finishOld: (value: Response) => void = () => {};
  let oldSignal: AbortSignal | null | undefined;
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementationOnce((_input, options) => {
    oldSignal = options?.signal;
    return new Promise(resolve => { finishOld = resolve; });
  }).mockResolvedValueOnce(json(evidenceBundle([{ status: 'OK', versionId: 'kv-2', unitId: 'u-2', checksum, content: '新的精确引用。' }])));
  const view = render(<KnowledgeDrawer project="project" reference={{ versionId: 'kv-1', unitId: 'u-1', checksum }} onClose={vi.fn()} />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
  view.rerender(<KnowledgeDrawer project="project" reference={{ versionId: 'kv-2', unitId: 'u-2', checksum }} onClose={vi.fn()} />);
  expect(await screen.findByText('新的精确引用。')).toBeVisible();
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => finishOld(json(evidenceBundle([{ status: 'OK', versionId: 'kv-1', unitId: 'u-1', checksum, content: '过时引用。' }]))));
  expect(screen.queryByText('过时引用。')).not.toBeInTheDocument();
  expect(screen.getByRole('article', { name: '引用片段原文：知识文档' })).toHaveFocus();
});

it('keeps keyboard focus inside the knowledge drawer and restores the opener on close', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [], nextCursor: null }));
  const opener = document.createElement('button');
  document.body.append(opener);
  opener.focus();
  const view = render(<KnowledgeDrawer project="project" onClose={vi.fn()} />);
  await screen.findByText('知识目录还没有内容。');
  const close = screen.getByRole('button', { name: '关闭知识库' });
  const last = await screen.findByRole('button', { name: '搜索' });
  last.focus();
  fireEvent.keyDown(last, { key: 'Tab' });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
  expect(last).toHaveFocus();
  view.unmount();
  expect(opener).toHaveFocus();
  opener.remove();
});

it('lists datasets and opens one immutable Data version', async () => {
  const detail = dataDetail({ title: '地下水观测', descriptor: { policy: 'FILE_QUERYABLE', rowCount: 973004, fields: [{ fieldId: 'well', name: 'well_id', type: 'string', nullCount: 0 }] } });
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input);
    if (url.includes('/data/datasets?limit=30')) return json({ items: [{ datasetId: 'dataset-1', project: 'project', title: '地下水观测', tags: [], currentVersion: 'version-1', createdAt: '2026-10-01T00:00:00Z', formats: ['json'], semantics: { description: '长表' } }], nextCursor: null, matchBasis: 'catalog' });
    if (url.includes('/data/datasets/dataset-1/versions')) return json({ items: [{ versionId: 'version-1', semanticId: 'semantics-1', createdAt: '2026-10-01T00:00:00Z' }], nextCursor: null });
    return json(detail);
  });
  const attach = vi.fn();
  render(<DatabaseWorkbench project="project" onClose={vi.fn()} onAttach={attach} />);
  fireEvent.click(await screen.findByRole('button', { name: /地下水观测/ }));
  const detailPane = screen.getByRole('region', { name: '数据详情' });
  expect(await within(detailPane).findByText('973004')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '附加精确版本' }));
  expect(attach.mock.calls[0][0]).toEqual(expect.objectContaining({ versionId: 'version-1', semanticId: 'semantics-1', objectId: 'object-1' }));
});

it('reports a failed Data version read and keeps the directory return action', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ error: { code: 'VERSION_NOT_FOUND' } }, 404));
  render(<DatabaseWorkbench project="project" initialId="missing" onClose={vi.fn()} onAttach={vi.fn()} />);
  await screen.findByRole('alert');
  expect(screen.getByRole('region', { name: '数据目录' })).toBeVisible();
});

it('renders native dataset, version, semantics and object identity', () => {
  render(<ResourcePreview resource={dataDetail()} project="project" />);
  for (const value of ['dataset-1', 'version-1', 'semantics-1', 'object-1']) expect(screen.getByText(value)).toBeVisible();
});

it('focuses and closes the v11 database workbench and knowledge drawer', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input);
    return json(url.includes('/data/datasets')
      ? { items: [], nextCursor: null, matchBasis: 'catalog' }
      : { items: [], nextCursor: null });
  });
  for (const kind of ['data', 'knowledge'] as const) {
    const close = vi.fn();
    const view = render(kind === 'data' ? <DatabaseWorkbench project="project" onClose={close} onAttach={vi.fn()} /> : <KnowledgeDrawer project="project" onClose={close} />);
    const dialog = screen.getByRole('dialog', { name: kind === 'data' ? '数据库' : '知识库' });
    await waitFor(() => expect(kind === 'data' ? screen.getByLabelText('搜索') : dialog).toHaveFocus());
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(close).toHaveBeenCalledOnce();
    view.unmount();
  }
});
