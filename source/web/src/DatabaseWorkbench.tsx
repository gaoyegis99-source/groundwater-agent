import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ApiError, callApi, contentUrl } from './api';
import { decodeResourceDetail } from './api-contract';
import { sizeLabel } from './format';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import type { ResourceDetail, ResourceItem } from './types';
import { resourceItemFromDetail } from './resource-item';

export { resourceItemFromDetail } from './resource-item';

const ArtifactViewer = lazy(() => import('./ArtifactViewer').then(module => ({ default: module.ArtifactViewer })));

type Dataset = { datasetId: string; title: string; currentVersion: string; tags?: string[]; formats?: string[]; semantics?: { description?: string } };
type VersionItem = { versionId: string; semanticId: string; createdAt: string };
type Pane = 'catalog' | 'detail' | 'preview';
const FORMATS = ['', 'csv', 'parquet', 'xlsx', 'json', 'geojson', 'geopackage', 'geotiff', 'cog', 'png', 'pytorch-state-dict'];

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
  return <dl className="version-diff"><div><dt>数据集</dt><dd>{left.datasetId} → {right.datasetId}</dd></div><div><dt>版本</dt><dd>{left.versionId} → {right.versionId}</dd></div><div><dt>语义版本</dt><dd>{left.semanticId} → {right.semanticId}</dd></div><div><dt>对象身份</dt><dd>{objects(left)} → {objects(right)}</dd></div><div><dt>格式与 MIME</dt><dd>{formats(left)} → {formats(right)}</dd></div><div><dt>checksum</dt><dd>{checksums(left)} → {checksums(right)}</dd></div><div><dt>对象大小</dt><dd>{sizes(left)} → {sizes(right)}</dd></div><div><dt>字段新增</dt><dd>{added.join('、') || '无'}</dd></div><div><dt>字段移除</dt><dd>{removed.join('、') || '无'}</dd></div><div><dt>字段变化</dt><dd>{changed.join('、') || '无'}</dd></div><div><dt>语义变化</dt><dd>{semanticChanges.length ? <ul>{semanticChanges.map(name => <li key={name}><strong>{name}</strong>：{semanticValue(left.semantics[name as keyof typeof left.semantics])} → {semanticValue(right.semantics[name as keyof typeof right.semantics])}</li>)}</ul> : '无'}</dd></div></dl>;
}

