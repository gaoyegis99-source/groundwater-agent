import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { localizedSessionTitle } from './SessionNavigation';
import { outputName } from './card-presentation';
import { WorkItemArchive, WorkItemEdit, WorkItemIssue } from './WorkItemForms';
import { WorkItemSessions } from './WorkItemSessions';
import { MaterialIdentity, WorkItemMaterials } from './WorkItemMaterials';
import { listWorkItemActivity, readWorkItem, reviewWorkItem } from './work-item-api';
import { useWorkItemCommand } from './use-work-item-command';
import { useWorkItemPages, workItemAccessLost } from './use-work-item-queries';
import { subscribeWorkItem } from './work-item-stream';
import { sessionFromWorkItem } from './work-item-route';
import { workItemNextStep, workItemStateLabel, workItemTime } from './work-item-presentation';
import { newRequestId } from './message-routing';
import type { WorkItemActivityEntry, WorkItemDetail, WorkItemLifecycleSnapshot, WorkItemReviewCommand } from './types';

type Track = WorkItemLifecycleSnapshot['tracks'][number];
const eventsKey = (event: WorkItemActivityEntry) => event.sequence;

const commands: Record<Extract<WorkItemActivityEntry, { kind: 'COMMAND' }>['command'], string> = {
  CREATE: '创建工作', UPDATE: '更新工作信息', LINK_SESSION: '关联会话', LINK_OBJECT: '关联业务对象',
  LINK_ASSET: '关联资料或成果', ARCHIVE: '归档工作', REVIEW: '验收交付',
};
const sessionEvents: Record<string, string> = {
  'task.revised': '任务已更新', 'task.completed': '任务已完成', 'confirmation.created': '提交执行方案',
  'confirmation.approved': '执行方案已确认', 'confirmation.rejected': '执行方案未获确认',
  'workflow.started': '开始执行', 'workflow.completed': '执行记录已更新',
};

function WorkItemSection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return <section className="work-item-section" aria-labelledby={id}><h2 id={id}>{title}</h2>{children}</section>;
}

function WorkItemActivity({ id, project, cursor }: { id: string; project: string; cursor: number | undefined }) {
  const readPage = useCallback(async (after: number | undefined, signal: AbortSignal) => {
    const page = await listWorkItemActivity(id, project, { after, limit: 15, signal });
    return { items: page.entries, nextAfter: page.nextAfter };
  }, [id, project]);
  const page = useWorkItemPages(readPage, eventsKey);
  const latest = page.items.at(-1)?.sequence ?? 0;
  return <WorkItemSection id="work-item-activity" title="活动">
    <WorkItemIssue error={page.error} retry={page.retry} />
    {page.loading && <p role="status">正在读取活动…</p>}
    {!page.loading && !page.error && !page.items.length && <p>暂无活动。</p>}
    {page.items.length > 0 && <ol className="work-item-activity">
      {page.items.map(event => <li key={event.sequence}>
        <span>{event.kind === 'COMMAND' ? commands[event.command] : Object.hasOwn(sessionEvents, event.eventType)
          ? sessionEvents[event.eventType] : '关联会话进展已更新'}</span>
        <time dateTime={event.createdAt}>{workItemTime(event.createdAt)}</time>
      </li>)}
    </ol>}
    <div className="work-item-actions">
      {page.nextAfter !== null && <button type="button" onClick={page.more} disabled={page.loading || !!page.error}>加载更多活动</button>}
      {cursor !== undefined && cursor > latest && page.nextAfter === null && !page.error &&
        <button type="button" onClick={page.reload} disabled={page.loading}>刷新活动</button>}
    </div>
  </WorkItemSection>;
}

