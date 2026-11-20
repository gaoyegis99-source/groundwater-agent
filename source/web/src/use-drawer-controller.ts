import { useCallback, useEffect, useRef, useState } from 'react';
import type { KnowledgeReference, ResourceReference } from './types';

export type Drawer =
  | { kind: 'database'; id?: string; reference?: ResourceReference; compare?: boolean; formats?: string[]; inspectMetadata?: boolean }
  | { kind: 'knowledge'; reference?: KnowledgeReference }
  | { kind: 'capabilities' }
  | { kind: 'business-capability'; id: string; version: string }
  | { kind: 'readiness' }
  | null;

export function useDrawerController(scope: string) {
  const [selection, setSelection] = useState<{ scope: string; drawer: NonNullable<Drawer> } | null>(null);
  const drawer = selection?.scope === scope ? selection.drawer : null;
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (selection && selection.scope !== scope) {
      opener.current = null;
      setSelection(current => current === selection ? null : current);
    }
  }, [scope, selection]);
  const open = useCallback((next: NonNullable<Drawer>) => {
    if (!opener.current && document.activeElement instanceof HTMLElement) opener.current = document.activeElement;
    setSelection({ scope, drawer: next });
  }, [scope]);
  const closeDrawer = useCallback(() => {
    const previous = opener.current;
    opener.current = null;
    setSelection(null);
    requestAnimationFrame(() => {
      if (!opener.current && previous?.isConnected && previous.getClientRects().length
        && document.activeElement === document.body) previous.focus({ preventScroll: true });
    });
  }, []);
  const openDatabase = useCallback(
    (options: Omit<NonNullable<Extract<Drawer, { kind: 'database' }>>, 'kind'> = {}) =>
      open({ kind: 'database', ...options }),
    [open],
  );
  const openKnowledge = useCallback(
    (reference?: KnowledgeReference) => open({ kind: 'knowledge', reference }),
    [open],
  );
  const openCapabilities = useCallback(() => open({ kind: 'capabilities' }), [open]);
  const openBusinessCapability = useCallback((id: string, version: string) => open({ kind: 'business-capability', id, version }), [open]);
  const openReadiness = useCallback(() => open({ kind: 'readiness' }), [open]);
  return {
    drawer,
    closeDrawer,
    openDatabase,
    openKnowledge,
    openCapabilities,
    openBusinessCapability,
    openReadiness,
  };
}
