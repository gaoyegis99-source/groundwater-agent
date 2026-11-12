import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DatabaseWorkbench } from './DatabaseWorkbench';
import { dataDetail, dataRef } from './test-fixtures';

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });

it('shows persisted field statistics, bounded samples and the declared unit', async () => {
  const detail = dataDetail({
    descriptor: { rowCount: 7, profile: { schemaVersion: 'field-profile.v1', statisticsScope: 'all_rows', sampleScope: 'first_non_null_values', sampleLimitPerField: 5 },
      fields: [{ fieldId: 'f1', name: '水位', type: 'double', nullCount: 1, profile: {
        nonNullCount: 6, nonFiniteCount: 1, sampleValues: [1, 2, 3, 'inf', 4], valueRange: { minimum: 1, maximum: 20 },
      } }, { fieldId: 'f2', name: '批次号', type: 'int64', nullCount: 5, profile: {
        nonNullCount: 2, sampleValues: [2 ** 53, 2 ** 53], valueRange: { minimum: 2 ** 53, maximum: 2 ** 53 },
        exactIntegers: { sampleValues: ['9007199254740992', '9007199254740993'], minimum: '9007199254740992', maximum: '9007199254740993' },
      } }, { fieldId: 'f3', name: '旧记录号', type: 'int64', nullCount: 6, profile: {
        nonNullCount: 1, sampleValues: [2 ** 53],
      } }] },
    semantics: { fields: { f1: { unit: 'm' } } },
  });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/data/versions/version-1')) return json(detail);
    if (url.includes('/versions?')) return json({ items: [], nextCursor: null });
    if (url.includes('/data/datasets?')) return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  render(<DatabaseWorkbench project="project" initialId="version-1" compare onClose={vi.fn()} onAttach={vi.fn()} />);
  expect(await screen.findByText('全量统计 · 7 行')).toBeVisible();
  expect(screen.getByText(/最先出现的 5 个非空值/)).toBeVisible();
  const table = within(screen.getByRole('region', { name: '字段与质量' }));
  const field = within(table.getByRole('row', { name: /水位/ }));
  expect(field.getByText('m', { exact: true })).toBeVisible();
  expect(field.getByText('1 – 20', { exact: true })).toBeVisible();
  expect(field.getByText('非有限数 1')).toBeVisible();
  fireEvent.click(field.getByText('查看样例'));
  expect(field.getByText('inf')).toBeVisible();
  const exact = within(table.getByRole('row', { name: /批次号/ }));
  expect(exact.getByText('9007199254740992 – 9007199254740993')).toBeVisible();
  fireEvent.click(exact.getByText('查看样例'));
  expect(exact.getByText('9007199254740993')).toBeVisible();
  const legacy = within(table.getByRole('row', { name: /旧记录号/ }));
  fireEvent.click(legacy.getByText('查看样例'));
  expect(legacy.getByText('精度不可确认')).toBeVisible();
});

