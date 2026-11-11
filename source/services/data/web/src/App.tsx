import { useEffect, useState } from "react";
import { identityScope } from "@platform/ui-boundary";
import {
  DiagnosticsPanel,
  OperationCenter,
  SystemInformation,
  TaskTabs,
  WorkspaceHeader,
  tabIds,
  useUrlView,
  useVisitedViews,
} from "@platform/operator-ui";
import { CatalogPanel } from "./CatalogPanel";
import { PublicationPanel } from "./PublicationPanel";
import { QueryPanel } from "./QueryPanel";
import {
  api,
  dataDiagnostics,
  decodeCapabilities,
  errorText,
  PlatformUiError,
  type DataCapabilities,
} from "./api";
import { readRecovery, recoveryKey } from "./recovery";
import { useCatalog } from "./use-catalog";
import { usePublication } from "./use-publication";
import { useQuery } from "./use-query";
import { copyText } from "./clipboard";

type DataView = "catalog" | "query" | "publication";
const DATA_VIEW_IDS: readonly DataView[] = ["catalog", "query", "publication"];

export function App() {
  const [capabilities, setCapabilities] = useState<DataCapabilities | null>(null);
  const [error, setError] = useState<PlatformUiError | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void api("data_capabilities", "capabilities", undefined, { signal: controller.signal })
      .then(value => {
        setCapabilities(decodeCapabilities(value));
        setError(null);
      })
      .catch(caught => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "UI_INITIALIZATION_FAILED"));
        }
      });
    return () => controller.abort();
  }, []);
  if (error) return <InitializationError error={error} />;
  if (!capabilities) return <main><p role="status">正在读取 Data 管理边界…</p></main>;
  return <DataWorkspace capabilities={capabilities} />;
}

function InitializationError({ error }: { error: PlatformUiError }) {
  const unavailable = error.status === 0 || error.status === 502 || error.status === 503;
  return (
    <main className="initialization-error">
      <h1>{unavailable ? "Data 服务暂时不可用" : "Data 管理配置不可用"}</h1>
      <p role="alert">{errorText(error)}</p>
      <p>管理操作保持禁用。{unavailable ? "请检查服务状态后重新加载。" : "请将请求 ID 提供给项目管理员后重新加载。"}</p>
      <button onClick={() => window.location.reload()}>重新检查</button>
      <DiagnosticsPanel sink={dataDiagnostics} />
    </main>
  );
}

