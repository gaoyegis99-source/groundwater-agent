import { describe, expect, it } from 'vitest';
import { decodeDataPreviewResponse, decodeDataQueryResponse } from './api-contract';

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
});
