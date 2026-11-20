import { useSyncExternalStore } from 'react';

export type WorkItemRoute = { kind: 'list' | 'create' | 'invalid' } | { kind: 'detail'; id: string };

function subscribeToHash(notify: () => void) {
  window.addEventListener('hashchange', notify);
  return () => window.removeEventListener('hashchange', notify);
}

function readHash() {
  return window.location.hash;
}

export function useWorkItemHash() {
  return useSyncExternalStore(subscribeToHash, readHash);
}

const opaqueId = /^[a-z0-9-]{1,256}$/i;

export function workItemHref(id?: string) {
  return id ? `#/work-items/${encodeURIComponent(id)}` : '#/work-items';
}

export function readWorkItemRoute(hash: string): WorkItemRoute | null {
  const path = hash.split('?')[0];
  if (path === '#/work-items' || path === '#/work-items/') return { kind: 'list' };
  if (path === '#/work-items/new') return { kind: 'create' };
  if (!path.startsWith('#/work-items/')) return null;
  const id = path.slice('#/work-items/'.length);
  return opaqueId.test(id) ? { kind: 'detail', id } : { kind: 'invalid' };
}

export function workItemReturnId(hash: string) {
  if (!hash.startsWith('#/sessions/')) return null;
  const query = hash.indexOf('?');
  if (query === -1) return null;
  const id = new URLSearchParams(hash.slice(query + 1)).get('workItem');
  return id && opaqueId.test(id) && id !== 'new' ? id : null;
}

export function sessionFromWorkItem(path: string, workItemId: string | null) {
  return workItemId ? `${path}?${new URLSearchParams({ workItem: workItemId })}` : path;
}
