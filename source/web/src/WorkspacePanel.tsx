import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, callApi, errorCodeMessage } from './api';
import { decodeResourceDetail } from './api-contract';
import { agentTokenUsageLabel, taskLabel } from './chat-state';
import { argumentLabel, modelName, outputName, resourceTitle } from './card-presentation';
import { productUi } from './product-ui';
import { ConfirmationEditor } from './ConfirmationEditor';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { readEnumCache, readFlagCache, readNumberCache } from './browser-cache';
import type { Capabilities, Confirmation, History, ResourceDetail, ResourceItem, ResourceReference, Result, TaskPreparationEntry } from './types';
import type { ConfirmationFocus } from './use-workspace-controller';
import { scopedStorageKey } from './browser-scope';

export type WorkspaceTab = 'inputs' | 'parameters' | 'tasks' | 'results';
const tabs: readonly WorkspaceTab[] = ['inputs', 'parameters', 'tasks', 'results'];
const labels: Record<WorkspaceTab, string> = { inputs: '数据', parameters: '参数', tasks: '任务', results: '成果' };
const ArtifactViewer = lazy(() => import('./ArtifactViewer').then(module => ({ default: module.ArtifactViewer })));

export interface WorkspaceState { taskStatus: string; turnStatus: string; tone: 'neutral' | 'working' | 'warning' | 'error' | 'success'; recommendedTab: WorkspaceTab }

