import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { newRequestId } from "@platform/ui-boundary";
import { useUrlParam } from "@platform/operator-ui";
import { BindingPicker } from "./BindingPicker";
import {
  errorText,
  knowledgeApi,
  PlatformUiError,
  pretty,
  validatePackage,
} from "./api";
import type { DataCapabilities, KnowledgeCapabilities, KnowledgeSchema } from "./api";
import { retryDelay, retryable } from "./polling";
import { readImportRecovery, saveImportRecovery } from "./recovery";

const TERMINAL = new Set(["SUCCEEDED", "FAILED"]);
const MANUAL_RETRYABLE = new Set([
  "DATA_RETRY_EXHAUSTED",
  "DATA_RETRY_DEADLINE_EXCEEDED",
  "EMBEDDING_RETRY_EXHAUSTED",
  "EMBEDDING_RETRY_DEADLINE_EXCEEDED",
]);

const importStateLabels: Record<string, string> = {
  QUEUED: "等待处理",
  VALIDATING: "正在校验知识与数据绑定",
  INDEXING: "正在建立检索索引",
  RETRY_WAIT: "等待重试外部校验",
  SUCCEEDED: "导入完成",
  FAILED: "导入失败",
};

export function ImportPanel({
  scope,
  recoveryKey,
  capabilities,
  dataCapabilities,
  crossServiceCompatible,
  onImported,
}: {
  scope: string;
  recoveryKey: string;
  capabilities: KnowledgeCapabilities;
  dataCapabilities: DataCapabilities | null;
  crossServiceCompatible: boolean;
  onImported: () => void;
}) {
  const recovered = useMemo(() => readImportRecovery(sessionStorage, recoveryKey), [recoveryKey]);
  const [urlImportId, setUrlImportId] = useUrlParam("import");
  const initialImportId = urlImportId || recovered?.id || "";
  const [importId, setImportId] = useState(initialImportId);
  const [createdAt, setCreatedAt] = useState(
    recovered && recovered.id === initialImportId ? recovered.createdAt : "",
  );
  const [knowledgePackage, setKnowledgePackage] = useState<KnowledgeSchema["KnowledgePackageRequest"] | null>(null);
  const [preflight, setPreflight] = useState<KnowledgeSchema["ImportPreflightResponse"] | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [binding, setBinding] = useState<KnowledgeSchema["DataBinding"] | null>(null);
  const [bindingMode, setBindingMode] = useState<"append" | "replace">("append");
  const [bindingConfirmOpen, setBindingConfirmOpen] = useState(false);
  const [error, setError] = useState<PlatformUiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const retryRequestId = useRef("");

  useEffect(() => {
    const value = readImportRecovery(sessionStorage, recoveryKey);
    const nextImportId = urlImportId || value?.id || "";
    setImportId(nextImportId);
    setCreatedAt(value && value.id === nextImportId ? value.createdAt : "");
  }, [recoveryKey, urlImportId]);

  const active = useQuery<KnowledgeSchema["ImportResponse"], PlatformUiError>({
    queryKey: [scope, "knowledge-import", importId],
    enabled: Boolean(importId),
    queryFn: ({ signal }) => knowledgeApi("knowledge_get_import", `imports/${encodeURIComponent(importId)}`, undefined, signal),
    retry: retryable,
    retryDelay,
    refetchInterval: query => {
      const state = query.state.data;
      if (!state || TERMINAL.has(state.state)) return false;
      const observedCreatedAt = state.createdAt || createdAt;
      const deadline = state.retryDeadline ? Date.parse(state.retryDeadline) : Date.parse(observedCreatedAt) + Number(capabilities.dataRetry.deadlineSeconds ?? 600) * 1000;
      return Date.now() > deadline ? false : 1_500;
    },
  });
  const history = useQuery({
    queryKey: [scope, "knowledge-import-history"],
    queryFn: ({ signal }) => knowledgeApi("knowledge_list_imports", "imports?limit=50", undefined, signal),
    retry: retryable,
    retryDelay,
  });

  const runPreflight = async (value: KnowledgeSchema["KnowledgePackageRequest"]) => {
    setPreflightLoading(true);
    try {
      const result = await knowledgeApi("knowledge_preflight_import", "imports/preflight", value);
      setPreflight(result);
      return result;
    } catch (caught) {
      setPreflight(null);
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "IMPORT_PREFLIGHT_FAILED"));
      return null;
    } finally {
      setPreflightLoading(false);
    }
  };

  useEffect(() => {
    if (active.error && [404, 410].includes(active.error.status)) {
      sessionStorage.removeItem(recoveryKey);
      setImportId("");
      setUrlImportId("", "replace");
      setCreatedAt("");
    }
  }, [active.error, recoveryKey, setUrlImportId]);
  useEffect(() => {
    if (active.data?.state === "SUCCEEDED") {
      void history.refetch();
      onImported();
    }
  }, [active.data?.state]);

  const chooseFile = async (file: File | undefined) => {
    setError(null);
    setKnowledgePackage(null);
    setPreflight(null);
    if (!file) return;
    if (file.size > capabilities.maxBodyBytes) {
      setError(new PlatformUiError(413, "REQUEST_TOO_LARGE"));
      return;
    }
    try {
      const value: unknown = JSON.parse(await file.text());
      const decoded = await validatePackage(value);
      if (decoded.projectId !== capabilities.project) throw new PlatformUiError(409, "PROJECT_MISMATCH");
      sessionStorage.removeItem(recoveryKey);
      setImportId("");
      setUrlImportId("", "replace");
      setCreatedAt("");
      setBinding(null);
      setBindingMode("append");
      setKnowledgePackage(decoded);
      await runPreflight(decoded);
    } catch (caught) {
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(422, "KNOWLEDGE_PACKAGE_JSON_INVALID"));
    }
  };

  const applyBinding = () => {
    if (!knowledgePackage || !binding) return;
    const updated = {
      ...knowledgePackage,
      documents: knowledgePackage.documents.map(document => {
        const current = document.bindings ?? [];
        const next = bindingMode === "replace"
          ? [binding]
          : [...current.filter(item => JSON.stringify(item) !== JSON.stringify(binding)), binding];
        return { ...document, bindings: next };
      }),
    };
    setKnowledgePackage(updated);
    void runPreflight(updated);
  };

  const startNewImport = () => {
    sessionStorage.removeItem(recoveryKey);
    retryRequestId.current = "";
    setImportId("");
    setUrlImportId("", "replace");
    setCreatedAt("");
    setKnowledgePackage(null);
    setPreflight(null);
    setBinding(null);
    setBindingMode("append");
    setError(null);
  };

  const retryImport = async () => {
    if (!importId) return;
    setSubmitting(true);
    setError(null);
    const requestId = retryRequestId.current || newRequestId();
    retryRequestId.current = requestId;
    try {
      await knowledgeApi("knowledge_retry_import", `imports/${encodeURIComponent(importId)}/retry`, { requestId });
      retryRequestId.current = "";
      await active.refetch();
    } catch (caught) {
      if (caught instanceof PlatformUiError && caught.status !== 0) retryRequestId.current = "";
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "IMPORT_RETRY_FAILED"));
    } finally {
      setSubmitting(false);
    }
  };

  const submit = async () => {
    if (!knowledgePackage) return;
    setSubmitting(true);
    setError(null);
    try {
      const validated = await validatePackage(knowledgePackage);
      const check = await runPreflight(validated);
      if (!check?.canSubmit) throw new PlatformUiError(409, "IMPORT_PREFLIGHT_FAILED");
      const accepted = await knowledgeApi("knowledge_submit_import", "imports", validated);
      const record = saveImportRecovery(sessionStorage, recoveryKey, accepted.importId);
      setImportId(accepted.importId);
      setUrlImportId(accepted.importId, "replace");
      setCreatedAt(record.createdAt);
      await active.refetch();
    } catch (caught) {
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "IMPORT_SUBMIT_FAILED"));
    } finally {
      setSubmitting(false);
    }
  };

  const current = active.data;
  const serverCreatedAt = current?.createdAt ?? createdAt;
  const retryDeadline = current?.retryDeadline ? Date.parse(current.retryDeadline) : Date.parse(serverCreatedAt) + Number(capabilities.dataRetry.deadlineSeconds ?? 600) * 1000;
  const deadlineStopped = Boolean(current && !TERMINAL.has(current.state) && Date.now() > retryDeadline);
  const terminalCode = String(((current?.error ?? current?.lastError) as { code?: unknown } | null)?.code ?? "");
  const canRetry = Boolean(current?.state === "FAILED" && MANUAL_RETRYABLE.has(terminalCode));
  return (
    <section className="panel task-panel">
      <div className="panel-heading">
        <h2>导入更新</h2>
      </div>
      {capabilities.permissions.importWrite && (
        <>
          {!knowledgePackage && !current && (
            <div className="upload-start">
              <label className="file-dropzone">
                <strong>选择知识包 JSON</strong>
                <span>文件通过预检后，由你确认提交。</span>
                <input aria-label="知识包文件" type="file" accept=".json,application/json" disabled={submitting} onChange={event => void chooseFile(event.target.files?.[0])} />
              </label>
              <details className="guidance-card">
                <summary>知识包要求</summary>
                <ul><li>每条知识都有可核验来源</li><li>复核日期与生命周期信息明确</li><li>如绑定 Data，已选择不可变版本和语义修订</li></ul>
              </details>
            </div>
          )}
          {knowledgePackage && (
            <div className="package-preview">
              <div className="preview-heading">
                <div><span className="status-chip">本地校验通过</span><h3>待导入内容</h3></div>
                <label className="replace-file">更换文件<input aria-label="更换知识包文件" type="file" accept=".json,application/json" disabled={submitting} onChange={event => void chooseFile(event.target.files?.[0])} /></label>
              </div>
              <dl className="summary-grid">
                <div><dt>项目</dt><dd>{knowledgePackage.projectId}</dd></div>
                <div><dt>文档</dt><dd>{knowledgePackage.documents.length}</dd></div>
                <div><dt>知识片段</dt><dd>{knowledgePackage.documents.reduce((total, document) => total + document.units.length, 0)}</dd></div>
                <div><dt>来源</dt><dd>{knowledgePackage.documents.reduce((total, document) => total + document.sources.length, 0)}</dd></div>
                <div><dt>已有精确绑定</dt><dd>{knowledgePackage.documents.reduce((total, document) => total + (document.bindings?.length ?? 0), 0)}</dd></div>
              </dl>
              <section className="preflight" aria-labelledby="preflight-title">
                <div className="section-heading"><h4 id="preflight-title">变更预检</h4>{preflightLoading ? <span className="state-badge state-running">正在预检</span> : preflight && <span className={`state-badge state-${preflight.canSubmit ? "ready" : "failed"}`}>{preflight.canSubmit ? "可提交" : "需要修正"}</span>}</div>
                {preflight && <>
                  <ul className="preflight-checks">{preflight.checks.map(check => <li key={check.name} className={`check-${check.state.toLowerCase()}`}><strong>{check.state === "PASSED" ? "已完成" : check.state === "PENDING" ? "提交后执行" : "未通过"}</strong><span>{check.detail}</span></li>)}</ul>
                  <div className="table-scroll" role="region" tabIndex={0} aria-label="知识导入预检表格"><table><thead><tr><th>知识</th><th>操作 / expectedVersion</th><th>内容版本</th><th>来源与复核</th><th>Data 绑定差异</th></tr></thead><tbody>{preflight.documents.map(document => <tr key={document.knowledgeId}><td>{document.title}<small>{document.knowledgeId}</small></td><td>{document.operation === "CREATE" ? "新建" : "更新"}<small className={document.expectedVersionMatches ? "status-ok" : "inline-error"}>{document.expectedVersionMatches ? "当前版本匹配" : `冲突：当前 ${document.currentVersion ?? "不存在"}`}</small></td><td>{document.currentContentVersion ?? "—"} → {document.nextContentVersion}<small>{document.contentVersionChanged ? "内容版本将变化" : "内容版本号未变化"}</small></td><td>{document.sourceCount} 个来源<small>最早复核 {document.nextReviewAfter}</small></td><td>+{document.bindingAdded} / −{document.bindingRemoved} / 保留 {document.bindingUnchanged}</td></tr>)}</tbody></table></div>
                </>}
              </section>
              <p className="muted">数据专属知识可绑定版本；通用知识无需绑定。</p>
              <BindingPicker capabilities={dataCapabilities} compatible={crossServiceCompatible} value={binding} onChange={setBinding} />
              <label className="binding-mode">应用方式
                <select value={bindingMode} onChange={event => setBindingMode(event.target.value as "append" | "replace")}>
                  <option value="append">保留现有绑定并追加</option>
                  <option value="replace">替换全部现有绑定</option>
                </select>
              </label>
              <button type="button" className="secondary" aria-haspopup="dialog" aria-expanded={bindingConfirmOpen} disabled={!binding} onPointerDown={() => setBindingConfirmOpen(true)} onClick={() => setBindingConfirmOpen(true)}>预览并应用到全部文档</button>
              {bindingConfirmOpen && <div className="modal" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setBindingConfirmOpen(false); }}>
                  <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="binding-confirm-title">
                      <h2 id="binding-confirm-title">确认批量应用 Data 绑定</h2>
                      <p>{bindingMode === "append" ? "现有绑定会保留，并追加当前选择。" : "每个文档的现有绑定都会被当前选择替换。"}</p>
                      <div className="table-scroll" role="region" tabIndex={0} aria-label="批量 Data 绑定差异表格"><table><thead><tr><th>知识</th><th>现有绑定</th><th>应用后</th></tr></thead><tbody>
                        {knowledgePackage.documents.map(document => {
                          const existing = document.bindings?.length ?? 0;
                          return <tr key={document.knowledgeId}><td>{document.title}</td><td>{existing}</td><td>{bindingMode === "replace" ? 1 : existing + (document.bindings?.some(item => JSON.stringify(item) === JSON.stringify(binding)) ? 0 : 1)}</td></tr>;
                        })}
                      </tbody></table></div>
                      <button type="button" className="secondary" onClick={() => setBindingConfirmOpen(false)}>返回核对</button>
                      <button type="button" className={bindingMode === "replace" ? "danger" : undefined} onClick={() => { applyBinding(); setBindingConfirmOpen(false); }}>确认应用</button>
                  </section>
              </div>}
              <details><summary>技术详情：只读知识包 JSON</summary><pre>{pretty(knowledgePackage)}</pre></details>
              <div className="form-actions">
                <span className="muted">Data 绑定和索引仍在提交后执行；全部成功才会切换版本。</span>
                <button id="upload-button" disabled={submitting || preflightLoading || !preflight?.canSubmit} onClick={() => void submit()}>{submitting ? "正在提交…" : preflightLoading ? "正在预检…" : "确认并提交导入"}</button>
              </div>
            </div>
          )}
        </>
      )}
      {!capabilities.permissions.importWrite && <div className="empty-state compact"><strong>当前身份没有导入权限。</strong><p>仍可在知识目录查看版本和生命周期，或在证据检索中验证现有知识。</p></div>}
      {error && <p role="alert" className="inline-error">{errorText(error)}</p>}
      {active.error && <p role="alert" className="inline-error">{errorText(active.error)}</p>}
      {deadlineStopped && <p role="status" className="notice">导入已超过服务期限，自动检查已停止；ID 已保留。<button onClick={() => void active.refetch()}>手动检查</button></p>}
      {current && (
        <div id="import-status" role="status" className="operation-card">
          <div className="result-heading">
            <div><span className={`state-badge state-${current.state.toLowerCase()}`}>{importStateLabels[current.state] ?? current.state}</span><h3>导入结果</h3></div>
            <details><summary>操作标识</summary><code>{current.importId}</code></details>
          </div>
          <dl className="summary-grid">
            <div><dt>Data 绑定核验</dt><dd>{current.dataAttempts} / {String(capabilities.dataRetry.maxAttempts ?? 5)} 次</dd></div>
            <div><dt>下一次检查</dt><dd>{current.nextRetryAt ? new Date(current.nextRetryAt).toLocaleString() : "无需重试"}</dd></div>
          </dl>
          {(current.error || current.lastError) && <p className="inline-error">{String(((current.error ?? current.lastError) as { code?: unknown }).code ?? "IMPORT_FAILED")}</p>}
          {current.result && <p className="success-message">已生成 {Array.isArray((current.result as { versions?: unknown }).versions) ? (current.result as { versions: unknown[] }).versions.length : 0} 个不可变版本。可回到“知识目录”继续核对。</p>}
          {TERMINAL.has(current.state) && (
            <div className="terminal-actions">
              {canRetry && <button disabled={submitting} onClick={() => void retryImport()}>{submitting ? "正在重新提交…" : "重新校验并重试"}</button>}
              <button className="secondary" onClick={startNewImport}>导入另一个知识包</button>
            </div>
          )}
          <details><summary>技术详情与完整回执</summary><pre>{pretty(current)}</pre></details>
        </div>
      )}
      <details className="history-details">
        <summary>查看最近导入记录</summary>
        {history.isLoading ? <p>正在读取…</p> : (
          <div className="table-scroll" role="region" tabIndex={0} aria-label="知识导入历史表格"><table><thead><tr><th>更新时间</th><th>状态</th><th>Data 核验</th><th>操作</th></tr></thead><tbody>
            {history.data?.items.map(item => <tr key={item.importId}><td>{item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "—"}<small>{item.importId}</small></td><td>{importStateLabels[item.state] ?? item.state}</td><td>{item.dataAttempts} 次</td><td><button className="link-button" onClick={() => { const record = saveImportRecovery(sessionStorage, recoveryKey, item.importId, item.createdAt ?? undefined); setImportId(item.importId); setUrlImportId(item.importId, "replace"); setCreatedAt(record.createdAt); setKnowledgePackage(null); }}>查看回执</button></td></tr>)}
          </tbody></table>
          </div>
        )}
      </details>
    </section>
  );
}
