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

export function useWorkspaceController(activeSessionId: string, storageScope: string) {
  const [collapsed, setCollapsed] = useState(window.innerWidth < 1200);
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

  const focus = useCallback((tab: WorkspaceTab) => {
    setFocusRequest({ tab, nonce: Date.now() });
    setCollapsed(false);
  }, []);
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
    toggleCollapsed: () => setCollapsed(value => !value),
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
