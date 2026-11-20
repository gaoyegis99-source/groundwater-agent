import { useState } from 'react';
import { ApiError } from './api';
import { readJsonCache } from './browser-cache';
import { scopedStorageKey } from './browser-scope';
import { outputName } from './card-presentation';
import { newRequestId } from './message-routing';
import { localizedSessionTitle } from './SessionNavigation';
import { useWorkItemCommand } from './use-work-item-command';
import { reviewWorkItem } from './work-item-api';
import { WorkItemIssue } from './WorkItemForms';
import { workItemNextStep, workItemStateLabel, workItemTime } from './work-item-presentation';
import { sessionFromWorkItem } from './work-item-route';
import type { WorkItemDetail, WorkItemLifecycleSnapshot, WorkItemReviewCommand } from './types';

type Track = WorkItemLifecycleSnapshot['tracks'][number];

function readPendingReview(key: string, projectId: string, sessionId: string) {
  return readJsonCache(key, (value): value is WorkItemReviewCommand => {
    if (value === null || typeof value !== 'object') return false;
    const { projectId: project, sessionId: session, requestId, expectedRevision, reviewToken } = value as WorkItemReviewCommand;
    return project === projectId && session === sessionId && typeof requestId === 'string' && !!requestId
      && Number.isSafeInteger(expectedRevision) && expectedRevision >= 1
      && typeof reviewToken === 'string' && /^sha256:[0-9a-f]{64}$/.test(reviewToken);
  });
}

export function WorkItemReview({ detail, track, scope, current, onRefresh }: {
  detail: WorkItemDetail;
  track: Track;
  scope: string;
  current: boolean;
  onRefresh: () => void;
}) {
  const item = detail.workItem;
  const session = detail.sessions.find(link => link.sessionId === track.sessionId);
  const title = localizedSessionTitle(session?.title ?? null);
  const key = scopedStorageKey(scope, 'work-item-review', JSON.stringify([item.workItemId, track.sessionId]));
  const [restored] = useState(() => readPendingReview(key, item.projectId, track.sessionId));
  const [confirmation, setConfirmation] = useState<{ token: string; revision: number } | null>(null);
  const review = track.review;
  const command = useWorkItemCommand<WorkItemReviewCommand>(
    (value, signal) => reviewWorkItem(item.workItemId, value, signal),
    () => { setConfirmation(null); onRefresh(); },
    {
      restored,
      remember: value => {
        try {
          if (value) sessionStorage.setItem(key, JSON.stringify(value));
          else sessionStorage.removeItem(key);
        } catch (error) {
          if (!(error instanceof DOMException)) throw error;
          // The command remains recoverable in memory when the browser cache is unavailable.
        }
      },
      onRejected: () => { setConfirmation(null); onRefresh(); },
    },
  );
  const candidates = review?.resultLinkIds.flatMap(id => {
    const asset = detail.assets.find(link => link.linkId === id && link.state === 'AVAILABLE');
    const reference = asset?.reference;
    return reference?.kind === 'RESULT' && detail.workflowRuns.some(run => run.runId === reference.runId && run.sessionId === track.sessionId)
      ? [{ id, reference }] : [];
  }) ?? [];
  const candidateVisible = !!review && review.resultLinkIds.length === candidates.length && !!session && item.status === 'ACTIVE';
  const stale = !!confirmation && (!candidateVisible || confirmation.token !== review?.token || confirmation.revision !== item.revision);
  const confirming = !!confirmation && !stale;
  const conflict = command.error instanceof ApiError && command.error.status === 409;
  const status = track.acceptedAt ? '已验收' : workItemStateLabel[track.state];
  if (!review && !command.unsettled && !command.error && !stale && !track.acceptedAt
    && !['WAITING_REVIEW', 'RESULT_REVIEW'].includes(track.state)) return null;
  return <div className="work-result-review-row">
    <div className="work-item-actions"><strong>{title}</strong><span>{status}</span>
      {track.acceptedAt && <time dateTime={track.acceptedAt}>{workItemTime(track.acceptedAt)}</time>}</div>
    {!track.acceptedAt && !candidateVisible && <p>{workItemNextStep(track.reason) ?? '等待核对交付。'}</p>}
    {!track.acceptedAt && session && track.reason === 'TASK_REVIEW_REQUIRED' && <a href={sessionFromWorkItem(
      `#/sessions/${encodeURIComponent(session.sessionId)}/workbench/tasks`, item.workItemId,
    )}>查看任务审核</a>}
    {!conflict && <WorkItemIssue error={command.error} />}
    {(stale || conflict) && !command.unsettled && <p role="status">交付已变化，请重新核对并发起验收。</p>}
    {!confirming && !command.unsettled && candidateVisible && <button type="button" disabled={!current}
      onClick={() => { command.clearError(); setConfirmation({ token: review.token, revision: item.revision }); }}>验收 · {title}</button>}
    {(confirming || command.unsettled) && <div className="work-item-command">
      <p>{command.unsettled ? '验收回执待核对。' : `验收「${title}」的交付？`}</p>
      {!command.unsettled && candidates.length > 0 && <ul>{candidates.map(({ id, reference }) => <li key={id}>
        {outputName(reference.outputName)} · <code title={reference.dataRef.versionId}>{reference.dataRef.versionId.slice(0, 12)}</code>
      </li>)}</ul>}
      <div className="work-item-actions">
        <button type="button" disabled={command.busy || (!command.unsettled && !current)} onClick={() => {
          if (command.unsettled) { void command.submit(command.unsettled); return; }
          if (!confirmation || stale || !current) return;
          void command.submit({
            projectId: item.projectId, sessionId: track.sessionId, reviewToken: confirmation.token,
            requestId: newRequestId(), expectedRevision: confirmation.revision,
          });
        }}>{command.busy ? '正在验收…' : command.unsettled ? '重试验收' : '确认验收'}</button>
        {!command.unsettled && <button type="button" onClick={() => setConfirmation(null)}>取消</button>}
      </div>
    </div>}
  </div>;
}
