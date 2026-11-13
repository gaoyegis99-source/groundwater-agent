import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, historyFixture, replyFixture, sessionResponseFixture, sessionResultsFixture, sessionSummaryFixture } from '../src/test-fixtures';

const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));

test('archived sessions and operation feedback remain visible on desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  let archived = false;
  const session = () => sessionSummaryFixture({ title: '长期监测井分析', archived });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (route.request().method() === 'PATCH') {
      archived = true;
      return route.fulfill({ json: { sessionId: 'session-1', title: '长期监测井分析', archived } });
    }
    if (url.pathname.endsWith('/sessions')) return route.fulfill({ json: { items: archived && url.searchParams.get('archived') !== 'true' ? [] : [session()], nextCursor: null } });
    if (url.pathname.endsWith('/messages')) return route.fulfill({ json: historyFixture() });
    if (url.pathname.endsWith('/results')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (url.pathname.endsWith('/events') || url.pathname.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    throw new Error(`Unexpected conversation request: ${url.pathname}`);
  });
  await page.goto('/#/sessions/session-1');
  await page.getByRole('button', { name: '归档', exact: true }).click();
  await expect(page.locator('.session-notice')).toBeVisible();
  await page.getByRole('checkbox', { name: '显示归档' }).check();
  await expect(page.locator('.session-archive-label')).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.locator('.session-archive-label')).toBeVisible();
  await expect(page.locator('.session-notice')).toBeVisible();
  const archivedLink = page.getByTestId('session-list').getByRole('link', { name: /长期监测井分析/ });
  await archivedLink.focus();
  await expect(archivedLink).toBeFocused();
  await expect(archivedLink).toHaveAttribute('title', /长期监测井分析/);
  const rail = page.getByTestId('session-list');
  expect(await rail.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('a second tab can create a conversation after one workspace login', async ({ page, context }) => {
  const capabilities = capabilitiesForProfile(profile);
  await context.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await context.route('**/api/v12/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = await request.allHeaders();
    if (path.endsWith('/auth/login')) return route.fulfill({
      json: { currentUser: capabilities.currentUser, csrfToken: 'test-shared-csrf' },
      headers: { 'Set-Cookie': 'gw_research_session=test-session; Path=/; HttpOnly; SameSite=Strict' },
    });
    if (!headers.cookie?.includes('gw_research_session=test-session')) {
      return route.fulfill({ status: 401, json: { code: 'AUTHENTICATION_REQUIRED' } });
    }
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilities });
    if (path.endsWith('/sessions') && request.method() === 'POST') {
      if (headers['x-csrf-token'] !== 'test-shared-csrf') {
        return route.fulfill({ status: 403, json: { code: 'ROLE_FORBIDDEN' } });
      }
      return route.fulfill({ status: 201, json: sessionResponseFixture() });
    }
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/messages')) return route.fulfill({ json: historyFixture() });
    if (path.endsWith('/results')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    throw new Error(`Unexpected workspace request: ${path}`);
  });

  await page.goto('/');
  await page.getByLabel('用户名').fill('researcher');
  await page.getByLabel('密码').fill('fixture-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '新建对话' })).toBeEnabled();

  const other = await context.newPage();
  await other.goto('/');
  const created = other.waitForResponse(response =>
    response.url().endsWith('/api/v12/sessions') && response.request().method() === 'POST',
  );
  await other.getByRole('button', { name: '新建对话' }).click();
  expect((await created).status()).toBe(201);
});

