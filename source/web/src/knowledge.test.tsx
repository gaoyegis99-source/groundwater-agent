import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(evidenceBundle([{
    status: 'OK', versionId: 'knowledge-version-1', unitId: 'unit-3', checksum,
    content: '以观测井口作为测量基准。', scope: 'DATA_BOUND', bindingMatch: 'EXACT',
    sources: [{ sourceId: 'manual', kind: 'manual', title: '监测手册', publisher: '监测部门', reviewAfter: '2027-10-01', url: 'https://example.org/manual' }],
  }])));
  render(<KnowledgeDrawer project="project" reference={{ versionId: 'knowledge-version-1', unitId: 'unit-3', checksum }} onClose={vi.fn()} />);
  expect(await screen.findByText('以观测井口作为测量基准。')).toBeVisible();
  expect(screen.getByText('数据绑定知识 · 精确匹配')).toBeVisible();
  expect(screen.getAllByText('unit-3')).toHaveLength(2);
  expect(fetchMock).toHaveBeenCalledWith('/api/v12/knowledge/evidence/read', expect.objectContaining({ method: 'POST' }));
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
  expect(await screen.findByText('973004')).toBeVisible();
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
