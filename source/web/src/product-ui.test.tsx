import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import sourceProfile from '../../products/groundwater/ui/product-ui.json';
import { ReadinessDrawer } from './ProductDrawers';
import { resolveViewerPreference } from './ArtifactViewer';
import { decodeProductUi, setProductUi, verifyProductUi } from './product-ui';
import { dataDetail } from './test-fixtures';

const copy = () => JSON.parse(JSON.stringify(sourceProfile));

it('strictly decodes the assembled profile and rejects unknown fields', () => {
  const profile = decodeProductUi(copy());
  expect(profile.brand.productName).toBe('地下水分析');
  const invalid = copy();
  invalid.brand.script = '<script />';
  expect(() => decodeProductUi(invalid)).toThrow('PRODUCT_UI_INVALID');
});

it('requires product and model identities to exactly match capabilities', () => {
  const profile = decodeProductUi(copy());
  setProductUi(profile);
  const models = Object.keys(profile.models).map(modelId => ({ modelId, description: '', tool: modelId }));
  expect(() => verifyProductUi({ productId: profile.productId, projectId: 'p', models })).not.toThrow();
  expect(() => verifyProductUi({ productId: 'other', projectId: 'p', models })).toThrow('PRODUCT_UI_IDENTITY_MISMATCH');
  expect(() => verifyProductUi({ productId: profile.productId, projectId: 'p', models: models.slice(1) })).toThrow('PRODUCT_UI_IDENTITY_MISMATCH');
});

it('falls back to auto when a recommended viewer is incompatible', () => {
  const object = dataDetail({ objects: [{ objectId: 'object', size: 10, checksum: 'a'.repeat(64), role: 'raw', format: 'csv', mediaType: 'text/csv' }] }).objects[0];
  expect(resolveViewerPreference('map', object)).toBe('auto');
  expect(resolveViewerPreference('chart', object)).toBe('chart');
  expect(resolveViewerPreference('metadata', object)).toBe('metadata');
});

it.each([200, 503])('renders structured readiness returned with HTTP %s', async status => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: status === 200 ? 'READY' : 'DEGRADED', components: { kernel: { status: 'UP' }, data: { status: 'DOWN', error: 'DATA_UNAVAILABLE' } } }), { status, headers: { 'Content-Type': 'application/json' } })));
  render(<ReadinessDrawer onClose={vi.fn()} />);
  expect(await screen.findByText(status === 200 ? 'READY' : 'DEGRADED')).toBeVisible();
  expect(screen.getByText('DATA_UNAVAILABLE')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
});

it('renders unknown service state after a network failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
  render(<ReadinessDrawer onClose={vi.fn()} />);
  expect((await screen.findAllByText('UNKNOWN')).length).toBeGreaterThan(1);
  expect(screen.getByRole('alert')).toBeVisible();
});

it.each([
  { status: 'UP' },
  { status: 'DOWN', components: null },
  { status: 'UP', components: {} },
])('rejects readiness without a non-empty component map: %o', async payload => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })));
  render(<ReadinessDrawer onClose={vi.fn()} />);
  expect((await screen.findAllByText('UNKNOWN')).length).toBeGreaterThan(1);
  expect(screen.getByRole('alert')).toBeVisible();
  expect(screen.getByText(/API_RESPONSE_INVALID/)).toBeInTheDocument();
});

it('shows only services selected by the assembled product topology', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    status: 'UP', components: {
      kernel: { status: 'UP' }, data: { status: 'UP' }, executor: { status: 'UP' },
      'agent-provider': { status: 'UP' }, 'intent-router-provider': { status: 'UP' },
    },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
  render(<ReadinessDrawer services={['data', 'executor']} onClose={vi.fn()} />);
  await screen.findAllByText('UP');
  expect(screen.getByText('数据服务')).toBeVisible();
  expect(screen.getByText('执行服务')).toBeVisible();
  expect(screen.queryByText('知识服务')).not.toBeInTheDocument();
  expect(screen.queryByText('向量检索')).not.toBeInTheDocument();
});

it('does not restore legacy capability services when the assembled topology is empty', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    status: 'UP', components: {
      kernel: { status: 'UP' }, 'agent-provider': { status: 'UP' },
      'intent-router-provider': { status: 'UP' },
    },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
  render(<ReadinessDrawer services={[]} onClose={vi.fn()} />);
  await screen.findAllByText('UP');
  expect(screen.queryByText('数据服务')).not.toBeInTheDocument();
  expect(screen.queryByText('知识服务')).not.toBeInTheDocument();
  expect(screen.queryByText('执行服务')).not.toBeInTheDocument();
});
