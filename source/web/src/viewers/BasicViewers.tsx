import { useEffect, useState } from 'react';
import { ApiError } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import { Icon } from '../Icon';
import type { ResourceDetail } from '../types';

export function JsonViewer({ url }: { url: string }) {
  const [value, setValue] = useState<unknown>();
  const [query, setQuery] = useState('');
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new ApiError(response.status, 'RESOURCE_CONTENT_UNAVAILABLE');
      setValue(await response.json());
    }).catch(caught => {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(caught instanceof ApiError ? caught : new ApiError(0, 'NETWORK_UNCERTAIN'));
    });
    return () => controller.abort();
  }, [url]);
  const text = value === undefined ? '' : JSON.stringify(value, null, 2);
  const shown = query ? text.split('\n').filter(line => line.toLowerCase().includes(query.toLowerCase())).join('\n') : text;
  return <div className="json-workspace">{error && <ErrorNotice error={error} />}<div className="artifact-toolbar"><input aria-label="搜索 JSON" placeholder="搜索键或值" value={query} onChange={event => setQuery(event.target.value)} /><button onClick={() => void navigator.clipboard.writeText(text)}>复制 JSON</button></div>{query ? <pre>{shown || '没有匹配内容'}</pre> : value === undefined ? <p>正在读取…</p> : <div className="json-tree"><JsonNode value={value} path="$" label="$" /></div>}</div>;
}

function JsonNode({ value, path, label }: { value: unknown; path: string; label: string }) {
  if (value !== null && typeof value === 'object') return <details open={path === '$'}><summary><button className="copy-path" title="复制字段路径" onClick={event => { event.preventDefault(); void navigator.clipboard.writeText(path); }}>{label}</button> <small>{Array.isArray(value) ? `[${value.length}]` : `{${Object.keys(value).length}}`}</small></summary><div>{Object.entries(value).map(([key, child]) => <JsonNode key={key} value={child} label={key} path={Array.isArray(value) ? `${path}[${key}]` : `${path}.${key}`} />)}</div></details>;
  return <div className="json-leaf"><button className="copy-path" title="复制字段路径" onClick={() => void navigator.clipboard.writeText(path)}>{label}</button>: <code>{JSON.stringify(value)}</code></div>;
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
