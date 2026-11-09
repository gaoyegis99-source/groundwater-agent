import type { Activity, AgentMetrics, Confirmation, ExecutionTask, History, ProgressUpdate, ResourceReference, Turn } from './types';
import { routingLabel } from './message-routing';

export function isBusy(history: History | null) {
  const turn = history?.turns.find(item => item.goalRevision === history.goalRevision);
  return !!turn && ['ROUTING', 'QUEUED', 'RUNNING', 'WAITING_EXECUTION'].includes(turn.status);
}
export function hasVerificationMarker(value: string | null | undefined) {
  return !!value && (value.includes('SYNTHETIC_TEST_ONLY') || value.startsWith('【验证会话】'));
}
export function isVerificationHistory(history: History | null) {
  return history?.items.some(message => hasVerificationMarker(message.content)) ?? false;
}
export function canConfirm(confirmation: Confirmation, history: History | null, busy: boolean) {
  const current = history?.turns.find(turn => turn.goalRevision === history.goalRevision);
  return !busy && confirmation.status === 'PENDING' && confirmation.decision === null &&
    confirmation.decisionState === null && current?.status === 'WAITING_CONFIRMATION' &&
    current.confirmations.at(-1)?.confirmationId === confirmation.confirmationId &&
    history?.goalRevision === confirmation.goalRevision;
}
export function withReferences(message: string, resources: (ResourceReference & { name: string })[]) {
  if (!resources.length) return message;
  return `${message}${message ? '\n\n' : ''}附加数据：\n${resources.map(item =>
    `- ${item.name}（数据集: ${item.datasetId}；版本: ${item.versionId}；语义: ${item.semanticId}；校验和: ${item.checksum.replace(/^sha256:/i, '').toLowerCase()}）`
  ).join('\n')}`;
}
export function mergeHistory(previous: History, page: History): History {
  const messages = new Map([...previous.items, ...page.items].map(item => [item.messageId, item]));
  const turns = new Map([...previous.turns, ...page.turns].map(item => [item.goalRevision, item]));
  return { ...page, goalRevision: Math.max(previous.goalRevision, page.goalRevision),
    items: [...messages.values()].sort((a, b) => a.messageId - b.messageId),
    turns: [...turns.values()].sort((a, b) => a.goalRevision - b.goalRevision) };
}
export function refreshHistory(previous: History, latest: History): History {
  if (latest.eventCursor < previous.eventCursor) return previous;
  const oldIds = new Set(previous.items.map(item => item.messageId));
  if (latest.nextCursor !== null && !latest.items.some(item => oldIds.has(item.messageId))) return latest;
  return { ...mergeHistory(previous, latest), nextCursor: previous.nextCursor };
}
export function prependHistory(previous: History, page: History): History {
  return { ...mergeHistory(page, previous), goalRevision: previous.goalRevision,
    eventCursor: previous.eventCursor, snapshotAt: previous.snapshotAt, nextCursor: page.nextCursor };
}
export function activityLabel(activity: Activity, _activities: Activity[] = []) {
  return activity.title;
}
export function activityDescription(activity: Activity, _turn?: Turn, _activities: Activity[] = []) {
  return activity.summary;
}

