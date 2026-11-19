import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ConfirmationCard, ResultCard } from './ChatCards';
import { productUi } from './product-ui';
import { dataDetail, dataRef, historyFixture, instant, taskFixture } from './test-fixtures';
import type { Confirmation, Result, WorkflowDraftPayload } from './types';

const approval: Confirmation = {
  confirmationId: 'approval-review', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
  reason: '请核对已选择的分析内容', decision: null, decisionState: null,
  actions: [
    { toolCallId: 'flow', tool: 'run_flow_field', modelId: 'groundwater.flow-field', modelVersion: '2.0.0', workflowId: 'flow', workflowVersion: '1.1.0',
      arguments: { versionId: 'exact-input-version-3', valueField: 'computed_head', referenceElevationUnit: 'cm', depthToWaterUnit: 'mm', sourceCrs: 'EPSG:4326', targetCrs: 'EPSG:32650', pixelSize: 37.5, hydraulicConductivityMetersPerDay: 2.75, effectivePorosity: 0.23 } },
    { toolCallId: 'trend', tool: 'run_monitoring_trend', modelId: 'groundwater.monitoring-trend', modelVersion: '2.0.0', workflowId: 'trend', workflowVersion: '1.0.0',
      arguments: { valueField: 'depth', expectedStart: '2026-01-01', expectedEnd: '2026-12-31', alpha: 0.025 } },
  ],
};

it('shows both frozen spatial join ports and their exact versions before confirmation', () => {
  const target = dataRef({ versionId: 'exact-target-version' });
  const join = dataRef({ versionId: 'exact-join-version' });
  const confirmation: Confirmation = {
    ...approval, actions: [{ ...approval.actions[0], tool: 'run_composed_workflow', workflowChecksum: 'frozen-digest',
      arguments: {}, inputReferences: { geom: target, attr: join } }],
  };
  const draft = {
    compositionCheck: { status: 'VALIDATED', definitionDigest: 'frozen-digest' },
    workflow: {
      nodes: [{ nodeId: 'spatial', inputs: { target: { workflowInput: 'geom' }, join: { workflowInput: 'attr' } } }],
      outputs: [],
    },
  } as unknown as WorkflowDraftPayload;
  const onResource = vi.fn();
  const history = historyFixture({ turns: [{ status: 'WAITING_CONFIRMATION', confirmations: [confirmation] }] });
  const properties = { confirmation, history, busy: false, onDecision: vi.fn(), onResource, project: 'demo', current: true };
  const { rerender } = render(<ConfirmationCard {...properties} />);
  expect(screen.getByTestId('confirm-execution')).toBeDisabled();
  rerender(<ConfirmationCard {...properties} draft={draft} />);
  expect(screen.getByText('spatial · target')).toBeVisible();
  expect(screen.getByText('spatial · join')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '查看target绑定的数据版本 exact-target-version' }));
  expect(onResource).toHaveBeenCalledWith(target.versionId, target);
  expect(screen.getByRole('button', { name: '查看join绑定的数据版本 exact-join-version' })).toBeVisible();
  expect(screen.getByTestId('confirm-execution')).toBeEnabled();
});

it('keeps every frozen scientific value visible across a batch and opens the exact input identity without approving', async () => {
  const onResource = vi.fn(), onDecision = vi.fn();
  const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(dataDetail({ versionId: 'exact-input-version-3', title: '井水位原始资料.csv' })), { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetch);
  const history = historyFixture({ turns: [{ status: 'WAITING_CONFIRMATION', confirmations: [approval] }] });
  render(<ConfirmationCard confirmation={approval} history={history} busy={false} onDecision={onDecision} onResource={onResource} project="demo" />);
  for (const value of ['computed_head', 'cm', 'mm', 'EPSG:4326', 'EPSG:32650', '37.5', '2.75', '0.23', 'depth', '2026-01-01', '2026-12-31', '0.025']) {
    expect(screen.getByText(value, { exact: true })).toBeVisible();
  }
  expect(screen.getByText('新生成水头字段')).toBeVisible();
  expect(screen.getByText('取值字段')).toBeVisible();
  expect(screen.getByText('渗透系数（米/天）')).toBeVisible();
  fireEvent.click(await screen.findByRole('button', { name: '井水位原始资料.csv' }));
  expect(fetch.mock.calls[0][0]).toBe('/api/v12/data/versions/exact-input-version-3');
  expect(onResource).toHaveBeenCalledExactlyOnceWith('exact-input-version-3');
  expect(onDecision).not.toHaveBeenCalled();
  expect(screen.queryByText(text => text.includes('"resourceId": "exact-input-version-3"'))).not.toBeInTheDocument();
  expect(screen.queryByText('模型身份与原始参数')).not.toBeInTheDocument();
  expect(screen.queryByText(approval.reason)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /^确认执行/ }));
  expect(onDecision).toHaveBeenCalledExactlyOnceWith('approval-review', 'confirm');
});

