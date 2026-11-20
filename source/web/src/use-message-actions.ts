import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { canConfirm, isBusy } from './chat-state';
import { inputReferences, isResourceItems, messageBody, newRequestId, savedSubmission, savedSubmissionForKey, saveSubmission, type SavedSubmission } from './message-routing';
import { readJsonCache } from './browser-cache';
import { readQueue, saveQueue, type QueuedMessage } from './task-state';
import { saveWorkflowSelection, storedWorkflowSelection, workflowFileKey, workflowSelectionBody, type WorkflowSelection } from './workflow-selection';
import type { Confirmation, ConfirmationRevisionAction, History, ResourceItem, Session } from './types';
import type { SessionStreamRefs } from './use-session-stream';
import { scopedStorageKey } from './browser-scope';
import { currentSessionId } from './use-project-sessions';

const naturalControls = new Set(['批准', '同意', '确认执行', '批准执行', '拒绝', '不同意', '拒绝执行', '停止', '停下', '停止分析', '停止本次分析', '取消', '取消执行', '取消计算', '状态', '查看状态', '当前状态', '查询状态', '进度怎么样', '进度怎么样？', '现在怎么样了', '现在怎么样了？']);
const statusQueries = new Set(['状态', '查看状态', '当前状态', '查询状态', '进度怎么样', '进度怎么样？', '现在怎么样了', '现在怎么样了？']);
export function isNaturalControl(message: string, hasAttachments = false) { return !hasAttachments && naturalControls.has(message.trim()); }
function isStatusQuery(message: string, hasAttachments = false) { return !hasAttachments && statusQueries.has(message.trim()); }
function queuedRequest(item: QueuedMessage, requestId: string) {
  return {
    requestId, message: item.text || (item.workflow ? '请准备所选工作流。' : '请处理已附加的数据。'),
    requestedMode: item.workflow ? 'DIALOGUE' as const : 'AUTO' as const,
    ...(item.workflow && { workflowSelection: workflowSelectionBody(item.workflow, item.attachments) }),
    ...(item.attachments.length && { inputReferences: inputReferences(item.attachments) }),
    ...(item.correctsAgentRunId && { correctsAgentRunId: item.correctsAgentRunId }),
  };
}
function savedAttachments(scope: string, id: string): ResourceItem[] {
  return readJsonCache(scopedStorageKey(scope, 'draft-attachments', id), isResourceItems) ?? [];
}

type ConfirmationMutation = {
  scope: string;
  sessionId: string;
  goalRevision: number;
  label: string;
  send: () => Promise<unknown>;
} & (
  { kind: 'confirm' | 'reject'; confirmationId: string } |
  { kind: 'revision'; confirmationId: string; requestId: string } |
  { kind: 'recovery'; requestId: string }
);

function reconcileConfirmation(mutation: ConfirmationMutation, history: History) {
  const confirmations = history.turns.flatMap(turn => turn.confirmations);
  if ('requestId' in mutation && confirmations.some(item => item.requestId === mutation.requestId)) return 'applied';
  const original = 'confirmationId' in mutation ? confirmations.find(item => item.confirmationId === mutation.confirmationId) : undefined;
  if (mutation.kind === 'confirm' && original?.status === 'CONFIRMED') return 'applied';
  if (mutation.kind === 'reject' && original?.status === 'REJECTED') return 'applied';
  if (history.goalRevision !== mutation.goalRevision) return 'stale';
  if (original?.decisionState) return 'checking';
  if (mutation.kind === 'recovery') {
    const current = history.turns.find(turn => turn.goalRevision === history.goalRevision);
    return current?.confirmations.at(-1)?.status === 'PENDING' ? 'stale' : 'retry';
  }
  return original && canConfirm(original, history, false) ? 'retry' : 'stale';
}

type MessageActionOptions = {
  activeId: string;
  storageScope: string;
  project: string;
  subject: string;
  history: History | null;
  connected: boolean;
  loading: boolean;
  externalError: ApiError | null;
  clearExternalError: () => void;
  loadSessions: (before?: number | null) => Promise<void>;
  refresh: (id: string) => Promise<void>;
  refs: SessionStreamRefs;
  closeConfirmationEditor: () => void;
};

