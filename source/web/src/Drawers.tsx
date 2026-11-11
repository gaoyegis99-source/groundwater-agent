import { useEffect, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { KnowledgeEvidenceView } from './KnowledgeEvidenceView';
import type { KnowledgeEvidence, KnowledgeEvidenceBundle, KnowledgeReference, ResourceDetail } from './types';
export { sizeLabel } from './format';

export function ResourcePreview({ resource }: { resource: ResourceDetail; project: string }) {
  const object = resource.objects.find(item => item.role === 'raw') ?? resource.objects[0];
  return <dl className="argument-list"><div><dt>数据集</dt><dd>{resource.datasetId}</dd></div><div><dt>数据版本</dt><dd>{resource.versionId}</dd></div><div><dt>语义版本</dt><dd>{resource.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object?.objectId ?? '—'}</dd></div></dl>;
}

export function KnowledgeDrawer({ project, reference, onClose }: { project: string; reference?: KnowledgeReference; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement>(null);
  const [documents, setDocuments] = useState<{ documentId: string; currentVersion: string; title: string }[]>([]);
  const [items, setItems] = useState<KnowledgeEvidence[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  async function read(ref: KnowledgeReference) {
    const bundle = await callApi('knowledge_read_evidence', '/knowledge/evidence/read', {
      refs: [{ versionId: ref.versionId, unitId: ref.unitId, checksum: ref.checksum }],
    }) as KnowledgeEvidenceBundle;
    setItems(bundle.items); setError(null);
  }
  useEffect(() => {
    drawerRef.current?.focus();
    if (reference) void read(reference).catch(caught => { if (caught instanceof ApiError) setError(caught); else throw caught; });
    else void callApi('knowledge_list_documents', '/knowledge/documents?limit=100').then(page => setDocuments(page.items)).catch(caught => { if (caught instanceof ApiError) setError(caught); else throw caught; });
  }, [reference]);
  async function lookup(id: string) { const bundle = await callApi('knowledge_lookup', '/knowledge/lookup', { key: id }) as KnowledgeEvidenceBundle; setItems(bundle.items); }
  return <aside ref={drawerRef} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-label="知识库" data-testid="knowledge-drawer" onKeyDown={event => { if (event.key === 'Escape') onClose(); }}>
    <div className="drawer-heading"><div><span className="drawer-eyebrow">新版知识库</span><h2>知识与证据</h2><p>仅显示当前生命周期有效且完整读取的单元级证据。</p></div><button className="icon-button" aria-label="关闭知识库" onClick={onClose}><Icon name="close" /></button></div>
    {error && <ErrorNotice error={error} title="知识读取未完成" />}
    {items.length > 0 && <><button className="text-button" onClick={() => setItems([])}><Icon name="arrow-left" />返回知识目录</button><KnowledgeEvidenceView items={items} reference={reference} project={project} /></>}
    {!items.length && <div className="library-list">{documents.map(item => <button className="library-item" key={item.documentId} onClick={() => void lookup(item.documentId)}><span className="file-icon knowledge"><Icon name="book" /></span><span><strong>{item.title}</strong><small>知识版本 {item.currentVersion}</small></span><Icon name="arrow-up-right" /></button>)}</div>}
  </aside>;
}
