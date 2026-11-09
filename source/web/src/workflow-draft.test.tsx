import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { WorkflowDraftCard } from './WorkflowDraftCard';
import { PreparationDetails } from './WorkspacePanel';
import type { WorkflowDraft } from './types';
import { capabilitiesFixture, dataRef, historyFixture, type HistoryFixture } from './test-fixtures';

const draft: WorkflowDraft = {
  schemaVersion: 'workflow-draft-resource.v1', draftId: 'draft-1', sessionId: 'session-1', goalRevision: 2,
  agentRunId: 'agent-2', status: 'COMPLETE', errorCode: null, diagnostics: null,
  payload: {
    schemaVersion: 'workflow-draft.v1', title: '插值与制图草稿', summary: '先生成空间表面，再制作静态地图。',
    workflow: { schemaVersion: 'workflow.v6', workflowId: 'draft.generated', version: '1.0.0',
      inputs: [{ name: 'wells', cardinality: 'one', mediaTypes: ['text/csv'], formats: ['csv'] }, { name: 'boundary', cardinality: 'one', mediaTypes: ['application/geo+json'], formats: ['geojson'] }],
      parameters: ['field', 'title', 'rules'], nodes: [
        { nodeId: 'interpolate', operatorId: 'spatial.interpolate', operatorVersion: '1.0.0', inputs: { data: { workflowInput: 'wells' }, boundary: { workflowInput: 'boundary' } }, parameters: { valueField: { workflowParameter: 'field' }, rules: { workflowParameter: 'rules' } } },
        { nodeId: 'map', operatorId: 'cartography.render-png', operatorVersion: '1.0.0', inputs: { data: { nodeOutput: { nodeId: 'interpolate', port: 'raster' } } }, parameters: { title: { workflowParameter: 'title' } } },
      ], outputs: [{ name: 'map-image', nodeOutput: { nodeId: 'map', port: 'image' } }] },
    workflowChecksum: 'sha256:workflow', operatorCatalogDigest: 'sha256:catalog',
    operators: [
      { nodeId: 'interpolate', operatorId: 'spatial.interpolate', operatorVersion: '1.0.0', implementationRevision: 'rev-1', runtime: 'native', title: '空间插值' },
      { nodeId: 'map', operatorId: 'cartography.render-png', operatorVersion: '1.0.0', implementationRevision: 'rev-2', runtime: 'native', title: '静态地图' },
    ],
    parameterValues: { title: '空间分布', rules: { conditions: [{ field: 'quality_status', operator: 'eq', value: 'VALID' }], sort: [{ field: 'observed_at', order: 'ascending' }] } },
    parameterSources: [
      { name: 'title', source: 'USER', explanation: '用户指定图名' },
      { name: 'rules', source: 'DATA', explanation: '根据已核对字段整理的嵌套规则。' },
    ],
    dataBindings: [{ inputName: 'wells', dataRef: { kind: 'data_version', service: 'data-primary', datasetId: 'dataset-1', versionId: 'version-3', semanticId: 'semantics-3', objectId: 'object-3', checksum: 'a'.repeat(64), mediaType: 'text/csv', format: 'csv', sizeBytes: 42 } }],
    missingInputs: [{ kind: 'DATA', name: 'boundary', requiredBy: [{ nodeId: 'interpolate', port: 'boundary' }] }, { kind: 'PARAMETER', name: 'field', requiredBy: [{ nodeId: 'interpolate', port: 'valueField' }] }],
    knowledgeRefs: [{ versionId: 'method-version-1', unitId: 'unit-2', checksum: 'b'.repeat(64) }],
    unresolvedItems: ['待明确坐标参考系'], limitations: ['静态图不表示预测置信区间'], executionAllowed: false,
  },
};

beforeEach(() => {
  TestEvents.cursor = 0;
  sessionStorage.clear();
  window.location.hash = '#/';
  Element.prototype.scrollIntoView = vi.fn();
});

