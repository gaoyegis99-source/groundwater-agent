import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, messageReceiptFixture, replyFixture, sessionResponseFixture, sessionResultsFixture, taskFixture, turnFixture } from '../src/test-fixtures';
import type { Confirmation, ResourceItem } from '../src/types';
import { identityStorageScope, scopedStorageKey } from '../src/browser-scope';

const profile = JSON.parse(
  readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'),
) as { productId: string; models: Record<string, unknown> };
const currentUser = { subject: 'researcher-1', displayName: '分析用户一', roles: ['researcher'] };

async function noHighImpactViolations(page: Page) {
  const result = await new AxeBuilder({ page }).withTags([
    'wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa',
  ]).analyze();
  const violations = result.violations.filter(item =>
    item.impact === 'critical' || item.impact === 'serious');
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
}

async function mockApplication(page: Page, withSession = false, analysis?: string, pendingRevision = false, approved = false, capabilitiesReady?: Promise<void>) {
  await page.addInitScript(() => {
    // The layout fixture stays connected. Native SSE transport is tested separately.
    class ConnectedEvents extends EventTarget {
      onopen: (() => void) | null = null;
      constructor() { super(); queueMicrotask(() => this.onopen?.()); }
      close() {}
    }
    Object.assign(window, { EventSource: ConnectedEvents });
  });
  const reference = dataRef({ outputName: pendingRevision ? '地下水监测观测井长周期水位与降水联合观测样本'.repeat(3) : 'summary',
    format: 'json', mediaType: 'application/json' });
  const confirmation: Confirmation = {
    confirmationId: 'confirmation-1', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null, revision: 1, kind: 'INITIAL',
    actions: [{
      toolCallId: 'tool-1', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'groundwater.summary', workflowVersion: '1.0.0', arguments: { resourceId: 'version-1', axis: 'columns' },
      inputReferences: { resource: reference },
    }],
  };
  const task = taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: 1, finishedAt: '2026-10-17T01:01:00Z' });
  const history = historyFixture({
    sessionId: 'application-session', goalRevision: 1,
    items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '应用可访问性检查' }],
    turns: pendingRevision ? [turnFixture({ goalRevision: 1, status: 'WAITING_CONFIRMATION', confirmations: [
      { ...confirmation, status: 'SUPERSEDED' },
      { ...confirmation, confirmationId: 'confirmation-2', revision: 2, kind: 'EDITED', replacesConfirmationId: 'confirmation-1',
        actions: confirmation.actions.map(action => ({ ...action, arguments: { ...action.arguments, axis: 'flatten' } })) },
    ] })] : [turnFixture({ goalRevision: 1, status: analysis ? 'COMPLETE' : 'WAITING_EXECUTION',
      confirmations: [approved ? { ...confirmation, status: 'CONFIRMED', decision: 'CONFIRM' } : confirmation],
      executionTasks: [approved ? { ...task, status: 'RUNNING', stage: 'EXECUTING', outputCount: 0, finishedAt: null } : task],
      ...(analysis ? { reply: replyFixture(analysis) } : {}) })],
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    const reply = (value: unknown) => route.fulfill({ json: value });
    if (url.pathname.endsWith('/capabilities') && !url.pathname.includes('/data/')) {
      await capabilitiesReady;
      return reply(capabilitiesForProfile(profile, { currentUser }));
    }
    if (url.pathname.endsWith('/data/capabilities')) return reply({
      version: '1', productId: profile.productId, project: 'demo', currentUser,
      permissions: {
        catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true,
        userUploadCreate: true, managedPublicationWrite: false, semanticWrite: false,
        accessWrite: true, governanceRead: false, qualityWrite: false, mlopsWrite: false,
      },
      identityMode: 'gateway', writeEnabled: true,
      formats: ['csv'], formatMediaTypes: { csv: 'text/csv' }, query: {}, preview: {},
      limits: { uploadBytes: 1073741824 }, contracts: {}, openapi: '/openapi.json',
    });
    if (url.pathname.endsWith('/sessions')) return reply({
      items: withSession ? [{
        sessionId: 'application-session', projectId: 'demo', title: '应用可访问性检查', createdAt: '2026-10-17T01:00:00Z',
        goalRevision: 1, agentRunId: 'agent-1', pendingAgentRunId: null, workflowDraftId: null, archived: false,
      }] : [], nextCursor: null,
    });
    if (url.pathname.endsWith('/messages')) return reply(history);
    if (url.pathname.endsWith('/results')) return reply(sessionResultsFixture(pendingRevision || approved ? [] : [
      { goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary',
        status: 'COMPLETE', workflowRunId: task.workflowRunId, remoteOutputs: [reference] },
    ], { sessionId: 'application-session' }));
    if (url.pathname.endsWith('/data/versions/version-1')) return reply(dataDetail());
    if (url.pathname.endsWith('/events') || url.pathname.endsWith('/preview')) {
      // These layout tests keep the initial snapshot. Native reconnect is exercised by the Turn timeline spec.
      return route.fulfill({ status: 204 });
    }
    if (url.pathname.endsWith('/data/datasets')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/knowledge/documents')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/sessions/task-history')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/ready')) return reply({ status: 'READY', components: { kernel: { status: 'READY' } } });
    if (url.pathname.endsWith('/trace')) return reply({});
    throw new Error(`Unexpected application accessibility request: ${url.pathname}`);
  });
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },

  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
]) {
  test(`application home reflows and has no serious accessibility findings at ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await mockApplication(page);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '常用任务', exact: true })).toBeVisible();
    const accent = await page.locator('html').evaluate(node => getComputedStyle(node).getPropertyValue('--accent').trim().toLowerCase());
    expect(accent).toBe('#244a68');
    await expect(page.getByTestId('workspace-panel')).toBeAttached();
    await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(240, 244, 243)');
    await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeInViewport();
    await expect(page.getByText('正在读取会话', { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).toBeVisible();
    await noHighImpactViolations(page);
    await page.screenshot({ path: testInfo.outputPath('home.png'), fullPage: true });
  });
}

for (const [width, height] of [[1440, 900], [1366, 900], [1280, 800], [1024, 768]]) {
  test(`task workbench opens and supports keyboard tabs at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height });
    await mockApplication(page);
    await page.goto('/');
    await expect(page.getByTestId('workspace-panel')).toBeAttached();
    await expect(page.getByTestId('workspace-panel')).toBeHidden();
    await page.getByRole('button', { name: '打开任务工作台', exact: true }).click();
    const tabs = page.getByRole('tablist', { name: '任务工作台视图' });
    await expect(tabs).toBeVisible();
    const data = tabs.getByRole('tab', { name: '数据', exact: true });
    await data.click();
    await page.keyboard.press('End');
    await expect(tabs.getByRole('tab', { name: '成果', exact: true })).toBeFocused();
    await page.keyboard.press('Home');
    await expect(data).toBeFocused();
    const bounds = await page.getByTestId('workspace-panel').boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await noHighImpactViolations(page);
    await page.screenshot({ path: testInfo.outputPath('workbench.png'), fullPage: true });
    const panel = page.getByTestId('workspace-panel');
    await expect(panel).toHaveAttribute('role', 'complementary');
    await panel.getByRole('button', { name: '折叠任务工作台' }).click();
    await expect(page.getByRole('button', { name: '打开任务工作台', exact: true })).toBeFocused();
  });
}

