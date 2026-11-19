import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { StatisticsSummary } from './StatisticsSummary';

const counts = { count: 4, nullCount: 1, nonFiniteCount: 2, q1: 2, median: 3, q3: 4 };
it('reads the published statistics, preserves ties, and expands all typed groups and missing values', () => {
  const groups = Array.from({ length: 6 }, (_, i) => ({
    key: { type: 'string', value: 'W' + (i + 1) },
    statistics: i === 5 ? { ...counts, count: 0, q1: null, median: null, q3: null } : counts,
  }));
  render(<StatisticsSummary data={{ selection: { column: 'level', groupField: 'well', unit: 'm' },
    series: [{ field: { name: 'level' }, statistics: { ...counts, count: 20 }, groups }] }} />);
  expect(screen.getByText(/共 20 个有效值/)).toBeVisible();
  expect(screen.getByText('已显示 5 / 6 组')).toBeVisible();
  expect(screen.getAllByRole('cell', { name: '3' })).toHaveLength(5);
  fireEvent.click(screen.getByRole('button', { name: '查看完整统计明细' }));
  expect(within(screen.getByRole('row', { name: /W6/ })).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['0', '—', '—', '—', '1', '2']);
});
it('shows overall statistics for ungrouped input and rejects malformed data', () => {
  const view = render(<StatisticsSummary data={{ selection: { column: 'level' },
    series: [{ field: { name: 'level' }, statistics: counts, groups: [] }] }} />);
  expect(screen.getByRole('table', { name: '总体统计' })).toBeVisible();
  view.rerender(<StatisticsSummary data={{ selection: {}, series: [{}] }} />);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getByText(/格式无法识别/)).toBeVisible();
});
