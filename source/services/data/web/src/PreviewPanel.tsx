import { errorText, pretty } from "./api";
import type { RunAction, Version } from "./api";
import { SpatialPreviewProfile } from "./DataProfile";
import { usePreview } from "./use-preview";

const stateLabels: Record<string, string> = { QUEUED: "等待生成", RUNNING: "正在生成", SUCCEEDED: "预览完成", FAILED: "预览失败", CANCELLED: "已取消" };

export function PreviewPanel({ version, allowed, deadlineSeconds, run }: { version: Version; allowed: boolean; deadlineSeconds: number; run: RunAction }) {
  const preview = usePreview(version, deadlineSeconds);
  if (!allowed) return <section className="content-preview"><h4>内容预览</h4><p className="muted">当前身份没有创建预览的权限。</p></section>;
  if (preview.eligibleObjects.length === 0) return <section className="content-preview"><h4>内容预览</h4><p className="muted">当前版本没有可由 Preview 服务解析的 XLSX 或 GeoPackage 原始对象。可查询格式请前往“查询工作台”查看记录。</p></section>;
  const page = preview.result?.result as { fields?: Array<{ fieldId?: string; name?: string }>; rows?: unknown[][]; nextOffset?: number | null; metadata?: Record<string, unknown>; features?: unknown[]; truncated?: boolean } | null;
  return (
    <section className="content-preview" aria-labelledby="content-preview-title">
      <div className="section-heading"><div><h4 id="content-preview-title">内容预览</h4><p>从受控服务读取前 100 行，不需要下载原文件。</p></div>{preview.result && <span className={`state-badge state-${preview.result.state.toLowerCase()}`}>{stateLabels[preview.result.state] ?? preview.result.state}</span>}</div>
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
        {page?.fields && page.rows && <><div className="table-scroll"><table><thead><tr>{page.fields.map((field, index) => <th key={field.fieldId ?? index}>{field.name ?? field.fieldId ?? `字段 ${index + 1}`}</th>)}</tr></thead><tbody>{page.rows.map((row, rowIndex) => <tr key={preview.offset + rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null ? "∅" : String(cell)}</td>)}</tr>)}</tbody></table></div><div className="pagination"><button className="secondary" disabled={preview.offset === 0} onClick={() => preview.setOffset(Math.max(0, preview.offset - 100))}>上一页</button><span>第 {Math.floor(preview.offset / 100) + 1} 页</span><button className="secondary" disabled={page.nextOffset == null} onClick={() => preview.setOffset(page.nextOffset!)}>下一页</button></div></>}
        {preview.result.receipt && <details><summary>预览回执与技术详情</summary><pre>{pretty(preview.result.receipt)}</pre></details>}
      </div>}
    </section>
  );
}
