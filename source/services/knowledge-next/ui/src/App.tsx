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
import { ImportPanel } from "./ImportPanel";
import { RetrievalPanel } from "./RetrievalPanel";
import {
  dataApi,
  decodeDataCapabilities,
  decodeKnowledgeCapabilities,
  knowledgeDiagnostics,
  errorText,
  knowledgeApi,
  PlatformUiError,
} from "./api";
import type { DataCapabilities, KnowledgeCapabilities } from "./api";
import { importRecoveryKey, readImportRecovery } from "./recovery";

type Boundary = {
  knowledge: KnowledgeCapabilities;
  data: DataCapabilities | null;
  compatible: boolean;
  dataError: PlatformUiError | null;
};

type KnowledgeView = "catalog" | "retrieval" | "import";
const KNOWLEDGE_VIEW_IDS: readonly KnowledgeView[] = ["catalog", "retrieval", "import"];

export function App() {
  const [boundary, setBoundary] = useState<Boundary | null>(null);
  const [error, setError] = useState<PlatformUiError | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const knowledge = decodeKnowledgeCapabilities(await knowledgeApi("knowledge_capabilities", "capabilities", undefined, controller.signal));
        let data: DataCapabilities | null = null;
        let dataError: PlatformUiError | null = null;
        if (knowledge.dataConfigured) {
          try {
            data = decodeDataCapabilities(await dataApi("data_capabilities", "capabilities", controller.signal));
          } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") throw caught;
            dataError = caught instanceof PlatformUiError
              ? caught
              : new PlatformUiError(0, "DATA_BOUNDARY_UNAVAILABLE");
          }
        }
        const compatible = data === null || (
          data.productId === knowledge.productId
          && data.project === knowledge.project
          && data.currentUser.subject === knowledge.currentUser.subject
        );
        setBoundary({ knowledge, data, compatible, dataError });
      } catch (caught) {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "UI_INITIALIZATION_FAILED"));
        }
      }
    })();
    return () => controller.abort();
  }, []);
  if (error) {
    const unavailable = error.status === 0 || error.status === 502 || error.status === 503;
    return <main className="initialization-error"><h1>{unavailable ? "Knowledge 服务暂时不可用" : "Knowledge 管理配置不可用"}</h1><p role="alert">{errorText(error)}</p><p>管理操作保持禁用。{unavailable ? "请检查服务状态后重新加载。" : "请将请求 ID 提供给项目管理员。"}</p><button onClick={() => window.location.reload()}>重新检查</button><DiagnosticsPanel sink={knowledgeDiagnostics} /></main>;
  }
  if (!boundary) return <main><p role="status">正在读取 Knowledge 管理边界…</p></main>;
  return <KnowledgeWorkspace boundary={boundary} />;
}

