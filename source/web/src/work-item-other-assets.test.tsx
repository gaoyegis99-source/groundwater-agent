import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { WorkItemOtherAssets } from './WorkItemOtherAssets';
import type { WorkItemDetail } from './types';

const createdAt = '2026-10-01T00:00:00Z';
const checksum = `sha256:${'a'.repeat(64)}`;
const oldChecksum = `sha256:${'b'.repeat(64)}`;
const workflowLink = {
  runId: 'run-1', agentRunId: 'agent-run-1', sessionId: 'session-1',
  workflowRef: { id: 'workflow-1', version: '1.0.0', checksum },
};
const detail: WorkItemDetail = {
  workItem: {
    workItemId: 'work-item-1', projectId: 'project', title: '监测工作', description: null,
    status: 'ACTIVE', revision: 1, archivedAt: null, createdAt, updatedAt: createdAt, ownerSubject: 'researcher',
  },
  sessions: [], tasks: [], taskRuns: [], agentRuns: [], workflowRuns: [workflowLink], assets: [], objects: [],
};
const pinned = { kind: 'KNOWLEDGE' as const, documentId: 'manual', versionId: 'version-old', checksum: oldChecksum };
const document = {
  documentId: 'manual', currentVersion: 'version-new', title: '监测指南', summary: '',
  createdAt, bindingCount: 0, hasDataBinding: false, kind: 'method', language: 'zh-CN', sourceCount: 1, status: 'ACTIVE',
};
const knowledgeVersion = {
  versionId: 'version-old', documentId: 'manual', checksum: oldChecksum,
  body: {
    title: '历史监测指南', contentVersion: '1.0.0', knowledgeId: 'manual', kind: 'method', language: 'zh-CN',
    sources: [{
      sourceId: 'source-1', kind: 'OFFICIAL_GUIDANCE', title: '监测工作规程', publisher: '监测部门', reviewAfter: '2090-01-01',
    }],
    units: [{ unitId: 'unit-1', content: '旧版原文。', sourceRefs: ['source-1'] }], summary: '旧版适用范围',
  },
  createdAt, importId: 'import-1', lifecycleActor: {}, lifecycleRevision: 1, reviewAfter: '2090-01-01',
  status: 'ACTIVE', validThrough: null, verifiedAt: null,
};
const output = {
  kind: 'data_version', service: 'data-primary', datasetId: 'dataset-1', versionId: 'data-version-1',
  semanticId: 'semantic-1', objectId: 'object-1', mediaType: 'text/csv', format: 'csv',
  sizeBytes: 234, checksum, outputName: 'checked_output',
};
const withheld = { ...output, outputName: 'withheld_output', versionId: 'hidden-version' };
const run = {
  runId: 'run-1', agentRunId: 'agent-run-1', toolCallId: 'call-1', position: 0, projectId: 'project',
  modelId: 'groundwater-model', modelVersion: '2.0.0', modelChecksum: checksum,
  workflowId: 'workflow-1', workflowVersion: '1.0.0', workflowChecksum: checksum,
  executionService: 'executor-service', executorId: 'executor', arguments: {},
  status: 'SUCCEEDED', remoteJobId: 'remote-1', outputs: [output, withheld], error: null,
  createdAt, startedAt: createdAt, finishedAt: createdAt, durationMs: 1000,
};
const publication = { workflowRunId: 'run-1', publicationId: 'publication-1', state: 'PUBLISHED' };
const dataVersion = {
  project: 'project', datasetId: 'dataset-1', versionId: 'data-version-1', currentVersion: 'data-version-2',
  publicationId: 'publication-1', semanticId: 'semantic-1', createdAt, descriptor: {}, inputVersions: [], tags: [],
  title: '核对后的成果', semantics: {},
  objects: [{ role: 'raw', objectId: 'object-1', mediaType: 'text/csv', format: 'csv', checksum, size: 234 }],
};

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
});

function routeFetch(routes: Record<string, (url: URL) => Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, params) => {
    const url = new URL(String(input), 'http://localhost/');
    if (params?.method === 'GET' && routes[url.pathname]) return routes[url.pathname](url);
    return json({}, 404);
  });
}

