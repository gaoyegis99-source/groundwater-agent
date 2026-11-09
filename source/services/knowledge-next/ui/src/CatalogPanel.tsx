import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useUrlParam } from "@platform/operator-ui";
import { useForm } from "react-hook-form";
import { Button, Dialog, DialogTrigger, Heading, Modal } from "react-aria-components";
import { errorText, knowledgeApi, PlatformUiError, pretty, validateKnowledgeVersion } from "./api";
import type { KnowledgeCapabilities, KnowledgeSchema } from "./api";
import { retryDelay, retryable } from "./polling";

type LifecycleForm = { reviewAfter: string; validThrough: string; reason: string };
type BatchLifecycleForm = LifecycleForm & { status: "ACTIVE" | "WITHDRAWN" };
type DiffRow = { field: string; left: string[]; right: string[] };

const lifecycleLabels: Record<string, string> = { ACTIVE: "有效", WITHDRAWN: "已撤回" };
const reviewLabels: Record<string, string> = { due: "已到期复核", upcoming: "30 天内复核", current: "复核期正常" };
const kindLabels: Record<string, string> = { knowledge: "知识", definition: "定义", method: "方法", case: "案例", rule: "规则" };
const differenceLabels: Record<string, string> = {
  contentVersion: "内容版本", title: "标题", summary: "摘要", kind: "知识类型",
  language: "语言", aliases: "别名", bindings: "数据绑定", sources: "来源", units: "知识片段",
};

function body(value: KnowledgeSchema["VersionResponse"] | null | undefined): KnowledgeSchema["Document"] | null {
  return value?.body as KnowledgeSchema["Document"] | null;
}

function bindingText(binding: KnowledgeSchema["DataBinding"]): string {
  return `${binding.datasetId} / ${binding.versionId} / ${binding.semanticId}${binding.fieldId ? ` / 字段 ${binding.fieldId}` : " / 整个版本"}`;
}

function sourceText(source: KnowledgeSchema["Source"]): string {
  return `${source.title} · ${source.publisher} · ${source.kind} · 复核 ${source.reviewAfter}${source.lifecycleStatus === "WITHDRAWN" ? " · 已撤回" : ""}`;
}

function diffValues(field: keyof KnowledgeSchema["Document"], value: KnowledgeSchema["Document"][keyof KnowledgeSchema["Document"]]): string[] {
  if (field === "sources") return (value as KnowledgeSchema["Source"][]).map(sourceText);
  if (field === "bindings") return (value as KnowledgeSchema["DataBinding"][]).map(bindingText);
  if (field === "units") return (value as KnowledgeSchema["Unit"][]).map(unit => `${unit.unitId} · ${unit.content.slice(0, 120)}${unit.content.length > 120 ? "…" : ""}`);
  if (Array.isArray(value)) return value.map(item => String(item));
  return [String(value ?? "未设置")];
}

function diff(left: KnowledgeSchema["VersionResponse"], right: KnowledgeSchema["VersionResponse"]): DiffRow[] {
  const first = body(left)!;
  const second = body(right)!;
  const rows: DiffRow[] = [];
  for (const key of ["contentVersion", "title", "summary", "kind", "language", "aliases", "bindings", "sources", "units"] as const) {
    if (JSON.stringify(first[key]) !== JSON.stringify(second[key])) rows.push({ field: key, left: diffValues(key, first[key]), right: diffValues(key, second[key]) });
  }
  return rows;
}

