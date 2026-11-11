import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const createdAt = "2026-10-17T10:00:00Z";
const checksum = `sha256:${"a".repeat(64)}`;
const capabilities = {
  version: "2.0.0", productId: "sample", project: "project-1",
  currentUser: { subject: "operator-1", displayName: "Operator", roles: ["admin"] },
  permissions: { catalogRead: true, retrievalCreate: true, evidenceRead: true, evidenceValidate: true, importWrite: true, lifecycleWrite: true, accessWrite: true },
  identity: "local", writeEnabled: true, schema: "knowledge-package.v1", databaseSchema: 6,
  retrievalScopes: ["general", "bound", "combined"], defaultScope: "general_without_binding_combined_with_binding",
  dataRetry: { maxAttempts: 5, delaysSeconds: [5, 15], deadlineSeconds: 600 },
  modelProfile: "embedding-v1", indexGeneration: "generation-1", vectorSearch: "exact",
  maxBodyBytes: 16_777_216, dataConfigured: false, openapi: "/openapi.json",
};
const document = {
  knowledgeId: "manual-1", title: "运行手册", contentVersion: "1.0.0", kind: "knowledge", language: "zh-CN", summary: "应用操作说明",
  sources: [{ sourceId: "source-1", kind: "MANUAL", title: "内部手册", publisher: "Operator", reviewAfter: "2099-01-01", lifecycleStatus: "ACTIVE" }],
  units: [
    { unitId: "unit-1", content: "按受控流程操作。", sourceRefs: ["source-1"], requiredUnits: [] },
    { unitId: "unit-2", content: "阅读知识时，先确认所选版本与引用来源。记录适用的数据范围、采样时间和单位，再将需要复核的条目交给项目负责人。\n保留历史版本，以便之后还原当时的分析依据。", sourceRefs: ["source-1"], requiredUnits: [] },
  ],
  bindings: [], aliases: [],
};
const version = {
  versionId: "version-1", documentId: "document-1", body: document, checksum, importId: "import-1",
  verifiedAt: createdAt, createdAt, lifecycleRevision: 1, status: "ACTIVE", validThrough: null,
  reviewAfter: "2099-01-01", lifecycleActor: { subject: "operator-1" },
};
const documentSummary = {
  documentId: "document-1", currentVersion: "version-1", title: "运行手册", summary: "应用操作说明",
  kind: "knowledge", language: "zh-CN", createdAt, modifiedAt: createdAt, status: "ACTIVE",
  reviewAfter: "2099-01-01", validThrough: null, reviewState: "current", sourceKinds: ["MANUAL"],
  sourceCount: 1, bindingCount: 0, hasDataBinding: false,
};
const documentFacets = {
  statuses: [{ name: "ACTIVE", count: 1 }], reviewStates: [{ name: "current", count: 1 }],
  sourceKinds: [{ name: "MANUAL", count: 1 }], knowledgeKinds: [{ name: "knowledge", count: 1 }],
  bindingStates: [{ name: "general", count: 1 }],
};
const directoryExamples = [
  documentSummary,
  { ...documentSummary, documentId: "document-2", currentVersion: "version-2", title: "水位观测质量检查", summary: "采样时间、水位单位与异常记录的复核流程" },
  { ...documentSummary, documentId: "document-3", currentVersion: "version-3", title: "地下水趋势分析", summary: "分析前的数据覆盖期、采样间隔与缺失检查" },
];
const emptyActivity = { items: [], generatedAt: createdAt };

