import { useCallback, useEffect, useId, useState } from 'react';
import { ApiError, callApi } from './api';
import { modelName, outputName } from './card-presentation';
import { statusLabel } from './chat-state';
import { selectResourcePublicationUi } from './ui-selectors';
import { useWorkItemPages } from './use-work-item-queries';
import { workItemTime } from './work-item-presentation';
import { readWorkItemOutput, readWorkItemPublication, readWorkItemRun, workItemOutputRows } from './work-item-result-evidence';
import type { components, operations } from './generated/public-api-v12';
import type { BusinessAssetReference, WorkItemDetail } from './types';
import type { WorkItemNamedOutput as NamedOutput, WorkItemRun as WorkflowRun, WorkItemRunLink as WorkflowLink } from './work-item-result-evidence';

type AssetSelection = operations['kernel_link_work_item_asset']['requestBody']['content']['application/json']['reference'];
type KnowledgeReference = Extract<BusinessAssetReference, { kind: 'KNOWLEDGE' }>;
type Document = components['schemas']['knowledge_next__DocumentSummary'];
type VersionSummary = components['schemas']['knowledge_next__VersionSummary'];
type SelectionProps = {
  disabled: boolean;
  onSelect?: (reference: AssetSelection) => void;
};
type Props = SelectionProps & {
  mode: 'knowledge' | 'run';
  project: string;
  detail: WorkItemDetail;
  pinnedKnowledge?: KnowledgeReference;
  onClose: () => void;
};

const checksumPattern = /^sha256:[0-9a-f]{64}$/;
const documentKey = (item: Document) => item.documentId;
const versionKey = (item: VersionSummary) => item.versionId;
const outputKey = (item: NamedOutput) => item.outputName;

function safePathSegment(id: string) {
  if (id === '.' || id === '..' || !id.trim()) throw new ApiError(409, 'WORK_ITEM_REFERENCE_MISMATCH');
  return encodeURIComponent(id);
}

function identityMismatch() {
  return new ApiError(409, 'WORK_ITEM_REFERENCE_MISMATCH');
}

function useAssetRead<T>(get: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [read, setRead] = useState<{ value: T } | { error: ApiError } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void get(controller.signal).then(value => {
      if (!controller.signal.aborted) setRead({ value });
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (!(error instanceof ApiError)) throw error;
      setRead({ error });
    });
    return () => controller.abort();
  }, [attempt, get]);

  return {
    value: read && 'value' in read ? read.value : undefined,
    error: read && 'error' in read ? read.error : undefined,
    loading: read === null,
    retry: () => { setRead(null); setAttempt(value => value + 1); },
  };
}

function ReadIssue({ label, error, retry }: { label: string; error: unknown; retry: () => void }) {
  if (!error) return null;
  return <div role="alert" className="work-item-issue">
    <span>{label}暂不可用。{error instanceof ApiError && (error.status === 0 || error.status >= 500) ? '服务连接未完成。' : ''}</span>
    <button type="button" onClick={retry}>重试</button>
  </div>;
}

function pagedQuery(cursor?: string) {
  const params = new URLSearchParams({ limit: '20' });
  if (cursor) params.set('cursor', cursor);
  return params;
}

