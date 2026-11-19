import { useCallback, useRef, useState } from 'react';
import type { KnowledgeReference, ResourceReference } from './types';

export type Drawer =
  | { kind: 'database'; id?: string; reference?: ResourceReference; compare?: boolean; formats?: string[] }
  | { kind: 'knowledge'; reference?: KnowledgeReference }
  | { kind: 'capabilities' }
  | { kind: 'readiness' }
  | null;

export function useDrawerController() {
  const [drawer, setDrawer] = useState<Drawer>(null);
  const opener = useRef<HTMLElement | null>(null);
  const open = useCallback((next: NonNullable<Drawer>) => {
    if (!opener.current && document.activeElement instanceof HTMLElement) opener.current = document.activeElement;
    setDrawer(next);
  }, []);
  const closeDrawer = useCallback(() => {
    const previous = opener.current;
    opener.current = null;
    setDrawer(null);
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
  const openReadiness = useCallback(() => open({ kind: 'readiness' }), [open]);
  return {
    drawer,
    closeDrawer,
    openDatabase,
    openKnowledge,
    openCapabilities,
    openReadiness,
  };
}
