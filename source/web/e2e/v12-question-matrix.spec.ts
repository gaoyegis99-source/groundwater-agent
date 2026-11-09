import { expect, test, type Page } from '@playwright/test';

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
});

test('逐题 03：知识问题读取证据后回复，不产生只读副作用', async ({ page }) => {
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
});
