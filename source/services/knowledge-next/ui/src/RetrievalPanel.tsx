import { useState } from "react";
import { BindingPicker } from "./BindingPicker";
import {
  errorText,
  knowledgeApi,
  PlatformUiError,
  pretty,
} from "./api";
import type { DataCapabilities, KnowledgeCapabilities, KnowledgeSchema } from "./api";

const bindingLabels: Record<string, string> = {
  GENERAL: "未限定具体数据",
  EXACT: "精确绑定匹配",
  NOT_CHECKED: "未提供任务上下文",
  MISMATCH: "绑定不匹配",
};

const resultLabels: Record<string, string> = {
  OK: "已找到可用证据",
  NO_MATCH: "没有匹配证据",
  PARTIAL: "仅找到部分证据",
};

const evidenceStatusLabels: Record<string, string> = {
  OK: "可引用",
  STALE: "需要复核",
  WITHDRAWN: "已撤回",
  EXPIRED: "已过期",
  NOT_FOUND: "内容缺失",
  NEEDS_EXPANSION: "依赖片段不完整",
};

const coverageLabels: Record<string, string> = {
  retrieval_exhaustive: "是否穷尽目录",
  retrievalExhaustive: "是否穷尽目录",
  required_context_complete: "所需上下文是否完整",
  requiredContextComplete: "所需上下文是否完整",
  strategy: "检索策略",
  vector_search: "向量检索方式",
  vectorSearch: "向量检索方式",
  candidates: "候选片段",
  top_k: "最多返回",
  topK: "最多返回",
  min_score: "最低相关度",
  minScore: "最低相关度",
};

type RetrievalSnapshot = {
  query: string;
  mode: "hybrid" | "lexical";
  scope: "general" | "bound" | "combined";
  binding: KnowledgeSchema["DataBinding"] | null;
  topK: number;
  minScore: number;
  kind: string;
  sourceKinds: string[];
  language: string;
  reviewDue: "" | "true" | "false";
  submittedAt: string;
};

