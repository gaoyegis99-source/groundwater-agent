import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createDiagnosticSink } from "@platform/ui-boundary";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { api, dataDiagnostics, PlatformUiError } from "./api";
import { copyText } from "./clipboard";
import { deadlineExceeded, retryDelay, retryable } from "./polling";
import {
  clearLegacyRecoveryKeys,
  readRecovery,
  recoveryKey,
  saveRecovery,
} from "./recovery";
import { server } from "./test-setup";
import { uploadFile } from "./upload";
import { usePublication } from "./use-publication";

const capabilities = {
  version: "1", productId: "sample", project: "project-1",
  currentUser: { subject: "operator-1", displayName: "Operator", roles: ["admin"] },
  permissions: {
    catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true,
    userUploadCreate: true, managedPublicationWrite: false, semanticWrite: false,
    accessWrite: true, governanceRead: false, qualityWrite: false, mlopsWrite: false,
  },
  identityMode: "gateway", writeEnabled: true,
  formats: ["csv"], formatMediaTypes: { csv: "text/csv" }, query: {}, preview: {},
  limits: { querySeconds: 60, prepareSeconds: 3600 }, contracts: {}, openapi: "/openapi.json",
};

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

describe("Data UI trust boundary", () => {
  it("falls back to a temporary textarea when the Clipboard API is unavailable", async () => {
    const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    const execute = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", { configurable: true, value: execute });
    try {
      await expect(copyText("request-123")).resolves.toBe(true);
      expect(execute).toHaveBeenCalledWith("copy");
      expect(document.querySelector(".clipboard-fallback")).toBeNull();
    } finally {
      if (original) Object.defineProperty(navigator, "clipboard", original);
      else Reflect.deleteProperty(navigator, "clipboard");
      Reflect.deleteProperty(document, "execCommand");
    }
  });

  it("does not start a publication upload after cancellation during checksum", async () => {
    const controller = new AbortController();
    const xhr = vi.fn();
    vi.stubGlobal("XMLHttpRequest", xhr);
    const file = {
      name: "fixture.csv",
      size: 1,
      stream: () => ({
        async *[Symbol.asyncIterator]() {
          yield new Uint8Array([1]);
          controller.abort();
        },
      }),
    } as unknown as File;

    try {
      await expect(uploadFile("publication", "output", file, controller.signal, vi.fn()))
        .rejects.toMatchObject({ name: "AbortError" });
      expect(xhr).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps the shared diagnostic buffer bounded and subscriber-scoped", () => {
    const sink = createDiagnosticSink(2);
    const seen: string[] = [];
    const unsubscribe = sink.subscribe(entry => seen.push(entry.name));
    sink.record({ time: "now", kind: "STATE", name: "first" });
    sink.record({ time: "now", kind: "STATE", name: "second" });
    sink.record({ time: "now", kind: "STATE", name: "third" });
    unsubscribe();
    sink.record({ time: "now", kind: "STATE", name: "fourth" });
    expect(seen).toEqual(["first", "second", "third"]);
    expect(sink.snapshot().map(entry => entry.name)).toEqual(["third", "fourth"]);
  });

  it("records bounded safe diagnostics and sends a request ID", async () => {
    dataDiagnostics.clear();
    let requestId: string | null = null;
    server.use(http.get("*/data/v3/capabilities", ({ request }) => {
      requestId = request.headers.get("X-Request-ID");
      return HttpResponse.json(capabilities, { headers: { "X-Request-ID": requestId ?? "response-id" } });
    }));
    await api("data_capabilities", "capabilities");
    expect(requestId).toMatch(/^[0-9a-f-]{36}$/);
    expect(dataDiagnostics.snapshot()).toEqual([expect.objectContaining({
      kind: "HTTP", name: "data_capabilities", status: 200, requestId,
    })]);
    expect(JSON.stringify(dataDiagnostics.snapshot())).not.toContain("response body");
  });

  it("rejects an invalid successful response before rendering", async () => {
    server.use(http.get("*/data/v3/capabilities", () => HttpResponse.json({ productId: "sample" })));
    await expect(api("data_capabilities", "capabilities")).rejects.toMatchObject({ code: "API_RESPONSE_INVALID" });
  });

  it("decodes Problem, request ID and Retry-After without exposing response text", async () => {
    dataDiagnostics.clear();
    server.use(http.get("*/data/v3/capabilities", () => HttpResponse.json({
      type: "urn:sample:problem:BUSY", title: "Busy", status: 503, detail: "internal text",
      code: "SERVICE_BUSY", requestId: "body-id", traceId: "trace", errors: [],
    }, { status: 503, headers: { "X-Request-ID": "header-id", "Retry-After": "7" } })));
    await expect(api("data_capabilities", "capabilities")).rejects.toMatchObject({
      code: "SERVICE_BUSY", requestId: "header-id", retryAfterMs: 7000,
    });
    expect(dataDiagnostics.snapshot()).toEqual([expect.objectContaining({
      kind: "HTTP", name: "data_capabilities", status: 503, code: "SERVICE_BUSY", requestId: "header-id",
    })]);
    expect(JSON.stringify(dataDiagnostics.snapshot())).not.toContain("internal text");
  });

  it("scopes recovery by product, project and subject and deletes ownerless legacy keys", () => {
    localStorage.setItem("data-query", "legacy");
    localStorage.setItem("data-publication", "legacy");
    clearLegacyRecoveryKeys(localStorage);
    expect(localStorage.getItem("data-query")).toBeNull();
    const first = recoveryKey("query", "sample", "project-1", "operator-1");
    const second = recoveryKey("query", "sample", "project-1", "operator-2");
    expect(first).not.toBe(second);
    saveRecovery(localStorage, first, "query-1");
    expect(readRecovery(localStorage, first)?.id).toBe("query-1");
    expect(readRecovery(localStorage, second)).toBeNull();
  });

  it("limits retry to network, 429 and 503 and honors Retry-After and deadline", () => {
    expect(retryable(0, new PlatformUiError(503, "DOWN"))).toBe(true);
    expect(retryable(0, new PlatformUiError(422, "INVALID"))).toBe(false);
    expect(retryable(3, new PlatformUiError(0, "NETWORK_UNCERTAIN"))).toBe(false);
    expect(retryDelay(0, new PlatformUiError(429, "BUSY", null, [], 4_000))).toBe(4_000);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-17T10:01:01Z"));
    expect(deadlineExceeded("2026-10-17T10:00:00Z", 60)).toBe(true);
    vi.restoreAllMocks();
  });

  it("renders identity and hides management writes without permission", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/data/v3/capabilities", () => HttpResponse.json(capabilities)),
      http.post("*/data/v3/discovery", () => HttpResponse.json({ items: [], nextCursor: null, totalMatches: 0, matchBasis: "semantic_projection" })),
      http.get("*/data/v3/inventory", () => HttpResponse.json({
        datasetCount: 0, describedDatasetCount: 0, taggedDatasetCount: 0,
        formats: [], tags: [], projectionBasis: "current_versions",
      })),
    );
    renderApp();
    await screen.findByRole("heading", { name: "数据管理" });
    const system = screen.getByText("系统信息").closest("details")!;
    expect(system).not.toHaveAttribute("open");
    expect(screen.getByText("operator-1")).not.toBeVisible();
    await user.click(screen.getByText("系统信息"));
    expect(await screen.findByText(/operator-1/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /发布数据/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择文件")).not.toBeInTheDocument();
  });

  it("restores a scoped publication ID after a fresh mount", async () => {
    const key = recoveryKey("publication", "sample", "project-1", "operator-1");
    saveRecovery(localStorage, key, "publication-1");
    server.use(http.get("*/data/v3/publications/publication-1", () => HttpResponse.json({
      publicationId: "publication-1", state: "PUBLISHED", manifest: { outputs: [] }, uploads: [], operations: [],
      receipt: { publicationId: "publication-1", state: "PUBLISHED", outputs: [] }, idempotencyKey: "key-1",
      manifestChecksum: `sha256:${"a".repeat(64)}`, createdAt: "2026-10-17T10:00:00Z", events: [],
    })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const hook = renderHook(() => usePublication(
      "sample/project-1/operator-1",
      key,
      ["csv"],
      { csv: "text/csv" },
      1_073_741_824,
      3_600,
      async () => {},
    ), { wrapper });
    await waitFor(() => expect(hook.result.current.publication?.state).toBe("PUBLISHED"));
  });
});
