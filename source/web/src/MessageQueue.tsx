import type { QueuedMessage } from './task-state';
export function MessageQueue({ items, onChange }: { items: QueuedMessage[]; onChange: (items: QueuedMessage[]) => void }) {
  if (!items.length) return null;
  return <section className="message-queue" aria-label="稍后发送的消息"><strong>稍后发送</strong><small>当前处理完成后自动发送；关闭页面会取消。</small>{items.map((item, index) => <div key={item.id}>
    <label>{item.requestId ? '正在核对提交状态' : '尚未提交'}<textarea aria-label={`排队消息 ${index + 1}`} value={item.text} disabled={!!item.requestId} onChange={event => onChange(items.map(value => value.id === item.id ? { ...value, text: event.target.value } : value))} /></label>
    <span>{item.attachments.length ? `${item.attachments.length} 个附件` : ''}</span>
    <button disabled={!!item.requestId || index === 0 || !!items[index - 1]?.requestId} onClick={() => { const next = [...items]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; onChange(next); }}>上移</button>
    <button disabled={!!item.requestId} onClick={() => onChange(items.filter(value => value.id !== item.id))}>删除</button>
  </div>)}</section>;
}
