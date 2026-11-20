import { expect, it, vi } from 'vitest';
import type { BusinessCapability, BusinessCapabilityProfile, ProductUiProfile } from './product-ui';
import {
  readWorkItemPublication, readWorkItemResult, readWorkItemRun, readWorkItemRunProfile,
  readWorkItemSessionEvidence, workItemOutputRows,
} from './work-item-result-evidence';
import type { WorkItemNamedOutput, WorkItemResultReference, WorkItemRun } from './work-item-result-evidence';

const digest = `sha256:${'a'.repeat(64)}`;
const olderDigest = `sha256:${'b'.repeat(64)}`;
const time = '2026-11-20T00:00:00Z';
const modelRef = { id: 'model', version: '2.0.0', checksum: digest };
const workflowRef = { id: 'workflow', version: '1.0.0', checksum: digest };
const profileRef = { id: 'profile', version: '1.0.0', checksum: digest };
const resultContractRef = { id: 'results', version: '1.0.0', checksum: digest };
const capabilityRef = { id: 'capability', version: '1.0.0', checksum: digest };
const link = { runId: 'run', agentRunId: 'agent', sessionId: 'session', workflowRef };
const output: WorkItemNamedOutput = {
  kind: 'data_version', service: 'data-primary', datasetId: 'dataset', versionId: 'version',
  semanticId: 'semantic-old', objectId: 'object', mediaType: 'text/csv', format: 'csv',
  sizeBytes: 10, checksum: digest, outputName: 'primary',
};
const reference: WorkItemResultReference = {
  kind: 'RESULT', runId: 'run', publicationId: 'publication', outputName: 'primary', dataRef: output,
};
const asset = { state: 'AVAILABLE' as const, linkId: 'link', linkedAt: time, reference };
const run: WorkItemRun = {
  runId: 'run', agentRunId: 'agent', toolCallId: 'call', position: 0, projectId: 'project',
  modelId: 'model', modelVersion: '2.0.0', modelChecksum: digest,
  workflowId: 'workflow', workflowVersion: '1.0.0', workflowChecksum: digest,
  executionService: 'executor-primary', executorId: 'executor', arguments: {}, remoteJobId: 'job',
  status: 'SUCCEEDED', outputs: [output], error: null, createdAt: time, startedAt: time, finishedAt: time, durationMs: 100,
};
const publication = { workflowRunId: 'run', publicationId: 'publication', state: 'PUBLISHED' };
const raw = { role: 'raw', objectId: 'object', mediaType: 'text/csv', format: 'csv', checksum: digest, size: 10 };
const version = {
  project: 'project', datasetId: 'dataset', versionId: 'version', currentVersion: 'latest',
  publicationId: 'publication', semanticId: 'semantic-current', createdAt: time, descriptor: {}, inputVersions: [],
  tags: [], title: 'Exact result', semantics: {}, objects: [raw],
};
const semanticRevision = {
  versionId: 'version', semanticId: 'semantic-old', body: {}, createdAt: time, actor: {},
};
const expected: BusinessCapabilityProfile['outputs'][number] = {
  name: 'primary', role: 'PRIMARY_RESULT', meaning: 'Main result', format: 'csv',
};
const profile: BusinessCapabilityProfile = {
  profileRef, modelRef, workflowRef, resultContractRef, label: 'Frozen profile', roles: ['researcher'],
  inputs: [], outputs: [expected], limitations: ['Known limitation'],
};
const capability: BusinessCapability = {
  ref: capabilityRef, title: 'Capability', description: '', scenario: '', tier: 'CORE',
  defaultVisible: true, roles: ['researcher'], prompt: '', requiredServices: [],
  validationStatus: 'TECHNICALLY_VALIDATED', profiles: [profile],
};
const product = { businessCapabilities: [capability] } as ProductUiProfile;
const publicationPath = '/api/v12/workflow-runs/run/publication';
const versionPath = '/api/v12/data/versions/version';
const semanticPath = versionPath + '/semantics/semantic-old';
const tracePath = '/api/v12/agent-runs/agent/trace';
const historyPath = '/api/v12/sessions/session/messages';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

