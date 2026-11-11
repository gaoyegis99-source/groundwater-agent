import { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import type { FeatureLike } from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import WebGLTileLayer from 'ol/layer/WebGLTile';
import type { Style as RasterStyle } from 'ol/layer/WebGLTile';
import VectorSource from 'ol/source/Vector';
import GeoTIFF from 'ol/source/GeoTIFF';
import { Fill, Stroke, Style, Circle as CircleStyle } from 'ol/style';
import { unByKey } from 'ol/Observable';
import { toLonLat } from 'ol/proj';
import { fromUrl } from 'geotiff';
import {
  loadRasterBands,
  probeRasterValue,
  type RasterBand,
  type RasterProbeImage,
  type RasterProbeResult,
} from './raster-probe';
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

export function GeoViewer({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  const sourceRef = useRef<VectorSource<FeatureLike> | null>(null);
  const layerRef = useRef<VectorLayer<VectorSource<FeatureLike>> | null>(null);
  const defaultStyleRef = useRef<Style | null>(null);
  const [property, setProperty] = useState<Record<string, unknown> | null>(null);
  const [coordinate, setCoordinate] = useState('');
  const [numericFields, setNumericFields] = useState<string[]>([]);
  const [colorField, setColorField] = useState('');
  const [range, setRange] = useState<[number, number] | null>(null);

  useEffect(() => {
    if (!host.current) return;
    setProperty(null);
    setCoordinate('');
    setNumericFields([]);
    setColorField('');
    setRange(null);

    const source = new VectorSource({ url, format: new GeoJSON() });
    const normal = defaultVectorStyle();
    const layer = new VectorLayer({ source, style: normal });
    const map = new Map({
      target: host.current,
      layers: [layer],
      view: new View({ center: [0, 0], zoom: 2 }),
    });
    sourceRef.current = source;
    layerRef.current = layer;
    defaultStyleRef.current = normal;

    const sourceKey = source.on('change', () => {
      if (source.getState() !== 'ready') return;
      unByKey(sourceKey);
      const features = source.getFeatures();
      const fields = [...new Set(features.flatMap(feature => (
        Object.entries(feature.getProperties())
          .filter(([key, value]) => key !== 'geometry' && typeof value === 'number' && Number.isFinite(value))
          .map(([key]) => key)
      )))];
      setNumericFields(fields);
      setColorField(previous => fields.includes(previous) ? previous : fields[0] ?? '');
      const extent = source.getExtent();
      if (features.length && extent) map.getView().fit(extent, { padding: [24, 24, 24, 24], maxZoom: 15 });
    });
    const pointerKey = map.on('pointermove', event => {
      const [longitude, latitude] = toLonLat(event.coordinate);
      setCoordinate(`${longitude.toFixed(5)}, ${latitude.toFixed(5)}`);
    });
    const clickKey = map.on('singleclick', event => {
      const feature = map.forEachFeatureAtPixel(event.pixel, item => item);
      setProperty(feature ? feature.getProperties() : null);
    });

    return () => {
      unByKey([sourceKey, pointerKey, clickKey]);
      if (sourceRef.current === source) sourceRef.current = null;
      if (layerRef.current === layer) layerRef.current = null;
      if (defaultStyleRef.current === normal) defaultStyleRef.current = null;
      map.setTarget(undefined);
      map.dispose();
      layer.dispose();
      source.dispose();
    };
  }, [url]);

  useEffect(() => {
    const source = sourceRef.current;
    const layer = layerRef.current;
    if (!source || !layer) return;
    if (!colorField) {
      setRange(null);
      layer.setStyle(defaultStyleRef.current ?? undefined);
      return;
    }
    const values = source.getFeatures()
      .map(feature => feature.get(colorField))
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    if (!values.length) {
      setRange(null);
      layer.setStyle(defaultStyleRef.current ?? undefined);
      return;
    }
    const bounds: [number, number] = [Math.min(...values), Math.max(...values)];
    setRange(bounds);
    layer.setStyle(feature => {
      const value = feature.get(colorField);
      const ratio = typeof value === 'number' && Number.isFinite(value) && bounds[1] !== bounds[0]
        ? (value - bounds[0]) / (bounds[1] - bounds[0])
        : .5;
      const red = Math.round(44 + 210 * ratio);
      const blue = Math.round(210 - 170 * ratio);
      const color = `rgb(${red},92,${blue})`;
      return new Style({
        stroke: new Stroke({ color, width: 2 }),
        fill: new Fill({ color: `rgba(${red},92,${blue},.35)` }),
        image: new CircleStyle({
          radius: 6,
          fill: new Fill({ color }),
          stroke: new Stroke({ color: '#fff', width: 1 }),
        }),
      });
    });
  }, [colorField, url]);

  return <div className="map-workspace"><div className="artifact-toolbar"><label>数值着色<select aria-label="GeoJSON 数值着色" value={colorField} onChange={event => setColorField(event.target.value)}><option value="">统一样式</option>{numericFields.map(field => <option key={field}>{field}</option>)}</select></label>{range && colorField && <span className="map-legend"><i />{range[0]} – {range[1]}</span>}<output aria-label="地图坐标">{coordinate || '移动指针查看坐标'}</output></div><div ref={host} className="artifact-map" />{property && <pre>{JSON.stringify(property, (key, value) => key === 'geometry' ? undefined : value, 2)}</pre>}</div>;
}

type RasterPalette = 'source' | 'gray' | 'viridis';

function rasterStyle(palette: RasterPalette, band: number): RasterStyle {
  if (palette === 'gray') {
    return { color: ['color', ['band', band], ['band', band], ['band', band], 1] };
  }
  if (palette === 'viridis') {
    return {
      color: [
        'interpolate', ['linear'], ['band', band],
        0, [68, 1, 84, 1],
        .5, [33, 145, 140, 1],
        1, [253, 231, 37, 1],
      ],
    };
  }
  return {};
}

function displayNumber(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  return String(Number(value.toPrecision(10)));
}

function formatProbe(result: RasterProbeResult): string {
  const coordinate = `地图坐标 ${displayNumber(result.coordinate[0])}, ${displayNumber(result.coordinate[1])}`;
  if (result.kind === 'outside') return `${coordinate}；超出栅格范围`;
  const prefix = `${coordinate}；像元 [${result.pixel.join(', ')}]；波段 ${result.band.number}`;
  if (result.kind === 'nodata') {
    return `${prefix}；NoData；原始值 ${displayNumber(result.rawValue)}；单位 ${result.band.unit ?? '未声明'}`;
  }
  const unit = result.band.unit ? ` ${result.band.unit}` : '（单位未声明）';
  return `${prefix}；物理值 ${displayNumber(result.physicalValue)}${unit}；原始值 ${displayNumber(result.rawValue)}（scale ${displayNumber(result.band.scale)}，offset ${displayNumber(result.band.offset)}）`;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export function RasterViewer({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  const layerRef = useRef<WebGLTileLayer | null>(null);
  const imageRef = useRef<RasterProbeImage | null>(null);
  const bandsRef = useRef<RasterBand[]>([]);
  const selectedBandRef = useRef(0);
  const probeAbortRef = useRef<AbortController | null>(null);
  const probeSequenceRef = useRef(0);
  const [opacity, setOpacity] = useState(.85);
  const [palette, setPalette] = useState<RasterPalette>('source');
  const [bands, setBands] = useState<RasterBand[]>([]);
  const [selectedBand, setSelectedBand] = useState(0);
  const [probe, setProbe] = useState('正在读取栅格元数据…');
  selectedBandRef.current = selectedBand;

  useEffect(() => {
    if (!host.current) return;
    let active = true;
    let rawFile: Awaited<ReturnType<typeof fromUrl>> | null = null;
    let rawFileClosed = false;
    const closeRawFile = () => {
      if (!rawFile || rawFileClosed) return;
      rawFileClosed = true;
      const closing = rawFile.close();
      if (closing) void closing.catch(() => undefined);
    };
    const loadController = new AbortController();
    const source = new GeoTIFF({ sources: [{ url }], normalize: true });
    const layer = new WebGLTileLayer({ source, opacity, style: rasterStyle(palette, 1) });
    const map = new Map({ target: host.current, layers: [layer], view: source.getView() });

    layerRef.current = layer;
    imageRef.current = null;
    bandsRef.current = [];
    selectedBandRef.current = 0;
    setBands([]);
    setSelectedBand(0);
    setProbe('正在读取栅格元数据…');

    const clickKey = map.on('singleclick', async event => {
      const image = imageRef.current;
      const band = bandsRef.current[selectedBandRef.current];
      if (!image || !band) {
        setProbe('栅格元数据尚未就绪');
        return;
      }
      probeAbortRef.current?.abort();
      const probeController = new AbortController();
      const sequence = ++probeSequenceRef.current;
      probeAbortRef.current = probeController;
      setProbe('正在读取像元物理值…');
      try {
        const result = await probeRasterValue(
          image,
          [Number(event.coordinate[0]), Number(event.coordinate[1])],
          band,
          probeController.signal,
        );
        if (active && sequence === probeSequenceRef.current) setProbe(formatProbe(result));
      } catch (error) {
        if (active && sequence === probeSequenceRef.current && !isAbortError(error)) {
          setProbe(`像元读取失败：${error instanceof Error ? error.message : String(error)}`);
        }
      }
    });

    void (async () => {
      try {
        const file = await fromUrl(url, {}, loadController.signal);
        rawFile = file;
        const image = await file.getImage();
        const loadedBands = await loadRasterBands(image);
        if (!active) {
          closeRawFile();
          return;
        }
        imageRef.current = image;
        bandsRef.current = loadedBands;
        setBands(loadedBands);
        setProbe('单击地图探查物理值');
      } catch (error) {
        if (!active) closeRawFile();
        else if (!isAbortError(error)) {
          setProbe(`栅格元数据读取失败：${error instanceof Error ? error.message : String(error)}`);
        }
      }
    })();

    return () => {
      active = false;
      loadController.abort();
      probeAbortRef.current?.abort();
      probeAbortRef.current = null;
      probeSequenceRef.current += 1;
      unByKey(clickKey);
      imageRef.current = null;
      bandsRef.current = [];
      if (layerRef.current === layer) layerRef.current = null;
      map.setTarget(undefined);
      map.dispose();
      layer.dispose();
      source.dispose();
      closeRawFile();
    };
  }, [url]);

  useEffect(() => {
    layerRef.current?.setStyle(rasterStyle(palette, selectedBand + 1));
  }, [palette, selectedBand]);

  useEffect(() => {
    layerRef.current?.setOpacity(opacity);
  }, [opacity]);

  return <div className="map-workspace"><div className="artifact-toolbar"><label>色带<select aria-label="栅格色带" value={palette} onChange={event => setPalette(event.target.value as RasterPalette)}><option value="source">原始颜色</option><option value="gray">灰度</option><option value="viridis">翠绿渐变（Viridis）</option></select></label><label>波段<select aria-label="栅格波段" value={selectedBand} disabled={bands.length < 2} onChange={event => { setSelectedBand(Number(event.target.value)); setProbe('单击地图探查物理值'); }}>{bands.length ? bands.map(band => <option key={band.index} value={band.index}>波段 {band.number}{band.unit ? `（${band.unit}）` : ''}</option>) : <option value={0}>波段 1</option>}</select></label><label>透明度<input type="range" min="0.1" max="1" step="0.05" value={opacity} onChange={event => setOpacity(Number(event.target.value))} /></label><output aria-label="栅格探针" aria-live="polite">{probe}</output></div><div ref={host} className="artifact-map" /></div>;
}
