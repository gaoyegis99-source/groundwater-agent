import type { components as PublicApiComponents, paths as PublicApiPaths } from './generated/public-api-v12';

export type PublicApiPath = keyof PublicApiPaths;

export interface JsonSchemaProperty { type?: string; description?: string; default?: unknown; enum?: unknown[]; minimum?: number; maximum?: number }
export interface Capabilities { productId: string; projectId: string; orchestrationOwner?: 'AGENT'; observationTools?: { name: string; description: string; inputSchema: Record<string, unknown>; outputSchema: Record<string, unknown> }[]; services?: string[]; currentUser: { subject: string; displayName: string; roles: string[] }; models: { modelId: string; modelVersion?: string; modelChecksum?: string; workflowId?: string; workflowVersion?: string; workflowChecksum?: string; description: string; tool: string; inputSchema?: { type: string; properties?: Record<string, JsonSchemaProperty>; required?: string[] }; dataInputParameters?: string[]; requiredInputs?: string[]; defaultParameters?: Record<string, unknown>; finalOutputs?: { name?: string }[] | string[]; applicabilityStatus?: string; deadlineSeconds?: number }[] }
export interface SessionSummary {
  sessionId: string; title: string | null; createdAt: string | null; goalRevision: number;
  agentRunId: string | null; pendingAgentRunId: string | null;
  workflowDraftId: string | null;
  archived: boolean;
}
export interface Action {
  toolCallId: string; tool: string; modelId: string; modelVersion: string;
  workflowId: string; workflowVersion: string; arguments: Record<string, unknown>;
  inputReferences?: Record<string, ResourceReference>;
}
export interface Confirmation {
  confirmationId: string; goalRevision: number; status: string; agentRunId: string;
  reason: string; actions: Action[]; decision: string | null; decisionState: string | null;
  revision?: number; kind?: 'INITIAL' | 'EDITED' | 'RECOVERY'; replacesConfirmationId?: string | null;
  sourceWorkflowRunId?: string | null; requestId?: string | null; createdAt?: string;
}
export interface Session {
  sessionId: string; projectId: string; goalRevision: number; agentRunId: string | null;
  workflowDraftId: string | null;
  confirmation: Confirmation | null; agentOutput: {
    message: string; presentation: ReplyPresentation; unresolvedItems: string[]; interactionOptions: InteractionOption[];
  } | null;
  controlProposals?: ControlProposal[];
  preparation?: TaskPreparation | null;
}
export interface Message { messageId: number; goalRevision: number; role: string; content: string; inputReferences?: ResourceReference[] }
export type MessageMode = 'AUTO' | 'DIALOGUE' | 'WORKFLOW_DRAFT';
export type RequestedMode = MessageMode;
export type IntentDecision = 'DIALOGUE' | 'WORKFLOW_DRAFT' | 'CONTROL' | 'NEEDS_CLARIFICATION';
export type DialoguePolicy = 'READ_ONLY' | 'EXECUTION_PROPOSAL';
export type SemanticIntent = 'CONSULT' | 'COMPARE' | 'DESIGN' | 'EXECUTE_ON_DATA' | 'CREATE_WORKFLOW' | 'REVIEW_RESULT' | 'STATUS_QUERY' | 'UNSPECIFIED' | 'UNKNOWN';
export type TaskAction = 'NONE' | 'CREATE' | 'REVISE' | 'CONTINUE' | 'REVIEW';
export type ControlIntent = 'APPROVE_CONFIRMATION' | 'REJECT_CONFIRMATION' | 'STOP_ANALYSIS' | 'CANCEL_EXECUTION';
export type ClarificationReason = 'REQUEST_KIND_AMBIGUOUS' | 'TASK_CONFLICT' | 'CONTROL_TARGET_MISSING' | 'CONTROL_TARGET_AMBIGUOUS';
export interface IntentDecisionV4 {
  schemaVersion: 'intent-decision.v4'; responseKind: IntentDecision;
  semanticIntent: SemanticIntent; taskAction: TaskAction;
  dialoguePolicy: DialoguePolicy | null; controlIntent: ControlIntent | null;
  clarificationReason: ClarificationReason | null;
}
export interface ControlProposal {
  proposalId: string; sessionId: string; sourceRequestId: string;
  intent: ControlIntent; targetType: 'CONFIRMATION' | 'AGENT_RUN'; targetId: string;
  taskRevision: number | null; status: 'PENDING' | 'CONFIRMED' | 'DISMISSED' | 'STALE' | 'COMPLETE' | 'FAILED';
  decisionRequestId: string | null; createdAt: string; decidedAt: string | null;
}
export interface ControlMessage {
  requestId: string; message: string; responseKind: IntentDecision;
  semanticIntent: SemanticIntent;
  controlIntent: ControlIntent | null; clarificationReason: ClarificationReason | null;
  statusSnapshot: 'IDLE' | 'ANALYZING' | 'EXECUTING' | 'WAITING_CONFIRMATION' | 'RUNNING' | 'WAITING_EXECUTION' | 'COMPLETE' | 'FAILED' | 'CANCELLED';
  waitingReason: 'ANALYSIS_IN_PROGRESS' | 'EXECUTION_IN_PROGRESS' | 'CONFIRMATION_REQUIRED' | null;
  results: ResourceReference[];
  status: 'CONTROL_PENDING' | 'CONTROL_COMPLETE'; createdAt: string;
}
export type MessageRequest = PublicApiComponents['schemas']['kernel__MessageCreate'];
export interface RoutingMetrics {
  provider: string | null; model: string | null; requestCount: number; inputTokens: number | null;
  outputTokens: number | null; totalTokens: number | null; durationMs: number | null; unknownAttempts: number;
}
export interface AgentDiagnostics {
  reason: string;
  issues?: { path: string; type: string }[];
}
export interface AgentMetrics {
  requestCount: number; observationCallCount: number; inputTokens: number;
  outputTokens: number; totalTokens: number; durationMs: number;
}
export interface MessageReceipt {
  schemaVersion: 'message-request.v5';
  request: {
    requestId: string; message: string; requestedMode: RequestedMode;
    correctsRequestId: string | null;
    inputReferences: ResourceReference[];
  };
  requestId: string; sessionId: string;
  goalRevision: number; agentRunId: string; requestedMode: RequestedMode;
  routingSource: 'AUTO' | 'MODEL' | 'RULE' | 'EXPLICIT';
  routingStatus: 'ROUTING_REQUESTED' | 'ROUTING_RUNNING' | 'DECIDED' | 'START_REQUESTED' | 'CONTROL_PENDING' | 'CONTROL_COMPLETE' | 'FAILED';
  intentDecision: IntentDecision | null; dialoguePolicy: DialoguePolicy | null;
  intent: IntentDecisionV4 | null; semanticIntent: SemanticIntent | null;
  taskAction: TaskAction | null; controlIntent: ControlIntent | null;
  clarificationReason: ClarificationReason | null;
  resolvedMode: Exclude<RequestedMode, 'AUTO'> | null;
  clarificationRequired: boolean; errorCode: string | null;
  workflowDraftId: string | null; correctsRequestId: string | null;
}
export interface InteractionOption { label: string; description: string; message: string }
export interface ReplySection { markdown: string; sourceIndexes: number[] }
export interface ReplySource extends KnowledgeReference { title: string }
export interface ReplyPresentation {
  conclusion: ReplySection | null;
  details: ReplySection[];
  executionSummary: ReplySection | null;
  unresolvedItems: ReplySection[];
  sources: ReplySource[];
}
export interface Turn {
  goalRevision: number; agentRunId: string; status: string; errorCode: string | null;
  requestMode: string;
  workflowDraftId: string | null;
  requestId: string | null; requestedMode: RequestedMode | null;
  routingSource: MessageReceipt['routingSource'] | null;
  routingStatus: MessageReceipt['routingStatus'] | null; intentDecision: IntentDecision | null;
  dialoguePolicy: DialoguePolicy | null;
  semanticIntent: SemanticIntent | null; taskAction: TaskAction | null;
  controlIntent: ControlIntent | null; clarificationReason: ClarificationReason | null;
  resolvedMode: MessageReceipt['resolvedMode']; clarificationRequired: boolean;
  correctsRequestId: string | null; correctsAgentRunId?: string | null; routingMetrics: RoutingMetrics | null;
  diagnostics: AgentDiagnostics | null; agentMetrics: AgentMetrics | null;
  reply: ReplyPresentation | null;
  interactionOptions: InteractionOption[];
  confirmations: Confirmation[];
  timing: { createdAt: string | null; startedAt: string | null; finishedAt: string | null };
  progressUpdates?: ProgressUpdate[];
  activities: Activity[];
  plans: AgentPlan[];
  executionTasks: ExecutionTask[];
}
export interface History {
  task?: TaskContext | null;
  preparation?: TaskPreparation | null;
  controlRequests?: { requestId: string; agentRunId: string; status: 'REQUESTED' | 'COMPLETE' }[];
  controlProposals?: ControlProposal[]; controlMessages?: ControlMessage[];
  sessionId: string; goalRevision: number; items: Message[]; turns: Turn[]; nextCursor: number | null;
  eventCursor: number; snapshotAt: string;
}

