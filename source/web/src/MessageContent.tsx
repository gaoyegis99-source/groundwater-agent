import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { shortVersion } from './format';
import type { Message, ReplyPresentation } from './types';

function splitLegacyAttachments(content: string) {
  // Only the exact attachment envelope produced by withReferences is a UI attachment.
  const block = /(?:^|\n\n)附加数据：\n((?:- [^\n]+\n?)+)$/.exec(content);
  const matches = block?.[1].trimEnd().split('\n').map(line => /^- (.+)（(?:数据集|Dataset): ([\w-]+)；(?:版本|Version): ([\w-]+)；(?:语义|Semantics): ([\w-]+)；(?:校验和|checksum): (?:sha256:)?([a-f0-9]{64})）$/i.exec(line));
  if (!block || !matches?.every(match => match !== null)) return { body: content, attachments: [] };
  return { body: content.slice(0, block.index), attachments: matches.map(match => ({ name: match[1], versionId: match[3] })) };
}

export function copyQuestionText({ content, inputReferences }: Pick<Message, 'content' | 'inputReferences'>) {
  const { body, attachments } = splitLegacyAttachments(content);
  const names = [
    ...attachments.map(item => item.name),
    ...(inputReferences ?? []).filter(ref => !attachments.some(item => item.versionId === ref.versionId))
      .map(ref => ref.outputName || `${ref.format.toUpperCase()} 资料`),
  ];
  return names.length ? [body, `附加资料\n${names.map(name => `- ${name}`).join('\n')}`].filter(Boolean).join('\n\n') : body;
}

const diagnosticKeys = new Set([
  'traceid', 'requestid', 'agentrunid', 'workflowrunid', 'modelid', 'modelversion', 'workflowid', 'workflowversion',
  'datasetid', 'versionid', 'semanticid', 'objectid', 'checksum', 'errorcode', 'durationms', 'status',
]);

function isDiagnosticJson(body: string) {
  let value: unknown;
  try { value = JSON.parse(body); } catch { return false; }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Object.keys(value).map(key => key.toLowerCase().replaceAll('_', ''));
  return keys.some(key => key.endsWith('id') || key === 'checksum' || key === 'errorcode')
    && keys.every(key => diagnosticKeys.has(key));
}

const internalHref = String.raw`(?:https?:\/\/[^\s/()<>]+)?\/api\/v12\/(?:data\/versions|knowledge|workflow-runs|agent-runs)\/[^\s()<>]+`;
const internalMarkdownLink = new RegExp(String.raw`!?\[([^\]\n]*)\]\(<?(${internalHref})>?(?:[ \t]+"[^"\n]*")?\)`, 'g');
const internalAutolink = new RegExp(`<${internalHref}>`, 'g');

export function copyReplyText(reply: ReplyPresentation) {
  return [reply.conclusion, reply.executionSummary, ...reply.details, ...reply.unresolvedItems]
    .flatMap(section => section ? [section.markdown] : [])
    .map(markdown => markdown
      .replace(/^ {0,3}(`{3,}|~{3,})([^\r\n]*)\r?\n([\s\S]*?)^ {0,3}\1[ \t]*(?=\r?$)/gm, (block, _fence, info: string, body: string) => {
        const language = info.trim().toLowerCase();
        return /^(logs?|trace)$/.test(language) || (language === 'json' && isDiagnosticJson(body)) ? '' : block;
      })
      .replace(internalMarkdownLink, (_link, label: string, href: string) => label && !href.split(/[/?#]/).includes(label) ? label : '查看详情')
      .replace(internalAutolink, '查看详情')
      .replace(/[（(]knowledge_id: [\w-]+, chunk_index: \d+, checksum: (?:sha256:)?[a-f0-9]{64}[）)]/gi, '')
      .trim())
    .filter(Boolean).join('\n\n');
}

export function MessageContent({ content, onResource }: { content: string; onResource: (id: string) => void }) {
  const { body, attachments } = splitLegacyAttachments(content);
  if (!attachments.length) return <p>{body}</p>;
  return <>
    {body && <p>{body}</p>}
    <div className="message-attachments">{attachments.map(({ name, versionId }) => <div key={versionId}>
      <button className="attachment-reference" data-resource-id={versionId} title={`数据版本 ${versionId}`} onClick={() => onResource(versionId)} aria-label={`查看附件 ${name} ${shortVersion(versionId)}`}>
        <Icon name="file" />{name} <small>{shortVersion(versionId)}</small>
      </button>
    </div>)}</div>
  </>;
}

export function CopyMessage({ text, label }: { text: () => string; label: string }) {
  const [feedback, setFeedback] = useState('');
  const [manualCopy, setManualCopy] = useState<{ content: string }>();
  const manualText = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!manualCopy) return;
    manualText.current?.focus();
    manualText.current?.select();
  }, [manualCopy]);
  return <div className="copy-message">
    <button type="button" className="text-button" onClick={async () => {
      const content = text();
      try {
        await navigator.clipboard.writeText(content);
        setManualCopy(undefined);
        setFeedback('已复制');
      } catch {
        setManualCopy({ content });
        setFeedback('请复制下方已选中的文字。');
      }
    }}>{label}</button>
    {feedback && <small className="copy-feedback" role="status" data-copied={!manualCopy}>
      {!manualCopy && <Icon name="check" />}{feedback}
    </small>}
    {manualCopy && <textarea ref={manualText} className="manual-copy" aria-label="待复制文本" readOnly value={manualCopy.content} />}
  </div>;
}
