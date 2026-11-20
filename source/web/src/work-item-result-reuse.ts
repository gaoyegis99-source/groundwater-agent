import { ApiError } from './api';
import type { SavedSubmission } from './message-routing';
import { isResourceReference } from './resource-item';
import { readWorkItem, readWorkItemSessionContext } from './work-item-api';
import { sameDataIdentity, sameWorkItemResult, type WorkItemResultReference } from './work-item-result-evidence';

type Scope = Parameters<typeof readWorkItem>[1];
type Selection = { sourceSessionId: string; reference: WorkItemResultReference; assetLinkId?: string };

export async function requireReusableResults(
  workItemId: string, targetSessionId: string, scope: Scope, selections: Selection[], signal: AbortSignal,
) {
  if (!scope.project || !scope.subject || !workItemId || !targetSessionId
    || selections.some(item => item.sourceSessionId === targetSessionId)) {
    throw new ApiError(409, 'WORK_ITEM_RESULT_REUSE_TARGET_UNAVAILABLE');
  }

  const [current, context] = await Promise.all([
    readWorkItem(workItemId, scope, signal),
    readWorkItemSessionContext(targetSessionId, scope, signal),
  ]);
  if (current.workItem.status !== 'ACTIVE' || context?.workItem.workItemId !== workItemId
    || context.workItem.status !== 'ACTIVE'
    || !current.sessions.some(session => session.sessionId === targetSessionId && session.archivedAt === null)) {
    throw new ApiError(409, 'WORK_ITEM_RESULT_REUSE_TARGET_UNAVAILABLE');
  }

  for (const { sourceSessionId, reference, assetLinkId } of selections) {
    const link = current.assets.find(asset => (!assetLinkId || asset.linkId === assetLinkId)
      && asset.state === 'AVAILABLE' && asset.reference?.kind === 'RESULT' && sameWorkItemResult(asset.reference, reference));
    // Session context is bounded. The complete WorkItem read also projects current visibility.
    const contextLink = link && context.assets.find(item => item.linkId === link.linkId);
    if (!link || (contextLink && (contextLink.state !== 'AVAILABLE' || contextLink.reference?.kind !== 'RESULT'
      || !sameWorkItemResult(contextLink.reference, reference)))
      || !current.workflowRuns.some(run => run.runId === reference.runId && run.sessionId === sourceSessionId)) {
      throw new ApiError(409, 'WORK_ITEM_RESULT_REUSE_UNAVAILABLE');
    }
  }
}

export async function verifySubmittedWorkItemResults(sent: SavedSubmission, sessionId: string, scope: Scope, signal: AbortSignal) {
  let workItemId = '';
  const selections: Selection[] = [];
  const unavailable = () => new ApiError(409, 'WORK_ITEM_RESULT_REUSE_UNAVAILABLE');
  for (const [index, item] of sent.attachments.entries()) {
    const provenance = item.resultProvenance;
    if (provenance === undefined) continue;
    const wire = sent.body.inputReferences?.[index];
    if (!provenance || typeof provenance.workItemId !== 'string' || !/^[a-z0-9-]{1,256}$/i.test(provenance.workItemId)
      || ![provenance.sourceSessionId, provenance.runId, provenance.publicationId].every(value => typeof value === 'string' && !!value.trim())
      || (workItemId && workItemId !== provenance.workItemId)
      || !provenance.outputName || provenance.outputName !== item.outputName
      || !isResourceReference(wire) || !sameDataIdentity(item, wire)) throw unavailable();
    workItemId = provenance.workItemId;
    selections.push({ sourceSessionId: provenance.sourceSessionId, reference: {
      kind: 'RESULT', runId: provenance.runId, publicationId: provenance.publicationId, outputName: provenance.outputName,
      dataRef: { ...wire, outputName: provenance.outputName },
    } });
  }
  if (!selections.length) return;
  try {
    await requireReusableResults(workItemId, sessionId, scope, selections, signal);
  } catch (error) {
    if (error instanceof ApiError) {
      throw new ApiError(error.status, 'WORK_ITEM_RESULT_REUSE_UNAVAILABLE', error.issues, error.requestId, error.retryAfter);
    }
    if (error instanceof TypeError && error.message === 'API_RESPONSE_INVALID') throw unavailable();
    throw error;
  }
}
