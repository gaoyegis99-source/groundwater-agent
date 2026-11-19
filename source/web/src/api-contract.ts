import { validDatasetProfile, validFieldProfile } from '@platform/ui-boundary';
import type { DataPreviewResponse, DataQueryResponse, ResourceDetail, Result } from './types';

type RecordValue = Record<string, unknown>;

function record(value: unknown): RecordValue | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as RecordValue
    : null;
}

function requiredString(item: RecordValue, key: string): string {
  if (typeof item[key] !== 'string') throw new TypeError('API_RESPONSE_INVALID');
  return item[key];
}

export function decodeResourceDetail(value: unknown): ResourceDetail {
  const item = record(value);
  if (!item) throw new TypeError('API_RESPONSE_INVALID');
  for (const key of ['versionId', 'datasetId', 'publicationId', 'semanticId', 'title']) requiredString(item, key);
  const descriptor = record(item.descriptor);
  const semantics = record(item.semantics);
  if (!descriptor || !semantics || !Array.isArray(item.inputVersions) || !Array.isArray(item.objects)
      || !item.inputVersions.every(entry => typeof entry === 'string')) throw new TypeError('API_RESPONSE_INVALID');
  for (const objectValue of item.objects) {
    const object = record(objectValue);
    if (!object || typeof object.objectId !== 'string' || typeof object.size !== 'number'
        || typeof object.checksum !== 'string' || typeof object.format !== 'string'
        || typeof object.mediaType !== 'string' || !['raw', 'query'].includes(String(object.role))) {
      throw new TypeError('API_RESPONSE_INVALID');
    }
  }
  const profile = validDatasetProfile(descriptor.profile) ? descriptor.profile : undefined;
  const rowCount = typeof descriptor.rowCount === 'number' ? descriptor.rowCount : null;
  if (descriptor.profile !== undefined && (!profile || rowCount === null
      || !Number.isInteger(rowCount) || rowCount < 0 || !Array.isArray(descriptor.fields))) throw new TypeError('API_RESPONSE_INVALID');
  if (descriptor.fields !== undefined && (!Array.isArray(descriptor.fields)
      || !descriptor.fields.every(fieldValue => {
        const field = record(fieldValue);
        if (!field || typeof field.fieldId !== 'string' || typeof field.name !== 'string' || typeof field.type !== 'string') return false;
        if (profile === undefined) return field.profile === undefined;
        return typeof field.nullCount === 'number'
          && Number.isInteger(field.nullCount) && field.nullCount >= 0
          && validFieldProfile(field.profile, profile.sampleLimitPerField, rowCount, field.nullCount);
      }))) throw new TypeError('API_RESPONSE_INVALID');
  return value as ResourceDetail;
}

export function decodeResults(value: unknown): { items: Result[]; nextCursor: number | null } {
  const page = record(value);
  if (!page || !Array.isArray(page.items) || !(page.nextCursor === null || typeof page.nextCursor === 'number')) {
    throw new TypeError('API_RESPONSE_INVALID');
  }
  for (const resultValue of page.items) {
    const result = record(resultValue);
    if (!result || typeof result.goalRevision !== 'number' || typeof result.agentRunId !== 'string'
        || typeof result.toolCallId !== 'string' || typeof result.modelId !== 'string'
        || typeof result.status !== 'string' || !Array.isArray(result.remoteOutputs)) {
      throw new TypeError('API_RESPONSE_INVALID');
    }
  }
  return page as { items: Result[]; nextCursor: number | null };
}

function operation(value: unknown, id: 'queryId' | 'previewId'): RecordValue {
  const item = record(value);
  const states = new Set(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED']);
  if (!item || typeof item[id] !== 'string' || !states.has(String(item.state))
      || typeof item.resultAvailable !== 'boolean'
      || !(item.expiresAt === null || typeof item.expiresAt === 'string')
      || !(item.error === null || record(item.error))) {
    throw new TypeError('API_RESPONSE_INVALID');
  }
  return item;
}

export function parseDataQueryJson(text: string): unknown {
  const value: unknown = JSON.parse(text);
  const result = record(record(value)?.result);
  if (!result || !Array.isArray(result.rows)) return value;
  const hasUnsafeInteger = result.rows.some(row => Array.isArray(row) && row.some(cell =>
    typeof cell === 'number' && Number.isInteger(cell) && !Number.isSafeInteger(cell),
  ));
  if (!hasUnsafeInteger) return value;

  // Match quoted strings and complete number tokens so their contents cannot be mistaken for integers.
  const tokens = /"[^"\\]*(?:\\.[^"\\]*)*"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  const exactText = text.replace(tokens, (token, number: string | undefined) =>
    number !== undefined && /^-?\d+$/.test(number) && !Number.isSafeInteger(Number(number))
      ? '"' + number + '"' : token,
  );
  // Only project the recovered cells. Counts and all other metadata keep their normal JSON types.
  const exact = JSON.parse(exactText) as { result: { rows: unknown[] } };
  result.rows = exact.result.rows;
  return value;
}

export function decodeDataQueryResponse(value: unknown): DataQueryResponse {
  const item = operation(value, 'queryId');
  const result = item.result === null ? null : record(item.result);
  const receipt = item.receipt === null ? null : record(item.receipt);
  if ((item.result !== null && (!result || !Array.isArray(result.fields)
      || !Array.isArray(result.rows) || !result.fields.every(field => !!record(field) && typeof record(field)!.fieldId === 'string')
      || typeof result.truncated !== 'boolean'
      || !(result.nextOffset === null || typeof result.nextOffset === 'number')))
      || (item.receipt !== null && (!receipt || typeof receipt.queryId !== 'string'
        || typeof receipt.returnedRows !== 'number'
        || typeof receipt.resultChecksum !== 'string'
        || typeof receipt.truncated !== 'boolean'))) {
    throw new TypeError('API_RESPONSE_INVALID');
  }
  return value as DataQueryResponse;
}

export function decodeDataPreviewResponse(value: unknown): DataPreviewResponse {
  const item = operation(value, 'previewId');
  const result = item.result === null ? null : record(item.result);
  const receipt = item.receipt === null ? null : record(item.receipt);
  if ((item.result !== null && (!result
      || !['xlsx_table', 'geopackage_layer'].includes(String(result.kind))
      || !Array.isArray(result.fields) || !result.fields.every(field => !!record(field) && typeof record(field)!.fieldId === 'string') || !Array.isArray(result.rows)
      || !Array.isArray(result.features) || !Array.isArray(result.warnings)
      || typeof result.truncated !== 'boolean'
      || !(result.nextOffset === null || typeof result.nextOffset === 'number')))
      || (item.receipt !== null && !receipt)) {
    throw new TypeError('API_RESPONSE_INVALID');
  }
  return value as DataPreviewResponse;
}
