import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';

const run = promisify(execFile);
const terminal = process.env.PLATFORM_TLI_EXECUTABLE;
const python = process.env.PLATFORM_API_PYTHON;
const credentials = process.env.PLATFORM_CASE_CREDENTIALS_FILE;
const inputVersion = process.env.PLATFORM_INTERACTION_INPUT_VERSION;
const title = '候选评价 JSON 或阻止标记';

test.use({ trace: 'off', actionTimeout: 20_000 });

test('WebUI, TLI and installed SDK share one confirmed Gateway workflow', async ({ page, baseURL }, info) => {
  test.skip(!terminal || !python || !credentials || !inputVersion, 'Requires the installed clients and the local synthetic case');
  test.setTimeout(240_000);
  expect(['127.0.0.1', 'localhost']).toContain(new URL(baseURL!).hostname);
  const account = Object.fromEntries(readFileSync(credentials!, 'utf8').split(/\r?\n/)
    .filter(line => line.includes('='))
    .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
  const login = await page.request.post('/api/v12/auth/login', {
    data: { username: account.username, password: account.password },
  });
  expect(login.status()).toBe(200);
  const { csrfToken } = await login.json();
  await page.addInitScript(value => sessionStorage.setItem('platform.v12.auth.csrf', value), csrfToken);
  const headers = { 'X-CSRF-Token': csrfToken };
  const issued = await page.request.post('/api/v12/auth/tokens', {
    headers, data: { name: '桌面三端验收', expiresDays: 1 },
  });
  expect(issued.status()).toBe(201);
  const { token, tokenId } = await issued.json();
  const env: NodeJS.ProcessEnv = { ...process.env, GROUNDWATER_API_TOKEN: token };
  delete env.PYTHONPATH;
  const cli = async (...args: string[]) => {
    const { stdout } = await run(terminal!, ['--origin', baseURL!, ...args], { env, timeout: 30_000 });
    return stdout;
  };
  const sdk = async (script: string, ...args: string[]) => {
    const { stdout } = await run(python!, ['-c', script, baseURL!, ...args], { env, timeout: 30_000 });
    return JSON.parse(stdout);
  };
  const sdkSetup = `import json, os, sys
from groundwater_api_client import GroundwaterClient
with GroundwaterClient(sys.argv[1], api_token=os.environ["GROUNDWATER_API_TOKEN"]) as client:
    session_id = sys.argv[2]
`;
  let sessionId = '';
  try {
    const catalog = JSON.parse(await cli('datasets', '--query', title));
    const dataset = catalog.items.find((item: { currentVersion: string }) => item.currentVersion === inputVersion);
    expect(dataset).toMatchObject({ title, currentVersion: inputVersion });
    const created = JSON.parse(await cli('new'));
    sessionId = created.sessionId;
    expect(created.projectId).toBe('groundwater-demo');
    await page.goto(`/#/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText(/(项目|实时更新)已连接/);
    await page.getByRole('button', { name: '数据库', exact: true }).click();
    const drawer = page.getByTestId('database-workbench');
    await drawer.getByPlaceholder('标题、说明或标识').fill(title);
    await drawer.locator('.database-item').filter({ hasText: dataset.datasetId }).click();
    await expect(drawer.getByLabel('不可变版本')).toHaveValue(inputVersion!);
    await drawer.getByRole('button', { name: '附加精确版本' }).click();
    await expect(page.getByTestId('pending-attachments')).toContainText(title);
    const args = { assessmentVersionId: inputVersion };
    await page.getByRole('textbox', { name: '消息输入框' }).fill(
      `请运行腰站堡模拟案例（SYNTHETIC_TEST_ONLY）的综合评价。使用工具 run_source_sustainability，只采用指定精确版本，先提出执行方案，等我确认后执行。参数：${JSON.stringify(args)}`,
    );
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await expect(page.getByTestId('confirm-execution')).toBeEnabled({ timeout: 120_000 });
    await page.screenshot({ path: info.outputPath('shared-confirmation.png') });

    const status = JSON.parse(await cli('--session', sessionId, 'status'));
    expect(status.confirmation).toMatchObject({
      status: 'PENDING',
      actions: [{ modelId: 'groundwater.source-sustainability', arguments: args }],
    });
    expect(status.workflowRunIds).toEqual([]);
    const confirmation = status.confirmation.confirmationId;
    const approved = await sdk(sdkSetup + `    current = client.session(session_id)
    confirmation_id, version_id = sys.argv[3:5]
    assert current.confirmation and current.confirmation["confirmationId"] == confirmation_id
    assert current.confirmation["status"] == "PENDING" and not current.workflow_run_ids
    (action,) = current.confirmation["actions"]
    assert action["modelId"] == "groundwater.source-sustainability"
    assert {ref["versionId"] for ref in action["inputReferences"].values()} == {version_id}
    reference = client.input_reference(version_id)
    candidate = json.loads(b"".join(client.download_object(version_id, reference.object_id)))
    assert candidate["synthetic_example"] is True
    cursor = client.history(session_id).event_cursor
    response = client.confirm(session_id, confirmation_id)
    print(json.dumps({"sessionId": response.session_id, "cursor": cursor}))
`, sessionId, confirmation, inputVersion!);
    expect(approved.sessionId).toBe(sessionId);
    const card = page.getByTestId('result-card');
    await expect(card).toHaveAttribute('data-status', 'SUCCEEDED', { timeout: 120_000 });
    await expect(card.getByRole('link', { name: '下载', exact: true })).toHaveCount(3);
    const result = await sdk(sdkSetup + `    state = client.session(session_id)
    (workflow_id,) = state.workflow_run_ids
    print(json.dumps({"workflowId": workflow_id, "outputs": state.remote_outputs}))
`, sessionId);
    expect(await card.getAttribute('data-workflow-run-id')).toBe(result.workflowId);
    expect(result.outputs.map((item: { outputName: string }) => item.outputName).sort()).toEqual([
      'sustainability-result', 'sustainability-result-file', 'sustainability-run-receipt',
    ]);
    const events = await sdk(sdkSetup + `    from contextlib import closing
    cursor, workflow_id = int(sys.argv[3]), sys.argv[4]
    buffer = b""
    observed = []
    with closing(client.events(session_id, cursor=cursor)) as stream:
        for chunk in stream:
            buffer += chunk
            while b"\\n\\n" in buffer:
                frame, buffer = buffer.split(b"\\n\\n", 1)
                fields = dict(line.split(b":", 1) for line in frame.splitlines() if b":" in line)
                if b"id" not in fields or b"data" not in fields:
                    continue
                item = json.loads(fields[b"data"])
                if item["data"].get("workflowRunId") != workflow_id:
                    continue
                observed.append({"id": int(fields[b"id"]), "type": fields[b"event"].strip().decode()})
                if observed[-1]["type"] == "workflow.succeeded":
                    print(json.dumps(observed))
                    sys.exit(0)
    raise RuntimeError("workflow.succeeded was not observed")
`, sessionId, String(approved.cursor), result.workflowId);
    expect(events.map((event: { type: string }) => event.type)).toEqual(expect.arrayContaining([
      'workflow.queued', 'workflow.succeeded',
    ]));
    expect(events.every((event: { id: number }) => event.id > approved.cursor)).toBe(true);
    const listed = JSON.parse(await cli('--session', sessionId, 'results'));
    expect(listed.items).toMatchObject([{ workflowRunId: result.workflowId }]);
    for (const output of result.outputs) {
      const path = info.outputPath(`${output.outputName}.json`);
      await cli('download', output.versionId, output.objectId, path);
      const bytes = readFileSync(path);
      expect(bytes.length).toBe(output.sizeBytes);
      expect('sha256:' + createHash('sha256').update(bytes).digest('hex')).toBe(output.checksum);
      if (output.outputName === 'sustainability-result') {
        expect(JSON.parse(bytes.toString('utf8'))).toMatchObject({ synthetic_example: true, final_grade: 'II' });
      }
    }
    await page.reload();
    await expect(card).toHaveAttribute('data-status', 'SUCCEEDED');
    await page.screenshot({ path: info.outputPath('shared-result.png') });
    writeFileSync(info.outputPath('interaction-verification.json'), JSON.stringify({
      status: 'PASS', sessionId, confirmation, workflowId: result.workflowId, inputVersion,
      outputs: result.outputs, resumedAfterEvent: approved.cursor, events,
    }, null, 2));
  } finally {
    const revoked = await page.request.delete(`/api/v12/auth/tokens/${tokenId}`, { headers });
    expect(revoked.status()).toBe(204);
  }
});
