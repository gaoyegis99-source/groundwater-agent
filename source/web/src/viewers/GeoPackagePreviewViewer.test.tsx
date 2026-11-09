import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { dataDetail } from '../test-fixtures';
import { GeoPackagePreviewViewer } from './GeoPackagePreviewViewer';

const start = vi.fn();
const wait = vi.fn();

vi.mock('../api', async importOriginal => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    startDataPreview: (...args: unknown[]) => start(...args),
    waitForDataPreview: (...args: unknown[]) => wait(...args),
    cancelDataPreview: vi.fn(),
  };
});

const resource = dataDetail({
  objects: [{
    objectId: 'gpkg-1', size: 2048, checksum: 'b'.repeat(64), role: 'raw',
    format: 'geopackage', mediaType: 'application/geopackage+sqlite3',
  }],
});

beforeEach(() => {
  start.mockReset(); wait.mockReset();
  start.mockResolvedValue({ previewId: 'preview-gpkg' });
  wait.mockResolvedValue({
    previewId: 'preview-gpkg', state: 'SUCCEEDED', error: null, receipt: {}, resultAvailable: true, expiresAt: null,
    result: {
      kind: 'geopackage_layer', metadata: {
        layers: [{ tableName: 'wells', identifier: '监测井', geometryTypeName: 'POINT' }],
        selectedLayer: 'wells', featureCount: 2, crs: 'EPSG:0', mapAvailable: false,
      },
      fields: [{ fieldId: 'name', name: 'name', type: 'TEXT' }], rows: [['W01']], features: [],
      truncated: false, warnings: ['坐标参考系无法转换到 WGS84，仅显示属性表。'], nextOffset: null,
    },
  });
});

it('falls back to the attribute table when a GeoPackage CRS cannot be converted', async () => {
  render(<GeoPackagePreviewViewer resource={resource} object={resource.objects[0]} url="/points.gpkg" />);
  expect(await screen.findByText('W01')).toBeVisible();
  expect(screen.getByText('坐标参考系无法转换到 WGS84，仅显示属性表。')).toBeVisible();
  expect(screen.getByRole('button', { name: '地图' })).toBeDisabled();
  expect(screen.getByLabelText('GeoPackage 图层')).toHaveValue('wells');
});
