import type { Page } from '@playwright/test';

export async function prepareClipboardReadback(page: Page, browserName: string) {
  if (browserName === 'chromium') {
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    return;
  }

  // Firefox and WebKit cannot grant programmatic clipboard read access.
  // Record successful native writes in the page for assertions.
  await page.addInitScript(() => {
    const clipboard = navigator.clipboard;
    if (!clipboard) return;
    const write = clipboard.writeText.bind(clipboard);
    let copied = '';
    clipboard.writeText = async text => {
      await write(text);
      copied = text;
    };
    clipboard.readText = async () => copied;
  });
}
