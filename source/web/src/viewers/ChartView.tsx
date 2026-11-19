import { useEffect, useId, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { ApiError, runDataQuery } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import type { DataQueryValue, ResourceDetail } from '../types';
import type { DataFilters } from './query';
import { fieldUnit, fieldWithUnit, scientificCell } from './scientific-display';
import { observeChartSize, scientificChartTheme } from './chart-presentation';
import { EmptyState, Loading } from '../ui/Feedback';

echarts.use([
  BarChart, LineChart, ScatterChart,
  GridComponent, LegendComponent, TooltipComponent, CanvasRenderer,
]);

type Field = { fieldId: string; name: string; type: string };
type AxisKind = 'category' | 'value' | 'time';
type TimeBasis = 'date' | 'wall' | 'utc';
type ChartPoint = {
  x: string | number;
  originalX: DataQueryValue;
  timeBasis: TimeBasis | null;
  values: (number | null)[];
};
type ChartGroup = { label: string | null; points: ChartPoint[] };
type ChartData = {
  groups: ChartGroup[];
  totalGroups: number;
  skipped: number;
  truncated: boolean;
  returnedRows: number;
};

function axisKind(type: string): AxisKind {
  if (/^(?:date(?:32|64)?|timestamp)\b/i.test(type)) return 'time';
  if (/^(?:u?int(?:8|16|32|64)?|float(?:16|32|64)?|double|decimal(?:128|256)?)\b/i.test(type)) return 'value';
  return 'category';
}

function numericValue(value: DataQueryValue | undefined): number | null {
  const number = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? number : null;
}

function horizontalValue(value: DataQueryValue | undefined, kind: AxisKind) {
  if (value == null) return null;
  if (kind === 'category') return { x: scientificCell(value), timeBasis: null };
  if (kind === 'value') {
    const number = numericValue(value);
    return number == null ? null : { x: number, timeBasis: null };
  }
  if (typeof value !== 'string') return null;
  const match = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?)?$/i.exec(value.trim());
  if (!match) return null;
  const [, date, time, zone] = match;
  const midnight = Date.parse(`${date}T00:00:00Z`);
  if (!Number.isFinite(midnight) || !new Date(midnight).toISOString().startsWith(date)) return null;
  const stamp = time ? Date.parse(`${date}T${time}${zone ?? 'Z'}`) : midnight;
  const timeBasis: TimeBasis = zone ? 'utc' : time ? 'wall' : 'date';
  return Number.isFinite(stamp) ? { x: stamp, timeBasis } : null;
}

function chartTime(value: number, basis: TimeBasis) {
  const iso = new Date(value).toISOString();
  return basis === 'date' ? iso.slice(0, 10) : basis === 'wall' ? iso.replace('T', ' ').slice(0, -1) : iso;
}

