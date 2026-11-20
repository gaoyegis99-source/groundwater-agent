import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { once } from 'node:events';
import { createServer, request } from 'node:http';
import { identityStorageScope, scopedStorageKey } from '../src/browser-scope';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, messageReceiptFixture, replyFixture, sessionResponseFixture, sessionResultsFixture, sessionSummaryFixture, taskFixture } from '../src/test-fixtures';

const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));

test('the conversation can select every installed workflow and send the selected request with its attachment', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  const product = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui-porous.v1.json', import.meta.url), 'utf8'));
  const capabilities = capabilitiesForProfile(product);
  const sessionId = 'workflow-choices';
  const reference = dataRef({ format: 'geotiff', mediaType: 'image/tiff' });
  const scope = identityStorageScope(product.productId, capabilities.projectId, capabilities.currentUser.subject);
  await page.addInitScript(({ key, attachments }) => sessionStorage.setItem(key, JSON.stringify(attachments)), {
    key: scopedStorageKey(scope, 'draft-attachments', sessionId),
    attachments: [{ ...reference, familyId: reference.datasetId, name: '研究区.tif' }],
  });
  const posts: unknown[] = [];
  await page.route('**/product-ui.json', route => route.fulfill({ json: product }));
  await page.route('**/api/v12/**', route => {
    const { pathname } = new URL(route.request().url());
    if (pathname.endsWith('/capabilities')) return route.fulfill({ json: capabilities });
    if (pathname.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId })], nextCursor: null } });
    if (pathname.endsWith('/messages') && route.request().method() === 'POST') {
      expect(pathname).toBe(`/api/v12/sessions/${sessionId}/messages`);
      const body = route.request().postDataJSON();
      posts.push(body);
      return route.fulfill({ status: 202, json: messageReceiptFixture({
        sessionId, goalRevision: 2, request: body, requestId: body.requestId, routingStatus: 'DECIDED',
      }) });
    }
    if (pathname.endsWith('/messages')) return route.fulfill({ json: historyFixture({ sessionId }) });
    if (pathname.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId }) });
    if (pathname.endsWith('/events') || pathname.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    if (/^\/api\/v12\/sessions\/[^/]+\/work-item$/.test(pathname) && route.request().method() === 'GET') return route.fulfill({ json: { context: null } });
    throw new Error(`Unexpected workflow selection request: ${pathname}`);
  });
  await page.goto(`/#/sessions/${sessionId}`);
  const picker = page.getByRole('button', { name: '工作流', exact: true });
  const input = page.getByRole('textbox', { name: '消息输入框' });
  const attachment = page.getByTestId('pending-attachments').getByRole('button', { name: /^研究区\.tif/ });
  const context = '请在结果中保留坐标参考系。';
  await expect(attachment).toBeVisible();
  await input.fill(context);
  await picker.click();
  const catalog = page.getByRole('dialog', { name: '能力目录' });
  await expect(catalog.getByRole('button', { name: /^在会话中使用/ })).toHaveCount(18);
  for (const entry of await catalog.getByRole('button', { name: /^在会话中使用/ }).all()) await expect(entry).toBeEnabled();
  await page.keyboard.press('Escape');
  await expect(picker).toBeFocused();
  await picker.click();
  const search = catalog.getByRole('searchbox', { name: '搜索工作流' });
  await search.fill('迁移学习回测');
  await catalog.getByRole('button', { name: '在会话中使用迁移学习回测' }).click();
  const transfer = page.getByRole('region', { name: '选中的工作流' });
  await expect(transfer.getByText(/资料已绑定 0\/19/)).toBeVisible();
  await page.getByRole('button', { name: '撤销填入' }).click();
  await picker.click();
  await search.fill('栅格地图准备');
  await expect(catalog.getByRole('button', { name: /^在会话中使用/ })).toHaveCount(1);
  await catalog.getByRole('button', { name: '在会话中使用孔隙型栅格地图准备' }).click();
  await expect(input).toBeFocused();
  await expect(input).toHaveValue(new RegExp(`^请准备执行「孔隙型栅格地图准备」工作流。.*\\n\\n${context}$`));
  await expect(attachment).toBeVisible();
  expect(posts).toHaveLength(0);
  await page.getByRole('button', { name: '撤销填入' }).click();
  await expect(input).toHaveValue(context);
  await picker.click();
  await search.fill('栅格地图准备');
  await catalog.getByRole('button', { name: '在会话中使用孔隙型栅格地图准备' }).click();
  const selection = page.getByRole('region', { name: '选中的工作流' });
  await selection.getByText('资料已绑定 0/1').click();
  await selection.getByRole('combobox').first().selectOption({ label: '研究区.tif' });
  await expect(selection.getByText('资料已绑定 1/1')).toBeVisible();
  const message = '请检查坐标系后准备执行。';
  await input.fill(message);
  await page.reload();
  await expect(selection.getByText('资料已绑定 1/1')).toBeVisible();
  await expect(input).toHaveValue(message);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ message, requestedMode: 'DIALOGUE', inputReferences: [reference], workflowSelection: {
    modelId: 'groundwater.porous-map', modelVersion: '1.0.0', workflowId: 'groundwater.porous-map', workflowVersion: '1.0.0',
    inputBindings: { geoTiffVersionId: 0 },
  } });
});

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
    if (/^\/api\/v12\/sessions\/[^/]+\/work-item$/.test(url.pathname) && route.request().method() === 'GET') return route.fulfill({ json: { context: null } });
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

