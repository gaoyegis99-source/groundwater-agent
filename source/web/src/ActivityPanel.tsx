import { useEffect, useState } from 'react';
import { activityDescription, activityLabel, activityStatusLabel, groupedVisibleActivities, progressDescription, progressLabel, statusLabel, turnPhase } from './chat-state';
import { ErrorNotice } from './ErrorNotice';
import type { Turn } from './types';
import { routingLabel } from './message-routing';

function elapsed(start: string | null, end: string | null, now: number) {
  if (start === null) return null;
  const milliseconds = Math.max(0, (end === null ? now : Date.parse(end)) - Date.parse(start));
  if (milliseconds > 0 && milliseconds < 1000) return '少于 1 秒';
  if (milliseconds < 10_000 && milliseconds % 1000 !== 0) return `${Math.round(milliseconds / 100) / 10} 秒`;
  const seconds = Math.floor(milliseconds / 1000);
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function activityElapsed(activity: Turn['activities'][number], activities: Turn['activities'], now: number) {
  if (activity.kind === 'SEGMENT') {
    const firstRequest = activities.find(item => item.segmentId === activity.segmentId && item.kind === 'MODEL_REQUEST');
    if (firstRequest) return elapsed(firstRequest.createdAt, firstRequest.finishedAt, now);
  }
  return elapsed(activity.createdAt, activity.finishedAt, now);
}

export function ActivityPanel({ turn, snapshotAt, connected }: { turn: Turn; snapshotAt: string; connected: boolean }) {
  const active = ['ROUTING', 'QUEUED', 'RUNNING', 'WAITING_CONFIRMATION', 'WAITING_EXECUTION'].includes(turn.status);
  const needsAttention = ['FAILED', 'TIMED_OUT'].includes(turn.status);
  const disclosureKey = `${turn.agentRunId}:${active ? 'active' : 'terminal'}`;
  const [disclosure, setDisclosure] = useState<{ key: string; expanded: boolean } | null>(null);
  const [showAllFor, setShowAllFor] = useState<string | null>(null);
  const expanded = disclosure?.key === disclosureKey ? disclosure.expanded : needsAttention;
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
  const duration = elapsed(turn.timing.createdAt, turn.timing.finishedAt, now);
  const publicActivities = groupedVisibleActivities(turn.activities);
  const publicProgress = turn.progressUpdates ?? [];
  const completedProgress = new Set(publicProgress.filter(item => !['RUNNING'].includes(item.status))
    .slice(-4).map(item => item.progressId));
  const progress = showAll ? publicProgress : publicProgress.filter(item => item.status === 'RUNNING' || completedProgress.has(item.progressId));
  const recent = new Set(publicActivities.filter(item => !['QUEUED', 'RUNNING'].includes(item.status))
    .sort((a, b) => Date.parse(a.finishedAt!) - Date.parse(b.finishedAt!)).slice(-5).map(item => item.activityId));
  const activities = showAll ? publicActivities : publicActivities.filter(item => ['QUEUED', 'RUNNING'].includes(item.status) || recent.has(item.activityId));
  const summary = needsAttention ? '处理未完成' : active ? '正在处理' : statusLabel[turn.status] ?? turn.status;
  return <section className={`activity-panel ${active ? 'is-active' : 'is-terminal'}`} data-testid="activity-panel" data-agent-run-id={turn.agentRunId}>
    <button className="activity-toggle" aria-expanded={expanded} aria-controls={contentId} onClick={() => setDisclosure({ key: disclosureKey, expanded: !expanded })}>
      <span aria-hidden="true">{expanded ? '⌄' : '›'}</span><strong>{summary}</strong>
      {active ? <span role="status" aria-live="polite">{turnPhase(turn)}</span> : <span className="activity-summary-hint">查看详情</span>}
      {duration && <small aria-live="off">用时 {duration}</small>}
    </button>
    {active && !connected && <p className="activity-sync">实时更新尚未连接，当前显示最后收到的状态。</p>}
    <div className="activity-content" id={contentId} hidden={!expanded}>
      {routingLabel(turn) && <p className="routing-status">{routingLabel(turn)}</p>}
      {progress.length > 0 && <ol className="activity-list progress-list" aria-label="流程里程碑">{progress.map(item => <li key={item.progressId} data-progress-id={item.progressId}>
        <div><strong>{progressLabel(item)}</strong><span className="badge">{item.status === 'INTERRUPTED' ? '已中断' : statusLabel[item.status] ?? item.status}</span>
          <small aria-live="off">{elapsed(item.startedAt ?? item.createdAt, item.finishedAt, now)}</small></div>
        <p className="muted">{progressDescription(item)}</p>
      </li>)}</ol>}
      {publicProgress.length > progress.length && <button className="text-button" onClick={() => setShowAllFor(disclosureKey)}>查看全部 {publicProgress.length} 个步骤</button>}
      {activities.length > 0 && <ol className="activity-list" aria-label="Agent 实际处理">{activities.map(item => <li key={item.activityId} data-activity-id={item.activityId}>
        <div><strong>{activityLabel(item, turn.activities)}</strong><span className="badge">{activityStatusLabel(item)}</span>
          <small aria-live="off">{activityElapsed(item, turn.activities, now)}</small></div>
        {activityDescription(item, turn, turn.activities) && <p className="muted">{activityDescription(item, turn, turn.activities)}</p>}
        {item.errorCode && <ErrorNotice code={item.errorCode} />}
      </li>)}</ol>}
      {publicActivities.length > activities.length && <button className="text-button" onClick={() => setShowAllFor(disclosureKey)}>查看全部 {publicActivities.length} 项 Agent 处理</button>}
      {!publicProgress.length && !turn.activities.length && <p className="muted">{turn.status === 'QUEUED' ? '请求已接收，等待开始处理。' : '本轮尚无处理活动。'}</p>}
    </div>
  </section>;
}
