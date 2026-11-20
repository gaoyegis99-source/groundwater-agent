import { useCallback, useEffect, useState } from 'react';
import { ApiError } from './api';
import { argumentLabel, modelName, outputName } from './card-presentation';
import { localizedSessionTitle } from './SessionNavigation';
import { WorkItemIssue } from './WorkItemForms';
import { readWorkItemSessionContext } from './work-item-api';
import { workItemTime } from './work-item-presentation';
import { sessionFromWorkItem, workItemHref } from './work-item-route';
import type { Confirmation, ResourceReference, TaskPreparationEntry, WorkItemSessionContext } from './types';
import { workflowFileKey } from './workflow-selection';
import './work-item-workspace.css';

type Read =
  | { status: 'loading' }
  | { status: 'error'; error: unknown }
  | { status: 'loaded'; context: WorkItemSessionContext | null };
const loading: Read = { status: 'loading' };

export function useSessionWorkContext(sessionId: string, project: string, subject: string, route: string) {
  const key = JSON.stringify([sessionId, project, subject, route]);
  const [attempt, setAttempt] = useState(0);
  const [read, setRead] = useState<{ key: string; value: Read }>();
  const refresh = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    if (!sessionId || !project || !subject) return;
    let pending: AbortController | undefined;
    function load() {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      setRead({ key, value: loading });
      void readWorkItemSessionContext(sessionId, { project, subject }, controller.signal).then(
        context => { if (!controller.signal.aborted) setRead({ key, value: { status: 'loaded', context } }); },
        error => { if (!controller.signal.aborted) setRead({ key, value: { status: 'error', error } }); },
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
  }, [key, sessionId, project, subject, attempt]);
  return { state: read?.key === key && sessionId ? read.value : loading, refresh };
}

export function SessionWorkScope({ state, id, retry }: { state: Read; id?: string; retry?: () => void }) {
  const work = state.status === 'loaded' ? state.context?.workItem : undefined;
  const code = state.status === 'error' && state.error instanceof ApiError ? state.error.status : undefined;
  const name = work?.title ?? (state.status === 'loading' ? '正在核对工作归属…' : state.status === 'loaded' ? '普通咨询'
    : code === 0 ? '连接中断，工作归属待核对'
      : code === 401 || code === 403 ? '当前身份无法查看工作归属' : '工作归属暂不可用');
  return <div className="session-work-scope">
    <span id={id} aria-live="polite">{work ? `业务工作 · ${name}${work.status === 'ARCHIVED' ? '（已归档）' : ''}` : name}</span>
    {work && <a href={workItemHref(work.workItemId)}>返回业务工作</a>}
    {state.status === 'error' && retry && <button className="text-button" onClick={retry}>重新读取工作归属</button>}
  </div>;
}

const objectLabels = { WELL: '监测井', REGION: '区域', AQUIFER: '含水层' };

type InputEvidence = {
  draftInputs?: ResourceReference[];
  prepared?: TaskPreparationEntry[];
  confirmation?: Confirmation;
};

function inputState(reference: ResourceReference, { draftInputs, prepared, confirmation }: InputEvidence) {
  const key = workflowFileKey(reference);
  const matches = (item: ResourceReference) => workflowFileKey(item) === key;
  if (confirmation && ['PENDING', 'CONFIRMED'].includes(confirmation.status) &&
      confirmation.actions.some(action => Object.values(action.inputReferences ?? {}).some(matches))) {
    return confirmation.status === 'CONFIRMED' ? '确认采用此版本' : '待确认方案使用此版本';
  }
  const checks = prepared?.filter(entry => Object.values(entry.inputs).some(matches)) ?? [];
  if (checks.length) return checks.some(entry => entry.status === 'BLOCKED') ? '输入准备待补充' : '当前准备已校验此版本';
  return draftInputs?.some(matches) ? '已放入本会话草稿' : '已关联到工作';
}

