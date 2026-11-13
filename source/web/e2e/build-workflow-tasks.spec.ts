import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataRef, historyFixture, sessionResponseFixture, sessionResultsFixture, sessionSummaryFixture, taskFixture } from '../src/test-fixtures';
import type { Confirmation } from '../src/types';

const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));

// This production-bundle interaction test controls service snapshots and event
// delivery. The separate Compose owner exercises the real SSE transport.
async function controlledEvents(page: Page) {
  await page.addInitScript(() => {
    class SessionEvents extends EventTarget {
      static streams: SessionEvents[] = [];
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      closed = false;
      constructor(public url: string) {
        super(); SessionEvents.streams.push(this);
        queueMicrotask(() => { if (!this.closed) this.onopen?.(); });
      }
      close() { this.closed = true; }
    }
    Object.assign(window, { EventSource: SessionEvents, taskEventStreams: SessionEvents.streams });
  });
}

async function emit(page: Page, type: string, id: number) {
  await page.evaluate(({ type, id }) => {
    const streams = (window as unknown as { taskEventStreams: { url: string; closed: boolean; dispatchEvent: (event: Event) => boolean }[] }).taskEventStreams;
    for (const stream of streams.filter(item => !item.closed && item.url.includes('/events?'))) {
      stream.dispatchEvent(new MessageEvent(type, { lastEventId: String(id), data: JSON.stringify({ type, data: {} }) }));
    }
  }, { type, id });
}

