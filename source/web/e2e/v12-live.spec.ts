import { createHash, randomUUID } from 'node:crypto';
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

async function proposeSummary(page: Page, sessionId: string, marker: string) {
  const versionId = await summaryInput(page);
  const message = `[Evaluation case: ${marker}]\n[Model execution] ${JSON.stringify({
    tool: 'run_summary', arguments: { versionId, column: 'hydraulic_head_m', axis: 'columns' },
  })}`;
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
  await expect(drawer).toContainText(createHash('sha256').update(bytes).digest('hex'));
  await page.screenshot({ path: info.outputPath('03-uploaded-version.png'), fullPage: true });
});

test('04 execution proposal confirms, publishes, downloads and restores results', async ({ page }, info) => {
  const outputLabel = (await productUi(page)).outputs.summary.label;
  const id = await newChat(page);
  const inputVersion = await proposeSummary(page, id, `frontend-confirm-${randomUUID()}`);
  const confirmation = page.getByTestId('confirmation-card');
  await expect(confirmation).toContainText(inputVersion);
  await expect(confirmation).toContainText('确认后才会执行');
  const confirmed = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v12/sessions/${id}/confirm`
      && response.request().method() === 'POST');
  await page.getByTestId('confirm-execution').click();
  expect((await confirmed).status()).toBe(202);
  const resultCard = page.getByTestId('result-card');
  await expect(resultCard).toBeVisible({ timeout: 90_000 });
  const downloads = resultCard.getByRole('link', { name: '下载' });
  await expect(downloads).toHaveCount(2);
  const download = page.waitForEvent('download');
  await downloads.first().click();
  expect((await download).suggestedFilename()).toBeTruthy();
  await resultCard.getByRole('button', { name: outputLabel, exact: true }).click();
  const workspace = page.getByTestId('workspace-panel');
  await expect(workspace.getByRole('heading', { name: outputLabel })).toBeVisible();
  await expect(workspace.getByRole('heading', { name: 'Summary' })).toHaveCount(0);
  await expect(workspace.getByText('数据集', { exact: true })).toBeVisible();
  await expect(workspace.getByText('数据版本', { exact: true })).toBeVisible();
  await expect(workspace.getByText('语义版本', { exact: true })).toBeVisible();
  await expect(workspace).toContainText('血缘输入');
  await expect(workspace).toContainText(inputVersion);
  await page.reload();
  await expect(page.getByTestId('result-card')).toBeVisible();
  await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('04-confirmed-result.png'), fullPage: true });
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
  await expect(page.getByTestId('confirmation-card')).toContainText('已拒绝');
  await page.screenshot({ path: info.outputPath('05-rejected.png'), fullPage: true });
});
