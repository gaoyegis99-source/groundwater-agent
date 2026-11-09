import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { ApiError, callApi } from './api';
import { isBusy } from './chat-state';
import { inputReferences, isResourceItems, messageBody, newRequestId, savedSubmission, savedSubmissionForKey, saveSubmission, type SavedSubmission } from './message-routing';
import { readJsonCache } from './browser-cache';
import { readQueue, saveQueue, type QueuedMessage } from './task-state';
import type { Confirmation, History, ResourceItem, Session } from './types';
import type { SessionStreamRefs } from './use-session-stream';
import { scopedStorageKey } from './browser-scope';

const naturalControls = new Set(['批准', '同意', '确认执行', '批准执行', '拒绝', '不同意', '拒绝执行', '停止', '停下', '停止分析', '停止本次分析', '取消', '取消执行', '取消计算', '状态', '查看状态', '当前状态', '查询状态', '进度怎么样', '进度怎么样？', '现在怎么样了', '现在怎么样了？']);
const statusQueries = new Set(['状态', '查看状态', '当前状态', '查询状态', '进度怎么样', '进度怎么样？', '现在怎么样了', '现在怎么样了？']);
export function isNaturalControl(message: string, hasAttachments = false) { return !hasAttachments && naturalControls.has(message.trim()); }
function isStatusQuery(message: string, hasAttachments = false) { return !hasAttachments && statusQueries.has(message.trim()); }
function savedAttachments(scope: string, id: string): ResourceItem[] {
  return readJsonCache(scopedStorageKey(scope, 'draft-attachments', id), isResourceItems) ?? [];
}

type MessageActionOptions = {
  activeId: string;
  storageScope: string;
  project: string;
  history: History | null;
  connected: boolean;
  loading: boolean;
  stopping: boolean;
  setStopping: Dispatch<SetStateAction<boolean>>;
  externalError: ApiError | null;
  clearExternalError: () => void;
  loadSessions: (before?: number | null) => Promise<void>;
  refresh: (id: string) => Promise<void>;
  refs: SessionStreamRefs;
  closeConfirmationEditor: () => void;
};

