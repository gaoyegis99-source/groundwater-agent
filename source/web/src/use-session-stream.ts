import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { allResults, ApiError, callApi, projectQuery } from './api';
import { prependHistory, refreshHistory } from './chat-state';
import { allDetails, applySnapshot, beginSnapshotRead, connectStream, detailsAfterEvent, forceStreamRefresh, hasDetails, initialStreamProgress, isMetadataEvent, mergeDetails, noDetails, receiveStreamEvent, type DetailScope } from './session-stream-state';
import type { DebugEntry, History, Result, WorkflowDraft } from './types';

const eventTypes = [
  'session.created', 'session.metadata_updated', 'turn.accepted', 'task.created', 'task.revised', 'task.completed', 'task.reopened', 'task.preparation_revised', 'control.requested', 'control.complete',
  'control.proposed', 'control.confirmed', 'control.dismissed', 'control.stale', 'control.failed',
  'workflow_draft.requested', 'workflow_draft.published',
  ...['requested', 'resolved', 'failed', 'corrected'].map(status => `intent_routing.${status}`),
  ...['running', 'waiting_confirmation', 'waiting_execution', 'complete', 'failed', 'superseded', 'cancelled'].map(status => `agent.${status}`),
  ...['pending', 'processing', 'confirmed', 'rejected', 'superseded'].map(status => `confirmation.${status}`),
  'confirmation.revised', 'confirmation.recovery_created', 'workflow.retry_queued', 'workflow.recovery_completed', 'workflow.recovery_failed',
  ...['queued', 'running', 'succeeded', 'failed', 'cancelled', 'timed_out'].map(status => `workflow.${status}`),
  ...['queued', 'running', 'succeeded', 'failed', 'cancelled', 'timed_out'].map(status => `job.${status}`),
  'agent.activity', 'agent.plan_recorded', 'workflow.stage_changed', 'workflow.communication_changed',
];

export type InteractionTiming = {
  requestId: string;
  startedAt: number;
  lastFeedbackAt: number;
  firstFeedbackAt: number | null;
  maxSilenceMs: number;
  snapshotRequests: number;
  modelRequests: number;
};

export type SessionStreamRefs = {
  activeRef: MutableRefObject<string>;
  viewGeneration: MutableRefObject<number>;
  interactionTiming: MutableRefObject<InteractionTiming | null>;
};

type DraftRead = { status: 'loading' | 'ready' } | { status: 'error'; error: ApiError };

