import { useEffect, useState } from 'react';
import { ApiError, callApi } from './api';
import type { components } from './generated/public-api-v12';
import type { ResourceDetail } from './types';
import { Badge, ErrorState, Loading, type BadgeTone } from './ui/Feedback';

type VersionIdentity = Pick<ResourceDetail, 'datasetId' | 'versionId' | 'semanticId'>;
type QualityRun = components['schemas']['data__QualityRunResponse'];
type QualityState =
  | { kind: 'loading' }
  | { kind: 'empty' }
  | { kind: 'more'; cursor: string }
  | { kind: 'error'; message: string }
  | { kind: 'loaded'; run: QualityRun };

const statusLabels = {
  PASSED: { label: '通过', tone: 'success' },
  PASSED_WITH_WARNINGS: { label: '通过（有警告）', tone: 'warning' },
  FAILED: { label: '未通过', tone: 'error' },
  ERROR: { label: '检查出错', tone: 'error' },
} satisfies Record<QualityRun['status'], { label: string; tone: BadgeTone }>;

async function readLatestRun(
  { datasetId, versionId, semanticId }: VersionIdentity, start: string | null, signal: AbortSignal,
): Promise<QualityState> {
  const path = `/data/datasets/${encodeURIComponent(datasetId)}/quality-runs`;
  const params = new URLSearchParams({ limit: '20' });
  const seenCursors = new Set<string>();
  let cursor = start;
  if (cursor) { params.set('cursor', cursor); seenCursors.add(cursor); }
  for (let page = 0; page < 2; page++) {
    signal.throwIfAborted();
    const { items, nextCursor } = await callApi('data_list_quality_runs', `${path}?${params}`, undefined, signal);
    // Data orders this feed by creation time and run ID, newest first.
    const run = items.find(item => item.datasetId === datasetId && item.versionId === versionId && item.semanticId === semanticId);
    if (run) return { kind: 'loaded', run };
    if (nextCursor === null) return { kind: 'empty' };
    if (seenCursors.has(nextCursor)) throw new ApiError(200, 'API_RESPONSE_INVALID');
    seenCursors.add(nextCursor);
    params.set('cursor', nextCursor);
    cursor = nextCursor;
  }
  return cursor ? { kind: 'more', cursor } : { kind: 'empty' };
}

function readErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if ([401, 403, 404].includes(error.status)) return '当前无法访问质量检查记录。';
    if (error.status === 0 || error.status >= 500) return '质量检查服务暂不可用。';
  }
  return '质量检查结果暂不可核验。';
}

function QualityRunDetails({ run }: { run: QualityRun }) {
  const status = statusLabels[run.status];
  return <>
    <div role="status">
      <Badge tone={status.tone} title={run.status}>{status.label}</Badge>
      <p className="muted">检查时间 · <time dateTime={run.createdAt} title={run.createdAt}>{new Date(run.createdAt).toLocaleString('zh-CN')}</time></p>
    </div>
    <details className="database-identity">
      <summary>运行与规则详情</summary>
      <dl className="argument-list">
        <div><dt>规则套件版本</dt><dd><code>{run.suiteVersionId}</code></dd></div>
        <div><dt>质量运行标识</dt><dd><code>{run.qualityRunId}</code></dd></div>
      </dl>
      {run.results.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="规则检查结果">
        <table>
          <thead><tr><th scope="col">规则标识</th><th scope="col">严重度</th><th scope="col">状态</th><th scope="col" data-column="number">违规数</th></tr></thead>
          <tbody>{run.results.map(result => <tr key={result.ruleId}>
            <th scope="row"><code>{result.ruleId}</code></th>
            <td>{result.severity}</td><td>{result.status}</td><td data-column="number">{result.violationCount}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="muted">此运行暂无规则结果。</p>}
    </details>
  </>;
}

export function DataVersionQuality({ version }: { version: VersionIdentity }) {
  const { datasetId, versionId, semanticId } = version;
  return <VersionQuality key={JSON.stringify([datasetId, versionId, semanticId])} version={version} />;
}

function VersionQuality({ version: { datasetId, versionId, semanticId } }: { version: VersionIdentity }) {
  const [state, setState] = useState<QualityState>({ kind: 'loading' });
  const [search, setSearch] = useState<{ cursor: string | null }>({ cursor: null });

  useEffect(() => {
    const controller = new AbortController();
    void readLatestRun({ datasetId, versionId, semanticId }, search.cursor, controller.signal).then(result => {
      if (!controller.signal.aborted) setState(result);
    }, error => {
      if (!controller.signal.aborted) setState({ kind: 'error', message: readErrorMessage(error) });
    });
    return () => controller.abort();
  }, [datasetId, versionId, semanticId, search]);

  function load(cursor: string | null) {
    setState({ kind: 'loading' });
    setSearch({ cursor });
  }

  return <section aria-label="质量检查">
    <h4>质量检查</h4>
    {state.kind === 'loading' && <Loading>正在读取质量检查…</Loading>}
    {state.kind === 'empty' && <div role="status">
      <Badge>待核验</Badge>
      <p className="muted">所选数据版本和语义版本暂无质量检查记录。</p>
    </div>}
    {state.kind === 'more' && <div>
      <p role="status"><Badge>待核验</Badge> 已读取记录未找到精确匹配。</p>
      <button type="button" onClick={() => load(state.cursor)}>查找更早的质量记录</button>
    </div>}
    {state.kind === 'error' && <ErrorState title="质量状态待核验" description={state.message}
      action={<button type="button" onClick={() => load(null)}>重新读取质量检查</button>} />}
    {state.kind === 'loaded' && <QualityRunDetails run={state.run} />}
  </section>;
}
