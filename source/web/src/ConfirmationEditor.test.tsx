import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ConfirmationEditor } from './ConfirmationEditor';
import { dataDetail } from './test-fixtures';
import type { Capabilities, Confirmation } from './types';

const json = (value: unknown) => new Response(JSON.stringify(value), {
  status: 200, headers: { 'content-type': 'application/json' },
});

const resourceConfirmation: Confirmation = {
  confirmationId: 'confirmation-resource', goalRevision: 1, status: 'PENDING',
  agentRunId: 'agent-1', reason: '', decision: null, decisionState: null,
  revision: 1, kind: 'INITIAL', actions: [{
    toolCallId: 'call-resource', tool: 'run_summary', modelId: 'groundwater.summary',
    modelVersion: '3.0.0', workflowId: 'groundwater.summary', workflowVersion: '1.0.0',
    arguments: { versionId: 'frozen-version' },
  }],
};

const resourceCapabilities: Capabilities = {
  productId: 'groundwater', projectId: 'project-a',
  currentUser: { subject: 'u', displayName: '用户', roles: ['researcher'] },
  models: [{
    modelId: 'groundwater.summary', description: 'summary', tool: 'run_summary',
    dataInputParameters: ['versionId'], inputSchema: {
      type: 'object', required: ['versionId'], properties: { versionId: { type: 'string' } },
    },
  }],
};

function renderResourceEditor(project = 'project-a', onSave = vi.fn()) {
  return render(<ConfirmationEditor
    confirmation={resourceConfirmation} capabilities={resourceCapabilities}
    project={project} busy={false} onClose={vi.fn()} onSave={onSave}
  />);
}

const jsonConfirmation: Confirmation = {
  confirmationId: 'confirmation-json', goalRevision: 1, status: 'PENDING',
  agentRunId: 'agent-1', reason: '', decision: null, decisionState: null,
  revision: 1, kind: 'INITIAL', actions: [{
    toolCallId: 'call-json', tool: 'run_json', modelId: 'groundwater.json',
    modelVersion: '1.0.0', workflowId: 'groundwater.json', workflowVersion: '1.0.0',
    arguments: { settings: { threshold: 1 }, steps: ['read'] },
  }],
};

const jsonCapabilities: Capabilities = {
  productId: 'groundwater', projectId: 'project-a',
  currentUser: { subject: 'u', displayName: '用户', roles: ['researcher'] },
  models: [{
    modelId: 'groundwater.json', description: 'json fields', tool: 'run_json',
    inputSchema: {
      type: 'object', required: ['settings', 'steps'], properties: {
        settings: { type: 'object' }, steps: { type: 'array' },
      },
    },
  }],
};

function renderJsonEditor(save = vi.fn()) {
  render(<ConfirmationEditor
    confirmation={jsonConfirmation} capabilities={jsonCapabilities}
    project="project-a" busy={false} onClose={vi.fn()} onSave={save}
  />);
  return save;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it('edits the complete action and submits a new immutable revision payload', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], nextCursor: null, matchBasis: 'catalog' }), { status: 200, headers: { 'content-type': 'application/json' } })));
  const confirmation: Confirmation = {
    confirmationId: 'confirmation-1', goalRevision: 4, status: 'PENDING',
    agentRunId: 'agent-1', reason: '', decision: null, decisionState: null,
    revision: 1, kind: 'INITIAL', actions: [{
      toolCallId: 'call-1', tool: 'run_summary', modelId: 'groundwater.summary',
      modelVersion: '1.1.0', workflowId: 'groundwater.summary', workflowVersion: '1.0.0',
      arguments: { resourceId: 'resource-1', threshold: 2 },
    }],
  };
  const capabilities: Capabilities = { productId: 'groundwater', projectId: 'p', currentUser: { subject: 'u', displayName: '用户', roles: ['researcher'] }, models: [{
    modelId: 'groundwater.summary', description: 'summary', tool: 'run_summary',
    dataInputParameters: ['resource_id'], inputSchema: {
      type: 'object', required: ['resource_id', 'threshold'], properties: {
        resourceId: { type: 'string' }, threshold: { type: 'number', minimum: 0, maximum: 10 },
      },
    },
  }] };
  const save = vi.fn();
  render(<ConfirmationEditor confirmation={confirmation} capabilities={capabilities} project="p" busy={false} onClose={vi.fn()} onSave={save} />);
  await userEvent.clear(screen.getByRole('spinbutton'));
  await userEvent.type(screen.getByRole('spinbutton'), '5');
  expect(screen.getByText('原值：2')).toBeVisible();
  expect(screen.getByText('字段变化 · 1')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '保存为新修订' }));
  expect(save).toHaveBeenCalledWith([{ toolCallId: 'call-1', arguments: {
    resourceId: 'resource-1', threshold: 5,
  } }]);
});

