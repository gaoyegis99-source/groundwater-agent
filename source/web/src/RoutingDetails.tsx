import { routingLabel } from './message-routing';
import type { Turn } from './types';

export function IntentOptions({ turn, actionable, busy, onChoose }: { turn: Turn; actionable: boolean; busy: boolean; onChoose: (message: string) => void }) {
  if (turn.status !== 'COMPLETE' || turn.interactionOptions.length === 0) return null;
  return <section className="intent-options" aria-label="下一步选项">
    {turn.clarificationRequired && <p>选择最接近你的目标，或直接补充说明。</p>}
    <div>{turn.interactionOptions.map(option =>
      <button key={option.message} disabled={busy || !actionable} onClick={() => onChoose(option.message)}><strong>{option.label}</strong><span>{option.description}</span></button>)}</div>
  </section>;
}

export function RoutingDebug({ turns }: { turns: Turn[] }) {
  return <details><summary>需求判断与请求记录</summary>{turns.map(turn => <div key={turn.goalRevision}>
    <h4>第 {turn.goalRevision} 轮{routingLabel(turn) ? ` · ${routingLabel(turn)}` : ''}</h4>
    <pre>{JSON.stringify({ requestId: turn.requestId, requestedMode: turn.requestedMode,
      routingStatus: turn.routingStatus, routingSource: turn.routingSource, intentDecision: turn.intentDecision,
      dialoguePolicy: turn.dialoguePolicy,
      resolvedMode: turn.resolvedMode, clarificationRequired: turn.clarificationRequired,
      correctsRequestId: turn.correctsRequestId, routingMetrics: turn.routingMetrics }, null, 2)}</pre>
  </div>)}</details>;
}
