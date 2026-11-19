import { useRef } from 'react';
import type { ApiError } from './api';
import { Icon } from './Icon';
import { SessionRenameDialog } from './SessionRenameDialog';
import type { SessionSummary } from './types';
import { selectSessionUi } from './ui-selectors';
import { Loading } from './ui/Feedback';
import './session-navigation.css';

export function localizedSessionTitle(title: string | null) {
  return (title || '新的对话')
    .replace(/Dataset:/gi, '数据集:')
    .replace(/Version:/gi, '版本:')
    .replace(/Semantics:/gi, '语义:')
    .replace(/checksum:/gi, '校验和:');
}

const sessionDate = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' });

interface SessionNavigationProps {
  activeId: string;
  recoveredTitle: string | null;
  sessions: readonly SessionSummary[];
  query: string;
  showArchived: boolean;
  cursor: number | null;
  loading: boolean;
  error: ApiError | null;
  notice: string;
  updatingId: string | null;
  canCreate: boolean;
  canWrite: boolean;
  onCreate: () => void;
  onQuery: (value: string) => void;
  onArchiveFilter: (value: boolean) => void;
  onRead: (cursor?: number | null) => void;
  onRename: (sessionId: string, title: string) => Promise<void>;
  onArchive: (sessionId: string, archived: boolean) => void;
}

export function SessionNavigation(props: SessionNavigationProps) {
  const search = useRef<HTMLInputElement>(null);
  const active = props.sessions.find(item => item.sessionId === props.activeId);
  const activeStatus = active && selectSessionUi({
    sessionId: active.sessionId, metadata: active, freshness: 'current', canWrite: props.canWrite,
  }).session;
  const archiveAction = activeStatus?.actions.includes('unarchive') ? 'unarchive' : 'archive';
  const recoveredTitle = props.activeId && !active ? props.recoveredTitle : null;
  const updating = props.updatingId === props.activeId;

  return <>
    <button aria-label="新建对话" className="new-chat" onClick={props.onCreate} disabled={!props.canCreate}>
      <Icon name="plus" /><span className="new-chat-label">新建对话</span>
    </button>
    <h2 className="sidebar-label">{props.showArchived ? '已归档对话' : '科研会话'}</h2>
    <div className="session-filters">
      <label><span className="sr-only">搜索对话</span><input ref={search} type="search" maxLength={120} value={props.query}
        onChange={event => props.onQuery(event.target.value)} placeholder="搜索对话" /></label>
      <label className="archive-toggle"><input type="checkbox" checked={props.showArchived}
        onChange={event => props.onArchiveFilter(event.target.checked)} />显示归档</label>
    </div>
    <nav className="session-list" data-testid="session-list" aria-label="历史对话" aria-busy={props.loading}>
      {recoveredTitle && <a className="session-link active" aria-current="page" data-testid="recovered-active-session"
        href={`#/sessions/${props.activeId}`} aria-label={recoveredTitle} title={recoveredTitle}>
        <span className="chat-symbol"><Icon name="chat" /></span><span className="session-title">{recoveredTitle}</span>
      </a>}
      {props.sessions.map(item => {
        const title = localizedSessionTitle(item.title);
        const selected = item.sessionId === props.activeId;
        const { session } = selectSessionUi({ sessionId: item.sessionId, metadata: item, freshness: 'current' });
        const archived = session.raw === 'true';
        const date = item.createdAt ? new Date(item.createdAt) : null;
        const created = date && Number.isFinite(date.getTime()) ? date : null;
        const metaId = archived || created ? `session-meta-${item.sessionId}` : undefined;
        return <div className="session-row" key={item.sessionId}>
          <a className={`session-link${selected ? ' active' : ''}${metaId ? ' has-metadata' : ''}`}
            aria-current={selected ? 'page' : undefined} href={`#/sessions/${item.sessionId}`}
            aria-label={title} aria-describedby={metaId} title={title}>
            <span className="chat-symbol"><Icon name="chat" /></span><span className="session-title">{title}</span>
          </a>
          {metaId && <div className="session-row-meta" id={metaId}>
            {created && <time dateTime={created.toISOString()}>创建于 {sessionDate.format(created)}</time>}
            {archived && <small className="session-archive-label">{session.label}</small>}
          </div>}
        </div>;
      })}
      {props.loading && <div className="sidebar-empty session-loading"><Loading>
        {props.sessions.length && props.cursor !== null ? '正在读取更多对话…' : '正在读取对话…'}
      </Loading></div>}
      {!props.loading && !props.error && !props.sessions.length && (!recoveredTitle || props.showArchived || props.query.trim()) && <div className="sidebar-empty">
        <p role={props.query.trim() ? 'status' : undefined}>{props.query.trim() ? '没有匹配的对话，试试其他关键词。' : props.showArchived ? '暂无归档对话' : '暂无对话'}</p>
        {props.query.trim() && <button type="button" className="session-clear-search" onClick={() => { props.onQuery(''); search.current?.focus(); }}>清除搜索</button>}
      </div>}
      {props.error && <div className="session-list-error"><p role="alert">对话列表或操作未完成。</p>
        <button onClick={() => props.onRead()} disabled={props.loading}>重新读取对话</button></div>}
      {props.cursor !== null && <button className="text-button session-more" disabled={props.loading}
        onClick={() => props.onRead(props.cursor)}>更早的对话</button>}
    </nav>
    {active && activeStatus?.actions.includes('rename') && <div className="session-actions">
      <SessionRenameDialog key={active.sessionId} title={active.title ?? ''} disabled={props.updatingId !== null}
        onRename={title => props.onRename(active.sessionId, title)} />
      <button disabled={props.updatingId !== null} onClick={() => props.onArchive(active.sessionId, archiveAction === 'archive')}>
        {updating ? '正在保存…' : archiveAction === 'unarchive' ? '取消归档' : '归档'}
      </button>
    </div>}
    {props.notice && <p className="session-notice" role="status">{props.notice}</p>}
  </>;
}