function WorkItemReview({ item, track, title, disabled, onRefresh }: {
  item: WorkItemDetail['workItem'];
  track: Track;
  title: string;
  disabled: boolean;
  onRefresh: () => void;
}) {
  const [confirmation, setConfirmation] = useState<{ token: string; revision: number } | null>(null);
  const review = track.review;
  const command = useWorkItemCommand<WorkItemReviewCommand>(
    (value, signal) => reviewWorkItem(item.workItemId, value, signal),
    () => { setConfirmation(null); onRefresh(); },
    { onRejected: onRefresh },
  );
  const stale = !!confirmation && (confirmation.token !== review?.token || confirmation.revision !== item.revision);
  const confirming = !!confirmation && !stale;
  if (!review && !command.unsettled && !command.error && !stale) return null;
  return <div className="work-item-command">
    <WorkItemIssue error={command.error} />
    {stale && !command.unsettled && <p role="status">交付已变化，请重新核对并发起验收。</p>}
    {!confirming && !command.unsettled && review && <button type="button" disabled={disabled}
      onClick={() => setConfirmation({ token: review.token, revision: item.revision })}>验收 · {title}</button>}
    {confirming || command.unsettled ? <div className="work-item-actions">
      <span>{`验收「${title}」的交付${review?.resultLinkIds.length ? `（${review.resultLinkIds.length} 项成果）` : ''}？`}</span>
      <button type="button" disabled={command.busy || (!command.unsettled && disabled)} onClick={() => {
        if (command.unsettled) { void command.submit(command.unsettled); return; }
        if (!confirmation || stale || disabled) return;
        void command.submit({
          projectId: item.projectId, sessionId: track.sessionId, reviewToken: confirmation.token,
          requestId: newRequestId(), expectedRevision: confirmation.revision,
        });
      }}>{command.busy ? '正在验收…' : command.unsettled ? '重试验收' : '确认验收'}</button>
      {!command.unsettled && <button type="button" onClick={() => setConfirmation(null)}>取消</button>}
    </div> : null}
  </div>;
}

function sessionHref(sessionId: string, workItemId: string, results = false) {
  return sessionFromWorkItem(`#/sessions/${encodeURIComponent(sessionId)}${results ? '/workbench/results' : ''}`, workItemId);
}

function WorkItemResults({ detail, snapshot, disabled, onRefresh }: {
  detail: WorkItemDetail;
  snapshot: WorkItemLifecycleSnapshot | null;
  disabled: boolean;
  onRefresh: () => void;
}) {
  const assets = detail.assets.flatMap(({ linkId, reference, state }) =>
    state === 'AVAILABLE' && reference?.kind === 'RESULT' ? [{ linkId, reference }] : []);
  return <WorkItemSection id="work-item-results" title="成果">
    {!assets.length && <p>暂无可读的关联成果。</p>}
    {!!assets.length && <ul className="work-item-items">
      {assets.map(({ linkId, reference }) => {
        const run = detail.workflowRuns.find(workflow => workflow.runId === reference.runId);
        return <li key={linkId}>
          <div><strong>{outputName(reference.outputName)}</strong><span>已发布 · {reference.dataRef.format.toUpperCase()}</span>
            <MaterialIdentity reference={reference.dataRef} /></div>
          {run && <a href={sessionHref(run.sessionId, detail.workItem.workItemId, true)}>在会话中查看成果</a>}
        </li>;
      })}
    </ul>}
    {snapshot?.tracks.map(track => <WorkItemReview key={track.sessionId} track={track} item={detail.workItem}
      title={localizedSessionTitle(detail.sessions.find(session => session.sessionId === track.sessionId)?.title ?? null)}
      disabled={disabled || detail.workItem.status === 'ARCHIVED' || !!track.review?.resultLinkIds.some(id => !assets.some(asset => asset.linkId === id))}
      onRefresh={onRefresh} />)}
  </WorkItemSection>;
}

type Observation = { snapshot: WorkItemLifecycleSnapshot | null; error: unknown; epoch: number; fresh: boolean };

