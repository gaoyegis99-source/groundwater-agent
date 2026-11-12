import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { newRequestId } from "@platform/ui-boundary";
import type { DiagnosticSink, UiDiagnostic } from "@platform/ui-boundary";

export type OperatorErrorInfo = { summary: string; action: string; technical: string };

const errorGuidance: Record<string, [string, string]> = {
  QUERY_SUBMISSION_UNCERTAIN: ["查询提交结果仍无法确认。", "核对查询状态后再尝试提交。"],
  NETWORK_UNCERTAIN: ["网络连接中断，结果尚未确认。", "检查局域网连接后重试；不要重复提交已经获得操作 ID 的请求。"],
  API_RESPONSE_INVALID: ["服务返回的数据与当前界面合同不一致。", "重新加载页面；若仍出现，请把请求 ID 交给 Operator。"],
  API_REQUEST_INVALID: ["待提交内容没有通过合同校验。", "检查标红字段和文件结构后重新提交。"],
  PROJECT_MISMATCH: ["知识包或资源不属于当前项目。", "切换到正确项目，或使用为当前项目准备的文件。"],
  CONFIGURATION_IDENTITY_MISMATCH: ["跨服务身份不一致。", "继续使用不依赖跨服务绑定的功能，并联系 Operator 检查配置。"],
  VERSION_CONFLICT: ["目标版本已被其他操作更新。", "刷新目录，核对新的当前版本后重新生成差异。"],
  LIFECYCLE_CONFLICT: ["生命周期记录已经变化。", "刷新版本详情，基于最新修订再次操作。"],
  ROLE_FORBIDDEN: ["当前身份没有执行此操作的权限。", "返回可用的只读任务，或联系 Operator 核对角色。"],
  AUTHENTICATION_REQUIRED: ["当前登录身份无效或已过期。", "重新进入开发实例并确认受信任身份配置。"],
  REQUEST_TOO_LARGE: ["文件超过当前服务允许的大小。", "拆分或压缩文件，并按页面显示的上限重新选择。"],
  IMPORT_PREFLIGHT_FAILED: ["导入预检未通过。", "先解决 expectedVersion、项目身份或结构冲突，再提交。"],
  PUBLICATION_UNAVAILABLE: ["发布服务暂时无法接收新批次。", "当前草稿仍保留，可稍后重试。"],
  KNOWLEDGE_UNAVAILABLE: ["Knowledge 服务暂时不可用。", "保留当前问题，待服务恢复后重新检索。"],
  DATA_UNAVAILABLE: ["Data 服务暂时不可用。", "通用知识功能仍可继续；精确绑定请在服务恢复后重试。"],
  HASH_WORKER_FAILED: ["浏览器无法计算文件校验值。", "重新选择文件或刷新页面后重试。"],
  UPLOAD_UNCERTAIN: ["上传结果尚未确认。", "保留操作 ID 并手动检查批次状态，不要立即重复上传。"],
};

export function explainOperatorError(value: unknown): OperatorErrorInfo {
  const error = value && typeof value === "object" ? value as {
    code?: unknown; requestId?: unknown; issues?: Array<{ pointer?: unknown; message?: unknown }>;
  } : {};
  const code = typeof error.code === "string" ? error.code : "UI_OPERATION_FAILED";
  const [summary, action] = errorGuidance[code]
    ?? (code.includes("UNAVAILABLE") || code.includes("BUSY")
      ? ["服务暂时无法完成操作。", "稍后重试；若已有操作 ID，请先检查现有操作。"]
      : code.includes("INVALID") || code.includes("VALIDATION")
        ? ["提交内容没有通过校验。", "检查输入和技术详情后重新提交。"]
        : ["操作未完成。", "保留当前输入并重试；持续失败时把技术详情交给 Operator。"]);
  const request = typeof error.requestId === "string" && error.requestId ? ` · 请求 ID ${error.requestId}` : "";
  const issue = error.issues?.[0];
  const field = issue ? ` · ${String(issue.pointer ?? "字段")}：${String(issue.message ?? "无效")}` : "";
  return { summary, action, technical: `${code}${request}${field}` };
}

