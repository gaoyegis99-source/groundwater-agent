import { useId, useState } from 'react';
import type { QueuedMessage } from './task-state';
export function MessageQueue({ items, onChange }: { items: QueuedMessage[]; onChange: (items: QueuedMessage[]) => void }) {
  const [initiallyOpen] = useState(() => !window.matchMedia?.('(max-height: 560px)').matches);
  const queueId = useId();
  if (!items.length) return null;
  return <section className="message-queue" aria-label="稍后发送的消息">
    <details open={initiallyOpen}>
      <summary className="queue-heading"><strong>稍后发送</strong> <span>{items.length} 条</span></summary>
      <div className="queue-content">
        <small>当前处理完成后自动发送。<span>关闭页面后暂停，重新打开本会话后可继续发送。</span></small>
        <ol className="queue-messages">{items.map((item, index) => <li key={item.id} data-awaiting-receipt={!!item.requestId}>
          <div className="queue-item-header">
            <span id={`${queueId}-${index}`}>{item.requestId ? '正在核对提交状态' : '尚未提交'}</span>
            {item.attachments.length > 0 && <small>{item.attachments.length} 个附件</small>}
          </div>
          <textarea rows={2} aria-label={`排队消息 ${index + 1}`} aria-describedby={`${queueId}-${index}`} value={item.text} disabled={!!item.requestId} onChange={event => onChange(items.map(value => value.id === item.id ? { ...value, text: event.target.value } : value))} />
          <div className="queue-item-actions">
          <button disabled={!!item.requestId || index === 0 || !!items[index - 1]?.requestId} onClick={() => { const next = [...items]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; onChange(next); }}>上移</button>
          <button disabled={!!item.requestId} onClick={() => onChange(items.filter(value => value.id !== item.id))}>删除</button>
          </div>
        </li>)}</ol>
      </div>
    </details>
  </section>;
}
