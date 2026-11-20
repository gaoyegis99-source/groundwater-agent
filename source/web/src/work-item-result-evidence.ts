import { ApiError, callApi, projectQuery, readDataVersion, readDataVersionSemantic } from './api';
import { isResourceReference, resourceItemFromDetail } from './resource-item';
import type { components } from './generated/public-api-v12';
import type { BusinessCapability, BusinessCapabilityProfile, ProductUiProfile } from './product-ui';
import type { Action, BusinessAssetReference, ResourceDetail, ResourceItem, ResourceReference, TaskContext, WorkItemDetail } from './types';

export type WorkItemResultReference = Extract<BusinessAssetReference, { kind: 'RESULT' }>;
export type WorkItemNamedOutput = components['schemas']['kernel__WorkItemResultDataReference'];
export type WorkItemRun = components['schemas']['kernel__WorkflowRunResponse'];
export type WorkItemRunLink = WorkItemDetail['workflowRuns'][number];
export type WorkItemPublication = components['schemas']['kernel__WorkflowPublicationResponse'];

type ExpectedOutput = BusinessCapabilityProfile['outputs'][number];
export type WorkItemOutputRow = {
  name: string;
  output?: WorkItemNamedOutput;
  expected?: ExpectedOutput;
  state: 'PRESENT' | 'MISSING' | 'INVALID' | 'DUPLICATE' | 'FORMAT_MISMATCH' | 'UNEXPECTED' | 'UNVERIFIED';
};

export type WorkItemResultEvidence = {
  reference: WorkItemResultReference;
  version: ResourceDetail;
  item: ResourceItem;
  publicationSource: 'KERNEL' | 'DATA';
};

export type WorkItemProfileEvidence =
  | { state: 'VERIFIED'; profile: BusinessCapabilityProfile; capability: BusinessCapability }
  | { state: 'UNVERIFIED' | 'MISMATCH' };

export type WorkItemSessionEvidence = {
  action: Action | null;
  task: {
    spec: Pick<TaskContext['spec'], 'sessionId' | 'taskId' | 'revision' | 'objective'>;
    progress: Pick<TaskContext['progress'], 'lifecycle' | 'state'>;
  } | null;
  taskAdoption: 'ADOPTED' | 'NOT_ADOPTED' | 'UNVERIFIED';
};

const checksumPattern = /^sha256:[0-9a-f]{64}$/;
const dataIdentity = [
  'kind', 'service', 'datasetId', 'versionId', 'semanticId', 'objectId',
  'checksum', 'mediaType', 'format', 'sizeBytes', 'outputName',
] as const satisfies readonly (keyof ResourceReference)[];

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.flatMap(item => {
    const entry = record(item);
    return entry ? [entry] : [];
  }) : [];
}

function identityMismatch() {
  return new ApiError(409, 'WORK_ITEM_REFERENCE_MISMATCH');
}

function validSegment(value: unknown): value is string {
  return typeof value === 'string' && !!value.trim() && value !== '.' && value !== '..';
}

function safeSegment(value: string) {
  if (!validSegment(value)) throw identityMismatch();
  return encodeURIComponent(value);
}

function matchesRunLink(run: WorkItemRun, link: WorkItemRunLink) {
  return run.runId === link.runId && run.agentRunId === link.agentRunId
    && matchesIdentity(link.workflowRef, { id: run.workflowId, version: run.workflowVersion, checksum: run.workflowChecksum });
}

async function readRun(runId: string, project: string, signal: AbortSignal) {
  const path = `/workflow-runs/${safeSegment(runId)}?${projectQuery(project)}`;
  const run = await callApi('kernel_get_workflow_run', path, undefined, signal);
  if (run.runId !== runId || run.projectId !== project) throw identityMismatch();
  return run;
}

export async function readWorkItemRun(link: WorkItemRunLink, project: string, signal: AbortSignal) {
  const run = await readRun(link.runId, project, signal);
  if (!matchesRunLink(run, link)) throw identityMismatch();
  return run;
}

export async function readWorkItemPublication(runId: string, project: string, signal: AbortSignal) {
  const path = `/workflow-runs/${safeSegment(runId)}/publication?${projectQuery(project)}`;
  const value = await callApi('kernel_get_workflow_run_publication', path, undefined, signal);
  if (value.workflowRunId !== runId || (value.publicationId === null) !== (value.state === null)
    || (value.publicationId !== null && !validSegment(value.publicationId))) throw identityMismatch();
  return value;
}

function validDataReference(value: unknown): value is ResourceReference {
  return isResourceReference(value) && [value.datasetId, value.versionId, value.semanticId, value.objectId, value.mediaType, value.format].every(validSegment)
    && checksumPattern.test(value.checksum) && Number.isSafeInteger(value.sizeBytes) && value.sizeBytes >= 0;
}