export function CatalogPanel({ scope, capabilities, refreshToken }: {
  scope: string;
  capabilities: KnowledgeCapabilities;
  refreshToken: number;
}) {
  const [search, setUrlSearch] = useUrlParam("q");
  const [status, setStatus] = useUrlParam("status");
  const [review, setReview] = useUrlParam("review");
  const [sourceKind, setSourceKind] = useUrlParam("source");
  const [kind, setKind] = useUrlParam("kind");
  const [bound, setBound] = useUrlParam("bound");
  const [sort, setSort] = useUrlParam("sort", "modified-desc");
  const [selectedDocumentId, setSelectedDocumentId] = useUrlParam("document");
  const [selectedVersionId, setSelectedVersionId] = useUrlParam("version");
  const [compareVersionId, setCompareVersionId] = useUrlParam("compare");
  const [searchDraft, setSearchDraft] = useState(search);
  const [error, setError] = useState<PlatformUiError | null>(null);
  const [selectedForBatch, setSelectedForBatch] = useState<Set<string>>(() => new Set());
  useEffect(() => setSearchDraft(search), [search]);

  const documentsQuery = useInfiniteQuery<KnowledgeSchema["DocumentPage"], PlatformUiError>({
    queryKey: [scope, "knowledge-documents", search, status, review, sourceKind, kind, bound, sort, refreshToken],
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ query: search, limit: "30", sort: sort || "modified-desc" });
      if (pageParam) params.set("cursor", String(pageParam));
      if (status) params.set("status", status);
      if (review) params.set("review", review);
      if (sourceKind) params.set("sourceKind", sourceKind);
      if (kind) params.set("kind", kind);
      if (bound) params.set("bound", bound);
      return knowledgeApi("knowledge_list_documents", `documents?${params}`, undefined, signal);
    },
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const documents = useMemo(() => {
    const items = documentsQuery.data?.pages.flatMap(page => page.items) ?? [];
    return [...new Map(items.map(item => [item.documentId, item])).values()];
  }, [documentsQuery.data]);
  const firstPage = documentsQuery.data?.pages[0];
  const selectedDocument = documents.find(item => item.documentId === selectedDocumentId) ?? null;
  const batchDocuments = useMemo(
    () => documents.filter(document => selectedForBatch.has(document.documentId)),
    [documents, selectedForBatch],
  );

  useEffect(() => {
    const loaded = new Set(documents.map(document => document.documentId));
    setSelectedForBatch(current => {
      const next = new Set([...current].filter(documentId => loaded.has(documentId)));
      return next.size === current.size ? current : next;
    });
  }, [documents]);

  useEffect(() => {
    if (!selectedDocument) return;
    if (!selectedVersionId || refreshToken > 0) setSelectedVersionId(selectedDocument.currentVersion, "replace");
  }, [refreshToken, selectedDocument?.currentVersion, selectedDocument?.documentId]);

  const versions = useInfiniteQuery<KnowledgeSchema["VersionPage"], PlatformUiError>({
    queryKey: [scope, "knowledge-versions", selectedDocumentId, refreshToken],
    enabled: Boolean(selectedDocumentId),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => knowledgeApi(
      "knowledge_list_document_versions",
      `documents/${encodeURIComponent(selectedDocumentId)}/versions?limit=50${pageParam ? `&cursor=${encodeURIComponent(String(pageParam))}` : ""}`,
      undefined,
      signal,
    ),
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const versionItems = versions.data?.pages.flatMap(page => page.items) ?? [];
  const selected = useQuery({
    queryKey: [scope, "knowledge-version", selectedVersionId, refreshToken],
    enabled: Boolean(selectedVersionId),
    queryFn: async ({ signal }) => {
      const value = await knowledgeApi("knowledge_get_version", `versions/${encodeURIComponent(selectedVersionId)}`, undefined, signal);
      validateKnowledgeVersion(value);
      return value;
    },
    retry: retryable,
    retryDelay,
  });
  const compared = useQuery({
    queryKey: [scope, "knowledge-version", compareVersionId, refreshToken],
    enabled: Boolean(compareVersionId),
    queryFn: async ({ signal }) => {
      const value = await knowledgeApi("knowledge_get_version", `versions/${encodeURIComponent(compareVersionId)}`, undefined, signal);
      validateKnowledgeVersion(value);
      return value;
    },
    retry: retryable,
    retryDelay,
  });
  const timeline = useQuery({
    queryKey: [scope, "knowledge-lifecycle", selectedVersionId, refreshToken],
    enabled: Boolean(selectedVersionId),
    queryFn: ({ signal }) => knowledgeApi("knowledge_get_lifecycle_history", `versions/${encodeURIComponent(selectedVersionId)}/lifecycle`, undefined, signal),
    retry: retryable,
    retryDelay,
  });
  const differences = useMemo(() => selected.data && compared.data ? diff(selected.data, compared.data) : [], [selected.data, compared.data]);
  const reportError = (caught: unknown) => setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "UI_OPERATION_FAILED"));
  const clearFilters = () => {
    setSearchDraft(""); setUrlSearch(""); setStatus("", "replace"); setReview("", "replace");
    setSourceKind("", "replace"); setKind("", "replace"); setBound("", "replace"); setSort("modified-desc", "replace");
  };
  const selectDocument = (document: KnowledgeSchema["DocumentSummary"]) => {
    setSelectedDocumentId(document.documentId);
    setSelectedVersionId(document.currentVersion, "replace");
    setCompareVersionId("", "replace");
  };
  const toggleBatchDocument = (documentId: string, checked: boolean) => {
    setSelectedForBatch(current => {
      const next = new Set(current);
      if (checked && next.size < 100) next.add(documentId);
      if (!checked) next.delete(documentId);
      return next;
    });
  };
  const refreshAfterBatch = async () => {
    setSelectedForBatch(new Set());
    const refreshes: Promise<unknown>[] = [documentsQuery.refetch()];
    if (selectedVersionId) refreshes.push(selected.refetch(), timeline.refetch());
    await Promise.all(refreshes);
  };

  return (
    <div className="catalog-workspace">
      <section className="panel catalog-browser">
        <div className="panel-heading compact">
          <div><p className="step-kicker">任务 1</p><h2>选择知识</h2><p>按业务含义筛选，打开后核对来源、绑定和生命周期。</p></div>
          {!documentsQuery.isLoading && <span className="count-badge">匹配 {firstPage?.totalMatches ?? documents.length} · 已加载 {documents.length}</span>}
        </div>
        <form className="knowledge-filters" onSubmit={event => { event.preventDefault(); setUrlSearch(searchDraft.trim()); }}>
          <label className="wide">关键词<input aria-label="目录关键词" value={searchDraft} onChange={event => setSearchDraft(event.target.value)} placeholder="标题、摘要或知识编号" /></label>
          <label>生命周期<select aria-label="生命周期筛选" value={status} onChange={event => setStatus(event.target.value)}><option value="">全部状态</option>{(firstPage?.facets?.statuses ?? []).map(item => <option key={item.name} value={item.name}>{lifecycleLabels[item.name] ?? item.name}（{item.count}）</option>)}</select></label>
          <label>复核状态<select aria-label="复核状态筛选" value={review} onChange={event => setReview(event.target.value)}><option value="">全部日期</option>{(firstPage?.facets?.reviewStates ?? []).map(item => <option key={item.name} value={item.name}>{reviewLabels[item.name] ?? item.name}（{item.count}）</option>)}</select></label>
          <label>知识类型<select aria-label="知识类型筛选" value={kind} onChange={event => setKind(event.target.value)}><option value="">全部类型</option>{(firstPage?.facets?.knowledgeKinds ?? []).map(item => <option key={item.name} value={item.name}>{kindLabels[item.name] ?? item.name}（{item.count}）</option>)}</select></label>
          <label>来源类型<select aria-label="来源类型筛选" value={sourceKind} onChange={event => setSourceKind(event.target.value)}><option value="">全部来源</option>{(firstPage?.facets?.sourceKinds ?? []).map(item => <option key={item.name} value={item.name}>{item.name}（{item.count}）</option>)}</select></label>
          <label>Data 绑定<select aria-label="Data 绑定筛选" value={bound} onChange={event => setBound(event.target.value)}><option value="">全部</option><option value="true">已绑定</option><option value="false">通用知识</option></select></label>
          <label>排序<select aria-label="知识目录排序" value={sort || "modified-desc"} onChange={event => setSort(event.target.value)}><option value="modified-desc">最近变更</option><option value="review-asc">优先复核</option><option value="title-asc">标题 A–Z</option><option value="title-desc">标题 Z–A</option><option value="id-asc">知识编号</option></select></label>
          <div className="filter-actions"><button>应用搜索</button><button type="button" className="secondary" onClick={clearFilters}>清空</button></div>
        </form>
        <p className="catalog-status" role="status">{documentsQuery.isLoading ? "正在读取知识目录…" : `共匹配 ${firstPage?.totalMatches ?? documents.length} 份知识，当前已加载 ${documents.length} 份。`}</p>
        {documentsQuery.error && <p role="alert" className="inline-error">{errorText(documentsQuery.error)}</p>}
        {!documentsQuery.isLoading && documents.length === 0 && <div className="empty-state compact"><strong>没有找到匹配的知识</strong><p>{search || status || review || sourceKind || kind || bound ? "调整或清空筛选条件后重试。" : "当前目录还没有知识，可前往“导入更新”添加第一份知识包。"}</p></div>}
        {capabilities.permissions.lifecycleWrite && documents.length > 0 && <div className="batch-selection-bar" aria-label="批量治理工具栏"><div><strong>批量治理</strong><span role="status">已选择 {batchDocuments.length} / 100 份</span></div><div><button type="button" className="secondary" onClick={() => setSelectedForBatch(new Set(documents.slice(0, 100).map(document => document.documentId)))}>选择已加载</button><button type="button" className="secondary" disabled={batchDocuments.length === 0} onClick={() => setSelectedForBatch(new Set())}>清空</button>{batchDocuments.length > 0 && <BatchLifecycleDialog key={batchDocuments.map(document => document.documentId).join("|")} documents={batchDocuments} onUpdated={refreshAfterBatch} onError={reportError} />}</div></div>}
        <div className="catalog-scroll" role="region" tabIndex={0} aria-label="知识目录结果"><ul className="catalog">{documents.map(document => <li key={document.documentId} className={selectedDocumentId === document.documentId ? "selected" : undefined}>{capabilities.permissions.lifecycleWrite && <label className="catalog-select"><input type="checkbox" aria-label={`选择 ${document.title} 进行批量治理`} checked={selectedForBatch.has(document.documentId)} disabled={!selectedForBatch.has(document.documentId) && selectedForBatch.size >= 100} onChange={event => toggleBatchDocument(document.documentId, event.target.checked)} /><span className="sr-only">选择 {document.title}</span></label>}<button onClick={() => selectDocument(document)}><span>{document.title}</span><small>{document.summary || "尚未填写摘要"}</small><span className="catalog-item-meta">{kindLabels[document.kind] ?? document.kind} · {lifecycleLabels[document.status ?? ""] ?? "状态未知"} · {reviewLabels[document.reviewState ?? ""] ?? "复核日期未知"}</span><span className="catalog-item-meta">{document.sourceCount} 个来源 · {document.hasDataBinding ? `${document.bindingCount} 个 Data 绑定` : "通用知识"}</span></button><span aria-hidden="true">›</span></li>)}</ul></div>
        {documentsQuery.hasNextPage && <button disabled={documentsQuery.isFetchingNextPage} onClick={() => void documentsQuery.fetchNextPage()}>{documentsQuery.isFetchingNextPage ? "正在加载…" : "加载更多知识"}</button>}
      </section>

      <section className="panel catalog-detail">
        <div className="panel-heading compact"><div><p className="step-kicker">核对</p><h2>版本、来源与有效性</h2><p>先读业务信息，再按需展开技术身份和完整 JSON。</p></div></div>
        {!selectedDocumentId ? <div className="empty-state detail-empty"><span className="empty-icon" aria-hidden="true">✓</span><div><strong>从目录选择一份知识。</strong><p>这里会展示来源、Data 绑定、版本差异和生命周期记录。</p></div></div> : <div className="detail-panel">
          <div className="resource-heading"><div><span className="status-chip">不可变版本</span><h3>{body(selected.data)?.title ?? selectedDocument?.title ?? selectedDocumentId}</h3><p>目录选择绑定明确版本，不会随“最新内容”静默变化。</p></div><div className="version-controls"><label>查看版本<select value={selectedVersionId} onChange={event => { setSelectedVersionId(event.target.value); setCompareVersionId("", "replace"); }}>{versionItems.map(item => <option key={item.versionId} value={item.versionId}>{new Date(item.createdAt).toLocaleString()}</option>)}</select></label><label>比较版本<select value={compareVersionId} onChange={event => setCompareVersionId(event.target.value)}><option value="">暂不比较</option>{versionItems.filter(item => item.versionId !== selectedVersionId).map(item => <option key={item.versionId} value={item.versionId}>{new Date(item.createdAt).toLocaleString()}</option>)}</select></label>{versions.hasNextPage && <button className="secondary" disabled={versions.isFetchingNextPage} onClick={() => void versions.fetchNextPage()}>更多历史版本</button>}</div></div>
          {Boolean(selected.error || versions.error || timeline.error || compared.error) && <p role="alert" className="inline-error">{errorText(selected.error ?? versions.error ?? timeline.error ?? compared.error)}</p>}
          {selected.isLoading && <p role="status">正在读取不可变知识版本…</p>}
          {selected.data && <VersionCard version={selected.data} />}
          {compared.data && <div className="version-diff"><h4>版本差异</h4>{differences.length === 0 ? <p>两版结构化内容相同。</p> : <div className="table-scroll"><table><thead><tr><th>内容项</th><th>当前版本</th><th>比较版本</th></tr></thead><tbody>{differences.map(row => <tr key={row.field}><td>{differenceLabels[row.field] ?? row.field}<small>{row.field}</small></td><td><DiffValues values={row.left} /></td><td><DiffValues values={row.right} /></td></tr>)}</tbody></table></div>}</div>}
          <div className="lifecycle-actions"><div><h4>生命周期</h4><p className="muted">撤回会阻止该版本继续作为有效证据，但不会删除审计记录。</p></div>{selected.data && capabilities.permissions.lifecycleWrite && <LifecycleDialog version={selected.data} onUpdated={async () => { await Promise.all([selected.refetch(), timeline.refetch(), documentsQuery.refetch()]); }} onError={reportError} />}</div>
          <ol className="timeline">{timeline.data?.items.map(item => <li key={item.revision}><strong>{lifecycleLabels[item.status] ?? item.status}</strong><span>{new Date(item.createdAt).toLocaleString()} · {item.reason}</span><small>修订 {item.revision} · 建议复核 {item.reviewAfter}</small></li>)}</ol>
        </div>}
        {error && <p role="alert" className="inline-error">{errorText(error)}</p>}
      </section>
    </div>
  );
}

