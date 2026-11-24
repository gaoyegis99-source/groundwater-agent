import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import authored from '../../products/groundwater/ui/product-ui.json';
import porous from '../../products/groundwater/ui/product-ui-porous.v1.json';
import App from './App';
import { caseRequestParameters } from './case-request';
import { decodeProductUi, setProductUi } from './product-ui';
import { capabilitiesForProfile, dataDetail } from './test-fixtures';

const modelId = 'groundwater.monitoring-trend';
const caseTag = 'case:monitoring';
const caseRequest = porous.models[modelId].caseRequest;
const original = decodeProductUi(authored);

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, '', '/#/capabilities');
});

afterEach(() => setProductUi(original));

it('recommends the case for a plain request and keeps other intents and named data on their normal route', () => {
  expect(caseRequestParameters(caseRequest, '我想要分析监测趋势。')).toEqual({ seasonalTrendEnabled: false, thresholdEventsEnabled: false });
  expect(caseRequestParameters(caseRequest, '请分析地下水水位趋势，同时检查阈值超限')).toEqual({ seasonalTrendEnabled: false, thresholdEventsEnabled: true });
  for (const message of [
    '监测趋势怎么分析？', '请设计监测趋势分析方案', '请分析监测趋势，使用我上传的观测表。',
    '请分析监测趋势，并检查阈值事件，阈值设为 83。', '请分析监测趋势，但不要检查阈值事件。',
  ]) expect(caseRequestParameters(caseRequest, message)).toBeNull();
});

