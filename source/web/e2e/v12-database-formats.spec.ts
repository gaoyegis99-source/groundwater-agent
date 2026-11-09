import { expect, test, type Locator, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

async function chooseFormat(workbench: Locator, format: string) {
  await workbench.getByLabel('格式筛选').selectOption(format);
  const first = workbench.locator('.database-item').first();
  await expect(first.locator('.tag-list')).toContainText(format.toUpperCase());
  await first.click();
  await expect(workbench.locator('.database-preview')).toContainText(format.toUpperCase());
}

async function openWorkbench(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: '数据库' }).click();
  return page.getByTestId('database-workbench');
}

test('数据库工作台显示表格、工作簿、结构树和模型安全元数据', async ({ page }) => {
  const workbench = await openWorkbench(page);

  await chooseFormat(workbench, 'csv');
  await expect(workbench.getByRole('button', { name: '表格', exact: true })).toBeVisible();

  await chooseFormat(workbench, 'parquet');
  await expect(workbench.getByText(/行$/).first()).toBeVisible();

  await chooseFormat(workbench, 'xlsx');
  await expect(workbench.getByLabel('工作表')).toBeVisible();

  await chooseFormat(workbench, 'json');
  await expect(workbench.getByLabel('搜索 JSON')).toBeVisible();

  await chooseFormat(workbench, 'pytorch-state-dict');
  await expect(workbench.getByText('为避免执行不受信任代码，系统不会反序列化模型权重。')).toBeVisible();
});

test('数据库工作台显示地图、栅格和图像并在切换时销毁旧预览', async ({ page }) => {
  const workbench = await openWorkbench(page);

  await chooseFormat(workbench, 'geojson');
  await expect(workbench.getByLabel('GeoJSON 数值着色')).toBeVisible();

  await chooseFormat(workbench, 'geopackage');
  await expect(workbench.getByLabel('GeoPackage 图层')).toBeVisible();

  for (const format of ['geotiff', 'cog']) {
    await chooseFormat(workbench, format);
    await expect(workbench.getByLabel('栅格色带')).toBeVisible();
  }

  await chooseFormat(workbench, 'png');
  await expect(workbench.locator('.artifact-image img')).toBeVisible();
});

test('COG 对象支持单段 Range，非法多段范围返回 416', async ({ request }) => {
  const datasets = await request.get('/api/v12/data/datasets?format=cog&limit=1');
  expect(datasets.ok()).toBeTruthy();
  const dataset = (await datasets.json()).items[0];
  expect(dataset).toBeTruthy();
  const detail = await request.get(`/api/v12/data/versions/${dataset.currentVersion}`);
  expect(detail.ok()).toBeTruthy();
  const version = await detail.json();
  const object = version.objects.find((item: { format: string }) => item.format === 'cog');
  expect(object).toBeTruthy();
  const path = `/api/v12/data/versions/${version.versionId}/objects/${object.objectId}/content`;
  const range = await request.get(path, { headers: { Range: 'bytes=0-63' } });
  expect(range.status()).toBe(206);
  expect(range.headers()['accept-ranges']).toBe('bytes');
  expect(range.headers()['content-range']).toMatch(/^bytes 0-63\/\d+$/);
  expect((await range.body()).length).toBe(64);
  expect((await request.get(path, { headers: { Range: 'bytes=0-1,3-4' } })).status()).toBe(416);
});