type StopRequest = {
  sessionId: string;
  scope: string;
  generation: number;
  agentRunId: string;
  requestId: string;
};

export function useMessageActions({
  activeId,
  storageScope,
  project,
  subject,
  history,
  connected,
  loading,
  externalError,
  clearExternalError,
  loadSessions,
  refresh,
  refs,
  closeConfirmationEditor,
}: MessageActionOptions) {
  const { activeRef, viewGeneration, interactionTiming } = refs;
  const [queue, setQueue] = useState<QueuedMessage[]>(() => storageScope ? readQueue(storageScope, activeId) : []);
  const queueSending = useRef(false);
  const [draft, setDraft] = useState(() => storageScope ? sessionStorage.getItem(scopedStorageKey(storageScope, 'draft', activeId)) ?? '' : '');
  const [selection, setSelection] = useState<{ scope: string; session: string; value: WorkflowSelection | null }>();
  const workflow = selection?.scope === storageScope && selection.session === activeId
    ? selection.value : (storageScope ? storedWorkflowSelection(storageScope, activeId) : null);
  function setWorkflow(value: WorkflowSelection | null) {
    if (!storageScope) return;
    saveWorkflowSelection(storageScope, activeId, value);
    setSelection({ scope: storageScope, session: activeId, value });
  }
  const [attachments, setAttachments] = useState<ResourceItem[]>(() => storageScope ? savedAttachments(storageScope, activeId) : []);
  const [submission, setSubmission] = useState(() => storageScope ? savedSubmission(storageScope, activeId) : null);
  const [pendingOperation, setPendingOperation] = useState<{ id: number; sessionId: string; generation: number; label: string } | null>(null);
  const [continuationVersionId, setContinuationVersionId] = useState<string | null>(null);
  const sessionCreationPending = useRef(false);
  const [uncertain, setUncertain] = useState<{ mutation: ConfirmationMutation; before: History | null } | null>(null);
  const currentUncertain = uncertain?.mutation.scope === storageScope && uncertain.mutation.sessionId === activeId ? uncertain : null;
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const draftRef = useRef(draft);
  const previousStorageScope = useRef(storageScope);
  const activeStorageScope = useRef(storageScope);
  activeStorageScope.current = storageScope;
  const resultCheck = useRef<AbortController | null>(null);
  useLayoutEffect(() => () => {
    resultCheck.current?.abort();
    resultCheck.current = null;
  }, [activeId, storageScope]);
  const skipAttachmentPersist = useRef(false);
  draftRef.current = draft;
  const mutationSerial = useRef(0);
  const optionSubmission = useRef(false);
  const confirmationMutation = useRef<number | null>(null);
  const stopMutation = useRef<StopRequest | null>(null);
  const [pendingStop, setPendingStop] = useState<StopRequest | null>(null);
  const [stopError, setStopError] = useState<{ request: StopRequest; error: ApiError } | null>(null);
  const pending = pendingOperation?.sessionId === activeId && pendingOperation.generation >= viewGeneration.current;
  const pendingLabel = pendingOperation?.label ?? '正在提交';
  const runtimeBusy = isBusy(history);
  const pendingControl = history?.controlProposals?.some(item => ['PENDING', 'CONFIRMED'].includes(item.status)) ?? false;
  const busy = pending || runtimeBusy || pendingControl || loading || !!currentUncertain;
  const lastTurn = history?.turns.find(turn => turn.goalRevision === history.goalRevision);
  const stopControls = history?.sessionId === activeId ? history.controlRequests : undefined;
  const stopping = (pendingStop?.sessionId === activeId && pendingStop.scope === storageScope
    && pendingStop.generation === viewGeneration.current && pendingStop.agentRunId === lastTurn?.agentRunId)
    || (stopControls?.some(item => item.agentRunId === lastTurn?.agentRunId && item.status !== 'COMPLETE') ?? false);
  const error = actionError ?? externalError;

  useEffect(() => {
    if (!stopError || stopError.request.sessionId !== activeId || stopError.request.scope !== storageScope) return;
    const { request, error } = stopError;
    if (!stopControls?.some(item => item.agentRunId === request.agentRunId && item.requestId === request.requestId)) return;
    setActionError(previous => previous === error ? null : previous);
    setStopError(null);
  }, [activeId, storageScope, stopControls, stopError]);

  function showError(caught: unknown) {
    if (!(caught instanceof ApiError)) throw caught;
    setActionError(caught);
  }

  useEffect(() => {
    const firstIdentityBinding = !previousStorageScope.current && !!storageScope;
    previousStorageScope.current = storageScope;
    if (!storageScope) return;
    // The dependency change renders once with the previous session's attachment
    // state. Do not persist that stale state under the newly selected identity.
    skipAttachmentPersist.current = true;
    if (firstIdentityBinding) {
      const persistedDraft = sessionStorage.getItem(scopedStorageKey(storageScope, 'draft', activeId)) ?? '';
      if (draftRef.current) sessionStorage.setItem(scopedStorageKey(storageScope, 'draft', activeId), draftRef.current);
      else { setDraft(persistedDraft); draftRef.current = persistedDraft; }
      const persistedAttachments = savedAttachments(storageScope, activeId);
      if (attachments.length) sessionStorage.setItem(scopedStorageKey(storageScope, 'draft-attachments', activeId), JSON.stringify(attachments));
      else setAttachments(persistedAttachments);
      setQueue(readQueue(storageScope, activeId));
      setSubmission(savedSubmission(storageScope, activeId));
      return;
    }
    setQueue(readQueue(storageScope, activeId));
    queueSending.current = false;
    setDraft(sessionStorage.getItem(scopedStorageKey(storageScope, 'draft', activeId)) ?? '');
    setAttachments(savedAttachments(storageScope, activeId));
    setSubmission(savedSubmission(storageScope, activeId));
    setActionError(null);
  }, [activeId, storageScope]);

  useEffect(() => {
    if (skipAttachmentPersist.current) { skipAttachmentPersist.current = false; return; }
    if (storageScope) sessionStorage.setItem(scopedStorageKey(storageScope, 'draft-attachments', activeId), JSON.stringify(attachments));
  }, [activeId, attachments, storageScope]);
  useEffect(() => {
    if (submission && history?.turns.some(turn => turn.requestId === submission.body.requestId)) acceptSubmission(activeId, submission);
  }, [history, submission, activeId]);
  useEffect(() => {
    if (submission) return;
    const sent = savedSubmissionForKey(storageScope, 'routing-draft', activeId);
    if (sent === null) return;
    const turn = history?.turns.find(item => item.requestId === sent.body.requestId);
    if (!turn || !['FAILED', 'DECIDED', 'START_REQUESTED'].includes(turn.routingStatus ?? '')) return;
    if (turn.routingStatus === 'FAILED' && !draftRef.current && sent.draftText !== null) {
      updateDraft(sent.draftText); setAttachments(sent.attachments);
    }
    sessionStorage.removeItem(scopedStorageKey(storageScope, 'routing-draft', activeId));
  }, [history, submission, activeId, storageScope]);

  function acceptSubmission(id: string, sent: SavedSubmission, updateView = true) {
    const sameSession = activeStorageScope.current === storageScope && activeRef.current === id;
    if (sent.queueItemId) {
      const remaining = readQueue(storageScope, id).filter(item => item.id !== sent.queueItemId);
      saveQueue(storageScope, id, remaining); if (sameSession) { setQueue(remaining); queueSending.current = false; }
    }
    saveSubmission(storageScope, id, null);
    const retained = sent.draftText !== null && sent.body.workflowSelection ? new Set(
      Object.values(sent.body.workflowSelection.inputBindings ?? {}).map(index => sent.attachments[index]).filter(Boolean).map(workflowFileKey),
    ) : new Set<string>();
    const remaining = (item: ResourceItem) => retained.has(workflowFileKey(item))
      || !sent.attachments.some(resource => workflowFileKey(resource) === workflowFileKey(item));
    if (sent.body.requestedMode === 'AUTO' && sent.draftText !== null) sessionStorage.setItem(scopedStorageKey(storageScope, 'routing-draft', id), JSON.stringify(sent));
    if (sent.draftText !== null) {
      if (sessionStorage.getItem(scopedStorageKey(storageScope, 'draft', id)) === sent.draftText) sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft', id));
    }
    sessionStorage.setItem(scopedStorageKey(storageScope, 'draft-attachments', id), JSON.stringify(savedAttachments(storageScope, id).filter(remaining)));
    if (!sameSession) return;
    setSubmission(previous => previous?.body.requestId === sent.body.requestId ? null : previous);
    if (!updateView) return;
    if (sent.draftText !== null && draftRef.current === sent.draftText) {
      setDraft(''); sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft', id));
    }
    setAttachments(previous => previous.filter(remaining));
  }
  function changeQueue(items: QueuedMessage[]) { saveQueue(storageScope, activeId, items); setQueue(items); }
  function retryQueuedResult(id: string) {
    changeQueue(queue.map(item => item.id === id ? { ...item, resultCheckBlocked: undefined } : item));
    setActionError(error => error?.code === 'WORK_ITEM_RESULT_REUSE_UNAVAILABLE' ? null : error);
  }
  function enqueue(correctsAgentRunId?: string) {
    if (!activeId || (!draft.trim() && !attachments.length && !workflow)) return;
    const item: QueuedMessage = { id: newRequestId(), text: draft, attachments, createdAt: new Date().toISOString(),
      ...(workflow && { workflow }), ...(correctsAgentRunId ? { correctsAgentRunId } : {}) };
    changeQueue([...queue, item]); updateDraft(''); setAttachments([]); setWorkflow(null);
  }
  async function stopRun(correct = false) {
    if (!lastTurn || history?.sessionId !== activeId || stopping) return;
    const generation = viewGeneration.current;
    const { agentRunId } = lastTurn;
    if (stopMutation.current?.generation === generation && stopMutation.current.agentRunId === agentRunId) return;
    const current = () => activeRef.current === activeId && viewGeneration.current === generation;
    const key = scopedStorageKey(storageScope, 'stop-request', `${activeId}:${agentRunId}`);
    const requestId = localStorage.getItem(key) ?? newRequestId();
    localStorage.setItem(key, requestId);
    const request = { sessionId: activeId, scope: storageScope, generation, agentRunId, requestId };
    stopMutation.current = request;
    setPendingStop(request); setActionError(null); setStopError(null);
    if (correct) enqueue(agentRunId);
    try {
      try { await callApi('kernel_stop_run', `/sessions/${activeId}/stop`, { requestId, agentRunId }); }
      catch (caught) {
        if (current() && stopMutation.current === request) {
          if (!(caught instanceof ApiError)) throw caught;
          setActionError(caught);
          setStopError({ request, error: caught });
        }
      }
      if (current()) await refresh(activeId);
    } finally {
      if (stopMutation.current === request) stopMutation.current = null;
      setPendingStop(previous => previous === request ? null : previous);
    }
  }
  const awaitingConfirmation = lastTurn?.confirmations.some(item => item.status === 'PENDING') ?? false;
  useEffect(() => {
    if (!activeId || busy || stopping || awaitingConfirmation || submission || error || !connected || !queue.length || queueSending.current) return;
    const head = queue[0];
    if (head.resultCheckBlocked || (!head.text.trim() && !head.attachments.length)) return;
    // A persisted ID means the previous POST may have reached Kernel. Recover the
    // receipt first and offer only the existing request for an explicit retry.
    if (head.requestId) {
      const sent: SavedSubmission = { queueItemId: head.id, body: queuedRequest(head, head.requestId), draftText: null, attachments: head.attachments };
      if (history?.turns.some(turn => turn.requestId === head.requestId)) acceptSubmission(activeId, sent);
      else { setSubmission(sent); saveSubmission(storageScope, activeId, sent); }
      return;
    }
    queueSending.current = true;
    void send({ queueItemId: head.id, body: queuedRequest(head, newRequestId()), draftText: null, attachments: head.attachments }, undefined, true);
  }, [activeId, busy, stopping, awaitingConfirmation, submission, error, connected, queue]);
  function updateDraft(value: string) { setDraft(value); if (storageScope) sessionStorage.setItem(scopedStorageKey(storageScope, 'draft', activeId), value); }
  function navigate(id: string) { window.location.hash = `/sessions/${id}`; }
  async function createSession(operation: number, initialDraft?: { text: string; attachments: ResourceItem[]; submission?: SavedSubmission; workflow?: WorkflowSelection }) {
    const generation = viewGeneration.current;
    const created = await callApi('kernel_create_session', '/sessions', { projectId: project }) as Session;
    if (viewGeneration.current !== generation || currentSessionId() !== activeId) {
      await loadSessions();
      return null;
    }
    if (initialDraft !== undefined) {
      sessionStorage.setItem(scopedStorageKey(storageScope, 'draft', created.sessionId), initialDraft.text);
      sessionStorage.setItem(scopedStorageKey(storageScope, 'draft-attachments', created.sessionId), JSON.stringify(initialDraft.attachments));
      if (initialDraft.workflow) saveWorkflowSelection(storageScope, created.sessionId, initialDraft.workflow);
      if (initialDraft.submission) saveSubmission(storageScope, created.sessionId, initialDraft.submission);
    }
    setPendingOperation(previous => previous?.id === operation ? { ...previous, sessionId: created.sessionId, generation: generation + 1 } : previous);
    navigate(created.sessionId); await loadSessions(); return created.sessionId;
  }
  async function newChat() {
    if (pending || sessionCreationPending.current || !project) return;
    sessionCreationPending.current = true;
    const operation = ++mutationSerial.current;
    const generation = viewGeneration.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation: viewGeneration.current, label: '正在创建对话' }); setActionError(null);
    try { await createSession(operation); } catch (caught) { if (operation === mutationSerial.current && generation === viewGeneration.current && activeRef.current === activeId) showError(caught); }
    finally { sessionCreationPending.current = false; setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function newChatWithAttachments(nextAttachments: ResourceItem[], text = '', workflow?: WorkflowSelection) {
    if (pending || sessionCreationPending.current || !project) return;
    sessionCreationPending.current = true;
    setContinuationVersionId(nextAttachments[0]?.versionId ?? null);
    const operation = ++mutationSerial.current;
    const generation = viewGeneration.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation: viewGeneration.current, label: '正在创建对话' }); setActionError(null);
    try { await createSession(operation, { text, attachments: nextAttachments, workflow }); }
    catch (caught) { if (operation === mutationSerial.current && generation === viewGeneration.current && activeRef.current === activeId) showError(caught); }
    finally { sessionCreationPending.current = false; setContinuationVersionId(null); setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function send(retry?: SavedSubmission, suggestedMessage?: string, firstQueuedAttempt = false) {
    if (suggestedMessage && optionSubmission.current) return;
    const selected = suggestedMessage ? null : workflow;
    const message = suggestedMessage ?? (draft.trim() || (selected ? '请准备所选工作流。' : attachments.length ? '请处理已附加的数据。' : ''));
    const controlAllowed = isNaturalControl(message, attachments.length > 0 || !!selected)
      && (!pendingControl || isStatusQuery(message, attachments.length > 0));
    if ((!retry && ((busy && !controlAllowed) || submission)) || pending || !project) return;
    if (!retry && !message) return;
    const sent: SavedSubmission = retry ?? {
      body: { ...messageBody(message),
        ...(selected && { requestedMode: 'DIALOGUE' as const, workflowSelection: workflowSelectionBody(selected, attachments) }),
        ...(!suggestedMessage && attachments.length ? { inputReferences: inputReferences(attachments) } : {}) },
      draftText: suggestedMessage ? null : draft, attachments: suggestedMessage ? [] : attachments,
    };
    const checksResult = sent.attachments.some(item => item.resultProvenance !== undefined);
    if (checksResult && resultCheck.current) return;
    const validation = checksResult ? new AbortController() : null;
    if (validation) resultCheck.current = validation;
    const startedAt = performance.now();
    interactionTiming.current = {
      requestId: sent.body.requestId, startedAt, lastFeedbackAt: startedAt,
      firstFeedbackAt: null, maxSilenceMs: 0, snapshotRequests: 0, modelRequests: 0,
    };
    const operation = ++mutationSerial.current;
    if (suggestedMessage) optionSubmission.current = true;
    const generation = viewGeneration.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation,
      label: validation ? '正在核对成果附件' : '正在提交消息' }); setActionError(null);
    let id = activeId;
    let migrated = false;
    let submitted = false;
    const current = () => activeStorageScope.current === storageScope && activeRef.current === id
      && viewGeneration.current === generation + (migrated ? 1 : 0);
    const currentResultCheck = () => current() && currentSessionId() === id && !validation?.signal.aborted;
    try {
      if (validation) {
        try {
          const { verifySubmittedWorkItemResults } = await import('./work-item-result-reuse');
          if (!currentResultCheck()) return;
          await verifySubmittedWorkItemResults(sent, id, { project, subject }, validation.signal);
        } catch (error) {
          if (!currentResultCheck()) return;
          if (firstQueuedAttempt) {
            const queued = readQueue(storageScope, id);
            const updated = queued.map(item => item.id === sent.queueItemId && !item.requestId
              ? { ...item, resultCheckBlocked: true as const } : item);
            changeQueue(updated);
          }
          showError(error);
          return;
        }
        if (!currentResultCheck() || (retry && !firstQueuedAttempt
          && savedSubmission(storageScope, id)?.body.requestId !== sent.body.requestId)) return;
        setPendingOperation(previous => previous?.id === operation ? { ...previous, label: '正在提交消息' } : previous);
      }
      if (firstQueuedAttempt) {
        const queued = readQueue(storageScope, id);
        const head = queued[0];
        if (!head || head.id !== sent.queueItemId || head.requestId
          || JSON.stringify(queuedRequest(head, sent.body.requestId)) !== JSON.stringify(sent.body)) return;
        changeQueue([{ ...head, requestId: sent.body.requestId }, ...queued.slice(1)]);
      }
      setSubmission(sent); saveSubmission(storageScope, id, sent);
      submitted = true;
      if (!id) {
        const created = await createSession(operation, { text: draft, attachments, submission: sent, workflow: selected ?? undefined });
        if (created === null) return;
        id = created; migrated = true;
        saveSubmission(storageScope, '', null);
        sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft'));
        sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft-attachments'));
        saveWorkflowSelection(storageScope, '', null);
      }
      await callApi('kernel_create_message', `/sessions/${id}/messages`, sent.body, validation?.signal);
      acceptSubmission(id, sent, current());
    } catch (caught) {
      if (validation?.signal.aborted) return;
      if (!(caught instanceof ApiError)) throw caught;
      if (!id || (caught.status >= 400 && caught.status < 500)) {
        saveSubmission(storageScope, id, null);
        if (current()) setSubmission(null);
      }
      if (current()) showError(caught);
    }
    finally {
      if (resultCheck.current === validation) resultCheck.current = null;
      if (firstQueuedAttempt && !submitted && current()) queueSending.current = false;
      if (id && current() && (submitted || (retry && !firstQueuedAttempt))) await refresh(id);
      if (submitted) await loadSessions();
      if (suggestedMessage) optionSubmission.current = false;
      setPendingOperation(previous => previous?.id === operation ? null : previous);
    }
  }
  const receiptState = currentUncertain && history?.sessionId === activeId && history !== currentUncertain.before
    ? reconcileConfirmation(currentUncertain.mutation, history) : 'checking';
  useEffect(() => {
    if (!currentUncertain) return;
    if (receiptState === 'applied') {
      setUncertain(null);
      if (currentUncertain.mutation.kind === 'revision' || currentUncertain.mutation.kind === 'recovery') closeConfirmationEditor();
    } else if (receiptState === 'stale') {
      setUncertain(null);
      setActionError(new ApiError(409, 'CONFIRMATION_NOT_CURRENT'));
    }
  }, [receiptState, currentUncertain, closeConfirmationEditor]);

  async function runConfirmationMutation(mutation: ConfirmationMutation) {
    const generation = viewGeneration.current;
    if (confirmationMutation.current === generation) return;
    confirmationMutation.current = generation;
    const operation = ++mutationSerial.current;
    const current = () => activeRef.current === mutation.sessionId && viewGeneration.current === generation;
    setPendingOperation({ id: operation, sessionId: mutation.sessionId, generation, label: mutation.label });
    setActionError(null);
    try {
      await mutation.send();
      if (current()) {
        setUncertain(null);
        await refresh(mutation.sessionId);
        if (current() && (mutation.kind === 'revision' || mutation.kind === 'recovery')) closeConfirmationEditor();
      }
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (current()) {
        // A gateway or server failure may follow a committed mutation.
        if (caught.status === 0 || caught.status >= 500 || (caught.status >= 200 && caught.status < 300)) {
          setUncertain({ mutation, before: history });
        } else {
          setUncertain(null);
          showError(caught);
        }
        await refresh(mutation.sessionId);
      }
    } finally {
      if (confirmationMutation.current === generation) confirmationMutation.current = null;
      setPendingOperation(previous => previous?.id === operation ? null : previous);
    }
  }

  async function decision(id: string, kind: 'confirm' | 'reject', reason?: string) {
    if (!history) return;
    await runConfirmationMutation({
      scope: storageScope, sessionId: activeId, goalRevision: history.goalRevision,
      kind, confirmationId: id, label: '正在提交确认决定',
      send: () => callApi(kind === 'reject' ? 'kernel_reject' : 'kernel_confirm', `/sessions/${activeId}/${kind}`, { confirmationId: id, ...(kind === 'reject' ? { reason } : {}) }),
    });
  }
  async function controlDecision(id: string, action: 'confirm' | 'dismiss') {
    const generation = viewGeneration.current;
    const operation = ++mutationSerial.current;
    const current = () => activeRef.current === activeId && viewGeneration.current === generation;
    setPendingOperation({ id: operation, sessionId: activeId, generation, label: '正在提交控制确认' }); setActionError(null);
    try { await callApi(action === 'confirm' ? 'kernel_confirm_control_proposal' : 'kernel_dismiss_control_proposal', `/sessions/${activeId}/control-proposals/${id}/${action}`, { requestId: newRequestId() }); }
    catch (caught) { if (current()) showError(caught); }
    finally { if (current()) await refresh(activeId); setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function reviseConfirmation(confirmation: Confirmation, actions: ConfirmationRevisionAction[]) {
    if (!history) return;
    const body = { requestId: newRequestId(), expectedGoalRevision: history.goalRevision, actions };
    await runConfirmationMutation({
      scope: storageScope, sessionId: activeId, goalRevision: history.goalRevision, kind: 'revision',
      confirmationId: confirmation.confirmationId, requestId: body.requestId, label: '正在保存确认修订',
      send: () => callApi('kernel_revise_confirmation', `/sessions/${activeId}/confirmations/${confirmation.confirmationId}/revisions`, body),
    });
  }
  async function recoverWorkflow(workflowRunId: string) {
    if (!history) return;
    const body = { requestId: newRequestId(), expectedGoalRevision: history.goalRevision };
    await runConfirmationMutation({
      scope: storageScope, sessionId: activeId, goalRevision: history.goalRevision, kind: 'recovery',
      requestId: body.requestId, label: '正在创建恢复批次',
      send: () => callApi('kernel_recover_workflow', `/sessions/${activeId}/workflow-runs/${workflowRunId}/recoveries`, body),
    });
  }

  function clearError() {
    setActionError(null);
    clearExternalError();
  }

  const canSend = !pending && (!busy || (isNaturalControl(draft, attachments.length > 0 || !!workflow) && (!pendingControl || isStatusQuery(draft, attachments.length > 0))))
    && !submission && !!project && (!!draft.trim() || !!attachments.length || !!workflow);

  return {
    workflow,
    setWorkflow,
    queue,
    changeQueue,
    retryQueuedResult,
    enqueue,
    draft,
    updateDraft,
    attachments,
    setAttachments,
    submission,
    receiptPending: !!currentUncertain,
    canRetryReceipt: receiptState === 'retry',
    retryReceipt: () => {
      if (currentUncertain && receiptState === 'retry' && !pending) void runConfirmationMutation(currentUncertain.mutation);
    },
    pending,
    pendingLabel,
    continuationVersionId,
    runtimeBusy,
    pendingControl,
    busy,
    lastTurn,
    awaitingConfirmation,
    stopping,
    error,
    clearError,
    canSend,
    stopRun,
    newChat,
    newChatWithAttachments,
    send,
    decision,
    controlDecision,
    reviseConfirmation,
    recoverWorkflow,
  };
}
