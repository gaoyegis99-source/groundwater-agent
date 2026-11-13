import { describe, expect, it } from 'vitest';
import { activityFixture, dataRef, instant, taskFixture, turnFixture } from './test-fixtures';
import type { Confirmation, ExecutionTask, History, Result, Turn } from './types';
import { selectResourcePublicationUi, selectSessionUi, selectWorkflowDraftUi, selectWorkflowNodeUi } from './ui-selectors';

const metadata = { sessionId: 'session-1', archived: false };
const criterion = {
  id: 'reply', text: '解释监测井水位', sourceQuote: '解释监测井水位', interpretation: null,
  sourceMessageId: 1, sourceGoalRevision: 1, kind: 'REPLY' as const, modelId: null, outputNames: [],
};

function task(state: string, satisfied = false) {
  return {
    schemaVersion: 'task-context.v1' as const,
    spec: {
      schemaVersion: 'task-spec.v1', taskId: 'task-1', sessionId: metadata.sessionId, revision: 1,
      objective: criterion.text, sourceMessageId: 1, sourceGoalRevision: 1, criteria: [criterion],
      constraints: [], unresolvedItems: state === 'WAITING_INPUT' ? ['监测时间范围'] : [], suggestions: [],
      steps: [{ id: 'explain', title: '解释', dependsOn: [], criterionIds: [criterion.id], gaps: [] }],
    },
    progress: {
      schemaVersion: 'task-progress.v1', lifecycle: state === 'COMPLETE' ? 'COMPLETE' as const : 'ACTIVE' as const,
      state, criteria: [{ id: criterion.id, satisfied,
        evidence: satisfied ? { criterionId: criterion.id, agentRunId: 'agent-1', workflowRunId: null, reviewQuote: null } : null }],
      steps: [{ id: 'explain', complete: satisfied }], results: [], blockingReasons: [],
      metrics: { scope: 'EXPLICITLY_LINKED_RUNS', inputTokens: 0, outputTokens: 0, totalTokens: 0,
        durationMs: 0, requestCount: 0, observationCallCount: 0 },
    },
  };
}

function history(turn = turnFixture(), overrides: Partial<History> = {}): History {
  return {
    sessionId: metadata.sessionId, goalRevision: turn.goalRevision, eventCursor: 10, snapshotAt: instant,
    nextCursor: null, items: [{ messageId: 1, goalRevision: turn.goalRevision, role: 'user', content: criterion.text, inputReferences: [] }],
    turns: [turn], task: null, preparation: null, controlRequests: [], controlProposals: [], controlMessages: [],
    ...overrides,
  };
}

function workflow(overrides: Partial<ExecutionTask> = {}) {
  return taskFixture({ attempt: 1, isLatestAttempt: true, retryOfWorkflowRunId: null, ...overrides });
}

function completedRun(overrides: Partial<ExecutionTask> = {}) {
  return workflow({ status: 'SUCCEEDED', stage: null, finishedAt: instant, durationMs: 0, outputCount: 1, ...overrides });
}

function result(run: ExecutionTask, overrides: Partial<Result> = {}) {
  return {
    goalRevision: run.goalRevision, agentRunId: run.agentRunId, toolCallId: run.toolCallId, modelId: run.modelId,
    status: 'COMPLETE', workflowRunId: run.workflowRunId, attempt: run.attempt ?? null,
    remoteOutputs: [dataRef({ outputName: 'summary' })], ...overrides,
  };
}

const initialAction = {
  toolCallId: 'tool-1', tool: 'groundwater.summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
  modelChecksum: dataRef().checksum, workflowId: 'workflow.summary', workflowVersion: '1.0.0',
  workflowChecksum: dataRef().checksum, inputSchemaChecksum: dataRef().checksum, fingerprint: dataRef().checksum,
  arguments: {}, inputReferences: {},
};

function approval(overrides: Partial<Confirmation>): Confirmation {
  return {
    confirmationId: 'confirmation-1', goalRevision: 1, agentRunId: 'agent-1', status: 'PENDING',
    reason: null, actions: [initialAction], decision: null, decisionState: null,
    revision: 1, kind: 'INITIAL' as const, replacesConfirmationId: null, sourceWorkflowRunId: null,
    requestId: null, createdAt: instant, ...overrides,
  };
}