it('searches and pages the Knowledge catalog and links a verified historical version', async () => {
  routeFetch({
    '/api/v12/knowledge/documents': url => {
      const query = url.searchParams.get('query');
      const cursor = url.searchParams.get('cursor');
      return query === '水位' && cursor === 'catalog-page-2' ? json({ items: [document], nextCursor: null, totalMatches: 2 })
        : query === '水位' ? json({
          items: [{ ...document, documentId: 'intro', title: '水位简介' }], nextCursor: 'catalog-page-2', totalMatches: 2,
        }) : json({ items: [], nextCursor: null, totalMatches: 0 });
    },
    '/api/v12/knowledge/documents/manual/versions': url => url.searchParams.get('cursor') === 'history-page-2'
      ? json({ items: [{ versionId: 'version-old', createdAt, checksum: oldChecksum }], nextCursor: null })
      : json({ items: [{ versionId: 'version-new', createdAt, checksum }], nextCursor: 'history-page-2' }),
    '/api/v12/knowledge/versions/version-old': () => json(knowledgeVersion),
  });
  const onSelect = vi.fn();
  render(<WorkItemOtherAssets mode="knowledge" detail={detail} project="project" disabled={false} onClose={vi.fn()} onSelect={onSelect} />);

  fireEvent.change(screen.getByRole('searchbox', { name: '搜索知识目录' }), { target: { value: ' 水位 ' } });
  fireEvent.click(screen.getByRole('button', { name: '搜索' }));
  expect(await screen.findByText('水位简介')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '加载更多知识' }));
  fireEvent.click(await screen.findByRole('button', { name: /监测指南/ }));
  expect(screen.getByRole('button', { name: '返回知识目录' })).toHaveFocus();
  fireEvent.click(await screen.findByRole('button', { name: '加载更多版本' }));
  fireEvent.click(await screen.findByRole('button', { name: /version-old/ }));
  expect(screen.getByRole('button', { name: '返回版本记录' })).toHaveFocus();
  expect(await screen.findByText('历史监测指南')).toBeVisible();
  expect(screen.getByText(oldChecksum)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '关联此知识版本' }));
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(pinned);
});