function validOutputName(name: unknown): name is string {
  return typeof name === 'string' && !!name.trim() && Array.from(name).length <= 256;
}

function isNamedOutput(value: unknown): value is WorkItemNamedOutput {
  return validDataReference(value) && validOutputName(value.outputName) && Object.keys(value).length === dataIdentity.length;
}

export function sameDataIdentity(left: ResourceReference, right: ResourceReference) {
  return dataIdentity.every(field => left[field] === right[field]);
}

export function sameWorkItemResult(left: WorkItemResultReference, right: WorkItemResultReference) {
  return left.runId === right.runId && left.outputName === right.outputName
    && left.publicationId === right.publicationId && sameDataIdentity(left.dataRef, right.dataRef);
}

export function workItemResultReference(runId: string, publicationId: string, output: WorkItemNamedOutput): WorkItemResultReference {
  return { kind: 'RESULT', runId, publicationId, outputName: output.outputName, dataRef: output };
}

export function workItemOutputRows(run: WorkItemRun, profile?: BusinessCapabilityProfile): WorkItemOutputRow[] {
  const outputs = new Map<string, WorkItemRun['outputs']>();
  const versionNames = new Map<string, Set<string>>();
  for (const output of run.outputs) {
    const name = output.outputName;
    if (!validOutputName(name)) continue;
    const rows = outputs.get(name);
    if (rows) rows.push(output);
    else outputs.set(name, [output]);
    if (profile && isNamedOutput(output)) {
      const names = versionNames.get(output.versionId) ?? new Set<string>();
      names.add(name);
      versionNames.set(output.versionId, names);
    }
  }
  const expectedByName = new Map(profile?.outputs.map(output => [output.name, output]));
  const names = new Set([...expectedByName.keys(), ...outputs.keys()]);
  return Array.from(names, name => {
    const expected = expectedByName.get(name);
    const candidates = outputs.get(name) ?? [];
    if (candidates.length === 0) return { name, expected, state: 'MISSING' };
    if (candidates.length > 1) return { name, expected, state: 'DUPLICATE' };
    const [output] = candidates;
    if (!isNamedOutput(output)) return { name, expected, state: 'INVALID' };
    if (!profile) return { name, output, state: 'UNVERIFIED' };
    if (!expected) return { name, output, state: 'UNEXPECTED' };
    if ((versionNames.get(output.versionId)?.size ?? 0) > 1) return { name, expected, state: 'INVALID' };
    const formatMatches = output.format === expected.format && (expected.mediaType === undefined || output.mediaType === expected.mediaType);
    return { name, output, expected, state: formatMatches ? 'PRESENT' : 'FORMAT_MISMATCH' };
  });
}

export async function readWorkItemOutput(
  output: WorkItemNamedOutput, project: string, publicationId: string, signal: AbortSignal,
): Promise<ResourceDetail> {
  if (!isNamedOutput(output) || !validSegment(publicationId)) throw identityMismatch();
  const version = await readDataVersion(output.versionId, signal);
  if (version.project !== project || version.publicationId !== publicationId || version.datasetId !== output.datasetId) throw identityMismatch();
  const matches = version.objects.filter(object => object.objectId === output.objectId);
  const object = matches[0];
  if (matches.length !== 1 || object.role !== 'raw' || object.checksum !== output.checksum || object.format !== output.format
    || object.mediaType !== output.mediaType || object.size !== output.sizeBytes) throw identityMismatch();
  return readDataVersionSemantic(version, output.semanticId, signal);
}

export async function readWorkItemResult(
  reference: WorkItemResultReference, project: string, signal: AbortSignal, link?: WorkItemRunLink,
  availableAsset?: WorkItemDetail['assets'][number],
): Promise<WorkItemResultEvidence> {
  const { runId, outputName, dataRef, publicationId } = reference;
  if (!isNamedOutput(dataRef) || dataRef.outputName !== outputName || !validSegment(publicationId)
    || (link && link.runId !== runId)) throw identityMismatch();
  const run = link ? await readWorkItemRun(link, project, signal) : await readRun(runId, project, signal);
  const matches = run.outputs.filter(output => output.outputName === outputName);
  const output = matches[0];
  if (run.status !== 'SUCCEEDED' || matches.length !== 1 || !isNamedOutput(output)
    || !sameDataIdentity(output, dataRef)) throw identityMismatch();

  // Only a freshly available WorkItem link can corroborate historical publication when the Executor target has changed.
  const assetReference = availableAsset?.reference;
  const canVerifyHistorical = link && availableAsset?.state === 'AVAILABLE' && assetReference?.kind === 'RESULT'
    && sameWorkItemResult(reference, assetReference);
  const publication = await readWorkItemPublication(runId, project, signal).catch(error => {
    if (canVerifyHistorical && error instanceof ApiError && error.code === 'WORKFLOW_PUBLICATION_UNAVAILABLE') return null;
    throw error;
  });
  const fromKernel = publication?.state === 'PUBLISHED';
  if (fromKernel ? publication.publicationId !== publicationId : !canVerifyHistorical || publication?.state != null) {
    throw new ApiError(409, 'WORK_ITEM_RESULT_NOT_PUBLISHED');
  }
  const version = await readWorkItemOutput(output, project, publicationId, signal);
  const object = version.objects.find(object => object.objectId === output.objectId);
  if (!object) throw identityMismatch();
  return { reference, version, item: { ...resourceItemFromDetail(version, object), outputName }, publicationSource: fromKernel ? 'KERNEL' : 'DATA' };
}

