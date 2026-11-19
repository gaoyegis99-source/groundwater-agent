import { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import WebGLTileLayer from 'ol/layer/WebGLTile';
import type { Style as RasterStyle } from 'ol/layer/WebGLTile';
import GeoTIFF from 'ol/source/GeoTIFF';
import { unByKey } from 'ol/Observable';
import { getContext } from 'ol/webgl';
import { fromUrl } from 'geotiff';
import { mapOptions } from './map-options';
import {
  loadRasterBands,
  probeRasterValue,
  type RasterBand,
  type RasterProbeImage,
  type RasterProbeResult,
} from './raster-probe';
import 'ol/ol.css';

type RasterPalette = 'source' | 'gray' | 'viridis';

export interface RasterCategory { value: number; color: string; label: string }

function rasterStyle(palette: RasterPalette, band: number, categories?: RasterCategory[]): RasterStyle {
  if (categories?.length) return { color: ['match', ['band', band], ...categories.flatMap(item => [item.value, item.color]), 'transparent'] };
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

export function RasterViewer({ url, categories }: { url: string; categories?: RasterCategory[] }) {
  const categorical = !!categories?.length;
  const categoriesKey = JSON.stringify(categories ?? []);
  const host = useRef<HTMLDivElement>(null);
  const layerRef = useRef<WebGLTileLayer | null>(null);
  const imageRef = useRef<RasterProbeImage | null>(null);
  const bandsRef = useRef<RasterBand[]>([]);
  const selectedBandRef = useRef(0);
  const probeAbortRef = useRef<AbortController | null>(null);
  const probeSequenceRef = useRef(0);
  const probeCenterRef = useRef<(() => void) | null>(null);
  const [opacity, setOpacity] = useState(.85);
  const [palette, setPalette] = useState<RasterPalette>('source');
  const [bands, setBands] = useState<RasterBand[]>([]);
  const [selectedBand, setSelectedBand] = useState(0);
  const [probe, setProbe] = useState('正在读取栅格元数据…');
  const [hasMapRenderer, setHasMapRenderer] = useState(true);
  const [coordinateSystem, setCoordinateSystem] = useState<{ code: string; unit: string } | null>();
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
    const source = new GeoTIFF({ sources: [{ url }], normalize: !categorical, interpolate: !categorical });
    const context = getContext(document.createElement('canvas'));
    const layer = context ? new WebGLTileLayer({ source, opacity, style: rasterStyle(palette, 1, categories) }) : null;
    context?.getExtension('WEBGL_lose_context')?.loseContext();
    setHasMapRenderer(layer !== null);
    const rasterView = source.getView();
    const map = new Map({ ...mapOptions(), target: host.current, layers: layer ? [layer] : [], view: rasterView.catch(() => ({})) });

    layerRef.current = layer;
    imageRef.current = null;
    bandsRef.current = [];
    selectedBandRef.current = 0;
    setBands([]);
    setSelectedBand(0);
    setCoordinateSystem(undefined);
    setProbe('正在读取栅格元数据…');

    async function probeCoordinate(coordinate: number[]) {
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
          [coordinate[0], coordinate[1]],
          band,
          probeController.signal,
        );
        if (active && sequence === probeSequenceRef.current) setProbe(formatProbe(result));
      } catch (error) {
        if (active && sequence === probeSequenceRef.current && !isAbortError(error)) {
          setProbe(`像元读取失败：${error instanceof Error ? error.message : String(error)}`);
        }
      }
    }
    const clickKey = map.on('singleclick', event => void probeCoordinate(event.coordinate));
    probeCenterRef.current = () => {
      const center = map.getView().getCenter();
      if (center) void probeCoordinate(center);
      else setProbe('地图视图尚未就绪');
    };
    void rasterView.then(() => {
      if (!active) return;
      const projection = source.getProjection();
      const unit = projection?.getUnits();
      setCoordinateSystem(projection ? { code: projection.getCode(), unit: unit === 'degrees' ? '°' : unit ?? '未声明' } : null);
    }).catch(() => { if (active) setCoordinateSystem(null); });

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
        setProbe(layer ? '单击地图探查物理值' : '可探查视图中心的像元值');
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
      probeCenterRef.current = null;
      unByKey(clickKey);
      imageRef.current = null;
      bandsRef.current = [];
      if (layerRef.current === layer) layerRef.current = null;
      map.setTarget(undefined);
      map.dispose();
      layer?.dispose();
      source.dispose();
      closeRawFile();
    };
  }, [url, categorical]);

  useEffect(() => {
    layerRef.current?.setStyle(rasterStyle(palette, selectedBand + 1, JSON.parse(categoriesKey)));
  }, [palette, selectedBand, categoriesKey]);

  useEffect(() => {
    layerRef.current?.setOpacity(opacity);
  }, [opacity]);

  return <div className="map-workspace">
    {categorical && <ul className="raster-categories" aria-label="等级图例">
      {categories?.map(item => <li key={item.value}><span style={{ backgroundColor: item.color }} />{item.label}</li>)}
    </ul>}
    <div className="artifact-toolbar raster-tools">
      <label>色带<select aria-label="栅格色带" disabled={categorical || !hasMapRenderer} value={palette} onChange={event => setPalette(event.target.value as RasterPalette)}>
        <option value="source">原始颜色</option><option value="gray">灰度</option><option value="viridis">翠绿渐变（Viridis）</option>
      </select></label>
      <label>波段<select aria-label="栅格波段" value={selectedBand} disabled={bands.length < 2} onChange={event => {
        probeAbortRef.current?.abort();
        probeSequenceRef.current += 1;
        setSelectedBand(Number(event.target.value));
        setProbe(hasMapRenderer ? '单击地图探查物理值' : '可探查视图中心的像元值');
      }}>
        {bands.length ? bands.map(band => <option key={band.index} value={band.index}>波段 {band.number}{band.unit ? `（${band.unit}）` : ''}</option>) : <option value={0}>波段 1</option>}
      </select></label>
      <label>透明度<input type="range" min="0.1" max="1" step="0.05" value={opacity} disabled={!hasMapRenderer} onChange={event => setOpacity(Number(event.target.value))} /></label>
      <button type="button" disabled={!bands.length || coordinateSystem === undefined} onClick={() => probeCenterRef.current?.()}>探查视图中心</button>
    </div>
    <div className="map-probe">
      {!hasMapRenderer && <p role="status">当前浏览器无法使用 WebGL，栅格图层无法绘制。仍可探查视图中心或下载原文件。</p>}
      {coordinateSystem !== undefined && <p className="muted">
        {coordinateSystem ? `栅格坐标参考系 ${coordinateSystem.code} · X、Y 单位 ${coordinateSystem.unit}` : '栅格坐标参考系未解析 · X、Y 单位未声明'}
      </p>}
      <output aria-label="栅格探针" aria-live="polite">{probe}</output>
    </div>
    <div ref={host} className="artifact-map" tabIndex={0} role="region" aria-label="栅格地图" />
  </div>;
}