it('binds the case version and prefills editable parameters without sending or replacing an explicit value', async () => {
  const profile = decodeProductUi({ ...authored, models: { ...authored.models, [modelId]: {
    ...authored.models[modelId], inputCaseTag: caseTag, inputCaseParameters: { groupField: 'well', timeField: 'time' },
  } } });
  setProductUi(profile);
  const capabilities = capabilitiesForProfile(profile);
  const model = capabilities.models.find(item => item.modelId === modelId)!;
  model.inputSchema = {
    type: 'object',
    properties: { versionId: { type: 'string' }, groupField: { type: 'string' }, timeField: { type: 'string' } },
    required: ['versionId', 'groupField', 'timeField'],
  };
  model.dataInputParameters = ['versionId'];
  const sample = dataDetail({ title: '监测趋势案例' });
  sample.objects[0] = { ...sample.objects[0], format: 'parquet', mediaType: 'application/vnd.apache.parquet' };
  const { datasetId, currentVersion, title, project, createdAt, semantics } = sample;
  const fetch = vi.fn(async (raw: string, options?: RequestInit) => {
    expect(options?.method || 'GET').toBe('GET');
    const url = new URL(raw, 'http://localhost');
    if (url.pathname.endsWith('/capabilities')) return Response.json(capabilities);
    if (url.pathname.endsWith('/sessions')) return Response.json({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/data/datasets')) return Response.json({
      items: [{ datasetId, currentVersion, title, project, createdAt, semantics, formats: ['parquet'],
        tags: [caseTag, 'case-batch:test', 'input:versionId'] }], nextCursor: null,
      matchBasis: 'case_insensitive_literal_metadata_substring',
    });
    if (url.pathname.endsWith(`/data/versions/${currentVersion}`)) return Response.json(sample);
    throw new Error(`Unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  render(<App />);

  fireEvent.click(await screen.findByRole('button', { name: '在会话中使用监测趋势' }));
  const workflow = within(await screen.findByRole('region', { name: '选中的工作流' }));
  fireEvent.click(workflow.getByText(/资料已绑定 0\/1/));
  fireEvent.change(workflow.getByRole('textbox', { name: /分组字段/ }), { target: { value: 'station' } });
  fireEvent.click(workflow.getByRole('button', { name: '匹配案例资料' }));
  fireEvent.click(await workflow.findByRole('button', { name: /应用.*案例参数/ }));

  expect(workflow.getByText('资料已绑定 1/1')).toBeVisible();
  expect(workflow.getByRole('textbox', { name: /分组字段/ })).toHaveValue('station');
  const timeField = workflow.getByRole('textbox', { name: /时间字段/ });
  expect(timeField).toHaveValue('time');
  fireEvent.change(timeField, { target: { value: 'observed_at' } });
  expect(timeField).toHaveValue('observed_at');
  expect(fetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
});

it('matches the case tag for a direct trend request and holds submission until its requested business parameters are supplied', async () => {
  window.history.replaceState({}, '', '/#/');
  const profile = decodeProductUi({ ...authored, models: { ...authored.models, [modelId]: {
    ...authored.models[modelId], inputCaseTag: caseTag, inputCaseParameters: { groupField: 'well' }, caseRequest,
  } } });
  setProductUi(profile);
  const capabilities = capabilitiesForProfile(profile);
  const model = capabilities.models.find(item => item.modelId === modelId)!;
  model.inputSchema = {
    type: 'object',
    properties: {
      versionId: { type: 'string' }, groupField: { type: 'string' }, seasonalTrendEnabled: { type: 'boolean' },
      thresholdEventsEnabled: { type: 'boolean' }, seasonFrequency: { type: 'string', enum: ['month', 'quarter'] },
      eventComparison: { type: 'string', enum: ['above', 'below'] }, eventThreshold: { type: 'number' },
    },
    required: ['versionId', 'groupField'],
  };
  model.dataInputParameters = ['versionId'];
  model.applicabilityStatus = 'REQUIRES_CURRENT_KNOWLEDGE';
  const sample = dataDetail({ title: '合成监测趋势案例' });
  sample.objects[0] = { ...sample.objects[0], format: 'parquet', mediaType: 'application/vnd.apache.parquet' };
  const { datasetId, currentVersion, title, project, createdAt, semantics } = sample;
  const writes: string[] = [];
  const fetch = vi.fn(async (raw: string, options?: RequestInit) => {
    const url = new URL(raw, 'http://localhost');
    if (options?.method === 'POST') {
      writes.push(url.pathname);
      return new Response(null, { status: 503 });
    }
    if (url.pathname.endsWith('/capabilities')) return Response.json(capabilities);
    if (url.pathname.endsWith('/sessions')) return Response.json({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/data/datasets')) {
      expect(url.searchParams.get('tag')).toBe(caseTag);
      return Response.json({ items: [{ datasetId, currentVersion, title, project, createdAt, semantics, formats: ['parquet'],
        tags: [caseTag, 'case-batch:test', 'input:versionId'] }], nextCursor: null,
      matchBasis: 'case_insensitive_literal_metadata_substring' });
    }
    if (url.pathname.endsWith(`/data/versions/${currentVersion}`)) return Response.json(sample);
    throw new Error(`Unexpected endpoint ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  const request = '请分析监测趋势，并检查季节趋势和阈值事件。';
  const composer = await screen.findByRole('textbox', { name: '消息输入框' });
  fireEvent.change(composer, { target: { value: request } });
  const send = screen.getByRole('button', { name: '发送消息', exact: true });
  fireEvent.click(send);
  const workflow = within(await screen.findByRole('region', { name: '选中的工作流' }));
  fireEvent.click(await workflow.findByRole('button', { name: '应用 1 份候选资料和案例参数' }));
  expect(composer).toHaveValue(request);
  expect(workflow.getByRole('textbox', { name: /分组字段/ })).toHaveValue('well');
  expect(workflow.getByText(/请补充.*事件阈值/)).toBeVisible();
  expect(send).toBeDisabled();
  expect(writes).toEqual([]);
  fireEvent.change(workflow.getByRole('combobox', { name: /季节周期/ }), { target: { value: 'month' } });
  fireEvent.change(workflow.getByRole('combobox', { name: /阈值比较方式/ }), { target: { value: 'above' } });
  fireEvent.change(workflow.getByRole('spinbutton', { name: /事件阈值/ }), { target: { value: '83' } });
  expect(send).toBeEnabled();
  expect(writes).toEqual([]);
});
