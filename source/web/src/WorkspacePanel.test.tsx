import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { resolveWorkspaceState, WorkspacePanel } from './WorkspacePanel';
import { dataRef, historyFixture, replyFixture, taskFixture } from './test-fixtures';
import type { Result, TaskContext } from './types';
import { scopedStorageKey } from './browser-scope';
import { useWorkspaceController } from './use-workspace-controller';

beforeEach(() => sessionStorage.clear());

it('restores the selected result identity before the result list arrives and scopes it to its owner', () => {
  const exact = dataRef({ semanticId: 'published-semantics', objectId: 'published-object' });
  const first = renderHook(() => useWorkspaceController('session-1', 'researcher-a'));
  act(() => first.result.current.openResource(exact.versionId, exact));
  first.unmount();

  const restored = renderHook(({ scope }) => useWorkspaceController('session-1', scope), { initialProps: { scope: 'researcher-a' } });
  expect(restored.result.current.resourceId).toBe(exact.versionId);
  expect(restored.result.current.resourceReference).toEqual(exact);
  restored.rerender({ scope: 'researcher-b' });
  expect(restored.result.current.resourceId).toBeNull();
  expect(restored.result.current.resourceReference).toBeNull();
  restored.rerender({ scope: 'researcher-a' });
  act(() => restored.result.current.openResource(exact.versionId));
  expect(restored.result.current.resourceReference).toBeNull();
});

