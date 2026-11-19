import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { dataDetail, dataRef } from '../test-fixtures';
import CalibrationChart from './CalibrationChart';

const { read, option } = vi.hoisted(() => ({ read: vi.fn(), option: vi.fn() }));
vi.mock('../api', async importOriginal => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, readDataVersion: read };
});
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: () => ({ setOption: option, resize: vi.fn(), dispose: vi.fn() }),
}));

it('uses the published calibration units and keeps zero and missing readings available in the data table', async () => {
  const source = dataRef({ versionId: 'rate-result', semanticId: 'rate-sem', objectId: 'rate-residuals', format: 'json', mediaType: 'application/json' });
  read.mockResolvedValue(dataDetail({ semantics: { fields: {
    'rows.time': { unit: 'day' }, 'rows.observed': { unit: 'cm' }, 'rows.simulated': { unit: 'cm' },
  } } }));
  render(<CalibrationChart source={source} rmse={0} rows={[
    { time: 10, observed: 4, simulated: 4, residual: 0 },
    { time: 20, observed: null, simulated: 3, residual: null },
  ]} />);
  const fit = await screen.findByRole('img', { name: '观测水头与模拟水头' });
  expect(read).toHaveBeenCalledExactlyOnceWith(source.versionId, expect.any(AbortSignal), source);
  expect(fit).toHaveAccessibleDescription(/观测水头（cm）.*模拟水头（cm）.*rate-result.*rate-residuals.*模型时间范围 10 至 20（day）/);
  await waitFor(() => expect(option.mock.calls.at(-1)?.[0].series[0].data).toEqual([[4, 4]]));
  fireEvent.change(screen.getByRole('combobox', { name: '查看图表' }), { target: { value: 'residual' } });
  expect(await screen.findByRole('img', { name: '模拟水头与残差' })).toHaveAccessibleDescription(/残差（单位未声明）/);
  await waitFor(() => expect(option.mock.calls.at(-1)?.[0].series[0].data).toEqual([[4, 0]]));
  fireEvent.click(screen.getByText('查看率定数据表（2 行）'));
  const table = within(await screen.findByRole('region', { name: '率定数据表' }));
  expect(table.getByRole('cell', { name: '0', exact: true })).toBeVisible();
  expect(table.getAllByRole('cell', { name: '缺测', exact: true })).toHaveLength(2);
});
