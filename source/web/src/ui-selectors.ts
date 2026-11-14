import { canConfirm, isBusy } from './chat-state';
import type { components } from './generated/public-api-v12';
import { isResourceReference } from './resource-item';
import type { Activity, ExecutionTask, History, ResourceReference, Result, TaskContext, Turn, WorkflowDraft } from './types';

type Schemas = components['schemas'];
type SessionMetadata = Pick<Schemas['kernel__SessionSummary'], 'sessionId' | 'archived'>;
type NodeDiagnostics = Schemas['kernel__WorkflowNodeDiagnosticsResponse'];
type Publication = Schemas['data__PublicationResponse'];

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';
type Action = 'open' | 'rename' | 'archive' | 'unarchive' | 'view' | 'refresh' |
  'clarify' | 'review' | 'stop' | 'edit' | 'confirm' | 'reject' | 'check-recovery' |
  'preview' | 'download' | 'export-draft';

export interface UiStatus {
  raw: string | null;
  label: string;
  tone: Tone;
  description: string;
  actions: readonly Action[];
}

type Display = readonly [label: string, tone: Tone, description: string];
type StatusMap = Readonly<Record<string, Display>>;

function display(raw: string | null, [label, tone, description]: Display, actions: readonly Action[] = []): UiStatus {
  return { raw, label, tone, description, actions };
}

function lookup(map: StatusMap, raw: string | null, actions: readonly Action[] = []): UiStatus {
  return raw !== null && Object.hasOwn(map, raw)
    ? display(raw, map[raw], actions)
    : display(raw, ['状态待核实', 'neutral', '当前数据无法确认此状态。'], actions.filter(action => action === 'view' || action === 'refresh'));
}

const taskLifecycle = {
  ACTIVE: ['任务未完成', 'info', '科研交付目标仍在跟进。'],
  COMPLETE: ['任务已完成', 'success', '当前任务修订已完成。'],
} satisfies Record<TaskContext['progress']['lifecycle'], Display>;

const taskStates = {
  ACTIVE: ['任务进行中', 'info', '仍有交付条件待满足。'],
  ANALYZING: ['正在分析', 'info', '服务端正在处理当前研究请求。'],
  WAITING_INPUT: ['等待补充信息', 'warning', '请查看任务缺项或准备检查。'],
  WAITING_CONFIRMATION: ['等待执行确认', 'warning', '请核对当前冻结批次。'],
  EXECUTING: ['正在执行', 'info', '任务正在等待已批准的计算。'],
  BLOCKED: ['任务受阻', 'danger', '请查看服务端记录的阻塞原因。'],
  WAITING_REVIEW: ['等待业务评审', 'warning', '有待用户评审的交付条件。'],
  COMPLETE: ['任务条件已满足', 'success', '当前任务的交付条件和业务步骤已完成。'],
} satisfies StatusMap;

const turnStates = {
  ROUTING: ['正在判断需求', 'info', '服务端正在识别请求意图。'],
  QUEUED: ['本轮等待处理', 'neutral', '请求已受理，等待启动。'],
  RUNNING: ['本轮处理中', 'info', '本轮协作仍在进行。'],
  WAITING_CONFIRMATION: ['本轮等待确认', 'warning', '本轮等待当前执行决定。'],
  WAITING_EXECUTION: ['本轮等待计算', 'info', '本轮等待已确认的计算。'],
  COMPLETE: ['本轮已结束', 'success', '服务端已结束本轮协作。'],
  FAILED: ['本轮未完成', 'danger', '请查看本轮失败信息。'],
  CANCELLED: ['本轮已取消', 'neutral', '服务端已记录本轮取消。'],
  SUPERSEDED: ['本轮已被替代', 'neutral', '旧轮次保留供查阅。'],
} satisfies StatusMap;

const activityStates = {
  QUEUED: ['活动等待中', 'neutral', '活动等待开始。'],
  RUNNING: ['活动进行中', 'info', '活动正在进行。'],
  COMPLETE: ['活动已完成', 'success', '该活动已结束。'],
  FAILED: ['活动失败', 'danger', '该活动未能完成。'],
  INTERRUPTED: ['活动已中断', 'neutral', '该活动已停止。'],
} satisfies Record<Activity['status'], Display>;