async function assertNoSeriousAxe(page: Page) {
  const result = await new AxeBuilder({ page }).analyze();
  expect(result.violations.filter(item => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
}

async function capture(page: Page, path: string, fullPage = true) {
  await expect(async () => {
    await page.screenshot({ path, fullPage, animations: "disabled" });
  }).toPass({ timeout: 5000, intervals: [100, 250, 500] });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/data/v3/activity**", route => route.fulfill({ json: emptyActivity }));
});

async function routeWorkspace(page: Page, withDocument = false, capabilityOverrides: Partial<typeof capabilities> = {}, directory = withDocument ? [documentSummary] : []) {
  let lifecycleStatus: "ACTIVE" | "WITHDRAWN" = "ACTIVE";
  let lifecycleRevision = 1;
  let lifecycleReviewAfter = "2099-01-01";
  await page.route("**/knowledge/v3/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/knowledge/v3/", "");
    let response: unknown;
    if (path === "capabilities") response = { ...capabilities, ...capabilityOverrides };
    else if (path === "activity") response = emptyActivity;
    else if (path === "imports") response = { items: [], nextCursor: null };
    else if (path === "imports/preflight" && request.method() === "POST") {
      const pkg = request.postDataJSON() as { documents: Array<{ knowledgeId: string; title: string; contentVersion: string; expectedVersion?: string; sources: Array<{ reviewAfter: string }>; bindings?: unknown[] }> };
      response = {
        projectMatches: true, canSubmit: true, generatedAt: createdAt,
        checks: [
          { name: "package_schema", state: "PASSED", detail: "知识包结构和引用关系有效" },
          { name: "expected_versions", state: "PASSED", detail: "全部 expectedVersion 与当前目录一致" },
          { name: "embedding_index", state: "PENDING", detail: "提交后生成索引" },
        ],
        documents: pkg.documents.map(item => ({
          knowledgeId: item.knowledgeId, title: item.title, operation: "CREATE", currentVersion: null,
          expectedVersion: item.expectedVersion ?? null, expectedVersionMatches: true,
          currentContentVersion: null, nextContentVersion: item.contentVersion,
          contentVersionChanged: true, contentChanged: true,
          sourceCount: item.sources.length, nextReviewAfter: item.sources.map(source => source.reviewAfter).sort()[0],
          bindingAdded: item.bindings?.length ?? 0, bindingRemoved: 0, bindingUnchanged: 0,
        })),
      };
    }
    else if (path === "documents") response = { items: directory.map(item => ({ ...item, status: item.documentId === "document-1" ? lifecycleStatus : item.status })), nextCursor: null, totalMatches: directory.length, facets: directory.length ? Object.fromEntries(Object.entries(documentFacets).map(([key, items]) => [key, items.map(item => ({ ...item, count: directory.length }))])) : { statuses: [], reviewStates: [], sourceKinds: [], knowledgeKinds: [], bindingStates: [] } };
    else if (path === "documents/document-1/versions") response = { items: [{ versionId: "version-1", checksum, createdAt }], nextCursor: null };
    else if (path === "versions/version-1") response = { ...version, status: lifecycleStatus, lifecycleRevision, reviewAfter: lifecycleReviewAfter };
    else if (path === "lifecycle/batch/preflight" && request.method() === "POST") {
      const body = request.postDataJSON() as { status: "ACTIVE" | "WITHDRAWN"; reviewAfter: string; validThrough: string | null };
      response = {
        canSubmit: true, generatedAt: createdAt,
        items: [{
          documentId: "document-1", title: "运行手册", versionId: "version-1",
          currentRevision: lifecycleRevision, currentStatus: lifecycleStatus, targetStatus: body.status,
          currentValidThrough: null, targetValidThrough: body.validThrough,
          currentReviewAfter: lifecycleReviewAfter, targetReviewAfter: body.reviewAfter,
          changed: lifecycleStatus !== body.status || lifecycleReviewAfter !== body.reviewAfter,
        }],
      };
    }
    else if (path === "lifecycle/batch" && request.method() === "POST") {
      const body = request.postDataJSON() as { items: Array<{ status: "ACTIVE" | "WITHDRAWN"; reviewAfter: string }> };
      lifecycleStatus = body.items[0].status;
      lifecycleReviewAfter = body.items[0].reviewAfter;
      lifecycleRevision += 1;
      response = { items: [{ ...version, status: lifecycleStatus, lifecycleRevision, reviewAfter: lifecycleReviewAfter }], updatedAt: createdAt };
    }
    else if (path === "versions/version-1/lifecycle" && request.method() === "POST") {
      lifecycleStatus = request.postDataJSON().status;
      lifecycleReviewAfter = request.postDataJSON().reviewAfter;
      lifecycleRevision += 1;
      response = { ...version, status: lifecycleStatus, lifecycleRevision, reviewAfter: lifecycleReviewAfter };
    }
    else if (path === "versions/version-1/lifecycle") response = { items: [{ versionId: "version-1", revision: lifecycleRevision, status: lifecycleStatus, validThrough: null, reviewAfter: lifecycleReviewAfter, reason: "测试更新", actor: { subject: "operator-1" }, createdAt }], nextCursor: null };
    else throw new Error(`Unexpected request: ${request.method()} ${path}`);
    await route.fulfill({ json: response });
  });
}

