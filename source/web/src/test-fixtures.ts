import { productUi } from './product-ui';
import type { Activity, Capabilities, ExecutionTask, History, ReplyPresentation, ResourceDetail, ResourceReference, Turn } from './types';

export function capabilitiesForProfile(
  profile: { productId: string; models: Record<string, unknown> },
  overrides: Partial<Capabilities> = {},
): Capabilities {
  return {
    productId: profile.productId,
    projectId: 'demo',
    orchestrationOwner: 'AGENT',
    observationTools: [],
    currentUser: { subject: 'test-user', displayName: '测试用户', roles: ['researcher'] },
    services: ['data', 'knowledge-next', 'executor'],
    models: Object.keys(profile.models).map(modelId => ({
      modelId,
      modelVersion: '1.0.0',
      modelChecksum: `sha256:${'1'.repeat(64)}`,
      workflowId: modelId,
      workflowVersion: '1.0.0',
      workflowChecksum: `sha256:${'2'.repeat(64)}`,
      description: '',
      tool: modelId,
      inputSchema: { type: 'object', properties: {}, required: [] },
      dataInputParameters: [],
      requiredInputs: [],
      defaultParameters: {},
      finalOutputs: [],
      applicabilityStatus: 'AVAILABLE',
      deadlineSeconds: 60,
    })),
    ...overrides,
  };
}

export function capabilitiesFixture(overrides: Partial<Capabilities> = {}): Capabilities {
  return capabilitiesForProfile(productUi(), overrides);
}

export const instant = '2026-10-09T01:00:00Z';
export function replyFixture(markdown: string): ReplyPresentation {
  return { conclusion: { markdown, sourceIndexes: [] }, details: [], executionSummary: null,
    unresolvedItems: [], sources: [] };
}
export function turnFixture(overrides: Partial<Turn> = {}): Turn {
  return { goalRevision: 1, agentRunId: 'agent-1', status: 'COMPLETE', errorCode: null,
    requestMode: 'DIALOGUE', workflowDraftId: null,
    requestId: null, requestedMode: null, routingSource: null, routingStatus: null, intentDecision: null, dialoguePolicy: null, resolvedMode: null,
    semanticIntent: null, taskAction: null, controlIntent: null, clarificationReason: null,
    clarificationRequired: false, correctsRequestId: null, routingMetrics: null,
    diagnostics: null, agentMetrics: null, reply: null, interactionOptions: [],
    confirmations: [], timing: { createdAt: instant, startedAt: instant, finishedAt: instant },
    progressUpdates: [], activities: [], plans: [], executionTasks: [], ...overrides };
}
export type HistoryFixture = Omit<Partial<History>, 'turns'> & { turns?: Partial<Turn>[] };
export function historyFixture(overrides: HistoryFixture = {}): History {
  return { sessionId: 'session-1', goalRevision: 1, items: [], nextCursor: null, eventCursor: 0, snapshotAt: instant,
    task: null, preparation: null, controlRequests: [], controlProposals: [], controlMessages: [],
    ...overrides, turns: overrides.turns?.map(turnFixture) ?? [turnFixture()] };
}
export function activityFixture(overrides: Partial<Activity> = {}): Activity {
  return { agentRunId: 'agent-1', goalRevision: 1, segmentId: 'segment-1', activityId: 'activity-1',
    kind: 'TOOL', purpose: 'OBSERVATION', status: 'RUNNING', toolName: 'list_resources', toolCallId: 'tool-1', errorCode: null,
    title: '获取所需信息', summary: '正在读取当前请求所需的系统信息。',
    createdAt: instant, startedAt: instant, finishedAt: null, ...overrides };
}
export function taskFixture(overrides: Partial<ExecutionTask> = {}): ExecutionTask {
  return { workflowRunId: 'workflow-1', agentRunId: 'agent-1', goalRevision: 1, confirmationId: 'confirmation-1',
    toolCallId: 'tool-1', position: 0, modelId: 'groundwater.summary', modelVersion: '1.0.0', workflowId: 'workflow.summary', workflowVersion: '1.0.0',
    status: 'RUNNING', stage: 'EXECUTING', createdAt: instant, startedAt: instant, finishedAt: null, durationMs: null,
    errorCode: null, communicationError: null, outputCount: 0, blockedByWorkflowRunId: null, ...overrides };
}
export function dataRef(overrides: Partial<ResourceReference> = {}): ResourceReference {
  return { kind: 'data_version', service: 'data-primary', datasetId: 'dataset-1', versionId: 'version-1', semanticId: 'semantics-1', objectId: 'object-1',
    checksum: `sha256:${'a'.repeat(64)}`, mediaType: 'application/json', format: 'json', sizeBytes: 42, ...overrides };
}
export function dataDetail(overrides: Partial<ResourceDetail> = {}): ResourceDetail {
  return { versionId: 'version-1', datasetId: 'dataset-1', publicationId: 'publication-1', semanticId: 'semantics-1', title: '测试数据',
    currentVersion: 'version-1', project: 'demo', tags: [], createdAt: instant,
    descriptor: { policy: 'FILE_ONLY' }, semantics: { description: '测试数据说明' }, inputVersions: [],
    objects: [{ objectId: 'object-1', size: 42, checksum: `sha256:${'a'.repeat(64)}`, role: 'raw', format: 'json', mediaType: 'application/json' }], ...overrides };
}

export function sessionResponseFixture(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: 'session-1', projectId: 'demo', goalRevision: 1,
    agentRunId: null, workflowDraftId: null, confirmation: null, agentOutput: null,
    workflowRunIds: [], remoteOutputs: [], agentSteps: [], controlProposals: [], task: null,
    ...overrides,
  };
}

export function sessionSummaryFixture(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: 'session-1', projectId: 'demo', title: '当前对话', createdAt: instant,
    goalRevision: 1, agentRunId: null, pendingAgentRunId: null, workflowDraftId: null,
    archived: false, ...overrides,
  };
}

export function sessionResultsFixture(
  items: Record<string, unknown>[] = [],
  overrides: Record<string, unknown> = {},
) {
  return { sessionId: 'session-1', projectId: 'demo', items, nextCursor: null, ...overrides };
}

export function messageReceiptFixture(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 'message-receipt.v1', request: {}, requestId: 'request-1',
    sessionId: 'session-1', goalRevision: 1, agentRunId: 'agent-1',
    requestedMode: 'AUTO', routingSource: 'router', routingStatus: 'RESOLVED',
    intentDecision: null, intent: null, semanticIntent: null, taskAction: null,
    controlIntent: null, clarificationReason: null, dialoguePolicy: null,
    resolvedMode: 'DIALOGUE', clarificationRequired: false, workflowDraftId: null,
    correctsRequestId: null, correctsAgentRunId: null, errorCode: null,
    ...overrides,
  };
}
