import { expect, test } from "@playwright/test";

test("exact task binding combines with general knowledge; defaults exclude bound evidence", async ({ page, request }) => {
  const data = await (await request.get("/data/v3/datasets?limit=1")).json();
  const dataset = data.items[0];
  const version = await (await request.get(`/data/v3/versions/${dataset.currentVersion}`)).json();
  const binding = { serviceBinding: "data-primary", projectId: dataset.project, datasetId: dataset.datasetId, versionId: version.versionId, semanticId: version.semanticId, fieldId: null };
  const id = `scope-browser-${Date.now()}`;
  const documents = ["general", "bound"].map(kind => ({
    knowledgeId: `${id}-${kind}`, title: id, contentVersion: "1",
    bindings: kind === "bound" ? [binding] : [],
    sources: [{ sourceId: "test", kind: "DEMO_ASSUMPTION", title: "UI test", publisher: "Test", reviewAfter: "2099-01-01" }],
    units: [{ unitId: "text", content: `${id} ${kind}`, sourceRefs: ["test"] }],
  }));
  const accepted = await (await request.post("/knowledge/v3/imports", { data: { projectId: dataset.project, idempotencyKey: id, documents } })).json();
  let state: any;
  await expect.poll(async () => {
    state = await (await request.get(`/knowledge/v3/imports/${accepted.importId}`)).json();
    return state.state;
  }, { timeout: 45_000 }).toBe("SUCCEEDED");
  try {
    await page.goto("/knowledge/");
    await page.getByRole("tab", { name: /证据检索/ }).click();
    await page.getByLabel("检索问题").fill(id);
    await page.getByLabel("检索方式").selectOption("lexical");
    await page.getByRole("button", { name: "开始检索", exact: true }).click();
    await expect(page.locator("#evidence")).toContainText(`${id} general`);
    await expect(page.locator("#evidence")).not.toContainText(`${id} bound`);

    await page.getByText("需要限定到某份数据？选择不可变版本和语义修订").click();
    await page.getByRole("button", { name: "打开数据集列表" }).click();
    await page.getByRole("option", { name: new RegExp(dataset.title) }).click();
    await page.getByRole("button", { name: /打开版本列表/ }).click();
    await page.getByRole("option", { name: new RegExp(version.versionId) }).click();
    await page.getByLabel("适用范围", { exact: true }).selectOption("combined");
    await page.getByRole("button", { name: "开始检索", exact: true }).click();
    await expect(page.locator("#evidence")).toContainText(`${id} general`);
    await expect(page.locator("#evidence")).toContainText(`${id} bound`);
    await expect(page.locator("#evidence")).toContainText("精确绑定匹配");
    await page.getByLabel("适用范围", { exact: true }).selectOption("bound");
    await page.getByRole("button", { name: "开始检索", exact: true }).click();
    await expect(page.locator("#evidence")).toContainText(`${id} bound`);
    await expect(page.locator("#evidence")).not.toContainText(`${id} general`);
  } finally {
    for (const item of state.result.versions) {
      await request.post(`/knowledge/v3/versions/${item.versionId}/lifecycle`, { data: { expectedRevision: 1, status: "WITHDRAWN", reviewAfter: "2099-01-01", reason: "UI test complete" } });
    }
  }
});

test("retry progress survives reload and shows terminal reason", async ({ page, request }) => {
  const caps = await (await request.get("/knowledge/v3/capabilities")).json();
  const segment = (value: string) => encodeURIComponent(value);
  const recoveryKey = `knowledge-import:v1:${segment(caps.productId)}/${segment(caps.project)}/${segment(caps.currentUser.subject)}`;
  let terminal = false;
  await page.addInitScript(({ key }) => {
    sessionStorage.setItem("knowledge-import", "ownerless-legacy-id");
    sessionStorage.setItem(key, JSON.stringify({ version: 1, id: "ui-retry", createdAt: new Date().toISOString() }));
  }, { key: recoveryKey });
  await page.route("**/knowledge/v3/imports/ui-retry", route => route.fulfill({ json: {
    importId: "ui-retry", state: terminal ? "FAILED" : "RETRY_WAIT", dataAttempts: terminal ? 5 : 2,
    nextRetryAt: terminal ? null : "2099-01-01T12:00:00Z", retryDeadline: "2099-01-02T12:00:00Z",
    lastError: { code: terminal ? "DATA_RETRY_EXHAUSTED" : "DATA_UNAVAILABLE" },
    error: terminal ? { code: "DATA_RETRY_EXHAUSTED" } : null, result: null,
    actor: { subject: caps.currentUser.subject }, createdAt: "2026-10-17T12:00:00Z", updatedAt: "2026-10-17T12:00:01Z",
  } }));
  await page.goto("/knowledge/");
  await expect(page.locator("#import-status")).toContainText("RETRY_WAIT");
  await expect(page.locator("#import-status")).toContainText("2 / 5");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("knowledge-import"))).toBeNull();
  await page.reload();
  await expect(page.locator("#import-status")).toContainText("2099");
  terminal = true;
  await expect(page.locator("#import-status")).toContainText("DATA_RETRY_EXHAUSTED", { timeout: 10_000 });
  await expect(page.locator("#import-status")).toContainText("FAILED");
});