function DiffValues({ values }: { values: string[] }) {
  if (values.length === 0) return <span className="muted">无</span>;
  return <ul className="diff-values">{values.map((value, index) => <li key={`${index}-${value}`}>{value}</li>)}</ul>;
}

function VersionCard({ version }: { version: KnowledgeSchema["VersionResponse"] }) {
  const document = body(version)!;
  return <article className="version-card" id="version-body">
    <div className="version-summary"><span className={`state-badge state-${version.status.toLowerCase()}`}>{lifecycleLabels[version.status] ?? version.status}</span><strong>{document.summary || "尚未提供内容摘要"}</strong></div>
    <dl className="summary-grid"><div><dt>内容版本</dt><dd>{document.contentVersion}</dd></div><div><dt>知识类型</dt><dd>{kindLabels[document.kind] ?? document.kind}</dd></div><div><dt>建议复核日期</dt><dd>{version.reviewAfter}</dd></div><div><dt>有效期至</dt><dd>{version.validThrough ?? "未设置"}</dd></div><div><dt>来源</dt><dd>{document.sources.length}</dd></div><div><dt>知识片段</dt><dd>{document.units.length}</dd></div><div><dt>Data 绑定</dt><dd>{document.bindings?.length ?? 0}</dd></div><div><dt>语言</dt><dd>{document.language}</dd></div></dl>
    <section className="governance-section" aria-labelledby="source-applicability-title"><h4 id="source-applicability-title">来源与适用性</h4><div className="source-cards">{document.sources.map(source => <article key={source.sourceId}><div><strong>{source.title}</strong><span className={`state-badge state-${source.lifecycleStatus.toLowerCase()}`}>{lifecycleLabels[source.lifecycleStatus] ?? source.lifecycleStatus}</span></div><p>{source.publisher} · {source.kind}{source.version ? ` · ${source.version}` : ""}</p><dl><div><dt>发布</dt><dd>{source.publishedAt ?? "未声明"}</dd></div><div><dt>访问</dt><dd>{source.accessedAt ?? "未声明"}</dd></div><div><dt>建议复核</dt><dd>{source.reviewAfter}</dd></div></dl>{source.url && <a href={source.url} target="_blank" rel="noreferrer">打开正式来源</a>}</article>)}</div></section>
    <section className="governance-section" aria-labelledby="binding-title"><h4 id="binding-title">Data 精确绑定</h4>{document.bindings?.length ? <ul className="binding-list">{document.bindings.map((binding, index) => <li key={`${binding.versionId}-${binding.fieldId ?? index}`}><strong>{binding.datasetId}</strong><span>版本 {binding.versionId} · 语义 {binding.semanticId}{binding.fieldId ? ` · 字段 ${binding.fieldId}` : " · 整个版本"}</span></li>)}</ul> : <p className="muted">这是通用知识，没有限定到特定 Data 版本。</p>}</section>
    <details className="content-details"><summary>查看知识内容（{document.units.length} 个片段）</summary>{document.units.map((unit, index) => <article key={unit.unitId} className="unit-card"><strong>片段 {index + 1}</strong><p>{unit.content}</p><span>来源：{unit.sourceRefs.join("、")}</span>{Object.keys(unit.locator ?? {}).length > 0 && <span>定位：{Object.entries(unit.locator ?? {}).map(([key, value]) => `${key}=${value}`).join(" · ")}</span>}<small>片段标识：{unit.unitId}</small></article>)}</details>
    <details><summary>技术身份与完整版本</summary><dl className="technical-grid"><div><dt>生命周期状态</dt><dd>{version.status}</dd></div><div><dt>Checksum</dt><dd><code>{version.checksum}</code></dd></div><div><dt>版本标识</dt><dd><code>{version.versionId}</code></dd></div></dl><pre>{pretty(version)}</pre></details>
  </article>;
}

