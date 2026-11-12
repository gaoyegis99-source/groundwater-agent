import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { GeoViewer, RasterViewer } from './SpatialViewers';

const ol = vi.hoisted(() => ({
  maps: [] as any[],
  sources: [] as any[],
  layers: [] as any[],
  rasterSources: [] as any[],
  rasterLayers: [] as any[],
  unByKey: vi.fn(),
}));

const rawRaster = vi.hoisted(() => {
  const image = {
    getSamplesPerPixel: vi.fn(() => 2),
    getGDALNoData: vi.fn(() => -9999),
    getGDALMetadata: vi.fn(async (sample: number) => (
      sample === 0
        ? { SCALE: '0.5', OFFSET: '10', UNITTYPE: 'm' }
        : { SCALE: '2', OFFSET: '-5', UNITTYPE: 'mg/L' }
    )),
    getFileDirectory: vi.fn(() => ({ loadValue: vi.fn(async () => undefined) })),
    getTiePoints: vi.fn(async () => [{ i: 0, j: 0, k: 0, x: 100, y: 200, z: 0 }]),
    getOrigin: vi.fn(() => [100, 200, 0]),
    getResolution: vi.fn(() => [10, -10, 0]),
    getWidth: vi.fn(() => 2),
    getHeight: vi.fn(() => 2),
    readRasters: vi.fn(async ({ samples, window }: { samples: number[]; window: number[] }) => (
      [new Float32Array([window[1] === 1 ? -9999 : samples[0] === 0 ? 2 : 20])]
    )),
  };
  const file = { getImage: vi.fn(async () => image), close: vi.fn(() => false) };
  return { image, file, fromUrl: vi.fn(async () => file) };
});

vi.mock('geotiff', () => ({ fromUrl: rawRaster.fromUrl }));

vi.mock('ol/Map', () => ({
  default: class MockMap {
    handlers = new globalThis.Map<string, (event: any) => void>();
    view = { fit: vi.fn() };
    setTarget = vi.fn();
    dispose = vi.fn();
    forEachFeatureAtPixel = vi.fn(() => null);
    on = vi.fn((type: string, handler: (event: any) => void) => {
      this.handlers.set(type, handler);
      return { target: this, type, listener: handler };
    });
    constructor(public options: any) { ol.maps.push(this); }
    getView() { return this.view; }
  },
}));

vi.mock('ol/View', () => ({ default: class MockView { constructor(public options: any) {} } }));
vi.mock('ol/format/GeoJSON', () => ({ default: class MockGeoJSON {} }));
vi.mock('ol/source/Vector', () => ({
  default: class MockVectorSource {
    state = 'loading';
    features: any[] = [];
    listeners = new globalThis.Map<string, ((event?: any) => void)[]>();
    onceListeners = new globalThis.Map<string, ((event?: any) => void)[]>();
    dispose = vi.fn();
    on = vi.fn((type: string, handler: (event?: any) => void) => {
      this.listeners.set(type, [...(this.listeners.get(type) ?? []), handler]);
      return { target: this, type, listener: handler };
    });
    once = vi.fn((type: string, handler: (event?: any) => void) => {
      this.onceListeners.set(type, [...(this.onceListeners.get(type) ?? []), handler]);
      return { target: this, type, listener: handler };
    });
    constructor(public options: any) { ol.sources.push(this); }
    getState() { return this.state; }
    getFeatures() { return this.features; }
    getExtent() { return [0, 0, 10, 10]; }
    emitReady(features: any[]) {
      this.features = features;
      this.state = 'ready';
      for (const handler of this.onceListeners.get('change') ?? []) handler();
      this.onceListeners.delete('change');
      for (const handler of this.listeners.get('change') ?? []) handler();
    }
  },
}));
vi.mock('ol/layer/Vector', () => ({
  default: class MockVectorLayer {
    source: any;
    style: any;
    setStyle = vi.fn((style: any) => { this.style = style; });
    dispose = vi.fn();
    constructor(options: any) { this.source = options.source; this.style = options.style; ol.layers.push(this); }
  },
}));
vi.mock('ol/style', () => {
  class MockStyle { constructor(public options: any) {} }
  class MockFill { constructor(public options: any) {} }
  class MockStroke { constructor(public options: any) {} }
  class MockCircleStyle { constructor(public options: any) {} }
  return { Style: MockStyle, Fill: MockFill, Stroke: MockStroke, Circle: MockCircleStyle };
});
vi.mock('ol/Observable', () => ({ default: class MockObservable {}, unByKey: ol.unByKey }));
vi.mock('ol/proj', () => ({ toLonLat: (coordinate: number[]) => coordinate }));
vi.mock('ol/layer/WebGLTile', () => ({
  default: class MockWebGLTileLayer {
    setStyle = vi.fn();
    setOpacity = vi.fn();
    dispose = vi.fn();
    constructor(public options: any) { ol.rasterLayers.push(this); }
  },
}));
vi.mock('ol/source/GeoTIFF', () => ({
  default: class MockGeoTIFF {
    dispose = vi.fn();
    constructor(public options: any) { ol.rasterSources.push(this); }
    getView() { return {}; }
  },
}));