test('desktop recovery retries safely, confirms the failed scope, tracks publishing and restores the latest attempt', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await controlledEvents(page);
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  let phase: 'failed' | 'confirmation' | 'executing' | 'publishing' | 'complete' = 'failed';
  let cursor = 10, resultReads = 0, confirmPosts = 0;
  const nodeReads: string[] = [];
  const recoveryPosts: { path: string; body: Record<string, unknown> }[] = [];
  let releaseRecovery!: () => void;
  const recoveryResponse = new Promise<void>(resolve => { releaseRecovery = resolve; });
  const source = taskFixture({ workflowRunId: 'failed-source', agentRunId: 'agent-2', goalRevision: 2, toolCallId: 'source',
    position: 1, status: 'TIMED_OUT', stage: null, isLatestAttempt: true, attempt: 1, errorCode: 'WORKFLOW_TIMEOUT' });
  const downstream = taskFixture({ ...source, workflowRunId: 'blocked-downstream', toolCallId: 'downstream', position: 2,
    status: 'FAILED', blockedByWorkflowRunId: source.workflowRunId });
  const recovery: Confirmation = {
    confirmationId: 'recovery-confirmation', agentRunId: 'agent-2', goalRevision: 2, status: 'PENDING', kind: 'RECOVERY',
    decision: null, decisionState: null, reason: '恢复未完成的两项分析',
    actions: [source, downstream].map(task => ({ toolCallId: task.toolCallId, tool: 'run_summary', modelId: task.modelId,
      modelVersion: task.modelVersion, workflowId: task.workflowId, workflowVersion: task.workflowVersion, arguments: {} })),
  };
  const output = dataRef({ versionId: 'recovered-output-version', outputName: 'summary' });
  const history = () => {
    const retried = ['executing', 'publishing', 'complete'].includes(phase);
    const retries = [source, downstream].map((item, index) => taskFixture({ ...item,
      workflowRunId: index === 0 ? 'recovered-source' : 'recovered-downstream', confirmationId: recovery.confirmationId,
      isLatestAttempt: true, attempt: 2, errorCode: null, blockedByWorkflowRunId: null,
      status: phase === 'complete' ? 'SUCCEEDED' : index === 0 ? 'RUNNING' : 'QUEUED',
      stage: phase === 'complete' ? null : index === 0 ? phase === 'publishing' ? 'PUBLISHING' : 'EXECUTING' : 'QUEUED',
      outputCount: phase === 'complete' && index === 0 ? 1 : 0,
    }));
    return historyFixture({ sessionId: 'workflow-session', goalRevision: 2, eventCursor: cursor,
      items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '历史目标' }, { messageId: 2, goalRevision: 2, role: 'user', content: '恢复本轮工作流' }],
      turns: [
        { goalRevision: 1, agentRunId: 'agent-1', status: 'FAILED', executionTasks: [taskFixture({ workflowRunId: 'historical-failure', status: 'FAILED', isLatestAttempt: true })] },
        { goalRevision: 2, agentRunId: 'agent-2', status: phase === 'complete' ? 'COMPLETE' : retried ? 'WAITING_EXECUTION' : phase === 'confirmation' ? 'WAITING_CONFIRMATION' : 'FAILED',
          confirmations: phase === 'failed' ? [] : [{ ...recovery, status: retried ? 'CONFIRMED' : 'PENDING' }],
          executionTasks: [{ ...source, isLatestAttempt: !retried }, { ...downstream, isLatestAttempt: !retried }, ...(retried ? retries : [])] },
      ],
    });
  };
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8') }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (url.pathname.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId: 'workflow-session', title: '工作流恢复', goalRevision: 2 })], nextCursor: null } });
    if (url.pathname.endsWith('/messages')) return route.fulfill({ json: history() });
    if (url.pathname.endsWith('/nodes')) {
      const runId = url.pathname.split('/').at(-2)!;
      nodeReads.push(runId);
      const original = runId === 'failed-source';
      if (original && nodeReads.length === 1) return route.fulfill({ status: 503, json: { code: 'WORKFLOW_NODE_DIAGNOSTICS_UNAVAILABLE' } });
      return route.fulfill({ json: { workflowRunId: runId, availability: 'AVAILABLE', nodes: [{
        nodeId: original ? 'summarize-original' : 'summarize-recovered', operatorId: 'table.summary', runtime: 'native',
        status: original ? 'FAILED' : phase === 'complete' ? 'SUCCEEDED' : 'RUNNING', attemptCount: original ? 2 : 1,
        startedAt: null, finishedAt: null, errorCode: original ? 'WORKER_LOST' : null,
      }] } });
    }
    if (url.pathname.endsWith('/results')) {
      resultReads++;
      return route.fulfill({ json: sessionResultsFixture(phase === 'complete' ? [{ goalRevision: 2, agentRunId: 'agent-2', toolCallId: 'source', modelId: source.modelId,
        status: 'COMPLETE', workflowRunId: 'recovered-source', remoteOutputs: [output] }] : [], { sessionId: 'workflow-session' }) });
    }
    if (url.pathname.endsWith('/recoveries')) {
      recoveryPosts.push({ path: url.pathname, body: route.request().postDataJSON() });
      if (recoveryPosts.length === 1) return route.abort('connectionreset');
      await recoveryResponse;
      phase = 'confirmation'; cursor++;
      return route.fulfill({ status: 201, json: sessionResponseFixture({ sessionId: 'workflow-session', goalRevision: 2 }) });
    }
    if (url.pathname.endsWith('/confirm')) {
      confirmPosts++; expect(route.request().postDataJSON()).toEqual({ confirmationId: recovery.confirmationId });
      phase = 'executing'; cursor++;
      return route.fulfill({ status: 202, json: sessionResponseFixture({ sessionId: 'workflow-session', goalRevision: 2 }) });
    }
    throw new Error(`Unexpected workflow test request: ${url.pathname}`);
  });
  await page.goto('/#/sessions/workflow-session');
  const workspace = page.getByTestId('workspace-panel');
  await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '运行' }).click();
  const recover = workspace.getByRole('button', { name: '准备恢复未完成步骤' });
  await expect(recover).toBeEnabled();
  await expect(workspace).toContainText('从第 2 项开始恢复 2 项分析。新的恢复批次需要确认。');
  await expect(page.locator('[data-workflow-run-id="blocked-downstream"]')).toContainText('因前项失败未执行');
  expect(nodeReads).toEqual([]);
  const failedNodes = workspace.locator('[data-execution-run-id="failed-source"]');
  await failedNodes.getByText('节点详情', { exact: true }).click();
  await expect(failedNodes.getByRole('alert')).toBeVisible();
  await failedNodes.getByRole('button', { name: '重新加载' }).click();
  await expect(failedNodes).toContainText('summarize-original');
  await expect(failedNodes).toContainText('尝试 2 次');
  await expect(failedNodes).toContainText('WORKER_LOST');
  await page.screenshot({ path: info.outputPath('workflow-failed-scope-desktop.png'), fullPage: true });
  await recover.click();
  await expect(workspace.getByText('操作结果待核对')).toBeVisible();
  await workspace.getByRole('button', { name: '重试同一操作' }).click();
  await expect.poll(() => recoveryPosts.length).toBe(2);
  expect(recoveryPosts[1]).toEqual(recoveryPosts[0]);
  await expect(recover).toBeDisabled();
  expect(recoveryPosts.every(item => item.path.endsWith('/failed-source/recoveries') && item.body.expectedGoalRevision === 2)).toBe(true);
  releaseRecovery();
  const confirmation = page.getByTestId('confirmation-card');
  await expect(confirmation).toContainText('失败恢复批次');
  expect(confirmPosts).toBe(0);
  await confirmation.getByRole('button', { name: /^确认执行修订 / }).click();
  const recovered = page.locator('[data-workflow-run-id="recovered-source"]');
  await expect(recovered).toContainText('正在执行');
  await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '运行' }).click();
  const recoveredNodes = workspace.locator('[data-execution-run-id="recovered-source"]');
  await recoveredNodes.getByText('节点详情', { exact: true }).click();
  await expect(recoveredNodes).toContainText('summarize-recovered');
  await expect(recoveredNodes).not.toContainText('summarize-original');
  const readsBeforePublishing = resultReads;
  phase = 'publishing'; cursor++;
  await emit(page, 'workflow.stage_changed', cursor);
  await expect(workspace).toContainText('正在发布结果');
  expect(resultReads).toBe(readsBeforePublishing);
  phase = 'complete'; cursor++;
  await emit(page, 'workflow.recovery_completed', cursor);
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await expect(recovered.getByRole('link', { name: '下载', exact: true })).toHaveAttribute('href', '/api/v12/data/versions/recovered-output-version/objects/object-1/content');
  await expect(recovered).toContainText('已通过第 2 次执行恢复');
  await page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '运行' }).click();
  await recoveredNodes.getByText('节点详情', { exact: true }).click();
  await expect(recoveredNodes).toContainText('节点已完成');
  await failedNodes.getByText('节点详情', { exact: true }).click();
  await expect(failedNodes).toContainText('summarize-original');
  await expect(workspace.getByRole('button', { name: /准备恢复未完成步骤/ })).toHaveCount(0);
  await expect(page.locator('[data-workflow-run-id="failed-source"]').getByRole('link', { name: '下载' })).toHaveCount(0);
  expect(confirmPosts).toBe(1);
  await page.reload();
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await expect(page.locator('[data-workflow-run-id="recovered-source"]').getByRole('link', { name: '下载', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /^确认执行修订 / })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /准备恢复未完成步骤/ })).toHaveCount(0);
  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: info.outputPath('workflow-recovered-desktop.png'), fullPage: true });
});
