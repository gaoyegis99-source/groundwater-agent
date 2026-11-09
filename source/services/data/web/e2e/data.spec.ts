import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("upload, atomic publication, search, inspect, query, refresh and download", async ({
  page,
}, testInfo) => {
  const title = "浏览器验收-" + Date.now() + ".csv";
  const content = Buffer.from("well,value\n001,2\n002,4\n");
  await page.goto("/data/");
  await expect(
    page.getByRole("heading", { name: "数据管理工作台", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /发布数据/ }).click();
  await page
    .getByLabel("选择文件")
    .setInputFiles({ name: title, mimeType: "text/csv", buffer: content });
  await page.getByRole("button", { name: "核对发布内容" }).click();
  await page.getByRole("button", { name: "上传并准备", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认发布", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /^数据目录/ }).click();
  await page.getByLabel("搜索数据").fill(title);
  await page.getByRole("button", { name: "应用筛选", exact: true }).click();
  await expect(
    page.locator(".catalog button").filter({ hasText: title }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: /发布数据/ }).click();
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await page.getByRole("button", { name: "确认提交", exact: true }).click();
  await expect(page.locator(".publication")).toContainText("发布完成");
  await page.getByRole("tab", { name: /^数据目录/ }).click();
  await page
    .locator(".catalog button")
    .filter({ hasText: title })
    .click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  const rawDownload = page.waitForEvent("download");
  await page
    .getByRole("link", { name: /下载原始文件/ })
    .click();
  const raw = await rawDownload;
  expect(await readFile((await raw.path())!)).toEqual(content);
  await page.getByRole("button", { name: /查询这份数据/ }).click();
  await page.getByRole("button", { name: "执行查询", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "001", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("cell", { name: "001", exact: true }),
  ).toBeVisible();
  await page.getByText("查询回执与计算范围", { exact: true }).click();
  await expect(
    page.locator("pre").filter({ hasText: "all_matching_rows" }),
  ).toBeVisible();
  const queryDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载查询结果", exact: true }).click();
  const downloaded = await queryDownload;
  const result = JSON.parse(await readFile((await downloaded.path())!, "utf8"));
  expect(result.rows).toEqual([
    ["001", "2"],
    ["002", "4"],
  ]);
  await page.screenshot({
    path:
      process.env.DATA_E2E_SCREENSHOT ||
      testInfo.outputPath("browser.png"),
    fullPage: true,
  });
});
