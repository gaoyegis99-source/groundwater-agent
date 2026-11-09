import { useCallback, useEffect, useState } from 'react';
import type { WorkspaceTab } from './WorkspacePanel';
import { scopedStorageKey } from './browser-scope';

type WorkspaceFocus = { tab: WorkspaceTab; nonce: number } | null;
export type ConfirmationFocus = { field: string; value?: string } | null;

export function useWorkspaceController(activeSessionId: string, storageScope: string) {
  const [collapsed, setCollapsed] = useState(window.innerWidth < 1200);
  const [resourceId, setResourceId] = useState<string | null>(
    sessionStorage.getItem(scopedStorageKey(storageScope, 'workspace-resource', activeSessionId)),
  );
  const [editingConfirmationId, setEditingConfirmationId] = useState<string | null>(null);
  const [confirmationFocus, setConfirmationFocus] = useState<ConfirmationFocus>(null);
  const [focusRequest, setFocusRequest] = useState<WorkspaceFocus>(null);

  useEffect(() => {
    setResourceId(sessionStorage.getItem(scopedStorageKey(storageScope, 'workspace-resource', activeSessionId)));
    setEditingConfirmationId(null);
    setConfirmationFocus(null);
  }, [activeSessionId, storageScope]);

  const focus = useCallback((tab: WorkspaceTab) => {
    setFocusRequest({ tab, nonce: Date.now() });
    setCollapsed(false);
  }, []);
  const openResource = useCallback((id: string) => {
    setResourceId(id || null);
    const key = scopedStorageKey(storageScope, 'workspace-resource', activeSessionId);
    if (id) sessionStorage.setItem(key, id);
    else sessionStorage.removeItem(key);
    focus('results');
  }, [activeSessionId, focus, storageScope]);
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
    toggleCollapsed: () => setCollapsed(value => !value),
    resourceId,
    editingConfirmationId,
    confirmationFocus,
    focusRequest,
    focus,
    openResource,
    editConfirmation,
    closeConfirmation,
  };
}