export function WorkItemDetails({ id, project, subject, scope, dataEnabled, knowledgeEnabled, onSessionsChange }: {
  id: string;
  project: string;
  subject: string;
  scope: string;
  dataEnabled: boolean;
  knowledgeEnabled: boolean;
  onSessionsChange: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [observed, setObserved] = useState<Observation>({ snapshot: null, error: null, epoch: 0, fresh: false });
  const [body, setBody] = useState<{ detail: WorkItemDetail | null; error: unknown; epoch: number }>({ detail: null, error: null, epoch: 0 });
  const { snapshot, epoch } = observed;

  function refresh() {
    setObserved(previous => ({ ...previous, fresh: false }));
    setAttempt(previous => previous + 1);
  }

  useEffect(() => {
    const visible = () => { if (document.visibilityState === 'visible') refresh(); };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', visible);
    };
  }, []);

  useEffect(() => subscribeWorkItem(id, project, {
    onSnapshot: next => setObserved(previous => ({
      snapshot: next, error: null, fresh: true,
      epoch: previous.snapshot !== null && previous.snapshot.eventCursor > next.eventCursor ? previous.epoch + 1 : previous.epoch,
    })),
    onError: error => setObserved(previous => ({ ...previous, error, fresh: false })),
  }), [id, project, attempt]);

  useEffect(() => {
    const controller = new AbortController();
    void readWorkItem(id, { project, subject }, controller.signal).then(
      detail => { if (!controller.signal.aborted) setBody({ detail, error: null, epoch }); },
      error => {
        if (!controller.signal.aborted) {
          setBody(previous => ({ detail: workItemAccessLost(error) || previous.epoch !== epoch ? null : previous.detail, error, epoch }));
        }
      },
    );
    return () => controller.abort();
  }, [id, project, subject, attempt, snapshot, epoch]);

  const unavailable = workItemAccessLost(observed.error) || workItemAccessLost(body.error);
  const detail = body.epoch === epoch && !unavailable ? body.detail : null;
  if (!detail) return <>
    <h1 id="work-item-heading">业务工作详情</h1>
    <WorkItemIssue error={body.error ?? observed.error} retry={refresh} />
    {!body.error && !observed.error && <p role="status">正在读取工作详情…</p>}
  </>;

  const item = detail.workItem;
  const current = observed.fresh && !!snapshot && item.revision === snapshot.revision && !body.error;
  const reason = snapshot && workItemNextStep(snapshot.reason);
  return <>
    <div className="work-item-title work-item-detail-title">
      <div>
        <h1 id="work-item-heading">{item.title}</h1>
        {item.description && <p>{item.description}</p>}
      </div>
      <span className="work-item-state" data-state={snapshot?.state}>{snapshot ? workItemStateLabel[snapshot.state] : item.status === 'ARCHIVED' ? '已归档' : '状态待核对'}</span>
    </div>
    <WorkItemIssue error={body.error ?? observed.error} retry={refresh} />
    <div className="work-item-detail-grid">
      <div className="work-item-detail-main">
        <WorkItemSection id="work-item-progress" title="当前进展">
          {reason && <p>{reason}</p>}
          {!current && <p role="status">{snapshot ? '显示最近一次核对的状态，等待更新。' : '正在核对进展…'}</p>}
          <p className="work-item-meta"><time dateTime={item.updatedAt}>工作信息更新于 {workItemTime(item.updatedAt)}</time></p>
          <ul className="work-item-items">
            {snapshot?.tracks.map(track => {
              const session = detail.sessions.find(item => item.sessionId === track.sessionId);
              return <li key={track.sessionId}><div>
                <strong>{localizedSessionTitle(session?.title ?? null)}</strong>
                <span>{workItemStateLabel[track.state]}</span>
              </div>
              {track.publications.length > 0 && <span>{track.publications.map(({ state }) => ({
                PUBLISHED: '已发布', PENDING: '待发布', FAILED: '发布失败', UNAVAILABLE: '发布状态待核验',
              })[state]).join('、')}</span>}</li>;
            })}
          </ul>
        </WorkItemSection>
        <WorkItemSection id="work-item-sessions" title="关联会话">
          <WorkItemSessions detail={detail} scope={scope} current={current}
            onRefresh={refresh} onSessionsChange={onSessionsChange} />
        </WorkItemSection>
        {!body.error && <WorkItemMaterials detail={detail} scope={scope} current={current}
          dataEnabled={dataEnabled} knowledgeEnabled={knowledgeEnabled} onRefresh={refresh} />}
        {!body.error && <WorkItemResults detail={detail} snapshot={snapshot} disabled={!current} onRefresh={refresh} />}
      </div>
      <aside className="work-item-detail-aside">
        <WorkItemSection id="work-item-operations" title="可用操作">
          {item.status === 'ARCHIVED' ? <p>这项工作已归档。</p> : <div className="work-item-operations">
            <WorkItemEdit item={item} disabled={!current} onRefresh={refresh} />
            <WorkItemArchive item={item} disabled={!current} onRefresh={refresh} />
          </div>}
        </WorkItemSection>
        <WorkItemActivity key={epoch} id={id} project={project} cursor={snapshot?.eventCursor} />
      </aside>
    </div>
  </>;
}