function fetchRoutes(overrides: Record<string, unknown> = {}) {
  const routes = new Map(Object.entries({
    '/api/v12/workflow-runs/run': run, [publicationPath]: publication, [versionPath]: version,
    [semanticPath]: semanticRevision, ...overrides,
  }));
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const path = new URL(String(input), 'http://localhost').pathname;
    const value = routes.get(path);
    return value instanceof Response ? value : value === undefined ? json({}, 404) : json(value);
  });
}

const signal = () => new AbortController().signal;

it.each([
  { runId: 'another' }, { agentRunId: 'another' }, { projectId: 'another' },
  { workflowId: 'another' }, { workflowVersion: 'older' }, { workflowChecksum: olderDigest },
])('rejects a WorkflowRun with inconsistent link identity %o', async mismatch => {
  fetchRoutes({ '/api/v12/workflow-runs/run': { ...run, ...mismatch } });
  await expect(readWorkItemRun(link, 'project', signal())).rejects.toMatchObject({ code: 'WORK_ITEM_REFERENCE_MISMATCH' });
});

it('reads executor publication separately and rejects a publication claimed by another run', async () => {
  fetchRoutes({ [publicationPath]: { ...publication, workflowRunId: 'another' } });
  await expect(readWorkItemRun(link, 'project', signal())).resolves.toMatchObject({ status: 'SUCCEEDED' });
  await expect(readWorkItemPublication('run', 'project', signal())).rejects.toMatchObject({ code: 'WORK_ITEM_REFERENCE_MISMATCH' });
});

it('returns the exact historical semantic revision for a published result', async () => {
  fetchRoutes();
  await expect(readWorkItemResult(reference, 'project', signal(), link)).resolves.toMatchObject({
    reference, version: { semanticId: 'semantic-old' }, item: output, publicationSource: 'KERNEL',
  });
});

it.each([
  ['a different output name', { ...reference, outputName: 'different' }, {}],
  ['a changed output identity', { ...reference, dataRef: { ...output, checksum: olderDigest } }, {}],
  ['a duplicate output name', reference, { '/api/v12/workflow-runs/run': { ...run, outputs: [output, output] } }],
  ['a different project', reference, { [versionPath]: { ...version, project: 'other' } }],
  ['a different dataset', reference, { [versionPath]: { ...version, datasetId: 'other' } }],
  ['a different publication', reference, { [versionPath]: { ...version, publicationId: 'other' } }],
  ['a different raw object checksum', reference, { [versionPath]: { ...version, objects: [{ ...raw, checksum: olderDigest }] } }],
  ['a query object', reference, { [versionPath]: { ...version, objects: [{ ...raw, role: 'query' }] } }],
  ['a different semantic revision', reference, { [semanticPath]: { ...semanticRevision, semanticId: 'other' } }],
])('rejects a result with %s', async (_case, pin, overrides) => {
  fetchRoutes(overrides);
  await expect(readWorkItemResult(pin, 'project', signal(), link)).rejects.toMatchObject({ status: 409 });
});

it('propagates access revocation for an exact result', async () => {
  fetchRoutes({ [versionPath]: json({}, 403) });
  await expect(readWorkItemResult(reference, 'project', signal(), link, asset)).rejects.toMatchObject({ status: 403 });
});

it('checks the exact Data-backed historical pin when the executor target no longer recognizes the run', async () => {
  fetchRoutes({ [publicationPath]: { workflowRunId: 'run', publicationId: null, state: null } });
  await expect(readWorkItemResult(reference, 'project', signal(), link, asset)).resolves.toMatchObject({
    reference, publicationSource: 'DATA',
  });
  await expect(readWorkItemResult(reference, 'project', signal(), link)).rejects.toMatchObject({ status: 409 });
});

it('uses a current exact WorkItem asset when the Executor specifically reports unavailable publication', async () => {
  fetchRoutes({ [publicationPath]: json({ code: 'WORKFLOW_PUBLICATION_UNAVAILABLE' }, 503) });
  await expect(readWorkItemResult(reference, 'project', signal(), link, asset)).resolves.toMatchObject({ publicationSource: 'DATA' });
  await expect(readWorkItemResult(reference, 'project', signal(), link, { ...asset, state: 'UNAVAILABLE', reference: null }))
    .rejects.toMatchObject({ status: 503 });
});

