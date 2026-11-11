import type { ReactNode, RefObject } from 'react';
import { Icon } from './Icon';

export function MessageComposer({ inputRef, value, selection, busy, canSend, tools, unavailableReason, onChange, onSend }: {
  inputRef: RefObject<HTMLTextAreaElement | null>; value: string; selection: boolean;
  busy: boolean; canSend: boolean; tools: ReactNode;
  unavailableReason?: string;
  onChange: (value: string) => void; onSend: () => void;
}) {
  return <>
    <textarea ref={inputRef} aria-label="消息输入框" title="回车发送 · Shift+回车换行" placeholder={selection ? '补充资料或回答问题，继续完善这个方案…' : '描述你的问题，或附上资料一起讨论…'} value={value}
      onChange={event => onChange(event.target.value)} onKeyDown={event => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (canSend) onSend(); }
      }} rows={2} />
    <div className="composer-toolbar"><div>{tools}</div><button className="send-button" aria-label={busy && !canSend ? '发送消息：等待处理完成' : '发送消息'} title={canSend ? '发送消息' : unavailableReason ?? (busy ? '请等待当前处理完成' : '请输入问题或附加资料')} disabled={!canSend} onClick={onSend}>
      {busy && !canSend ? <><span className="sending-dot" /><span>等待</span></> : <><span>发送</span><Icon name="arrow-up" /></>}
    </button></div>
  </>;
}