export function DatabaseWorkbench({ project, initialId, compare = false, allowedFormats = [], onClose, onAttach }: {
  project: string; initialId?: string; compare?: boolean; allowedFormats?: string[]; onClose: () => void; onAttach: (item: ResourceItem) => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(''); const [tag, setTag] = useState(''); const [format, setFormat] = useState(allowedFormats.length === 1 ? allowedFormats[0] : '');
  const [datasets, setDatasets] = useState<Dataset[]>([]); const [cursor, setCursor] = useState<string | null>(null); const [catalogLoading, setCatalogLoading] = useState(false);
  const [target, setTarget] = useState(initialId ?? ''); const [version, setVersion] = useState<ResourceDetail | null>(null); const [versions, setVersions] = useState<VersionItem[]>([]); const [objectId, setObjectId] = useState('');
  const [targetFormat, setTargetFormat] = useState('');
  const [compareTarget, setCompareTarget] = useState(''); const [comparison, setComparison] = useState<ResourceDetail | null>(null);
  const [pane, setPane] = useState<Pane>(initialId ? 'detail' : 'catalog'); const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => { (initialId ? dialog.current : search.current)?.focus(); }, [initialId]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setCatalogLoading(true);
      const params = new URLSearchParams({ limit: '30' });
      if (query.trim()) params.set('q', query.trim()); if (tag.trim()) params.set('tag', tag.trim()); if (format) params.set('format', format);
      void callApi('data_list_datasets', `/data/datasets?${params}`, undefined, controller.signal).then(value => { setDatasets(value.items as Dataset[]); setCursor(value.nextCursor); setError(null); setCatalogLoading(false); }).catch(caught => { if (caught instanceof DOMException && caught.name === 'AbortError') return; setError(caught as ApiError); setCatalogLoading(false); });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, tag, format, project]);
  useEffect(() => {
    if (!target) { setVersion(null); setVersions([]); setObjectId(''); return; }
    const controller = new AbortController();
    setError(null);
    void callApi('data_get_version', `/data/versions/${encodeURIComponent(target)}`, undefined, controller.signal, decodeResourceDetail).then(async value => {
      setVersion(value); setObjectId(value.objects.find(item => targetFormat && item.format.toLowerCase() === targetFormat)?.objectId ?? value.objects.find(item => item.role === 'raw')?.objectId ?? value.objects[0]?.objectId ?? '');
      const history = await callApi('data_list_dataset_versions', `/data/datasets/${encodeURIComponent(value.datasetId)}/versions?limit=100`, undefined, controller.signal);
      setVersions(history.items); setPane(current => current === 'catalog' ? 'detail' : current);
    }).catch(caught => { if (caught instanceof DOMException && caught.name === 'AbortError') return; setError(caught as ApiError); });
    return () => controller.abort();
  }, [target, targetFormat, project]);
  useEffect(() => {
    if (!compareTarget || compareTarget === version?.versionId) { setComparison(null); return; }
    const controller = new AbortController();
    void callApi('data_get_version', `/data/versions/${encodeURIComponent(compareTarget)}`, undefined, controller.signal, decodeResourceDetail).then(value => {
      if (value.datasetId !== version?.datasetId) throw new ApiError(409, 'DATASET_VERSION_FAMILY_MISMATCH');
      setComparison(value);
    }).catch(caught => { if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(caught as ApiError); });
    return () => controller.abort();
  }, [compareTarget, version?.versionId]);

  function keyDown(event: React.KeyboardEvent) {
    if (event.key === 'Escape') { onClose(); return; }
    if (event.key !== 'Tab' || !dialog.current) return;
    const items = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),[tabindex="0"]')];
    if (!items.length) return;
    const first = items[0], last = items.at(-1)!;
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  async function more() {
    if (!cursor) return; setCatalogLoading(true);
    const params = new URLSearchParams({ limit: '30', after: cursor });
    if (query.trim()) params.set('q', query.trim()); if (tag.trim()) params.set('tag', tag.trim()); if (format) params.set('format', format);
    try { const value = await callApi('data_list_datasets', `/data/datasets?${params}`); setDatasets(current => [...current, ...value.items as Dataset[]]); setCursor(value.nextCursor); }
    catch (caught) { setError(caught as ApiError); }
    finally { setCatalogLoading(false); }
  }
  const object = version?.objects.find(item => item.objectId === objectId) ?? version?.objects[0];
  return <aside ref={dialog} tabIndex={-1} className="database-workbench" role="dialog" aria-modal="true" aria-label="数据库" data-testid="database-workbench" onKeyDown={keyDown}>
    <header className="database-heading"><div><span className="drawer-eyebrow">Data PostgreSQL · 不可变对象库</span><h2>数据库</h2><p>检索数据集、核对不可变版本，并按格式查看数据内容。</p></div><button className="icon-button" aria-label="关闭数据库" onClick={onClose}><Icon name="close" /></button></header>
    <nav className="database-mobile-nav" aria-label="数据库工作区"><button className={pane === 'catalog' ? 'active' : ''} onClick={() => setPane('catalog')}>目录</button><button className={pane === 'detail' ? 'active' : ''} disabled={!version} onClick={() => setPane('detail')}>详情</button><button className={pane === 'preview' ? 'active' : ''} disabled={!version || !object} onClick={() => setPane('preview')}>预览</button></nav>
    {error && <ErrorNotice error={error} title="数据库读取未完成" />}
    <div className="database-grid">
      <section className="database-pane database-catalog" data-mobile-active={pane === 'catalog'} aria-label="数据目录">
        <div className="database-pane-heading"><div><strong>数据目录</strong><span>{datasets.length} 个已加载数据集</span></div></div>
        <div className="database-filters"><label>搜索<input ref={search} value={query} onChange={event => setQuery(event.target.value)} placeholder="标题、说明或标识" /></label><div><label>标签<input value={tag} onChange={event => setTag(event.target.value)} placeholder="精确标签" /></label><label>格式<select aria-label="格式筛选" value={format} onChange={event => setFormat(event.target.value)}>{FORMATS.filter(item => !item || !allowedFormats.length || allowedFormats.includes(item)).map(item => <option key={item || 'all'} value={item}>{item ? item.toUpperCase() : '全部格式'}</option>)}</select></label></div></div>
        <div className="database-list">{datasets.map(item => <button key={item.datasetId} className={version?.datasetId === item.datasetId ? 'database-item selected' : 'database-item'} onClick={() => { setTargetFormat(format); setTarget(item.currentVersion); setPane('detail'); }}><span><strong>{item.title}</strong><small>{item.datasetId}</small><p>{item.semantics?.description || '暂无数据说明'}</p><span className="tag-list">{item.formats?.map(value => <i key={value}>{value.toUpperCase()}</i>)}</span></span><Icon name="arrow-up-right" /></button>)}{!datasets.length && !catalogLoading && <p className="empty-inline">没有符合条件的数据集。</p>}</div>
        {catalogLoading && <p className="muted" role="status">正在读取数据目录…</p>}<button className="secondary database-more" disabled={!cursor || catalogLoading} onClick={() => void more()}>{cursor ? '加载更多' : '已显示全部'}</button>
      </section>
      <section className="database-pane database-detail" data-mobile-active={pane === 'detail'} aria-label="数据详情">
        {!version || !object ? <div className="database-empty"><Icon name="file" /><h3>选择数据集</h3><p>从左侧目录选择数据后，这里会显示版本、语义、字段质量和血缘。</p></div> : <><div className="database-pane-heading"><div><span className="type-badge">数据版本</span><h3>{version.title}</h3></div><button className="text-button" onClick={() => setPane('preview')}>查看内容<Icon name="arrow-up-right" /></button></div><p className="database-description">{version.semantics.description || '暂无数据说明'}</p>
          <div className="version-control"><label>不可变版本<select value={version.versionId} onChange={event => { setTargetFormat(''); setTarget(event.target.value); }}>{versions.map(item => <option key={item.versionId} value={item.versionId}>{item.versionId}</option>)}</select></label><small>{version.createdAt ? new Date(version.createdAt).toLocaleString() : ''}</small></div>
          {compare && <section className="version-comparison"><h4>版本比较</h4><label>另一版本<select aria-label="比较版本" value={compareTarget} onChange={event => setCompareTarget(event.target.value)}><option value="">选择版本</option>{versions.filter(item => item.versionId !== version.versionId).map(item => <option key={item.versionId} value={item.versionId}>{item.versionId}</option>)}</select></label>{comparison ? <VersionDiff left={version} right={comparison} /> : versions.length <= 1 ? <p className="muted">当前数据集没有另一版本。</p> : <p className="muted">选择另一不可变版本后比较身份、字段和语义。</p>}</section>}
          {version.objects.length > 1 && <label className="database-object-select">数据对象<select value={object.objectId} onChange={event => setObjectId(event.target.value)}>{version.objects.map(item => <option key={item.objectId} value={item.objectId}>{item.role} · {item.format} · {sizeLabel(item.size)}</option>)}</select></label>}
          <div className="database-summary"><div><span>行数</span><strong>{version.descriptor.rowCount ?? '未统计'}</strong></div><div><span>字段</span><strong>{version.descriptor.fields?.length ?? 0}</strong></div><div><span>格式</span><strong>{object.format.toUpperCase()}</strong></div><div><span>大小</span><strong>{sizeLabel(object.size)}</strong></div></div>
          <dl className="argument-list">{version.semantics.grain && <div><dt>数据粒度</dt><dd>{version.semantics.grain}</dd></div>}{version.semantics.timeRange && <div><dt>时间范围</dt><dd>{version.semantics.timeRange}</dd></div>}{version.semantics.timezone && <div><dt>时区</dt><dd>{version.semantics.timezone}</dd></div>}{version.semantics.crs && <div><dt>坐标参考系</dt><dd>{version.semantics.crs}</dd></div>}{version.semantics.verticalDatum && <div><dt>垂向基准</dt><dd>{version.semantics.verticalDatum}</dd></div>}</dl>
          {version.semantics.limitations?.length ? <section><h4>使用限制</h4><ul>{version.semantics.limitations.map(item => <li key={item}>{item}</li>)}</ul></section> : null}
          {version.descriptor.fields?.length ? <section><h4>字段与质量</h4><div className="table-scroll"><table><thead><tr><th>字段</th><th>类型</th><th>单位</th><th>空值</th></tr></thead><tbody>{version.descriptor.fields.map(field => <tr key={field.fieldId}><td>{field.name}</td><td>{field.type}</td><td>{version.semantics.fields?.[field.fieldId]?.unit ?? '—'}</td><td>{field.nullCount ?? '—'}</td></tr>)}</tbody></table></div></section> : null}
          {version.inputVersions.length > 0 && <section className="lineage-section"><h4>血缘输入</h4><div className="citation-list">{version.inputVersions.map(id => <button className="citation" key={id} onClick={() => { setTargetFormat(''); setTarget(id); }}>{id}<Icon name="arrow-up-right" /></button>)}</div></section>}
          <section className="database-identity"><h4>不可变身份</h4><dl className="argument-list"><div><dt>数据集</dt><dd>{version.datasetId}</dd></div><div><dt>数据版本</dt><dd>{version.versionId}</dd></div><div><dt>语义版本</dt><dd>{version.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object.objectId}</dd></div><div><dt>校验和</dt><dd>{object.checksum}</dd></div><div><dt>媒体类型</dt><dd>{object.mediaType}</dd></div></dl></section>
          <div className="actions database-actions"><button className="primary" onClick={() => onAttach(resourceItemFromDetail(version, object))}>附加精确版本</button><a className="button secondary" href={contentUrl(project, version.versionId, object.objectId)} download><Icon name="download" />下载对象</a></div></>}
      </section>
      <section className="database-pane database-preview" data-mobile-active={pane === 'preview'} aria-label="数据预览">
        {!version || !object ? <div className="database-empty"><Icon name="map" /><h3>数据预览</h3><p>支持表格、图表、地图、栅格、图像和结构化内容。</p></div> : <><div className="database-pane-heading"><div><strong>内容预览</strong><span>{object.format.toUpperCase()} · {sizeLabel(object.size)}</span></div></div><Suspense fallback={<p className="muted">正在加载数据查看器…</p>}><ArtifactViewer key={`${version.versionId}:${object.objectId}`} resource={version} objectId={object.objectId} showIdentity={false} project={project} onResource={id => { setTarget(id); setPane('detail'); }} /></Suspense></>}
      </section>
    </div>
  </aside>;
}
