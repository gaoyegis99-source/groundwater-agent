import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { WorkflowNodeDetails } from './WorkflowNodeDetails';
import * as api from './api';

const diagnostics = { workflowRunId: 'run-2', availability: 'AVAILABLE', nodes: [
  { nodeId: 'read', operatorId: 'table.read', runtime: 'native', status: 'SUCCEEDED', attemptCount: 1,
    startedAt: null, finishedAt: null, errorCode: null },
  { nodeId: 'analyze', operatorId: 'table.summary', runtime: 'native', status: 'FAILED', attemptCount: 2,
    startedAt: null, finishedAt: null, errorCode: 'WORKER_LOST' },
] };

it('aborts a hidden run read and ignores the response if it arrives after reopening', async () => {
  let finishFirst!: (value: unknown) => void;
  const first = new Promise(resolve => { finishFirst = resolve; });
  const data = { workflowRunId: 'run-1', availability: 'AVAILABLE', nodes: [] };
  const read = vi.spyOn(api, 'callApi').mockImplementationOnce(() => first).mockResolvedValueOnce(data);
  const view = render(<WorkflowNodeDetails runId="run-1" project="p" status="FAILED" />);
  try {
    const summary = screen.getByText('节点详情');
    fireEvent.click(summary);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    const signal = read.mock.calls[0][3];
    fireEvent.click(summary);
    await waitFor(() => expect(signal?.aborted).toBe(true));
    fireEvent.click(summary);
    await screen.findByText('暂无节点记录。');
    await act(async () => finishFirst({ ...data, availability: 'UNAVAILABLE' }));
    expect(screen.getByText('暂无节点记录。')).toBeVisible();
    expect(screen.queryByText('这次运行的节点记录不可用。')).toBeNull();
  } finally {
    view.unmount();
    read.mockRestore();
  }
});


it('uses the frozen dependency graph and shows selected node diagnostics', async () => {
  const read = vi.spyOn(api, 'callApi').mockResolvedValueOnce(diagnostics).mockResolvedValueOnce({
    workflowRunId: 'run-2', availability: 'AVAILABLE',
    nodes: [{ nodeId: 'read', dependsOn: [] }, { nodeId: 'analyze', dependsOn: ['read'] }],
  });
  const view = render(<WorkflowNodeDetails runId="run-2" project="p" status="FAILED" />);
  try {
    fireEvent.click(screen.getByText('节点详情'));
    fireEvent.click(await screen.findByRole('button', { name: /analyze.*节点失败.*上游.*read/ }));
    expect(screen.getByText('WORKER_LOST')).toBeVisible();
    expect(screen.getByText(/尝试 2 次/)).toBeVisible();
  } finally {
    view.unmount();
    read.mockRestore();
  }
});

it('keeps multiple node diagnostics available when the older Gateway has no graph route', async () => {
  const read = vi.spyOn(api, 'callApi').mockResolvedValueOnce(diagnostics)
    .mockRejectedValueOnce(new api.ApiError(404, 'HTTP_404'));
  const view = render(<WorkflowNodeDetails runId="run-2" project="p" status="FAILED" />);
  try {
    fireEvent.click(screen.getByText('节点详情'));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(screen.getByText('read · 节点已完成')).toBeVisible();
    expect(screen.getByText('analyze · 节点失败')).toBeVisible();
    expect(screen.getByText('WORKER_LOST')).toBeVisible();
    expect(screen.queryByRole('region', { name: '工作流节点依赖图' })).toBeNull();
  } finally {
    view.unmount();
    read.mockRestore();
  }
});
