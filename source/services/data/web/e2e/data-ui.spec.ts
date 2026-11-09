import AxeBuilder from "@axe-core/playwright";
import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const createdAt = "2026-10-17T10:00:00Z";
const checksum = `sha256:${"a".repeat(64)}`;
const fields = [{ fieldId: "f0", name: "well", type: "string", nullCount: 0 }];
const capabilities = {
  version: "1", productId: "sample", project: "project-1",
  currentUser: { subject: "operator-1", displayName: "Operator", roles: ["admin"] },
  permissions: { catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true, userUploadCreate: true, managedPublicationWrite: true, semanticWrite: true, accessWrite: true, governanceRead: true, qualityWrite: true, mlopsWrite: true },
  identityMode: "local", writeEnabled: true, formats: ["binary", "csv"],
  formatMediaTypes: { binary: "application/octet-stream", csv: "text/csv" },
  query: {}, preview: {}, limits: { querySeconds: 60, previewSeconds: 60, uploadBytes: 1_073_741_824 }, contracts: {}, openapi: "/openapi.json",
};
const dataset = { datasetId: "d1", project: "project-1", title: "监测数据", tags: ["demo"], currentVersion: "v1", createdAt, semantics: { description: "井号" }, formats: ["csv"] };
const discoveryItem = {
  datasetId: "d1", title: "监测数据", description: "井号", tags: ["demo"], formats: ["csv"],
  currentVersion: "v1", semanticId: "s1", score: 1, matchReasons: [], matchedFields: [],
  grain: null, timeRange: null, crs: null, unknowns: [],
};
const inventory = {
  datasetCount: 1, describedDatasetCount: 1, taggedDatasetCount: 1,
  formats: [{ name: "csv", count: 1 }], tags: [{ name: "demo", count: 1 }], projectionBasis: "current_versions",
};
const emptyActivity = { items: [], generatedAt: createdAt };
const governance = {
  datasetId: "d1", title: "监测数据", currentVersion: "v1", ownerSubject: "operator-1",
  qualityTrend: [{ versionId: "v1", createdAt, rowCount: 2, quality: {} }],
  lineage: [{ outputVersionId: "v1", inputVersionId: "upstream-v1", inputDatasetId: "upstream-d1", inputTitle: "上游水位台账", createdAt, isCurrent: true }],
  activity: [{
    service: "data", operationId: "s1", kind: "semantic_revision", state: "RECORDED",
    title: "更新字段含义与业务语义", subject: "operator-1", createdAt, updatedAt: createdAt,
    href: "/data/?view=catalog&version=v1", details: { versionId: "v1" },
  }],
  generatedAt: createdAt,
};
const version = (semanticId = "s1") => ({
  versionId: "v1", datasetId: "d1", currentVersion: "v1", publicationId: "published-1",
  semanticId, title: "监测数据", descriptor: { policy: "FILE_QUERYABLE", fields, rowCount: 2, quality: {} },
  semantics: { description: semanticId === "s1" ? "井号" : "监测井编号", fields: { f0: { meaning: "井编号", unit: null } }, source: "test", limitations: [] },
  objects: [{ objectId: "o1", size: 20, checksum, role: "raw", format: "csv", mediaType: "text/csv" }],
  inputVersions: [], createdAt, tags: ["demo"], project: "project-1",
});
const queryResponse = (id: string, offset: number) => ({
  queryId: id, state: "SUCCEEDED", error: null, expiresAt: "2026-10-18T10:00:00Z", resultAvailable: true,
  result: { fields: [{ fieldId: "f0" }], rows: [[offset ? "002" : "001"]], nextOffset: offset ? null : 100, truncated: false, coverage: {}, engine: {} },
  receipt: { queryId: id, inputs: [], plan: {}, planDigest: checksum, coverage: { scope: "all_matching_rows" }, truncated: false, returnedRows: 1, resultChecksum: checksum, engine: {} },
});

