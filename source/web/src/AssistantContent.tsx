import { createContext, lazy, memo, useContext, useLayoutEffect } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Icon } from './Icon';
import { contentUrl } from './api';
import type { KnowledgeReference, ReplyPresentation, ReplySection, ReplySource, ResourceReference } from './types';

const noImages: readonly ResourceReference[] = [];

type ReplyResources = { onResource: (id: string) => void; images: readonly ResourceReference[] };
const ReplyResourceContext = createContext<ReplyResources | null>(null);
const MathMarkdown = lazy(() => import('./MathMarkdown'));

const markdownComponents: Components = {
  a: function ReplyLink({ href, children }) {
    const resources = useContext(ReplyResourceContext);
    const data = /^\/api\/v12\/data\/versions\/([a-zA-Z0-9-]+)$/.exec(href ?? '');
    if (data && resources) return <button className="citation-link" onClick={() => resources.onResource(data[1])}>{children}</button>;
    return href && /^(https?:|mailto:)/.test(href) ? <a href={href} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>;
  },
  img: function ReplyImage({ src, alt }) {
    const resources = useContext(ReplyResourceContext);
    return resources?.images.some(ref => src === contentUrl('', ref.versionId, ref.objectId))
      ? <img src={src} alt={alt || '本轮成果图片'} loading="lazy" /> : <span>{alt}</span>;
  },
  pre: ({ children }) => <details className="technical-details"><summary>查看代码或结构化内容</summary><pre>{children}</pre></details>,
  table: ({ children }) => <div className="table-scroll" role="region" aria-label="分析表格，可横向滚动" tabIndex={0}><table>{children}</table></div>,
};

const MarkdownBody = memo(function MarkdownBody({ source }: { source: string }) {
  // A fenced language can spell "math" with character references.
  const mayContainMath = source.includes('$') ||
    ((source.includes('```') || source.includes('~~~')) && (source.includes('math') || source.includes('&')));
  return <div className="markdown-content">{mayContainMath
    ? <MathMarkdown source={source} components={markdownComponents} />
    : <Markdown skipHtml remarkPlugins={[remarkGfm]} components={markdownComponents}>{source}</Markdown>}
  </div>;
});

function ReplyMarkdown({ section, onResource, images }: ReplyResources & { section: ReplySection }) {
  return <ReplyResourceContext.Provider value={{ onResource, images }}>
    <MarkdownBody source={section.markdown} />
  </ReplyResourceContext.Provider>;
}

export function AssistantContent({ reply, onKnowledge, onResource, onReady, images = noImages, unresolvedLabel = '需要补充' }: {
  reply: ReplyPresentation; unresolvedLabel?: string; images?: readonly ResourceReference[]; onKnowledge: (reference: KnowledgeReference) => void; onResource: (id: string) => void;
  onReady?: () => void;
}) {
  useLayoutEffect(() => { onReady?.(); }, [onReady]);
  const executionDetails = reply.executionSummary ? reply.details : [];
  const supportingDetails = reply.executionSummary ? [] : reply.details;
  const hasSources = reply.sources.length > 0;
  const hasMultipleSources = reply.sources.length > 1;
  function openSource({ versionId, unitId, checksum }: ReplySource) {
    onKnowledge({ versionId, unitId, checksum });
  }
  function renderSection(section: ReplySection) {
    return <>
      <ReplyMarkdown section={section} onResource={onResource} images={images} />
      {hasMultipleSources && section.sourceIndexes.length > 0 && <div className="reply-section-sources">
        {section.sourceIndexes.map(index => {
          const source = reply.sources[index];
          return source && <button type="button" className="citation-link" key={index}
            aria-label={`查看依据 ${index + 1} · ${source.title}`} onClick={() => openSource(source)}>依据 {index + 1}</button>;
        })}
      </div>}
    </>;
  }
  return <div className="reply-text">
    {reply.conclusion && <div className="reply-conclusion">{renderSection(reply.conclusion)}</div>}
    {reply.executionSummary && <section className="reply-execution" aria-label="执行摘要"><h3>执行摘要</h3>{renderSection(reply.executionSummary)}</section>}
    {executionDetails.length > 0 && <section className="reply-result-explanation" aria-label="结果说明"><h3>结果说明</h3>{executionDetails.map((detail, index) => <div className="reply-detail" key={index}>{renderSection(detail)}</div>)}</section>}
    {reply.unresolvedItems.length > 0 && <section className="reply-unresolved" aria-label={unresolvedLabel}><h3>{unresolvedLabel}</h3><ul>{reply.unresolvedItems.map((item, index) => <li key={index}>{renderSection(item)}</li>)}</ul></section>}
    {(supportingDetails.length > 0 || hasSources) && <details className="reply-details"><summary>更多信息</summary>
      {supportingDetails.map((detail, index) => <div className="reply-detail" key={index}>{renderSection(detail)}</div>)}
      {hasSources && <section className="reply-sources" aria-label="回答依据"><h3>回答依据</h3><div className="citation-list">{reply.sources.map((source, index) => <span
        className="citation-entry" key={JSON.stringify([source.versionId, source.unitId, source.checksum])}
      ><button type="button" className="citation"
          data-knowledge-version-id={source.versionId} data-knowledge-unit-id={source.unitId}
          aria-label={hasMultipleSources ? `打开依据 ${index + 1} · ${source.title}` : `打开依据：${source.title}`}
          onClick={() => openSource(source)}
        ><Icon name="book" />{hasMultipleSources ? `${index + 1}. ${source.title}` : source.title}</button>{index < reply.sources.length - 1 && <span aria-hidden="true">；</span>}</span>)}</div></section>}
    </details>}
  </div>;
}
