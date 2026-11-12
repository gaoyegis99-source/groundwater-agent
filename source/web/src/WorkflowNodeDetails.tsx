import { useEffect, useState } from 'react';
import { ApiError, callApi } from './api';
import { ErrorNotice } from './ErrorNotice';
import type { components } from './generated/public-api-v12';

type Diagnostics = components['schemas']['kernel__WorkflowNodeDiagnosticsResponse'];
type Node = Diagnostics['nodes'][number];
type ReadState = { kind: 'loading' } | { kind: 'ready'; data: Diagnostics } | { kind: 'error'; error: ApiError };

const nodeLabels: Record<Node['status'], string> = {
  WAITING: '等待上游', READY: '等待调度', LEASED: '已领取', RUNNING: '正在运行',
  SUCCEEDED: '节点已完成', FAILED: '节点失败', CANCELLED: '节点已取消', SKIPPED: '节点已跳过',
};

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—';
}

export function WorkflowNodeDetails({ runId, project, status }: { runId: string; project: string; status: string }) {
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState(0);
  const [state, setState] = useState<ReadState>({ kind: 'loading' });

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const path = `/workflow-runs/${encodeURIComponent(runId)}/nodes?${new URLSearchParams({ projectId: project })}`;
    async function load() {
      try {
        const data = await callApi('kernel_get_workflow_run_nodes', path, undefined, controller.signal);
        if (controller.signal.aborted) return;
        if (data.workflowRunId !== runId) throw new ApiError(200, 'API_RESPONSE_INVALID');
        setState({ kind: 'ready', data });
        if (status === 'QUEUED' || status === 'RUNNING') timer = setTimeout(() => void load(), 5000);
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', error: error instanceof ApiError ? error : new ApiError(0, 'WORKFLOW_NODE_DIAGNOSTICS_UNAVAILABLE') });
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [open, project, request, runId, status]);

  function reload() {
    setState({ kind: 'loading' });
    setRequest(value => value + 1);
  }

  return <details className="workflow-node-details" onToggle={event => {
    setOpen(event.currentTarget.open);
    if (!event.currentTarget.open) setState({ kind: 'loading' });
  }}>
    <summary>节点详情</summary>
    {open && state.kind === 'loading' && <p role="status">正在读取节点状态…</p>}
    {open && state.kind === 'error' && <>
      <ErrorNotice error={state.error} />
      <button type="button" onClick={reload}>重新加载</button>
    </>}
    {open && state.kind === 'ready' && <>
      {state.data.availability === 'NOT_DISPATCHED' && <p>这次运行尚无 Executor 作业记录。</p>}
      {state.data.availability === 'UNAVAILABLE' && <p>这次运行的节点记录不可用。</p>}
      {state.data.availability === 'AVAILABLE' && (state.data.nodes.length === 0 ? <p>暂无节点记录。</p> : <dl>
        {state.data.nodes.map(node => <div key={node.nodeId}>
          <dt>{node.nodeId} · {nodeLabels[node.status]}</dt>
          <dd>{node.operatorId} · {node.runtime}{node.attemptCount !== null && ` · 尝试 ${node.attemptCount} 次`}</dd>
          {(node.startedAt || node.finishedAt) && <dd>{formatTime(node.startedAt)} → {formatTime(node.finishedAt)}</dd>}
          {node.errorCode && <dd><code>{node.errorCode}</code></dd>}
        </div>)}
      </dl>)}
      <button type="button" onClick={reload}>刷新节点状态</button>
    </>}
  </details>;
}
