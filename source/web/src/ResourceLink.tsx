import { useEffect, useState } from 'react';
import { ApiError, callApi } from './api';
import { decodeResourceDetail } from './api-contract';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import type { ResourceDetail } from './types';

export function ResourceLink({ id, project, onResource }: { id: string; project: string; onResource: (id: string) => void }) {
  const [resource, setResource] = useState<ResourceDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setResource(null); setError(null);
    void callApi('data_get_version', `/data/versions/${encodeURIComponent(id)}`, undefined, controller.signal, decodeResourceDetail)
      .then(setResource).catch(caught => {
        if (caught instanceof DOMException && caught.name === 'AbortError') return;
        if (!(caught instanceof ApiError)) throw caught;
        setError(caught);
      });
    return () => controller.abort();
  }, [id, project]);
  return <>
    <button className="text-button" onClick={() => onResource(id)}>{resource
      ? <>{resource.title} · {resource.versionId}<Icon name="arrow-up-right" /></>
      : error ? '数据名称暂不可用 · 打开详情' : '正在读取数据名称…'}</button>
    {error && <ErrorNotice error={error} />}
  </>;
}
