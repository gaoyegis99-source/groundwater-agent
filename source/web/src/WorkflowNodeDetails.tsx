import { useEffect, useMemo, useState } from 'react';
import { ApiError, callApi } from './api';
import { selectWorkflowNodeStatus } from './ui-selectors';
import { ErrorNotice } from './ErrorNotice';
import type { components } from './generated/public-api-v12';

type Diagnostics = components['schemas']['kernel__WorkflowNodeDiagnosticsResponse'];
type Node = Diagnostics['nodes'][number];
type ReadState = { kind: 'loading' } | { kind: 'ready'; data: Diagnostics } | { kind: 'error'; error: ApiError };
type ScopedRead = { runId: string; project: string; state: ReadState };

type Graph = components['schemas']['kernel__WorkflowGraphResponse'];

function matchesDiagnostics(graph: Graph, nodes: Node[]) {
  const graphIds = new Set(graph.nodes.map(node => node.nodeId));
  const diagnosticIds = new Set(nodes.map(node => node.nodeId));
  return graphIds.size === graph.nodes.length && diagnosticIds.size === nodes.length
    && graphIds.size === diagnosticIds.size && nodes.every(node => graphIds.has(node.nodeId));
}

function graphLayout(graph: Graph) {
  const positions = new Map<string, { x: number; y: number }>();
  const remaining = [...graph.nodes];
  let column = 0;
  let rows = 1;
  while (remaining.length) {
    const ready = remaining.filter(node => node.dependsOn.every(parent => positions.has(parent)));
    if (!ready.length) return null;
    ready.forEach((node, row) => {
      positions.set(node.nodeId, { x: 12 + column * 188, y: 12 + row * 58 });
      remaining.splice(remaining.indexOf(node), 1);
    });
    rows = Math.max(rows, ready.length);
    column += 1;
  }
  return { positions, width: column * 188 - 22, height: rows * 58 + 8 };
}

function RunDependencyGraph({ graph, nodes }: { graph: Graph; nodes: Node[] }) {
  const layout = useMemo(() => graphLayout(graph), [graph]);
  const [selection, setSelection] = useState<string | null>(null);
  const byId = new Map(nodes.map(node => [node.nodeId, node]));
  if (!layout) return <dl>{nodes.map(node => <NodeDetails key={node.nodeId} node={node} />)}</dl>;
  const selected = selection ? byId.get(selection) : undefined;
  return <>
    <div className="run-graph-scroll" role="region" aria-label="工作流节点依赖图" tabIndex={0}>
      <div className="run-graph" style={{ width: layout.width, height: layout.height }}>
        {graph.nodes.flatMap(node => node.dependsOn.map(parent => {
          const start = layout.positions.get(parent)!;
          const end = layout.positions.get(node.nodeId)!;
          const dx = end.x - start.x - 142;
          const dy = end.y - start.y;
          return <span key={`${parent}:${node.nodeId}`} className="run-graph-edge" aria-hidden="true"
            style={{ left: start.x + 142, top: start.y + 21, width: Math.hypot(dx, dy), transform: `rotate(${Math.atan2(dy, dx)}rad)` }} />;
        }))}
        {graph.nodes.map(node => <button key={node.nodeId} type="button" className="run-graph-node" aria-pressed={selection === node.nodeId}
          style={{ left: layout.positions.get(node.nodeId)!.x, top: layout.positions.get(node.nodeId)!.y }}
          onClick={() => setSelection(node.nodeId)}>
          <strong>{node.nodeId}</strong><small>{selectWorkflowNodeStatus(byId.get(node.nodeId)?.status ?? null).label}</small>
          <span className="sr-only">{node.dependsOn.length ? `上游：${node.dependsOn.join('、')}` : '无上游节点'}</span>
        </button>)}
      </div>
    </div>
    {selected && <dl><NodeDetails node={selected} /></dl>}
  </>;
}

function NodeDetails({ node }: { node: Node }) {
  return <div>
    <dt>{node.nodeId} · {selectWorkflowNodeStatus(node.status).label}</dt>
    <dd>{node.operatorId} · {node.runtime}{node.attemptCount !== null && ` · 尝试 ${node.attemptCount} 次`}</dd>
    {(node.startedAt || node.finishedAt) && <dd>{formatTime(node.startedAt)} → {formatTime(node.finishedAt)}</dd>}
    {node.errorCode && <dd><code>{node.errorCode}</code></dd>}
  </div>;
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—';
}

export function WorkflowNodeDetails({ runId, project, status }: { runId: string; project: string; status: string }) {
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState(0);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [read, setRead] = useState<ScopedRead | null>(null);
  const state: ReadState = read?.runId === runId && read.project === project ? read.state : { kind: 'loading' };

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
        setRead({ runId, project, state: { kind: 'ready', data } });
        if (status === 'QUEUED' || status === 'RUNNING') timer = setTimeout(() => void load(), 5000);
      } catch (error) {
        if (controller.signal.aborted) return;
        setRead({ runId, project, state: { kind: 'error', error: error instanceof ApiError ? error : new ApiError(0, 'WORKFLOW_NODE_DIAGNOSTICS_UNAVAILABLE') } });
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [open, project, request, runId, status]);

  const hasMultipleNodes = state.kind === 'ready' && state.data.availability === 'AVAILABLE' && state.data.nodes.length > 1;
  useEffect(() => {
    setGraph(null);
    if (!open || !hasMultipleNodes) return;
    const controller = new AbortController();
    const path = `/workflow-runs/${encodeURIComponent(runId)}/graph?${new URLSearchParams({ projectId: project })}`;
    void callApi('kernel_get_workflow_run_graph', path, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted && data.workflowRunId === runId && data.availability === 'AVAILABLE') setGraph(data);
    }).catch(error => {
      if (!controller.signal.aborted && !(error instanceof ApiError)) throw error;
    });
    return () => controller.abort();
  }, [open, hasMultipleNodes, runId, project, request]);

  function reload() {
    setRead(null);
    setRequest(value => value + 1);
  }

  return <details className="workflow-node-details" onToggle={event => {
    setOpen(event.currentTarget.open);
    if (!event.currentTarget.open) setRead(null);
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
      {state.data.availability === 'AVAILABLE' && (state.data.nodes.length === 0 ? <p>暂无节点记录。</p>
        : graph && graph.workflowRunId === runId && graph.nodes.length > 1 &&
          matchesDiagnostics(graph, state.data.nodes)
          ? <RunDependencyGraph key={runId} graph={graph} nodes={state.data.nodes} />
          : <dl>{state.data.nodes.map(node => <NodeDetails key={node.nodeId} node={node} />)}</dl>)}
      <button type="button" onClick={reload}>刷新节点状态</button>
    </>}
  </details>;
}
