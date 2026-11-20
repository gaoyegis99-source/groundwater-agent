import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Icon } from './Icon';
import { WorkItemDetails } from './WorkItemDetails';
import { WorkItemArchive, WorkItemCreate, WorkItemIssue } from './WorkItemForms';
import { listWorkItems } from './work-item-api';
import { workItemHref, type WorkItemRoute } from './work-item-route';
import { workItemNextStep, workItemStateLabel, workItemTime } from './work-item-presentation';
import { useWorkItemPages, useWorkItemRowStates, workItemAccessLost } from './use-work-item-queries';
import type { WorkItemSummary } from './types';
import './work-item-workspace.css';

type Scope = { project: string; subject: string };
const rowId = (item: WorkItemSummary) => item.workItemId;

function WorkItemRows({ project, subject, includeArchived }: Scope & { includeArchived: boolean }) {
  const readPage = useCallback((after: string | undefined, signal: AbortSignal) =>
    listWorkItems({ project, subject }, { after, includeArchived, signal }), [project, subject, includeArchived]);
  const page = useWorkItemPages(readPage, rowId);
  const statuses = useWorkItemRowStates(page.items, project);

  return <>
    <WorkItemIssue error={page.error} retry={page.retry} />
    {page.loading && <p role="status">正在读取业务工作…</p>}
    {!page.loading && !page.error && !page.items.length && <div className="work-item-empty">
      <p>暂无业务工作。</p>
      <a href="#/work-items/new">新建业务工作</a>
    </div>}
    <ul className="work-item-list" aria-label="业务工作列表" aria-busy={page.loading}>
      {page.items.map(item => {
        const state = statuses.get(item.workItemId);
        if (state?.item === item && workItemAccessLost(state.error)) return null;
        const snapshot = state?.item === item ? state.snapshot : undefined;
        const status = item.status === 'ARCHIVED' ? 'ARCHIVED' : snapshot?.state;
        const next = snapshot && workItemNextStep(snapshot.reason);
        return <li key={item.workItemId}>
          <div className="work-list-name">
            <h2><a href={workItemHref(item.workItemId)}>{item.title}</a></h2>
            {item.description && <p>{item.description}</p>}
          </div>
          <div className="work-list-progress">
            <span className="work-item-state" data-state={status}>{status ? workItemStateLabel[status] : state?.error ? '状态暂不可用' : '正在核对状态'}</span>
            {next && <p>{next}</p>}
          </div>
          <div className="work-list-meta">
            <time dateTime={item.updatedAt}>更新于 {workItemTime(item.updatedAt)}</time>
            <span>{snapshot ? `${snapshot.tracks.length} 个会话` : '会话数待核对'}</span>
          </div>
          <WorkItemArchive item={item} compact disabled={page.loading || !!page.error} onRefresh={page.reload} />
        </li>;
      })}
    </ul>
    <div className="work-item-actions work-item-list-pagination">
      {page.nextAfter !== null && <button type="button" disabled={page.loading || !!page.error} onClick={page.more}>加载更多工作</button>}
      {!!page.items.length && <button type="button" disabled={page.loading} onClick={page.reload}>刷新列表</button>}
    </div>
  </>;
}

function WorkItemIndex(scope: Scope) {
  const [includeArchived, setIncludeArchived] = useState(false);
  return <>
    <div className="work-item-title">
      <h1 id="work-item-heading" tabIndex={-1}>业务工作</h1>
      <a className="button primary" href="#/work-items/new"><Icon name="plus" />新建业务工作</a>
    </div>
    <div className="work-item-list-tools">
      <label><input type="checkbox" checked={includeArchived} onChange={event => setIncludeArchived(event.target.checked)} /> 包含已归档工作</label>
    </div>
    <WorkItemRows key={String(includeArchived)} {...scope} includeArchived={includeArchived} />
  </>;
}

export function WorkItemWorkspace({ route, project, subject, scope, mark, brand, signOut, permitted, identityError, retryIdentity,
  dataEnabled, knowledgeEnabled, onSessionsChange }: Scope & {
  route: WorkItemRoute;
  scope: string;
  mark: string;
  brand: string;
  signOut: ReactNode;
  permitted: boolean;
  identityError: boolean;
  retryIdentity: () => void;
  dataEnabled: boolean;
  knowledgeEnabled: boolean;
  onSessionsChange: () => void;
}) {
  const main = useRef<HTMLElement>(null);
  const routeKey = route.kind === 'detail' ? route.id : route.kind;
  useEffect(() => { main.current?.focus(); }, [routeKey, permitted, scope]);
  return <div className="work-item-shell">
    <header className="work-item-topbar">
      <a href="#/" className="work-item-brand"><img src={mark} width={28} height={28} alt="" />{brand}</a>
      <nav aria-label="工作台导航">
        {permitted && <a href={workItemHref()} aria-current={route.kind === 'list' ? 'page' : undefined}>业务工作</a>}
        <a href="#/">对话首页</a>
        {signOut}
      </nav>
    </header>
    <main className="work-item-main" ref={main} tabIndex={-1} aria-labelledby="work-item-heading">
      {identityError ? <>
        <h1 id="work-item-heading">业务工作</h1>
        <div className="work-item-issue" role="alert">无法核实当前身份。<button type="button" onClick={retryIdentity}>重试连接</button></div>
      </> : !scope ? <><h1 id="work-item-heading">业务工作</h1><p role="status">正在验证身份…</p></>
        : !permitted ? <><h1 id="work-item-heading">业务工作</h1><p role="alert">当前身份无法访问业务工作</p></>
          : route.kind === 'list' ? <WorkItemIndex key={scope} project={project} subject={subject} />
            : <>
              <a className="work-item-back" href={workItemHref()}><Icon name="arrow-left" />工作列表</a>
              {route.kind === 'create'
                ? <><h1 id="work-item-heading">新建业务工作</h1><WorkItemCreate key={scope} project={project} scope={scope} /></>
                : route.kind === 'detail'
                  ? <WorkItemDetails key={`${scope}/${route.id}`} id={route.id} project={project} subject={subject}
                    scope={scope} dataEnabled={dataEnabled} knowledgeEnabled={knowledgeEnabled} onSessionsChange={onSessionsChange} />
                  : <><h1 id="work-item-heading">业务工作</h1><p role="alert">工作链接无效。</p></>}
            </>}
    </main>
  </div>;
}