it('does not request resource names for collapsed historical confirmations and reads only the exact resource when opened', async () => {
  const fetch = vi.fn(async (_url: string) => new Response(JSON.stringify(dataDetail({ versionId: 'exact-input-version-3', title: '历史输入.csv' })), { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetch);
  render(<ConfirmationCard confirmation={{ ...approval, status: 'CONFIRMED' }} history={null} busy={false} onDecision={vi.fn()} onResource={vi.fn()} project="demo" />);
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByText('新生成水头字段')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('查看本批次 2 项分析的资料与参数'));
  expect(await screen.findByRole('button', { name: '历史输入.csv' })).toBeVisible();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/v12/data/versions/exact-input-version-3']);
  expect(screen.queryByRole('button', { name: /^确认执行/ })).not.toBeInTheDocument();
});

it.each([
  { quantity: 'hydraulic_head', meaning: '观测水头高程', dictionary: '观测水头数据字典', recorded: true },
  { quantity: 'depth_to_water', meaning: '地下水埋深', dictionary: '埋深观测数据字典', recorded: false },
])('distinguishes same-title metre observations by $quantity and verifies dictionary identity', async ({ quantity, meaning, dictionary, recorded }) => {
  const input = dataRef({ versionId: `frozen-version-${quantity}`, semanticId: 'frozen-semantics' });
  const args = {
    groupField: 'well', timeField: 'observed_at', valueField: 'level', frequency: 'month', aggregation: 'mean',
    expectedStart: '2025-01-01', expectedEnd: '2025-12-01', baselineStart: '2025-01-01', baselineEnd: '2025-06-01',
    comparisonStart: '2025-07-01', comparisonEnd: '2025-12-01', seasonalTrendEnabled: false, seasonFrequency: 'month',
  };
  const action = { ...approval.actions[1], arguments: { versionId: input.versionId, ...args }, inputReferences: { versionId: input } };
  const confirmation = { ...approval, actions: [action], ...(recorded ? {} : { status: 'CONFIRMED' }) };
  const exact = { versionId: 'dictionary-version', unitId: 'level-entry', checksum: `sha256:${'b'.repeat(64)}` };
  const sibling = { ...exact, unitId: 'other-entry' };
  const binding = { serviceBinding: 'data-primary', projectId: 'demo', datasetId: input.datasetId,
    versionId: input.versionId, semanticId: input.semanticId, fieldId: 'f2' };
  const history = historyFixture({ turns: [{ status: 'WAITING_CONFIRMATION', confirmations: [confirmation] }],
    preparation: recorded ? {
      schemaVersion: 'task-preparation.v1', preparationId: 'preparation-1', revision: 1, sessionId: 'session-1', agentRunId: 'agent-1',
      goalRevision: 1, taskId: null, baseTaskRevision: null, status: 'READY', freshForCurrentTurn: true, disposition: 'WAITING_CONFIRMATION',
      entries: [{ targetKind: 'EXECUTABLE_MODEL', targetId: action.modelId, targetVersion: action.modelVersion, status: 'READY',
        selectionPolicy: 'PINNED', inputs: { versionId: input }, parameters: args, parameterSources: {},
        checks: [], blockingReasons: [], knowledgeRefs: [exact, sibling] }],
    } : null,
  });
  const detail = dataDetail({ versionId: input.versionId, title: '合成验收观测表', semanticId: 'newer-semantics',
    descriptor: { fields: [{ fieldId: 'f2', name: 'level', type: 'float64' }] },
    semantics: { fields: { f2: { meaning: '资料当前修订的含义', unit: 'm' } } },
  });
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith(`/data/versions/${input.versionId}`)) return json(detail);
    if (url.endsWith(`/semantics/${input.semanticId}`)) return json({ versionId: input.versionId, semanticId: input.semanticId,
      actor: {}, createdAt: instant, body: { fields: { f2: { quantityKind: quantity, meaning, unit: 'm' } } },
    });
    expect(url).toBe(recorded ? '/api/v12/knowledge/evidence/read' : '/api/v12/knowledge/lookup');
    expect(JSON.parse(String(init?.body))).toMatchObject(recorded ? { refs: [exact, sibling] } : { binding });
    const item = { ...exact, status: 'OK', content: meaning, scope: 'DATA_BOUND', bindingMatch: recorded ? 'NOT_CHECKED' : 'EXACT' };
    return json({ schemaVersion: 'evidence-bundle.v1', status: 'OK', receiptId: 'receipt-1', knowledgeRevision: 1,
      indexGeneration: 'generation-1', observedAt: instant, coverage: {}, budget: {}, timingsMs: {},
      items: [{ ...item, bindings: [binding], documentTitle: dictionary },
        { ...item, ...sibling, bindings: [{ ...binding, fieldId: 'f3' }], documentTitle: '同名表的其他字段依据' }],
    });
  });
  vi.stubGlobal('fetch', fetch);
  const onKnowledge = vi.fn(), onResource = vi.fn();
  render(<ConfirmationCard current={recorded} confirmation={confirmation} history={history} busy={false}
    onDecision={vi.fn()} onResource={onResource} onKnowledge={onKnowledge} project="demo" />);
  const title = await screen.findByRole('button', { name: '合成验收观测表' });
  expect(title).toHaveAccessibleDescription(`level · ${meaning}（米）`);
  expect(screen.queryByText('资料当前修订的含义')).not.toBeInTheDocument();
  fireEvent.click(title);
  expect(onResource).toHaveBeenCalledExactlyOnceWith(input.versionId, input);
  fireEvent.click(await screen.findByRole('button', { name: recorded ? `字段字典 · ${dictionary}` : dictionary }));
  expect(onKnowledge).toHaveBeenCalledExactlyOnceWith(exact);
  expect(screen.getByText(recorded ? '字段字典' : '绑定字段的字典')).toBeVisible();
  expect(screen.queryByText('同名表的其他字段依据')).not.toBeInTheDocument();
  for (const item of ['统计周期 month · 聚合方式 mean', '2025-01-01 至 2025-12-01', '2025-01-01 至 2025-06-01', '2025-07-01 至 2025-12-01']) {
    expect(screen.getByText(item, { exact: true })).toBeVisible();
  }
  expect(screen.queryByText('季节周期')).not.toBeInTheDocument();
  expect(screen.queryByText(input.versionId)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText(recorded ? '核对本批次资料与参数' : '查看本批次 1 项分析的资料与参数'));
  const identity = await screen.findByText('模型与资料标识');
  expect(screen.getByText(input.versionId)).not.toBeVisible();
  fireEvent.click(identity);
  expect(screen.getByText(input.versionId)).toBeVisible();
  expect(screen.getByText(input.checksum)).toBeVisible();
});

