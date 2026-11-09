import type { ControlMessage, ControlProposal } from './types';

const labels = {
  APPROVE_CONFIRMATION: '批准当前计算方案',
  REJECT_CONFIRMATION: '拒绝当前计算方案',
  STOP_ANALYSIS: '停止本次分析',
  CANCEL_EXECUTION: '取消正在执行的计算',
};
const reasons = {
  REQUEST_KIND_AMBIGUOUS: '还不能确定你希望系统执行哪类操作。',
  TASK_CONFLICT: '这项请求与当前主任务状态冲突，请明确是继续还是修改任务。',
  CONTROL_TARGET_MISSING: '当前没有可执行这项控制的目标。',
  CONTROL_TARGET_AMBIGUOUS: '当前存在多个可能目标，请明确要控制哪一次运行。',
};
const statusLabels: Record<string, string> = { IDLE: '当前空闲', ANALYZING: '正在分析', EXECUTING: '正在执行计算', WAITING_CONFIRMATION: '等待计算确认', RUNNING: '正在分析', WAITING_EXECUTION: '正在执行计算', COMPLETE: '最近一轮已完成', FAILED: '最近一轮未完成', CANCELLED: '最近一轮已取消' };
const waitingReasons: Record<string, string> = { ANALYSIS_IN_PROGRESS: '分析尚未完成。', EXECUTION_IN_PROGRESS: '已批准的计算仍在执行。', CONFIRMATION_REQUIRED: '有计算方案等待确认。' };

export function ControlProposalCard({ proposal, message, disabled, onDecision, onResource }: {
  proposal?: ControlProposal; message: ControlMessage; disabled: boolean;
  onDecision: (proposalId: string, action: 'confirm' | 'dismiss') => void;
  onResource?: (resourceId: string) => void;
}) {
  if (message.semanticIntent === 'STATUS_QUERY') return <section className="control-proposal complete" role="status">
    <strong>当前状态</strong><p>{statusLabels[message.statusSnapshot] ?? message.statusSnapshot}</p>
    {message.waitingReason && <p>{waitingReasons[message.waitingReason] ?? message.waitingReason}</p>}
    {!!message.results.length && <div>{message.results.map((result, index) => <button key={result.versionId} onClick={() => onResource?.(result.versionId)}>{result.outputName ?? `查看结果 ${index + 1}`}</button>)}</div>}
  </section>;
  if (!proposal) return <section className="control-proposal stale" role="status">
    <strong>{message.message}</strong>
    <p>{message.clarificationReason ? reasons[message.clarificationReason] : '控制请求未能绑定目标。'}</p>
  </section>;
  const pending = proposal.status === 'PENDING';
  return <section className={`control-proposal ${proposal.status.toLowerCase()}`} aria-label="控制确认">
    <strong>{labels[proposal.intent]}</strong>
    <p>{pending ? '请再次确认。确认前不会改变当前运行或方案。' : proposal.status === 'STALE' ? '请求已失效，当前状态已经变化。' : `状态：${proposal.status}`}</p>
    {pending && <div><button disabled={disabled} onClick={() => onDecision(proposal.proposalId, 'confirm')}>确认</button><button disabled={disabled} onClick={() => onDecision(proposal.proposalId, 'dismiss')}>放弃</button></div>}
  </section>;
}
