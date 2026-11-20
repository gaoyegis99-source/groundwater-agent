import { ApiError, callApi, projectQuery } from './api';
import { loadResponseValidator } from './generated/public-api-v12.validators.lazy.js';
import type {
  WorkItemActivityEntry, WorkItemActivityPage, WorkItemChangeCommand, WorkItemCreateCommand,
  WorkItemDetail, WorkItemLifecycleSnapshot, WorkItemReceipt, WorkItemReviewCommand,
  WorkItemMaterialCommand, WorkItemSessionAttach, WorkItemSessionContext, WorkItemSummary, WorkItemUpdateCommand,
} from './types';

type WorkItemScope = { project: string; subject: string };

function requireScope(item: WorkItemSummary, { project, subject }: WorkItemScope) {
  if (item.projectId !== project || item.ownerSubject !== subject) {
    throw new TypeError('API_RESPONSE_INVALID');
  }
}

export function workItemPath(workItemId: string) {
  return `/work-items/${encodeURIComponent(workItemId)}`;
}

export function listWorkItems(
  scope: WorkItemScope,
  { after, includeArchived, signal }: { after?: string; includeArchived: boolean; signal: AbortSignal },
) {
  const query = projectQuery(scope.project, { after: after ?? null, includeArchived: String(includeArchived), limit: 12 });
  return callApi('kernel_list_work_items', `/work-items?${query}`, undefined, signal, value => {
    const page = value as { items: WorkItemSummary[]; nextAfter: string | null };
    page.items.forEach(item => requireScope(item, scope));
    return page;
  });
}

export function createWorkItem(command: WorkItemCreateCommand, signal?: AbortSignal) {
  return callApi('kernel_create_work_item', '/work-items', command, signal);
}

export function readWorkItem(workItemId: string, scope: WorkItemScope, signal?: AbortSignal) {
  return callApi('kernel_get_work_item', `${workItemPath(workItemId)}?${projectQuery(scope.project)}`, undefined, signal, value => {
    const detail = value as WorkItemDetail;
    requireScope(detail.workItem, scope);
    if (detail.workItem.workItemId !== workItemId) throw new TypeError('API_RESPONSE_INVALID');
    return detail;
  });
}

function itemReceipt(workItemId: string, value: unknown): WorkItemReceipt {
  const receipt = value as WorkItemReceipt;
  if (receipt.workItemId !== workItemId) throw new TypeError('API_RESPONSE_INVALID');
  return receipt;
}

export function archiveWorkItem(workItemId: string, command: WorkItemChangeCommand, signal?: AbortSignal) {
  return callApi('kernel_archive_work_item', `${workItemPath(workItemId)}/archive`, command, signal, value => itemReceipt(workItemId, value));
}

export function createWorkItemSession(workItemId: string, command: WorkItemChangeCommand, signal: AbortSignal) {
  return callApi('kernel_create_work_item_session', `${workItemPath(workItemId)}/sessions/create`, command, signal,
    value => itemReceipt(workItemId, value));
}

export function linkWorkItemSession(workItemId: string, command: WorkItemSessionAttach, signal: AbortSignal) {
  return callApi('kernel_link_work_item_session', `${workItemPath(workItemId)}/sessions`, command, signal, value => {
    const receipt = itemReceipt(workItemId, value);
    if (receipt.linkId !== command.sessionId) throw new TypeError('API_RESPONSE_INVALID');
    return receipt;
  });
}

export function linkWorkItemMaterial(workItemId: string, { reference, ...change }: WorkItemMaterialCommand, signal: AbortSignal) {
  const path = workItemPath(workItemId);
  const decode = (value: unknown) => {
    const receipt = itemReceipt(workItemId, value);
    if (!receipt.linkId) throw new TypeError('API_RESPONSE_INVALID');
    return receipt;
  };
  return 'objectKind' in reference
    ? callApi('kernel_link_work_item_object', `${path}/objects`, { ...change, reference }, signal, decode)
    : callApi('kernel_link_work_item_asset', `${path}/assets`, { ...change, reference }, signal, decode);
}

export function readWorkItemSessionContext(sessionId: string, scope: WorkItemScope, signal: AbortSignal) {
  const path = `/sessions/${encodeURIComponent(sessionId)}/work-item?${projectQuery(scope.project)}`;
  return callApi('kernel_get_session_work_item', path, undefined, signal, value => {
    const { context } = value as { context: WorkItemSessionContext | null };
    if (context !== null) {
      requireScope(context.workItem, scope);
      if (context.sessionId !== sessionId) throw new TypeError('API_RESPONSE_INVALID');
    }
    return context;
  });
}

export function readWorkItemSnapshot(workItemId: string, project: string, signal?: AbortSignal): Promise<WorkItemLifecycleSnapshot> {
  return callApi('kernel_get_work_item_snapshot', `${workItemPath(workItemId)}/snapshot?${projectQuery(project)}`, undefined, signal, value => {
    const snapshot = value as WorkItemLifecycleSnapshot;
    if (!snapshot || snapshot.workItemId !== workItemId || !Number.isSafeInteger(snapshot.eventCursor) || snapshot.eventCursor < 0) {
      throw new TypeError('API_RESPONSE_INVALID');
    }
    return snapshot;
  });
}

export function listWorkItemActivity(workItemId: string, project: string, options: { after?: number; limit?: number; signal?: AbortSignal } = {}): Promise<WorkItemActivityPage> {
  const query = projectQuery(project, { after: options.after ?? null, limit: options.limit ?? null });
  return callApi('kernel_list_work_item_activity', `${workItemPath(workItemId)}/activity?${query}`, undefined, options.signal);
}

export function updateWorkItem(workItemId: string, command: WorkItemUpdateCommand, signal?: AbortSignal) {
  return callApi('kernel_update_work_item', `${workItemPath(workItemId)}/update`, command, signal, value => itemReceipt(workItemId, value));
}

export function reviewWorkItem(workItemId: string, command: WorkItemReviewCommand, signal?: AbortSignal) {
  return callApi('kernel_review_work_item', `${workItemPath(workItemId)}/review`, command, signal, value => itemReceipt(workItemId, value));
}

export async function decodeWorkItemActivity(data: string): Promise<WorkItemActivityEntry> {
  let entry: unknown;
  try { entry = JSON.parse(data); }
  catch { throw new ApiError(200, 'API_RESPONSE_INVALID'); }
  // The SSE payload is one entry from the paginated JSON activity contract.
  const validate = await loadResponseValidator('kernel_list_work_item_activity');
  if (!validate?.({ entries: [entry], nextAfter: null })) throw new ApiError(200, 'API_RESPONSE_INVALID');
  return entry as WorkItemActivityEntry;
}