function KnowledgeCatalog({ query, onDocument }: { query: string; onDocument: (document: Document) => void }) {
  const getPage = useCallback(async (cursor: string | undefined, signal: AbortSignal) => {
    const params = pagedQuery(cursor);
    if (query) params.set('query', query);
    const page = await callApi('knowledge_list_documents', `/knowledge/documents?${params}`, undefined, signal);
    return { items: page.items, nextAfter: page.nextCursor };
  }, [query]);
  const page = useWorkItemPages(getPage, documentKey);

  return <>
    <ReadIssue label="知识目录" error={page.error} retry={page.retry} />
    {page.loading && <p role="status">正在读取知识目录…</p>}
    {!page.loading && !page.error && !page.items.length && <p>没有找到匹配的知识。</p>}
    {page.items.length > 0 && <ul className="work-item-items" aria-label="知识目录结果">
      {page.items.map(item => <li key={item.documentId}>
        <div><strong>{item.title}</strong>
          {item.status === 'WITHDRAWN' && <span>当前版本已撤回</span>}
        </div>
        <button type="button" disabled={!!page.error} aria-label={`查看「${item.title}」版本`} onClick={() => onDocument(item)}>查看版本</button>
      </li>)}
    </ul>}
    {page.nextAfter !== null && <button type="button" disabled={page.loading || !!page.error} onClick={page.more}>加载更多知识</button>}
  </>;
}

function KnowledgeVersions({ document, onVersion }: { document: Document; onVersion: (reference: KnowledgeReference) => void }) {
  const getPage = useCallback(async (cursor: string | undefined, signal: AbortSignal) => {
    const path = `/knowledge/documents/${safePathSegment(document.documentId)}/versions?${pagedQuery(cursor)}`;
    const page = await callApi('knowledge_list_document_versions', path, undefined, signal);
    return { items: page.items, nextAfter: page.nextCursor };
  }, [document.documentId]);
  const page = useWorkItemPages(getPage, versionKey);

  return <>
    <ReadIssue label="知识版本记录" error={page.error} retry={page.retry} />
    {page.loading && <p role="status">正在读取知识版本记录…</p>}
    {!page.loading && !page.error && !page.items.length && <p>暂无可查看的知识版本。</p>}
    {page.items.length > 0 && <ul className="work-item-items" aria-label="知识版本记录">
      {page.items.map(item => <li key={item.versionId}>
        <div><strong>{item.versionId}</strong>
          <span>{item.versionId === document.currentVersion ? '当前版本' : '历史版本'} · {workItemTime(item.createdAt)}</span>
        </div>
        <button type="button" disabled={!!page.error} aria-label={`核对知识版本 ${item.versionId}`}
          onClick={() => onVersion({ kind: 'KNOWLEDGE', documentId: document.documentId, versionId: item.versionId, checksum: item.checksum })}>核对版本</button>
      </li>)}
    </ul>}
    {page.nextAfter !== null && <button type="button" disabled={page.loading || !!page.error} onClick={page.more}>加载更多版本</button>}
  </>;
}

function day(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const timestamp = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(timestamp.getTime()) && timestamp.toISOString().slice(0, 10) === value ? value : undefined;
}

function KnowledgeVersion({ reference, project, linked, disabled, onSelect }: SelectionProps & {
  reference: KnowledgeReference;
  project: string;
  linked?: boolean;
}) {
  const { versionId, documentId, checksum } = reference;
  const get = useCallback(async (signal: AbortSignal) => {
    if (!checksumPattern.test(checksum)) throw identityMismatch();
    const value = await callApi('knowledge_get_version', `/knowledge/versions/${safePathSegment(versionId)}`, undefined, signal);
    if (value.versionId !== versionId || value.documentId !== documentId || value.body.knowledgeId !== documentId
      || value.checksum !== checksum || value.body.bindings?.some(binding => binding.projectId !== project)
      || (value.validThrough !== null && !day(value.validThrough))) throw identityMismatch();
    return value;
  }, [versionId, documentId, checksum, project]);
  const { value, loading, error, retry } = useAssetRead(get);
  if (loading) return <p role="status">正在核对知识版本…</p>;
  if (error) return <ReadIssue label="知识版本" error={error} retry={retry} />;
  if (!value) return null;
  const today = new Date().toISOString().slice(0, 10);
  if (value.status === 'WITHDRAWN') return <p role="status">知识版本已撤回，当前无法作为计算依据。</p>;
  if (value.validThrough !== null && value.validThrough < today) return <p role="status">知识版本已过期，当前无法作为计算依据。</p>;
  if (value.status !== 'ACTIVE') return <p role="status">知识版本状态待核实。</p>;

  return <>
    <h4>{value.body.title}</h4>
    {value.body.summary && <p>{value.body.summary}</p>}
    <dl className="argument-list">
      <div><dt>文档 ID</dt><dd>{documentId}</dd></div>
      <div><dt>版本 ID</dt><dd>{versionId}</dd></div>
      <div><dt>版本校验值</dt><dd><code>{checksum}</code></dd></div>
      <div><dt>来源</dt><dd>{value.body.sources.map(source => source.title).join('、')}</dd></div>
      <div><dt>复核日期</dt><dd>{value.reviewAfter}{value.reviewAfter < today ? '（待复核）' : ''}</dd></div>
      {value.validThrough !== null && <div><dt>有效期至</dt><dd>{value.validThrough}</dd></div>}
    </dl>
    {onSelect && <button type="button" disabled={disabled || linked}
      onClick={() => onSelect(reference)}>{linked ? '此知识版本已关联' : '关联此知识版本'}</button>}
  </>;
}

