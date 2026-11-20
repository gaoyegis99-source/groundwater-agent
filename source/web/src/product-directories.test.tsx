import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { DatabaseWorkbench } from './DatabaseWorkbench';
import { KnowledgeDrawer } from './Drawers';
import { CapabilityDrawer } from './ProductDrawers';
import type { BusinessCapability } from './product-ui';
import { capabilitiesFixture, dataDetail, instant } from './test-fixtures';

const checksum = `sha256:${'a'.repeat(64)}`;
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });

it('opens database results within the page and keeps the global navigation available', async () => {
  const dataset = { datasetId: 'dataset-1', project: 'demo', title: '井水位观测', currentVersion: 'version-1',
    tags: [], createdAt: instant, formats: ['json'], semantics: {} };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/data/datasets')) return json({ items: [dataset], nextCursor: null, matchBasis: 'catalog' });
    if (url.pathname.endsWith('/data/datasets/dataset-1/versions')) return json({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/data/versions/version-1')) return json(dataDetail({ title: dataset.title }));
    if (url.pathname.endsWith('/content')) return json({ observations: [] });
    throw new Error(`Unexpected endpoint ${url}`);
  }));
  const user = userEvent.setup();
  const close = vi.fn();
  render(<div className="app-shell"><a href="#/work">工作项</a><main><h1>数据库</h1>
    <DatabaseWorkbench embedded project="demo" onClose={close} />
  </main></div>);

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '关闭数据库' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '数据目录', level: 2 })).toBeVisible();
  screen.getByRole('link', { name: '工作项' }).focus();
  await user.tab();
  expect(screen.getByRole('searchbox', { name: '搜索' })).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(close).not.toHaveBeenCalled();

  const selected = await screen.findByRole('button', { name: /井水位观测/ });
  await user.click(selected);
  const detail = screen.getByRole('region', { name: '数据详情' });
  expect(detail).toHaveFocus();
  expect(await within(detail).findByRole('heading', { name: '井水位观测', level: 2 })).toBeVisible();
  await user.click(within(detail).getByRole('button', { name: '返回数据目录' }));
  expect(selected).toHaveFocus();
});

it('returns from an embedded knowledge reading to its directory entry in normal tab order', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/knowledge/documents')) return json({
      items: [{ documentId: 'method', currentVersion: 'knowledge-1', title: '观测方法', createdAt: instant }], nextCursor: null,
    });
    if (url.pathname.endsWith('/knowledge/lookup')) return json({
      schemaVersion: 'evidence-bundle.v1', status: 'OK', receiptId: 'receipt-1',
      knowledgeRevision: 1, indexGeneration: 'generation-1', observedAt: instant,
      items: [{ status: 'OK', versionId: 'knowledge-1', unitId: 'unit-1', checksum, content: '按井记录观测日期。' }],
      coverage: {}, budget: {}, timingsMs: {},
    });
    throw new Error(`Unexpected endpoint ${url}`);
  }));
  const user = userEvent.setup();
  render(<main><h1>知识与方法</h1>
    <KnowledgeDrawer embedded project="demo" onClose={vi.fn()} />
    <a href="#/capabilities">能力目录</a>
  </main>);

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '关闭知识库' })).not.toBeInTheDocument();
  const selected = await screen.findByRole('button', { name: /观测方法/ });
  await user.click(selected);
  expect(await screen.findByText('按井记录观测日期。')).toBeVisible();
  expect(screen.getByRole('heading', { name: '证据原文', level: 2 })).toBeVisible();
  expect(screen.getByRole('article')).toHaveFocus();
  await user.click(screen.getByRole('button', { name: '返回知识目录' }));
  expect(selected).toHaveFocus();
  await user.tab();
  expect(screen.getByRole('link', { name: '能力目录' })).toHaveFocus();
});

it('filters the embedded capability directory and exposes business scenario details', async () => {
  const scenario: BusinessCapability = {
    ref: { id: 'groundwater.monitoring', version: '1.0.0', checksum }, title: '按井复核观测',
    description: '按井核对可用观测资料。', scenario: '水位观测复核', tier: 'CORE', defaultVisible: true,
    roles: ['researcher'], prompt: '复核观测数据。', requiredServices: ['data'],
    validationStatus: 'TECHNICALLY_VALIDATED', profiles: [],
  };
  const user = userEvent.setup();
  const openScenario = vi.fn();
  render(<main><h1>能力目录</h1><CapabilityDrawer embedded capabilities={capabilitiesFixture({ models: [] })}
    businessCapabilities={[scenario]} examples={[]} onBusinessCapability={openScenario} onExample={vi.fn()} onClose={vi.fn()}
  /></main>);

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '关闭能力目录' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '业务场景', level: 2 })).toBeVisible();
  await user.type(screen.getByRole('searchbox', { name: '搜索工作流' }), '不存在的工作流');
  expect(screen.queryByRole('button', { name: scenario.title })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '清除关键词' }));
  await user.click(screen.getByRole('button', { name: scenario.title }));
  expect(openScenario).toHaveBeenCalledWith(scenario.ref.id, scenario.ref.version);
});