export interface TaskPreparation {
  schemaVersion: 'task-preparation.v1'; preparationId: string; revision: number;
  sessionId: string; agentRunId: string; goalRevision: number;
  taskId: string | null; baseTaskRevision: number | null;
  status: 'READY' | 'BLOCKED'; entries: TaskPreparationEntry[];
  freshForCurrentTurn: boolean;
  disposition: 'ACTIVE' | 'WAITING_CONFIRMATION' | 'EXECUTING' | 'CONSUMED' | 'REJECTED' | 'FAILED' | 'ABANDONED' | 'STALE';
}
export interface TaskPreparationEntry {
  targetKind: 'EXECUTABLE_MODEL' | 'WORKFLOW_DRAFT'; targetId: string;
  targetVersion: string | null; selectionPolicy: 'PINNED' | 'CURRENT_REQUIRED'; status: 'READY' | 'BLOCKED';
  inputs: Record<string, ResourceReference>; knowledgeRefs: KnowledgeReference[];
  parameters: Record<string, unknown>;
  parameterSources: Record<string, string>;
  checks: TaskPreparationCheck[];
  blockingReasons: string[];
  blockingIssues?: { code: string; kind: 'MISSING_DATA' | 'MISSING_PARAMETER' | 'INVALID_PARAMETER' | 'UNRESOLVED_ITEM' | 'DATA_VERSION_NOT_CURRENT' | 'UNKNOWN'; input?: string; parameter?: string; path?: string; validator?: string }[];
}
export interface TaskPreparationCheck {
  kind: string; input?: string; parameter?: string;
  status: 'PASS' | 'UNKNOWN' | 'FAIL';
  versionId?: string; semanticId?: string; currentVersion?: string;
  fieldId?: string; fieldName?: string; actualType?: string;
  allowedTypes?: string[]; actualUnit?: string; expectedUnit?: string;
  relationship?: 'DECLARED' | 'EQUIVALENT' | 'CONVERTIBLE' | 'NOT_COMPARABLE';
  actualCrs?: string; expectedCrs?: string;
  crsRequirement?: 'valid' | 'projectedMetric'; crsUnit?: string;
  timezone?: string; timeRange?: string;
  requestedStart?: string; requestedEnd?: string;
  referenceField?: string; depthField?: string;
  referenceMeaning?: string; depthMeaning?: string; verticalDatum?: string;
}
export interface Activity {
  contextUsage?: import('./ContextUsageDetails').ContextUsage;
  title: string; summary: string | null; outputBytes?: number; outputTokens?: number;
  capability?: string; semanticRole?: string; templateKey?: string;
  outcomeCode?: string; facts?: Record<string, string | number | boolean | null>;
  agentRunId: string; goalRevision: number; segmentId: string; activityId: string;
  kind: 'SEGMENT' | 'MODEL_REQUEST' | 'TOOL';
  purpose: 'INITIAL' | 'RESUME_EXECUTION' | 'RESUME_REJECTION' | 'PROCESSING' | 'COMPACTION' | 'DECLARED_TOOL' | 'OBSERVATION' | 'CATALOG' | 'EXECUTION_FACT';
  status: 'QUEUED' | 'RUNNING' | 'COMPLETE' | 'FAILED' | 'INTERRUPTED';
  toolName: string | null; toolCallId: string | null; errorCode: string | null;
  createdAt: string; startedAt: string | null; finishedAt: string | null;
  repeatCount?: number;
}
export interface ProgressUpdate {
  progressId: string; agentRunId: string; workflowRunId: string | null;
  code: 'CASE_MATCHED' | 'INPUTS_VERIFIED' | 'PLAN_READY' | 'WAITING_CONFIRMATION' |
    'EXECUTION_QUEUED' | 'EXECUTION_RUNNING' | 'RESULTS_PUBLISHING' |
    'EXECUTION_SUCCEEDED' | 'EXECUTION_FAILED' | 'RECOVERY_SUCCEEDED';
  status: 'RUNNING' | 'COMPLETE' | 'FAILED' | 'INTERRUPTED';
  scope: 'TURN' | 'WORKFLOW';
  title: string; summary: string;
  facts: Record<string, string | number | boolean | null>;
  createdAt: string | null; startedAt: string | null; finishedAt: string | null;
}
export interface AgentPlan {
  revision: number; goal: string; reason: string;
  steps: { stepId: string; tool: string; purpose: string }[];
}
export interface ExecutionTask {
  workflowRunId: string; agentRunId: string; goalRevision: number; confirmationId: string;
  toolCallId: string; position: number; modelId: string; modelVersion: string;
  workflowId: string; workflowVersion: string; status: string;
  stage: 'QUEUED' | 'EXECUTING' | 'PUBLISHING' | null;
  createdAt: string; startedAt: string | null; finishedAt: string | null; durationMs: number | null;
  errorCode: string | null; communicationError: string | null; outputCount: number;
  blockedByWorkflowRunId: string | null;
  attempt?: number; retryOfWorkflowRunId?: string | null; isLatestAttempt?: boolean;
}
export interface KnowledgeReference { versionId: string; unitId: string; checksum: string }
export type KnowledgeEvidence = PublicApiComponents['schemas']['knowledge_next__EvidenceItem'];
export type KnowledgeEvidenceBundle = PublicApiComponents['schemas']['knowledge_next__EvidenceBundleV1'];
export interface ResourceReference {
  kind: 'data_version'; service: 'data-primary'; datasetId: string; versionId: string;
  semanticId: string; objectId: string; checksum: string; outputName?: string;
  mediaType: string; format: string; sizeBytes: number;
}
export interface WorkflowDefinition {
  schemaVersion: 'workflow.v6'; workflowId: string; version: string;
  inputs: { name: string; cardinality: 'one'; mediaTypes: string[]; formats: string[] }[];
  parameters: string[];
  nodes: {
    nodeId: string; operatorId: string; operatorVersion: string;
    inputs: Record<string, { workflowInput: string } | { nodeOutput: { nodeId: string; port: string } }>;
    parameters: Record<string, { workflowParameter: string }>;
  }[];
  outputs: { name: string; nodeOutput: { nodeId: string; port: string } }[];
}
export interface WorkflowDraftPayload {
  schemaVersion: 'workflow-draft.v1'; title: string; summary: string;
  workflow: WorkflowDefinition; workflowChecksum: string; operatorCatalogDigest: string;
  operators: { nodeId: string; operatorId: string; operatorVersion: string; implementationRevision: string; runtime: string; title: string }[];
  parameterValues: Record<string, unknown>;
  parameterSources: { name: string; source: 'USER' | 'DATA' | 'KNOWLEDGE' | 'OPERATOR_DEFAULT'; explanation: string }[];
  dataBindings: { inputName: string; dataRef: ResourceReference }[];
  missingInputs: { kind: 'DATA' | 'PARAMETER'; name: string; requiredBy: { nodeId: string; port: string }[] }[];
  knowledgeRefs: KnowledgeReference[]; unresolvedItems: string[]; limitations: string[];
  executionAllowed: false;
}
export interface WorkflowValidationDiagnostics {
  kind: 'WORKFLOW_VALIDATION'; code: string; path: string;
}
export interface WorkflowDraft {
  schemaVersion: 'workflow-draft-resource.v1'; draftId: string; sessionId: string;
  goalRevision: number; agentRunId: string; status: 'GENERATING' | 'COMPLETE' | 'FAILED' | 'CANCELLED' | 'SUPERSEDED';
  errorCode: string | null; payload: WorkflowDraftPayload | null;
  diagnostics: WorkflowValidationDiagnostics | null;
}
export interface Result {
  goalRevision: number; agentRunId: string; toolCallId: string; modelId: string;
  status: string; workflowRunId: string | null; remoteOutputs: ResourceReference[];
  attempt?: number | null;
}

