import { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import type { FeatureLike } from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import VectorImageLayer from 'ol/layer/VectorImage';
import VectorSource from 'ol/source/Vector';
import { Fill, Stroke, Style, Circle as CircleStyle } from 'ol/style';
import { unByKey } from 'ol/Observable';
import { toLonLat } from 'ol/proj';
import type { ResourceDetail } from '../types';
import { fieldUnit, fieldWithUnit, scientificCell } from './scientific-display';
import { mapOptions } from './map-options';
import 'ol/ol.css';

function defaultVectorStyle() {
  return new Style({
    stroke: new Stroke({ color: '#3157a4', width: 2 }),
    fill: new Fill({ color: 'rgba(49,87,164,.2)' }),
    image: new CircleStyle({
      radius: 5,
      fill: new Fill({ color: '#3157a4' }),
      stroke: new Stroke({ color: '#fff', width: 1 }),
    }),
  });
}

type NumericField = { min: number; max: number; count: number };

function numericFieldsOf(features: FeatureLike[]) {
  const fields = new globalThis.Map<string, NumericField>();
  for (const feature of features) {
    for (const [name, value] of Object.entries(feature.getProperties())) {
      if (name === 'geometry' || typeof value !== 'number' || !Number.isFinite(value)) continue;
      const field = fields.get(name);
      if (!field) {
        fields.set(name, { min: value, max: value, count: 1 });
        continue;
      }
      field.min = Math.min(field.min, value);
      field.max = Math.max(field.max, value);
      field.count += 1;
    }
  }
  return fields;
}

function numericStyle(name: string, { min, max }: NumericField) {
  const scale = Number.isFinite(max - min) ? 1 : 0.5;
  const lower = min * scale;
  const span = max * scale - lower;
  const styles = new globalThis.Map<number, Style>();
  const missingStyle = new Style({
    stroke: new Stroke({ color: '#54666d', width: 2, lineDash: [3, 2] }),
    image: new CircleStyle({ radius: 5, stroke: new Stroke({ color: '#54666d', width: 2 }) }),
  });
  return (feature: FeatureLike) => {
    const value = feature.get(name);
    if (typeof value !== 'number' || !Number.isFinite(value)) return missingStyle;
    const ratio = span > 0 ? (value * scale - lower) / span : .5;
    const red = Math.round(44 + 210 * ratio);
    const blue = Math.round(210 - 170 * ratio);
    const key = red * 256 + blue;
    const cached = styles.get(key);
    if (cached) return cached;
    const color = `rgb(${red},92,${blue})`;
    const style = new Style({
      stroke: new Stroke({ color, width: 2 }),
      fill: new Fill({ color: `rgba(${red},92,${blue},.35)` }),
      image: new CircleStyle({
        radius: 6,
        fill: new Fill({ color }),
        stroke: new Stroke({ color: '#fff', width: 1 }),
      }),
    });
    styles.set(key, style);
    return style;
  };
}

export function GeoViewer({ url, resource }: { url: string; resource: ResourceDetail }) {
  const host = useRef<HTMLDivElement>(null);
  const featuresRef = useRef<FeatureLike[]>([]);
  const layerRef = useRef<VectorImageLayer<VectorSource<FeatureLike>> | null>(null);
  const defaultStyleRef = useRef<Style | null>(null);
  const [property, setProperty] = useState<Record<string, unknown> | null>(null);
  const [coordinate, setCoordinate] = useState('');
  const [numericFields, setNumericFields] = useState(() => new globalThis.Map<string, NumericField>());
  const [colorField, setColorField] = useState('');
  const [featureCount, setFeatureCount] = useState(0);
  const [featurePosition, setFeaturePosition] = useState('1');
  const [readState, setReadState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const field = resource.descriptor.fields?.find(item => item.name === colorField);
  const unit = fieldUnit(resource, field?.fieldId ?? colorField);
  const range = numericFields.get(colorField);
  const missing = range ? featureCount - range.count : 0;

  useEffect(() => {
    if (!host.current) return;
    setProperty(null);
    setCoordinate('');
    setNumericFields(new globalThis.Map());
    setColorField('');
    setFeatureCount(0);
    setFeaturePosition('1');
    setReadState('loading');

    const controller = new AbortController();
    const format = new GeoJSON();
    const source = new VectorSource({
      url,
      format,
      async loader(extent, _resolution, projection) {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(`GeoJSON request failed (${response.status})`);
        const document: unknown = await response.json();
        controller.signal.throwIfAborted();
        return format.readFeatures(document as object, { extent, featureProjection: projection });
      },
    });
    const normal = defaultVectorStyle();
    const layer = new VectorImageLayer({ source, style: normal, imageRatio: 1.2 });
    const map = new Map({
      ...mapOptions(),
      target: host.current,
      layers: [layer],
      view: new View({ center: [0, 0], zoom: 2 }),
    });
    layerRef.current = layer;
    defaultStyleRef.current = normal;

    const sourceKey = source.once('featuresloadend', () => {
      const features = source.getFeatures();
      featuresRef.current = features;
      setFeatureCount(features.length);
      const fields = numericFieldsOf(features);
      setNumericFields(fields);
      const initial = fields.entries().next().value;
      if (initial) layer.setStyle(numericStyle(...initial));
      setColorField(initial?.[0] ?? '');
      const extent = source.getExtent();
      if (features.length && extent) map.getView().fit(extent, { padding: [24, 24, 24, 24], maxZoom: 15 });
      setReadState('ready');
    });
    const errorKey = source.once('featuresloaderror', () => setReadState('failed'));
    const pointerKey = map.on('pointermove', event => {
      const [longitude, latitude] = toLonLat(event.coordinate);
      setCoordinate(`${longitude.toFixed(5)}, ${latitude.toFixed(5)}`);
    });
    const clickKey = map.on('singleclick', event => {
      const feature = map.forEachFeatureAtPixel(event.pixel, item => item);
      setProperty(feature ? feature.getProperties() : null);
    });

    return () => {
      unByKey([sourceKey, errorKey, pointerKey, clickKey]);
      controller.abort();
      featuresRef.current = [];
      if (layerRef.current === layer) layerRef.current = null;
      if (defaultStyleRef.current === normal) defaultStyleRef.current = null;
      map.setTarget(undefined);
      map.dispose();
      layer.dispose();
      source.dispose();
    };
  }, [url]);

  return <div className="map-workspace">
    <div className="artifact-toolbar">
      <label>数值着色<select aria-label="GeoJSON 数值着色" value={colorField} onChange={event => {
        const name = event.target.value;
        const field = numericFields.get(name);
        layerRef.current?.setStyle(field ? numericStyle(name, field) : defaultStyleRef.current ?? undefined);
        setColorField(name);
      }}>
        <option value="">统一样式</option>
        {[...numericFields.keys()].map(name => {
          const id = resource.descriptor.fields?.find(item => item.name === name)?.fieldId ?? name;
          return <option key={name} value={name}>{fieldWithUnit(name, fieldUnit(resource, id))}</option>;
        })}
      </select></label>
      {range && colorField && <><span className="map-legend"><i />{range.min} – {range.max}</span><span>{unit ?? '单位未声明'}</span></>}
      {missing > 0 && <span className="map-missing">空心圆或虚线 · {missing.toLocaleString()} 个要素缺测</span>}
      <output aria-label="地图坐标">经度、纬度（°） · {coordinate || '移动指针查看'}</output>
    </div>
    {readState === 'loading' && <p role="status">正在读取地图要素…</p>}
    {readState === 'failed' && <p role="alert">地图要素读取失败。可在数据详情中下载原文件。</p>}
    <div ref={host} className="artifact-map" tabIndex={0} role="region" aria-label="GeoJSON 地图" />
    {featureCount > 0 && <form className="artifact-toolbar" onSubmit={event => {
      event.preventDefault();
      const position = Number(featurePosition);
      if (!Number.isInteger(position) || position < 1 || position > featureCount) return;
      const feature = featuresRef.current[position - 1];
      if (feature) setProperty(feature.getProperties());
    }}>
      <label>要素位置<input type="number" min={1} max={featureCount} required value={featurePosition}
        onChange={event => setFeaturePosition(event.target.value)} /></label>
      <button type="submit">查看要素属性</button>
      <span className="muted">{featureCount.toLocaleString()} 个已读取要素</span>
    </form>}
    {property && <div className="table-scroll" role="region" aria-label="要素属性" tabIndex={0}>
      <table>
        <thead><tr><th scope="col">字段</th><th scope="col">值</th></tr></thead>
        <tbody>{Object.entries(property).filter(([name]) => name !== 'geometry').map(([name, value]) => {
          const id = resource.descriptor.fields?.find(item => item.name === name)?.fieldId ?? name;
          return <tr key={name}><th scope="row">{fieldWithUnit(name, fieldUnit(resource, id))}</th>
            <td>{value == null || ['string', 'number', 'boolean'].includes(typeof value)
              ? scientificCell(value as string | number | boolean | null) : JSON.stringify(value)}</td></tr>;
        })}</tbody>
      </table>
    </div>}
  </div>;
}
