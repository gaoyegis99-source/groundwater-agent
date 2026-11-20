import { lazy, Suspense, useState } from 'react';
import { contentUrl } from './api';
import { Icon } from './Icon';
import type { ResourceDetail } from './types';
import type { ViewerKind } from './product-ui';
import { EmptyState, Loading } from './ui/Feedback';

const ImageViewer = lazy(() => import('./viewers/BasicViewers').then(module => ({ default: module.ImageViewer })));
const JsonViewer = lazy(() => import('./viewers/BasicViewers').then(module => ({ default: module.JsonViewer })));
const MetadataViewer = lazy(() => import('./viewers/BasicViewers').then(module => ({ default: module.MetadataViewer })));
const TableViewer = lazy(() => import('./viewers/TableViewer').then(module => ({ default: module.TableViewer })));
const GeoViewer = lazy(() => import('./viewers/GeoViewer').then(module => ({ default: module.GeoViewer })));
const RasterViewer = lazy(() => import('./viewers/RasterViewer').then(module => ({ default: module.RasterViewer })));
const XlsxPreviewViewer = lazy(() => import('./viewers/XlsxPreviewViewer').then(module => ({ default: module.XlsxPreviewViewer })));
const GeoPackagePreviewViewer = lazy(() => import('./viewers/GeoPackagePreviewViewer').then(module => ({ default: module.GeoPackagePreviewViewer })));

export function resolveViewerPreference(preferred: ViewerKind, object: ResourceDetail['objects'][number]): ViewerKind {
  const format = object.format.toLowerCase();
  if (preferred === 'auto' || preferred === 'metadata') return preferred;
  if (preferred === 'image' && (object.mediaType.startsWith('image/') || format === 'png')) return preferred;
  if (preferred === 'json' && (object.mediaType.includes('json') || format === 'json')) return preferred;
  if ((preferred === 'table' || preferred === 'chart') && ['csv', 'parquet'].includes(format)) return preferred;
  if (preferred === 'map' && (object.mediaType.includes('geo+json') || ['geojson', 'geotiff', 'cog', 'tif', 'tiff'].includes(format))) return preferred;
  return 'auto';
}

export function ArtifactViewer({ resource, project, objectId, showIdentity = true, allowDownload = true, contextHeading: ContextHeading = 'h4', preferredViewer = 'auto', onResource }: { resource: ResourceDetail; project: string; objectId?: string; showIdentity?: boolean; allowDownload?: boolean; contextHeading?: 'h3' | 'h4'; preferredViewer?: ViewerKind; onResource?: (id: string) => void }) {
  const [openedJson, setOpenedJson] = useState<string | null>(null);
  const object = objectId === undefined
    ? resource.objects.find(item => item.role === 'raw') ?? resource.objects[0]
    : resource.objects.find(item => item.objectId === objectId);
  if (!object) return <EmptyState description="该数据版本没有可读取对象。" />;
  const format = object.format.toLowerCase();
  const url = contentUrl(project, resource.versionId, object.objectId);
  const preference = resolveViewerPreference(preferredViewer, object);
  const canOpenJson = preference === 'metadata' && (format === 'json' || object.mediaType === 'application/json') && object.size <= 32 * 1024 ** 2;
  const showingJson = canOpenJson && openedJson === url;
  let viewer;
  if (showingJson) viewer = <JsonViewer url={url} />;
  else if (preference === 'metadata') viewer = <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="产品建议首先查看元数据。" />;
  else if (object.mediaType === 'image/png' || format === 'png') viewer = object.size <= 128 * 1024 ** 2 ? <ImageViewer allowDownload={allowDownload} url={url} name={resource.title || '数据图片'} /> : <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="图像超过 128 MiB，仅提供元数据和下载。" />;
  else if (object.mediaType.includes('geo+json') || format === 'geojson') viewer = object.size <= 32 * 1024 ** 2 ? <GeoViewer url={url} resource={resource} /> : <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="GeoJSON 超过 32 MiB，在线地图已关闭。" />;
  else if (object.mediaType.includes('tiff') || ['geotiff', 'cog', 'tif', 'tiff'].includes(format)) viewer = <RasterViewer url={url} />;
  else if (['csv', 'parquet'].includes(format)) viewer = <TableViewer key={`${project}:${resource.versionId}`} resource={resource} project={project} initialChart={preference === 'chart'} />;
  else if (format === 'xlsx') viewer = <XlsxPreviewViewer allowDownload={allowDownload} resource={resource} object={object} url={url} />;
  else if (format === 'geopackage') viewer = <GeoPackagePreviewViewer allowDownload={allowDownload} resource={resource} object={object} url={url} />;
  else if (object.mediaType.includes('json') || format === 'json') viewer = object.size <= 32 * 1024 ** 2 ? <JsonViewer url={url} /> : <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="JSON 超过 32 MiB，仅提供元数据和下载。" />;
  else viewer = <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} />;
  return <div className="artifact-resource">
    <section className="artifact-context" aria-label="预览来源与范围">
      <ContextHeading>预览来源与范围</ContextHeading>
      <dl className="argument-list">
        {!showIdentity && <div><dt>来源</dt><dd>{resource.datasetId} · <code title={resource.versionId}>{resource.versionId.slice(0, 12)}</code></dd></div>}
        {showIdentity && <div><dt>数据版本</dt><dd><code>{resource.versionId}</code></dd></div>}
        {showIdentity && <div><dt>文件格式</dt><dd>{object.format.toUpperCase()}</dd></div>}
        {resource.semantics.source && <div><dt>来源声明</dt><dd>{resource.semantics.source === 'caller_declared' ? '由发布方声明' : resource.semantics.source}</dd></div>}
        <div><dt>数据时间范围</dt><dd>{resource.semantics.timeRange || '未声明'}</dd></div>
        {(resource.semantics.timezone || resource.semantics.timeRange) && <div><dt>数据时区</dt><dd>{resource.semantics.timezone || '未声明'}</dd></div>}
        {resource.semantics.grain && <div><dt>数据粒度</dt><dd>{resource.semantics.grain}</dd></div>}
        {resource.semantics.crs && <div><dt>数据坐标参考系</dt><dd>{resource.semantics.crs}</dd></div>}
        {resource.semantics.verticalDatum && <div><dt>垂向基准</dt><dd>{resource.semantics.verticalDatum}</dd></div>}
      </dl>
    </section>
    {canOpenJson && <div className="artifact-toolbar"><button aria-expanded={showingJson} onClick={() => setOpenedJson(showingJson ? null : url)}>{showingJson ? '返回元数据' : '查看 JSON 内容'}</button></div>}
    <div className="artifact-content"><Suspense key={url} fallback={<Loading>正在加载数据查看器…</Loading>}>{viewer}</Suspense></div>
    {showIdentity && <details className="artifact-identity"><summary>查看精确版本与来源</summary>
      <dl className="argument-list">
        <div><dt>数据集</dt><dd>{resource.datasetId}</dd></div>
        <div><dt>语义版本</dt><dd>{resource.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object.objectId}</dd></div>
      </dl>
      {resource.inputVersions.length > 0 && <section className="lineage-section"><h4>血缘输入</h4><div className="citation-list">
        {resource.inputVersions.map(id => onResource ? <button className="citation" key={id} onClick={() => onResource(id)}>{id}<Icon name="arrow-up-right" /></button>
          : <code key={id}>{id}</code>)}
      </div></section>}
    </details>}
  </div>;
}
