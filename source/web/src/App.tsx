import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { FormEvent, MouseEvent } from 'react';
import { useDrawerController } from './use-drawer-controller';
import { useWorkspaceController } from './use-workspace-controller';
import { useMessageActions } from './use-message-actions';
import { currentSessionId, useProjectSessions } from './use-project-sessions';
import { useSessionStream } from './use-session-stream';
import { hasVerificationMarker, isVerificationHistory, turnPhase } from './chat-state';
import { TurnTimeline } from './TurnTimeline';
import { MessageComposer } from './MessageComposer';
import { RoutingDebug } from './RoutingDetails';
import { ConfirmationCard } from './ChatCards';
import { resourceItemFromDetail } from './resource-item';
import { UploadDialog } from './UploadDialog';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { productIconUrl, productUi } from './product-ui';
import { sessionTask, TaskHeader } from './TaskCard';
import { MessageQueue } from './MessageQueue';
import { ControlProposalCard } from './ControlProposalCard';
import type { DebugEntry, KnowledgeReference, ResourceItem, ResourceReference } from './types';
import { ApiError, callApi, loginResearch, logoutResearch, readDataVersion } from './api';
import { localizedSessionTitle, SessionNavigation } from './SessionNavigation';
import { identityStorageScope } from './browser-scope';
import { shortVersion } from './format';
import './conversation-polish.css';

const WorkspacePanel = lazy(() => import('./WorkspacePanel').then(module => ({ default: module.WorkspacePanel })));
const DatabaseWorkbench = lazy(() => import('./DatabaseWorkbench').then(module => ({ default: module.DatabaseWorkbench })));
const KnowledgeDrawer = lazy(() => import('./Drawers').then(module => ({ default: module.KnowledgeDrawer })));
const CapabilityDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.CapabilityDrawer })));
const ReadinessDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.ReadinessDrawer })));

