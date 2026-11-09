import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ArtifactViewer } from './ArtifactViewer';
import type { DataQueryResponse, ResourceDetail } from './types';
import { dataDetail } from './test-fixtures';

const queryMock = vi.fn();
const setOptionMock = vi.fn();
vi.mock('./api', async importOriginal => {
  const actual = await importOriginal<typeof import('./api')>();
  return { ...actual, runDataQuery: (...args: unknown[]) => queryMock(...args) };
});
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: () => ({ setOption: setOptionMock, resize: vi.fn(), dispose: vi.fn() }),
}));

beforeEach(() => {
  queryMock.mockReset();
  setOptionMock.mockReset();
});

const table: DataQueryResponse = {
  queryId: 'query-1', state: 'SUCCEEDED', error: null, resultAvailable: true,
  expiresAt: '2026-10-13T00:00:00Z',
  receipt: { queryId: 'query-1', coverage: { sourceRows: 1, matchedRows: 1, calculation: 'all_matching_rows' }, returnedRows: 1, resultChecksum: 'a'.repeat(64), truncated: false },
  result: { fields: [{ fieldId: 'f0', type: 'BIGINT' }, { fieldId: 'f1', type: 'DOUBLE' }, { fieldId: 'f2', type: 'VARCHAR' }], rows: [[1, 3, 'a']], nextOffset: null, truncated: false },
};
const resource: ResourceDetail = dataDetail({ title: '表格', descriptor: { policy: 'FILE_QUERYABLE', rowCount: 1, fields: [{ fieldId: 'f0', name: 'time', type: 'int64' }, { fieldId: 'f1', name: 'value', type: 'double' }, { fieldId: 'f2', name: 'group', type: 'string' }] }, objects: [{ objectId: 'object-1', size: 20, checksum: 'a'.repeat(64), role: 'raw', format: 'csv', mediaType: 'text/csv' }] });

it('shows immutable Data identity and opens exact lineage inputs', () => {
  const open = vi.fn();
  render(<ArtifactViewer resource={dataDetail({ inputVersions: ['input-version-1'], objects: [{ objectId: 'object-result', size: 2, checksum: 'b'.repeat(64), role: 'raw', format: 'bin', mediaType: 'application/octet-stream' }] })} project="groundwater-demo" onResource={open} />);
  expect(screen.getByText('数据集')).toBeVisible();
  expect(screen.getByText('dataset-1')).toBeVisible();
  expect(screen.getByText('数据版本')).toBeVisible();
  expect(screen.getByText('version-1')).toBeVisible();
  expect(screen.getByText('语义版本')).toBeVisible();
  expect(screen.getByText('semantics-1')).toBeVisible();
  expect(screen.getByText('血缘输入')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /input-version-1/ }));
  expect(open).toHaveBeenCalledExactlyOnceWith('input-version-1');
});

it('queries typed table controls and configures a grouped chart with up to four Y fields', async () => {
  queryMock.mockResolvedValue(table);
  render(<ArtifactViewer resource={resource} project="groundwater-demo" />);
  expect(await screen.findByText('3')).toBeVisible();

  fireEvent.click(screen.getByText('字段、筛选与排序'));
  fireEvent.click(screen.getByRole('button', { name: '添加筛选' }));
  fireEvent.change(screen.getByLabelText('筛选字段 1'), { target: { value: 'f1' } });
  fireEvent.change(screen.getByLabelText('筛选值 1'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: '添加排序' }));
  fireEvent.change(screen.getByLabelText('排序字段 1'), { target: { value: 'f1' } });
  fireEvent.change(screen.getByLabelText('排序方向 1'), { target: { value: 'DESC' } });
  await waitFor(() => expect(queryMock).toHaveBeenCalledWith(
    expect.objectContaining({
      filters: [{ field: 'f1', op: 'eq', value: 3 }],
      sort: [{ field: 'f1', direction: 'desc' }],
    }),
    0,
    100,
    expect.any(AbortSignal),
  ));

  fireEvent.click(screen.getByRole('button', { name: '图表' }));
  // ChartView is intentionally lazy-loaded; cold CI workers can need more
  // than Testing Library's 1s default while Vite resolves the chunk.
  const y = await screen.findByLabelText('纵轴字段', {}, { timeout: 5000 }) as HTMLSelectElement;
  for (const option of y.options) option.selected = ['f0', 'f1'].includes(option.value);
  fireEvent.change(y);
  fireEvent.change(screen.getByLabelText('分组字段'), { target: { value: 'f2' } });
  await waitFor(() => expect(queryMock).toHaveBeenCalledWith(
    expect.objectContaining({ fields: ['f0', 'f1', 'f2'] }),
    0,
    1000,
    expect.any(AbortSignal),
  ));
  await waitFor(() => expect(setOptionMock).toHaveBeenCalled());
  const option = setOptionMock.mock.calls.at(-1)?.[0];
  expect(option.series[0].data[0][0]).toBe(1);
});

it('resets table state when the selected resource changes and ignores the old response', async () => {
  let resolveFirst!: (value: DataQueryResponse) => void;
  const first = new Promise<DataQueryResponse>(resolve => { resolveFirst = resolve; });
  const second: DataQueryResponse = { ...table, queryId: 'query-2', result: { ...table.result!, rows: [[2, 7, 'b']] } };
  queryMock
    .mockImplementationOnce(() => first)
    .mockImplementationOnce(() => Promise.resolve(second));
  const { rerender } = render(<ArtifactViewer resource={resource} project="groundwater-demo" />);
  rerender(<ArtifactViewer resource={{ ...resource, versionId: 'version-2' }} project="groundwater-demo" />);
  expect(await screen.findByText('7')).toBeVisible();
  resolveFirst(table);
  await Promise.resolve();
  expect(screen.queryByText('3')).not.toBeInTheDocument();
  const signals = queryMock.mock.calls.map(call => call[3] as AbortSignal);
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
});

it('discloses truncated chart data', async () => {
  queryMock.mockImplementation((_: unknown, __: number, limit: number) => Promise.resolve(limit === 1000 ? {
    ...table, receipt: { ...table.receipt!, truncated: true },
  } : table));
  render(<ArtifactViewer resource={resource} project="groundwater-demo" />);
  await screen.findByText('3');
  fireEvent.click(screen.getByRole('button', { name: '图表' }));
  expect(await screen.findByText('图表仅显示查询返回的前 1,000 行和前 8 组，共读取 1 组。')).toBeVisible();
});

it('does not replace a new chart query with a late response from the previous grouping', async () => {
  let resolveOld!: (value: DataQueryResponse) => void;
  const old = new Promise<DataQueryResponse>(resolve => { resolveOld = resolve; });
  queryMock.mockResolvedValueOnce(table).mockReturnValueOnce(old).mockResolvedValueOnce({
    ...table, result: { ...table.result!, rows: [[200, 300, 'new']] },
  });
  render(<ArtifactViewer resource={resource} project="groundwater-demo" />);
  await screen.findByText('3');
  fireEvent.click(screen.getByRole('button', { name: '图表' }));
  fireEvent.change(await screen.findByLabelText('分组字段'), { target: { value: 'f2' } });
  await waitFor(() => expect(setOptionMock.mock.calls.at(-1)?.[0].series[0].data[0][0]).toBe(200));
  const renders = setOptionMock.mock.calls.length;
  await act(async () => { resolveOld(table); await old; });
  expect(setOptionMock).toHaveBeenCalledTimes(renders);
  expect(setOptionMock.mock.calls.at(-1)?.[0].series[0].data[0][0]).toBe(200);
});