const confirmationStates = {
  PENDING: ['待确认', 'warning', '冻结批次等待决定。'],
  CONFIRMED: ['已确认', 'success', '此批次已获得批准。'],
  REJECTED: ['已拒绝', 'danger', '此批次的执行请求已被拒绝。'],
  SUPERSEDED: ['已被替代', 'neutral', '旧批次保留供查阅。'],
} satisfies StatusMap;

const decisionStates = {
  REQUESTED: ['决定已受理', 'info', '等待服务端处理决定。'],
  RUNNING: ['正在处理决定', 'info', '服务端正在处理此决定。'],
} satisfies StatusMap;

type ControlStatus = NonNullable<History['controlRequests']>[number]['status'];
const controlStates = {
  REQUESTED: ['已请求停止', 'warning', '停止请求已受理，等待服务端处理。'],
  PROCESSING: ['正在处理停止', 'warning', '等待服务端核对最终运行状态。'],
  COMPLETE: ['停止请求处理结束', 'neutral', '请查看本轮和执行记录的最终状态。'],
} satisfies Record<ControlStatus, Display>;

const runStates = {
  QUEUED: ['等待执行', 'neutral', '运行已入队。'],
  RUNNING: ['正在执行', 'info', '运行仍在执行链路中。'],
  SUCCEEDED: ['执行成功', 'success', '此执行尝试已成功结束。'],
  FAILED: ['执行失败', 'danger', '此尝试失败，请查看详情。'],
  TIMED_OUT: ['执行超时', 'danger', '此尝试达到执行时限。'],
  CANCELLED: ['执行已取消', 'neutral', '服务端已记录该尝试取消。'],
} satisfies StatusMap;

const runStages = {
  QUEUED: ['等待派发', 'neutral', '运行等待派发或开始执行。'],
  EXECUTING: ['正在运行节点', 'info', 'Executor 正在执行工作流节点。'],
  PUBLISHING: ['正在发布成果', 'info', 'Executor 正在完成发布链路。'],
} satisfies Record<NonNullable<ExecutionTask['stage']>, Display>;

const nodeStates = {
  WAITING: ['等待依赖', 'neutral', '节点等待上游依赖。'],
  READY: ['等待调度', 'neutral', '节点已具备调度条件。'],
  LEASED: ['已分配', 'info', '节点已取得 Worker 租约。'],
  RUNNING: ['节点运行中', 'info', 'Worker 正在执行此节点。'],
  SUCCEEDED: ['节点已完成', 'success', '此节点执行成功。'],
  FAILED: ['节点失败', 'danger', '请查看节点错误。'],
  CANCELLED: ['节点已取消', 'neutral', '服务端已记录节点取消。'],
  SKIPPED: ['节点未执行', 'neutral', '此节点被跳过。'],
} satisfies Record<Schemas['kernel__WorkflowNodeDiagnostic']['status'], Display>;

const nodeAvailability = {
  NOT_DISPATCHED: ['尚未派发', 'neutral', '尚无 Executor Job 节点事实。'],
  UNAVAILABLE: ['节点详情暂不可用', 'warning', '暂时无法取得节点事实。'],
  AVAILABLE: ['节点详情可用', 'info', '可以查看当前 Job 的节点事实。'],
} satisfies Record<NodeDiagnostics['availability'], Display>;

const publicationStates = {
  OPEN: ['发布已创建', 'neutral', '等待提交发布资料。'],
  PREPARING: ['准备发布', 'info', 'Data 正在准备提交。'],
  READY: ['待提交发布', 'warning', 'Data 已完成发布准备。'],
  PUBLISHED: ['已发布', 'success', 'Data 已提交此发布批次。'],
  FAILED: ['发布失败', 'danger', '请查看 Data 发布详情。'],
  ABORTED: ['发布已终止', 'neutral', 'Data 已终止此发布批次。'],
} satisfies Record<Publication['state'], Display>;

const draftStates = {
  GENERATING: ['正在生成草稿', 'info', '服务端正在生成方案草稿。'],
  COMPLETE: ['草稿已生成', 'success', '草稿可供查看和导出。'],
  FAILED: ['草稿生成失败', 'danger', '请查看草稿诊断。'],
  CANCELLED: ['草稿已取消', 'neutral', '服务端已取消草稿生成。'],
  SUPERSEDED: ['草稿已被替代', 'neutral', '旧草稿保留供查阅。'],
} satisfies Record<WorkflowDraft['status'], Display>;

