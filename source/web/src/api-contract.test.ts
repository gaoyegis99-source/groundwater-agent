import { describe, expect, it } from 'vitest';
import { decodeDataPreviewResponse, decodeDataQueryResponse, parseDataQueryJson } from './api-contract';

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
});
