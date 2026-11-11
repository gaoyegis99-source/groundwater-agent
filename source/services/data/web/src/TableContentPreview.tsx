import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { newRequestId } from "@platform/ui-boundary";
import { api, errorText, PlatformUiError, pretty, type QueryResult, type Version } from "./api";
import { retryDelay, retryable } from "./polling";

const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export function TableContentPreview({ version, deadlineSeconds }: { version: Version; deadlineSeconds: number }) {
  const [requestId, setRequestId] = useState(newRequestId);
  const [offset, setOffset] = useState(0);
  const [actionError, setActionError] = useState<PlatformUiError | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const identity = [version.project, version.versionId, version.semanticId];
  const creation = useQuery({
    queryKey: ["data-content-operation", ...identity, requestId],
    queryFn: async ({ signal }) => {
      const accepted = await api("data_create_query", "queries", {
        idempotencyKey: requestId, kind: "table", versionIds: [version.versionId],
        semanticIds: { [version.versionId]: version.semanticId }, fields: [],
      }, { signal });
      return { ...accepted, startedAt: Date.now() };
    },
    retry: retryable, retryDelay, staleTime: Infinity,
  });
  const queryId = creation.data?.queryId;
  const expired = () => Boolean(creation.data && Date.now() - creation.data.startedAt > deadlineSeconds * 1000);
  const server = useQuery<QueryResult, PlatformUiError>({
    queryKey: ["data-content-page", ...identity, queryId, offset],
    enabled: Boolean(queryId),
    queryFn: async ({ signal }) => {
      const result = await api("data_get_query", `queries/${encodeURIComponent(queryId!)}?offset=${offset}&limit=100`, undefined, { signal });
      if (result.queryId !== queryId) throw new PlatformUiError(0, "API_RESPONSE_INVALID");
      return result;
    },
    retry: retryable, retryDelay,
    refetchInterval: query => !query.state.data || terminal.has(query.state.data.state) || expired() ? false : 1500,
  });
  const result = server.data;
  const error = actionError ?? creation.error ?? server.error;
  const page = result?.result;
  const waiting = Boolean(queryId && !terminal.has(result?.state ?? ""));
  const deadlineStopped = waiting && expired();
  const fieldName = (fieldId: string) => version.descriptor.fields.find(field => field.fieldId === fieldId)?.name ?? fieldId;
  const restart = () => { setActionError(null); setOffset(0); setRequestId(newRequestId()); };
  const cancel = async () => {
    if (!queryId || cancelling) return;
    setCancelling(true);
    setActionError(null);
    try {
      await api("data_cancel_query", `queries/${encodeURIComponent(queryId)}/cancel`, {});
      await server.refetch();
    } catch (caught) {
      setActionError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "CONTENT_CANCEL_FAILED"));
    } finally {
      setCancelling(false);
    }
  };
  return <section className="content-preview table-content" aria-labelledby="content-preview-title">
    <div className="section-heading"><h4 id="content-preview-title">数据内容</h4><span className="muted">每页 100 行</span></div>
    {(creation.isPending || server.isPending || waiting) && !error && !deadlineStopped && <p role="status" className="preview-state">正在读取数据内容…</p>}
    {error && <div className="read-feedback"><p role="alert" className="inline-error">{errorText(error)}</p><button className="secondary" disabled={creation.isFetching || server.isFetching} onClick={() => { setActionError(null); void (creation.error ? creation.refetch() : server.refetch()); }}>重试读取内容</button></div>}
    {deadlineStopped && <div className="read-feedback"><p role="status">已停止自动检查，可手动读取结果。</p><button className="secondary" disabled={server.isFetching} onClick={() => void server.refetch()}>检查读取结果</button></div>}
    {waiting && <button className="link-button" disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "正在取消…" : "取消读取"}</button>}
    {(result?.state === "FAILED" || result?.state === "CANCELLED" || (result?.state === "SUCCEEDED" && !result.resultAvailable)) && <div className="read-feedback"><p role="alert" className="inline-error">{result.state === "FAILED" ? "内容读取失败。" : result.state === "CANCELLED" ? "已取消读取。" : "内容结果已过期。"}</p><button className="secondary" onClick={restart}>重新读取内容</button></div>}
    {page && <>
      {page.rows.length === 0 ? <p className="muted">此版本没有数据行。</p> : <div className="table-scroll" role="region" tabIndex={0} aria-label="数据内容表格"><table><thead><tr>{page.fields.map((field, index) => { const id = field.fieldId ?? field.id ?? ""; return <th key={id || index}>{id ? fieldName(id) : `字段 ${index + 1}`}</th>; })}</tr></thead><tbody>{page.rows.map((row, rowIndex) => <tr key={offset + rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null ? "∅" : String(cell)}</td>)}</tr>)}</tbody></table></div>}
      <div className="pagination"><button className="secondary" disabled={offset === 0 || server.isFetching} onClick={() => setOffset(Math.max(0, offset - 100))}>上一页</button><span>第 {Math.floor(offset / 100) + 1} 页</span><button className="secondary" disabled={page.nextOffset == null || server.isFetching} onClick={() => setOffset(page.nextOffset!)}>下一页</button></div>
      {page.truncated && <p className="muted">结果已截断，范围见读取回执。</p>}
    </>}
    {queryId && <details className="preview-technical"><summary>读取回执</summary><code>{queryId}</code>{result?.receipt && <pre>{pretty(result.receipt)}</pre>}{result?.error && <pre>{pretty(result.error)}</pre>}</details>}
  </section>;
}
