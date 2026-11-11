import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { CatalogPanel } from "./CatalogPanel";
import { useCatalog } from "./use-catalog";
import { server } from "./test-setup";
import type { RunAction } from "./api";

const run: RunAction = async action => { await action(); };
const emptyPage = { items: [], nextCursor: null, totalMatches: 0, matchBasis: "semantic_projection" };
const inventory = { datasetCount: 0, describedDatasetCount: 0, taggedDatasetCount: 0, formats: [], tags: [], projectionBasis: "current_versions" };
const denied = () => HttpResponse.json({ type: "urn:problem:FORBIDDEN", title: "Forbidden", status: 403, code: "FORBIDDEN", detail: "Access denied", errors: [] }, { status: 403 });

function Workspace() {
  const controller = useCatalog(run, true, false);
  return <CatalogPanel controller={controller} run={run} canWriteSemantics={false} canPreview={false} previewDeadlineSeconds={60} />;
}

function renderCatalog() {
  server.use(http.get("*/data/v3/inventory", () => HttpResponse.json(inventory)));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><Workspace /></QueryClientProvider>);
}

it("keeps a failed directory distinct from an empty result and retries its submitted filters", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/data/?q=水位");
  const queries: string[] = [];
  server.use(http.post("*/data/v3/discovery", async ({ request }) => {
    queries.push((await request.json() as { query: string }).query);
    return queries.length === 1 ? denied() : HttpResponse.json(emptyPage);
  }));
  renderCatalog();
  expect(screen.getByRole("status")).toHaveTextContent("正在发现可用数据");
  await screen.findByRole("alert");
  expect(screen.queryByText("没有找到匹配的数据")).not.toBeInTheDocument();
  expect(screen.queryByText(/共匹配 0 份数据/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重试读取数据目录" }));
  expect(await screen.findByText("没有找到匹配的数据")).toBeVisible();
  expect(queries).toEqual(["水位", "水位"]);
  expect(screen.getByRole("textbox", { name: "搜索数据" })).toHaveValue("水位");
});

it("shows selected-version loading and recovery in the detail panel without misreporting the catalog", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/data/?version=v1");
  let reads = 0;
  server.use(
    http.post("*/data/v3/discovery", () => HttpResponse.json(emptyPage)),
    http.get("*/data/v3/versions/v1", () => { reads += 1; return denied(); }),
  );
  renderCatalog();
  expect(screen.getByText("正在读取不可变数据版本…")).toBeVisible();
  expect(screen.queryByText("从目录选择一份数据。")).not.toBeInTheDocument();
  const detail = screen.getByRole("heading", { name: "数据内容" }).closest("section")!;
  await within(detail).findByRole("alert");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  await user.click(within(detail).getByRole("button", { name: "重试读取版本详情" }));
  await waitFor(() => expect(reads).toBe(2));
  expect(window.location.search).toContain("version=v1");
});

it("keeps advanced filters collapsed while preserving the applied count and draft through toggles", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/data/?q=水位&unit=m");
  server.use(http.post("*/data/v3/discovery", () => HttpResponse.json(emptyPage)));
  renderCatalog();
  const summary = screen.getByText("高级筛选");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  expect(screen.getByText("已应用 1 项")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "单位筛选" })).not.toBeVisible();
  await user.click(summary);
  const unit = screen.getByRole("textbox", { name: "单位筛选" });
  await user.clear(unit);
  await user.type(unit, "mg/L");
  await user.click(summary);
  await user.click(summary);
  expect(unit).toHaveValue("mg/L");
  await user.click(screen.getByRole("button", { name: "清除筛选" }));
  expect(screen.getByRole("textbox", { name: "搜索数据" })).toHaveValue("水位");
  expect(unit).toHaveValue("");
  expect(screen.queryByText("已应用 1 项")).not.toBeInTheDocument();
});
