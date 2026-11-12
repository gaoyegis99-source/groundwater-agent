import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { WorkflowNodeDetails } from './WorkflowNodeDetails';
import * as api from './api';

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
