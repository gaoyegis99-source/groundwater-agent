import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SessionNavigation } from './SessionNavigation';
import { sessionSummaryFixture } from './test-fixtures';

function fixture() {
  return {
    activeId: 'session-1', recoveredTitle: null,
    sessions: [sessionSummaryFixture({ title: '监测井水位变化', archived: true })],
    query: '', showArchived: true, cursor: 17, loading: false, error: null, notice: '',
    updatingId: null, canCreate: true, canWrite: true,
    onCreate: vi.fn(), onQuery: vi.fn(), onArchiveFilter: vi.fn(), onRead: vi.fn(),
    onOpen: vi.fn(), onRename: vi.fn(), onArchive: vi.fn(),
  };
}

it('uses directory metadata for the selected row, archived label and recovery action', () => {
  const props = fixture();
  render(<SessionNavigation {...props} />);
  const directory = within(screen.getByRole('navigation', { name: '历史对话' }));
  const row = directory.getByRole('link', { name: '监测井水位变化' });
  expect(row).toHaveAttribute('aria-current', 'page');
  expect(row).toHaveAttribute('href', '#/sessions/session-1');
  expect(within(row).getByText('已归档')).toBeVisible();
  expect(directory.queryByText(/2026|任务|执行中/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '取消归档' }));
  expect(props.onArchive).toHaveBeenCalledWith('session-1', false);
  fireEvent.click(directory.getByRole('button', { name: '更早的对话' }));
  expect(props.onRead).toHaveBeenCalledWith(17);
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '水位' } });
  expect(props.onQuery).toHaveBeenCalledWith('水位');
});

it('shows read-only archive metadata and disables directory paging during a load', () => {
  const props = fixture();
  render(<SessionNavigation {...props} canWrite={false} canCreate={false} loading />);
  const directory = within(screen.getByRole('navigation', { name: '历史对话' }));
  expect(directory.getByText('已归档')).toBeVisible();
  expect(screen.queryByRole('button', { name: '取消归档' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重命名' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '新建对话' })).toBeDisabled();
  expect(directory.getByRole('button', { name: '更早的对话' })).toBeDisabled();
  expect(directory.getByRole('status')).toHaveTextContent('正在读取更多对话');
});
