import { scopedStorageKey } from './browser-scope';
import { readJsonCache } from './browser-cache';
import type { WorkItemCreateCommand } from './types';

const kind = 'work-item-pending-create';

export function readPendingWorkItemCreate(scope: string, project: string) {
  try {
    return readJsonCache(scopedStorageKey(scope, kind), (value): value is WorkItemCreateCommand => {
      if (!value || typeof value !== 'object') return false;
      const saved = value as Partial<WorkItemCreateCommand>;
      return Object.keys(value).length === 4
        && saved.projectId === project && typeof saved.requestId === 'string' && saved.requestId.length > 0
        && saved.requestId.length <= 256 && typeof saved.title === 'string' && saved.title.trim().length > 0
        && saved.title.length <= 120 && (saved.description === null || (typeof saved.description === 'string' && saved.description.length <= 2000));
    });
  } catch {
    return null;
  }
}

export function rememberWorkItemCreate(scope: string, value: WorkItemCreateCommand | null) {
  try {
    const key = scopedStorageKey(scope, kind);
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
  } catch {
    // The in-memory command can still be replayed while this page is mounted.
  }
}

export function browserStorageAvailable() {
  try {
    // Reading length catches environments that block access to the Storage object.
    return sessionStorage.length >= 0;
  } catch {
    return false;
  }
}