function KnowledgePicker({ project, detail, disabled, onSelect }: Omit<Props, 'mode' | 'onClose' | 'pinnedKnowledge'>) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState({ text: '', revision: 0 });
  const [document, setDocument] = useState<Document | null>(null);
  const [reference, setReference] = useState<KnowledgeReference | null>(null);

  if (reference) {
    const linked = detail.assets.some(({ state, reference: asset }) => state === 'AVAILABLE' && asset?.kind === 'KNOWLEDGE'
      && asset.documentId === reference.documentId && asset.versionId === reference.versionId && asset.checksum === reference.checksum);
    return <>
      <button key="versions" autoFocus type="button" onClick={() => setReference(null)}>返回版本记录</button>
      <KnowledgeVersion reference={reference} project={project} disabled={disabled}
        onSelect={onSelect} linked={linked} />
    </>;
  }
  if (document) return <>
    <button key="catalog" autoFocus type="button" onClick={() => setDocument(null)}>返回知识目录</button>
    <KnowledgeVersions document={document} onVersion={setReference} />
  </>;
  return <>
    <form role="search" aria-label="知识目录搜索" className="work-item-actions" onSubmit={event => {
      event.preventDefault();
      setQuery(previous => ({ text: search.trim(), revision: previous.revision + 1 }));
    }}>
      <label>搜索知识目录<input autoFocus type="search" value={search} maxLength={512} onChange={event => setSearch(event.target.value)} /></label>
      <button type="submit">搜索</button>
    </form>
    <KnowledgeCatalog key={query.revision} query={query.text} onDocument={setDocument} />
  </>;
}

async function verifyPublishedOutput(output: NamedOutput, project: string, publicationId: string, signal: AbortSignal) {
  try {
    await readWorkItemOutput(output, project, publicationId, signal);
    return true;
  } catch (error) {
    if (signal.aborted || error instanceof ApiError) return false;
    throw error;
  }
}

