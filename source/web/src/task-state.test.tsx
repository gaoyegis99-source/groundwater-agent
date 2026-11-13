import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { readQueue, saveQueue } from './task-state';
import { MessageQueue } from './MessageQueue';
import { TaskCard } from './TaskCard';
import type { TaskContext } from './types';
import { scopedStorageKey } from './browser-scope';

beforeEach(() => localStorage.clear());
it('restores per-session messages with attachments and stable uncertain request identity', () => {
  saveQueue('alice', 'one', [{ id: 'local', text: '继续分析', attachments: [], createdAt: 'now', requestId: 'submitted' }]);
  expect(readQueue('alice', 'one')[0].requestId).toBe('submitted');
  expect(readQueue('alice', 'two')).toEqual([]);
  localStorage.setItem(scopedStorageKey('alice', 'message-queue', 'two'), '[{"text": "bad"}]');
  expect(readQueue('alice', 'two')).toEqual([]);
  expect(readQueue('alice', 'one')).toHaveLength(1);
  expect(readQueue('bob', 'one')).toEqual([]);
});
it('prevents editing or removing an uncertain submitted queue item', () => {
  render(<MessageQueue items={[{ id: 'one', text: '分析', attachments: [], createdAt: 'now', requestId: 'uncertain' }]} onChange={vi.fn()} />);
  expect(screen.getByRole('textbox')).toBeDisabled();
  expect(screen.getByRole('button', { name: '删除' })).toBeDisabled();
});
it('task review submits explicit natural-language evidence without approving computation', () => {
  const review = vi.fn();
  const task: TaskContext = { schemaVersion: 'task-context.v1', spec: { taskId: 't', revision: 1, objective: '整理资料', constraints: [], criteria: [{ id: 'r', text: '业务可用', kind: 'USER_REVIEW' }], unresolvedItems: [], suggestions: [], steps: [] }, progress: { lifecycle: 'ACTIVE', state: 'WAITING_REVIEW', criteria: [{ id: 'r', satisfied: false }], steps: [], results: [], metrics: { totalTokens: 12, durationMs: 1000 } } };
  render(<TaskCard task={task} disabled={false} onReview={review} onResource={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '确认此项满足' }));
  expect(review).toHaveBeenCalledWith('确认完成：业务可用');
  expect(screen.queryByText(/100%/)).toBeNull();
});

it('keeps model interpretation separate from the authoritative review text', () => {
  const review = vi.fn();
  const task: TaskContext = { schemaVersion: 'task-context.v1', spec: { taskId: 't', revision: 1, objective: '交付报告', constraints: [], criteria: [{ id: 'r', text: '交付报告', interpretation: '无需交付报告', kind: 'USER_REVIEW' }], unresolvedItems: [], suggestions: [], steps: [] }, progress: { lifecycle: 'ACTIVE', state: 'WAITING_REVIEW', criteria: [{ id: 'r', satisfied: false }], steps: [], results: [], metrics: { totalTokens: 12, durationMs: 1000 } } };
  render(<TaskCard task={task} disabled={false} onReview={review} onResource={vi.fn()} />);
  expect(screen.getByText('智能体解释：无需交付报告')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '确认此项满足' }));
  expect(review).toHaveBeenCalledWith('确认完成：交付报告');
});
it('explains that unsent messages resume after reopening the session', () => {
  render(<MessageQueue items={[{ id: 'one', text: '分析', attachments: [], createdAt: 'now' }]} onChange={vi.fn()} />);
  expect(screen.getByText('关闭页面后暂停，重新打开本会话后可继续发送。')).toBeVisible();
});