function LifecycleDialog({ version, onUpdated, onError }: { version: KnowledgeSchema["VersionResponse"]; onUpdated: () => Promise<void>; onError: (error: unknown) => void }) {
  const withdrawing = version.status !== "WITHDRAWN";
  const targetStatus: "ACTIVE" | "WITHDRAWN" = withdrawing ? "WITHDRAWN" : "ACTIVE";
  const form = useForm<LifecycleForm>({ defaultValues: { reviewAfter: version.reviewAfter, validThrough: version.validThrough ?? "", reason: "" } });
  const { reset } = form;
  useEffect(() => reset({ reviewAfter: version.reviewAfter, validThrough: version.validThrough ?? "", reason: "" }), [reset, version.lifecycleRevision, version.reviewAfter, version.status, version.validThrough]);
  const submit = async (value: LifecycleForm, close: () => void) => {
    try {
      await knowledgeApi("knowledge_update_lifecycle", `versions/${encodeURIComponent(version.versionId)}/lifecycle`, { expectedRevision: version.lifecycleRevision, status: targetStatus, reviewAfter: value.reviewAfter, validThrough: value.validThrough || null, reason: value.reason });
      close(); await onUpdated();
    } catch (caught) { onError(caught); }
  };
  return <DialogTrigger><Button className={withdrawing ? "danger" : undefined}>{withdrawing ? "撤回此版本" : "恢复此版本"}</Button><Modal isDismissable className="modal"><Dialog className="dialog">{({ close }) => <form className="form-grid" onSubmit={form.handleSubmit(value => void submit(value, close))}><Heading slot="title">{withdrawing ? "撤回知识版本？" : "恢复知识版本？"}</Heading><p className="wide lifecycle-consequence">{withdrawing ? "撤回后，该不可变版本不会再作为有效证据返回；既有审计记录和历史回执仍会保留。" : "恢复后，该不可变版本将重新参与证据检索；请先确认内容、来源和复核日期仍然有效。"}</p><label>复核日期<input type="date" {...form.register("reviewAfter", { required: true })} /></label><label>失效日期<input type="date" {...form.register("validThrough")} /></label><label className="wide">原因<textarea placeholder={withdrawing ? "说明撤回原因，便于后续审计与恢复判断。" : "说明恢复依据，便于后续审计。"} {...form.register("reason", { required: true, minLength: 1, maxLength: 1000 })} /></label><Button className="secondary" onPress={close}>取消</Button><button className={withdrawing ? "danger" : undefined} type="submit">{withdrawing ? "确认撤回" : "确认恢复"}</button></form>}</Dialog></Modal></DialogTrigger>;
}

