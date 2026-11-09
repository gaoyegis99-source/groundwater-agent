import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ConfirmationCard, ResultCard } from './ChatCards';
import { productUi } from './product-ui';
import { dataDetail, dataRef, historyFixture, taskFixture } from './test-fixtures';
import type { Confirmation, Result } from './types';

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
  fireEvent.click(await screen.findByRole('button', { name: /井水位原始资料.csv · exact-input-version-3/ }));
  expect(fetch.mock.calls[0][0]).toBe('/api/v12/data/versions/exact-input-version-3');
  expect(onResource).toHaveBeenCalledExactlyOnceWith('exact-input-version-3');
  expect(onDecision).not.toHaveBeenCalled();
  expect(screen.queryByText(text => text.includes('"resourceId": "exact-input-version-3"'))).not.toBeInTheDocument();
  expect(screen.queryByText('模型身份与原始参数')).not.toBeInTheDocument();
  expect(screen.queryByText(approval.reason)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
  expect(onDecision).toHaveBeenCalledExactlyOnceWith('approval-review', 'confirm');
});

it('does not request resource names for collapsed historical confirmations and reads only the exact resource when opened', async () => {
  const fetch = vi.fn(async (_url: string) => new Response(JSON.stringify(dataDetail({ versionId: 'exact-input-version-3', title: '历史输入.csv' })), { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetch);
  render(<ConfirmationCard confirmation={{ ...approval, status: 'CONFIRMED' }} history={null} busy={false} onDecision={vi.fn()} onResource={vi.fn()} project="demo" />);
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByText('新生成水头字段')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('查看本批次 2 项分析的资料与参数'));
  expect(await screen.findByRole('button', { name: /历史输入.csv · exact-input-version-3/ })).toBeVisible();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/v12/data/versions/exact-input-version-3']);
  expect(screen.queryByRole('button', { name: '确认执行' })).not.toBeInTheDocument();
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
  expect(screen.queryByRole('button', { name: '确认执行' })).not.toBeInTheDocument();
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
  expect(onResource).toHaveBeenCalledExactlyOnceWith('released-head');
  expect(screen.getByText('geotiff · head-semantics')).toBeVisible();
  expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('href', '/api/v12/data/versions/released-head/objects/object-head/content');
  expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('download', 'head-raster');
  expect(screen.queryByText(text => text.includes('"checksum": "sha256:exact-output"'))).not.toBeInTheDocument();
  expect(screen.queryByText('模型与结果身份')).not.toBeInTheDocument();
});

it('dispatches each result reuse action with the exact immutable reference', () => {
  const task = taskFixture({ modelId: 'groundwater.summary', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const output = dataRef({ versionId: 'reuse-version', objectId: 'reuse-object', outputName: 'summary' });
  const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'SUCCEEDED', workflowRunId: task.workflowRunId, remoteOutputs: [output] };
  const onAttach = vi.fn(), onContinue = vi.fn(), onCompare = vi.fn();
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} onAttach={onAttach} onContinue={onContinue} onCompare={onCompare} />);
  fireEvent.click(screen.getByRole('button', { name: '作为下一步输入' }));
  fireEvent.click(screen.getByRole('button', { name: '在新对话中继续' }));
  fireEvent.click(screen.getByRole('button', { name: '与另一版本比较' }));
  expect(onAttach).toHaveBeenCalledExactlyOnceWith(output);
  expect(onContinue).toHaveBeenCalledExactlyOnceWith(output);
  expect(onCompare).toHaveBeenCalledExactlyOnceWith(output);
});

it('keeps a large result group compact with every published file reachable on expansion', () => {
  const task = taskFixture({ modelId: 'groundwater.transfer-learning-analysis', status: 'SUCCEEDED', stage: null, outputCount: 25 });
  const result: Result = { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: task.modelId, status: 'SUCCEEDED', workflowRunId: task.workflowRunId,
    remoteOutputs: Array.from({ length: 25 }, (_, index) => dataRef({ versionId: `published-${index + 1}`, objectId: `object-${index + 1}`, outputName: `结果 ${index + 1}` })) };
  render(<ResultCard task={task} result={result} project="demo" onResource={vi.fn()} />);
  expect(screen.getAllByRole('link', { name: '下载' })).toHaveLength(3);
  fireEvent.click(screen.getByRole('button', { name: '查看全部 25 个结果' }));
  const downloads = screen.getAllByRole('link', { name: '下载' });
  expect(downloads).toHaveLength(25);
  expect(downloads[24]).toHaveAttribute('href', '/api/v12/data/versions/published-25/objects/object-25/content');
  fireEvent.click(screen.getByRole('button', { name: '收起结果列表' }));
  expect(screen.getAllByRole('link', { name: '下载' })).toHaveLength(3);
});

it('uses a business label instead of exposing an unfamiliar model identity', () => {
  const task = taskFixture({ modelId: 'internal.experimental-model', status: 'FAILED', stage: null, outputCount: 0 });
  render(<ResultCard task={task} project="demo" onResource={vi.fn()} />);
  expect(screen.getByText('分析任务')).toBeVisible();
  expect(screen.queryByText('internal.experimental-model')).not.toBeInTheDocument();
});
