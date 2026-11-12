import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    return json(dataDetail({ versionId: item.currentVersion, datasetId: item.datasetId, title: item.title, semanticId }));
  }));

  const save = vi.fn();
  const user = userEvent.setup();
  renderResourceEditor('project-a', save);
  expect(await screen.findByRole('option', { name: '观测 50 · version-50' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: '观测 51 · version-51' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '下一页数据集' }));
  const option = await screen.findByRole('option', { name: '观测 51 · version-51' });
  await user.selectOptions(screen.getByRole('combobox', { name: /输入资料/ }), option);
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
  await user.click(screen.getByRole('button', { name: '保存为新修订' }));
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

it('keeps successful resource options and discloses failed version details', async () => {
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

  renderResourceEditor();
  expect(await screen.findByRole('option', { name: '成功数据 · version-ok' })).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('1 个数据版本详情加载失败');
  expect(screen.getByRole('combobox', { name: /输入资料/ })).toBeEnabled();
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
  expect(await screen.findByText('1 个数据版本没有原始对象，不能作为本次输入。')).toBeVisible();
  expect(screen.queryByRole('option', { name: /仅查询对象/ })).not.toBeInTheDocument();
  expect(screen.queryByText('当前项目没有可选的数据版本。')).not.toBeInTheDocument();
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
