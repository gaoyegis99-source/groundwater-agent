import type { MessageRequest, ResourceItem, Turn } from './types';
import { newRequestId as compatibleRequestId } from '@platform/ui-boundary';
import { readJsonCache } from './browser-cache';
import { scopedStorageKey } from './browser-scope';
import { isResourceReference } from './resource-item';

export interface SavedSubmission {
  queueItemId?: string;
  body: MessageRequest;
  draftText: string | null;
  attachments: ResourceItem[];
}
function isResourceItem(value: unknown): value is ResourceItem {
  return isResourceReference(value) && 'familyId' in value && typeof value.familyId === 'string'
    && 'name' in value && typeof value.name === 'string';
}
export function isResourceItems(value: unknown): value is ResourceItem[] {
  return Array.isArray(value) && value.every(isResourceItem);
}
function isSavedSubmission(value: unknown): value is SavedSubmission {
  if (value === null || typeof value !== 'object') return false;
  const saved = value as Record<string, unknown>;
  if (saved.body === null || typeof saved.body !== 'object') return false;
  const body = saved.body as Record<string, unknown>;
  return typeof body.requestId === 'string' && typeof body.message === 'string'
    && ['AUTO', 'DIALOGUE', 'WORKFLOW_DRAFT'].includes(String(body.requestedMode))
    && (body.correctsAgentRunId === undefined || typeof body.correctsAgentRunId === 'string')
    && (saved.queueItemId === undefined || typeof saved.queueItemId === 'string')
    && (body.correctsRequestId === undefined || typeof body.correctsRequestId === 'string')
    && (saved.draftText === null || typeof saved.draftText === 'string')
    && isResourceItems(saved.attachments);
}
export function savedSubmissionForKey(scope: string, kind: string, id: string): SavedSubmission | null {
  return readJsonCache(scopedStorageKey(scope, kind, id), isSavedSubmission);
}
export function savedSubmission(scope: string, id: string): SavedSubmission | null {
  return savedSubmissionForKey(scope, 'message-request', id);
}
export function saveSubmission(scope: string, id: string, value: SavedSubmission | null) {
  const key = scopedStorageKey(scope, 'message-request', id);
  if (value === null) sessionStorage.removeItem(key);
  else sessionStorage.setItem(key, JSON.stringify(value));
}
export function newRequestId(): string {
  return compatibleRequestId();
}
export function messageBody(message: string): MessageRequest {
  return { requestId: newRequestId(), message, requestedMode: 'AUTO' };
}
export function inputReferences(resources: ResourceItem[]) {
  return resources.map(({ kind, service, datasetId, versionId, semanticId, objectId, mediaType, format, sizeBytes, checksum, outputName }) => ({
    kind, service, datasetId: datasetId, versionId: versionId,
    semanticId: semanticId, objectId: objectId,
    mediaType: mediaType, format, sizeBytes: sizeBytes, checksum,
    ...(outputName ? { outputName: outputName } : {}),
  }));
}
export function routingLabel(turn: Turn): string | null {
  if (turn.requestedMode !== 'AUTO') return null;
  if (turn.routingStatus === 'ROUTING_REQUESTED' || turn.routingStatus === 'ROUTING_RUNNING') return '正在判断需求';
  if (turn.routingStatus === 'FAILED') return '需求理解未完成';
  return null;
}
