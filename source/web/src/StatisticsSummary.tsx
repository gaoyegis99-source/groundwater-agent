import { useState } from 'react';

type Statistics = { count: number; nullCount: number; nonFiniteCount: number; q1: number | null; median: number | null; q3: number | null };
type Row = { label: string; stats: Statistics };
const number = new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 7 });
const object = (value: unknown): Record<string, unknown> | undefined =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;

function statistics(value: unknown): Statistics | null {
  const data = object(value);
  if (!data || !['count', 'nullCount', 'nonFiniteCount'].every(key => Number.isSafeInteger(data[key]) && Number(data[key]) >= 0)
    || !['q1', 'median', 'q3'].every(key => data.count === 0 ? data[key] === null : Number.isFinite(data[key]))) return null;
  return data as Statistics;
}

function readSeries(data: Record<string, unknown>) {
  const selection = object(data.selection);
  if (!selection || !Array.isArray(data.series) || !data.series.length) return null;
  const groupField = typeof selection.groupField === 'string' ? selection.groupField : '';
  const unit = typeof selection.unit === 'string' ? selection.unit : '';
  const rows: Row[] = [];
  let total = 0;
  for (const raw of data.series) {
    const item = object(raw);
    const stats = statistics(item?.statistics);
    const field = object(item?.field)?.name;
    if (!stats || typeof field !== 'string' || !Array.isArray(item?.groups)) return null;
    total += stats.count;
    if (!groupField) rows.push({ label: field, stats });
    else for (const group of item.groups) {
      const key = object(object(group)?.key);
      const subset = statistics(object(group)?.statistics);
      if (!key || !subset || !(key.value === null || ['string', 'number', 'boolean'].includes(typeof key.value))) return null;
      rows.push({ label: key.value === null ? '（空值）' : String(key.value), stats: subset });
    }
  }
  return { rows, total, groupField, unit, column: typeof selection.column === 'string' ? selection.column : '' };
}

export function StatisticsSummary({ data }: { data: Record<string, unknown> }) {
  const [full, setFull] = useState(false);
  const summary = readSeries(data);
  if (!summary) return <p className="muted">统计明细格式无法识别，可打开原成果核对。</p>;
  const { rows, total, groupField, unit, column } = summary;
  const format = (value: number | null) => value === null ? '—' : number.format(value);
  return <section className="statistics-summary" aria-label="描述统计结果">
    <p><strong>{column || '所选数据'}{unit && `（${unit}）`}</strong>共 {total} 个有效值{groupField && `，按 ${groupField} 分为 ${rows.length} 组`}。</p>
    <div className="table-scroll" role="region" tabIndex={0} aria-label="统计分布表，可横向滚动">
      <table><caption>{groupField ? '分组统计' : '总体统计'}{unit && `（${unit}）`}</caption>
        <thead><tr><th scope="col">{groupField || '统计字段'}</th><th scope="col">有效数</th>
          {full && <th scope="col">P25</th>}<th scope="col">中位数</th>{full && <><th scope="col">P75</th><th scope="col">空值</th><th scope="col">非有限值</th></>}</tr></thead>
        <tbody>{(full ? rows : rows.slice(0, 5)).map(({ label, stats }, index) => <tr key={index}><th scope="row">{label}</th>
          <td>{stats.count}</td>{full && <td>{format(stats.q1)}</td>}<td>{format(stats.median)}</td>
          {full && <><td>{format(stats.q3)}</td><td>{stats.nullCount}</td><td>{stats.nonFiniteCount}</td></>}</tr>)}</tbody>
      </table>
    </div>
    {!full && rows.length > 5 && <small>已显示 5 / {rows.length} {groupField ? '组' : '列'}</small>}
    <button type="button" className="text-button" aria-expanded={full} onClick={() => setFull(!full)}>{full ? '收起统计明细' : '查看完整统计明细'}</button>
  </section>;
}
