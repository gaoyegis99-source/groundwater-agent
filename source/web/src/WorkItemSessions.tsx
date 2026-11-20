import { useCallback, useEffect, useRef, useState } from 'react';
import { callApi, projectQuery } from './api';
import { readJsonCache } from './browser-cache';
import { scopedStorageKey } from './browser-scope';
import { newRequestId } from './message-routing';
import { localizedSessionTitle } from './SessionNavigation';
import { WorkItemIssue } from './WorkItemForms';
import { createWorkItemSession, linkWorkItemSession } from './work-item-api';
import { workItemTime } from './work-item-presentation';
import { sessionFromWorkItem } from './work-item-route';
import { useWorkItemCommand } from './use-work-item-command';
import { useWorkItemPages } from './use-work-item-queries';
import type { components } from './generated/public-api-v12';
import type { SessionSummary, WorkItemChangeCommand, WorkItemDetail, WorkItemSessionAttach } from './types';

type SessionOperation =
  | { kind: 'create'; request: WorkItemChangeCommand }
  | { kind: 'link'; request: WorkItemSessionAttach };

function readPending(key: string, project: string) {
  try {
    return readJsonCache(key, (value): value is SessionOperation => {
      if (!value || typeof value !== 'object') return false;
      const operation = value as Partial<SessionOperation>;
      const request = operation.request;
      return !!request && request.projectId === project
        && Number.isSafeInteger(request.expectedRevision) && request.expectedRevision >= 1
        && typeof request.requestId === 'string' && /^[a-z0-9-]{1,256}$/i.test(request.requestId)
        && (operation.kind === 'create' && Object.keys(request).length === 3
          || operation.kind === 'link' && Object.keys(request).length === 4
            && 'sessionId' in request && typeof request.sessionId === 'string' && /^[a-z0-9-]{1,256}$/i.test(request.sessionId));
    });
  } catch {
    return null;
  }
}

function persistPending(key: string, operation: SessionOperation | null) {
  try {
    if (operation) sessionStorage.setItem(key, JSON.stringify(operation));
    else sessionStorage.removeItem(key);
  } catch {
    // In-memory replay remains available until leaving this page.
  }
}

const sessionKey = (item: SessionSummary) => item.sessionId;

function SessionOptions({ project, query, archived, linked, selected, onSelect, disabled }: {
  project: string;
  query: string;
  archived: boolean;
  linked: ReadonlySet<string>;
  selected: string;
  onSelect: (id: string) => void;
  disabled: boolean;
}) {
  const readPage = useCallback(async (cursor: number | undefined, signal: AbortSignal) => {
    const filters = projectQuery(project, { cursor: cursor ?? null, limit: 15, query, archived: String(archived) });
    const page = await callApi('kernel_list_sessions', `/sessions?${filters}`, undefined, signal, value => {
      const result = value as components['schemas']['kernel__SessionPage'];
      if (result.items.some(item => item.projectId !== project)) throw new TypeError('API_RESPONSE_INVALID');
      return result;
    });
    return { items: page.items, nextAfter: page.nextCursor };
  }, [archived, project, query]);
  const page = useWorkItemPages(readPage, sessionKey);
  const items = page.items.filter(item => !linked.has(item.sessionId));

  return <>
    <WorkItemIssue error={page.error} retry={page.retry} />
    {page.loading && <p role="status">正在查找会话…</p>}
    {!page.loading && !page.error && !items.length && <p>本页没有可选会话。</p>}
    {!!items.length && <fieldset className="work-session-options" disabled={disabled || !!page.error}>
      <legend>选择要关联的会话</legend>
      {items.map(item => <label key={item.sessionId}>
        <input type="radio" name="linked-session" checked={item.sessionId === selected} onChange={() => onSelect(item.sessionId)} />
        <span>{localizedSessionTitle(item.title)}{item.archived ? '（已归档）' : ''}</span>
        {item.createdAt && <small>{workItemTime(item.createdAt)}</small>}
      </label>)}
    </fieldset>}
    {page.nextAfter !== null && <button type="button" disabled={page.loading || !!page.error} onClick={page.more}>加载更多会话</button>}
  </>;
}

