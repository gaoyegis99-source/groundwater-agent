import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmationCard } from './ChatCards';
import { canConfirm } from './chat-state';
import { dataRef, historyFixture } from './test-fixtures';
import type { Confirmation } from './types';

const recovery: Confirmation = {
  confirmationId: 'recovery-2', goalRevision: 2, status: 'PENDING', agentRunId: 'agent-2',
  reason: '', decision: null, decisionState: null, kind: 'RECOVERY', revision: 2,
  sourceWorkflowRunId: 'failed-workflow',
  actions: [{ toolCallId: 'action-1', tool: 'run_transfer', modelId: 'transfer', modelVersion: '1',
    workflowId: 'flow', workflowVersion: '1', arguments: { source: 'frozen-version', horizon: 12 } }],
};
const original: Confirmation = { ...recovery, confirmationId: 'original-1', kind: 'INITIAL', status: 'CONFIRMED', decision: 'CONFIRM', sourceWorkflowRunId: null };
const current = (status = 'FAILED') => historyFixture({ goalRevision: 2, turns: [{
  goalRevision: 2, agentRunId: 'agent-2', status, confirmations: [original, recovery],
}] });

describe('recovery confirmation after the original turn is terminal', () => {
  it('shows the frozen action and allows only an explicit decision for the current recovery', () => {
    const decide = vi.fn();
    render(<ConfirmationCard confirmation={recovery} history={current()} busy={false} onDecision={decide} onResource={vi.fn()} project="demo" />);
    expect(screen.getByText('失败恢复批次')).toBeVisible();
    expect(screen.getByRole('button', { name: /^确认执行/ })).toBeEnabled();
    expect(screen.getByText('frozen-version')).toBeVisible();
    expect(screen.getByText('12')).toBeVisible();
    expect(decide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^确认执行/ }));
    expect(decide).toHaveBeenCalledExactlyOnceWith('recovery-2', 'confirm');
  });

  it.each(['FAILED', 'CANCELLED', 'COMPLETE'])('accepts a current recovery after %s', status => {
    expect(canConfirm(recovery, current(status), false)).toBe(true);
  });

  it.each([
    { description: 'busy', candidate: recovery, history: current(), busy: true },
    { description: 'different agent', candidate: { ...recovery, agentRunId: 'agent-other' }, history: current(), busy: false },
    { description: 'no source', candidate: { ...recovery, sourceWorkflowRunId: null }, history: current(), busy: false },
    { description: 'older goal', candidate: { ...recovery, goalRevision: 1 }, history: current(), busy: false },
    { description: 'older confirmation', candidate: { ...recovery, confirmationId: 'old' }, history: current(), busy: false },
    { description: 'already decided', candidate: { ...recovery, decision: 'CONFIRM', decisionState: 'REQUESTED' }, history: current(), busy: false },
    { description: 'already confirmed', candidate: { ...recovery, status: 'CONFIRMED' }, history: current(), busy: false },
    { description: 'non-recovery', candidate: { ...recovery, kind: 'INITIAL' as const }, history: current(), busy: false },
    { description: 'in-flight turn', candidate: recovery, history: current('RUNNING'), busy: false },
    { description: 'queued turn', candidate: recovery, history: current('QUEUED'), busy: false },
    { description: 'execution in flight', candidate: recovery, history: current('WAITING_EXECUTION'), busy: false },
    { description: 'superseded turn', candidate: recovery, history: current('SUPERSEDED'), busy: false },
    { description: 'newer goal', candidate: recovery, history: historyFixture({ goalRevision: 3, turns: [
      { goalRevision: 2, agentRunId: 'agent-2', status: 'FAILED', confirmations: [recovery] },
      { goalRevision: 3, agentRunId: 'agent-3', status: 'FAILED', confirmations: [] },
    ] }), busy: false },
  ])('rejects $description', ({ candidate, history, busy }) => {
    expect(canConfirm(candidate, history, busy)).toBe(false);
  });
});

it('shows the exact frozen input and the prior revision comparison on the current batch', () => {
  const ref = dataRef({ versionId: 'checked-version' });
  const before: Confirmation = { ...original, status: 'SUPERSEDED' };
  const after: Confirmation = { ...recovery, kind: 'EDITED', replacesConfirmationId: before.confirmationId,
    actions: [{ ...recovery.actions[0], arguments: { source: 'checked-version' }, inputReferences: { source: ref } }],
  };
  const history = historyFixture({ goalRevision: 2, turns: [{ goalRevision: 2, status: 'WAITING_CONFIRMATION',
    agentRunId: 'agent-2', confirmations: [before, after] }] });
  const resource = vi.fn();
  render(<ConfirmationCard current confirmation={after} history={history} busy={false}
    onDecision={vi.fn()} onResource={resource} project="demo" />);
  const details = screen.getByText('核对本批次资料与参数').closest('details')!;
  details.open = true;
  fireEvent(details, new Event('toggle'));
  expect(details).toHaveTextContent('已移除');
  fireEvent.click(screen.getByRole('button', { name: '查看冻结资料 source' }));
  expect(resource).toHaveBeenCalledExactlyOnceWith(ref.versionId, ref);
});
