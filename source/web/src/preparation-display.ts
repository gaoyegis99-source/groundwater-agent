import type { TaskPreparationCheck, TaskPreparationEntry } from './types';

export function preparationIssues(
  entry: TaskPreparationEntry,
): NonNullable<TaskPreparationEntry['blockingIssues']> {
  if (entry.blockingIssues?.length) return entry.blockingIssues;
  return entry.blockingReasons.map(code => {
    const data = /^WORKFLOW_DRAFT_MISSING_DATA:(.+)$/.exec(code);
    const parameter = /^WORKFLOW_DRAFT_MISSING_PARAMETER:(.+)$/.exec(code) ?? /^validator:(.+)$/.exec(code);
    if (data) return { code, kind: 'MISSING_DATA' as const, input: data[1] };
    if (parameter) return { code, kind: 'MISSING_PARAMETER' as const, parameter: parameter[1] };
    if (code === 'WORKFLOW_DRAFT_UNRESOLVED_ITEMS') return { code, kind: 'UNRESOLVED_ITEM' as const };
    if (code === 'DATA_VERSION_NOT_CURRENT') return { code, kind: 'DATA_VERSION_NOT_CURRENT' as const };
    return { code, kind: 'UNKNOWN' as const };
  });
}

export function preparationCheckFacts(check: TaskPreparationCheck): string | null {
  if (check.kind === 'FIELD_UNIT') {
    return [
      check.actualUnit ? `已声明 ${check.actualUnit}` : null,
      check.expectedUnit ? `执行参数 ${check.expectedUnit}` : null,
      check.relationship ?? null,
    ].filter(Boolean).join('；') || null;
  }
  if (check.kind === 'CRS' || check.kind === 'CRS_PARAMETER') {
    return [
      check.actualCrs ? `已声明 ${check.actualCrs}` : null,
      check.expectedCrs ? `期望 ${check.expectedCrs}` : null,
      check.crsRequirement === 'projectedMetric' ? '要求米制投影 CRS' : null,
      check.crsUnit ? `轴单位 ${check.crsUnit}` : null,
    ].filter(Boolean).join('；') || null;
  }
  if (check.kind === 'TIMEZONE') return check.timezone ? `时区 ${check.timezone}` : null;
  if (check.kind === 'TIME_RANGE') {
    return [
      check.timeRange ? `可用范围 ${check.timeRange}` : null,
      check.requestedStart || check.requestedEnd
        ? `请求 ${check.requestedStart ?? '？'} 至 ${check.requestedEnd ?? '？'}`
        : null,
    ].filter(Boolean).join('；') || null;
  }
  if (check.kind === 'VERTICAL_BASIS') {
    return [
      check.referenceField ? `高程字段 ${check.referenceField}` : null,
      check.depthField ? `埋深字段 ${check.depthField}` : null,
      check.referenceMeaning ? `高程含义 ${check.referenceMeaning}` : null,
      check.depthMeaning ? `埋深含义 ${check.depthMeaning}` : null,
      check.verticalDatum ? `高程基准 ${check.verticalDatum}` : null,
    ].filter(Boolean).join('；') || null;
  }
  return null;
}
