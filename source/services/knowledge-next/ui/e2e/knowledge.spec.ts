import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function assertNoSeriousAxe(page: Page) {
  const result = await new AxeBuilder({ page }).analyze();
  expect(result.violations.filter(item => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
}

test("imports, retrieves, compares immutable versions, withdraws and restores", async ({ page, request }) => {
  const id = `browser-${Date.now()}`;
  const caps = await (await request.get("/knowledge/v3/capabilities")).json();
  const pkg = {
    schemaVersion: "knowledge-package.v1", projectId: caps.project, idempotencyKey: id,
    documents: [{
      knowledgeId: id, title: id, contentVersion: "1.0.0",
      sources: [{ sourceId: "manual", kind: "DEMO_ASSUMPTION", title: "Test source", publisher: "Browser test", reviewAfter: "2099-01-01" }],
      units: [{ unitId: "u0", content: "浏览器测试知识，单位为米。", sourceRefs: ["manual"] }],
    }],
  };
  await page.goto("/knowledge/");
  await page.getByRole("tab", { name: /导入更新/ }).click();
  await page.getByLabel("知识包文件").setInputFiles({ name: "knowledge.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(pkg)) });
  const acceptedResponse = page.waitForResponse(response => response.url().endsWith("/knowledge/v3/imports") && response.request().method() === "POST");
  await page.getByRole("button", { name: "确认并提交导入" }).click();
  const accepted = await (await acceptedResponse).json();
  let importState: any;
  await expect.poll(async () => {
    importState = await (await request.get(`/knowledge/v3/imports/${accepted.importId}`)).json();
    return importState.state;
  }, { timeout: 45_000 }).toBe("SUCCEEDED");
  const originalVersionId = importState.result.versions[0].versionId;
  await expect(page.locator("#import-status")).toContainText("导入完成", { timeout: 45_000 });
  await page.getByRole("tab", { name: /知识目录/ }).click();
  await page.getByLabel("目录关键词").fill(id);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.locator(".catalog").getByRole("button", { name: new RegExp(id) }).click();
  await expect(page.locator("#version-body")).toContainText("1.0.0");
  await page.getByRole("button", { name: "关闭知识" }).click();
  const revised = {
    ...pkg, idempotencyKey: `${id}-v2`,
    documents: [{ ...pkg.documents[0], expectedVersion: originalVersionId, contentVersion: "1.0.1" }],
  };
  await page.getByRole("tab", { name: /导入更新/ }).click();
  await page.getByLabel(/知识包文件/).setInputFiles({ name: "updated.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(revised)) });
  await page.getByRole("button", { name: "确认并提交导入" }).click();
  await expect(page.locator("#import-status")).toContainText("导入完成", { timeout: 45_000 });
  await page.getByRole("tab", { name: /知识目录/ }).click();
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.locator(".catalog").getByRole("button", { name: new RegExp(id) }).click();
  await expect(page.locator("#version-body")).toContainText("1.0.1");
  await page.getByLabel("比较版本").selectOption(originalVersionId);
  await expect(page.getByRole("heading", { name: "版本差异" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /内容版本/ })).toBeVisible();
  await page.getByRole("button", { name: "关闭知识" }).click();
  await page.getByRole("tab", { name: /证据检索/ }).click();
  await page.getByLabel("检索问题").fill(id);
  await page.getByLabel("检索方式").selectOption("lexical");
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  await expect(page.locator("#evidence")).toContainText("浏览器测试知识");
  await assertNoSeriousAxe(page);

  await page.getByRole("tab", { name: /知识目录/ }).click();
  await page.locator(".catalog").getByRole("button", { name: new RegExp(id) }).click();
  await page.getByText("生命周期与变更记录", { exact: true }).click();
  await page.getByRole("button", { name: "撤回此版本" }).click();
  await page.getByLabel("原因").fill("浏览器测试撤回");
  await page.getByRole("button", { name: "确认撤回" }).click();
  await expect(page.locator("#version-body")).toContainText("已撤回");
  await page.getByRole("button", { name: "恢复此版本" }).click();
  await page.getByLabel("原因").fill("浏览器测试恢复");
  await page.getByRole("button", { name: "确认恢复" }).click();
  await expect(page.locator("#version-body")).toContainText("有效");
});

test("invalid package is rejected before execution", async ({ page }) => {
  await page.goto("/knowledge/");
  await page.getByRole("tab", { name: /导入更新/ }).click();
  await page.getByLabel("知识包文件").setInputFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from('{"documents":[]}') });
  await expect(page.getByRole("alert")).toContainText("API_REQUEST_INVALID");
  await expect(page.getByRole("button", { name: "确认并提交导入" })).toHaveCount(0);
});
