import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ApiError, callApi, contentUrl, readDataVersion, readDataVersionSemantic, readDataVersionWithHead } from './api';
import { DataFieldQuality } from './DataFieldQuality';
import { DataSemanticHistory } from './DataSemanticHistory';
import { DataVersionQuality } from './DataVersionQuality';
import { sizeLabel } from './format';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import type { ResourceDetail, ResourceItem, ResourceReference } from './types';
import { resourceItemFromDetail } from './resource-item';
import { containTabFocus, useWorkbenchModal } from './workbench-focus';
import { EmptyState, Loading } from './ui/Feedback';
import { LinkedWorkData, type LinkedWorkScope } from './LinkedWorkData';
import './workbench-polish.css';

export { resourceItemFromDetail } from './resource-item';

const ArtifactViewer = lazy(() => import('./ArtifactViewer').then(module => ({ default: module.ArtifactViewer })));

type Dataset = { datasetId: string; title: string; currentVersion: string; tags?: string[]; formats?: string[]; semantics?: { description?: string } };
type VersionItem = { versionId: string; semanticId: string; createdAt: string };
const FORMATS = ['', 'csv', 'parquet', 'xlsx', 'json', 'geojson', 'geopackage', 'geotiff', 'cog', 'png', 'pytorch-state-dict', 'zip'];

function VersionDiff({ left, right }: { left: ResourceDetail; right: ResourceDetail }) {
  const leftFields = new Map((left.descriptor.fields ?? []).map(field => [field.name, field]));
  const rightFields = new Map((right.descriptor.fields ?? []).map(field => [field.name, field]));
  const added = [...rightFields.keys()].filter(name => !leftFields.has(name));
  const removed = [...leftFields.keys()].filter(name => !rightFields.has(name));
  const changed = [...leftFields.keys()].filter(name => rightFields.has(name) && JSON.stringify(leftFields.get(name)) !== JSON.stringify(rightFields.get(name)));
  const semanticKeys = new Set([...Object.keys(left.semantics), ...Object.keys(right.semantics)]);
  const semanticChanges = [...semanticKeys].filter(name => JSON.stringify(left.semantics[name as keyof typeof left.semantics]) !== JSON.stringify(right.semantics[name as keyof typeof right.semantics]));
  const semanticValue = (value: unknown) => value === undefined ? '∅' : JSON.stringify(value);
  const objects = (value: ResourceDetail) => value.objects.map(item => `${item.role}:${item.objectId}`).join('、') || '无';
  const formats = (value: ResourceDetail) => value.objects.map(item => `${item.role}:${item.format}/${item.mediaType}`).join('、') || '无';
  const checksums = (value: ResourceDetail) => value.objects.map(item => `${item.role}:${item.checksum}`).join('、') || '无';
  const sizes = (value: ResourceDetail) => value.objects.map(item => `${item.role}:${sizeLabel(item.size)}`).join('、') || '无';
  return <dl className="version-diff">
    <div><dt>数据集</dt><dd>{left.datasetId} → {right.datasetId}</dd></div>
    <div><dt>版本</dt><dd>{left.versionId} → {right.versionId}</dd></div>
    <div><dt>语义版本</dt><dd>{left.semanticId} → {right.semanticId}</dd></div>
    <div><dt>对象身份</dt><dd>{objects(left)} → {objects(right)}</dd></div>
    <div><dt>格式与 MIME</dt><dd>{formats(left)} → {formats(right)}</dd></div>
    <div><dt>checksum</dt><dd>{checksums(left)} → {checksums(right)}</dd></div>
    <div><dt>对象大小</dt><dd>{sizes(left)} → {sizes(right)}</dd></div>
    <div><dt>血缘输入版本</dt><dd>{left.inputVersions.join('、') || '无'} → {right.inputVersions.join('、') || '无'}</dd></div>
    <div><dt>字段新增</dt><dd>{added.join('、') || '无'}</dd></div>
    <div><dt>字段移除</dt><dd>{removed.join('、') || '无'}</dd></div>
    <div><dt>字段变化</dt><dd>{changed.join('、') || '无'}</dd></div>
    <div><dt>语义变化</dt><dd>{semanticChanges.length ? <ul>{semanticChanges.map(name => <li key={name}><strong>{name}</strong>：{semanticValue(left.semantics[name as keyof typeof left.semantics])} → {semanticValue(right.semantics[name as keyof typeof right.semantics])}</li>)}</ul> : '无'}</dd></div>
  </dl>;
}