test('foundation overlays contain keyboard focus and restore it to their opener', async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApplication(page, true);
  await page.goto('/#/sessions/application-session');
  const rename = page.getByRole('button', { name: '重命名', exact: true });
  await rename.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: '重命名对话' });
  await expect(dialog.getByRole('textbox', { name: '对话名称' })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: '保存', exact: true })).toBeFocused();
  await noHighImpactViolations(page);
  await page.screenshot({ path: info.outputPath('rename-dialog.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(rename).toBeFocused();

  await page.setViewportSize({ width: 1024, height: 768 });
  const opener = page.getByRole('button', { name: '工作流', exact: true });
  await opener.focus();
  await page.keyboard.press('Enter');
  const drawer = page.getByRole('dialog', { name: '能力目录' });
  await expect(drawer.getByRole('button', { name: '关闭能力目录' })).toBeFocused();
  const search = drawer.getByRole('searchbox', { name: '搜索工作流' });
  await search.fill('无匹配结果');
  await expect(drawer.getByRole('status')).toHaveText('没有匹配的工作流。');
  await drawer.getByRole('button', { name: '清除关键词' }).click();
  await expect(search).toBeFocused();
  expect((await drawer.boundingBox())!.width).toBeLessThan(1024);
  await noHighImpactViolations(page);
  await page.screenshot({ path: info.outputPath('capability-drawer.png') });
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test('long scientific replies keep readable hierarchy and complete tables on desktop', async ({ page }, testInfo) => {
  const limitation = '插值估计不代表实际观测，单位为米，缺测值未填零。'.repeat(5);
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApplication(page, true, `# 分析报告\n\n## 水位趋势\n\n### 适用范围\n\n正文保留真实数据和时间边界。\n\n> 观测与估计应分别解释。\n\n| 监测井 | 适用范围与限制 |\n| --- | --- |\n| W01 | ${limitation} |`);
  await page.goto('/#/sessions/application-session');
  await expect(page.getByRole('heading', { name: '分析报告', exact: true })).toBeVisible();
  const panel = page.getByRole('button', { name: '折叠任务工作台', exact: true });
  if (await panel.isVisible()) await panel.click();
  const sizes = await page.locator('.markdown-content h1, .markdown-content h2, .markdown-content h3').evaluateAll(nodes => nodes.map(node => parseFloat(getComputedStyle(node).fontSize)));
  expect(sizes[0]).toBeGreaterThan(sizes[1]);
  expect(sizes[1]).toBeGreaterThan(sizes[2]);
  const table = page.getByRole('region', { name: '分析表格，可横向滚动' });
  await table.focus();
  await expect(table).toBeFocused();
  const cell = table.getByRole('cell', { name: limitation });
  await expect(cell).toBeVisible();
  expect(await cell.evaluate(node => getComputedStyle(node).textOverflow)).toBe('clip');
  expect(await cell.evaluate(node => getComputedStyle(node).whiteSpace)).toBe('normal');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('scientific-reply.png'), fullPage: true });
});

test('upload, database, confirmation and result surfaces have no serious accessibility findings', async ({ page }) => {
  await mockApplication(page, true);
  await page.goto('/#/sessions/application-session');
  await expect(page.getByTestId('confirmation-card')).toBeVisible();
  await expect(page.getByTestId('result-card')).toBeVisible();
  await noHighImpactViolations(page);
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  await expect(page.getByTestId('database-workbench')).toBeVisible();
  await noHighImpactViolations(page);
  await page.keyboard.press('Escape');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'sample.csv', mimeType: 'text/csv', buffer: Buffer.from('x,y\n1,2\n'),
  });
  await expect(page.getByRole('dialog', { name: '添加资料' })).toBeVisible();
  await noHighImpactViolations(page);
});