test("renders the catalog and resets the lifecycle action after an update", async ({ page }) => {
  await routeWorkspace(page, true);
  await page.goto("/knowledge/");
  await page.getByText("系统信息").click();
  await expect(page.getByText(/operator-1/)).toBeVisible();
  await page.getByRole("button", { name: /运行手册/ }).click();
  await expect(page.locator("#version-body")).toContainText("1.0.0");
  await assertNoSeriousAxe(page);
  await page.getByText("生命周期与变更记录", { exact: true }).click();
  await page.getByRole("button", { name: "撤回此版本" }).click();
  const dialog = page.getByRole("dialog", { name: "撤回知识版本？" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("不会再作为有效证据返回");
  await expect(dialog.getByRole("combobox")).toHaveCount(0);
  await assertNoSeriousAxe(page);
  await page.getByLabel("原因").fill("测试撤回");
  await page.getByRole("button", { name: "确认撤回" }).click();
  await expect(page.getByRole("button", { name: "恢复此版本" })).toBeVisible();
  await page.getByRole("button", { name: "恢复此版本" }).click();
  await expect(page.getByRole("dialog", { name: "恢复知识版本？" })).toContainText("重新参与证据检索");
  await page.getByLabel("原因").fill("测试恢复");
  await page.getByRole("button", { name: "确认恢复" }).click();
  await expect(page.getByRole("button", { name: "撤回此版本" })).toBeVisible();
});

test("reads knowledge in a drawer, clears stale content on navigation, and returns focus when closed", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await routeWorkspace(page, true);
  const secondSummary = { ...documentSummary, documentId: "document-2", currentVersion: "version-2", title: "水位监测规范", summary: "采样与复核的适用条件" };
  const secondVersion = { ...version, versionId: "version-2", documentId: "document-2", body: { ...document, knowledgeId: "manual-2", title: secondSummary.title, summary: secondSummary.summary, units: [{ ...document.units[0], content: "记录采样时间并复核水位。" }] } };
  let releaseSecond = () => {};
  const secondReady = new Promise<void>(resolve => { releaseSecond = resolve; });
  await page.route(/\/knowledge\/v3\/documents\?/, route => route.fulfill({ json: { items: [documentSummary, secondSummary], nextCursor: null, totalMatches: 2, facets: documentFacets } }));
  await page.route("**/knowledge/v3/documents/document-2/versions?*", route => route.fulfill({ json: { items: [{ versionId: "version-2", checksum, createdAt }], nextCursor: null } }));
  await page.route("**/knowledge/v3/versions/version-2/lifecycle", route => route.fulfill({ json: { items: [], nextCursor: null } }));
  await page.route("**/knowledge/v3/versions/version-2", async route => { await secondReady; await route.fulfill({ json: secondVersion }); });
  await page.goto("/knowledge/?q=监测");
  const firstEntry = page.getByRole("button", { name: /运行手册/ });
  await firstEntry.click();
  const reader = page.getByRole("dialog", { name: "知识阅读" });
  await expect(reader.getByRole("region", { name: "知识正文" })).toContainText("按受控流程操作。");
  await expect(reader.getByText(checksum, { exact: true })).not.toBeVisible();
  await reader.getByRole("button", { name: "下一篇" }).click();
  await expect(reader.getByRole("status")).toContainText("正在读取不可变知识版本");
  await expect(reader.getByText("按受控流程操作。", { exact: true })).toHaveCount(0);
  releaseSecond();
  await expect(reader.getByRole("region", { name: "知识正文" })).toContainText("记录采样时间并复核水位。");
  await expect(reader.getByRole("heading", { name: "水位监测规范" })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("version")).toBe("version-2");
  await page.keyboard.press("Escape");
  await expect(reader).toHaveCount(0);
  await expect(firstEntry).toBeFocused();
  await expect(page.getByRole("textbox", { name: "目录关键词" })).toHaveValue("监测");
  const secondEntry = page.getByRole("button", { name: /水位监测规范/ });
  await secondEntry.click();
  await reader.getByRole("button", { name: "关闭知识" }).click();
  await expect(reader).toHaveCount(0);
  await expect(secondEntry).toBeFocused();
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }]) {
  test(`shows readable knowledge with sources at ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await routeWorkspace(page, true, {}, directoryExamples);
    await page.goto("/knowledge/");
    await expect(page.locator(".catalog > li")).toHaveCount(3);
    await capture(page, testInfo.outputPath(`knowledge-directory-${viewport.width}.png`));
    await page.getByRole("button", { name: /运行手册/ }).click();
    const reader = page.getByRole("dialog", { name: "知识阅读" });
    await expect(reader.getByRole("region", { name: "知识正文" })).toContainText("按受控流程操作。");
    await expect(reader.locator(".knowledge-source-overview")).toContainText("内部手册");
    await expect(reader.getByRole("combobox", { name: "查看版本" })).toHaveValue("version-1");
    await capture(page, testInfo.outputPath(`knowledge-reader-${viewport.width}.png`), false);
    await assertNoSeriousAxe(page);
    if (viewport.width === 1366) {
      await reader.getByText("版本与来源详情", { exact: true }).click();
      await reader.getByText("生命周期与变更记录", { exact: true }).click();
      await reader.getByRole("button", { name: "撤回此版本" }).click();
      await expect(page.getByRole("dialog", { name: "撤回知识版本？" })).toBeVisible();
      await capture(page, testInfo.outputPath("knowledge-governance-1366.png"), false);
    }
  });
}

test("preflights and atomically applies batch lifecycle governance", async ({ page }) => {
  await routeWorkspace(page, true);
  await page.goto("/knowledge/");
  await page.getByRole("checkbox", { name: "选择 运行手册 进行批量治理" }).check();
  await expect(page.getByText("已选择 1 / 100 份")).toBeVisible();
  await page.getByRole("button", { name: "批量治理（1）" }).click();
  const setup = page.getByRole("dialog", { name: "批量治理 1 份知识" });
  await setup.getByLabel("统一原因").fill("来源失效，批量撤回等待复核");
  await setup.getByRole("button", { name: "预检变更" }).click();
  const confirmation = page.getByRole("dialog", { name: "确认批量治理" });
  await expect(confirmation).toContainText("任一版本或修订号已变化时，整批都不会写入");
  await expect(confirmation.getByRole("row", { name: /运行手册/ })).toContainText("有效");
  await expect(confirmation.getByRole("row", { name: /运行手册/ })).toContainText("已撤回");
  await assertNoSeriousAxe(page);
  await confirmation.getByRole("button", { name: "确认应用到 1 份知识" }).click();
  await expect(page.getByText("已选择 0 / 100 份")).toBeVisible();
  await expect(page.locator(".catalog")).toContainText("已撤回");
});

test("clears stale retrieval evidence when the next request fails", async ({ page }) => {
  await routeWorkspace(page);
  let attempts = 0;
  const retrievalBodies: Record<string, unknown>[] = [];
  await page.route("**/knowledge/v3/retrievals", async route => {
    attempts += 1;
    retrievalBodies.push(route.request().postDataJSON());
    if (attempts === 1) {
      await route.fulfill({ status: 200, json: {
        schemaVersion: "evidence-bundle.v1",
        status: "OK",
        items: [{
          versionId: "version-1", unitId: "unit-1", checksum, status: "OK",
          content: "第一轮证据不应在失败后继续显示。", knowledgeId: "manual-1",
          documentTitle: "运行手册", relevanceScore: 1, rank: 1, rankingReason: "向量语义与关键词共同命中",
          scope: "GENERAL", bindingMatch: "GENERAL", reviewDue: false,
          sources: document.sources, locator: { section: "1" },
        }],
        coverage: { retrieval_exhaustive: true, required_context_complete: true, strategy: "lexical" },
        budget: {}, timingsMs: {}, indexGeneration: "generation-1", knowledgeRevision: 1,
        observedAt: createdAt, receiptId: "receipt-1",
      } });
      return;
    }
    await route.fulfill({ status: 503, contentType: "application/problem+json", body: JSON.stringify({
      type: "urn:sample:problem:DATA_UNAVAILABLE", title: "Unavailable", status: 503,
      detail: "unavailable", code: "KNOWLEDGE_UNAVAILABLE", requestId: "request-2", traceId: "trace-2", errors: [],
    }) });
  });
  await page.goto("/knowledge/");
  await page.getByRole("tab", { name: /证据检索/ }).click();
  await page.getByLabel("检索问题").fill("第一轮问题");
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  await expect(page.locator("#evidence")).toContainText("第一轮证据");
  await expect(page.locator("#evidence")).toContainText("100% 相关");
  expect(retrievalBodies[0]).toMatchObject({ topK: 5, minScore: 0, metadataFilters: { sourceKinds: [] } });
  await expect(page.getByLabel("本次检索上下文")).toContainText("第一轮问题");
  await page.getByLabel("检索问题").fill("第二轮问题");
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("KNOWLEDGE_UNAVAILABLE");
  await expect(page.locator("#evidence")).toHaveCount(0);
  await expect(page.getByLabel("本次检索上下文")).toHaveCount(0);
});

test("offers manual retry and a clean new-import path for terminal imports", async ({ page }) => {
  await routeWorkspace(page);
  const recoveryKey = "knowledge-import:v1:sample/project-1/operator-1";
  await page.addInitScript(({ key, timestamp }) => {
    sessionStorage.setItem(key, JSON.stringify({ version: 1, id: "import-failed", createdAt: timestamp }));
  }, { key: recoveryKey, timestamp: createdAt });
  let retried = false;
  await page.route("**/knowledge/v3/imports/import-failed**", async route => {
    const request = route.request();
    if (request.url().endsWith("/retry")) {
      retried = true;
      await route.fulfill({ status: 202, json: {
        importId: "import-failed", state: "QUEUED", dataAttempts: 2, attempt: 2,
        nextRetryAt: null, retryDeadline: null, retryGeneration: 1, lastError: null,
        error: null, result: null, actor: { subject: "operator-1" }, createdAt, updatedAt: createdAt,
      } });
      return;
    }
    await route.fulfill({ json: {
      importId: "import-failed", state: retried ? "QUEUED" : "FAILED", dataAttempts: 2, attempt: 2,
      nextRetryAt: null, retryDeadline: null, retryGeneration: retried ? 1 : 0,
      lastError: retried ? null : { code: "DATA_RETRY_EXHAUSTED" },
      error: retried ? null : { code: "DATA_RETRY_EXHAUSTED" }, result: null,
      actor: { subject: "operator-1" }, createdAt, updatedAt: createdAt,
    } });
  });
  await page.goto("/knowledge/?view=import&import=import-failed");
  await expect(page.getByRole("button", { name: "重新校验并重试" })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("import")).toBe("import-failed");
  await page.getByRole("button", { name: "重新校验并重试" }).click();
  await expect.poll(() => retried).toBe(true);
  await expect(page.locator("#import-status")).toContainText("等待处理");

  // A terminal receipt never traps the operator: clearing it exposes a fresh file chooser.
  retried = false;
  await page.reload();
  await expect(page.getByRole("button", { name: "导入另一个知识包" })).toBeVisible();
  await page.getByRole("button", { name: "导入另一个知识包" }).click();
  await expect(page.getByLabel("知识包文件")).toBeVisible();
  await expect.poll(() => page.evaluate(key => sessionStorage.getItem(key), recoveryKey)).toBeNull();
  await expect.poll(() => new URL(page.url()).searchParams.get("import")).toBeNull();
});

test("previews append or replace effects before applying one binding to every document", async ({ page }) => {
  await routeWorkspace(page, false, { dataConfigured: true });
  const dataCapabilities = {
    version: "1", productId: "sample", project: "project-1",
    currentUser: { subject: "operator-1", displayName: "Operator", roles: ["admin"] },
    permissions: { catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true, userUploadCreate: true, managedPublicationWrite: true, semanticWrite: true, accessWrite: true, governanceRead: true, qualityWrite: true, mlopsWrite: true },
    identityMode: "local", writeEnabled: true, formats: ["csv"], formatMediaTypes: { csv: "text/csv" },
    query: {}, preview: {}, limits: {}, contracts: {}, openapi: "/openapi.json",
  };
  const dataVersion = {
    versionId: "data-v1", datasetId: "data-1", currentVersion: "data-v1", publicationId: "publication-1",
    semanticId: "semantic-1", title: "水位观测", descriptor: { policy: "FILE_QUERYABLE", fields: [{ fieldId: "well", name: "井号", type: "string", nullCount: 0 }], rowCount: 1, quality: {} },
    semantics: { description: "水位观测", fields: { well: { meaning: "井号", unit: null } }, source: "test", limitations: [] },
    objects: [{ objectId: "object-1", size: 10, checksum, role: "raw", format: "csv", mediaType: "text/csv" }],
    inputVersions: [], createdAt, tags: [], project: "project-1",
  };
  let datasetRequests = 0;
  let versionRequests = 0;
  await page.route("**/data/v3/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/data/v3/", "");
    if (path === "datasets") datasetRequests += 1;
    if (path === "datasets/data-1/versions") versionRequests += 1;
    const response = path === "capabilities" ? dataCapabilities
      : path === "activity" ? emptyActivity
      : path === "datasets" ? { items: [{ datasetId: "data-1", project: "project-1", title: "水位观测", tags: [], currentVersion: "data-v1", createdAt, semantics: { description: "水位观测" }, formats: ["csv"] }], nextCursor: null, matchBasis: "catalog" }
      : path === "datasets/data-1/versions" ? { items: [{ versionId: "data-v1", semanticId: "semantic-1", createdAt }], nextCursor: null }
      : path === "versions/data-v1" ? dataVersion
      : path === "versions/data-v1/semantics" ? { items: [{ semanticId: "semantic-1", createdAt, actor: { subject: "operator-1" } }], nextCursor: null }
      : null;
    if (!response) throw new Error(`Unexpected Data request: ${path}`);
    await route.fulfill({ json: response });
  });
  const existingBinding = { serviceBinding: "data-primary", projectId: "project-1", datasetId: "old-data", versionId: "old-version", semanticId: "old-semantic", fieldId: null };
  const pkg = {
    schemaVersion: "knowledge-package.v1", projectId: "project-1", idempotencyKey: "binding-preview-test",
    documents: [
      { ...document, knowledgeId: "knowledge-a", title: "已有绑定", bindings: [existingBinding] },
      { ...document, knowledgeId: "knowledge-b", title: "无绑定", bindings: [] },
    ],
  };
  await page.goto("/knowledge/");
  await page.getByRole("tab", { name: /导入更新/ }).click();
  await page.getByLabel("知识包文件").setInputFiles({ name: "knowledge.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(pkg)) });
  await expect.poll(() => datasetRequests).toBe(1);
  await expect(page.locator(".package-preview .binding-catalog-state")).toHaveText("已加载 1 个数据集");
  const importPreview = page.locator(".package-preview");
  const datasetCombo = importPreview.getByRole("combobox", { name: "数据集" });
  await datasetCombo.fill("水位");
  await datasetCombo.press("ArrowDown");
  const datasetOption = page.getByRole("option", { name: /水位观测/ });
  await expect(datasetOption).toBeVisible();
  await datasetOption.click();
  await expect.poll(() => versionRequests).toBe(1);
  await expect(importPreview.locator(".binding-version-state")).toHaveText("已加载 1 个不可变版本");
  const versionCombo = importPreview.getByRole("combobox", { name: "不可变版本" });
  await versionCombo.fill("data-v1");
  await versionCombo.press("ArrowDown");
  const versionOption = page.getByRole("option", { name: /data-v1/ });
  await expect(versionOption).toBeVisible();
  await versionOption.click();
  await expect(importPreview.getByRole("button", { name: "预览并应用到全部文档" })).toBeEnabled();
  await page.getByLabel("应用方式").selectOption("replace");
  await page.getByRole("button", { name: "预览并应用到全部文档" }).click();
  await expect(page.getByRole("button", { name: "预览并应用到全部文档" })).toHaveAttribute("aria-expanded", "true");
  const dialog = page.getByRole("dialog", { name: "确认批量应用 Data 绑定" });
  await expect(dialog).toContainText("现有绑定都会被当前选择替换");
  await expect(dialog.getByRole("row", { name: /已有绑定 1 1/ })).toBeVisible();
  await expect(dialog.getByRole("row", { name: /无绑定 0 1/ })).toBeVisible();
  await dialog.getByRole("button", { name: "确认应用" }).click();
  await page.getByText("技术详情：只读知识包 JSON").click();
  await expect(page.locator(".package-preview pre")).toContainText("data-v1");
  await expect(page.locator(".package-preview pre")).not.toContainText("old-version");
});


for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`has no serious accessibility findings at ${viewport.width}x${viewport.height} and 200%`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await routeWorkspace(page);
    await page.goto("/knowledge/");
    await expect(page.getByRole("heading", { name: "知识管理" })).toBeVisible();
    await expect(page.getByText("没有找到匹配的知识")).toBeVisible();
    await expect(async () => {
      await page.screenshot({ path: testInfo.outputPath(`knowledge-catalog-${viewport.width}.png`), fullPage: true, animations: "disabled" });
    }).toPass({ timeout: 5000, intervals: [100, 250, 500] });
    await assertNoSeriousAxe(page);
    await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
    await assertNoSeriousAxe(page);
  });
}