type Ref = BusinessCapabilityProfile['profileRef'];

function matchesIdentity(value: unknown, expected: Ref) {
  const identity = record(value);
  return identity?.id === expected.id && identity.version === expected.version && identity.checksum === expected.checksum;
}

function validIdentity(value: unknown): value is Ref {
  const identity = record(value);
  return Boolean(identity && validSegment(identity.id) && validSegment(identity.version)
    && typeof identity.checksum === 'string' && checksumPattern.test(identity.checksum));
}

export async function readWorkItemRunProfile(
  link: WorkItemRunLink, run: WorkItemRun, product: ProductUiProfile, signal: AbortSignal, roles?: readonly string[],
): Promise<WorkItemProfileEvidence> {
  if (!matchesRunLink(run, link)) throw identityMismatch();
  const tracePath = `/agent-runs/${safeSegment(link.agentRunId)}/trace?${projectQuery(run.projectId)}`;
  const trace = await callApi('kernel_get_agent_trace', tracePath, undefined, signal);
  if (trace.agentRunId !== link.agentRunId) throw identityMismatch();
  if (trace.trace?.businessCapability === undefined) return { state: 'UNVERIFIED' };
  const selection = record(trace.trace.businessCapability);
  if (!selection
    || ![selection.capabilityRef, selection.profileRef, selection.resultContractRef].every(validIdentity)
    || !validIdentity(selection.modelRef) || !validIdentity(selection.workflowRef)) return { state: 'MISMATCH' };
  // The trace records the AgentRun selection. A separate model dispatched in the same turn has no verified profile here.
  if (selection.modelRef.id !== run.modelId || selection.workflowRef.id !== run.workflowId) return { state: 'UNVERIFIED' };
  if (!matchesIdentity(selection.modelRef, { id: run.modelId, version: run.modelVersion, checksum: run.modelChecksum })
    || !matchesIdentity(selection.workflowRef, link.workflowRef)) return { state: 'MISMATCH' };

  const capabilities = product.businessCapabilities?.filter(capability => matchesIdentity(selection.capabilityRef, capability.ref)) ?? [];
  const candidates = capabilities.flatMap(capability => capability.profiles
    .filter(profile => matchesIdentity(selection.profileRef, profile.profileRef))
    .map(profile => ({ capability, profile })));
  if (candidates.length === 0) return { state: 'UNVERIFIED' };
  if (candidates.length !== 1) return { state: 'MISMATCH' };
  const [{ capability, profile }] = candidates;
  if (roles && !roles.some(role => capability.roles.some(value => value === role) && profile.roles.some(value => value === role))) {
    return { state: 'UNVERIFIED' };
  }
  if (!matchesIdentity(selection.modelRef, profile.modelRef) || !matchesIdentity(selection.workflowRef, profile.workflowRef)
    || !matchesIdentity(selection.resultContractRef, profile.resultContractRef)) return { state: 'MISMATCH' };
  return { state: 'VERIFIED', profile, capability };
}

async function readAgentGoal(link: WorkItemRunLink, project: string, signal: AbortSignal) {
  const path = `/agent-runs/${safeSegment(link.agentRunId)}?${projectQuery(project)}`;
  const agent = await callApi('kernel_get_agent_run', path, undefined, signal);
  if (agent.agentRunId !== link.agentRunId || agent.projectId !== project || agent.sessionId !== link.sessionId) throw identityMismatch();
  return agent.goalRevision;
}

