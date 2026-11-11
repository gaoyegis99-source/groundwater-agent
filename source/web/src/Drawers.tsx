import { useEffect, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { KnowledgeEvidenceView } from './KnowledgeEvidenceView';
import type { KnowledgeEvidence, KnowledgeEvidenceBundle, KnowledgeReference, ResourceDetail } from './types';
import './knowledge-polish.css';
export { sizeLabel } from './format';

export function ResourcePreview({ resource }: { resource: ResourceDetail; project: string }) {
  const object = resource.objects.find(item => item.role === 'raw') ?? resource.objects[0];
  return <dl className="argument-list"><div><dt>数据集</dt><dd>{resource.datasetId}</dd></div><div><dt>数据版本</dt><dd>{resource.versionId}</dd></div><div><dt>语义版本</dt><dd>{resource.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object?.objectId ?? '—'}</dd></div></dl>;
}

type KnowledgeDocument = { documentId: string; currentVersion: string; title: string; summary?: string; createdAt: string; sourceCount?: number; status?: string | null };
type KnowledgeSelection = { kind: 'reference'; reference: KnowledgeReference } | { kind: 'document'; document: KnowledgeDocument };
type KnowledgeDrawerProps = { project: string; reference?: KnowledgeReference; onClose: () => void };

export function KnowledgeDrawer(props: KnowledgeDrawerProps) {
  const { project, reference } = props;
  return <KnowledgeBrowser key={JSON.stringify([project, reference?.versionId, reference?.unitId, reference?.checksum])} {...props} />;
}

function KnowledgeBrowser({ project, reference, onClose }: KnowledgeDrawerProps) {
  const drawerRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const directoryRef = useRef<HTMLDivElement>(null);
  const selectedButton = useRef<HTMLButtonElement | null>(null);
  const directoryScroll = useRef(0);
  const restoreDirectory = useRef(false);
  const [search, setSearch] = useState('');
  const [catalogRequest, setCatalogRequest] = useState<{ query: string; cursor?: string } | null>(reference ? null : { query: '' });
  const [catalogRetry, setCatalogRetry] = useState(0);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(!reference);
  const [catalogError, setCatalogError] = useState<ApiError | null>(null);
  const [selection, setSelection] = useState<KnowledgeSelection | null>(reference ? { kind: 'reference', reference } : null);
  const [detailRetry, setDetailRetry] = useState(0);
  const [items, setItems] = useState<KnowledgeEvidence[]>([]);
  const [detailLoading, setDetailLoading] = useState(Boolean(reference));
  const [detailError, setDetailError] = useState<ApiError | null>(null);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    drawerRef.current?.focus();
    return () => { document.body.style.overflow = previousOverflow; if (opener?.isConnected) opener.focus(); };
  }, []);

  useEffect(() => {
    if (!catalogRequest) return;
    const controller = new AbortController();
    const { query, cursor } = catalogRequest;
    const params = new URLSearchParams({ limit: '100' });
    if (query) params.set('query', query);
    if (cursor) params.set('cursor', cursor);
    setCatalogLoading(true); setCatalogError(null);
    if (!cursor) { setDocuments([]); setNextCursor(null); }
    void callApi('knowledge_list_documents', `/knowledge/documents?${params}`, undefined, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        setDocuments(current => [...new Map([...(cursor ? current : []), ...page.items].map(item => [item.documentId, item])).values()]);
        setNextCursor(page.nextCursor); setCatalogLoading(false);
      }).catch(caught => {
        if (controller.signal.aborted) return;
        setCatalogLoading(false);
        if (caught instanceof ApiError) setCatalogError(caught); else throw caught;
      });
    return () => controller.abort();
  }, [catalogRequest, catalogRetry]);

  useEffect(() => {
    if (!selection) return;
    const controller = new AbortController();
    setDetailLoading(true); setDetailError(null); setItems([]);
    const request = selection.kind === 'reference'
      ? callApi('knowledge_read_evidence', '/knowledge/evidence/read', { refs: [selection.reference] }, controller.signal)
      : callApi('knowledge_lookup', '/knowledge/lookup', { key: selection.document.documentId }, controller.signal);
    void request.then(value => {
      if (controller.signal.aborted) return;
      const bundle = value as KnowledgeEvidenceBundle;
      if (selection.kind === 'reference' && !bundle.items.some(item => item.versionId === selection.reference.versionId && item.unitId === selection.reference.unitId && item.checksum === selection.reference.checksum)) {
        throw new ApiError(200, 'API_RESPONSE_INVALID');
      }
      setItems(bundle.items); setDetailLoading(false);
    }).catch(caught => {
      if (controller.signal.aborted) return;
      setDetailLoading(false);
      if (caught instanceof ApiError) setDetailError(caught); else throw caught;
    });
    return () => controller.abort();
  }, [selection, detailRetry]);

  useEffect(() => {
    if (!selection && restoreDirectory.current) {
      restoreDirectory.current = false;
      if (directoryRef.current) directoryRef.current.scrollTop = directoryScroll.current;
      (selectedButton.current?.isConnected ? selectedButton.current : searchRef.current)?.focus({ preventScroll: true });
    } else if (selection && !detailLoading && items.length) {
      drawerRef.current?.querySelector<HTMLElement>('[data-primary="true"], .evidence-article')?.focus();
    }
  }, [selection, detailLoading, items]);

  function back() {
    restoreDirectory.current = true;
    setSelection(null); setItems([]); setDetailError(null);
    if (!catalogRequest) setCatalogRequest({ query: search.trim() });
  }
  function keyDown(event: React.KeyboardEvent) {
    if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
    if (event.key !== 'Tab' || !drawerRef.current) return;
    const controls = [...drawerRef.current.querySelectorAll<HTMLElement>('*')].filter(element => element.matches('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]') && !element.closest('[hidden]') && (!element.closest('details:not([open])') || Boolean(element.closest('summary'))));
    const first = controls[0]; const last = controls.at(-1);
    if (!first || !last) { event.preventDefault(); drawerRef.current.focus(); return; }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === drawerRef.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return <aside ref={drawerRef} tabIndex={-1} className="drawer knowledge-drawer" role="dialog" aria-modal="true" aria-label="知识库" data-testid="knowledge-drawer" onKeyDown={keyDown}>
    <div className="drawer-heading"><div><span className="drawer-eyebrow">知识库</span><h2>知识与证据</h2><p>查找方法，核对引用原文与来源。</p></div><button className="icon-button" aria-label="关闭知识库" onClick={onClose}><Icon name="close" /></button></div>
    <div className="knowledge-directory" hidden={Boolean(selection)}>
      <form role="search" aria-label="知识目录搜索" className="knowledge-directory-search" onSubmit={event => { event.preventDefault(); selectedButton.current = null; directoryScroll.current = 0; setCatalogRequest({ query: search.trim() }); }}>
        <label>搜索知识目录<input ref={searchRef} type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="标题、摘要或知识编号" /></label><button type="submit">搜索</button>
      </form>
      <p className="knowledge-directory-count" role="status">{catalogLoading ? '正在读取知识目录…' : catalogError ? '目录读取未完成。' : `已加载 ${documents.length} 份知识${nextCursor ? '，可继续加载。' : '。'}`}</p>
      {catalogError && <div className="knowledge-read-error"><ErrorNotice error={catalogError} title="知识目录读取未完成" /><button onClick={() => setCatalogRetry(value => value + 1)}>重试读取目录</button></div>}
      <div ref={directoryRef} className="library-list knowledge-directory-results" role="region" aria-label="知识目录结果" aria-busy={catalogLoading}>
        {documents.map(item => <button className="library-item" key={item.documentId} title={item.title} onClick={event => { selectedButton.current = event.currentTarget; directoryScroll.current = directoryRef.current?.scrollTop ?? 0; setItems([]); setDetailLoading(true); setSelection({ kind: 'document', document: item }); }}><span className="file-icon knowledge"><Icon name="book" /></span><span><strong>{item.title}</strong>{item.summary && <span className="knowledge-directory-summary">{item.summary}</span>}<small>{item.status === 'WITHDRAWN' ? '已撤回' : '知识文档'}{item.sourceCount !== undefined ? ` · ${item.sourceCount} 个来源` : ''}</small></span><Icon name="arrow-up-right" /></button>)}
        {catalogLoading && !documents.length && <div className="knowledge-skeleton" aria-hidden="true"><span /><span /><span /></div>}
        {!catalogLoading && !catalogError && !documents.length && <p className="empty-inline">{catalogRequest?.query ? '没有找到匹配的知识，请调整关键词。' : '知识目录还没有内容。'}</p>}
      </div>
      {nextCursor && <button className="secondary knowledge-more" disabled={catalogLoading} onClick={() => setCatalogRequest({ query: catalogRequest?.query ?? '', cursor: nextCursor })}>{catalogLoading ? '正在加载…' : '加载更多知识'}</button>}
    </div>
    {selection && <div className="knowledge-reading">
      <button className="text-button knowledge-return" onClick={back}><Icon name="arrow-left" />返回知识目录</button>
      {detailLoading && <><p role="status">正在读取证据原文…</p><div className="knowledge-skeleton" aria-hidden="true"><span /><span /><span /></div></>}
      {detailError && <div className="knowledge-read-error"><ErrorNotice error={detailError} title="知识读取未完成" /><button onClick={() => setDetailRetry(value => value + 1)}>重新读取证据</button></div>}
      {!detailLoading && !detailError && !items.length && <div className="empty-inline" role="status">没有可读取的证据片段，请返回目录选择其他知识。</div>}
      {!detailLoading && !detailError && items.length > 0 && <KnowledgeEvidenceView items={items} reference={selection.kind === 'reference' ? selection.reference : undefined} project={project} />}
    </div>}
  </aside>;
}