function turnWithRun(run: ExecutionTask, status = 'COMPLETE'): Turn {
  return turnFixture({
    status, executionTasks: [run],
    confirmations: [approval({ status: 'CONFIRMED', decision: 'CONFIRM' })],
  });
}

function select(snapshot: History, items?: Result[], freshness: 'current' | 'unverified' = 'current', verifiedOutputs?: ReturnType<typeof dataRef>[]) {
  const results = items === undefined ? undefined : { status: 'loaded' as const, items };
  return selectSessionUi({ sessionId: metadata.sessionId, metadata, history: snapshot, results, freshness, canWrite: true, verifiedOutputs });
}

describe('Session UI projection', () => {
  it('projects a completed consultation with no primary task', () => {
    const ui = select(history(turnFixture({
      requestedMode: 'AUTO', resolvedMode: 'DIALOGUE', routingStatus: 'DECIDED', routingSource: 'MODEL',
      requestId: 'request-1', semanticIntent: 'CONSULT', taskAction: 'NONE', intentDecision: 'DIALOGUE', dialoguePolicy: 'READ_ONLY',
    })));
    expect(ui.session).toMatchObject({ label: '未归档', actions: ['open', 'rename', 'archive'] });
    expect(ui.task.presence?.label).toBe('无主任务');
    expect(ui.turns[0].status.label).toBe('本轮已结束');
    expect(ui.turns[0].workflowRuns).toEqual([]);
  });

  it('keeps a Session task active when its Turn is complete', () => {
    const ui = select(history(turnFixture(), { task: task('ACTIVE') }));
    expect(ui.task).toMatchObject({ taskId: 'task-1', lifecycle: { raw: 'ACTIVE' }, state: { raw: 'ACTIVE' } });
    expect(ui.turns[0].status.raw).toBe('COMPLETE');
  });

  it('offers input when Kernel task state is WAITING_INPUT', () => {
    const ui = select(history(turnFixture(), { task: task('WAITING_INPUT') }));
    expect(ui.task.state).toMatchObject({ tone: 'warning', actions: ['view', 'clarify'] });
    expect(ui.turns[0].status.raw).toBe('COMPLETE');
  });

  it('projects a task waiting for the current approval', () => {
    const turn = turnFixture({ status: 'WAITING_CONFIRMATION', confirmations: [approval({})] });
    const ui = select(history(turn, { task: task('WAITING_CONFIRMATION') }));
    expect(ui.task.state?.label).toBe('等待执行确认');
    expect(ui.turns[0].confirmations[0].status.actions).toEqual(['view', 'edit', 'confirm', 'reject']);
    expect(ui.turns[0].workflowRuns).toEqual([]);
  });

  it('projects Task execution, Turn waiting and Agent Activity independently', () => {
    const turn = { ...turnWithRun(workflow(), 'WAITING_EXECUTION'), activities: [activityFixture()] };
    const ui = select(history(turn, { task: task('EXECUTING') }));
    expect(ui.task.state?.raw).toBe('EXECUTING');
    expect(ui.turns[0].status).toMatchObject({ raw: 'WAITING_EXECUTION', actions: ['view', 'stop'] });
    expect(ui.turns[0].workflowRuns[0].status.raw).toBe('RUNNING');
    expect(ui.turns[0].activities[0].status.raw).toBe('RUNNING');
  });

  it('shows successful execution and matched published references without scientific validation', () => {
    const run = completedRun();
    const outcome = result(run);
    const ui = select(history(turnWithRun(run)), [outcome], 'current', outcome.remoteOutputs).turns[0].workflowRuns[0];
    expect(ui.status).toMatchObject({ raw: 'SUCCEEDED', tone: 'success' });
    expect(ui.publication.actions).toEqual(['preview', 'download']);
    expect(ui.scientificValidation).toMatchObject({ raw: null, tone: 'neutral', actions: [] });
  });

  it('keeps publication pending while Executor is publishing, and synchronizing after success without results', () => {
    const publishing = workflow({ stage: 'PUBLISHING' });
    const inProgress = select(history(turnWithRun(publishing, 'WAITING_EXECUTION'))).turns[0].workflowRuns[0];
    expect(inProgress.status.raw).toBe('RUNNING');
    expect(inProgress.publication.label).toBe('正在发布成果');
    const run = completedRun();
    const syncedRun = select(history(turnWithRun(run)), [result(run, { workflowRunId: 'other-run' })]).turns[0].workflowRuns[0];
    expect(syncedRun.status.raw).toBe('SUCCEEDED');
    expect(syncedRun.publication).toMatchObject({ label: '成果待同步', actions: ['refresh'] });
    expect(syncedRun.outputs).toEqual([]);
  });

  it('keeps execution failure visible and offers a recovery check on the latest eligible attempt', () => {
    const run = workflow({ status: 'FAILED', stage: null, finishedAt: instant, errorCode: 'WORKFLOW_FAILED' });
    const ui = select(history(turnWithRun(run, 'FAILED'), { task: task('BLOCKED') }));
    expect(ui.task.state?.raw).toBe('BLOCKED');
    expect(ui.turns[0].workflowRuns[0].status).toMatchObject({ raw: 'FAILED', tone: 'danger', actions: ['view', 'check-recovery'] });
  });

  it.each(['REQUESTED', 'PROCESSING'] as const)('keeps stop %s separate from Run status', status => {
    const ui = select(history(turnWithRun(workflow(), 'WAITING_EXECUTION'), {
      controlRequests: [{ requestId: 'stop-1', agentRunId: 'agent-1', status }],
    })).turns[0];
    expect(ui.stop?.raw).toBe(status);
    expect(ui.status.actions).toEqual(['view']);
    expect(ui.workflowRuns[0].status.raw).toBe('RUNNING');
  });

  it('only shows cancellation when the authoritative status is CANCELLED', () => {
    const cancelled = workflow({ status: 'CANCELLED', stage: null, finishedAt: instant, errorCode: 'JOB_CANCELLED' });
    const controls = { controlRequests: [{ requestId: 'stop-1', agentRunId: 'agent-1', status: 'COMPLETE' as const }] };
    const ui = select(history(turnWithRun(cancelled, 'CANCELLED'), controls)).turns[0];
    expect(ui.stop?.label).toBe('停止请求处理结束');
    expect(ui.workflowRuns[0].status.label).toBe('执行已取消');
    const wonRace = select(history(turnWithRun(completedRun()), controls)).turns[0];
    expect(wonRace.workflowRuns[0].status.raw).toBe('SUCCEEDED');
  });

  it('renders unknown enums with no success or write action', () => {
    const turn = turnWithRun(workflow({ status: 'toString', stage: null }), 'FUTURE_TURN');
    const ui = select(history(turn, { task: task('FUTURE_TASK') }));
    for (const status of [ui.task.state, ui.turns[0].status, ui.turns[0].workflowRuns[0].status]) {
      expect(status).toMatchObject({ label: '状态待核实', tone: 'neutral', actions: ['view'] });
    }
    // Simulate a newer server value crossing the generated enum type boundary.
    expect(selectResourcePublicationUi({ state: 'FUTURE_STATE' as 'READY' }).tone).toBe('neutral');
  });

  it('preserves attempts and sibling Runs, including two actions using the same model', () => {
    const first = workflow({ status: 'FAILED', stage: null, isLatestAttempt: false, finishedAt: instant });
    const retry = completedRun({ workflowRunId: 'retry-1', attempt: 2, retryOfWorkflowRunId: first.workflowRunId,
      confirmationId: 'recovery-1' });
    const sibling = completedRun({ workflowRunId: 'run-2', toolCallId: 'tool-2', position: 1 });
    const turn = turnWithRun(first);
    const ui = select(history({ ...turn, executionTasks: [first, retry, sibling], confirmations: [
      approval({ status: 'CONFIRMED', decision: 'CONFIRM', actions: [initialAction, { ...initialAction, toolCallId: 'tool-2' }] }),
      approval({ confirmationId: 'recovery-1', kind: 'RECOVERY', status: 'CONFIRMED',
        revision: 2, decision: 'CONFIRM', sourceWorkflowRunId: first.workflowRunId }),
    ] }), [result(retry), result(sibling, { remoteOutputs: [dataRef({ versionId: 'sibling-version' })] })]).turns[0].workflowRuns;
    expect(ui.map(run => run.workflowRunId)).toEqual(['workflow-1', 'retry-1', 'run-2']);
    expect(ui[0].status.actions).toEqual(['view']);
    expect(ui[1]).toMatchObject({ attempt: 2, retryOfWorkflowRunId: 'workflow-1' });
    expect(ui[2].outputs[0].versionId).toBe('sibling-version');
  });

  it('retains a superseded Confirmation and only enables its current replacement', () => {
    const turn = turnFixture({ status: 'WAITING_CONFIRMATION', confirmations: [
      approval({ status: 'SUPERSEDED' }),
      approval({ confirmationId: 'confirmation-2', kind: 'EDITED', revision: 2, replacesConfirmationId: 'confirmation-1' }),
    ] });
    const snapshot = history(turn, { task: task('WAITING_CONFIRMATION') });
    const confirmations = select(snapshot).turns[0].confirmations;
    expect(confirmations[0].status).toMatchObject({ raw: 'SUPERSEDED', actions: ['view'] });
    expect(confirmations[1]).toMatchObject({
      replacesConfirmationId: 'confirmation-1', status: { raw: 'PENDING', actions: ['view', 'edit', 'confirm', 'reject'] },
    });
    expect(select(snapshot, [], 'unverified').turns[0].confirmations[1].status.actions).toEqual(['view']);
  });

  it('does not let a later completed stop hide an unresolved stop on the same AgentRun', () => {
    const turn = turnWithRun(workflow(), 'WAITING_EXECUTION');
    const ui = select(history(turn, { controlRequests: [
      { requestId: 'stop-pending', agentRunId: turn.agentRunId, status: 'REQUESTED' },
      { requestId: 'stop-done', agentRunId: turn.agentRunId, status: 'COMPLETE' },
    ] })).turns[0];
    expect(ui.stop?.raw).toBe('REQUESTED');
    expect(ui.status.actions).toEqual(['view']);
  });

  it('allows a current recovery Confirmation on a terminal Turn while preserving the failed attempt', () => {
    const run = workflow({ status: 'FAILED', stage: null, finishedAt: instant, errorCode: 'WORKFLOW_FAILED' });
    const turn = turnWithRun(run, 'FAILED');
    turn.confirmations.push(approval({
      confirmationId: 'recovery-1', revision: 2, kind: 'RECOVERY', requestId: 'recover-1',
      sourceWorkflowRunId: run.workflowRunId,
    }));
    const ui = select(history(turn)).turns[0];
    expect(ui.confirmations[1].status.actions).toContain('confirm');
    expect(ui.workflowRuns[0].status).toMatchObject({ raw: 'FAILED', actions: ['view'] });
  });

  it('requires criteria and steps to support Task completion', () => {
    const incomplete = select(history(turnFixture(), { task: task('COMPLETE') }));
    const complete = select(history(turnFixture(), { task: task('COMPLETE', true) }));
    expect(incomplete.task.lifecycle?.tone).toBe('neutral');
    expect(complete.task.lifecycle?.tone).toBe('success');
  });

  it('ignores late data from another Session and distinguishes unknown task from absent task', () => {
    const ui = selectSessionUi({ sessionId: 'session-2', metadata, history: history(), freshness: 'current', canWrite: true });
    expect(ui.session.actions).toEqual(['open']);
    expect(ui.task.presence?.label).toBe('主任务待核对');
    expect(ui.turns).toEqual([]);
  });

  it('projects nodes, Data state and Draft without deriving one from another', () => {
    const diagnostics = selectWorkflowNodeUi({ workflowRunId: 'workflow-1', availability: 'AVAILABLE', nodes: [{
      nodeId: 'node-1', operatorId: 'summary', runtime: 'native', status: 'LEASED', attemptCount: 1,
      startedAt: null, finishedAt: null, errorCode: null,
    }] });
    expect(diagnostics.nodes[0].status.raw).toBe('LEASED');
    expect(selectResourcePublicationUi({ state: 'PUBLISHED' }).label).toBe('已发布');
    expect(selectWorkflowDraftUi({ status: 'GENERATING', payload: null }).actions).toEqual(['view']);
  });
});