function BatchLifecycleDialog({ documents, onUpdated, onError }: {
  documents: KnowledgeSchema["DocumentSummary"][];
  onUpdated: () => Promise<void>;
  onError: (error: unknown) => void;
}) {
  const defaultReviewAfter = documents.map(document => document.reviewAfter).filter((value): value is string => Boolean(value)).sort()[0] ?? new Date().toISOString().slice(0, 10);
  const form = useForm<BatchLifecycleForm>({ defaultValues: { status: "WITHDRAWN", reviewAfter: defaultReviewAfter, validThrough: "", reason: "" } });
  const [preview, setPreview] = useState<KnowledgeSchema["LifecycleBatchPreflightResponse"] | null>(null);
  const [submittedValues, setSubmittedValues] = useState<BatchLifecycleForm | null>(null);
  const [working, setWorking] = useState(false);
  const [localError, setLocalError] = useState<unknown>(null);

  const loadPreview = async (values: BatchLifecycleForm) => {
    setWorking(true); setLocalError(null);
    try {
      const result = await knowledgeApi("knowledge_preflight_lifecycle_batch", "lifecycle/batch/preflight", {
        documentIds: documents.map(document => document.documentId), status: values.status,
        reviewAfter: values.reviewAfter, validThrough: values.validThrough || null, reason: values.reason,
      });
      setSubmittedValues(values); setPreview(result);
    } catch (caught) { setLocalError(caught); onError(caught); }
    finally { setWorking(false); }
  };
  const apply = async (close: () => void) => {
    if (!preview || !submittedValues) return;
    setWorking(true); setLocalError(null);
    try {
      await knowledgeApi("knowledge_update_lifecycle_batch", "lifecycle/batch", {
        items: preview.items.map(item => ({
          documentId: item.documentId, versionId: item.versionId, expectedRevision: item.currentRevision,
          status: submittedValues.status, reviewAfter: submittedValues.reviewAfter,
          validThrough: submittedValues.validThrough || null, reason: submittedValues.reason,
        })),
      });
      close(); await onUpdated();
    } catch (caught) { setLocalError(caught); onError(caught); }
    finally { setWorking(false); }
  };

  return <DialogTrigger><Button>批量治理（{documents.length}）</Button><Modal isDismissable={!working} className="modal"><Dialog className="dialog batch-dialog">{({ close }) => preview && submittedValues ? <div className="batch-confirm"><Heading slot="title">确认批量治理</Heading><p className="lifecycle-consequence">系统将在同一事务中更新 {preview.items.length} 份知识；任一版本或修订号已变化时，整批都不会写入。</p><div className="table-scroll"><table><thead><tr><th>知识</th><th>当前状态</th><th>应用后</th><th>复核日期</th></tr></thead><tbody>{preview.items.map(item => <tr key={item.documentId}><td>{item.title}<small>当前修订 {item.currentRevision}</small></td><td>{lifecycleLabels[item.currentStatus] ?? item.currentStatus}<small>{item.currentValidThrough ? `有效期至 ${item.currentValidThrough}` : "未设置失效日期"}</small></td><td>{lifecycleLabels[item.targetStatus] ?? item.targetStatus}<small>{item.changed ? "状态或日期将变化" : "状态与日期不变，将新增审计修订"}</small></td><td>{item.currentReviewAfter} → {item.targetReviewAfter}</td></tr>)}</tbody></table></div><p className="batch-reason"><strong>统一原因</strong>{submittedValues.reason}</p>{Boolean(localError) && <p role="alert" className="inline-error">{errorText(localError)}</p>}<div className="batch-dialog-actions"><button type="button" className="secondary" disabled={working} onClick={() => { setPreview(null); setLocalError(null); }}>返回修改</button><button type="button" className={submittedValues.status === "WITHDRAWN" ? "danger" : undefined} disabled={working || !preview.canSubmit} onClick={() => void apply(close)}>{working ? "正在原子提交…" : `确认应用到 ${preview.items.length} 份知识`}</button></div></div> : <form className="form-grid" onSubmit={form.handleSubmit(value => void loadPreview(value))}><Heading slot="title">批量治理 {documents.length} 份知识</Heading><p className="wide muted">先设置统一目标并预检。下一步会展示逐项差异，确认前不会写入。</p><label>目标状态<select {...form.register("status", { required: true })}><option value="WITHDRAWN">撤回，不再参与检索</option><option value="ACTIVE">恢复，重新参与检索</option></select></label><label>复核日期<input type="date" {...form.register("reviewAfter", { required: true })} /></label><label>失效日期<input type="date" {...form.register("validThrough")} /></label><label className="wide">统一原因<textarea placeholder="说明本次批量治理的依据、范围和责任判断。" {...form.register("reason", { required: true, minLength: 1, maxLength: 1000 })} /></label>{Boolean(localError) && <p role="alert" className="inline-error wide">{errorText(localError)}</p>}<Button className="secondary" isDisabled={working} onPress={close}>取消</Button><button type="submit" disabled={working}>{working ? "正在预检…" : "预检变更"}</button></form>}</Dialog></Modal></DialogTrigger>;
}