export function visibleActivities(activities: Activity[]) {
  let lastProcessingRequest = -1;
  activities.forEach((item, index) => {
    if (item.kind === 'MODEL_REQUEST' && item.purpose !== 'COMPACTION') lastProcessingRequest = index;
  });
  return activities.filter((item, index) => item.kind !== 'MODEL_REQUEST' || item.purpose === 'COMPACTION' ||
    item.status !== 'COMPLETE' || index === lastProcessingRequest);
}
export function groupedVisibleActivities(activities: Activity[]) {
  const visible = visibleActivities(activities);
  const grouped: Activity[] = [];
  for (const item of visible) {
    const previous = grouped.at(-1);
    if (item.kind === 'TOOL' && item.status === 'COMPLETE' && item.templateKey &&
      previous?.kind === 'TOOL' && previous.status === 'COMPLETE' &&
      previous.templateKey === item.templateKey && previous.outcomeCode === item.outcomeCode &&
      previous.summary === item.summary) {
      grouped[grouped.length - 1] = { ...previous, repeatCount: (previous.repeatCount ?? 1) + 1,
        finishedAt: item.finishedAt, outputBytes: (previous.outputBytes ?? 0) + (item.outputBytes ?? 0),
        outputTokens: (previous.outputTokens ?? 0) + (item.outputTokens ?? 0) };
    } else grouped.push({ ...item, repeatCount: 1 });
  }
  return grouped;
}
export function progressLabel(progress: ProgressUpdate) {
  return progress.title;
}
export function progressDescription(progress: ProgressUpdate) {
  return progress.summary;
}
export function currentProgress(turn: Turn) {
  const progress = turn.progressUpdates ?? [];
  return progress.find(item => item.status === 'RUNNING') ?? progress.at(-1) ?? null;
}
export function taskLabel(task: ExecutionTask) {
  if (task.blockedByWorkflowRunId) return '因前项失败未执行';
  if (task.status !== 'QUEUED' && task.status !== 'RUNNING') return statusLabel[task.status] ?? task.status;
  if (task.communicationError) return '执行服务暂不可达，等待同步';
  return task.stage === 'PUBLISHING' ? '正在发布结果' : task.stage === 'EXECUTING' ? '正在执行' : task.status === 'QUEUED' ? '等待执行' : '正在执行';
}
export function activityStatusLabel(activity: Activity) {
  return activity.status === 'INTERRUPTED' ? '已中断' : statusLabel[activity.status] ?? activity.status;
}
export function agentTokenUsageLabel(metrics: AgentMetrics) {
  if (metrics.requestCount > 0 && metrics.inputTokens === 0 && metrics.outputTokens === 0 && metrics.totalTokens === 0) {
    return '服务端未返回词元用量';
  }
  return `${metrics.totalTokens} 词元`;
}
export function turnPhase(turn: Turn) {
  if (turn.requestedMode === 'AUTO' && ((turn.status === 'ROUTING' && ['ROUTING_REQUESTED', 'ROUTING_RUNNING'].includes(turn.routingStatus ?? '')) || turn.routingStatus === 'FAILED')) return routingLabel(turn)!;
  const progress = currentProgress(turn);
  if (progress?.status === 'RUNNING') return progressLabel(progress);
  if (turn.status === 'WAITING_EXECUTION') return '正在执行已确认的分析';
  if (turn.status !== 'RUNNING') return statusLabel[turn.status] ?? turn.status;
  const active = turn.activities.filter(item => item.status === 'RUNNING' || item.status === 'QUEUED');
  const tools = active.filter(item => item.kind === 'TOOL');
  if (tools.length) return tools[0].title;
  const segment = active.filter(item => item.kind === 'SEGMENT').at(-1);
  const model = active.filter(item => item.kind === 'MODEL_REQUEST').at(-1);
  if (model) return model.title;
  if (segment) return segment.title;
  return '处理请求';
}
export const statusLabel: Record<string, string> = {
  ROUTING: '正在判断需求',
  QUEUED: '等待处理', RUNNING: '正在分析', WAITING_CONFIRMATION: '等待确认', WAITING_EXECUTION: '正在执行',
  COMPLETE: '已完成', SUCCEEDED: '已完成', FAILED: '未完成', TIMED_OUT: '已超时', CANCELLED: '已取消',
  PENDING: '待确认', CONFIRMED: '已确认', REJECTED: '已拒绝', SUPERSEDED: '已被新要求替代',
  READY: '方案已生成', GENERATING: '正在查阅资料', APPROVED: '已批准', PROPOSED: '待确认', SKIPPED: '未执行',
};