it('reads the pinned Knowledge identity directly and keeps inspection read-only', async () => {
  const fetch = routeFetch({
    '/api/v12/knowledge/versions/version-old': () => json(knowledgeVersion),
  });
  const onSelect = vi.fn();
  render(<WorkItemOtherAssets mode="knowledge" detail={detail} project="project" pinnedKnowledge={pinned}
    disabled={false} onClose={vi.fn()} onSelect={onSelect} />);
  expect(await screen.findByText('历史监测指南')).toBeVisible();
  expect(screen.getByText(oldChecksum)).toBeVisible();
  expect(screen.queryByRole('button', { name: '关联此知识版本' })).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([
  ['checksum changed', { ...knowledgeVersion, checksum }, '知识版本暂不可用'],
  ['document changed', { ...knowledgeVersion, documentId: 'other-document' }, '知识版本暂不可用'],
  ['version changed', { ...knowledgeVersion, versionId: 'other-version' }, '知识版本暂不可用'],
  ['project differs', { ...knowledgeVersion, body: { ...knowledgeVersion.body, bindings: [{
    projectId: 'other-project', datasetId: 'private-dataset', versionId: 'data-version', semanticId: 'data-semantic',
  }] } }, '知识版本暂不可用'],
  ['withdrawn', { ...knowledgeVersion, status: 'WITHDRAWN' }, '知识版本已撤回'],
  ['expired', { ...knowledgeVersion, validThrough: '2020-01-01' }, '知识版本已过期'],
])('shows a safe pinned Knowledge state when %s', async (_case, response, state) => {
  routeFetch({ '/api/v12/knowledge/versions/version-old': () => json(response) });
  render(<WorkItemOtherAssets mode="knowledge" detail={detail} project="project" pinnedKnowledge={pinned}
    disabled={false} onClose={vi.fn()} />);
  expect(await screen.findByText(new RegExp(state))).toBeVisible();
  expect(screen.queryByText('历史监测指南')).toBeNull();
  expect(screen.queryByText('旧版适用范围')).toBeNull();
  expect(screen.queryByText('监测工作规程')).toBeNull();
  expect(screen.queryByText('旧版原文。')).toBeNull();
});

it('hides a revoked Knowledge version and retries its exact identity', async () => {
  let readable = false;
  routeFetch({ '/api/v12/knowledge/versions/version-old': () => readable ? json(knowledgeVersion) : json({
    detail: 'private response data',
  }, 403) });
  render(<WorkItemOtherAssets mode="knowledge" detail={detail} project="project" pinnedKnowledge={pinned}
    disabled={false} onClose={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('知识版本暂不可用');
  expect(screen.queryByText('private response data')).toBeNull();
  readable = true;
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('历史监测指南')).toBeVisible();
  readable = false;
  fireEvent.focus(window);
  expect(await screen.findByRole('alert')).toHaveTextContent('知识版本暂不可用');
  expect(screen.queryByText('历史监测指南')).not.toBeInTheDocument();
});

it('offers the linked run and only a published output whose Data authority confirms access and identity', async () => {
  routeFetch({
    '/api/v12/workflow-runs/run-1': url => url.searchParams.get('projectId') === 'project' ? json(run) : json({}, 403),
    '/api/v12/workflow-runs/run-1/publication': url => url.searchParams.get('projectId') === 'project' ? json(publication) : json({}, 403),
    '/api/v12/data/versions/data-version-1': () => json({ ...dataVersion, semanticId: 'semantic-2' }),
    '/api/v12/data/versions/data-version-1/semantics/semantic-1': () => json({
      versionId: 'data-version-1', semanticId: 'semantic-1', createdAt, actor: {}, body: {},
    }),
    '/api/v12/data/versions/hidden-version': () => json({}, 404),
  });
  const onSelect = vi.fn();
  render(<WorkItemOtherAssets mode="run" detail={detail} project="project" disabled={false} onClose={vi.fn()} onSelect={onSelect} />);

  fireEvent.click(screen.getByRole('button', { name: /查看.*run-1/ }));
  fireEvent.click(await screen.findByRole('button', { name: '关联此计算记录' }));
  expect(onSelect).toHaveBeenNthCalledWith(1, { kind: 'WORKFLOW', runId: 'run-1' });
  const result = await screen.findByRole('button', { name: /关联成果.*checked output/ });
  expect(screen.queryByText(/withheld output|hidden-version/)).toBeNull();
  fireEvent.click(result);
  expect(onSelect).toHaveBeenNthCalledWith(2, { kind: 'RESULT', runId: 'run-1', outputName: 'checked_output' });
});

it('does not expose or select outputs before the authoritative publication completes', async () => {
  const fetch = routeFetch({
    '/api/v12/workflow-runs/run-1': () => json(run),
    '/api/v12/workflow-runs/run-1/publication': () => json({ ...publication, state: 'READY' }),
  });
  render(<WorkItemOtherAssets mode="run" detail={detail} project="project" disabled={false} onClose={vi.fn()} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /查看.*run-1/ }));
  expect(await screen.findByText(/成果发布状态待核验|成果尚未发布/)).toBeVisible();
  expect(screen.queryByRole('button', { name: /关联成果/ })).toBeNull();
  expect(fetch.mock.calls.some(([url]) => String(url).includes('/data/versions/'))).toBe(false);
});

it.each([
  ['from another project', { ...dataVersion, project: 'private-project' }],
  ['from another publication', { ...dataVersion, publicationId: 'wrong-publication' }],
  ['with a different raw checksum', { ...dataVersion, objects: [{ ...dataVersion.objects[0], checksum: oldChecksum }] }],
])('hides a workflow output when Data returns a version %s', async (_case, version) => {
  routeFetch({
    '/api/v12/workflow-runs/run-1': () => json({ ...run, outputs: [output] }),
    '/api/v12/workflow-runs/run-1/publication': () => json(publication),
    '/api/v12/data/versions/data-version-1': () => json(version),
  });
  render(<WorkItemOtherAssets mode="run" detail={detail} project="project" disabled={false} onClose={vi.fn()} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /查看.*run-1/ }));
  expect(await screen.findByText(/已发布成果暂不可用/)).toBeVisible();
  expect(screen.queryByText('checked output')).toBeNull();
  expect(screen.queryByRole('button', { name: /关联成果/ })).toBeNull();
});

it('does not expose unverified run metadata from another project', async () => {
  routeFetch({ '/api/v12/workflow-runs/run-1': () => json({ ...run, projectId: 'elsewhere', modelId: 'private-model' }) });
  render(<WorkItemOtherAssets mode="run" detail={detail} project="project" disabled={false} onClose={vi.fn()} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /查看.*run-1/ }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('计算记录暂不可用'));
  expect(screen.queryByText('private-model')).toBeNull();
  expect(screen.queryByRole('button', { name: '关联此计算记录' })).toBeNull();
});
