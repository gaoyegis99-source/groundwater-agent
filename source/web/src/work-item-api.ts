import { ApiError, callApi, projectQuery } from './api';
import { loadResponseValidator } from './generated/public-api-v12.validators.lazy.js';
import type { WorkItemActivityEntry, WorkItemActivityPage, WorkItemLifecycleSnapshot, WorkItemReviewCommand, WorkItemUpdateCommand } from './types';

export function workItemPath(workItemId: string) {
  return `/work-items/${encodeURIComponent(workItemId)}`;
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
  return callApi('kernel_update_work_item', `${workItemPath(workItemId)}/update`, command, signal);
}

export function reviewWorkItem(workItemId: string, command: WorkItemReviewCommand, signal?: AbortSignal) {
  return callApi('kernel_review_work_item', `${workItemPath(workItemId)}/review`, command, signal);
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
