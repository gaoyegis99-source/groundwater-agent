import { expect, test, type Page } from '@playwright/test';

const project = 'groundwater-demo';

async function readSession(page: Page, sessionId: string) {
  const response = await page.request.get(
    `/api/v12/sessions/${sessionId}?projectId=${project}`,
  );
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

async function readLatestTurn(page: Page, sessionId: string) {
  const response = await page.request.get(
    `/api/v12/sessions/${sessionId}/messages?projectId=${project}`,
  );
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
  const history = await response.json();
  return history.turns?.at(-1);
}

test('natural language runs the Yaozhanbao workflow and explains its outputs', async ({ page }, info) => {
  test.setTimeout(480_000);
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toContainText('项目已连接');

  const created = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v12/sessions'
      && response.request().method() === 'POST');
  await page.getByRole('button', { name: '新建对话', exact: true }).click();
  const sessionId = ((await created).json() as Promise<{ sessionId: string }>);
  const { sessionId: id } = await sessionId;

  await page.getByRole('textbox', { name: '消息输入框' }).fill(
    '请运行腰站堡迁移学习开发案例，优先使用系统中的标准化资料。',
  );
  await page.getByRole('button', { name: '发送消息', exact: true }).click();

  const confirmation = page.getByTestId('confirmation-card');
  await expect.poll(async () => {
    const session = await readSession(page, id);
    const state = session.agentSteps.at(-1);
    if (['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(state)) {
      const turn = await readLatestTurn(page, id);
      throw new Error(
        `Agent ended before confirmation: ${state} ${turn?.errorCode ?? ''}`,
      );
    }
    return state;
  }, {
    timeout: 60_000,
    intervals: [500, 1_000],
  }).toBe('WAITING_CONFIRMATION');
  await expect(confirmation).toContainText('腰站堡迁移学习', { timeout: 60_000 });
  await expect(confirmation).toContainText('确认后才会执行');
  const pending = await readSession(page, id);
  expect(pending.agentSteps.at(-1)).toBe('WAITING_CONFIRMATION');
  expect(pending.confirmation.actions[0].modelId).toBe(
    'groundwater.transfer-learning-analysis',
  );
  expect(Object.keys(pending.confirmation.actions[0].inputReferences)).toHaveLength(19);

  await page.getByTestId('confirm-execution').click();
  await expect.poll(async () => {
    const session = await readSession(page, id);
    const state = session.agentSteps.at(-1);
    if (['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(state)) {
      const turn = await readLatestTurn(page, id);
      const latestAttempt = turn?.executionTasks
        ?.filter((task: { isLatestAttempt?: boolean }) => task.isLatestAttempt)
        ?.at(-1);
      throw new Error(
        `Agent ended after confirmation: ${state} ${latestAttempt?.errorCode ?? turn?.errorCode ?? ''}`,
      );
    }
    return state;
  }, {
    timeout: 360_000,
    intervals: [1_000],
  }).toBe('COMPLETE');

  const completed = await readSession(page, id);
  expect(completed.workflowRunIds).toHaveLength(1);
  await expect(page.getByTestId('result-card')).toContainText('已完成');
  await expect(page.getByTestId('result-card')).toContainText('查看全部 25 个结果');
  await expect(page.getByTestId('message-list')).toContainText('发布了 25 项可核验成果');
  await expect(page.getByTestId('message-list')).toContainText(
    '这是开发调试案例；模拟或历史标准化数据不代表当前真实业务状况',
  );

  await page.getByRole('tab', { name: /成果/ }).click();
  await expect(page.getByRole('tabpanel')).toContainText('评价指标');
  await page.screenshot({
    path: info.outputPath('yaozhanbao-workflow-complete.png'),
    fullPage: true,
  });
});