export function resolveWorkspaceState(history: History | null): WorkspaceState {
  const turn = history?.turns.find(item => item.goalRevision === history.goalRevision);
  if (!turn) return { taskStatus: history ? '暂无任务' : '正在读取会话', turnStatus: '本轮尚未开始', tone: 'neutral', recommendedTab: 'tasks' };
  if (['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(turn.status)) return {
    taskStatus: turn.status === 'TIMED_OUT' ? '执行已超时' : turn.status === 'CANCELLED' ? '任务已取消' : '本轮未完成',
    turnStatus: turn.errorCode ? errorCodeMessage(turn.errorCode) : '请查看失败记录', tone: 'error', recommendedTab: 'tasks',
  };
  const running = turn.executionTasks.find(item => ['QUEUED', 'RUNNING'].includes(item.status));
  if (running) return { taskStatus: running.stage === 'PUBLISHING' ? '正在发布成果' : '正在执行', turnStatus: taskLabel(running), tone: 'working', recommendedTab: 'tasks' };
  if (turn.confirmations.some(item => item.status === 'PENDING')) return { taskStatus: '等待确认', turnStatus: '执行参数已准备，请核对后决定', tone: 'warning', recommendedTab: 'parameters' };
  if (['ROUTING', 'QUEUED', 'RUNNING', 'WAITING_EXECUTION'].includes(turn.status)) return { taskStatus: '正在处理', turnStatus: turn.status === 'ROUTING' ? '正在判断需求' : '本轮处理进行中', tone: 'working', recommendedTab: 'tasks' };
  if (history?.task) {
    const { lifecycle, state } = history.task.progress;
    const criteria = new Map(history.task.progress.criteria.map(item => [item.id, item.satisfied]));
    const allCriteriaSatisfied = history.task.spec.criteria.every(item => criteria.get(item.id) === true);
    if (lifecycle === 'COMPLETE' && allCriteriaSatisfied) return { taskStatus: '任务已完成', turnStatus: '本轮回答已生成', tone: 'success', recommendedTab: history.task.progress.results.length ? 'results' : 'tasks' };
    const stateLabel: Record<string, string> = { WAITING_INPUT: '需要补充信息', WAITING_REVIEW: '等待业务评审', WAITING_CONFIRMATION: '等待确认', BLOCKED: '任务受阻', EXECUTING: '正在执行', ANALYZING: '正在分析' };
    const taskStatus = lifecycle === 'COMPLETE' ? '等待验收条件完成' : stateLabel[state] ?? '任务进行中';
    return { taskStatus, turnStatus: '本轮状态已更新', tone: lifecycle === 'COMPLETE' || state === 'WAITING_INPUT' || state === 'BLOCKED' ? 'warning' : 'working', recommendedTab: state === 'WAITING_INPUT' ? 'inputs' : state === 'WAITING_CONFIRMATION' ? 'parameters' : 'tasks' };
  }
  if (turn.reply?.unresolvedItems.length) return { taskStatus: '需要补充信息', turnStatus: '本轮回答已生成', tone: 'warning', recommendedTab: 'inputs' };
  return { taskStatus: '本轮回答已完成', turnStatus: '本轮回答已生成', tone: 'success', recommendedTab: 'tasks' };
}

function uniqueReferences(items: ResourceReference[]) {
  const seen = new Set<string>();
  return items.filter(item => { const key = `${item.versionId}/${item.objectId}`; if (seen.has(key)) return false; seen.add(key); return true; });
}

function ReferenceDetails({ item }: { item: ResourceReference }) {
  return <details className="workspace-reference-details"><summary>查看精确身份</summary>
    <dl><dt>Dataset</dt><dd>{item.datasetId}</dd><dt>Version</dt><dd>{item.versionId}</dd><dt>Semantic</dt><dd>{item.semanticId}</dd><dt>Object</dt><dd>{item.objectId}</dd><dt>checksum</dt><dd>{item.checksum}</dd><dt>MIME</dt><dd>{item.mediaType}</dd><dt>大小</dt><dd>{item.sizeBytes.toLocaleString()} B</dd></dl>
  </details>;
}

function preparationIssues(
  entry: TaskPreparationEntry,
): NonNullable<TaskPreparationEntry['blockingIssues']> {
  if (entry.blockingIssues?.length) return entry.blockingIssues;
  return entry.blockingReasons.map(code => {
    const data = /^WORKFLOW_DRAFT_MISSING_DATA:(.+)$/.exec(code);
    const parameter = /^WORKFLOW_DRAFT_MISSING_PARAMETER:(.+)$/.exec(code) ?? /^validator:(.+)$/.exec(code);
    if (data) return { code, kind: 'MISSING_DATA' as const, input: data[1] };
    if (parameter) return { code, kind: 'MISSING_PARAMETER' as const, parameter: parameter[1] };
    if (code === 'WORKFLOW_DRAFT_UNRESOLVED_ITEMS') return { code, kind: 'UNRESOLVED_ITEM' as const };
    if (code === 'DATA_VERSION_NOT_CURRENT') return { code, kind: 'DATA_VERSION_NOT_CURRENT' as const };
    return { code, kind: 'UNKNOWN' as const };
  });
}

export function PreparationDetails({ history, dataAvailable = true, onData, onVersion, onParameter }: { history: History; dataAvailable?: boolean; onData: (formats: string[]) => void; onVersion: (id: string, input?: string) => void; onParameter: (name: string) => void }) {
  const entries = history.preparation?.entries ?? [];
  return <details open><summary>输入准备 · {!history.preparation?.freshForCurrentTurn ? '需要本轮重新核验' : history.preparation.status === 'READY' ? '合同可提案' : '需要补充'}</summary><p>准备修订 {history.preparation?.revision} · {history.preparation?.disposition}</p>{entries.map((entry, entryIndex) => <section key={`${entry.targetId}/${entryIndex}`}><h4>{modelName(entry.targetId)}</h4>{entry.knowledgeRefs.length > 0 && <p>已核验知识证据 {entry.knowledgeRefs.length} 项</p>}{entry.checks.map((check, index) => {
    const inputVersion = check.input ? entry.inputs[check.input]?.versionId : undefined;
    const needsAdmin = check.status === 'UNKNOWN' && (check.kind === 'FIELD_METADATA' || check.kind === 'FIELD_SEMANTICS');
    return <div className="preparation-check" key={`${check.kind}/${check.input ?? index}`}><span>{check.status === 'PASS' ? '通过' : check.status === 'UNKNOWN' ? '待判断' : '失败'} · {check.input ? `${argumentLabel(check.input, entry.targetId)} · ` : ''}{check.kind}</span>{dataAvailable && check.currentVersion && check.status === 'FAIL' && <button onClick={() => onVersion(check.currentVersion!, check.input)}>使用当前版本</button>}{needsAdmin && inputVersion && <><p className="muted">需要 Data 管理员补充字段元数据或语义；主工作区不修改这些事实。</p>{dataAvailable && <button onClick={() => onVersion(inputVersion)}>查看数据详情</button>}</>}</div>;
  })}{preparationIssues(entry).map((issue, index) => {
    const formats = issue.input ? productUi().models[entry.targetId]?.inputs[issue.input]?.formats ?? [] : [];
    const currentVersion = issue.input ? entry.checks.find(check => check.input === issue.input && check.currentVersion)?.currentVersion : undefined;
    return <div className="preparation-blocker" key={`${issue.code}/${issue.path ?? issue.input ?? issue.parameter ?? index}`}><span>阻塞：{issue.code}{issue.path ? ` · ${issue.path}` : ''}</span>
      {issue.kind === 'MISSING_DATA' && dataAvailable && <button onClick={() => onData(formats)}>选择所需数据</button>}
      {(issue.kind === 'MISSING_PARAMETER' || issue.kind === 'INVALID_PARAMETER') && issue.parameter && <button onClick={() => onParameter(issue.parameter!)}>补充参数</button>}
      {issue.kind === 'UNRESOLVED_ITEM' && <button onClick={() => onParameter('未解决事项')}>补充未解决事项</button>}
      {issue.kind === 'DATA_VERSION_NOT_CURRENT' && currentVersion && dataAvailable && <button onClick={() => onVersion(currentVersion, issue.input)}>使用当前版本</button>}
    </div>;
  })}</section>)}</details>;
}

export function WorkspacePanel({ history, results, activeId, storageScope = '', project = '', capabilities = null, dataAvailable = true, collapsed, pendingInputs = [], selectedResourceId = null, editingConfirmationId = null, confirmationFocus = null, focusRequest, busy = false, onToggle, onJump, onResource = () => {}, onInputResource = () => {}, onOpenDatabase = () => {}, onRemovePending = () => {}, onEdit = () => {}, onResolveParameter = () => {}, onEditClose = () => {}, onRevision = () => {}, onRecover = () => {}, onReview = () => {} }: {
  history: History | null; results: Result[]; activeId: string; storageScope?: string; project?: string; capabilities?: Capabilities | null; dataAvailable?: boolean; collapsed: boolean;
  pendingInputs?: ResourceItem[]; selectedResourceId?: string | null; editingConfirmationId?: string | null; confirmationFocus?: ConfirmationFocus; focusRequest?: { tab: WorkspaceTab; nonce: number } | null; busy?: boolean;
  onToggle: () => void; onJump: (revision: number) => void; onResource?: (id: string) => void; onInputResource?: (id: string, input?: string) => void; onOpenDatabase?: (formats?: string[]) => void; onRemovePending?: (id: string) => void; onEdit?: (id: string) => void; onResolveParameter?: (name: string) => void; onEditClose?: () => void;
  onRevision?: (confirmation: Confirmation, actions: { toolCallId: string; arguments: Record<string, unknown> }[]) => void; onRecover?: (workflowRunId: string) => void; onReview?: (text: string) => void;
}) {
  const turn = history?.turns.find(item => item.goalRevision === history.goalRevision);
  const confirmations = history?.turns.flatMap(item => item.confirmations.map(value => ({ ...value, goalRevision: item.goalRevision }))) ?? [];
  const pendingConfirmation = [...confirmations].reverse().find(item => item.status === 'PENDING') ?? null;
  const editing = confirmations.find(item => item.confirmationId === editingConfirmationId) ?? null;
  const attached = uniqueReferences(history?.items.filter(item => item.role === 'user').flatMap(item => item.inputReferences ?? []) ?? []);
  const frozen = uniqueReferences(confirmations.flatMap(item => item.actions.flatMap(action => Object.values(action.inputReferences ?? {}))));
  const preparationEntries = history?.preparation?.entries ?? [];
  const preparationActive = !!history?.preparation?.freshForCurrentTurn && ['ACTIVE', 'WAITING_CONFIRMATION', 'EXECUTING'].includes(history.preparation.disposition);
  const prepared = preparationActive ? uniqueReferences(preparationEntries.flatMap(entry => Object.values(entry.inputs))) : [];
  const preparedParameterCount = preparationActive ? new Set(preparationEntries.flatMap(entry => Object.keys(entry.parameters))).size : 0;
  const taskOutputs = history?.task?.progress.results ?? [];
  const outputEntries = [...results.filter(result => result.status === 'COMPLETE').flatMap(result => result.remoteOutputs.map(reference => ({ reference, revision: result.goalRevision, workflowRunId: result.workflowRunId }))), ...taskOutputs.map(reference => ({ reference, revision: history?.task?.spec.revision ?? 0, workflowRunId: null }))];
  const outputs = [...new Map(outputEntries.reverse().map(item => [item.reference.versionId, item])).values()];
  const selectedOutput = outputs.find(output => output.reference.versionId === selectedResourceId)?.reference;
  const state = resolveWorkspaceState(history);
  const tabKey = scopedStorageKey(storageScope, 'workspace-tab', activeId);
  const pinnedKey = scopedStorageKey(storageScope, 'workspace-pinned', activeId);
  const widthKey = scopedStorageKey(storageScope, 'workspace-width', activeId);
  const [tab, setTab] = useState<WorkspaceTab>(() => readEnumCache(tabKey, tabs, 'tasks'));
  const [pinned, setPinned] = useState(() => readFlagCache(pinnedKey));
  const [fullscreen, setFullscreen] = useState(false);
  const [width, setWidth] = useState(() => readNumberCache(widthKey, 460, 360, 760));
  const [resource, setResource] = useState<ResourceDetail | null>(null);
  const [resourceError, setResourceError] = useState<ApiError | null>(null);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [referenceChecks, setReferenceChecks] = useState<Record<string, 'readable' | 'mismatch' | 'unavailable'>>({});
  const [mobile, setMobile] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 640px)').matches);
  const resizing = useRef(false);
  const panelRef = useRef<HTMLElement>(null);
  const toggleRef = useRef(onToggle);
  const previousSnapshot = useRef('');
  const previousCounts = useRef({ attached: 0, outputs: 0 });

  useEffect(() => { setPinned(readFlagCache(pinnedKey)); setTab(readEnumCache(tabKey, tabs, 'tasks')); setWidth(readNumberCache(widthKey, 460, 360, 760)); setResource(null); }, [activeId, storageScope]);
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
  useEffect(() => { toggleRef.current = onToggle; }, [onToggle]);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(max-width: 640px)');
    const update = () => setMobile(query.matches);
    update(); query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => { const move = (event: PointerEvent) => { if (resizing.current) setWidth(Math.max(360, Math.min(760, window.innerWidth - event.clientX - 10))); }; const up = () => { resizing.current = false; }; window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); }; }, []);
  useEffect(() => {
    setResource(null); setResourceError(null);
    if (!selectedResourceId) { setResourceLoading(false); return; }
    const controller = new AbortController(); setResourceLoading(true);
    void callApi('data_get_version', `/data/versions/${encodeURIComponent(selectedResourceId)}`, undefined, controller.signal, decodeResourceDetail).then(value => { setResource(value); setResourceLoading(false); }).catch(caught => { if (caught instanceof DOMException && caught.name === 'AbortError') return; setResourceError(caught as ApiError); setResourceLoading(false); });
    return () => controller.abort();
  }, [selectedResourceId, project]);
  useEffect(() => {
    const all = uniqueReferences([...pendingInputs, ...attached, ...prepared, ...frozen]);
    const controllers = all.map(item => {
      const controller = new AbortController();
      void callApi('data_get_version', `/data/versions/${encodeURIComponent(item.versionId)}`, undefined, controller.signal, decodeResourceDetail).then(detail => {
        const object = detail.objects.find(value => value.objectId === item.objectId);
        const matches = detail.datasetId === item.datasetId && detail.semanticId === item.semanticId && !!object && object.checksum === item.checksum && object.format === item.format && object.mediaType === item.mediaType;
        setReferenceChecks(previous => ({ ...previous, [`${item.versionId}/${item.objectId}`]: matches ? 'readable' : 'mismatch' }));
      }).catch(caught => { if (!(caught instanceof DOMException && caught.name === 'AbortError')) setReferenceChecks(previous => ({ ...previous, [`${item.versionId}/${item.objectId}`]: 'unavailable' })); });
      return controller;
    });
    return () => controllers.forEach(controller => controller.abort());
  }, [JSON.stringify([...pendingInputs, ...attached, ...prepared, ...frozen].map(item => [item.versionId, item.objectId, item.checksum]))]);
  useEffect(() => {
    if (collapsed) return;
    const prior = document.body.style.overflow;
    if (mobile) { document.body.style.overflow = 'hidden'; panelRef.current?.querySelector<HTMLElement>('button')?.focus(); }
    const key = (event: KeyboardEvent) => {
      if (typeof window.matchMedia !== 'function' || !window.matchMedia('(max-width: 640px)').matches) return;
      if (event.key === 'Escape') toggleRef.current();
      if (event.key === 'Tab' && panelRef.current) { const nodes = [...panelRef.current.querySelectorAll<HTMLElement>('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(node => !node.hasAttribute('disabled')); if (!nodes.length) return; const first = nodes[0]; const last = nodes[nodes.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    };
    document.addEventListener('keydown', key);
    return () => { if (mobile) document.body.style.overflow = prior; document.removeEventListener('keydown', key); if (mobile) requestAnimationFrame(() => panelRef.current?.querySelector<HTMLElement>('.panel-toggle')?.focus()); };
  }, [collapsed, mobile]);

  function chooseTab(value: WorkspaceTab) { setTab(value); setPinned(true); sessionStorage.setItem(pinnedKey, '1'); }
  const latestFailed = useMemo(() => history?.turns.flatMap(item => item.executionTasks).filter(item => item.isLatestAttempt && ['FAILED', 'TIMED_OUT'].includes(item.status)).at(-1), [history]);
  const unresolved = history?.task?.spec.unresolvedItems ?? turn?.reply?.unresolvedItems.map(item => item.markdown) ?? [];
  const count = { inputs: uniqueReferences([...pendingInputs, ...attached, ...prepared, ...frozen]).length, parameters: Math.max(confirmations.filter(item => item.status === 'PENDING').length, preparedParameterCount), results: outputs.length };

  const renderReferences = (title: string, items: ResourceReference[], stage: 'pending' | 'attached' | 'frozen') => <section className="workspace-data-group"><h4>{title}<span>{items.length}</span></h4>{items.length ? <ul>{items.map(item => { const key = `${item.versionId}/${item.objectId}`; const check = referenceChecks[key]; return <li key={key}><div className="workspace-data-title"><Icon name="file" /><span><strong>{'name' in item ? String((item as ResourceItem).name) : item.outputName ?? item.datasetId}</strong><small>{item.format.toUpperCase()} · {item.versionId.slice(0, 12)}</small></span></div><span className={`reference-state is-${check ?? 'checking'}`}>{check === 'readable' ? '当前可读取' : check === 'mismatch' ? '身份不一致' : check === 'unavailable' ? '暂时无法核验' : '正在核验'}</span><ReferenceDetails item={item} /><div className="workspace-inline-actions"><button onClick={() => onInputResource(item.versionId)}>查看版本与内容</button>{stage === 'pending' && <button onClick={() => onRemovePending(item.versionId)}>移除</button>}</div></li>; })}</ul> : <p className="workspace-empty">暂无数据</p>}</section>;

  return <aside ref={panelRef} id="workspace-panel" style={collapsed || fullscreen ? undefined : { width, flexBasis: width }} className={`workspace-panel ${collapsed ? 'is-collapsed' : ''} ${fullscreen ? 'is-fullscreen' : ''}`} aria-label="任务工作台" data-testid="workspace-panel">
    {!collapsed && !fullscreen && <div className="workspace-resizer" role="separator" aria-label="调整任务工作台宽度" onPointerDown={() => { resizing.current = true; }} />}
    <div className="workspace-heading"><h2>{collapsed ? '任务' : '任务工作台'}</h2><div className="workspace-controls">{!collapsed && <><button className={`icon-button ${pinned ? 'active' : ''}`} title={pinned ? '已固定，点击恢复自动切换' : '固定当前页签'} aria-label={pinned ? '恢复自动切换' : '固定当前页签'} onClick={() => { const next = !pinned; setPinned(next); sessionStorage.setItem(pinnedKey, next ? '1' : '0'); }}><Icon name="pin" /></button><button className="icon-button" aria-label={fullscreen ? '退出全屏' : '全屏任务工作台'} onClick={() => setFullscreen(value => !value)}><Icon name={fullscreen ? 'close' : 'arrow-up-right'} /></button></>}<button className="icon-button panel-toggle" aria-label={collapsed ? '打开任务工作台' : '折叠任务工作台'} onClick={onToggle}><Icon name={collapsed ? 'arrow-left' : 'arrow-right'} /></button></div></div>
    {!collapsed && <div className={`workspace-state is-${state.tone}`}><span className="workspace-phase-dot" /><div><small>任务状态</small><strong>{state.taskStatus}</strong><small>本轮状态 · {state.turnStatus}</small></div></div>}
    <div id="workspace-panel-content" className="workspace-scroll" hidden={collapsed}>
      <nav className="workspace-tabs" role="tablist" aria-label="任务工作台视图">{tabs.map(item => <button key={item} role="tab" aria-selected={tab === item} aria-controls={`workspace-${item}`} className={tab === item ? 'active' : ''} onClick={() => chooseTab(item)}>{labels[item]}{item !== 'tasks' && count[item] > 0 && <span>{count[item]}</span>}{item === 'tasks' && <i className={`workspace-status-dot is-${state.tone}`} />}</button>)}</nav>
      {tab === 'inputs' && <div role="tabpanel" id="workspace-inputs">{renderReferences('待发送', pendingInputs, 'pending')}{renderReferences('已附加', attached, 'attached')}{renderReferences('已准备', prepared, 'attached')}{renderReferences('已冻结', frozen, 'frozen')}{!pendingInputs.length && !attached.length && !prepared.length && !frozen.length && unresolved.length > 0 && <div className="workspace-attention"><strong>需要补充</strong>{unresolved.map((item, index) => <p key={index}>{item}</p>)}</div>}{dataAvailable && <button className="primary" onClick={() => onOpenDatabase()}>打开数据库选择数据</button>}</div>}
      {tab === 'parameters' && <div role="tabpanel" id="workspace-parameters">{editing && capabilities ? <ConfirmationEditor confirmation={editing} capabilities={capabilities} project={project} busy={busy} focusField={confirmationFocus?.field} suggestedValue={confirmationFocus?.value} onClose={onEditClose} onSave={actions => onRevision(editing, actions)} /> : <section className="workspace-section"><h3>参数批次</h3>{history?.preparation && preparationEntries.some(entry => Object.keys(entry.parameters).length > 0) && <details open><summary>准备参数 · {!history.preparation.freshForCurrentTurn ? '需要本轮重新核验' : history.preparation.status === 'READY' ? '已通过合同检查' : '存在阻塞'}</summary>{preparationEntries.map((entry, entryIndex) => <div key={`${entry.targetId}/${entryIndex}`}><strong>{modelName(entry.targetId)}</strong><dl>{Object.entries(entry.parameters).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{JSON.stringify(value)} · {entry.parameterSources[name] ?? '来源未记录'}</dd></div>)}</dl></div>)}</details>}{confirmations.length ? <ol className="workspace-confirmations">{[...confirmations].reverse().map(item => <li key={item.confirmationId}><div><strong>{item.kind === 'RECOVERY' ? '恢复批次' : item.kind === 'EDITED' ? '用户修订' : '原始批次'} · 修订 {item.revision ?? 1}</strong><small>{item.status === 'PENDING' ? '待确认' : item.status}</small></div>{item.actions.map(action => <div key={action.toolCallId}><b>{modelName(action.modelId)}</b><dl>{Object.entries(action.arguments).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{JSON.stringify(value)}</dd></div>)}</dl></div>)}{item.status === 'PENDING' && <button disabled={busy} onClick={() => onEdit(item.confirmationId)}>编辑当前参数</button>}</li>)}</ol> : <p className="workspace-empty">当前没有确认参数批次。</p>}</section>}</div>}
      {tab === 'tasks' && <div role="tabpanel" id="workspace-tasks"><section className="workspace-section"><div className="workspace-section-heading"><h3>任务事实</h3>{turn && <button className="text-button" onClick={() => onJump(turn.goalRevision)}>查看本轮回答<Icon name="arrow-up-right" /></button>}</div>{history?.task ? <><h4>{history.task.spec.objective}</h4><ul className="workspace-criteria">{history.task.spec.criteria.map(item => { const progress = history.task!.progress.criteria.find(value => value.id === item.id); return <li key={item.id}><span>{progress?.satisfied ? '已满足' : '待完成'}</span>{item.text}{item.kind === 'USER_REVIEW' && !progress?.satisfied && <button disabled={busy} onClick={() => onReview(`确认完成：${item.text}`)}>确认此项满足</button>}</li>; })}</ul>{history.task.spec.steps.length > 0 && <details open><summary>任务步骤</summary><ol>{history.task.spec.steps.map(step => <li key={step.id}>{history.task!.progress.steps.find(item => item.id === step.id)?.complete ? '已完成' : '未完成'} · {step.title}{step.gaps.map((gap, index) => <p key={index}>缺口：{gap}</p>)}</li>)}</ol></details>}{history.task.spec.constraints.length > 0 && <details><summary>约束与建议</summary>{history.task.spec.constraints.map(item => <p key={item.id}>{item.text}</p>)}{history.task.spec.suggestions.map((item, index) => <p key={index}>建议：{item}</p>)}</details>}</> : unresolved.length ? <div className="workspace-attention"><strong>需要补充信息</strong>{unresolved.map((item, index) => <p key={index}>{item}</p>)}{dataAvailable && <button onClick={() => { setTab('inputs'); onOpenDatabase(); }}>打开数据库</button>}</div> : <p className="workspace-empty">本轮未创建持续任务。</p>}
      {history?.task?.progress.blockingReasons?.length ? <details open><summary>阻塞原因</summary>{history.task.progress.blockingReasons.map((item, index) => <p key={index}>{item}</p>)}</details> : null}
      {history?.preparation && <PreparationDetails history={history} dataAvailable={dataAvailable} onData={onOpenDatabase} onVersion={onInputResource} onParameter={onResolveParameter} />}
      {history?.turns.flatMap(item => item.plans.map(plan => ({ ...plan, goalRevision: item.goalRevision }))).map(plan => <details key={`${plan.goalRevision}/${plan.revision}`}><summary>Agent Plan 第 {plan.revision} 版</summary><p>{plan.goal}</p><ol>{plan.steps.map(step => <li key={step.stepId}>{step.purpose}</li>)}</ol></details>)}
      <h3>执行记录</h3>{history?.turns.some(item => item.executionTasks.length) ? <ol className="workspace-tasks">{history.turns.flatMap(item => item.executionTasks).map(task => <li key={task.workflowRunId} className={!task.isLatestAttempt ? 'is-history' : ''}><span>{modelName(task.modelId)}<small>第 {task.attempt ?? 1} 次 · {task.durationMs ? `${Math.round(task.durationMs / 1000)} 秒` : '未计时'}</small></span><small>{taskLabel(task)}</small>{task.errorCode && <small>{errorCodeMessage(task.errorCode)}</small>}</li>)}</ol> : <p className="workspace-empty">暂无执行记录。</p>}{latestFailed && <button className="primary recover-button" disabled={busy || !!pendingConfirmation} onClick={() => onRecover(latestFailed.workflowRunId)}>恢复最新失败批次</button>}
      {(history?.task || turn?.agentMetrics) && <details className="workspace-usage"><summary>技术用量</summary>{history?.task && <p>任务累计：{history.task.progress.metrics.totalTokens} 词元，{Math.round(history.task.progress.metrics.durationMs / 1000)} 秒</p>}{turn?.agentMetrics && <p>本轮：模型请求 {turn.agentMetrics.requestCount} 次，查证 {turn.agentMetrics.observationCallCount} 次，{agentTokenUsageLabel(turn.agentMetrics)}</p>}</details>}</section></div>}
      {tab === 'results' && <div role="tabpanel" id="workspace-results"><section className="workspace-section artifact-section"><div className="workspace-section-heading"><h3>{selectedOutput?.outputName ? outputName(selectedOutput.outputName) : resource?.title ? resourceTitle(resource.title) : '已发布成果'}</h3>{selectedResourceId && <button className="text-button" onClick={() => onResource('')}>返回成果列表</button>}</div>{resourceError && <ErrorNotice error={resourceError} />}{resourceLoading ? <p className="muted">正在读取成果信息…</p> : resource ? <Suspense fallback={<p className="muted">正在加载成果查看器…</p>}><ArtifactViewer resource={resource} project={project} preferredViewer={selectedOutput?.outputName ? productUi().outputs[selectedOutput.outputName]?.viewer : 'auto'} onResource={onResource} /></Suspense> : selectedResourceId ? null : outputs.length ? <ul className="workspace-outputs">{outputs.map(({ reference, revision, workflowRunId }) => <li key={reference.versionId}><button onClick={() => onResource(reference.versionId)}><Icon name={reference.mediaType === 'image/png' ? 'map' : 'file'} /><span><strong>{reference.outputName ? outputName(reference.outputName) : '结果文件'}</strong><small>{reference.format} · 第 {revision} 轮{workflowRunId ? ` · ${workflowRunId.slice(0, 8)}` : ''}</small></span><Icon name="arrow-up-right" /></button></li>)}</ul> : <p className="workspace-empty">暂无已发布成果。</p>}</section></div>}
    </div>
  </aside>;
}
