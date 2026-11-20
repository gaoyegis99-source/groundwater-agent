import { Activity, lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useDrawerController } from './use-drawer-controller';
import { useWorkspaceController } from './use-workspace-controller';
import { useMessageActions } from './use-message-actions';
import { currentSessionId, useProjectSessions } from './use-project-sessions';
import { useSessionStream } from './use-session-stream';
import { hasVerificationMarker, isComposedConfirmation, isVerificationHistory, needsExecutionInputs, turnPhase } from './chat-state';
import { TurnTimeline } from './TurnTimeline';
import { MessageComposer } from './MessageComposer';
import { RoutingDebug } from './RoutingDetails';
import { ConfirmationCard } from './ChatCards';
import { WorkflowPreparation } from './WorkflowPreparation';
import { matchesModel, modelIdentity, workflowFileKey, type WorkflowModel, type WorkflowSelection } from './workflow-selection';
import { resourceItemFromDetail } from './resource-item';
import { UploadDialog } from './UploadDialog';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { productIconUrl, productUi, visibleBusinessCapabilities, type BusinessCapability, type ProductUiProfile, type WorkflowContinuation } from './product-ui';
import { sessionTask, TaskHeader } from './TaskCard';
import { modelName, taskModelName } from './card-presentation';
import { MessageQueue } from './MessageQueue';
import { PendingAttachments } from './PendingAttachments';
import { Loading } from './ui/Feedback';
import { ControlProposalCard } from './ControlProposalCard';
import type { DebugEntry, KnowledgeReference, ResourceItem, ResourceReference } from './types';
import { ApiError, callApi, loginResearch, logoutResearch, readDataVersion } from './api';
import { localizedSessionTitle, SessionNavigation } from './SessionNavigation';
import { identityStorageScope } from './browser-scope';
import './conversation-polish.css';
import './conversation-tasks.css';
import { ProjectTaskHistory } from './ProjectTaskHistory';
import { readWorkItemRoute, sessionFromWorkItem, useWorkItemHash, workItemHref, workItemReturnId } from './work-item-route';

const WorkspacePanel = lazy(() => import('./WorkspacePanel').then(module => ({ default: module.WorkspacePanel })));
const WorkItemWorkspace = lazy(() => import('./WorkItemWorkspace').then(module => ({ default: module.WorkItemWorkspace })));
const SessionWorkContext = lazy(() => import('./SessionWorkContext').then(module => ({ default: module.SessionWorkContext })));
const DatabaseWorkbench = lazy(() => import('./DatabaseWorkbench').then(module => ({ default: module.DatabaseWorkbench })));
const KnowledgeDrawer = lazy(() => import('./Drawers').then(module => ({ default: module.KnowledgeDrawer })));
const CapabilityDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.CapabilityDrawer })));
const BusinessCapabilityDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.BusinessCapabilityDrawer })));
const ReadinessDrawer = lazy(() => import('./ProductDrawers').then(module => ({ default: module.ReadinessDrawer })));