function SessionPicker({ project, linked, selected, select, submit, close, disabled }: {
  project: string;
  linked: ReadonlySet<string>;
  selected: string;
  select: (id: string) => void;
  submit: () => void;
  close: () => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [archived, setArchived] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { search.current?.focus(); }, []);

  return <div className="work-session-picker">
    <form className="work-item-actions" onSubmit={event => {
      event.preventDefault();
      select('');
      setQuery(draft.trim());
    }}>
      <label>搜索会话<input ref={search} type="search" value={draft} maxLength={120} onChange={event => setDraft(event.target.value)} /></label>
      <button type="submit">查找</button>
      <label><input type="checkbox" checked={archived} onChange={event => { select(''); setArchived(event.target.checked); }} />已归档会话</label>
    </form>
    <SessionOptions key={`${archived}/${query}`} project={project} query={query} archived={archived}
      linked={linked} selected={selected} onSelect={select} disabled={disabled} />
    <div className="work-item-actions">
      <button type="button" className="primary" disabled={disabled || !selected || linked.has(selected)} onClick={submit}>确认关联</button>
      <button type="button" onClick={close}>关闭会话选择</button>
    </div>
  </div>;
}

export function WorkItemSessions({ detail, scope, current, onRefresh, onSessionsChange }: {
  detail: WorkItemDetail;
  scope: string;
  current: boolean;
  onRefresh: () => void;
  onSessionsChange: () => void;
}) {
  const item = detail.workItem;
  const key = scopedStorageKey(scope, 'work-item-session-command', item.workItemId);
  const [restored] = useState(() => readPending(key, item.projectId));
  const [showPicker, setShowPicker] = useState(false);
  const [selected, setSelected] = useState('');
  const [linkedTitle, setLinkedTitle] = useState('');
  const trigger = useRef<HTMLButtonElement>(null);
  const command = useWorkItemCommand<SessionOperation>(
    (operation, signal) => operation.kind === 'create'
      ? createWorkItemSession(item.workItemId, operation.request, signal)
      : linkWorkItemSession(item.workItemId, operation.request, signal),
    (receipt, operation) => {
      setShowPicker(false);
      setSelected('');
      onRefresh();
      onSessionsChange();
      if (operation.kind === 'create' && receipt.linkId) {
        window.location.hash = sessionFromWorkItem(`#/sessions/${encodeURIComponent(receipt.linkId)}`, item.workItemId);
      } else setLinkedTitle('会话已关联，可以打开继续工作。');
    },
    { restored, remember: value => persistPending(key, value), onRejected: onRefresh },
  );
  const linked = new Set(detail.sessions.map(session => session.sessionId));
  const writable = current && item.status === 'ACTIVE';
  function start(kind: SessionOperation['kind']) {
    if (!writable || command.unsettled) return;
    const request = { projectId: item.projectId, requestId: newRequestId(), expectedRevision: item.revision };
    setLinkedTitle('');
    if (kind === 'create') {
      void command.submit({ kind, request });
    } else if (selected && !linked.has(selected)) {
      void command.submit({ kind, request: { ...request, sessionId: selected } });
    }
  }

  return <>
    {!detail.sessions.length && <p>暂无关联会话。</p>}
    {!!detail.sessions.length && <ul className="work-item-items">
      {detail.sessions.map(session => <li key={session.sessionId}>
        <a href={sessionFromWorkItem(`#/sessions/${encodeURIComponent(session.sessionId)}`, item.workItemId)}>{localizedSessionTitle(session.title)}</a>
        {session.archivedAt && <span>已归档</span>}
      </li>)}
    </ul>}
    {linkedTitle && <p role="status">{linkedTitle}</p>}
    <WorkItemIssue error={command.error} />
    {command.unsettled ? <div className="work-item-actions">
      <span role="status">{command.busy ? '正在保存会话关联…' : '上次会话操作的回执尚未确认。'}</span>
      {!command.busy && <button type="button" onClick={() => {
        if (command.unsettled) void command.submit(command.unsettled);
      }}>重试会话操作</button>}
    </div> : item.status === 'ACTIVE' && <>
      <div className="work-item-actions work-session-actions">
        <button type="button" disabled={!writable} onClick={() => start('create')}>在此工作中新建会话</button>
        <button type="button" ref={trigger} disabled={!writable} aria-expanded={showPicker}
          onClick={() => { command.clearError(); setShowPicker(value => !value); setSelected(''); }}>加入已有会话</button>
      </div>
      {showPicker && <SessionPicker project={item.projectId} linked={linked} selected={selected} select={setSelected}
        submit={() => start('link')} close={() => {
          setShowPicker(false);
          setSelected('');
          requestAnimationFrame(() => trigger.current?.focus());
        }} disabled={!writable} />}
    </>}
  </>;
}