test('a second tab can create a conversation after one workspace login', async ({ page, context, baseURL }) => {
  const capabilities = capabilitiesForProfile(profile);
  const server = createServer((incoming, response) => {
    const url = new URL(incoming.url ?? '/', baseURL);
    const path = url.pathname;
    const json = (value: unknown, status = 200) => {
      response.writeHead(status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(value));
    };
    if (path === '/product-ui.json') return json(profile);
    if (path.endsWith('/auth/login')) {
      response.setHeader('Set-Cookie', 'gw_research_session=test-session; Path=/; HttpOnly; SameSite=Strict');
      return json({ currentUser: capabilities.currentUser, csrfToken: 'test-shared-csrf' });
    }
    if (path.startsWith('/api/v12/')) {
      if (!incoming.headers.cookie?.includes('gw_research_session=test-session')) {
        return json({ code: 'AUTHENTICATION_REQUIRED' }, 401);
      }
      if (path.endsWith('/capabilities')) return json(capabilities);
      if (path.endsWith('/sessions') && incoming.method === 'POST') {
        if (incoming.headers['x-csrf-token'] !== 'test-shared-csrf') return json({ code: 'ROLE_FORBIDDEN' }, 403);
        return json(sessionResponseFixture(), 201);
      }
      if (path.endsWith('/sessions') || path.endsWith('/results')) return json({ items: [], nextCursor: null });
      if (path.endsWith('/messages')) return json(historyFixture());
      if (path.endsWith('/events') || path.endsWith('/preview')) {
        response.writeHead(200, { 'Content-Type': 'text/event-stream' }).end(': connected\n\n');
        return;
      }
      return json({ code: 'UNEXPECTED_WORKSPACE_REQUEST' }, 404);
    }
    const forward = request(url, { method: incoming.method }, upstream => {
      response.writeHead(upstream.statusCode ?? 502, upstream.headers);
      upstream.pipe(response);
    });
    forward.on('error', () => response.destroy());
    incoming.pipe(forward);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Local workspace fixture address unavailable');
    const url = `http://127.0.0.1:${address.port}/`;
    await page.goto(url);
    for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }]) {
      await page.setViewportSize(viewport);
      await expect(page.getByLabel('用户名')).toBeInViewport();
      await expect(page.getByRole('button', { name: '登录', exact: true })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    }
    await page.getByLabel('用户名').fill('researcher');
    await page.getByLabel('密码').fill('fixture-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page.getByRole('button', { name: '新建对话' })).toBeEnabled();

    const other = await context.newPage();
    await other.goto(url);
    const created = other.waitForResponse(response =>
      response.url().endsWith('/api/v12/sessions') && response.request().method() === 'POST',
    );
    await other.getByRole('button', { name: '新建对话' }).click();
    expect((await created).status()).toBe(201);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test('UI-03A directory supports Session operations and navigation at desktop widths', async ({ page }, info) => {
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
    if (/^\/api\/v12\/sessions\/[^/]+\/work-item$/.test(path) && route.request().method() === 'GET') return route.fulfill({ json: { context: null } });
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
  await expect(directory.getByRole('link', { name: '监测井水位质量复核' })).toHaveAccessibleDescription(/已归档/);
  await directory.getByRole('link', { name: '监测井水位质量复核' }).click();
  await page.getByRole('button', { name: '取消归档' }).click();
  await expect(page.getByText('已恢复到最近对话。')).toBeVisible();
  await page.getByRole('checkbox', { name: '显示归档' }).uncheck();
  await expect(directory.getByRole('link', { name: '监测井水位质量复核' })).toBeVisible();
  await page.getByRole('button', { name: '新建对话' }).click();
  await expect(page).toHaveURL(/#\/sessions\/new-session$/);
  await expect(directory.getByRole('link', { name: '新的对话' })).toHaveAttribute('aria-current', 'page');

  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(search).toBeVisible();
  await expect(page.getByRole('checkbox', { name: '显示归档' })).toBeVisible();
  await expect(directory.getByRole('button', { name: '更早的对话' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('session-navigation-desktop-1024.png') });
  const audit = await new AxeBuilder({ page }).include('#conversation-sidebar').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(audit.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await directory.getByRole('link', { name: '华北平原地下水位变化' }).click();
  await expect(directory).toBeVisible();
});

test('a published inversion weight starts a pinned sampling draft without submitting a turn', async ({ page }) => {
  const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui-porous.v1.json', import.meta.url), 'utf8'));
  const capabilities = capabilitiesForProfile(profile);
  const task = taskFixture({ modelId: 'groundwater.porous-inversion-training', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const weights = dataRef({ outputName: 'porous-inversion-weights', format: 'binary', mediaType: 'application/octet-stream' });
  const sampling = dataRef({ versionId: 'sampling-config', objectId: 'sampling-config-object', outputName: '采样配置' });
  const scope = identityStorageScope(profile.productId, capabilities.projectId, capabilities.currentUser.subject);
  await page.addInitScript(({ key, attachments }) => sessionStorage.setItem(key, JSON.stringify(attachments)), {
    key: scopedStorageKey(scope, 'draft-attachments', 'session-1'),
    attachments: [{ ...sampling, familyId: sampling.datasetId, name: '采样配置.json' }],
  });
  const posts: unknown[] = [];
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/api/v12/**', route => {
    const { pathname } = new URL(route.request().url());
    if (pathname.endsWith('/capabilities')) return route.fulfill({ json: capabilities });
    if (pathname.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (pathname.endsWith('/messages') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      posts.push(body);
      return route.fulfill({ status: 202, json: messageReceiptFixture({ request: body, requestId: body.requestId }) });
    }
    if (pathname.endsWith('/messages')) return route.fulfill({ json: historyFixture({
      items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '检查反演训练成果' }], turns: [{ executionTasks: [task] }],
    }) });
    if (pathname.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([{
      workflowRunId: task.workflowRunId, goalRevision: 1, agentRunId: task.agentRunId, toolCallId: task.toolCallId,
      modelId: task.modelId, status: 'COMPLETE', remoteOutputs: [weights],
    }]) });
    if (pathname.endsWith(`/data/versions/${weights.versionId}`)) return route.fulfill({ json: dataDetail({ objects: [{
      objectId: weights.objectId, checksum: weights.checksum, size: weights.sizeBytes,
      format: weights.format, mediaType: weights.mediaType, role: 'raw',
    }] }) });
    if (pathname.endsWith('/events') || pathname.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    if (/^\/api\/v12\/sessions\/[^/]+\/work-item$/.test(pathname) && route.request().method() === 'GET') return route.fulfill({ json: { context: null } });
    throw new Error(`Unexpected continuation request: ${pathname}`);
  });
  await page.goto('/#/sessions/session-1');
  await page.getByRole('button', { name: '使用这份权重准备采样' }).click();
  const preparation = page.getByRole('region', { name: '选中的工作流' });
  await expect(preparation.getByText('孔隙型反演采样', { exact: true })).toBeVisible();
  await preparation.getByText('资料已绑定 1/2').click();
  await expect(preparation.getByRole('combobox', { name: '反演训练权重', exact: true })).toBeDisabled();
  expect(posts).toHaveLength(0);
  await preparation.getByRole('combobox', { name: '采样配置 JSON', exact: true }).selectOption({ label: '采样配置.json' });
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ requestedMode: 'DIALOGUE', inputReferences: [sampling, weights], workflowSelection: {
    modelId: 'groundwater.porous-inversion-sampling', inputBindings: { inversionWeightsVersionId: 1, inversionSamplingVersionId: 0 },
    continuation: { runId: task.workflowRunId, outputName: 'porous-inversion-weights' },
  } });
});