test('interaction feedback keeps controls usable and respects reduced motion', async ({ page, browserName }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApplication(page, true);
  let releaseCatalog!: () => void;
  const firstCatalog = new Promise<void>(resolve => { releaseCatalog = resolve; });
  let reads = 0;
  await page.route('**/api/v12/data/datasets*', async route => {
    if (++reads === 1) {
      await firstCatalog;
      return route.fulfill({ status: 503, json: { code: 'DATA_UNAVAILABLE' } });
    }
    return route.fulfill({ json: { items: [], nextCursor: null, matchBasis: 'catalog' } });
  });
  await page.goto('/#/sessions/application-session');
  const newChat = page.getByRole('button', { name: '新建对话' });
  await expect(newChat).toBeEnabled();
  const restingFill = await newChat.evaluate(node => getComputedStyle(node).backgroundColor);
  await newChat.hover();
  await expect(newChat).not.toHaveCSS('background-color', restingFill);
  await page.mouse.down();
  await expect(newChat).toHaveCSS('outline-offset', '-3px');
  await page.mouse.move(250, 20);
  await page.mouse.up();

  const search = page.getByRole('searchbox', { name: '搜索对话' });
  await search.focus();
  await expect(search).toHaveCSS('outline-style', 'solid');
  await expect(search).toHaveCSS('outline-width', '2px');
  await page.evaluate(() => document.addEventListener('animationstart', event => {
    if (event.target instanceof HTMLElement && event.target.matches('.gw-overlay')) {
      document.documentElement.dataset.observedOverlayMotion = event.animationName;
    }
  }));
  const rename = page.getByRole('button', { name: '重命名', exact: true });
  await rename.click();
  await expect(page.getByRole('dialog', { name: '重命名对话' })).toBeVisible();
  await expect(page.locator('.gw-overlay')).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-observed-overlay-motion', 'workspace-fade-in');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '重命名对话' })).toHaveCount(0);
  await expect(rename).toBeFocused();
  if (browserName !== 'webkit') {
    await expect(page.locator('html')).toHaveAttribute('data-observed-overlay-motion', 'workspace-fade-out');
  }

  const openDatabase = page.getByRole('button', { name: '数据库', exact: true });
  const shade = page.locator('.app-shell > .drawer-shade[data-open]');
  const drawer = page.getByRole('dialog', { name: '数据库' });
  try {
    await openDatabase.click();
    await expect(drawer).toHaveCSS('animation-name', 'workspace-reveal');
    await expect(shade).toHaveCSS('opacity', '1');
    await expect(drawer.getByText('正在读取数据目录…')).toBeVisible();
    await expect(drawer.locator('.workbench-skeleton')).toHaveAttribute('aria-hidden', 'true');
    await expect(drawer.locator('.gw-spinner')).toHaveCSS('animation-iteration-count', '4');
    await page.screenshot({ path: info.outputPath('interaction-database-loading.png') });
    releaseCatalog();
    await expect(drawer.getByRole('alert')).toContainText('数据目录读取失败');
    await expect(drawer.locator('.workbench-skeleton')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('interaction-database-error.png') });
    await drawer.getByRole('button', { name: '重新读取目录' }).click();
    await expect(drawer.getByText('没有符合条件的数据集。')).toBeVisible();
    const format = drawer.getByRole('combobox', { name: '格式筛选' });
    await format.focus();
    await expect(format).toBeFocused();
    if (browserName === 'chromium') {
      const closedMenuFill = await format.evaluate(node => getComputedStyle(node).backgroundColor);
      await format.press('Space');
      await expect.poll(() => format.evaluate(node => node.matches(':open'))).toBe(true);
      await expect(format).not.toHaveCSS('background-color', closedMenuFill);
      await page.keyboard.press('Escape');
      await expect.poll(() => format.evaluate(node => node.matches(':open'))).toBe(false);
    }
    await format.selectOption('csv');
    await expect(format).toHaveValue('csv');
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    await expect(shade).toHaveCSS('pointer-events', 'none');
    await expect(openDatabase).toBeFocused();
  } finally {
    releaseCatalog();
  }

  const composer = page.getByRole('textbox', { name: '消息输入框' });
  await composer.fill('切换时保留的观测条件');
  await page.getByRole('link', { name: '数据', exact: true }).click();
  const workbench = page.getByRole('main', { name: '任务工作台' });
  await expect(workbench.locator('h1')).toBeFocused();
  await expect(workbench).toHaveCSS('animation-name', 'workspace-reveal');
  await workbench.getByRole('tab', { name: /^数据/ }).press('ArrowRight');
  await expect(workbench.getByRole('tab', { name: '草稿' })).toBeFocused();
  await expect(workbench.getByRole('tabpanel', { name: '草稿' })).toBeVisible();
  await workbench.getByRole('button', { name: '返回对话' }).click();
  await expect(composer).toHaveValue('切换时保留的观测条件');

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openDatabase.click();
  await expect(drawer).toHaveCSS('animation-name', 'none');
  await expect(shade).toHaveCSS('transition-property', 'none');
  await drawer.getByRole('button', { name: '关闭数据库' }).click();
  await expect(openDatabase).toBeFocused();
});