async function assertNoSeriousAxe(page: Page) {
  const result = await new AxeBuilder({ page }).analyze();
  expect(result.violations.filter(item => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
}

test.beforeEach(async ({ page }) => {
  await page.route("**/knowledge/v3/activity**", route => route.fulfill({ json: emptyActivity }));
});

test("catalog, semantic CAS, query pagination, version selection and scoped refresh", async ({ page }) => {
  const queryBodies: Record<string, unknown>[] = [];
  const semanticBodies: Record<string, unknown>[] = [];
  let currentSemantic = "s1";
  await page.route("**/data/v3/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/data/v3/", "");
    let response: unknown;
    if (path === "capabilities") response = capabilities;
    else if (path === "activity") response = emptyActivity;
    else if (path === "discovery") response = { items: [discoveryItem], nextCursor: null, totalMatches: 1, matchBasis: "semantic_projection" };
    else if (path === "inventory") response = inventory;
    else if (path === "versions/v1" && request.method() === "GET") response = version(currentSemantic);
    else if (path === "datasets/d1/versions") response = { items: [
      { versionId: "v1", semanticId: currentSemantic, createdAt },
      { versionId: "v2", semanticId: "s2", createdAt: "2026-10-16T10:00:00Z" },
    ], nextCursor: null };
    else if (path === "datasets/d1/governance") response = governance;
    else if (path === "versions/v1/semantics" && request.method() === "GET") response = { items: [{ semanticId: currentSemantic, createdAt, actor: { subject: "operator-1" } }], nextCursor: null };
    else if (path === "versions/v1/semantics" && request.method() === "POST") {
      semanticBodies.push(request.postDataJSON()); currentSemantic = "s2";
      response = { semanticId: "s2", versionId: "v1" };
    } else if (path === "queries" && request.method() === "POST") {
      queryBodies.push(request.postDataJSON()); response = { operationId: `op-${queryBodies.length}`, queryId: `q${queryBodies.length}` };
    } else if (path.startsWith("queries/q")) response = queryResponse(path.split("/")[1]!, Number(url.searchParams.get("offset") ?? 0));
    else throw new Error(`Unexpected request: ${request.method()} ${path}`);
    await route.fulfill({ json: response });
  });
  await page.goto("/data/");
  await page.getByText("查看身份与环境").click();
  await expect(page.getByText(/operator-1/)).toBeVisible();
  await page.locator(".catalog").getByRole("button", { name: /监测数据/ }).click();
  await expect(page.getByText("所有者：operator-1")).toBeVisible();
  await expect(page.getByText("上游水位台账")).toBeVisible();
  await page.getByText("治理字段含义").click();
  await page.getByLabel("描述").fill("监测井编号");
  await page.getByRole("button", { name: "预览修订" }).click();
  await expect(page.getByLabel("语义修订差异")).toContainText("description");
  await page.getByRole("button", { name: "创建语义修订" }).click();
  await expect.poll(() => semanticBodies).toEqual([expect.objectContaining({ expectedSemanticId: "s1" })]);
  await page.getByRole("button", { name: /查询这份数据/ }).click();
  await page.getByLabel("筛选值").fill("001");
  await page.getByRole("button", { name: "执行查询", exact: true }).click();
  await expect(page.getByRole("cell", { name: "001", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("cell", { name: "002", exact: true })).toBeVisible();
  await page.getByText(/比较不可变版本中的指标/).click();
  await page.getByLabel("指标名称").fill("RMSE");
  await page.locator('.version-list [role="option"]').filter({ hasText: "v1" }).click();
  await page.locator('.version-list [role="option"]').filter({ hasText: "v2" }).click({ modifiers: ["Control"] });
  await page.getByRole("button", { name: "比较", exact: true }).click();
  await expect.poll(() => queryBodies.length).toBe(2);
  expect(queryBodies[1]).toMatchObject({ kind: "metrics", metricName: "RMSE", versionIds: expect.arrayContaining(["v1", "v2"]) });
  const history = page.locator(".query-history");
  await history.locator(":scope > summary").click();
  await history.getByRole("button", { name: "查看结果" }).nth(1).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("query")).toBe("q1");
  const requestSnapshot = page.locator(".request-snapshot");
  await requestSnapshot.locator("summary").click();
  await expect(requestSnapshot).toContainText('"kind": "table"');
  await expect(requestSnapshot).toContainText("001");
  await expect(requestSnapshot).not.toContainText("RMSE");
  await assertNoSeriousAxe(page);
  await page.reload();
  await expect(page.getByRole("cell", { name: "001", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^数据目录/ }).click();
  await expect(page.getByRole("heading", { name: "监测数据", exact: true })).toBeVisible();
});

test("previews an XLSX object through the bounded Preview service", async ({ page }) => {
  const previewBodies: Record<string, unknown>[] = [];
  const xlsxItem = { ...discoveryItem, formats: ["xlsx"], title: "水位台账" };
  const xlsxVersion = {
    ...version(), title: "水位台账",
    objects: [{ objectId: "xlsx-1", size: 2048, checksum, role: "raw", format: "xlsx", mediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }],
  };
  await page.route("**/data/v3/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/data/v3/", "");
    let response: unknown;
    if (path === "capabilities") response = { ...capabilities, formats: [...capabilities.formats, "xlsx"] };
    else if (path === "activity") response = emptyActivity;
    else if (path === "discovery") response = { items: [xlsxItem], nextCursor: null, totalMatches: 1, matchBasis: "semantic_projection" };
    else if (path === "inventory") response = { ...inventory, formats: [{ name: "xlsx", count: 1 }] };
    else if (path === "versions/v1") response = xlsxVersion;
    else if (path === "datasets/d1/versions") response = { items: [{ versionId: "v1", semanticId: "s1", createdAt }], nextCursor: null };
    else if (path === "datasets/d1/governance") response = { ...governance, title: "水位台账", lineage: [] };
    else if (path === "versions/v1/semantics") response = { items: [{ semanticId: "s1", createdAt, actor: { subject: "operator-1" } }], nextCursor: null };
    else if (path === "previews" && request.method() === "POST") {
      previewBodies.push(request.postDataJSON());
      response = { previewId: "preview-1", operationId: "preview-operation-1" };
    } else if (path.startsWith("previews/preview-1")) {
      response = {
        previewId: "preview-1", state: "SUCCEEDED", error: null, expiresAt: "2026-10-18T10:00:00Z", resultAvailable: true,
        result: { fields: [{ fieldId: "well", name: "井号" }], rows: [["W-001"]], nextOffset: null, metadata: { sheet: "监测井" } },
        receipt: { previewId: "preview-1", selector: { sheetName: "监测井" }, returnedRows: 1 },
      };
    } else throw new Error(`Unexpected request: ${request.method()} ${path}`);
    await route.fulfill({ json: response });
  });
  await page.goto("/data/");
  await page.locator(".catalog").getByRole("button", { name: /水位台账/ }).click();
  await page.getByLabel("工作表（可选）").fill("监测井");
  await page.getByRole("button", { name: "生成预览" }).click();
  await expect(page.getByRole("cell", { name: "W-001" })).toBeVisible();
  expect(previewBodies).toEqual([expect.objectContaining({
    kind: "xlsx_table", objectId: "xlsx-1", versionId: "v1", semanticId: "s1", sheetName: "监测井",
  })]);
  await assertNoSeriousAxe(page);
});

