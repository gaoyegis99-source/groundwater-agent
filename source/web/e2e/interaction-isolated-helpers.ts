import { execFile } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { promisify } from 'node:util';
import { expect, type Page } from '@playwright/test';
import type { Confirmation, ResourceReference, Session } from '../src/types';

const run = promisify(execFile);
const title = '六模型观测表';

export interface InteractionSession extends Session {
  agentSteps: string[];
  workflowRunIds: string[];
  remoteOutputs: ResourceReference[];
}

function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} for the isolated interaction gate.`);
  return value;
}

export async function isolatedInteractionClients(page: Page, baseURL: string | undefined) {
  const gateway = new URL(baseURL ?? required('PLATFORM_BASE_URL'));
  expect(['127.0.0.1', 'localhost']).toContain(gateway.hostname);
  expect(gateway.port).not.toBe('28186');
  expect(gateway.port).not.toBe('');
  const origin = gateway.origin;
  const terminal = required('PLATFORM_TLI_EXECUTABLE');
  const interpreter = required('PLATFORM_API_PYTHON');
  const inputVersion = required('PLATFORM_INTERACTION_INPUT_VERSION');
  const credentials = readFileSync(required('PLATFORM_CASE_CREDENTIALS_FILE'), 'utf8');
  const account = Object.fromEntries(credentials.split(/\r?\n/).filter(line => line.includes('='))
    .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
  const env: NodeJS.ProcessEnv = { ...process.env, GROUNDWATER_API_TOKEN: required('GROUNDWATER_API_TOKEN') };
  delete env.PYTHONPATH;
  delete env.PYTHONHOME;

  const login = await page.request.post('/api/v12/auth/login', {
    data: { username: account.username, password: account.password },
  });
  expect(login.status(), 'browser cookie login').toBe(200);
  const { csrfToken } = await login.json();
  expect(typeof csrfToken).toBe('string');
  await page.addInitScript(value => sessionStorage.setItem('platform.v12.auth.csrf', value), csrfToken);

  return {
    inputVersion,
    async cli(...args: string[]) {
      return (await run(terminal, ['--origin', origin, ...args], { env, timeout: 150_000 })).stdout;
    },
    async python<T>(script: string, ...args: string[]): Promise<T> {
      const { stdout } = await run(interpreter, ['-I', '-B', '-c', script, origin, ...args], {
        env, timeout: 150_000,
      });
      return JSON.parse(stdout) as T;
    },
    async get<T>(path: string): Promise<T> {
      const response = await page.request.get(path);
      expect(response.status(), path).toBe(200);
      return await response.json() as T;
    },
  };
}

type Clients = Awaited<ReturnType<typeof isolatedInteractionClients>>;

export async function openSummaryProposal(page: Page, clients: Clients, marker: string) {
  const versionId = clients.inputVersion;
  const catalog = JSON.parse(await clients.cli('datasets', '--query', title));
  const dataset = (catalog.items as { title: string; currentVersion: string; datasetId: string }[])
    .find(item => item.currentVersion === versionId);
  expect(dataset).toMatchObject({ title, currentVersion: versionId });
  const { sessionId, projectId } = JSON.parse(await clients.cli('new')) as InteractionSession;
  expect(projectId).toBe('groundwater-demo');
  await page.goto(`/#/sessions/${sessionId}`);
  await expect(page.getByTestId('connection-status')).toHaveText(/(项目|实时更新)已连接/);
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  const drawer = page.getByTestId('database-workbench');
  await drawer.getByPlaceholder('标题、说明或标识').fill(title);
  await drawer.locator('.database-item').filter({ hasText: dataset!.datasetId }).click();
  await expect(drawer.getByLabel('不可变版本')).toHaveValue(versionId);
  await drawer.getByRole('button', { name: '附加精确版本' }).click();
  await expect(page.getByTestId('pending-attachments')).toContainText(title);

  const args = { versionId, column: 'hydraulic_head_m', axis: 'columns' };
  const message = `[Evaluation case: ${marker}]\n[Model execution] ${JSON.stringify({
    tool: 'run_summary', arguments: args,
  })}`;
  await page.getByRole('textbox', { name: '消息输入框' }).fill(message);
  const submitted = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/v12/sessions/${sessionId}/messages`
      && response.request().method() === 'POST');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  const headers = await response.request().allHeaders();
  expect(Boolean(headers.authorization), 'browser must use the authenticated cookie').toBe(false);
  expect(Boolean(headers.cookie)).toBe(true);
  expect(Boolean(headers['x-csrf-token'])).toBe(true);
  const receipt = await response.json() as { goalRevision: number };
  expect(response.request().postDataJSON().inputReferences).toMatchObject([{ versionId }]);
  await expect(page.getByTestId('confirm-execution')).toBeEnabled({ timeout: 60_000 });

  const status = JSON.parse(await clients.cli('--session', sessionId, 'status')) as InteractionSession;
  expect(status.goalRevision).toBe(receipt.goalRevision);
  expect(status.confirmation).toMatchObject({
    status: 'PENDING', actions: [{ tool: 'run_summary', modelId: 'groundwater.summary', arguments: args }],
  });
  expect(status.workflowRunIds).toEqual([]);
  expect(status.remoteOutputs).toEqual([]);
  const confirmation = status.confirmation as Confirmation;
  const history = await clients.get<{ eventCursor: number }>(`/api/v12/sessions/${sessionId}/messages`);
  return {
    sessionId, projectId, versionId, arguments: args, confirmation, confirmationId: confirmation.confirmationId,
    goalRevision: receipt.goalRevision, cursor: history.eventCursor,
  };
}
