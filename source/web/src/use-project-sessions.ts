import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, callApi, observeApi } from './api';
import type { Capabilities, DebugEntry, SessionSummary } from './types';
import { verifyProductUi } from './product-ui';

export function currentSessionId() {
  return /^#\/sessions\/([a-zA-Z0-9-]+)$/.exec(window.location.hash)?.[1] ?? '';
}

export function useProjectSessions(log: (entry: DebugEntry) => void) {
  const [activeId, setActiveId] = useState(currentSessionId);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilityError, setCapabilityError] = useState<ApiError | null>(null);
  const [sessionError, setSessionError] = useState<ApiError | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionCursor, setSessionCursor] = useState<number | null>(null);
  const [sessionQuery, setSessionQueryState] = useState('');
  const [showArchived, setShowArchivedState] = useState(false);
  const queryRef = useRef('');
  const archivedRef = useRef(false);
  const filtersMounted = useRef(false);
  const identityRef = useRef('');
  const requestedFilterRef = useRef('');

  const loadSessions = useCallback(async (before: number | null = null) => {
    const filterKey = `${queryRef.current.trim()}\0${archivedRef.current}`;
    if (before === null) requestedFilterRef.current = filterKey;
    try {
      const query = new URLSearchParams();
      if (before !== null) query.set('cursor', String(before));
      if (queryRef.current.trim()) query.set('query', queryRef.current.trim());
      if (archivedRef.current) query.set('archived', 'true');
      const suffix = query.size ? `?${query.toString()}` : '';
      const page = await callApi('kernel_list_sessions',
        `/sessions${suffix}`,
      );
      setSessions(previous => before === null ? page.items : [...previous, ...page.items]);
      setSessionCursor(page.nextCursor);
      setSessionError(null);
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      if (before === null && requestedFilterRef.current === filterKey) {
        requestedFilterRef.current = '';
      }
      setSessionError(caught);
    }
  }, []);

  const setSessionQuery = useCallback((value: string) => {
    queryRef.current = value;
    setSessionQueryState(value);
  }, []);
  const setShowArchived = useCallback((value: boolean) => {
    archivedRef.current = value;
    setShowArchivedState(value);
  }, []);

  const updateSession = useCallback(async (
    sessionId: string, update: { title?: string; archived?: boolean },
  ) => {
    try {
      await callApi(
        'kernel_update_session', `/sessions/${encodeURIComponent(sessionId)}`,
        update, undefined, undefined, 'patch',
      );
      await loadSessions();
      setSessionError(null);
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      setSessionError(caught);
      throw caught;
    }
  }, [loadSessions]);

  const connectProject = useCallback(async () => {
    try {
      const value = await callApi('kernel_capabilities', '/capabilities') as Capabilities;
      try { verifyProductUi(value); } catch { throw new ApiError(409, 'PRODUCT_UI_IDENTITY_MISMATCH'); }
      const identity = `${value.productId}\0${value.projectId}\0${value.currentUser.subject}`;
      if (identityRef.current && identityRef.current !== identity) {
        setSessions([]);
        setSessionCursor(null);
        setActiveId('');
        window.location.hash = '#/';
      }
      identityRef.current = identity;
      setCapabilities(value);
      setCapabilityError(null);
      await loadSessions();
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      setCapabilityError(caught);
    }
  }, [loadSessions]);

  useEffect(() => {
    const stop = observeApi(log);
    void connectProject();
    const onHash = () => setActiveId(currentSessionId());
    window.addEventListener('hashchange', onHash);
    return () => {
      stop();
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
    sessionQuery,
    setSessionQuery,
    showArchived,
    setShowArchived,
    updateSession,
    loadSessions,
    connectProject,
  };
}