it('pages and searches a catalog of 51 datasets while retaining the selected version', async () => {
  const items = Array.from({ length: 51 }, (_, index) => {
    const number = index + 1;
    return {
      datasetId: `dataset-${number}`, project: 'project-a', title: `观测 ${number}`, tags: [],
      currentVersion: `version-${number}`, createdAt: '2026-11-10T00:00:00Z', formats: ['csv'], semantics: {},
    };
  });
  const datasetRequests: URLSearchParams[] = [];
  const detailRequests: string[] = [];
  let semanticId = 'semantic-seen';
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://local.test');
    if (url.pathname.endsWith('/datasets')) {
      datasetRequests.push(url.searchParams);
      const filtered = url.searchParams.has('query') ? items.slice(50) : items;
      const offset = url.searchParams.has('cursor') ? 50 : 0;
      return json({ items: filtered.slice(offset, offset + 50), nextCursor: filtered.length > offset + 50 ? 'dataset-50' : null, matchBasis: 'catalog' });
    }
    const item = items.find(candidate => url.pathname.endsWith(`/${candidate.currentVersion}`));
    if (!item) throw new Error(`unexpected endpoint ${url.pathname}`);
    detailRequests.push(item.currentVersion);
    return json(dataDetail({ versionId: item.currentVersion, datasetId: item.datasetId, title: item.title, semanticId }));
  }));

  const save = vi.fn();
  const user = userEvent.setup();
  renderResourceEditor('project-a', save);
  expect(await screen.findByRole('option', { name: '观测 50 · version-50' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: '观测 51 · version-51' })).not.toBeInTheDocument();
  expect(detailRequests).toEqual([]);
  await user.click(screen.getByRole('button', { name: '下一页数据集' }));
  const option = await screen.findByRole('option', { name: '观测 51 · version-51' });
  expect(detailRequests).toEqual([]);
  await user.selectOptions(screen.getByRole('combobox', { name: /输入资料/ }), option);
  const saveButton = screen.getByRole('button', { name: '保存为新修订' });
  await waitFor(() => expect(saveButton).toBeEnabled());
  expect(datasetRequests[1].get('cursor')).toBe('dataset-50');
  await user.click(screen.getByRole('button', { name: '上一页数据集' }));
  expect(await screen.findByRole('option', { name: '观测 50 · version-50' })).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: /输入资料/ })).toHaveValue('version-51');
  semanticId = 'semantic-after-selection';
  await user.type(screen.getByRole('searchbox', { name: '搜索数据集' }), '观测 51');
  await user.click(screen.getByRole('button', { name: '搜索' }));
  expect(await screen.findByRole('option', { name: '观测 51 · version-51' })).toBeInTheDocument();
  expect(datasetRequests.at(-1)?.get('query')).toBe('观测 51');
  expect(datasetRequests.at(-1)?.has('cursor')).toBe(false);
  await user.click(saveButton);
  expect(detailRequests).toEqual(['version-51']);
  const raw = dataDetail().objects.find(item => item.role === 'raw')!;
  expect(save).toHaveBeenCalledWith([{
    toolCallId: 'call-resource', arguments: { versionId: 'version-51' },
    expectedInputReferences: { versionId: {
      kind: 'data_version', service: 'data-primary', datasetId: 'dataset-51',
      versionId: 'version-51', semanticId: 'semantic-seen', objectId: raw.objectId,
      checksum: raw.checksum, mediaType: raw.mediaType, format: raw.format, sizeBytes: raw.size,
    } },
  }]);
});

