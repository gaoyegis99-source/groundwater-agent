import { createHash, randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import type { History, Result } from '../src/types';

const project = 'groundwater-demo';

test.describe.configure({ mode: 'serial' });

async function newChat(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toContainText('项目已连接');
  const created = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v12/sessions'
      && response.request().method() === 'POST');
  await page.getByRole('button', { name: '新建对话', exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(200);
  const session = await response.json();
  await expect(page).toHaveURL(new RegExp(`#/sessions/${session.sessionId}$`));
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeEnabled();
  return session.sessionId as string;
}

async function session(page: Page, id: string) {
  const response = await page.request.get(`/api/v12/sessions/${id}?projectId=${project}`);
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

async function productUi(page: Page) {
  const response = await page.request.get('/product-ui.json');
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<{ outputs: Record<string, { label: string }> }>;
}

async function summaryInput(page: Page) {
  const response = await page.request.get('/api/v12/data/datasets?query=六模型观测表&limit=20');
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  const dataset = body.items.find((item: any) => item.title === '六模型观测表');
  expect(dataset).toBeTruthy();
  return dataset.currentVersion as string;
}

async function proposeSummary(page: Page, sessionId: string, marker: string, objective?: string, axis = 'columns') {
  const versionId = await summaryInput(page);
  const execution = `[Evaluation case: ${marker}]\n[Model execution] ${JSON.stringify({
    tool: 'run_summary', arguments: { versionId, column: 'hydraulic_head_m', axis },
  })}`;
  const message = objective ? `${objective}\n${execution}` : execution;
  await page.getByRole('textbox', { name: '消息输入框' }).fill(message);
  const submitted = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v12/sessions/${sessionId}/messages`
      && response.request().method() === 'POST');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  expect((await submitted).status()).toBe(202);
  await expect(page.getByTestId('confirm-execution')).toBeEnabled({ timeout: 60_000 });
  return versionId;
}

test('01 desktop shell, navigation, API boundary and keyboard controls', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await expect(page.getByTestId('chat-app')).toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeVisible();
  await expect(page.getByTestId('connection-status')).toContainText('项目已连接');
  await expect(page.getByRole('button', { name: '数据库' })).toBeEnabled();
  await expect(page.getByRole('button', { name: '知识库' })).toBeEnabled();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole('button', { name: '折叠会话侧栏' })).toBeVisible();
  await page.getByRole('button', { name: '打开任务工作台' }).click();
  await expect(page.getByRole('heading', { name: '任务工作台' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '数据' })).toBeVisible();
  const tabs = page.getByRole('tablist', { name: '任务工作台视图' });
  await tabs.getByRole('tab', { name: '数据', exact: true }).click();
  await page.keyboard.press('End');
  await expect(tabs.getByRole('tab', { name: '成果', exact: true })).toBeFocused();
  await page.getByRole('button', { name: '折叠任务工作台' }).click();
  await expect(page.getByRole('button', { name: '打开任务工作台' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('01-shell-desktop.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('02 Data and Knowledge drawers navigate real v12 records', async ({ page }, info) => {
  await page.goto('/');
  await page.getByRole('button', { name: '数据库' }).click();
  const data = page.getByTestId('database-workbench');
  await expect(data.locator('.database-item').first()).toBeVisible();
  await data.locator('.database-item').first().click();
  await data.locator('details.database-identity > summary').click();
  await expect(data.getByText('数据版本', { exact: true }).first()).toBeVisible();
  await expect(data.getByText('数据集', { exact: true })).toBeVisible();
  await expect(data.getByText('语义版本', { exact: true })).toBeVisible();
  await expect(data.getByLabel('数据详情').getByText('校验和', { exact: true })).toBeVisible();
  await expect(data).not.toContainText('Data Version');
  await data.getByRole('button', { name: '附加精确版本' }).click();
  await expect(data).toHaveCount(0);
  await expect(page.locator('.attachment-chip')).toHaveCount(1);
  await page.locator('.attachment-chip .text-button').click();
  await expect(page.getByTestId('database-workbench')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('database-workbench')).toHaveCount(0);

  await page.getByRole('button', { name: '知识库' }).click();
  const knowledge = page.getByTestId('knowledge-drawer');
  await expect(knowledge.locator('.library-item').first()).toBeVisible();
  await knowledge.locator('.library-item').first().click();
  await expect(knowledge.locator('.knowledge-body').first()).toBeVisible();
  const technicalIdentity = knowledge.locator('details.evidence-identity').first();
  await expect(technicalIdentity).not.toHaveAttribute('open', '');
  await technicalIdentity.locator('summary').click();
  await expect(knowledge.getByText('知识版本', { exact: true }).first()).toBeVisible();
  await expect(knowledge.getByText('证据单元', { exact: true }).first()).toBeVisible();
  await expect(knowledge.getByText('内容校验值', { exact: true }).first()).toBeVisible();
  await expect(knowledge).not.toContainText('Knowledge Next');
  await page.screenshot({ path: info.outputPath('02-knowledge-evidence.png'), fullPage: true });
});

test('03 upload, attach, send, reload and inspect exact Data version', async ({ page }, info) => {
  const id = await newChat(page);
  const bytes = Buffer.from('well,value\nW01,10\nW02,12\nW03,14\n');
  const name = `前端逐项测试-${randomUUID().slice(0, 8)}.csv`;
  await page.locator('input[type=file]').setInputFiles({
    name, mimeType: 'text/csv', buffer: bytes,
  });
  const dialog = page.getByRole('dialog', { name: '添加资料' });
  await expect(dialog).toBeVisible();
  await dialog.getByPlaceholder('简述文件内容').fill('前端上传与精确版本附加测试');
  const publication = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v12/data/publications'
      && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: '上传并附加' }).click();
  expect([200, 201]).toContain((await publication).status());
  await expect(dialog).toHaveCount(0, { timeout: 60_000 });
  await expect(page.locator('.attachment-chip')).toContainText(name);
  await page.getByRole('textbox', { name: '消息输入框' }).fill('请说明这个附件的数据结构，不要执行计算。');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect.poll(async () => (await session(page, id)).agentSteps.at(-1), {
    timeout: 60_000,
  }).toBe('COMPLETE');
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('message-list')).toContainText('请说明这个附件的数据结构，不要执行计算。');
  await expect(page.getByTestId('session-list')).not.toContainText('Dataset:');
  const attachment = page.getByTestId('message-list').locator('.message-input-references button');
  await expect(attachment).toHaveCount(1);
  await attachment.click();
  const drawer = page.getByTestId('database-workbench');
  await expect(drawer).toContainText(name);
  await drawer.getByText('不可变身份与技术详情', { exact: true }).click();
  await expect(drawer).toContainText(createHash('sha256').update(bytes).digest('hex'));
  await expect(drawer.getByRole('heading', { name: '字段与质量' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('03-uploaded-version.png'), fullPage: true });
});

test('G18 keeps a blocked spatial check while approving and executing the ready trend', async ({ page }, info) => {
  const id = await newChat(page);
  const name = `g18-observations-${randomUUID().slice(0, 8)}.csv`;
  const rows = Array.from({ length: 24 }, (_, index) =>
    `W${Math.floor(index / 12) + 1},2025-${String(index % 12 + 1).padStart(2, '0')}-01,${80 + index / 10}`);
  await page.locator('input[type=file]').setInputFiles({
    name, mimeType: 'text/csv', buffer: Buffer.from(['well,time,hydraulic_head_m', ...rows].join('\n') + '\n'),
  });
  const dialog = page.getByRole('dialog', { name: '添加资料' });
  await dialog.getByPlaceholder('简述文件内容').fill('合成月度水位，缺少井位坐标');
  await dialog.getByRole('button', { name: '上传并附加' }).click();
  await expect(page.locator('.attachment-chip')).toContainText(name, { timeout: 60_000 });
  const datasets = await page.request.get('/api/v12/data/datasets', { params: { query: name } });
  expect(datasets.ok()).toBeTruthy();
  const versionId = (await datasets.json()).items.find((item: { title: string }) => item.title === name)?.currentVersion;
  expect(versionId).toBeTruthy();
  const arguments_ = {
    versionId, groupField: 'well', timeField: 'time', valueField: 'hydraulic_head_m', frequency: 'month',
    expectedStart: '2025-01-01', expectedEnd: '2025-12-01', baselineStart: '2025-01-01',
    baselineEnd: '2025-06-01', comparisonStart: '2025-07-01', comparisonEnd: '2025-12-01', aggregation: 'mean',
  };
  const path = `/api/v12/sessions/${id}`;
  const message = `请检查流场和时序趋势。缺坐标时先执行时序趋势。\n[Evaluation case: g18-partial-${id}]\n[Model execution] ${JSON.stringify({ tool: 'run_monitoring_trend', arguments: arguments_ })}`;
  await page.getByRole('textbox', { name: '消息输入框' }).fill(message);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByTestId('confirm-execution')).toBeEnabled({ timeout: 60_000 });
  const before = await session(page, id);
  expect(before.workflowRunIds).toEqual([]);
  expect(before.confirmation.actions.map((action: { tool: string }) => action.tool)).toEqual(['run_monitoring_trend']);
  const preparationResponse = await page.request.get(path + '/preparation');
  expect(preparationResponse.ok()).toBeTruthy();
  const prepared = await preparationResponse.json();
  expect(prepared.entries.map((entry: { targetId: string; status: string }) => [entry.targetId, entry.status])).toEqual([
    ['groundwater.flow-field', 'BLOCKED'], ['groundwater.monitoring-trend', 'READY'],
  ]);
  expect(prepared.entries[0].blockingIssues).toEqual(expect.arrayContaining([
    expect.objectContaining({ parameter: 'xField', kind: 'MISSING_PARAMETER' }),
    expect.objectContaining({ parameter: 'yField', kind: 'MISSING_PARAMETER' }),
  ]));
  await page.reload();
  const workspace = page.getByTestId('workspace-panel');
  await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '数据', exact: true }).click();
  const checks = workspace.locator('.workspace-preparation');
  await expect(checks).toContainText('地下水流场');
  await expect(checks).toContainText('待补充');
  await expect(checks).toContainText('可提案');
  await page.screenshot({ path: info.outputPath('g18-preparation-after-refresh.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await page.getByTestId('confirm-execution').click();
  await expect.poll(async () => (await session(page, id)).agentSteps.at(-1), { timeout: 120_000 }).toBe('COMPLETE');
  const finished = await session(page, id);
  expect(finished.workflowRunIds).toHaveLength(1);
  const runResponse = await page.request.get(`/api/v12/workflow-runs/${finished.workflowRunIds[0]}`, { params: { projectId: project } });
  expect(runResponse.ok()).toBeTruthy();
  const run = await runResponse.json();
  expect(run).toMatchObject({ status: 'SUCCEEDED', modelId: 'groundwater.monitoring-trend' });
  expect(run.outputs.length).toBeGreaterThan(0);
  expect(finished.preparation.entries).toEqual(prepared.entries);
  await writeFile(info.outputPath('g18-lifecycle.json'), JSON.stringify({ id, versionId, prepared, confirmation: before.confirmation, run }, null, 2));
});

test('04 execution proposal confirms, publishes, downloads and restores results', async ({ page }, info) => {
  const outputLabel = (await productUi(page)).outputs.summary.label;
  const id = await newChat(page);
  const inputVersion = await proposeSummary(page, id, `frontend-confirm-${randomUUID()}`);
  const confirmation = page.getByTestId('confirmation-card');
  await confirmation.getByText('核对本批次资料与参数', { exact: true }).click();
  await expect(confirmation).toContainText(inputVersion);
  await expect(confirmation.getByRole('button', { name: '确认执行修订 1' })).toBeEnabled();
  expect((await session(page, id)).workflowRunIds).toEqual([]);
  const resultPath = `/api/v12/sessions/${id}/results`;
  let releaseResults!: () => void;
  const heldResults = new Promise<void>(resolve => { releaseResults = resolve; });
  const holdResults = (url: URL) => url.pathname === resultPath;
  await page.route(holdResults, async route => {
    await heldResults;
    await route.fulfill({ status: 503, json: { code: 'SERVICE_UNAVAILABLE' } });
  });
  const confirmed = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v12/sessions/${id}/confirm`
      && response.request().method() === 'POST');
  await page.getByTestId('confirm-execution').click();
  expect((await confirmed).status()).toBe(202);
  const resultCard = page.getByTestId('result-card');
  try {
    await expect(resultCard).toHaveAttribute('data-status', 'SUCCEEDED', { timeout: 90_000 });
    await expect(resultCard.getByRole('link', { name: '下载' })).toHaveCount(0);
    const runId = (await session(page, id)).workflowRunIds[0];
    const linked = await page.request.get(`/api/v12/workflow-runs/${runId}/publication`, { params: { projectId: project } });
    expect(linked.ok()).toBeTruthy();
    const publication = await linked.json();
    expect(publication).toMatchObject({ workflowRunId: runId, publicationId: expect.any(String), state: 'PUBLISHED' });
    await writeFile(info.outputPath('04-publication-before-results.json'), JSON.stringify(publication, null, 2));
    await page.screenshot({ path: info.outputPath('04-published-results-read-pending.png') });
  } finally {
    releaseResults();
  }
  await expect(resultCard).toContainText('成果读取失败');
  await expect(resultCard.getByRole('link', { name: '下载' })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('04-published-results-read-failed.png') });
  await page.unroute(holdResults);
  await page.getByRole('button', { name: '重新读取成果', exact: true }).click();
  const downloads = resultCard.getByRole('link', { name: '下载' });
  await expect(downloads).toHaveCount(2);
  const results = await page.request.get(`/api/v12/sessions/${id}/results?projectId=${project}`);
  expect(results.status()).toBe(200);
  const published = (await results.json()).items.find((item: { status: string }) => item.status === 'COMPLETE');
  const output = published.remoteOutputs.find((item: { outputName: string }) => item.outputName === 'summary');
  expect(output.format).toBe('json');
  const row = resultCard.locator(`[data-resource-id="${output.versionId}"]`);
  const download = row.getByRole('link', { name: '下载' });
  const contentPath = `/api/v12/data/versions/${output.versionId}/objects/${output.objectId}/content`;
  await expect(download).toHaveAttribute('href', contentPath);
  await expect(download).toHaveAttribute('download', 'summary.json');
  const downloadResponse = await page.request.get(contentPath);
  expect(downloadResponse.status()).toBe(200);
  const downloaded = await downloadResponse.body();
  expect(downloaded.length).toBe(output.sizeBytes);
  expect(`sha256:${createHash('sha256').update(downloaded).digest('hex')}`).toBe(output.checksum);
  await resultCard.getByRole('button', { name: outputLabel, exact: true }).click();
  const workspace = page.getByTestId('workspace-panel');
  await expect(workspace.getByRole('heading', { name: outputLabel })).toBeVisible();
  await expect(workspace.getByRole('heading', { name: 'Summary' })).toHaveCount(0);
  await expect(workspace.getByText('数据集', { exact: true })).toBeVisible();
  await expect(workspace.getByText('数据版本', { exact: true })).toBeVisible();
  await expect(workspace.getByText('语义版本', { exact: true })).toBeVisible();
  await expect(workspace).toContainText('血缘输入');
  await expect(workspace).toContainText(inputVersion);
  await expect(workspace.getByText('科学验证状态未提供', { exact: true })).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: '搜索 JSON' })).toBeVisible();
  await workspace.getByRole('button', { name: 'hydraulic_head_m', exact: true }).locator('..').click();
  await expect(workspace.getByText('count', { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: info.outputPath('04-structured-report-preview.png') });
  await page.reload();
  await expect(page).toHaveURL(/\/workbench\/results$/);
  await expect(workspace.getByRole('heading', { name: outputLabel })).toBeVisible();
  await workspace.getByRole('button', { name: '返回对话' }).click();
  await expect(page.getByTestId('result-card')).toBeVisible();
  await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('04-confirmed-result.png'), fullPage: true });

  const semanticWrite = await page.request.post(`/api/v12/data/versions/${output.versionId}/semantics`, {
    data: { expectedSemanticId: output.semanticId, semantics: { description: '隔离验收后续语义' } },
  });
  expect(semanticWrite.status()).toBe(200);
  const laterSemanticId = (await semanticWrite.json()).semanticId;
  expect(laterSemanticId).not.toBe(output.semanticId);

  const readDraftAttachment = (sessionId: string) => page.evaluate(current => {
    const key = Object.keys(sessionStorage).find(entry => !entry.includes(':unbound:') && entry.endsWith(`:draft-attachments:${current}`));
    return key ? JSON.parse(sessionStorage.getItem(key) ?? '[]') : [];
  }, sessionId);
  await row.getByRole('button', { name: '附加到当前草稿' }).click();
  await expect(page.getByTestId('pending-attachments')).toContainText(outputLabel);
  await expect.poll(() => readDraftAttachment(id)).toEqual([expect.objectContaining(output)]);
  const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v12/sessions' && response.request().method() === 'POST');
  await row.getByRole('button', { name: '带成果打开新会话' }).click();
  const continuation = await created;
  expect(continuation.status()).toBe(200);
  const nextId = (await continuation.json()).sessionId;
  await expect(page).toHaveURL(new RegExp(`#/sessions/${nextId}$`));
  await expect(page.getByTestId('pending-attachments')).toContainText(outputLabel);
  await expect.poll(() => readDraftAttachment(nextId)).toEqual([expect.objectContaining(output)]);
  await page.getByTestId('pending-attachments').getByTitle(/查看资料内容与版本/).click();
  const database = page.getByRole('dialog', { name: '数据库' });
  const detail = database.getByRole('region', { name: '数据详情' });
  await detail.getByText('不可变身份与技术详情').click();
  await expect(detail.getByText(output.semanticId, { exact: true })).toBeVisible();
  await expect(detail.getByText('隔离验收后续语义')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('continued-exact-result.png') });
  const newSession = await session(page, nextId);
  expect(newSession.workflowRunIds).toEqual([]);
  const messages = await page.request.get(`/api/v12/sessions/${nextId}/messages?projectId=${project}`);
  expect(messages.status()).toBe(200);
  expect((await messages.json()).items).toEqual([]);
  await database.getByRole('button', { name: '关闭数据库' }).click();
  await page.goto(`/#/sessions/${id}`);
  await page.getByRole('button', { name: '归档', exact: true }).click();
  await expect(page).toHaveURL(/#\/$/);
  await page.getByRole('checkbox', { name: '显示归档' }).check();
  const archived = page.locator(`a.session-link[href="#/sessions/${id}"]`);
  await expect(archived).toContainText('已归档');
  await archived.click();
  await expect(page.locator(`[data-resource-id="${output.versionId}"]`).getByRole('link', { name: '下载' }))
    .toHaveAttribute('href', contentPath);
  await writeFile(info.outputPath('published-result-reuse.json'), JSON.stringify({
    sessionId: id, continuationSessionId: nextId, workflowRunId: published.workflowRunId,
    output, laterSemanticId, verifiedDownloadBytes: downloaded.length, continuationWorkflowRunIds: newSession.workflowRunIds,
  }, null, 2) + '\n');
});