function PublishedOutputs({ runId, outputs, project, publicationId, assets, disabled, onSelect }: SelectionProps & {
  runId: string;
  outputs: NamedOutput[];
  project: string;
  publicationId: string;
  assets: WorkItemDetail['assets'];
}) {
  const getPage = useCallback(async (cursor: number | undefined, signal: AbortSignal) => {
    const first = cursor ?? 0;
    const batch = outputs.slice(first, first + 8);
    const visible = await Promise.all(batch.map(output => verifyPublishedOutput(output, project, publicationId, signal)));
    const next = first + batch.length;
    return { items: batch.filter((_, index) => visible[index]), nextAfter: next < outputs.length ? next : null };
  }, [outputs, project, publicationId]);
  const page = useWorkItemPages(getPage, outputKey);

  return <>
    <ReadIssue label="成果核对" error={page.error} retry={page.retry} />
    {page.loading && <p role="status">正在核对已发布的成果…</p>}
    {!page.loading && !page.error && page.items.length < (page.nextAfter ?? outputs.length) &&
      <p role="status">部分已发布成果暂不可用。 <button type="button" onClick={page.reload}>重新核对成果</button></p>}
    {page.items.length > 0 && <ul className="work-item-items" aria-label="已核对的发布成果">
      {page.items.map(output => {
        const name = outputName(output.outputName);
        const linked = assets.some(({ state, reference }) => state === 'AVAILABLE' && reference?.kind === 'RESULT'
          && reference.runId === runId && reference.outputName === output.outputName);
        return <li key={output.outputName}>
          <div><strong>{name}</strong><span>{output.format.toUpperCase()} · {output.versionId}</span></div>
          {onSelect && <button type="button" disabled={disabled || linked}
            aria-label={linked ? `${name}已关联` : `关联成果 · ${name}`}
            onClick={() => onSelect({ kind: 'RESULT', runId, outputName: output.outputName })}>{linked ? '已关联' : '关联成果'}</button>}
        </li>;
      })}
    </ul>}
    {page.nextAfter !== null && <button type="button" disabled={page.loading || !!page.error} onClick={page.more}>核对更多成果</button>}
  </>;
}

function RunPublication({ run, project, assets, disabled, onSelect }: SelectionProps & {
  run: WorkflowRun;
  project: string;
  assets: WorkItemDetail['assets'];
}) {
  const { runId } = run;
  const get = useCallback(async (signal: AbortSignal) => {
    const publication = await readWorkItemPublication(runId, project, signal);
    const outputs = workItemOutputRows(run).flatMap(row => row.output ? [row.output] : []);
    return { publication, outputs };
  }, [run, runId, project]);
  const { value, loading, error, retry } = useAssetRead(get);
  if (loading) return <p role="status">正在读取成果发布状态…</p>;
  if (error) return <ReadIssue label="成果发布状态" error={error} retry={retry} />;
  if (!value) return null;
  const { publication, outputs } = value;

  if (publication.state !== 'PUBLISHED' || !publication.publicationId) return <div className="work-item-actions">
    <p role="status">{publication.state ? `成果尚未发布（${selectResourcePublicationUi({ state: publication.state }).label}）` : '成果发布状态待核验。'}</p>
    <button type="button" onClick={retry}>刷新发布状态</button>
  </div>;

  return <>
    <p>已发布 · {publication.publicationId}</p>
    {outputs.length ? <PublishedOutputs runId={runId} outputs={outputs} project={project} publicationId={publication.publicationId}
      assets={assets} disabled={disabled} onSelect={onSelect} /> : <p>暂无可关联的具名成果。</p>}
  </>;
}