it('rejects inconsistent Executor publication identity for a readable historical pin', async () => {
  fetchRoutes({ [publicationPath]: { ...publication, publicationId: 'different' } });
  await expect(readWorkItemResult(reference, 'project', signal(), link, asset)).rejects.toMatchObject({ status: 409 });
});

it.each(['OPEN', 'READY', 'FAILED', 'ABORTED'])('rejects incomplete or failed publication %s even for a pinned result', async state => {
  fetchRoutes({ [publicationPath]: { ...publication, state } });
  await expect(readWorkItemResult(reference, 'project', signal(), link, asset)).rejects.toMatchObject({ status: 409 });
});

it('reports absent, invalid, duplicate, unexpected, and format-mismatched contract outputs distinctly', () => {
  const names = ['primary', 'missing', 'invalid', 'duplicate', 'matching'];
  const fullProfile = { ...profile, outputs: names.map(name => ({ ...expected, name })) };
  const rows = workItemOutputRows({ ...run, outputs: [
    { ...output, format: 'json' }, { outputName: 'invalid' }, { ...output, outputName: 'duplicate', versionId: 'duplicate-version' },
    { outputName: 'duplicate' }, { ...output, outputName: 'matching', versionId: 'matching-version' },
    { ...output, outputName: 'extra', versionId: 'extra-version' },
  ] }, fullProfile);
  expect(rows.map(({ name, state }) => [name, state])).toEqual([
    ['primary', 'FORMAT_MISMATCH'], ['missing', 'MISSING'], ['invalid', 'INVALID'],
    ['duplicate', 'DUPLICATE'], ['matching', 'PRESENT'], ['extra', 'UNEXPECTED'],
  ]);
  expect(workItemOutputRows(run)).toMatchObject([{ name: 'primary', state: 'UNVERIFIED' }]);
});

it('marks exact contract outputs invalid when one Data version is reported under different names', () => {
  const other = { ...output, outputName: 'other' };
  expect(workItemOutputRows({ ...run, outputs: [output, other] }, { ...profile, outputs: [expected, { ...expected, name: 'other' }] }))
    .toMatchObject([{ name: 'primary', state: 'INVALID' }, { name: 'other', state: 'INVALID' }]);
});

it('classifies a declared media type mismatch even when the output format matches', () => {
  const declared = { ...expected, mediaType: 'application/octet-stream' };
  expect(workItemOutputRows(run, { ...profile, outputs: [declared] })).toMatchObject([
    { name: 'primary', state: 'FORMAT_MISMATCH' },
  ]);
  expect(workItemOutputRows(run, profile)).toMatchObject([{ name: 'primary', state: 'PRESENT' }]);
});

it('shows only the exact persisted profile and Result Contract bound to the run', async () => {
  fetchRoutes({ [tracePath]: {
    agentRunId: 'agent', traceId: 'trace', spans: [],
    trace: { businessCapability: { capabilityRef, profileRef, modelRef, workflowRef, resultContractRef } },
  } });
  await expect(readWorkItemRunProfile(link, run, product, signal(), ['researcher'])).resolves.toMatchObject({
    state: 'VERIFIED', profile, capability,
  });
  await expect(readWorkItemRunProfile(link, { ...run, modelChecksum: olderDigest }, product, signal(), ['researcher']))
    .resolves.toEqual({ state: 'MISMATCH' });
  await expect(readWorkItemRunProfile(link, { ...run, modelId: 'additional-model' }, product, signal(), ['researcher']))
    .resolves.toEqual({ state: 'UNVERIFIED' });
  const changed = { ...capability, profiles: [{ ...profile, resultContractRef: { ...resultContractRef, checksum: olderDigest } }] };
  await expect(readWorkItemRunProfile(link, run, { ...product, businessCapabilities: [changed] }, signal(), ['researcher']))
    .resolves.toEqual({ state: 'MISMATCH' });
  const current = { ...capability, profiles: [{ ...profile, profileRef: { ...profileRef, version: '2.0.0' } }] };
  await expect(readWorkItemRunProfile(link, run, { ...product, businessCapabilities: [current] }, signal(), ['researcher']))
    .resolves.toEqual({ state: 'UNVERIFIED' });
  await expect(readWorkItemRunProfile(link, run, product, signal(), ['viewer'])).resolves.toEqual({ state: 'UNVERIFIED' });
});

