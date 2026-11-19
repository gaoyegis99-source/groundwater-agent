import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import { Icon } from '../Icon';
import type { ResourceDetail } from '../types';
import { EmptyState, Loading } from '../ui/Feedback';

type JsonRead = { value: unknown; text: string } | { error: ApiError };

function jsonObject(value: unknown): value is Record<string, unknown> | unknown[] {
  return value !== null && typeof value === 'object';
}

const pageSize = 40;
const valuePreviewSize = 280;

function jsonPreview(value: unknown, at = 0) {
  if (Array.isArray(value)) return `[${value.length}]`;
  if (jsonObject(value)) return '{…}';
  if (typeof value !== 'string' || value.length <= valuePreviewSize) return JSON.stringify(value);
  const start = Math.max(0, at - 35);
  const part = value.slice(start, start + valuePreviewSize);
  return `${start ? '…' : ''}${JSON.stringify(part)}${start + part.length < value.length ? '…' : ''}`;
}

function* jsonFields(value: Record<string, unknown> | unknown[]): Generator<[string, unknown]> {
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) yield [String(i), value[i]];
  } else {
    for (const key of Object.keys(value)) yield [key, value[key]];
  }
}

function searchJson(value: unknown, query: string) {
  const stack: Iterator<[string, unknown]>[] = [[['$', value] as [string, unknown]][Symbol.iterator]()];
  const matches: string[] = [];
  const needle = query.toLowerCase();
  const limit = 60;
  while (stack.length > 0) {
    const next = stack.at(-1)!.next();
    if (next.done) { stack.pop(); continue; }
    const [label, item] = next.value;
    const at = jsonObject(item) ? -1 : String(item).toLowerCase().indexOf(needle);
    if (at >= 0 || label.toLowerCase().includes(needle)) {
      if (matches.length === limit) return { matches, truncated: true };
      matches.push(`${JSON.stringify(label)}: ${jsonPreview(item, at)}`);
    }
    if (jsonObject(item)) stack.push(jsonFields(item));
  }
  return { matches, truncated: false };
}

export function JsonViewer({ url }: { url: string }) {
  const [read, setRead] = useState<JsonRead | null>(null);
  const [query, setQuery] = useState('');
  const [retry, setRetry] = useState(0);
  const [clipboardFeedback, setClipboardFeedback] = useState('');
  const [manualCopy, setManualCopy] = useState<{ text: string }>();
  const copyField = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!manualCopy) return;
    copyField.current?.focus();
    copyField.current?.select();
  }, [manualCopy]);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new ApiError(response.status, 'RESOURCE_CONTENT_UNAVAILABLE');
      const text = await response.text();
      if (controller.signal.aborted) return;
      let value: unknown;
      try { value = JSON.parse(text); }
      catch { throw new ApiError(response.status, 'API_RESPONSE_INVALID'); }
      setRead({ value, text });
    }).catch(caught => {
      if (!controller.signal.aborted) setRead({ error: caught instanceof ApiError ? caught : new ApiError(0, 'NETWORK_UNCERTAIN') });
    });
    return () => controller.abort();
  }, [url, retry]);

  async function copy(value: string, confirmation: string) {
    setClipboardFeedback('');
    try {
      await navigator.clipboard.writeText(value);
      setManualCopy(undefined);
      setClipboardFeedback(confirmation);
    } catch {
      setManualCopy({ text: value });
      setClipboardFeedback('请复制下方已选中的文字。');
    }
  }

  const loaded = read !== null && 'value' in read;
  const search = useDeferredValue(query.trim());
  const shown = useMemo(() => loaded && search ? searchJson(read.value, search) : null, [read, search]);
  return <div className="json-workspace" aria-busy={read === null}>
    <div className="artifact-toolbar">
      <input type="search" aria-label="搜索 JSON" placeholder="搜索键或值…" disabled={!loaded}
        value={query} onChange={event => setQuery(event.target.value)} />
      <button type="button" disabled={!loaded} onClick={() => { if (loaded) void copy(read.text, '已复制 JSON。'); }}>复制 JSON</button>
    </div>
    <div className="json-feedback" role="status">{clipboardFeedback}</div>
    {manualCopy && <textarea className="manual-copy" ref={copyField} aria-label="待复制内容" readOnly value={manualCopy.text} />}
    {read === null ? <Loading>正在读取 JSON…</Loading>
      : 'error' in read ? <div className="workbench-read-error">
        <ErrorNotice title="无法读取 JSON" error={read.error} />
        <button type="button" onClick={() => { setRead(null); setRetry(value => value + 1); }}>重新读取 JSON</button>
      </div>
      : search ? shown?.matches.length ? <>
        {shown.truncated && <p className="muted" role="status">显示前 {shown.matches.length} 条匹配。可缩小关键词范围。</p>}
        <pre tabIndex={0} aria-label="JSON 搜索结果">{shown.matches.join('\n')}</pre>
      </> : <EmptyState description="没有匹配内容。请调整搜索关键词。" />
      : <div className="json-tree" role="region" aria-label="JSON 字段树" tabIndex={0}>
        <JsonNode value={read.value} path="$" label="$" depth={0} onCopy={copy} />
      </div>}
  </div>;
}

