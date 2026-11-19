import { useId, type ReactNode, type RefObject } from 'react';
import { Icon } from './Icon';

export function MessageComposer({ inputRef, value, selection, busy, submitting = false, canSend, tools, unavailableReason, feedback, announceFeedback, onChange, onSend }: {
  inputRef: RefObject<HTMLTextAreaElement | null>; value: string; selection: boolean;
  busy: boolean; canSend: boolean; tools: ReactNode;
  submitting?: boolean;
  unavailableReason?: string;
  feedback?: string;
  announceFeedback?: boolean;
  onChange: (value: string) => void; onSend: () => void;
}) {
  const inputId = useId();
  return <>
    <label className="composer-label" htmlFor={inputId}>消息输入框</label>
    <textarea id={inputId} ref={inputRef} name="message" autoComplete="off" aria-describedby={`${inputId}-keys${feedback ? ` ${inputId}-feedback` : ''}`} placeholder={selection ? '补充资料或回答问题，继续完善这个方案…' : '描述你的问题，或附上资料一起讨论…'} value={value}
      onChange={event => onChange(event.target.value)} onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.keyCode === 229) return;
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (canSend) onSend(); }
      }} rows={2} />
    <div className="composer-toolbar"><div>{tools}</div><small id={`${inputId}-keys`} className="composer-keys">
      Enter 发送 · Shift+Enter 换行
    </small><button className="send-button" type="button" aria-busy={submitting || undefined}
      aria-label={submitting ? '发送消息：正在提交' : busy && !canSend ? '发送消息：等待处理完成' : '发送消息'}
      title={canSend ? '发送消息' : unavailableReason ?? (busy ? '请等待当前处理完成' : '请输入问题或附加资料')} disabled={!canSend} onClick={onSend}>
      {submitting ? <><span className="gw-spinner send-spinner" aria-hidden="true" /><span>提交中</span></>
        : busy && !canSend ? <><span className="sending-dot" aria-hidden="true" /><span>等待</span></> : <><span>发送</span><Icon name="arrow-up" /></>}
    </button></div>
    {feedback && <p id={`${inputId}-feedback`} className="composer-disabled-reason" role={announceFeedback ? 'status' : undefined}>{feedback}</p>}
  </>;
}
