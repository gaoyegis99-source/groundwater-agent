import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { knowledgeApi, knowledgeDiagnostics, PlatformUiError } from "./api";
import { retryDelay, retryable } from "./polling";
import {
  clearLegacyImport,
  importRecoveryKey,
  readImportRecovery,
  saveImportRecovery,
} from "./recovery";
import { server } from "./test-setup";

const knowledgeCapabilities = {
  version: "2.0.0", productId: "sample", project: "project-1",
  currentUser: { subject: "operator-1", displayName: "Operator", roles: ["admin"] },
  permissions: {
    catalogRead: true, retrievalCreate: true, evidenceRead: true, evidenceValidate: true,
    importWrite: false, lifecycleWrite: false, accessWrite: true,
  },
  identity: "gateway", writeEnabled: true, schema: "knowledge-package.v1",
  databaseSchema: 6, retrievalScopes: ["general", "bound", "combined"],
  defaultScope: "general_without_binding_combined_with_binding",
  dataRetry: { maxAttempts: 5, delaysSeconds: [5, 15], deadlineSeconds: 600 },
  modelProfile: "embedding-v1", indexGeneration: "generation-1", vectorSearch: "exact",
  maxBodyBytes: 16_777_216, dataConfigured: false, openapi: "/openapi.json",
};

const dataCapabilities = {
  version: "1", productId: "other", project: "project-1",
  currentUser: { subject: "operator-1", displayName: "Operator", roles: ["admin"] },
  permissions: { catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true, userUploadCreate: true, managedPublicationWrite: true, semanticWrite: true, accessWrite: true, governanceRead: true, qualityWrite: true, mlopsWrite: true },
  identityMode: "gateway", writeEnabled: true, formats: [], formatMediaTypes: {},
  query: {}, preview: {}, limits: {}, contracts: {}, openapi: "/openapi.json",
};

