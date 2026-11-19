import { useCallback, useEffect, useState } from 'react';
import type { WorkspaceTab } from './WorkspacePanel';
import type { ResourceReference } from './types';
import { scopedStorageKey } from './browser-scope';
import { readJsonCache } from './browser-cache';
import { isResourceReference } from './resource-item';

type WorkspaceFocus = { tab: WorkspaceTab; nonce: number } | null;
export type ConfirmationFocus = { field: string; value?: string } | null;

function readResourceSelection(key: string) {
  const id = sessionStorage.getItem(key);
  const reference = readJsonCache(`${key}:reference`, isResourceReference);
  return { key, id, reference: reference?.versionId === id ? reference : null };
}

function readWorkspaceRoute(): WorkspaceTab | null {
  const route = /^#\/sessions\/[a-zA-Z0-9-]+\/workbench\/(inputs|draft|parameters|tasks|results)$/.exec(window.location.hash);
  return route ? route[1] as WorkspaceTab : null;
}

export function useWorkspaceController(activeSessionId: string, storageScope: string) {
  const [homePanel, setHomePanel] = useState<'initial' | 'open' | 'collapsed'>('initial');
  const [sessionCollapsed, setSessionCollapsed] = useState(false);
  const [routeTab, setRouteTab] = useState(readWorkspaceRoute);
  useEffect(() => {
    const sync = () => setRouteTab(readWorkspaceRoute());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  const collapsed = activeSessionId
    ? !routeTab && sessionCollapsed
    : homePanel !== 'open';
  const selectionKey = scopedStorageKey(storageScope, 'workspace-resource', activeSessionId);
  const [selection, setSelection] = useState(() => readResourceSelection(selectionKey));
  const [editingConfirmationId, setEditingConfirmationId] = useState<string | null>(null);
  const [confirmationFocus, setConfirmationFocus] = useState<ConfirmationFocus>(null);
  const [focusRequest, setFocusRequest] = useState<WorkspaceFocus>(null);

  useEffect(() => {
    setSelection(readResourceSelection(selectionKey));
    setEditingConfirmationId(null);
    setConfirmationFocus(null);
  }, [selectionKey]);

  const returnToConversation = useCallback(() => {
    if (activeSessionId) {
      window.location.hash = `#/sessions/${activeSessionId}`;
      setRouteTab(null);
    } else setHomePanel(panel => panel === 'open' ? 'collapsed' : panel);
  }, [activeSessionId]);
  const focus = useCallback((tab: WorkspaceTab) => {
    if (activeSessionId) {
      window.location.hash = `#/sessions/${activeSessionId}/workbench/${tab}`;
      setRouteTab(tab);
      return;
    }
    setFocusRequest({ tab, nonce: Date.now() });
    setHomePanel('open');
  }, [activeSessionId]);
  const openResource = useCallback((id: string, reference: ResourceReference | null = null) => {
    const exact = id && reference?.versionId === id ? reference : null;
    setSelection({ key: selectionKey, id: id || null, reference: exact });
    if (id) sessionStorage.setItem(selectionKey, id);
    else sessionStorage.removeItem(selectionKey);
    if (exact) sessionStorage.setItem(`${selectionKey}:reference`, JSON.stringify(exact));
    else sessionStorage.removeItem(`${selectionKey}:reference`);
    focus('results');
  }, [focus, selectionKey]);
  const editConfirmation = useCallback((id: string, field: ConfirmationFocus = null) => {
    setEditingConfirmationId(id);
    setConfirmationFocus(field);
    focus('parameters');
  }, [focus]);
  const closeConfirmation = useCallback(() => {
    setEditingConfirmationId(null);
    setConfirmationFocus(null);
  }, []);

  return {
    collapsed,
    homeOpened: homePanel !== 'initial',
    routeTab,
    returnToConversation,
    toggleCollapsed: () => {
      if (!activeSessionId) setHomePanel(panel => panel === 'open' ? 'collapsed' : 'open');
      else if (routeTab) returnToConversation();
      else setSessionCollapsed(value => !value);
    },
    resourceId: selection.key === selectionKey ? selection.id : null,
    resourceReference: selection.key === selectionKey ? selection.reference : null,
    editingConfirmationId,
    confirmationFocus,
    focusRequest,
    focus,
    openResource,
    editConfirmation,
    closeConfirmation,
  };
}