it('explains a rejected verification batch and keeps a large transfer plan summarized and grouped', async () => {
  const arguments_ = {
    workbookVersionId: 'workbook-version', groundwater_systemsVersionId: 'systems-version',
    'pretrain.epochs': 80, 'pretrain.hiddenSize': 64, 'finetune.epochs': 100,
    'map-month.targetTime': '2024-12-01',
    ...Object.fromEntries(Array.from({ length: 25 }, (_, index) => [`boundary-projected.option${index}`, index])),
  };
  const confirmation: Confirmation = {
    ...approval, status: 'REJECTED', reason: '别名命中验证完成，不重复执行',
    actions: [{ ...approval.actions[0], tool: 'run_transfer_learning_analysis', modelId: 'groundwater.transfer-learning-analysis', arguments: arguments_,
      inputReferences: Object.fromEntries(Array.from({ length: 19 }, (_, index) => [`input${index}`, dataRef({ versionId: `version-${index}` })])) }],
  };
  const history = historyFixture({ turns: [{ status: 'COMPLETE', confirmations: [confirmation], progressUpdates: [{
    progressId: 'case', agentRunId: 'agent-1', workflowRunId: null, code: 'CASE_MATCHED', status: 'COMPLETE', scope: 'TURN', title: '匹配分析案例', summary: '已匹配固定案例。',
    facts: { caseId: 'yaozhanbao-transfer-case' }, createdAt: null, startedAt: null, finishedAt: null,
  }, {
    progressId: 'inputs', agentRunId: 'agent-1', workflowRunId: null, code: 'INPUTS_VERIFIED', status: 'COMPLETE', scope: 'TURN', title: '核验案例输入', summary: '已核验案例输入。',
    facts: { verifiedInputs: 19, profile: 'standard' }, createdAt: null, startedAt: null, finishedAt: null,
  }] }] });
  render(<ConfirmationCard confirmation={confirmation} history={history} busy={false} onDecision={vi.fn()} onResource={vi.fn()} project="demo" />);

  expect(screen.getByRole('heading', { name: '本批次已拒绝' })).toBeVisible();
  expect(screen.getByText('验证会话 · 不代表业务执行')).toBeVisible();
  expect(screen.getAllByText('标准训练档')).toHaveLength(2);
  expect(screen.getByText('腰站堡迁移学习')).toBeVisible();
  expect(screen.getByText('预训练 · 训练轮数 80 · 微调 · 训练轮数 100 · 预训练 · 隐藏层大小 64')).toBeVisible();
  expect(screen.getByText('2024-12-01')).toBeVisible();
  expect(screen.getByText('本批次已拒绝，未创建工作流，也未生成分析结果。')).toBeVisible();
  expect(screen.getByText('别名命中验证完成，不重复执行')).toBeVisible();
  expect(screen.queryByText('请核对以下资料与参数；只有确认后才会执行。')).not.toBeInTheDocument();
  expect(screen.getByText('19 项')).toBeVisible();
  expect(screen.getByText('31 项')).toBeVisible();
  expect(screen.queryByText('数据工作簿')).not.toBeInTheDocument();

  fireEvent.click(screen.getByText('查看本批次 1 项分析的资料与参数'));
  expect(await screen.findByRole('heading', { name: '1. 迁移学习回测' })).toBeVisible();
  fireEvent.click(screen.getByText('输入资料', { selector: 'summary' }));
  expect(screen.getByText('数据工作簿')).toBeVisible();
  expect(screen.getByText('地下水系统资料')).toBeVisible();
  fireEvent.click(screen.getByText('训练与评价'));
  expect(screen.getByText('预训练 · 训练轮数')).toBeVisible();
  expect(screen.getByText('微调 · 训练轮数')).toBeVisible();
  expect(screen.queryByText('groundwater_systemsVersionId')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^确认执行/ })).not.toBeInTheDocument();
});

