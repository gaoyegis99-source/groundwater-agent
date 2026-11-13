import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { resolveWorkspaceState, WorkspacePanel } from './WorkspacePanel';
import { capabilitiesFixture, dataDetail, dataRef, historyFixture, replyFixture, taskFixture } from './test-fixtures';
import type { Confirmation, Result, TaskContext } from './types';
import { scopedStorageKey } from './browser-scope';
import { useWorkspaceController } from './use-workspace-controller';

beforeEach(() => sessionStorage.clear());

it('keeps a pending confirmation from an older turn read-only in the parameter workspace', () => {
  const previous: Confirmation = {
    confirmationId: 'old-confirmation', goalRevision: 1, agentRunId: 'agent-1', status: 'PENDING',
    reason: null, decision: null, decisionState: null, revision: 1,
    actions: [{ toolCallId: 'summary', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'summary', workflowVersion: '1.0.0', arguments: { axis: 'columns' } }],
  };
  const history = historyFixture({ goalRevision: 2, turns: [
    { goalRevision: 1, agentRunId: 'agent-1', status: 'WAITING_CONFIRMATION', confirmations: [previous] },
    { goalRevision: 2, agentRunId: 'agent-2', status: 'COMPLETE' },
  ] });
  const props = { history, results: [], activeId: 'session-1', project: 'demo', capabilities: capabilitiesFixture(),
    collapsed: false, routedTab: 'parameters' as const, onToggle: vi.fn(), onJump: vi.fn() };
  const view = render(<WorkspacePanel {...props} />);
  expect(screen.getByText('历史批次', { exact: true })).toBeVisible();
  expect(screen.queryByRole('button', { name: '编辑当前参数' })).not.toBeInTheDocument();
  view.rerender(<WorkspacePanel {...props} editingConfirmationId={previous.confirmationId} />);
  expect(screen.getByText(/此批次已更新/)).toBeVisible();
  expect(screen.getByRole('button', { name: '保存为新修订' })).toBeDisabled();
});

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
  expect(screen.queryByRole('button', { name: /准备恢复未完成步骤/ })).not.toBeInTheDocument();
  view.rerender(<WorkspacePanel history={historyFixture({ turns: [{ executionTasks: [
    taskFixture({ status: 'FAILED', isLatestAttempt: false, attempt: 1 }),
    taskFixture({ workflowRunId: 'retry', status: 'SUCCEEDED', isLatestAttempt: true, attempt: 2 }),
  ] }] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
  expect(screen.queryByRole('button', { name: /准备恢复未完成步骤/ })).not.toBeInTheDocument();
});

it('recovers the failed source and its blocked downstream batch, not just the last blocked row', () => {
  const recover = vi.fn();
  render(<WorkspacePanel history={historyFixture({ turns: [{ status: 'FAILED', executionTasks: [
    taskFixture({ workflowRunId: 'succeeded', position: 0, status: 'SUCCEEDED', isLatestAttempt: true }),
    taskFixture({ workflowRunId: 'failed-source', toolCallId: 'source', position: 1, status: 'TIMED_OUT', isLatestAttempt: true }),
    taskFixture({ workflowRunId: 'blocked', toolCallId: 'downstream', position: 2, status: 'FAILED', blockedByWorkflowRunId: 'failed-source', isLatestAttempt: true }),
  ] }] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onRecover={recover} />);
  fireEvent.click(screen.getByRole('button', { name: /准备恢复未完成步骤/ }));
  expect(recover).toHaveBeenCalledExactlyOnceWith('failed-source');
  expect(screen.getByText(/2 项分析.*需要确认/)).toBeVisible();
});

it('allows the latest cancelled source to request a separate recovery confirmation', () => {
  const recover = vi.fn();
  render(<WorkspacePanel history={historyFixture({ turns: [{ status: 'CANCELLED', executionTasks: [
    taskFixture({ workflowRunId: 'cancelled-source', status: 'CANCELLED', isLatestAttempt: true }),
  ] }] })} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onRecover={recover} />);
  fireEvent.click(screen.getByRole('button', { name: /准备恢复未完成步骤/ }));
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
  expect(screen.getByText(/版本 released/)).toBeVisible();
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

it('reports a failed publication read and retries without treating cached output as current', () => {
  const result: Result = {
    workflowRunId: 'run-1', goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1',
    modelId: 'groundwater.summary', status: 'COMPLETE', remoteOutputs: [dataRef({ outputName: 'summary' })],
  };
  const retry = vi.fn();
  const props = { history: historyFixture(), results: [result], activeId: 'session-1', routedTab: 'results' as const,
    collapsed: false, onToggle: vi.fn(), onJump: vi.fn(), onRetryResults: retry };
  const { rerender } = render(<WorkspacePanel {...props} resultsRead={{ status: 'loading' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('正在读取成果列表');
  expect(screen.queryByRole('button', { name: /汇总结果/ })).not.toBeInTheDocument();
  rerender(<WorkspacePanel {...props} resultsRead={{ status: 'failed' }} />);
  expect(screen.queryByText('暂无已发布成果。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重新读取成果列表' }));
  expect(retry).toHaveBeenCalledOnce();
  rerender(<WorkspacePanel {...props} resultsRead={{ status: 'loaded', items: [result] }} />);
  expect(screen.getByRole('button', { name: /汇总结果/ })).toBeVisible();
});

it('keeps a restored exact preview readable while withholding result actions until publication is checked', async () => {
  const output = dataRef({ outputName: 'summary', format: 'zip', mediaType: 'application/zip' });
  const detail = dataDetail();
  detail.objects[0] = { ...detail.objects[0], format: output.format, mediaType: output.mediaType };
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(detail), {
    headers: { 'Content-Type': 'application/json' },
  })));
  const result: Result = {
    workflowRunId: 'run-1', goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1',
    modelId: 'groundwater.summary', status: 'COMPLETE', remoteOutputs: [output],
  };
  const props = { history: historyFixture(), results: [result], activeId: 'session-1', routedTab: 'results' as const,
    selectedResourceId: output.versionId, selectedResourceReference: output, onContinueResult: vi.fn(),
    collapsed: false, onToggle: vi.fn(), onJump: vi.fn() };
  const { rerender } = render(<WorkspacePanel {...props} resultsRead={{ status: 'loading' }} />);
  expect(await screen.findByText('此格式暂不支持交互预览。')).toBeVisible();
  expect(screen.queryByRole('link', { name: /下载/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '带成果打开新会话' })).not.toBeInTheDocument();
  rerender(<WorkspacePanel {...props} resultsRead={{ status: 'loaded', items: [result] }} />);
  expect(screen.getByRole('link', { name: '下载', exact: true })).toHaveAttribute('href',
    `/api/v12/data/versions/${output.versionId}/objects/${output.objectId}/content`);
  expect(screen.getByRole('button', { name: '带成果打开新会话' })).toBeEnabled();
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
  expect(resolveWorkspaceState(history)).toMatchObject({ taskStatus: '状态待核实', tone: 'neutral' });
  render(<WorkspacePanel history={history} results={[]} activeId="session-1" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} />);
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

it('displays verified Data facts and discards a late version read after a session switch', async () => {
  let release!: (response: Response) => void;
  const ref = dataRef();
  const second = dataRef({ versionId: 'next-version' });
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/version-1')) return new Promise<Response>(resolve => { release = resolve; });
    if (url.endsWith('/next-version')) return json(dataDetail({ versionId: 'next-version',
      semantics: { crs: 'EPSG:4547' }, inputVersions: ['original-survey'] }));
    throw new Error(`Unexpected read ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  const props = { results: [], collapsed: false, routedTab: 'inputs' as const, onToggle: vi.fn(), onJump: vi.fn() };
  const snapshot = (sessionId: string, reference: typeof ref) => historyFixture({ sessionId,
    items: [{ role: 'user', content: '资料', messageId: 1, goalRevision: 1, inputReferences: [reference] }],
  });
  const view = render(<WorkspacePanel {...props} history={snapshot('session-1', ref)} activeId="session-1" />);
  await waitFor(() => expect(release).toBeDefined());
  view.rerender(<WorkspacePanel {...props} history={snapshot('session-2', second)} activeId="session-2" />);
  fireEvent.click(await screen.findByText('数据质量与来源'));
  expect(screen.getByText('EPSG:4547')).toBeVisible();
  expect(screen.getByText('original-survey')).toBeVisible();
  expect(screen.getByText('未提供全量字段统计')).toBeVisible();
  await act(async () => release(json(dataDetail({ semantics: { crs: 'EPSG:4326' } }))));
  expect(screen.queryByText('EPSG:4326')).not.toBeInTheDocument();
  expect(screen.getAllByText('当前可读取')).toHaveLength(1);
});

it('shows the explicitly bound approval and exact output on each workflow attempt', () => {
  const output = dataRef({ outputName: 'summary' });
  const run = taskFixture({ workflowRunId: 'run-2', status: 'SUCCEEDED', outputCount: 1, attempt: 2,
    confirmationId: 'approval-2', retryOfWorkflowRunId: 'run-1' });
  const older = { ...run, workflowRunId: 'run-1', attempt: 1, status: 'FAILED', confirmationId: 'approval-1', outputCount: 0 };
  const approval = { confirmationId: 'approval-2', agentRunId: run.agentRunId, goalRevision: run.goalRevision,
    revision: 2, reason: null, status: 'CONFIRMED', decision: 'CONFIRM', decisionState: null,
    actions: [{ toolCallId: run.toolCallId, tool: run.modelId, modelId: run.modelId, modelVersion: run.modelVersion,
      workflowId: run.workflowId, workflowVersion: run.workflowVersion, arguments: { axis: 'columns' }, inputReferences: { source: output } }],
  };
  const result: Result = { workflowRunId: run.workflowRunId, agentRunId: run.agentRunId, goalRevision: run.goalRevision,
    toolCallId: run.toolCallId, modelId: run.modelId, status: 'COMPLETE', remoteOutputs: [output] };
  const history = historyFixture({ turns: [{ executionTasks: [older, run], confirmations: [approval] }] });
  const open = vi.fn();
  const { container } = render(<WorkspacePanel history={history} results={[result]} activeId="session-1"
    routedTab="tasks" collapsed={false} onToggle={vi.fn()} onJump={vi.fn()} onResource={open} />);
  const newer = within(container.querySelector('[data-execution-run-id="run-2"]')! as HTMLElement);
  const earlier = within(container.querySelector('[data-execution-run-id="run-1"]')! as HTMLElement);
  expect(newer.getByText(/确认修订 2/)).toBeVisible();
  expect(earlier.queryByText(/确认修订 2/)).not.toBeInTheDocument();
  fireEvent.click(newer.getByRole('button', { name: '汇总结果' }));
  expect(earlier.queryByRole('button', { name: '汇总结果' })).not.toBeInTheDocument();
  expect(open).toHaveBeenCalledExactlyOnceWith(output.versionId, output);
});
