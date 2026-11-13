import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, historyFixture, sessionResponseFixture, sessionSummaryFixture } from '../src/test-fixtures';

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
