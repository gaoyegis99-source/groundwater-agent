import { afterEach, expect, it, vi } from 'vitest';
import { runDataQuery, waitForDataPreview } from './api';

const jsonResponse = (value: unknown, status = 200) => new Response(
  JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } },
);

afterEach(() => {
  vi.unstubAllGlobals();
});

function runPollTimersImmediately() {
  vi.spyOn(window, 'setTimeout').mockImplementation(((handler: TimerHandler) => {
    queueMicrotask(() => {
      if (typeof handler === 'function') handler();
    });
    return 1;
  }) as typeof window.setTimeout);
}

it('waits for a query terminal state beyond the former client polling budget', async () => {
  runPollTimersImmediately();
  let polls = 0;
  vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return jsonResponse({ operationId: 'operation-1', queryId: 'query-1' }, 202);
    }
    polls += 1;
    if (polls <= 240) {
      return jsonResponse({
        queryId: 'query-1', state: 'RUNNING', error: null, expiresAt: null,
        resultAvailable: false, receipt: null, result: null,
      });
    }
    const digest = `sha256:${'a'.repeat(64)}`;
    return jsonResponse({
      queryId: 'query-1', state: 'SUCCEEDED', error: null, expiresAt: null,
      resultAvailable: true,
      receipt: {
        queryId: 'query-1', inputs: [], plan: {}, planDigest: digest, coverage: {},
        truncated: false, returnedRows: 0, resultChecksum: digest, engine: {},
      },
      result: { fields: [], rows: [], nextOffset: null, truncated: false, coverage: {}, engine: {} },
    });
  }));

  const result = await runDataQuery(
    { versionId: 'version-1', semanticId: 'semantic-1' },
    0,
    100,
  );

  expect(result).toMatchObject({ state: 'SUCCEEDED' });
  expect(polls).toBe(241);
});

it('waits for a preview terminal state beyond the former client polling budget', async () => {
  runPollTimersImmediately();
  let polls = 0;
  vi.stubGlobal('fetch', vi.fn(async () => {
    polls += 1;
    if (polls <= 240) {
      return jsonResponse({
        previewId: 'preview-1', state: 'RUNNING', error: null, expiresAt: null,
        resultAvailable: false, receipt: null, result: null,
      });
    }
    return jsonResponse({
      previewId: 'preview-1', state: 'SUCCEEDED', error: null, expiresAt: null,
      resultAvailable: true, receipt: {},
      result: {
        kind: 'xlsx_table', metadata: {}, fields: [], rows: [], features: [],
        truncated: false, warnings: [], nextOffset: null,
      },
    });
  }));

  const result = await waitForDataPreview('preview-1', 0, 100);

  expect(result).toMatchObject({ state: 'SUCCEEDED' });
  expect(polls).toBe(241);
});