it('renders business steps, missing items and evidence without exposing raw graph identity', () => {
  const readKnowledge = vi.fn(), readResource = vi.fn();
  render(<WorkflowDraftCard draft={draft} onKnowledge={readKnowledge} onResource={readResource} />);
  expect(screen.getByTestId('workflow-draft-card')).toHaveAttribute('data-draft-id', 'draft-1');
  expect(within(screen.getByTestId('draft-nodes')).getAllByRole('listitem')).toHaveLength(2);
  expect(screen.getByText('空间插值')).toBeVisible();
  expect(within(screen.getByTestId('draft-nodes')).getByText('静态地图')).toBeVisible();
  expect(screen.getByText(/第 1 步 · 空间插值的输出/)).toBeVisible();
  expect(screen.queryByText(/interpolate\.raster|map\.data|map\.image|spatial\.interpolate/)).not.toBeInTheDocument();
  expect(screen.getByText('缺少数据：boundary')).toBeVisible();
  expect(screen.getByText('缺少参数：field')).toBeVisible();
  expect(screen.getAllByText('用于 第 1 步 · 空间插值')).toHaveLength(2);
  expect(screen.getByText('空间分布')).toBeVisible();
  expect(screen.getByText('用户说明 · 用户指定图名')).toBeVisible();
  expect(screen.getByText('conditions')).toBeVisible();
  expect(screen.getByText('quality_status')).toBeVisible();
  expect(screen.getByText('数据 · 根据已核对字段整理的嵌套规则。')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /wells · version-3/ }));
  expect(readResource).toHaveBeenCalledExactlyOnceWith('version-3');
  fireEvent.click(screen.getByText('输入格式与知识依据'));
  fireEvent.click(screen.getByRole('button', { name: /知识依据 1 · unit-2/ }));
  expect(readKnowledge).toHaveBeenCalledExactlyOnceWith(draft.payload!.knowledgeRefs[0]);
  expect(screen.getByText('用户说明 · 用户指定图名')).toBeVisible();
  expect(screen.getByRole('link', { name: '导出草稿 JSON' })).toHaveAttribute('href', '/api/v12/sessions/session-1/workflow-drafts/draft-1/export');
  expect(screen.getByText(/仅草稿，不可执行/)).toBeVisible();
  expect(screen.queryByText('冻结身份与原始 JSON')).not.toBeInTheDocument();
  expect(screen.queryByText('sha256:workflow')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认执行' })).not.toBeInTheDocument();
});

it('dispatches known preparation blockers and leaves an unknown blocker read-only', () => {
  const onData = vi.fn(), onVersion = vi.fn(), onParameter = vi.fn();
  const input = dataRef({ versionId: 'stale-version', format: 'csv', mediaType: 'text/csv' });
  const history = historyFixture({ preparation: {
    schemaVersion: 'task-preparation.v1', preparationId: 'preparation-1', revision: 1,
    sessionId: 'session-1', agentRunId: 'agent-1', goalRevision: 1,
    taskId: null, baseTaskRevision: null, status: 'BLOCKED', freshForCurrentTurn: true, disposition: 'ACTIVE',
    entries: [{ targetKind: 'EXECUTABLE_MODEL', targetId: 'groundwater.summary', targetVersion: '3.0.0',
      selectionPolicy: 'CURRENT_REQUIRED', status: 'BLOCKED', inputs: { versionId: input }, knowledgeRefs: [], parameters: {}, parameterSources: {},
      checks: [
        { kind: 'DATA_VERSION_NOT_CURRENT', input: 'versionId', status: 'FAIL', currentVersion: 'current-version' },
        { kind: 'FIELD_METADATA', input: 'versionId', status: 'UNKNOWN' },
      ],
      blockingReasons: [
        'WORKFLOW_DRAFT_MISSING_DATA:versionId',
        'WORKFLOW_DRAFT_MISSING_PARAMETER:column',
        'WORKFLOW_DRAFT_UNRESOLVED_ITEMS',
        'FUTURE_UNKNOWN_BLOCKER',
      ] }],
  } });
  render(<PreparationDetails history={history} onData={onData} onVersion={onVersion} onParameter={onParameter} />);
  fireEvent.click(screen.getByRole('button', { name: '使用当前版本' }));
  fireEvent.click(screen.getByRole('button', { name: '查看数据详情' }));
  fireEvent.click(screen.getByRole('button', { name: '选择所需数据' }));
  fireEvent.click(screen.getByRole('button', { name: '补充参数' }));
  fireEvent.click(screen.getByRole('button', { name: '补充未解决事项' }));
  expect(onVersion).toHaveBeenNthCalledWith(1, 'current-version', 'versionId');
  expect(onVersion).toHaveBeenNthCalledWith(2, 'stale-version');
  expect(onData).toHaveBeenCalledExactlyOnceWith(['csv', 'json', 'parquet', 'arrow-ipc-file', 'npy']);
  expect(onParameter).toHaveBeenNthCalledWith(1, 'column');
  expect(onParameter).toHaveBeenNthCalledWith(2, '未解决事项');
  expect(screen.getByText('阻塞：FUTURE_UNKNOWN_BLOCKER')).toBeVisible();
  expect(screen.getAllByRole('button')).toHaveLength(5);
});