function handlers(capabilities = knowledgeCapabilities) {
  server.use(
    http.get("*/knowledge/v3/capabilities", () => HttpResponse.json(capabilities)),
    http.get("*/knowledge/v3/imports", () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get("*/knowledge/v3/documents", () => HttpResponse.json({ items: [], nextCursor: null, totalMatches: 0 })),
  );
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

describe("Knowledge UI trust boundary", () => {
  it("records safe diagnostics and propagates a request ID", async () => {
    knowledgeDiagnostics.clear();
    let requestId: string | null = null;
    server.use(http.get("*/knowledge/v3/capabilities", ({ request }) => {
      requestId = request.headers.get("X-Request-ID");
      return HttpResponse.json(knowledgeCapabilities, { headers: { "X-Request-ID": requestId ?? "response-id" } });
    }));
    await knowledgeApi("knowledge_capabilities", "capabilities");
    expect(requestId).toMatch(/^[0-9a-f-]{36}$/);
    expect(knowledgeDiagnostics.snapshot()).toEqual([expect.objectContaining({
      kind: "HTTP", name: "knowledge_capabilities", status: 200, requestId,
    })]);
  });

  it("rejects an invalid successful response and preserves stable errors", async () => {
    server.use(http.get("*/knowledge/v3/capabilities", () => HttpResponse.json({ version: "2" })));
    await expect(knowledgeApi("knowledge_capabilities", "capabilities")).rejects.toMatchObject({ code: "API_RESPONSE_INVALID" });
  });

  it("decodes request ID and Retry-After from Problem responses", async () => {
    knowledgeDiagnostics.clear();
    server.use(http.get("*/knowledge/v3/capabilities", () => HttpResponse.json({
      type: "urn:sample:problem:BUSY", title: "Busy", status: 429, detail: "hidden",
      code: "CAPACITY_LIMIT", requestId: "body-id", traceId: "trace", errors: [],
    }, { status: 429, headers: { "X-Request-ID": "request-1", "Retry-After": "3" } })));
    await expect(knowledgeApi("knowledge_capabilities", "capabilities")).rejects.toMatchObject({
      code: "CAPACITY_LIMIT", requestId: "request-1", retryAfterMs: 3000,
    });
    expect(knowledgeDiagnostics.snapshot()).toEqual([expect.objectContaining({
      kind: "HTTP", name: "knowledge_capabilities", status: 429, code: "CAPACITY_LIMIT", requestId: "request-1",
    })]);
  });

  it("scopes import recovery and removes the ownerless legacy key", () => {
    sessionStorage.setItem("knowledge-import", "legacy");
    clearLegacyImport(sessionStorage);
    expect(sessionStorage.getItem("knowledge-import")).toBeNull();
    const first = importRecoveryKey("sample", "project-1", "operator-1");
    const second = importRecoveryKey("sample", "project-1", "operator-2");
    saveImportRecovery(sessionStorage, first, "import-1");
    expect(readImportRecovery(sessionStorage, first)?.id).toBe("import-1");
    expect(readImportRecovery(sessionStorage, second)).toBeNull();
  });

  it("does not retry 4xx and caps Retry-After", () => {
    expect(retryable(0, new PlatformUiError(503, "DOWN"))).toBe(true);
    expect(retryable(0, new PlatformUiError(403, "FORBIDDEN"))).toBe(false);
    expect(retryDelay(0, new PlatformUiError(429, "BUSY", null, [], 60_000))).toBe(30_000);
  });

  it("does not start import polling when submit returns 422", async () => {
    const user = userEvent.setup();
    const capabilities = {
      ...knowledgeCapabilities,
      permissions: { ...knowledgeCapabilities.permissions, importWrite: true },
    };
    let undefinedPolls = 0;
    server.use(
      http.get("*/knowledge/v3/capabilities", () => HttpResponse.json(capabilities)),
      http.get("*/knowledge/v3/imports", () => HttpResponse.json({ items: [], nextCursor: null })),
      http.get("*/knowledge/v3/documents", () => HttpResponse.json({ items: [], nextCursor: null, totalMatches: 0 })),
      http.post("*/knowledge/v3/imports/preflight", async ({ request }) => {
        const pkg = await request.json() as { documents: Array<{ knowledgeId: string; title: string; contentVersion: string; sources: Array<{ reviewAfter: string }>; bindings?: unknown[] }> };
        return HttpResponse.json({
          projectMatches: true, canSubmit: true, generatedAt: "2026-10-17T10:00:00Z",
          checks: [{ name: "package_schema", state: "PASSED", detail: "结构有效" }],
          documents: pkg.documents.map(item => ({
            knowledgeId: item.knowledgeId, title: item.title, operation: "CREATE", currentVersion: null,
            expectedVersion: null, expectedVersionMatches: true, currentContentVersion: null,
            nextContentVersion: item.contentVersion, contentVersionChanged: true, contentChanged: true, sourceCount: item.sources.length,
            nextReviewAfter: item.sources[0]!.reviewAfter, bindingAdded: item.bindings?.length ?? 0,
            bindingRemoved: 0, bindingUnchanged: 0,
          })),
        });
      }),
      http.post("*/knowledge/v3/imports", () => HttpResponse.json({
        type: "urn:sample:problem:PACKAGE_INVALID",
        title: "Invalid package",
        status: 422,
        detail: "invalid package",
        code: "PACKAGE_INVALID",
        requestId: "request-422",
        traceId: "trace-422",
        errors: [],
      }, { status: 422 })),
      http.get("*/knowledge/v3/imports/undefined", () => {
        undefinedPolls += 1;
        return HttpResponse.json({ items: [], nextCursor: null }, { status: 404 });
      }),
    );
    renderApp();
    await user.click(await screen.findByRole("tab", { name: /导入更新/ }));
    const fileInput = await screen.findByLabelText("知识包文件");
    const pkg = {
      schemaVersion: "knowledge-package.v1",
      projectId: capabilities.project,
      idempotencyKey: "ui-422-regression",
      documents: [{
        knowledgeId: "ui-422-document",
        title: "UI 422 regression",
        contentVersion: "1",
        sources: [{ sourceId: "manual", kind: "DEMO_ASSUMPTION", title: "Test source", publisher: "Test", reviewAfter: "2099-01-01" }],
        units: [{ unitId: "u0", content: "Invalid submit must not poll.", sourceRefs: ["manual"] }],
      }],
    };
    const file = new File([JSON.stringify(pkg)], "package.json", { type: "application/json" });
    // jsdom's File implementation does not expose Blob.text() consistently
    // across supported Node versions; the browser path is covered by Playwright.
    Object.defineProperty(file, "text", { value: async () => JSON.stringify(pkg) });
    await user.upload(fileInput, file);
    await user.click(await screen.findByRole("button", { name: "确认并提交导入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("PACKAGE_INVALID");
    await waitFor(() => expect(undefinedPolls).toBe(0));
    expect(sessionStorage.getItem("knowledge-import:v1:sample/project-1/operator-1")).toBeNull();
  });

  it("hides import writes when the capability permission is false", async () => {
    const user = userEvent.setup();
    handlers();
    renderApp();
    await screen.findByRole("heading", { name: "知识管理" });
    await user.click(screen.getByText("系统信息"));
    expect(await screen.findByText(/operator-1/)).toBeVisible();
    await user.click(screen.getByRole("tab", { name: /导入更新/ }));
    expect(screen.queryByLabelText("知识包文件")).not.toBeInTheDocument();
    expect(screen.getByText("当前身份没有导入权限。")).toBeVisible();
  });

  it("closes the reader when URL navigation changes task and retains the explicit historical version", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/knowledge/?view=retrieval&document=d1&version=historical-v1");
    handlers();
    server.use(
      http.get("*/knowledge/v3/documents/d1/versions", () => HttpResponse.json({ items: [], nextCursor: null })),
      http.get("*/knowledge/v3/versions/historical-v1/lifecycle", () => HttpResponse.json({ items: [], nextCursor: null })),
      http.get("*/knowledge/v3/versions/historical-v1", () => HttpResponse.json({ type: "urn:problem:FORBIDDEN", title: "Forbidden", status: 403, code: "FORBIDDEN", detail: "Access denied", errors: [] }, { status: 403 })),
    );
    renderApp();
    await screen.findByRole("heading", { name: "知识管理" }, { timeout: 5000 });
    await user.click(await screen.findByRole("tab", { name: "知识目录" }));
    await screen.findByRole("dialog", { name: "知识阅读" });
    act(() => {
      window.history.replaceState({}, "", "/knowledge/?view=retrieval&document=d1&version=historical-v1");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "知识阅读" })).not.toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "证据检索" })).toHaveAttribute("aria-selected", "true");
    expect(new URLSearchParams(window.location.search).get("version")).toBe("historical-v1");
    await user.click(screen.getByRole("tab", { name: "知识目录" }));
    await screen.findByRole("dialog", { name: "知识阅读" });
    expect(screen.getByRole("combobox", { name: "查看版本" })).toHaveValue("historical-v1");
  });

  it("fails closed for cross-service product identity mismatch", async () => {
    handlers({ ...knowledgeCapabilities, dataConfigured: true });
    server.use(http.get("*/data/v3/capabilities", () => HttpResponse.json(dataCapabilities)));
    renderApp();
    expect(await screen.findByText(/CONFIGURATION_IDENTITY_MISMATCH/, {}, { timeout: 5000 })).toBeVisible();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /证据检索/ }));
    await user.click(screen.getByText("数据范围（可选）"));
    await waitFor(() => expect(screen.getByText(/精确绑定已禁用/)).toBeVisible());
  });

  it("keeps Knowledge available when the configured Data service is down", async () => {
    handlers({ ...knowledgeCapabilities, dataConfigured: true });
    server.use(http.get("*/data/v3/capabilities", () => HttpResponse.json({
      type: "urn:sample:problem:DOWN", title: "Unavailable", status: 503,
      detail: "down", code: "DATA_UNAVAILABLE", requestId: "data-request", traceId: "trace", errors: [],
    }, { status: 503 })));
    renderApp();
    expect(await screen.findByRole("heading", { name: "知识管理" }, { timeout: 5000 })).toBeVisible();
    expect(await screen.findByText(/目录与通用检索仍可用/)).toBeVisible();
    expect(screen.getByRole("tab", { name: /知识目录/ })).toBeEnabled();
    expect(screen.getByRole("tab", { name: /证据检索/ })).toBeEnabled();
    expect(screen.getAllByText(/精确绑定已禁用/).length).toBeGreaterThan(0);
  });
});