export function DatabaseWorkbench({ project, initialId, reference, compare = false, allowedFormats, inputRole,
  inspectMetadata = false, linkedWork, chooseLabel, onChoose, onClose, onAttach }: {
  project: string; initialId?: string; reference?: ResourceReference; compare?: boolean; allowedFormats?: string[]; inputRole?: string;
  onClose: () => void; onAttach?: (item: ResourceItem) => void;
  inspectMetadata?: boolean;
  linkedWork?: LinkedWorkScope;
  chooseLabel?: string;
  onChoose?: (item: ResourceItem, detail: ResourceDetail) => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const detail = useRef<HTMLElement>(null);
  const preview = useRef<HTMLElement>(null);
  const [query, setQuery] = useState(''); const [tag, setTag] = useState(''); const [format, setFormat] = useState(allowedFormats?.length === 1 ? allowedFormats[0] : '');
  const formatsKey = (allowedFormats ?? []).map(value => value.toLowerCase()).join(',');
  const [datasets, setDatasets] = useState<Dataset[]>([]); const [cursor, setCursor] = useState<string | null>(null); const [catalogLoading, setCatalogLoading] = useState(true);
  const [target, setTarget] = useState(initialId ?? ''); const [loadedVersion, setVersion] = useState<ResourceDetail | null>(null); const [versions, setVersions] = useState<VersionItem[]>([]); const [objectId, setObjectId] = useState('');
  const [semanticId, setSemanticId] = useState('');
  const [currentSemanticId, setCurrentSemanticId] = useState('');
  const [described, setDescribed] = useState<{ key: string; semanticId: string }>();
  const [linkedReference, setLinkedReference] = useState<ResourceReference>();
  const [reviewingHead, setReviewingHead] = useState(false);
  const pinned = reviewingHead ? undefined
    : linkedReference?.versionId === target ? linkedReference : reference?.versionId === target ? reference : undefined;
  const semanticTarget = semanticId || pinned?.semanticId;
  const showMetadata = inspectMetadata || !!linkedWork;
  const targetKey = JSON.stringify([project, target]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const historyRequest = useRef<AbortController | null>(null);
  const [versionScope, setVersionScope] = useState(project);
  // A pending selection must never expose the prior version's attach/download actions.
  const version = loadedVersion?.versionId === target && versionScope === project
    && (!semanticTarget || loadedVersion.semanticId === semanticTarget) ? loadedVersion : null;
  const [targetFormat, setTargetFormat] = useState('');
  const [compareTarget, setCompareTarget] = useState<VersionItem | null>(null); const [loadedComparison, setComparison] = useState<ResourceDetail | null>(null);
  const comparison = loadedComparison && compareTarget && loadedComparison.versionId === compareTarget.versionId
    && loadedComparison.semanticId === compareTarget.semanticId && loadedComparison.datasetId === version?.datasetId ? loadedComparison : null;
  const [previewRequested, setPreviewRequested] = useState(false);
  const [catalogError, setCatalogError] = useState<ApiError | null>(null);
  const [versionError, setVersionError] = useState<ApiError | null>(null);
  const [historyError, setHistoryError] = useState<ApiError | null>(null);
  const [compareError, setCompareError] = useState<ApiError | null>(null);
  const [versionLoading, setVersionLoading] = useState(!!initialId);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [catalogRetry, setCatalogRetry] = useState(0), [versionRetry, setVersionRetry] = useState(0);
  const [historyRetry, setHistoryRetry] = useState(0), [compareRetry, setCompareRetry] = useState(0);
  const catalogRequest = useRef<AbortController | null>(null);

  useWorkbenchModal(dialog, true, initialId ? undefined : 'input[type="search"]');
  useEffect(() => {
    setTarget(initialId ?? ''); setTargetFormat(''); setSemanticId(''); setLinkedReference(undefined); setReviewingHead(false);
    setCompareTarget(null); setPreviewRequested(false);
  }, [initialId, project]);
  useEffect(() => {
    const controller = new AbortController();
    catalogRequest.current = controller;
    setCatalogLoading(true); setCatalogError(null); setDatasets([]); setCursor(null);
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ limit: '30' });
      if (query.trim()) params.set('query', query.trim()); if (tag.trim()) params.set('tag', tag.trim()); if (format) params.set('format', format);
      void callApi('data_list_datasets', `/data/datasets?${params}`, undefined, controller.signal).then(value => {
        if (controller.signal.aborted) return;
        setDatasets(value.items as Dataset[]); setCursor(value.nextCursor); setCatalogLoading(false);
      }).catch(caught => { if (controller.signal.aborted) return; setCatalogError(caught as ApiError); setCatalogLoading(false); });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, tag, format, project, catalogRetry]);
  useEffect(() => {
    setVersion(null); setVersions([]); setObjectId(''); setComparison(null); setVersionError(null);
    if (!target) { setVersionLoading(false); return; }
    const controller = new AbortController();
    setVersionLoading(true);
    const pin = pinned && semanticTarget ? { ...pinned, semanticId: semanticTarget } : pinned;
    void readDataVersionWithHead(target, controller.signal, pin).then(async ({ detail, currentSemanticId }) => {
      if (showMetadata && detail.project !== project) throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
      return { value: semanticTarget ? await readDataVersionSemantic(detail, semanticTarget, controller.signal) : detail, currentSemanticId };
    }).then(({ value, currentSemanticId }) => {
      if (controller.signal.aborted) return;
      const frozenObjectId = pinned?.objectId;
      const formats = formatsKey ? formatsKey.split(',') : [];
      const candidates = value.objects.filter(item => !formats.length || formats.includes(item.format.toLowerCase()));
      const raw = candidates.find(item => item.role === 'raw');
      const preferred = (reviewingHead || inputRole ? raw : undefined)
        ?? candidates.find(item => targetFormat && item.format.toLowerCase() === targetFormat)
        ?? raw ?? candidates[0] ?? value.objects[0];
      setDescribed({ key: targetKey, semanticId: value.semanticId });
      setCurrentSemanticId(currentSemanticId);
      setVersion(value); setVersionScope(project); setObjectId(frozenObjectId ?? preferred?.objectId ?? '');
      setVersionLoading(false);
    }).catch(caught => { if (controller.signal.aborted) return; setVersionError(caught as ApiError); setVersionLoading(false); });
    return () => controller.abort();
  }, [target, targetFormat, project, versionRetry, pinned, semanticTarget, formatsKey, showMetadata, targetKey, reviewingHead, inputRole]);
  useEffect(() => {
    if (!showMetadata) return;
    const reloadVersion = () => {
      setVersion(null);
      setVersionRetry(value => value + 1);
    };
    window.addEventListener('focus', reloadVersion);
    return () => window.removeEventListener('focus', reloadVersion);
  }, [showMetadata]);
  useEffect(() => {
    setVersions([]); setHistoryCursor(null); setHistoryError(null);
    if (!version) { setHistoryLoading(false); return; }
    const controller = new AbortController();
    historyRequest.current = controller;
    void readVersionHistory(version.datasetId, null, controller.signal);
    return () => controller.abort();
  }, [version?.versionId, project, historyRetry]);
  useEffect(() => {
    setComparison(null); setCompareError(null);
    if (!version || !compareTarget || compareTarget.versionId === version.versionId) { setCompareLoading(false); return; }
    const controller = new AbortController();
    setCompareLoading(true);
    void readDataVersion(compareTarget.versionId, controller.signal).then(value => {
      if (value.datasetId !== version.datasetId) throw new ApiError(409, 'DATASET_VERSION_FAMILY_MISMATCH');
      return readDataVersionSemantic(value, compareTarget.semanticId, controller.signal);
    }).then(value => {
      if (controller.signal.aborted) return;
      setComparison(value); setCompareLoading(false);
    }).catch(caught => { if (controller.signal.aborted) return; setCompareError(caught as ApiError); setCompareLoading(false); });
    return () => controller.abort();
  }, [compareTarget, version?.versionId, project, compareRetry]);

  async function readVersionHistory(datasetId: string, after: string | null, signal: AbortSignal) {
    setHistoryLoading(true);
    setHistoryError(null);
    const params = new URLSearchParams({ limit: '100' });
    if (after) params.set('cursor', after);
    try {
      const history = await callApi('data_list_dataset_versions', `/data/datasets/${encodeURIComponent(datasetId)}/versions?${params}`, undefined, signal);
      if (signal.aborted) return;
      setVersions(previous => after ? [...new Map([...previous, ...history.items].map(item => [item.versionId, item])).values()] : history.items);
      setHistoryCursor(history.nextCursor);
    } catch (caught) {
      if (!signal.aborted) setHistoryError(caught as ApiError);
    } finally {
      if (!signal.aborted) setHistoryLoading(false);
    }
  }

  function moreVersions() {
    const signal = historyRequest.current?.signal;
    if (!version || !historyCursor || !signal || signal.aborted || historyLoading) return;
    void readVersionHistory(version.datasetId, historyCursor, signal);
  }

  function keyDown(event: React.KeyboardEvent) {
    if (event.defaultPrevented) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); return; }
    containTabFocus(event, event.currentTarget as HTMLElement);
  }
  async function more() {
    const controller = catalogRequest.current;
    if (!cursor || catalogLoading || !controller || controller.signal.aborted) return; setCatalogLoading(true); setCatalogError(null);
    const params = new URLSearchParams({ limit: '30', cursor });
    if (query.trim()) params.set('query', query.trim()); if (tag.trim()) params.set('tag', tag.trim()); if (format) params.set('format', format);
    try { const value = await callApi('data_list_datasets', `/data/datasets?${params}`, undefined, controller.signal); if (!controller.signal.aborted) { setDatasets(current => [...new Map([...current, ...value.items as Dataset[]].map(item => [item.datasetId, item])).values()]); setCursor(value.nextCursor); } }
    catch (caught) { if (!controller.signal.aborted) setCatalogError(caught as ApiError); }
    finally { if (!controller.signal.aborted) setCatalogLoading(false); }
  }
  function selectVersion(id: string, preferredFormat = '', link?: ResourceReference) {
    if (id !== target || preferredFormat !== targetFormat || semanticId || linkedReference !== link || reviewingHead) setVersion(null);
    setTargetFormat(preferredFormat); setTarget(id); setSemanticId(''); setLinkedReference(link);
    setReviewingHead(false);
    setCompareTarget(null); setPreviewRequested(false);
  }
  const object = version?.objects.find(item => item.objectId === objectId) ?? version?.objects[0];
  const permitted = !formatsKey || (!!object && formatsKey.split(',').includes(object.format.toLowerCase()));
  const canChangeSemantic = showMetadata && !linkedWork;
  const permissionLost = versionError && [401, 403, 404, 409].includes(versionError.status);
  const anySelect = !!onAttach || !!onChoose;
  const historicalSemantic = !!version && version.semanticId !== currentSemanticId;
  const uniqueRaw = object?.role === 'raw' && version?.objects.filter(item => item.role === 'raw').length === 1;
  const blockedForInput = !!onAttach && (showMetadata || !!inputRole) && (historicalSemantic || !uniqueRaw);
  const selectableVersions = version && !versions.some(item => item.versionId === version.versionId) ? [{ versionId: version.versionId, semanticId: version.semanticId, createdAt: version.createdAt ?? '' }, ...versions] : versions;
  const versionHistory = version && <>
    <div className="version-control"><label>不可变版本<select value={version.versionId} onChange={event => selectVersion(event.target.value)}>{selectableVersions.map(item => <option key={item.versionId} value={item.versionId}>{item.versionId}</option>)}</select></label>
      {version.createdAt && <time dateTime={version.createdAt}>{new Date(version.createdAt).toLocaleString('zh-CN')}</time>}</div>
    {historyLoading && <Loading>正在读取版本列表…</Loading>}
    {historyError ? <div className="workbench-read-error"><ErrorNotice error={historyError} title="版本列表读取失败" /><button onClick={() => historyCursor ? moreVersions() : setHistoryRetry(value => value + 1)}>重新读取版本列表</button></div>
      : historyCursor && <button type="button" disabled={historyLoading} onClick={moreVersions}>加载更多版本</button>}
  </>;
  return <section ref={dialog} tabIndex={-1} className="database-workbench" role="dialog" aria-modal="true" aria-label="数据库" data-testid="database-workbench" onKeyDown={keyDown}>
    <header className="database-heading"><div><h2>数据库</h2>
      {inputRole && <p>为 <strong>{inputRole}</strong> 选择数据{formatsKey ? ` · ${formatsKey.toUpperCase().split(',').join('、')}` : ''}</p>}
    </div><button className="icon-button" aria-label="关闭数据库" onClick={onClose}><Icon name="close" /></button></header>
    <div className="database-grid">
      <section className="database-pane database-catalog" aria-label="数据目录">
        <div className="database-pane-heading"><div><h3>数据目录</h3><span>{datasets.length.toLocaleString()} 个已加载数据集</span></div></div>
        {linkedWork && <LinkedWorkData key={`${project}/${linkedWork.subject}/${linkedWork.sessionId}`}
          scope={linkedWork} project={project} allowedFormats={allowedFormats}
          onInspect={value => selectVersion(value.versionId, value.format, value)} />}
        <div className="database-filters"><label>搜索<input type="search" name="dataset-query" autoComplete="off" value={query} onChange={event => setQuery(event.target.value)} placeholder="标题、说明或标识…" /></label><div><label>标签<input name="dataset-tag" autoComplete="off" value={tag} onChange={event => setTag(event.target.value)} placeholder="精确标签…" /></label><label>格式<select aria-label="格式筛选" value={format} onChange={event => setFormat(event.target.value)}>{FORMATS.filter(item => !item || !formatsKey || formatsKey.split(',').includes(item)).map(item => <option key={item || 'all'} value={item}>{item ? item.toUpperCase() : '全部格式'}</option>)}</select></label></div></div>
        {catalogError && <div className="workbench-read-error"><ErrorNotice error={catalogError} title="数据目录读取失败" /><button onClick={() => cursor ? void more() : setCatalogRetry(value => value + 1)}>重新读取目录</button></div>}
        <div className="database-list" aria-busy={catalogLoading}>{datasets.map(item => <button key={item.datasetId} aria-pressed={version?.datasetId === item.datasetId || target === item.currentVersion} className={version?.datasetId === item.datasetId || target === item.currentVersion ? 'database-item selected' : 'database-item'} onClick={() => selectVersion(item.currentVersion, format)}><span><strong title={item.title}>{item.title}</strong><small title={item.datasetId}>{item.datasetId}</small><p>{item.semantics?.description || '暂无数据说明'}</p><span className="tag-list">{item.formats?.map(value => <i key={value}>{value.toUpperCase()}</i>)}</span></span><Icon name="arrow-up-right" /></button>)}{!datasets.length && !catalogLoading && !catalogError && <EmptyState title="没有符合条件的数据集。" description="调整搜索或减少筛选条件后重试。" />}</div>
        {catalogLoading && <div className="workbench-loading"><Loading>正在读取数据目录…</Loading>{!datasets.length && <div className="workbench-skeleton" aria-hidden="true"><span /><span /><span /></div>}</div>}<button className="secondary database-more" disabled={!cursor || catalogLoading} onClick={() => void more()}>{catalogLoading ? '正在读取…' : cursor ? '加载更多' : catalogError ? '目录尚未完整读取' : '已显示全部'}</button>
      </section>
      <section className="database-pane database-detail" ref={detail} tabIndex={-1} aria-label="数据详情">
        {canChangeSemantic && described?.key === targetKey && !permissionLost &&
          <DataSemanticHistory key={targetKey} versionId={target} semanticId={semanticId || described.semanticId}
            onChange={value => { setSemanticId(value); setPreviewRequested(false); }} />}
        {versionLoading ? <div className="workbench-loading"><Loading>正在读取所选版本…</Loading><div className="workbench-skeleton" aria-hidden="true"><span /><span /><span /></div></div> : versionError ? <div className="workbench-read-error"><ErrorNotice error={versionError} title="所选版本读取失败" /><button onClick={() => setVersionRetry(value => value + 1)}>重新读取所选版本</button></div> : !version || !object ? <>
          <div className="database-empty"><Icon name="file" /><h3>{version ? version.title : '选择数据集'}</h3><p>{version ? '该版本没有可读取的数据对象，请选择其他版本。' : '从数据目录选择数据集后可查看版本和资料说明。'}</p></div>
          {versionHistory}
        </> : <><div className="database-pane-heading"><div><h3>{version.title}</h3><span className="type-badge">{version.versionId === version.currentVersion ? '当前版本' : '不可变版本'}</span></div><button className="text-button" onClick={() => { setPreviewRequested(true); preview.current?.focus(); }}>查看内容<Icon name="arrow-up-right" /></button></div><p className="database-description">{version.semantics.description || '暂无数据说明'}</p>
          {versionHistory}
          {compare && <section className="version-comparison"><h4>版本比较</h4><label>另一版本<select aria-label="比较版本" value={compareTarget?.versionId ?? ''} onChange={event => setCompareTarget(versions.find(item => item.versionId === event.target.value) ?? null)}><option value="">选择版本</option>{versions.filter(item => item.versionId !== version.versionId).map(item => <option key={item.versionId} value={item.versionId}>{item.versionId}</option>)}</select></label>{compareError && <div className="workbench-read-error"><ErrorNotice error={compareError} title="比较版本读取失败" /><button onClick={() => setCompareRetry(value => value + 1)}>重新读取比较版本</button></div>}{compareLoading ? <p className="muted" role="status">正在读取比较版本…</p> : comparison ? <VersionDiff left={version} right={comparison} /> : !historyLoading && !historyError && versions.length <= 1 ? <p className="muted">当前数据集没有另一版本。</p> : <p className="muted">选择另一不可变版本后比较身份、字段和语义。</p>}</section>}
          {version.objects.length > 1 && !linkedReference && <label className="database-object-select">数据对象<select value={object.objectId} onChange={event => setObjectId(event.target.value)}>{version.objects.map(item => <option key={item.objectId} value={item.objectId}>{item.role} · {item.format} · {sizeLabel(item.size)}</option>)}</select></label>}
          <div className="database-summary"><div><span>行数</span><strong>{version.descriptor.rowCount ?? '未统计'}</strong></div><div><span>字段</span><strong>{version.descriptor.fields?.length ?? 0}</strong></div><div><span>格式</span><strong>{object.format.toUpperCase()}</strong></div><div><span>大小</span><strong>{sizeLabel(object.size)}</strong></div></div>
          <dl className="argument-list">{version.semantics.grain && <div><dt>数据粒度</dt><dd>{version.semantics.grain}</dd></div>}
            {(showMetadata || version.semantics.timeRange) && <div><dt>时间范围</dt><dd>{version.semantics.timeRange || '未声明'}</dd></div>}
            {(showMetadata || version.semantics.timezone) && <div><dt>时区</dt><dd>{version.semantics.timezone || '未声明'}</dd></div>}
            {(showMetadata || version.semantics.crs) && <div><dt>坐标参考系</dt><dd>{version.semantics.crs || '未声明'}</dd></div>}
            {(showMetadata || version.semantics.verticalDatum) && <div><dt>垂向基准</dt><dd>{version.semantics.verticalDatum || '未声明'}</dd></div>}
            {showMetadata && <>
              <div><dt>空间覆盖</dt><dd>{version.semantics.spatialExtent
                ? `${version.semantics.spatialExtent.bbox.join('，')} · ${version.semantics.spatialExtent.crs}` : '未声明'}</dd></div>
              <div><dt>语义来源</dt><dd>{version.semantics.source || '未声明'}</dd></div>
            </>}
          </dl>
          {version.semantics.limitations?.length ? <section><h4>使用限制</h4><ul>{version.semantics.limitations.map(item => <li key={item}>{item}</li>)}</ul></section> : null}
          {(showMetadata || blockedForInput) && (historicalSemantic || !uniqueRaw) && <div className="workbench-read-error" role="status">
            <p>{historicalSemantic ? '当前查看的是历史语义。执行需要该数据版本的当前语义。' : '执行需要该数据版本唯一的原始对象。'}</p>
            {blockedForInput && (historicalSemantic || !!pinned) && <button type="button" onClick={() => {
              setVersion(null);
              setLinkedReference(undefined);
              setReviewingHead(true);
              setSemanticId(currentSemanticId);
              setPreviewRequested(false);
              setVersionRetry(value => value + 1);
            }}>核对此版本的当前语义和原始对象</button>}
          </div>}
          {showMetadata && <DataVersionQuality key={project} version={version} />}
          <DataFieldQuality descriptor={version.descriptor} semantics={version.semantics} />
          {version.inputVersions.length > 0 && <section className="lineage-section"><h4>血缘输入</h4><div className="citation-list">{version.inputVersions.map(id => <button className="citation" key={id} onClick={() => selectVersion(id)}>{id}<Icon name="arrow-up-right" /></button>)}</div></section>}
          <details className="database-identity"><summary>不可变身份与技术详情</summary><dl className="argument-list"><div><dt>数据集</dt><dd>{version.datasetId}</dd></div><div><dt>数据版本</dt><dd>{version.versionId}</dd></div><div><dt>语义版本</dt><dd>{version.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object.objectId}</dd></div><div><dt>校验和</dt><dd>{object.checksum}</dd></div><div><dt>媒体类型</dt><dd>{object.mediaType}</dd></div></dl></details>
          {anySelect && !permitted && <p className="muted" role="status">所选对象为 {object.format.toUpperCase()}。请选择 {formatsKey.toUpperCase().split(',').join('、')} 格式的对象。</p>}
          <div className="actions database-actions">{anySelect && <button className="primary" disabled={!permitted || blockedForInput}
            onClick={() => {
              const item = resourceItemFromDetail(version, object);
              if (onChoose) onChoose(item, version);
              else onAttach?.(item);
            }}>{chooseLabel ?? (inputRole ? `绑定到${inputRole}` : '附加精确版本')}</button>}
            <a className="button secondary" href={contentUrl(project, version.versionId, object.objectId)} download><Icon name="download" />下载对象</a>
          </div></>}
      </section>
      <section className="database-pane database-preview" ref={preview} tabIndex={-1} aria-label="数据预览">
        {!version || !object ? <div className="database-empty"><Icon name="map" /><h3>数据预览</h3><p>{versionLoading ? '所选版本读取完成后显示预览。' : versionError ? '请先重新读取所选版本。' : '支持表格、图表、地图、栅格、图像和结构化内容。'}</p>{versionError && <button onClick={() => detail.current?.focus()}>返回版本详情</button>}</div> : (compare || showMetadata) && !previewRequested ? <div className="database-empty"><Icon name="file" /><h3>{compare ? '版本比较' : '数据预览'}</h3><button onClick={() => { setPreviewRequested(true); preview.current?.focus(); }}>打开数据预览</button></div> : <><div className="database-pane-heading"><div><strong>{version.title}</strong><span title={version.versionId}>{object.format.toUpperCase()} · {sizeLabel(object.size)} · 版本 {version.versionId.slice(0, 12)}</span></div></div>
          <Suspense fallback={<Loading>正在加载数据查看器…</Loading>}>
            <ArtifactViewer key={`${version.versionId}:${version.semanticId}:${object.objectId}`} resource={version} objectId={object.objectId} showIdentity={false} contextHeading="h3" project={project} onResource={id => selectVersion(id)} />
          </Suspense>
        </>}
      </section>
    </div>
  </section>;
}
