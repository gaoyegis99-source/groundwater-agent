import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ControlProposalCard } from './ControlProposalCard';
import { dataRef } from './test-fixtures';

const message = { requestId: 'request', message: '停止分析', responseKind: 'CONTROL' as const,
  semanticIntent: 'UNSPECIFIED' as const, statusSnapshot: 'ANALYZING' as const,
  waitingReason: 'ANALYSIS_IN_PROGRESS' as const, results: [],
  controlIntent: 'STOP_ANALYSIS' as const, clarificationReason: null,
  status: 'CONTROL_PENDING' as const, createdAt: 'now' };
const proposal = { proposalId: 'proposal', sessionId: 'session', sourceRequestId: 'request',
  intent: 'STOP_ANALYSIS' as const, targetType: 'AGENT_RUN' as const, targetId: 'run',
  taskRevision: 2, status: 'PENDING' as const, decisionRequestId: null,
  createdAt: 'now', decidedAt: null };

describe('ControlProposalCard', () => {
  it('requires an explicit second action', async () => {
    const decide = vi.fn();
    render(<ControlProposalCard proposal={proposal} message={message} disabled={false} onDecision={decide} />);
    expect(screen.getByText('确认前不会改变当前运行或方案。', { exact: false })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '确认' }));
    expect(decide).toHaveBeenCalledWith('proposal', 'confirm');
  });

  it('withdraws actions for a stale target', () => {
    render(<ControlProposalCard proposal={{ ...proposal, status: 'STALE' }} message={message} disabled={false} onDecision={vi.fn()} />);
    expect(screen.getByText('请求已失效，当前状态已经变化。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认' })).not.toBeInTheDocument();
  });

  it('shows authoritative waiting reason and released result links', async () => {
    const open = vi.fn();
    render(<ControlProposalCard message={{ ...message,
      responseKind: 'DIALOGUE', semanticIntent: 'STATUS_QUERY', controlIntent: null,
      statusSnapshot: 'EXECUTING', waitingReason: 'EXECUTION_IN_PROGRESS',
      results: [dataRef({ versionId: 'resource-1', outputName: '水位结果' })],
    }} disabled={false} onDecision={vi.fn()} onResource={open} />);
    expect(screen.getByText('已批准的计算仍在执行。')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '水位结果' }));
    expect(open).toHaveBeenCalledWith('resource-1');
  });
});
