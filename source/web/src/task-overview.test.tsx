import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { TaskCard, TaskHeader } from './TaskCard';
import { useSessionStream } from './use-session-stream';
import { capabilitiesFixture, dataRef, historyFixture, sessionResultsFixture, sessionSummaryFixture, turnFixture } from './test-fixtures';
import type { Result, TaskContext } from './types';

function task(state = 'WAITING_INPUT', sessionId = 'session-1'): TaskContext {
  return {
    schemaVersion: 'task-context.v1',
    spec: {
      taskId: 'monitoring', sessionId, revision: 2, objective: '分析监测井水位变化',
      constraints: [{ id: 'scope', text: '仅纳入 2025 年有统一高程基准的监测井' }],
      criteria: [{ id: 'reply', kind: 'REPLY', text: '交付趋势解释' }],
      unresolvedItems: state === 'WAITING_INPUT' ? ['缺少井口高程基准'] : [],
      suggestions: [], steps: [{ id: 'compare', title: '比较同基准水位', dependsOn: [], criterionIds: ['reply'], gaps: [] }],
    },
    progress: {
      state, lifecycle: state === 'COMPLETE' ? 'COMPLETE' : 'ACTIVE',
      criteria: [{ id: 'reply', satisfied: state === 'COMPLETE',
        evidence: state === 'COMPLETE' ? { criterionId: 'reply', agentRunId: 'agent-1' } : null }],
      steps: [{ id: 'compare', complete: state === 'COMPLETE' }], results: [],
      blockingReasons: state === 'BLOCKED' ? ['AGENT_BUDGET_EXCEEDED'] : [],
      metrics: { totalTokens: 0, durationMs: 0 },
    },
  };
}

beforeEach(() => {
  window.history.replaceState({}, '', '/#/sessions/session-1');
  Element.prototype.scrollIntoView = vi.fn();
});

it.each([
  ['WAITING_INPUT', '等待补充信息', '缺少井口高程基准'],
  ['WAITING_CONFIRMATION', '等待执行确认', '请核对当前冻结批次。'],
  ['EXECUTING', '正在执行', '任务正在等待已批准的计算。'],
  ['BLOCKED', '任务受阻', null],
  ['COMPLETE', '任务已完成', '当前任务的交付条件和业务步骤已完成。'],
])('shows Task %s using the selector and real waiting facts', (state, label, detail) => {
  const open = vi.fn();
  render(<TaskHeader task={task(state)} name="监测井趋势研究" onOpen={open} />);
  const header = screen.getByRole('region', { name: '当前研究任务' });
  expect(within(header).getByRole('heading')).toHaveTextContent('监测井趋势研究');
  expect(header).toHaveTextContent('当前研究目标 分析监测井水位变化');
  expect(within(header).getByText(label)).toBeVisible();
  if (detail) expect(within(header).getByText(detail)).toBeVisible();
  fireEvent.click(within(header).getByRole('button', { name: '查看任务概览' }));
  expect(open).toHaveBeenCalledOnce();
});

