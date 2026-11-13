import type { GeoTIFF } from 'geotiff';

export type RasterProbeImage = Awaited<ReturnType<GeoTIFF['getImage']>>;

export type RasterBand = {
  index: number;
  number: number;
  scale: number;
  offset: number;
  unit: string | null;
  noData: number | null;
};

export type RasterProbeResult = {
  coordinate: [number, number];
} & (
  | { kind: 'outside' }
  | {
    kind: 'nodata';
    pixel: [number, number];
    band: RasterBand;
    rawValue: number;
  }
  | {
    kind: 'value';
    pixel: [number, number];
    band: RasterBand;
    rawValue: number;
    physicalValue: number;
  }
);

function metadataEntry(metadata: Record<string, unknown> | null, name: string): unknown {
  const entry = Object.entries(metadata ?? {}).find(([key]) => key.toUpperCase() === name);
  return entry?.[1];
}

function finiteMetadataNumber(value: unknown, fallback: number): number {
  if (typeof value !== 'number' && typeof value !== 'string') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function metadataUnit(value: unknown): string | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const unit = String(value).trim();
  return unit || null;
}

export async function loadRasterBands(image: RasterProbeImage): Promise<RasterBand[]> {
  const noData = image.getGDALNoData();
  return Promise.all(Array.from({ length: image.getSamplesPerPixel() }, async (_, index) => {
    const metadata = await image.getGDALMetadata(index);
    return {
      index,
      number: index + 1,
      scale: finiteMetadataNumber(metadataEntry(metadata, 'SCALE'), 1),
      offset: finiteMetadataNumber(metadataEntry(metadata, 'OFFSET'), 0),
      unit: metadataUnit(metadataEntry(metadata, 'UNITTYPE')),
      noData,
    };
  }));
}

function inverseAffinePixel(
  transformation: ArrayLike<number>,
  coordinate: [number, number],
): [number, number] | null {
  const [a, b, , d, e, f, , h] = Array.from(transformation);
  const determinant = (a * f) - (b * e);
  if (!Number.isFinite(determinant) || determinant === 0) return null;
  const x = coordinate[0] - d;
  const y = coordinate[1] - h;
  return [
    ((f * x) - (b * y)) / determinant,
    ((a * y) - (e * x)) / determinant,
  ];
}

async function coordinateToPixel(
  image: RasterProbeImage,
  coordinate: [number, number],
): Promise<[number, number]> {
  const transformation = await image.getFileDirectory().loadValue('ModelTransformation');
  if (transformation && typeof transformation === 'object') {
    const transformed = inverseAffinePixel(transformation, coordinate);
    if (transformed) return transformed.map(Math.floor) as [number, number];
  }

  const [resolutionX, resolutionY] = image.getResolution();
  const [tiePoint] = await image.getTiePoints();
  const [originX, originY] = image.getOrigin();
  const rasterX = tiePoint?.i ?? 0;
  const rasterY = tiePoint?.j ?? 0;
  const modelX = tiePoint?.x ?? originX;
  const modelY = tiePoint?.y ?? originY;
  return [
    Math.floor(rasterX + ((coordinate[0] - modelX) / resolutionX)),
    Math.floor(rasterY + ((coordinate[1] - modelY) / resolutionY)),
  ];
}

function isNoData(value: number, noData: number | null): boolean {
  if (noData === null) return false;
  return Number.isNaN(noData) ? Number.isNaN(value) : value === noData;
}

export async function probeRasterValue(
  image: RasterProbeImage,
  coordinate: [number, number],
  band: RasterBand,
  signal?: AbortSignal,
): Promise<RasterProbeResult> {
  if (band.index < 0 || band.index >= image.getSamplesPerPixel()) {
    throw new RangeError(`Raster band ${band.number} is not available.`);
  }
  const pixel = await coordinateToPixel(image, coordinate);
  if (pixel[0] < 0 || pixel[1] < 0 || pixel[0] >= image.getWidth() || pixel[1] >= image.getHeight()) {
    return { kind: 'outside', coordinate };
  }

  const rasters = await image.readRasters({
    window: [pixel[0], pixel[1], pixel[0] + 1, pixel[1] + 1],
    samples: [band.index],
    signal,
  });
  const samples = rasters[0];
  const rawValue = Number(samples?.[0]);
  const noData = samples instanceof Float32Array && band.noData !== null
    ? Math.fround(band.noData)
    : band.noData;
  if (isNoData(rawValue, noData)) {
    return { kind: 'nodata', coordinate, pixel, band, rawValue };
  }
  if (!Number.isFinite(rawValue)) {
    throw new Error('GeoTIFF returned a non-finite pixel value without a matching NoData marker.');
  }
  return {
    kind: 'value',
    coordinate,
    pixel,
    band,
    rawValue,
    physicalValue: (rawValue * band.scale) + band.offset,
  };
}