for (const viewport of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 },
  { width: 1280, height: 800 }, { width: 1024, height: 768 },

]) {
  test(`current revision is actionable and reachable with the keyboard at ${viewport.width}px`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    await mockApplication(page, true, undefined, true);
    await page.goto('/#/sessions/application-session');
    const confirm = page.getByRole('button', { name: '确认执行修订 2', exact: true });
    await expect(confirm).toBeInViewport();
    await expect(confirm).toBeEnabled();
    expect(await confirm.evaluate(node => {
      const bounds = node.getBoundingClientRect();
      const hit = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
      return node.contains(hit) ? null : { overlay: hit?.className, y: bounds.y, viewport: node.closest('.chat-scroll')?.getBoundingClientRect().toJSON() };
    })).toBeNull();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: '查看当前确认', exact: true }).first()).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#current-confirmation')).toBeFocused();
    await expect(page.getByText('历史修订 1 · 已被替代')).toBeVisible();
    await expect(page.getByTestId('confirm-execution')).toHaveCount(1);
    await page.screenshot({ path: info.outputPath('current-confirmation.png') });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}


test('the session toolbar keeps every action in one row across desktop widths', async ({ page }) => {
  await mockApplication(page, true, '水位随时间的变化');
  await page.goto('/#/sessions/application-session');
  const toolbar = page.locator('.session-toolbar');
  const navigation = toolbar.getByRole('navigation', { name: '会话详情' });
  await expect(navigation.getByRole('link')).toHaveCount(5);
  await expect(toolbar.getByRole('button', { name: '任务历史', exact: true })).toBeVisible();
  await expect(toolbar.getByRole('button', { name: '收起任务工作台', exact: true })).toBeVisible();
  await expect(toolbar.getByRole('button', { name: '退出', exact: true })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);

  for (const width of [2540, 1920, 1440, 1366, 1280, 1100, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(toolbar.locator('.session-workbench-entry')).toHaveAttribute('aria-expanded', 'true');
    const layout = await toolbar.evaluate(element => {
      const frame = element.getBoundingClientRect();
      const actions = Array.from(element.querySelectorAll('a, button'))
        .filter(action => action.getClientRects().length)
        .map(action => action.getBoundingClientRect());
      return {
        height: frame.height,
        width: frame.width,
        sameRow: actions.every(action => Math.abs(action.y + action.height / 2 - frame.y - frame.height / 2) <= 1),
        fits: actions.every((action, index) => action.left >= frame.left && action.right <= frame.right + 1
          && (index === 0 || action.left >= actions[index - 1].right - 1)),
      };
    });
    expect(layout, `${width}px`).toMatchObject({ sameRow: true, fits: true });
    expect(layout.height, `${width}px`).toBeLessThanOrEqual(52);
    expect(layout.width, `${width}px`).toBeLessThanOrEqual(896);
  }
  await noHighImpactViolations(page);
});

test('the desktop task sidebar collapses and restores while retaining the draft', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 });
  await mockApplication(page, true, '水位随时间的变化');
  await page.goto('/#/sessions/application-session');
  const sidebar = page.getByRole('complementary', { name: '任务工作台', exact: true });
  const sidebarTabs = sidebar.getByRole('tablist', { name: '任务工作台视图' });
  const compose = page.getByRole('textbox', { name: '消息输入框' });
  const toolbar = page.locator('.session-toolbar');
  const open = toolbar.getByRole('button', { name: '打开任务工作台', exact: true });
  await expect(sidebarTabs).toBeVisible();
  await expect(compose).toBeVisible();
  await compose.fill('保留任务侧栏切换前的草稿');
  const width = (await sidebar.boundingBox())!.width;
  expect(width).toBeLessThanOrEqual(448);
  await sidebar.getByRole('button', { name: '折叠任务工作台' }).click();
  await expect(sidebarTabs).toBeHidden();
  const restore = sidebar.getByRole('button', { name: '展开右侧任务栏' });
  await expect(restore).toBeFocused();
  expect((await sidebar.boundingBox())!.width).toBeLessThan(width);
  await open.click();
  await expect(sidebarTabs).toBeVisible();
  await expect(page).toHaveURL(/#\/sessions\/application-session$/);
  await page.getByRole('button', { name: '任务历史', exact: true }).click();
  await expect(page.getByRole('complementary', { name: '项目任务历史' })).toBeVisible();
  await expect(sidebar).toHaveCount(0);
  await page.getByRole('button', { name: '收起任务历史' }).click();
  await expect(sidebarTabs).toBeVisible();
  await expect(compose).toHaveValue('保留任务侧栏切换前的草稿');
});

