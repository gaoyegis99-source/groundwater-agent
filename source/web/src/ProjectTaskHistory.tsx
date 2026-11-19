import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { decodeResults } from './api-contract';
import { ArgumentValue, argumentLabel, taskModelName } from './card-presentation';
import { statusLabel } from './chat-state';
import { ConfirmationCard, ResultCard } from './ChatCards';
import { Icon } from './Icon';
import { ResourceLink } from './ResourceLink';
import { useVerifiedOutputs } from './TurnTimeline';
import { selectSessionUi, type ResultsRead } from './ui-selectors';
import type { History, ResourceReference } from './types';
import type { components } from './generated/public-api-v12';

type Item = components['schemas']['kernel__TaskHistoryItem'];
type OpenResource = (id: string, reference?: ResourceReference) => void;
const states: Record<string, string> = { ...statusLabel, PUBLISHING: '正在发布', PUBLISHED: '已发布', PARTIAL: '部分成果已发布', BLOCKED: '待补资料或参数', READY: '已就绪' };
const title = (item: Item) => item.modelId ? taskModelName(item.modelId) : item.title || '分析任务';
const identity = (item: Item) => item.sessionId + '/' + item.goalRevision;

function TaskHistoryDetail({ item, project, onResource }: { item: Item; project: string; onResource: OpenResource }) {
  const [history, setHistory] = useState<History>();
  const [results, setResults] = useState<ResultsRead>({ status: 'not-requested' });
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    const base = '/sessions/' + encodeURIComponent(item.sessionId);
    const query = '?goalRevision=' + item.goalRevision;
    async function read() {
      const snapshot = await callApi('kernel_get_messages', base + '/messages' + query, undefined, controller.signal) as History;
      const turn = snapshot.turns.find(turn => turn.goalRevision === item.goalRevision);
      if (snapshot.sessionId !== item.sessionId || !turn) throw new ApiError(404, 'TASK_HISTORY_UNAVAILABLE');
      if (controller.signal.aborted) return;
      setHistory(snapshot);
      if (!turn.executionTasks.some(run => run.outputCount > 0)) return;
      setResults({ status: 'loading' });
      const page = await callApi('kernel_get_session_results', base + '/results' + query, undefined, controller.signal, decodeResults);
      if (!controller.signal.aborted) setResults({ status: 'loaded', items: page.items.filter(result =>
        result.agentRunId === turn.agentRunId && result.goalRevision === turn.goalRevision) });
    }
    void read().catch(() => {
      if (!controller.signal.aborted) { setFailed(true); setResults({ status: 'failed' }); }
    });
    return () => controller.abort();
  }, [item.sessionId, item.goalRevision, retry]);
  const input = useMemo(() => ({ sessionId: item.sessionId, history, results, freshness: 'unverified' as const, canWrite: false }), [item.sessionId, history, results]);
  const projection = selectSessionUi(input);
  const refs = projection.turns.flatMap(turn => turn.workflowRuns.flatMap(run => run.outputs));
  const check = useVerifiedOutputs(item.sessionId, results, refs);
  const view = selectSessionUi({ ...input, verifiedOutputs: check.verified });
  const turn = history?.turns.find(turn => turn.goalRevision === item.goalRevision);
  const confirmation = turn?.confirmations.at(-1);
  const preparation = history?.preparation?.agentRunId === turn?.agentRunId ? history?.preparation : null;
  return <article className="history-detail">
    <h3>{title(item)}</h3>
    {item.title && item.title !== title(item) && <p>{item.title}</p>}
    {failed && <p role="alert">任务详情暂时无法完整读取。<button onClick={() => setRetry(n => n + 1)}>重试</button></p>}
    {!history && !failed && <p role="status">正在读取任务详情…</p>}
    {turn?.executionTasks.filter(run => run.isLatestAttempt !== false).map(run => <ResultCard key={run.workflowRunId} idPrefix="history-result"
      task={run} presentation={view.turns.find(t => t.goalRevision === turn.goalRevision)?.workflowRuns.find(r => r.workflowRunId === run.workflowRunId)}
      project={project} onResource={ref => onResource(ref.versionId, ref)} verificationErrors={check.errors} retryVerification={check.retry} />)}
    {confirmation && <ConfirmationCard confirmation={confirmation} history={history ?? null} busy readOnly onDecision={() => {}} project={project} onResource={onResource} />}
    {!confirmation && preparation?.entries.map(entry => <section key={entry.targetId}>
      <h4>资料与参数</h4>
      {Object.entries(entry.inputs).map(([name, ref]) => <ResourceLink key={name} project={project} id={ref.versionId} reference={ref} onResource={onResource} />)}
      <dl>{Object.entries(entry.parameters).map(([name, value]) => <div key={name}><dt>{argumentLabel(name, entry.targetId)}</dt><dd><ArgumentValue modelId={entry.targetId} name={name} value={value} /></dd></div>)}</dl>
    </section>)}
    {!confirmation && !preparation && !turn?.executionTasks.length && turn && <p>{turn.reply?.conclusion?.markdown || '这项任务尚未形成执行批次。'}</p>}
  </article>;
}

export function ProjectTaskHistory({ project, obscured, onClose, onResource }: {
  project: string; obscured: boolean; onClose: () => void; onResource: OpenResource;
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState(false);
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<Item>();
  const close = useRef<HTMLButtonElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const origin = useRef<HTMLButtonElement | null>(null);
  useEffect(() => { close.current?.focus(); }, []);
  useEffect(() => {
    if (selected) back.current?.focus();
    else origin.current?.focus();
  }, [selected]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailure(false);
    void callApi('kernel_list_task_history', '/sessions/task-history' + (before ? '?cursor=' + before : ''), undefined, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        setItems(previous => [...new Map([...(before ? previous : []), ...page.items].map(item => [identity(item), item])).values()]);
        setCursor(page.nextCursor);
      }).catch(() => { if (!controller.signal.aborted) setFailure(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [project, before, retry]);
  return <section className="project-task-history" id="project-task-history"
    aria-label="项目任务历史" role="complementary" hidden={obscured}
    onKeyDown={event => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
    }}>
    <header><h2>任务历史</h2><button type="button" className="icon-button" ref={close} onClick={onClose} aria-label="收起任务历史"><Icon name="close" /></button></header>
    {selected && <><button type="button" ref={back} className="text-button" onClick={() => setSelected(undefined)}>返回任务列表</button>
      <TaskHistoryDetail key={identity(selected)} item={selected} project={project} onResource={onResource} /></>}
    <div hidden={!!selected}>
      <ul>{items.map(item => <li key={identity(item)}><button onClick={event => { origin.current = event.currentTarget; setSelected(item); }}>
        <strong>{title(item)}</strong><span>{states[item.status] ?? '状态待核对'} · <time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}</time></span>
      </button></li>)}</ul>
      {!loading && !failure && !items.length && <p>当前项目暂无分析任务。</p>}
      {loading && <p role="status">正在读取任务…</p>}
      {failure ? <button onClick={() => setRetry(n => n + 1)}>重新读取任务列表</button> : cursor && <button disabled={loading} onClick={() => setBefore(cursor)}>更早任务</button>}
    </div>
  </section>;
}
