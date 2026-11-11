import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ActivityPanel } from './ActivityPanel';
import { ResultCard } from './ChatCards';
import { prependHistory, refreshHistory, taskLabel, turnPhase } from './chat-state';
import { activityFixture, dataRef, historyFixture, instant, taskFixture, turnFixture } from './test-fixtures';
import type { Confirmation, ProgressUpdate, Result } from './types';

const confirmation: Confirmation = {
  confirmationId: 'confirmation-1', agentRunId: 'agent-1', goalRevision: 1,
  status: 'CONFIRMED', decision: 'CONFIRM', decisionState: null, reason: '两次独立计算',
  actions: ['tool-1', 'tool-2'].map(toolCallId => ({ toolCallId, tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0', workflowId: 'summary', workflowVersion: '1.0.0', arguments: {} })),
};

function openActivityDetails() {
  fireEvent.click(screen.getByRole('button', { name: /^(?:正在处理|已完成|处理未完成)/ }));
}

describe('actual activity and task presentation', () => {
  it('uses authoritative server titles while keeping activity and Agent completion distinct', () => {
    expect(turnPhase(turnFixture({ status: 'RUNNING', activities: [activityFixture({ status: 'COMPLETE', finishedAt: instant })] }))).toBe('处理请求');
    expect(turnPhase(turnFixture({ status: 'RUNNING', activities: [activityFixture({ kind: 'SEGMENT', purpose: 'INITIAL', status: 'QUEUED', title: '等待执行槽位' })] }))).toBe('等待执行槽位');
    expect(turnPhase(turnFixture({ status: 'RUNNING', activities: [activityFixture({ kind: 'SEGMENT', purpose: 'RESUME_EXECUTION', title: '整理执行结果' })] }))).toBe('整理执行结果');
    expect(turnPhase(turnFixture({ status: 'WAITING_EXECUTION', activities: [activityFixture({ status: 'COMPLETE' })] }))).toBe('正在执行已确认的分析');
    expect(taskLabel(taskFixture({ status: 'FAILED', stage: null, blockedByWorkflowRunId: 'previous' }))).toBe('因前项失败未执行');
  });

  it('shows current activity and five latest completed activities, with full history on demand', () => {
    const activities = Array.from({ length: 8 }, (_, i) => activityFixture({ activityId: `activity-${i}`, toolCallId: `call-${i}`, status: 'COMPLETE', finishedAt: instant }));
    render(<ActivityPanel turn={turnFixture({ status: 'RUNNING', activities: [...activities, activityFixture({
      activityId: 'current', toolName: 'opaque-tool-name', title: '读取并核验证据',
      summary: '完整读取命中单元，并核验版本、单元与校验和。',
    })] })} snapshotAt={instant} connected />);
    openActivityDetails();
    const list = screen.getByRole('list', { name: 'Agent 实际处理' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(6);
    expect(within(list).getByText('读取并核验证据')).toBeVisible();
    expect(within(list).getByText('完整读取命中单元，并核验版本、单元与校验和。')).toBeVisible();
    expect(screen.queryByText('opaque-tool-name')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看全部 9 项 Agent 处理' }));
    expect(within(screen.getByRole('list', { name: 'Agent 实际处理' })).getAllByRole('listitem')).toHaveLength(9);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('condenses repeated model requests into business stages without exposing technical accounting', () => {
    const usage = { tokenizer: 'o200k_base' as const, totalTokens: 1200, availableInputTokens: 126976, excessTokens: 0,
      breakdown: { tools: 400, instructions: 500, dialogue: 50, executionReferences: 100, observations: 100, serialization: 50 } };
    const activities = [
      activityFixture({ activityId: 'segment', kind: 'SEGMENT', purpose: 'INITIAL', toolName: null, status: 'COMPLETE', finishedAt: instant,
        title: '处理当前请求', summary: '正在装载当前会话、任务状态和可用能力。' }),
      activityFixture({ activityId: 'model-1', kind: 'MODEL_REQUEST', purpose: 'PROCESSING', toolName: null, status: 'COMPLETE', finishedAt: instant, contextUsage: usage,
        title: '生成下一步处理决策', summary: 'Agent 正在基于当前上下文选择下一项明确动作。' }),
      activityFixture({ activityId: 'retrieve', toolName: 'opaque-retrieve', status: 'COMPLETE', finishedAt: instant,
        title: '检索候选证据', summary: '找到 2 个候选知识单元，下一步将读取并核验完整内容。' }),
      activityFixture({ activityId: 'model-2', kind: 'MODEL_REQUEST', purpose: 'PROCESSING', toolName: null, status: 'COMPLETE', finishedAt: instant, contextUsage: usage,
        title: '筛选候选证据', summary: 'Agent 正在依据候选标识选择需要完整读取的知识单元。' }),
      activityFixture({ activityId: 'read', toolName: 'opaque-read', status: 'COMPLETE', finishedAt: instant,
        title: '读取并核验证据', summary: '已完整读取并核验 2 个可引用知识单元。' }),
      activityFixture({ activityId: 'model-3', kind: 'MODEL_REQUEST', purpose: 'PROCESSING', toolName: null, status: 'RUNNING', contextUsage: usage,
        title: '组织证据结论', summary: 'Agent 正在基于已完整读取的证据组织结论、限制和来源。' }),
    ];
    render(<ActivityPanel turn={turnFixture({ status: 'RUNNING', activities })} snapshotAt={instant} connected />);
    expect(screen.getByRole('button', { name: /^正在处理/ })).toHaveAttribute('aria-expanded', 'false');
    openActivityDetails();
    expect(within(screen.getByRole('list', { name: 'Agent 实际处理' })).getAllByRole('listitem')).toHaveLength(4);
    expect(screen.getByText('处理当前请求')).toBeVisible();
    expect(screen.getByText('正在装载当前会话、任务状态和可用能力。')).toBeVisible();
    expect(screen.getByText('检索候选证据')).toBeVisible();
    expect(screen.getByText('找到 2 个候选知识单元，下一步将读取并核验完整内容。')).toBeVisible();
    expect(screen.getByText('读取并核验证据')).toBeVisible();
    expect(screen.getByText('已完整读取并核验 2 个可引用知识单元。')).toBeVisible();
    expect(screen.getAllByText('组织证据结论')).toHaveLength(2);
    expect(screen.getByText('Agent 正在基于已完整读取的证据组织结论、限制和来源。')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('组织证据结论');
    expect(screen.getByText('用时 0 秒')).toBeVisible();
    expect(screen.queryByText(/技术信息/)).not.toBeInTheDocument();
    expect(screen.queryByText(/上下文峰值/)).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('uses the first request time for the initial summary instead of the enclosing segment time', () => {
    const activities = [
      activityFixture({ activityId: 'segment', kind: 'SEGMENT', purpose: 'INITIAL', status: 'COMPLETE', createdAt: '2026-10-09T01:00:00.000Z', finishedAt: '2026-10-09T01:00:04.000Z', title: '处理当前请求' }),
      activityFixture({ activityId: 'model', kind: 'MODEL_REQUEST', purpose: 'PROCESSING', status: 'COMPLETE', createdAt: '2026-10-09T01:00:00.100Z', finishedAt: '2026-10-09T01:00:00.500Z', title: '生成下一步处理决策' }),
    ];
    render(<ActivityPanel turn={turnFixture({ status: 'COMPLETE', activities })} snapshotAt="2026-10-09T01:00:04Z" connected />);
    openActivityDetails();
    const initial = screen.getByText('生成下一步处理决策').closest('li')!;
    expect(initial).toHaveTextContent('少于 1 秒');
    expect(initial).not.toHaveTextContent('4 秒');
  });

  it('renders the persisted server summary without decorating it from reply fields', () => {
    const read = activityFixture({ toolName: 'read_evidence', status: 'COMPLETE', finishedAt: instant,
      title: '读取并核验证据', summary: '已完整读取并核验 2 个可引用知识单元。' });
    render(<ActivityPanel turn={turnFixture({ activities: [read], reply: {
      conclusion: { markdown: '结论', sourceIndexes: [0, 1] }, details: [], executionSummary: null, unresolvedItems: [],
      sources: [
        { title: 'gw-aggregation-choice', versionId: 'version-1', unitId: 'chunk-0', checksum: 'a'.repeat(64) },
        { title: 'gw-aggregation-choice', versionId: 'version-1', unitId: 'chunk-1', checksum: 'b'.repeat(64) },
      ],
    } })} snapshotAt={instant} connected />);
    openActivityDetails();
    expect(screen.getByText('已完整读取并核验 2 个可引用知识单元。')).toBeVisible();
    expect(screen.queryByText(/本轮引用：/)).not.toBeInTheDocument();
  });

  it('shows a rejected tool call as requiring repair instead of completed', () => {
    const retry = activityFixture({
      activityId: 'retry', toolName: 'discover_datasets', status: 'FAILED', finishedAt: instant,
      title: '检索项目数据', summary: '工具参数未通过合同校验，Agent 正在按合同修正后重试。',
    });
    const model = activityFixture({
      activityId: 'model', kind: 'MODEL_REQUEST', purpose: 'PROCESSING', toolName: null,
      status: 'RUNNING', toolCallId: null, title: '修正工具调用',
      summary: 'Agent 正在根据合同校验结果修正参数并重新调用同一能力。',
    });
    render(<ActivityPanel turn={turnFixture({ status: 'RUNNING', activities: [retry, model] })} snapshotAt={instant} connected />);

    openActivityDetails();
    expect(screen.getByText('未完成')).toBeVisible();
    expect(screen.getAllByText('修正工具调用')).toHaveLength(2);
    expect(screen.getByText('Agent 正在根据合同校验结果修正参数并重新调用同一能力。')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('修正工具调用');
  });

  it('keeps routine progress collapsed until requested and preserves the user choice per state', () => {
    const running = turnFixture({ status: 'RUNNING', activities: [activityFixture()] });
    const view = render(<ActivityPanel turn={running} snapshotAt={instant} connected />);
    const toggle = screen.getByRole('button', { name: /^正在处理/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'activity-content-agent-1');
    fireEvent.click(toggle);
    expect(document.getElementById('activity-content-agent-1')).toBeVisible();
    view.rerender(<ActivityPanel turn={{ ...running, status: 'COMPLETE' }} snapshotAt={instant} connected />);
    expect(screen.getByRole('button', { name: /^已完成/ })).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByRole('button', { name: /^已完成/ }));
    expect(document.getElementById('activity-content-agent-1')).toBeVisible();
    view.rerender(<ActivityPanel turn={{ ...running, status: 'COMPLETE' }} snapshotAt="2026-10-09T01:00:01Z" connected />);
    expect(screen.getByRole('button', { name: /^已完成/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('selects the latest completed activities by finish time while retaining their timeline order', () => {
    const segment = activityFixture({ activityId: 'segment', kind: 'SEGMENT', purpose: 'INITIAL', status: 'COMPLETE', finishedAt: '2026-10-09T01:01:00Z' });
    const tools = Array.from({ length: 6 }, (_, i) => activityFixture({ activityId: `tool-${i}`, status: 'COMPLETE', finishedAt: `2026-10-09T01:00:0${i + 1}Z` }));
    const view = render(<ActivityPanel turn={turnFixture({ status: 'RUNNING', activities: [segment, ...tools] })} snapshotAt="2026-10-09T01:01:01Z" connected />);
    expect([...view.container.querySelectorAll('[data-activity-id]')].map(element => element.getAttribute('data-activity-id'))).toEqual(['segment', 'tool-2', 'tool-3', 'tool-4', 'tool-5']);
  });

  it('keeps full plans and execution batches out of the conversation process panel', () => {
    const turn = turnFixture({ status: 'WAITING_EXECUTION', confirmations: [confirmation, { ...confirmation, confirmationId: 'future', status: 'PENDING', decision: null }],
      executionTasks: [taskFixture({ status: 'FAILED', stage: null, errorCode: 'ALGORITHM_FAILED' }),
        taskFixture({ workflowRunId: 'workflow-2', toolCallId: 'tool-2', position: 1, status: 'FAILED', stage: null, blockedByWorkflowRunId: 'workflow-1' })],
      plans: [{ revision: 2, goal: '比较两个版本', reason: '采用同一种统计方法', steps: [{ stepId: 'first', tool: 'run_summary', purpose: '分析第一版' }, { stepId: 'second', tool: 'run_summary', purpose: '分析第二版' }] }] });
    render(<ActivityPanel turn={turn} snapshotAt={instant} connected />);
    expect(screen.queryByText(/第 1 批任务/)).not.toBeInTheDocument();
    expect(screen.queryByText('执行计划 · 第 2 版')).not.toBeInTheDocument();
    expect(screen.queryByText('分析第一版')).not.toBeInTheDocument();
    openActivityDetails();
    expect(screen.getByText('本轮尚无处理活动。')).toBeVisible();
  });

  it('shows semantic milestones together with server-authored Agent activity phrases', () => {
    const progress = (code: ProgressUpdate['code'], status: ProgressUpdate['status'], title: string, summary: string, facts: ProgressUpdate['facts'] = {}): ProgressUpdate => ({
      progressId: `agent-1:${code}`, agentRunId: 'agent-1', workflowRunId: code.startsWith('EXECUTION') || code === 'RESULTS_PUBLISHING' ? 'workflow-1' : null,
      code, status, scope: code.startsWith('EXECUTION') || code === 'RESULTS_PUBLISHING' ? 'WORKFLOW' : 'TURN', title, summary, facts,
      createdAt: instant, startedAt: instant, finishedAt: status === 'RUNNING' ? null : instant,
    });
    const activities = Array.from({ length: 19 }, (_, index) => activityFixture({
      activityId: `inspect-${index}`, toolCallId: `inspect-${index}`, toolName: 'inspect_data_version',
      status: 'COMPLETE', finishedAt: instant, title: '核验候选数据版本',
      summary: '已核验指定数据版本及其 1 个数据对象。',
    }));
    render(<ActivityPanel turn={turnFixture({ status: 'RUNNING', progressUpdates: [
      progress('CASE_MATCHED', 'COMPLETE', '匹配分析案例', '已匹配固定案例 yaozhanbao-transfer-case。', { caseId: 'yaozhanbao-transfer-case' }),
      progress('INPUTS_VERIFIED', 'COMPLETE', '核验案例输入', '已核验 19 项同批输入，版本、对象和校验和均匹配。', { verifiedInputs: 19, profile: 'standard' }),
      progress('EXECUTION_RUNNING', 'RUNNING', '执行分析工作流', '正在运行已确认的分析步骤。', { attempt: 1 }),
    ], activities })} snapshotAt={instant} connected />);
    openActivityDetails();
    expect(within(screen.getByRole('list', { name: '流程里程碑' })).getAllByRole('listitem')).toHaveLength(3);
    expect(within(screen.getByRole('list', { name: 'Agent 实际处理' })).getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByText('已核验 19 项同批输入，版本、对象和校验和均匹配。')).toBeVisible();
    expect(screen.getAllByText('核验候选数据版本')).toHaveLength(5);
    expect(screen.getAllByText('已核验指定数据版本及其 1 个数据对象。')).toHaveLength(5);
    expect(screen.getByRole('status')).toHaveTextContent('执行分析工作流');
    expect(screen.queryByText('已并行核验 19 项数据版本及其对象。')).not.toBeInTheDocument();
  });

  it('uses queue admission time for complete segment duration and keeps connection uncertainty separate', () => {
    render(<ActivityPanel turn={turnFixture({ status: 'RUNNING', activities: [activityFixture({ kind: 'SEGMENT', purpose: 'INITIAL', createdAt: instant, startedAt: '2026-10-09T01:01:00Z', title: '处理当前请求' })] })} snapshotAt="2026-10-09T01:02:00Z" connected={false} />);
    openActivityDetails();
    expect(screen.getByText('2 分 0 秒')).toBeVisible();
    expect(screen.getByText('实时更新尚未连接，当前显示最后收到的状态。')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('处理当前请求');
  });

  it('shows a truthful sub-second duration instead of zero seconds', () => {
    render(<ActivityPanel turn={turnFixture({ status: 'COMPLETE', timing: {
      createdAt: '2026-10-09T01:00:00.000Z', startedAt: '2026-10-09T01:00:00.000Z', finishedAt: '2026-10-09T01:00:00.548Z',
    } })} snapshotAt="2026-10-09T01:00:01Z" connected />);
    expect(screen.getByText('用时 少于 1 秒')).toBeVisible();
  });

  it('uses snapshot task status and waits for the complete published output group', () => {
    const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary', status: 'RUNNING', workflowRunId: 'workflow-1', remoteOutputs: [] };
    const task = taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: 1 });
    const view = render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} />);
    expect(screen.getByText('已完成')).toBeVisible();
    expect(screen.getByText('正在同步结果文件…')).toBeVisible();
    view.rerender(<ResultCard task={task} result={{ ...result, remoteOutputs: [dataRef({ versionId: 'output-1', outputName: 'summary' })] }} project="demo" onResource={vi.fn()} />);
    expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('href', '/api/v12/data/versions/output-1/objects/object-1/content');
  });
});

it.each(['CANCELLED', 'SUPERSEDED'] as const)('does not label the %s turn as completed', status => {
  render(<ActivityPanel turn={turnFixture({ status })} snapshotAt={instant} connected />);
  expect(screen.getByTestId('activity-panel').querySelector('.activity-toggle strong')).toHaveTextContent(status === 'CANCELLED' ? '已取消' : '已被新要求替代');
  expect(screen.queryByText('已完成')).not.toBeInTheDocument();
});

it('keeps live status and cursor when loading history, and rejects an older latest snapshot', () => {
  const latest = historyFixture({ eventCursor: 120, turns: [{ status: 'COMPLETE' }] });
  const historical = historyFixture({ goalRevision: 2, eventCursor: 135, snapshotAt: '2026-10-09T01:05:00Z', turns: [{ status: 'RUNNING' }], nextCursor: 5 });
  const merged = prependHistory(latest, historical);
  expect(merged.eventCursor).toBe(120);
  expect(merged.goalRevision).toBe(1);
  expect(merged.snapshotAt).toBe(latest.snapshotAt);
  expect(merged.turns[0].status).toBe('COMPLETE');
  expect(refreshHistory(merged, historyFixture({ eventCursor: 119, turns: [{ status: 'RUNNING' }] }))).toEqual(merged);
});
