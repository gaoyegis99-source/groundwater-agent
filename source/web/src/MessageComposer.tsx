import type { ReactNode, RefObject } from 'react';
import { Icon } from './Icon';

export function MessageComposer({ inputRef, value, selection, busy, canSend, tools, onChange, onSend }: {
  inputRef: RefObject<HTMLTextAreaElement | null>; value: string; selection: boolean;
  busy: boolean; canSend: boolean; tools: ReactNode;
  onChange: (value: string) => void; onSend: () => void;
}) {
  return <>
    <textarea ref={inputRef} aria-label="消息输入框" title="回车发送 · Shift+回车换行" placeholder={selection ? '补充资料或回答问题，继续完善这个方案…' : '描述你的问题，或附上资料一起讨论…'} value={value}
      onChange={event => onChange(event.target.value)} onKeyDown={event => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (canSend) onSend(); }
      }} rows={2} />
    <div className="composer-toolbar"><div>{tools}</div><button className="send-button" aria-label="发送消息" title={!canSend ? '请等待当前处理完成，或输入问题' : '发送消息'} disabled={!canSend} onClick={onSend}>
      {busy ? <span className="sending-dot" /> : <><span>发送</span><Icon name="arrow-up" /></>}
    </button></div>
  </>;
}
