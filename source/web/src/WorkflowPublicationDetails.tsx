import { useEffect, useState } from 'react';
import { ApiError, callApi } from './api';
import { ErrorNotice } from './ErrorNotice';
import { selectResourcePublicationUi } from './ui-selectors';
import type { components } from './generated/public-api-v12';

type Publication = components['schemas']['kernel__WorkflowPublicationResponse'];

export function WorkflowPublicationDetails({ runId, project, status }: {
  runId: string; project: string; status: string;
}) {
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState(0);
  const [read, setRead] = useState<Publication | ApiError | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const path = `/workflow-runs/${encodeURIComponent(runId)}/publication?${new URLSearchParams({ projectId: project })}`;
    async function load() {
      try {
        const value = await callApi('kernel_get_workflow_run_publication', path, undefined, controller.signal);
        if (controller.signal.aborted) return;
        if (value.workflowRunId !== runId || (value.publicationId === null) !== (value.state === null)) {
          throw new ApiError(200, 'API_RESPONSE_INVALID');
        }
        setRead(value);
        if (status === 'RUNNING' && !['PUBLISHED', 'FAILED', 'ABORTED'].includes(value.state ?? '')) {
          timer = setTimeout(() => void load(), 3000);
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        if (!(error instanceof ApiError)) throw error;
        setRead(error);
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [open, request, runId, project, status]);

  return <details onToggle={event => {
    setOpen(event.currentTarget.open);
    if (!event.currentTarget.open) setRead(null);
  }}>
    <summary>成果发布详情</summary>
    {open && (read instanceof ApiError ? <ErrorNotice error={read} />
      : !read ? <p role="status">正在读取发布状态…</p>
        : read.state && read.publicationId ? <dl className="argument-list">
          <div><dt>Data 发布状态</dt><dd>{selectResourcePublicationUi({ state: read.state }).label}</dd></div>
          <div><dt>发布 ID</dt><dd><code>{read.publicationId}</code></dd></div>
        </dl> : <p>发布状态未提供</p>)}
    {open && read && <button type="button" onClick={() => { setRead(null); setRequest(value => value + 1); }}>刷新发布状态</button>}
  </details>;
}