it.each([
  ['GENERATING', null, '正在查阅可用方法与当前资料，整理分析步骤。'],
  ['COMPLETE', null, '未生成可用草稿，请查看本轮回复中的说明。'],
] as const)('never exports a %s draft without a payload', (status, errorCode, text) => {
  render(<WorkflowDraftCard draft={{ ...draft, status, errorCode, payload: null }} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent(text);
  expect(screen.queryByRole('link', { name: '导出草稿 JSON' })).not.toBeInTheDocument();
});

it('keeps a failed draft non-executable and exposes its diagnostic code on demand', () => {
  render(<WorkflowDraftCard draft={{ ...draft, status: 'FAILED', errorCode: 'INVALID_WORKFLOW_DRAFT', payload: null }} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  expect(screen.getByText('编排未完成')).toBeVisible();
  expect(screen.queryByRole('link', { name: '导出草稿 JSON' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /执行|确认/ })).not.toBeInTheDocument();
  expect(screen.getByText('INVALID_WORKFLOW_DRAFT')).not.toBeVisible();
});

it('locates a rejected edge and preserves its validation details without offering execution', () => {
  render(<WorkflowDraftCard draft={{ ...draft, status: 'FAILED', errorCode: 'AGENT_MODEL_OUTPUT_INVALID', payload: null,
    diagnostics: { kind: 'WORKFLOW_VALIDATION', code: 'WORKFLOW_PORT_INCOMPATIBLE', path: '/graph/nodes/1/inputs/data/nodeOutput' },
  }} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  expect(screen.getByText('编排步骤的输入与上游输出不兼容，请核对端口和文件格式。')).toBeVisible();
  expect(screen.getByText('请核对第 2 步，调整编排要求后，可重新提交草稿。')).toBeVisible();
  expect(screen.getByText('/graph/nodes/1/inputs/data/nodeOutput')).not.toBeVisible();
  fireEvent.click(screen.getByText('技术详情'));
  expect(screen.getByText('/graph/nodes/1/inputs/data/nodeOutput')).toBeVisible();
  expect(screen.queryByRole('link', { name: '导出草稿 JSON' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /执行|确认/ })).not.toBeInTheDocument();
});

it('a complete draft with no missing inputs still has no execution or approval action', () => {
  render(<WorkflowDraftCard draft={{ ...draft, payload: { ...draft.payload!, missingInputs: [], unresolvedItems: [] } }} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  expect(screen.queryByTestId('draft-missing')).not.toBeInTheDocument();
  expect(screen.getByText('仅草稿，不可执行。')).toBeVisible();
  expect(screen.queryByRole('button', { name: /执行|确认|选择/ })).not.toBeInTheDocument();
});

class TestEvents {
  static latest: TestEvents;
  static cursor = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  constructor() { TestEvents.latest = this; }
  addEventListener(type: string, handler: (event: MessageEvent) => void) { this.listeners.set(type, handler); }
  close() {}
  emit(type: string, id: number) { TestEvents.cursor = id; this.listeners.get(type)?.(new MessageEvent(type, { lastEventId: String(id), data: '{}' })); }
}
const json = (data: unknown) => new Response(JSON.stringify(data && typeof data === 'object' && 'turns' in data ? historyFixture({ eventCursor: TestEvents.cursor, ...data as HistoryFixture }) : data), { status: 200, headers: { 'Content-Type': 'application/json' } });

it('keeps cache and UI terminal when an older-page publication overtakes a delayed generating read', async () => {
  window.location.hash = '#/sessions/session-1';
  vi.stubGlobal('EventSource', TestEvents);
  let finishInitial!: (value: Response) => void;
  let draftReads = 0;
  const calls = vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json({ items: [], nextCursor: null });
    if (url.includes('/messages')) return json({ sessionId: 'session-1', goalRevision: 2, nextCursor: url.includes('?cursor=') ? null : 2,
      items: [{ messageId: url.includes('?cursor=') ? 1 : 2, goalRevision: 2, role: 'user', content: '查询草稿的发布结果' }],
      turns: [{ goalRevision: 2, agentRunId: 'agent-2', status: 'RUNNING', requestMode: 'WORKFLOW_DRAFT', workflowDraftId: 'draft-1' }] });
    if (url.includes('/workflow-drafts/')) {
      draftReads++;
      return draftReads === 1 ? new Promise<Response>(resolve => { finishInitial = resolve; }) : json(draft);
    }
    throw new Error(`unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', calls); render(<App />);
  await screen.findByText('查询草稿的发布结果');
  await waitFor(() => expect(draftReads).toBe(1));
  fireEvent.click(screen.getByRole('button', { name: '更早消息' }));
  await screen.findByRole('link', { name: '导出草稿 JSON' });
  await act(async () => finishInitial(json({ ...draft, status: 'GENERATING', payload: null })));
  expect(screen.getByRole('link', { name: '导出草稿 JSON' })).toBeVisible();
  await act(async () => { TestEvents.latest.emit('workflow_draft.published', 10); await new Promise(resolve => setTimeout(resolve, 150)); });
  expect(draftReads).toBe(2);
  expect(screen.getByRole('link', { name: '导出草稿 JSON' })).toBeVisible();
});

it('a delayed draft read from an older page cannot replace a newly published immutable draft', async () => {
  window.location.hash = '#/sessions/session-1';
  vi.stubGlobal('EventSource', TestEvents);
  let generated = false, reads = 0;
  let finishOlderDraft!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json({ items: [], nextCursor: null });
    if (url.includes('/workflow-drafts/')) {
      reads += 1;
      if (reads === 2) return new Promise<Response>(resolve => { finishOlderDraft = resolve; });
      return json(generated ? draft : { ...draft, status: 'GENERATING', payload: null });
    }
    if (url.includes('/messages')) return json({ sessionId: 'session-1', goalRevision: 2,
      items: [{ messageId: url.includes('?cursor=') ? 1 : 2, goalRevision: 2, role: 'user', content: url.includes('?cursor=') ? '更早的问题片段' : '编排插值与制图' }],
      turns: [{ goalRevision: 2, agentRunId: 'agent-2', status: generated ? 'COMPLETE' : 'RUNNING', errorCode: null, requestMode: 'WORKFLOW_DRAFT', workflowDraftId: 'draft-1', confirmations: [] }], nextCursor: url.includes('?cursor=') ? null : 2 });
    return json({ sessionId: 'session-1', projectId: 'demo', goalRevision: 2, agentRunId: 'agent-2', workflowDraftId: 'draft-1', confirmation: null });
  }));
  render(<App />);
  await screen.findByText('正在查阅可用方法与当前资料，整理分析步骤。');
  fireEvent.click(screen.getByRole('button', { name: '更早消息' }));
  await waitFor(() => expect(finishOlderDraft).toBeDefined());
  generated = true;
  await act(async () => { TestEvents.latest.emit('workflow_draft.published', 10); await new Promise(resolve => setTimeout(resolve, 150)); });
  await screen.findByRole('link', { name: '导出草稿 JSON' });
  await act(async () => { finishOlderDraft(json({ ...draft, status: 'GENERATING', payload: null })); });
  await screen.findByText('更早的问题片段');
  expect(screen.getByRole('link', { name: '导出草稿 JSON' })).toBeVisible();
  expect(screen.getByText('插值与制图草稿')).toBeVisible();
});