export function ChartView({ resource, fields, filters }: {
  resource: ResourceDetail;
  fields: Field[];
  filters: DataFilters;
}) {
  const numeric = fields.filter(field => axisKind(field.type) === 'value');
  const [x, setX] = useState(fields[0]?.fieldId ?? '');
  const [y, setY] = useState<string[]>([numeric[0]?.fieldId ?? ''].filter(Boolean));
  const [groupBy, setGroupBy] = useState('');
  const [kind, setKind] = useState<'line' | 'bar' | 'scatter'>('line');
  const [showSample, setShowSample] = useState(false);
  const [retry, setRetry] = useState(0);
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.EChartsType | null>(null);
  const captionId = useId();
  const xAxisKind = axisKind(fields.find(field => field.fieldId === x)?.type ?? '');
  const yKey = JSON.stringify(y);
  const filtersKey = JSON.stringify(filters);
  const selectionKey = JSON.stringify([resource.versionId, resource.semanticId, x, xAxisKind, yKey, groupBy, filtersKey, retry]);
  const [read, setRead] = useState<{ key: string; data: ChartData } | { key: string; error: ApiError }>();
  const current = read?.key === selectionKey ? read : null;
  const data = current && 'data' in current ? current.data : null;

  useEffect(() => {
    const yFields = JSON.parse(yKey) as string[];
    if (data || !x || !yFields.length) return;
    const controller = new AbortController();
    let finished = false;
    const selected = [...new Set([x, ...yFields, ...(groupBy ? [groupBy] : [])])];
    void runDataQuery({
      versionId: resource.versionId,
      semanticId: resource.semanticId,
      fields: selected,
      filters: JSON.parse(filtersKey) as DataFilters,
      sort: [{ field: x, direction: 'asc' }],
    }, 0, 1000, controller.signal).then(value => {
      finished = true;
      if (controller.signal.aborted) return;
      const result = value.result!;
      const indexes = Object.fromEntries(result.fields.map((field, index) => [field.fieldId, index]));
      const grouped = new Map<string, ChartGroup>();
      let skipped = 0;
      for (const row of result.rows) {
        const originalX = row[indexes[x]];
        const horizontal = horizontalValue(originalX, xAxisKind);
        if (!horizontal) {
          skipped += 1;
          continue;
        }
        const groupValue = groupBy ? row[indexes[groupBy]] : null;
        const key = JSON.stringify(groupValue ?? null);
        const group = grouped.get(key) ?? { label: groupBy ? scientificCell(groupValue) : null, points: [] };
        group.points.push({ ...horizontal, originalX, values: yFields.map(field => numericValue(row[indexes[field]])) });
        grouped.set(key, group);
      }
      const groups = [...grouped.values()];
      setRead({ key: selectionKey, data: {
        groups: groups.slice(0, 8), totalGroups: groups.length, skipped,
        returnedRows: result.rows.length,
        truncated: Boolean(value.receipt?.truncated || result.truncated || result.nextOffset != null),
      } });
    }).catch(caught => {
      finished = true;
      if (!controller.signal.aborted) setRead({ key: selectionKey, error: caught as ApiError });
    });
    return () => { if (!finished) controller.abort(); };
  }, [resource.versionId, resource.semanticId, selectionKey, x, xAxisKind, yKey, groupBy, filtersKey, data]);

  const points = useMemo(() => data?.groups.flatMap(group => group.points) ?? [], [data]);
  const timeBases = useMemo(() => new Set(points.flatMap(point => point.timeBasis ? [point.timeBasis] : [])), [points]);
  const mixedTime = timeBases.has('utc') && (timeBases.has('wall') || timeBases.has('date'));
  const timeBasis = timeBases.has('utc') ? 'utc' : timeBases.has('wall') ? 'wall' : 'date';
  const timeNote = timeBasis === 'utc' ? 'UTC' : timeBasis === 'wall' ? '记录时间，保留原时钟读数' : '日历日期';
  const timeRange = xAxisKind === 'time' && points.length > 0 && !mixedTime
    ? [Math.min(...points.map(point => Number(point.x))), Math.max(...points.map(point => Number(point.x)))] : null;
  const label = (id: string) => fields.find(field => field.fieldId === id)?.name ?? id;
  const xPlotLabel = fieldWithUnit(label(x), fieldUnit(resource, x));
  const xLabel = xAxisKind === 'time' ? `${label(x)}（${timeNote}）` : xPlotLabel;
  const fieldLabel = (id: string) => fieldWithUnit(label(id), fieldUnit(resource, id));
  const yAxes = new Map<string, { title: string; fields: string[] }>();
  for (const id of y) {
    const unit = fieldUnit(resource, id);
    const key = unit ? `unit:${unit}` : `field:${id}`;
    const axis = yAxes.get(key);
    if (axis) axis.fields.push(id);
    else yAxes.set(key, { title: fieldWithUnit(label(id), unit), fields: [id] });
  }
  const axes = [...yAxes.values()].map(axis => ({
    ...axis,
    title: axis.fields.length > 1 ? `数值（${fieldUnit(resource, axis.fields[0])}）` : axis.title,
  }));
  const chartReady = points.some(point => point.values.some(value => value !== null)) && !mixedTime;
  const showLegend = !!data && (data.groups.length * y.length > 1 || Boolean(data.groups[0]?.label));

  useEffect(() => {
    if (!host.current || !chartReady) return;
    const element = host.current;
    const instance = echarts.init(element);
    chart.current = instance;
    const stopObserving = observeChartSize(element, () => instance.resize());
    return () => {
      stopObserving?.();
      chart.current = null;
      instance.dispose();
    };
  }, [chartReady]);

  // Option construction is bounded to four fields and the query sample.
  useEffect(() => {
    if (!data || !chart.current || !host.current) return;
    const theme = scientificChartTheme(host.current);
    const range = xAxisKind !== 'category' && points.length > 0 ? {
      min: Math.min(...points.map(point => Number(point.x))),
      max: Math.max(...points.map(point => Number(point.x))),
    } : null;
    const grids = (compact: boolean) => axes.map((_, index) => ({
      left: compact ? 50 : 62, right: compact ? 16 : 24,
      top: (showLegend ? 68 : 42) + index * 225, height: 140,
    }));
    const ticks = (compact: boolean) => axes.map(() => ({ splitNumber: compact ? 3 : 6 }));
    chart.current.setOption({ baseOption: {
        animation: false,
        useUTC: true,
        color: theme.palette,
        textStyle: theme.textStyle,
        tooltip: { ...theme.tooltip, trigger: kind === 'scatter' ? 'item' : 'axis', renderMode: 'richText', confine: true },
        legend: { show: showLegend, type: 'scroll', top: 12, left: 14, right: 14, itemWidth: 14, itemHeight: 8, textStyle: theme.textStyle },
        grid: grids(false),
        xAxis: axes.map((_, index) => ({
          ...theme.axis,
          gridIndex: index, type: xAxisKind, name: xPlotLabel, nameLocation: 'middle', nameGap: 30,
          nameTextStyle: { ...theme.textStyle, overflow: 'truncate', width: 190 },
          ...(xAxisKind === 'time' ? { axisLabel: { ...theme.axis.axisLabel, formatter: { day: '{M}/{d}' } } } : {}),
          ...(range && range.min < range.max ? range : {}),
        })),
        yAxis: axes.map((axis, index) => ({
          ...theme.axis, splitLine: theme.splitLine,
          gridIndex: index, type: 'value', name: axis.title,
          nameTextStyle: { ...theme.textStyle, overflow: 'truncate', width: 140, align: 'left' },
          scale: kind !== 'bar',
        })),
        series: axes.flatMap((axis, index) => data.groups.flatMap((group, groupIndex) => axis.fields.map(field => ({
          name: [group.label && `${groupIndex + 1} · ${group.label}`, fieldLabel(field)].filter(Boolean).join(' · '),
          type: kind,
          xAxisIndex: index,
          yAxisIndex: index,
          connectNulls: false,
          symbol: ['circle', 'rect', 'diamond', 'triangle'][groupIndex % 4],
          symbolSize: kind === 'scatter' ? 7 : 5,
          ...(kind === 'line' ? { lineStyle: { width: 2.5, type: ['solid', 'dashed', 'dotted'][groupIndex % 3] } } : {}),
          ...(kind === 'bar' ? { barMaxWidth: 32 } : {}),
          data: group.points.map(point => [point.x, point.values[y.indexOf(field)]]),
        })))),
    }, media: [
      { query: { maxWidth: 420 }, option: { grid: grids(true), xAxis: ticks(true) } },
      { option: { grid: grids(false), xAxis: ticks(false) } },
    ] }, { notMerge: true });
  }, [data, fields, kind, resource.semantics.fields, showLegend, x, xAxisKind, y]);

  return <section className="scientific-chart" aria-label="图表预览">
    <div className="artifact-toolbar scientific-chart-controls">
      <label>横轴<select value={x} onChange={event => setX(event.target.value)}>
        {fields.map(field => <option key={field.fieldId} value={field.fieldId}>{fieldLabel(field.fieldId)}</option>)}
      </select></label>
      <label>纵轴（最多 4 个）<select aria-label="纵轴字段" multiple value={y}
        onChange={event => setY([...event.target.selectedOptions].map(option => option.value).slice(0, 4))}>
        {numeric.map(field => <option key={field.fieldId} value={field.fieldId}>{fieldLabel(field.fieldId)}</option>)}
      </select></label>
      <label>分组<select aria-label="分组字段" value={groupBy} onChange={event => setGroupBy(event.target.value)}>
        <option value="">不分组</option>
        {fields.map(field => <option key={field.fieldId} value={field.fieldId}>{field.name}</option>)}
      </select></label>
      <label>图表类型<select aria-label="图表类型" value={kind} onChange={event => setKind(event.target.value as typeof kind)}>
        <option value="line">折线</option><option value="bar">柱状</option><option value="scatter">散点</option>
      </select></label>
    </div>
    {current && 'error' in current && <div className="workbench-read-error">
      <ErrorNotice title="图表读取失败" error={current.error} />
      <button type="button" onClick={() => setRetry(value => value + 1)}>重新读取图表</button>
    </div>}
    {x && y.length > 0 && !current && <Loading>正在准备图表…</Loading>}
    {(!x || y.length === 0) && <EmptyState description={numeric.length ? '请选择横轴和至少一个数值纵轴字段。' : '该数据没有可用作纵轴的数值字段。'} />}
    {data && <figure className="scientific-figure">
      <figcaption id={captionId}>
        <dl className="scientific-axes">
          <div><dt>横轴</dt><dd>{xLabel}{xAxisKind === 'value' && !fieldUnit(resource, x) ? ' · 单位未声明' : ''}</dd></div>
          <div><dt>纵轴</dt><dd>{y.map(id => fieldUnit(resource, id) ? fieldLabel(id) : `${label(id)}（单位未声明）`).join('、')}</dd></div>
        </dl>
        {timeRange && <p><strong>样本时间范围</strong> {chartTime(timeRange[0], timeBasis)} 至 {chartTime(timeRange[1], timeBasis)}</p>}
        <p className="muted">查询返回 {data.returnedRows.toLocaleString()} 行{data.truncated && '（查询结果有截断）'}。
          {data.totalGroups > 8 && ` 返回 ${data.totalGroups} 组，显示前 8 组。`}
          {data.skipped > 0 && ` ${data.skipped} 行横轴值缺测或无法解析，未纳入图表。`}
          {' '}纵轴缺测留空，零值保留。
        </p>
      </figcaption>
      {mixedTime ? <p role="status">样本同时包含带时区和不带时区的时间。请在样本表中核对原始时间。</p>
        : chartReady ? <div ref={host} className="artifact-chart" role="img" aria-label="数据图表" aria-describedby={captionId}
          style={{ height: axes.length * 225 + (showLegend ? 45 : 19) }} /> : <EmptyState description="没有符合条件的图表数据。" />}
      {points.length > 0 && <details open={showSample} onToggle={event => setShowSample(event.currentTarget.open)}>
        <summary>查看图表样本（{points.length.toLocaleString()} 行）</summary>
        {showSample && <div className="table-scroll scientific-sample" role="region" aria-label="图表样本" tabIndex={0}>
          <table>
            <thead><tr>{groupBy && <th scope="col">{label(groupBy)}</th>}<th scope="col" data-column={xAxisKind === 'value' ? 'number' : xAxisKind === 'time' ? 'time' : undefined}>{label(x)}</th>
              {y.map(id => <th key={id} scope="col" data-column="number">{fieldLabel(id)}</th>)}</tr></thead>
            <tbody>{data.groups.flatMap((group, groupIndex) => group.points.map((point, index) =>
              <tr key={`${groupIndex}/${index}`}>
                {groupBy && <td>{group.label}</td>}<td data-column={xAxisKind === 'value' ? 'number' : xAxisKind === 'time' ? 'time' : undefined}>{scientificCell(point.originalX)}</td>
                {point.values.map((value, column) => <td key={y[column]} data-column="number">{scientificCell(value)}</td>)}
              </tr>))}
            </tbody>
          </table>
        </div>}
      </details>}
    </figure>}
  </section>;
}