it('searches and paginates the database catalog and attaches the selected immutable object', async () => {
  const detail = dataDetail({
    title: '模型文件',
    descriptor: { policy: 'FILE_ONLY', rowCount: null, fields: [] },
    objects: [
      { objectId: 'weight-1', size: 2048, checksum: `sha256:${'a'.repeat(64)}`, role: 'raw', format: 'pytorch-state-dict', mediaType: 'application/vnd.pytorch' },
      { objectId: 'metadata-1', size: 128, checksum: `sha256:${'b'.repeat(64)}`, role: 'query', format: 'json', mediaType: 'application/json' },
    ],
  });
  const calls = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/datasets/dataset-1/versions')) return json({ items: [{ versionId: 'version-1', semanticId: 'semantics-1', createdAt: '2026-10-01T00:00:00Z' }], nextCursor: null });
    if (url.includes('/data/versions/version-1')) return json(detail);
    if (url.includes('cursor=dataset-1')) return json({ items: [{ datasetId: 'dataset-2', project: 'project', title: '第二页', tags: [], currentVersion: 'version-2', createdAt: '2026-10-02T00:00:00Z', formats: ['json'], semantics: {} }], nextCursor: null, matchBasis: 'catalog' });
    if (url.includes('/data/datasets?')) return json({ items: [{ datasetId: 'dataset-1', project: 'project', title: '模型文件', tags: [], currentVersion: 'version-1', createdAt: '2026-10-01T00:00:00Z', formats: ['pytorch-state-dict'], semantics: { description: '安全元数据预览' } }], nextCursor: 'dataset-1', matchBasis: 'catalog' });
    throw new Error(`unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', calls);
  const attach = vi.fn();
  render(<DatabaseWorkbench project="project" onClose={vi.fn()} onAttach={attach} />);
  fireEvent.change(screen.getByLabelText('搜索'), { target: { value: '模型' } });
  fireEvent.change(screen.getByLabelText('格式筛选'), { target: { value: 'pytorch-state-dict' } });
  await waitFor(() => expect(calls.mock.calls.some(([url]) => String(url).includes('query=%E6%A8%A1%E5%9E%8B') && String(url).includes('format=pytorch-state-dict'))).toBe(true));
  fireEvent.click(await screen.findByRole('button', { name: /模型文件/ }));
  // The production workbench deliberately lazy-loads viewers. Give the
  // dynamically imported metadata viewer a bounded window under the full
  // parallel suite instead of relying on the default 1s DOM timeout.
  await screen.findByText('为避免执行不受信任代码，系统不会反序列化模型权重。', undefined, { timeout: 5_000 });
  fireEvent.click(screen.getByRole('button', { name: '附加精确版本' }));
  expect(attach).toHaveBeenCalledWith(expect.objectContaining({ versionId: 'version-1', semanticId: 'semantics-1', objectId: 'weight-1', checksum: `sha256:${'a'.repeat(64)}` }));
  fireEvent.click(screen.getByRole('button', { name: '目录' }));
  fireEvent.click(screen.getByRole('button', { name: '加载更多' }));
  expect(await screen.findByRole('button', { name: /第二页/ })).toBeVisible();
  const nextPage = calls.mock.calls.map(([url]) => String(url)).find(url => url.includes('cursor=dataset-1'));
  expect(nextPage).toContain('query=%E6%A8%A1%E5%9E%8B');
  expect(nextPage).toContain('format=pytorch-state-dict');
});

it('uses the catalog cursor to load the next page', async () => {
  const calls = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/data/datasets')) {
      const second = url.searchParams.get('cursor') === 'page-1';
      return json({ items: [{ datasetId: second ? 'second' : 'first', project: 'project', title: second ? '后续数据' : '首份数据', tags: [], currentVersion: second ? 'version-2' : 'version-1', createdAt: '2026-10-01T00:00:00Z', formats: ['csv'], semantics: {} }], nextCursor: second ? null : 'page-1', matchBasis: 'catalog' });
    }
    throw new Error(`unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', calls);
  render(<DatabaseWorkbench project="project" onClose={vi.fn()} onAttach={vi.fn()} />);
  await screen.findByRole('button', { name: /首份数据/ });
  fireEvent.click(screen.getByRole('button', { name: '加载更多' }));
  expect(await screen.findByRole('button', { name: /后续数据/ })).toBeVisible();
});

it('closes with Escape and traps focus inside the database dialog', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ items: [], nextCursor: null, matchBasis: 'catalog' })));
  const close = vi.fn();
  render(<DatabaseWorkbench project="project" onClose={close} onAttach={vi.fn()} />);
  const dialog = screen.getByRole('dialog', { name: '数据库' });
  await waitFor(() => expect(screen.getByLabelText('搜索')).toHaveFocus());
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(close).toHaveBeenCalledOnce();
});

