import { useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { sizeLabel } from './format';
import { abortUpload, checksumInWorker, uploadFile, uploadOutcomeUncertain } from './upload';
import type { ResourceItem } from './types';

const formats: Record<string, [string, string, string]> = {
  csv: ['csv', 'text/csv', 'CSV 表格'], json: ['json', 'application/json', 'JSON 数据'], geojson: ['geojson', 'application/geo+json', 'GeoJSON 地理数据'],
  parquet: ['parquet', 'application/vnd.apache.parquet', 'Parquet 表格'], arrow: ['arrow-ipc-file', 'application/vnd.apache.arrow.file', 'Arrow 表格'],
  tif: ['geotiff', 'image/tiff', 'GeoTIFF 栅格'], tiff: ['geotiff', 'image/tiff', 'GeoTIFF 栅格'], png: ['png', 'image/png', 'PNG 图片'],
  xlsx: ['xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'Excel 工作簿'], gpkg: ['geopackage', 'application/geopackage+sqlite3', 'GeoPackage 地理数据'],
};
export function UploadDialog({ file, project, storageScope = '', onClose, onUploaded, maxBytes = 1024 ** 3 }: {
  file: File; project: string; storageScope?: string; onClose: () => void; onUploaded: (resource: ResourceItem) => void; maxBytes?: number;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    nameRef.current?.focus();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  const extension = file.name.lastIndexOf('.') > 0 ? file.name.slice(file.name.lastIndexOf('.') + 1).toLowerCase() : '';
  const initial = formats[extension];
  const [format, setFormat] = useState(initial?.[0] ?? '');
  const [mediaType, setMediaType] = useState(initial?.[1] ?? file.type);
  const [name, setName] = useState(file.name);
  const [description, setDescription] = useState('');
  const [phase, setPhase] = useState<'idle' | 'hash' | 'upload' | 'verify'>('idle');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const publicationRef = useRef<string | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const busy = phase !== 'idle';
  useEffect(() => { if (busy) dialogRef.current?.focus(); }, [busy]);
  async function submit() {
    setError(null);
    if (file.size > maxBytes) {
      setError(`单个文件最大 ${sizeLabel(maxBytes)}。更大文件请联系项目管理员。`); return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setPhase('hash'); setProgress(0);
      const checksum = await checksumInWorker(file, processed => setProgress(file.size ? processed / file.size : 1), controller.signal);
      setPhase('upload'); setProgress(0);
      const result = await uploadFile(file, {
        projectId: project, name, description: description || null, tags: [], effectiveDate: null,
        mediaType, format, sizeBytes: file.size, checksum,
      }, storageScope, (loaded, total) => { setProgress(loaded / total); if (loaded === total) setPhase('verify'); }, controller.signal, (publicationId, idempotencyKey) => {
        publicationRef.current = publicationId;
        idempotencyKeyRef.current = idempotencyKey;
      });
      onUploaded(result);
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      setError(caught); setUncertain(uploadOutcomeUncertain(caught)); setPhase('idle');
    } finally {
      abortRef.current = null;
    }
  }
  async function cancel() {
    abortRef.current?.abort();
    let confirmed = publicationRef.current === null;
    try { await abortUpload(publicationRef.current, idempotencyKeyRef.current, storageScope); confirmed = true; }
    catch { /* A later status lookup remains authoritative if abort could not be confirmed. */ }
    publicationRef.current = null;
    idempotencyKeyRef.current = null;
    setPhase('idle');
    setUncertain(!confirmed);
    setError(new ApiError(0, confirmed ? 'UPLOAD_CANCELLED' : 'UPLOAD_UNCERTAIN'));
  }
  return <div className="modal-shade"><section ref={dialogRef} tabIndex={-1} className="upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title" onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }
    if (event.key !== 'Tab') return;
    const elements = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary')].filter(element => element.getClientRects().length > 0);
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && (event.target === first || event.target === event.currentTarget)) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && event.target === last) { event.preventDefault(); first?.focus(); }
  }}>
    <div className="section-heading"><h2 id="upload-title">添加资料</h2><button aria-label="关闭上传" disabled={busy} onClick={onClose}><Icon name="close" /></button></div>
    <div className="file-summary"><Icon name="upload" /><strong>{file.name}</strong><span>{sizeLabel(file.size)}</span></div>
    <p className="limitations">上传资料对本项目的授权成员可见。禁止上传个人资料、未脱敏资料或单文件超过 {sizeLabel(maxBytes)} 的内容。</p>
    <label>资料名称<input ref={nameRef} value={name} onChange={e => setName(e.target.value)} disabled={busy || uncertain} /></label>
    <label>说明 <span className="muted">· 可选</span><textarea value={description} onChange={e => setDescription(e.target.value)} disabled={busy || uncertain} placeholder="简述文件内容" /></label>
    {!initial && <p className="limitations">无法从文件名确定格式。请根据文件来源说明填写文件格式和媒体类型；填写完整后才能上传。</p>}
    <details open={!initial}><summary>{initial && format === initial[0] && mediaType === initial[1] ? `${initial[2]} · 高级设置` : '文件格式与高级信息'}</summary><div className="field-row"><label>文件格式<input value={format} onChange={e => setFormat(e.target.value)} disabled={busy || uncertain} placeholder="例如 csv、geojson" /></label>
      <label>媒体类型<input value={mediaType} onChange={e => setMediaType(e.target.value)} disabled={busy || uncertain} placeholder="例如 text/csv" /></label></div><p className="muted">媒体类型是文件的标准类型名称，例如 CSV 对应 text/csv。无法确定时，请向文件提供方核对。</p><p className="muted">所有格式统一限制为 {sizeLabel(maxBytes)}。</p></details>
    {busy && <div role="status" data-testid="upload-progress"><div className="progress-label"><span>{phase === 'hash' ? '检查文件完整性' : phase === 'verify' ? '文件已传送，等待校验和保存' : '正在上传文件'}</span>{phase !== 'verify' && <span>{Math.round(progress * 100)}%</span>}</div>{phase === 'verify' ? <progress aria-label="正在校验和保存" /> : <progress aria-label={phase === 'hash' ? '文件检查进度' : '文件上传进度'} value={progress} max={1} />}</div>}
    {error && <ErrorNotice error={error} title={uncertain ? '上传结果尚未确认' : '文件尚未上传成功'} />}
    {uncertain && <p className="limitations" role="status">请保留当前文件并重新选择同一文件。系统会先查询已记录的 publication 状态；只有确认仍可继续时才会重新传送，避免产生重复版本。</p>}
    <div className="upload-actions">{busy && <button type="button" onClick={() => void cancel()}>取消上传</button>}<button className="primary wide" disabled={busy || uncertain || !format.trim() || !mediaType.trim() || !name.trim()} onClick={() => void submit()}>上传并附加</button></div>
  </section></div>;
}
