import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useDrawerController } from './use-drawer-controller';
import { useWorkspaceController } from './use-workspace-controller';
import { useMessageActions } from './use-message-actions';
import { useProjectSessions } from './use-project-sessions';
import { useSessionStream } from './use-session-stream';
import { hasVerificationMarker, isVerificationHistory, turnPhase } from './chat-state';
import { ActivityPanel } from './ActivityPanel';
import { MessageComposer } from './MessageComposer';
import { IntentOptions, RoutingDebug } from './RoutingDetails';
import { ConfirmationCard, ResultCard } from './ChatCards';
import { resourceItemFromDetail } from './resource-item';
import { UploadDialog } from './UploadDialog';
import { AssistantContent, MessageContent } from './MessageContent';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { WorkflowDraftCard } from './WorkflowDraftCard';
import { productIconUrl, productUi } from './product-ui';
import { TaskUpdateCard } from './TaskCard';
import { MessageQueue } from './MessageQueue';
import { ControlProposalCard } from './ControlProposalCard';
import type { DebugEntry, KnowledgeReference, ResourceItem } from './types';
import { ApiError, callApi, loginResearch, logoutResearch } from './api';
import { SessionRenameDialog } from './SessionRenameDialog';
import { decodeResourceDetail } from './api-contract';
import { identityStorageScope } from './browser-scope';

const WorkspacePanel = lazy(() => import('./WorkspacePanel').then(module => ({ default: module.WorkspacePanel })));
const DatabaseWorkbench = lazy(() => import('./DatabaseWorkbench').then(module => ({ default: module.DatabaseWorkbench })));
const KnowledgeDrawer = lazy(() => import('./Drawers').then(module => ({ default: module.KnowledgeDrawer })));
const CapabilityDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.CapabilityDrawer })));
const ReadinessDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.ReadinessDrawer })));

function localizedSessionTitle(title: string | null) {
  return (title || '新的对话')
    .replace(/Dataset:/gi, '数据集:')
    .replace(/Version:/gi, '版本:')
    .replace(/Semantics:/gi, '语义:')
    .replace(/checksum:/gi, '校验和:');
}