export function operatorErrorText(value: unknown): string {
  const error = explainOperatorError(value);
  return `${error.summary} ${error.action}（技术详情：${error.technical}）`;
}

export type OperatorService = "data" | "knowledge";

export type OperatorActivityItem = {
  service: OperatorService;
  operationId: string;
  kind: string;
  state: string;
  title: string;
  subject: string;
  createdAt: string;
  updatedAt: string;
  href: string;
  details: Record<string, unknown>;
};

type ActivitySourceState = {
  service: OperatorService;
  items: OperatorActivityItem[];
  error: string | null;
};

function decodeActivityPage(service: OperatorService, value: unknown): OperatorActivityItem[] {
  if (!value || typeof value !== "object" || !Array.isArray((value as { items?: unknown }).items)) {
    throw new Error("API_RESPONSE_INVALID");
  }
  return (value as { items: unknown[] }).items.map(raw => {
    if (!raw || typeof raw !== "object") throw new Error("API_RESPONSE_INVALID");
    const item = raw as Record<string, unknown>;
    const required = ["operationId", "kind", "state", "title", "subject", "createdAt", "updatedAt", "href"];
    if (required.some(key => typeof item[key] !== "string" || !(item[key] as string).trim())) {
      throw new Error("API_RESPONSE_INVALID");
    }
    if (item.service !== service || !(item.href as string).startsWith(`/${service}/`)) {
      throw new Error("API_RESPONSE_INVALID");
    }
    return {
      service,
      operationId: item.operationId as string,
      kind: item.kind as string,
      state: item.state as string,
      title: item.title as string,
      subject: item.subject as string,
      createdAt: item.createdAt as string,
      updatedAt: item.updatedAt as string,
      href: item.href as string,
      details: item.details && typeof item.details === "object" ? item.details as Record<string, unknown> : {},
    };
  });
}

const inProgressActivityStates = new Set(["PREPARING", "QUEUED", "RUNNING", "VALIDATING", "INDEXING", "RETRY_WAIT"]);
const attentionActivityStates = new Set(["OPEN", "READY", "FAILED", "ERROR", "ABORTED", "WARNING", "PASSED_WITH_WARNINGS"]);
const activityStateLabels: Record<string, string> = {
  OPEN: "待上传", PREPARING: "准备中", READY: "待确认", PUBLISHED: "已发布",
  QUEUED: "排队中", RUNNING: "执行中", SUCCEEDED: "已完成", FAILED: "失败",
  CANCELLED: "已取消", ABORTED: "已终止", VALIDATING: "校验中", INDEXING: "索引中",
  RETRY_WAIT: "等待重试", ACTIVE: "有效", WITHDRAWN: "已撤回", RECORDED: "已记录",
  WARNING: "需复核", ERROR: "异常", PASSED: "检查通过", PASSED_WITH_WARNINGS: "有警告",
  CANDIDATE: "候选", VALIDATED: "已验证", APPROVED: "已批准", PRODUCTION: "生产中",
};

export type OperatorActivityGuidance = {
  summary: string;
  impact: string;
  action: string;
  category: "attention" | "in-progress" | "completed";
};