export function RetrievalPanel({
  capabilities,
  dataCapabilities,
  crossServiceCompatible,
}: {
  capabilities: KnowledgeCapabilities;
  dataCapabilities: DataCapabilities | null;
  crossServiceCompatible: boolean;
}) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"hybrid" | "lexical">("hybrid");
  const [scope, setScope] = useState<"" | "general" | "bound" | "combined">("");
  const [binding, setBinding] = useState<KnowledgeSchema["DataBinding"] | null>(null);
  const [topK, setTopK] = useState(5);
  const [minScore, setMinScore] = useState(0);
  const [kind, setKind] = useState("");
  const [sourceKinds, setSourceKinds] = useState("");
  const [language, setLanguage] = useState("");
  const [reviewDue, setReviewDue] = useState<"" | "true" | "false">("");
  const [result, setResult] = useState<KnowledgeSchema["EvidenceBundleV1"] | null>(null);
  const [receipt, setReceipt] = useState<KnowledgeSchema["ReceiptResponse"] | null>(null);
  const [validation, setValidation] = useState<KnowledgeSchema["EvidenceBundleV1"] | null>(null);
  const [requestContext, setRequestContext] = useState<RetrievalSnapshot | null>(null);
  const [error, setError] = useState<PlatformUiError | null>(null);
  const [busy, setBusy] = useState(false);

  const retrieve = async () => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) return;
    const effectiveScope = scope || (binding ? "combined" : "general");
    const normalizedSourceKinds = sourceKinds.split(",").map(value => value.trim()).filter(Boolean);
    const snapshot: RetrievalSnapshot = {
      query: normalizedQuery,
      mode,
      scope: effectiveScope,
      binding: binding && effectiveScope !== "general" ? binding : null,
      topK,
      minScore,
      kind,
      sourceKinds: normalizedSourceKinds,
      language,
      reviewDue,
      submittedAt: new Date().toISOString(),
    };
    setBusy(true);
    setError(null);
    setResult(null);
    setRequestContext(null);
    setReceipt(null);
    setValidation(null);
    try {
      const value = await knowledgeApi("knowledge_retrieve", "retrievals", {
        query: normalizedQuery,
        mode,
        purpose: "management-ui",
        topK,
        minScore,
        ...(kind ? { kind } : {}),
        metadataFilters: {
          sourceKinds: normalizedSourceKinds,
          ...(language ? { language } : {}),
          ...(reviewDue ? { reviewDue: reviewDue === "true" } : {}),
        },
        ...(scope ? { scope } : {}),
        ...(binding && scope !== "general" ? { binding } : {}),
      });
      setResult(value);
      setRequestContext(snapshot);
    } catch (caught) {
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "RETRIEVAL_FAILED"));
    } finally {
      setBusy(false);
    }
  };

  const loadReceipt = async () => {
    if (!result) return;
    try {
      setReceipt(await knowledgeApi("knowledge_get_retrieval_receipt", `retrievals/${encodeURIComponent(result.receiptId)}`));
    } catch (caught) {
      setError(caught as PlatformUiError);
    }
  };

  const validateEvidence = async () => {
    if (!result || result.items.length === 0) return;
    try {
      setValidation(await knowledgeApi("knowledge_validate_evidence", "evidence/validate", {
        refs: result.items.map(item => ({
          versionId: item.versionId,
          unitId: item.unitId,
          checksum: item.checksum,
        })),
      }));
    } catch (caught) {
      setError(caught as PlatformUiError);
    }
  };

  const coverageEntries = result
    ? Object.entries(result.coverage).filter(([key, value]) => key in coverageLabels && typeof value !== "object")
    : [];
  return (
    <section className="panel task-panel">
      <div className="panel-heading">
        <h2>证据检索</h2>
      </div>
      <form className="retrieval-form" onSubmit={event => { event.preventDefault(); void retrieve(); }}>
        <label className="question-field">要查证什么？
          <textarea
            aria-label="检索问题"
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="例如：地下水位异常值应如何复核？请说明适用条件和来源。"
            required
          />
        </label>
        <div className="retrieval-options">
          <label>检索方式<select aria-label="检索方式" value={mode} onChange={event => setMode(event.target.value as typeof mode)}><option value="hybrid">混合检索（推荐）</option><option value="lexical">仅关键词匹配</option></select></label>
          <label>知识范围<select aria-label="适用范围" value={scope} onChange={event => setScope(event.target.value as typeof scope)}><option value="">根据是否绑定数据自动选择</option><option value="general">仅通用知识</option><option value="bound">仅这份数据的专属知识</option><option value="combined">通用知识 + 数据专属知识</option></select></label>
        </div>
        <details className="binding-details" open={scope === "bound" || scope === "combined" ? true : undefined}>
          <summary>数据范围{binding ? " · 已绑定" : "（可选）"}</summary>
          <BindingPicker capabilities={dataCapabilities} compatible={crossServiceCompatible} value={binding} onChange={setBinding} />
        </details>
        <details className="retrieval-tuning">
          <summary>高级检索选项</summary>
          <p className="muted">提高相关度阈值会减少弱匹配，也可能漏掉有用证据。</p>
          <div className="tuning-grid">
            <label>最多返回<input aria-label="最多返回证据" type="number" min="1" max="16" value={topK} onChange={event => setTopK(Math.max(1, Math.min(16, Number(event.target.value) || 1)))} /></label>
            <label>最低相关度<input aria-label="最低相关度" type="number" min="0" max="1" step="0.05" value={minScore} onChange={event => setMinScore(Math.max(0, Math.min(1, Number(event.target.value) || 0)))} /></label>
            <label>知识类型<select aria-label="检索知识类型" value={kind} onChange={event => setKind(event.target.value)}><option value="">全部类型</option><option value="knowledge">知识</option><option value="definition">定义</option><option value="method">方法</option><option value="case">案例</option><option value="rule">规则</option></select></label>
            <label>来源类型<input aria-label="来源类型过滤" placeholder="多个类型用逗号分隔" value={sourceKinds} onChange={event => setSourceKinds(event.target.value)} /></label>
            <label>语言<input aria-label="知识语言过滤" placeholder="例如 zh-CN" value={language} onChange={event => setLanguage(event.target.value)} /></label>
            <label>复核状态<select aria-label="检索复核状态" value={reviewDue} onChange={event => setReviewDue(event.target.value as typeof reviewDue)}><option value="">全部</option><option value="true">仅已到期复核</option><option value="false">仅复核期正常</option></select></label>
          </div>
        </details>
        <div className="form-actions">
          {!capabilities.permissions.retrievalCreate && <span className="inline-error">当前身份没有创建检索的权限。</span>}
          {(scope === "bound" || scope === "combined") && !binding && <span className="muted">请先选择数据版本。</span>}
          <button disabled={busy || !capabilities.permissions.retrievalCreate || ((scope === "bound" || scope === "combined") && !binding)}>{busy ? "正在检索…" : "开始检索"}</button>
        </div>
      </form>
      {error && <p role="alert" className="inline-error">{errorText(error)}</p>}
      {result && requestContext && (
        <div id="search-status" className="retrieval-result">
          <div className="result-heading">
            <div><span className={`state-badge state-${result.status.toLowerCase()}`}>{resultLabels[result.status] ?? result.status}</span><h3>检索结果</h3></div>
            <details><summary>回执标识</summary><code>{result.receiptId}</code></details>
          </div>
          <dl className="request-context" aria-label="本次检索上下文">
            <div><dt>查证问题</dt><dd>{requestContext.query}</dd></div>
            <div><dt>检索方式</dt><dd>{requestContext.mode === "hybrid" ? "混合检索" : "仅关键词匹配"}</dd></div>
            <div><dt>实际范围</dt><dd>{requestContext.scope === "general" ? "仅通用知识" : requestContext.scope === "bound" ? "仅数据专属知识" : "通用知识 + 数据专属知识"}</dd></div>
            <div><dt>候选策略</dt><dd>Top {requestContext.topK} · 最低相关度 {requestContext.minScore.toFixed(2)}</dd></div>
            <div><dt>元数据过滤</dt><dd>{[requestContext.kind, requestContext.language, ...requestContext.sourceKinds, requestContext.reviewDue === "true" ? "已到期复核" : requestContext.reviewDue === "false" ? "复核期正常" : ""].filter(Boolean).join(" · ") || "未限制"}</dd></div>
            <div><dt>完成时间</dt><dd>{new Date(result.observedAt || requestContext.submittedAt).toLocaleString()}</dd></div>
          </dl>
          <p className="result-summary" role="status">共返回 <strong>{result.items.length}</strong> 个候选片段。引用前请检查状态、适用范围和来源。</p>
          {coverageEntries.length > 0 && (
            <dl className="coverage-grid">
              {coverageEntries.map(([key, value]) => <div key={key}><dt>{coverageLabels[key]}</dt><dd>{typeof value === "boolean" ? (value ? "是" : "否") : String(value)}</dd></div>)}
            </dl>
          )}
          <div className="actions">
            <button className="secondary" onClick={() => void loadReceipt()}>查看检索回执</button>
            <button className="secondary" disabled={!capabilities.permissions.evidenceValidate || result.items.length === 0} onClick={() => void validateEvidence()}>重新校验证据状态</button>
          </div>
          {result.items.length === 0 && <div className="empty-state compact"><strong>没有找到可引用证据</strong><p>尝试补充问题中的对象、条件或术语，或调整知识范围后重新检索。</p></div>}
          <div id="evidence">
            {result.items.map((item, index) => (
              <article className="evidence" key={`${item.versionId}:${item.unitId}`}>
                <div className="evidence-heading"><span>证据 {item.rank ?? index + 1}</span><strong>{item.documentTitle ?? item.knowledgeId ?? item.unitId}</strong>{item.relevanceScore != null && <em>{Math.round(item.relevanceScore * 100)}% 相关</em>}</div>
                <ul className="tags">
                  <li>{evidenceStatusLabels[item.status] ?? item.status}</li>
                  <li>{item.scope === "GENERAL" ? "通用知识" : "数据专属知识"}</li>
                  <li>{bindingLabels[item.bindingMatch ?? ""] ?? "范围未知"}</li>
                  {item.reviewDue && <li>需要复核</li>}
                </ul>
                <p>{item.content ?? "此片段不可引用；请检查生命周期、checksum 或预算。"}</p>
                {item.rankingReason && <p className="ranking-reason"><strong>排序依据：</strong>{item.rankingReason}</p>}
                {item.sources && item.sources.length > 0 && (
                  <div className="evidence-sources">
                    <h4>引用来源</h4>
                    <ul>{item.sources.map(source => (
                      <li key={source.sourceId}>
                        <strong>{source.title}</strong>
                        <span>{source.publisher} · 建议复核 {source.reviewAfter}</span>
                        {source.url && <a href={source.url} target="_blank" rel="noreferrer">打开来源</a>}
                      </li>
                    ))}</ul>
                  </div>
                )}
                {item.locator && Object.keys(item.locator).length > 0 && (
                  <p className="locator"><strong>来源定位：</strong>{Object.entries(item.locator).map(([key, value]) => `${key}=${value}`).join(" · ")}</p>
                )}
                <details><summary>核对来源、定位和技术身份</summary><dl className="summary-grid"><div><dt>不可变版本</dt><dd>{item.versionId}</dd></div><div><dt>知识片段</dt><dd>{item.unitId}</dd></div><div><dt>内容校验值</dt><dd><code>{item.checksum}</code></dd></div><div><dt>绑定校验时间</dt><dd>{item.bindingsVerifiedAt ?? "未校验"}</dd></div></dl><pre>{pretty({ ...item, content: undefined })}</pre></details>
              </article>
            ))}
          </div>
          {receipt && <ReceiptView receipt={receipt} />}
          {validation && <div className="operation-card"><h3>证据状态复核</h3><p>{validation.items.filter(item => item.status === "OK").length}/{validation.items.length} 个片段仍可引用</p><ul>{validation.items.map(item => <li key={`${item.versionId}:${item.unitId}`}>{item.unitId}：{evidenceStatusLabels[item.status] ?? item.status}</li>)}</ul><details><summary>技术详情</summary><pre>{pretty(validation)}</pre></details></div>}
          <details className="technical-response"><summary>技术详情：完整检索响应</summary><pre>{pretty(result)}</pre></details>
        </div>
      )}
    </section>
  );
}

function ReceiptView({ receipt }: { receipt: KnowledgeSchema["ReceiptResponse"] }) {
  const body = receipt.body;
  return (
    <div className="operation-card">
      <h3>检索回执与候选筛选</h3>
      <dl className="summary-grid">
        <div><dt>回执标识</dt><dd>{receipt.receiptId}</dd></div>
        <div><dt>回执类型</dt><dd>{receipt.kind}</dd></div>
        <div><dt>请求校验值</dt><dd><code>{receipt.requestHash}</code></dd></div>
        <div><dt>创建时间</dt><dd>{new Date(receipt.createdAt).toLocaleString()}</dd></div>
      </dl>
      <table><thead><tr><th>回执字段</th><th>值</th></tr></thead><tbody>{Object.entries(body).filter(([, value]) => typeof value !== "object").map(([key, value]) => <tr key={key}><td>{key}</td><td>{String(value)}</td></tr>)}</tbody></table>
      <details><summary>技术详情：完整回执</summary><pre>{pretty(receipt)}</pre></details>
    </div>
  );
}