test('UI-03A directory supports Session operations and navigation at desktop and mobile sizes', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const records = [
    sessionSummaryFixture({ sessionId: 'session-1', title: '华北平原地下水位变化' }),
    sessionSummaryFixture({ sessionId: 'session-2', title: '监测井水位异常排查' }),
    sessionSummaryFixture({ sessionId: 'session-3', title: '流域补给条件梳理' }),
    sessionSummaryFixture({ sessionId: 'session-4', title: '地下水监测数据字段核对' }),
    sessionSummaryFixture({ sessionId: 'session-5', title: '历史监测资料整理', archived: true }),
  ];
  let resolveOldSearch!: () => void;
  const oldSearch = new Promise<void>(resolve => { resolveOldSearch = resolve; });
  let resolveOldSnapshot!: () => void;
  const oldSnapshot = new Promise<void>(resolve => { resolveOldSnapshot = resolve; });
  const snapshots = new Map([
    ['session-1', ['如何比较不同监测井的地下水位变化？', '先统一时间范围、采样频率和高程基准，再按井比较趋势。缺测时段应保留标记，避免误读为水位突变。']],
    ['session-2', ['如何排查监测井水位异常？', '可结合水位过程线与邻近井记录，核对观测时间、单位和高程基准。']],
  ]);
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (path.endsWith('/sessions') && request.method() === 'POST') {
      expect(request.postDataJSON()).toEqual({ projectId: 'demo' });
      records.unshift(sessionSummaryFixture({ sessionId: 'new-session', title: null, goalRevision: 0 }));
      return route.fulfill({ json: sessionResponseFixture({ sessionId: 'new-session', goalRevision: 0 }) });
    }
    if (path.endsWith('/sessions')) {
      const query = url.searchParams.get('query');
      if (query === '历史') await oldSearch;
      const items = records.filter(item => item.archived === (url.searchParams.get('archived') === 'true')
        && (!query || item.title?.includes(query)));
      const more = items.length > 3 && !url.searchParams.has('cursor');
      const pageItems = url.searchParams.has('cursor') ? items.slice(2) : items.slice(0, 3);
      return route.fulfill({ json: { items: pageItems, nextCursor: more ? 3 : null } });
    }
    const id = path.split('/')[4];
    if (request.method() === 'PATCH') {
      const record = records.find(item => item.sessionId === id)!;
      Object.assign(record, request.postDataJSON());
      return route.fulfill({ json: { sessionId: id, title: record.title, archived: record.archived } });
    }
    if (path.endsWith('/messages')) {
      if (id === 'session-3') {
        await oldSnapshot;
        return route.fulfill({ json: historyFixture({
          sessionId: id, items: [{ messageId: 31, goalRevision: 1, role: 'user', content: '迟到的流域内容' }],
        }) }).catch(() => {});
      }
      const [question, answer] = snapshots.get(id) ?? [];
      return route.fulfill({ json: historyFixture({
        sessionId: id, goalRevision: question ? 1 : 0,
        items: question ? [{ messageId: 1, goalRevision: 1, role: 'user', content: question }] : [],
        turns: answer ? [{ reply: replyFixture(answer) }] : [],
      }) });
    }
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId: id }) });
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    throw new Error(`Unexpected directory request: ${path}`);
  });

  await page.goto('/#/sessions/session-1');
  const directory = page.getByTestId('session-list');
  const search = page.getByRole('searchbox', { name: '搜索对话' });
  await expect(directory.getByRole('link', { name: '华北平原地下水位变化' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('如何比较不同监测井的地下水位变化？', { exact: true })).toBeVisible();
  await directory.getByRole('button', { name: '更早的对话' }).click();
  await expect(directory.getByRole('link')).toHaveCount(4);
  await expect(directory.getByRole('link', { name: '流域补给条件梳理' })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('session-navigation-desktop.png') });

  const oldSearchRequest = page.waitForRequest(request => new URL(request.url()).searchParams.get('query') === '历史');
  await search.fill('历史');
  await oldSearchRequest;
  await search.fill('监测井');
  await expect(directory.getByRole('link')).toHaveCount(2); // Search hit plus the currently open Session.
  await expect(directory.getByRole('link', { name: '监测井水位异常排查' })).toBeVisible();
  resolveOldSearch();
  await expect(directory.getByRole('link', { name: '监测井水位异常排查' })).toBeVisible();
  await search.clear();
  const slowSnapshot = page.waitForRequest(request => request.url().endsWith('/session-3/messages'));
  await directory.getByRole('link', { name: '流域补给条件梳理' }).click();
  await slowSnapshot;
  await directory.getByRole('link', { name: '监测井水位异常排查' }).click();
  await expect(page.getByText('如何排查监测井水位异常？', { exact: true })).toBeVisible();
  resolveOldSnapshot();
  await expect(directory.getByRole('link', { name: '监测井水位异常排查' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('迟到的流域内容')).toHaveCount(0);

  await page.getByRole('button', { name: '重命名' }).click();
  await page.getByRole('textbox', { name: '对话名称' }).fill('监测井水位质量复核');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(directory.getByRole('link', { name: '监测井水位质量复核' })).toBeVisible();
  await page.getByRole('button', { name: '归档', exact: true }).click();
  await expect(page).toHaveURL(/#\/$/);
  await page.getByRole('checkbox', { name: '显示归档' }).check();
  await expect(directory.getByRole('link', { name: '监测井水位质量复核' }).getByText('已归档')).toBeVisible();
  await directory.getByRole('link', { name: '监测井水位质量复核' }).click();
  await page.getByRole('button', { name: '取消归档' }).click();
  await expect(page.getByText('已恢复到最近对话。')).toBeVisible();
  await page.getByRole('checkbox', { name: '显示归档' }).uncheck();
  await expect(directory.getByRole('link', { name: '监测井水位质量复核' })).toBeVisible();
  await page.getByRole('button', { name: '新建对话' }).click();
  await expect(page).toHaveURL(/#\/sessions\/new-session$/);
  await expect(directory.getByRole('link', { name: '新的对话' })).toHaveAttribute('aria-current', 'page');

  await page.setViewportSize({ width: 390, height: 844 });
  const open = page.getByRole('button', { name: '打开会话导航' });
  await open.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: '关闭会话导航' })).toBeFocused();
  await expect(search).toBeVisible();
  await expect(page.getByRole('checkbox', { name: '显示归档' })).toBeVisible();
  await expect(directory.getByRole('button', { name: '更早的对话' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('session-navigation-mobile.png') });
  const audit = await new AxeBuilder({ page }).include('#conversation-sidebar').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(audit.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(open).toBeFocused();
  await open.click();
  await directory.getByRole('link', { name: '华北平原地下水位变化' }).click();
  await expect(directory).toBeHidden();
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeVisible();
  await expect(page).toHaveURL(/#\/sessions\/session-1$/);
});
