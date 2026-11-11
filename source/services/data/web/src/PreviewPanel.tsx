import { errorText, pretty } from "./api";
import type { RunAction, Version } from "./api";
import { SpatialPreviewProfile } from "./DataProfile";
import { usePreview } from "./use-preview";
import { TableContentPreview } from "./TableContentPreview";

const stateLabels: Record<string, string> = { QUEUED: "等待生成", RUNNING: "正在生成", SUCCEEDED: "预览完成", FAILED: "预览失败", CANCELLED: "已取消" };

export function PreviewPanel({ version, allowed, queryAllowed = false, deadlineSeconds, queryDeadlineSeconds = deadlineSeconds, run }: { version: Version; allowed: boolean; queryAllowed?: boolean; deadlineSeconds: number; queryDeadlineSeconds?: number; run: RunAction }) {
  const preview = usePreview(version, deadlineSeconds);
  if (version.descriptor.policy === "FILE_QUERYABLE") return queryAllowed
    ? <TableContentPreview key={`${version.versionId}:${version.semanticId}`} version={version} deadlineSeconds={queryDeadlineSeconds} />
    : <section className="content-preview"><h4>数据内容</h4><p className="muted">当前身份没有读取表格内容的权限。</p></section>;
  if (!allowed) return <section className="content-preview"><h4>内容预览</h4><p className="muted">当前身份没有创建预览的权限。</p></section>;
  if (preview.eligibleObjects.length === 0) return <section className="content-preview"><h4>数据内容</h4><p className="muted">此格式暂不支持在线预览，可下载原文件。</p></section>;
  const page = preview.result?.result as { fields?: Array<{ fieldId?: string; name?: string }>; rows?: unknown[][]; nextOffset?: number | null; metadata?: Record<string, unknown>; features?: unknown[]; truncated?: boolean } | null;
  return (
    <section className="content-preview" aria-labelledby="content-preview-title">
      <div className="section-heading"><h4 id="content-preview-title">数据内容</h4><span className="muted">表格预览 · 每页 100 行</span></div>
      <div className="preview-controls">
        <label>预览对象<select value={preview.objectId} onChange={event => preview.setObjectId(event.target.value)}>{preview.eligibleObjects.map(object => <option key={object.objectId} value={object.objectId}>{object.format?.toUpperCase()} · {(object.size / 1024).toFixed(1)} KiB</option>)}</select></label>
        <label>{preview.selectedObject?.format === "xlsx" ? "工作表（可选）" : "图层（可选）"}<input value={preview.selector} placeholder="留空时由服务选择默认项" onChange={event => preview.setSelector(event.target.value)} /></label>
        <button disabled={preview.busy} onClick={() => void preview.create()}>{preview.busy ? "正在提交…" : "生成预览"}</button>
      </div>
      {preview.error && <p role="alert" className="inline-error">{errorText(preview.error)}</p>}
      {preview.result && <div className="preview-result">
        <p className="preview-state" role="status">{stateLabels[preview.result.state] ?? preview.result.state}</p>
        {["QUEUED", "RUNNING"].includes(preview.result.state) && <button className="secondary" onClick={() => void run(preview.cancel)}>取消预览</button>}
        {preview.result.error && <p className="inline-error">{String((preview.result.error as { code?: unknown }).code ?? "PREVIEW_FAILED")}</p>}
        {page?.metadata && <><SpatialPreviewProfile metadata={page.metadata} truncated={Boolean(page.truncated)} /><details className="preview-technical"><summary>预览技术元数据</summary><dl className="preview-metadata">{Object.entries(page.metadata).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></details></>}
        {page?.fields && page.rows && <><div className="table-scroll" role="region" tabIndex={0} aria-label="数据预览表格"><table><thead><tr>{page.fields.map((field, index) => <th key={field.fieldId ?? index}>{field.name ?? field.fieldId ?? `字段 ${index + 1}`}</th>)}</tr></thead><tbody>{page.rows.map((row, rowIndex) => <tr key={preview.offset + rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null ? "∅" : String(cell)}</td>)}</tr>)}</tbody></table></div><div className="pagination"><button className="secondary" disabled={preview.offset === 0} onClick={() => preview.setOffset(Math.max(0, preview.offset - 100))}>上一页</button><span>第 {Math.floor(preview.offset / 100) + 1} 页</span><button className="secondary" disabled={page.nextOffset == null} onClick={() => preview.setOffset(page.nextOffset!)}>下一页</button></div></>}
        {preview.result.receipt && <details><summary>预览回执与技术详情</summary><pre>{pretty(preview.result.receipt)}</pre></details>}
      </div>}
    </section>
  );
}