describe('UI-02 selector boundary review', () => {
  const run = completedRun();
  const snapshot = history(turnWithRun(run));

  it.each([
    [undefined, '未请求成果', 'neutral'],
    [{ status: 'loading' }, '正在读取成果', 'info'],
    [{ status: 'loaded', items: [] }, '成果待同步', 'warning'],
    [{ status: 'failed' }, '成果读取失败', 'danger'],
  ] as const)('distinguishes Results read state %j for a successful Run', (results, label, tone) => {
    const ui = selectSessionUi({ sessionId: metadata.sessionId, history: snapshot, freshness: 'current', results });
    expect(ui.turns[0].workflowRuns[0]).toMatchObject({
      status: { raw: 'SUCCEEDED' },
      publication: { label, tone, actions: results?.status === 'loading' ? [] : ['refresh'] },
      outputs: [],
    });
  });

  it('fails closed for an unknown Results read state', () => {
    const ui = selectSessionUi({
      sessionId: metadata.sessionId, history: snapshot, freshness: 'current',
      results: { status: 'unexpected' as 'loading' },
    });
    expect(ui.turns[0].workflowRuns[0].publication).toMatchObject({
      label: '状态待核实', tone: 'neutral', actions: ['refresh'],
    });
  });

  it('keeps published references visible while requiring exact Data verification per output', () => {
    const first = dataRef();
    const second = dataRef({ versionId: 'version-2', objectId: 'object-2' });
    const turn = turnWithRun(completedRun({ outputCount: 2 }));
    const input = {
      sessionId: metadata.sessionId, history: history(turn), freshness: 'current' as const,
      results: { status: 'loaded' as const, items: [result(run, { remoteOutputs: [first, second] })] },
    };
    const unverified = selectSessionUi(input).turns[0].workflowRuns[0];
    expect(unverified.publication).toMatchObject({ label: '已取得发布成果', actions: [] });
    expect(unverified.outputs).toEqual([first, second]);
    const partial = selectSessionUi({
      ...input, verifiedOutputs: [first, { ...second, semanticId: 'other-semantics' }],
    }).turns[0].workflowRuns[0];
    expect(partial.publication.actions).toEqual([]);
    expect(partial.outputAccess.map(item => item.actions)).toEqual([['preview', 'download'], []]);
    const verified = selectSessionUi({ ...input, verifiedOutputs: [first, second] }).turns[0].workflowRuns[0];
    expect(verified.publication.actions).toEqual(['preview', 'download']);
  });

  it('accepts a completed Task with criteria satisfied and no declared business steps', () => {
    const completed = task('COMPLETE', true);
    const ui = select(history(turnFixture(), { task: {
      ...completed, spec: { ...completed.spec, steps: [] }, progress: { ...completed.progress, steps: [] },
    } }));
    expect(ui.task.lifecycle).toMatchObject({ raw: 'COMPLETE', tone: 'success' });
  });

  it('fails closed for a completion claim with no criteria or a missing declared step check', () => {
    const completed = task('COMPLETE', true);
    const noCriteria = {
      ...completed, spec: { ...completed.spec, criteria: [], steps: [] },
      progress: { ...completed.progress, criteria: [], steps: [] },
    };
    const missingStepCheck = { ...completed, progress: { ...completed.progress, steps: [] } };
    for (const candidate of [noCriteria, missingStepCheck]) {
      const ui = select(history(turnFixture(), { task: candidate }));
      expect(ui.task.lifecycle).toMatchObject({ label: '状态待核实', tone: 'neutral' });
      expect(ui.task.state).toMatchObject({ label: '状态待核实', tone: 'neutral' });
    }
  });

  it('offers only a recovery check on the failed Run until Kernel supplies a new Confirmation', () => {
    const failed = workflow({ status: 'FAILED', stage: null, finishedAt: instant });
    const terminal = turnWithRun(failed, 'FAILED');
    const ui = select(history(terminal)).turns[0];
    expect(ui.workflowRuns[0].status.actions).toEqual(['view', 'check-recovery']);
    expect(ui.confirmations.map(item => item.status.actions)).toEqual([['view']]);
    expect(select(history(terminal), undefined, 'unverified').turns[0].workflowRuns[0].status.actions).toEqual(['view']);
  });
});
