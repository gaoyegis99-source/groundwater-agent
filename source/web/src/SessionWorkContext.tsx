import { useEffect, useState } from 'react';
import { argumentLabel, modelName, outputName } from './card-presentation';
import { localizedSessionTitle } from './SessionNavigation';
import { WorkItemIssue } from './WorkItemForms';
import { readWorkItemSessionContext } from './work-item-api';
import { workItemTime } from './work-item-presentation';
import { sessionFromWorkItem, workItemHref } from './work-item-route';
import type { ResourceReference, WorkItemSessionContext } from './types';
import './work-item-workspace.css';

type Read =
  | { status: 'loading' }
  | { status: 'error'; error: unknown }
  | { status: 'loaded'; context: WorkItemSessionContext | null };

const objectLabels = { WELL: '监测井', REGION: '区域', AQUIFER: '含水层' };

function ContextSources({ context, onOpenData }: {
  context: WorkItemSessionContext;
  onOpenData: (reference: ResourceReference) => void;
}) {
  const workItemId = context.workItem.workItemId;
  const source = (sessionId: string) => sessionFromWorkItem(`#/sessions/${encodeURIComponent(sessionId)}`, workItemId);
  return <>
    {context.objects.length > 0 && <div>
      <h4>业务对象</h4>
      <ul className="work-context-sources">
        {context.objects.map(link => {
          const reference = link.state === 'AVAILABLE' ? link.reference : null;
          return reference && <li key={link.linkId}>
            <span>{objectLabels[reference.objectKind]} · {reference.externalId}</span>
            <small>来自此工作 · <time dateTime={link.linkedAt}>{workItemTime(link.linkedAt)}</time></small>
            <details><summary>资料版本</summary>版本 {reference.source.versionId} · 语义 {reference.source.semanticId}</details>
          </li>;
        })}
      </ul>
    </div>}
    {context.assets.length > 0 && <div>
      <h4>关联资料与成果</h4>
      <ul className="work-context-sources">
        {context.assets.map(link => {
          const reference = link.state === 'AVAILABLE' ? link.reference : null;
          if (!reference || reference.kind === 'WORKFLOW') return null;
          return <li key={link.linkId}>
            {reference.kind === 'KNOWLEDGE' ? <span>知识资料</span> : <button type="button"
              onClick={() => onOpenData(reference.dataRef)}>
              {reference.kind === 'RESULT' ? outputName(reference.outputName) : `打开 ${reference.dataRef.format.toUpperCase()} 资料`}
            </button>}
            <small>关联于 <time dateTime={link.linkedAt}>{workItemTime(link.linkedAt)}</time></small>
            <details><summary>来源与版本</summary>{reference.kind === 'KNOWLEDGE'
              ? <>文档 {reference.documentId} · 版本 {reference.versionId}</>
              : <>版本 {reference.dataRef.versionId} · 语义 {reference.dataRef.semanticId}
                {reference.kind === 'RESULT' && <> · 发布 {reference.publicationId}</>}</>}</details>
          </li>;
        })}
      </ul>
    </div>}
    {context.openTasks.length > 0 && <div>
      <h4>待继续的任务</h4>
      <ul className="work-context-sources">
        {context.openTasks.map(task => <li key={task.taskId}>
          <a href={source(task.sessionId)}>{localizedSessionTitle(task.sessionTitle)}</a>
          <small>任务修订 {task.revision} · <time dateTime={task.updatedAt}>{workItemTime(task.updatedAt)}</time></small>
        </li>)}
      </ul>
    </div>}
    {context.approvedActions.length > 0 && <div>
      <h4>已确认的方案</h4>
      <ul className="work-context-sources">
        {context.approvedActions.map(action => <li key={`${action.sessionId}/${action.eventId}/${action.position}`}>
          <strong>{modelName(action.modelId)}</strong>
          <span>{action.label}</span>
          <small><a href={source(action.sessionId)}>{localizedSessionTitle(action.sessionTitle)}</a> · 方案修订 {action.decisionRevision}
            {' · '}方法版本 {action.modelVersion} · <time dateTime={action.approvedAt}>{workItemTime(action.approvedAt)}</time></small>
          {Object.keys(action.parameters).length > 0 && <dl className="work-context-parameters">
            {Object.entries(action.parameters).map(([key, value]) => <div key={key}>
              <dt>{argumentLabel(key, action.modelId)}</dt>
              <dd>{typeof value === 'boolean' ? value ? '是' : '否' : value}</dd>
            </div>)}
          </dl>}
        </li>)}
      </ul>
    </div>}
  </>;
}

export function SessionWorkContext({ sessionId, project, subject, openOnEntry, onOpenData }: {
  sessionId: string;
  project: string;
  subject: string;
  openOnEntry: boolean;
  onOpenData: (reference: ResourceReference) => void;
}) {
  const [open, setOpen] = useState(openOnEntry);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Read>({ status: 'loading' });
  function refresh() { setAttempt(value => value + 1); }
  useEffect(() => {
    if (!open) return;
    let pending: AbortController | undefined;
    function load() {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      setState({ status: 'loading' });
      void readWorkItemSessionContext(sessionId, { project, subject }, controller.signal).then(
        context => { if (!controller.signal.aborted) setState({ status: 'loaded', context }); },
        error => { if (!controller.signal.aborted) setState({ status: 'error', error }); },
      );
    }
    load();
    const visible = () => { if (document.visibilityState === 'visible') load(); };
    window.addEventListener('focus', load);
    document.addEventListener('visibilitychange', visible);
    return () => {
      pending?.abort();
      window.removeEventListener('focus', load);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [open, sessionId, project, subject, attempt]);

  const context = state.status === 'loaded' ? state.context : null;
  return <section className="session-work-context" aria-label="工作背景">
    <div className="work-context-bar">
      <button type="button" className="text-button" aria-expanded={open} aria-controls={open ? 'work-context-content' : undefined}
        onClick={() => { if (!open) setState({ status: 'loading' }); setOpen(value => !value); }}>工作背景</button>
      {open && context && <a href={workItemHref(context.workItem.workItemId)}>返回业务工作</a>}
    </div>
    {open && <div className="work-context-content" id="work-context-content" aria-busy={state.status === 'loading'}>
      {state.status === 'loading' && <p role="status">正在核对工作背景…</p>}
      {state.status === 'error' && <WorkItemIssue error={state.error} retry={refresh} />}
      {state.status === 'loaded' && !context && <p>此会话未关联业务工作。</p>}
      {context && <>
        <h3>{context.workItem.title}</h3>
        {context.workItem.description && <p>{context.workItem.description}</p>}
        <p className="work-item-meta">工作修订 {context.workItem.revision} · 更新于 <time dateTime={context.workItem.updatedAt}>{workItemTime(context.workItem.updatedAt)}</time></p>
        {context.workItem.status === 'ARCHIVED' ? <p>此业务工作已归档。</p> : <ContextSources context={context} onOpenData={onOpenData} />}
      </>}
    </div>}
  </section>;
}
