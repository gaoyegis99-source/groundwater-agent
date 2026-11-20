import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { localizedSessionTitle } from './SessionNavigation';
import { WorkItemArchive, WorkItemEdit, WorkItemIssue } from './WorkItemForms';
import { WorkItemSessions } from './WorkItemSessions';
import { WorkItemMaterials } from './WorkItemMaterials';
import { WorkItemResults, type WorkItemResultInspectorSource } from './WorkItemResults';
import { listWorkItemActivity, readWorkItem } from './work-item-api';
import { useWorkItemPages, workItemAccessLost } from './use-work-item-queries';
import { subscribeWorkItem } from './work-item-stream';
import { workItemNextStep, workItemStateLabel, workItemTime } from './work-item-presentation';
import { sameDataIdentity } from './work-item-result-evidence';
import { sessionFromWorkItem, workItemHref, type WorkItemSection as Section } from './work-item-route';
import type { ResourceReference, WorkItemActivityEntry, WorkItemDetail, WorkItemLifecycleSnapshot } from './types';

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

const sections: Record<Section, [label: string, heading: string]> = {
  overview: ['概览', 'work-item-overview'],
  conversations: ['对话', 'work-item-sessions'],
  materials: ['资料', 'work-item-materials'],
  tasks: ['任务与进度', 'work-item-progress'],
  results: ['成果', 'work-item-results'],
  activity: ['活动', 'work-item-activity'],
};
const sectionIds = Object.keys(sections) as Section[];
const resultReasons = new Set([
  'DELIVERY_REVIEW_REQUIRED', 'RESULT_LINK_REQUIRED', 'RESULT_ACCEPTANCE_REQUIRED', 'RESULT_UNAVAILABLE',
  'PUBLICATION_PENDING', 'PUBLICATION_FAILED', 'PUBLICATION_UNVERIFIED',
]);
const publicationLabels = {
  PUBLISHED: '已发布', PENDING: '待发布', FAILED: '发布失败', UNAVAILABLE: '发布状态待核验',
};

function WorkItemSection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return <section className="work-item-section" aria-labelledby={id}><h2 id={id}>{title}</h2>{children}</section>;
}

function WorkItemOverview({ detail, scope, snapshot, current, onRefresh }: {
  detail: WorkItemDetail;
  scope: string;
  snapshot: WorkItemLifecycleSnapshot | null;
  current: boolean;
  onRefresh: () => void;
}) {
  const item = detail.workItem;
  // Linked sessions are ordered by linkedAt; favor the last active one related to the current reason.
  const candidates = detail.sessions.filter(session => !session.archivedAt).reverse();
  const relevantSessions = new Set(snapshot?.tracks.filter(track => track.reason === snapshot.reason).map(track => track.sessionId));
  const resume = candidates.find(session => relevantSessions.has(session.sessionId)) ?? candidates[0];
  const reason = snapshot && workItemNextStep(snapshot.reason);
  const seeResults = snapshot && (resultReasons.has(snapshot.reason) || snapshot.state === 'COMPLETED');
  const next = reason ?? (item.status === 'ACTIVE' && !resume ? '创建或关联对话，继续这项工作。' : null);
  return <div className="work-item-detail-grid">
    <WorkItemSection id="work-item-overview" title="下一步">
      {next && <p>{next}</p>}
      <div className="work-item-actions work-item-overview-actions">
        {resume ? <a className="button primary" href={sessionFromWorkItem(
          `#/sessions/${encodeURIComponent(resume.sessionId)}`, item.workItemId,
        )}>{item.status === 'ARCHIVED' ? '查看对话' : '继续对话'}</a>
          : <a className="button primary" href={workItemHref(item.workItemId, 'conversations')}>
            {item.status === 'ARCHIVED' ? '查看关联对话' : '创建或关联对话'}</a>}
        {seeResults ? <a href={workItemHref(item.workItemId, 'results')}>查看成果</a>
          : !!snapshot?.tracks.length && <a href={workItemHref(item.workItemId, 'tasks')}>查看任务与进度</a>}
      </div>
      {resume && <p className="work-item-meta">{localizedSessionTitle(resume.title)}</p>}
      <p className="work-item-meta"><time dateTime={item.updatedAt}>工作信息更新于 {workItemTime(item.updatedAt)}</time></p>
    </WorkItemSection>
    <WorkItemSection id="work-item-operations" title="可用操作">
      {item.status === 'ARCHIVED' && <p>这项工作已归档。</p>}
      <div className="work-item-operations">
        <WorkItemEdit item={item} scope={scope} disabled={!current} onRefresh={onRefresh} />
        <WorkItemArchive item={item} scope={scope} disabled={!current} onRefresh={onRefresh} />
      </div>
    </WorkItemSection>
  </div>;
}

