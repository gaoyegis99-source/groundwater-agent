import { useEffect, useRef, useState } from 'react';
import { ApiError, cancelDataPreview, getDataPreview, startDataPreview, waitForDataPreview } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import { Icon } from '../Icon';
import type { DataPreviewResponse, ResourceDetail } from '../types';

export function XlsxPreviewViewer({ resource, object, url, allowDownload = true }: { resource: ResourceDetail; object: ResourceDetail['objects'][number]; url: string; allowDownload?: boolean }) {
  const [sheet, setSheet] = useState('');
  const [previewId, setPreviewId] = useState('');
  const [response, setResponse] = useState<DataPreviewResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const pageRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let operation = '';
    let settled = false;
    pageRequest.current?.abort();
    setLoading(true); setError(null); setResponse(null); setOffset(0);
    void startDataPreview({ kind: 'xlsx_table', versionId: resource.versionId, semanticId: resource.semanticId, objectId: object.objectId, ...(sheet ? { sheetName: sheet } : {}) }, controller.signal)
      .then(async accepted => {
        operation = accepted.previewId; setPreviewId(operation);
        const result = await waitForDataPreview(operation, 0, 100, controller.signal);
        settled = true; setResponse(result); setLoading(false);
      }).catch(caught => {
        if (caught instanceof DOMException && caught.name === 'AbortError') return;
        setError(caught as ApiError); setLoading(false);
      });
    return () => { controller.abort(); pageRequest.current?.abort(); if (operation && !settled) void cancelDataPreview(operation).catch(() => undefined); };
  }, [resource.versionId, resource.semanticId, object.objectId, sheet]);
  async function page(next: number) {
    pageRequest.current?.abort();
    const controller = new AbortController();
    pageRequest.current = controller;
    setLoading(true); setError(null);
    try { setResponse(await getDataPreview(previewId, next, 100, controller.signal)); setOffset(next); }
    catch (caught) { if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(caught as ApiError); }
    finally { if (pageRequest.current === controller) { pageRequest.current = null; setLoading(false); } }
  }
  const result = response?.result;
  const sheets = (result?.metadata.sheets as { name: string; state: string; maxRow: number; maxColumn: number }[] | undefined) ?? [];
  const selected = String(result?.metadata.selectedSheet ?? sheet);
  return <div className="table-workspace preview-operation">{error && <><ErrorNotice error={error} title="工作簿预览未完成" />{allowDownload && <a className="button secondary" href={url} download><Icon name="download" />下载原文件</a>}</>}{loading && !result && <p>正在安全读取工作簿…</p>}{result && <><div className="artifact-toolbar"><label>工作表<select aria-label="工作表" value={selected} onChange={event => setSheet(event.target.value)}>{sheets.map(item => <option key={item.name} value={item.name}>{item.name}{item.state !== 'visible' ? '（隐藏）' : ''}</option>)}</select></label><span>{String(result.metadata.effectiveRange ?? '')} · 共 {String(result.metadata.sourceRows ?? 0)} 行</span>{loading && <span>正在更新…</span>}</div>{result.warnings.map(item => <p className="limitations" role="status" key={item}>{item}</p>)}<PreviewTable result={result} offset={offset} /><div className="artifact-toolbar"><button disabled={offset === 0 || loading} onClick={() => void page(Math.max(0, offset - 100))}>上一页</button><button disabled={result.nextOffset === null || loading} onClick={() => void page(result.nextOffset!)}>下一页</button></div></>}</div>;
}

function PreviewTable({ result, offset }: { result: NonNullable<DataPreviewResponse['result']>; offset: number }) {
  if (!result.rows.length) return <p>所选工作表没有可显示的数据。</p>;
  return <div className="table-scroll"><table><thead><tr>{result.fields.map(field => <th key={field.fieldId}>{field.name}<small>{field.type}</small></th>)}</tr></thead><tbody>{result.rows.map((row, index) => <tr key={offset + index}>{result.fields.map((field, column) => <td key={field.fieldId}>{String(row[column] ?? '∅')}</td>)}</tr>)}</tbody></table></div>;
}
