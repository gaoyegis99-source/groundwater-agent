import { routingLabel } from './message-routing';
import type { Turn } from './types';
import { Icon } from './Icon';

export function IntentOptions({ turn, current, actionable, busy, onChoose }: { turn: Turn; current: boolean; actionable: boolean; busy: boolean; onChoose: (message: string) => void }) {
  if (turn.status !== 'COMPLETE' || turn.interactionOptions.length === 0) return null;
  return <section className="intent-options" aria-label={current ? "下一步选项" : undefined}>
    <h3>{turn.clarificationRequired ? '确认分析方向' : '下一步选项'}</h3>
    {turn.clarificationRequired && actionable && <p>选择最接近你的目标，或直接补充说明。</p>}
    <ul>{turn.interactionOptions.map(option => <li key={option.message}>
      <button type="button" disabled={busy || !actionable} onClick={() => onChoose(option.message)}>
        <strong>{option.label}</strong><span>{option.description}</span><Icon name="arrow-right" />
      </button>
    </li>)}</ul>
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