export default function App() {
  const ui = productUi();
  const mark = productIconUrl();
  const debugEnabled = new URLSearchParams(window.location.search).get('debug') === '1';
  const [debug, setDebug] = useState<DebugEntry[]>([]);
  const log = useCallback((entry: DebugEntry) => {
    if (debugEnabled) setDebug(previous => [...previous.slice(-79), entry]);
  }, [debugEnabled]);
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
  const hash = useWorkItemHash();
  const researchWorkspace = ui.workspaceLayout === 'research';
  const workRoute = researchWorkspace ? readWorkItemRoute(hash) : null;
  const showWorkItems = researchWorkspace && !!storageScope && canResearch && !capabilityError;
  const returnWorkItem = showWorkItems ? workItemReturnId(hash) : null;
  const configuredServices = new Set(capabilities?.services ?? []);
  const businessCapabilities = visibleBusinessCapabilities(ui, capabilityError ? null : capabilities);
  const featuredCapabilities = businessCapabilities.filter(item => item.tier === 'CORE' && item.defaultVisible);
  const linkedExamples = new Set(ui.businessCapabilities?.map(item => item.exampleId));
  const examples = ui.examples.filter(item => !linkedExamples.has(item.id));
  const featuredExamples = ui.businessCapabilities ? [] : examples.filter(item => item.featured);
  const otherExamples = ui.businessCapabilities ? examples : examples.filter(item => !item.featured);
  const [historyPanelKey, setHistoryPanelKey] = useState('');
  const historyButton = useRef<HTMLButtonElement>(null);
  const historyPanelOpen = !!storageScope && historyPanelKey === `${storageScope}/${activeId}`;
  const [readingOlder, setReadingOlder] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const homeWorkbenchOpener = useRef<HTMLButtonElement>(null);
  const {
    drawer, closeDrawer, openDatabase, openKnowledge,
    openCapabilities, openBusinessCapability, openReadiness,
  } = useDrawerController();
  const businessDetail = drawer?.kind === 'business-capability'
    ? businessCapabilities.find(({ ref }) => ref.id === drawer.id && ref.version === drawer.version) : undefined;
  const businessDetailUnavailable = drawer?.kind === 'business-capability' && !businessDetail;
  useEffect(() => { if (businessDetailUnavailable) closeDrawer(); }, [businessDetailUnavailable, closeDrawer]);
  const drawerShadeVisible = drawer?.kind === 'database' || drawer?.kind === 'knowledge';
  const compactConversation = researchWorkspace && !!activeId;
  const workspace = useWorkspaceController(activeId, storageScope);
  const hideCollapsedHomeWorkspace = !activeId && researchWorkspace;
  const deferHomeWorkspace = hideCollapsedHomeWorkspace && !workspace.homeOpened && !workspace.routeTab;
  const sessionWorkspaceOpen = compactConversation && !historyPanelOpen && !workspace.collapsed;
  function toggleWorkspace() {
    if (compactConversation && historyPanelOpen && !workspace.routeTab) {
      setHistoryPanelKey('');
      if (workspace.collapsed) workspace.toggleCollapsed();
      return;
    }
    workspace.toggleCollapsed();
    if (!activeId && !workspace.collapsed) {
      requestAnimationFrame(() => homeWorkbenchOpener.current?.focus({ preventScroll: true }));
    }
  }
  const viewingWorkbench = !!workspace.routeTab;
  const workspaceOpener = useRef<HTMLElement | null>(null);
  const previouslyInWorkbench = useRef(false);
  const composerAfterRoute = useRef(false);
  useEffect(() => {
    if (viewingWorkbench) {
      if (document.activeElement instanceof HTMLElement && document.activeElement.closest('.main-panel, .project-task-history')) {
        workspaceOpener.current = document.activeElement;
      }
      previouslyInWorkbench.current = true;
      return;
    }
    if (previouslyInWorkbench.current) {
      previouslyInWorkbench.current = false;
      const frame = requestAnimationFrame(() => {
        const opener = workspaceOpener.current;
        workspaceOpener.current = null;
        if (document.activeElement?.matches('#current-confirmation, .turn')) return;
        if (composerAfterRoute.current) {
          composerAfterRoute.current = false;
          composer.current?.focus();
          return;
        }
        if (opener?.isConnected && opener.getClientRects().length) opener.focus();
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
    decision, controlDecision, reviseConfirmation, recoverWorkflow, newChatWithAttachments, workflow, setWorkflow,
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
  const currentDraft = lastTurn?.workflowDraftId ? workflowDrafts[lastTurn.workflowDraftId] : undefined;
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
  const followReply = !viewingWorkbench && !currentConfirmation;
  const alignReadyReply = useCallback(() => {
    if (followReply && atBottom.current) bottom.current?.scrollIntoView({ block: 'end' });
  }, [followReply]);
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
  const attachButton = useRef<HTMLButtonElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const [templateChange, setTemplateChange] = useState<{ scope: string; sessionId: string; before: string; after: string; title: string; workflowContext?: string; previousWorkflow?: WorkflowSelection | null } | null>(null);
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
    setReadingOlder(false);
    setHistoryPanelKey('');
  }, [activeId, storageScope]);
  useEffect(() => {
    if (history?.sessionId !== activeId || viewingWorkbench) return;
    const key = `${storageScope}/${activeId}`;
    if (latestVisible.current?.key === key && latestVisible.current.signature === liveSignature) return;
    const initial = latestVisible.current === null;
    latestVisible.current = { key, signature: liveSignature };
    if (currentConfirmation && initial) {
      const scroller = chatScroll.current;
      const confirmation = document.getElementById('current-confirmation');
      const target = confirmation?.querySelector('[data-testid="confirm-execution"]') ?? confirmation;
      if (scroller && target) {
        const viewport = scroller.getBoundingClientRect();
        const bounds = target.getBoundingClientRect();
        if (bounds.bottom > viewport.bottom - 24) scroller.scrollTop += bounds.bottom - viewport.top - viewport.height * 0.65;
      }
      return;
    }
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

  const selectedModel = workflow && capabilities?.models.find(model => matchesModel(workflow, model));
  const preparationForSelection = workflow && snapshotCurrent && preparation?.freshForCurrentTurn
    ? preparation.entries.filter(entry => entry.targetId === workflow.target.modelId && entry.targetVersion === workflow.target.modelVersion
      && Object.entries(workflow.parameters).every(([name, value]) => entry.parameters[name] === value)
      && Object.entries(workflow.bindings).every(([name, key]) => {
        const item = attachments.find(ref => workflowFileKey(ref) === key);
        const inspected = entry.inputs[name];
        return !key || (!!item && (inspected ? workflowFileKey(inspected) === key : entry.parameters[name] === item.versionId));
      })).sort((left, right) => Number(right.status === 'READY') - Number(left.status === 'READY'))[0]
    : undefined;
  const [bindingTarget, setBindingTarget] = useState<{ session: string; scope: string; modelId: string; modelVersion: string; name: string }>();
  const activeBinding = workflow && bindingTarget?.session === activeId && bindingTarget.scope === storageScope
    && bindingTarget.modelId === workflow.target.modelId && bindingTarget.modelVersion === workflow.target.modelVersion ? bindingTarget : undefined;
  useEffect(() => { if (drawer?.kind !== 'database') setBindingTarget(undefined); }, [drawer]);
  function pickWorkflowInput(name: string, formats: string[]) {
    if (!workflow) return;
    setBindingTarget({ session: activeId, scope: storageScope, modelId: workflow.target.modelId, modelVersion: workflow.target.modelVersion, name });
    openDatabase({ formats });
  }
  function attach(resource: ResourceItem) {
    if (currentSessionId() !== activeId || !canResearch) return;
    currentVersionRead.current?.abort();
    setCurrentVersionError(null);
    setAttachments(previous => [...previous.filter(item => workflowFileKey(item) !== workflowFileKey(resource)), resource]);
    if (workflow && activeBinding) {
      setWorkflow({ ...workflow, bindings: { ...workflow.bindings, [activeBinding.name]: workflowFileKey(resource) } });
      setBindingTarget(undefined);
    }
    setFile(null);
    closeDrawer();
    if (workspace.routeTab === 'inputs' && activeBinding) {
      requestAnimationFrame(() => (document.getElementById('workflow-preparation') ?? document.getElementById('workspace-route-heading'))?.focus());
    } else if (workspace.routeTab) {
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
  const detailOrigin = useRef<{ session: string; node: HTMLElement; fallback: HTMLElement | null; offset: number } | undefined>(undefined);
  const rememberDetailOrigin = useCallback(() => {
    const node = document.activeElement;
    if (node instanceof HTMLElement && node.closest('.main-panel, .project-task-history')) {
      detailOrigin.current = { session: activeId, node, fallback: node.closest('.turn, .confirmation-card, #workflow-preparation'), offset: chatScroll.current?.scrollTop ?? 0 };
    }
  }, [activeId]);
  useLayoutEffect(() => {
    if (workspace.routeTab || drawer || !detailOrigin.current) return;
    const origin = detailOrigin.current;
    detailOrigin.current = undefined;
    if (origin.session !== activeId) return;
    if (chatScroll.current) chatScroll.current.scrollTop = origin.offset;
    const target = origin.node.isConnected ? origin.node : origin.fallback;
    target?.focus({ preventScroll: true });
  }, [workspace.routeTab, drawer, activeId]);
  const openResource = (id: string, reference?: ResourceReference) => {
    rememberDetailOrigin();
    workspace.openResource(id, reference);
  };
  const openInputResource = useCallback((id: string, reference?: ResourceReference) => { rememberDetailOrigin(); openDatabase({ id, reference }); }, [openDatabase, rememberDetailOrigin]);
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
  const showKnowledge = useCallback((reference: KnowledgeReference) => { rememberDetailOrigin(); openKnowledge(reference); }, [openKnowledge, rememberDetailOrigin]);
  useEffect(() => {
    if (workspace.routeTab || turnAfterRoute.current === null) return;
    const revision = turnAfterRoute.current;
    turnAfterRoute.current = null;
    const turn = document.getElementById(`turn-${revision}`);
    turn?.focus({ preventScroll: true });
    turn?.scrollIntoView({ block: 'start' });
  }, [workspace.routeTab]);

  function jumpToTurn(revision: number, runId?: string) {
    if (workspace.routeTab) {
      turnAfterRoute.current = revision;
      workspace.returnToConversation();
      return;
    }
    const turn = (runId && document.getElementById(`result-${runId}`)) || document.getElementById(`turn-${revision}`);
    turn?.focus({ preventScroll: true });
    turn?.scrollIntoView({ block: 'start' });
  }
  function exampleButton(example: ProductUiProfile['examples'][number]) {
    return <button type="button" key={example.id} aria-label={`${example.title} ${example.prompt}`} onClick={() => fillTemplate(example.prompt, example.title)}><Icon name={example.icon} /><span className="example-copy"><strong>{example.title}</strong><small>{example.prompt}</small></span><Icon name="arrow-right" /></button>;
  }
  function fillTemplate(prompt: string, title: string, workflowContext?: string, previousWorkflow?: WorkflowSelection | null) {
    setTemplateChange({ scope: storageScope, sessionId: activeId, before: draft, after: prompt, title, workflowContext, previousWorkflow });
    updateDraft(prompt); composer.current?.focus();
  }
  const undoTemplate = templateChange?.scope === storageScope && templateChange.sessionId === activeId && templateChange.after === draft ? templateChange : null;
  function fillFromCapabilities(prompt: string, title: string, workflowContext?: string) {
    closeDrawer();
    fillTemplate(prompt, title, workflowContext);
    requestAnimationFrame(() => composer.current?.focus());
  }
  function fillBusinessCapability(capability: BusinessCapability) {
    closeDrawer();
    setBindingTarget(undefined);
    fillTemplate(capability.prompt, capability.title, undefined, workflow);
    setWorkflow(null);
    focusComposerFromWorkbench();
  }
  function chooseWorkflow(model: WorkflowModel) {
    const target = modelIdentity(model);
    if (!target) return;
    const name = productUi().models[model.modelId].displayName;
    const request = `请准备执行「${name}」工作流。请核对我提供的资料和参数，准备检查通过后提交执行确认。`;
    const existing = undoTemplate?.workflowContext ?? draft;
    const filled = existing.trim() ? `${request}\n\n${existing}` : request;
    closeDrawer();
    setBindingTarget(undefined);
    fillTemplate(filled, name, existing, workflow);
    setWorkflow(workflow && matchesModel(workflow, model) ? workflow : { target, bindings: {}, parameters: {} });
    requestAnimationFrame(() => {
      if (workspace.routeTab === 'inputs') document.getElementById('workflow-preparation')?.focus();
      else composer.current?.focus();
    });
  }
  function prepareContinuation(runId: string, policy: WorkflowContinuation, reference: ResourceReference) {
    if (currentSessionId() !== activeId || reference.outputName !== policy.output) return;
    const targetModel = capabilities?.models.find(model => model.modelId === policy.targetModel);
    const target = targetModel && modelIdentity(targetModel);
    if (!target) return;
    const item = resultItem(reference);
    setBindingTarget(undefined);
    setAttachments(previous => [...previous.filter(resource => resource.versionId !== reference.versionId), item]);
    setWorkflow({ target, bindings: { [policy.targetInput]: workflowFileKey(item) }, parameters: {},
      continuation: { runId, outputName: policy.output } });
    if (!draft.trim()) updateDraft('请使用这份成果准备下一步工作流。');
    requestAnimationFrame(() => composer.current?.focus());
  }
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
  function focusComposerFromWorkbench() {
    if (workspace.routeTab) {
      composerAfterRoute.current = true;
      workspace.returnToConversation();
      return;
    }
    requestAnimationFrame(() => composer.current?.focus());
  }
  function requestTaskChanges() {
    if (!activeTask || !canResearch || currentSessionId() !== activeId) return;
    if (!draft.trim()) updateDraft('请修改本任务的以下内容：');
    focusComposerFromWorkbench();
  }
  function resolvePreparationParameter(name: string) {
    if (currentConfirmation && !isComposedConfirmation(currentConfirmation)) {
      workspace.editConfirmation(currentConfirmation.confirmationId, { field: name });
      return;
    }
    updateDraft(`${draft}${draft.trim() ? '\n' : ''}补充参数 ${name}：`);
    focusComposerFromWorkbench();
  }
  const latestRuns = lastTurn?.executionTasks.filter(run => run.isLatestAttempt !== false) ?? [];
  const latestRun = latestRuns.find(run => run.status === 'RUNNING' && run.stage !== 'PUBLISHING')
    ?? latestRuns.find(run => run.status === 'RUNNING')
    ?? latestRuns.find(run => run.status === 'QUEUED') ?? latestRuns.at(-1);
  const failedRun = latestRuns.find(run => ['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(run.status));
  const hasOpenRun = latestRun && ['QUEUED', 'RUNNING'].includes(latestRun.status);
  const needsInputs = preparationForSelection?.status === 'BLOCKED' || activeTask?.progress.state === 'WAITING_INPUT'
    || (!runtimeBusy && !currentConfirmation && needsExecutionInputs(lastTurn));
  const taskModel = currentConfirmation?.actions[0]?.modelId ?? lastTurn?.confirmations.at(-1)?.actions[0]?.modelId
    ?? latestRuns[0]?.modelId ?? (preparation?.agentRunId === lastTurn?.agentRunId ? preparation?.entries[0]?.targetId : null);
  const openPreparation = () => {
    const section = document.getElementById('workflow-preparation');
    const details = section?.querySelector('details');
    if (details) details.open = true;
    const issue = section?.querySelector<HTMLElement>('[data-needs-input="true"] :is(select, input, button):not(:disabled)');
    (issue ?? section ?? composer.current)?.focus();
  };
  const currentTask: { name: string; stage: string; action?: string; run?: () => void } | null = taskModel || currentConfirmation || (activeTask && lastTurn?.dialoguePolicy !== 'READ_ONLY') || needsInputs ? {
    name: taskModel ? taskModelName(taskModel) : activeSummary?.title || '研究任务',
    ...(connection === 'disconnected' && activeId ? { stage: '状态待核对', action: '刷新状态', run: () => void refresh(activeId) }
      : currentConfirmation ? { stage: '等待执行确认' }
      : hasOpenRun ? { stage: latestRun.stage === 'PUBLISHING' ? '正在发布成果' : latestRun.status === 'QUEUED' ? '等待执行' : '正在执行', action: '查看运行详情', run: () => { rememberDetailOrigin(); workspace.focus('tasks'); } }
      : lastTurn?.confirmations.at(-1)?.status === 'REJECTED' ? { stage: '本批次已拒绝' }
      : lastTurn?.confirmations.some(item => item.publishedOutputSteps?.some(step => step.errorCode)) ? { stage: '分析已发布，地图准备未完成' }
      : needsInputs ? { stage: '需要补充资料或参数', action: '补齐资料', run: openPreparation }
      : failedRun ? { stage: failedRun.sourceWorkflowRunId ? '分析已发布，地图生成未完成' : '执行未完成', action: '查看失败与恢复', run: () => jumpToTurn(failedRun.goalRevision, failedRun.workflowRunId) }
      : latestRun?.status === 'SUCCEEDED' ? { stage: '分析已完成', action: '查看成果', run: () => jumpToTurn(latestRun.goalRevision, latestRun.workflowRunId) }
      : { stage: lastTurn ? turnPhase(lastTurn) : '准备任务', action: '查看任务概览', run: () => workspace.focus('tasks') }),
  } : null;
  const researchOverview = activeTask && !taskModel && lastTurn?.semanticIntent !== 'CONSULT';
  const verificationSession = hasVerificationMarker(activeSummary?.title) || isVerificationHistory(history);
  const recoveredTitle = localizedSessionTitle(activeHistory?.items.find(item => item.role === 'user')?.content.slice(0, 120) ?? '当前对话');
  const recoveredSessionTitle = `当前会话 · ${verificationSession && !recoveredTitle.startsWith('【验证会话】') ? `【验证会话】${recoveredTitle}` : recoveredTitle}`;
  const connectionLabel = capabilityError ? '服务未连接' : !project ? '正在连接项目' : activeId
    ? { connecting: '正在连接实时更新', connected: '实时更新已连接', disconnected: '正在恢复连接 · 显示最近状态' }[connection]
    : '项目已连接';

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
  if (capabilityError?.code === 'AUTHENTICATION_REQUIRED' && !capabilities) return <main className="research-login"><form onSubmit={event => void submitLogin(event)}><img src={mark} width={44} height={44} alt="" /><h1>登录工作区</h1><label>用户名<input name="username" autoComplete="username" spellCheck={false} autoCapitalize="none" required value={loginUsername} onChange={event => setLoginUsername(event.target.value)} /></label><label>密码<input name="password" type="password" autoComplete="current-password" required value={loginPassword} onChange={event => setLoginPassword(event.target.value)} /></label>{loginError && <p role="alert">登录失败：{loginError}</p>}<button type="submit" disabled={loggingIn}>{loggingIn ? '正在登录…' : '登录'}</button></form></main>;
  if (capabilityError && !capabilities) return <main className="service-unavailable"><h1>服务暂时不可用</h1><p role="alert">{capabilityError.code}：当前无法读取平台能力，业务操作保持禁用。</p>{capabilityError.requestId && <p>请求 ID：<code>{capabilityError.requestId}</code></p>}<button onClick={() => void connectProject()}>重新检查</button></main>;

  const receiptFeedback = receiptPending && <div className="submission-recovery confirmation-receipt" role="status">
        <span>操作结果待核对</span>
        <button disabled={pending} onClick={() => void refresh(activeId)}>重新读取状态</button>
        {canRetryReceipt && <button disabled={pending || !canResearch || !snapshotCurrent} onClick={retryReceipt}>重试同一操作</button>}
      </div>;
  const historyError = activeId && !activeHistory ? stream.error : null;
  const historyReadFailure = historyError && <div className="workbench-read-error session-read-failure">
    <ErrorNotice title="对话读取未完成" error={historyError} />
    <button type="button" aria-disabled={loading} onClick={async event => {
      if (loading) return;
      const retryButton = event.currentTarget;
      await refresh(activeId);
      requestAnimationFrame(() => {
        if (!retryButton.isConnected && document.activeElement === document.body && currentSessionId() === activeId) {
          (document.getElementById('workspace-route-heading') ?? composer.current)?.focus({ preventScroll: true });
        }
      });
    }}>{loading ? '正在重新读取…' : '重新读取对话'}</button>
  </div>;
  const operationFeedback = error && error !== historyError && <div className="error-banner"><ErrorNotice error={error} /><button className="icon-button" aria-label="关闭提示" onClick={clearError}><Icon name="close" /></button></div>;
  const submissionFeedback = submission && !pending && <div className="submission-recovery" role="status">
    <span>这条消息的发送状态待确认，草稿已保留。</span>
    <button disabled={loading || !project || !canResearch} onClick={() => void send(submission)}>重试发送</button>
  </div>;
  const composerFeedback = pending ? pendingLabel : loading ? '正在恢复对话'
    : pendingControl ? '等待控制确认' : submission ? '请先确认上一条消息的发送结果'
    : lastTurn && runtimeBusy ? turnPhase(lastTurn) : null;
  const stopAcknowledged = activeHistory?.controlRequests?.some(control =>
    control.agentRunId === lastTurn?.agentRunId && ['REQUESTED', 'PROCESSING'].includes(control.status));
  const draftId = lastTurn?.workflowDraftId;
  const scopedDraft = draftId && currentDraft?.draftId === draftId && currentDraft.sessionId === activeHistory?.sessionId
    && currentDraft.goalRevision === lastTurn.goalRevision && currentDraft.agentRunId === lastTurn.agentRunId ? currentDraft : undefined;
  const draftRead = draftId ? workflowDraftReads[draftId] : undefined;
  const confirmationCard = currentConfirmation && <ConfirmationCard key={currentConfirmation.confirmationId} confirmation={currentConfirmation} history={activeHistory}
    busy={busy || confirmationWriteDisabled} verificationSession={verificationSession} onDecision={(id, kind, reason) => void decision(id, kind, reason)}
    onEdit={workspace.routeTab === 'parameters' ? undefined : id => { rememberDetailOrigin(); workspace.editConfirmation(id); }}
    onResource={openInputResource} project={project} current capabilities={capabilities}
    onKnowledge={configuredServices.has('knowledge-next') ? showKnowledge : undefined}
    draft={scopedDraft?.payload} />;
  const workflowPreparation = workflow && <WorkflowPreparation
    key={`${storageScope}/${activeId}/${workflow.target.modelId}/${workflow.target.modelVersion}`}
    selection={workflow} model={selectedModel || undefined} attachments={attachments} prepared={preparationForSelection || undefined}
    project={project} expanded={workspace.routeTab === 'inputs'} onResource={openInputResource}
    onKnowledge={configuredServices.has('knowledge-next') ? showKnowledge : undefined}
    onApplyCandidates={configuredServices.has('data') && canResearch ? candidates => {
      const selected = Object.values(candidates);
      setAttachments(previous => [...new Map([...previous, ...selected].map(item => [workflowFileKey(item), item])).values()]);
      setWorkflow({ ...workflow, bindings: { ...workflow.bindings, ...Object.fromEntries(
        Object.entries(candidates).map(([name, item]) => [name, workflowFileKey(item)]),
      ) } });
    } : undefined}
    onChange={value => { setBindingTarget(undefined); setWorkflow(value); }}
    onPick={pickWorkflowInput} />;
  const projectTitle = project ? `项目：${project}` : '正在连接项目…';
  const signOut = capabilities?.authenticationRequired !== false &&
    <button className="text-button" onClick={() => void logoutResearch().finally(() => window.location.reload())}>退出</button>;
  if (workRoute) return <Suspense fallback={<main aria-busy="true"><Loading>正在打开业务工作…</Loading></main>}>
    <WorkItemWorkspace route={workRoute} scope={storageScope} project={project} subject={capabilities?.currentUser.subject ?? ''}
      mark={mark} brand={ui.brand.productName} signOut={signOut} permitted={showWorkItems} identityError={!!capabilityError}
      retryIdentity={() => void connectProject()} onSessionsChange={() => void loadSessions()} />
  </Suspense>;
  const sessionContext = activeId && <div className="session-context">
    <h1 className="sr-only">{activeSummary?.title ? localizedSessionTitle(activeSummary.title) : '研究对话'}</h1>
    <nav className="session-detail-nav" aria-label="会话详情" onClick={event => {
      if (event.target instanceof HTMLElement && event.button === 0 &&
          !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
        const target = event.target.closest<HTMLAnchorElement>('a');
        if (target) workspaceOpener.current = target;
      }
    }}>
      <a href={sessionFromWorkItem(`#/sessions/${activeId}/workbench/inputs`, returnWorkItem)}>数据</a>
      <a href={sessionFromWorkItem(`#/sessions/${activeId}/workbench/draft`, returnWorkItem)}>草稿</a>
      <a href={sessionFromWorkItem(`#/sessions/${activeId}/workbench/parameters`, returnWorkItem)}>参数</a>
      <a href={sessionFromWorkItem(`#/sessions/${activeId}/workbench/tasks`, returnWorkItem)}>运行</a>
      <a href={sessionFromWorkItem(`#/sessions/${activeId}/workbench/results`, returnWorkItem)}>成果</a>
      <button type="button" ref={historyButton} className="session-history-entry" aria-label="任务历史" title="任务历史"
        aria-expanded={historyPanelOpen} aria-controls="project-task-history" disabled={!storageScope}
        onClick={() => setHistoryPanelKey(historyPanelOpen ? '' : `${storageScope}/${activeId}`)}>
        {compactConversation && <Icon name="clock" />}<span>任务历史</span>
      </button>
      <button type="button" className="session-workbench-entry" title={sessionWorkspaceOpen ? '收起任务工作台' : '打开任务工作台'}
        aria-label={sessionWorkspaceOpen ? '收起任务工作台' : '打开任务工作台'}
        aria-expanded={compactConversation ? sessionWorkspaceOpen : undefined}
        aria-controls={compactConversation && !historyPanelOpen ? 'workspace-panel' : undefined}
        onClick={compactConversation ? toggleWorkspace : () => workspace.focus('tasks')}>
        <Icon name="layers" /><span>任务工作台</span><span className="workbench-label-short" aria-hidden="true">工作台</span>
      </button>
    </nav>
  </div>;
  return <div className={`app-shell${activeId ? " has-session" : ""}${workspace.routeTab ? " workspace-route" : ""}`} data-testid="chat-app">
    {currentConfirmation && !workspace.routeTab && <button type="button" className="current-confirmation-skip" onClick={focusCurrentConfirmation}>查看当前确认</button>}
    <a className="main-content-skip" href={workspace.routeTab ? '#workspace-route-heading' : '#conversation-main'}
      onClick={event => {
        event.preventDefault();
        document.getElementById(workspace.routeTab ? 'workspace-route-heading' : 'conversation-main')?.focus();
      }}>跳到主要内容</a>
    <aside id="conversation-sidebar" className={`sidebar session-navigation ${leftCollapsed ? 'is-collapsed' : ''}`}
      aria-label="会话侧栏">
      <div className="sidebar-heading">
        <a className="brand" href="#/" aria-label={`${ui.brand.productName}首页`}><img src={mark} width={32} height={32} alt="" /><span>{ui.brand.productName}</span></a>
        <button className="icon-button panel-toggle desktop-session-toggle" aria-label={leftCollapsed ? '展开会话侧栏' : '折叠会话侧栏'}
          aria-expanded={!leftCollapsed} onClick={() => setLeftCollapsed(value => !value)}><Icon name={leftCollapsed ? 'arrow-right' : 'arrow-left'} /></button>
      </div>
      <SessionNavigation key={storageScope} activeId={activeId} recoveredTitle={activeHistory ? recoveredSessionTitle : null}
        sessions={sessions} query={sessionQuery} showArchived={showArchived} cursor={sessionCursor}
        loading={sessionsLoading} error={sessionError} notice={sessionNotice} updatingId={updatingSessionId}
        canCreate={!!project && !pending && !continuationVersionId && canResearch} canWrite={canResearch}
        onCreate={() => void newChat()}
        onQuery={setSessionQuery} onArchiveFilter={setShowArchived} onRead={cursor => void loadSessions(cursor)}
        onRename={renameSession} onArchive={(id, archived) => void archiveSession(id, archived)} />
      <div className="sidebar-tools"><button aria-label="能力目录" title="能力目录" onClick={openCapabilities} disabled={!capabilities}><Icon name="layers" /><span className="sidebar-tool-label">能力</span><Icon name="arrow-up-right" /></button>{configuredServices.has('data') && <button aria-label="数据库" title="数据库" onClick={() => openDatabase()} disabled={!project}><Icon name="folder" /><span className="sidebar-tool-label">数据库</span><Icon name="arrow-up-right" /></button>}{configuredServices.has('knowledge-next') && <button aria-label="知识库" title="知识库" onClick={() => openKnowledge()} disabled={!project}><Icon name="book" /><span className="sidebar-tool-label">知识库</span><Icon name="arrow-up-right" /></button>}</div>
    </aside>
    <main id="conversation-main" className="main-panel" tabIndex={-1} hidden={!!workspace.routeTab}>
      <header className={`topbar${compactConversation ? ' session-toolbar' : ''}`}>
        <div className="topbar-start">
          {!compactConversation && <>
            <span className="product-title" title={projectTitle}>{ui.brand.workspaceName}</span>
            <span className="environment-badge">项目环境</span>
            <span className="current-user">{capabilities?.currentUser.displayName ?? '正在验证身份'}</span>
            {signOut}
          </>}
        </div>
        {compactConversation && sessionContext}
        <div className="topbar-actions">
          {showWorkItems && !activeId && <a className="secondary work-items-entry" href={workItemHref()}>业务工作</a>}
          <button type="button" className="connection-state" data-testid="connection-status" aria-label={`服务状态 · ${connectionLabel}`}
            title={compactConversation ? projectTitle : connectionLabel} aria-haspopup="dialog" onClick={openReadiness}>
            <span className={`status-dot ${project && (!activeId || connected) ? 'online' : ''}`} aria-hidden="true" />
            <span className="connection-label">{connectionLabel}</span><span className="connection-compact" aria-hidden="true">状态</span>
          </button>
          {!activeId && researchWorkspace && <button type="button" ref={homeWorkbenchOpener} className="icon-button home-workbench-entry" aria-label={workspace.collapsed ? '打开任务工作台' : '收起任务工作台'}
            aria-controls="workspace-panel" aria-expanded={!workspace.collapsed}
            onClick={toggleWorkspace}><Icon name="layers" />工作台</button>}
          {compactConversation && signOut}
        </div>
      </header>
      {!compactConversation && sessionContext}
      {showWorkItems && activeId && <Suspense fallback={null}>
        <SessionWorkContext key={`${storageScope}/${activeId}/${returnWorkItem ?? ''}`} sessionId={activeId}
          project={project} subject={capabilities?.currentUser.subject ?? ''} openOnEntry={!!returnWorkItem}
          onOpenData={reference => openInputResource(reference.versionId, reference)} />
      </Suspense>}
      {activeHistory && connection === 'disconnected' && <div className="conversation-offline" role="status">
        <span>连接中断，显示最近快照 <time dateTime={activeHistory.snapshotAt}>{new Date(activeHistory.snapshotAt).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}</time></span>
        <button type="button" className="text-button" disabled={loading} onClick={() => void refresh(activeId)}>刷新会话</button>
      </div>}
      {!viewingWorkbench && receiptFeedback}
      {researchOverview && <TaskHeader task={activeTask} name={activeSummary?.title} onOpen={() => workspace.focus('tasks')}
        onSupplement={!taskReviewDisabled && !busy ? openPreparation : undefined}
        onConfirmation={snapshotCurrent && currentConfirmation ? focusCurrentConfirmation : undefined} />}
      <div className="chat-scroll" ref={chatScroll} data-testid="message-list" onScroll={event => { const node = event.currentTarget; atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; const card = node.querySelector('[data-task-card]');
        setReadingOlder(!!card && card.getBoundingClientRect().top >= node.getBoundingClientRect().bottom); if (atBottom.current) setNewMessages(false); }}>
        {historyReadFailure ? <div className="conversation">{historyReadFailure}</div> : !history?.items.length && !history?.controlMessages?.length && !activeTask && !currentTask && !loading ? <section className="welcome" aria-labelledby="welcome-title">
          <h1 id="welcome-title">{ui.brand.welcome}</h1>
          <details className="usage-guide"><summary>使用说明</summary><p>PRIVATE 资料仅 owner 和 admin 可读；PROJECT 资料可供项目成员共享。归档前请复核输入 checksum、参数、随机种子、模型实现和 lineage。</p></details>
          {capabilityError && <div className="connection-notice" role="alert"><strong>业务服务尚未连接</strong><p>{capabilityError.code === 'PRODUCT_UI_IDENTITY_MISMATCH' ? '产品配置与当前服务不一致，业务操作已停用。' : capabilityError.code === 'API_RESPONSE_INVALID' ? '当前仅提供前端预览，暂不能发送。' : '无法连接业务接口，暂不能发送。'}</p><button onClick={() => void connectProject()}>重试连接</button></div>}
          <section className="example-group" aria-label="常用任务">
            {(featuredCapabilities.length > 0 || featuredExamples.length > 0) && <>
              <h2>常用任务</h2>
              <div className="example-grid">
                {featuredCapabilities.map(({ ref, title, description }) => <button type="button" key={`${ref.id}@${ref.version}`} aria-label={`${title} ${description}`} aria-haspopup="dialog" onClick={() => openBusinessCapability(ref.id, ref.version)}>
                  <Icon name="layers" /><span className="example-copy"><strong>{title}</strong><small>{description}</small></span><Icon name="arrow-right" />
                </button>)}
                {featuredExamples.map(exampleButton)}
              </div>
            </>}
            {otherExamples.length > 0 && <details className="more-examples">
              <summary>更多场景<Icon name="arrow-down" /></summary><div className="example-grid">{otherExamples.map(exampleButton)}</div>
            </details>}
          </section>
        </section> : <div className="conversation">
          {history?.controlMessages?.map(message => <ControlProposalCard
            key={message.requestId} message={message}
            proposal={history.controlProposals?.find(item => item.sourceRequestId === message.requestId)}
            disabled={pending} onDecision={(id, action) => void controlDecision(id, action)} onResource={openResource}
          />)}
          {history?.nextCursor !== null && history?.nextCursor !== undefined && <button className="older-messages" disabled={loadingOlder} onClick={readOlderMessages}><Icon name="arrow-up" />{loadingOlder ? '正在读取更早消息…' : '更早消息'}</button>}
          {history?.sessionId === activeId && <Activity mode={viewingWorkbench ? 'hidden' : 'visible'}>
            <TurnTimeline key={`${storageScope}/${activeId}`} history={history}
              task={researchOverview || !currentTask ? undefined : { ...currentTask, research: activeTask ?? undefined }} confirmationCard={viewingWorkbench ? null : confirmationCard}
              scope={`${storageScope}/${activeId}`} project={project} assistant={{ name: ui.brand.assistantName, image: mark }}
              connected={connected} currentConfirmationId={currentConfirmation?.confirmationId} busy={busy || !!submission || !snapshotCurrent || connection === 'disconnected'}
              canWrite={canResearch} verificationSession={verificationSession} preview={preview}
              drafts={workflowDrafts} draftReads={workflowDraftReads} retryDraft={retryWorkflowDraft}
              results={resultsRead} retryResults={() => void refresh(activeId)} onReplyReady={alignReadyReply}
              onJump={jumpToTurn} onRecover={id => void recoverWorkflow(id)}
              modelChoices={capabilities?.models.filter(item => modelIdentity(item)).map(item => ({
                label: modelName(item.modelId), isMap: ui.models[item.modelId]?.resultOverview?.insight?.kind === 'map', prepare: () => chooseWorkflow(item),
              }))}
              onChoose={choice => void send(undefined, choice)} onResource={openResource}
              onInputResource={openInputResource} onKnowledge={showKnowledge} knowledgeAvailable={configuredServices.has('knowledge-next')}
              onAttach={resource => attach(resultItem(resource))}
              continueDisabled={pending || !canResearch} continuationVersionId={continuationVersionId}
              onContinue={resource => void newChatWithAttachments([resultItem(resource)], '请基于已附加成果继续分析。')}
              onPrepareContinuation={canResearch ? prepareContinuation : undefined}
              onCompare={resource => { rememberDetailOrigin(); openDatabase({ id: resource.versionId, reference: resource, compare: true }); }}
            />
          </Activity>}
          {loading && <Loading>正在恢复对话…</Loading>}<div ref={bottom} />
        </div>}
      </div>
      <div className="composer-region">
        <div className="composer-auxiliary">
          {readingOlder && currentTask && liveTurn ? <button type="button" className="new-messages" onClick={() => {
            const card = document.getElementById(`turn-${liveTurn.goalRevision}`)?.querySelector<HTMLElement>('[data-task-card]');
            if (card) { card.tabIndex = -1; card.focus(); card.scrollIntoView({ block: 'start' }); }
          }}>回到当前任务<Icon name="arrow-down" /></button> : newMessages && <button className="new-messages" onClick={() => { atBottom.current = true; setNewMessages(false); bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }}>查看最新消息<Icon name="arrow-down" /></button>}
          {!viewingWorkbench && operationFeedback}
          {uploadConfigError && <div className="error-banner"><ErrorNotice error={uploadConfigError} title="上传配置不可用" /><button disabled={preparingUpload} onClick={() => { if (uploadCandidate.current) void prepareUpload(uploadCandidate.current); }}>重试读取上传配置</button><button className="icon-button" aria-label="关闭上传提示" onClick={() => setUploadConfigError(null)}><Icon name="close" /></button></div>}
          {!viewingWorkbench && submissionFeedback}
          <MessageQueue items={queue} onChange={changeQueue} />
          {activeId && (runtimeBusy || awaitingConfirmation) && !loading && <div className="run-controls">
            {lastTurn && <small>第 {lastTurn.goalRevision} 轮</small>}
            <button disabled={stopping || pending} onClick={() => void stopRun()}>{stopAcknowledged ? '等待停止结果' : stopping ? '正在请求停止…' : awaitingConfirmation && !runtimeBusy ? '撤回本轮待确认请求' : '停止当前处理'}</button>
            {(runtimeBusy || draft.trim()) && <button disabled={stopping || pending || !draft.trim()} onClick={() => lastTurn?.status === 'WAITING_EXECUTION' ? enqueue(lastTurn.agentRunId) : void stopRun(true)}>{lastTurn?.status === 'WAITING_EXECUTION' ? '将纠正排为后续消息' : '停止后按输入内容纠正'}</button>}
          </div>}
        </div>
        <div className="composer" data-testid="composer">
          {(workspace.routeTab !== 'inputs' && workflowPreparation || undoTemplate || attachments.length > 0) &&
            <div className="composer-context">
              {workspace.routeTab !== 'inputs' && workflowPreparation}
              {undoTemplate && <div className="template-feedback" role="status"><span>已填入“{undoTemplate.title}”，可修改后发送。</span><button className="text-button" onClick={() => { updateDraft(undoTemplate.before); if (undoTemplate.previousWorkflow !== undefined) setWorkflow(undoTemplate.previousWorkflow); setTemplateChange(null); composer.current?.focus(); }}>撤销填入</button></div>}
              <PendingAttachments items={attachments} onOpen={openInputResource}
                onRemove={item => setAttachments(previous => previous.filter(value => workflowFileKey(value) !== workflowFileKey(item)))} />
            </div>}
        <MessageComposer key={activeId} inputRef={composer} value={draft} selection={false} busy={busy}
            submitting={pending && pendingLabel === '正在提交消息'}
            canSend={canSend && canResearch && (!workflow || !!selectedModel)}
            feedback={capabilityError && history?.items.length ? '业务服务尚未连接，暂不能发送。'
              : composerFeedback ? `${composerFeedback} · 草稿会保留` : capabilities && !canResearch ? '当前身份只有查看权限' : undefined}
            announceFeedback={pending || Boolean(capabilityError && history?.items.length)}
            unavailableReason={capabilityError ? '业务服务尚未连接，暂不能发送。'
              : !capabilities ? '正在验证身份与连接项目' : !canResearch ? '当前身份只有查看权限'
                : !project ? '正在连接项目' : submission ? '请先确认上一条消息的发送结果'
                  : composerFeedback ? `${composerFeedback} · 草稿会保留` : undefined}
            onChange={value => { setTemplateChange(null); updateDraft(value); }} onSend={() => void send()} tools={<>
              {activeId && busy && <button disabled={!canResearch || (!draft.trim() && !attachments.length && !workflow)} onClick={() => enqueue()}>稍后发送</button>}
              {canResearch && !!capabilities?.models.length && <button type="button" className="composer-workflow-button" aria-haspopup="dialog" onClick={openCapabilities}><Icon name="layers" />工作流</button>}
              {configuredServices.has('data') && <><button ref={attachButton} className="icon-button attach-button" aria-label="添加附件" title={preparingUpload ? '正在读取上传要求' : '上传资料'} disabled={!canResearch || !project || pending || !!file || preparingUpload} onClick={() => fileInput.current?.click()}><Icon name="plus" /></button>{preparingUpload && <span className="upload-preparing">正在读取上传要求…</span>}
              <input ref={fileInput} type="file" hidden onChange={event => { const selected = event.target.files?.[0]; if (selected) void prepareUpload(selected); event.target.value = ''; }} /></>}
            </>} />
        </div>
        <div className="composer-caption">{debugEnabled && <details className="debug-panel" onToggle={event => { if (event.currentTarget.open) void loadTrace(); }}><summary>调试信息</summary><div className="debug-content"><h3>请求、事件与交互延迟</h3><p>不展示提示词、工具正文或模型服务商配置。</p><pre>{debug.map(entry => `${entry.time} ${entry.kind} ${entry.name} ${entry.status ?? entry.eventId ?? ''}${entry.code ? ` code=${entry.code}` : ''}${entry.elapsedMs !== undefined ? ` ${entry.elapsedMs}ms` : ''}${entry.requestId ? ` request=${entry.requestId}` : ''}`).join('\n')}</pre><RoutingDebug turns={history?.turns ?? []} />{trace !== null && <details><summary>当前运行追踪元数据</summary><pre>{JSON.stringify(trace, null, 2)}</pre></details>}</div></details>}</div>
      </div>
    </main>
    {historyPanelOpen && !workspace.routeTab && <ProjectTaskHistory key={historyPanelKey} project={project} obscured={!!drawer || !!file}
      onResource={openInputResource} onClose={() => { setHistoryPanelKey(''); historyButton.current?.focus(); }} />}
    {deferHomeWorkspace && <div id="workspace-panel" data-testid="workspace-panel" className="is-collapsed" hidden />}
    {(!activeId && !deferHomeWorkspace || viewingWorkbench || (compactConversation && !historyPanelOpen)) && <Suspense fallback={<p id="workspace-panel" className="muted workspace-loading" role="status">正在加载工作台…</p>}><WorkspacePanel history={activeHistory} results={activeHistory ? results : []} resultsRead={activeHistory ? resultsRead : { status: 'not-requested' }} activeId={activeId} storageScope={storageScope} project={project} capabilities={capabilities} dataAvailable={configuredServices.has('data')} collapsed={workspace.collapsed} pendingInputs={attachments} selectedResourceId={workspace.resourceId} selectedResourceReference={workspace.resourceReference} editingConfirmationId={workspace.editingConfirmationId} confirmationFocus={workspace.confirmationFocus} focusRequest={workspace.focusRequest} busy={busy} onToggle={toggleWorkspace} onJump={jumpToTurn} onCurrentConfirmation={focusCurrentConfirmation} routedTab={workspace.routeTab} onRouteTab={workspace.focus}
      hideWhenCollapsed={hideCollapsedHomeWorkspace}
      feedback={<>{receiptFeedback}{workspace.routeTab && historyReadFailure}{operationFeedback}{submissionFeedback}</>}
      inputEditor={workspace.routeTab === 'inputs' ? workflowPreparation : null}
      onChooseMethod={workspace.routeTab === 'inputs' && canResearch && capabilities?.models.length ? openCapabilities : undefined}
      confirmationReview={workspace.routeTab === 'parameters' ? confirmationCard : null}
      draft={scopedDraft}
      draftRead={draftRead?.status === 'error' ? { error: draftRead.error, retry: () => { if (draftId) retryWorkflowDraft(draftId); } } : draftRead?.status}
      onDraftKnowledge={showKnowledge}
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
      onOpenDatabase={(formats, input) => {
        if (input && workflow?.target.modelId === input.modelId && workflow.target.modelVersion === input.modelVersion
          && ui.models[input.modelId]?.inputs[input.name]) pickWorkflowInput(input.name, formats ?? []);
        else openDatabase({ formats });
      }} onRemovePending={reference => setAttachments(previous => previous.filter(item => workflowFileKey(item) !== workflowFileKey(reference)))} onEdit={id => workspace.editConfirmation(id)} onResolveParameter={resolvePreparationParameter} onEditClose={workspace.closeConfirmation} onRevision={(confirmation, actions) => void reviseConfirmation(confirmation, actions)} onRecover={id => void recoverWorkflow(id)} /></Suspense>}
    <button className="drawer-shade" type="button" tabIndex={-1} aria-hidden="true" data-overlay-dismiss
      data-open={drawerShadeVisible} disabled={!drawerShadeVisible} onClick={closeDrawer} />
    {drawer?.kind === 'database' && <Suspense fallback={<p className="muted drawer-loading">正在加载数据库…</p>}><DatabaseWorkbench key={`database:${drawer.id ?? ''}:${drawer.reference?.semanticId ?? ''}:${drawer.reference?.objectId ?? ''}:${drawer.compare ? 'compare' : ''}:${drawer.formats?.join(',') ?? ''}`}
      project={project} initialId={drawer.id} reference={drawer.reference} compare={drawer.compare} allowedFormats={drawer.formats}
      inputRole={activeBinding ? ui.models[activeBinding.modelId]?.inputs[activeBinding.name]?.label : undefined}
      onClose={() => { setBindingTarget(undefined); closeDrawer(); }} onAttach={canResearch ? attach : undefined} /></Suspense>}
    {drawer?.kind === 'knowledge' && <Suspense fallback={<p className="muted drawer-loading">正在加载知识库…</p>}><KnowledgeDrawer key={`knowledge:${drawer.reference?.versionId ?? ''}:${drawer.reference?.unitId ?? ''}`} project={project} reference={drawer.reference} onClose={closeDrawer} /></Suspense>}
    {drawer?.kind === 'capabilities' && capabilities && <Suspense fallback={<p className="muted drawer-loading">正在加载能力目录…</p>}>
      <CapabilityDrawer capabilities={capabilities} businessCapabilities={businessCapabilities} examples={examples}
        onBusinessCapability={openBusinessCapability} onWorkflow={canResearch ? chooseWorkflow : undefined}
        onExample={prompt => fillFromCapabilities(prompt, examples.find(item => item.prompt === prompt)?.title ?? '推荐问题')} onClose={closeDrawer} />
    </Suspense>}
    {businessDetail && <Suspense fallback={<p className="muted drawer-loading">正在加载业务场景…</p>}>
      <BusinessCapabilityDrawer capability={businessDetail} onClose={closeDrawer}
        onUse={canResearch && businessDetail.tier !== 'EXPERIMENTAL' && businessDetail.validationStatus === 'TECHNICALLY_VALIDATED'
          ? () => fillBusinessCapability(businessDetail) : undefined} />
    </Suspense>}
    {drawer?.kind === 'readiness' && <Suspense fallback={<p className="muted drawer-loading">正在加载服务状态…</p>}><ReadinessDrawer services={capabilities?.services} onClose={closeDrawer} /></Suspense>}
    {file && uploadLimitBytes && <UploadDialog file={file} project={project} storageScope={storageScope} maxBytes={uploadLimitBytes}
      onClose={() => { setFile(null); requestAnimationFrame(() => attachButton.current?.focus({ preventScroll: true })); }} onUploaded={attach} />}
  </div>;
}
