import { lazy, Suspense, useState } from 'react';
import { contentUrl } from './api';
import { Icon } from './Icon';
import type { ResourceDetail } from './types';
import type { ViewerKind } from './product-ui';

const ImageViewer = lazy(() => import('./viewers/BasicViewers').then(module => ({ default: module.ImageViewer })));
const JsonViewer = lazy(() => import('./viewers/BasicViewers').then(module => ({ default: module.JsonViewer })));
const MetadataViewer = lazy(() => import('./viewers/BasicViewers').then(module => ({ default: module.MetadataViewer })));
const TableViewer = lazy(() => import('./viewers/TableViewer').then(module => ({ default: module.TableViewer })));
const GeoViewer = lazy(() => import('./viewers/SpatialViewers').then(module => ({ default: module.GeoViewer })));
const RasterViewer = lazy(() => import('./viewers/SpatialViewers').then(module => ({ default: module.RasterViewer })));
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

export function ArtifactViewer({ resource, project, objectId, showIdentity = true, allowDownload = true, preferredViewer = 'auto', onResource = () => {} }: { resource: ResourceDetail; project: string; objectId?: string; showIdentity?: boolean; allowDownload?: boolean; preferredViewer?: ViewerKind; onResource?: (id: string) => void }) {
  const [openedJson, setOpenedJson] = useState<string | null>(null);
  const object = objectId === undefined
    ? resource.objects.find(item => item.role === 'raw') ?? resource.objects[0]
    : resource.objects.find(item => item.objectId === objectId);
  if (!object) return <div className="artifact-unsupported"><p>该数据版本没有可读取对象。</p></div>;
  const format = object.format.toLowerCase();
  const url = contentUrl(project, resource.versionId, object.objectId);
  const preference = resolveViewerPreference(preferredViewer, object);
  const canOpenJson = preference === 'metadata' && (format === 'json' || object.mediaType === 'application/json') && object.size <= 32 * 1024 ** 2;
  const showingJson = canOpenJson && openedJson === url;
  let viewer;
  if (showingJson) viewer = <JsonViewer url={url} />;
  else if (preference === 'metadata') viewer = <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="产品建议首先查看元数据。" />;
  else if (object.mediaType === 'image/png' || format === 'png') viewer = object.size <= 128 * 1024 ** 2 ? <ImageViewer allowDownload={allowDownload} url={url} name={resource.title || '数据图片'} /> : <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="图像超过 128 MiB，仅提供元数据和下载。" />;
  else if (object.mediaType.includes('geo+json') || format === 'geojson') viewer = object.size <= 32 * 1024 ** 2 ? <GeoViewer url={url} /> : <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="GeoJSON 超过 32 MiB，在线地图已关闭。" />;
  else if (object.mediaType.includes('tiff') || ['geotiff', 'cog', 'tif', 'tiff'].includes(format)) viewer = <RasterViewer url={url} />;
  else if (['csv', 'parquet'].includes(format)) viewer = <TableViewer key={`${project}:${resource.versionId}`} resource={resource} project={project} initialChart={preference === 'chart'} />;
  else if (format === 'xlsx') viewer = <XlsxPreviewViewer allowDownload={allowDownload} resource={resource} object={object} url={url} />;
  else if (format === 'geopackage') viewer = <GeoPackagePreviewViewer allowDownload={allowDownload} resource={resource} object={object} url={url} />;
  else if (object.mediaType.includes('json') || format === 'json') viewer = object.size <= 32 * 1024 ** 2 ? <JsonViewer url={url} /> : <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} reason="JSON 超过 32 MiB，仅提供元数据和下载。" />;
  else viewer = <MetadataViewer allowDownload={allowDownload} resource={resource} object={object} url={url} />;
  return <div className="artifact-resource">
    {showIdentity && <dl className="argument-list"><div><dt>数据集</dt><dd>{resource.datasetId}</dd></div><div><dt>数据版本</dt><dd>{resource.versionId}</dd></div><div><dt>语义版本</dt><dd>{resource.semanticId}</dd></div><div><dt>数据对象</dt><dd>{object.objectId}</dd></div></dl>}
    {showIdentity && resource.inputVersions.length > 0 && <section className="lineage-section"><h4>血缘输入</h4><div className="citation-list">{resource.inputVersions.map(id => <button className="citation" key={id} onClick={() => onResource(id)}>{id}<Icon name="arrow-up-right" /></button>)}</div></section>}
    {canOpenJson && <div className="artifact-toolbar"><button aria-expanded={showingJson} onClick={() => setOpenedJson(showingJson ? null : url)}>{showingJson ? '返回元数据' : '查看 JSON 内容'}</button></div>}
    <Suspense key={url} fallback={<p className="muted">正在加载数据查看器…</p>}>{viewer}</Suspense>
  </div>;
}
