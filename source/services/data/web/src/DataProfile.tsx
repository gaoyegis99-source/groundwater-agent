import { formatFieldProfileValue } from "@platform/ui-boundary";
import type { FieldDescriptor, Version } from "./api";

function percentage(numerator: number, denominator: number): string {
  if (denominator <= 0) return "—";
  const value = numerator / denominator * 100;
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

function valueLabel(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function countLabel(count: number, total: number): string {
  return `${count} / ${total} (${percentage(count, total)})`;
}

function NumericStringEvidence({ field }: { field: FieldDescriptor }) {
  const evidence = field.profile?.numericString;
  if (!evidence || evidence.parsedCount === 0) return null;
  const ratio = `${evidence.parsedCount} / ${evidence.checkedCount}`;
  if (evidence.identifierLike) {
    return <p className="profile-hint neutral">{ratio} 可解析为数字（{percentage(evidence.parsedCount, evidence.checkedCount)}）；值包含前导零，可能是编号；保留原字符串，不自动改型。</p>;
  }
  if (evidence.suggestedType) {
    return <p className="profile-hint">{ratio} 可解析为数字（{percentage(evidence.parsedCount, evidence.checkedCount)}），可考虑转换为 <code>{evidence.suggestedType}</code>；系统不会自动转换。</p>;
  }
  return <p className="profile-hint neutral">仅 {ratio} 可解析为数字（{percentage(evidence.parsedCount, evidence.checkedCount)}），不建议整列转换。</p>;
}

function FieldProfileItem({ field, version }: { field: FieldDescriptor; version: Version }) {
  const profile = field.profile;
  const semantic = version.semantics.fields?.[field.fieldId];
  const rowCount = version.descriptor.rowCount;
  const nonNull = profile?.nonNullCount ?? (rowCount === null ? null : rowCount - field.nullCount);
  return <li className="field-profile-card" data-testid={`field-profile-${field.fieldId}`}>
    <div className="field-profile-heading">
      <div><strong>{field.name}</strong>{semantic?.meaning && <span>{semantic.meaning}</span>}</div>
      <div><span className="type-chip">{field.type}</span>{semantic?.unit && <span className="unit-chip">单位 {semantic.unit}</span>}</div>
    </div>
    <details className="inline-technical"><summary>技术标识</summary><code>{field.fieldId}</code></details>
    <dl className="field-profile-facts">
      <div><dt>非空值</dt><dd>{nonNull === null ? "未统计" : rowCount ? countLabel(nonNull, rowCount) : nonNull}</dd></div>
      <div><dt>空值</dt><dd>{rowCount ? countLabel(field.nullCount, rowCount) : field.nullCount}</dd></div>
      {profile?.valueRange && <div><dt>范围</dt><dd>{formatFieldProfileValue(profile.valueRange.minimum, field.type, profile.exactIntegers?.minimum)} – {formatFieldProfileValue(profile.valueRange.maximum, field.type, profile.exactIntegers?.maximum)}</dd></div>}
      {profile?.temporal && <div><dt>日期解析</dt><dd>{countLabel(profile.temporal.parsedCount, profile.temporal.checkedCount)}</dd></div>}
      {profile?.temporal?.kind === "timestamp" && <div><dt>物理时区</dt><dd>{profile.temporal.physicalTimezone || "未声明"}</dd></div>}
      {profile?.temporal && <div><dt>业务时区</dt><dd>{version.semantics.timezone || "未声明"}</dd></div>}
      {profile?.nonFiniteCount ? <div><dt>非有限数</dt><dd>{profile.nonFiniteCount}</dd></div> : null}
    </dl>
    {profile && <section className="field-sample" aria-label={`${field.name} 样例`}>
      <h5>样例值</h5>
      {profile.sampleValues.length > 0
        ? <ul>{profile.sampleValues.map((value, index) => <li key={index}><code>{formatFieldProfileValue(value, field.type, profile.exactIntegers?.sampleValues?.[index])}</code></li>)}</ul>
        : <p className="muted">没有非空样例</p>}
    </section>}
    <NumericStringEvidence field={field} />
  </li>;
}

export function FieldProfile({ version }: { version: Version }) {
  const profile = version.descriptor.profile;
  const rowCount = version.descriptor.rowCount;
  return <section className="field-profile" aria-labelledby="field-profile-title">
    <div className="section-heading field-profile-title">
      <div><h4 id="field-profile-title">字段画像</h4><p>核对物理类型、完整性、值域与已声明语义。</p></div>
      <span className="profile-scope">{profile?.statisticsScope === "all_rows" ? `全量统计 · ${rowCount ?? 0} 行` : "基础字段信息"}</span>
    </div>
    {profile
      ? <p className="profile-sample-note">样例为每字段最先出现的 {profile.sampleLimitPerField} 个非空值，不代表值分布。</p>
      : <p className="profile-sample-note" role="status">该历史版本未生成字段画像；仅展示已持久化的类型与空值事实。</p>}
    <ul className="field-profile-list">{version.descriptor.fields.map(field => <FieldProfileItem key={field.fieldId} field={field} version={version} />)}</ul>
  </section>;
}

function metadataValue(metadata: Record<string, unknown>, snake: string, camel: string): unknown {
  return metadata[snake] ?? metadata[camel];
}

export function SpatialPreviewProfile({ metadata, truncated }: {
  metadata: Record<string, unknown>;
  truncated: boolean;
}) {
  const featureCount = metadataValue(metadata, "feature_count", "featureCount");
  const geometryTypes = metadataValue(metadata, "geometry_types", "geometryTypes");
  const crs = metadata.crs;
  const bbox = metadataValue(metadata, "bbox_wgs84", "bboxWgs84");
  if (featureCount === undefined && geometryTypes === undefined && crs === undefined && bbox === undefined) return null;
  return <section className="spatial-profile" role="region" aria-label="空间画像">
    <div className="spatial-profile-heading"><strong>空间画像</strong><span>{truncated ? "预览样本统计" : "当前预览已覆盖全部要素"}</span></div>
    <dl>
      {featureCount !== undefined && <div><dt>完整要素数</dt><dd>{valueLabel(featureCount)}</dd></div>}
      {Array.isArray(geometryTypes) && <div><dt>几何类型</dt><dd>{geometryTypes.map(valueLabel).join("、") || "未识别"}</dd></div>}
      {crs !== undefined && <div><dt>坐标参考系</dt><dd>{valueLabel(crs)}</dd></div>}
      {Array.isArray(bbox) && <div><dt>WGS84 范围</dt><dd>{bbox.map(valueLabel).join(", ")}</dd></div>}
    </dl>
    <p>{truncated ? "范围与几何类型仅基于当前受限预览，不代表全量空间范围。" : "范围与几何类型基于当前已完整覆盖的预览要素。"}</p>
  </section>;
}