it('shows only the next action supported by the current Task snapshot and conversation', () => {
  const onSupplement = vi.fn();
  const onConfirmation = vi.fn();
  const onOpen = vi.fn();
  const { rerender } = render(<TaskHeader task={task('WAITING_INPUT')} {...{ onOpen, onSupplement, onConfirmation }} />);
  fireEvent.click(screen.getByRole('button', { name: '补充任务信息' }));
  expect(onSupplement).toHaveBeenCalledOnce();
  expect(screen.queryByRole('button', { name: '查看当前执行确认' })).not.toBeInTheDocument();
  rerender(<TaskHeader task={task('WAITING_CONFIRMATION')} onOpen={onOpen} />);
  expect(screen.queryByRole('button', { name: '查看当前执行确认' })).not.toBeInTheDocument();
  rerender(<TaskHeader task={task('WAITING_CONFIRMATION')} {...{ onOpen, onSupplement, onConfirmation }} />);
  expect(screen.queryByRole('button', { name: '补充任务信息' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看当前执行确认' }));
  expect(onConfirmation).toHaveBeenCalledOnce();
});

it.each(['missing criterion', 'missing evidence', 'wrong evidence', 'missing step progress'])('does not show completion with %s', defect => {
  const value = task('COMPLETE');
  if (defect === 'missing criterion') value.spec.criteria = [];
  if (defect === 'missing evidence') value.progress.criteria[0].evidence = null;
  if (defect === 'wrong evidence') value.progress.criteria[0].evidence = { criterionId: 'other', agentRunId: 'agent-1' };
  if (defect === 'missing step progress') value.progress.steps = [];
  render(<TaskHeader task={value} onOpen={vi.fn()} />);
  expect(screen.getByText('状态待核实')).toBeVisible();
  expect(screen.queryByText('任务已完成')).not.toBeInTheDocument();
});

it.each([
  { reviewQuote: '确认完成：其他条件', reviewGoalRevision: 2 },
  { reviewQuote: '确认完成：交付趋势解释' },
])('does not show completion with incomplete or mismatched review evidence', review => {
  const value = task('COMPLETE');
  value.spec.criteria[0].kind = 'USER_REVIEW';
  value.progress.criteria[0].evidence = { criterionId: 'reply', agentRunId: 'agent-1', ...review };
  render(<TaskHeader task={value} onOpen={vi.fn()} />);
  expect(screen.getByText('状态待核实')).toBeVisible();
  expect(screen.queryByText('任务已完成')).not.toBeInTheDocument();
});

it('reads the current specification, constraints and an explicitly empty step list', () => {
  const value = task('COMPLETE');
  value.spec.steps = [];
  value.progress.steps = [];
  render(<><TaskHeader task={value} onOpen={vi.fn()} /><TaskCard task={value} disabled onReview={vi.fn()} /></>);
  expect(screen.getByText('任务已完成')).toBeVisible();
  expect(screen.getByRole('heading', { name: '原始研究目标' })).toBeVisible();
  expect(screen.getByText('当前规格 · 修订 2')).toBeVisible();
  expect(screen.getByText('仅纳入 2025 年有统一高程基准的监测井')).toBeVisible();
  expect(screen.getByText('已满足')).toBeVisible();
  expect(screen.getByText('当前规格未定义业务步骤。')).toBeVisible();
});

it('shows missing and unknown condition evidence, the declared step gap, and the task-level blocker', () => {
  const value = task('BLOCKED');
  value.spec.criteria.push({ id: 'future', kind: 'FUTURE' as 'REPLY', text: '新类型条件' },
    { id: 'draft', kind: 'DRAFT', text: '已拟定对比方案' },
    { id: 'review', kind: 'USER_REVIEW', text: '业务验收' });
  value.progress.criteria.push({ id: 'future', satisfied: false }, {
    id: 'draft', satisfied: true, evidence: { criterionId: 'draft', agentRunId: 'agent-1' },
  }, { id: 'review', satisfied: false });
  value.spec.steps.push({ id: 'deliver', title: '交付对比报告', criterionIds: ['draft'], dependsOn: ['compare'], gaps: ['缺少可读取方案'] });
  render(<TaskCard task={value} disabled={false} onReview={vi.fn()} />);
  expect(screen.getByText('条件类型待核实')).toBeVisible();
  expect(screen.getAllByText('当前快照无法核实该条件的状态或证据。')).toHaveLength(2);
  expect(screen.getByRole('heading', { name: '任务阻塞原因' })).toBeVisible();
  const step = within(screen.getByText('交付对比报告').closest('li')!);
  expect(step.getByText('待核实')).toBeVisible();
  expect(step.getByText('依赖：比较同基准水位')).toBeVisible();
  expect(step.getByText('关联条件：已拟定对比方案')).toBeVisible();
  expect(step.getByText('缺口：缺少可读取方案')).toBeVisible();
  expect(screen.getByRole('button', { name: '确认此项满足' })).toBeDisabled();
});

it('opens exact output and evidence sources without attributing other attempts or turns', () => {
  const value = task('ACTIVE');
  const output = dataRef({ outputName: 'summary' });
  const other = { ...output, checksum: 'sha256:' + 'c'.repeat(64) };
  value.spec.criteria = [
    { id: 'result', kind: 'RESULT', text: '生成趋势汇总', modelId: 'example-model', outputNames: ['summary'] },
    { id: 'review', kind: 'USER_REVIEW', text: '验收趋势解释' },
    { id: 'draft', kind: 'DRAFT', text: '查看旧草稿' },
  ];
  value.progress.criteria = [
    { id: 'result', satisfied: true, evidence: { criterionId: 'result', agentRunId: 'agent-1', workflowRunId: 'run-1' } },
    { id: 'review', satisfied: true, evidence: { criterionId: 'review', agentRunId: 'agent-1', reviewGoalRevision: 2, reviewQuote: '确认完成：验收趋势解释' } },
    { id: 'draft', satisfied: true, evidence: { criterionId: 'draft', agentRunId: 'agent-1', workflowDraftId: 'old-draft' } },
  ];
  value.progress.results = [output];
  const run: Result = { agentRunId: 'agent-1', goalRevision: 1, workflowRunId: 'run-1',
    modelId: 'example-model', toolCallId: 'call-1', status: 'COMPLETE', remoteOutputs: [other] };
  const props = { task: value, disabled: false, onReview: vi.fn(), onResource: vi.fn(), onEvidenceTurn: vi.fn(),
    turns: [turnFixture({ workflowDraftId: 'different-draft' }), turnFixture({ goalRevision: 2, agentRunId: 'agent-2' })] };
  const { rerender } = render(<TaskCard {...props} results={[run, { ...run, workflowRunId: 'run-2', remoteOutputs: [output] }]} />);
  const condition = within(screen.getByText('生成趋势汇总').closest('li')!);
  expect(condition.getByText('当前成果列表未提供与此证据匹配的版本。')).toBeVisible();
  rerender(<TaskCard {...props} results={[{ ...run, remoteOutputs: [output] }]} />);
  fireEvent.click(condition.getByRole('button', { name: /JSON/ }));
  expect(props.onResource).toHaveBeenCalledWith(output.versionId, output);
  const review = within(screen.getByText('验收趋势解释').closest('li')!);
  expect(review.getByText('确认完成：验收趋势解释')).toBeVisible();
  fireEvent.click(review.getByText('查看服务端证据'));
  fireEvent.click(review.getByRole('button', { name: '查看来源对话' }));
  expect(props.onEvidenceTurn).toHaveBeenCalledExactlyOnceWith(2);
  const draft = within(screen.getByText('查看旧草稿').closest('li')!);
  fireEvent.click(draft.getByText('查看服务端证据'));
  expect(draft.getByText('old-draft')).toBeVisible();
  expect(draft.queryByRole('button', { name: '查看来源对话' })).not.toBeInTheDocument();
});

it('clears a previous task immediately and ignores a late snapshot after Session switch', async () => {
  let late!: (response: Response) => void;
  let refreshRequested!: () => void;
  const refreshStarted = new Promise<void>(resolve => { refreshRequested = resolve; });
  let reads = 0;
  const streams: { url: string; handler?: (event: MessageEvent) => void }[] = [];
  vi.stubGlobal('EventSource', class {
    constructor(public url: string) { streams.push(this); }
    handler?: (event: MessageEvent) => void;
    addEventListener(type: string, fn: (event: MessageEvent) => void) { if (type === 'task.revised') this.handler = fn; }
    close() {}
  });
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [sessionSummaryFixture({ title: '趋势研究' }),
      sessionSummaryFixture({ sessionId: 'session-2', title: '普通咨询' })], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/session-2/messages')) return json(historyFixture({ sessionId: 'session-2', task: null,
      items: [{ messageId: 1, role: 'user', goalRevision: 1, content: '咨询地下水术语' }] }));
    if (url.endsWith('/session-1/messages')) {
      if (++reads === 1) return json(historyFixture({ task: task(), eventCursor: 10 }));
      return new Promise<Response>(resolve => { late = resolve; refreshRequested(); });
    }
    throw new Error(`unexpected ${url}`);
  }));
  render(<App />);
  expect(await screen.findByRole('region', { name: '当前研究任务' })).toHaveTextContent('趋势研究');
  fireEvent.click(screen.getByRole('button', { name: '查看任务概览' }));
  expect(await screen.findByRole('region', { name: '主任务' })).toHaveTextContent('交付趋势解释');
  act(() => streams.find(stream => stream.url.includes('/events'))?.handler?.(new MessageEvent('task.revised', { lastEventId: '11' })));
  await refreshStarted;
  act(() => {
    window.history.replaceState({}, '', '/#/sessions/session-2');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(await screen.findByText('咨询地下水术语')).toBeVisible();
  await act(async () => late(json(historyFixture({ task: task('COMPLETE'), eventCursor: 11 }))));
  expect(screen.queryByRole('region', { name: '当前研究任务' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '主任务' })).not.toBeInTheDocument();
  expect(screen.queryByText('分析监测井水位变化')).not.toBeInTheDocument();
});


it('applies a newer Task revision from the active Session snapshot and rejects a lower watermark', async () => {
  const revised = task('ANALYZING');
  revised.spec.revision = 3;
  let snapshot = historyFixture({ task: task(), eventCursor: 10 });
  const reads = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/results') ? sessionResultsFixture() : snapshot),
    { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', reads);
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  const log = vi.fn();
  const { result } = renderHook(() => useSessionStream('session-1', 'demo', 'scope', log));
  await waitFor(() => expect(result.current.history?.task?.spec.revision).toBe(2));
  snapshot = historyFixture({ task: revised, eventCursor: 11 });
  await act(async () => result.current.refresh('session-1'));
  expect(result.current.history?.task?.spec.revision).toBe(3);
  expect(result.current.history?.task?.spec.unresolvedItems).toEqual([]);
  snapshot = historyFixture({ task: task(), eventCursor: 10 });
  await act(async () => result.current.refresh('session-1'));
  expect(result.current.history?.task?.spec.revision).toBe(3);
});