test("combines Data and Knowledge work in the cross-service operation center", async ({ page }) => {
  await page.route("**/data/v3/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/data/v3/", "");
    if (path === "capabilities") return route.fulfill({ json: capabilities });
    if (path === "discovery") return route.fulfill({ json: { items: [], nextCursor: null, totalMatches: 0, matchBasis: "semantic_projection" } });
    if (path === "inventory") return route.fulfill({ json: { ...inventory, datasetCount: 0, formats: [], tags: [] } });
    if (path === "activity") return route.fulfill({ json: {
      items: [{ service: "data", operationId: "query-1", kind: "query", state: "RUNNING", title: "数据查询 · 水位", subject: "operator-1", createdAt, updatedAt: createdAt, href: "/data/?view=query&query=query-1", details: {} }],
      generatedAt: createdAt,
    } });
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.route("**/knowledge/v3/activity**", route => route.fulfill({ json: {
    items: [{ service: "knowledge", operationId: "import-1", kind: "import", state: "SUCCEEDED", title: "地下水规范导入", subject: "operator-1", createdAt, updatedAt: createdAt, href: "/knowledge/?view=import&import=import-1", details: {} }],
    generatedAt: createdAt,
  } }));
  await page.goto("/data/");
  await page.getByText("查看全部活动（2）").click();
  await expect(page.getByRole("link", { name: "数据查询 · 水位" })).toHaveAttribute("href", "/data/?view=query&query=query-1");
  await expect(page.getByRole("link", { name: "地下水规范导入" })).toHaveAttribute("href", "/knowledge/?view=import&import=import-1");
  await expect(page.getByLabel("操作状态摘要").getByText("进行中")).toBeVisible();
  await expect(page.getByText("暂无阻断项")).toBeVisible();
});