it('keeps an unavailable input name explicit without guessing its name or version', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'RESOURCE_NOT_FOUND' }), { status: 404, headers: { 'Content-Type': 'application/json' } })));
  const history = historyFixture({ turns: [{ status: 'WAITING_CONFIRMATION', confirmations: [approval] }] });
  const onResource = vi.fn();
  render(<ConfirmationCard confirmation={approval} history={history} busy={false} onDecision={vi.fn()} onResource={onResource} project="demo" />);
  fireEvent.click(await screen.findByRole('button', { name: '数据名称暂不可用 · 打开详情' }));
  expect(onResource).toHaveBeenCalledExactlyOnceWith('exact-input-version-3');
  expect(screen.getByRole('alert')).toBeVisible();
  expect(screen.getByText(/RESOURCE_NOT_FOUND/)).not.toBeVisible();
  expect(screen.queryByText(/v3/)).not.toBeInTheDocument();
});

it('keeps unfamiliar and structured parameter values inspectable instead of omitting them', () => {
  const confirmation = { ...approval, actions: [{ ...approval.actions[0], arguments: { customRule: { lower: 0, upper: 0.75, allowed: ['保留', '待核实'], enabled: false }, empty: '', absent: null } }] };
  const history = historyFixture({ turns: [{ status: 'WAITING_CONFIRMATION', confirmations: [confirmation] }] });
  render(<ConfirmationCard confirmation={confirmation} history={history} busy={false} onDecision={vi.fn()} onResource={vi.fn()} project="demo" />);
  for (const value of ['customRule', 'lower', '0', 'upper', '0.75', '保留', '待核实', '否', '空字符串', '未设置']) {
    expect(screen.getByText(value, { exact: true })).toBeVisible();
  }
});