it('compares immutable identity, objects, fields and complete semantics without reading content', async () => {
  const left = dataDetail({
    inputVersions: ['input-shared', 'input-old'],
    descriptor: { policy: 'FILE_ONLY', rowCount: 2, fields: [{ fieldId: 'pm', name: 'pm25', type: 'number', nullCount: 0 }] },
    semantics: { description: '旧说明', fields: { pm: { meaning: '颗粒物', unit: 'µg/m³' } }, limitations: ['旧限制'] },
    objects: [{ objectId: 'raw-old', size: 42, checksum: `sha256:${'a'.repeat(64)}`, role: 'raw', format: 'csv', mediaType: 'text/csv' }],
  });
  const right = dataDetail({
    versionId: 'version-2', semanticId: 'semantics-2',
    inputVersions: ['input-shared', 'input-new'],
    descriptor: { policy: 'FILE_ONLY', rowCount: 3, fields: [{ fieldId: 'pm', name: 'pm25', type: 'number', nullCount: 1 }, { fieldId: 'site', name: 'site', type: 'string', nullCount: 0 }] },
    semantics: { description: '新说明', fields: { pm: { meaning: '细颗粒物', unit: 'µg/m³' }, site: { meaning: '站点' } }, limitations: ['新限制'] },
    objects: [{ objectId: 'raw-new', size: 84, checksum: `sha256:${'b'.repeat(64)}`, role: 'raw', format: 'parquet', mediaType: 'application/vnd.apache.parquet' }],
  });
  const calls = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/versions/version-1')) return json(left);
    if (url.endsWith('/data/versions/version-2/semantics/semantics-2')) return json({
      versionId: 'version-2', semanticId: 'semantics-2', body: right.semantics, actor: {}, createdAt: '2026-10-02T00:00:00Z',
    });
    if (url.endsWith('/data/versions/version-2')) return json({
      ...right, semanticId: 'semantics-2-later', semantics: { description: '列表读取后才保存的语义' },
    });
    if (url.includes('/data/datasets/dataset-1/versions')) return json({ items: [
      { versionId: 'version-1', semanticId: 'semantics-1', createdAt: '2026-10-01T00:00:00Z' },
      { versionId: 'version-2', semanticId: 'semantics-2', createdAt: '2026-10-02T00:00:00Z' },
    ], nextCursor: null });
    if (url.includes('/data/datasets?')) return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    throw new Error(`unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', calls);
  render(<DatabaseWorkbench project="project" initialId="version-1" compare onClose={vi.fn()} onAttach={vi.fn()} />);
  const comparison = await screen.findByLabelText('比较版本');
  await within(comparison).findByRole('option', { name: 'version-2' });
  fireEvent.change(comparison, { target: { value: 'version-2' } });

  expect(await screen.findByText(/raw-old.*raw-new/)).toBeVisible();
  expect(screen.getByText(/csv\/text\/csv.*parquet\/application\/vnd\.apache\.parquet/)).toBeVisible();
  expect(screen.getByText(/a{64}.*b{64}/)).toBeVisible();
  expect(screen.getByText('site')).toBeVisible();
  expect(screen.getByText(/limitations/)).toBeVisible();
  expect(screen.getByText(/旧限制.*新限制/)).toBeVisible();
  expect(screen.getByText('semantics-1 → semantics-2')).toBeVisible();
  expect(screen.queryByText(/列表读取后才保存的语义/)).not.toBeInTheDocument();
  expect(screen.getByText(/input-old.*input-new/)).toBeVisible();
  expect(calls.mock.calls.some(([url]) => String(url).includes('/content'))).toBe(false);
  expect(calls.mock.calls.some(([url]) => String(url).includes('/queries'))).toBe(false);
});

it('reopens a published result with its exact object and historical semantics', async () => {
  const result = dataRef({ datasetId: 'dataset-1', versionId: 'version-1', semanticId: 'semantics-frozen', objectId: 'object-frozen', sizeBytes: 42, format: 'json', mediaType: 'application/json', checksum: `sha256:${'b'.repeat(64)}` });
  const detail = dataDetail({
    semanticId: 'semantics-current', semantics: { description: '当前语义' },
    objects: [
      { objectId: 'raw-current', size: 20, checksum: `sha256:${'a'.repeat(64)}`, role: 'raw', format: 'json', mediaType: 'application/json' },
      { objectId: result.objectId, size: result.sizeBytes, checksum: result.checksum, role: 'query', format: result.format, mediaType: result.mediaType },
    ],
  });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/data/versions/version-1/semantics/semantics-frozen')) return json({ versionId: 'version-1', semanticId: 'semantics-frozen', body: { description: '冻结语义' }, actor: {}, createdAt: '2026-10-01T00:00:00Z' });
    if (url.endsWith('/data/versions/version-1')) return json(detail);
    if (url.includes('/versions?')) return json({ items: [], nextCursor: null });
    if (url.includes('/data/datasets?')) return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    throw new Error(`unexpected endpoint ${url}`);
  }));
  const onAttach = vi.fn();
  render(<DatabaseWorkbench project="project" initialId={result.versionId} reference={result} compare onClose={vi.fn()} onAttach={onAttach} />);
  expect(await screen.findByText('冻结语义')).toBeVisible();
  expect(screen.queryByText('当前语义')).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: '下载对象' })).toHaveAttribute('href', '/api/v12/data/versions/version-1/objects/object-frozen/content');
  fireEvent.click(screen.getByRole('button', { name: '附加精确版本' }));
  expect(onAttach).toHaveBeenCalledWith(expect.objectContaining(result));
});

it('finds comparison versions beyond the first 100 history entries', async () => {
  const versions = Array.from({ length: 101 }, (_, index) => ({
    versionId: `version-${index + 1}`, semanticId: `semantics-${index + 1}`,
    createdAt: '2026-10-01T00:00:00Z',
  }));
  const calls = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/datasets/dataset-1/versions')) {
      const next = url.searchParams.get('cursor') === 'version-100';
      return json({ items: next ? versions.slice(100) : versions.slice(0, 100), nextCursor: next ? null : 'version-100' });
    }
    if (url.pathname.endsWith('/versions/version-1')) return json(dataDetail());
    if (url.pathname.endsWith('/versions/version-101')) return json(dataDetail(versions[100]));
    if (url.pathname.endsWith('/data/datasets')) return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    throw new Error(`unexpected endpoint ${url.pathname}`);
  });
  vi.stubGlobal('fetch', calls);
  render(<DatabaseWorkbench project="project" initialId="version-1" compare onClose={vi.fn()} onAttach={vi.fn()} />);
  const compare = await screen.findByLabelText('比较版本');
  await within(compare).findByRole('option', { name: 'version-100' });
  fireEvent.click(screen.getByRole('button', { name: '加载更多版本' }));
  await within(compare).findByRole('option', { name: 'version-101' });
  fireEvent.change(compare, { target: { value: 'version-101' } });
  expect(await screen.findByText('version-1 → version-101')).toBeVisible();
  expect(calls.mock.calls.some(([url]) => String(url).includes('cursor=version-100'))).toBe(true);
});