export function selectWorkflowDraftUi(draft: Pick<WorkflowDraft, 'status' | 'payload'>) {
  if (draft.status === 'COMPLETE' && !draft.payload) {
    return display(draft.status, ['未生成可用草稿', 'neutral', '请查看本轮回复中的说明。'], ['view']);
  }
  return lookup(draftStates, draft.status, draft.status === 'COMPLETE' && !!draft.payload
    ? ['view', 'export-draft'] : ['view']);
}

export function selectWorkflowNodeUi(diagnostics: NodeDiagnostics) {
  return {
    workflowRunId: diagnostics.workflowRunId,
    availability: lookup(nodeAvailability, diagnostics.availability, ['refresh']),
    nodes: diagnostics.availability === 'AVAILABLE'
      ? diagnostics.nodes.map(node => ({ nodeId: node.nodeId, status: selectWorkflowNodeStatus(node.status) }))
      : [],
  };
}

export function selectWorkflowNodeStatus(status: NodeDiagnostics['nodes'][number]['status'] | null) {
  return lookup(nodeStates, status, ['view']);
}

export function selectResourcePublicationUi(publication: Pick<Publication, 'state'>) {
  return lookup(publicationStates, publication.state, ['view', 'refresh']);
}

export type ResultsRead =
  | { status: 'not-requested' | 'loading' | 'failed' }
  | { status: 'loaded'; items: readonly Result[] };

interface SessionUiInput {
  sessionId: string;
  metadata?: SessionMetadata | null;
  history?: History | null;
  results?: ResultsRead;
  // Exact references successfully checked by Data in the current access scope.
  verifiedOutputs?: readonly ResourceReference[];
  freshness: 'current' | 'unverified';
  canWrite?: boolean;
}

const criterionKinds = {
  REPLY: '对话答复',
  DRAFT: '工作流草稿',
  RESULT: '计算成果',
  USER_REVIEW: '用户评审',
} satisfies Record<TaskContext['spec']['criteria'][number]['kind'], string>;

export function selectTaskCriterion(criterion: TaskContext['spec']['criteria'][number], progress: TaskContext['progress']['criteria']) {
  const check = progress.find(item => item.id === criterion.id);
  const kind = Object.hasOwn(criterionKinds, criterion.kind) ? criterionKinds[criterion.kind] : '条件类型待核实';
  const proof = check?.evidence;
  if (Object.hasOwn(criterionKinds, criterion.kind)) {
    if (check?.satisfied === false) return { kind, state: 'pending', label: '待完成', tone: 'warning', evidence: null } as const;
    if (check?.satisfied === true && proof?.criterionId === criterion.id
      && typeof proof.agentRunId === 'string' && proof.agentRunId.trim()) {
      const reference = criterion.kind === 'DRAFT' ? proof.workflowDraftId
        : criterion.kind === 'RESULT' ? proof.workflowRunId
        : proof.agentRunId;
      const reviewMatches = criterion.kind !== 'USER_REVIEW' || (
        proof.reviewQuote === `确认完成：${criterion.text}`
        && typeof proof.reviewGoalRevision === 'number'
        && Number.isSafeInteger(proof.reviewGoalRevision) && proof.reviewGoalRevision > 0
      );
      if (reviewMatches && typeof reference === 'string' && reference.trim()) {
        return { kind, state: 'satisfied', label: '已满足', tone: 'success', evidence: proof } as const;
      }
    }
  }
  return { kind, state: 'unknown', label: '待核实', tone: 'neutral', evidence: null } as const;
}

export function selectTaskStep(step: TaskContext['spec']['steps'][number], progress: TaskContext['progress']['steps']) {
  const complete = progress.find(check => check.id === step.id)?.complete;
  if (complete === true) return { label: '已完成', tone: 'success' } as const;
  if (complete === false) return { label: '未完成', tone: 'neutral' } as const;
  return { label: '待核实', tone: 'neutral' } as const;
}

export function selectTaskCriterionOutputs(
  criterion: TaskContext['spec']['criteria'][number],
  evidence: NonNullable<ReturnType<typeof selectTaskCriterion>['evidence']>,
  taskOutputs: readonly ResourceReference[],
  results: readonly Result[],
) {
  if (criterion.kind !== 'RESULT') return [];
  const run = results.find(item => item.workflowRunId === evidence.workflowRunId && item.agentRunId === evidence.agentRunId
    && item.modelId === criterion.modelId && ['COMPLETE', 'SUCCEEDED'].includes(item.status));
  if (!run) return [];
  const identities = new Set(taskOutputs.filter(isResourceReference).map(outputIdentity));
  return run.remoteOutputs.filter(output => isResourceReference(output) && output.outputName && criterion.outputNames?.includes(output.outputName)
    && identities.has(outputIdentity(output)));
}