it('does not offer recovery for a failed run from an earlier goal or a replaced attempt', () => {
  const view = render(<WorkspacePanel history={historyFixture({ goalRevision: 2, turns: [
    { goalRevision: 1, agentRunId: 'agent-old', executionTasks: [taskFixture({ agentRunId: 'agent-old', status: 'FAILED', isLatestAttempt: true })] },
    { goalRevision: 2, agentRunId: 'agent-current', status: 'COMPLETE' },
  ] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  expect(screen.queryByRole('button', { name: /恢复.*批次/ })).not.toBeInTheDocument();
  view.rerender(<WorkspacePanel history={historyFixture({ turns: [{ executionTasks: [
    taskFixture({ status: 'FAILED', isLatestAttempt: false, attempt: 1 }),
    taskFixture({ workflowRunId: 'retry', status: 'SUCCEEDED', isLatestAttempt: true, attempt: 2 }),
  ] }] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  expect(screen.queryByRole('button', { name: /恢复.*批次/ })).not.toBeInTheDocument();
});

it('recovers the failed source and its blocked downstream batch, not just the last blocked row', () => {
  const recover = vi.fn();
  render(<WorkspacePanel history={historyFixture({ turns: [{ status: 'FAILED', executionTasks: [
    taskFixture({ workflowRunId: 'succeeded', position: 0, status: 'SUCCEEDED', isLatestAttempt: true }),
    taskFixture({ workflowRunId: 'failed-source', toolCallId: 'source', position: 1, status: 'TIMED_OUT', isLatestAttempt: true }),
    taskFixture({ workflowRunId: 'blocked', toolCallId: 'downstream', position: 2, status: 'FAILED', blockedByWorkflowRunId: 'failed-source', isLatestAttempt: true }),
  ] }] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onRecover={recover} />);
  fireEvent.click(screen.getByRole('button', { name: /恢复.*批次/ }));
  expect(recover).toHaveBeenCalledExactlyOnceWith('failed-source');
  expect(screen.getByText(/2 项分析.*确认后执行/)).toBeVisible();
});

it('allows the latest cancelled source to request a separate recovery confirmation', () => {
  const recover = vi.fn();
  render(<WorkspacePanel history={historyFixture({ turns: [{ status: 'CANCELLED', executionTasks: [
    taskFixture({ workflowRunId: 'cancelled-source', status: 'CANCELLED', isLatestAttempt: true }),
  ] }] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onRecover={recover} />);
  fireEvent.click(screen.getByRole('button', { name: /恢复.*批次/ }));
  expect(recover).toHaveBeenCalledExactlyOnceWith('cancelled-source');
});

it('shows recorded work and released outputs without presenting pending files as published', () => {
  const onJump = vi.fn(), onResource = vi.fn();
  const task = taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const history = historyFixture({ turns: [{ status: 'COMPLETE', plans: [{ revision: 2, goal: '核对井点数据', reason: '需要分析', steps: [{ stepId: 'one', tool: 'run_summary', purpose: '计算描述统计' }] }], executionTasks: [task] }] });
  const results: Result[] = [
    { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'COMPLETE', workflowRunId: task.workflowRunId, remoteOutputs: [dataRef({ versionId: 'released', outputName: 'summary' })] },
    { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-2', modelId: task.modelId, status: 'RUNNING', workflowRunId: 'workflow-2', remoteOutputs: [dataRef({ versionId: 'pending', outputName: 'pending' })] },
    { goalRevision: 0, agentRunId: 'agent-0', toolCallId: 'tool-0', modelId: task.modelId, status: 'COMPLETE', workflowRunId: 'workflow-0', remoteOutputs: [dataRef({ versionId: 'older', outputName: 'older' })] },
  ];
  render(<WorkspacePanel history={history} results={results} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={onJump} onResource={onResource} />);
  expect(screen.getByText('核对井点数据')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Agent Plan 第 2 版'));
  expect(screen.getByText('计算描述统计')).toBeVisible();
  expect(screen.getByText('本轮回答已完成')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: /成果/ }));
  expect(screen.getByText('汇总结果')).toBeVisible();
  expect(screen.queryByText('pending')).not.toBeInTheDocument();
  expect(screen.getByText('older')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: /任务/ }));
  fireEvent.click(screen.getByRole('button', { name: /查看本轮回答/ }));
  fireEvent.click(screen.getByRole('tab', { name: /成果/ }));
  fireEvent.click(screen.getByRole('button', { name: /汇总结果/ }));
  expect(onJump).toHaveBeenCalledExactlyOnceWith(1);
  expect(onResource).toHaveBeenCalledExactlyOnceWith('released', results[0].remoteOutputs[0]);
});

it('keeps every selected tab usable for an empty session', () => {
  render(<WorkspacePanel history={null} results={[]} activeId="" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onResource={vi.fn()} />);
  expect(screen.getByText('暂无任务')).toBeVisible();
  expect(screen.queryByText('正在读取会话')).not.toBeInTheDocument();
  expect(screen.getByText('本轮未创建持续任务。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '数据' }));
  expect(screen.getByRole('button', { name: '打开数据库选择数据' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '成果' }));
  expect(screen.getByText('暂无已发布成果。')).toBeInTheDocument();
});

it('shows loading while the selected session history is being read', () => {
  render(<WorkspacePanel history={null} results={[]} activeId="session-loading" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  expect(screen.getByText('正在读取会话')).toBeVisible();
  expect(screen.queryByText('暂无任务')).not.toBeInTheDocument();
});

it('recovers invalid persisted tab, pin and width without hiding the panel', () => {
  const tab = scopedStorageKey('', 'workspace-tab', 'damaged');
  const pinned = scopedStorageKey('', 'workspace-pinned', 'damaged');
  const width = scopedStorageKey('', 'workspace-width', 'damaged');
  sessionStorage.setItem(tab, 'legacy-tab');
  sessionStorage.setItem(pinned, 'true');
  sessionStorage.setItem(width, '99999');
  render(<WorkspacePanel history={null} results={[]} activeId="damaged" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);

  expect(screen.getByRole('complementary', { name: '任务工作台' })).toHaveStyle({ width: '460px' });
  expect(screen.getByRole('tab', { name: '任务' })).toHaveClass('active');
  expect(screen.getByRole('button', { name: '固定当前页签' })).not.toHaveClass('active');
  expect(sessionStorage.getItem(tab)).toBe('tasks');
  expect(sessionStorage.getItem(width)).toBe('460');
  expect(sessionStorage.getItem(pinned)).toBeNull();
});

it('opens an explicitly selected result even when the task tab is pinned', async () => {
  sessionStorage.setItem(scopedStorageKey('', 'workspace-tab', 'session-selected'), 'tasks');
  sessionStorage.setItem(scopedStorageKey('', 'workspace-pinned', 'session-selected'), '1');
  render(<WorkspacePanel history={historyFixture()} results={[]} activeId="session-selected" selectedResourceId="result-version" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('tab', { name: '成果' })).toHaveClass('active'));
});

it('projects an unresolved completed turn as needing input rather than task completion', () => {
  const history = historyFixture({ turns: [{ status: 'COMPLETE', reply: { ...replyFixture('未找到匹配的数据'), unresolvedItems: [{ markdown: '未找到数据', sourceIndexes: [] }] } }] });
  expect(resolveWorkspaceState(history)).toMatchObject({ taskStatus: '需要补充信息', recommendedTab: 'inputs' });
  render(<WorkspacePanel history={history} results={[]} activeId="missing-data" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  expect(screen.getAllByText('需要补充信息')[0]).toBeVisible();
  expect(screen.queryByText('任务已完成')).not.toBeInTheDocument();
});

it('requires every task acceptance criterion before showing task completion', () => {
  const task: TaskContext = {
    schemaVersion: 'task-context.v1',
    spec: {
      taskId: 'task-1', revision: 3, objective: '整理地下水成果', constraints: [],
      criteria: [{ id: 'review', text: '用户确认成果可用', kind: 'USER_REVIEW' }],
      unresolvedItems: [], suggestions: [], steps: [],
    },
    progress: {
      lifecycle: 'COMPLETE', state: 'WAITING_REVIEW',
      criteria: [{ id: 'review', satisfied: false }], steps: [], results: [],
      blockingReasons: ['仍需用户评审'], metrics: { totalTokens: 10, durationMs: 50 },
    },
  };
  const history = historyFixture({ task });
  expect(resolveWorkspaceState(history)).toMatchObject({ taskStatus: '等待验收条件完成', tone: 'warning' });
  render(<WorkspacePanel history={history} results={[]} activeId="criteria" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  expect(screen.queryByText('任务已完成')).not.toBeInTheDocument();
  expect(screen.getByText('仍需用户评审')).toBeVisible();
});

it('does not count a consumed preparation as an active prepared input', () => {
  const history = historyFixture({
    preparation: {
      schemaVersion: 'task-preparation.v1', preparationId: 'preparation-1',
      revision: 1, sessionId: 'session-1', agentRunId: 'agent-1',
      goalRevision: 1, taskId: null, baseTaskRevision: null,
      status: 'READY', freshForCurrentTurn: true, disposition: 'CONSUMED',
      entries: [{
        targetKind: 'EXECUTABLE_MODEL', targetId: 'groundwater.summary',
        targetVersion: '1.0.0', selectionPolicy: 'PINNED', status: 'READY',
        inputs: { versionId: dataRef({ datasetId: 'consumed-prepared-dataset' }) },
        knowledgeRefs: [], parameters: { axis: 'columns' },
        parameterSources: { axis: 'MODEL_DEFAULT' }, checks: [],
        blockingReasons: [],
      }],
    },
  });

  render(<WorkspacePanel history={history} results={[]} activeId="consumed" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onResource={vi.fn()} />);
  fireEvent.click(screen.getByRole('tab', { name: '数据' }));
  expect(screen.queryByText('consumed-prepared-dataset')).not.toBeInTheDocument();
  expect(screen.getAllByText('暂无数据')).toHaveLength(4);
});