export default function App() {
  const ui = productUi();
  const mark = productIconUrl();
  const debugEnabled = new URLSearchParams(window.location.search).get('debug') === '1';
  const [debug, setDebug] = useState<DebugEntry[]>([]);
  const log = useCallback((entry: DebugEntry) => setDebug(previous => [...previous.slice(-79), entry]), []);
  const {
    activeId, capabilities, capabilityError, sessionError, clearSessionError,
    sessions, sessionCursor, loadSessions, connectProject,
    sessionQuery, setSessionQuery, showArchived, setShowArchived, updateSession,
  } = useProjectSessions(log);
  const project = capabilities?.projectId ?? '';
  const storageScope = identityStorageScope(ui.productId, project, capabilities?.currentUser.subject ?? '');
  const canResearch = capabilities?.currentUser.roles.some(
    role => role === 'admin' || role === 'researcher',
  ) ?? false;
  const configuredServices = new Set(capabilities?.services ?? []);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const {
    drawer, closeDrawer, openDatabase, openKnowledge,
    openCapabilities, openReadiness,
  } = useDrawerController();
  const workspace = useWorkspaceController(activeId, storageScope);
  const stream = useSessionStream(activeId, project, log);
  const {
    preview, stopping, history, workflowDrafts, results, loading, loadingOlder,
    connection, connected, trace, refresh, olderMessages, loadTrace,
  } = stream;
  const {
    queue, changeQueue, enqueue, draft, updateDraft, attachments, setAttachments,
    submission, pending, pendingLabel, runtimeBusy, pendingControl, busy, lastTurn,
    awaitingConfirmation, error, clearError, canSend, stopRun, newChat, send,
    decision, controlDecision, reviseConfirmation, recoverWorkflow, newChatWithAttachments,
  } = useMessageActions({
    activeId,
    storageScope,
    project,
    history,
    connected,
    loading,
    stopping,
    setStopping: stream.setStopping,
    externalError: stream.error ?? sessionError,
    clearExternalError: () => { stream.clearError(); clearSessionError(); },
    loadSessions,
    refresh,
    refs: stream.refs,
    closeConfirmationEditor: workspace.closeConfirmation,
  });
  const [file, setFile] = useState<File | null>(null);
  const [uploadLimitBytes, setUploadLimitBytes] = useState<number | null>(null);
  const [uploadConfigError, setUploadConfigError] = useState<ApiError | null>(null);
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [newMessages, setNewMessages] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    atBottom.current = true;
    setNewMessages(false);
  }, [activeId]);
  useEffect(() => {
    if (atBottom.current) bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    else setNewMessages(true);
  }, [history?.items.at(-1)?.messageId, results.length]);

  function attach(resource: ResourceItem) {
    setAttachments(previous => [...previous.filter(item => item.versionId !== resource.versionId), resource]);
    setFile(null);
    closeDrawer();
    workspace.focus('inputs');
    composer.current?.focus();
  }
  async function prepareUpload(selected: File) {
    try {
      const data = await callApi('data_capabilities', '/data/capabilities');
      const limit = data.limits.uploadBytes;
      if (!Number.isSafeInteger(limit) || limit <= 0 || data.project !== project) {
        throw new ApiError(502, 'API_RESPONSE_INVALID');
      }
      setUploadLimitBytes(limit);
      setUploadConfigError(null);
      setFile(selected);
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      setUploadConfigError(caught);
    }
  }
  function resultItem(resource: import('./types').ResourceReference): ResourceItem {
    return { ...resource, familyId: resource.datasetId, name: resource.outputName ? (ui.outputs[resource.outputName]?.label ?? resource.outputName) : '分析成果' };
  }
  const openResource = workspace.openResource;
  const openInputResource = useCallback((id: string) => openDatabase({ id }), [openDatabase]);
  async function attachCurrentVersion(id: string) {
    try {
      const detail = await callApi('data_get_version', `/data/versions/${encodeURIComponent(id)}`, undefined, undefined, decodeResourceDetail);
      const object = detail.objects.find(item => item.role === 'raw') ?? detail.objects[0];
      if (!object) throw new TypeError('DATA_VERSION_HAS_NO_OBJECT');
      attach(resourceItemFromDetail(detail, object));
    } catch {
      openInputResource(id);
    }
  }
  const showKnowledge = useCallback((reference: KnowledgeReference) => openKnowledge(reference), [openKnowledge]);
  function jumpToTurn(revision: number) {
    document.getElementById(`turn-${revision}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function exampleButton(index: number) {
    const example = ui.examples[index];
    return <button key={example.id} aria-label={example.title} title="填入示例问题，发送前可修改" onClick={() => { updateDraft(example.prompt); composer.current?.focus(); }}><Icon name={example.icon} /><span>{example.title}</span><Icon name="arrow-up-right" /></button>;
  }

  const activeSummary = sessions.find(item => item.sessionId === activeId);
  const verificationSession = hasVerificationMarker(activeSummary?.title) || isVerificationHistory(history);
  const recoveredTitle = localizedSessionTitle(history?.items.find(item => item.role === 'user')?.content.slice(0, 120) ?? '当前对话');
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

  return <div className="app-shell" data-testid="chat-app">
    <aside id="conversation-sidebar" className={`sidebar ${leftCollapsed ? 'is-collapsed' : ''}`} aria-label="会话侧栏">
      <div className="sidebar-heading"><a className="brand" href="#/" aria-label={`${ui.brand.productName}首页`}><img src={mark} alt="" /><span>{ui.brand.productName}</span></a><button className="icon-button panel-toggle" aria-label={leftCollapsed ? '展开会话侧栏' : '折叠会话侧栏'} aria-expanded={!leftCollapsed} onClick={() => setLeftCollapsed(value => !value)}><Icon name={leftCollapsed ? 'arrow-right' : 'arrow-left'} /></button></div>
      <button aria-label="新建对话" className="new-chat" onClick={() => void newChat()} disabled={!project || pending || !canResearch}><Icon name="plus" /><span className="new-chat-label">新建对话</span><span className="key-hint"><Icon name="arrow-up-right" /></span></button>
      <div className="sidebar-label">{showArchived ? '已归档对话' : '最近对话'}</div>
      <div className="session-filters"><label><span className="sr-only">搜索对话</span><input type="search" value={sessionQuery} onChange={event => setSessionQuery(event.target.value)} placeholder="搜索对话" /></label><label className="archive-toggle"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />显示归档</label></div>
      <nav className="session-list" data-testid="session-list" aria-label="历史对话">{activeId && history && !activeSummary && <a className="session-link active" data-testid="recovered-active-session" href={`#/sessions/${activeId}`} aria-label={recoveredSessionTitle} title={recoveredSessionTitle}><span className="chat-symbol"><Icon name="chat" /></span><span className="session-title">{recoveredSessionTitle}</span></a>}{sessions.map(item => { const title = localizedSessionTitle(item.title); return <a className={`session-link ${item.sessionId === activeId ? 'active' : ''}`} key={item.sessionId} href={`#/sessions/${item.sessionId}`} aria-label={title} title={title}><span className="chat-symbol"><Icon name="chat" /></span><span className="session-title">{title}</span></a>; })}
        {!sessions.length && !(activeId && history) && <p className="sidebar-empty">暂无对话</p>}
        {sessionCursor !== null && <button className="text-button" onClick={() => void loadSessions(sessionCursor)}>更早的对话</button>}
      </nav>
      {activeSummary && <div className="session-actions"><SessionRenameDialog title={activeSummary.title ?? ''} onRename={title => updateSession(activeSummary.sessionId, { title })} /><button onClick={() => void updateSession(activeSummary.sessionId, { archived: !activeSummary.archived }).then(() => { if (!activeSummary.archived) window.location.hash = '#/'; })}>{activeSummary.archived ? '取消归档' : '归档'}</button></div>}
      <div className="sidebar-tools"><button aria-label="能力目录" title="能力目录" onClick={openCapabilities} disabled={!capabilities}><Icon name="layers" /><span className="sidebar-tool-label">能力</span><Icon name="arrow-up-right" /></button>{configuredServices.has('data') && <button aria-label="数据库" title="数据库" onClick={() => openDatabase()} disabled={!project}><Icon name="folder" /><span className="sidebar-tool-label">数据库</span><Icon name="arrow-up-right" /></button>}{configuredServices.has('knowledge-next') && <button aria-label="知识库" title="知识库" onClick={() => openKnowledge()} disabled={!project}><Icon name="book" /><span className="sidebar-tool-label">知识库</span><Icon name="arrow-up-right" /></button>}</div>
    </aside>
    <main className="main-panel">
      <header className="topbar"><div><span className="product-title" title={project ? `项目：${project}` : '正在连接项目…'}>{ui.brand.workspaceName}</span><span className="environment-badge">项目环境</span><span className="current-user">{capabilities?.currentUser.displayName ?? '正在验证身份'}</span><button className="text-button" onClick={() => void logoutResearch().finally(() => window.location.reload())}>退出</button></div><button className="connection-state" data-testid="connection-status" onClick={openReadiness}><span className={`status-dot ${project && (!activeId || connected) ? 'online' : ''}`} />{capabilityError ? '服务未连接' : !project ? '正在连接项目' : activeId ? ({ connecting: '正在连接实时更新', connected: '实时更新已连接', disconnected: '实时更新连接中断' })[connection] : '项目已连接'}</button></header>
      <div className="result-notice" role="note">结果须结合输入、参数、lineage 和验收报告解释。</div>
      <div className="chat-scroll" data-testid="message-list" onScroll={event => { const node = event.currentTarget; atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; if (atBottom.current) setNewMessages(false); }}>
        {!history?.items.length && !history?.controlMessages?.length && !history?.task && !loading ? <section className="welcome" aria-labelledby="welcome-title"><h1 id="welcome-title">{ui.brand.welcome}</h1><details className="usage-guide"><summary>使用说明</summary><p>PRIVATE 资料仅 owner 和 admin 可读；PROJECT 资料可供项目成员共享。归档前请复核输入 checksum、参数、随机种子、模型实现和 lineage。</p></details>{capabilityError && <div className="connection-notice" role="alert"><strong>业务服务尚未连接</strong><p>{capabilityError.code === 'PRODUCT_UI_IDENTITY_MISMATCH' ? '产品配置与当前服务不一致，业务操作已停用。' : capabilityError.code === 'API_RESPONSE_INVALID' ? '当前仅提供前端预览，暂不能发送。' : '无法连接业务接口，暂不能发送。'}</p><button onClick={() => void connectProject()}>重试连接</button></div>}<section className="example-group" aria-label="常用任务"><h2>常用任务</h2><div className="example-grid">{ui.examples.map((item, index) => item.featured ? exampleButton(index) : null)}</div><details className="more-examples"><summary>更多场景<Icon name="arrow-down" /></summary><div className="example-grid">{ui.examples.map((item, index) => !item.featured ? exampleButton(index) : null)}</div></details></section></section> : <div className="conversation">
          {history && <TaskUpdateCard history={history} taskAction={lastTurn?.taskAction} onOpen={() => workspace.focus(history.task?.spec.unresolvedItems.length || lastTurn?.reply?.unresolvedItems.length ? 'inputs' : 'tasks')} />}
          {history?.controlMessages?.map(message => <ControlProposalCard
            key={message.requestId} message={message}
            proposal={history.controlProposals?.find(item => item.sourceRequestId === message.requestId)}
            disabled={pending} onDecision={(id, action) => void controlDecision(id, action)} onResource={openResource}
          />)}
          {history?.nextCursor !== null && history?.nextCursor !== undefined && <button className="older-messages" disabled={loadingOlder} onClick={() => void olderMessages()}><Icon name="arrow-up" />更早消息</button>}
          {history?.turns.map(turn => {
            return <section className="turn" id={`turn-${turn.goalRevision}`} key={turn.goalRevision} data-goal-revision={turn.goalRevision}>
            {history.items.filter(message => message.goalRevision === turn.goalRevision && message.role === 'user').map(message => <div className="user-message" key={message.messageId}><div className="user-message-content"><MessageContent content={message.content} onResource={openInputResource} />{(message.inputReferences?.length ?? 0) > 0 && <div className="message-input-references">{message.inputReferences!.map(item => { const label = item.outputName ?? item.datasetId; return <button aria-label={`查看附件 ${label}`} key={`${item.versionId}/${item.objectId}`} onClick={() => openInputResource(item.versionId)}><Icon name="file" />{label} · {item.versionId.slice(0, 12)}</button>; })}</div>}</div></div>)}
            <div className="assistant-message"><img className="assistant-avatar" src={mark} alt={ui.brand.assistantName} /><div className="assistant-body">
              <ActivityPanel turn={turn} snapshotAt={history.snapshotAt} connected={connected} />
              {preview?.agentRunId === turn.agentRunId && !turn.reply && !turn.errorCode && <div className="reply-preview" role="status"><small>生成中 · 尚未核验</small><p>{preview.text}</p></div>}
              {turn.reply && <AssistantContent reply={turn.reply} onResource={openResource} onKnowledge={showKnowledge} />}
              <IntentOptions turn={turn} actionable={turn.goalRevision === history.goalRevision} busy={busy || !!submission} onChoose={choice => void send(undefined, choice)} />
              {turn.requestMode === 'WORKFLOW_DRAFT' && turn.workflowDraftId && workflowDrafts[turn.workflowDraftId] && <WorkflowDraftCard draft={workflowDrafts[turn.workflowDraftId]} onKnowledge={showKnowledge} onResource={openResource} />}
              {turn.confirmations.map(confirmation => <ConfirmationCard key={confirmation.confirmationId} confirmation={confirmation} history={history} busy={busy} verificationSession={verificationSession} onDecision={(id, kind, reason) => void decision(id, kind, reason)} onEdit={id => workspace.editConfirmation(id)} onResource={openResource} project={project} />)}
              {turn.executionTasks.map(task => <ResultCard key={task.workflowRunId} task={task} result={results.find(result => result.workflowRunId === task.workflowRunId)} project={project} onResource={openResource} onAttach={resource => attach(resultItem(resource))} onContinue={resource => void newChatWithAttachments([resultItem(resource)], '请基于已附加成果继续分析。')} onCompare={resource => openDatabase({ id: resource.versionId, compare: true })} />)}
              {turn.errorCode && <ErrorNotice title="本轮未完成" code={turn.errorCode}
                agentDiagnostics={turn.diagnostics} agentMetrics={turn.agentMetrics} />}
            </div></div>
          </section>})}
          {loading && <p className="muted">正在恢复对话…</p>}<div ref={bottom} />
        </div>}
      </div>
      <div className="composer-region">
        {newMessages && <button className="new-messages" onClick={() => { atBottom.current = true; setNewMessages(false); bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }}>查看最新消息<Icon name="arrow-down" /></button>}
        {error && <div className="error-banner"><ErrorNotice error={error} /><button className="icon-button" aria-label="关闭提示" onClick={clearError}><Icon name="close" /></button></div>}
        {uploadConfigError && <div className="error-banner"><ErrorNotice error={uploadConfigError} title="上传配置不可用" /><button className="icon-button" aria-label="关闭上传提示" onClick={() => setUploadConfigError(null)}><Icon name="close" /></button></div>}
        {submission && !pending && <div className="submission-recovery" role="status"><span>这条消息的发送状态待确认，草稿已保留。</span><button disabled={loading || !project} onClick={() => void send(submission)}>重试发送</button></div>}
        <MessageQueue items={queue} onChange={changeQueue} />
        {activeId && (runtimeBusy || awaitingConfirmation) && !loading && <div className="run-controls"><button disabled={stopping || pending} onClick={() => void stopRun()}>{stopping ? '正在请求停止…' : '停止当前处理'}</button><button disabled={stopping || pending || !draft.trim()} onClick={() => lastTurn?.status === 'WAITING_EXECUTION' ? enqueue(lastTurn.agentRunId) : void stopRun(true)}>{lastTurn?.status === 'WAITING_EXECUTION' ? '将纠正排为后续消息' : '停止后按输入内容纠正'}</button></div>}
        <div className="composer" data-testid="composer">
          {attachments.length > 0 && <div className="attachment-list">{attachments.map(item => <div className="attachment-chip" key={item.versionId}><button className="text-button" title="查看资料内容与版本" onClick={() => openInputResource(item.versionId)}><Icon name="file" />{item.name} · {item.versionId}</button><button aria-label={`移除附件 ${item.name}`} onClick={() => setAttachments(previous => previous.filter(value => value.versionId !== item.versionId))}><Icon name="close" /></button></div>)}</div>}
        <MessageComposer key={activeId} inputRef={composer} value={draft} selection={false} busy={busy || !canResearch}
            canSend={canSend && canResearch}
            onChange={updateDraft} onSend={() => void send()} tools={<>
              {activeId && busy && <button disabled={!canResearch || (!draft.trim() && !attachments.length)} onClick={() => enqueue()}>稍后发送</button>}
              {configuredServices.has('data') && <><button className="icon-button attach-button" aria-label="添加附件" title="上传资料" disabled={!canResearch || !project || pending || !!file} onClick={() => fileInput.current?.click()}><Icon name="plus" /></button>
              <input ref={fileInput} type="file" hidden onChange={event => { const selected = event.target.files?.[0]; if (selected) void prepareUpload(selected); event.target.value = ''; }} /></>}
            </>} />
        </div>
        <div className="composer-caption">{capabilityError && history?.items.length ? <span>业务服务尚未连接，暂不能发送。</span> : busy && <span>{`${loading ? '正在恢复对话' : pendingControl ? '等待控制确认' : lastTurn && runtimeBusy ? turnPhase(lastTurn) : pendingLabel} · 草稿会保留`}</span>}{debugEnabled && <details className="debug-panel" onToggle={event => { if (event.currentTarget.open) void loadTrace(); }}><summary>调试信息</summary><div className="debug-content"><h3>请求、事件与交互延迟</h3><p>不展示提示词、工具正文或模型服务商配置。</p><pre>{debug.map(entry => `${entry.time} ${entry.kind} ${entry.name} ${entry.status ?? entry.eventId ?? ''}${entry.code ? ` code=${entry.code}` : ''}${entry.elapsedMs !== undefined ? ` ${entry.elapsedMs}ms` : ''}${entry.requestId ? ` request=${entry.requestId}` : ''}`).join('\n')}</pre><RoutingDebug turns={history?.turns ?? []} />{trace !== null && <details><summary>当前运行追踪元数据</summary><pre>{JSON.stringify(trace, null, 2)}</pre></details>}</div></details>}</div>
      </div>
    </main>
    <Suspense fallback={<p className="muted workspace-loading">正在加载工作台…</p>}><WorkspacePanel history={history} results={results} activeId={activeId} storageScope={storageScope} project={project} capabilities={capabilities} dataAvailable={configuredServices.has('data')} collapsed={workspace.collapsed} pendingInputs={attachments} selectedResourceId={workspace.resourceId} editingConfirmationId={workspace.editingConfirmationId} confirmationFocus={workspace.confirmationFocus} focusRequest={workspace.focusRequest} busy={busy} onToggle={workspace.toggleCollapsed} onJump={jumpToTurn} onResource={openResource} onInputResource={(id, input) => { const pendingConfirmation = input ? history?.turns.flatMap(turn => turn.confirmations).find(item => item.status === 'PENDING') : null; if (pendingConfirmation && input) workspace.editConfirmation(pendingConfirmation.confirmationId, { field: input, value: id }); else if (input) void attachCurrentVersion(id); else openInputResource(id); }} onOpenDatabase={formats => openDatabase({ formats })} onRemovePending={id => setAttachments(previous => previous.filter(item => item.versionId !== id))} onEdit={id => workspace.editConfirmation(id)} onResolveParameter={name => { const pendingConfirmation = history?.turns.flatMap(turn => turn.confirmations).find(item => item.status === 'PENDING'); if (pendingConfirmation) workspace.editConfirmation(pendingConfirmation.confirmationId, { field: name }); else { updateDraft(`${draft}${draft.trim() ? '\n' : ''}补充参数 ${name}：`); composer.current?.focus(); } }} onEditClose={workspace.closeConfirmation} onRevision={(confirmation, actions) => void reviseConfirmation(confirmation, actions)} onRecover={id => void recoverWorkflow(id)} onReview={text => void send(undefined, text)} /></Suspense>
    {drawer && <button className="drawer-shade" aria-label="关闭浮层" onClick={closeDrawer} />}
    {drawer?.kind === 'database' && <Suspense fallback={<p className="muted drawer-loading">正在加载数据库…</p>}><DatabaseWorkbench key={`database:${drawer.id ?? ''}:${drawer.compare ? 'compare' : ''}:${drawer.formats?.join(',') ?? ''}`} project={project} initialId={drawer.id} compare={drawer.compare} allowedFormats={drawer.formats} onClose={closeDrawer} onAttach={attach} /></Suspense>}
    {drawer?.kind === 'knowledge' && <Suspense fallback={<p className="muted drawer-loading">正在加载知识库…</p>}><KnowledgeDrawer key={`knowledge:${drawer.reference?.versionId ?? ''}:${drawer.reference?.unitId ?? ''}`} project={project} reference={drawer.reference} onClose={closeDrawer} /></Suspense>}
    {drawer?.kind === 'capabilities' && capabilities && <Suspense fallback={<p className="muted drawer-loading">正在加载能力目录…</p>}><CapabilityDrawer capabilities={capabilities} onExample={prompt => { updateDraft(prompt); closeDrawer(); composer.current?.focus(); }} onClose={closeDrawer} /></Suspense>}
    {drawer?.kind === 'readiness' && <Suspense fallback={<p className="muted drawer-loading">正在加载服务状态…</p>}><ReadinessDrawer services={capabilities?.services} onClose={closeDrawer} /></Suspense>}
    {file && uploadLimitBytes && <UploadDialog file={file} project={project} storageScope={storageScope} maxBytes={uploadLimitBytes} onClose={() => setFile(null)} onUploaded={attach} />}
  </div>;
}