test("upload hashes bytes, prepares, confirms and restores publication without mutation replay", async ({ page }) => {
  const content = Buffer.from("well,value\n001,2\n");
  const commands: string[] = [];
  let state = "OPEN";
  let uploaded = false;
  const publication = () => ({
    publicationId: "p1", state, manifest: { outputs: [] }, uploads: uploaded ? ["output_0"] : [], operations: [],
    receipt: state === "PUBLISHED" ? { publicationId: "p1", state: "PUBLISHED", outputs: [{ name: "output_0", datasetId: "d1", versionId: "v1", semanticId: "s1" }] } : null,
    idempotencyKey: "key-1", manifestChecksum: checksum, createdAt, events: [],
  });
  await page.route("**/data/v3/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/data/v3/", "");
    let response: unknown;
    if (path === "capabilities") response = capabilities;
    else if (path === "activity") response = emptyActivity;
    else if (path === "discovery") response = { items: state === "PUBLISHED" ? [discoveryItem] : [], nextCursor: null, totalMatches: state === "PUBLISHED" ? 1 : 0, matchBasis: "semantic_projection" };
    else if (path === "inventory") response = { ...inventory, datasetCount: state === "PUBLISHED" ? 1 : 0 };
    else {
      if (request.method() !== "GET") commands.push(path);
      if (path === "publications/p1/items/output_0") {
        expect(request.postDataBuffer()).toEqual(content);
        expect(request.headers()["x-content-sha256"]).toBe(`sha256:${createHash("sha256").update(content).digest("hex")}`);
        uploaded = true;
        response = { objectId: "upload-1", size: content.length, checksum: request.headers()["x-content-sha256"], role: "raw", format: "csv", mediaType: "text/csv" };
      } else if (path === "publications/p1/prepare") { state = "READY"; response = { operationId: "prepare-1" }; }
      else if (path === "publications/p1/commit") { state = "PUBLISHED"; response = publication().receipt; }
      else if (path === "publications") response = publication();
      else if (path === "publications/p1") response = publication();
      else throw new Error(`Unexpected request: ${path}`);
    }
    await route.fulfill({ json: response });
  });
  await page.goto("/data/");
  await page.getByRole("tab", { name: /发布数据/ }).click();
  await page.getByLabel("选择文件").setInputFiles({ name: "upload.csv", mimeType: "text/csv", buffer: content });
  await page.getByRole("button", { name: "核对发布内容" }).click();
  await page.getByRole("button", { name: "上传并准备", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认发布", exact: true })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("publication")).toBe("p1");
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "提交不可变数据版本？" })).toBeVisible();
  await assertNoSeriousAxe(page);
  await page.getByRole("button", { name: "确认提交" }).click();
  await expect(page.locator(".publication")).toContainText("发布完成");
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("data-publication:")))).toHaveLength(1);
  await page.reload();
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("data-publication:")))).toHaveLength(1);
  await expect(page.locator(".publication")).toContainText("发布完成");
  expect(commands).toEqual(["publications", "publications/p1/items/output_0", "publications/p1/prepare", "publications/p1/commit"]);
});