function frozenAction(turn: Record<string, unknown>, run: WorkItemRun, goalRevision: number): Action | null {
  const dispatches = records(turn.executionTasks).filter(task => task.workflowRunId === run.runId
    && task.agentRunId === run.agentRunId && task.toolCallId === run.toolCallId && task.goalRevision === goalRevision);
  if (dispatches.length !== 1 || !validSegment(dispatches[0].confirmationId)) return null;
  const confirmations = records(turn.confirmations).filter(confirmation => confirmation.confirmationId === dispatches[0].confirmationId
    && confirmation.agentRunId === run.agentRunId && confirmation.goalRevision === goalRevision && confirmation.status === 'CONFIRMED');
  if (confirmations.length !== 1) return null;
  const actions = records(confirmations[0].actions).filter(action => action.toolCallId === run.toolCallId);
  const [action] = actions;
  if (actions.length !== 1 || action.modelId !== run.modelId || action.modelVersion !== run.modelVersion
    || action.modelChecksum !== run.modelChecksum || action.workflowId !== run.workflowId
    || action.workflowVersion !== run.workflowVersion || action.workflowChecksum !== run.workflowChecksum
    || typeof action.tool !== 'string') return null;
  const args = record(action.arguments);
  const refs = record(action.inputReferences);
  if (!args || !refs || !Object.values(refs).every(validDataReference)) return null;
  return {
    toolCallId: run.toolCallId, tool: action.tool, modelId: run.modelId, modelVersion: run.modelVersion,
    workflowId: run.workflowId, workflowVersion: run.workflowVersion, workflowChecksum: run.workflowChecksum,
    arguments: args, inputReferences: refs as Record<string, ResourceReference>,
  };
}

function exactTask(
  value: Record<string, unknown> | null, link: WorkItemRunLink, run: WorkItemRun, reference?: WorkItemResultReference,
): Pick<WorkItemSessionEvidence, 'task' | 'taskAdoption'> {
  const unverified = { task: null, taskAdoption: 'UNVERIFIED' } as const;
  if (run.status !== 'SUCCEEDED' || !reference || reference.runId !== run.runId || reference.outputName !== reference.dataRef.outputName
    || !isNamedOutput(reference.dataRef)) return unverified;
  const outputs = run.outputs.filter(output => output.outputName === reference.outputName);
  if (outputs.length !== 1 || !isNamedOutput(outputs[0]) || !sameDataIdentity(outputs[0], reference.dataRef)) return unverified;
  if (value === null) return { task: null, taskAdoption: 'NOT_ADOPTED' };
  const spec = record(value.spec);
  const progress = record(value.progress);
  if (value.schemaVersion !== 'task-context.v1' || !spec || spec.sessionId !== link.sessionId || !progress
    || typeof spec.taskId !== 'string' || typeof spec.objective !== 'string'
    || typeof spec.revision !== 'number' || !Number.isSafeInteger(spec.revision) || spec.revision < 1
    || !(progress.lifecycle === 'COMPLETE' || progress.lifecycle === 'ACTIVE') || typeof progress.state !== 'string'
    || !Array.isArray(spec.criteria) || !Array.isArray(progress.criteria) || !Array.isArray(progress.results)) return unverified;
  const criteria = new Set(records(spec.criteria).filter(item => typeof item.id === 'string' && item.kind === 'RESULT' && item.modelId === run.modelId
    && Array.isArray(item.outputNames) && item.outputNames.includes(reference.outputName)).map(item => item.id));
  const adopted = records(progress.criteria).some(item => {
    const evidence = record(item.evidence);
    return criteria.has(item.id) && item.satisfied === true && evidence?.agentRunId === run.agentRunId && evidence.workflowRunId === run.runId
      && (evidence.criterionId === undefined || evidence.criterionId === item.id);
  }) && progress.results.some(item => isNamedOutput(item) && sameDataIdentity(item, reference.dataRef));
  if (!adopted) return { task: null, taskAdoption: 'NOT_ADOPTED' };
  return {
    task: {
      spec: { sessionId: spec.sessionId, taskId: spec.taskId, revision: spec.revision, objective: spec.objective },
      progress: { lifecycle: progress.lifecycle, state: progress.state },
    },
    taskAdoption: 'ADOPTED',
  };
}

export async function readWorkItemSessionEvidence(
  link: WorkItemRunLink, run: WorkItemRun, signal: AbortSignal, reference?: WorkItemResultReference, goalRevision?: number,
): Promise<WorkItemSessionEvidence> {
  if (!matchesRunLink(run, link)) throw identityMismatch();
  const revision = goalRevision ?? await readAgentGoal(link, run.projectId, signal);
  if (!Number.isSafeInteger(revision) || revision < 1) throw identityMismatch();
  const path = `/sessions/${safeSegment(link.sessionId)}/messages?goalRevision=${revision}`;
  const history = await callApi('kernel_get_messages', path, undefined, signal);
  if (history.sessionId !== link.sessionId || history.goalRevision < revision) throw identityMismatch();
  const turns = history.turns.filter(turn => turn.goalRevision === revision && turn.agentRunId === link.agentRunId);
  const action = turns.length === 1 ? frozenAction(turns[0], run, revision) : null;
  return { action, ...exactTask(history.task, link, run, reference) };
}
