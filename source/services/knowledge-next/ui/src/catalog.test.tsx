import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { CatalogPanel } from "./CatalogPanel";
import { server } from "./test-setup";
import type { KnowledgeCapabilities } from "./api";

const capabilities = { permissions: { lifecycleWrite: false } } as KnowledgeCapabilities;
const emptyPage = { items: [], nextCursor: null, totalMatches: 0 };
const denied = () => HttpResponse.json({ type: "urn:problem:FORBIDDEN", title: "Forbidden", status: 403, code: "FORBIDDEN", detail: "Access denied", errors: [] }, { status: 403 });

const createdAt = "2026-10-17T10:00:00Z";
const version = {
  versionId: "v1", documentId: "d1", checksum: `sha256:${"a".repeat(64)}`, importId: "i1", verifiedAt: createdAt, createdAt,
  lifecycleRevision: 1, status: "ACTIVE", validThrough: null, reviewAfter: "2099-01-01", lifecycleActor: { subject: "operator-1" },
  body: {
    knowledgeId: "manual-1", title: "监测手册", contentVersion: "1.0.0", kind: "knowledge", language: "zh-CN", summary: "监测说明", bindings: [], aliases: [],
    sources: [{ sourceId: "s1", kind: "MANUAL", title: "监测规范", publisher: "Operator", reviewAfter: "2099-01-01", lifecycleStatus: "ACTIVE" }],
    units: [{ unitId: "u1", content: "核对井号。", sourceRefs: ["s1"], requiredUnits: [] }],
  },
};

function renderCatalog(allowLifecycle = false, refreshToken = 0) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><CatalogPanel scope="project-1" capabilities={{ ...capabilities, permissions: { ...capabilities.permissions, lifecycleWrite: allowLifecycle } }} refreshToken={refreshToken} /></QueryClientProvider>);
}

it("keeps advanced filters collapsed while leaving the keyword search available", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/");
  server.use(http.get("*/knowledge/v3/documents", () => HttpResponse.json(emptyPage)));
  renderCatalog();
  await screen.findByText("没有找到匹配的知识");
  expect(screen.queryByText("选择一份知识阅读内容。")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "目录关键词" })).toBeVisible();
  const advanced = screen.getByText("高级筛选", { exact: true });
  expect(advanced.closest("details")).not.toHaveAttribute("open");
  expect(screen.getByLabelText("生命周期筛选")).not.toBeVisible();
  await user.click(advanced);
  expect(screen.getByLabelText("生命周期筛选")).toBeVisible();
  expect(screen.getByLabelText("Data 绑定筛选")).toBeVisible();
});

it("shows applied advanced filters and clears them without losing the search", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/?q=监测&status=ACTIVE&bound=true&sort=title-asc");
  const queries: URLSearchParams[] = [];
  server.use(
    http.get("*/knowledge/v3/documents", ({ request }) => { queries.push(new URL(request.url).searchParams); return HttpResponse.json(emptyPage); }),
    http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/historical-v1/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/historical-v1", denied),
  );
  renderCatalog();
  expect(await screen.findByText("已应用 3 项")).toBeVisible();
  expect(screen.getByText("高级筛选", { exact: true }).closest("details")).not.toHaveAttribute("open");
  await user.click(screen.getByRole("button", { name: "清除筛选" }));
  await waitFor(() => expect(queries.at(-1)?.get("status")).toBeNull());
  expect(queries.at(-1)?.get("bound")).toBeNull();
  expect(queries.at(-1)?.get("sort")).toBe("modified-desc");
  expect(queries.at(-1)?.get("query")).toBe("监测");
  expect(screen.getByLabelText("目录关键词")).toHaveValue("监测");
  expect(screen.queryByText("已应用 3 项")).not.toBeInTheDocument();
});

it("opens readable knowledge and restores the catalog focus after Escape", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/?q=监测");
  server.use(
    http.get("*/knowledge/v3/documents", () => HttpResponse.json({ items: [{ documentId: "d1", currentVersion: "v1", title: "监测手册", createdAt }], nextCursor: null, totalMatches: 1 })),
    http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [{ versionId: "v1", checksum: version.checksum, createdAt }], nextCursor: null })),
    http.get("*/knowledge/v3/versions/v1", () => HttpResponse.json(version)),
    http.get("*/knowledge/v3/versions/v1/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
  );
  renderCatalog();
  const entry = await screen.findByRole("button", { name: /监测手册/ });
  await user.click(entry);
  const reader = screen.getByRole("dialog", { name: "知识阅读" });
  expect(await within(reader).findByText("核对井号。")).toBeVisible();
  expect(new URLSearchParams(window.location.search).get("version")).toBe("v1");
  for (const identity of within(reader).getAllByText(version.checksum)) expect(identity).not.toBeVisible();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "知识阅读" })).not.toBeInTheDocument());
  await waitFor(() => expect(entry).toHaveFocus());
  expect(new URLSearchParams(window.location.search).get("q")).toBe("监测");
});

