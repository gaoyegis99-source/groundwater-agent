import { expect, test, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import type { History, WorkflowDraft } from '../src/types';

const project = 'groundwater-demo';

test.describe.configure({ mode: 'serial' });

async function newChat(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toContainText('项目已连接');
  const created = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v12/sessions'
      && response.request().method() === 'POST');
  const mobileNavigation = page.getByRole('button', { name: '打开会话导航' });
  if (await mobileNavigation.isVisible()) await mobileNavigation.click();
  await page.getByRole('button', { name: '新建对话', exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(200);
  const session = await response.json();
  await expect(page).toHaveURL(new RegExp(`#/sessions/${session.sessionId}$`));
  return session.sessionId as string;
}

async function session(page: Page, id: string) {
  const response = await page.request.get(`/api/v12/sessions/${id}?projectId=${project}`);
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

async function messages(page: Page, id: string) {
  const response = await page.request.get(`/api/v12/sessions/${id}/messages?projectId=${project}`);
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

async function ask(page: Page, id: string, question: string) {
  await page.getByRole('textbox', { name: '消息输入框' }).fill(question);
  const accepted = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v12/sessions/${id}/messages`
      && response.request().method() === 'POST');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  expect((await accepted).status()).toBe(202);
  await expect.poll(async () => (await session(page, id)).agentSteps.at(-1), {
    timeout: 60_000,
  }).toBe('COMPLETE');
}

test('逐题 01：方法咨询只回复说明，不创建执行确认', async ({ page }) => {
  const id = await newChat(page);
  await ask(page, id, '地下水监测数据应该怎么分析？请只比较方法，不执行计算。');
  await expect(page.getByTestId('message-list')).toContainText('可以先比较趋势分析与空间分布分析');
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  expect((await session(page, id)).workflowRunIds).toEqual([]);
  expect((await messages(page, id)).task).toBeNull();
});

test('逐题 02：模糊请求给出一个问题和三个互斥选项', async ({ page }) => {
  const id = await newChat(page);
  await ask(page, id, '帮我处理一下');
  const options = page.getByRole('region', { name: '下一步选项' });
  await expect(options).toContainText('解释资料');
  await expect(options).toContainText('比较方法');
  await expect(options).toContainText('整理流程');
  await expect(options.getByRole('button')).toHaveCount(3);
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  expect((await session(page, id)).workflowRunIds).toEqual([]);
  const selected = options.getByRole('button', { name: /比较方法/ });
  const request = page.waitForRequest(value =>
    new URL(value.url()).pathname === `/api/v12/sessions/${id}/messages` && value.method() === 'POST');
  await selected.click();
  const submitted = (await request).postDataJSON();
  expect(submitted.requestedMode).toBe('AUTO');
  await expect.poll(async () => (await messages(page, id)).items.some(
    (item: { role: string; content: string }) => item.role === 'user' && item.content === submitted.message,
  )).toBe(true);
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
});

test('水位高程和埋深澄清作为普通消息保存，且不会批准运行', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const id = await newChat(page);
  await ask(page, id, '监测表只有“水位（米）”一列，帮我分析这列水位。');
  const history = async (): Promise<History> => messages(page, id);
  const before = await history();
  expect(before.turns).toHaveLength(1);
  const [clarification] = before.turns;
  expect(clarification).toMatchObject({ clarificationRequired: true, confirmations: [], executionTasks: [] });
  const options = page.getByRole('region', { name: '下一步选项' });
  await expect(page.getByTestId('message-list')).toContainText('水位高程还是从井口向下量的埋深');
  await expect(options.getByRole('button', { name: /水位高程/ })).toBeEnabled();
  const depth = options.getByRole('button', { name: /井口埋深/ });
  await expect(depth).toBeEnabled();
  await depth.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('water-level-clarification-mobile.png') });
  const selected = clarification.interactionOptions.find(option => option.label === '井口埋深')!;
  const requested = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/v12/sessions/${id}/messages`);
  await depth.click();
  const accepted = await requested;
  expect(accepted.status()).toBe(202);
  expect(accepted.request().postDataJSON()).toMatchObject({ requestedMode: 'AUTO', message: selected.message });
  const { goalRevision, requestId } = await accepted.json();
  await expect.poll(async () => (await history()).turns.find(turn => turn.requestId === requestId)?.status).toBe('COMPLETE');
  const completed = await history();
  expect(completed.items.filter(item => item.role === 'user' && item.content === selected.message))
    .toEqual([expect.objectContaining({ goalRevision })]);
  expect(completed.turns).toHaveLength(2);
  expect(completed.turns.every(turn => !turn.confirmations.length && !turn.executionTasks.length)).toBe(true);
  expect(completed.task).toBeNull();
  expect((await session(page, id)).workflowRunIds).toEqual([]);
  await page.reload();
  await expect(page.locator(`#turn-${goalRevision}`)).toContainText('同一高程基准');
  await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
  writeFileSync(info.outputPath('clarification-message.json'), JSON.stringify({
    sessionId: id, clarificationGoalRevision: clarification.goalRevision, goalRevision, requestId,
    requestedMode: 'AUTO', confirmationCount: 0, workflowRunCount: 0,
  }, null, 2) + '\n');
});

test('水位方法草稿保留依据和旧草稿，且没有执行授权', async ({ page }, info) => {
  const id = await newChat(page);
  const path = `/api/v12/sessions/${id}`;
  const drafts = page.getByTestId('workflow-draft-card');
  const askDraft = async (prompt: string, count: number) => {
    await ask(page, id, prompt);
    await expect(drafts.getByRole('heading', { name: '水位描述统计草稿' })).toHaveCount(count);
    const history: History = await messages(page, id);
    const draftId = history.turns.at(-1)?.workflowDraftId;
    expect(draftId).toBeTruthy();
    const response = await page.request.get(`${path}/workflow-drafts/${draftId}`);
    expect(response.status()).toBe(200);
    const draft: WorkflowDraft = await response.json();
    expect(draft).toMatchObject({ draftId, sessionId: id, status: 'COMPLETE', payload: { executionAllowed: false } });
    expect(draft.payload?.knowledgeRefs).toHaveLength(1);
    return draft;
  };

  const first = await askDraft('请生成水位描述统计工作流草稿，并给出方法依据。', 1);
  const card = drafts.first();
  await expect(card).toContainText('仅草稿，不可执行');
  const graph = card.locator('details').filter({ has: page.getByText('查看工作流依赖图', { exact: true }) });
  await expect(graph).toHaveCount(1);
  await expect(graph).not.toHaveAttribute('open', '');
  const graphView = graph.getByRole('group', { name: '工作流依赖图', includeHidden: true });
  await expect(graphView).toHaveCount(0);
  const graphToggle = graph.getByText('查看工作流依赖图', { exact: true });
  await graphToggle.focus();
  await page.keyboard.press('Enter');
  await expect(graph).toHaveAttribute('open', '');
  await expect(graphView).toBeVisible();
  await card.screenshot({ path: info.outputPath('method-draft-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(graphView).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await graph.screenshot({ path: info.outputPath('method-draft-graph-mobile.png') });
  await graphToggle.click();
  await expect(graphView).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await card.getByText('输入格式与知识依据', { exact: true }).click();
  await card.getByRole('button', { name: /知识依据 1/ }).click();
  await expect(page.getByTestId('knowledge-drawer').locator('.knowledge-body').first()).toBeVisible();
  await page.keyboard.press('Escape');
  const exported = await page.request.get(`${path}/workflow-drafts/${first.draftId}/export`);
  expect(exported.status()).toBe(200);
  expect(await exported.json()).toEqual(first.payload);

  const second = await askDraft('请重新生成水位描述统计工作流草稿，并保留原草稿供复核。', 2);
  expect(second.draftId).not.toBe(first.draftId);
  const old = await page.request.get(`${path}/workflow-drafts/${first.draftId}`);
  expect(await old.json()).toEqual(first);
  expect((await session(page, id)).workflowRunIds).toEqual([]);
  const history: History = await messages(page, id);
  expect(history.turns.every(turn => !turn.confirmations.length && !turn.executionTasks.length)).toBe(true);
  const secondPath = `${path}/workflow-drafts/${second.draftId}`;
  let secondReads = 0;
  await page.route(`**${secondPath}`, async route => {
    if (++secondReads === 1) {
      await route.fulfill({ json: first });
    } else {
      await route.continue();
    }
  });
  await page.reload();
  const readError = page.getByRole('region', { name: '工作流草稿读取失败' });
  await expect(readError.getByText('返回的草稿与本轮记录不一致，请重试读取。')).toBeVisible();
  await expect(drafts).toHaveCount(1);
  await expect(drafts.first()).toHaveAttribute('data-draft-id', first.draftId);
  await readError.getByRole('button', { name: '重试读取草稿' }).click();
  await expect(drafts.getByRole('heading', { name: '水位描述统计草稿' })).toHaveCount(2);
  await expect(drafts.last()).toHaveAttribute('data-draft-id', second.draftId);
  await expect(readError).toHaveCount(0);
  await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
  await drafts.last().scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('method-draft-history.png') });
});

test('逐题 03：知识问题读取证据后回复，不产生只读副作用', async ({ page }, testInfo) => {
  const id = await newChat(page);
  await ask(page, id, '请查询地下水时间聚合方法知识，只做说明。');
  await expect(page.getByTestId('message-list')).toContainText('没有适用于所有数据的唯一组合');
  await expect(page.getByTestId('message-list')).toContainText('频率选择');
  await expect(page.getByTestId('message-list')).toContainText('聚合函数');
  await expect(page.getByTestId('message-list')).toContainText('质量与限制');
  await expect(page.getByTestId('message-list')).toContainText('gw-aggregation-choice');
  await expect(page.getByTestId('message-list')).toContainText('更多信息');
  const activity = page.getByTestId('activity-panel').last();
  await activity.getByRole('button', { name: /^已完成/ }).click();
  await expect(activity).toContainText('检索候选知识证据');
  await expect(activity).toContainText(/找到 \d+ 个候选知识单元/);
  await expect(activity).toContainText('读取并核验证据');
  await expect(activity).toContainText(
    /已读取并核验 \d+ 个可引用知识单元；另有 \d+ 个单元不可引用/,
  );
  await expect(activity.locator('.activity-list > li')).toHaveCount(4);
  await expect(activity).toContainText('组织证据结论');
  await expect(activity).toContainText('读取并核验已选择的知识证据');
  await expect(activity).not.toContainText('技术信息');
  await expect(activity).not.toContainText('上下文峰值');
  await expect(activity.locator('.context-usage')).toHaveCount(0);
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  const state = await session(page, id);
  expect(state.workflowRunIds).toEqual([]);
  const turn = (await messages(page, id)).turns.at(-1);
  expect(turn.status).toBe('COMPLETE');
  expect(turn.activities
    .filter((activity: { kind: string }) => activity.kind === 'TOOL')
    .map((activity: { toolName: string }) => activity.toolName))
    .toEqual(['retrieve_evidence', 'read_evidence']);
  expect(turn.confirmations).toEqual([]);
  expect(turn.executionTasks).toEqual([]);

  const conclusion = page.locator('.reply-conclusion');
  const sourceIndexes: number[] = turn.reply.conclusion.sourceIndexes;
  expect(sourceIndexes.length).toBeGreaterThan(1);
  const index = sourceIndexes[1];
  const { versionId, unitId, checksum, title } = turn.reply.sources[index];
  const ref = { versionId, unitId, checksum };
  const citation = conclusion.getByRole('button', { name: `查看依据 ${index + 1} · ${title}`, exact: true });
  const evidenceResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v12/knowledge/evidence/read');
  await citation.press('Enter');
  const response = await evidenceResponse;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON()).toEqual({ refs: [ref] });
  const bundle = await response.json();
  const evidence = bundle.items.find((item: typeof ref) => item.versionId === versionId && item.unitId === unitId && item.checksum === checksum);
  expect(evidence?.status).toBe('OK');
  expect(evidence.content.trim().length).toBeGreaterThan(0);
  const drawer = page.getByRole('dialog', { name: '知识库' });
  const article = drawer.locator('[data-primary="true"]');
  await expect(article.locator('.knowledge-content')).toHaveText(evidence.content.trim());
  await expect(article).toBeFocused();
  await article.getByText('技术身份').click();
  await expect(article.getByText(checksum, { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('knowledge-citation.png') });
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(citation).toBeFocused();

  const wrongChecksum = `${checksum.slice(0, -1)}${checksum.endsWith('0') ? '1' : '0'}`;
  const staleResponse = await page.request.post('/api/v12/knowledge/evidence/read', { data: { refs: [{ ...ref, checksum: wrongChecksum }] } });
  expect(staleResponse.status()).toBe(200);
  const stale = await staleResponse.json();
  expect(stale.items).toHaveLength(1);
  expect(stale.items[0]).toMatchObject({ ...ref, checksum: wrongChecksum, status: 'STALE' });
  expect(stale.items[0].content == null).toBe(true);
  const reloadedState = await session(page, id);
  expect(reloadedState.workflowRunIds).toEqual([]);
  await page.reload();
  await expect(citation).toBeVisible();
  writeFileSync(testInfo.outputPath('knowledge-citation.json'), JSON.stringify({
    sessionId: id, index, ref, receiptId: bundle.receiptId, status: evidence.status,
    contentLength: evidence.content.length, staleReceiptId: stale.receiptId,
    staleStatus: stale.items[0].status, workflowRunIds: reloadedState.workflowRunIds,
  }, null, 2) + '\n');
});
