import { useEffect, useId, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { ApiError, readDataVersion } from '../api';
import type { ResourceDetail, ResourceReference } from '../types';
import { fieldUnit, fieldWithUnit, scientificCell } from './scientific-display';
import { observeChartSize, scientificChartTheme } from './chart-presentation';

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, CanvasRenderer]);

const columns = [
  { key: 'time', name: '模型时间' },
  { key: 'observed', name: '观测水头' },
  { key: 'simulated', name: '模拟水头' },
  { key: 'residual', name: '残差' },
] as const;

type Column = (typeof columns)[number]['key'];

function finiteReading(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function publishedUnit(resource: ResourceDetail | null, name: Column) {
  if (!resource) return null;
  const matches = resource.descriptor.fields?.filter(field => field.name === name || field.name === `rows.${name}`) ?? [];
  if (matches.length > 1) return null;
  return fieldUnit(resource, matches[0]?.fieldId ?? `rows.${name}`) ?? fieldUnit(resource, name);
}

export default function CalibrationChart({ rows, source, rmse }: { rows: readonly unknown[]; source: ResourceReference; rmse: unknown }) {
  const [residual, setResidual] = useState(false);
  const [showRows, setShowRows] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.EChartsType | null>(null);
  const captionId = useId();
  const sourceKey = JSON.stringify(source);
  const [loaded, setLoaded] = useState<{ key: string; resource: ResourceDetail | null }>();
  const metadata = loaded?.key === sourceKey ? loaded : undefined;
  useEffect(() => {
    const ref: ResourceReference = JSON.parse(sourceKey);
    const controller = new AbortController();
    void readDataVersion(ref.versionId, controller.signal, ref).then(resource => {
      if (!controller.signal.aborted) setLoaded({ key: sourceKey, resource });
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (!(error instanceof ApiError)) throw error;
      setLoaded({ key: sourceKey, resource: null });
    });
    return () => controller.abort();
  }, [sourceKey]);

  const records = useMemo(() => rows.map(row => {
    const value = row && typeof row === 'object' ? row as Record<string, unknown> : {};
    return {
      time: finiteReading(value.time),
      observed: finiteReading(value.observed),
      simulated: finiteReading(value.simulated),
      residual: finiteReading(value.residual),
    };
  }), [rows]);
  const timeRange = useMemo(() => {
    const times = records.flatMap(row => row.time == null ? [] : [row.time]);
    return times.length ? [Math.min(...times), Math.max(...times)] : null;
  }, [records]);
  const x = residual ? columns[2] : columns[1];
  const y = residual ? columns[3] : columns[2];
  const xUnit = publishedUnit(metadata?.resource ?? null, x.key);
  const yUnit = publishedUnit(metadata?.resource ?? null, y.key);
  const timeUnit = publishedUnit(metadata?.resource ?? null, 'time');
  const rmseValue = finiteReading(rmse);
  const residualUnit = publishedUnit(metadata?.resource ?? null, 'residual');
  const xLabel = fieldWithUnit(x.name, xUnit ?? '单位未声明');
  const yLabel = fieldWithUnit(y.name, yUnit ?? '单位未声明');
  const sameScale = !residual && !!xUnit && xUnit === yUnit;
  const points = useMemo(() => records.flatMap(row => {
    const a = row[x.key], b = row[y.key];
    return a != null && b != null ? [[a, b]] : [];
  }), [records, x.key, y.key]);
  const chartReady = Boolean(metadata && points.length > 0);

  useEffect(() => {
    const element = host.current;
    if (!chartReady || !element) return;
    const chart = echarts.init(element);
    chartRef.current = chart;
    const stopObserving = observeChartSize(element, () => chart.resize());
    return () => { stopObserving?.(); chartRef.current = null; chart.dispose(); };
  }, [chartReady]);

  useEffect(() => {
    if (!chartRef.current || !host.current || !points.length) return;
    const theme = scientificChartTheme(host.current);
    const limits = points.reduce(([lo, hi], point) => {
      const values = sameScale ? point : [point[0]];
      return [Math.min(lo, ...values), Math.max(hi, ...values)];
    }, [Infinity, -Infinity]);
    const [lo, hi] = limits[0] === limits[1] ? [limits[0] - 1, limits[1] + 1] : limits;
    const axis = {
      ...theme.axis, splitLine: theme.splitLine,
      type: 'value', nameLocation: 'middle', scale: true,
      nameTextStyle: { ...theme.textStyle, overflow: 'truncate', width: 190 },
    } as const;
    chartRef.current.setOption({
      animation: false,
      grid: { top: 26, bottom: 58, left: 70, right: 22 },
      textStyle: theme.textStyle,
      color: theme.palette,
      tooltip: { ...theme.tooltip, trigger: 'item', confine: true, renderMode: 'richText' },
      xAxis: { ...axis, name: xLabel, nameGap: 32, min: lo, max: hi },
      yAxis: { ...axis, name: yLabel, nameGap: 52, ...(sameScale ? { min: lo, max: hi } : {}) },
      series: [
        { type: 'scatter', name: '率定记录', symbolSize: 7, data: points },
        ...(residual || sameScale ? [{ type: 'line', symbol: 'none', silent: true, lineStyle: { type: 'dashed', color: theme.reference },
          data: [[lo, residual ? 0 : lo], [hi, residual ? 0 : hi]] }] : []),
      ],
    }, { notMerge: true });
  }, [points, xLabel, yLabel, residual, sameScale, chartReady]);

  return <section aria-label="率定图表" className="result-map-preview">
    <label>查看图表<select value={residual ? 'residual' : 'fit'} onChange={event => setResidual(event.target.value === 'residual')}>
      <option value="fit">观测水头与模拟水头</option><option value="residual">模拟水头与残差</option>
    </select></label>
    <div id={captionId} className="calibration-caption">
      <p>横轴 {xLabel} · 纵轴 {yLabel}</p>
      <p>率定残差来源 · 版本 <code>{source.versionId}</code> · 对象 <code>{source.objectId}</code></p>
      {rmseValue != null && <p>率定后 RMSE {rmseValue}（{residualUnit ?? '单位未声明'}）</p>}
      <p>{timeRange ? `模型时间范围 ${timeRange[0]} 至 ${timeRange[1]}（${timeUnit ?? '单位未声明'}）` : '成果未提供模型时间范围'}</p>
      <p>图中 {points.length} 条记录{points.length < records.length && ` · ${records.length - points.length} 条轴值缺测，见数据表`}</p>
    </div>
    {!metadata && <p role="status">正在核对率定数据的字段单位…</p>}
    {metadata && !metadata.resource && <p className="muted">暂时无法核对率定数据的字段单位。</p>}
    {chartReady ? <div className="result-calibration-chart" ref={host} role="img" aria-label={`${x.name}与${y.name}`} aria-describedby={captionId} />
      : metadata && <p>所选坐标轴没有成对读数。</p>}
    {records.length > 0 && <details open={showRows} onToggle={event => setShowRows(event.currentTarget.open)}>
      <summary>查看率定数据表（{records.length} 行）</summary>
      {showRows && <div className="table-scroll scientific-sample" tabIndex={0} role="region" aria-label="率定数据表">
        <table>
          <caption>缺测表示空值，0 表示实际零值。</caption>
          <thead><tr>{columns.map(column => <th scope="col" key={column.key} data-column="number">{fieldWithUnit(column.name, publishedUnit(metadata?.resource ?? null, column.key) ?? '单位未声明')}</th>)}</tr></thead>
          <tbody>{records.map((row, index) => <tr key={index}>
            {columns.map(column => <td key={column.key} data-column="number">{scientificCell(row[column.key])}</td>)}
          </tr>)}</tbody>
        </table>
      </div>}
    </details>}
  </section>;
}
