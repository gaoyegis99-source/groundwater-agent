import { useEffect, useRef, useState, type FormEvent } from 'react';
import { browserStorageAvailable, readPendingWorkItemCreate, rememberWorkItemCreate } from './work-item-create-cache';
import { newRequestId } from './message-routing';
import { useWorkItemCommand } from './use-work-item-command';
import { archiveWorkItem, createWorkItem, updateWorkItem } from './work-item-api';
import { workItemHref } from './work-item-route';
import { workItemErrorMessage } from './work-item-presentation';
import type { WorkItemChangeCommand, WorkItemCreateCommand, WorkItemSummary, WorkItemUpdateCommand } from './types';

export function WorkItemIssue({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  return <div className="work-item-issue" role="alert">
    <span>{workItemErrorMessage(error)}</span>
    {retry && <button type="button" onClick={retry}>重试</button>}
  </div>;
}

type Draft = { title: string; description: string };

function WorkItemFields({ draft, change, disabled }: { draft: Draft; change: (draft: Draft) => void; disabled: boolean }) {
  return <fieldset className="work-item-fields" disabled={disabled}>
    <label>标题<input required maxLength={120} value={draft.title} onChange={event => change({ ...draft, title: event.target.value })} /></label>
    <label>说明（可选）<textarea rows={4} maxLength={2000} value={draft.description} onChange={event => change({ ...draft, description: event.target.value })} /></label>
  </fieldset>;
}

function usableTitle(title: string) {
  const value = title.trim();
  return !!value && !/[\u0000-\u001f\u007f]/.test(value);
}

export function WorkItemCreate({ scope, project }: { scope: string; project: string }) {
  const [restored] = useState(() => readPendingWorkItemCreate(scope, project));
  const [draft, setDraft] = useState<Draft>(() => ({
    title: restored?.title ?? '', description: restored?.description ?? '',
  }));
  const command = useWorkItemCommand(createWorkItem, receipt => {
    window.location.hash = workItemHref(receipt.workItemId);
  }, { restored, remember: value => rememberWorkItemCreate(scope, value) });
  const [storageAvailable] = useState(browserStorageAvailable);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!usableTitle(draft.title)) return;
    const request: WorkItemCreateCommand = command.unsettled ?? {
      projectId: project, requestId: newRequestId(),
      title: draft.title.trim(), description: draft.description.trim() || null,
    };
    void command.submit(request);
  }

  return <form className="work-item-form" onSubmit={submit}>
    <WorkItemFields draft={draft} change={setDraft} disabled={!!command.unsettled} />
    {command.unsettled && !command.busy && <p role="status">上次提交结果尚未确认，可以安全重试原请求。</p>}
    {!storageAvailable && command.unsettled && !command.busy && <p>请留在本页，直至确认提交结果。</p>}
    <WorkItemIssue error={command.error} />
    <div className="work-item-actions">
      <button className="primary" type="submit" disabled={command.busy || !usableTitle(draft.title)}>
        {command.busy ? '正在提交…' : command.unsettled ? '重试提交' : '创建工作'}
      </button>
      <a href={workItemHref()}>返回工作列表</a>
    </div>
  </form>;
}

export function WorkItemArchive({ item, disabled, onRefresh, compact }: {
  item: WorkItemSummary;
  disabled?: boolean;
  onRefresh: () => void;
  compact?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const command = useWorkItemCommand<WorkItemChangeCommand>(
    (value, signal) => archiveWorkItem(item.workItemId, value, signal),
    () => { setConfirming(false); onRefresh(); },
    { onRejected: onRefresh },
  );
  useEffect(() => { if (confirming) confirmButton.current?.focus(); }, [confirming]);
  if (item.status === 'ARCHIVED' && !command.unsettled) return null;

  return <div className="work-item-command">
    <WorkItemIssue error={command.error} />
    {confirming || command.unsettled ? <div className="work-item-actions">
      <span>{command.unsettled && !command.busy ? '归档结果待确认' : '确认归档这项工作？'}</span>
      <button type="button" ref={confirmButton} disabled={command.busy || (disabled && !command.unsettled)}
        onClick={() => void command.submit(command.unsettled ?? {
          projectId: item.projectId, expectedRevision: item.revision, requestId: newRequestId(),
        })}>{command.busy ? '正在归档…' : command.unsettled ? '重试归档' : '确认归档'}</button>
      {!command.unsettled && <button type="button" onClick={() => {
        setConfirming(false);
        requestAnimationFrame(() => trigger.current?.focus());
      }}>取消</button>}
    </div> : <button type="button" ref={trigger} disabled={disabled} aria-label={compact ? `归档 ${item.title}` : undefined}
      onClick={() => { command.clearError(); setConfirming(true); }}>归档工作</button>}
  </div>;
}

export function WorkItemEdit({ item, disabled, onRefresh }: {
  item: WorkItemSummary;
  disabled?: boolean;
  onRefresh: () => void;
}) {
  const [draft, setDraft] = useState<(Draft & { revision: number }) | null>(null);
  const editor = useRef<HTMLFormElement>(null);
  const command = useWorkItemCommand<WorkItemUpdateCommand>(
    (value, signal) => updateWorkItem(item.workItemId, value, signal),
    () => { setDraft(null); onRefresh(); },
    { onRejected: onRefresh },
  );
  const editing = draft !== null;
  useEffect(() => { if (editing) editor.current?.querySelector('input')?.focus(); }, [editing]);
  if (item.status === 'ARCHIVED' && !command.unsettled) return null;
  if (!draft) return <button type="button" disabled={disabled} onClick={() => {
    command.clearError();
    setDraft({ title: item.title, description: item.description ?? '', revision: item.revision });
  }}>编辑标题和说明</button>;

  const title = draft.title.trim();
  const description = draft.description.trim() || null;
  const changed = title !== item.title || description !== item.description;
  const older = draft.revision !== item.revision;
  return <form ref={editor} className="work-item-form" onSubmit={event => {
    event.preventDefault();
    if (command.unsettled) { void command.submit(command.unsettled); return; }
    if (older || !changed || !usableTitle(title)) return;
    void command.submit({
      projectId: item.projectId, expectedRevision: draft.revision, requestId: newRequestId(), title, description,
    });
  }}>
    <WorkItemFields draft={draft} change={value => setDraft({ ...value, revision: draft.revision })} disabled={!!command.unsettled} />
    <WorkItemIssue error={command.error} />
    {older && !command.unsettled && <p role="status">工作有新修订。请重新打开编辑器核对内容。</p>}
    <div className="work-item-actions">
      <button type="submit" disabled={command.busy || (!command.unsettled && (disabled || older || !changed || !usableTitle(title)))}>
        {command.busy ? '正在保存…' : command.unsettled ? '重试保存' : '保存修改'}
      </button>
      {!command.unsettled && <button type="button" onClick={() => setDraft(null)}>取消编辑</button>}
    </div>
  </form>;
}