export function operatorActivityGuidance(item: OperatorActivityItem): OperatorActivityGuidance {
  if (item.state === "OPEN") {
    return {
      summary: `${item.title}尚未完成上传`,
      impact: "内容还没有进入校验或发布流程。",
      action: "打开记录，继续上传并检查文件清单。",
      category: "attention",
    };
  }
  if (item.state === "READY") {
    return {
      summary: `${item.title}已完成预检，等待确认`,
      impact: "确认前不会改变当前已发布版本。",
      action: "打开记录，核对差异与影响后确认。",
      category: "attention",
    };
  }
  if (["FAILED", "ERROR", "ABORTED"].includes(item.state)) {
    const quality = item.kind === "quality_alert" || item.kind === "quality_run";
    return {
      summary: `${item.title}${quality ? "发现阻断性质量问题" : "未完成"}`,
      impact: quality ? "该数据版本不应进入后续批准或生产使用。" : "目标结果尚未生效，现有版本保持不变。",
      action: quality ? "打开数据版本，查看规则与违规数并修正源数据。" : "打开记录查看失败原因，修正后从原操作继续。",
      category: "attention",
    };
  }
  if (["WARNING", "PASSED_WITH_WARNINGS"].includes(item.state)) {
    return {
      summary: `${item.title}需要复核`,
      impact: "流程已完成，但存在非阻断性质量问题。",
      action: "打开数据版本，确认警告是否可接受并留下处理记录。",
      category: "attention",
    };
  }
  if (item.state === "RETRY_WAIT") {
    return {
      summary: `${item.title}正在等待自动重试`,
      impact: "结果尚未完成；重复提交可能产生冲突。",
      action: "等待自动重试，持续失败时再打开记录处理。",
      category: "in-progress",
    };
  }
  if (inProgressActivityStates.has(item.state)) {
    return {
      summary: `${item.title}正在处理中`,
      impact: "结果尚未完成，当前无需重复提交。",
      action: "等待状态更新；超出预期时间后打开记录检查。",
      category: "in-progress",
    };
  }
  return {
    summary: item.title,
    impact: "该操作已经形成可追踪记录。",
    action: "如需核对结果，可打开记录查看详情。",
    category: "completed",
  };
}

export function OperationCenter({ enabled = true }: { enabled?: boolean }) {
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState("");
  const [sources, setSources] = useState<ActivitySourceState[]>([
    { service: "data", items: [], error: null },
    { service: "knowledge", items: [], error: null },
  ]);
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    const results = await Promise.all((["data", "knowledge"] as const).map(async service => {
      try {
        const response = await fetch(`/${service}/v3/activity?limit=30`, {
          signal,
          headers: { "X-Request-ID": newRequestId() },
        });
        if (!response.ok) throw new Error(`HTTP_${response.status}`);
        return { service, items: decodeActivityPage(service, await response.json()), error: null };
      } catch (caught) {
        // Unmounting aborts both requests; collapsing the details keeps polling active.
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return { service, items: [], error: null };
        }
        return {
          service,
          items: [],
          error: caught instanceof Error ? caught.message : "SERVICE_UNAVAILABLE",
        };
      }
    }));
    if (!signal?.aborted) {
      setSources(results);
      setUpdatedAt(new Date().toISOString());
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (!enabled) return undefined;
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => void load(controller.signal), 30_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [enabled, load]);
  if (!enabled) return null;
  const items = sources.flatMap(source => source.items).sort((left, right) => (
    Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
  ));
  const attention = items.filter(item => attentionActivityStates.has(item.state));
  const inProgress = items.filter(item => inProgressActivityStates.has(item.state));
  const completed = items.length - attention.length - inProgress.length;
  const next = attention[0] ?? inProgress[0] ?? items[0];
  const nextGuidance = next ? operatorActivityGuidance(next) : null;
  const unavailable = sources.filter(source => source.error).map(source => source.service);
  return (
    <details className="operation-center">
      <summary>
        <strong>操作记录</strong>
        <span className="operation-summary" aria-live="polite">
          {loading && !updatedAt ? <span>正在读取…</span> : <>
            {attention.length > 0 && <span className="status-warning">{attention.length} 项需处理</span>}
            {inProgress.length > 0 && <span>{inProgress.length} 项进行中</span>}
            {!attention.length && !inProgress.length && !unavailable.length && <span>暂无待处理</span>}
            {unavailable.length > 0 && <span className="status-warning">{unavailable.map(value => value === "data" ? "Data" : "Knowledge").join("、")} 状态暂不可用</span>}
          </>}
        </span>
      </summary>
      <div className="operation-body">
      <div className="operation-overview" aria-label="操作状态摘要">
        <div className={attention.length > 0 ? "attention" : undefined}><strong>{attention.length}</strong><span>待处理</span></div>
        <div><strong>{inProgress.length}</strong><span>进行中</span></div>
        <div><strong>{completed}</strong><span>近期完成</span></div>
      </div>
      {next && nextGuidance && (
        <div className={`operation-next ${nextGuidance.category}`}>
          <span className={`operation-service ${next.service}`}>{next.service === "data" ? "Data" : "Knowledge"}</span>
          <div>
            <strong>下一步：{nextGuidance.summary}</strong>
            <p>{nextGuidance.action}</p>
          </div>
          <a href={next.href}>打开处理</a>
        </div>
      )}
      <div className="operation-center-toolbar">
        <p aria-live="polite">{loading ? "正在刷新操作状态…" : updatedAt ? `更新于 ${new Date(updatedAt).toLocaleTimeString()}` : "尚未加载"}</p>
        <button type="button" className="secondary" disabled={loading} onClick={() => void load()}>{loading ? "刷新中…" : "立即刷新"}</button>
      </div>
      {unavailable.length > 0 && (
        <p role="status" className="operation-partial">{unavailable.map(value => value === "data" ? "Data" : "Knowledge").join("、")} 状态暂不可用；其余服务记录仍可查看。</p>
      )}
      {!loading && items.length === 0 ? <p className="muted operation-empty">当前没有可显示的操作记录。</p> : (
        <details className="operation-history">
          <summary>查看全部活动（{items.length}）</summary>
          <ol className="operation-list">
            {items.map(item => {
              const guidance = operatorActivityGuidance(item);
              const errorCode = typeof item.details.errorCode === "string" ? item.details.errorCode : null;
              return (
                <li key={`${item.service}-${item.operationId}`}>
                  <span className={`operation-service ${item.service}`}>{item.service === "data" ? "Data" : "Knowledge"}</span>
                  <div>
                    <a href={item.href}>{guidance.summary}</a>
                    <small>{guidance.impact}</small>
                    <details className="operation-technical">
                      <summary>技术详情</summary>
                      <code>操作 ID {item.operationId}{errorCode ? ` · 错误码 ${errorCode}` : ""} · {item.subject} · {new Date(item.updatedAt).toLocaleString()}</code>
                    </details>
                  </div>
                  <span className={`operation-state state-${item.state.toLowerCase().replaceAll("_", "-")}`}>{activityStateLabels[item.state] ?? item.state}</span>
                </li>
              );
            })}
          </ol>
        </details>
      )}
      </div>
    </details>
  );
}

