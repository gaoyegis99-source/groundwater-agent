import { useEffect, useState, type ReactNode } from 'react';
import { activityDescription, activityLabel, activityStatusLabel, groupedVisibleActivities, progressDescription, progressLabel, statusLabel, turnPhase } from './chat-state';
import { ErrorNotice } from './ErrorNotice';
import type { TaskContext, Turn } from './types';
import { routingLabel } from './message-routing';
import { TaskFacts } from './TaskCard';
import { Badge } from './ui/Feedback';
import { Icon } from './Icon';

function elapsed(start: string | null, end: string | null, now: number) {
  if (start === null) return null;
  const startMs = Date.parse(start);
  const endMs = end === null ? now : Date.parse(end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null;
  const milliseconds = Math.max(0, endMs - startMs);
  if (milliseconds > 0 && milliseconds < 1000) return '少于 1 秒';
  if (milliseconds < 10_000 && milliseconds % 1000 !== 0) return `${Math.round(milliseconds / 100) / 10} 秒`;
  const seconds = Math.floor(milliseconds / 1000);
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function activityElapsed(activity: Turn['activities'][number], now: number) {
  if (activity.status === 'QUEUED' || (activity.status !== 'RUNNING' && !activity.finishedAt)) return null;
  return elapsed(activity.startedAt, activity.finishedAt, now);
}

const activityClock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
function activityOrder(item: { createdAt: string | null; startedAt: string | null; finishedAt: string | null }) {
  const timestamp = Date.parse(item.startedAt ?? item.createdAt ?? item.finishedAt ?? '');
  return Number.isFinite(timestamp) ? timestamp : Number.MAX_SAFE_INTEGER;
}

function ActivityTime({ createdAt, startedAt, finishedAt, duration }: {
  createdAt: string | null; startedAt: string | null; finishedAt: string | null; duration: string | null;
}) {
  const timestamp = startedAt ?? createdAt ?? finishedAt;
  const date = timestamp ? new Date(timestamp) : null;
  return <div className="activity-time" aria-live="off">
    {date && Number.isFinite(date.getTime()) && <time dateTime={date.toISOString()} title={date.toLocaleString('zh-CN')}>
      {startedAt ? '开始' : createdAt ? '记录' : '结束'} {activityClock.format(date)}
    </time>}
    {duration && <small>用时 <span>{duration}</span></small>}
  </div>;
}

export interface CurrentTaskView { name: string; stage: string; action?: string; run?: () => void; research?: TaskContext }

export function ActivityPanel({ turn, snapshotAt, connected, task, analysisResultCount, children }: {
  turn: Turn; snapshotAt: string; connected: boolean; task?: CurrentTaskView; analysisResultCount?: number | null; children?: ReactNode;
}) {
  const active = ['ROUTING', 'QUEUED', 'RUNNING', 'WAITING_CONFIRMATION', 'WAITING_EXECUTION'].includes(turn.status);
  const needsAttention = ['FAILED', 'TIMED_OUT'].includes(turn.status);
  const disclosureKey = `${turn.agentRunId}:${active ? 'active' : 'terminal'}`;
  const [disclosure, setDisclosure] = useState<{ key: string; expanded: boolean } | null>(null);
  const [showAllFor, setShowAllFor] = useState<string | null>(null);
  const expanded = disclosure?.key === disclosureKey ? disclosure.expanded : needsAttention && !task;
  const showAll = showAllFor === disclosureKey;
  const [now, setNow] = useState(() => Date.parse(snapshotAt));
  const contentId = `activity-content-${turn.agentRunId}`;
  useEffect(() => {
    const serverNow = Date.parse(snapshotAt);
    const received = performance.now();
    setNow(serverNow);
    if (!active || !connected) return;
    const timer = setInterval(() => setNow(serverNow + performance.now() - received), 1000);
    return () => clearInterval(timer);
  }, [snapshotAt, active, connected]);
  const confirmation = turn.confirmations.at(-1);
  const waiting = confirmation?.createdAt && (confirmation.decisionRequestedAt || (confirmation.status === 'PENDING' && turn.status === 'WAITING_CONFIRMATION'))
    ? elapsed(confirmation.createdAt, confirmation.decisionRequestedAt ?? null, now) : null;
  const duration = !confirmation && (active || turn.timing.finishedAt) ? elapsed(turn.timing.startedAt, turn.timing.finishedAt, now) : null;
  const ownedActivities = turn.activities.filter(activity => activity.agentRunId === turn.agentRunId && activity.goalRevision === turn.goalRevision);
  const publicActivities = groupedVisibleActivities(ownedActivities);
  const publicProgress = turn.progressUpdates?.filter(item => item.agentRunId === turn.agentRunId) ?? [];
  const phase = turnPhase({ ...turn, activities: ownedActivities, progressUpdates: publicProgress });
  const completedProgress = new Set(publicProgress.filter(item => !['RUNNING'].includes(item.status))
    .slice(-4).map(item => item.progressId));
  const progress = (showAll ? [...publicProgress] : publicProgress.filter(item => item.status === 'RUNNING' || completedProgress.has(item.progressId)))
    .sort((a, b) => activityOrder(a) - activityOrder(b));
  const recent = new Set(publicActivities.filter(item => !['QUEUED', 'RUNNING'].includes(item.status))
    .sort((a, b) => Date.parse(a.finishedAt ?? a.createdAt) - Date.parse(b.finishedAt ?? b.createdAt)).slice(-5).map(item => item.activityId));
  const activities = (showAll ? publicActivities : publicActivities.filter(item => ['QUEUED', 'RUNNING'].includes(item.status) || recent.has(item.activityId)))
    .sort((a, b) => activityOrder(a) - activityOrder(b));
  const summary = needsAttention ? '处理未完成' : active ? '正在处理' : statusLabel[turn.status] ?? '状态待核实';
  const runs = turn.executionTasks.filter(run => run.agentRunId === turn.agentRunId && run.goalRevision === turn.goalRevision && run.isLatestAttempt !== false);
  const executing = runs.find(run => run.status === 'RUNNING' && run.stage !== 'PUBLISHING');
  const stageIndex = confirmation?.status === 'PENDING' ? 1 : executing || runs.some(run => run.status === 'QUEUED') ? 2
    : runs.some(run => run.stage === 'PUBLISHING' || run.status === 'SUCCEEDED') ? 3 : runs.length ? 2 : confirmation ? 1 : 0;
  const planReady = publicProgress.some(item => item.code === 'PLAN_READY' && item.status === 'COMPLETE');
  const recentUpdates = publicProgress.filter(item => item.code !== 'CASE_MATCHED'
    && !(planReady && confirmation?.status === 'PENDING' && item.code === 'WAITING_CONFIRMATION' && item.status === 'RUNNING')).slice(-2);
  const executionTime = executing?.startedAt ? elapsed(executing.startedAt, null, now) : null;
  function describeProgress(item: NonNullable<Turn['progressUpdates']>[number]) {
    if (analysisResultCount !== undefined && item.code === 'EXECUTION_SUCCEEDED') {
      return analysisResultCount === null ? '执行成功，分析成果已发布。' : `执行成功，已发布 ${analysisResultCount} 份分析成果。`;
    }
    return progressDescription(item);
  }
  return <section className={`activity-panel ${active ? 'is-active' : 'is-terminal'}`} aria-label={task ? '当前研究任务' : undefined} data-task-card={task ? true : undefined} data-testid="activity-panel" data-agent-run-id={turn.agentRunId}>
    {task && <>
      <div className="task-card-heading"><h2>{task.name}</h2><span role="status"><span className="task-stage-label" key={task.stage}>{task.stage}</span></span></div>
      <ol className="task-stages" aria-label="任务阶段">{['资料', '确认', '计算', '成果'].map((label, index) =>
        <li key={label} aria-current={index === stageIndex ? 'step' : undefined} data-reached={index <= stageIndex}>{label}</li>)}</ol>
      {task.research && <TaskFacts task={task.research} runPhase={task.stage} />}
      {executionTime && <small className="stage-time">已执行 {executionTime}</small>}
      {children}
      {task.action && <button type="button" className="task-next-action" onClick={task.run}>{task.action}</button>}
    </>}
    <div className={task ? 'task-update-row' : undefined}>
    {task && recentUpdates.length > 0 && <div className="recent-updates"><h3>最近更新</h3>
      <ul aria-live="polite">{recentUpdates.map(item => <li key={item.progressId}>{item.code === 'WAITING_CONFIRMATION' && item.status === 'INTERRUPTED'
        ? '本次执行确认已结束。' : describeProgress(item)}</li>)}</ul></div>}
    <button className="activity-toggle" aria-expanded={expanded} aria-controls={contentId} onClick={() => setDisclosure({ key: disclosureKey, expanded: !expanded })}>
      <Icon name={expanded ? 'arrow-down' : 'arrow-right'} />{task ? '执行记录' : <><strong>{summary}</strong>
      {active && <span className="activity-phase" role="status"><span className="task-stage-label" key={phase}>{phase}</span></span>}
      {waiting && <small aria-live="off">等待确认 {waiting}</small>}
      {duration && <small aria-live="off">用时 {duration}</small>}</>}
    </button></div>
    {!task && children}
    {active && !connected && <p className="activity-sync"><span>实时更新尚未连接，当前显示最后收到的状态。</span><small> 最后核对 {new Date(snapshotAt).toLocaleString('zh-CN')}</small></p>}
    <div className="activity-content" id={contentId} hidden={!expanded}>
      {task && waiting && <small className="stage-time">等待确认 {waiting}</small>}
      {routingLabel(turn) && <p className="routing-status">{routingLabel(turn)}</p>}
      {progress.length > 0 && <><h3 className="activity-section-title">流程里程碑</h3><ol className="activity-list progress-list" aria-label="流程里程碑">{progress.map(item => <li key={item.progressId} data-progress-id={item.progressId} data-tone={activityTone(item.status)}>
        <div><strong>{progressLabel(item)}</strong><Badge tone={activityTone(item.status)}>{item.status === 'INTERRUPTED' ? '已中断' : statusLabel[item.status] ?? '状态待核实'}</Badge></div>
        <p className="muted">{describeProgress(item)}</p>
        <ActivityTime {...item} duration={item.finishedAt || item.status === 'RUNNING' ? elapsed(item.startedAt, item.finishedAt, now) : null} />
      </li>)}</ol></>}
      {publicProgress.length > progress.length && <button className="text-button" onClick={() => setShowAllFor(disclosureKey)}>查看全部 {publicProgress.length} 个步骤</button>}
      {activities.length > 0 && <><h3 className="activity-section-title">Agent 实际处理</h3><ol className="activity-list" aria-label="Agent 实际处理">{activities.map(item => <li key={item.activityId} data-activity-id={item.activityId} data-tone={activityTone(item.status)}>
        <div><strong>{activityLabel(item, turn.activities)}</strong><Badge tone={activityTone(item.status)}>{activityStatusLabel(item)}</Badge></div>
        {activityDescription(item, turn, turn.activities) && <p className="muted">{activityDescription(item, turn, turn.activities)}</p>}
        <ActivityTime {...item} duration={activityElapsed(item, now)} />
        {item.errorCode && <ErrorNotice code={item.errorCode} />}
      </li>)}</ol></>}
      {publicActivities.length > activities.length && <button className="text-button" onClick={() => setShowAllFor(disclosureKey)}>查看全部 {publicActivities.length} 项 Agent 处理</button>}
      {!publicProgress.length && !turn.activities.length && <p className="muted">{turn.status === 'QUEUED' ? '请求已接收，等待开始处理。' : '本轮尚无处理活动。'}</p>}
    </div>
  </section>;
}

function activityTone(status: string) {
  if (status === 'RUNNING') return 'running';
  if (status === 'FAILED') return 'error';
  if (status === 'COMPLETE') return 'success';
  return 'neutral';
}
