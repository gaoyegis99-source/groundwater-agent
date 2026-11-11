import { fromArrayBuffer, writeArrayBuffer } from 'geotiff';
import { describe, expect, it, vi } from 'vitest';
import { loadRasterBands, probeRasterValue } from './raster-probe';

async function createFixture() {
  const buffer = writeArrayBuffer(new Float32Array([
    1, 2,
    -9999, 4,
  ]), {
    width: 2,
    height: 2,
    ModelPixelScale: [10, 10, 0],
    ModelTiepoint: [0, 0, 0, 100, 200, 0],
    GeographicTypeGeoKey: 4326,
    GTModelTypeGeoKey: 2,
    GDAL_NODATA: '-9999',
  });
  const image = await (await fromArrayBuffer(buffer)).getImage();
  vi.spyOn(image, 'getGDALMetadata').mockResolvedValue({
    SCALE: '0.5',
    OFFSET: '10',
    UNITTYPE: 'm',
  });
  return image;
}

describe('raster physical-value probing', () => {
  it('reads the selected pixel from a known GeoTIFF fixture and applies band scale/offset', async () => {
    const image = await createFixture();
    const [band] = await loadRasterBands(image);

    const result = await probeRasterValue(image, [115, 195], band);

    expect(band).toMatchObject({ index: 0, number: 1, scale: 0.5, offset: 10, unit: 'm' });
    expect(result).toMatchObject({
      kind: 'value',
      coordinate: [115, 195],
      pixel: [1, 0],
      rawValue: 2,
      physicalValue: 11,
    });
  });

  it('reports NoData explicitly and does not turn it into a physical value', async () => {
    const image = await createFixture();
    const [band] = await loadRasterBands(image);

    const result = await probeRasterValue(image, [105, 185], band);

    expect(result).toMatchObject({
      kind: 'nodata',
      pixel: [0, 1],
      rawValue: -9999,
    });
    expect(result).not.toHaveProperty('physicalValue');
  });

  it('uses the requested one-based band and returns outside coordinates without reading a window', async () => {
    const buffer = writeArrayBuffer([
      [[1, 2], [3, 4]],
      [[10, 20], [30, 40]],
    ], {
      ModelPixelScale: [10, 10, 0],
      ModelTiepoint: [0, 0, 0, 100, 200, 0],
      GeographicTypeGeoKey: 4326,
      GTModelTypeGeoKey: 2,
    });
    const image = await (await fromArrayBuffer(buffer)).getImage();
    vi.spyOn(image, 'getGDALMetadata').mockImplementation(async sample => (
      sample === 1 ? { scale: '2', offset: '-5', unittype: 'mg/L' } : null
    ));
    const bands = await loadRasterBands(image);
    const readRasters = vi.spyOn(image, 'readRasters');

    const selected = await probeRasterValue(image, [115, 195], bands[1]);
    expect(selected).toMatchObject({
      kind: 'value',
      pixel: [1, 0],
      rawValue: 20,
      physicalValue: 35,
      band: { index: 1, number: 2, unit: 'mg/L' },
    });
    expect(readRasters).toHaveBeenLastCalledWith(expect.objectContaining({
      window: [1, 0, 2, 1],
      samples: [1],
    }));

    readRasters.mockClear();
    await expect(probeRasterValue(image, [125, 195], bands[1])).resolves.toEqual({
      kind: 'outside',
      coordinate: [125, 195],
    });
    expect(readRasters).not.toHaveBeenCalled();
  });
});