const feature = (properties: Record<string, unknown>) => ({
  getProperties: () => ({ geometry: {}, ...properties }),
  get: (name: string) => properties[name],
});

beforeEach(() => {
  ol.maps.length = 0;
  ol.sources.length = 0;
  ol.layers.length = 0;
  ol.rasterSources.length = 0;
  ol.rasterLayers.length = 0;
  ol.unByKey.mockReset();
  rawRaster.fromUrl.mockClear();
  rawRaster.file.getImage.mockClear();
  rawRaster.file.close.mockClear();
  rawRaster.image.getGDALMetadata.mockClear();
  rawRaster.image.readRasters.mockClear();
});

it('colors 150,000 GeoJSON features and preserves finite extrema', async () => {
  render(<GeoViewer url="/data/large.geojson" />);
  const features = Array.from({ length: 150_000 }, (_, index) => feature({ reading: index - 10 }));
  features.push(feature({ reading: Infinity }), feature({ reading: null }));
  act(() => ol.sources[0].emitReady(features));
  expect(await screen.findByText('-10 – 149989')).toBeVisible();
  expect(ol.maps).toHaveLength(1);
});

it('changes numeric styling and legend without rebuilding or reloading the GeoJSON map', async () => {
  render(<GeoViewer url="/data/first.geojson" />);
  expect(ol.maps).toHaveLength(1);
  expect(ol.sources).toHaveLength(1);
  expect(ol.sources[0].options.url).toBe('/data/first.geojson');

  const features = [
    feature({ level: 10, elevation: 100 }),
    feature({ level: 30, elevation: 200 }),
  ];
  act(() => ol.sources[0].emitReady(features));
  const selector = screen.getByRole('combobox', { name: 'GeoJSON 数值着色' });
  await waitFor(() => expect(selector).toHaveValue('level'));
  expect(screen.getByText('10 – 30')).toBeVisible();
  expect(ol.maps).toHaveLength(1);
  expect(ol.sources).toHaveLength(1);
  expect(ol.layers).toHaveLength(1);

  fireEvent.change(selector, { target: { value: 'elevation' } });
  await waitFor(() => expect(screen.getByText('100 – 200')).toBeVisible());

  expect(ol.maps).toHaveLength(1);
  expect(ol.sources).toHaveLength(1);
  expect(ol.layers).toHaveLength(1);
  expect(ol.maps[0].dispose).not.toHaveBeenCalled();
  const style = ol.layers[0].setStyle.mock.calls.at(-1)?.[0];
  expect(style).toEqual(expect.any(Function));
  expect(style(features[0]).options.fill.options.color).not.toBe(style(features[1]).options.fill.options.color);
});

