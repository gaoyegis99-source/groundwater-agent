import type { ResourceItem } from './types';
import { isResourceItems } from './message-routing';
import { scopedStorageKey } from './browser-scope';

export interface QueuedMessage {
  id: string; text: string; attachments: ResourceItem[]; createdAt: string;
  requestId?: string; correctsAgentRunId?: string;
}
export function readQueue(scope: string, session: string): QueuedMessage[] {
  const key = scopedStorageKey(scope, 'message-queue', session);
  const raw = localStorage.getItem(key);
  if (!raw) return [];
  try {
    const values: unknown = JSON.parse(raw);
    if (!Array.isArray(values) || !values.every(item => item && typeof item.id === 'string' && typeof item.text === 'string' && typeof item.createdAt === 'string' && isResourceItems(item.attachments) && (item.requestId === undefined || typeof item.requestId === 'string') && (item.correctsAgentRunId === undefined || typeof item.correctsAgentRunId === 'string'))) throw new Error('invalid queue');
    return values as QueuedMessage[];
  } catch { localStorage.removeItem(key); return []; }
}
export function saveQueue(scope: string, session: string, items: QueuedMessage[]) {
  const key = scopedStorageKey(scope, 'message-queue', session);
  if (items.length) localStorage.setItem(key, JSON.stringify(items));
  else localStorage.removeItem(key);
}