test('05 rejection requires a reason and creates no WorkflowRun', async ({ page }, info) => {
  const id = await newChat(page);
  await proposeSummary(page, id, `frontend-reject-${randomUUID()}`);
  await page.getByRole('button', { name: '拒绝此批次' }).click();
  const submit = page.getByRole('button', { name: '提交拒绝' });
  await expect(submit).toBeDisabled();
  await page.getByRole('textbox', { name: '拒绝原因' }).fill('前端逐项测试：参数需要调整');
  const rejected = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v12/sessions/${id}/reject`
      && response.request().method() === 'POST');
  await submit.click();
  expect((await rejected).status()).toBe(200);
  await expect.poll(async () => (await session(page, id)).agentSteps.at(-1), {
    timeout: 60_000,
  }).toBe('COMPLETE');
  const state = await session(page, id);
  expect(state.workflowRunIds).toEqual([]);
  await expect(page.getByTestId('result-card')).toHaveCount(0);
  await page.locator('.historical-confirmation > summary').click();
  await expect(page.getByTestId('confirmation-card')).toContainText('已拒绝');
  await page.screenshot({ path: info.outputPath('05-rejected.png'), fullPage: true });
});

test('06 Task review is persisted and the archived Session restores its completion', async ({ page }, info) => {
  const id = await newChat(page);
  const goal = '请整理一份地下水资料评审工作说明，列出资料缺口，最终需要我评审是否可用。暂不查询资料。';
  const history = async (): Promise<History> => {
    const response = await page.request.get(`/api/v12/sessions/${id}/messages`);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  await page.getByRole('textbox', { name: '消息输入框' }).fill(goal);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect.poll(async () => (await history()).task?.progress.state).toBe('WAITING_REVIEW');
  const header = page.getByRole('region', { name: '当前研究任务' });
  await expect(header).toContainText(goal);
  const firstRevision = (await history()).task!.spec.revision;
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  await composer.fill('补充要求：仅使用公开资料。请更新主任务，保留原有交付和评审条件。');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect.poll(async () => {
    const task = (await history()).task;
    return task?.progress.state === 'WAITING_REVIEW' ? task.spec.revision : 0;
  }).toBeGreaterThan(firstRevision);
  await header.getByRole('button', { name: '查看任务概览' }).click();
  const taskCard = page.getByRole('region', { name: '主任务' });
  await expect(taskCard.getByRole('button', { name: '确认此项满足' })).toHaveCount(2);
  const reviewLines = (await history()).task!.spec.criteria
    .filter(criterion => criterion.kind === 'USER_REVIEW')
    .map(criterion => `确认完成：${criterion.text}`);
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await composer.fill(reviewLines.join('\n'));
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect.poll(async () => (await history()).task?.progress.lifecycle).toBe('COMPLETE');
  const completed = await history();
  expect(completed.task?.progress.criteria.every(criterion => criterion.satisfied)).toBe(true);
  expect(completed.turns.flatMap(turn => turn.executionTasks)).toEqual([]);
  await header.getByRole('button', { name: '查看任务概览' }).click();
  await expect(taskCard.getByRole('blockquote')).toHaveCount(2);
  await page.screenshot({ path: info.outputPath('06-task-reviewed.png') });

  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await page.getByRole('button', { name: '归档', exact: true }).click();
  await expect(page).toHaveURL(/#\/$/);
  await page.getByRole('checkbox', { name: '显示归档' }).check();
  const archived = page.locator(`a.session-link[href="#/sessions/${id}"]`);
  await expect(archived).toContainText('已归档');
  await archived.click();
  await expect(header.getByText('任务已完成', { exact: true })).toBeVisible();
  await page.reload();
  await expect(header.getByText('任务已完成', { exact: true })).toBeVisible();
  expect((await history()).task?.spec.taskId).toBe(completed.task?.spec.taskId);
  await page.screenshot({ path: info.outputPath('06-archived-task-restored.png') });
});

test('07 one Task retains its reviewed publication after archiving and reloading', async ({ page }, info) => {
  const id = await newChat(page);
  const path = `/api/v12/sessions/${id}`;
  const history = async (): Promise<History> => {
    const response = await page.request.get(path + '/messages');
    expect(response.status()).toBe(200);
    return response.json();
  };
  const objective = '请建立水位统计成果评审任务，交付水位统计汇总和对照统计汇总，最后由我确认统计汇总可用于复核。';
  const inputVersion = await proposeSummary(page, id, `task-published-review-${randomUUID()}`, objective);
  const pending = await history();
  const taskId = pending.task?.spec.taskId;
  expect(taskId).toBeTruthy();
  expect(pending.task).toMatchObject({
    spec: { objective },
    progress: { lifecycle: 'ACTIVE', state: 'WAITING_CONFIRMATION', results: [] },
  });
  expect(pending.turns.flatMap(turn => turn.executionTasks)).toEqual([]);
  await page.getByTestId('confirm-execution').click();
  const executionRevision = pending.goalRevision;
  await expect.poll(async () => (await history()).turns.find(turn => turn.goalRevision === executionRevision)?.status,
    { timeout: 90_000 }).toBe('COMPLETE');
  const [run] = (await history()).turns.flatMap(turn => turn.executionTasks);
  expect(run?.status).toBe('SUCCEEDED');
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  const register = async (label: string, criterionId: string, execution: typeof run) => {
    await composer.fill(`请将已发布${label}登记为任务成果证据。AgentRun: ${execution.agentRunId}，WorkflowRun: ${execution.workflowRunId}。`);
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await expect.poll(async () => {
      const snapshot = await history();
      const turn = snapshot.turns.at(-1);
      if (turn?.status === 'FAILED') return { errorCode: turn.errorCode, diagnostics: turn.diagnostics };
      return snapshot.task?.progress.criteria.find(item => item.id === criterionId)?.satisfied
        ? snapshot.task.progress.state : null;
    }).toBe('WAITING_REVIEW');
  };
  await register('汇总', 'summary', run);
  const baseline = (await history()).task!.progress.results;
  expect(baseline.map(item => item.outputName).sort()).toEqual(['summary', 'summary-details']);

  await proposeSummary(page, id, `task-comparison-${randomUUID()}`, '请执行对照统计汇总。', 'flatten');
  const comparisonPending = await history();
  expect(comparisonPending.task?.spec.taskId).toBe(taskId);
  expect(comparisonPending.turns.flatMap(turn => turn.executionTasks)).toEqual([run]);
  await page.getByTestId('confirm-execution').click();
  await expect.poll(async () => (await history()).turns.find(turn => turn.goalRevision === comparisonPending.goalRevision)?.status,
    { timeout: 90_000 }).toBe('COMPLETE');
  const comparison = (await history()).turns.find(turn => turn.goalRevision === comparisonPending.goalRevision)!.executionTasks[0];
  expect(comparison?.status).toBe('SUCCEEDED');
  expect(comparison.workflowRunId).not.toBe(run.workflowRunId);
  await register('对照汇总', 'comparison', comparison);
  const awaiting = await history();
  expect(awaiting.task?.spec.taskId).toBe(taskId);
  const checks = awaiting.task!.progress.criteria;
  expect(checks).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'summary', satisfied: true, evidence: expect.objectContaining({
      agentRunId: run.agentRunId, workflowRunId: run.workflowRunId,
    }) }),
    expect.objectContaining({ id: 'comparison', satisfied: true, evidence: expect.objectContaining({
      agentRunId: comparison.agentRunId, workflowRunId: comparison.workflowRunId,
    }) }),
    expect.objectContaining({ id: 'review', satisfied: false }),
  ]));
  const taskResults = awaiting.task!.progress.results;
  expect(taskResults.map(item => item.outputName).sort()).toEqual(['summary', 'summary', 'summary-details', 'summary-details']);
  const resultsResponse = await page.request.get(path + '/results');
  expect(resultsResponse.status()).toBe(200);
  const results: { items: Result[] } = await resultsResponse.json();
  const output = baseline.find(item => item.outputName === 'summary')!;
  const comparisonResult = results.items.find(item => item.workflowRunId === comparison.workflowRunId);
  expect(comparisonResult).toMatchObject({ agentRunId: comparison.agentRunId, status: 'COMPLETE' });
  const comparisonOutput = comparisonResult!.remoteOutputs.find(item => item.outputName === 'summary')!;
  expect(taskResults).toContainEqual(comparisonOutput);
  expect(comparisonOutput.versionId).not.toBe(output.versionId);
  const objectPath = `/api/v12/data/versions/${output.versionId}/objects/${output.objectId}/content`;
  const header = page.getByRole('region', { name: '当前研究任务' });
  await header.getByRole('button', { name: '查看任务概览' }).click();
  const taskCard = page.getByRole('region', { name: '主任务' });
  const resultCriterion = taskCard.locator('.task-criteria > li').filter({
    has: page.getByText('水位统计汇总', { exact: true }),
  });
  const comparisonCriterion = taskCard.locator('.task-criteria > li').filter({
    has: page.getByText('对照统计汇总', { exact: true }),
  });
  const resultLabel = (await productUi(page)).outputs.summary.label;
  await expect(resultCriterion.getByRole('button', { name: new RegExp(resultLabel) })).toBeVisible();
  await expect(comparisonCriterion.getByRole('button', { name: new RegExp(resultLabel) })).toBeVisible();
  for (const [criterion, runId] of [[resultCriterion, run.workflowRunId], [comparisonCriterion, comparison.workflowRunId]] as const) {
    await criterion.getByText('查看服务端证据', { exact: true }).click();
    await expect(criterion.getByText(runId, { exact: true })).toBeVisible();
  }
  const quote = '确认完成：统计汇总可用于复核';
  const reviewAttempts: { requestId: string; message: string }[] = [];
  // Fail delivery before forwarding, then replay the saved request to the real Kernel.
  await page.route(`**${path}/messages`, async route => {
    const request = route.request();
    if (request.method() === 'POST' && request.postDataJSON().message === quote) {
      reviewAttempts.push(request.postDataJSON());
      if (reviewAttempts.length === 1) {
        await route.abort('failed');
        return;
      }
    }
    await route.continue();
  });
  await expect(taskCard.getByRole('button', { name: '确认此项满足' })).toBeEnabled();
  await taskCard.getByRole('button', { name: '确认此项满足' }).click();
  const retry = page.getByTestId('workspace-panel').getByRole('button', { name: '重试发送' });
  await expect(retry).toBeEnabled();
  expect((await history()).task?.progress.state).toBe('WAITING_REVIEW');
  await page.reload();
  await expect(retry).toBeEnabled();
  await expect(taskCard.getByRole('button', { name: '确认此项满足' })).toBeDisabled();
  await page.screenshot({ path: info.outputPath('07-task-review-retry.png') });
  await retry.click();
  await expect.poll(async () => (await history()).task?.progress.lifecycle).toBe('COMPLETE');
  const complete = await history();
  expect(reviewAttempts).toHaveLength(2);
  expect(reviewAttempts[1]).toEqual(reviewAttempts[0]);
  expect(complete.items.filter(item => item.role === 'user' && item.content === quote)).toHaveLength(1);
  expect(complete.turns.filter(turn => turn.requestId === reviewAttempts[0].requestId)).toHaveLength(1);
  expect(complete.task?.spec.taskId).toBe(taskId);
  expect(complete.task?.progress.results).toEqual(taskResults);
  await expect(taskCard.getByRole('blockquote')).toHaveText(quote);
  await page.screenshot({ path: info.outputPath('07-task-published-reviewed.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await page.getByRole('button', { name: '归档', exact: true }).click();
  await expect(page).toHaveURL(/#\/$/);
  await page.getByRole('checkbox', { name: '显示归档' }).check();
  const archived = page.locator(`a.session-link[href="#/sessions/${id}"]`);
  await expect(archived).toContainText('已归档');
  await archived.click();
  await page.reload();
  await expect(header.getByText('任务已完成', { exact: true })).toBeVisible();
  expect((await history()).task).toEqual(complete.task);
  const newestPage = await page.request.get(`/api/v12/sessions/${id}/messages?limit=1`);
  expect(newestPage.status()).toBe(200);
  const newest = await newestPage.json() as History;
  expect(newest.items).toHaveLength(1);
  expect(newest.nextCursor).toBe(newest.items[0].messageId);
  expect(newest.nextCursor).not.toBeNull();
  const olderPage = await page.request.get(`/api/v12/sessions/${id}/messages?limit=1&cursor=${newest.nextCursor}`);
  expect(olderPage.status()).toBe(200);
  const older = await olderPage.json() as History;
  expect(older.sessionId).toBe(id);
  expect(older.items).toHaveLength(1);
  expect(older.items[0].messageId).toBeLessThan(newest.items[0].messageId);
  expect(older.eventCursor).toBeGreaterThanOrEqual(newest.eventCursor);
  const card = page.locator(`[data-workflow-run-id="${run.workflowRunId}"]`);
  const download = card.locator(`[data-resource-id="${output.versionId}"]`).getByRole('link', { name: '下载', exact: true });
  await expect(download).toHaveAttribute('href', objectPath);
  const comparisonCard = page.locator(`[data-workflow-run-id="${comparison.workflowRunId}"]`);
  await expect(comparisonCard.locator(`[data-resource-id="${output.versionId}"]`)).toHaveCount(0);
  await expect(card.locator(`[data-resource-id="${comparisonOutput.versionId}"]`)).toHaveCount(0);
  await expect(comparisonCard.locator(`[data-resource-id="${comparisonOutput.versionId}"]`).getByRole('link', { name: '下载', exact: true }))
    .toHaveAttribute('href', `/api/v12/data/versions/${comparisonOutput.versionId}/objects/${comparisonOutput.objectId}/content`);
  const response = await page.request.get(objectPath);
  expect(response.status()).toBe(200);
  expect(`sha256:${createHash('sha256').update(await response.body()).digest('hex')}`).toBe(output.checksum);
  const comparisonResponse = await page.request.get(`/api/v12/data/versions/${comparisonOutput.versionId}/objects/${comparisonOutput.objectId}/content`);
  expect(comparisonResponse.status()).toBe(200);
  expect(`sha256:${createHash('sha256').update(await comparisonResponse.body()).digest('hex')}`).toBe(comparisonOutput.checksum);
  await header.getByRole('button', { name: '查看任务概览' }).click();
  await expect(taskCard.getByRole('blockquote')).toHaveText(quote);
  await expect(resultCriterion.getByRole('button', { name: new RegExp(resultLabel) })).toBeVisible();
  await page.screenshot({ path: info.outputPath('07-archived-task-publication.png') });
  await writeFile(info.outputPath('07-archived-task-publication.json'), JSON.stringify({
    sessionId: id, taskId, runId: run.workflowRunId, comparisonRunId: comparison.workflowRunId, inputVersion, output, comparisonOutput, quote,
    pagination: { newest: newest.items[0].messageId, older: older.items[0].messageId, eventCursor: older.eventCursor },
  }, null, 2));
});
