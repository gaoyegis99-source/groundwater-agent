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

export function GeoPackagePreviewViewer({ resource, object, url }: { resource: ResourceDetail; object: ResourceDetail['objects'][number]; url: string }) {
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
  return <div className="preview-operation">{error && <><ErrorNotice error={error} title="GeoPackage 预览未完成" /><a className="button secondary" href={url} download><Icon name="download" />下载原文件</a></>}{loading && !result && <p>正在安全读取空间图层…</p>}{result && <><div className="artifact-toolbar"><label>图层<select aria-label="GeoPackage 图层" value={selected} onChange={event => setLayer(event.target.value)}>{layers.map(item => <option key={item.tableName} value={item.tableName}>{item.identifier || item.tableName}</option>)}</select></label><button className={mode === 'map' ? 'active' : ''} disabled={!mapAvailable} onClick={() => setMode('map')}>地图</button><button className={mode === 'table' ? 'active' : ''} onClick={() => setMode('table')}>属性表</button><span>{String(result.metadata.featureCount ?? 0)} 个要素 · {String(result.metadata.crs ?? '')}</span></div>{result.warnings.map(item => <p className="limitations" role="status" key={item}>{item}</p>)}{mode === 'map' && mapAvailable ? <FeatureMap features={result.features} /> : <PreviewTable result={result} offset={offset} />}<div className="artifact-toolbar"><button disabled={offset === 0 || loading} onClick={() => void page(Math.max(0, offset - 100))}>上一页</button><button disabled={result.nextOffset === null || loading} onClick={() => void page(result.nextOffset!)}>下一页</button></div></>}</div>;
}

function FeatureMap({ features }: { features: DataGeoJsonFeature[] }) {
  const host = useRef<HTMLDivElement>(null); const [property, setProperty] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (!host.current) return;
    const source = new VectorSource({ features: new GeoJSON().readFeatures({ type: 'FeatureCollection', features }, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' }) });
    const layer = new VectorLayer({ source, style: new Style({ stroke: new Stroke({ color: '#3157a4', width: 2 }), fill: new Fill({ color: 'rgba(49,87,164,.25)' }), image: new CircleStyle({ radius: 6, fill: new Fill({ color: '#3157a4' }), stroke: new Stroke({ color: '#fff', width: 1 }) }) }) });
    const map = new Map({ target: host.current, layers: [layer], view: new View({ center: [0, 0], zoom: 2 }) });
    const extent = source.getExtent();
    if (source.getFeatures().length && extent !== null) map.getView().fit(extent, { padding: [24, 24, 24, 24], maxZoom: 15 });
    map.on('singleclick', event => { const feature = map.forEachFeatureAtPixel(event.pixel, item => item); setProperty(feature ? feature.getProperties() : null); });
    return () => map.setTarget(undefined);
  }, [features]);
  return <div className="map-workspace"><div ref={host} className="artifact-map" />{property && <pre>{JSON.stringify(property, (key, value) => key === 'geometry' ? undefined : value, 2)}</pre>}</div>;
}

function PreviewTable({ result, offset }: { result: NonNullable<DataPreviewResponse['result']>; offset: number }) {
  if (!result.rows.length) return <p>所选图层没有属性记录。</p>;
  return <div className="table-scroll"><table><thead><tr>{result.fields.map(field => <th key={field.fieldId}>{field.name}<small>{field.type}</small></th>)}</tr></thead><tbody>{result.rows.map((row, index) => <tr key={offset + index}>{result.fields.map((field, column) => <td key={field.fieldId}>{String(row[column] ?? '∅')}</td>)}</tr>)}</tbody></table></div>;
}