it('shows every parameter in a large product-defined custom group', async () => {
  const names = Array.from({ length: 25 }, (_, index) => `customParameter${index}`);
  const profile = productUi();
  for (const name of names) profile.parameterNames[name] = { label: `参数 ${name}`, group: '产品自定义组' };
  try {
    const confirmation = { ...approval, status: 'CONFIRMED', actions: [{ ...approval.actions[0], arguments: Object.fromEntries(names.map((name, index) => [name, `value-${index}`])) }] };
    render(<ConfirmationCard confirmation={confirmation} history={null} busy={false} onDecision={vi.fn()} onResource={vi.fn()} project="demo" />);
    fireEvent.click(screen.getByText('查看本批次 1 项分析的资料与参数'));
    fireEvent.click(await screen.findByText('产品自定义组'));
    for (let index = 0; index < names.length; index += 1) expect(screen.getByText(`value-${index}`)).toBeVisible();
  } finally {
    for (const name of names) delete profile.parameterNames[name];
  }
});

it('presents released result names in Chinese while retaining the real download and version without raw identity', () => {
  const onResource = vi.fn();
  const task = taskFixture({ modelId: 'groundwater.flow-field', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'SUCCEEDED', workflowRunId: task.workflowRunId,
    remoteOutputs: [dataRef({ versionId: 'released-head', objectId: 'object-head', semanticId: 'head-semantics', outputName: 'head-raster', format: 'geotiff', mediaType: 'image/tiff' })] };
  render(<ResultCard task={task} result={result} project="demo" onResource={onResource} />);
  expect(screen.getByText('地下水流场')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /水头栅格/ }));
  expect(onResource).toHaveBeenCalledExactlyOnceWith(result.remoteOutputs[0]);
  expect(screen.getByText('geotiff', { exact: true })).toBeVisible();
  expect(screen.getByText('released-head', { exact: true })).not.toBeVisible();
  expect(screen.getByText('head-semantics')).not.toBeVisible();
  expect(screen.getByRole('link', { name: '下载 插值水头栅格' })).toHaveAttribute('href', '/api/v12/data/versions/released-head/objects/object-head/content');
  expect(screen.getByRole('link', { name: '下载 插值水头栅格' })).toHaveAttribute('download', 'head-raster.tif');
  expect(screen.queryByText(text => text.includes('"checksum": "sha256:exact-output"'))).not.toBeInTheDocument();
  expect(screen.queryByText('模型与结果身份')).not.toBeInTheDocument();
});

it('dispatches each result reuse action with the exact immutable reference', () => {
  const task = taskFixture({ modelId: 'groundwater.summary', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const output = dataRef({ versionId: 'reuse-version', objectId: 'reuse-object', outputName: 'summary' });
  const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'SUCCEEDED', workflowRunId: task.workflowRunId, remoteOutputs: [output] };
  const onAttach = vi.fn(), onContinue = vi.fn(), onCompare = vi.fn();
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} onAttach={onAttach} onContinue={onContinue} onCompare={onCompare} />);
  fireEvent.click(screen.getByRole('button', { name: '附加到当前草稿' }));
  fireEvent.click(screen.getByRole('button', { name: '带成果打开新会话' }));
  fireEvent.click(screen.getByRole('button', { name: '与另一版本比较' }));
  expect(onAttach).toHaveBeenCalledExactlyOnceWith(output);
  expect(onContinue).toHaveBeenCalledExactlyOnceWith(output);
  expect(onCompare).toHaveBeenCalledExactlyOnceWith(output);
});

it('keeps long result identities inspectable behind the business name and file format', () => {
  const output = dataRef({ versionId: 'cfd5fab9-652d-4a39-a8f9-b11c630a0623', semanticId: 'semantics-2026-11-11-long-identity', outputName: 'summary' });
  const task = taskFixture({ modelId: 'groundwater.summary', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'SUCCEEDED', workflowRunId: task.workflowRunId, remoteOutputs: [output] };
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} />);
  expect(screen.getByTestId('result-card')).toHaveAttribute('data-status', 'SUCCEEDED');
  expect(screen.getByText('json', { exact: true })).toBeVisible();
  expect(screen.queryByText(/版本 cfd5fab9…/)).not.toBeInTheDocument();
  expect(screen.getByText(output.versionId, { exact: true })).not.toBeVisible();
  fireEvent.click(screen.getByText('数据身份'));
  expect(screen.getByText(output.versionId, { exact: true })).toBeVisible();
  expect(screen.getByText(output.semanticId, { exact: true })).toBeVisible();
  expect(screen.getByText(output.checksum, { exact: true })).toBeVisible();
});

