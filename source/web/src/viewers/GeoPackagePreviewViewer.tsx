import { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { Fill, Stroke, Style, Circle as CircleStyle } from 'ol/style';
import 'ol/ol.css';
import { ApiError, cancelDataPreview, getDataPreview, startDataPreview, waitForDataPreview } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import { Icon } from '../Icon';
import type { DataGeoJsonFeature, DataPreviewResponse, ResourceDetail } from '../types';
import { Button } from '../ui/Button';
import { Loading } from '../ui/Feedback';
import { Select } from '../ui/Fields';
import { Tabs } from '../ui/Tabs';
import { Tooltip } from '../ui/Tooltip';
import { PreviewTable } from './PreviewTable';
import { mapOptions } from './map-options';

export function GeoPackagePreviewViewer({ resource, object, url, allowDownload = true }: { resource: ResourceDetail; object: ResourceDetail['objects'][number]; url: string; allowDownload?: boolean }) {
  const [layer, setLayer] = useState('');
  const [previewId, setPreviewId] = useState('');
  const [response, setResponse] = useState<DataPreviewResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [mode, setMode] = useState<'map' | 'table'>('map');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const pageRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController(); let operation = ''; let settled = false;
    pageRequest.current?.abort();
    setLoading(true); setError(null); setResponse(null); setOffset(0);
    void startDataPreview({ kind: 'geopackage_layer', versionId: resource.versionId, semanticId: resource.semanticId, objectId: object.objectId, ...(layer ? { layerName: layer } : {}) }, controller.signal)
      .then(async accepted => { operation = accepted.previewId; setPreviewId(operation); const result = await waitForDataPreview(operation, 0, 100, controller.signal); settled = true; setResponse(result); setLoading(false); })
      .catch(caught => { if (caught instanceof DOMException && caught.name === 'AbortError') return; setError(caught as ApiError); setLoading(false); });
    return () => { controller.abort(); pageRequest.current?.abort(); if (operation && !settled) void cancelDataPreview(operation).catch(() => undefined); };
  }, [resource.versionId, resource.semanticId, object.objectId, layer]);
  async function page(next: number) {
    pageRequest.current?.abort();
    const controller = new AbortController();
    pageRequest.current = controller;
    setLoading(true); setError(null);
    try { setResponse(await getDataPreview(previewId, next, 100, controller.signal)); setOffset(next); }
    catch (caught) { if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(caught as ApiError); }
    finally { if (pageRequest.current === controller) { pageRequest.current = null; setLoading(false); } }
  }
  const result = response?.result;
  const layers = (result?.metadata.layers as { tableName: string; identifier?: string; geometryTypeName?: string }[] | undefined) ?? [];
  const selected = String(result?.metadata.selectedLayer ?? layer);
  const mapAvailable = Boolean(result?.metadata.mapAvailable);
  return <div className="preview-operation">
    {error && <>
      <ErrorNotice error={error} title="GeoPackage 预览未完成" />
      {allowDownload && <a className="button secondary" href={url} download><Icon name="download" />下载原文件</a>}
    </>}
    {loading && <Loading>正在安全读取空间图层…</Loading>}
    {result && <>
      <div className="artifact-toolbar">
        <Select label="GeoPackage 图层" value={selected} onChange={event => setLayer(event.target.value)}>
          {layers.map(item => <option key={item.tableName} value={item.tableName}>{item.identifier || item.tableName}</option>)}
        </Select>
        <span>{result.metadata.featureCount == null ? '要素数未统计' : `${result.metadata.featureCount} 个要素`} · {String(result.metadata.crs ?? '坐标参考系未声明')}</span>
        <Tooltip label="坐标参考系说明" description="坐标参考系定义图层使用的坐标单位和空间位置。比较图层前，请核对它们的坐标参考系。" />
      </div>
      {result.warnings.map(item => <p className="limitations" role="status" key={item}>{item}</p>)}
      <Tabs label="空间图层视图" selectedKey={mapAvailable ? mode : 'table'} onSelectionChange={setMode}
        items={[
          { id: 'map', label: '地图', disabled: !mapAvailable, panel: <FeatureMap features={result.features} /> },
          { id: 'table', label: '属性表', panel: <PreviewTable result={result} offset={offset} label="空间图层属性" empty="所选图层没有属性记录。" /> },
        ]} />
      <div className="artifact-toolbar">
        <Button isDisabled={offset === 0 || loading} onPress={() => void page(Math.max(0, offset - 100))}>上一页</Button>
        <Button isDisabled={result.nextOffset === null || loading} onPress={() => { if (result.nextOffset !== null) void page(result.nextOffset); }}>下一页</Button>
      </div>
    </>}
  </div>;
}

function FeatureMap({ features }: { features: DataGeoJsonFeature[] }) {
  const host = useRef<HTMLDivElement>(null); const [property, setProperty] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (!host.current) return;
    setProperty(null);
    const source = new VectorSource({ features: new GeoJSON().readFeatures({ type: 'FeatureCollection', features }, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' }) });
    const layer = new VectorLayer({ source, style: new Style({ stroke: new Stroke({ color: '#3157a4', width: 2 }), fill: new Fill({ color: 'rgba(49,87,164,.25)' }), image: new CircleStyle({ radius: 6, fill: new Fill({ color: '#3157a4' }), stroke: new Stroke({ color: '#fff', width: 1 }) }) }) });
    const map = new Map({ ...mapOptions(), target: host.current, layers: [layer], view: new View({ center: [0, 0], zoom: 2 }) });
    const extent = source.getExtent();
    if (source.getFeatures().length && extent !== null) map.getView().fit(extent, { padding: [24, 24, 24, 24], maxZoom: 15 });
    map.on('singleclick', event => { const feature = map.forEachFeatureAtPixel(event.pixel, item => item); setProperty(feature ? feature.getProperties() : null); });
    return () => { map.setTarget(undefined); map.dispose(); layer.dispose(); source.dispose(); };
  }, [features]);
  return <div className="map-workspace"><p className="muted">图层预览 · {features.length.toLocaleString()} 个要素</p>
    <div ref={host} className="artifact-map" tabIndex={0} role="region" aria-label="GeoPackage 图层地图" />
    {property && <pre>{JSON.stringify(property, (key, value) => key === 'geometry' ? undefined : value, 2)}</pre>}
  </div>;
}