export function selectTaskUi(task: History['task'], actionable: boolean) {
  if (!task) {
    return {
      presence: task === null
        ? display(null, ['无主任务', 'neutral', '当前会话尚未建立主任务。'])
        : display(null, ['主任务待核对', 'neutral', '尚未取得主任务快照。'], ['refresh']),
      taskId: null, lifecycle: null, state: null,
    };
  }
  const { spec, progress } = task;
  const actions: Action[] = ['view'];
  if (actionable && progress.lifecycle === 'ACTIVE') {
    if (progress.state === 'WAITING_INPUT') actions.push('clarify');
    if (progress.state === 'WAITING_REVIEW') actions.push('review');
  }
  const claimsCompletion = progress.lifecycle === 'COMPLETE' || progress.state === 'COMPLETE';
  const completionSupported = progress.lifecycle === 'COMPLETE' && progress.state === 'COMPLETE'
    && spec.criteria.length > 0
    && spec.criteria.every(criterion => selectTaskCriterion(criterion, progress.criteria).state === 'satisfied')
    && spec.steps.every(step => progress.steps.some(check => check.id === step.id && check.complete === true));

  return {
    presence: null,
    taskId: spec.taskId,
    lifecycle: lookup(claimsCompletion && !completionSupported ? {} : taskLifecycle, progress.lifecycle, ['view']),
    state: lookup(claimsCompletion && !completionSupported ? {} : taskStates, progress.state, actions),
  };
}