test('task history waits for the project identity before it can be opened', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  let connect!: () => void;
  const capabilitiesReady = new Promise<void>(resolve => { connect = resolve; });
  await mockApplication(page, true, undefined, false, false, capabilitiesReady);
  await page.goto('/#/sessions/application-session');
  const entry = page.getByRole('button', { name: '任务历史', exact: true });
  try {
    await expect(entry).toBeVisible();
    await expect(entry).toBeDisabled();
  } finally { connect(); }
  await entry.click();
  const history = page.getByRole('complementary', { name: '项目任务历史' });
  await expect(history).toBeVisible();
  await expect(history.getByRole('button', { name: '收起任务历史' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(entry).toBeFocused();
});

test('session workbench routes have distinct pages and keep the conversation draft', async ({ page }, info) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await mockApplication(page, true, undefined, true);
  await page.goto('/#/sessions/application-session');
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  const parameters = page.getByRole('navigation', { name: '会话详情' }).getByRole('link', { name: '参数' });
  await expect(page.locator('.session-context h1')).toHaveClass('sr-only');
  await expect(parameters).toBeVisible();
  await expect(page.getByRole('complementary', { name: '任务工作台' })).toBeVisible();
  await composer.fill('保留的研究草稿');
  await parameters.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/workbench\/parameters$/);
  await expect(page.getByRole('heading', { name: '参数', exact: true })).toBeFocused();
  await expect(composer).toBeHidden();
  await expect(page.getByRole('button', { name: '确认执行修订 2' })).toBeEnabled();
  await expect(page.locator('#current-confirmation')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('parameters-route.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await expect(parameters).toBeFocused();
  await parameters.press('Enter');
  await page.getByRole('tab', { name: '草稿', exact: true }).click();
  await expect(page).toHaveURL(/workbench\/draft$/);
  await expect(page.getByRole('heading', { name: '工作流草稿', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('tabpanel', { name: '草稿' })).toBeVisible();
  await page.getByRole('tab', { name: '成果', exact: true }).click();
  await expect(page).toHaveURL(/workbench\/results$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: '成果', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await expect(page).toHaveURL(/#\/sessions\/application-session$/);
  await expect(composer).toHaveValue('保留的研究草稿');
  await expect(composer).toBeFocused();
});

test('confirmation validates Kriging bounds and reads only the selected catalog version before saving', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApplication(page, true);
  const model = JSON.parse(readFileSync(new URL('../../domains/groundwater/6.2.0/models/kriging.model.json', import.meta.url), 'utf8'));
  const capabilities = capabilitiesForProfile(profile, { currentUser });
  const kriging = capabilities.models.find(item => item.modelId === model.modelId)!;
  Object.assign(kriging, { inputSchema: model.tool.inputSchema, dataInputParameters: ['versionId'] });
  let confirmation: Confirmation = {
    confirmationId: 'kriging-confirmation', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null, revision: 1, kind: 'INITIAL',
    actions: [{
      toolCallId: 'kriging-step', tool: model.tool.name, modelId: model.modelId, modelVersion: model.version,
      workflowId: model.workflowRef.workflowId, workflowVersion: model.workflowRef.version,
      arguments: {
        ...model.defaultParameters, versionId: 'version-1', valueField: 'water_level', xField: 'x', yField: 'y',
        sourceCrs: 'EPSG:4547', targetCrs: 'EPSG:4547', pixelSize: 10,
      },
    }],
  };
  const datasets = Array.from({ length: 50 }, (_, index) => ({
    datasetId: `revision-data-${index}`, project: 'demo', title: `复核观测 ${index}`, tags: [],
    currentVersion: `revision-version-${index}`, createdAt: '2026-10-17T01:00:00Z', semantics: {}, formats: ['json'],
  }));
  const versionsRead: string[] = [];
  const payloads: { actions: { arguments: Record<string, unknown>; expectedInputReferences: Record<string, unknown> }[] }[] = [];
  let releaseVersion!: () => void;
  const versionReady = new Promise<void>(resolve => { releaseVersion = resolve; });
  await page.route(url => url.pathname === '/api/v12/capabilities', route => route.fulfill({ json: capabilities }));
  await page.route(url => url.pathname.endsWith('/sessions/application-session/messages'), route => route.fulfill({ json: historyFixture({
    sessionId: 'application-session',
    items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '复核克里金插值' }],
    turns: [turnFixture({ goalRevision: 1, status: 'WAITING_CONFIRMATION', confirmations: [confirmation] })],
  }) }));
  await page.route(url => url.pathname.endsWith('/data/datasets'), route => route.fulfill({
    json: { items: datasets, nextCursor: null, matchBasis: 'catalog' },
  }));
  await page.route(url => url.pathname.includes('/data/versions/revision-version-'), async route => {
    const versionId = new URL(route.request().url()).pathname.split('/').at(-1)!;
    versionsRead.push(versionId);
    const dataset = datasets.find(item => item.currentVersion === versionId)!;
    await versionReady;
    return route.fulfill({ json: dataDetail({ datasetId: dataset.datasetId, versionId, title: dataset.title }) });
  });
  await page.route(url => url.pathname.endsWith('/confirmations/kriging-confirmation/revisions'), route => {
    const body = route.request().postDataJSON() as (typeof payloads)[number];
    payloads.push(body);
    confirmation = { ...confirmation, confirmationId: 'kriging-confirmation-2', replacesConfirmationId: confirmation.confirmationId,
      revision: 2, kind: 'EDITED', actions: [{ ...confirmation.actions[0], arguments: body.actions[0].arguments }] };
    return route.fulfill({ status: 201, json: sessionResponseFixture({ sessionId: 'application-session', confirmation }) });
  });
  try {
    await page.goto('/#/sessions/application-session/workbench/parameters');
    await page.getByRole('button', { name: '编辑当前参数' }).click();
    const editor = page.getByTestId('confirmation-editor');
    const count = editor.locator('[data-parameter-field="neighborCount"] input');
    const version = editor.locator('[data-parameter-field="versionId"] select');
    for (const field of [count, version]) {
      await field.evaluate(node => { const group = node.closest('details'); if (group) group.open = true; });
    }
    await expect(version.getByRole('option')).toHaveCount(52);
    expect(versionsRead).toEqual([]);
    const save = editor.getByRole('button', { name: '保存为新修订' });
    await count.fill('0');
    await expect(count).toHaveAccessibleDescription(/不能小于 1/);
    await expect(save).toBeDisabled();
    expect(payloads).toEqual([]);
    await count.fill('13');
    await expect(save).toBeEnabled();
    await version.selectOption('revision-version-49');
    await expect.poll(() => versionsRead).toEqual(['revision-version-49']);
    await expect(save).toBeDisabled();
    releaseVersion();
    await expect(save).toBeEnabled();
    await save.click();
    await expect.poll(() => payloads.length).toBe(1);
    expect(payloads[0].actions[0]).toMatchObject({
      arguments: { neighborCount: 13, versionId: 'revision-version-49' },
      expectedInputReferences: { versionId: { datasetId: 'revision-data-49', versionId: 'revision-version-49' } },
    });
  } finally {
    releaseVersion();
  }
});

test('a long pending queue keeps the composer and latest turn reachable in a short viewport', async ({ page }, info) => {
  await page.setViewportSize({ width: 1024, height: 450 });
  const key = scopedStorageKey(identityStorageScope(profile.productId, 'demo', currentUser.subject), 'message-queue', 'application-session');
  await page.addInitScript(storageKey => localStorage.setItem(storageKey, JSON.stringify(
    Array.from({ length: 8 }, (_, index) => ({
      id: String(index), text: `后续科研问题 ${index + 1}`, attachments: [], createdAt: '2026-10-17T01:00:00Z',
    })),
  )), key);
  await mockApplication(page, true, undefined, false, true);
  await page.goto('/#/sessions/application-session');
  const queue = page.getByRole('region', { name: '稍后发送的消息' });
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  await expect(queue.getByRole('textbox')).toHaveCount(0);
  await expect(composer).toBeInViewport({ ratio: 1 });
  const question = page.getByRole('region', { name: '第 1 轮' }).getByText('应用可访问性检查', { exact: true });
  await question.scrollIntoViewIfNeeded();
  await expect(question).toBeInViewport();
  await page.screenshot({ path: info.outputPath('queued-conversation-collapsed.png') });
  await composer.fill('补充一个观测条件');
  await queue.locator('summary').press('Enter');
  await expect(queue.getByRole('textbox')).toHaveCount(8);
  await queue.getByRole('textbox', { name: '排队消息 8' }).focus();
  await expect(queue.getByRole('textbox', { name: '排队消息 8' })).toBeInViewport();
  await expect(composer).toBeInViewport({ ratio: 1 });
  await noHighImpactViolations(page);
  await page.screenshot({ path: info.outputPath('queued-conversation-short.png') });
});

test('conversation distinguishes loading, uncertain submission, local queue and server stop receipt', async ({ page }, info) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await mockApplication(page, true);
  const sessionId = 'application-session';
  const prompt = '按统一高程基准核对两口井';
  const next = '补查六月缺测的原始记录';
  let running = false;
  let stopRequestId: string | null = null;
  const messageIds: string[] = [];
  const controls: Array<{ agentRunId: string; requestId: string }> = [];
  let load!: () => void, fail!: () => void, stop!: () => void;
  const initialRead = new Promise<void>(resolve => { load = resolve; });
  const firstSend = new Promise<void>(resolve => { fail = resolve; });
  const stopReceipt = new Promise<void>(resolve => { stop = resolve; });
  await page.route(`**/api/v12/sessions/${sessionId}/messages`, async route => {
    if (route.request().method() === 'POST') {
      const { requestId } = route.request().postDataJSON();
      messageIds.push(requestId);
      if (messageIds.length === 1) {
        await firstSend;
        return route.abort('failed');
      }
      running = true;
      return route.fulfill({ status: 202, json: messageReceiptFixture({ requestId, sessionId, goalRevision: 2, agentRunId: 'agent-2' }) });
    }
    await initialRead;
    const items = [{ messageId: 1, goalRevision: 1, role: 'user', content: '先核对观测资料' }];
    const turns = [turnFixture({ status: 'COMPLETE', reply: replyFixture('请提供两口井的观测记录。') })];
    if (running) {
      items.push({ messageId: 2, goalRevision: 2, role: 'user', content: prompt });
      turns.push(turnFixture({ goalRevision: 2, agentRunId: 'agent-2', status: 'RUNNING' }));
    }
    return route.fulfill({ json: historyFixture({
      sessionId, goalRevision: running ? 2 : 1, items, turns,
      controlRequests: stopRequestId ? [{ requestId: stopRequestId, agentRunId: 'agent-2', status: 'REQUESTED' }] : [],
    }) });
  });
  await page.route(`**/api/v12/sessions/${sessionId}/stop`, async route => {
    const request = route.request().postDataJSON();
    controls.push(request);
    await stopReceipt;
    stopRequestId = request.requestId;
    return route.fulfill({ json: { sessionId, ...request, status: 'REQUESTED' } });
  });
  try {
    await page.goto(`/#/sessions/${sessionId}`);
    await expect(page.getByTestId('message-list').getByRole('status')).toHaveText('正在恢复对话…');
    load();
    await expect(page.getByRole('article', { name: '研究者提问' })).toContainText('先核对观测资料');
    const composer = page.getByRole('textbox', { name: '消息输入框' });
    await composer.fill(prompt);
    await composer.press('Enter');
    await expect(page.getByText('正在提交消息 · 草稿会保留', { exact: true })).toBeVisible();
    const submitting = page.getByRole('button', { name: '发送消息：正在提交' });
    await expect(submitting).toHaveAttribute('aria-busy', 'true');
    await expect(submitting).toBeDisabled();
    await expect(submitting).toHaveText('提交中');
    await expect(composer).toHaveValue(prompt);
    fail();
    await expect(page.getByText('这条消息的发送状态待确认，草稿已保留。')).toBeVisible();
    await page.getByRole('button', { name: '重试发送' }).click();
    const current = page.getByRole('region', { name: '第 2 轮' });
    await expect(current.getByText(prompt, { exact: true })).toBeVisible();
    await expect(submitting).toHaveCount(0);
    await expect(composer).toHaveValue('');
    expect(messageIds).toEqual([messageIds[0], messageIds[0]]);

    await composer.fill(next);
    await page.getByRole('button', { name: '稍后发送', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '排队消息 1' })).toHaveValue(next);
    await expect(current.getByText(next, { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: '停止当前处理', exact: true }).click();
    await expect(page.getByRole('button', { name: '正在请求停止…' })).toBeDisabled();
    stop();
    await expect(current.getByText('已请求停止', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '等待停止结果' })).toBeDisabled();
    await expect(current.getByText('本轮处理中', { exact: true })).toBeVisible();
    expect(controls).toEqual([{ agentRunId: 'agent-2', requestId: expect.any(String) }]);
    expect(messageIds).toHaveLength(2);
    await page.screenshot({ path: info.outputPath('conversation-stop-and-queue.png') });
  } finally {
    load(); fail(); stop();
  }
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }]) {
  test(`desktop research surfaces keep actions and keyboard focus at ${viewport.width}x${viewport.height}`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    const columns = Array.from({ length: 10 }, (_, index) => `监测项 ${index + 1}`);
    const observations = columns.map((_, index) => index ? `观测记录 ${index}` : 'UnbrokenMonitoringIdentifier'.repeat(12));
    const paragraphs = Array.from({ length: 42 }, (_, index) => `### 第 ${index + 1} 段核查说明\n\n${'中文连续监测井数据需要核实抽水背景和测量高程以及缺测月份。'.repeat(10)}`);
    await mockApplication(page, true, [
      '# 地下水监测报告', '',
      `| ${columns.join(' | ')} |`,
      `| ${columns.map(() => '---').join(' | ')} |`,
      ...Array.from({ length: 120 }, (_, index) => `| W${index + 1} ${observations.join(' | ')} |`), '',
      ...paragraphs.flatMap(text => [text, '']),
    ].join('\n'));
    await page.goto('/#/sessions/application-session');
    const main = page.locator('.main-panel');
    const nav = page.getByRole('navigation', { name: '会话详情' });
    const table = page.getByRole('region', { name: '分析表格，可横向滚动' });
    const noPageOverflow = async () => {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    };
    await expect(table.getByRole('columnheader')).toHaveCount(10);
    await expect(table.getByRole('row')).toHaveCount(121);
    await expect(page.getByRole('heading', { name: '第 42 段核查说明' })).toBeAttached();
    await expect.poll(() => table.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
    await table.focus();
    await expect(table).toBeFocused();
    await expect.poll(async () => {
      await table.press('ArrowRight');
      return table.evaluate(node => node.scrollLeft);
    }).toBeGreaterThan(0);
    await noPageOverflow();
    await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeInViewport();

    for (const [label, path] of [['数据', 'inputs'], ['草稿', 'draft'], ['参数', 'parameters'], ['运行', 'tasks'], ['成果', 'results']]) {
      const entry = nav.getByRole('link', { name: label, exact: true });
      await entry.focus();
      await entry.press('Enter');
      await expect(page).toHaveURL(new RegExp(`/workbench/${path}$`));
      await expect(page.getByRole('tabpanel')).toBeVisible();
      await noPageOverflow();
      await page.getByRole('button', { name: '返回对话', exact: true }).click();
      await expect(entry).toBeFocused();
    }

    const history = nav.getByRole('button', { name: '任务历史' });
    await history.click();
    await expect(page.getByRole('button', { name: '收起任务历史' })).toBeFocused();
    await noPageOverflow();
    await page.keyboard.press('Escape');
    await expect(history).toBeFocused();

    const ready = page.getByRole('button', { name: /^服务状态/ });
    await ready.click();
    const status = page.getByRole('dialog', { name: '服务状态' });
    await expect(status.getByRole('button', { name: '关闭服务状态' })).toBeFocused();
    await noPageOverflow();
    await page.keyboard.press('Escape');
    await expect(ready).toBeFocused();

    const sidebar = page.getByRole('complementary', { name: '会话侧栏' });
    await sidebar.getByRole('button', { name: '折叠会话侧栏' }).click();
    await sidebar.getByRole('button', { name: '展开会话侧栏' }).click();
    const rename = sidebar.getByRole('button', { name: '重命名', exact: true });
    await rename.click();
    await expect(page.getByRole('dialog', { name: '重命名对话' }).getByRole('textbox')).toBeVisible();
    await noPageOverflow();
    await page.keyboard.press('Escape');
    await expect(rename).toBeFocused();

    for (const title of ['数据库', '知识库', '能力目录']) {
      const opener = sidebar.getByRole('button', { name: title, exact: true });
      await opener.focus();
      await opener.press('Enter');
      const overlay = page.getByRole('dialog', { name: title });
      await expect(overlay).toBeVisible();
      const close = overlay.getByRole('button', { name: `关闭${title}` });
      await close.focus();
      await page.keyboard.press('Shift+Tab');
      expect(await overlay.evaluate(node => node.contains(document.activeElement))).toBe(true);
      await noPageOverflow();
      await page.keyboard.press('Escape');
      await expect(overlay).toHaveCount(0);
      await expect(opener).toBeFocused();
    }

    const attach = main.getByRole('button', { name: '添加附件' });
    await attach.focus();
    await page.locator('input[type="file"]').setInputFiles({
      name: 'long-groundwater-observation-identifier-'.repeat(4) + '.obs',
      mimeType: 'application/octet-stream', buffer: Buffer.from('well,head\nW01,10\n'),
    });
    const upload = page.getByRole('dialog', { name: '添加资料' });
    await expect(upload.getByRole('textbox', { name: '媒体类型' })).toBeVisible();
    await upload.getByRole('textbox', { name: '媒体类型' }).fill('text/csv');
    await upload.getByRole('textbox', { name: '文件格式' }).fill('csv');
    await expect(upload.getByRole('button', { name: '上传并附加' })).toBeEnabled();
    await expect(upload.getByRole('button', { name: '上传并附加' })).toBeInViewport();
    await noPageOverflow();
    await page.screenshot({ path: info.outputPath('upload.png') });
    await page.keyboard.press('Escape');
    await expect(attach).toBeFocused();
  });
}

