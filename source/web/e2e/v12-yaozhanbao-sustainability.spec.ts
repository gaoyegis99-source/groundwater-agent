import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page, type TestInfo } from '@playwright/test';

const caseDirectory = process.env.PLATFORM_SYNTHETIC_CASE_DIR;
const credentials = process.env.PLATFORM_CASE_CREDENTIALS_FILE;
const fixedProvider = process.env.PLATFORM_CASE_FIXED_PROVIDER === '1';
const project = 'groundwater-demo';

test.use({ trace: 'off', actionTimeout: 20_000 });

type Reference = { versionId: string; objectId: string; checksum: string; outputName: string; sizeBytes: number };
type Session = {
  goalRevision: number;
  agentSteps: string[];
  workflowRunIds: string[];
  confirmation?: {
    confirmationId: string;
    status: string;
    actions: { modelId: string; arguments: Record<string, unknown>; inputReferences: Record<string, Reference> }[];
  };
  remoteOutputs: Reference[];
};

const digest = (bytes: Buffer) => 'sha256:' + createHash('sha256').update(bytes).digest('hex');

async function get<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(path);
  expect(response.status(), path).toBe(200);
  return response.json() as Promise<T>;
}

async function login(page: Page, baseURL: string) {
  if (!credentials) return;
  expect(['127.0.0.1', 'localhost'].includes(new URL(baseURL).hostname)).toBe(true);
  const values = Object.fromEntries(readFileSync(credentials, 'utf8').split(/\r?\n/)
    .filter(line => line.includes('='))
    .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
  const response = await page.request.post('/api/v12/auth/login', {
    data: { username: values.username, password: values.password },
  });
  expect(response.status(), '本地研究工作区登录').toBe(200);
  const { csrfToken } = await response.json();
  await page.addInitScript(token => sessionStorage.setItem('platform.v12.auth.csrf', token), csrfToken);
}

async function importFile(page: Page, filename: string, checksum: string, keepAttached: boolean) {
  const labels: Record<string, string> = {
    'simulation-evidence.json': '水量证据', 'monitoring-evidence.json': '监测证据',
    'management-evidence.json': '管理证据', 'water-budget.csv': '月度水量台账',
    'water-quality.csv': '水质点记录', 'well-recovery.csv': '单井恢复记录',
    'well-test.json': '井试假设', 'management-assumptions.json': '管理假设',
  };
  const title = `腰站堡模拟 · ${labels[filename]} · ${checksum.slice(7, 15)}`;
  const find = async () => {
    const listing = await get<{ items: { title: string; currentVersion: string }[] }>(
      page, `/api/v12/data/datasets?query=${encodeURIComponent(title)}&limit=20`,
    );
    return listing.items.find(item => item.title === title);
  };
  let version = await find();
  if (!version) {
    await page.locator('input[type=file]').setInputFiles(join(caseDirectory!, filename));
    const upload = page.getByRole('dialog', { name: '添加资料' });
    await upload.getByLabel('资料名称').fill(title);
    await upload.getByPlaceholder('简述文件内容').fill('SYNTHETIC_TEST_ONLY · 腰站堡模拟案例 · 2026 全年假设');
    await upload.getByRole('button', { name: '上传并附加' }).click();
    await expect(upload).toHaveCount(0, { timeout: 60_000 });
    version = await find();
  } else if (keepAttached) {
    await page.getByRole('button', { name: '数据库', exact: true }).click();
    const drawer = page.getByTestId('database-workbench');
    await drawer.getByPlaceholder('标题、说明或标识').fill(title);
    await drawer.locator('.database-item').filter({ hasText: title }).click();
    await drawer.getByRole('button', { name: '附加精确版本' }).click();
    await expect(drawer).toHaveCount(0);
  }
  expect(version, title).toBeTruthy();
  const detail = await get<{ objects: { role: string; checksum: string }[] }>(
    page, `/api/v12/data/versions/${version!.currentVersion}`,
  );
  expect(detail.objects.find(object => object.role === 'raw')?.checksum, title).toBe(checksum);
  const chip = page.locator('.attachment-chip').filter({ hasText: title });
  if (keepAttached) {
    await expect(chip).toHaveCount(1);
  } else if (await chip.count()) {
    await chip.getByRole('button', { name: `移除附件 ${title}` }).click();
  }
  return { title, versionId: version!.currentVersion, checksum };
}

async function viewData(page: Page, title: string, versionId: string, text: string, info: TestInfo) {
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  const drawer = page.getByTestId('database-workbench');
  await drawer.getByPlaceholder('标题、说明或标识').fill(title);
  await drawer.locator('.database-item').filter({ hasText: title }).click();
  await expect(drawer.getByLabel('不可变版本')).toHaveValue(versionId);
  const preview = drawer.getByLabel('数据预览');
  if (text === '2026-01') {
    await expect(preview.getByRole('cell', { name: text, exact: true })).toBeVisible();
  } else {
    await preview.getByRole('textbox', { name: '搜索 JSON' }).fill(text);
    await expect(preview.locator('pre')).toContainText(text);
  }
  await page.screenshot({ path: info.outputPath(`data-${text === '2026-01' ? 'table' : 'json'}.png`) });
  await drawer.getByRole('button', { name: '关闭数据库' }).click();
}

test('desktop imports and completes the synthetic Yaozhanbao assessment', async ({ page, baseURL }, info) => {
  test.setTimeout(900_000);
  test.skip(!caseDirectory, 'Set PLATFORM_SYNTHETIC_CASE_DIR to the generated case directory');
  const bundle = JSON.parse(readFileSync(join(caseDirectory!, 'case.json'), 'utf8'));
  expect(bundle.dataNature).toBe('SYNTHETIC_TEST_ONLY');
  await login(page, baseURL!);
  await page.addInitScript(() => {
    const observations: { id: string; status: string; label: string }[] = [];
    Object.assign(window, { caseProgress: observations });
    const capture = () => {
      document.querySelectorAll<HTMLElement>('[data-workflow-run-id]').forEach(card => {
        const { workflowRunId: id, status } = card.dataset;
        const label = card.getAttribute('aria-label') ?? '';
        if (id && status && !observations.some(item => item.id === id && item.status === status && item.label === label)) {
          observations.push({ id, status, label });
        }
      });
    };
    document.addEventListener('DOMContentLoaded', () => {
      new MutationObserver(capture).observe(document.body, { childList: true, subtree: true, attributes: true });
      capture();
    }, { once: true });
  });
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toContainText('项目已连接');
  const created = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v12/sessions' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '新建对话', exact: true }).click();
  const sessionId = (await (await created).json()).sessionId as string;
  await expect(page).toHaveURL(new RegExp(`#/sessions/${sessionId}$`));
  const readSession = () => get<Session>(page, `/api/v12/sessions/${sessionId}?projectId=${project}`);
  const imported: Record<string, Awaited<ReturnType<typeof importFile>>> = {};
  const sources = [...bundle.rawInputs, ...Object.values(bundle.evidence)] as { path: string; sha256: string }[];
  for (const source of sources) {
    expect(digest(readFileSync(join(caseDirectory!, source.path)))).toBe(source.sha256);
    imported[source.path] = await importFile(page, source.path, source.sha256, source.path.endsWith('-evidence.json'));
  }
  writeFileSync(info.outputPath('imported-versions.json'), JSON.stringify({ sessionId, imported }, null, 2));
  const budget = imported['water-budget.csv'];
  const monitoring = imported['monitoring-evidence.json'];
  await viewData(page, budget.title, budget.versionId, '2026-01', info);
  await viewData(page, monitoring.title, monitoring.versionId, 'class_iii_count_nd', info);

  async function execute(modelId: string, tool: string, args: Record<string, string>, priorRuns: number) {
    const label = priorRuns === 0 ? '生成腰站堡模拟案例的候选综合评价输入' : '运行腰站堡模拟案例的综合评价';
    const request = fixedProvider
      ? `[Evaluation case: yaozhanbao-synthetic-desktop-${priorRuns}]\n[Model execution] ${JSON.stringify({ tool, arguments: args })}`
      : `请${label}。这些都是 SYNTHETIC_TEST_ONLY 模拟资料。使用工具 ${tool}，只采用指定的精确版本，先提出执行方案，等我确认后执行。参数：${JSON.stringify(args)}`;
    await page.getByRole('textbox', { name: '消息输入框' }).fill(request);
    const submitted = page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/v12/sessions/${sessionId}/messages` &&
      response.request().method() === 'POST');
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    const receipt = await submitted;
    expect(receipt.status()).toBe(202);
    const { goalRevision } = await receipt.json() as { goalRevision: number };
    await expect.poll(async () => {
      const current = await readSession();
      if (current.goalRevision !== goalRevision) return 'PREVIOUS_TURN';
      const state = current.agentSteps.at(-1);
      if (['FAILED', 'TIMED_OUT', 'CANCELLED', 'COMPLETE'].includes(state ?? '')) {
        throw new Error(`准备未进入确认: ${modelId} ${state}`);
      }
      return state;
    }, { timeout: 180_000, intervals: [1_000] }).toBe('WAITING_CONFIRMATION');
    const before = await readSession();
    expect(before.workflowRunIds).toHaveLength(0);
    expect(before.confirmation?.status).toBe('PENDING');
    expect(before.confirmation?.actions).toHaveLength(1);
    const action = before.confirmation!.actions[0];
    expect(action.modelId).toBe(modelId);
    expect(action.arguments).toMatchObject(args);
    expect(Object.values(action.inputReferences).map(ref => ref.versionId).sort()).toEqual(Object.values(args).sort());
    const approve = page.getByTestId('confirm-execution');
    await expect(approve).toBeEnabled();
    await page.screenshot({ path: info.outputPath(`confirmation-${priorRuns + 1}.png`) });
    const confirmed = page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/v12/sessions/${sessionId}/confirm` &&
      response.request().method() === 'POST');
    await approve.click();
    const confirmationReceipt = await confirmed;
    expect(confirmationReceipt.request().postDataJSON()).toMatchObject({
      confirmationId: before.confirmation!.confirmationId,
    });
    expect(confirmationReceipt.status()).toBe(202);
    await expect.poll(async () => {
      const current = await readSession();
      if (['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(current.agentSteps.at(-1) ?? '')) {
        throw new Error(`执行失败: ${modelId} ${current.agentSteps.at(-1)}`);
      }
      return current.workflowRunIds.length === 1 ? current.agentSteps.at(-1) : '';
    }, { timeout: 180_000, intervals: [1_000] }).toBe('COMPLETE');
    const completed = await readSession();
    const workflowId = completed.workflowRunIds.at(-1)!;
    const card = page.locator(`[data-workflow-run-id="${workflowId}"]`);
    await expect(card).toHaveAttribute('data-status', 'SUCCEEDED');
    await expect(card.getByRole('link', { name: '下载', exact: true }).first()).toBeVisible();
    return { completed, workflowId, card, goalRevision, confirmationId: before.confirmation!.confirmationId };
  }

  const prepared = await execute('groundwater.sustainability-input-preparation', 'prepare_source_sustainability', {
    simulationEvidenceVersionId: imported['simulation-evidence.json'].versionId,
    monitoringEvidenceVersionId: monitoring.versionId,
    managementEvidenceVersionId: imported['management-evidence.json'].versionId,
  }, 0);
  const candidate = prepared.completed.remoteOutputs.find(item => item.outputName === 'assessment-input-candidate')!;
  expect(candidate).toBeTruthy();
  await prepared.card.locator(`[data-resource-id="${candidate.versionId}"]`).getByRole('button', { name: '作为下一步输入' }).click();
  await expect(page.getByTestId('pending-attachments')).toBeVisible();
  const evaluated = await execute('groundwater.source-sustainability', 'run_source_sustainability', {
    assessmentVersionId: candidate.versionId,
  }, 1);
  const outputs = evaluated.completed.remoteOutputs.filter(output => output.outputName.startsWith('sustainability-'));
  expect(outputs.map(item => item.outputName).sort()).toEqual([
    'sustainability-result', 'sustainability-result-file', 'sustainability-run-receipt',
  ]);
  const downloads: Record<string, any> = {};
  for (const output of outputs) {
    const item = evaluated.card.locator(`[data-resource-id="${output.versionId}"]`);
    const downloading = page.waitForEvent('download');
    await item.getByRole('link', { name: '下载', exact: true }).click();
    const download = await downloading;
    const path = info.outputPath(`${output.outputName}.json`);
    await download.saveAs(path);
    const content = readFileSync(path);
    expect(digest(content)).toBe(output.checksum);
    expect(content.length).toBe(output.sizeBytes);
    downloads[output.outputName] = JSON.parse(content.toString('utf8'));
  }
  const result = downloads['sustainability-result'];
  expect(downloads['sustainability-result-file']).toEqual(result);
  expect(result.synthetic_example).toBe(true);
  expect(result.final_grade).toBe('II');
  expect(result.overall_score_selected).toBeCloseTo(88.37075, 6);
  expect(downloads['sustainability-run-receipt'].inputs.assessment_input.sha256).toBe(candidate.checksum);

  const structured = outputs.find(output => output.outputName === 'sustainability-result')!;
  await evaluated.card.locator(`[data-resource-id="${structured.versionId}"] .output-heading button`).click();
  const workspace = page.getByTestId('workspace-panel');
  await expect(workspace.getByRole('heading', { name: '外部服务返回的结构化评价' })).toBeVisible();
  await workspace.getByRole('textbox', { name: '搜索 JSON' }).fill('final_grade');
  await expect(workspace.locator('.json-workspace pre')).toContainText('"final_grade": "II"');
  await page.screenshot({ path: info.outputPath('evaluation-result.png') });
  const observations = await page.evaluate(() => (window as unknown as {
    caseProgress: { id: string; status: string; label: string }[];
  }).caseProgress);
  for (const id of [prepared.workflowId, evaluated.workflowId]) {
    expect(observations.some(item => item.id === id && ['QUEUED', 'RUNNING'].includes(item.status)), id).toBe(true);
    expect(observations.some(item => item.id === id && item.status === 'SUCCEEDED'), id).toBe(true);
  }
  await page.getByRole('tab', { name: '任务', exact: true }).click();
  await expect(workspace.locator('.workspace-tasks li')).toHaveCount(2);
  const history = await get<{
    turns: { goalRevision: number; executionTasks: { workflowRunId: string; confirmationId: string; status: string }[] }[];
  }>(page, `/api/v12/sessions/${sessionId}/messages`);
  for (const run of [prepared, evaluated]) {
    const turn = history.turns.find(item => item.goalRevision === run.goalRevision);
    expect(turn?.executionTasks).toMatchObject([{
      workflowRunId: run.workflowId, confirmationId: run.confirmationId, status: 'SUCCEEDED',
    }]);
  }
  await page.screenshot({ path: info.outputPath('task-history.png') });
  await page.reload();
  await expect(page.locator(`[data-workflow-run-id="${evaluated.workflowId}"]`)).toHaveAttribute('data-status', 'SUCCEEDED');
  await expect(page.getByTestId('confirm-execution')).toHaveCount(0);
  writeFileSync(info.outputPath('case-verification.json'), JSON.stringify({
    status: 'PASS', baseURL, sessionId, imported, preparation: prepared.workflowId,
    assessment: evaluated.workflowId, observations, outputs, score: result.overall_score_selected,
    grade: result.final_grade, provider: fixedProvider ? 'fixed' : 'configured',
    confirmationIds: [prepared.confirmationId, evaluated.confirmationId],
  }, null, 2));
});