function selectRunUi(
  run: ExecutionTask,
  result: Result | undefined,
  resultsStatus: ResultsRead['status'],
  verifiedOutputs: ReadonlySet<string>,
  canCheckRecovery: boolean,
) {
  const runActions: Action[] = ['view'];
  if (canCheckRecovery && run.isLatestAttempt === true && run.confirmationId && !run.blockedByWorkflowRunId
    && ['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(run.status)) runActions.push('check-recovery');

  const outputs: readonly ResourceReference[] = run.status === 'SUCCEEDED' && run.outputCount > 0
    && result?.workflowRunId === run.workflowRunId && result.agentRunId === run.agentRunId
    && result.toolCallId === run.toolCallId && result.goalRevision === run.goalRevision
    && result.status === 'COMPLETE' && result.remoteOutputs.length === run.outputCount
    && result.remoteOutputs.every(isResourceReference) ? result.remoteOutputs : [];

  const outputAccess = outputs.map<{ reference: ResourceReference; actions: readonly Action[] }>(reference => ({
    reference,
    actions: verifiedOutputs.has(outputIdentity(reference)) ? outputActions : [],
  }));

  let publication: UiStatus;
  if (outputs.length > 0) {
    publication = display(null, ['已取得发布成果', 'success', '已同步此执行的精确成果引用。'], outputAccess.every(item => item.actions.length > 0) ? outputActions : []);
  } else if (run.status === 'SUCCEEDED' && run.outputCount > 0) {
    publication = lookup(resultsReadStates, resultsStatus, resultsStatus === 'loading' ? [] : ['refresh']);
  } else if (run.status === 'RUNNING' && run.stage === 'PUBLISHING') {
    publication = display(null, ['正在发布成果', 'info', 'Executor 正在完成发布链路。'], ['refresh']);
  } else {
    publication = display(null, ['发布状态未提供', 'neutral', '当前没有可核对的 Data 发布状态。']);
  }

  return {
    workflowRunId: run.workflowRunId,
    attempt: run.attempt ?? null,
    retryOfWorkflowRunId: run.retryOfWorkflowRunId ?? null,
    status: lookup(runStates, run.status, runActions),
    stage: run.stage === null ? null : lookup(runStages, run.stage),
    communication: run.communicationError === null ? null
      : display(run.communicationError, ['执行状态待核对', 'warning', '与执行服务通信异常，显示最近记录。'], ['refresh']),
    publication,
    scientificValidation: display(null, ['科学验证状态未提供', 'neutral', '当前运行未提供通用科学验证结论。']),
    outputs,
    outputAccess,
  };
}

const resultsReadStates = {
  'not-requested': ['未请求成果', 'neutral', '尚未读取此会话的成果列表。'],
  loading: ['正在读取成果', 'info', '正在读取此会话的成果列表。'],
  loaded: ['成果待同步', 'warning', '已读取成果列表，尚未取得与此运行匹配的完整引用。'],
  failed: ['成果读取失败', 'danger', '暂时无法读取成果列表，可重新查询。'],
} satisfies Record<ResultsRead['status'], Display>;

const outputActions = ['preview', 'download'] as const;

function outputIdentity(reference: ResourceReference) {
  return JSON.stringify([
    reference.kind, reference.service, reference.datasetId, reference.versionId,
    reference.semanticId, reference.objectId, reference.checksum,
    reference.format, reference.mediaType, reference.sizeBytes,
  ]);
}

function selectTurnUi(
  turn: Turn,
  history: History,
  results: ReadonlyMap<string | null, Result>,
  resultsStatus: ResultsRead['status'],
  verifiedOutputs: ReadonlySet<string>,
  actionable: boolean,
) {
  const current = turn.goalRevision === history.goalRevision;
  const controls = history.controlRequests?.filter(request => request.agentRunId === turn.agentRunId);
  const unresolvedStop = controls?.filter(request => request.status !== 'COMPLETE').at(-1);
  const stop = unresolvedStop ?? controls?.at(-1);
  const canAct = actionable && current && controls !== undefined && unresolvedStop === undefined;
  const turnActions: Action[] = ['view'];
  if (canAct && ['ROUTING', 'QUEUED', 'RUNNING', 'WAITING_CONFIRMATION', 'WAITING_EXECUTION'].includes(turn.status)) {
    turnActions.push('stop');
  }
  const canCheckRecovery = canAct
    && ['FAILED', 'CANCELLED', 'COMPLETE'].includes(turn.status)
    && !turn.confirmations.some(confirmation => confirmation.status === 'PENDING');

  return {
    goalRevision: turn.goalRevision,
    agentRunId: turn.agentRunId,
    status: lookup(turnStates, turn.status, turnActions),
    stop: stop ? lookup(controlStates, stop.status, ['refresh']) : null,
    activities: turn.activities.map(activity => ({
      activityId: activity.activityId, title: activity.title, summary: activity.summary,
      status: lookup(activityStates, activity.status, ['view']),
    })),
    confirmations: turn.confirmations.map(confirmation => {
      const enabled = canAct && canConfirm(confirmation, history, isBusy(history));
      return {
        confirmationId: confirmation.confirmationId,
        status: lookup(confirmationStates, confirmation.status, enabled ? ['view', 'edit', 'confirm', 'reject'] : ['view']),
        decision: confirmation.decisionState == null ? null : lookup(decisionStates, confirmation.decisionState, ['refresh']),
        replacesConfirmationId: confirmation.replacesConfirmationId ?? null,
      };
    }),
    workflowRuns: turn.executionTasks.map(run => selectRunUi(run, results.get(run.workflowRunId), resultsStatus, verifiedOutputs, canCheckRecovery)),
  };
}

export function selectSessionUi(input: SessionUiInput) {
  const metadata = input.metadata?.sessionId === input.sessionId ? input.metadata : null;
  const history = input.history?.sessionId === input.sessionId ? input.history : null;
  const writable = input.canWrite === true && input.freshness === 'current';
  const actions: Action[] = ['open'];
  if (writable && typeof metadata?.archived === 'boolean') {
    actions.push('rename', metadata.archived ? 'unarchive' : 'archive');
  }
  const session = typeof metadata?.archived === 'boolean'
    ? display(String(metadata.archived), metadata.archived
      ? ['已归档', 'neutral', '会话位于归档目录。']
      : ['未归档', 'info', '会话位于常用目录。'], actions)
    : display(null, ['归档状态未提供', 'neutral', '尚未取得此会话的目录元数据。'], actions);

  const resultsStatus = input.results?.status ?? 'not-requested';
  const items = input.results?.status === 'loaded' ? input.results.items : [];
  const results = new Map(items.map(result => [result.workflowRunId, result]));
  const verifiedOutputs = new Set(input.verifiedOutputs?.map(outputIdentity));
  return {
    sessionId: input.sessionId,
    session,
    freshness: history && input.freshness === 'current'
      ? null : display(null, ['状态待核对', 'warning', '请刷新以核对当前会话快照。'], ['refresh']),
    task: selectTaskUi(history?.task, writable),
    turns: history?.turns.map(turn => selectTurnUi(turn, history, results, resultsStatus, verifiedOutputs, writable)) ?? [],
  };
}