test("keeps the reviewed publication draft when beginning the server operation fails", async ({ page }) => {
  await page.route("**/data/v3/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/data/v3/", "");
    if (path === "capabilities") {
      await route.fulfill({ json: capabilities });
      return;
    }
    if (path === "activity") {
      await route.fulfill({ json: emptyActivity });
      return;
    }
    if (path === "discovery") {
      await route.fulfill({ json: { items: [], nextCursor: null, totalMatches: 0, matchBasis: "semantic_projection" } });
      return;
    }
    if (path === "inventory") {
      await route.fulfill({ json: { ...inventory, datasetCount: 0, describedDatasetCount: 0, taggedDatasetCount: 0, formats: [], tags: [] } });
      return;
    }
    if (path === "publications" && request.method() === "POST") {
      await route.fulfill({ status: 503, contentType: "application/problem+json", body: JSON.stringify({
        type: "urn:sample:problem:BUSY", title: "Busy", status: 503, detail: "busy",
        code: "PUBLICATION_UNAVAILABLE", requestId: "publication-request", traceId: "trace", errors: [],
      }) });
      return;
    }
    throw new Error(`Unexpected request: ${request.method()} ${path}`);
  });
  await page.goto("/data/");
  await page.getByRole("tab", { name: /发布数据/ }).click();
  await page.getByLabel("选择文件").setInputFiles({ name: "kept.csv", mimeType: "text/csv", buffer: Buffer.from("well\n001\n") });
  await page.getByRole("button", { name: "核对发布内容" }).click();
  await page.getByRole("button", { name: "上传并准备", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("PUBLICATION_UNAVAILABLE");
  await expect(page.getByRole("button", { name: "上传并准备", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "kept.csv" })).toBeVisible();
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("data-publication:")))).toHaveLength(0);
});

test("restored failed publications always provide a clean new-publication path", async ({ page }) => {
  const recoveryKey = "data-publication:v1:sample/project-1/operator-1";
  await page.addInitScript(({ key, timestamp }) => {
    localStorage.setItem(key, JSON.stringify({ version: 1, id: "publication-failed", createdAt: timestamp }));
  }, { key: recoveryKey, timestamp: createdAt });
  await page.route("**/data/v3/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/data/v3/", "");
    if (path === "capabilities") {
      await route.fulfill({ json: capabilities });
      return;
    }
    if (path === "activity") {
      await route.fulfill({ json: emptyActivity });
      return;
    }
    if (path === "discovery") {
      await route.fulfill({ json: { items: [], nextCursor: null, totalMatches: 0, matchBasis: "semantic_projection" } });
      return;
    }
    if (path === "inventory") {
      await route.fulfill({ json: { ...inventory, datasetCount: 0, describedDatasetCount: 0, taggedDatasetCount: 0, formats: [], tags: [] } });
      return;
    }
    if (path === "publications/publication-failed") {
      await route.fulfill({ json: {
        publicationId: "publication-failed", state: "FAILED", manifest: { outputs: [] }, uploads: [],
        operations: [{ operationId: "prepare-failed", state: "FAILED", error: { code: "VALIDATION_FAILED" } }],
        receipt: null, idempotencyKey: "key-failed", manifestChecksum: checksum, createdAt, events: [],
      } });
      return;
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/data/?view=publication&publication=publication-failed");
  await expect(page.getByRole("button", { name: "开始新发布" })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("publication")).toBe("publication-failed");
  await page.getByRole("button", { name: "开始新发布" }).click();
  await expect(page.getByLabel("选择文件")).toBeVisible();
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), recoveryKey)).toBeNull();
  await expect.poll(() => new URL(page.url()).searchParams.get("publication")).toBeNull();
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`has no serious accessibility findings at ${viewport.width}x${viewport.height} and 200%`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/data/v3/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("capabilities")) return route.fulfill({ json: capabilities });
      if (path.endsWith("inventory")) return route.fulfill({ json: { ...inventory, datasetCount: 0, describedDatasetCount: 0, taggedDatasetCount: 0, formats: [], tags: [] } });
      return route.fulfill({ json: { items: [], nextCursor: null, totalMatches: 0, matchBasis: "semantic_projection" } });
    });
    await page.goto("/data/");
    await expect(page.getByRole("heading", { name: "数据管理工作台" })).toBeVisible();
    await assertNoSeriousAxe(page);
    await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
    await assertNoSeriousAxe(page);
  });
}
