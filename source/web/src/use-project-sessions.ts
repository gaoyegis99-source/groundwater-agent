import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, callApi, observeApi } from './api';
import type { Capabilities, DebugEntry, SessionSummary } from './types';
import { verifyProductUi } from './product-ui';

export function currentSessionId() {
  return /^#\/sessions\/([a-zA-Z0-9-]+)(?:\/workbench\/(?:inputs|parameters|tasks|results))?$/.exec(window.location.hash)?.[1] ?? '';
}

export function useProjectSessions(log: (entry: DebugEntry) => void) {
  const [activeId, setActiveId] = useState(currentSessionId);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilityError, setCapabilityError] = useState<ApiError | null>(null);
  const [sessionError, setSessionError] = useState<ApiError | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionCursor, setSessionCursor] = useState<number | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [updatingSessionId, setUpdatingSessionId] = useState<string | null>(null);
  const [sessionQuery, setSessionQueryState] = useState('');
  const [showArchived, setShowArchivedState] = useState(false);
  const queryRef = useRef('');
  const archivedRef = useRef(false);
  const filtersMounted = useRef(false);
  const identityRef = useRef('');
  const projectRef = useRef('');
  const requestedFilterRef = useRef('');
  const requestSerial = useRef(0);
  const connectionSerial = useRef(0);
  const identityGeneration = useRef(0);
  const updatePending = useRef(false);

  const loadSessions = useCallback(async (before: number | null = null) => {
    const filterKey = `${queryRef.current.trim()}\0${archivedRef.current}`;
    if (!identityRef.current || (before !== null && requestedFilterRef.current !== filterKey)) return;
    const serial = ++requestSerial.current;
    const identity = identityRef.current;
    const isCurrent = () => serial === requestSerial.current && identity === identityRef.current
      && filterKey === `${queryRef.current.trim()}\0${archivedRef.current}`;
    if (before === null) requestedFilterRef.current = filterKey;
    setSessionsLoading(true);
    try {
      const query = new URLSearchParams();
      if (before !== null) query.set('cursor', String(before));
      if (queryRef.current.trim()) query.set('query', queryRef.current.trim());
      if (archivedRef.current) query.set('archived', 'true');
      const suffix = query.size ? `?${query.toString()}` : '';
      const page = await callApi('kernel_list_sessions',
        `/sessions${suffix}`,
      );
      if (!isCurrent()) return;
      if (page.items.some(item => item.projectId !== projectRef.current)) {
        throw new ApiError(409, 'API_RESPONSE_INVALID');
      }
      setSessions(previous => {
        const merged = new Map((before === null ? [] : previous).map(item => [item.sessionId, item]));
        for (const item of page.items) merged.set(item.sessionId, item);
        return [...merged.values()];
      });
      setSessionCursor(page.nextCursor);
      setSessionError(null);
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (!isCurrent()) return;
      if (before === null && requestedFilterRef.current === filterKey) {
        requestedFilterRef.current = '';
      }
      setSessionError(caught);
    } finally {
      if (isCurrent()) setSessionsLoading(false);
    }
  }, []);

  const setSessionQuery = useCallback((value: string) => {
    const filterChanged = value.trim() !== queryRef.current.trim();
    queryRef.current = value;
    setSessionQueryState(value);
    if (!filterChanged) return;
    requestSerial.current += 1;
    requestedFilterRef.current = '';
    setSessions([]);
    setSessionCursor(null);
    setSessionError(null);
    setSessionsLoading(!!identityRef.current);
  }, []);
  const setShowArchived = useCallback((value: boolean) => {
    if (value === archivedRef.current) return;
    requestSerial.current += 1;
    archivedRef.current = value;
    requestedFilterRef.current = '';
    setSessions([]);
    setSessionCursor(null);
    setSessionError(null);
    setSessionsLoading(!!identityRef.current);
    setShowArchivedState(value);
  }, []);

  const updateSession = useCallback(async (
    sessionId: string, update: { title?: string; archived?: boolean },
  ) => {
    if (updatePending.current) throw new ApiError(409, 'SESSION_UPDATE_IN_PROGRESS');
    updatePending.current = true;
    setUpdatingSessionId(sessionId);
    const generation = identityGeneration.current;
    try {
      const metadata = await callApi(
        'kernel_update_session', `/sessions/${encodeURIComponent(sessionId)}`,
        update, undefined, undefined, 'patch',
      );
      if (generation !== identityGeneration.current) return false;
      if (metadata.sessionId !== sessionId) throw new ApiError(409, 'API_RESPONSE_INVALID');
      setSessions(previous => previous.flatMap(item => {
        if (item.sessionId !== sessionId) return [item];
        if (metadata.archived !== archivedRef.current) return [];
        return [{ ...item, title: metadata.title ?? item.title, archived: metadata.archived }];
      }));
      await loadSessions();
      return generation === identityGeneration.current;
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (generation !== identityGeneration.current) return false;
      setSessionError(caught);
      throw caught;
    } finally {
      if (generation === identityGeneration.current) {
        updatePending.current = false;
        setUpdatingSessionId(null);
      }
    }
  }, [loadSessions]);

  const connectProject = useCallback(async () => {
    const serial = ++connectionSerial.current;
    try {
      const value = await callApi('kernel_capabilities', '/capabilities') as Capabilities;
      if (serial !== connectionSerial.current) return;
      try { verifyProductUi(value); } catch { throw new ApiError(409, 'PRODUCT_UI_IDENTITY_MISMATCH'); }
      const identity = `${value.productId}\0${value.projectId}\0${value.currentUser.subject}`;
      if (identityRef.current && identityRef.current !== identity) {
        identityGeneration.current += 1;
        updatePending.current = false;
        setUpdatingSessionId(null);
        setSessionError(null);
        queryRef.current = '';
        archivedRef.current = false;
        setSessionQueryState('');
        setShowArchivedState(false);
        setSessions([]);
        setSessionCursor(null);
        setActiveId('');
        window.location.hash = '#/';
      }
      identityRef.current = identity;
      projectRef.current = value.projectId;
      setCapabilities(value);
      setCapabilityError(null);
      await loadSessions();
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (serial === connectionSerial.current) setCapabilityError(caught);
    }
  }, [loadSessions]);

  useEffect(() => {
    const stop = observeApi(log);
    void connectProject();
    const onHash = () => setActiveId(currentSessionId());
    window.addEventListener('hashchange', onHash);
    return () => {
      stop();
      requestSerial.current += 1;
      connectionSerial.current += 1;
      identityGeneration.current += 1;
      updatePending.current = false;
      window.removeEventListener('hashchange', onHash);
    };
  }, [connectProject, log]);

  useEffect(() => {
    if (!filtersMounted.current) { filtersMounted.current = true; return; }
    if (!identityRef.current) return;
    const filterKey = `${queryRef.current.trim()}\0${archivedRef.current}`;
    if (requestedFilterRef.current === filterKey) return;
    const timer = window.setTimeout(() => {
      if (requestedFilterRef.current !== filterKey) void loadSessions();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [sessionQuery, showArchived, loadSessions]);

  return {
    activeId,
    capabilities,
    capabilityError,
    sessionError,
    clearSessionError: () => setSessionError(null),
    sessions,
    sessionCursor,
    sessionsLoading,
    updatingSessionId,
    sessionQuery,
    setSessionQuery,
    showArchived,
    setShowArchived,
    updateSession,
    loadSessions,
    connectProject,
  };
}