it('applies and focuses an explicitly requested field without caller DOM lookup', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], nextCursor: null, matchBasis: 'catalog' }), { status: 200, headers: { 'content-type': 'application/json' } })));
  const confirmation: Confirmation = {
    confirmationId: 'confirmation-focus', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null, revision: 1, kind: 'INITIAL',
    actions: [{ toolCallId: 'call-1', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '3.0.0', workflowId: 'groundwater.summary', workflowVersion: '1.0.0', arguments: { versionId: 'old-version' } }],
  };
  const capabilities: Capabilities = { productId: 'groundwater', projectId: 'p', currentUser: { subject: 'u', displayName: '用户', roles: ['researcher'] }, models: [{
    modelId: 'groundwater.summary', description: 'summary', tool: 'run_summary', inputSchema: {
      type: 'object', required: ['versionId'], properties: { versionId: { type: 'string' } },
    },
  }] };
  render(<ConfirmationEditor confirmation={confirmation} capabilities={capabilities} project="p" busy={false} focusField="versionId" suggestedValue="current-version" onClose={vi.fn()} onSave={vi.fn()} />);
  const input = screen.getByRole('textbox');
  await waitFor(() => expect(input).toHaveValue('current-version'));
  await waitFor(() => expect(input).toHaveFocus());
});

it('shows resource loading and aborts stale project requests on switch and unmount', async () => {
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  }));
  vi.stubGlobal('fetch', fetchMock);

  const view = renderResourceEditor();
  expect(screen.getByRole('status')).toHaveTextContent('正在加载可选数据版本');
  expect(screen.getByRole('combobox', { name: /输入资料/ })).toBeDisabled();
  expect(screen.getByRole('combobox', { name: /输入资料/ })).toHaveValue('frozen-version');
  expect(screen.getByRole('option', { name: '当前冻结版本 · frozen-version' })).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const firstSignal = fetchMock.mock.calls[0][1]?.signal;

  view.rerender(<ConfirmationEditor
    confirmation={resourceConfirmation} capabilities={resourceCapabilities}
    project="project-b" busy={false} onClose={vi.fn()} onSave={vi.fn()}
  />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(firstSignal?.aborted).toBe(true);
  const secondSignal = fetchMock.mock.calls[1][1]?.signal;

  view.unmount();
  expect(secondSignal?.aborted).toBe(true);
});

it('reports a dataset-list failure and retries into a distinct empty state', async () => {
  let listRequests = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (!String(input).includes('/data/datasets?limit=50')) throw new Error(`unexpected endpoint ${String(input)}`);
    listRequests += 1;
    if (listRequests === 1) throw new TypeError('offline');
    return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
  }));

  renderResourceEditor();
  expect(await screen.findByRole('alert')).toHaveTextContent('可选数据版本加载失败');
  await userEvent.click(screen.getByRole('button', { name: '重新加载数据版本' }));
  expect(await screen.findByText('当前项目没有可选的数据版本。')).toBeVisible();
  expect(listRequests).toBe(2);
});

it('discloses a selected version failure and keeps other catalog options selectable', async () => {
  const detail = dataDetail({
    versionId: 'version-ok', datasetId: 'dataset-ok', title: '成功数据',
    objects: [{
      objectId: 'raw-ok', size: 42, checksum: `sha256:${'a'.repeat(64)}`,
      role: 'raw', format: 'csv', mediaType: 'text/csv',
    }],
  });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/datasets?limit=50')) return json({ items: [
      { datasetId: 'dataset-ok', project: 'project-a', title: '成功数据', tags: [], currentVersion: 'version-ok', createdAt: '2026-11-10T00:00:00Z', formats: ['csv'], semantics: {} },
      { datasetId: 'dataset-failed', project: 'project-a', title: '失败数据', tags: [], currentVersion: 'version-failed', createdAt: '2026-11-10T00:00:00Z', formats: ['csv'], semantics: {} },
    ], nextCursor: null, matchBasis: 'catalog' });
    if (url.includes('/data/versions/version-ok')) return json(detail);
    if (url.includes('/data/versions/version-failed')) throw new TypeError('offline');
    throw new Error(`unexpected endpoint ${url}`);
  }));

  const save = vi.fn();
  renderResourceEditor('project-a', save);
  const user = userEvent.setup();
  expect(await screen.findByRole('option', { name: '成功数据 · version-ok' })).toBeInTheDocument();
  const select = screen.getByRole('combobox', { name: /输入资料/ });
  const group = select.closest('details');
  if (group && !group.open) fireEvent.click(group.querySelector('summary')!);
  const saveButton = screen.getByRole('button', { name: '保存为新修订' });
  await user.selectOptions(select, 'version-failed');
  expect(await screen.findByRole('alert')).toHaveTextContent('所选数据版本核验失败');
  expect(screen.getByRole('button', { name: '重新核验所选版本' })).toBeVisible();
  expect(saveButton).toBeDisabled();
  expect(select).toBeEnabled();
  await user.selectOptions(select, 'version-ok');
  await waitFor(() => expect(saveButton).toBeEnabled());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await user.click(saveButton);
  expect(save).toHaveBeenCalledWith([expect.objectContaining({
    arguments: { versionId: 'version-ok' },
    expectedInputReferences: { versionId: expect.objectContaining({ objectId: 'raw-ok' }) },
  })]);
});