export function useSessionStream(activeId: string, project: string, log: (entry: DebugEntry) => void) {
  const [preview, setPreview] = useState<{ agentRunId: string; text: string | null } | null>(null);
  const [stopping, setStopping] = useState(false);
  const [history, setHistory] = useState<History | null>(null);
  const [workflowDrafts, setWorkflowDrafts] = useState<Record<string, WorkflowDraft>>({});
  const [workflowDraftReads, setWorkflowDraftReads] = useState<Record<string, DraftRead>>({});
  const [results, setResults] = useState<Result[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const [trace, setTrace] = useState<unknown>(null);
  const activeRef = useRef(activeId);
  activeRef.current = activeId;
  const viewGeneration = useRef(0);
  const viewAbort = useRef<AbortController | null>(null);
  const refreshRequest = useRef<(() => Promise<void>) | null>(null);
  const olderRequest = useRef<((before: number) => Promise<void>) | null>(null);
  const draftRequest = useRef<((id: string) => void) | null>(null);
  const interactionTiming = useRef<InteractionTiming | null>(null);
  const connected = connection === 'connected';
  const lastTurn = history?.turns.find(turn => turn.goalRevision === history.goalRevision);

  const showError = useCallback((caught: unknown) => {
    if (!(caught instanceof ApiError)) throw caught;
    setError(caught);
  }, []);
  const refresh = useCallback(async (id: string) => {
    if (activeRef.current === id) await refreshRequest.current?.();
  }, []);

  useEffect(() => {
    const generation = ++viewGeneration.current;
    const controller = new AbortController();
    viewAbort.current = controller;
    setPreview(null); setStopping(false);
    setHistory(null); setResults([]); setWorkflowDrafts({}); setWorkflowDraftReads({}); setTrace(null); setError(null); setConnection('connecting'); setLoadingOlder(false);
    if (!activeId) { setLoading(false); return; }
    setLoading(true);
    let cancelled = false;
    let source: EventSource | undefined;
    const previewSource = new EventSource(`/api/v12/sessions/${activeId}/preview`);
    let previewSequence = 0;
    previewSource.addEventListener('preview.reset', () => { if (current()) { previewSequence = 0; setPreview(null); } });
    previewSource.addEventListener('preview.update', event => {
      if (!current()) return;
      let raw: unknown;
      try { raw = JSON.parse((event as MessageEvent).data); } catch { setPreview(null); return; }
      if (!raw || typeof raw !== 'object') { setPreview(null); return; }
      const item = raw as Record<string, unknown>;
      if (!Number.isInteger(item.sequence) || typeof item.agentRunId !== 'string'
        || !(typeof item.text === 'string' || item.text === null)) { setPreview(null); return; }
      const value = { sequence: item.sequence as number, agentRunId: item.agentRunId, text: item.text as string | null };
      if (value.sequence <= previewSequence) return;
      previewSequence = value.sequence;
      setPreview(previous => value.text === null ? null : previous?.agentRunId === value.agentRunId && previous.text === value.text ? previous : value);
    });
    previewSource.onerror = () => { if (current()) setPreview(null); };
    let progress = initialStreamProgress();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let finishDelay: (() => void) | undefined;
    let inFlight: Promise<void> | null = null;
    let detailsDirty = allDetails();
    let latestMessages: History | null = null;
    let olderBefore: number | null = null;
    const draftCache = new Map<string, WorkflowDraft>();
    const draftReadSequences = new Map<string, number>();
    const draftValidatedEpochs = new Map<string, number>();
    let draftRefreshEpoch = 0;
    let resultReadSequence = 0;
    const current = () => !cancelled && viewGeneration.current === generation && activeRef.current === activeId;
    const reportError = (caught: unknown) => {
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      if (current()) showError(caught);
    };
    const readDraft = (id: string) => {
      const epoch = draftRefreshEpoch;
      const sequence = (draftReadSequences.get(id) ?? 0) + 1;
      draftReadSequences.set(id, sequence);
      setWorkflowDraftReads(previous => ({ ...previous, [id]: { status: 'loading' } }));
      void (callApi('kernel_get_workflow_draft', `/sessions/${activeId}/workflow-drafts/${id}`, undefined, controller.signal) as Promise<WorkflowDraft>).then(item => {
        if (!current() || draftReadSequences.get(id) !== sequence) return;
        const cached = draftCache.get(id);
        const next = cached && cached.status !== 'GENERATING' && item.status === 'GENERATING' ? cached : item as WorkflowDraft;
        draftCache.set(id, next);
        draftValidatedEpochs.set(id, Math.max(draftValidatedEpochs.get(id) ?? -1, epoch));
        setWorkflowDrafts(previous => ({ ...previous, [id]: next }));
        setWorkflowDraftReads(previous => ({ ...previous, [id]: { status: 'ready' } }));
      }).catch(caught => {
        if (!current() || draftReadSequences.get(id) !== sequence || (caught instanceof DOMException && caught.name === 'AbortError')) return;
        if (!(caught instanceof ApiError)) throw caught;
        setWorkflowDraftReads(previous => ({ ...previous, [id]: { status: 'error', error: caught } }));
      });
    };
    const loadDetails = (messages: History, scope: DetailScope) => {
      const draftIds = [...new Set(messages.turns.map(turn => turn.workflowDraftId).filter((id): id is string => !!id))]
        .filter(id => {
          const cached = draftCache.get(id);
          return !cached || cached.status === 'GENERATING'
            || (draftValidatedEpochs.get(id) ?? -1) < draftRefreshEpoch;
        });
      if (scope.drafts && draftIds.length) {
        draftIds.forEach(readDraft);
      }
      if (scope.results) {
        const sequence = ++resultReadSequence;
        void allResults(activeId, controller.signal).then(outcomes => {
          if (!current() || sequence !== resultReadSequence) return;
          setResults(outcomes);
        }).catch(reportError);
      }
    };
    const scheduleRefresh = (immediate = false) => {
      if (inFlight !== null) return;
      if (immediate) {
        if (timer !== undefined) clearTimeout(timer);
        timer = undefined; void readMessages(); return;
      }
      if (timer !== undefined) return;
      timer = setTimeout(() => { timer = undefined; void readMessages(); }, 100);
    };
    const connect = (cursor: number) => {
      progress = connectStream(progress, cursor);
      source = new EventSource(`/api/v12/sessions/${activeId}/events?afterEventId=${cursor}`);
      const receive = (event: MessageEvent) => {
        if (!current()) return;
        const id = Number(event.lastEventId);
        const received = receiveStreamEvent(progress, id);
        if (!received.accepted) return;
        progress = received.state;
        if (event.type === 'control.complete') { setStopping(false); setPreview(null); }
        if (['agent.complete', 'agent.failed', 'agent.superseded', 'agent.cancelled'].includes(event.type)) setPreview(null);
        log({ kind: 'SSE', time: new Date().toLocaleTimeString(), name: event.type, eventId: event.lastEventId });
        const timing = interactionTiming.current;
        if (timing !== null && !['session.created', 'turn.accepted', 'intent_routing.requested'].includes(event.type)) {
          const now = performance.now();
          timing.maxSilenceMs = Math.max(timing.maxSilenceMs, now - timing.lastFeedbackAt);
          timing.lastFeedbackAt = now;
          if (timing.firstFeedbackAt === null) {
            timing.firstFeedbackAt = now;
            log({ kind: 'METRIC', time: new Date().toLocaleTimeString(), name: '首次有效反馈', elapsedMs: Math.round(now - timing.startedAt), requestId: timing.requestId });
          }
          if (event.type === 'agent.activity') {
            const payload = JSON.parse(event.data) as { data?: { kind?: string; status?: string } };
            if (payload.data?.kind === 'MODEL_REQUEST' && payload.data.status === 'QUEUED') timing.modelRequests += 1;
          }
          if (['agent.complete', 'agent.failed'].includes(event.type)) {
            log({ kind: 'METRIC', time: new Date().toLocaleTimeString(), name: `总完成时间；最长无反馈 ${Math.round(timing.maxSilenceMs)} 毫秒；快照 ${timing.snapshotRequests}；模型请求 ${timing.modelRequests}`, elapsedMs: Math.round(now - timing.startedAt), requestId: timing.requestId });
            interactionTiming.current = null;
          }
        }
        if (event.type.startsWith('workflow_draft.')) draftRefreshEpoch += 1;
        detailsDirty = detailsAfterEvent(detailsDirty, event.type);
        if (progress.dirty || hasDetails(detailsDirty)) scheduleRefresh(!isMetadataEvent(event.type));
      };
      eventTypes.forEach(type => source!.addEventListener(type, receive as EventListener));
      let opened = false;
      source.onopen = () => { if (current()) { setConnection('connected'); log({ kind: 'STATE', time: new Date().toLocaleTimeString(), name: opened ? 'sse.reconnected' : 'sse.connected' }); if (opened) { progress = forceStreamRefresh(progress); detailsDirty = allDetails(); draftRefreshEpoch += 1; scheduleRefresh(); } opened = true; } };
      source.onerror = () => { if (current()) { setConnection('disconnected'); log({ kind: 'STATE', time: new Date().toLocaleTimeString(), name: 'sse.disconnected' }); } };
    };
    const readMessages = (): Promise<void> => {
      if (inFlight !== null) return inFlight;
      if (!progress.dirty && olderBefore === null) {
        if (latestMessages && hasDetails(detailsDirty)) { loadDetails(latestMessages, detailsDirty); detailsDirty = noDetails(); }
        return Promise.resolve();
      }
      inFlight = (async () => {
        try {
          while (current() && (progress.dirty || olderBefore !== null || hasDetails(detailsDirty))) {
            if (!progress.dirty && olderBefore === null && latestMessages) {
              loadDetails(latestMessages, detailsDirty); detailsDirty = noDetails(); break;
            }
            const before = progress.dirty ? null : olderBefore;
            let details = before !== null ? { ...allDetails(), results: false } : detailsDirty;
            if (before === null) { progress = beginSnapshotRead(progress); detailsDirty = noDetails(); } else olderBefore = null;
            if (before === null && interactionTiming.current !== null) interactionTiming.current.snapshotRequests += 1;
            if (before === null) log({ kind: 'STATE', time: new Date().toLocaleTimeString(), name: 'snapshot.requested' });
            const messages = await callApi('kernel_get_messages', `/sessions/${activeId}/messages${before === null ? '' : `?cursor=${before}`}`, undefined, controller.signal) as History;
            if (!current()) return;
            if (before === null) {
              const applied = applySnapshot(progress, messages.eventCursor);
              if (applied.accepted) {
                progress = applied.state;
                latestMessages = messages;
                setHistory(previous => previous ? refreshHistory(previous, messages) : messages);
                setLoading(false);
                setStopping(messages.controlRequests?.some(item => ['REQUESTED', 'PROCESSING'].includes(item.status)) ?? false);
                log({ kind: 'STATE', time: new Date().toLocaleTimeString(), name: 'snapshot.applied' });
                // Subscribe before optional detail reads; events after this snapshot are durable.
                if (source === undefined) connect(messages.eventCursor);
                if (!progress.dirty) {
                  details = mergeDetails(details, detailsDirty);
                  detailsDirty = noDetails();
                }
                loadDetails(messages, details);
              }
            } else {
              setHistory(previous => previous ? prependHistory(previous, messages) : messages);
              setLoadingOlder(false);
              loadDetails(messages, details);
            }
            if (current() && (progress.dirty || olderBefore !== null || hasDetails(detailsDirty))) await new Promise<void>(resolve => {
              finishDelay = resolve;
              timer = setTimeout(() => { timer = undefined; finishDelay = undefined; resolve(); }, 100);
            });
          }
        } catch (caught) { reportError(caught); if (current()) { setLoading(false); setLoadingOlder(false); } }
        finally { inFlight = null; }
      })();
      return inFlight;
    };
    refreshRequest.current = () => {
      progress = forceStreamRefresh(progress); detailsDirty = allDetails(); draftRefreshEpoch += 1;
      if (timer !== undefined && inFlight === null) { clearTimeout(timer); timer = undefined; }
      return readMessages();
    };
    olderRequest.current = before => { olderBefore = before; setLoadingOlder(true); return readMessages(); };
    draftRequest.current = id => { if (current() && draftReadSequences.has(id)) readDraft(id); };
    void readMessages();
    return () => {
      cancelled = true; controller.abort(); source?.close(); previewSource.close();
      refreshRequest.current = null; olderRequest.current = null; draftRequest.current = null;
      if (timer !== undefined) clearTimeout(timer);
      finishDelay?.();
    };
  }, [activeId, log, showError]);

  async function olderMessages() {
    if (history?.nextCursor && !loadingOlder) await olderRequest.current?.(history.nextCursor);
  }

  async function loadTrace() {
    if (!lastTurn || ['ROUTING_REQUESTED', 'ROUTING_RUNNING'].includes(lastTurn.routingStatus ?? '')) return;
    const generation = viewGeneration.current;
    try {
      const value = await callApi('kernel_get_agent_trace', `/agent-runs/${lastTurn.agentRunId}/trace?${projectQuery(project)}`, undefined, viewAbort.current?.signal);
      if (viewGeneration.current === generation) setTrace(value);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      if (viewGeneration.current === generation) showError(caught);
    }
  }

  return {
    preview,
    stopping,
    setStopping,
    history,
    workflowDrafts,
    workflowDraftReads,
    retryWorkflowDraft: (id: string) => draftRequest.current?.(id),
    results,
    loading,
    loadingOlder,
    error,
    clearError: () => setError(null),
    connection,
    connected,
    trace,
    lastTurn,
    refresh,
    olderMessages,
    loadTrace,
    refs: { activeRef, viewGeneration, interactionTiming } satisfies SessionStreamRefs,
  };
}
