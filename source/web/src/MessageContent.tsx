import { useMemo } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Icon } from './Icon';
import type { KnowledgeReference, ReplyPresentation, ReplySection } from './types';

export function MessageContent({ content, onResource }: { content: string; onResource: (id: string) => void }) {
  // Only the exact attachment envelope produced by withReferences is a UI attachment.
  const block = /(?:^|\n\n)附加数据：\n((?:- [^\n]+\n?)+)$/.exec(content);
  const attachments = block?.[1].trimEnd().split('\n').map(line => /^- (.+)（(?:数据集|Dataset): ([a-zA-Z0-9-]+)；(?:版本|Version): ([a-zA-Z0-9-]+)；(?:语义|Semantics): ([a-zA-Z0-9-]+)；(?:校验和|checksum): (?:sha256:)?([a-f0-9]{64})）$/i.exec(line));
  if (!block || !attachments?.every(Boolean)) return <p>{content}</p>;
  return <>
    {content.slice(0, block.index) && <p>{content.slice(0, block.index)}</p>}
    <div className="message-attachments">{attachments.map(match => <div key={match![3]}>
      <button className="attachment-reference" data-resource-id={match![3]} onClick={() => onResource(match![3])} aria-label={`查看附件 ${match![1]}`}>
        <Icon name="file" />{match![1]} <small>{match![3]}</small>
      </button>
    </div>)}</div>
  </>;
}

function ReplyMarkdown({ section, onResource }: { section: ReplySection; onResource: (id: string) => void }) {
  const components = useMemo<Components>(() => ({
      a: ({ href, children }) => {
        const data = /^\/api\/v12\/data\/versions\/([a-zA-Z0-9-]+)$/.exec(href ?? '');
        if (data) return <button className="citation-link" onClick={() => onResource(data[1])}>{children}</button>;
        return href && /^(https?:|mailto:)/.test(href) ? <a href={href} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>;
      },
      img: ({ alt }) => <span>{alt}</span>,
      pre: ({ children }) => <details className="technical-details"><summary>查看代码或结构化内容</summary><pre>{children}</pre></details>,
      table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
  }), [onResource]);
  return <div className="markdown-content"><Markdown remarkPlugins={[remarkGfm]} components={components}>{section.markdown}</Markdown></div>;
}

export function AssistantContent({ reply, onKnowledge, onResource }: {
  reply: ReplyPresentation; onKnowledge: (reference: KnowledgeReference) => void; onResource: (id: string) => void;
}) {
  const executionDetails = reply.executionSummary ? reply.details : [];
  const supportingDetails = reply.executionSummary ? [] : reply.details;
  const hasSources = reply.sources.length > 0;
  return <div className="reply-text">
    {reply.conclusion && <div className="reply-conclusion"><ReplyMarkdown section={reply.conclusion} onResource={onResource} /></div>}
    {reply.executionSummary && <section className="reply-execution" aria-label="执行摘要"><h3>执行摘要</h3><ReplyMarkdown section={reply.executionSummary} onResource={onResource} /></section>}
    {executionDetails.length > 0 && <section className="reply-result-explanation" aria-label="结果说明"><h3>结果说明</h3>{executionDetails.map((detail, index) => <div className="reply-detail" key={index}><ReplyMarkdown section={detail} onResource={onResource} /></div>)}</section>}
    {reply.unresolvedItems.length > 0 && <section className="reply-unresolved" aria-label="需要补充"><h3>需要补充</h3><ul>{reply.unresolvedItems.map((item, index) => <li key={index}><ReplyMarkdown section={item} onResource={onResource} /></li>)}</ul></section>}
    {(supportingDetails.length > 0 || hasSources) && <details className="reply-details"><summary>更多信息</summary>
      {supportingDetails.map((detail, index) => <div className="reply-detail" key={index}><ReplyMarkdown section={detail} onResource={onResource} /></div>)}
      {hasSources && <section className="reply-sources" aria-label="回答依据"><h3>回答依据</h3><div className="citation-list">{reply.sources.map((source, index) => <span
        className="citation-entry" key={JSON.stringify([source.versionId, source.unitId, source.checksum])}
      ><button className="citation"
          data-knowledge-version-id={source.versionId} data-knowledge-unit-id={source.unitId}
          aria-label={`打开依据：${source.title}`}
          onClick={() => onKnowledge({ versionId: source.versionId, unitId: source.unitId, checksum: source.checksum })}
        ><Icon name="book" />{source.title}</button>{index < reply.sources.length - 1 && <span aria-hidden="true">；</span>}</span>)}</div></section>}
    </details>}
  </div>;
}