test.describe('desktop composition', () => {
  test.use({ viewport: { width: 1024, height: 768 }, reducedMotion: 'reduce' });
  test('supports Shift+Enter and keeps the send action visible with reduced motion', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockApplication(page, true, '已核对观测资料。');
    await page.goto('/#/sessions/application-session');
    const input = page.getByRole('textbox', { name: '消息输入框' });
    await input.fill('第一行监测问题');
    await input.press('Shift+Enter');
    await input.pressSequentially('W02');
    await expect(input).toHaveValue('第一行监测问题\nW02');
    await expect(input).toHaveAccessibleDescription('Enter 发送 · Shift+Enter 换行');
    const send = page.getByRole('button', { name: '发送消息', exact: true });
    await expect(send).toBeInViewport();
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    await expect(send).toHaveCSS('animation-name', 'none');
    await expect(send).toHaveCSS('transition-duration', '0s');
    await page.setViewportSize({ width: 1024, height: 480 });
    await expect(input).toBeInViewport();
    await expect(send).toBeInViewport();
  });

  test('keeps writing and closing an oversized upload reachable in a short desktop window', async ({ page }, info) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    const scope = identityStorageScope(profile.productId, 'demo', currentUser.subject);
    const key = scopedStorageKey(scope, 'draft-attachments', 'application-session');
    const queueKey = scopedStorageKey(scope, 'message-queue', 'application-session');
    const attachments: ResourceItem[] = Array.from({ length: 10 }, (_, index) => ({ ...dataRef({ versionId: `stress-version-${index}` }), familyId: 'dataset-1',
      name: `监测井附件 ${index + 1} ${'GroundwaterObservation'.repeat(14)}.json` }));
    await page.addInitScript(({ key, queueKey, attachments }) => {
      sessionStorage.setItem(key, JSON.stringify(attachments));
      localStorage.setItem(queueKey, JSON.stringify([{ id: 'pending-observation', text: '稍后核查观测站点', attachments: [], createdAt: '2026-10-17T01:00:00Z' }]));
    }, { key, queueKey, attachments });
    await mockApplication(page, true, undefined, false, true);
    await page.goto('/#/sessions/application-session');
    const input = page.getByRole('textbox', { name: '消息输入框' });
    const attachmentList = page.getByRole('region', { name: '待发送的附件' });
    await expect(attachmentList.getByRole('listitem')).toHaveCount(10);
    await input.fill('短视口下保留的观测草稿');
    await page.setViewportSize({ width: 1024, height: 450 });
    await expect(input).toBeFocused();
    await expect(input).toBeInViewport({ ratio: 1 });
    const submit = page.getByRole('button', { name: /^发送消息/ });
    await expect(submit).toBeInViewport({ ratio: 1 });
    const attach = page.getByRole('button', { name: '添加附件' });
    await expect(attach).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole('button', { name: '工作流', exact: true })).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole('button', { name: '稍后发送', exact: true })).toBeInViewport({ ratio: 1 });
    await page.screenshot({ path: info.outputPath('short-desktop-composer.png') });
    await attach.focus();
    await page.locator('input[type="file"]').setInputFiles({ name: `${'LongFile'.repeat(28)}.obs`,
      mimeType: 'application/octet-stream', buffer: Buffer.from('well,head\nW01,10\n') });
    const upload = page.getByRole('dialog', { name: '添加资料' });
    await expect(upload.getByRole('button', { name: '关闭上传' })).toBeInViewport({ ratio: 1 });
    await upload.getByRole('textbox', { name: '文件格式' }).fill('csv');
    await upload.getByRole('textbox', { name: '媒体类型' }).fill('text/csv');
    await expect(upload.getByRole('button', { name: '上传并附加' })).toBeInViewport({ ratio: 1 });
    await expect(upload.getByRole('button', { name: '上传并附加' })).toBeEnabled();
    await page.screenshot({ path: info.outputPath('short-desktop-upload.png') });
    await page.keyboard.press('Escape');
    await expect(attach).toBeFocused();
    await expect(input).toHaveValue('短视口下保留的观测草稿');
  });
});