it('leaves a historical unprofiled run unverified when its model and workflow match the current product catalog', async () => {
  fetchRoutes({ [tracePath]: { agentRunId: 'agent', traceId: 'trace', spans: [], trace: null } });
  await expect(readWorkItemRunProfile(link, run, product, signal())).resolves.toEqual({ state: 'UNVERIFIED' });
});

const goalRevision = 2;
const action = {
  toolCallId: 'call', tool: 'submit', modelId: 'model', modelVersion: '2.0.0', modelChecksum: digest,
  workflowId: 'workflow', workflowVersion: '1.0.0', workflowChecksum: digest, arguments: { metric: 'mean' },
  inputReferences: { water_levels: { ...output, versionId: 'input-version' } },
};
const confirmation = { confirmationId: 'correct', agentRunId: 'agent', goalRevision, status: 'CONFIRMED', actions: [action] };
const turn = {
  agentRunId: 'agent', goalRevision, confirmations: [
    { ...confirmation, confirmationId: 'wrong', actions: [{ ...action, arguments: { metric: 'median' } }] },
    confirmation,
  ],
  executionTasks: [{ workflowRunId: 'run', agentRunId: 'agent', goalRevision, toolCallId: 'call', confirmationId: 'correct' }],
};
const task = {
  schemaVersion: 'task-context.v1',
  spec: { sessionId: 'session', taskId: 'task', revision: 1, objective: 'Track changes',
    criteria: [{ id: 'result', kind: 'RESULT', modelId: 'model', outputNames: ['primary'] }] },
  progress: { lifecycle: 'COMPLETE', state: 'COMPLETE', results: [output],
    criteria: [{ id: 'result', satisfied: true, evidence: { agentRunId: 'agent', workflowRunId: 'run' } }] },
};
const history = {
  sessionId: 'session', goalRevision: 4, task, turns: [turn], items: [], eventCursor: 4, nextCursor: null,
  snapshotAt: time, controlRequests: [], controlMessages: [], controlProposals: [], preparation: null,
};

it('reads only the confirmation named by the run dispatch and recognizes a matching Task output', async () => {
  fetchRoutes({ [historyPath]: history });
  await expect(readWorkItemSessionEvidence(link, run, signal(), reference, goalRevision)).resolves.toMatchObject({
    action: { arguments: { metric: 'mean' }, inputReferences: action.inputReferences },
    task: { spec: { taskId: 'task', revision: 1 }, progress: { lifecycle: 'COMPLETE' } }, taskAdoption: 'ADOPTED',
  });
});

it('does not infer input roles from a confirmation attached to a different attempt', async () => {
  fetchRoutes({ [historyPath]: { ...history, turns: [{ ...turn, confirmations: [confirmation],
    executionTasks: [{ ...turn.executionTasks[0], confirmationId: 'other' }],
  }] } });
  await expect(readWorkItemSessionEvidence(link, run, signal(), reference, goalRevision)).resolves.toMatchObject({ action: null });
});

it('reports no Task adoption for the same run and output name with a different Data identity', async () => {
  fetchRoutes({ [historyPath]: { ...history, task: { ...task, progress: { ...task.progress,
    results: [{ ...output, semanticId: 'different' }],
  } } } });
  await expect(readWorkItemSessionEvidence(link, run, signal(), reference, goalRevision)).resolves.toMatchObject({
    task: null, taskAdoption: 'NOT_ADOPTED',
  });
});

it('rejects evidence from a different Session', async () => {
  fetchRoutes({ [historyPath]: { ...history, sessionId: 'other' } });
  await expect(readWorkItemSessionEvidence(link, run, signal(), reference, goalRevision))
    .rejects.toMatchObject({ code: 'WORK_ITEM_REFERENCE_MISMATCH' });
});