it('keeps a large result group compact with every published file reachable on expansion', () => {
  const task = taskFixture({ modelId: 'groundwater.summary', status: 'SUCCEEDED', stage: null, outputCount: 25 });
  const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'SUCCEEDED', workflowRunId: task.workflowRunId,
    remoteOutputs: Array.from({ length: 25 }, (_, index) => dataRef({ versionId: `published-${index + 1}`, objectId: `object-${index + 1}`, outputName: `结果 ${index + 1}` })) };
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} />);
  expect(screen.getAllByRole('link', { name: /^下载/ })).toHaveLength(3);
  fireEvent.click(screen.getByRole('button', { name: '查看全部 25 个结果' }));
  const downloads = screen.getAllByRole('link', { name: /^下载/ });
  expect(downloads).toHaveLength(25);
  expect(downloads[24]).toHaveAttribute('href', '/api/v12/data/versions/published-25/objects/object-25/content');
  fireEvent.click(screen.getByRole('button', { name: '收起结果列表' }));
  expect(screen.getAllByRole('link', { name: /^下载/ })).toHaveLength(3);
});

it('leaves the published metrics file accessible when a comparable numerical summary cannot be read', async () => {
  const task = taskFixture({ modelId: 'groundwater.transfer-learning-analysis', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const metrics = dataRef({ outputName: 'metrics' });
  const result: Result = { goalRevision: 1, agentRunId: task.agentRunId, toolCallId: task.toolCallId, modelId: task.modelId, status: 'SUCCEEDED',
    workflowRunId: task.workflowRunId, remoteOutputs: [metrics] };
  const fetchMetrics = vi.fn(async () => new Response(JSON.stringify({
    schemaVersion: 'regression-metrics.v1', rmse: 0.5, sample_count: 18, unit: 'm', baseline: { rmse: 0.3, sample_count: 12 },
  })));
  vi.stubGlobal('fetch', fetchMetrics);
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} />);
  expect(await screen.findByRole('link', { name: '下载 留出回测总体指标' })).toBeVisible();
  expect(fetchMetrics).toHaveBeenCalledOnce();
  expect(screen.queryByText('模型 RMSE')).not.toBeInTheDocument();
});

it('uses a business label instead of exposing an unfamiliar model identity', () => {
  const task = taskFixture({ modelId: 'internal.experimental-model', status: 'FAILED', stage: null, outputCount: 0 });
  render(<ResultCard task={task} project="demo" onResource={vi.fn()} />);
  expect(screen.getByText('分析任务')).toBeVisible();
  expect(screen.queryByText('internal.experimental-model')).not.toBeInTheDocument();
});

it.each([
  ['groundwater.flow-field', 'summary', { resultKind: 'MODEL_DERIVED_NOT_OBSERVED', validCellCount: 120, hydraulicConductivityMetersPerDay: 4, effectivePorosity: 0.25 }, '有效网格数', '120'],
  ['groundwater.kriging', 'kriging-summary', { schemaVersion: 'kriging-summary.v1', loo: { rmse: 0.3, evaluatedCount: 13, notEvaluatedCount: 1 }, valueUnit: 'm', variance: { unit: 'm^2' } }, '留一验证 RMSE', '0.3 m'],
  ['groundwater.drastic', 'drastic-summary', { schemaVersion: 'groundwater-drastic-summary.v1', classes: [{ code: 1, label: '中等', ratioOfClassificationValid: 0.75 }], counts: { classificationValidCellCount: 16, outputNoDataCellCount: 4 } }, '中等', '75 %'],
] as const)('shows the published figures and diagnostic indicators for %s', async (modelId, outputName, payload, label, value) => {
  const task = taskFixture({ modelId, status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const output = dataRef({ outputName });
  const result: Result = { goalRevision: 1, agentRunId: task.agentRunId, toolCallId: task.toolCallId, modelId, status: 'SUCCEEDED',
    workflowRunId: task.workflowRunId, remoteOutputs: [output] };
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload))));
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} />);
  expect(await screen.findByText(label, { exact: true })).toBeVisible();
  expect(screen.getByText(value, { exact: true })).toBeVisible();
});
