import { useEffect, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import type { KnowledgeReference, ResourceDetail } from './types';
export { sizeLabel } from './format';

type Evidence = { status: string; versionId: string; unitId: string; checksum: string; knowledgeId?: string; content?: string; locator?: Record<string, string | number>; sources?: { sourceId: string; title: string; publisher: string; url?: string | null }[]; bindingMatch?: string; scope?: string };
type EvidenceBundle = { status: string; items: Evidence[] };

const evidenceStatusLabels: Record<string, string> = {
  OK: '有效', STALE: '已失效', WITHDRAWN: '已撤回', EXPIRED: '已过期',
  NOT_FOUND: '未找到', NEEDS_EXPANSION: '需要补充读取',
};
const evidenceScopeLabels: Record<string, string> = { GENERAL: '通用知识', DATA_BOUND: '数据绑定知识' };
const bindingMatchLabels: Record<string, string> = {
  GENERAL: '通用匹配', EXACT: '精确匹配', NOT_CHECKED: '未核对', MISMATCH: '不匹配',
};

function localizedLabel(value: string | undefined, labels: Record<string, string>) {
  return value ? labels[value] ?? value : '';
}

export function ResourcePreview({ resource }: { resource: ResourceDetail; project: string }) {
  const object = resource.objects.find(item => item.role === 'raw') ?? resource.objects[0];
  return <dl className="argument-list"><div><dt>数据集</dt><dd>{resource.datasetId}</dd></div><div><dt>数据版本</dt><dd>{resource.versionId}</dd></div><div><dt>语义版本</dt><dd>{resource.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object?.objectId ?? '—'}</dd></div></dl>;
}

export function KnowledgeDrawer({ reference, onClose }: { project: string; reference?: KnowledgeReference; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement>(null);
  const [documents, setDocuments] = useState<{ documentId: string; currentVersion: string; title: string }[]>([]);
  const [items, setItems] = useState<Evidence[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  async function read(ref: KnowledgeReference) {
    const bundle = await callApi('knowledge_read_evidence', '/knowledge/evidence/read', {
      refs: [{ versionId: ref.versionId, unitId: ref.unitId, checksum: ref.checksum }],
    }) as EvidenceBundle;
    setItems(bundle.items); setError(null);
  }
  useEffect(() => {
    drawerRef.current?.focus();
    if (reference) void read(reference).catch(caught => { if (caught instanceof ApiError) setError(caught); else throw caught; });
    else void callApi('knowledge_list_documents', '/knowledge/documents?limit=100').then(page => setDocuments(page.items)).catch(caught => { if (caught instanceof ApiError) setError(caught); else throw caught; });
  }, [reference]);
  async function lookup(id: string) { const bundle = await callApi('knowledge_lookup', '/knowledge/lookup', { key: id }) as EvidenceBundle; setItems(bundle.items); }
  return <aside ref={drawerRef} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-label="知识库" data-testid="knowledge-drawer" onKeyDown={event => { if (event.key === 'Escape') onClose(); }}>
    <div className="drawer-heading"><div><span className="drawer-eyebrow">新版知识库</span><h2>知识与证据</h2><p>仅显示当前生命周期有效且完整读取的单元级证据。</p></div><button className="icon-button" aria-label="关闭知识库" onClick={onClose}><Icon name="close" /></button></div>
    {error && <ErrorNotice error={error} title="知识读取未完成" />}
    {items.length > 0 && <><button className="text-button" onClick={() => setItems([])}><Icon name="arrow-left" />返回知识目录</button><div className="library-list">{items.map(item => <article className="knowledge-body" key={`${item.versionId}:${item.unitId}`}><header className="detail-hero"><div className="detail-kicker"><span className="status-badge status-active">{localizedLabel(item.status, evidenceStatusLabels)}</span><span>{[localizedLabel(item.scope, evidenceScopeLabels), localizedLabel(item.bindingMatch, bindingMatchLabels)].filter(Boolean).join(' · ')}</span></div><h3>{item.knowledgeId ?? item.unitId}</h3></header><p className="prose knowledge-content">{item.content}</p><dl className="argument-list"><div><dt>知识版本</dt><dd>{item.versionId}</dd></div><div><dt>证据单元</dt><dd>{item.unitId}</dd></div><div><dt>校验和</dt><dd>{item.checksum}</dd></div></dl>{item.sources?.map(source => <p key={source.sourceId}>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title}<small>{source.publisher}</small></p>)}</article>)}</div></>}
    {!items.length && <div className="library-list">{documents.map(item => <button className="library-item" key={item.documentId} onClick={() => void lookup(item.documentId)}><span className="file-icon knowledge"><Icon name="book" /></span><span><strong>{item.title}</strong><small>知识版本 {item.currentVersion}</small></span><Icon name="arrow-up-right" /></button>)}</div>}
  </aside>;
}
