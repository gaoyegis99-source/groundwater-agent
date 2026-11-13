import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ConfirmationCard } from './ChatCards';
import { canConfirm, isBusy, mergeHistory, refreshHistory, withReferences } from './chat-state';
import { messageBody } from './message-routing';
import type { Confirmation } from './types';
import { dataRef, historyFixture } from './test-fixtures';

const confirmation: Confirmation = {
  confirmationId: 'approval-1', goalRevision: 2, status: 'PENDING', agentRunId: 'agent-2',
  reason: '已核实资料', decision: null, decisionState: null,
  actions: [{ toolCallId: 'action-1', tool: 'run_summary', modelId: 'summary', modelVersion: '1', workflowId: 'flow', workflowVersion: '1', arguments: { source: 'exact-resource', axis: 'flatten' } }],
};
const approvalHistory = historyFixture({ goalRevision: 2, turns: [{ goalRevision: 2, status: 'WAITING_CONFIRMATION', confirmations: [confirmation] }] });
describe('approval boundaries', () => {
  it('only approves the current undecided confirmation and passes its exact identity', () => {
    const decision = vi.fn();
    render(<ConfirmationCard confirmation={confirmation} history={approvalHistory} busy={false} onDecision={decision} onResource={vi.fn()} project="demo" />);
    fireEvent.click(screen.getByRole('button', { name: /^确认执行/ }));
    expect(decision).toHaveBeenCalledExactlyOnceWith('approval-1', 'confirm');
    expect(screen.getByText('exact-resource')).toBeVisible();
  });
  it.each([
    { status: 'CONFIRMED' }, { status: 'SUPERSEDED' }, { decision: 'CONFIRM', decisionState: 'REQUESTED' },
    { goalRevision: 1 }, { confirmationId: 'old-approval' },
  ])('does not reopen a historical or already requested decision: %o', change => {
    render(<ConfirmationCard confirmation={{ ...confirmation, ...change }} history={approvalHistory} busy={false} onDecision={vi.fn()} onResource={vi.fn()} project="demo" />);
    expect(screen.queryByRole('button', { name: /^确认执行/ })).not.toBeInTheDocument();
  });
  it('busy state blocks approval', () => {
    expect(canConfirm(confirmation, approvalHistory, true)).toBe(false);
  });
  it('keeps natural language approval as an AUTO turn', () => {
    expect(messageBody('可以，继续')).toEqual({ requestId: expect.any(String), requestedMode: 'AUTO', message: '可以，继续' });
  });
  it('keeps exact attachment identities in the visible user message', () => {
    expect(withReferences('分析这个文件', [{ ...dataRef({ datasetId: 'd1', versionId: 'v2', semanticId: 's2', checksum: '1'.repeat(64) }), name: '水位.csv' }])).toBe(`分析这个文件\n\n附加数据：\n- 水位.csv（数据集: d1；版本: v2；语义: s2；校验和: ${'1'.repeat(64)}）`);
    expect(withReferences('', [{ ...dataRef({ checksum: `sha256:${'A'.repeat(64)}` }), name: '带前缀.csv' }])).toContain(`校验和: ${'a'.repeat(64)}）`);
  });
});

describe('history and execution state', () => {
  const history = historyFixture({ sessionId: 's', goalRevision: 2, items: [{ messageId: 2, goalRevision: 2, role: 'user', content: '未回复问题' }],
    turns: [{ goalRevision: 2, agentRunId: 'a', status: 'QUEUED' }], nextCursor: 2 });
  it('blocks queued and execution turns, allows clarification at confirmation', () => {
    expect(isBusy(history)).toBe(true);
    expect(isBusy({ ...history, turns: [{ ...history.turns[0], status: 'WAITING_EXECUTION' }] })).toBe(true);
    expect(isBusy({ ...history, turns: [{ ...history.turns[0], status: 'WAITING_CONFIRMATION' }] })).toBe(false);
  });
  it('deduplicates replay and keeps incomplete/failed messages while updating turn states', () => {
    const updated = { ...history, turns: [{ ...history.turns[0], status: 'FAILED', errorCode: 'PROVIDER_FAILED' }] };
    const merged = mergeHistory(history, updated);
    expect(merged.items).toEqual(history.items);
    expect(merged.turns).toEqual(updated.turns);
    const older = { ...history, items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '之前的问题' }], turns: [], nextCursor: null };
    expect(mergeHistory(merged, older).items.map(item => item.messageId)).toEqual([1, 2]);
  });
  it('keeps a paging path across an offline history gap and preserves loaded pages when they overlap', () => {
    const previous = { ...history, nextCursor: null };
    const latest = { ...history, items: [{ messageId: 53, goalRevision: 27, role: 'user', content: '离线后新消息' }], nextCursor: 53 };
    expect(refreshHistory(previous, latest)).toEqual(latest);
    const overlapping = { ...latest, items: [...previous.items, ...latest.items], nextCursor: 2 };
    expect(refreshHistory(previous, overlapping).nextCursor).toBeNull();
    expect(refreshHistory(previous, overlapping).items).toHaveLength(2);
  });
  it('old-page merging keeps the current same-revision confirmation instead of an older running status', () => {
    const oldPage = { ...history, turns: [{ ...history.turns[0], status: 'RUNNING' }], nextCursor: null };
    const current = { ...history, turns: [{ ...history.turns[0], status: 'WAITING_CONFIRMATION', confirmations: [confirmation] }] };
    const merged = { ...mergeHistory(oldPage, current), nextCursor: oldPage.nextCursor };
    expect(merged.turns[0]).toEqual(current.turns[0]);
    expect(isBusy(merged)).toBe(false);
    expect(merged.nextCursor).toBeNull();
  });
});
