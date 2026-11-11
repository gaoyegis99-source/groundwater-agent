import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, historyFixture, sessionSummaryFixture } from '../src/test-fixtures';

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
