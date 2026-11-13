import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, historyFixture, replyFixture, sessionResultsFixture, sessionSummaryFixture } from '../src/test-fixtures';

const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));

test('Turn timeline resumes native SSE, merges an older page and keeps scientific content readable', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  let final = false;
  let eventConnections = 0;
  let resumeId: string | undefined;
  const rows = Array.from({ length: 12 }, (_, index) => `| W${index + 1} | ${(12.1 + index / 10).toFixed(1)} | 统一高程基准后比较 |`);
  const markdown = [
    '**水位趋势。** 先核对观测时间、井口高程和缺测区间，再解释井间差异。',
    '对均质介质，沿剖面的达西通量为 $q=-K\\frac{dh}{dx}$。',
    '| 监测井 | 水位（m） | 说明 |\n| --- | ---: | --- |\n' + rows.join('\n'),
    '解释范围限于本次观测井。相邻井出现同步变化时，还需要核查抽水与降水记录。',
  ].join('\n\n');
  const sessionId = 'turn-browser';
  const latest = () => historyFixture({
    sessionId, goalRevision: 2, eventCursor: final ? 12 : 10, nextCursor: 3,
    items: [
      { messageId: 3, role: 'assistant', goalRevision: 1, content: '第一轮的正式答复' },
      { messageId: 4, role: 'user', goalRevision: 2, content: '比较监测井水位，列出计算依据和表格。' },
      ...(final ? [{ messageId: 5, role: 'assistant', goalRevision: 2, content: '线性副本' }] : []),
    ],
    turns: [
      { goalRevision: 1, agentRunId: 'agent-1', reply: replyFixture('第一轮的正式答复') },
      { goalRevision: 2, agentRunId: 'agent-2', status: final ? 'COMPLETE' : 'RUNNING', reply: final ? replyFixture(markdown) : null,
        timing: { createdAt: '2026-10-09T01:00:03Z', startedAt: '2026-10-09T01:00:04Z', finishedAt: final ? '2026-10-09T01:00:09Z' : null } },
    ],
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml', body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId, title: '监测井水位趋势与依据' })], nextCursor: null } });
    if (path.endsWith('/messages') && url.searchParams.has('cursor')) return route.fulfill({ json: historyFixture({
      sessionId, goalRevision: 2, eventCursor: 12, nextCursor: null,
      items: [{ messageId: 1, role: 'user', goalRevision: 1, content: '如何统一监测井高程基准？' }],
      turns: [{ goalRevision: 1, agentRunId: 'agent-1', reply: replyFixture('第一轮的正式答复') }],
    }) });
    if (path.endsWith('/messages')) return route.fulfill({ json: latest() });
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId }) });
    if (path.endsWith('/preview')) return route.fulfill({ status: 204 });
    if (path.endsWith('/events')) {
      eventConnections++;
      expect(url.searchParams.get('afterEventId')).toBe('10');
      if (eventConnections === 1) return route.fulfill({ contentType: 'text/event-stream',
        body: 'retry: 150\nid: 11\nevent: agent.activity\ndata: {"schemaVersion":"session-event.v20","type":"agent.activity","data":{"agentRunId":"agent-2"}}\n\n' });
      if (eventConnections === 2) {
        resumeId = (await route.request().allHeaders())['last-event-id'];
        final = true;
        return route.fulfill({ contentType: 'text/event-stream',
          body: 'id: 12\nevent: agent.complete\ndata: {"schemaVersion":"session-event.v20","type":"agent.complete","data":{"agentRunId":"agent-2"}}\n\n' });
      }
      return route.fulfill({ status: 204 });
    }
    throw new Error(`Unexpected timeline request: ${path}`);
  });

  await page.goto('/#/sessions/turn-browser');
  const second = page.getByRole('region', { name: '第 2 轮' });
  await expect(second.getByText('水位趋势。')).toBeVisible();
  expect(resumeId).toBe('11');
  await expect(second.locator('math')).toHaveCount(1);
  await expect(second.getByRole('table')).toBeVisible();
  await page.getByRole('button', { name: '更早消息' }).click();
  const first = page.getByRole('region', { name: '第 1 轮' });
  await expect(first.getByText('如何统一监测井高程基准？')).toBeVisible();
  await expect(first.getByText('第一轮的正式答复')).toBeVisible();
  await page.getByLabel('定位对话轮次').selectOption('2');
  await expect(second).toBeFocused();
  await second.getByRole('button', { name: '复制提问' }).click();
  await expect(second.getByText('已复制')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('比较监测井水位，列出计算依据和表格。');
  await page.getByLabel('定位对话轮次').selectOption('2');
  await page.screenshot({ path: info.outputPath('turn-timeline-desktop.png') });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel('定位对话轮次').selectOption('2');
  await expect(second).toBeFocused();
  const region = second.getByRole('region', { name: '分析表格，可横向滚动' });
  await expect(region).toHaveAttribute('tabindex', '0');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('turn-timeline-mobile.png') });
});
