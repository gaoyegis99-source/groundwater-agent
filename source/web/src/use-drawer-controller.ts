import { useCallback, useState } from 'react';
import type { KnowledgeReference } from './types';

export type Drawer =
  | { kind: 'database'; id?: string; compare?: boolean; formats?: string[] }
  | { kind: 'knowledge'; reference?: KnowledgeReference }
  | { kind: 'capabilities' }
  | { kind: 'readiness' }
  | null;

export function useDrawerController() {
  const [drawer, setDrawer] = useState<Drawer>(null);
  const closeDrawer = useCallback(() => setDrawer(null), []);
  const openDatabase = useCallback(
    (options: Omit<NonNullable<Extract<Drawer, { kind: 'database' }>>, 'kind'> = {}) =>
      setDrawer({ kind: 'database', ...options }),
    [],
  );
  const openKnowledge = useCallback(
    (reference?: KnowledgeReference) => setDrawer({ kind: 'knowledge', reference }),
    [],
  );
  const openCapabilities = useCallback(() => setDrawer({ kind: 'capabilities' }), []);
  const openReadiness = useCallback(() => setDrawer({ kind: 'readiness' }), []);
  return {
    drawer,
    closeDrawer,
    openDatabase,
    openKnowledge,
    openCapabilities,
    openReadiness,
  };
}
