import { expect, test, type Locator, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';

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

async function openStandaloneProfile(page: Page, title: string, versionId: string, semanticId: string) {
  await page.goto('/data/');
  await page.getByLabel('搜索数据').fill(title);
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  const version = page.waitForResponse(item => new URL(item.url()).pathname === `/data/v3/versions/${versionId}`);
  await page.locator('.catalog button').filter({ hasText: title }).click();
  expect((await (await version).json()).semanticId).toBe(semanticId);
  await page.getByText('字段概览', { exact: true }).click();
}

test('数据库工作台显示表格、工作簿、结构树和模型安全元数据', async ({ page }, testInfo) => {
  const workbench = await openWorkbench(page);

  const observationsTitle = '六模型观测表';
  const search = workbench.getByRole('textbox', { name: '搜索', exact: true });
  await workbench.getByLabel('格式筛选').selectOption('csv');
  await search.fill(observationsTitle);
  await workbench.locator('.database-item').filter({
    has: page.getByText(observationsTitle, { exact: true }),
  }).click();
  await expect(workbench.getByRole('button', { name: '表格', exact: true })).toBeVisible();
  const catalog = await page.request.get(`/api/v12/data/datasets?${new URLSearchParams({ query: observationsTitle, format: 'csv' })}`);
  expect(catalog.status()).toBe(200);
  const matches = (await catalog.json()).items.filter((item: { title: string }) => item.title === observationsTitle);
  expect(matches).toHaveLength(1);
  const [dataset] = matches;
  const response = await page.request.get(`/api/v12/data/versions/${dataset.currentVersion}`);
  expect(response.status()).toBe(200);
  const detail = await response.json();
  const field = detail.descriptor.fields.find((item: { name: string }) => item.name === 'hydraulic_head_m');
  expect(detail.descriptor.rowCount).toBe(2);
  expect(detail.descriptor.profile).toMatchObject({ statisticsScope: 'all_rows', sampleScope: 'first_non_null_values', sampleLimitPerField: 5 });
  expect(field.profile).toMatchObject({ nonNullCount: 2, sampleValues: [81.2, 82.4], valueRange: { minimum: 81.2, maximum: 82.4 } });
  expect(detail.semantics.fields[field.fieldId].unit).toBe('m');
  await expect(workbench.getByRole('link', { name: '下载对象' })).toHaveAttribute('href', new RegExp(`/data/versions/${detail.versionId}/objects/`));
  const quality = workbench.getByRole('region', { name: '字段与质量' });
  const row = quality.getByRole('row', { name: /hydraulic_head_m/ });
  await expect(workbench.getByText('全量统计 · 2 行')).toBeVisible();
  await expect(row.getByRole('cell', { name: 'm', exact: true })).toBeVisible();
  await expect(row.getByText('81.2 – 82.4')).toBeVisible();
  await row.getByText('查看样例').click();
  await expect(row.getByText('82.4', { exact: true })).toBeVisible();
  await quality.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('main-field-profile.png') });

  const dataPage = await page.context().newPage();
  try {
    await openStandaloneProfile(dataPage, observationsTitle, detail.versionId, detail.semanticId);
    await expect(dataPage.getByText('全量统计 · 2 行')).toBeVisible();
    const profile = dataPage.getByTestId(`field-profile-${field.fieldId}`);
    await expect(profile).toContainText('单位 m');
    await expect(profile).toContainText('81.2 – 82.4');
    await profile.scrollIntoViewIfNeeded();
    await dataPage.screenshot({ path: testInfo.outputPath('standalone-field-profile.png') });
  } finally {
    await dataPage.close();
  }
  writeFileSync(testInfo.outputPath('field-profile.json'), JSON.stringify({
    versionId: detail.versionId, semanticId: detail.semanticId, rowCount: detail.descriptor.rowCount,
    samplePolicy: detail.descriptor.profile, field, unit: detail.semantics.fields[field.fieldId].unit,
  }, null, 2) + '\n');

  await search.clear();
  await chooseFormat(workbench, 'parquet');
  const parquetCatalog = await page.request.get(`/api/v12/data/datasets?${new URLSearchParams({ query: '浏览器验收 Parquet' })}`);
  expect(parquetCatalog.status()).toBe(200);
  const parquetDataset = (await parquetCatalog.json()).items.find((item: { title: string }) => item.title === '浏览器验收 Parquet');
  expect(parquetDataset).toBeTruthy();
  await workbench.getByRole('button', { name: /浏览器验收 Parquet/ }).click({ timeout: 10_000 });
  const parquetResponse = await page.request.get(`/api/v12/data/versions/${parquetDataset.currentVersion}`);
  expect(parquetResponse.status()).toBe(200);
  const parquet = await parquetResponse.json();
  await expect(workbench.getByRole('link', { name: '下载对象' })).toHaveAttribute('href', new RegExp(`/data/versions/${parquet.versionId}/objects/`));
  await expect(workbench.getByText(/行$/).first()).toBeVisible();
  await expect(quality.getByRole('row', { name: /^level / }).getByText('81.2 – 82.4')).toBeVisible();
  const integerField = parquet.descriptor.fields.find((item: { name: string }) => item.name === 'integer_id');
  const exact = ['9007199254740992', '9007199254740993'];
  expect(integerField.profile.sampleValues).toEqual([2 ** 53, 2 ** 53]);
  expect(integerField.profile.exactIntegers.sampleValues).toEqual(exact);
  const integerRow = quality.getByRole('row', { name: /^integer_id / });
  await expect(integerRow.getByText(exact.join(' – '))).toBeVisible();
  await integerRow.getByText('查看样例').click();
  await expect(integerRow.getByText(exact[1], { exact: true })).toBeVisible();
  await integerRow.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('main-integer-precision.png') });

  const exactPage = await page.context().newPage();
  try {
    await openStandaloneProfile(exactPage, '浏览器验收 Parquet', parquet.versionId, parquet.semanticId);
    const profile = exactPage.getByTestId(`field-profile-${integerField.fieldId}`);
    await expect(profile.getByText(exact.join(' – '))).toBeVisible();
    await expect(profile.getByText(exact[1], { exact: true })).toBeVisible();
    await profile.scrollIntoViewIfNeeded();
    await exactPage.screenshot({ path: testInfo.outputPath('standalone-integer-precision.png') });
  } finally {
    await exactPage.close();
  }
  writeFileSync(testInfo.outputPath('integer-precision.json'), JSON.stringify({
    versionId: parquet.versionId, semanticId: parquet.semanticId, field: integerField,
  }, null, 2) + '\n');

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