export function WorkspaceHeader({ current }: { current: OperatorService }) {
  return (
    <header className="workspace-header">
      <a className="operator-brand" href={current === "data" ? "/data/" : "/knowledge/"}>
        管理工作台
      </a>
      <nav className="service-nav" aria-label="管理服务">
        <a aria-current={current === "data" ? "page" : undefined} href="/data/">数据</a>
        <a aria-current={current === "knowledge" ? "page" : undefined} href="/knowledge/">知识</a>
      </nav>
    </header>
  );
}

export function SystemInformation({ service, currentUser, project, productId, writeEnabled, note }: {
  service: OperatorService;
  currentUser: { displayName: string; subject: string; roles: readonly string[] };
  project: string;
  productId: string;
  writeEnabled: boolean;
  note?: string;
}) {
  return <details className="system-information">
    <summary>
      <span>系统信息</span>
      <span className="system-context">{currentUser.displayName} · {project}</span>
      <span className="system-connection"><span className="status-dot" aria-hidden="true" />已连接{!writeEnabled && " · 只读"}</span>
    </summary>
    <dl className="environment-grid">
      <div><dt>当前用户</dt><dd>{currentUser.displayName}</dd></div>
      <div><dt>身份标识</dt><dd><code>{currentUser.subject}</code></dd></div>
      <div><dt>角色</dt><dd>{currentUser.roles.join("、")}</dd></div>
      <div><dt>项目 / 产品</dt><dd>{project} / {productId}</dd></div>
      <div><dt>服务</dt><dd>{service === "data" ? "Data" : "Knowledge"} 服务已连接</dd></div>
      <div><dt>写入状态</dt><dd>{writeEnabled ? "管理员写入已启用" : "只读部署"}</dd></div>
    </dl>
    {note && <p className="muted system-note">{note}</p>}
  </details>;
}

export type TaskTab<Value extends string> = {
  id: Value;
  label: string;
  description?: string;
};