it('distinguishes versions without a raw object from an empty catalog', async () => {
  const detail = dataDetail({
    versionId: 'version-query-only', datasetId: 'dataset-query-only', title: '仅查询对象',
    objects: [{
      objectId: 'query-only', size: 12, checksum: `sha256:${'b'.repeat(64)}`,
      role: 'query', format: 'json', mediaType: 'application/json',
    }],
  });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/data/datasets?limit=50')) return json({ items: [{
      datasetId: 'dataset-query-only', project: 'project-a', title: '仅查询对象', tags: [],
      currentVersion: 'version-query-only', createdAt: '2026-11-10T00:00:00Z', formats: ['json'], semantics: {},
    }], nextCursor: null, matchBasis: 'catalog' });
    if (url.includes('/data/versions/version-query-only')) return json(detail);
    throw new Error(`unexpected endpoint ${url}`);
  }));

  renderResourceEditor();
  const option = await screen.findByRole('option', { name: /仅查询对象/ });
  await userEvent.selectOptions(screen.getByRole('combobox', { name: /输入资料/ }), option);
  expect(await screen.findByRole('alert')).toHaveTextContent('该版本没有唯一的原始对象');
  expect(screen.getByRole('button', { name: '保存为新修订' })).toBeDisabled();
  expect(screen.queryByText('当前项目没有可选的数据版本。')).not.toBeInTheDocument();
});

