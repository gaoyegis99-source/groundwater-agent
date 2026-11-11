import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { ApiError, runDataQuery } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import type { DataQueryValue, ResourceDetail } from '../types';
import { toDataFilters, type TableFilter } from './query';

echarts.use([
  BarChart, LineChart, ScatterChart,
  GridComponent, LegendComponent, TooltipComponent, CanvasRenderer,
]);

type Field = { fieldId: string; name: string; type: string };
type ChartData = {
  groups: { name: string | null; points: DataQueryValue[][] }[];
  truncated: boolean;
  totalGroups: number;
};
export function ChartView({ resource, fields, filters }: {
  resource: ResourceDetail;
  fields: Field[];
  filters: TableFilter[];
}) {
  const numeric = fields.filter(field => /int|float|double|decimal/.test(field.type));
  const [x, setX] = useState(fields[0]?.fieldId ?? '');
  const [y, setY] = useState<string[]>([numeric[0]?.fieldId ?? fields[1]?.fieldId ?? ''].filter(Boolean));
  const [groupBy, setGroupBy] = useState('');
  const [kind, setKind] = useState<'line' | 'bar' | 'scatter'>('line');
  const host = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<ChartData | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!x || !y.length) {
      setData(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setData(null);
    setError(null);
    const selected = [...new Set([x, ...y, ...(groupBy ? [groupBy] : [])])];
    void runDataQuery({
      versionId: resource.versionId,
      semanticId: resource.semanticId,
      fields: selected,
      filters: toDataFilters(filters),
    }, 0, 1000, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      const result = value.result!;
      const indexes = Object.fromEntries(result.fields.map((field, index) => [field.fieldId, index]));
      const grouped = new Map<string, DataQueryValue[][]>();
      for (const row of result.rows) {
        const name = groupBy ? String(row[indexes[groupBy]] ?? '∅') : '';
        const points = grouped.get(name) ?? [];
        points.push([row[indexes[x]], ...y.map(field => row[indexes[field]])]);
        grouped.set(name, points);
      }
      const entries = [...grouped.entries()];
      setData({
        groups: entries.slice(0, 8).map(([name, points]) => ({ name: groupBy ? name : null, points })),
        totalGroups: entries.length,
        truncated: value.receipt!.truncated || entries.length > 8,
      });
      setLoading(false);
    }).catch(caught => {
      if (controller.signal.aborted) return;
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      setError(caught as ApiError);
      setLoading(false);
    });
    return () => controller.abort();
  }, [resource.versionId, resource.semanticId, x, JSON.stringify(y), groupBy, JSON.stringify(filters)]);

  useEffect(() => {
    if (!host.current || !data || data.groups.length === 0) return;
    const element = host.current;
    const instance = echarts.init(element);
    instance.setOption({
      tooltip: { trigger: 'axis' },
      legend: {},
      xAxis: { type: 'category' },
      yAxis: { type: 'value' },
      series: data.groups.flatMap(group => y.map((field, yIndex) => ({
        name: [group.name, fields.find(item => item.fieldId === field)?.name ?? field].filter(Boolean).join(' · '),
        type: kind,
        data: group.points.map(point => [point[0], point[yIndex + 1]]),
      }))),
    });
    const resize = () => {
      if (element.clientWidth > 0 && element.clientHeight > 0) instance.resize();
    };
    // Mobile panes can become visible without a window resize.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
    observer?.observe(element);
    window.addEventListener('resize', resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', resize);
      instance.dispose();
    };
  }, [data, kind, JSON.stringify(y)]);

  return <>
    <div className="artifact-toolbar">
      <label>横轴<select value={x} onChange={event => setX(event.target.value)}>
        {fields.map(field => <option key={field.fieldId} value={field.fieldId}>{field.name}</option>)}
      </select></label>
      <label>纵轴（最多 4 个）<select aria-label="纵轴字段" multiple value={y}
        onChange={event => setY([...event.target.selectedOptions].map(option => option.value).slice(0, 4))}>
        {numeric.map(field => <option key={field.fieldId} value={field.fieldId}>{field.name}</option>)}
      </select></label>
      <label>分组<select aria-label="分组字段" value={groupBy} onChange={event => setGroupBy(event.target.value)}>
        <option value="">不分组</option>
        {fields.map(field => <option key={field.fieldId} value={field.fieldId}>{field.name}</option>)}
      </select></label>
      <select aria-label="图表类型" value={kind} onChange={event => setKind(event.target.value as typeof kind)}>
        <option value="line">折线</option><option value="bar">柱状</option><option value="scatter">散点</option>
      </select>
    </div>
    {error && <ErrorNotice error={error} />}
    {loading && <p>正在准备图表…</p>}
    {data?.truncated && <p className="muted">图表仅显示查询返回的前 1,000 行和前 8 组，共读取 {data.totalGroups} 组。</p>}
    {data && data.groups.length === 0 ? <p>没有符合条件的图表数据。</p> : <div ref={host} className="artifact-chart" />}
  </>;
}