export function tabIds(prefix: string, value: string) {
  return {
    tab: `${prefix}-tab-${value}`,
    panel: `${prefix}-panel-${value}`,
  };
}

export function TaskTabs<Value extends string>({
  prefix,
  label,
  tabs,
  value,
  onChange,
}: {
  prefix: string;
  label: string;
  tabs: readonly TaskTab<Value>[];
  value: Value;
  onChange: (next: Value) => void;
}) {
  const selectFromKeyboard = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    const tab = tabs[next]!;
    onChange(tab.id);
    requestAnimationFrame(() => document.getElementById(tabIds(prefix, tab.id).tab)?.focus());
  };
  return (
    <div className="task-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => {
        const ids = tabIds(prefix, tab.id);
        const selected = value === tab.id;
        return (
          <button
            key={tab.id}
            id={ids.tab}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={ids.panel}
            tabIndex={selected ? 0 : -1}
            className={selected ? "active" : undefined}
            onClick={() => onChange(tab.id)}
            onKeyDown={event => selectFromKeyboard(event, index)}
          >
            <span>{tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function readUrlValue<Value extends string>(param: string, values: readonly Value[], fallback: Value): Value {
  const candidate = new URL(window.location.href).searchParams.get(param);
  return candidate !== null && values.includes(candidate as Value) ? candidate as Value : fallback;
}

export function useUrlView<Value extends string>(
  param: string,
  values: readonly Value[],
  fallback: Value,
): [Value, (next: Value) => void] {
  const [value, setValue] = useState<Value>(() => readUrlValue(param, values, fallback));
  useEffect(() => {
    const onPopState = () => setValue(readUrlValue(param, values, fallback));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [fallback, param, values]);
  const update = useCallback((next: Value) => {
    const url = new URL(window.location.href);
    if (url.searchParams.get(param) === next) return;
    url.searchParams.set(param, next);
    window.history.pushState({}, "", url);
    setValue(next);
  }, [param]);
  return [value, update];
}

export function useVisitedViews<Value extends string>(value: Value): ReadonlySet<Value> {
  const visited = useRef(new Set<Value>());
  visited.current.add(value);
  return visited.current;
}

export function useUrlParam(param: string, fallback = ""): [string, (next: string, mode?: "push" | "replace") => void] {
  const read = useCallback(() => new URL(window.location.href).searchParams.get(param) ?? fallback, [fallback, param]);
  const [value, setValue] = useState(read);
  useEffect(() => {
    const onPopState = () => setValue(read());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [read]);
  const update = useCallback((next: string, mode: "push" | "replace" = "push") => {
    const url = new URL(window.location.href);
    if (next) url.searchParams.set(param, next);
    else url.searchParams.delete(param);
    window.history[mode === "replace" ? "replaceState" : "pushState"]({}, "", url);
    setValue(next);
  }, [param]);
  return [value, update];
}

export function DiagnosticsPanel({ sink }: { sink: DiagnosticSink }) {
  const [entries, setEntries] = useState<UiDiagnostic[]>(() => sink.snapshot());
  useEffect(() => sink.subscribe(entry => {
    setEntries(current => [...current.slice(-79), entry]);
  }), [sink]);
  return (
    <details className="diagnostics-panel">
      <summary>诊断信息</summary>
      <p className="muted">仅显示操作、状态、耗时和请求 ID；不记录响应正文、文件内容或凭据。</p>
      <div className="controls">
        <button type="button" className="secondary" onClick={() => { sink.clear(); setEntries([]); }}>清空</button>
      </div>
      {entries.length === 0 ? <p className="muted">暂无诊断记录。</p> : (
        <ol className="diagnostics-list">
          {entries.map((entry, index) => (
            <li key={`${entry.time}-${index}`}>
              <code>{entry.time}</code>{" "}
              <strong>{entry.kind}</strong> {entry.name}
              {entry.status === undefined ? "" : ` · HTTP ${entry.status}`}
              {entry.code ? ` · ${entry.code}` : ""}
              {entry.elapsedMs === undefined ? "" : ` · ${entry.elapsedMs} ms`}
              {entry.requestId ? <small> · 请求 ID：{entry.requestId}</small> : null}
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}