export type DataQueryValue = string | number | boolean | null;
export interface DataQueryPage {
  fields: { fieldId: string; type: string }[];
  rows: DataQueryValue[][];
  nextOffset: number | null;
  truncated: boolean;
}
export interface DataQueryReceipt {
  queryId: string;
  coverage: { sourceRows: number | null; matchedRows: number; calculation: string };
  returnedRows: number;
  resultChecksum: string;
  truncated: boolean;
}
type GeneratedDataQueryResponse = PublicApiComponents['schemas']['data__QueryResponse'];
export type DataQueryResponse = Omit<GeneratedDataQueryResponse, 'error' | 'receipt' | 'result'> & {
  error: { code?: string } | null;
  receipt: DataQueryReceipt | null;
  result: DataQueryPage | null;
};
export interface DataPreviewField { fieldId: string; name: string; type: string }
export interface DataGeoJsonFeature {
  type: 'Feature'; id?: string | number;
  geometry: { type: string; coordinates?: unknown; geometries?: unknown[] } | null;
  properties: Record<string, unknown>;
}
export interface DataPreviewResult {
  kind: 'xlsx_table' | 'geopackage_layer';
  metadata: Record<string, unknown>;
  fields: DataPreviewField[];
  rows: DataQueryValue[][];
  features: DataGeoJsonFeature[];
  truncated: boolean;
  warnings: string[];
  nextOffset: number | null;
}
type GeneratedDataPreviewResponse = PublicApiComponents['schemas']['data__PreviewResponse'];
export type DataPreviewResponse = Omit<GeneratedDataPreviewResponse, 'error' | 'receipt' | 'result'> & {
  error: { code?: string } | null;
  receipt: { returnedRows?: number; truncated?: boolean } | null;
  result: DataPreviewResult | null;
};
export interface ResourceItem extends ResourceReference {
  familyId: string; name: string; descriptionPreview?: string | null;
  descriptionTruncated?: boolean;
  format: string; mediaType: string; sizeBytes: number;
}
export interface ResourceDetail {
  versionId: string; datasetId: string; publicationId: string; semanticId: string; currentVersion?: string; project?: string; tags?: string[]; title: string; createdAt?: string; inputVersions: string[];
  descriptor: { rowCount?: number | null; fields?: { fieldId: string; name: string; type: string; nullCount?: number }[]; policy?: string; provenance?: Record<string, string> };
  semantics: { description?: string; fields?: Record<string, { meaning?: string | null; unit?: string | null }>; grain?: string | null; timeRange?: string | null; timezone?: string | null; crs?: string | null; verticalDatum?: string | null; limitations?: string[] };
  objects: { objectId: string; size: number; checksum: string; role: 'raw' | 'query'; format: string; mediaType: string; sourceChecksum?: string }[];
}
export interface KnowledgeDocument {
  knowledgeId: string; title: string; summaryPreview: string; checksum: string;
  summaryTruncated?: boolean; chunkCount: number; freshnessStatus: string; sourceIds?: string[];
}
export interface ChunkPreview {
  chunkIndex: number; checksum: string; preview: string; previewTruncated?: boolean;
  freshnessStatus: string; sourceIds?: string[];
}
export interface KnowledgeChunk extends Omit<KnowledgeReference, 'checksum'> {
  checksum: string | null;
  status: 'OK' | 'STALE'; title: string | null; content: string | null; freshnessStatus: string | null;
  sources: {
    sourceId: string; title: string; url: string | null; publisher: string | null;
    kind: 'OFFICIAL_STANDARD' | 'OFFICIAL_GUIDANCE' | 'OFFICIAL_POLICY' | 'DOMAIN_DEFINITION' | 'DEMO_ASSUMPTION' | 'PROJECT_TEMPLATE';
    version: string | null; publishedAt: string | null; accessedAt: string | null;
    reviewAfter: string; validThrough: string | null; lifecycleStatus: 'ACTIVE' | 'WITHDRAWN';
  }[];
}
export interface TablePreview {
  status: string; inspection: null | {
    fieldCount: number; nextColumnOffset: number | null; rowCount: number | null;
    fields: { index: number; name: string; physicalType: string }[];
    sampleRows: { text: string | null; truncated: boolean }[][];
  };
}
export interface DebugEntry {
  time: string; kind: 'HTTP' | 'SSE' | 'STATE' | 'METRIC'; name: string; status?: number; eventId?: string;
  elapsedMs?: number; requestId?: string | null; code?: string;
}

export interface TaskContext {
  schemaVersion: 'task-context.v1';
  spec: {
    taskId: string; sessionId?: string; revision: number; objective: string;
    constraints: { id: string; text: string; interpretation?: string | null }[];
    criteria: { id: string; text: string; interpretation?: string | null; kind: 'REPLY' | 'RESULT' | 'DRAFT' | 'USER_REVIEW' }[];
    unresolvedItems: string[]; suggestions: string[];
    steps: { id: string; title: string; dependsOn: string[]; criterionIds: string[]; gaps: string[] }[];
  };
  progress: {
    lifecycle: 'ACTIVE' | 'COMPLETE'; state: string;
    criteria: { id: string; satisfied: boolean; evidence?: { workflowDraftId?: string } | null }[];
    steps: { id: string; complete: boolean }[];
    results: ResourceReference[];
    blockingReasons?: string[]; metrics: { totalTokens: number; durationMs: number; requestCount?: number; observationCallCount?: number };
  };
}
