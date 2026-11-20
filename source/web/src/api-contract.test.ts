import { describe, expect, it } from 'vitest';
import { decodeDataPreviewResponse, decodeDataQueryResponse, parseDataQueryJson } from './api-contract';
import { loadRequestValidator, loadResponseValidator } from './generated/public-api-v12.validators.lazy.js';
import type { BusinessAssetReference, WorkItemDetail } from './types';

describe('public API response boundary', () => {
  it('uses the public camelCase contract without a key-mapping compatibility layer', () => {
    const value = {
      dialoguePolicy: 'READ_ONLY',
      semanticIntent: 'CONSULT',
      intent: {
        schemaVersion: 'intent-decision.v4',
        responseKind: 'DIALOGUE',
        semanticIntent: 'CONSULT',
        taskAction: 'NONE',
        dialoguePolicy: 'READ_ONLY',
        controlIntent: null,
        clarificationReason: null,
      },
    };
    expect(value).toEqual({
      dialoguePolicy: 'READ_ONLY',
      semanticIntent: 'CONSULT',
      intent: {
        schemaVersion: 'intent-decision.v4',
        responseKind: 'DIALOGUE',
        semanticIntent: 'CONSULT',
        taskAction: 'NONE',
        dialoguePolicy: 'READ_ONLY',
        controlIntent: null,
        clarificationReason: null,
      },
    });
  });

  it('accepts the generated Data query response shape', () => {
    const value = {
      queryId: 'query-1', state: 'SUCCEEDED', error: null,
      expiresAt: '2026-10-13T00:00:00Z', resultAvailable: true,
      receipt: { queryId: 'query-1', coverage: {}, returnedRows: 1,
        resultChecksum: `sha256:${'a'.repeat(64)}`, truncated: false },
      result: { fields: [{ fieldId: 'value', type: 'number' }], rows: [[1]],
        nextOffset: null, truncated: false },
    };
    expect(decodeDataQueryResponse(value)).toBe(value);
  });

  it('preserves bare unsafe integers only in query cells', () => {
    const text = String.raw`{
      "result": {
        "rows": [
          [9007199254740992, 9007199254740993, -9223372036854775808, 18446744073709551615],
          [9007199254740991, 9007199254740993.0, 9.007199254740993e15, 4.25, true, null, "编号 \"9007199254740993\" \\ 样本"]
        ],
        "nextOffset": 100
      },
      "receipt": { "coverage": { "sourceRows": 9007199254740993 } }
    }`;
    expect(parseDataQueryJson(text)).toEqual({
      result: {
        rows: [
          ['9007199254740992', '9007199254740993', '-9223372036854775808', '18446744073709551615'],
          [Number.MAX_SAFE_INTEGER, 2 ** 53, 2 ** 53, 4.25, true, null, '编号 "9007199254740993" \\ 样本'],
        ],
        nextOffset: 100,
      },
      receipt: { coverage: { sourceRows: 2 ** 53 } },
    });
  });

  it('rejects a response that drifted from the required operation fields', () => {
    expect(() => decodeDataQueryResponse({
      queryId: 'query-1', state: 'SUCCEEDED', resultAvailable: true,
      error: null, receipt: null, result: null,
    })).toThrow('API_RESPONSE_INVALID');
  });

  it('checks the bounded preview result before rendering it', () => {
    const value = {
      previewId: 'preview-1', state: 'SUCCEEDED', error: null,
      expiresAt: null, resultAvailable: true, receipt: {},
      result: { kind: 'xlsx_table', metadata: {}, fields: [], rows: [],
        features: [], truncated: false, warnings: [], nextOffset: null },
    };
    expect(decodeDataPreviewResponse(value)).toBe(value);
    expect(() => decodeDataPreviewResponse({ ...value, result: { kind: 'xlsx_table' } }))
      .toThrow('API_RESPONSE_INVALID');
  });

  it('validates WorkItem result selection, publication identity, and redacted links', async () => {
    const [validateRequest, validateDetail] = await Promise.all([
      loadRequestValidator('kernel_link_work_item_asset'),
      loadResponseValidator('kernel_get_work_item'),
    ]);
    if (!validateRequest || !validateDetail) throw new Error('WorkItem contract missing');
    const selection = { kind: 'RESULT', runId: 'run', outputName: 'aggregate-summary' } as const;
    const command = { projectId: 'groundwater', requestId: 'request', expectedRevision: 2 };
    expect(validateRequest({ ...command, reference: selection })).toBe(true);
    expect(validateRequest({ ...command, expectedRevision: true, reference: selection })).toBe(false);
    expect(validateRequest({ ...command, reference: { kind: 'RESULT', runId: 'run' } })).toBe(false);

    const checksum = `sha256:${'a'.repeat(64)}`;
    const reference: BusinessAssetReference = {
      ...selection, publicationId: 'publication',
      dataRef: {
        kind: 'data_version', service: 'data-primary', datasetId: 'datasets', versionId: 'version',
        semanticId: 'semantics', objectId: 'raw', mediaType: 'application/json', format: 'json',
        sizeBytes: 1024, checksum, outputName: selection.outputName,
      },
    };
    const linkedAt = '2026-11-20T00:00:00Z';
    const detail: WorkItemDetail = {
      workItem: {
        workItemId: 'work', ownerSubject: 'researcher', title: '监测趋势', projectId: 'groundwater',
        revision: 3, status: 'ACTIVE', description: null, createdAt: linkedAt,
        updatedAt: linkedAt, archivedAt: null,
      },
      sessions: [], tasks: [], taskRuns: [], agentRuns: [], workflowRuns: [], objects: [],
      assets: [
        { linkId: 'visible', state: 'AVAILABLE', linkedAt, reference },
        { linkId: 'hidden', state: 'UNAVAILABLE', linkedAt, reference: null },
      ],
    };
    expect(validateDetail(detail)).toBe(true);
    expect(validateDetail({
      ...detail,
      assets: [{ ...detail.assets[0], reference: { ...selection, dataRef: reference.dataRef } }],
    })).toBe(false);
  });
});