it('keeps the latest selection when an earlier version lookup finishes late', async () => {
  const items = ['first', 'second'].map(name => ({
    datasetId: `dataset-${name}`, currentVersion: `version-${name}`, title: name,
    project: 'project-a', tags: [], createdAt: '2026-11-10T00:00:00Z', formats: ['csv'], semantics: {},
  }));
  let releaseFirst!: (response: Response) => void;
  const first = new Promise<Response>(resolve => { releaseFirst = resolve; });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/data/datasets?')) return json({ items, nextCursor: null, matchBasis: 'catalog' });
    if (url.endsWith('/version-first')) return first;
    if (url.endsWith('/version-second')) return json(dataDetail({ datasetId: 'dataset-second', versionId: 'version-second' }));
    throw new Error(`unexpected endpoint ${url} ${init?.method}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  const save = vi.fn();
  renderResourceEditor('project-a', save);
  await screen.findByRole('option', { name: 'first · version-first' });
  const select = screen.getByRole('combobox', { name: /输入资料/ });
  const button = screen.getByRole('button', { name: '保存为新修订' });
  fireEvent.change(select, { target: { value: 'version-first' } });
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/version-first'))).toBe(true));
  const firstSignal = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/version-first'))?.[1]?.signal;
  expect(button).toBeDisabled();
  fireEvent.change(select, { target: { value: 'version-second' } });
  expect(firstSignal?.aborted).toBe(true);
  await waitFor(() => expect(button).toBeEnabled());
  await act(async () => releaseFirst(json(dataDetail({ datasetId: 'dataset-first', versionId: 'version-first' }))));
  expect(select).toHaveValue('version-second');
  fireEvent.click(button);
  expect(save).toHaveBeenCalledWith([expect.objectContaining({
    arguments: { versionId: 'version-second' },
    expectedInputReferences: { versionId: expect.objectContaining({ datasetId: 'dataset-second' }) },
  })]);
});

it('blocks invalid numeric revisions and keeps nullable numeric arguments typed', () => {
  const action = { ...jsonConfirmation.actions[0], arguments: { count: 2, ratio: null } };
  const confirmation = { ...jsonConfirmation, actions: [action] };
  const capabilities = { ...jsonCapabilities, models: [{
    ...jsonCapabilities.models[0],
    inputSchema: { type: 'object', properties: {
      count: { type: 'integer', minimum: 1, maximum: 4 },
      ratio: { type: ['number', 'null'], exclusiveMinimum: 0, exclusiveMaximum: 1 },
    }, required: ['count', 'ratio'] },
  }] };
  const save = vi.fn();
  render(<ConfirmationEditor confirmation={confirmation} capabilities={capabilities} project="p" busy={false} onClose={vi.fn()} onSave={save} />);
  const count = screen.getByRole('spinbutton', { name: /count/ });
  const ratio = screen.getByRole('spinbutton', { name: /ratio/ });
  const saveButton = screen.getByRole('button', { name: '保存为新修订' });
  for (const [value, message] of [['0', '不能小于 1'], ['5', '不能大于 4'], ['2.5', '请输入整数'], ['', '请输入必填数值']]) {
    fireEvent.change(count, { target: { value } });
    expect(count).toHaveAccessibleDescription(new RegExp(message));
    expect(saveButton).toBeDisabled();
    fireEvent.click(saveButton);
  }
  fireEvent.change(count, { target: { value: '4' } });
  for (const [value, message] of [['0', '必须大于 0'], ['1', '必须小于 1']]) {
    fireEvent.change(ratio, { target: { value } });
    expect(ratio).toHaveAccessibleDescription(new RegExp(message));
    expect(saveButton).toBeDisabled();
  }
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(ratio, { target: { value: '0.25' } });
  expect(saveButton).toBeEnabled();
  fireEvent.click(saveButton);
  expect(save).toHaveBeenLastCalledWith([{ toolCallId: action.toolCallId, arguments: { count: 4, ratio: 0.25 } }]);
  fireEvent.change(ratio, { target: { value: '' } });
  fireEvent.click(saveButton);
  expect(save).toHaveBeenLastCalledWith([{ toolCallId: action.toolCallId, arguments: { count: 4, ratio: null } }]);
});

it('retains an invalid JSON draft, reports it inline, and clears the error after recovery', () => {
  const save = renderJsonEditor();
  const settings = screen.getByRole('textbox', { name: /settings/ });
  const saveButton = screen.getByRole('button', { name: '保存为新修订' });

  fireEvent.change(settings, { target: { value: '{"threshold":' } });
  fireEvent.blur(settings);

  expect(settings).toHaveValue('{"threshold":');
  expect(settings).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByRole('alert')).toHaveTextContent('JSON');
  expect(saveButton).toBeDisabled();
  expect(save).not.toHaveBeenCalled();

  fireEvent.change(settings, { target: { value: '{"threshold": 2}' } });

  expect(settings).toHaveAttribute('aria-invalid', 'false');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByText('字段变化 · 1')).toBeVisible();
  expect(saveButton).toBeEnabled();
});

it('rejects the wrong JSON root type and saves object and array values without coercion', async () => {
  const save = renderJsonEditor();
  const settings = screen.getByRole('textbox', { name: /settings/ });
  const steps = screen.getByRole('textbox', { name: /steps/ });
  const saveButton = screen.getByRole('button', { name: '保存为新修订' });

  fireEvent.change(settings, { target: { value: '[]' } });
  expect(screen.getByRole('alert')).toHaveTextContent('对象');
  expect(saveButton).toBeDisabled();

  fireEvent.change(settings, { target: { value: '{"threshold": 3}' } });
  fireEvent.change(steps, { target: { value: '{"step": "read"}' } });
  expect(screen.getByRole('alert')).toHaveTextContent('数组');
  expect(saveButton).toBeDisabled();

  fireEvent.change(steps, { target: { value: '["read", "analyze"]' } });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(saveButton).toBeEnabled();
  await userEvent.click(saveButton);

  expect(save).toHaveBeenCalledWith([{ toolCallId: 'call-json', arguments: {
    settings: { threshold: 3 }, steps: ['read', 'analyze'],
  } }]);
  const saved = save.mock.calls[0][0][0].arguments;
  expect(Array.isArray(saved.settings)).toBe(false);
  expect(Array.isArray(saved.steps)).toBe(true);
});