function WorkItemProgress({ detail, snapshot }: { detail: WorkItemDetail; snapshot: WorkItemLifecycleSnapshot | null }) {
  const id = detail.workItem.workItemId;
  const sessions = new Map(detail.sessions.map(session => [session.sessionId, session]));
  const tracks = snapshot?.tracks.flatMap(track => {
    const session = sessions.get(track.sessionId);
    return session ? [{ session, track }] : [];
  }) ?? [];
  return <WorkItemSection id="work-item-progress" title="任务与进度">
    {snapshot && !tracks.length && <p>暂无关联会话进展。 <a href={workItemHref(id, 'conversations')}>查看关联对话</a></p>}
    {!!tracks.length && <ul className="work-item-items work-item-tracks">
      {tracks.map(({ session, track }) => {
        const title = localizedSessionTitle(session.title);
        const path = `#/sessions/${encodeURIComponent(session.sessionId)}/workbench`;
        const next = workItemNextStep(track.reason);
        return <li key={track.sessionId}>
          <div>
            <strong>{title}</strong>
            {next && <span className="work-item-meta">{next}</span>}
            {track.publications.length > 0 && <span className="work-item-meta">
              成果发布 · {track.publications.map(({ state }) => publicationLabels[state]).join('、')}
            </span>}
            <div className="work-item-actions">
              <a href={sessionFromWorkItem(`${path}/tasks`, id)} aria-label={`查看任务与运行 · ${title}`}>查看任务与运行</a>
              {!session.archivedAt && <a href={sessionFromWorkItem(`${path}/parameters`, id)} aria-label={`配置参数 · ${title}`}>配置参数</a>}
            </div>
          </div>
          <span className="work-item-state" data-state={track.state}>{workItemStateLabel[track.state]}</span>
        </li>;
      })}
    </ul>}
  </WorkItemSection>;
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

type Observation = { snapshot: WorkItemLifecycleSnapshot | null; error: unknown; epoch: number; generation: number; fresh: boolean };

export function WorkItemDetails({ id, section, project, subject, scope, roles, dataEnabled, knowledgeEnabled,
  onSessionsChange, onInspectResult, inspectedResult, onAccessLost }: {
  id: string;
  section: Section;
  project: string;
  subject: string;
  scope: string;
  roles?: readonly string[];
  dataEnabled: boolean;
  knowledgeEnabled: boolean;
  onSessionsChange: () => void;
  onInspectResult?: (reference: ResourceReference) => void;
  inspectedResult?: ResourceReference;
  onAccessLost?: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const unavailableHeading = useRef<HTMLHeadingElement>(null);
  const focusedSection = useRef(section);
  const [attempt, setAttempt] = useState(0);
  const [observed, setObserved] = useState<Observation>({ snapshot: null, error: null, epoch: 0, generation: 0, fresh: false });
  const [inspector, setInspector] = useState<WorkItemResultInspectorSource>();
  const [body, setBody] = useState<{
    detail: WorkItemDetail | null; error: unknown; epoch: number; snapshot: WorkItemLifecycleSnapshot | null;
  }>({ detail: null, error: null, epoch: 0, snapshot: null });
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
      generation: previous.generation + 1,
      epoch: previous.snapshot !== null && previous.snapshot.eventCursor > next.eventCursor ? previous.epoch + 1 : previous.epoch,
    })),
    onError: error => setObserved(previous => ({ ...previous, error, fresh: false })),
  }), [id, project, attempt]);

  useEffect(() => {
    const controller = new AbortController();
    void readWorkItem(id, { project, subject }, controller.signal).then(
      detail => { if (!controller.signal.aborted) setBody({ detail, error: null, epoch, snapshot }); },
      error => {
        if (!controller.signal.aborted) {
          setBody(previous => ({ ...previous, detail: workItemAccessLost(error) || previous.epoch !== epoch ? null : previous.detail, error, epoch }));
        }
      },
    );
    return () => controller.abort();
  }, [id, project, subject, attempt, snapshot, epoch]);

  const unavailable = workItemAccessLost(observed.error) || workItemAccessLost(body.error);
  const detail = body.epoch === epoch && !unavailable ? body.detail : null;
  const hasDetail = !!detail;
  const issue = workItemAccessLost(observed.error) ? observed.error : body.error ?? observed.error;
  useEffect(() => {
    if (unavailable) onAccessLost?.();
  }, [unavailable, onAccessLost]);
  useEffect(() => {
    if (hasDetail && focusedSection.current !== section) {
      focusedSection.current = section;
      panel.current?.focus({ preventScroll: true });
    }
    if (unavailable) {
      // Wait until the inspector releases its modal background.
      const frame = requestAnimationFrame(() => unavailableHeading.current?.focus());
      return () => cancelAnimationFrame(frame);
    }
  }, [section, hasDetail, unavailable]);

  if (!detail) return <>
    <h1 id="work-item-heading" ref={unavailableHeading} tabIndex={-1}>业务工作详情</h1>
    <WorkItemIssue error={issue} retry={refresh} />
    {!issue && <p role="status">正在读取工作详情…</p>}
  </>;

  const item = detail.workItem;
  const current = observed.fresh && !!snapshot && item.revision === snapshot.revision && !body.error;
  const status = item.status === 'ARCHIVED' ? 'ARCHIVED' : snapshot?.state;
  const blocked = !!body.error && (section === 'materials' || section === 'results');
  const activeInspector = inspector && inspectedResult && sameDataIdentity(inspector.reference.dataRef, inspectedResult)
    ? inspector : undefined;
  return <>
    <div className="work-item-title work-item-detail-title">
      <div>
        <h1 id="work-item-heading">{item.title}</h1>
        {item.description && <p>{item.description}</p>}
      </div>
      <span className="work-item-state" data-state={status}>{status ? workItemStateLabel[status] : '状态待核对'}</span>
    </div>
    <nav className="work-item-section-nav" aria-label="工作分区">
      {sectionIds.map(page => <a key={page} href={workItemHref(id, page)} aria-current={section === page ? 'page' : undefined}>
        {sections[page][0]}
      </a>)}
    </nav>
    <WorkItemIssue error={issue} retry={refresh} />
    {!current && <p className="work-item-meta" role="status">
      {snapshot ? '显示最近一次核对的状态，等待更新。' : issue ? '进展暂不可用。' : '正在核对进展…'}
    </p>}
    <div className="work-item-detail-panel" role="group" aria-labelledby={blocked ? undefined : sections[section][1]}
      aria-label={blocked ? sections[section][0] : undefined} ref={panel} tabIndex={-1}>
      {section === 'overview' && <WorkItemOverview detail={detail} scope={scope} snapshot={snapshot} current={current} onRefresh={refresh} />}
      {section === 'conversations' && (
        <WorkItemSection id="work-item-sessions" title="关联会话">
          <WorkItemSessions detail={detail} scope={scope} current={current}
            onRefresh={refresh} onSessionsChange={onSessionsChange} />
        </WorkItemSection>
      )}
      {section === 'materials' && !blocked && <WorkItemMaterials detail={detail} scope={scope} current={current}
        dataEnabled={dataEnabled} knowledgeEnabled={knowledgeEnabled} onRefresh={refresh} />}
      {section === 'tasks' && <WorkItemProgress detail={detail} snapshot={snapshot} />}
      {section === 'results' && !blocked && <WorkItemResults detail={detail} snapshot={snapshot} scope={scope} roles={roles}
        current={current && snapshot === body.snapshot} recheck={observed.generation} onRefresh={refresh}
        inspector={activeInspector} onInspectorAccessLost={onAccessLost}
        onInspectResult={dataEnabled && onInspectResult ? source => {
          setInspector(source);
          onInspectResult(source.reference.dataRef);
        } : undefined} />}
      {section === 'activity' && <WorkItemActivity key={epoch} id={id} project={project} cursor={snapshot?.eventCursor} />}
    </div>
  </>;
}
