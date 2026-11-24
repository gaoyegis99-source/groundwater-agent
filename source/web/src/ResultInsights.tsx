import { lazy, Suspense, useEffect, useState } from 'react';
import { ApiError, contentUrl, readDataVersion, runDataQuery } from './api';
import { StatisticsSummary } from './StatisticsSummary';
import { outputName } from './card-presentation';
import type { ResultOverview } from './product-ui';
import type { ResourceDetail, ResourceReference } from './types';
import type { RasterCategory } from './viewers/RasterViewer';

const RasterViewer = lazy(() => import('./viewers/RasterViewer').then(module => ({ default: module.RasterViewer })));
const CalibrationChart = lazy(() => import('./viewers/CalibrationChart'));
const number = new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 5 });
const integer = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
const resolution = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });

export function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function usePublishedJson(project: string, output: ResourceReference | undefined, maxBytes = 262_144, retry = 0) {
  const url = output?.format === 'json' && output.sizeBytes > 0 && output.sizeBytes <= maxBytes
    ? contentUrl(project, output.versionId, output.objectId) : null;
  const checksum = output?.checksum;
  const [loaded, setLoaded] = useState<{ url: string; checksum?: string; value: unknown }>();
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    const save = (value: unknown) => { if (!controller.signal.aborted) setLoaded({ url, checksum, value }); };
    void fetch(url, { signal: controller.signal }).then(response => response.ok ? response.json() : null).then(save).catch(() => save(null));
    return () => controller.abort();
  }, [url, checksum, retry]);
  if (!url) return output ? null : undefined;
  return loaded?.url === url && loaded.checksum === checksum ? loaded.value : undefined;
}

function fact(label: string, value: unknown, unit = '') {
  if (typeof value === 'number' && Number.isFinite(value)) return { label, value: [number.format(value), unit].filter(Boolean).join(' ') };
  if (typeof value === 'string' && value.trim()) return { label, value };
  return { label, value: '未提供' };
}

const classColors = ['#156a84', '#368880', '#969149', '#ca702c', '#b44127', '#862f46'];

function useFlowEvidence(snapshot?: ResourceReference, diagnostics?: ResourceReference, raster?: ResourceReference) {
  const key = snapshot || diagnostics || raster ? JSON.stringify([snapshot, diagnostics, raster]) : '';
  const [loaded, setLoaded] = useState<{ key: string; descriptors: (ResourceDetail['descriptor'] | undefined)[] }>();
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const references: (ResourceReference | null)[] = JSON.parse(key);
    async function descriptor(reference: ResourceReference | null) {
      if (!reference) return;
      try {
        return (await readDataVersion(reference.versionId, controller.signal, reference)).descriptor;
      } catch (error) {
        if (controller.signal.aborted || error instanceof ApiError) return;
        throw error;
      }
    }
    void Promise.all(references.map(descriptor)).then(descriptors => {
      if (!controller.signal.aborted) setLoaded({ key, descriptors });
    });
    return () => controller.abort();
  }, [key]);
  const [active, total, head] = loaded?.key === key ? loaded.descriptors : [];
  const selectedCount = active?.rowCount;
  const participating = typeof selectedCount === 'number' && Number.isSafeInteger(selectedCount) && selectedCount >= 0
    ? selectedCount : undefined;
  const diagnosticCount = total?.rowCount;
  const excluded = participating !== undefined
    && typeof diagnosticCount === 'number' && Number.isSafeInteger(diagnosticCount) && diagnosticCount >= participating
    ? diagnosticCount - participating : undefined;
  const transform = record(record(head?.raster)?.grid)?.transform;
  if (!Array.isArray(transform) || transform.length !== 6
      || !transform.every(value => typeof value === 'number' && Number.isFinite(value))) return { participating, excluded };
  const width = Math.hypot(transform[1], transform[4]);
  const height = Math.hypot(transform[2], transform[5]);
  return {
    participating,
    excluded,
    actualResolution: width > 0 && height > 0 ? `${resolution.format(width)} × ${resolution.format(height)} m` : undefined,
  };
}