function RunDetails({ link, project, assets, disabled, onSelect }: SelectionProps & {
  link: WorkflowLink;
  project: string;
  assets: WorkItemDetail['assets'];
}) {
  const { runId, sessionId } = link;
  const get = useCallback((signal: AbortSignal) => readWorkItemRun(link, project, signal), [link, project]);
  const { value, loading, error, retry } = useAssetRead(get);
  if (loading) return <p role="status">正在读取计算记录…</p>;
  if (error) return <ReadIssue label="计算记录" error={error} retry={retry} />;
  if (!value) return null;
  const linked = assets.some(({ state, reference }) => state === 'AVAILABLE' && reference?.kind === 'WORKFLOW' && reference.runId === runId);

  return <>
    <h4>{modelName(value.modelId)}</h4>
    <dl className="argument-list">
      <div><dt>计算记录</dt><dd>{runId}</dd></div>
      <div><dt>来源会话</dt><dd>{sessionId}</dd></div>
      <div><dt>执行状态</dt><dd>{Object.hasOwn(statusLabel, value.status) ? statusLabel[value.status] : '待核实'}</dd></div>
      <div><dt>工作流</dt><dd>{value.workflowId} · {value.workflowVersion}</dd></div>
      <div><dt>工作流校验值</dt><dd><code>{value.workflowChecksum}</code></dd></div>
      <div><dt>模型</dt><dd>{value.modelId} · {value.modelVersion}</dd></div>
      <div><dt>执行时间</dt><dd>{workItemTime(value.startedAt ?? value.createdAt)}{value.finishedAt && ` 至 ${workItemTime(value.finishedAt)}`}</dd></div>
    </dl>
    {onSelect && <div className="work-item-actions"><button type="button" disabled={disabled || linked}
      onClick={() => onSelect({ kind: 'WORKFLOW', runId })}>{linked ? '此计算记录已关联' : '关联此计算记录'}</button></div>}
    <h4>发布成果</h4>
    {value.status === 'SUCCEEDED' ? <RunPublication run={value} project={project} assets={assets} disabled={disabled} onSelect={onSelect} />
      : <p role="status">执行尚无可关联的成果。 <button type="button" onClick={retry}>刷新计算记录</button></p>}
  </>;
}

function RunPicker({ project, detail, disabled, onSelect }: Omit<Props, 'mode' | 'onClose' | 'pinnedKnowledge'>) {
  const [runId, setRun] = useState<string | null>(null);
  const run = detail.workflowRuns.find(item => item.runId === runId);
  if (run) return <>
    <button autoFocus type="button" onClick={() => setRun(null)}>返回计算记录</button>
    <RunDetails key={JSON.stringify(run)} link={run} project={project} assets={detail.assets} disabled={disabled} onSelect={onSelect} />
  </>;
  if (!detail.workflowRuns.length) return <p>当前工作暂无可查看的计算记录。</p>;
  return <ul className="work-item-items" aria-label="工作中的计算记录">
    {detail.workflowRuns.map(item => <li key={item.runId}>
      <div><strong>{item.workflowRef.id} · {item.workflowRef.version}</strong><span>{item.runId}</span></div>
      <button type="button" aria-label={`查看计算记录 ${item.runId}`} onClick={() => setRun(item.runId)}>查看计算记录</button>
    </li>)}
  </ul>;
}

export function WorkItemOtherAssets({ mode, project, detail, pinnedKnowledge, disabled, onClose, onSelect }: Props) {
  const titleId = useId();
  const [verification, setVerification] = useState(0);
  useEffect(() => {
    const reload = () => setVerification(current => current + 1);
    const visible = () => { if (document.visibilityState === 'visible') reload(); };
    window.addEventListener('focus', reload);
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.removeEventListener('focus', reload);
      document.removeEventListener('visibilitychange', visible);
    };
  }, []);
  const title = pinnedKnowledge ? '已关联知识版本' : mode === 'knowledge' ? '关联知识' : '关联计算记录或成果';
  const key = JSON.stringify([project, detail.workItem.workItemId, pinnedKnowledge?.documentId, pinnedKnowledge?.versionId, pinnedKnowledge?.checksum, verification]);

  return <section className="work-session-picker" aria-labelledby={titleId}>
    <div className="work-item-actions"><h3 id={titleId}>{title}</h3>
      <button autoFocus={Boolean(pinnedKnowledge) || mode === 'run'} type="button" onClick={onClose}>关闭</button>
    </div>
    {detail.workItem.projectId !== project ? <p role="alert">关联资料暂不可用。</p>
      : pinnedKnowledge ? <KnowledgeVersion key={key} project={project} reference={pinnedKnowledge} disabled={disabled} />
        : mode === 'knowledge' ? <KnowledgePicker key={key} project={project} detail={detail} disabled={disabled} onSelect={onSelect} />
          : <RunPicker key={key} project={project} detail={detail} disabled={disabled} onSelect={onSelect} />}
  </section>;
}