it("rejects an unsafe source URL and recovers to the exact version with an HTTPS source", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/?document=d1&version=v1");
  let sourceUrl = "javascript:alert(1)";
  server.use(
    http.get("*/knowledge/v3/documents", () => HttpResponse.json(emptyPage)),
    http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/v1/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/v1", () => HttpResponse.json({ ...version, body: { ...version.body, sources: [{ ...version.body.sources[0], url: sourceUrl }] } })),
  );
  renderCatalog();
  expect(await screen.findByRole("alert")).toHaveTextContent("API_RESPONSE_INVALID");
  expect(screen.queryByRole("link", { name: "打开正式来源" })).not.toBeInTheDocument();
  sourceUrl = "https://example.org/manual";
  await user.click(screen.getByRole("button", { name: "重试读取版本详情" }));
  await screen.findByText("核对井号。");
  await user.click(screen.getByText("版本与来源详情"));
  expect(screen.getByRole("link", { name: "打开正式来源" })).toHaveAttribute("href", sourceUrl);
  expect(new URLSearchParams(window.location.search).get("version")).toBe("v1");
});

it("offers a directory retry without replacing a failed search with an empty-result message", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/?q=监测");
  const queries: string[] = [];
  server.use(http.get("*/knowledge/v3/documents", ({ request }) => {
    queries.push(new URL(request.url).searchParams.get("query") ?? "");
    return queries.length === 1 ? denied() : HttpResponse.json(emptyPage);
  }));
  renderCatalog();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取知识目录");
  await screen.findByRole("alert");
  expect(screen.queryByText("没有找到匹配的知识")).not.toBeInTheDocument();
  expect(screen.queryByText(/共匹配 0 份知识/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重试读取知识目录" }));
  expect(await screen.findByText("没有找到匹配的知识")).toBeVisible();
  expect(queries).toEqual(["监测", "监测"]);
  expect(screen.getByLabelText("目录关键词")).toHaveValue("监测");
});

it("prevents repeated lifecycle submissions and keeps failure recovery inside the dialog", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/?document=d1&version=v1");
  let writes = 0;
  let finish: (value: Response) => void = () => {};
  server.use(
    http.get("*/knowledge/v3/documents", () => HttpResponse.json(emptyPage)),
    http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [{ versionId: "v1", checksum: version.checksum, createdAt }], nextCursor: null })),
    http.get("*/knowledge/v3/versions/v1", () => HttpResponse.json(version)),
    http.get("*/knowledge/v3/versions/v1/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.post("*/knowledge/v3/versions/v1/lifecycle", () => { writes += 1; return new Promise<Response>(resolve => { finish = resolve; }); }),
  );
  renderCatalog(true);
  await screen.findByText("核对井号。");
  await user.click(screen.getByText("生命周期与变更记录"));
  await user.click(await screen.findByRole("button", { name: "撤回此版本" }));
  const dialog = screen.getByRole("dialog", { name: "撤回知识版本？" });
  await user.type(within(dialog).getByRole("textbox", { name: "原因" }), "待核验来源");
  await user.click(within(dialog).getByRole("button", { name: "确认撤回" }));
  await waitFor(() => expect(writes).toBe(1));
  expect(within(dialog).getByRole("button", { name: "正在提交…" })).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
  await act(async () => finish(denied()));
  expect(await within(dialog).findByRole("alert")).toBeVisible();
  expect(within(dialog).getByRole("textbox", { name: "原因" })).toHaveValue("待核验来源");
  expect(within(dialog).getByRole("button", { name: "确认撤回" })).toBeEnabled();
});

it("recovers the exact selected version without changing the directory filters", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/knowledge/?q=监测&document=d1&version=v1");
  let reads = 0;
  server.use(
    http.get("*/knowledge/v3/documents", () => HttpResponse.json(emptyPage)),
    http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/v1/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/v1", () => { reads += 1; return denied(); }),
  );
  renderCatalog();
  const detail = screen.getByRole("dialog", { name: "知识阅读" });
  await within(detail).findByRole("alert");
  await user.click(within(detail).getByRole("button", { name: "重试读取版本详情" }));
  await waitFor(() => expect(reads).toBe(2));
  expect(window.location.search).toContain("version=v1");
  expect(screen.getByLabelText("目录关键词")).toHaveValue("监测");
});

it("keeps an explicitly selected historical version when a refreshed directory points to a newer version", async () => {
  window.history.replaceState({}, "", "/knowledge/?document=d1&version=historical-v1");
  const reads: string[] = [];
  server.use(
    http.get("*/knowledge/v3/documents", () => HttpResponse.json({
      items: [{ documentId: "d1", currentVersion: "current-v2", title: "更新后的监测手册", createdAt: "2026-10-17T10:00:00Z" }], nextCursor: null, totalMatches: 1,
    })),
    http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/:version/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/versions/:version", ({ params }) => { reads.push(String(params.version)); return denied(); }),
  );
  renderCatalog(false, 1);
  await screen.findByTitle("更新后的监测手册");
  await screen.findByRole("alert");
  expect(new URLSearchParams(window.location.search).get("version")).toBe("historical-v1");
  expect(screen.getByRole("combobox", { name: "查看版本" })).toHaveValue("historical-v1");
  expect(reads).toEqual(["historical-v1"]);
});