export default function App() {
  const ui = productUi();
  const mark = productIconUrl();
  const debugEnabled = new URLSearchParams(window.location.search).get('debug') === '1';
  const [debug, setDebug] = useState<DebugEntry[]>([]);
  const log = useCallback((entry: DebugEntry) => setDebug(previous => [...previous.slice(-79), entry]), []);
  const {
    activeId, capabilities, capabilityError, sessionError, clearSessionError,
    sessions, sessionCursor, sessionsLoading, updatingSessionId, loadSessions, connectProject,
    sessionQuery, setSessionQuery, showArchived, setShowArchived, updateSession,
  } = useProjectSessions(log);
  const project = capabilities?.projectId ?? '';
  const storageScope = identityStorageScope(ui.productId, project, capabilities?.currentUser.subject ?? '');
  const canResearch = capabilities?.currentUser.roles.some(
    role => role === 'admin' || role === 'researcher',
  ) ?? false;
  const configuredServices = new Set(capabilities?.services ?? []);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [mobileSessionsScope, setMobileSessionsScope] = useState<string | null>(null);
  const mobileSessionsOpen = mobileSessionsScope === storageScope;
  const mobileSessionsTrigger = useRef<HTMLButtonElement>(null);
  const mobileSessionsClose = useRef<HTMLButtonElement>(null);
  const desktopSessionsToggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!mobileSessionsOpen) return;
    mobileSessionsClose.current?.focus();
    if (typeof window.matchMedia !== 'function') return;
    const narrow = window.matchMedia('(max-width: 800px)');
    function closeOnDesktop() {
      if (narrow.matches) return;
      setMobileSessionsScope(null);
      const focused = document.activeElement;
      if (focused === document.body || focused === mobileSessionsClose.current) {
        desktopSessionsToggle.current?.focus();
      }
    }
    narrow.addEventListener('change', closeOnDesktop);
    closeOnDesktop();
    return () => narrow.removeEventListener('change', closeOnDesktop);
  }, [mobileSessionsOpen]);

  function closeMobileSessions() {
    setMobileSessionsScope(null);
    requestAnimationFrame(() => mobileSessionsTrigger.current?.focus());
  }

  function openSessionLink(event: MouseEvent<HTMLAnchorElement>) {
    if (mobileSessionsOpen && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) closeMobileSessions();
  }
  const {
    drawer, closeDrawer, openDatabase, openKnowledge,
    openCapabilities, openReadiness,
  } = useDrawerController();
  const workspace = useWorkspaceController(activeId, storageScope);
  const viewingWorkbench = !!workspace.routeTab;
  const workspaceOpener = useRef<HTMLElement | null>(null);
  const previouslyInWorkbench = useRef(false);
  const composerAfterRoute = useRef(false);
  useEffect(() => {
    if (viewingWorkbench) {
      if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) workspaceOpener.current = document.activeElement;
      const frame = requestAnimationFrame(() => document.getElementById('workspace-route-heading')?.focus());
      previouslyInWorkbench.current = true;
      return () => cancelAnimationFrame(frame);
    }
    if (previouslyInWorkbench.current) {
      previouslyInWorkbench.current = false;
      const frame = requestAnimationFrame(() => {
        if (document.activeElement?.matches('#current-confirmation, .turn')) return;
        if (composerAfterRoute.current) {
          composerAfterRoute.current = false;
          composer.current?.focus();
          return;
        }
        if (workspaceOpener.current?.isConnected && workspaceOpener.current.getClientRects().length) workspaceOpener.current.focus();
        else composer.current?.focus();
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [viewingWorkbench]);
  const confirmationFocusAfterSave = useRef(false);
  const turnAfterRoute = useRef<number | null>(null);
  const stream = useSessionStream(storageScope ? activeId : '', project, storageScope, log);
  const {
    preview, history, workflowDrafts, workflowDraftReads, retryWorkflowDraft, results, resultsRead, snapshotCurrent, loading, loadingOlder,
    connection, connected, trace, refresh, olderMessages, loadTrace,
  } = stream;
  const {
    queue, changeQueue, enqueue, draft, updateDraft, attachments, setAttachments,
    submission, receiptPending, canRetryReceipt, retryReceipt, pending, pendingLabel, continuationVersionId, runtimeBusy, pendingControl, busy, lastTurn,
    awaitingConfirmation, stopping, error, clearError, canSend, stopRun, newChat, send,
    decision, controlDecision, reviseConfirmation, recoverWorkflow, newChatWithAttachments,
  } = useMessageActions({
    activeId,
    storageScope,
    project,
    history,
    connected,
    loading,
    externalError: stream.error ?? sessionError,
    clearExternalError: () => { stream.clearError(); clearSessionError(); },
    loadSessions,
    refresh,
    refs: stream.refs,
    closeConfirmationEditor: () => {
      confirmationFocusAfterSave.current = true;
      workspace.closeConfirmation();
      if (workspace.routeTab) workspace.returnToConversation();
    },
  });
  const latestConfirmation = lastTurn?.confirmations.at(-1);
  const currentConfirmation = latestConfirmation?.status === 'PENDING'
    && latestConfirmation.agentRunId === lastTurn?.agentRunId && latestConfirmation.goalRevision === lastTurn?.goalRevision
    ? latestConfirmation : null;
  useEffect(() => {
    if (confirmationFocusAfterSave.current && currentConfirmation && !workspace.routeTab) {
      confirmationFocusAfterSave.current = false;
      const frame = requestAnimationFrame(() => document.getElementById('current-confirmation')?.focus());
      return () => cancelAnimationFrame(frame);
    }
  }, [currentConfirmation?.confirmationId, workspace.editingConfirmationId, workspace.routeTab]);
  function focusCurrentConfirmation() {
    if (workspace.routeTab) {
      confirmationFocusAfterSave.current = true;
      workspace.returnToConversation();
      return;
    }
    document.getElementById('current-confirmation')?.focus();
  }
  const [file, setFile] = useState<File | null>(null);
  const [preparingUpload, setPreparingUpload] = useState(false);
  const uploadCandidate = useRef<File | null>(null);
  const uploadRequest = useRef(0);
  const [uploadLimitBytes, setUploadLimitBytes] = useState<number | null>(null);
  const [uploadConfigError, setUploadConfigError] = useState<ApiError | null>(null);
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const chatScroll = useRef<HTMLDivElement>(null);
  const olderAnchor = useRef<{ element: HTMLElement; top: number } | null>(null);

  function readOlderMessages() {
    const scroller = chatScroll.current;
    const bounds = scroller?.getBoundingClientRect();
    const element = scroller && bounds && [...scroller.querySelectorAll<HTMLElement>('.turn')]
      .find(turn => turn.getBoundingClientRect().bottom > bounds.top);
    olderAnchor.current = element ? { element, top: element.getBoundingClientRect().top } : null;
    void olderMessages();
  }

  useLayoutEffect(() => {
    const anchor = olderAnchor.current;
    if (loadingOlder || !anchor) return;
    olderAnchor.current = null;
    if (anchor.element.isConnected && chatScroll.current) {
      chatScroll.current.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top;
    }
  }, [loadingOlder, history?.items.length]);
  const atBottom = useRef(true);
  const [newMessages, setNewMessages] = useState(false);
  const latestVisible = useRef<{ key: string; signature: string } | null>(null);
  const latestMessage = history?.items.at(-1);
  const liveTurn = history?.turns.find(turn => turn.goalRevision === history.goalRevision);
  const liveSignature = JSON.stringify([
    latestMessage?.messageId, latestMessage?.content, liveTurn?.status, liveTurn?.reply,
    liveTurn?.confirmations.at(-1)?.confirmationId, liveTurn?.executionTasks.map(run => [run.workflowRunId, run.status, run.outputCount]),
    liveTurn?.workflowDraftId ? workflowDrafts[liveTurn.workflowDraftId]?.status : null, preview?.text,
    results.filter(result => result.agentRunId === liveTurn?.agentRunId && result.goalRevision === liveTurn?.goalRevision)
      .map(result => [result.workflowRunId, result.status, result.remoteOutputs]),
  ]);
  const fileInput = useRef<HTMLInputElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const [templateChange, setTemplateChange] = useState<{ scope: string; sessionId: string; before: string; after: string; title: string } | null>(null);
  const [sessionFeedback, setSessionFeedback] = useState<{ scope: string; text: string } | null>(null);
  const sessionNotice = sessionFeedback?.scope === storageScope ? sessionFeedback.text : '';

  useEffect(() => { setTemplateChange(null); }, [activeId, storageScope]);

  useEffect(() => {
    uploadRequest.current += 1;
    uploadCandidate.current = null;
    setPreparingUpload(false); setUploadConfigError(null); setFile(null);
    return () => { uploadRequest.current += 1; };
  }, [activeId, storageScope]);

  useEffect(() => {
    atBottom.current = true;
    latestVisible.current = null;
    olderAnchor.current = null;
    setNewMessages(false);
  }, [activeId, storageScope]);
  useEffect(() => {
    if (history?.sessionId !== activeId || viewingWorkbench) return;
    const key = `${storageScope}/${activeId}`;
    if (latestVisible.current?.key === key && latestVisible.current.signature === liveSignature) return;
    const initial = latestVisible.current === null;
    latestVisible.current = { key, signature: liveSignature };
    if (currentConfirmation && initial) return;
    if (atBottom.current) bottom.current?.scrollIntoView({ block: 'end' });
    else setNewMessages(true);
  }, [activeId, storageScope, history?.sessionId, liveSignature, currentConfirmation, viewingWorkbench]);

  const currentVersionRead = useRef<AbortController | null>(null);
  const [currentVersionError, setCurrentVersionError] = useState<ApiError | null>(null);
  const preparation = history?.preparation;
  useLayoutEffect(() => {
    setCurrentVersionError(null);
    return () => currentVersionRead.current?.abort();
  }, [activeId, storageScope, history?.goalRevision, preparation?.preparationId,
    preparation?.revision, preparation?.disposition, preparation?.freshForCurrentTurn, snapshotCurrent]);

  function attach(resource: ResourceItem) {
    if (currentSessionId() !== activeId) return;
    currentVersionRead.current?.abort();
    setCurrentVersionError(null);
    setAttachments(previous => [...previous.filter(item => item.versionId !== resource.versionId), resource]);
    setFile(null);
    closeDrawer();
    if (workspace.routeTab) {
      composerAfterRoute.current = true;
      workspace.returnToConversation();
    } else composer.current?.focus();
  }
  async function prepareUpload(selected: File) {
    const request = ++uploadRequest.current;
    const isCurrent = () => request === uploadRequest.current && currentSessionId() === activeId;
    uploadCandidate.current = selected;
    setPreparingUpload(true); setUploadConfigError(null);
    try {
      const data = await callApi('data_capabilities', '/data/capabilities');
      const limit = data.limits.uploadBytes;
      if (!Number.isSafeInteger(limit) || limit <= 0 || data.project !== project) {
        throw new ApiError(502, 'API_RESPONSE_INVALID');
      }
      if (!isCurrent()) return;
      setUploadLimitBytes(limit);
      setUploadConfigError(null);
      setFile(selected);
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (isCurrent()) setUploadConfigError(caught);
    } finally {
      if (isCurrent()) setPreparingUpload(false);
    }
  }
  function resultItem(resource: import('./types').ResourceReference): ResourceItem {
    return { ...resource, familyId: resource.datasetId, name: resource.outputName ? (ui.outputs[resource.outputName]?.label ?? resource.outputName) : '分析成果' };
  }
  const openResource = workspace.openResource;
  const openInputResource = useCallback((id: string, reference?: ResourceReference) => openDatabase({ id, reference }), [openDatabase]);
  async function attachCurrentVersion(id: string, datasetId: string) {
    currentVersionRead.current?.abort();
    setCurrentVersionError(null);
    const controller = new AbortController();
    currentVersionRead.current = controller;
    const generation = stream.refs.viewGeneration.current;
    const current = () => !controller.signal.aborted && currentSessionId() === activeId && stream.refs.viewGeneration.current === generation;
    try {
      const detail = await readDataVersion(id, controller.signal);
      if (!current()) return;
      if (detail.datasetId !== datasetId || detail.currentVersion !== id) {
        throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
      }
      const object = detail.objects.find(item => item.role === 'raw') ?? detail.objects[0];
      if (!object) throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
      attach(resourceItemFromDetail(detail, object));
    } catch (caught) {
      if (!current()) return;
      if (!(caught instanceof ApiError)) throw caught;
      setCurrentVersionError(caught);
    }
  }
  const showKnowledge = useCallback((reference: KnowledgeReference) => openKnowledge(reference), [openKnowledge]);
  useEffect(() => {
    if (workspace.routeTab || turnAfterRoute.current === null) return;
    const revision = turnAfterRoute.current;
    turnAfterRoute.current = null;
    const turn = document.getElementById(`turn-${revision}`);
    turn?.focus({ preventScroll: true });
    turn?.scrollIntoView({ block: 'start' });
  }, [workspace.routeTab]);

  function jumpToTurn(revision: number) {
    if (workspace.routeTab) {
      turnAfterRoute.current = revision;
      workspace.returnToConversation();
      return;
    }
    const turn = document.getElementById(`turn-${revision}`);
    turn?.focus({ preventScroll: true });
    turn?.scrollIntoView({ block: 'start' });
  }
  function exampleButton(index: number) {
    const example = ui.examples[index];
    return <button key={example.id} aria-labelledby={`example-title-${index} example-description-${index}`} onClick={() => fillTemplate(example.prompt, example.title)}><Icon name={example.icon} /><span className="example-copy"><strong id={`example-title-${index}`}>{example.title}</strong><small id={`example-description-${index}`}>{example.prompt}</small></span><Icon name="arrow-up-right" /></button>;
  }
  function fillTemplate(prompt: string, title: string) {
    setTemplateChange({ scope: storageScope, sessionId: activeId, before: draft, after: prompt, title });
    updateDraft(prompt); composer.current?.focus();
  }
  const undoTemplate = templateChange?.scope === storageScope && templateChange.sessionId === activeId && templateChange.after === draft ? templateChange : null;
  async function renameSession(sessionId: string, title: string) {
    if (await updateSession(sessionId, { title })) {
      setSessionFeedback({ scope: storageScope, text: '对话名称已保存。' });
    }
  }

  async function archiveSession(sessionId: string, archived: boolean) {
    setSessionFeedback(null);
    try {
      if (!await updateSession(sessionId, { archived })) return;
      setSessionFeedback({ scope: storageScope, text: archived ? '对话已归档，可在“显示归档”中找回。' : '已恢复到最近对话。' });
      if (archived && currentSessionId() === sessionId) window.location.hash = '#/';
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
    }
  }

  const activeSummary = sessions.find(item => item.sessionId === activeId);
  const activeTask = sessionTask(history, activeId);
  const activeHistory = history?.sessionId === activeId ? history : null;
  const confirmationWriteDisabled = !canResearch || !snapshotCurrent || connection === 'disconnected';
  const taskReviewDisabled = confirmationWriteDisabled || !!submission;
  function requestTaskChanges() {
    if (!activeTask || !canResearch || currentSessionId() !== activeId) return;
    if (!draft.trim()) updateDraft('请修改本任务的以下内容：');
    if (workspace.routeTab) {
      composerAfterRoute.current = true;
      workspace.returnToConversation();
    } else composer.current?.focus();
  }
  const verificationSession = hasVerificationMarker(activeSummary?.title) || isVerificationHistory(history);
  const recoveredTitle = localizedSessionTitle(activeHistory?.items.find(item => item.role === 'user')?.content.slice(0, 120) ?? '当前对话');
  const recoveredSessionTitle = `当前会话 · ${verificationSession && !recoveredTitle.startsWith('【验证会话】') ? `【验证会话】${recoveredTitle}` : recoveredTitle}`;

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoggingIn(true);
    setLoginError('');
    try {
      await loginResearch(loginUsername, loginPassword);
      setLoginPassword('');
      await connectProject();
    } catch (caught) {
      setLoginError(caught instanceof ApiError ? caught.code : 'LOGIN_FAILED');
    } finally {
      setLoggingIn(false);
    }
  }

  if (capabilityError?.code === 'PRODUCT_UI_IDENTITY_MISMATCH') return <main className="configuration-error" role="alert"><h1>产品配置不可用</h1><p>交互配置与当前服务身份不一致。业务操作已停用。</p><button onClick={() => void connectProject()}>重新检查</button></main>;
  if (capabilityError?.code === 'AUTHENTICATION_REQUIRED' && !capabilities) return <main className="research-login"><form onSubmit={event => void submitLogin(event)}><img src={mark} alt="" /><h1>登录工作区</h1><label>用户名<input autoComplete="username" required value={loginUsername} onChange={event => setLoginUsername(event.target.value)} /></label><label>密码<input type="password" autoComplete="current-password" required value={loginPassword} onChange={event => setLoginPassword(event.target.value)} /></label>{loginError && <p role="alert">登录失败：{loginError}</p>}<button type="submit" disabled={loggingIn}>{loggingIn ? '正在登录…' : '登录'}</button></form></main>;
  if (capabilityError && !capabilities) return <main className="service-unavailable"><h1>服务暂时不可用</h1><p role="alert">{capabilityError.code}：当前无法读取平台能力，业务操作保持禁用。</p>{capabilityError.requestId && <p>请求 ID：<code>{capabilityError.requestId}</code></p>}<button onClick={() => void connectProject()}>重新检查</button></main>;

  const receiptFeedback = receiptPending && <div className="submission-recovery confirmation-receipt" role="status">
        <span>操作结果待核对</span>
        <button disabled={pending} onClick={() => void refresh(activeId)}>重新读取状态</button>
        {canRetryReceipt && <button disabled={pending || !canResearch || !snapshotCurrent} onClick={retryReceipt}>重试同一操作</button>}
      </div>;
  const operationFeedback = error && <div className="error-banner"><ErrorNotice error={error} /><button className="icon-button" aria-label="关闭提示" onClick={clearError}><Icon name="close" /></button></div>;
  const submissionFeedback = submission && !pending && <div className="submission-recovery" role="status">
    <span>这条消息的发送状态待确认，草稿已保留。</span>
    <button disabled={loading || !project || !canResearch} onClick={() => void send(submission)}>重试发送</button>
  </div>;
  return <div className={`app-shell${activeId ? " has-session" : ""}${workspace.routeTab ? " workspace-route" : ""}${mobileSessionsOpen ? " mobile-sessions-open" : ""}`} data-testid="chat-app">
    {currentConfirmation && !workspace.routeTab && <button type="button" className="current-confirmation-skip" onClick={focusCurrentConfirmation}>查看当前确认</button>}
    <aside id="conversation-sidebar" className={`sidebar session-navigation ${leftCollapsed ? 'is-collapsed' : ''}`}
      aria-label="会话侧栏" onKeyDown={event => { if (mobileSessionsOpen && event.key === 'Escape' && !event.defaultPrevented) closeMobileSessions(); }}>
      <div className="sidebar-heading">
        <a className="brand" href="#/" onClick={openSessionLink} aria-label={`${ui.brand.productName}首页`}><img src={mark} alt="" /><span>{ui.brand.productName}</span></a>
        <button className="icon-button panel-toggle desktop-session-toggle" ref={desktopSessionsToggle} aria-label={leftCollapsed ? '展开会话侧栏' : '折叠会话侧栏'}
          aria-expanded={!leftCollapsed} onClick={() => setLeftCollapsed(value => !value)}><Icon name={leftCollapsed ? 'arrow-right' : 'arrow-left'} /></button>
        <button className="icon-button mobile-session-close" ref={mobileSessionsClose} aria-label="关闭会话导航"
          onClick={closeMobileSessions}><Icon name="close" /></button>
      </div>
      <SessionNavigation key={storageScope} activeId={activeId} recoveredTitle={activeHistory ? recoveredSessionTitle : null}
        sessions={sessions} query={sessionQuery} showArchived={showArchived} cursor={sessionCursor}
        loading={sessionsLoading} error={sessionError} notice={sessionNotice} updatingId={updatingSessionId}
        canCreate={!!project && !pending && !continuationVersionId && canResearch} canWrite={canResearch}
        onCreate={() => { if (mobileSessionsOpen) closeMobileSessions(); void newChat(); }}
        onQuery={setSessionQuery} onArchiveFilter={setShowArchived} onRead={cursor => void loadSessions(cursor)}
        onOpen={openSessionLink} onRename={renameSession} onArchive={(id, archived) => void archiveSession(id, archived)} />
      <div className="sidebar-tools"><button aria-label="能力目录" title="能力目录" onClick={openCapabilities} disabled={!capabilities}><Icon name="layers" /><span className="sidebar-tool-label">能力</span><Icon name="arrow-up-right" /></button>{configuredServices.has('data') && <button aria-label="数据库" title="数据库" onClick={() => openDatabase()} disabled={!project}><Icon name="folder" /><span className="sidebar-tool-label">数据库</span><Icon name="arrow-up-right" /></button>}{configuredServices.has('knowledge-next') && <button aria-label="知识库" title="知识库" onClick={() => openKnowledge()} disabled={!project}><Icon name="book" /><span className="sidebar-tool-label">知识库</span><Icon name="arrow-up-right" /></button>}</div>
    </aside>
    <main className="main-panel" hidden={!!workspace.routeTab}>
      <header className="topbar"><div><button type="button" className="icon-button mobile-session-trigger" ref={mobileSessionsTrigger}
        aria-label="打开会话导航" aria-expanded={mobileSessionsOpen} aria-controls="conversation-sidebar" disabled={!storageScope}
        onClick={() => setMobileSessionsScope(storageScope)}><Icon name="menu" /></button><span className="product-title" title={project ? `项目：${project}` : '正在连接项目…'}>{ui.brand.workspaceName}</span><span className="environment-badge">项目环境</span><span className="current-user">{capabilities?.currentUser.displayName ?? '正在验证身份'}</span><button className="text-button" onClick={() => void logoutResearch().finally(() => window.location.reload())}>退出</button></div><button className="connection-state" data-testid="connection-status" onClick={openReadiness}><span className={`status-dot ${project && (!activeId || connected) ? 'online' : ''}`} />{capabilityError ? '服务未连接' : !project ? '正在连接项目' : activeId ? ({ connecting: '正在连接实时更新', connected: '实时更新已连接', disconnected: '正在恢复连接 · 显示最近状态' })[connection] : '项目已连接'}</button></header>
      {activeId && <nav className="session-detail-nav" aria-label="会话详情">
        <a href={`#/sessions/${activeId}/workbench/inputs`}>数据</a>
        <a href={`#/sessions/${activeId}/workbench/parameters`}>参数</a>
        <a href={`#/sessions/${activeId}/workbench/tasks`}>运行</a>
        <a href={`#/sessions/${activeId}/workbench/results`}>成果</a>
      </nav>}
      <div className="result-notice" role="note">结果须结合输入、参数、lineage 和验收报告解释。</div>
      {!viewingWorkbench && receiptFeedback}
      {activeTask && <TaskHeader task={activeTask} name={activeSummary?.title} onOpen={() => workspace.focus('tasks')}
        onSupplement={!taskReviewDisabled && !busy ? () => composer.current?.focus() : undefined}
        onConfirmation={snapshotCurrent && activeHistory && currentConfirmation ? focusCurrentConfirmation : undefined} />}
      {currentConfirmation && <div className="current-confirmation">
        <ConfirmationCard key={currentConfirmation.confirmationId} confirmation={currentConfirmation} history={history}
          busy={busy || !snapshotCurrent || connection === 'disconnected' || !canResearch} verificationSession={verificationSession} onDecision={(id, kind, reason) => void decision(id, kind, reason)}
          onEdit={id => workspace.editConfirmation(id)} onResource={openInputResource} project={project} current capabilities={capabilities} />
      </div>}
      <div className="chat-scroll" ref={chatScroll} data-testid="message-list" onScroll={event => { const node = event.currentTarget; atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; if (atBottom.current) setNewMessages(false); }}>
        {!history?.items.length && !history?.controlMessages?.length && !activeTask && !loading ? <section className="welcome" aria-labelledby="welcome-title"><h1 id="welcome-title">{ui.brand.welcome}</h1><details className="usage-guide"><summary>使用说明</summary><p>PRIVATE 资料仅 owner 和 admin 可读；PROJECT 资料可供项目成员共享。归档前请复核输入 checksum、参数、随机种子、模型实现和 lineage。</p></details>{capabilityError && <div className="connection-notice" role="alert"><strong>业务服务尚未连接</strong><p>{capabilityError.code === 'PRODUCT_UI_IDENTITY_MISMATCH' ? '产品配置与当前服务不一致，业务操作已停用。' : capabilityError.code === 'API_RESPONSE_INVALID' ? '当前仅提供前端预览，暂不能发送。' : '无法连接业务接口，暂不能发送。'}</p><button onClick={() => void connectProject()}>重试连接</button></div>}<section className="example-group" aria-label="常用任务"><h2>常用任务</h2><div className="example-grid">{ui.examples.map((item, index) => item.featured ? exampleButton(index) : null)}</div><details className="more-examples"><summary>更多场景<Icon name="arrow-down" /></summary><div className="example-grid">{ui.examples.map((item, index) => !item.featured ? exampleButton(index) : null)}</div></details></section></section> : <div className="conversation">
          {history?.controlMessages?.map(message => <ControlProposalCard
            key={message.requestId} message={message}
            proposal={history.controlProposals?.find(item => item.sourceRequestId === message.requestId)}
            disabled={pending} onDecision={(id, action) => void controlDecision(id, action)} onResource={openResource}
          />)}
          {history?.nextCursor !== null && history?.nextCursor !== undefined && <button className="older-messages" disabled={loadingOlder} onClick={readOlderMessages}><Icon name="arrow-up" />{loadingOlder ? '正在读取更早消息…' : '更早消息'}</button>}
          {history?.sessionId === activeId && <TurnTimeline key={`${storageScope}/${activeId}`} history={history}
            scope={`${storageScope}/${activeId}`} project={project} assistant={{ name: ui.brand.assistantName, image: mark }}
            connected={connected} currentConfirmationId={currentConfirmation?.confirmationId} busy={busy || !!submission || !snapshotCurrent || connection === 'disconnected'}
            canWrite={canResearch} verificationSession={verificationSession} preview={preview}
            drafts={workflowDrafts} draftReads={workflowDraftReads} retryDraft={retryWorkflowDraft}
            results={resultsRead} retryResults={() => void refresh(activeId)}
            onJump={jumpToTurn} onChoose={choice => void send(undefined, choice)} onResource={openResource}
            onInputResource={openInputResource} onKnowledge={showKnowledge} onAttach={resource => attach(resultItem(resource))}
            continueDisabled={pending || !canResearch} continuationVersionId={continuationVersionId}
            onContinue={resource => void newChatWithAttachments([resultItem(resource)], '请基于已附加成果继续分析。')}
            onCompare={resource => openDatabase({ id: resource.versionId, reference: resource, compare: true })}
          />}
          {loading && <p className="muted" role="status">正在恢复对话…</p>}<div ref={bottom} />
        </div>}
      </div>
      <div className="composer-region">
        {newMessages && <button className="new-messages" onClick={() => { atBottom.current = true; setNewMessages(false); bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }}>查看最新消息<Icon name="arrow-down" /></button>}
        {!viewingWorkbench && operationFeedback}
        {uploadConfigError && <div className="error-banner"><ErrorNotice error={uploadConfigError} title="上传配置不可用" /><button disabled={preparingUpload} onClick={() => { if (uploadCandidate.current) void prepareUpload(uploadCandidate.current); }}>重试读取上传配置</button><button className="icon-button" aria-label="关闭上传提示" onClick={() => setUploadConfigError(null)}><Icon name="close" /></button></div>}
        {!viewingWorkbench && submissionFeedback}
        <MessageQueue items={queue} onChange={changeQueue} />
        {activeId && (runtimeBusy || awaitingConfirmation) && !loading && <div className="run-controls"><button disabled={stopping || pending} onClick={() => void stopRun()}>{stopping ? '正在请求停止…' : awaitingConfirmation && !runtimeBusy ? '撤回本轮待确认请求' : '停止当前处理'}</button>{(runtimeBusy || draft.trim()) && <button disabled={stopping || pending || !draft.trim()} onClick={() => lastTurn?.status === 'WAITING_EXECUTION' ? enqueue(lastTurn.agentRunId) : void stopRun(true)}>{lastTurn?.status === 'WAITING_EXECUTION' ? '将纠正排为后续消息' : '停止后按输入内容纠正'}</button>}</div>}
        <div className="composer" data-testid="composer">
          {undoTemplate && <div className="template-feedback" role="status"><span>已填入“{undoTemplate.title}”，可修改后发送。</span><button className="text-button" onClick={() => { updateDraft(undoTemplate.before); setTemplateChange(null); composer.current?.focus(); }}>撤销填入</button></div>}
          {attachments.length > 0 && <div className="pending-attachments" data-testid="pending-attachments"><p className="attachment-heading">待发送 · {attachments.length} 份资料</p><div className="attachment-list">{attachments.map(item => <div className="attachment-chip" key={item.versionId}><button className="text-button" title={`${item.name} · 查看资料内容与版本`} onClick={() => openInputResource(item.versionId, item)}><Icon name="file" />{item.name} · {shortVersion(item.versionId)}</button><button aria-label={`移除附件 ${item.name}`} onClick={() => setAttachments(previous => previous.filter(value => value.versionId !== item.versionId))}><Icon name="close" /></button></div>)}</div><details className="attachment-identities technical-details"><summary>附件版本详情</summary>{attachments.map(item => <div key={item.versionId}><strong>{item.name}</strong><dl className="argument-list"><div><dt>数据版本</dt><dd>{item.versionId}</dd></div><div><dt>数据集</dt><dd>{item.datasetId}</dd></div><div><dt>语义版本</dt><dd>{item.semanticId}</dd></div><div><dt>数据对象</dt><dd>{item.objectId}</dd></div><div><dt>SHA-256</dt><dd>{item.checksum}</dd></div></dl></div>)}</details></div>}
        <MessageComposer key={activeId} inputRef={composer} value={draft} selection={false} busy={busy}
            canSend={canSend && canResearch}
            unavailableReason={!capabilities ? '正在验证身份与连接项目' : !canResearch ? '当前身份只有查看权限' : !project ? '正在连接项目' : submission ? '请先确认上一条消息的发送结果' : busy ? `${loading ? '正在恢复对话' : pendingControl ? '等待控制确认' : lastTurn && runtimeBusy ? turnPhase(lastTurn) : pendingLabel} · 草稿会保留` : '请输入问题或附加资料'}
            onChange={value => { setTemplateChange(null); updateDraft(value); }} onSend={() => void send()} tools={<>
              {activeId && busy && <button disabled={!canResearch || (!draft.trim() && !attachments.length)} onClick={() => enqueue()}>稍后发送</button>}
              {configuredServices.has('data') && <><button className="icon-button attach-button" aria-label="添加附件" title={preparingUpload ? '正在读取上传要求' : '上传资料'} disabled={!canResearch || !project || pending || !!file || preparingUpload} onClick={() => fileInput.current?.click()}><Icon name="plus" /></button>{preparingUpload && <span className="upload-preparing">正在读取上传要求…</span>}
              <input ref={fileInput} type="file" hidden onChange={event => { const selected = event.target.files?.[0]; if (selected) void prepareUpload(selected); event.target.value = ''; }} /></>}
            </>} />
        </div>
        <div className="composer-caption">{capabilityError && history?.items.length ? <span>业务服务尚未连接，暂不能发送。</span> : busy && <span>{`${loading ? '正在恢复对话' : pendingControl ? '等待控制确认' : lastTurn && runtimeBusy ? turnPhase(lastTurn) : pendingLabel} · 草稿会保留`}</span>}{debugEnabled && <details className="debug-panel" onToggle={event => { if (event.currentTarget.open) void loadTrace(); }}><summary>调试信息</summary><div className="debug-content"><h3>请求、事件与交互延迟</h3><p>不展示提示词、工具正文或模型服务商配置。</p><pre>{debug.map(entry => `${entry.time} ${entry.kind} ${entry.name} ${entry.status ?? entry.eventId ?? ''}${entry.code ? ` code=${entry.code}` : ''}${entry.elapsedMs !== undefined ? ` ${entry.elapsedMs}ms` : ''}${entry.requestId ? ` request=${entry.requestId}` : ''}`).join('\n')}</pre><RoutingDebug turns={history?.turns ?? []} />{trace !== null && <details><summary>当前运行追踪元数据</summary><pre>{JSON.stringify(trace, null, 2)}</pre></details>}</div></details>}</div>
      </div>
    </main>
    <Suspense fallback={<p className="muted workspace-loading">正在加载工作台…</p>}><WorkspacePanel history={activeHistory} results={activeHistory ? results : []} resultsRead={activeHistory ? resultsRead : { status: 'not-requested' }} activeId={activeId} storageScope={storageScope} project={project} capabilities={capabilities} dataAvailable={configuredServices.has('data')} collapsed={workspace.collapsed || Boolean(drawer || file)} pendingInputs={attachments} selectedResourceId={workspace.resourceId} selectedResourceReference={workspace.resourceReference} editingConfirmationId={workspace.editingConfirmationId} confirmationFocus={workspace.confirmationFocus} focusRequest={workspace.focusRequest} busy={busy} onToggle={workspace.toggleCollapsed} onJump={jumpToTurn} onCurrentConfirmation={focusCurrentConfirmation} routedTab={workspace.routeTab} onRouteTab={workspace.focus}
      feedback={<>{receiptFeedback}{operationFeedback}{submissionFeedback}</>}
      dataFeedback={currentVersionError && <ErrorNotice error={currentVersionError} title="无法附加建议的数据版本" />}
      onRetryResults={() => void refresh(activeId)}
      onAttachResult={resource => attach(resultItem(resource))}
      onContinueResult={resource => void newChatWithAttachments([resultItem(resource)], '请基于已附加成果继续分析。')}
      continueResultDisabled={pending || !canResearch || !!continuationVersionId}
      onRequestTaskChanges={canResearch ? requestTaskChanges : undefined}
      onReview={text => {
        if (activeTask && !taskReviewDisabled && currentSessionId() === activeId) void send(undefined, text);
      }}
      taskReviewDisabled={taskReviewDisabled}
      confirmationWriteDisabled={confirmationWriteDisabled}
      onCompareResult={resource => openDatabase({ id: resource.versionId, reference: resource, compare: true })} onResource={openResource} onInputResource={openInputResource}
      onUseCurrentVersion={canResearch ? (id, datasetId) => void attachCurrentVersion(id, datasetId) : undefined}
      onOpenDatabase={formats => openDatabase({ formats })} onRemovePending={id => setAttachments(previous => previous.filter(item => item.versionId !== id))} onEdit={id => workspace.editConfirmation(id)} onResolveParameter={name => { if (currentConfirmation) workspace.editConfirmation(currentConfirmation.confirmationId, { field: name }); else { updateDraft(`${draft}${draft.trim() ? '\n' : ''}补充参数 ${name}：`); if (workspace.routeTab) { composerAfterRoute.current = true; workspace.returnToConversation(); } else requestAnimationFrame(() => composer.current?.focus()); } }} onEditClose={workspace.closeConfirmation} onRevision={(confirmation, actions) => void reviseConfirmation(confirmation, actions)} onRecover={id => void recoverWorkflow(id)} /></Suspense>
    {drawer && <button className="drawer-shade" aria-label="关闭浮层" onClick={closeDrawer} />}
    {drawer?.kind === 'database' && <Suspense fallback={<p className="muted drawer-loading">正在加载数据库…</p>}><DatabaseWorkbench key={`database:${drawer.id ?? ''}:${drawer.reference?.semanticId ?? ''}:${drawer.reference?.objectId ?? ''}:${drawer.compare ? 'compare' : ''}:${drawer.formats?.join(',') ?? ''}`} project={project} initialId={drawer.id} reference={drawer.reference} compare={drawer.compare} allowedFormats={drawer.formats} onClose={closeDrawer} onAttach={attach} /></Suspense>}
    {drawer?.kind === 'knowledge' && <Suspense fallback={<p className="muted drawer-loading">正在加载知识库…</p>}><KnowledgeDrawer key={`knowledge:${drawer.reference?.versionId ?? ''}:${drawer.reference?.unitId ?? ''}`} project={project} reference={drawer.reference} onClose={closeDrawer} /></Suspense>}
    {drawer?.kind === 'capabilities' && capabilities && <Suspense fallback={<p className="muted drawer-loading">正在加载能力目录…</p>}><CapabilityDrawer capabilities={capabilities} onExample={prompt => { fillTemplate(prompt, ui.examples.find(item => item.prompt === prompt)?.title ?? '推荐问题'); closeDrawer(); composer.current?.focus(); }} onClose={closeDrawer} /></Suspense>}
    {drawer?.kind === 'readiness' && <Suspense fallback={<p className="muted drawer-loading">正在加载服务状态…</p>}><ReadinessDrawer services={capabilities?.services} onClose={closeDrawer} /></Suspense>}
    {file && uploadLimitBytes && <UploadDialog file={file} project={project} storageScope={storageScope} maxBytes={uploadLimitBytes} onClose={() => setFile(null)} onUploaded={attach} />}
  </div>;
}
