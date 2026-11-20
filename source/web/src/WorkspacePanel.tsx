import { preparationCheckFacts, preparationIssues } from './preparation-display';
import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError, contentUrl, errorCodeMessage, readDataVersion } from './api';
import { agentTokenUsageLabel, canConfirm, isComposedConfirmation, needsExecutionInputs, statusLabel, taskLabel } from './chat-state';
import { ArgumentValue, argumentLabel, modelName, outputName, resourceTitle } from './card-presentation';
import { RecoveryCard, resultDownloadName } from './ChatCards';
import { productUi } from './product-ui';
import { DataFieldQuality } from './DataFieldQuality';
import { ConfirmationEditor } from './ConfirmationEditor';
import { WorkflowNodeDetails } from './WorkflowNodeDetails';
import { WorkflowPublicationDetails } from './WorkflowPublicationDetails';
import { ErrorNotice } from './ErrorNotice';
import { shortVersion } from './format';
import { isResourceReference } from './resource-item';
import { Icon } from './Icon';
import { readEnumCache, readFlagCache, readNumberCache } from './browser-cache';
import type { Capabilities, Confirmation, ConfirmationRevisionAction, History, KnowledgeReference, ResourceDetail, ResourceItem, ResourceReference, Result, WorkflowDraft } from './types';
import type { ConfirmationFocus } from './use-workspace-controller';
import { scopedStorageKey } from './browser-scope';
import './workbench-polish.css';
import { TaskCard, sessionTask } from './TaskCard';
import { selectSessionUi, selectTaskUi, type ResultsRead } from './ui-selectors';
import { containTabFocus, useWorkbenchModal } from './workbench-focus';
import { Badge, EmptyState, Loading } from './ui/Feedback';
import { WorkflowDraftCard } from './WorkflowDraftCard';

export type WorkspaceTab = 'inputs' | 'draft' | 'parameters' | 'tasks' | 'results';
type MethodInput = { modelId: string; modelVersion: string; name: string };
const panelWidth = { initial: 392, minimum: 360, maximum: 448 };
const tabs: readonly WorkspaceTab[] = ['inputs', 'draft', 'parameters', 'tasks', 'results'];
const labels: Record<WorkspaceTab, string> = { inputs: '数据', draft: '草稿', parameters: '参数', tasks: '任务', results: '成果' };
const ArtifactViewer = lazy(() => import('./ArtifactViewer').then(module => ({ default: module.ArtifactViewer })));
const ResultInsights = lazy(() => import('./ResultInsights').then(module => ({ default: module.ResultInsights })));

export interface WorkspaceState { taskStatus: string; turnStatus: string; tone: 'neutral' | 'working' | 'warning' | 'error' | 'success'; recommendedTab: WorkspaceTab }

