import { useEffect, useState } from "react";
import { identityScope } from "@platform/ui-boundary";
import {
  DiagnosticsPanel,
  OperationCenter,
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
  const roles = capabilities.currentUser.roles.join("、");
  const tabs: Array<{ id: DataView; label: string; description: string }> = [
    { id: "catalog", label: "数据目录", description: "查找、核对版本和字段含义" },
    { id: "query", label: "查询分析", description: "筛选数据并比较版本指标" },
    ...(capabilities.permissions.managedPublicationWrite
      ? [{ id: "publication" as const, label: "发布数据", description: "上传并生成不可变数据版本" }]
      : []),
  ];
  return (
    <main className="workspace-shell">
      <WorkspaceHeader current="data" />
      <section className="workspace-intro" aria-labelledby="workspace-title">
        <div>
          <p className="eyebrow">DATA OPERATIONS</p>
          <h1 id="workspace-title">数据管理工作台</h1>
          <p>先选择数据并核对版本与字段含义，再进行查询、比较或发布。每一步都会保留可追溯回执。</p>
        </div>
        <div className="service-state" aria-label="当前服务状态">
          <span className="status-dot" aria-hidden="true" />
          <span><strong>Data 服务已连接</strong><small>{capabilities.writeEnabled ? "允许治理与发布" : "当前为只读模式"}</small></span>
        </div>
      </section>
      <details className="environment-details">
        <summary>
          <span><strong>{capabilities.currentUser.displayName}</strong> / {capabilities.project}</span>
          <span className={capabilities.writeEnabled ? "status-ok" : "status-warning"}>
            {capabilities.writeEnabled ? "管理员写入已启用" : "只读部署"}
          </span>
          <span className="summary-action">查看身份与环境</span>
        </summary>
        <dl className="environment-grid">
          <div><dt>当前用户</dt><dd>{capabilities.currentUser.displayName}</dd></div>
          <div><dt>身份标识</dt><dd><code>{capabilities.currentUser.subject}</code></dd></div>
          <div><dt>角色</dt><dd>{roles}</dd></div>
          <div><dt>项目 / 产品</dt><dd>{capabilities.project} / {capabilities.productId}</dd></div>
        </dl>
      </details>
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
      {catalog.selected && (
        <aside className="working-context" aria-label="当前工作数据">
          <div>
            <span className="context-label">当前工作数据</span>
            <strong>{catalog.selected.title}</strong>
            <small>{catalog.selected.descriptor.rowCount ?? "未检查"} 行 · {catalog.selected.descriptor.fields.length} 个字段</small>
          </div>
          {view !== "catalog" && <button className="secondary" onClick={() => setView("catalog")}>返回核对数据</button>}
          {view !== "query" && <button onClick={() => setView("query")}>查询这份数据</button>}
        </aside>
      )}
      {visitedViews.has("catalog") && <div id={tabIds("data-task", "catalog").panel} role="tabpanel" aria-labelledby={tabIds("data-task", "catalog").tab} hidden={view !== "catalog"}>
        <CatalogPanel
          controller={catalog}
          run={run}
          canWriteSemantics={capabilities.permissions.semanticWrite}
          canPreview={capabilities.permissions.previewCreate}
          previewDeadlineSeconds={capabilities.limits.previewSeconds ?? 60}
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
      <footer>业务含义来自声明；字段与统计检查不代表业务适用性已经确认。</footer>
      <DiagnosticsPanel sink={dataDiagnostics} />
    </main>
  );
}