export function useMessageActions({
  activeId,
  storageScope,
  project,
  history,
  connected,
  loading,
  stopping,
  setStopping,
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
  const [attachments, setAttachments] = useState<ResourceItem[]>(() => storageScope ? savedAttachments(storageScope, activeId) : []);
  const [submission, setSubmission] = useState(() => storageScope ? savedSubmission(storageScope, activeId) : null);
  const [pendingOperation, setPendingOperation] = useState<{ id: number; sessionId: string; generation: number; label: string } | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const draftRef = useRef(draft);
  const previousStorageScope = useRef(storageScope);
  const skipAttachmentPersist = useRef(false);
  draftRef.current = draft;
  const mutationSerial = useRef(0);
  const optionSubmission = useRef(false);
  const pending = pendingOperation?.sessionId === activeId && pendingOperation.generation >= viewGeneration.current;
  const pendingLabel = pendingOperation?.label ?? '正在提交';
  const runtimeBusy = isBusy(history);
  const pendingControl = history?.controlProposals?.some(item => ['PENDING', 'CONFIRMED'].includes(item.status)) ?? false;
  const busy = pending || runtimeBusy || pendingControl || loading;
  const lastTurn = history?.turns.find(turn => turn.goalRevision === history.goalRevision);
  const error = actionError ?? externalError;

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
    if (sent.queueItemId) {
      const remaining = readQueue(storageScope, id).filter(item => item.id !== sent.queueItemId);
      saveQueue(storageScope, id, remaining); if (activeRef.current === id) { setQueue(remaining); queueSending.current = false; }
    }
    saveSubmission(storageScope, id, null);
    if (sent.body.requestedMode === 'AUTO' && sent.draftText !== null) sessionStorage.setItem(scopedStorageKey(storageScope, 'routing-draft', id), JSON.stringify(sent));
    if (sent.draftText !== null) {
      if (sessionStorage.getItem(scopedStorageKey(storageScope, 'draft', id)) === sent.draftText) sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft', id));
    }
    sessionStorage.setItem(scopedStorageKey(storageScope, 'draft-attachments', id), JSON.stringify(savedAttachments(storageScope, id).filter(item => !sent.attachments.some(resource => resource.versionId === item.versionId))));
    if (activeRef.current !== id) return;
    setSubmission(previous => previous?.body.requestId === sent.body.requestId ? null : previous);
    if (!updateView) return;
    if (sent.draftText !== null && draftRef.current === sent.draftText) {
      setDraft(''); sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft', id));
    }
    setAttachments(previous => previous.filter(item => !sent.attachments.some(resource => resource.versionId === item.versionId)));
  }
  function changeQueue(items: QueuedMessage[]) { saveQueue(storageScope, activeId, items); setQueue(items); }
  function enqueue(correctsAgentRunId?: string) {
    if (!activeId || (!draft.trim() && !attachments.length)) return;
    const item: QueuedMessage = { id: newRequestId(), text: draft, attachments, createdAt: new Date().toISOString(), ...(correctsAgentRunId ? { correctsAgentRunId } : {}) };
    changeQueue([...queue, item]); updateDraft(''); setAttachments([]);
  }
  async function stopRun(correct = false) {
    if (!lastTurn || stopping) return;
    if (correct) enqueue(lastTurn.agentRunId);
    setStopping(true); setActionError(null);
    const key = scopedStorageKey(storageScope, 'stop-request', `${activeId}:${lastTurn.agentRunId}`);
    const requestId = localStorage.getItem(key) ?? newRequestId();
    localStorage.setItem(key, requestId);
    try { await callApi('kernel_stop_run', `/sessions/${activeId}/stop`, { requestId, agentRunId: lastTurn.agentRunId }); }
    catch (caught) { setStopping(false); showError(caught); }
    await refresh(activeId);
  }
  const awaitingConfirmation = lastTurn?.confirmations.some(item => item.status === 'PENDING') ?? false;
  useEffect(() => {
    if (!activeId || busy || stopping || awaitingConfirmation || submission || error || !connected || !queue.length || queueSending.current) return;
    const head = queue[0];
    if (!head.text.trim() && !head.attachments.length) return;
    // A persisted ID means the previous POST may have reached Kernel. Recover the
    // receipt first and offer only the existing request for an explicit retry.
    if (head.requestId) {
      const sent: SavedSubmission = { queueItemId: head.id, body: { requestId: head.requestId, message: head.text || '请处理已附加的数据。', requestedMode: 'AUTO', ...(head.attachments.length ? { inputReferences: inputReferences(head.attachments) } : {}), ...(head.correctsAgentRunId ? { correctsAgentRunId: head.correctsAgentRunId } : {}) }, draftText: null, attachments: head.attachments };
      if (history?.turns.some(turn => turn.requestId === head.requestId)) acceptSubmission(activeId, sent);
      else { setSubmission(sent); saveSubmission(storageScope, activeId, sent); }
      return;
    }
    queueSending.current = true;
    const item = { ...head, requestId: head.requestId ?? newRequestId() };
    const updated = [item, ...queue.slice(1)]; saveQueue(storageScope, activeId, updated); setQueue(updated);
    void send({ queueItemId: item.id, body: { requestId: item.requestId, message: item.text || '请处理已附加的数据。', requestedMode: 'AUTO', ...(item.attachments.length ? { inputReferences: inputReferences(item.attachments) } : {}), ...(item.correctsAgentRunId ? { correctsAgentRunId: item.correctsAgentRunId } : {}) }, draftText: null, attachments: item.attachments });
  }, [activeId, busy, stopping, awaitingConfirmation, submission, error, connected, queue]);
  function updateDraft(value: string) { setDraft(value); if (storageScope) sessionStorage.setItem(scopedStorageKey(storageScope, 'draft', activeId), value); }
  function navigate(id: string) { window.location.hash = `/sessions/${id}`; }
  async function createSession(operation: number, initialDraft?: { text: string; attachments: ResourceItem[]; submission?: SavedSubmission }) {
    const generation = viewGeneration.current;
    const created = await callApi('kernel_create_session', '/sessions', { projectId: project }) as Session;
    if (viewGeneration.current !== generation) return null;
    if (initialDraft !== undefined) {
      sessionStorage.setItem(scopedStorageKey(storageScope, 'draft', created.sessionId), initialDraft.text);
      sessionStorage.setItem(scopedStorageKey(storageScope, 'draft-attachments', created.sessionId), JSON.stringify(initialDraft.attachments));
      if (initialDraft.submission) saveSubmission(storageScope, created.sessionId, initialDraft.submission);
    }
    setPendingOperation(previous => previous?.id === operation ? { ...previous, sessionId: created.sessionId, generation: generation + 1 } : previous);
    navigate(created.sessionId); await loadSessions(); return created.sessionId;
  }
  async function newChat() {
    const operation = ++mutationSerial.current;
    const generation = viewGeneration.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation: viewGeneration.current, label: '正在创建对话' }); setActionError(null);
    try { await createSession(operation); } catch (caught) { if (operation === mutationSerial.current && generation === viewGeneration.current && activeRef.current === activeId) showError(caught); }
    finally { setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function newChatWithAttachments(nextAttachments: ResourceItem[], text = '') {
    const operation = ++mutationSerial.current;
    const generation = viewGeneration.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation: viewGeneration.current, label: '正在创建对话' }); setActionError(null);
    try { await createSession(operation, { text, attachments: nextAttachments }); }
    catch (caught) { if (operation === mutationSerial.current && generation === viewGeneration.current && activeRef.current === activeId) showError(caught); }
    finally { setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function send(retry?: SavedSubmission, suggestedMessage?: string) {
    if (suggestedMessage && optionSubmission.current) return;
    const message = suggestedMessage ?? (draft.trim() || (attachments.length ? '请处理已附加的数据。' : ''));
    const controlAllowed = isNaturalControl(message, attachments.length > 0)
      && (!pendingControl || isStatusQuery(message, attachments.length > 0));
    if ((!retry && ((busy && !controlAllowed) || submission)) || pending || !project) return;
    if (!retry && !message) return;
    const sent: SavedSubmission = retry ?? {
      body: { ...messageBody(message), ...(!suggestedMessage && attachments.length ? { inputReferences: inputReferences(attachments) } : {}) },
      draftText: suggestedMessage ? null : draft, attachments: suggestedMessage ? [] : attachments,
    };
    const startedAt = performance.now();
    interactionTiming.current = {
      requestId: sent.body.requestId, startedAt, lastFeedbackAt: startedAt,
      firstFeedbackAt: null, maxSilenceMs: 0, snapshotRequests: 0, modelRequests: 0,
    };
    const operation = ++mutationSerial.current;
    if (suggestedMessage) optionSubmission.current = true;
    const generation = viewGeneration.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation, label: '正在提交消息' }); setActionError(null);
    setSubmission(sent); saveSubmission(storageScope, activeId, sent);
    let id = activeId;
    let migrated = false;
    const current = () => activeRef.current === id && viewGeneration.current === generation + (migrated ? 1 : 0);
    try {
      if (!id) {
        const created = await createSession(operation, { text: draft, attachments, submission: sent });
        if (created === null) return;
        id = created; migrated = true;
        saveSubmission(storageScope, '', null);
        sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft'));
        sessionStorage.removeItem(scopedStorageKey(storageScope, 'draft-attachments'));
      }
      await callApi('kernel_create_message', `/sessions/${id}/messages`, sent.body);
      acceptSubmission(id, sent, current());
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (!id || (caught.status >= 400 && caught.status < 500)) {
        saveSubmission(storageScope, id, null);
        if (current()) setSubmission(null);
      }
      if (current()) showError(caught);
    }
    finally { if (id && current()) await refresh(id); await loadSessions(); if (suggestedMessage) optionSubmission.current = false; setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function decision(id: string, kind: 'confirm' | 'reject', reason?: string) {
    const generation = viewGeneration.current;
    const operation = ++mutationSerial.current;
    const current = () => activeRef.current === activeId && viewGeneration.current === generation;
    setPendingOperation({ id: operation, sessionId: activeId, generation, label: '正在提交确认决定' }); setActionError(null);
    try { await callApi(kind === 'reject' ? 'kernel_reject' : 'kernel_confirm', `/sessions/${activeId}/${kind}`, { confirmationId: id, ...(kind === 'reject' ? { reason } : {}) }); }
    catch (caught) { if (current()) showError(caught); }
    finally { if (current()) await refresh(activeId); setPendingOperation(previous => previous?.id === operation ? null : previous); }
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
  async function reviseConfirmation(confirmation: Confirmation, actions: { toolCallId: string; arguments: Record<string, unknown> }[]) {
    if (!history) return;
    const generation = viewGeneration.current; const operation = ++mutationSerial.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation, label: '正在保存确认修订' }); setActionError(null);
    try { await callApi('kernel_revise_confirmation', `/sessions/${activeId}/confirmations/${confirmation.confirmationId}/revisions`, { requestId: newRequestId(), expectedGoalRevision: history.goalRevision, actions }); closeConfirmationEditor(); await refresh(activeId); }
    catch (caught) { if (viewGeneration.current === generation) showError(caught); }
    finally { setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }
  async function recoverWorkflow(workflowRunId: string) {
    if (!history) return;
    const generation = viewGeneration.current; const operation = ++mutationSerial.current;
    setPendingOperation({ id: operation, sessionId: activeId, generation, label: '正在创建恢复批次' }); setActionError(null);
    try { await callApi('kernel_recover_workflow', `/sessions/${activeId}/workflow-runs/${workflowRunId}/recoveries`, { requestId: newRequestId(), expectedGoalRevision: history.goalRevision }); await refresh(activeId); }
    catch (caught) { if (viewGeneration.current === generation) showError(caught); }
    finally { setPendingOperation(previous => previous?.id === operation ? null : previous); }
  }

  function clearError() {
    setActionError(null);
    clearExternalError();
  }

  const canSend = (!busy || (isNaturalControl(draft, attachments.length > 0) && (!pendingControl || isStatusQuery(draft, attachments.length > 0))))
    && !submission && !!project && (!!draft.trim() || !!attachments.length);

  return {
    queue,
    changeQueue,
    enqueue,
    draft,
    updateDraft,
    attachments,
    setAttachments,
    submission,
    pending,
    pendingLabel,
    runtimeBusy,
    pendingControl,
    busy,
    lastTurn,
    awaitingConfirmation,
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