function KnowledgeWorkspace({ boundary }: { boundary: Boundary }) {
  const [refreshToken, setRefreshToken] = useState(0);
  const { knowledge, data, compatible, dataError } = boundary;
  const bindingAvailable = Boolean(data && compatible && !dataError);
  const scope = identityScope(knowledge.productId, knowledge.project, knowledge.currentUser.subject);
  const recoveryKey = importRecoveryKey(knowledge.productId, knowledge.project, knowledge.currentUser.subject);
  const fallbackView: KnowledgeView = readImportRecovery(sessionStorage, recoveryKey) ? "import" : "catalog";
  const [view, setView] = useUrlView("view", KNOWLEDGE_VIEW_IDS, fallbackView);
  const visitedViews = useVisitedViews(view);
  const tabs: Array<{ id: KnowledgeView; label: string; description: string }> = [
    { id: "catalog", label: "知识目录", description: "查看版本、来源和生命周期" },
    { id: "retrieval", label: "证据检索", description: "提出问题并核对适用范围" },
    { id: "import", label: "导入更新", description: "校验并原子更新知识包" },
  ];
  return (
    <main className="workspace-shell">
      <WorkspaceHeader current="knowledge" />
      <section className="workspace-intro" aria-labelledby="workspace-title">
        <div>
          <p className="eyebrow">KNOWLEDGE OPERATIONS</p>
          <h1 id="workspace-title">知识管理工作台</h1>
          <p>先从目录核对版本、来源和生命周期，再检索可引用证据；导入更新会在完整校验后原子生效。</p>
        </div>
        <div className="service-state" aria-label="当前服务状态">
          <span className={`status-dot${bindingAvailable || !knowledge.dataConfigured ? "" : " warning"}`} aria-hidden="true" />
          <span>
            <strong>{compatible ? "Knowledge 服务已连接" : "跨服务绑定受限"}</strong>
            <small>{dataError
              ? "Data 暂不可用，精确绑定已禁用"
              : data
                ? (compatible ? "Data 精确绑定可用" : "Data 身份不匹配")
                : "当前未配置 Data 绑定"}</small>
          </span>
        </div>
      </section>
      <details className="environment-details">
        <summary>
          <span><strong>{knowledge.currentUser.displayName}</strong> / {knowledge.project}</span>
          <span className={knowledge.writeEnabled ? "status-ok" : "status-warning"}>{knowledge.writeEnabled ? "管理员写入已启用" : "只读部署"}</span>
          <span className="summary-action">查看身份与环境</span>
        </summary>
        <dl className="environment-grid">
          <div><dt>当前用户</dt><dd>{knowledge.currentUser.displayName}</dd></div>
          <div><dt>身份标识</dt><dd><code>{knowledge.currentUser.subject}</code></dd></div>
          <div><dt>角色</dt><dd>{knowledge.currentUser.roles.join("、")}</dd></div>
          <div><dt>项目 / 产品</dt><dd>{knowledge.project} / {knowledge.productId}</dd></div>
        </dl>
      </details>
      <OperationCenter enabled={knowledge.currentUser.roles.includes("admin")} />
      {dataError && (
        <div role="alert" className="configuration-error">
          <strong>Data 暂时不可用，知识目录、通用检索和生命周期管理仍可继续。</strong>
          <span>{errorText(dataError)}</span>
          <button className="secondary" onClick={() => window.location.reload()}>重新连接 Data</button>
        </div>
      )}
      {!compatible && <p role="alert" className="configuration-error">CONFIGURATION_IDENTITY_MISMATCH：Data 与 Knowledge 的 product、project 或 subject 不一致；跨服务绑定已禁用。</p>}
      <TaskTabs prefix="knowledge-task" label="知识管理任务" tabs={tabs} value={view} onChange={setView} />
      {visitedViews.has("catalog") && <div id={tabIds("knowledge-task", "catalog").panel} role="tabpanel" aria-labelledby={tabIds("knowledge-task", "catalog").tab} hidden={view !== "catalog"}>
        <CatalogPanel scope={scope} capabilities={knowledge} refreshToken={refreshToken} />
      </div>}
      {visitedViews.has("retrieval") && <div id={tabIds("knowledge-task", "retrieval").panel} role="tabpanel" aria-labelledby={tabIds("knowledge-task", "retrieval").tab} hidden={view !== "retrieval"}>
        <RetrievalPanel capabilities={knowledge} dataCapabilities={data} crossServiceCompatible={bindingAvailable} />
      </div>}
      {visitedViews.has("import") && <div id={tabIds("knowledge-task", "import").panel} role="tabpanel" aria-labelledby={tabIds("knowledge-task", "import").tab} hidden={view !== "import"}>
        <ImportPanel
          scope={scope}
          recoveryKey={recoveryKey}
          capabilities={knowledge}
          dataCapabilities={data}
          crossServiceCompatible={bindingAvailable}
          onImported={() => setRefreshToken(value => value + 1)}
        />
      </div>}
      <footer>检索结果和精确绑定均需人工复核；管理 UI 不证明知识或数据在业务上适用。</footer>
      <DiagnosticsPanel sink={knowledgeDiagnostics} />
    </main>
  );
}