export function ResultInsights({ overview, summary, trend, visuals, project, runArguments, snapshot, snapshotDiagnostics, headRaster }: {
  overview: NonNullable<ResultOverview['insight']>;
  summary?: ResourceReference;
  trend?: ResourceReference;
  visuals: ResourceReference[];
  project: string;
  runArguments?: Record<string, unknown>;
  snapshot?: ResourceReference;
  snapshotDiagnostics?: ResourceReference;
  headRaster?: ResourceReference;
}) {
  const [retry, setRetry] = useState(0);
  const payload = usePublishedJson(project, summary, undefined, retry);
  const value = record(payload);
  const data = overview.schemaVersion && value?.schemaVersion !== overview.schemaVersion ? null : value;
  const flow = overview.kind === 'darcy';
  const flowEvidence = useFlowEvidence(flow ? snapshot : undefined, flow ? snapshotDiagnostics : undefined, flow ? headRaster : undefined);
  const trendKey = trend ? JSON.stringify(trend) : null;
  const [trendCounts, setTrendCounts] = useState<{ key: string; counts: number[] | null }>();
  useEffect(() => {
    if (!trendKey) return;
    const controller = new AbortController();
    const ref: ResourceReference = JSON.parse(trendKey);
    async function read() {
      const detail = await readDataVersion(ref.versionId, controller.signal, ref);
      const fields = detail.descriptor.fields ?? [];
      const field = (name: string) => {
        const matches = fields.filter(item => item.name === name);
        if (matches.length !== 1) throw new ApiError(422, 'TREND_FIELD_UNAVAILABLE');
        return matches[0].fieldId;
      };
      const evaluated = { field: field('status'), op: 'eq' as const, value: 'EVALUATED' };
      const significant = { field: field('significant'), op: 'eq' as const, value: true };
      const slope = field('sen_slope_per_day');
      const count = async (filters: NonNullable<Parameters<typeof runDataQuery>[0]['filters']>) => {
        const response = await runDataQuery({
          versionId: ref.versionId, semanticId: ref.semanticId, filters,
          aggregates: [{ function: 'count', alias: 'count' }],
        }, 0, 1, controller.signal);
        const value = response.result?.rows[0]?.[0];
        if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0 || response.receipt?.truncated) {
          throw new ApiError(422, 'TREND_COUNT_UNAVAILABLE');
        }
        return value;
      };
      return Promise.all([
        count([evaluated]),
        count([evaluated, significant, { field: slope, op: 'gt', value: 0 }]),
        count([evaluated, significant, { field: slope, op: 'lt', value: 0 }]),
      ]);
    }
    void read().then(counts => {
      if (!controller.signal.aborted) setTrendCounts({ key: trendKey, counts });
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (!(error instanceof ApiError)) throw error;
      setTrendCounts({ key: trendKey, counts: null });
    });
    return () => controller.abort();
  }, [trendKey]);
  const [selected, setSelected] = useState('');
  const visual = visuals.find(item => item.outputName === selected) ?? visuals[0];
  const showingClasses = !!overview.categoricalVisual && visual?.outputName === overview.categoricalVisual;
  const facts: { label: string; value: string }[] = [];
  const categories: RasterCategory[] = [];
  const calibration = overview.kind === 'calibration' && data ? {
    rows: Array.isArray(data.rows) ? data.rows : [],
    diagnostics: record(data.diagnostics) ?? data,
  } : null;
  let note = '';
  if (overview.kind === 'trend' && data) {
    facts.push(fact('显著性阈值', data.alpha));
    const counts = trendCounts?.key === trendKey ? trendCounts?.counts : undefined;
    if (counts) {
      const [evaluated, increasing, decreasing] = counts;
      facts.push(fact('可检验井数', evaluated), fact('显著上升井数', increasing), fact('显著下降井数', decreasing));
    }
    note = counts === null ? '暂时无法核对逐井计数。可查看趋势明细。' : '方向按取值字段的 Sen 斜率计算。';
  }
  if (overview.kind === 'map' && data) {
    facts.push(fact('源坐标系', data.crs), fact('范围', Array.isArray(data.extent) && data.extent.length === 4
      && data.extent.every(item => typeof item === 'number' && Number.isFinite(item)) ? data.extent.map(item => number.format(item)).join(' · ') : undefined));
  }
  if (overview.kind === 'darcy' && data?.resultKind === 'MODEL_DERIVED_NOT_OBSERVED') {
    if (runArguments?.targetTime) facts.push(fact('目标时点', runArguments.targetTime));
    facts.push(fact('参与插值井数', flowEvidence.participating === undefined ? undefined : integer.format(flowEvidence.participating)),
      fact('被排除井数', flowEvidence.excluded === undefined ? undefined : integer.format(flowEvidence.excluded)),
      fact('目标像元大小', runArguments?.pixelSize, 'm'), fact('实际分辨率（X × Y）', flowEvidence.actualResolution),
      fact('流速有效网格数', data.validCellCount), fact('水力传导系数', data.hydraulicConductivityMetersPerDay, 'm/d'),
      fact('有效孔隙度', data.effectivePorosity));
    note = '流速来自二维稳态模型推导。方向从网格北顺时针计量，指向水头降低的方向。';
  }
  if (overview.kind === 'kriging' && data) {
    const loo = record(data.loo);
    const variogram = record(data.variogram);
    facts.push(fact('留一验证 RMSE', loo?.rmse, typeof data.valueUnit === 'string' ? data.valueUnit : ''),
      fact('参与验证点数', loo?.evaluatedCount), fact('未验证点数', loo?.notEvaluatedCount),
      fact('变异函数', variogram?.model), fact('方差单位', record(data.variance)?.unit));
    note = '方差来自插值模型。未验证点的原因见留一法明细。';
  }
  if (overview.kind === 'drastic' && data) {
    const classes = Array.isArray(data.classes) ? data.classes.map(record).filter(item => item !== null) : [];
    for (const [index, item] of classes.entries()) {
      if (typeof item.code !== 'number' || !Number.isFinite(item.code) || typeof item.label !== 'string') continue;
      categories.push({ value: item.code, color: classColors[index % classColors.length], label: item.label });
      facts.push(fact(item.label, typeof item.ratioOfClassificationValid === 'number' ? item.ratioOfClassificationValid * 100 : null, '%'));
    }
    const counts = record(data.counts);
    facts.push(fact('有效分类网格', counts?.classificationValidCellCount), fact('无数据网格', counts?.outputNoDataCellCount),
      fact('评分规则未覆盖网格', counts?.ratingUncoveredCellCount), fact('评分配置', record(data.config)?.title));
    note = '等级比例按有效分类网格计算，采用本次输入的评分规则与权重。';
  }
  if (calibration) {
    facts.push(fact('观测数', calibration.diagnostics.observations), fact('加权目标函数', calibration.diagnostics.objective));
    if (!calibration.rows.length) facts.push(fact('率定后 RMSE', calibration.diagnostics.rootMeanSquareResidual));
    note = calibration.rows.length ? '残差按观测水头减模拟水头计算。参数倍率见率定参数成果。'
      : '此成果提供汇总统计。率定图表需要逐条观测水头和模拟水头。';
  }
  if (overview.kind === 'statistics' && data) return <StatisticsSummary key={summary?.objectId} data={data} />;
  return <div className="workflow-insights">
    {summary && payload === undefined && <p role="status">正在读取成果摘要…</p>}
    {summary && payload === null && <p role="status">成果摘要暂时无法读取。<button type="button" onClick={() => setRetry(value => value + 1)}>重试读取摘要</button></p>}
    {facts.length > 0 ? <div className="result-metrics">
      {note && <p>{note}</p>}<dl>{facts.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
    </div> : payload != null && <p className="muted">这份成果未提供可核对的关键指标。</p>}
    {calibration && calibration.rows.length > 0 && summary && <Suspense fallback={<p>正在加载率定图表…</p>}>
      <CalibrationChart rows={calibration.rows} source={summary} rmse={calibration.diagnostics.rootMeanSquareResidual} />
    </Suspense>}
    {visual && <section aria-label="成果地图" className="result-map-preview">
      {visuals.length > 1 && <label>查看地图<select value={visual.outputName} onChange={event => setSelected(event.target.value)}>
        {visuals.map(item => <option key={item.versionId} value={item.outputName}>{outputName(item.outputName ?? '')}</option>)}
      </select></label>}
      {showingClasses && !categories.length
        ? <p className="muted">{payload === undefined ? '正在核对等级图例…' : '这份成果未提供可用的等级图例。'}</p>
        : <Suspense fallback={<p>正在加载成果地图…</p>}><RasterViewer key={visual.objectId}
          url={contentUrl(project, visual.versionId, visual.objectId)} categories={showingClasses ? categories : undefined} /></Suspense>}
    </section>}
  </div>;
}
