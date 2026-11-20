import { useCallback } from 'react';
import { callApi } from './api';
import { WorkItemIssue } from './WorkItemForms';
import { useWorkItemPages } from './use-work-item-queries';

const revisionKey = (item: { semanticId: string }) => item.semanticId;

export function DataSemanticHistory({ versionId, semanticId, onChange }: {
  versionId: string;
  semanticId: string;
  onChange: (semanticId: string) => void;
}) {
  const readPage = useCallback(async (cursor: string | undefined, signal: AbortSignal) => {
    const params = new URLSearchParams({ limit: '12' });
    if (cursor) params.set('cursor', cursor);
    const page = await callApi('data_list_semantic_revisions',
      `/data/versions/${encodeURIComponent(versionId)}/semantics?${params}`, undefined, signal);
    return { items: page.items, nextAfter: page.nextCursor };
  }, [versionId]);
  const page = useWorkItemPages(readPage, revisionKey);
  return <div className="version-control">
    <label>语义修订<select value={semanticId} disabled={!page.items.length}
      onChange={event => onChange(event.target.value)}>
      {!page.items.some(item => item.semanticId === semanticId) && <option value={semanticId}>{semanticId || '读取中'}</option>}
      {page.items.map(item => <option key={item.semanticId} value={item.semanticId}>
        {new Date(item.createdAt).toLocaleDateString('zh-CN')} · {item.semanticId}
      </option>)}
    </select></label>
    {page.loading && <small role="status">正在读取语义历史…</small>}
    <WorkItemIssue error={page.error} retry={page.retry} />
    {page.nextAfter !== null && !page.error && <button type="button" disabled={page.loading} onClick={page.more}>加载更多语义修订</button>}
  </div>;
}