function DataWorkspace({ capabilities }: { capabilities: DataCapabilities }) {
  const [error, setError] = useState<PlatformUiError | null>(null);
  const [copyStatus, setCopyStatus] = useState<"" | "copied" | "failed">("");
  const scope = identityScope(
    capabilities.productId,
    capabilities.project,
    capabilities.currentUser.subject,
  );
  const queryRecoveryKey = recoveryKey("query", capabilities.productId, capabilities.project, capabilities.currentUser.subject);
  const publicationRecoveryKey = recoveryKey("publication", capabilities.productId, capabilities.project, capabilities.currentUser.subject);
  const fallbackView = (() => {
    const queryRecord = readRecovery(localStorage, queryRecoveryKey);
    const publicationRecord = capabilities.permissions.managedPublicationWrite
      ? readRecovery(localStorage, publicationRecoveryKey)
      : null;
    if (publicationRecord && (!queryRecord || publicationRecord.createdAt >= queryRecord.createdAt)) return "publication";
    return queryRecord ? "query" : "catalog";
  })() satisfies DataView;
  const availableViews = capabilities.permissions.managedPublicationWrite
    ? DATA_VIEW_IDS
    : DATA_VIEW_IDS.filter(value => value !== "publication");
  const [view, setView] = useUrlView("view", availableViews, fallbackView);
  const visitedViews = useVisitedViews(view);
  const run = async (task: () => Promise<void>) => {
    setError(null);
    setCopyStatus("");
    try {
      await task();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "UI_OPERATION_FAILED"));
    }
  };
  const catalog = useCatalog(
    run,
    visitedViews.has("catalog"),
    capabilities.permissions.semanticWrite,
  );
  const query = useQuery(
    catalog.selected,
    catalog.history,
    scope,
    queryRecoveryKey,
    capabilities.limits.querySeconds ?? 60,
    visitedViews.has("query"),
  );
  const publication = usePublication(
    scope,
    publicationRecoveryKey,
    capabilities.formats,
    capabilities.formatMediaTypes,
    capabilities.limits.uploadBytes ?? 1_073_741_824,
    capabilities.limits.prepareSeconds ?? 3_600,
    catalog.load,
    visitedViews.has("publication"),
  );
  const tabs: Array<{ id: DataView; label: string }> = [
    { id: "catalog", label: "数据目录" },
    { id: "query", label: "查询分析" },
    ...(capabilities.permissions.managedPublicationWrite
      ? [{ id: "publication" as const, label: "发布数据" }]
      : []),
  ];
  return (
    <main className="workspace-shell">
      <WorkspaceHeader current="data" />
      <section className="workspace-intro" aria-labelledby="workspace-title">
        <h1 id="workspace-title">数据管理</h1>
      </section>
      <SystemInformation service="data" currentUser={capabilities.currentUser} project={capabilities.project} productId={capabilities.productId} writeEnabled={capabilities.writeEnabled} />
      <OperationCenter enabled={capabilities.currentUser.roles.includes("admin")} />
      {error && (
        <div role="alert" className="error">
          <strong>{errorText(error)}</strong>
          {error.requestId && (
            <>
              <code tabIndex={0}>{error.requestId}</code>
              <button onClick={() => void copyText(error.requestId!).then(copied => setCopyStatus(copied ? "copied" : "failed"))}>
                {copyStatus === "copied" ? "已复制" : "复制请求 ID"}
              </button>
            </>
          )}
          {copyStatus === "failed" && <span>自动复制不可用，请选择上方 ID 手动复制。</span>}
          <button className="secondary" onClick={() => { setError(null); setCopyStatus(""); }}>关闭</button>
        </div>
      )}
      <TaskTabs prefix="data-task" label="数据管理任务" tabs={tabs} value={view} onChange={setView} />
      {catalog.selected && view !== "catalog" && (
        <aside className="working-context" aria-label="当前工作数据">
          <div>
            <strong>{catalog.selected.title}</strong>
          </div>
          <button className="secondary" onClick={() => setView("catalog")}>返回数据目录</button>
          {view !== "query" && <button onClick={() => setView("query")}>查询这份数据</button>}
        </aside>
      )}
      {visitedViews.has("catalog") && <div id={tabIds("data-task", "catalog").panel} role="tabpanel" aria-labelledby={tabIds("data-task", "catalog").tab} hidden={view !== "catalog"}>
        <CatalogPanel
          controller={catalog}
          run={run}
          canWriteSemantics={capabilities.permissions.semanticWrite}
          canPreview={capabilities.permissions.previewCreate}
          canQueryContent={capabilities.permissions.queryCreate}
          previewDeadlineSeconds={capabilities.limits.previewSeconds ?? 60}
          queryDeadlineSeconds={capabilities.limits.querySeconds ?? 60}
          onQuery={() => setView("query")}
        />
      </div>}
      {visitedViews.has("query") && <div id={tabIds("data-task", "query").panel} role="tabpanel" aria-labelledby={tabIds("data-task", "query").tab} hidden={view !== "query"}>
        <QueryPanel
          controller={query}
          run={run}
          allowed={capabilities.permissions.queryCreate}
          onChooseDataset={() => setView("catalog")}
        />
      </div>}
      {capabilities.permissions.managedPublicationWrite && visitedViews.has("publication") && (
        <div id={tabIds("data-task", "publication").panel} role="tabpanel" aria-labelledby={tabIds("data-task", "publication").tab} hidden={view !== "publication"}>
          <PublicationPanel controller={publication} run={run} formats={capabilities.formats} />
        </div>
      )}
      <DiagnosticsPanel sink={dataDiagnostics} />
    </main>
  );
}
