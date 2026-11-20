import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import sourceProfile from '../../products/groundwater/ui/product-ui.json';
import App from './App';
import { decodeProductUi, setProductUi } from './product-ui';
import { capabilitiesForProfile } from './test-fixtures';
import type { Capabilities } from './types';

const authoredProfile = decodeProductUi(sourceProfile);
const modelId = 'groundwater.monitoring-trend';
const title = '分析监测水位的季节变化';
const prompt = '请准备监测井水位的季节趋势和指定阈值事件分析。';
const checksum = (digit: string) => `sha256:${digit.repeat(64)}`;

function businessCapability() {
  return {
    ref: { id: 'groundwater.water-level-seasonality', version: '1.0.0', checksum: checksum('a') },
    title,
    description: '核对水位时序中的季节变化与阈值事件。',
    scenario: '用于按井比较长期水位时序。',
    tier: 'CORE',
    defaultVisible: true,
    roles: ['researcher', 'admin'],
    exampleId: 'trend',
    prompt,
    requiredServices: ['data', 'executor'],
    validationStatus: 'TECHNICALLY_VALIDATED',
    profiles: [{
      profileRef: { id: 'groundwater.monitoring-trend.seasonal-and-threshold', version: '1.0.0', checksum: checksum('b') },
      modelRef: { id: modelId, version: '1.0.0', checksum: checksum('1') },
      workflowRef: { id: modelId, version: '1.0.0', checksum: checksum('2') },
      resultContractRef: { id: 'groundwater.monitoring-trend.results', version: '1.0.0', checksum: checksum('c') },
      label: '季节趋势与阈值事件',
      roles: ['researcher'],
      inputs: [{
        label: '井水位观测表',
        meaning: '包含井号、时间与水位。',
        formats: ['parquet'],
        preconditions: ['核对水位单位和资料的时间范围。'],
      }],
      outputs: [{ name: 'aggregated-series', role: 'PRIMARY_RESULT', meaning: '按频率汇总水位数据。', format: 'parquet' }],
      limitations: ['阈值由使用者提供。'],
    }],
  };
}

function openApp(fixture: Capabilities = capabilitiesForProfile(authoredProfile)) {
  const calls = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method && options.method !== 'GET') throw new Error(`Unexpected write to ${url}`);
    const headers = { 'Content-Type': 'application/json' };
    if (url.endsWith('/capabilities')) return new Response(JSON.stringify(fixture), { headers });
    if (url.endsWith('/sessions')) return new Response(JSON.stringify({ items: [], nextCursor: null }), { headers });
    throw new Error(`Unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', calls);
  render(<App />);
  return calls;
}

function installCapability(capability = businessCapability()) {
  setProductUi(decodeProductUi({ ...sourceProfile, businessCapabilities: [capability] }));
}

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, '', '/#/');
});

afterEach(() => setProductUi(authoredProfile));

it('decodes the projected capability and rejects malformed profile references, nested fields and roles', () => {
  const row = businessCapability();
  const decode = (...items: unknown[]) => decodeProductUi({ ...sourceProfile, businessCapabilities: items });

  expect(decode(row).businessCapabilities).toEqual([row]);
  expect(() => decode({ ...row, roles: ['other'] })).toThrow('PRODUCT_UI_INVALID');
  expect(() => decode({ ...row, profiles: [{ ...row.profiles[0], workflowRef: { id: modelId, version: '1.0.0' } }] })).toThrow('PRODUCT_UI_INVALID');
  expect(() => decode({ ...row, profiles: [{ ...row.profiles[0], secret: 'ignored' }] })).toThrow('PRODUCT_UI_INVALID');
  expect(() => decode(row, { ...row, title: '相同身份' })).toThrow('PRODUCT_UI_INVALID');
});

it('opens a generated home entry with business inputs, expected outputs and limitations', async () => {
  installCapability();
  const calls = openApp();

  fireEvent.click(await screen.findByRole('button', { name: new RegExp(title) }));
  const dialog = await screen.findByRole('dialog', { name: title });
  expect(within(dialog).getByText('用于按井比较长期水位时序。')).toBeVisible();
  expect(within(dialog).getByText('井水位观测表')).toBeVisible();
  expect(within(dialog).getByText('核对水位单位和资料的时间范围。')).toBeVisible();
  expect(within(dialog).getByText('聚合后的监测序列')).toBeVisible();
  expect(within(dialog).getByText('按频率汇总水位数据。')).toBeVisible();
  expect(within(dialog).getByText('阈值由使用者提供。')).toBeVisible();
  expect(dialog).not.toHaveTextContent(modelId);
  expect(dialog).not.toHaveTextContent('sha256:');
  expect(calls.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
});

it.each(['viewer', 'admin', 'missing-service', 'stale-model', 'stale-workflow'])('hides ineligible entries in the homepage and catalog for %s', async condition => {
  installCapability();
  const fixture = capabilitiesForProfile(authoredProfile);
  if (condition === 'viewer' || condition === 'admin') fixture.currentUser.roles = [condition];
  if (condition === 'missing-service') fixture.services = ['executor'];
  const model = fixture.models.find(model => model.modelId === modelId)!;
  if (condition === 'stale-model') model.modelChecksum = checksum('3');
  if (condition === 'stale-workflow') model.workflowVersion = '1.0.1';
  openApp(fixture);

  await screen.findByTitle('项目：demo');
  expect(screen.queryByRole('button', { name: new RegExp(title) })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('更多场景'));
  expect(screen.queryByRole('button', { name: /发现趋势/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '能力目录' }));
  const catalog = await screen.findByRole('dialog', { name: '能力目录' });
  expect(within(catalog).queryByRole('button', { name: new RegExp(title) })).not.toBeInTheDocument();
  expect(within(catalog).queryByRole('button', { name: '发现趋势' })).not.toBeInTheDocument();
});

it('offers a role-eligible advanced capability through the catalog without featuring it on the homepage', async () => {
  installCapability({ ...businessCapability(), tier: 'ADVANCED', defaultVisible: false });
  openApp();

  await screen.findByTitle('项目：demo');
  expect(screen.queryByRole('button', { name: new RegExp(title) })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '能力目录' }));
  const catalog = await screen.findByRole('dialog', { name: '能力目录' });
  fireEvent.click(within(catalog).getByRole('button', { name: new RegExp(title) }));
  expect(await screen.findByRole('dialog', { name: title })).toBeVisible();
});

it('fills an editable draft and restores the earlier technical selection on undo without submitting', async () => {
  installCapability();
  const calls = openApp();
  const composer = await screen.findByRole('textbox', { name: '消息输入框' });
  await screen.findByTitle('项目：demo');
  fireEvent.click(screen.getByRole('button', { name: '工作流' }));
  const catalog = await screen.findByRole('dialog', { name: '能力目录' });
  const modelLabel = authoredProfile.models[modelId].displayName;
  fireEvent.click(within(catalog).getByRole('button', { name: `在会话中使用${modelLabel}` }));
  const previous = (composer as HTMLTextAreaElement).value;
  expect(screen.getByRole('region', { name: '选中的工作流' })).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: new RegExp(title) }));
  const dialog = await screen.findByRole('dialog', { name: title });
  fireEvent.click(within(dialog).getByRole('button', { name: '填入对话' }));
  await waitFor(() => expect(composer).toHaveFocus());
  expect(composer).toHaveValue(prompt);
  expect(screen.queryByRole('region', { name: '选中的工作流' })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '撤销填入' }));
  expect(composer).toHaveValue(previous);
  expect(screen.getByRole('region', { name: '选中的工作流' })).toBeVisible();
  expect(calls.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
});