it('disposes map listeners, layer, and source only when the URL changes or the viewer unmounts', () => {
  const view = render(<GeoViewer url="/data/first.geojson" />);
  const firstMap = ol.maps[0];
  const firstSource = ol.sources[0];
  const firstLayer = ol.layers[0];

  view.rerender(<GeoViewer url="/data/second.geojson" />);
  expect(ol.maps).toHaveLength(2);
  expect(ol.sources).toHaveLength(2);
  expect(firstMap.setTarget).toHaveBeenCalledWith(undefined);
  expect(firstMap.dispose).toHaveBeenCalledOnce();
  expect(firstLayer.dispose).toHaveBeenCalledOnce();
  expect(firstSource.dispose).toHaveBeenCalledOnce();
  expect(ol.unByKey).toHaveBeenCalled();

  const secondMap = ol.maps[1];
  const secondSource = ol.sources[1];
  const secondLayer = ol.layers[1];
  view.unmount();
  expect(secondMap.dispose).toHaveBeenCalledOnce();
  expect(secondLayer.dispose).toHaveBeenCalledOnce();
  expect(secondSource.dispose).toHaveBeenCalledOnce();
});

it('probes raw GeoTIFF band values and keeps palette and opacity presentation-only', async () => {
  const view = render(<RasterViewer url="/data/fixture.tif" />);

  await waitFor(() => expect(rawRaster.fromUrl).toHaveBeenCalledWith(
    '/data/fixture.tif',
    {},
    expect.any(AbortSignal),
  ));
  await waitFor(() => expect(screen.getByText('单击地图探查物理值')).toBeVisible());
  expect(ol.rasterSources[0].options).toMatchObject({
    sources: [{ url: '/data/fixture.tif' }],
    normalize: true,
  });

  await act(async () => {
    await ol.maps[0].handlers.get('singleclick')?.({ coordinate: [115, 195], pixel: [10, 10] });
  });
  expect(await screen.findByText(/物理值 11 m/)).toHaveTextContent(
    '地图坐标 115, 195；像元 [1, 0]；波段 1；物理值 11 m；原始值 2（scale 0.5，offset 10）',
  );
  expect(rawRaster.image.readRasters).toHaveBeenCalledWith(expect.objectContaining({
    window: [1, 0, 2, 1],
    samples: [0],
  }));

  const originalProbe = screen.getByText(/物理值 11 m/).textContent;
  fireEvent.change(screen.getByRole('combobox', { name: '栅格色带' }), { target: { value: 'viridis' } });
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.5' } });
  expect(ol.maps).toHaveLength(1);
  expect(ol.rasterSources).toHaveLength(1);
  expect(ol.rasterLayers).toHaveLength(1);
  expect(ol.rasterLayers[0].setStyle).toHaveBeenCalled();
  expect(ol.rasterLayers[0].setOpacity).toHaveBeenLastCalledWith(0.5);
  expect(screen.getByText(/物理值 11 m/)).toHaveTextContent(originalProbe ?? '');

  fireEvent.change(screen.getByRole('combobox', { name: '栅格波段' }), { target: { value: '1' } });
  await act(async () => {
    await ol.maps[0].handlers.get('singleclick')?.({ coordinate: [115, 195], pixel: [10, 10] });
  });
  expect(await screen.findByText(/物理值 35 mg\/L/)).toHaveTextContent('波段 2');

  await act(async () => {
    await ol.maps[0].handlers.get('singleclick')?.({ coordinate: [105, 185], pixel: [5, 15] });
  });
  expect(await screen.findByText(/NoData/)).toHaveTextContent(
    '地图坐标 105, 185；像元 [0, 1]；波段 2；NoData；原始值 -9999；单位 mg/L',
  );

  view.unmount();
  expect(ol.maps[0].dispose).toHaveBeenCalledOnce();
  expect(ol.rasterLayers[0].dispose).toHaveBeenCalledOnce();
  expect(ol.rasterSources[0].dispose).toHaveBeenCalledOnce();
  expect(rawRaster.file.close).toHaveBeenCalledOnce();
});