function ContextSources({ context, onOpenData, ...evidence }: InputEvidence & {
  context: WorkItemSessionContext;
  onOpenData: (reference: ResourceReference) => void;
}) {
  const workItemId = context.workItem.workItemId;
  const source = (sessionId: string) => sessionFromWorkItem(`#/sessions/${encodeURIComponent(sessionId)}`, workItemId);
  return <>
    {context.objects.length > 0 && <div>
      <h3>业务对象</h3>
      <ul className="work-context-sources">
        {context.objects.map(link => {
          const reference = link.state === 'AVAILABLE' ? link.reference : null;
          return reference && <li key={link.linkId}>
            <span>{objectLabels[reference.objectKind]} · {reference.externalId}</span>
            <small>业务键来源字段 {reference.fieldId} · <time dateTime={link.linkedAt}>{workItemTime(link.linkedAt)}</time></small>
            <details><summary>资料版本</summary>版本 {reference.source.versionId} · 语义 {reference.source.semanticId}</details>
            <button type="button" onClick={() => onOpenData(reference.source)}>核对对象来源</button>
          </li>;
        })}
      </ul>
    </div>}
    {context.assets.length > 0 && <div>
      <h3>关联资料与成果</h3>
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
            {'dataRef' in reference && <small>{inputState(reference.dataRef, evidence)}</small>}
            <details><summary>来源与版本</summary>{reference.kind === 'KNOWLEDGE'
              ? <>文档 {reference.documentId} · 版本 {reference.versionId}</>
              : <>版本 {reference.dataRef.versionId} · 语义 {reference.dataRef.semanticId}
                {reference.kind === 'RESULT' && <> · 发布 {reference.publicationId}</>}</>}</details>
          </li>;
        })}
      </ul>
    </div>}
    {context.openTasks.length > 0 && <div>
      <h3>待继续的任务</h3>
      <ul className="work-context-sources">
        {context.openTasks.map(task => <li key={task.taskId}>
          <a href={source(task.sessionId)}>{localizedSessionTitle(task.sessionTitle)}</a>
          <small>任务修订 {task.revision} · <time dateTime={task.updatedAt}>{workItemTime(task.updatedAt)}</time></small>
        </li>)}
      </ul>
    </div>}
    {context.approvedActions.length > 0 && <div>
      <h3>已确认的方案</h3>
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

export function SessionWorkContext({ state, refresh, openOnEntry, onOpenData, ...evidence }: InputEvidence & {
  state: Read;
  refresh: () => void;
  openOnEntry: boolean;
  onOpenData: (reference: ResourceReference) => void;
}) {
  const [open, setOpen] = useState(openOnEntry);
  const context = state.status === 'loaded' ? state.context : null;
  if (state.status === 'loaded' && !context) return <div className="session-work-context is-unlinked">
    <span className="sr-only" id="session-work-scope">普通咨询</span>
  </div>;
  return <section className="session-work-context" aria-label="工作背景">
    <div className="work-context-bar">
      <SessionWorkScope state={state} id="session-work-scope" />
      <button type="button" className="text-button" aria-expanded={open} aria-controls={open ? 'work-context-content' : undefined}
        onClick={() => { if (!open) refresh(); setOpen(value => !value); }}>工作背景</button>
    </div>
    {open && <div className="work-context-content" id="work-context-content" aria-busy={state.status === 'loading'}>
      {state.status === 'loading' && <p role="status">正在核对工作背景…</p>}
      {state.status === 'error' && <WorkItemIssue error={state.error} retry={refresh} />}
      {context && <>
        <h2>{context.workItem.title}</h2>
        {context.workItem.description && <p>{context.workItem.description}</p>}
        <p className="work-item-meta">工作修订 {context.workItem.revision} · 更新于 <time dateTime={context.workItem.updatedAt}>{workItemTime(context.workItem.updatedAt)}</time></p>
        {context.workItem.status === 'ARCHIVED' ? <p>此业务工作已归档。</p> : <ContextSources context={context} onOpenData={onOpenData} {...evidence} />}
      </>}
    </div>}
  </section>;
}