function JsonNode({ value, path, label, depth, onCopy }: {
  value: unknown; path: string; label: string; depth: number;
  onCopy: (text: string, confirmation: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(depth === 0);
  const [page, setPage] = useState(0);
  const copyPath = <button type="button" className="copy-path copy-path-icon" aria-label={`复制路径 ${path}`} title="复制路径"
    onClick={() => void onCopy(path, '已复制字段路径。')}><Icon name="copy" /></button>;
  if (jsonObject(value)) {
    const array = Array.isArray(value);
    const keys = array ? [] : Object.keys(value);
    const count = array ? value.length : keys.length;
    const first = page * pageSize;
    const end = Math.min(count, first + pageSize);
    const visible = open ? (array ? value.slice(first, end).map((_, index) => String(first + index)) : keys.slice(first, end)) : [];
    return <div className="json-branch" data-deep={depth >= 5 || undefined}>
      <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
        <summary>{label} <small>{array ? `[${count}]` : `{${count}}`}</small></summary>
        {open && <div className="json-children">
          {visible.map(key => <JsonNode key={key} value={array ? value[Number(key)] : value[key]} label={key}
            path={array ? `${path}[${key}]` : /^[A-Za-z_$][\w$]*$/.test(key) ? `${path}.${key}` : `${path}[${JSON.stringify(key)}]`}
            depth={depth + 1} onCopy={onCopy} />)}
          {count > pageSize && <div className="json-pagination" role="group" aria-label={`${path} 分页`}>
            <button type="button" aria-disabled={page === 0} onClick={() => { if (page > 0) setPage(page - 1); }}>上一页</button>
            <span aria-live="polite">{first + 1}–{end} / {count}</span>
            <button type="button" aria-disabled={end === count} onClick={() => { if (end < count) setPage(page + 1); }}>下一页</button>
          </div>}
        </div>}
      </details>
      {copyPath}
    </div>;
  }
  return <div className="json-leaf"><span><strong>{label}</strong>: <code>{jsonPreview(value)}</code></span><div className="json-leaf-actions">{copyPath}
    {typeof value === 'string' && value.length > valuePreviewSize && <button type="button" className="copy-path"
      onClick={() => void onCopy(value, '已复制完整字段值。')}>复制完整值</button>}
  </div></div>;
}

export function ImageViewer({ url, name, allowDownload = true }: { url: string; name: string; allowDownload?: boolean }) {
  const [zoom, setZoom] = useState(1);
  return <div className="artifact-image"><div className="artifact-toolbar"><label>缩放<input type="range" min="0.25" max="3" step="0.25" value={zoom} onChange={event => setZoom(Number(event.target.value))} /></label><button onClick={() => setZoom(1)}>适应窗口</button>{allowDownload && <a className="button secondary" href={url} download><Icon name="download" />下载</a>}</div><div className="image-viewport"><img style={{ transform: `scale(${zoom})` }} src={url} alt={name} /></div></div>;
}

export function MetadataViewer({ resource, object, url, reason, allowDownload = true }: {
  resource: ResourceDetail;
  object: ResourceDetail['objects'][number];
  url: string;
  reason?: string;
  allowDownload?: boolean;
}) {
  return <div className="artifact-unsupported"><p>{reason ?? '此格式暂不支持交互预览。'}</p><dl className="argument-list"><div><dt>格式</dt><dd>{object.format}</dd></div><div><dt>媒体类型</dt><dd>{object.mediaType}</dd></div><div><dt>大小</dt><dd>{object.size.toLocaleString()} 字节</dd></div><div><dt>校验和</dt><dd>{object.checksum}</dd></div>{resource.descriptor.provenance && Object.entries(resource.descriptor.provenance).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>{object.format === 'pytorch-state-dict' && <p className="muted">为避免执行不受信任代码，系统不会反序列化模型权重。</p>}{allowDownload && <a className="button secondary" href={url} download><Icon name="download" />下载原文件</a>}</div>;
}
