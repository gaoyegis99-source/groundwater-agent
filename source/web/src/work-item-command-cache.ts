import { readJsonCache } from './browser-cache';
import { scopedStorageKey } from './browser-scope';
import type { WorkItemChangeCommand, WorkItemSummary } from './types';

export function workItemCommandCache<Command extends WorkItemChangeCommand>(
  scope: string,
  item: WorkItemSummary,
  operation: 'archive' | 'edit' | 'material' | 'result-association',
  matches: (value: Record<string, unknown>) => boolean,
  source?: string,
) {
  const id = source === undefined ? item.workItemId : JSON.stringify([item.workItemId, source]);
  const key = scopedStorageKey(scope, `work-item-pending-${operation}`, id);
  return {
    read() {
      if (!scope) return null;
      try {
        return readJsonCache(key, (value): value is Command => {
          if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
          const command = value as Record<string, unknown>;
          return command.projectId === item.projectId
            && typeof command.requestId === 'string' && /^[a-z0-9-]{1,256}$/i.test(command.requestId)
            && typeof command.expectedRevision === 'number' && Number.isSafeInteger(command.expectedRevision) && command.expectedRevision >= 1
            && matches(command);
        });
      } catch (error) {
        if (!(error instanceof DOMException)) throw error;
        return null;
      }
    },
    remember(command: Command | null) {
      if (!scope) return;
      try {
        if (command) sessionStorage.setItem(key, JSON.stringify(command));
        else sessionStorage.removeItem(key);
      } catch (error) {
        if (!(error instanceof DOMException)) throw error;
        // The mounted panel can still replay the command when browser storage is unavailable.
      }
    },
  };
}