export function resolveWorkspaceState(history: History | null, hasSelectedSession = true): WorkspaceState {
  const turn = history?.turns.find(item => item.goalRevision === history.goalRevision);
  if (history?.task) {
    const { lifecycle, state } = selectTaskUi(history.task, false);
    const status = lifecycle?.tone === 'success' ? lifecycle : state;
    const tone = { info: 'working', danger: 'error', warning: 'warning', success: 'success', neutral: 'neutral' } as const;
    return {
      taskStatus: status?.label ?? '状态待核实',
      turnStatus: turn ? statusLabel[turn.status] ?? '本轮状态待核实' : '本轮尚未开始',
      tone: tone[status?.tone ?? 'neutral'],
      recommendedTab: state?.raw === 'WAITING_INPUT' ? 'inputs' : state?.raw === 'WAITING_CONFIRMATION' ? 'parameters' : 'tasks',
    };
  }
  if (!turn) return { taskStatus: history || !hasSelectedSession ? '暂无任务' : '正在读取会话', turnStatus: '本轮尚未开始', tone: 'neutral', recommendedTab: 'tasks' };
  if (['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(turn.status)) return {
    taskStatus: turn.status === 'TIMED_OUT' ? '执行已超时' : turn.status === 'CANCELLED' ? '任务已取消' : '本轮未完成',
    turnStatus: turn.errorCode ? errorCodeMessage(turn.errorCode) : '请查看失败记录', tone: 'error', recommendedTab: 'tasks',
  };
  const running = turn.executionTasks.find(item => ['QUEUED', 'RUNNING'].includes(item.status));
  if (running) return { taskStatus: running.stage === 'PUBLISHING' ? '正在发布成果' : '正在执行', turnStatus: taskLabel(running), tone: 'working', recommendedTab: 'tasks' };
  if (turn.confirmations.some(item => item.status === 'PENDING')) return { taskStatus: '等待确认', turnStatus: '执行参数已准备，请核对后决定', tone: 'warning', recommendedTab: 'parameters' };
  if (['ROUTING', 'QUEUED', 'RUNNING', 'WAITING_EXECUTION'].includes(turn.status)) return { taskStatus: '正在处理', turnStatus: turn.status === 'ROUTING' ? '正在判断需求' : '本轮处理进行中', tone: 'working', recommendedTab: 'tasks' };
  if (needsExecutionInputs(turn)) return { taskStatus: '需要补充信息', turnStatus: '本轮回答已生成', tone: 'warning', recommendedTab: 'inputs' };
  return { taskStatus: '本轮回答已完成', turnStatus: '本轮回答已生成', tone: 'success', recommendedTab: 'tasks' };
}

function isCurrentPreparation(history: History) {
  const preparation = history.preparation;
  return !!preparation && preparation.sessionId === history.sessionId && preparation.goalRevision === history.goalRevision
    && preparation.freshForCurrentTurn && ['ACTIVE', 'WAITING_CONFIRMATION', 'EXECUTING'].includes(preparation.disposition);
}

function referenceKey(item: ResourceReference) {
  return JSON.stringify([item.kind, item.service, item.datasetId, item.versionId, item.objectId, item.semanticId, item.checksum, item.format, item.mediaType, item.sizeBytes]);
}

function uniqueReferences(items: ResourceReference[]) {
  const seen = new Set<string>();
  return items.filter(item => { const key = referenceKey(item); if (seen.has(key)) return false; seen.add(key); return true; });
}

function ReferenceDetails({ item }: { item: ResourceReference }) {
  return <details className="workspace-reference-details"><summary>查看精确身份</summary>
    <dl><dt>Dataset</dt><dd>{item.datasetId}</dd><dt>Version</dt><dd>{item.versionId}</dd><dt>Semantic</dt><dd>{item.semanticId}</dd><dt>Object</dt><dd>{item.objectId}</dd><dt>checksum</dt><dd>{item.checksum}</dd><dt>MIME</dt><dd>{item.mediaType}</dd><dt>大小</dt><dd>{item.sizeBytes.toLocaleString()} B</dd></dl>
  </details>;
}

const DATA_SEMANTIC_CHECKS = new Set([
  'FIELD_METADATA', 'FIELD_SEMANTICS', 'FIELD_UNIT', 'CRS',
  'TIME_FIELD_PARSEABILITY', 'TIMEZONE', 'TIME_RANGE', 'VERTICAL_BASIS',
]);

const preparationCheckLabels: Record<string, string> = {
  DATA_FORMAT: '文件格式', DATA_VERSION: '数据版本', DATA_OBJECT: '数据对象',
  FIELD_METADATA: '字段元数据', FIELD_SEMANTICS: '字段含义', FIELD_UNIT: '字段单位',
  CRS: '坐标参考系', CRS_PARAMETER: '分析坐标参考系', TIME_FIELD_PARSEABILITY: '时间字段可读性',
  TIMEZONE: '时区', TIME_RANGE: '时间覆盖范围', VERTICAL_BASIS: '高程与埋深基准', UTC_TIME_FIELD: 'UTC 观测时间',
};

export function PreparationDetails({ history, dataAvailable = true, onData, onVersion, onUseCurrentVersion, onParameter }: { history: History; dataAvailable?: boolean; onData: (formats: string[], input?: MethodInput) => void; onVersion: (id: string, reference: ResourceReference) => void; onUseCurrentVersion?: (id: string, datasetId: string) => void; onParameter: (name: string) => void }) {
  const preparation = history.preparation;
  if (!preparation || preparation.sessionId !== history.sessionId) return null;
  const current = isCurrentPreparation(history);
  const actionable = current && ['ACTIVE', 'WAITING_CONFIRMATION'].includes(preparation.disposition);
  const entries = preparation.entries;
  return <details open={current} className="workspace-preparation">
    <summary>输入准备 · {!current ? '需要本轮重新核验' : preparation.status === 'READY' ? '合同可提案' : preparation.status === 'BLOCKED' ? '有分析需要补充' : '状态待核实'}</summary>
    <p className="muted">准备修订 {preparation.revision} · {current ? '本轮核验' : `第 ${preparation.goalRevision} 轮记录`}</p>
    {entries.map((entry, entryIndex) => <section key={`${entry.targetId}/${entryIndex}`}>
      <h4>{modelName(entry.targetId)} <Badge tone={!current ? 'neutral' : entry.status === 'READY' ? 'success' : 'warning'}>
        {!current ? '历史记录' : entry.status === 'READY' ? '可提案' : entry.status === 'BLOCKED' ? '待补充' : '状态待核实'}
      </Badge></h4>
      {Object.entries(entry.inputs).length > 0 && <dl className="preparation-input-roles">
        {Object.entries(entry.inputs).map(([role, reference]) => <div key={role}>
          <dt>{argumentLabel(role, entry.targetId)}</dt>
          <dd>{dataAvailable ? <button type="button" onClick={() => onVersion(reference.versionId, reference)}>
            {reference.datasetId} · {shortVersion(reference.versionId)}
          </button> : <span>{reference.datasetId} · {shortVersion(reference.versionId)}</span>}</dd>
        </div>)}
      </dl>}
      {entry.knowledgeRefs.length > 0 && <p>知识证据 {entry.knowledgeRefs.length} 项</p>}
      {entry.checks.map((check, index) => {
    const input = check.input ? entry.inputs[check.input] : undefined;
    const needsAdmin = check.status === 'UNKNOWN' && DATA_SEMANTIC_CHECKS.has(check.kind);
    const facts = preparationCheckFacts(check);
    return <div className="preparation-check" key={`${check.kind}/${check.input ?? 'global'}/${index}`}>
      <div className="preparation-check-heading">
        <Badge tone={!current ? 'neutral' : check.status === 'PASS' ? 'success' : check.status === 'FAIL' ? 'error' : 'warning'}>
          {check.status === 'PASS' ? '通过' : check.status === 'UNKNOWN' ? '待判断' : check.status === 'FAIL' ? '失败' : '状态待核实'}
        </Badge>
        <strong>{check.input ? `${argumentLabel(check.input, entry.targetId)} · ` : ''}{preparationCheckLabels[check.kind] ?? check.kind}</strong>
      </div>
      {facts && <p className="muted">{facts}</p>}
      {check.kind === 'UTC_TIME_FIELD' && check.status !== 'PASS' && <>
        <p className="muted">请核对 UTC 类型的时间戳和资料的 UTC 时区声明。</p>
        {dataAvailable && input && <button type="button" onClick={() => onVersion(input.versionId, input)}>核对时间字段和时区</button>}
      </>}
      {actionable && dataAvailable && check.currentVersion && check.status === 'FAIL' && input && onUseCurrentVersion && <button onClick={() => onUseCurrentVersion(check.currentVersion!, input.datasetId)}>使用当前版本</button>}
      {needsAdmin && input && <><p className="muted">需要 Data 管理员补充字段、单位、CRS 或时间语义。</p>{dataAvailable && <button onClick={() => onVersion(input.versionId, input)}>查看数据详情</button>}</>}
    </div>;
  })}{preparationIssues(entry).map((issue, index) => {
    const formats = issue.input ? productUi().models[entry.targetId]?.inputs[issue.input]?.formats ?? [] : [];
    const input = entry.targetKind === 'EXECUTABLE_MODEL' && entry.targetVersion && issue.input
      ? { modelId: entry.targetId, modelVersion: entry.targetVersion, name: issue.input } : undefined;
    const businessIssue = issue.code.startsWith('CAPABILITY_');
    return <div className="preparation-blocker" key={`${issue.code}/${issue.path ?? issue.input ?? issue.parameter ?? index}`}><span>
      {businessIssue
        ? `${issue.parameter ? `${argumentLabel(issue.parameter, entry.targetId)} · ` : ''}${errorCodeMessage(issue.code)}`
        : `阻塞：${issue.code}${issue.path ? ` · ${issue.path}` : ''}`}
    </span>
      {actionable && issue.kind === 'MISSING_DATA' && dataAvailable && <button onClick={() => onData(formats, input)}>选择所需数据</button>}
      {actionable && (issue.kind === 'MISSING_PARAMETER' || issue.kind === 'INVALID_PARAMETER') && issue.parameter && <button onClick={() => onParameter(issue.parameter!)}>补充参数</button>}
      {actionable && issue.kind === 'UNRESOLVED_ITEM' && <button onClick={() => onParameter('未解决事项')}>补充未解决事项</button>}
    </div>;
  })}</section>)}</details>;
}

export function WorkspacePanel({ history, results, resultsRead = { status: 'loaded', items: results }, activeId, storageScope = '', project = '', capabilities = null, dataAvailable = true, collapsed, hideWhenCollapsed = false, pendingInputs = [], selectedResourceId = null, selectedResourceReference = null, editingConfirmationId = null, confirmationFocus = null, focusRequest, busy = false, routedTab, onRouteTab, feedback, dataFeedback, inputEditor, onChooseMethod, confirmationReview, draft, draftRead, onDraftKnowledge, onAttachResult, onContinueResult, onCompareResult, onRetryResults, continueResultDisabled = false, onToggle, onJump, onCurrentConfirmation, onResource = () => {}, onInputResource = () => {}, onUseCurrentVersion, onOpenDatabase = () => {}, onRemovePending = () => {}, onEdit = () => {}, onResolveParameter = () => {}, onEditClose = () => {}, onRevision = () => {}, onRecover = () => {}, onReview = () => {}, onRequestTaskChanges, taskReviewDisabled = false, confirmationWriteDisabled = false }: {
  history: History | null; results: Result[]; resultsRead?: ResultsRead; activeId: string; storageScope?: string; project?: string; capabilities?: Capabilities | null; dataAvailable?: boolean; collapsed: boolean; hideWhenCollapsed?: boolean;
  pendingInputs?: ResourceItem[]; selectedResourceId?: string | null; selectedResourceReference?: ResourceReference | null; editingConfirmationId?: string | null; confirmationFocus?: ConfirmationFocus; focusRequest?: { tab: WorkspaceTab; nonce: number } | null; busy?: boolean;
  routedTab?: WorkspaceTab | null; onRouteTab?: (tab: WorkspaceTab) => void;
  feedback?: ReactNode;
  dataFeedback?: ReactNode;
  inputEditor?: ReactNode;
  onChooseMethod?: () => void;
  confirmationReview?: ReactNode;
  draft?: WorkflowDraft;
  draftRead?: 'loading' | 'ready' | { error: ApiError; retry: () => void };
  onDraftKnowledge?: (reference: KnowledgeReference) => void;
  onAttachResult?: (resource: ResourceReference) => void;
  onRetryResults?: () => void;
  onContinueResult?: (resource: ResourceReference) => void;
  onCompareResult?: (resource: ResourceReference) => void;
  continueResultDisabled?: boolean;
  taskReviewDisabled?: boolean;
  confirmationWriteDisabled?: boolean;
  onToggle: () => void; onJump: (revision: number) => void; onCurrentConfirmation?: () => void; onResource?: (id: string, reference?: ResourceReference) => void; onInputResource?: (id: string, reference: ResourceReference) => void; onUseCurrentVersion?: (id: string, datasetId: string) => void; onOpenDatabase?: (formats?: string[], input?: MethodInput) => void; onRemovePending?: (reference: ResourceReference) => void; onEdit?: (id: string) => void; onResolveParameter?: (name: string) => void; onEditClose?: () => void;
  onRevision?: (confirmation: Confirmation, actions: ConfirmationRevisionAction[]) => void; onRecover?: (workflowRunId: string) => void; onReview?: (text: string) => void; onRequestTaskChanges?: () => void;
}) {
  const turn = history?.turns.find(item => item.goalRevision === history.goalRevision);
  const confirmations = history?.turns.flatMap(item => item.confirmations.map(value => ({ ...value, goalRevision: item.goalRevision }))) ?? [];
  const latestConfirmation = turn?.confirmations.at(-1);
  const pendingConfirmation = latestConfirmation && latestConfirmation.agentRunId === turn?.agentRunId
    && canConfirm(latestConfirmation, history, false) ? latestConfirmation : null;
  const editing = confirmations.find(item => item.confirmationId === editingConfirmationId && !isComposedConfirmation(item)) ?? null;
  const attached = uniqueReferences(history?.items.filter(item => item.role === 'user').flatMap(item => item.inputReferences ?? []) ?? []);
  const frozen = uniqueReferences(confirmations.flatMap(item => item.actions.flatMap(action => Object.values(action.inputReferences ?? {}))));
  const preparationEntries = history?.preparation?.entries ?? [];
  const preparationActive = history && isCurrentPreparation(history);
  const prepared = preparationActive ? uniqueReferences(preparationEntries.flatMap(entry => Object.values(entry.inputs))) : [];
  const preparedParameterCount = preparationActive ? new Set(preparationEntries.flatMap(entry => Object.keys(entry.parameters))).size : 0;
  const task = sessionTask(history, activeId || history?.sessionId || '');
  const taskOutputs = task?.progress.results ?? [];
  const outputs = resultsRead.status === 'loaded' ? uniqueReferences([
    ...[...resultsRead.items].reverse().filter(result => ['COMPLETE', 'SUCCEEDED'].includes(result.status)).flatMap(result => result.remoteOutputs.filter(isResourceReference)),
    ...taskOutputs.filter(isResourceReference),
  ]) : [];
  const selectedOutput = selectedResourceReference?.versionId === selectedResourceId ? selectedResourceReference
    : outputs.find(output => output.versionId === selectedResourceId);
  const publishedOutput = selectedOutput && outputs.some(item => referenceKey(item) === referenceKey(selectedOutput)) ? selectedOutput : null;
  const outputIdentity = JSON.stringify([storageScope, activeId, selectedOutput ?? null]);
  const state = resolveWorkspaceState(history && { ...history, task }, Boolean(activeId));
  const runViews = selectSessionUi({ sessionId: activeId || history?.sessionId || '', history,
    results: resultsRead, freshness: 'unverified' }).turns.flatMap(turn => turn.workflowRuns);
  const runById = new Map(runViews.map(run => [run.workflowRunId, run]));
  const executionRecords = (history?.turns.flatMap(item => item.executionTasks
    .filter(run => run.agentRunId === item.agentRunId && run.goalRevision === item.goalRevision)
    .map(run => ({ turn: item, run }))) ?? []).sort(({ run: a }, { run: b }) => a.goalRevision - b.goalRevision
      || Date.parse(a.createdAt) - Date.parse(b.createdAt) || a.position - b.position || (a.attempt ?? 0) - (b.attempt ?? 0));
  const owningRuns = publishedOutput ? runViews.filter(run => run.outputs.some(ref => referenceKey(ref) === referenceKey(publishedOutput))) : [];
  const resultRun = owningRuns.length === 1 ? owningRuns[0] : null;
  const execution = resultRun && executionRecords.find(({ run }) => run.workflowRunId === resultRun.workflowRunId)?.run;
  const overview = execution ? productUi().models[execution.modelId]?.resultOverview : undefined;
  const hasAnalysisSummary = !!overview?.insight && resultRun?.outputs.some(item => item.outputName === overview.insight?.output);
  const [closedAnalysis, setClosedAnalysis] = useState<string | null>(null);
  const analysisOpen = !!resultRun && closedAnalysis !== resultRun.workflowRunId;
  const publicationReadFeedback = activeId && (!selectedResourceId || selectedOutput) && (
    resultsRead.status === 'failed'
      ? <div className="workbench-read-error"><p role="status">成果列表读取失败。</p>{onRetryResults &&
        <button onClick={onRetryResults}>重新读取成果列表</button>}</div>
      : resultsRead.status !== 'loaded' ? <p className="muted" role="status">正在读取成果列表…</p> : null
  );
  const tabKey = scopedStorageKey(storageScope, 'workspace-tab', activeId);
  const pinnedKey = scopedStorageKey(storageScope, 'workspace-pinned', activeId);
  const widthKey = scopedStorageKey(storageScope, 'workspace-width', activeId);
  const [localTab, setTab] = useState<WorkspaceTab>(() => readEnumCache(tabKey, tabs, 'tasks'));
  const tab = routedTab ?? localTab;
  const [pinned, setPinned] = useState(() => readFlagCache(pinnedKey));
  const [fullscreen, setFullscreen] = useState(false);
  const [width, setWidth] = useState(() => Math.min(panelWidth.maximum, readNumberCache(widthKey, panelWidth.initial, panelWidth.minimum, 760)));
  const [loadedResource, setResource] = useState<ResourceDetail | null>(null);
  const [resourceScope, setResourceScope] = useState(project);
  const [loadedOutputIdentity, setLoadedOutputIdentity] = useState('');
  const resource = loadedResource?.versionId === selectedResourceId && resourceScope === project
    && loadedOutputIdentity === outputIdentity ? loadedResource : null;
  const [resourceError, setResourceError] = useState<ApiError | null>(null);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [resourceRetry, setResourceRetry] = useState(0);
  const referenceScope = JSON.stringify([project, storageScope, activeId]);
  const [referenceRead, setReferenceRead] = useState<{
    scope: string; items: Record<string, ResourceDetail | 'mismatch' | 'unavailable'>;
  }>();
  const referenceChecks = referenceRead?.scope === referenceScope ? referenceRead.items : {};
  const inputReferences = [...pendingInputs, ...attached, ...prepared, ...frozen];
  const referenceSignature = JSON.stringify(inputReferences.map(referenceKey));
  const overlay = !routedTab && !collapsed && fullscreen;
  const resizing = useRef(false);
  const panelRef = useRef<HTMLElement>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const listOrigin = useRef<{ scope: string; key: string } | null>(null);
  const resultFocus = useRef<'detail' | 'list' | null>(null);
  useLayoutEffect(() => {
    if (tab !== 'results' || collapsed || !resultFocus.current) return;
    if (resultFocus.current === 'detail' && selectedResourceId) {
      resultFocus.current = null;
      resultHeading.current?.focus();
    } else if (resultFocus.current === 'list' && !selectedResourceId) {
      resultFocus.current = null;
      const origin = listOrigin.current;
      listOrigin.current = null;
      const row = origin?.scope === referenceScope && [...panelRef.current?.querySelectorAll<HTMLButtonElement>('[data-result-list-key]') ?? []]
        .find(button => button.dataset.resultListKey === origin.key);
      if (row) row.focus();
      else resultHeading.current?.focus();
    }
  }, [tab, collapsed, selectedResourceId, referenceScope]);
  useWorkbenchModal(panelRef, overlay, '.panel-toggle');
  const routeHeading = useRef<HTMLHeadingElement>(null);
  const [selectedRun, setSelectedRun] = useState<{ session: string; scope: string; runId: string; jump: boolean }>();
  const requestedRunId = selectedRun?.session === activeId && selectedRun.scope === storageScope ? selectedRun.runId : '';
  const selectedRunId = executionRecords.some(({ run }) => run.workflowRunId === requestedRunId) ? requestedRunId : '';
  useEffect(() => {
    if (tab !== 'tasks' || collapsed || !selectedRunId || !selectedRun?.jump) return;
    const record = [...panelRef.current?.querySelectorAll<HTMLElement>('[data-execution-run-id]') ?? []]
      .find(element => element.dataset.executionRunId === selectedRunId);
    record?.focus({ preventScroll: true });
    record?.scrollIntoView?.({ block: 'start' });
    if (record) setSelectedRun({ ...selectedRun, jump: false });
  }, [tab, selectedRunId, selectedRun, collapsed]);
  const isRouted = !!routedTab;
  useEffect(() => {
    if (isRouted) routeHeading.current?.focus();
  }, [isRouted]);
  const previousSnapshot = useRef('');
  const previousCounts = useRef({ attached: 0, outputs: 0 });

  useEffect(() => { setPinned(readFlagCache(pinnedKey)); setTab(readEnumCache(tabKey, tabs, 'tasks')); setWidth(Math.min(panelWidth.maximum, readNumberCache(widthKey, panelWidth.initial, panelWidth.minimum, 760))); setResource(null); }, [activeId, storageScope]);
  useEffect(() => { if (focusRequest) { setTab(focusRequest.tab); if (collapsed) onToggle(); } }, [focusRequest?.nonce]);
  useEffect(() => {
    const snapshot = `${state.recommendedTab}:${state.taskStatus}:${attached.length}:${outputs.length}:${pendingConfirmation?.confirmationId ?? ''}`;
    if (!pinned && snapshot !== previousSnapshot.current) {
      if (previousSnapshot.current && outputs.length > previousCounts.current.outputs) setTab('results');
      else if (previousSnapshot.current && attached.length > previousCounts.current.attached) setTab('inputs');
      else setTab(state.recommendedTab);
    }
    previousSnapshot.current = snapshot;
    previousCounts.current = { attached: attached.length, outputs: outputs.length };
  }, [state.recommendedTab, state.taskStatus, attached.length, outputs.length, pendingConfirmation?.confirmationId, pinned]);
  useEffect(() => { if (selectedResourceId) setTab('results'); }, [selectedResourceId]);
  useEffect(() => { sessionStorage.setItem(tabKey, tab); }, [tab, tabKey]);
  useEffect(() => { sessionStorage.setItem(widthKey, String(width)); }, [width, widthKey]);
  useEffect(() => { if (collapsed) setFullscreen(false); }, [collapsed]);
  useEffect(() => { const move = (event: PointerEvent) => {
      if (!resizing.current || !panelRef.current) return;
      const requested = panelRef.current.getBoundingClientRect().right - event.clientX;
      setWidth(Math.max(panelWidth.minimum, Math.min(panelWidth.maximum, requested)));
    }; const up = () => { resizing.current = false; }; window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); }; }, []);
  useEffect(() => {
    setResource(null); setResourceError(null);
    if (!selectedResourceId) { setResourceLoading(false); return; }
    const controller = new AbortController(); setResourceLoading(true);
    void readDataVersion(selectedResourceId, controller.signal, selectedOutput ?? undefined).then(value => {
      if (controller.signal.aborted) return;
      setResource(value); setResourceScope(project); setLoadedOutputIdentity(outputIdentity); setResourceLoading(false);
    }).catch(caught => { if (controller.signal.aborted) return; setResourceError(caught as ApiError); setResourceLoading(false); });
    return () => controller.abort();
  }, [selectedResourceId, project, resourceRetry, outputIdentity]);
  useEffect(() => {
    setReferenceRead({ scope: referenceScope, items: {} });
    if (!dataAvailable || tab !== 'inputs') return;
    const all = uniqueReferences(inputReferences);
    function record(item: ResourceReference, value: ResourceDetail | 'mismatch' | 'unavailable') {
      setReferenceRead(previous => previous?.scope === referenceScope
        ? { scope: referenceScope, items: { ...previous.items, [referenceKey(item)]: value } } : previous);
    }
    const controllers = all.map(item => {
      const controller = new AbortController();
      void readDataVersion(item.versionId, controller.signal, item).then(detail => {
        if (controller.signal.aborted) return;
        record(item, detail);
      }).catch(error => {
        if (controller.signal.aborted) return;
        const status = error instanceof ApiError && error.code === 'DATA_VERSION_IDENTITY_MISMATCH' ? 'mismatch' : 'unavailable';
        record(item, status);
      });
      return controller;
    });
    return () => controllers.forEach(controller => controller.abort());
  }, [referenceScope, referenceSignature, dataAvailable, tab]);
  useEffect(() => {
    if (!overlay) return;
    const prior = document.body.style.overflow;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = 'hidden'; panelRef.current?.querySelector<HTMLElement>('.panel-toggle')?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setFullscreen(false); }
      else if (panelRef.current) containTabFocus(event, panelRef.current);
    };
    document.addEventListener('keydown', key);
    return () => {
      document.body.style.overflow = prior; document.removeEventListener('keydown', key);
      requestAnimationFrame(() => {
        const active = document.activeElement;
        if (active instanceof HTMLElement && active !== document.body && !panelRef.current?.contains(active)) return;
        if (previousFocus?.isConnected && !panelRef.current?.contains(previousFocus)) previousFocus.focus();
        else panelRef.current?.querySelector<HTMLElement>('.panel-toggle')?.focus();
      });
    };
  }, [overlay]);

  function chooseTab(value: WorkspaceTab) { if (routedTab) { onRouteTab?.(value); return; } setTab(value); setPinned(true); sessionStorage.setItem(pinnedKey, '1'); }
  function selectRun(runId: string, jump = false) {
    setSelectedRun({ session: activeId, scope: storageScope, runId, jump });
    if (jump && tab !== 'tasks') chooseTab('tasks');
  }
  function tabKeyDown(event: React.KeyboardEvent, current: WorkspaceTab) {
    const index = tabs.indexOf(current);
    const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1] : event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length] : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length] : null;
    if (!next) return;
    event.preventDefault(); chooseTab(next); panelRef.current?.querySelector<HTMLElement>(`#workspace-tab-${next}`)?.focus();
  }
  function openDatabase(formats?: string[], input?: MethodInput) { if (overlay) onToggle(); onOpenDatabase(formats, input); }
  function openConversationTurn(revision: number) {
    if (overlay) {
      onToggle();
      requestAnimationFrame(() => onJump(revision));
    } else onJump(revision);
  }
  function openInput(id: string, reference: ResourceReference) { if (overlay) onToggle(); onInputResource(id, reference); }
  const recovery = useMemo(() => {
    const latest = turn?.executionTasks.filter(item => item.agentRunId === turn.agentRunId && item.isLatestAttempt) ?? [];
    const source = latest.filter(item => ['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(item.status))
      .sort((left, right) => left.position - right.position)[0];
    if (!source) return null;
    // Kernel recovers the selected source and later FAILED actions from that
    // same frozen confirmation; previous successes and other batches stay out.
    const count = latest.filter(item => item.confirmationId === source.confirmationId && item.position >= source.position
      && (item.workflowRunId === source.workflowRunId || item.status === 'FAILED')).length;
    return { source, count };
  }, [turn]);
  const unresolved = task?.spec.unresolvedItems ?? (needsExecutionInputs(turn)
    ? turn?.reply?.unresolvedItems.map(item => item.markdown) ?? [] : []);
  const count = { inputs: uniqueReferences([...pendingInputs, ...attached, ...prepared, ...frozen]).length, draft: 0, parameters: Math.max(confirmations.filter(item => item.status === 'PENDING').length, preparedParameterCount), results: outputs.length };
  const visibleDataFeedback = !collapsed && (tab === 'inputs' || tab === 'tasks') ? dataFeedback : null;

  const SubsectionTitle = routedTab ? 'h3' : 'h4';
  const renderReferences = (title: string, items: ResourceReference[], pending = false) => items.length > 0 &&
    <section className="workspace-data-group">
      <SubsectionTitle>{title}<span>{items.length}</span></SubsectionTitle>
      <ul>{items.map(item => {
        const key = referenceKey(item);
        const detail = referenceChecks[key];
        const check = typeof detail === 'object' ? 'readable' : detail;
        const label = { readable: '当前可读取', mismatch: '身份不一致', unavailable: '暂时无法核验', checking: '正在核验' };
        return <li key={key}>
          <div className="workspace-data-title"><Icon name="file" /><span>
            <strong>{'name' in item ? String((item as ResourceItem).name) : item.outputName ?? item.datasetId}</strong>
            <small>{item.format.toUpperCase()} · {shortVersion(item.versionId)}</small>
          </span></div>
          <span className={`reference-state is-${check ?? 'checking'}`}>{dataAvailable ? label[check ?? 'checking'] : '数据服务未配置'}</span>
          <ReferenceDetails item={item} />
          {dataAvailable && typeof detail === 'object' && <details className="workspace-reference-details">
            <summary>数据质量与来源</summary>
            <p>{detail.descriptor.profile ? '已提供字段统计' : '未提供全量字段统计'}</p>
            <dl>
              <dt>坐标参考系</dt><dd>{detail.semantics.crs || '未声明'}</dd>
              <dt>时间范围</dt><dd>{detail.semantics.timeRange || '未声明'}</dd>
              <dt>输入版本</dt><dd>{detail.inputVersions.join('、') || '未提供'}</dd>
            </dl>
            <DataFieldQuality descriptor={detail.descriptor} semantics={detail.semantics} />
          </details>}
          <div className="workspace-inline-actions">
            {dataAvailable && <button onClick={() => openInput(item.versionId, item)}>查看版本与内容</button>}
            {pending && <button onClick={() => onRemovePending(item)}>移除</button>}
          </div>
        </li>;
      })}</ul>
    </section>;

  const Panel = routedTab ? 'main' : 'section';
  const PanelTitle = routedTab ? 'h1' : 'h2';
  const SectionTitle = routedTab ? 'h2' : 'h3';
  const toggleLabel = routedTab ? '返回对话' : !collapsed ? '折叠任务工作台' : activeId ? '展开右侧任务栏' : '打开任务工作台';
  return <>{overlay && <button className="workspace-backdrop" tabIndex={-1} aria-hidden="true" data-overlay-dismiss onClick={onToggle} />}<Panel ref={panelRef} id="workspace-panel" hidden={collapsed && hideWhenCollapsed} tabIndex={overlay ? -1 : undefined} role={overlay ? 'dialog' : routedTab ? undefined : 'complementary'} aria-modal={overlay ? true : undefined} style={collapsed || overlay || routedTab ? undefined : { width, flexBasis: width }} className={`workspace-panel ${collapsed ? 'is-collapsed' : ''} ${fullscreen && !collapsed ? 'is-fullscreen' : ''} ${overlay ? 'is-overlay' : ''} ${routedTab ? 'is-route' : ''}`} aria-label="任务工作台" data-testid="workspace-panel">
    {!collapsed && !overlay && !routedTab && <div className="workspace-resizer" role="separator" tabIndex={0} aria-label="调整任务工作台宽度" aria-orientation="vertical" aria-valuemin={panelWidth.minimum} aria-valuemax={panelWidth.maximum} aria-valuenow={width} aria-valuetext={`${width} 像素`} onPointerDown={() => { resizing.current = true; }} onKeyDown={event => { const next = event.key === 'Home' ? panelWidth.minimum : event.key === 'End' ? panelWidth.maximum : event.key === 'ArrowLeft' ? width + 20 : event.key === 'ArrowRight' ? width - 20 : null; if (next !== null) { event.preventDefault(); setWidth(Math.max(panelWidth.minimum, Math.min(panelWidth.maximum, next))); } }} />}
    <div className="workspace-heading"><PanelTitle ref={routeHeading} id={routedTab ? "workspace-route-heading" : undefined} tabIndex={routedTab ? -1 : undefined}>{routedTab ? labels[routedTab] : collapsed ? '任务' : '任务工作台'}</PanelTitle><div className="workspace-controls">{!collapsed && !routedTab && <><button className={`icon-button ${pinned ? 'active' : ''}`} title={pinned ? '已固定，点击恢复自动切换' : '固定当前页签'} aria-label={pinned ? '恢复自动切换' : '固定当前页签'} onClick={() => { const next = !pinned; setPinned(next); sessionStorage.setItem(pinnedKey, next ? '1' : '0'); }}><Icon name="pin" /></button><button className="icon-button" aria-label={fullscreen ? '退出全屏' : '全屏任务工作台'} onClick={() => setFullscreen(value => !value)}><Icon name={fullscreen ? 'close' : 'arrow-up-right'} /></button></>}<button className="icon-button panel-toggle" aria-label={toggleLabel} title={toggleLabel} aria-expanded={routedTab ? undefined : !collapsed} aria-controls={routedTab ? undefined : 'workspace-panel-content'} onClick={onToggle}><Icon name={routedTab || collapsed ? 'arrow-left' : 'arrow-right'} />{routedTab && '返回对话'}</button></div></div>
    {!collapsed && <div className={`workspace-state is-${state.tone}`}>
      <div className="workspace-state-title"><small>任务状态</small>
        <Badge key={state.taskStatus} tone={state.tone === 'working' ? 'running' : state.tone}>{state.taskStatus}</Badge>
      </div>
      <small>本轮状态 · {state.turnStatus}</small>
    </div>}
    {((routedTab && feedback) || visibleDataFeedback) &&
      <div className="workspace-feedback">{routedTab && feedback}{visibleDataFeedback}</div>}
    <div id="workspace-panel-content" className="workspace-scroll" hidden={collapsed}>
      <nav className="workspace-tabs" role="tablist" aria-label="任务工作台视图">{tabs.map(item => <button key={item} id={`workspace-tab-${item}`} role="tab" tabIndex={tab === item ? 0 : -1} aria-selected={tab === item} aria-controls={`workspace-${item}`} className={tab === item ? 'active' : ''} onClick={() => chooseTab(item)} onKeyDown={event => tabKeyDown(event, item)}>{labels[item]}{item !== 'tasks' && count[item] > 0 && <span>{count[item]}</span>}{item === 'tasks' && <i className={`workspace-status-dot is-${state.tone}`} />}</button>)}</nav>
      {tab === 'inputs' && <div role="tabpanel" tabIndex={0} aria-labelledby="workspace-tab-inputs" id="workspace-inputs">
        <section className="workspace-section">
          <div className="workspace-section-heading"><SectionTitle>数据准备</SectionTitle><div className="workspace-inline-actions">
            {onChooseMethod && <button type="button" onClick={onChooseMethod}>{inputEditor ? '更换分析方法' : '选择分析方法'}</button>}
            {dataAvailable && <button className="primary" onClick={() => openDatabase()}>打开数据库选择数据</button>}
          </div></div>
          {inputEditor}
          {history?.preparation && <PreparationDetails history={history} dataAvailable={dataAvailable} onData={openDatabase} onVersion={openInput} onUseCurrentVersion={onUseCurrentVersion} onParameter={onResolveParameter} />}
          {count.inputs > 0 ? <div className="workbench-input-groups">
            {renderReferences('待发送', pendingInputs, true)}{renderReferences('已附加', attached)}
            {renderReferences('已准备', prepared)}{renderReferences('已冻结', frozen)}
          </div> : (!activeId || history) && <EmptyState title="尚未添加数据"
            description={dataAvailable ? '从数据库选择资料，或回到对话添加附件。' : '可在对话中查看分析所需的数据。'} />}
          {!count.inputs && unresolved.length > 0 && <div className="workspace-attention"><strong>需要补充</strong>{unresolved.map((item, index) => <p key={index}>{item}</p>)}</div>}
        </section>
      </div>}
      {tab === 'draft' && <div role="tabpanel" tabIndex={0} aria-labelledby="workspace-tab-draft" id="workspace-draft">
        <section className="workspace-section">
          <div className="workspace-section-heading"><SectionTitle>工作流草稿</SectionTitle>
            {draft?.payload?.executionAllowed && pendingConfirmation && isComposedConfirmation(pendingConfirmation) &&
              <button className="primary" onClick={() => chooseTab('parameters')}>核对执行确认</button>}
            {draft?.payload?.missingInputs.some(input => input.kind === 'DATA') &&
              <button onClick={() => chooseTab('inputs')}>补齐输入资料</button>}
          </div>
          {draft && <WorkflowDraftCard draft={draft} onKnowledge={onDraftKnowledge ?? (() => {})}
            onResource={(id, reference) => reference ? onInputResource(id, reference) : onResource(id)} />}
          {draftRead === 'loading' && <Loading>正在读取工作流草稿…</Loading>}
          {typeof draftRead === 'object' && <div className="workbench-read-error">
            <ErrorNotice error={draftRead.error} title="草稿暂时无法读取" /><button onClick={draftRead.retry}>重试读取草稿</button>
          </div>}
          {!draft && !draftRead && <EmptyState description="本轮尚未生成工作流草稿。" action={<button onClick={() => chooseTab('inputs')}>打开数据准备</button>} />}
        </section>
      </div>}
      {tab === 'parameters' && <div role="tabpanel" tabIndex={0} aria-labelledby="workspace-tab-parameters" id="workspace-parameters">{editing && capabilities ? <ConfirmationEditor confirmation={editing} stale={editing.confirmationId !== pendingConfirmation?.confirmationId} capabilities={capabilities} project={project} busy={busy || confirmationWriteDisabled} focusField={confirmationFocus?.field} suggestedValue={confirmationFocus?.value} onClose={onEditClose} onSave={actions => onRevision(editing, actions)} /> : <section className="workspace-section">
        <SectionTitle>执行确认复核</SectionTitle>
        {confirmationReview}
        {history?.preparation && preparationEntries.some(entry => Object.keys(entry.parameters).length > 0) && <details><summary>准备参数 · {!isCurrentPreparation(history) ? '需要本轮重新核验' : history.preparation.status === 'READY' ? '已通过合同检查' : '存在阻塞'}</summary>{preparationEntries.map((entry, entryIndex) => <div key={`${entry.targetId}/${entryIndex}`}><strong>{modelName(entry.targetId)}</strong><dl className="argument-list">{Object.entries(entry.parameters).map(([name, value]) => <div key={name}><dt>{argumentLabel(name, entry.targetId)}</dt><dd><ArgumentValue value={value} name={name} modelId={entry.targetId} /> · {entry.parameterSources[name] ?? '来源未记录'}</dd></div>)}</dl></div>)}</details>}{pendingConfirmation && <div className="workspace-confirmation-summary">
          {!confirmationReview && <>
            <strong>待确认 · 修订 {pendingConfirmation.revision ?? 1}</strong>
            <p>{isComposedConfirmation(pendingConfirmation) ? '组合工作流' : pendingConfirmation.actions.map(action => modelName(action.modelId)).join('、')}</p>
          </>}
          {onCurrentConfirmation && <button onClick={onCurrentConfirmation}>查看当前确认</button>}
          {isComposedConfirmation(pendingConfirmation) ? <p>调整资料或参数时，请在对话中生成新草稿。</p>
            : <button disabled={busy || confirmationWriteDisabled} onClick={() => onEdit(pendingConfirmation.confirmationId)}>编辑当前参数</button>}
        </div>}
        {confirmations.some(item => item.confirmationId !== pendingConfirmation?.confirmationId) && <details>
          <summary>历史批次</summary>
          <ol className="workspace-confirmations">{[...confirmations].reverse().filter(item => item.confirmationId !== pendingConfirmation?.confirmationId).map(item => (
            <li key={item.confirmationId}>
              <details><summary>修订 {item.revision ?? 1} · {statusLabel[item.status] ?? '历史状态'}</summary>
                {item.actions.map(action => <div key={action.toolCallId}><b>{modelName(action.modelId)}</b><dl>{Object.entries(action.arguments).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{JSON.stringify(value)}</dd></div>)}</dl></div>)}
              </details>
            </li>
          ))}</ol>
        </details>}
        {confirmations.length === 0 && <p className="workspace-empty">当前没有确认参数批次。</p>}</section>}</div>}
      {tab === 'tasks' && <div role="tabpanel" tabIndex={0} aria-labelledby="workspace-tab-tasks" id="workspace-tasks"><section className="workspace-section"><div className="workspace-section-heading"><SectionTitle>{task ? '研究任务详情' : '任务事实'}</SectionTitle>{!task && turn && <button className="text-button" onClick={() => openConversationTurn(turn.goalRevision)}>查看本轮回答<Icon name="arrow-up-right" /></button>}</div>{task ? <TaskCard key={`${task.spec.taskId}/${task.spec.revision}`} task={task} disabled={busy || taskReviewDisabled}
        turns={history?.turns} results={results} onEvidenceTurn={openConversationTurn} onReview={onReview}
        onRequestChanges={onRequestTaskChanges} onResource={onResource} /> : unresolved.length ? <div className="workspace-attention"><strong>需要补充信息</strong>{unresolved.map((item, index) => <p key={index}>{item}</p>)}{dataAvailable && <button onClick={() => { setTab('inputs'); openDatabase(); }}>打开数据库</button>}</div> : <p className="workspace-empty">本轮未创建持续任务。</p>}
      {history?.preparation && <PreparationDetails history={history} dataAvailable={dataAvailable} onData={openDatabase} onVersion={openInput} onUseCurrentVersion={onUseCurrentVersion} onParameter={onResolveParameter} />}
      {history?.turns.flatMap(item => item.plans.map(plan => ({ ...plan, goalRevision: item.goalRevision }))).map(plan => <details key={`${plan.goalRevision}/${plan.revision}`}><summary>Agent Plan 第 {plan.revision} 版</summary><p>{plan.goal}</p><ol>{plan.steps.map(step => <li key={step.stepId}>{step.purpose}</li>)}</ol></details>)}
      <div className="workspace-section-heading execution-heading">
        <SectionTitle>会话执行记录</SectionTitle>
        {executionRecords.length > 1 && <div className="execution-navigation">
          <label>定位运行尝试<select value={selectedRunId} onChange={event => selectRun(event.target.value)}>
            <option value="">选择运行尝试</option>
            {executionRecords.map(({ run }) => <option key={run.workflowRunId} value={run.workflowRunId}>
              {modelName(run.modelId)} · 第 {run.goalRevision} 轮第 {run.position + 1} 项{run.attempt == null ? '' : ` · 尝试 ${run.attempt}`}
            </option>)}
          </select></label>
          <button type="button" disabled={!selectedRunId} onClick={() => selectRun(selectedRunId, true)}>跳到所选尝试</button>
        </div>}
      </div>
      {executionRecords.length > 0 ? <ol className="workspace-tasks">{executionRecords.map(({ turn, run: task }) => {
        const view = runById.get(task.workflowRunId);
        const confirmation = turn.confirmations.find(item => item.confirmationId === task.confirmationId
          && item.agentRunId === task.agentRunId && item.goalRevision === task.goalRevision);
        const action = confirmation?.actions.find(item => item.toolCallId === task.toolCallId
          && item.modelId === task.modelId && item.modelVersion === task.modelVersion
          && item.workflowId === task.workflowId && item.workflowVersion === task.workflowVersion);
        return (
        <li key={task.workflowRunId} data-execution-run-id={task.workflowRunId} tabIndex={-1}
          aria-current={selectedRunId === task.workflowRunId || undefined} className={task.isLatestAttempt === false ? 'is-history' : ''}>
          <span>{modelName(task.modelId)}<small>第 {task.goalRevision} 轮 · 第 {task.position + 1} 项
            {task.attempt === undefined ? ' · 尝试编号未提供' : ` · 第 ${task.attempt} 次`}
            {task.durationMs == null ? '' : ` · ${task.durationMs.toLocaleString()} 毫秒`}</small></span>
          <Badge tone={task.blockedByWorkflowRunId ? 'warning' : view?.status.tone}>
            {task.blockedByWorkflowRunId ? '因前项失败未执行' : view?.status.label ?? taskLabel(task)}
          </Badge>
          <div className="run-record-details">
            <ol className="workbench-run-timeline" aria-label="运行时间线">
              <li data-phase="registered"><strong>运行登记</strong><time dateTime={task.createdAt}>{new Date(task.createdAt).toLocaleString()}</time></li>
              {task.startedAt && <li><strong>执行开始</strong><time dateTime={task.startedAt}>{new Date(task.startedAt).toLocaleString()}</time></li>}
              {task.finishedAt ? <li data-tone={view?.status.tone}><strong>{view?.status.label ?? taskLabel(task)}</strong><time dateTime={task.finishedAt}>{new Date(task.finishedAt).toLocaleString()}</time></li>
                : view?.stage && <li data-tone="info" aria-current="step"><strong>{view.stage.label}</strong><span>当前阶段</span></li>}
            </ol>
            {view?.communication && <p>{view.communication.label}</p>}
            {task.status === 'SUCCEEDED' && task.outputCount > 0 && <p>{view?.publication.label}</p>}
            {project && <WorkflowPublicationDetails key={`${project}/${task.workflowRunId}`}
              project={project} runId={task.workflowRunId} status={task.status} />}
            {confirmation && <p>确认修订 {confirmation.revision ?? 1} · {statusLabel[confirmation.status] ?? '状态待核实'}</p>}
            <details><summary>运行标识与时间</summary>
              <dl className="argument-list">
                <div><dt>运行 ID</dt><dd><code>{task.workflowRunId}</code></dd></div>
                <div><dt>模型版本</dt><dd>{task.modelId} · {task.modelVersion}</dd></div>
                <div><dt>工作流版本</dt><dd>{task.workflowId} · {task.workflowVersion}</dd></div>
                {task.confirmationId && <div><dt>确认 ID</dt><dd><code>{task.confirmationId}</code></dd></div>}
                <div><dt>创建时间</dt><dd><time dateTime={task.createdAt}>{new Date(task.createdAt).toLocaleString()}</time></dd></div>
                {task.startedAt && <div><dt>开始时间</dt><dd><time dateTime={task.startedAt}>{new Date(task.startedAt).toLocaleString()}</time></dd></div>}
                {task.finishedAt && <div><dt>结束时间</dt><dd><time dateTime={task.finishedAt}>{new Date(task.finishedAt).toLocaleString()}</time></dd></div>}
              </dl>
            </details>
            {task.retryOfWorkflowRunId && <details><summary>恢复来源</summary><code>{task.retryOfWorkflowRunId}</code></details>}
            {task.blockedByWorkflowRunId && <details><summary>阻塞来源</summary><code>{task.blockedByWorkflowRunId}</code></details>}
            {action && <details><summary>查看冻结输入与参数</summary>
              {Object.entries(action.inputReferences ?? {}).map(([name, ref]) => <p key={name}>
                <button type="button" onClick={() => openInput(ref.versionId, ref)}>{argumentLabel(name, action.modelId)} · {shortVersion(ref.versionId)}</button>
              </p>)}
              <dl className="argument-list">{Object.entries(action.arguments).map(([name, value]) =>
                <div key={name}><dt>{argumentLabel(name, action.modelId)}</dt><dd><ArgumentValue value={value} name={name} modelId={action.modelId} /></dd></div>)}</dl>
            </details>}
            {view?.outputs.map(ref => <button key={referenceKey(ref)} type="button" onClick={() => onResource(ref.versionId, ref)}>
              {ref.outputName ? outputName(ref.outputName) : '查看成果'}
            </button>)}
          </div>
          {task.errorCode && <small className="workflow-task-error">{errorCodeMessage(task.errorCode)}</small>}
          {project && <WorkflowNodeDetails key={project} runId={task.workflowRunId} project={project} status={task.status} />}
        </li>
      ); })}</ol> : <p className="workspace-empty">暂无执行记录。</p>}{recovery && <RecoveryCard actions={<>
        <button className="primary recover-button" disabled={busy || !!pendingConfirmation} onClick={() => onRecover(recovery.source.workflowRunId)}>准备恢复未完成步骤</button>
        {dataAvailable && <button className="text-button" onClick={() => openDatabase()}>修改输入后发起新分析</button>}
      </>}>
        <p><strong>{modelName(recovery.source.modelId)}</strong> · {taskLabel(recovery.source)}</p>
        <p>沿用原批次冻结的输入与参数，从第 {recovery.source.position + 1} 项开始恢复 {recovery.count} 项分析。新的恢复批次需要确认。</p>
      </RecoveryCard>}
      {(task || turn?.agentMetrics) && <details className="workspace-usage"><summary>技术用量</summary>{task && <p>任务累计：{task.progress.metrics.totalTokens} 词元，{Math.round(task.progress.metrics.durationMs / 1000)} 秒</p>}{turn?.agentMetrics && <p>本轮：模型请求 {turn.agentMetrics.requestCount} 次，查证 {turn.agentMetrics.observationCallCount} 次，{agentTokenUsageLabel(turn.agentMetrics)}</p>}</details>}</section></div>}
      {tab === 'results' && <div role="tabpanel" tabIndex={0} aria-labelledby="workspace-tab-results" id="workspace-results"><section className="workspace-section artifact-section"><div className="workspace-section-heading"><SectionTitle tabIndex={-1} ref={resultHeading}>{selectedOutput?.outputName ? outputName(selectedOutput.outputName) : resource?.title ? resourceTitle(resource.title) : '已发布成果'}</SectionTitle>{selectedResourceId && <button className="text-button" onClick={() => { resultFocus.current = 'list'; onResource(''); }}>返回成果列表</button>}</div>{publicationReadFeedback}{resourceError && <div className="workbench-read-error"><ErrorNotice error={resourceError} title="成果读取未完成" /><button onClick={() => setResourceRetry(value => value + 1)}>重新读取成果</button></div>}{resourceLoading ? <div className="workbench-loading"><p className="muted" role="status">正在读取成果信息…</p><div className="workbench-skeleton" aria-hidden="true"><span /><span /></div></div> : resource ? <>{publishedOutput && <section className="result-review-summary" aria-label="成果复核">
      <SubsectionTitle>科研结果摘要</SubsectionTitle>
      {resource.semantics.description && <p className="result-review-description">{resource.semantics.description}</p>}
      {resultRun && overview && hasAnalysisSummary && <details open={analysisOpen} onToggle={event => setClosedAnalysis(event.currentTarget.open ? null : resultRun.workflowRunId)}>
        <summary>查看本次运行的科学指标</summary>
        {analysisOpen && <RunInsights key={resultRun.workflowRunId} overview={overview} project={project} outputs={resultRun.outputs}
          onResource={onResource} />}
      </details>}
      {!!resource.semantics.limitations?.length && <div className="result-review-limits"><strong>适用限制</strong><ul>{resource.semantics.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul></div>}
      <dl className="result-review-verification">
        <div><dt>计算</dt><dd>{resultRun?.status.label ?? '运行关联未提供'}</dd></div>
        <div><dt>发布</dt><dd>已读取精确成果版本</dd></div>
        <div><dt>科学验证</dt><dd>{resultRun?.scientificValidation.label ?? '科学验证状态未提供'}</dd></div>
        <div><dt>记录数</dt><dd>{resource.descriptor.rowCount?.toLocaleString() ?? '未统计'}</dd></div>
      </dl>
      {resultRun && <details><summary>查看运行身份</summary>
        {execution && <p>{modelName(execution.modelId)} · 工作流版本 {execution.workflowVersion}</p>}
        <code>{resultRun.workflowRunId}</code>
      </details>}
      {execution && <div><button className="text-button" onClick={() => selectRun(execution.workflowRunId, true)}>查看来源运行</button></div>}
      {!!resource.descriptor.fields?.length && <details><summary>查看 Data 字段质量</summary><DataFieldQuality descriptor={resource.descriptor} semantics={resource.semantics} /></details>}
    </section>}<Suspense fallback={<p className="muted" role="status">正在加载成果查看器…</p>}><ArtifactViewer key={`${resource.versionId}:${resource.semanticId}:${selectedOutput?.objectId ?? ""}`} resource={resource} objectId={selectedOutput?.objectId} allowDownload={!selectedOutput} contextHeading={SubsectionTitle} project={project} preferredViewer={selectedOutput?.outputName ? productUi().outputs[selectedOutput.outputName]?.viewer : 'auto'} onResource={onResource} /></Suspense>{publishedOutput && <div className="workspace-inline-actions result-route-actions">
          <a className="button" href={contentUrl(project, publishedOutput.versionId, publishedOutput.objectId)} download={resultDownloadName(publishedOutput)}>下载</a>
          {onCompareResult && <button onClick={() => onCompareResult(publishedOutput)}>与另一版本比较</button>}
          {onAttachResult && <button onClick={() => onAttachResult(publishedOutput)}>附加到当前草稿</button>}
          {onContinueResult && <button disabled={continueResultDisabled} onClick={() => onContinueResult(publishedOutput)}>带成果打开新会话</button>}
        </div>}</> : selectedResourceId ? null : outputs.length ? <ul className="workspace-outputs">{outputs.map(reference => <li key={referenceKey(reference)}><button type="button" data-result-list-key={referenceKey(reference)} onClick={() => {
          listOrigin.current = { scope: referenceScope, key: referenceKey(reference) };
          resultFocus.current = 'detail';
          onResource(reference.versionId, reference);
        }}><Icon name={reference.mediaType === 'image/png' ? 'map' : 'file'} /><span><strong>{reference.outputName ? outputName(reference.outputName) : '结果文件'}</strong><small title={reference.versionId}>{reference.format.toUpperCase()} · 版本 {shortVersion(reference.versionId)}</small></span><Icon name="arrow-up-right" /></button></li>)}</ul> : !publicationReadFeedback && <p className="workspace-empty">暂无已发布成果。</p>}</section></div>}
    </div>
  </Panel></>;
}

function RunInsights({ overview, outputs, project, onResource }: {
  overview: NonNullable<ReturnType<typeof productUi>['models'][string]['resultOverview']>;
  outputs: readonly ResourceReference[];
  project: string;
  onResource: (id: string, reference?: ResourceReference) => void;
}) {
  const insight = overview.insight;
  if (!insight) return null;
  function namedOutput(name?: string) {
    const matches = outputs.filter(output => output.outputName === name);
    return name && matches.length === 1 ? matches[0] : undefined;
  }
  const summary = namedOutput(insight.output);
  const trend = insight.kind === 'trend' ? namedOutput(overview.sections[0]?.outputs[0]) : undefined;
  const sources = uniqueReferences([summary, trend].filter(isResourceReference));
  if (!sources.length) return <p className="muted">本次运行尚未提供可读取的指标来源。</p>;
  return <div className="workbench-run-insights">
    <div className="workspace-inline-actions">{sources.map(ref => <button key={referenceKey(ref)} onClick={() => onResource(ref.versionId, ref)}>
      {outputName(ref.outputName ?? '')} · {shortVersion(ref.versionId)}
    </button>)}</div>
    <Suspense fallback={<Loading>正在加载科学指标…</Loading>}>
      <ResultInsights overview={insight} summary={summary} trend={trend} visuals={[]} project={project} />
    </Suspense>
  </div>;
}
