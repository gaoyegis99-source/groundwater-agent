import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { PreviewPanel } from "./PreviewPanel";
import type { Version } from "./api";
import { server } from "./test-setup";

const version = (id: string): Version => ({
  versionId: id, datasetId: "d1", currentVersion: "v2", publicationId: "p1",
  semanticId: `s-${id}`, title: "监测水位", project: "test", createdAt: "2026-11-01T00:00:00Z",
  descriptor: { policy: "FILE_QUERYABLE", fields: [{ fieldId: "f0", name: "水位", type: "float64", nullCount: 0 }], rowCount: 101, quality: {} },
  semantics: { description: "监测水位", source: "UI test fixture" }, inputVersions: [], tags: [],
  objects: [{ objectId: `o-${id}`, format: "csv", role: "raw", size: 100, checksum: `sha256:${"a".repeat(64)}` }],
});
const result = (id: string, value: number, nextOffset: number | null = null) => ({
  queryId: id, state: "SUCCEEDED", error: null, expiresAt: null, resultAvailable: true, receipt: null,
  result: { fields: [{ fieldId: "f0" }], rows: [[value]], nextOffset, truncated: false, coverage: {}, engine: {} },
});
const run = async (action: () => Promise<void>) => { await action(); };
function Content({ id = "v1" }: { id?: string }) {
  return <PreviewPanel key={id} version={version(id)} allowed={false} queryAllowed deadlineSeconds={60} run={run} />;
}
function renderContent() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><Content /></QueryClientProvider>);
}

it("shows actual CSV rows by default and pages the exact version and semantic snapshot", async () => {
  const user = userEvent.setup();
  const bodies: unknown[] = [];
  const pages: string[] = [];
  server.use(
    http.post("*/data/v3/queries", async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ operationId: "op1", queryId: "q1" }); }),
    http.get("*/data/v3/queries/q1", ({ request }) => { const url = new URL(request.url); pages.push(url.search); return HttpResponse.json(result("q1", url.searchParams.get("offset") === "100" ? 18.6 : 17.2, url.searchParams.get("offset") === "100" ? null : 100)); }),
  );
  renderContent();
  expect(await screen.findByRole("cell", { name: "17.2" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "水位" })).toBeVisible();
  expect(bodies).toEqual([expect.objectContaining({ kind: "table", versionIds: ["v1"], semanticIds: { v1: "s-v1" } })]);
  await user.click(screen.getByRole("button", { name: "下一页" }));
  expect(await screen.findByRole("cell", { name: "18.6" })).toBeVisible();
  expect(screen.queryByRole("cell", { name: "17.2" })).not.toBeInTheDocument();
  expect(pages).toEqual(["?offset=0&limit=100", "?offset=100&limit=100"]);
});

it("aborts a previous version read and never puts its late rows into the current content view", async () => {
  let release!: () => void;
  let started!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const received = new Promise<void>(resolve => { started = resolve; });
  let previousSignal: AbortSignal | undefined;
  server.use(
    http.post("*/data/v3/queries", async ({ request }) => { const body = await request.json() as { versionIds: string[] }; return HttpResponse.json({ operationId: `op-${body.versionIds[0]}`, queryId: `q-${body.versionIds[0]}` }); }),
    http.get("*/data/v3/queries/q-v1", async ({ request }) => { previousSignal = request.signal; started(); await pending; return HttpResponse.json(result("q-v1", 99)); }),
    http.get("*/data/v3/queries/q-v2", () => HttpResponse.json(result("q-v2", 23.8))),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><Content /></QueryClientProvider>);
  await received;
  view.rerender(<QueryClientProvider client={client}><Content id="v2" /></QueryClientProvider>);
  expect(await screen.findByRole("cell", { name: "23.8" })).toBeVisible();
  await waitFor(() => expect(previousSignal?.aborted).toBe(true));
  release();
  expect(screen.queryByRole("cell", { name: "99" })).not.toBeInTheDocument();
});

it("retries a failed content read without creating another query or reporting empty rows", async () => {
  const user = userEvent.setup();
  let creates = 0;
  let reads = 0;
  server.use(
    http.post("*/data/v3/queries", () => { creates += 1; return HttpResponse.json({ operationId: "op1", queryId: "q1" }); }),
    http.get("*/data/v3/queries/q1", () => {
      reads += 1;
      return reads === 1
        ? HttpResponse.json({ type: "urn:problem:FORBIDDEN", title: "Forbidden", status: 403, code: "FORBIDDEN", detail: "Access denied", errors: [] }, { status: 403 })
        : HttpResponse.json(result("q1", 17.2));
    }),
  );
  renderContent();
  await screen.findByRole("alert");
  expect(screen.queryByText("此版本没有数据行。")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重试读取内容" }));
  expect(await screen.findByRole("cell", { name: "17.2" })).toBeVisible();
  expect(creates).toBe(1);
  expect(reads).toBe(2);
});

it("does not submit a table query when the current identity cannot read content", async () => {
  let creates = 0;
  server.use(http.post("*/data/v3/queries", () => { creates += 1; return HttpResponse.json({ operationId: "op1", queryId: "q1" }); }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PreviewPanel version={version("v1")} allowed={false} queryAllowed={false} deadlineSeconds={60} run={run} /></QueryClientProvider>);
  expect(screen.getByText("当前身份没有读取表格内容的权限。")).toBeVisible();
  expect(creates).toBe(0);
});
